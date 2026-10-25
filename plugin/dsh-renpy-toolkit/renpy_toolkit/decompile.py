"""Recover ``.rpy`` source from compiled ``.rpyc`` files.

Why this module exists
----------------------
Translation work keeps running into the same wall: the game ships compiled
only.  ``game/*.rpyc`` and ``game/tl/<lang>/*.rpyc`` exist, the ``.rpy`` files
they were compiled from do not.  Without the sources you cannot:

* read the original English that a Spanish/Chinese ``translate`` block was
  written against,
* run :mod:`renpy_toolkit.tl` on a ``tl/<lang>`` directory (it parses text),
* diff an update against your own translation.

Ren'Py's own ``renpy.py <game> decompile`` command solves this by loading the
pickled AST with Ren'Py's own classes.  This module reaches the same result
without Ren'Py.

Design: a pickletools VM, not ``pickle.loads``
----------------------------------------------
``pickle.loads`` on a ``.rpyc`` read out of a game directory executes whatever
the pickle says -- a ``GLOBAL`` opcode naming ``os.system`` is enough.  A
translator who downloads a game is in the worst possible position to run
untrusted pickles, so this module never calls ``pickle.loads``.  Instead
:func:`renpy_toolkit.rpyc.decode_script` walks the stream with
``pickletools.genops`` and interprets only a whitelist of opcodes, building
plain Python values plus :class:`Node` objects.  Every other opcode raises
:class:`RpycError` instead of being executed.

The produce-what-we-can rule
----------------------------
The AST contains node types this module has no emitter for (``Parallel``,
``Python``, screens-as-code, ATL, ...).  Dropping them silently would produce a
``.rpy`` that *looks* fine and quietly changes the game, so the emitter always
writes a ``# TODO`` comment for each node it cannot represent and records the
node type in :class:`DecompileResult.unsupported`.  Callers decide whether a
75%-faithful file is useful; the module never pretends otherwise.

Round-trip contract for ``translate`` blocks
--------------------------------------------
Translation files are the case that must be perfect, and they are also the
simplest: they hold ``translate``/``EndTranslate`` pairs and ``TranslateString``
nodes, nothing else.  ``decompile`` then yields files that :mod:`tl` parses back
into the same identifiers -- which is what :func:`renpy_toolkit.rpyc.roundtrip_ok`
checks.
"""

from __future__ import annotations

import fnmatch
import os
import pickletools
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Sequence

from .errors import ToolkitError
from .rpyc import RpycError
from . import safeio

__all__ = [
    "DecompileResult",
    "Node",
    "ScriptTarget",
    "decode_script",
    "decompile_dir",
    "decompile_file",
    "escape_string",
    "emit_node",
    "find_scripts",
    "is_compiled_only",
    "renpy_escape",
]

# --------------------------------------------------------------------------
# AST model
# --------------------------------------------------------------------------


@dataclass
class Node:
    """One Ren'Py AST node, reduced to the fields an emitter needs.

    ``kind`` is the bare class name (``"Say"``, ``"Translate"``...).
    ``fields`` holds the unpickled ``__dict__`` of the node, with nested nodes
    already converted to :class:`Node` and everything else left as plain Python
    values.
    """

    kind: str
    fields: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str, default: Any = None) -> Any:
        return self.fields.get(name, default)

    def block(self) -> list["Node"]:
        value = self.fields.get("block")
        if isinstance(value, list):
            return [n for n in value if isinstance(n, Node)]
        return []

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Node({self.kind})"


# --------------------------------------------------------------------------
# Restricted unpickler
# --------------------------------------------------------------------------


class _Ref:
    """A resolved ``GLOBAL``/``STACK_GLOBAL``: module path + class name.

    Ren'Py nodes are instantiated through ``REDUCE`` (``__reduce__`` returns
    ``(cls, args, state)``).  We do not construct the real class -- it may not
    exist here -- so the callable is represented by this marker and
    ``REDUCE`` turns ``(_Ref('renpy.ast', 'Say'), args)`` into a :class:`Node`.
    """

    __slots__ = ("module", "name")

    def __init__(self, module: str, name: str) -> None:
        self.module = module
        self.name = name

    @property
    def kind(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.module}.{self.name}"


class _Sentinel:
    """Stand-in for ``None``-like singletons we refuse to resolve."""

    __slots__ = ("label",)

    def __init__(self, label: str) -> None:
        self.label = label

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return self.label


class _StrFactory:
    """Callable that reduces a ``renpy.ast`` str-subclass constructor to a str."""

    __slots__ = ()

    def __call__(self, *args: Any) -> Any:
        return str(args[0]) if args else ""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "PyExpr"


#: Ren'Py AST classes that are really ``str`` subclasses (expression text plus
#: a ``(filename, linenumber)`` origin).  They are never emitted as nodes.
_STR_SUBCLASSES = frozenset({"PyExpr"})


#: ``renpy.object.Sentinel`` names that may appear in an AST.  None of them
#: can be emitted as source, but they must not abort the decode either.
_SENTINELS = {"NotSet": "NotSet", "Deleted": "Deleted", "Nobody": "Nobody"}

#: Modules whose classes we accept as AST nodes.  Anything else in a
#: ``GLOBAL`` opcode is refused: this is the whole safety story.
_ALLOWED_MODULES = frozenset(
    {
        "renpy.ast",
        "renpy.atl",
        "renpy.parameter",
        "renpy.pyanalysis",
        "renpy.lexer",
        "renpy.object",
        "renpy.sl2.slast",
        "renpy.sl2.slparser",
        "renpy.styledata.stylesets",
    }
)

#: ``GLOBAL`` names that are plain-data constructors, not game objects.
_SAFE_GLOBALS = {
    ("builtins", "set"): set,
    ("builtins", "frozenset"): frozenset,
    ("builtins", "list"): list,
    ("builtins", "dict"): dict,
    ("builtins", "tuple"): tuple,
    ("builtins", "object"): object,
    ("__builtin__", "set"): set,
    ("__builtin__", "frozenset"): frozenset,
    ("__builtin__", "list"): list,
    ("__builtin__", "dict"): dict,
    ("__builtin__", "tuple"): tuple,
    ("__builtin__", "object"): object,
    ("collections", "OrderedDict"): dict,
    ("collections", "defaultdict"): _Sentinel("defaultdict"),
    ("copyreg", "_reconstructor"): _Sentinel("_reconstructor"),
    ("copy_reg", "_reconstructor"): _Sentinel("_reconstructor"),
    # Ren'Py stores most container metadata in revertable wrappers.  They are
    # plain ``dict``/``list``/``set`` subclasses, so unwrapping them loses
    # nothing an emitter cares about -- and refusing them would make every
    # real .rpyc undecodable, since the metadata dict is always one.
    ("renpy.revertable", "RevertableDict"): dict,
    ("renpy.revertable", "RevertableList"): list,
    ("renpy.revertable", "RevertableSet"): set,
    ("renpy.revertable", "RevertableObject"): _Sentinel("RevertableObject"),
}


def _resolve(module: str, name: str) -> Any:
    """Turn a ``GLOBAL`` target into something safe to call.

    Returns a :class:`_Ref` for Ren'Py AST classes, a plain Python callable for
    data types, a :class:`_Sentinel` for Ren'Py game objects we never need to
    look inside, or raises :class:`RpycError`.  Execution of arbitrary names is
    exactly what this function prevents.
    """
    if module == "renpy.object" and name in _SENTINELS:
        return _Sentinel(name)
    if (module, name) in _SAFE_GLOBALS:
        return _SAFE_GLOBALS[(module, name)]
    if name in _STR_SUBCLASSES:
        # ``PyExpr`` is a str subclass: ``PyExpr("text", "file.rpy", 12)`` is
        # just the expression text plus its origin.  The origin is dead weight
        # for an emitter -- and the AST is about to be rewritten anyway -- so
        # it collapses to the plain string.
        return _StrFactory()
    if module in _ALLOWED_MODULES:
        return _Ref(module, name)
    if module.split(".")[0] == "renpy":
        # Any other ``renpy.*`` class is a game/engine object that rides along
        # inside node fields (``Text`` displayables, ``sl2add`` style values,
        # store proxies...).  They are never constructed here -- only held
        # opaque -- so the safety story is unchanged, while refusing them would
        # make half the scripts undecodable.
        return _Sentinel(f"{module}.{name}")
    raise RpycError(
        f"pickle references {module}.{name}, which is not on the allow-list; "
        "refusing to execute it (this file may not be a Ren'Py script)"
    )


class _Builder:
    """Minimal VM over ``pickletools.genops``.

    Only the opcodes Ren'Py's own pickles use are implemented -- protocol 2,
    produced by ``pickle.dumps(..., 2)``.  An unknown opcode aborts the decode
    rather than being skipped, because silently skipping one would desynchronise
    the stack and emit garbage source.
    """

    def __init__(self) -> None:
        self.stack: list[Any] = []
        self.metastack: list[list[Any]] = []
        self.memo: dict[int, Any] = {}
        self._marks: list[int] = []

    # -- helpers -----------------------------------------------------------
    def _pop_mark(self) -> list[Any]:
        index = self._marks.pop()
        items = self.stack[index:]
        del self.stack[index:]
        return items

    def run(self, data: bytes) -> Any:
        for opcode, arg, _pos in pickletools.genops(data):
            self._step(opcode.name, arg)
        if not self.stack:
            raise RpycError("pickle ended with an empty stack")
        return self.stack[-1]

    # -- opcodes -----------------------------------------------------------
    def _step(self, name: str, arg: Any) -> None:
        stack = self.stack

        if name in ("PROTO", "FRAME"):
            return
        if name == "STOP":
            return
        if name == "MARK":
            self._marks.append(len(stack))
            return
        if name == "POP":
            stack.pop()
            return
        if name == "POP_MARK":
            self._pop_mark()
            return
        if name == "DUP":
            stack.append(stack[-1])
            return

        # memo ------------------------------------------------------------
        if name == "MEMOIZE":
            self.memo[len(self.memo)] = stack[-1]
            return
        if name in ("BINPUT", "LONG_BINPUT", "PUT"):
            self.memo[int(arg)] = stack[-1]
            return
        if name in ("BINGET", "LONG_BINGET", "GET"):
            stack.append(self.memo[int(arg)])
            return

        # scalars ---------------------------------------------------------
        if name in ("BININT", "BININT1", "BININT2", "INT", "LONG", "LONG1", "LONG4"):
            stack.append(int(arg))
            return
        if name == "BINFLOAT":
            stack.append(float(arg))
            return
        if name in ("BINUNICODE", "SHORT_BINUNICODE", "UNICODE", "BINSTRING"):
            stack.append(arg if isinstance(arg, str) else arg.decode("utf-8", "replace"))
            return
        if name in ("SHORT_BINSTRING", "STRING"):
            raw = arg if isinstance(arg, bytes) else str(arg).encode("latin-1", "replace")
            stack.append(raw.decode("utf-8", "replace"))
            return
        if name == "BINBYTES":
            stack.append(arg if isinstance(arg, bytes) else bytes(arg))
            return
        if name == "BINBYTES8":
            stack.append(bytes(arg))
            return
        if name == "NONE":
            stack.append(None)
            return
        if name == "NEWTRUE":
            stack.append(True)
            return
        if name == "NEWFALSE":
            stack.append(False)
            return

        # containers ------------------------------------------------------
        if name == "EMPTY_LIST":
            stack.append([])
            return
        if name == "EMPTY_DICT":
            stack.append({})
            return
        if name == "EMPTY_SET":
            stack.append(set())
            return
        if name == "EMPTY_TUPLE":
            stack.append(())
            return
        if name in ("TUPLE", "TUPLE1", "TUPLE2", "TUPLE3"):
            count = {"TUPLE1": 1, "TUPLE2": 2, "TUPLE3": 3}.get(name)
            if count is None:
                items = self._pop_mark()
            else:
                items = stack[-count:]
                del stack[-count:]
            stack.append(tuple(items))
            return
        if name == "LIST":
            items = self._pop_mark()
            stack.append(list(items))
            return
        if name == "DICT":
            items = self._pop_mark()
            stack.append(dict(zip(items[0::2], items[1::2])))
            return
        if name == "SETITEMS" or name == "ADDITEMS":
            items = self._pop_mark()
            target = stack[-1]
            if isinstance(target, dict):
                for key, value in zip(items[0::2], items[1::2]):
                    target[key] = value
            elif isinstance(target, list):
                target.extend(items)
            elif isinstance(target, set):
                target.update(items)
            elif isinstance(target, _Sentinel):
                # Opaque object we never construct: its contents are
                # unreachable by design (see :func:`_build`).
                pass
            else:  # pragma: no cover - malformed pickle
                raise RpycError(f"{name} on a {type(target).__name__}")
            return
        if name == "APPEND":
            value = stack.pop()
            if not isinstance(stack[-1], _Sentinel):
                stack[-1].append(value)
            return
        if name == "APPENDS":
            items = self._pop_mark()
            if not isinstance(stack[-1], _Sentinel):
                stack[-1].extend(items)
            return
        if name == "SETITEM":
            value = stack.pop()
            key = stack.pop()
            if isinstance(stack[-1], _Sentinel):
                return
            stack[-1][key] = value
            return

        # callables -------------------------------------------------------
        if name == "GLOBAL":
            module, _, cls = str(arg).partition(" ")
            stack.append(_resolve(module, cls))
            return
        if name == "STACK_GLOBAL":
            cls = stack.pop()
            module = stack.pop()
            stack.append(_resolve(str(module), str(cls)))
            return
        if name == "REDUCE":
            args = stack.pop()
            func = stack.pop()
            stack.append(_apply(func, args))
            return
        if name == "BUILD":
            state = stack.pop()
            instance = stack[-1]
            _build(instance, state)
            return
        if name == "NEWOBJ":
            args = stack.pop()
            cls = stack.pop()
            if isinstance(cls, _Ref):
                stack.append(Node(cls.kind, _unpack_state_dict(args)))
            elif isinstance(cls, _Sentinel):
                stack.append(cls)
            else:
                stack.append(cls(*args) if callable(cls) else args)
            return
        if name == "INST":
            module, _, cls = str(arg).partition(" ")
            stack.append(_resolve(module, cls))
            return

        # persistence / extensions we refuse ------------------------------
        if name in ("PERSID", "BINPERSID"):
            raise RpycError("persistent references are not supported in a script pickle")
        raise RpycError(f"unsupported pickle opcode {name}")


def _unpack_state_dict(args: Any) -> dict[str, Any]:
    """``__reduce__`` payload → field dict (Ren'Py passes ``(None, dict)``)."""
    if isinstance(args, tuple) and args and isinstance(args[-1], dict):
        return dict(args[-1])
    if isinstance(args, dict):
        return dict(args)
    return {}


def _apply(func: Any, args: Any) -> Any:
    if isinstance(func, _Ref):
        return Node(func.kind, _unpack_state_dict(args))
    if isinstance(func, _Sentinel):
        return func
    if not callable(func):
        # ``REDUCE`` on a non-callable cannot mean anything we can model.
        raise RpycError(f"REDUCE on non-callable {func!r}")
    if isinstance(args, tuple):
        try:
            return func(*args)
        except TypeError:
            return func(args)
    return func(args)


def _build(instance: Any, state: Any) -> None:
    """Apply a ``BUILD`` state to *instance* in place."""
    if isinstance(instance, Node):
        if isinstance(state, dict):
            instance.fields.update(state)
        elif isinstance(state, tuple) and len(state) == 2:
            _state_dict, slots = state
            if isinstance(_state_dict, dict):
                instance.fields.update(_state_dict)
            if isinstance(slots, dict):
                instance.fields.update(slots)
        return
    if isinstance(state, dict) and isinstance(instance, dict):
        instance.update(state)
    # ``_Sentinel`` stands for an opaque class we deliberately never construct
    # (``_reconstructor``, ``RevertableObject``).  Its state is unreachable
    # data by design, so dropping it is correct rather than lossy.


def _walk(value: Any) -> Iterator[Any]:
    """Yield every nested :class:`Node` reachable from *value*, depth first.

    Iterative on purpose: real ``.rpyc`` files nest several thousand levels
    deep (one ``Node`` per statement, chained through ``next``), which blows
    the Python recursion limit long before the data runs out.  *seen* guards
    against the reference cycles pickles of live objects can contain.
    """
    stack = [value]
    seen: set[int] = set()
    while stack:
        item = stack.pop()
        if isinstance(item, Node):
            marker = id(item)
            if marker in seen:
                continue
            seen.add(marker)
            yield item
            stack.extend(reversed(list(item.fields.values())))
        elif isinstance(item, dict):
            marker = id(item)
            if marker in seen:
                continue
            seen.add(marker)
            for key, sub in item.items():
                stack.append(sub)
                stack.append(key)
        elif isinstance(item, (list, tuple, set, frozenset)):
            marker = id(item)
            if marker in seen:
                continue
            seen.add(marker)
            stack.extend(item)


# --------------------------------------------------------------------------
# Slot decoding
# --------------------------------------------------------------------------


@dataclass
class ScriptTarget:
    """One compiled script and its decoded statement list."""

    path: str
    statements: list[Node] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    slot: int = 0
    unsupported: dict[str, int] = field(default_factory=dict)

    @property
    def game_version(self) -> int | None:
        value = self.metadata.get("version")
        return value if isinstance(value, int) else None


def _slot_payloads(path: str) -> list[bytes]:
    """Decompress every slot of *path* (RPC2 container), in file order.

    The container is ``b"RENPY RPC2"`` followed *immediately* by the slot table
    -- there is no newline and no header line.  Each entry is 12 bytes,
    ``struct("<III", slot, start, length)``, terminated by ``slot == 0``.
    ``start``/``length`` are absolute file offsets.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    if not data.startswith(b"RENPY RPC2"):
        raise RpycError(f"{path}: not a Ren'Py compiled script (bad magic)")

    pos = len(b"RENPY RPC2")
    entries: list[tuple[int, int, int]] = []
    while pos + 12 <= len(data):
        slot, start, length = struct.unpack_from("<III", data, pos)
        pos += 12
        if slot == 0:
            break
        entries.append((slot, start, length))
    if not entries:
        raise RpycError(f"{path}: empty slot table")

    payloads: list[bytes] = []
    for slot, start, length in entries:
        chunk = data[start : start + length]
        if len(chunk) != length:
            raise RpycError(
                f"{path}: slot {slot} claims {length} bytes, file has {len(chunk)}"
            )
        try:
            payloads.append(
                safeio.decompress_limited(chunk, what=f"{path}: slot {slot}")
            )
        except safeio.DecompressError as exc:
            raise RpycError(str(exc)) from exc
    return payloads


def decode_script(path: str) -> ScriptTarget:
    """Decode a ``.rpyc`` into a :class:`ScriptTarget`.

    Both slots are tried and the one carrying real ``Translate`` blocks wins:
    in the middle slot Ren'Py stores the same program flattened into
    ``TranslateSay`` nodes, which carry no identifier and so cannot be emitted
    back as a ``translate`` block.
    """
    payloads = _slot_payloads(path)
    best: ScriptTarget | None = None
    for index, payload in enumerate(payloads):
        try:
            value = _Builder().run(payload)
        except RpycError:
            continue
        metadata: dict[str, Any] = {}
        statements: list[Node] = []
        if isinstance(value, tuple) and len(value) == 2:
            if isinstance(value[0], dict):
                metadata = value[0]
            if isinstance(value[1], list):
                statements = [item for item in value[1] if isinstance(item, Node)]
        elif isinstance(value, list):
            statements = [item for item in value if isinstance(item, Node)]
        if not statements:
            continue
        score = sum(1 for n in statements if n.kind in _SCORE_KINDS)
        if best is None or score > sum(1 for n in best.statements if n.kind in _SCORE_KINDS):
            best = ScriptTarget(
                path=path, statements=statements, metadata=metadata, slot=index
            )
    if best is None:
        raise RpycError(f"{path}: no decodable program found in any slot")
    counts: dict[str, int] = {}
    for node in best.statements:
        for inner in _walk(node):
            if inner.kind in _EMITTABLE or inner.kind == "ArgumentInfo":
                # ``ArgumentInfo`` is a value holder, not a statement: it is
                # rendered inline by ``Say`` and is not a fidelity loss.
                continue
            counts[inner.kind] = counts.get(inner.kind, 0) + 1
    best.unsupported = counts
    return best


#: Node kinds that make a slot the "real" program rather than the flattened
#: translation index (which holds only ``TranslateSay``).
_SCORE_KINDS = frozenset({"Translate", "Label", "Init", "TranslateString", "Say"})


# --------------------------------------------------------------------------
# Emitting source
# --------------------------------------------------------------------------

#: Node kinds :func:`emit_node` knows how to write.
_EMITTABLE = frozenset(
    {
        "Translate",
        "TranslateString",
        "TranslateBlock",
        "TranslateSay",
        "Label",
        "Return",
        "Pass",
        "Jump",
        "Call",
        "Say",
        "Menu",
        "If",
        "While",
        "Init",
        "EndTranslate",
        "End",
        "EarlyReturn",
        "NvlClear",
    }
)


def renpy_escape(text: str) -> str:
    """Escape *text* for use inside a double-quoted Ren'Py string.

    This is the inverse of :func:`renpy_toolkit.tl.unescape_value` and matches
    what Ren'Py itself writes: backslash, double quote and newline are the only
    characters that change a string's structure, so they must be escaped, and
    ``\\n`` must be the two-character escape rather than a literal newline (a
    literal newline inside a quoted string is a syntax error).
    """
    out = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return out


#: Alias with a name that reads well at CLI call sites.
escape_string = renpy_escape


def _quote(text: str) -> str:
    return '"' + renpy_escape(text) + '"'


def _pad(depth: int) -> str:
    return "    " * depth


def _block_lines(block: Any, depth: int, out: list[str], result: _EmitState) -> None:
    if not isinstance(block, list):
        return
    for child in block:
        if isinstance(child, Node):
            emit_node(child, depth, out, result)


@dataclass
class _EmitState:
    """Counters threaded through one emit pass."""

    unsupported: dict[str, int] = field(default_factory=dict)
    wrote: int = 0

    def skip(self, node: Node, depth: int, out: list[str]) -> None:
        self.unsupported[node.kind] = self.unsupported.get(node.kind, 0) + 1
        out.append(_pad(depth) + f"# TODO: unsupported node {node.kind}")


def _format_arguments(arguments: Any) -> str:
    """Render a ``renpy.parameter.ArgumentInfo`` back to ``(a=1, *b, **c)``.

    Say statements carry ``(multiple=2)``-style arguments that Ren'Py folds into
    the translation identifier; dropping them would change every identifier in
    the rebuilt file, so they are reconstructed rather than skipped.
    """
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, Node):
        fields = arguments.fields
    elif isinstance(arguments, (list, tuple)) and len(arguments) >= 1:
        first = arguments[0]
        if isinstance(first, Node):
            fields = first.fields
        else:
            fields = {}
    else:
        return ""

    parts: list[str] = []
    positional = fields.get("arguments") or []
    for value in positional:
        parts.append(str(value))
    star = fields.get("extrapos")
    if star:
        parts.append("*" + str(star))
    for key, value in fields.get("keywords") or []:
        parts.append(f"{key}={value}")
    for key, value in fields.get("extrakw") or []:
        parts.append(f"**{key}" if value is None else f"**{key}")
    if not parts and fields.get("extrakw"):
        pass
    return "(" + ", ".join(parts) + ")" if parts else ""


def emit_node(
    node: Node,
    depth: int = 0,
    out: list[str] | None = None,
    state: _EmitState | None = None,
) -> list[str]:
    """Render *node* (and its children) as ``.rpy`` source lines."""
    if out is None:
        out = []
    if state is None:
        state = _EmitState()
    state.wrote += 1
    kind = node.kind
    pad = _pad(depth)

    if kind == "Translate":
        language = node.get("language") or "None"
        identifier = node.get("identifier")
        if identifier is None:
            out.append(pad + f"translate {language} strings:")
        else:
            out.append(pad + f"translate {language} {identifier}:")
        if node.get("alternate"):
            # ``translate <lang> <id>:`` is the only legal header; an alternate
            # language is expressed by a separate block, so flag it.
            out.append(pad + f"    # TODO: alternate language {node.get('alternate')!r}")
        before = len(out)
        _block_lines(node.get("block"), depth + 1, out, state)
        if len(out) == before:
            out.append(pad + "    pass")

    elif kind == "TranslateString":
        old = node.get("old")
        new = node.get("new")
        if "new" in node.fields:
            out.append(pad + f"old {_quote(str(old or ''))}")
            out.append(pad + f"new {_quote(str(new or ''))}")
        else:
            # ``old "x"`` with no ``new`` only appears in the *source* tree,
            # meaning "this string is translatable"; it is not valid inside a
            # translate block, so it is recorded instead of guessed at.
            state.skip(node, depth, out)

    elif kind == "Say":
        what = node.get("what")
        if what is None:
            # A missing ``what`` means "not a say statement" (e.g. the node
            # carries only a ``with`` clause); an *empty* string is a real,
            # translatable say whose text was blanked, so it must be emitted.
            state.skip(node, depth, out)
            return out
        who = node.get("who")
        attributes = node.get("attributes")
        text = _quote(str(what))
        rendered_arguments = _format_arguments(node.get("arguments"))
        if rendered_arguments:
            # ``e "text" (multiple=2)`` -- arguments come after the string and
            # before ``with``, matching the source grammar.
            text = f"{text} {rendered_arguments}"
        if attributes:
            text = f"{text} {' '.join(str(a) for a in attributes)}"
        with_ = node.get("with_")
        if with_:
            text = f"{text} with {with_}"
        if who and str(who) not in ("None", "null"):
            # Narration stores the literal string ``"None"`` as its speaker;
            # emitting that would produce ``None "text"``, a syntax error.
            text = f"{who} {text}"
        out.append(pad + text)

    elif kind == "Label":
        name = node.get("name")
        parameters = node.get("parameters")
        head = f"label {name}"
        if parameters is not None:
            state.unsupported["Label.parameters"] = (
                state.unsupported.get("Label.parameters", 0) + 1
            )
        out.append(pad + head + ":")
        before = len(out)
        _block_lines(node.get("block"), depth + 1, out, state)
        if node.get("hide"):
            out.append(pad + f"    # TODO: label {name} hide")
        if len(out) == before:
            out.append(pad + "    pass")

    elif kind == "Init":
        # ``init python:`` and ``init:`` containing only ``TranslateString``
        # nodes is what a ``translate <lang> strings:`` block compiles to:
        # Ren'Py hoists the string table into an init block so it loads before
        # any dialogue runs.  Emitting ``init:`` + ``old``/``new`` pairs would
        # be parsed as a say statement whose character is ``old``, so the block
        # is rebuilt in its original ``translate <lang> strings:`` form.
        children = node.block()
        strings_only = bool(children) and all(c.kind == "TranslateString" for c in children)
        if strings_only:
            languages = [
                str(c.get("language")) for c in children if c.get("language")
            ]
            language = languages[0] if languages else "None"
            out.append(pad + f"translate {language} strings:")
            for child in children:
                emit_node(child, depth + 1, out, state)
            return out

        priority = node.get("priority") or 0
        out.append(pad + (f"init {priority}:" if priority else "init:"))
        before = len(out)
        _block_lines(children, depth + 1, out, state)
        if len(out) == before:
            out.append(pad + "    pass")

    elif kind == "Menu":
        out.append(pad + "menu:")
        items = node.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                caption, condition, block = _menu_item(item)
                head = pad + "    " + _quote(str(caption)) + ":"
                if condition is not None:
                    head = pad + "    " + _quote(str(caption)) + f" if {condition}:"
                out.append(head)
                body = _pad(depth + 2)
                before = len(out)
                _block_lines(block, depth + 2, out, state)
                if len(out) == before:
                    out.append(body + "pass")
        for key in node.fields:
            if key not in ("items", "with_", "set", "name", "filename", "linenumber", "next"):
                out.append(pad + f"    # TODO: menu field {key}")
    elif kind == "If":
        entries = node.get("entries")
        if isinstance(entries, list) and entries:
            for index, entry in enumerate(entries):
                if not isinstance(entry, tuple) or len(entry) != 2:
                    continue
                condition, block = entry
                keyword = "if" if index == 0 else "elif"
                if condition is None or condition == "True":
                    keyword = "if" if index == 0 else "else"
                    out.append(pad + (keyword if keyword == "else" else "if True") + ":")
                else:
                    out.append(pad + f"{keyword} {condition}:")
                before = len(out)
                _block_lines(block, depth + 1, out, state)
                if len(out) == before:
                    out.append(pad + "    pass")
                if keyword == "else":
                    break
        else:
            state.skip(node, depth, out)

    elif kind == "While":
        condition = node.get("condition")
        out.append(pad + f"while {condition}:")
        before = len(out)
        _block_lines(node.get("block"), depth + 1, out, state)
        if len(out) == before:
            out.append(pad + "    pass")

    elif kind == "Jump":
        out.append(pad + f"jump {node.get('target')}")

    elif kind == "Call":
        out.append(pad + f"call {node.get('label')}")

    elif kind == "Return":
        expression = node.get("expression")
        out.append(pad + ("return " + str(expression) if expression else "return"))

    elif kind in ("Pass", "End"):
        out.append(pad + "pass")

    elif kind == "NvlClear":
        out.append(pad + "nvl clear")

    elif kind in ("EndTranslate",):
        return out

    elif kind == "EarlyReturn":
        out.append(pad + "return")

    else:
        state.skip(node, depth, out)
    return out


def _menu_item(item: Sequence[Any]) -> tuple[Any, Any, Any]:
    """Return ``(caption, condition, block)`` from a Ren'Py menu item tuple."""
    caption = item[0]
    rest = list(item[1:])
    condition = None
    block: Any = []
    for value in rest:
        if isinstance(value, list):
            block = value
        elif condition is None:
            condition = value
    return caption, condition, block


def decompile_file(
    path: str, *, header: str | None = None
) -> tuple[str, dict[str, int]]:
    """Return ``(source_text, unsupported_counts)`` for one ``.rpyc``."""
    target = decode_script(path)
    lines: list[str] = []
    if header:
        lines.append(header)
        lines.append("")
    state = _EmitState()
    for node in target.statements:
        emit_node(node, 0, lines, state)
    text = "\n".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    counts = dict(state.unsupported)
    for kind, count in target.unsupported.items():
        counts.setdefault(kind, count)
    return text, counts


# --------------------------------------------------------------------------
# Directory helpers
# --------------------------------------------------------------------------


def find_scripts(root: str) -> list[str]:
    """Every ``.rpyc`` under *root*, sorted, with a stable relative ordering."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith(".rpyc"):
                found.append(os.path.join(dirpath, name))
    return found


def find_sources(root: str) -> list[str]:
    """Every ``.rpy`` under *root* (neither ``.rpym`` nor ``.rpyc``).

    This is what a directory of freshly decompiled output looks like, so the
    checks that work on source text use it rather than :func:`find_scripts`.
    """
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith(".rpy"):
                found.append(os.path.join(dirpath, name))
    return found


def is_compiled_only(root: str) -> bool:
    """True when *root* holds ``.rpyc`` files but no ``.rpy`` sources."""
    has_rpyc = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in filenames:
            if name.endswith(".rpy"):
                return False
            if name.endswith(".rpyc"):
                has_rpyc = True
    return has_rpyc


@dataclass
class DecompileResult:
    """Outcome of :func:`decompile_dir`."""

    root: str
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    unsupported: dict[str, int] = field(default_factory=dict)
    written_bytes: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.written) and not self.failed

    failed: dict[str, str] = field(default_factory=dict)

    def text(self) -> str:
        lines = [
            f"{self.root}: {len(self.written)} file(s) decompiled, "
            f"{self.written_bytes / 1024 / 1024:.1f} MiB"
        ]
        if self.skipped:
            lines.append(f"  skipped (source already exists): {len(self.skipped)}")
        if self.failed:
            lines.append(f"  failed: {len(self.failed)}")
            for path, reason in sorted(self.failed.items()):
                lines.append(f"    {path}: {reason}")
        if self.unsupported:
            lines.append("  not representable in source (written as # TODO):")
            for kind, count in sorted(self.unsupported.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {count:6d}  {kind}")
        return "\n".join(lines)


def decompile_dir(
    root: str,
    dest: str | None = None,
    *,
    overwrite: bool = False,
    pattern: str = "*.rpyc",
    backup: bool = False,
    dry_run: bool = False,
) -> DecompileResult:
    """Decompile every ``.rpyc`` under *root* into matching ``.rpy`` files.

    *dest* defaults to *root*. Existing ``.rpy`` files are left alone unless
    *overwrite* is given: a running game keeps only compiled files, so an
    existing source is by definition newer or hand-edited and blowing it away
    would destroy work.

    *dry_run* still decodes every file (so unsupported nodes and real errors
    are reported) but writes nothing.
    """
    if not os.path.isdir(root):
        raise ToolkitError(f"{root}: not a directory")
    out_root = dest or root
    result = DecompileResult(root=os.path.abspath(root))
    for path in find_scripts(root):
        if not fnmatch.fnmatch(os.path.basename(path), pattern):
            continue
        target = os.path.join(
            out_root, os.path.relpath(path, root)[: -len(".rpyc")] + ".rpy"
        )
        if os.path.exists(target) and not overwrite:
            result.skipped.append(target)
            continue
        try:
            text, counts = decompile_file(
                path,
                header=(
                    "# Decompiled by renpy-toolkit from "
                    + os.path.relpath(path, root).replace("\\", "/")
                    + "."
                ),
            )
        except (RpycError, OSError) as exc:
            result.failed[path] = str(exc)
            continue
        for kind, count in counts.items():
            result.unsupported[kind] = result.unsupported.get(kind, 0) + count
        data = text.encode("utf-8")
        result.written.append(target)
        result.written_bytes += len(data)
        if dry_run:
            continue
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        if backup and os.path.exists(target):
            with open(target, "rb") as src, open(target + ".bak", "wb") as dst:
                dst.write(src.read())
        with open(target, "wb") as handle:
            handle.write(data)
    return result
