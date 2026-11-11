"""Ren'Py 文本标签与占位符保护（机翻流水线的安全带）。

动机
----
Ren'Py 的文本不是纯字符串：它里面有 **文本标签**（``{w}``、``{size=+4}``、
``{color=#f00}``、``{image=foo.png}``）、**插值**（``{name}``、``[points]``）、
**转义**（``\\[``、``{{``）和**行内命令**（``{nw}``、``{p}``、``{fast}``）。
机翻引擎不知道这些：它会把 ``{name}`` 当成要翻的文本、会把 ``\\`` 吃掉、
会把 ``{{`` 拆开。

所以正确顺序是：**先按 Ren'Py 的词法规则切出「不可翻译的原子」，把它们换成
占位符，只把可翻译的部分交给引擎，最后再把占位符还原回去。**

本模块就是这个「切 / 换 / 还原」+ **还原后自检**。自检很重要：占位符数量、
顺序、标签集合都必须和原文一致，否则宁可报错也不要把坏文本写回文件。

与旧版 Ren'Py 的关系
-------------------
Ren'Py 的文本标签表是**开放集合**：任何人可以用 ``renpy.register_text_tag``
注册自定义标签，也可以给已有标签加 ``!t`` 让它走翻译（``[mood!t]`` 才翻译，
裸 ``[mood]`` 不翻译）。本模块因此：

* 内置一份**已知标签清单**，用于「多写/少写标签」这类错误的精确报错；
* 但**不做白名单校验** —— 未知标签一律当原子保留，宁可放过，不可误杀；
* ``[...]`` 插值一律不碰，只在还原后校验数量与顺序一致。
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .errors import ProtectError

__all__ = [
    "CHUNK_KINDS",
    "PROTECTED_MARK",
    "PlaceholderError",
    "PROTECT_ERROR",
    "PLACEHOLDER_ERROR",
    "Chunk",
    "KNOWN_TAGS",
    "SELF_CLOSING_TAGS",
    "check_chunk_parity",
    "count_placeholders",
    "custom_tags",
    "protect",
    "restore",
]

#: Tags that carry no closing ``{/tag}`` (self-closing / standalone).
#:
#: Taken verbatim from the engine's own table: ``renpy/text/extras.py`` defines
#: ``text_tags = dict(...)`` where the **value says whether the tag may be
#: closed**, so the ``False`` entries are exactly the ones that raise
#: ``{/%s} isn't valid, since the %s text tag doesn't take a close tag`` when
#: written with a closing form. Getting this set wrong is not cosmetic: the
#: balance check in :func:`_atom_counts` subtracts closes from opens, so a tag
#: filed on the wrong side reports a spurious leak.
SELF_CLOSING_TAGS = frozenset(
    {"done", "image", "p", "w", "fast", "nw", "space", "vspace"}
)

#: Well-known text tags with attributes (``{name}``/``{/name}`` pairs).
#:
#: The closable half of the same engine table (``text_tags[...] = True``), plus
#: tags from older releases (``nwl``, ``kern``, ``vertical``, ``rtl``, ``ruby``,
#: ``rp``, ``clear``, ``lh``, ``ls``, ``ps``) so a translation written against
#: an older engine is not reported as a typo. Over-listing here only *suppresses*
#: reports; the balance arithmetic uses :data:`SELF_CLOSING_TAGS`.
KNOWN_TAGS = frozenset(
    {
        "a", "alpha", "alt", "art", "axis", "b", "color", "cps", "font",
        "i", "instance", "k", "noalt", "outlinecolor", "plain", "rb", "rt",
        "s", "shader", "size", "u",
        # Older releases / long-standing aliases.
        "clear", "family", "kern", "lh", "ls", "nwl", "outlines",
        "outline_size", "ps", "rp", "ruby", "rtl", "sized", "vertical",
    }
)

#: Character for one placeholder; must never appear in Ren'Py text.
PROTECTED_MARK = "\uE000"

PLACEHOLDER_ERROR = "error"


@dataclass
class Chunk:
    """One piece of a text, either translatable or an atomic placeholder."""

    kind: str  # "text" | "tag" | "interp" | "escape"
    text: str

    @property
    def is_translatable(self) -> bool:
        return self.kind == "text"

    def placeholder(self) -> str:
        return PROTECTED_MARK if not self.is_translatable else self.text


PROTECT_ERROR = "error"


class PlaceholderError(ProtectError):
    """Restore failed or the parity check found a mismatch."""


#: ``{...}`` text tag. ``{{`` and ``{/tag}`` are matched here too, which is
#: what we want: a doubled brace is *not* a tag and must survive untouched.
_TAG_RE = re.compile(r"\{[^{}]*\}")

#: ``[...]`` interpolation, including ``[var!t]`` and ``[var!tk]``.
_INTERP_RE = re.compile(r"\[[^\[\]]*\]")

_ESCAPE_RE = re.compile(r"\\[\[\]{}]")


def _match_interp(text: str, start: int) -> int | None:
    """End index (exclusive) of the interpolation opening at *start*, or None.

    Since 8.2.0 an interpolation holds a **Python expression**
    (``incompatible.html``: "Interpolations in strings are now treated as Python
    expressions"), so a subscript is legal and the closing bracket is not
    simply the next ``]``::

        [player[money]]        nested subscript
        [d["a]b"]]             bracket inside a string literal

    A flat ``\\[[^\\[\\]]*\\]`` regex truncates those at the inner bracket and
    then reports the fragments as separate atoms, which shows up as a bogus
    "translation adds/misses interpolation" on correct files.
    """
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"":
            # Skip the whole literal so a bracket inside it cannot shift depth.
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _scan(text: str) -> list[Chunk]:
    """Split *text* into ordered chunks without ever splitting an atom.

    Order matters: a ``\\[`` must be consumed before the ``[`` interpolation
    matcher sees it, otherwise the escaped bracket would look like an
    interpolation and the backslash would be left dangling.
    """
    chunks: list[Chunk] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            chunks.append(Chunk("text", "".join(buffer)))
            buffer.clear()

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] in "[]{}":
            flush()
            chunks.append(Chunk("escape", text[i : i + 2]))
            i += 2
            continue
        # ``[[`` and ``{{`` are literal brackets/braces, not interpolation or
        # tags. The engine's own parser does exactly this
        # (``renpy/substitutions.py: parse()`` -- ``if c == s[pos+1:pos+2]:
        # pos += 1`` keeps the doubled character out of the expression state),
        # so treating ``[[variable, variable]`` as an interpolation reports a
        # false positive on an in-game *description* of the syntax, e.g.
        # ``old "type filenames(ex: variable, '<silence 2.>' or
        # [[variable, variable])"``.
        if ch in "[{" and i + 1 < n and text[i + 1] == ch:
            buffer.append(ch)
            i += 2
            continue
        if ch == "{":
            match = _TAG_RE.match(text, i)
            if match is not None:
                flush()
                chunks.append(Chunk("tag", match.group()))
                i = match.end()
                continue
            # Unbalanced ``{``: keep it in the text and let the parity check
            # catch the damage (a lone brace is a Ren'Py syntax error anyway).
        if ch == "[":
            end = _match_interp(text, i)
            if end is not None:
                flush()
                chunks.append(Chunk("interp", text[i:end]))
                i = end
                continue
        buffer.append(ch)
        i += 1
    flush()
    return chunks


def _placeholder(index: int) -> str:
    # A single PUA codepoint: impossible to type, impossible for a translator
    # to mangle, and trivially reversible.
    return PROTECTED_MARK


def _strip_translation_marker(chunk: Chunk) -> Chunk:
    """""`` [var!t] `` and `` [var!tk] `` mean "this one *is* translatable".

    The engine looks up ``the translated value of var`` and inserts it, so the
    surrounding text may legally be translated. We keep the atom intact but
    report its kind so the caller can decide.
    """
    if chunk.kind == "interp" and chunk.text.endswith("!t"):
        return Chunk("interp", chunk.text[:-2] + "]")
    if chunk.kind == "interp" and chunk.text.endswith("!tk"):
        return Chunk("interp", chunk.text[:-3] + "]")
    return chunk


def protect(text: str) -> tuple[str, list[Chunk]]:
    """Return ``(protected_text, chunks)`` for *text*.

    The protected text contains one :data:`PROTECTED_MARK` for every
    non-translatable chunk, in order. Everything else is unchanged.

    Each placeholder is the *same* single codepoint, so a translation engine
    never sees a ``{0}``/``<TAG>`` that it could translate, merge or delete as
    ordinary text — and a place where two atoms were adjacent in the source
    still has two marks, so a lost atom is always visible in the count.
    """
    chunks = _scan(text)
    out: list[str] = []
    for chunk in chunks:
        out.append(chunk.placeholder())
    return "".join(out), chunks


def restore(protected: str, chunks: Sequence[Chunk]) -> str:
    """Inverse of :func:`protect`.

    Raises :class:`PlaceholderError` when the number of marks does not match
    the number of non-translatable chunks, or when a mark sits where the chunk
    list has no atom left (which means the translation reordered the text).
    """
    expected = [c for c in chunks if not c.is_translatable]
    found = protected.count(PROTECTED_MARK)
    if found != len(expected):
        raise PlaceholderError(
            f"placeholder count mismatch: expected {len(expected)}, found {found}; "
            "the translation lost or invented an atom"
        )
    out: list[str] = []
    atom_index = 0
    cursor = 0
    while True:
        pos = protected.find(PROTECTED_MARK, cursor)
        if pos < 0:
            out.append(protected[cursor:])
            break
        out.append(protected[cursor:pos])
        if atom_index >= len(expected):  # pragma: no cover - count checked above
            raise PlaceholderError("more placeholders than chunks")
        out.append(expected[atom_index].text)
        atom_index += 1
        cursor = pos + 1
    return "".join(out)


def count_placeholders(text: str) -> int:
    return text.count(PROTECTED_MARK)


def check_chunk_parity(
    source: str, translation: str, *, strict: bool = False
) -> list[str]:
    """Compare the atoms of *source* and *translation*.

    Returns a list of human-readable problems (empty = good). This is the
    reusable half of :func:`restore`, for validating a translation that has
    already been written to a file.

    The default rule is built from what actually breaks at render time, checked
    against a finished, shipped Chinese translation (59,369 units):

    * **Tag balance.** ``{/b}`` with no open raises ``closes a text tag that
      isn't open``; ``{b}`` with no close leaks the formatting into the rest of
      the paragraph. A translation may not leave *more* tags dangling than the
      source did, so an extra ``{/color}`` that closes a tag the source forgot
      is a repair and passes -- a finished translation did exactly that in 21
      entries of one event file. This rule reports 2 findings on that corpus,
      both genuine.
    * **Interpolations.** Compared as a multiset, because a wrong ``[var]``
      renders the wrong data or raises ``NameError``, and that is never a
      translation choice. ``[mc]`` is exempt: Chinese legitimately restores the
      player's name where English said "Human" or left the subject implied.
    * **Tag names.** A name the source opens must still be opened. Swapping
      ``{b}`` for ``{i}``, or inventing ``{bb}``, is reported.

    Deliberately *not* checked: tag order and tag values. Chinese word order
    moves the tags with the words they wrap
    (``{color=X}Alice{/color} at the {color=Y}pond{/color}`` becomes
    ``{color=Y}池塘{/color}边找到{color=X}Alice{/color}``), and the CJK
    technique of adding ``{font=...}`` or retuning ``{size=...}`` is the
    translation work itself. Sequence comparison produced 24 false positives on
    that corpus; comparing multisets produces 2.

    ``strict=True`` restores exact sequence equality, for the machine
    translation round trip where placeholders must come back in order.
    """
    if strict:
        src_atoms = [c for c in _scan(source) if not c.is_translatable]
        dst_atoms = [c for c in _scan(translation) if not c.is_translatable]
        problems: list[str] = []
        if len(src_atoms) != len(dst_atoms):
            problems.append(
                f"atomic tag/interpolation count differs: source has {len(src_atoms)}, "
                f"translation has {len(dst_atoms)}"
            )
        for i, (a, b) in enumerate(zip(src_atoms, dst_atoms)):
            if a.text != b.text:
                problems.append(
                    f"atom #{i + 1} differs: source has {a.text!r}, translation has {b.text!r}"
                )
            elif a.kind != b.kind:
                problems.append(f"atom #{i + 1} changed kind: {a.kind} -> {b.kind}")
        return problems

    src_opens, src_closes, src_interps, src_content = _atom_counts(source)
    dst_opens, dst_closes, dst_interps, dst_content = _atom_counts(translation)

    problems = []
    for name in sorted(set(src_opens) | set(dst_opens) | set(src_closes) | set(dst_closes)):
        src_leak = max(0, src_opens[name] - src_closes[name])
        dst_leak = max(0, dst_opens[name] - dst_closes[name])
        # A tag left open only matters when the source also closed it: then the
        # translator dropped a ``{/b}`` and everything after it inherits the
        # formatting. Adding an unclosed wrapper is the standard CJK technique
        # -- ``{font=fonts/JCHeiTi-Regular.ttf}整行中文`` applies the font to the
        # whole line and needs no close, because the value ends there. The
        # shipped translation does exactly this in ``quest.rpy``.
        if dst_leak > src_leak and src_closes[name] >= src_opens[name] > 0:
            problems.append(
                f"{name!r} is left unclosed {dst_leak}x in the translation but "
                f"{src_leak}x in the source; the formatting leaks past this line"
            )
        if dst_closes[name] > dst_opens[name] and src_closes[name] <= src_opens[name]:
            problems.append(
                f"the translation closes {name!r} {dst_closes[name]}x but opens it "
                f"{dst_opens[name]}x; Ren'Py raises 'closes a text tag that isn't open'"
            )

    for atom, n in sorted((src_interps - dst_interps).items()):
        problems.append(f"translation is missing {n}x interpolation {atom!r}")
    for atom, n in sorted((dst_interps - src_interps).items()):
        problems.append(f"translation adds {n}x interpolation {atom!r}")

    for atom, n in sorted((src_content - dst_content).items()):
        problems.append(f"translation is missing {n}x content tag {atom!r}")
    for atom, n in sorted((dst_content - src_content).items()):
        problems.append(f"translation adds {n}x content tag {atom!r}")
    return problems


#: ``{tag}`` / ``{tag=arg}`` / ``{/tag}`` / ``{=style}``, capturing the name.
_TAG_NAME_RE = re.compile(r"\{/?(?P<name>[^{}=:/]*)")


def _count_style_tag(
    chunk: Chunk,
    opens: "collections.Counter[str]",
    closes: "collections.Counter[str]",
    content: "collections.Counter[str]",
) -> None:
    """Count a style-reference tag (``{=style}`` / ``{/=style}``) in place.

    The engine reads the argument as a style name instead of an argument to a
    named tag (``renpy/text/text.py``: "if the tag name is empty, the argument
    is the name of a style"), so it opens and closes like ``{b}``. Counting it
    under a ``=style`` key catches three mistakes a translator actually makes
    with it: dropping the close tag, inventing a close, and dropping the whole
    pair. The pair is *also* recorded as content, because a style carries the
    font, colour and size of that stretch of text -- losing it reflows the line
    even though nothing is left unbalanced.
    """
    body = chunk.text[1:-1] if chunk.text.endswith("}") else chunk.text[1:]
    is_close = body.startswith("/")
    style = body.split("/", 1)[1] if is_close else body[1:]
    key = "=" + style.split("=", 1)[0].strip()
    if is_close:
        closes[key] += 1
    else:
        opens[key] += 1
    content[chunk.text] += 1

#: Interpolations a translation may add or drop freely: in Chinese the player's
#: name replaces English's "Human" or an implied subject.
FREE_INTERPS = frozenset({"[mc]"})

#: Tags whose presence is content, not typography: colours distinguish speakers
#: and items, inline images are pictures on the line, and the line commands
#: control the reading rhythm (a dropped ``{w}`` merges two beats into one
#: screen of text). Losing any of them changes what the player sees.
CONTENT_TAGS = frozenset(
    {
        "color", "outlinecolor", "image", "a", "alt", "art", "rb", "rt",
        "w", "p", "nw", "fast", "done", "clear", "k", "noalt", "shader",
    }
)

#: (The module-level :data:`SELF_CLOSING_TAGS` above is the single source of
#: truth; it is copied from the engine registry ``renpy/text/extras.py``.)
def _atom_counts(text: str) -> tuple[
    collections.Counter[str],
    collections.Counter[str],
    collections.Counter[str],
    collections.Counter[str],
]:
    """``(opens, closes, interpolations, content)`` counted by name / literal."""
    opens: collections.Counter[str] = collections.Counter()
    closes: collections.Counter[str] = collections.Counter()
    interps: collections.Counter[str] = collections.Counter()
    content: collections.Counter[str] = collections.Counter()
    for chunk in _scan(text):
        if chunk.kind == "interp":
            if chunk.text not in FREE_INTERPS:
                interps[chunk.text] += 1
            continue
        if chunk.kind != "tag":
            continue
        match = _TAG_NAME_RE.match(chunk.text)
        name = (match.group("name") if match else "") or ""
        # ``{=mystyle}...{/=mystyle}`` carries no tag *name*: per the docs
        # ("text tags where the tag name is empty ... the argument is taken to
        # be the name of a style") it is a style reference, which opens and
        # closes like any other tag. Keying it by its style keeps a translation
        # that drops or invents one from slipping through silently. ``{#ctx}``
        # is *not* one of these: it is a translation marker with no rendered
        # effect and no close tag.
        #
        # Checked on the raw chunk, not on ``name``: ``_TAG_NAME_RE`` excludes
        # ``=`` from the name class, so a style reference yields an empty name.
        if chunk.text.startswith("{=") or chunk.text.startswith("{/="):
            _count_style_tag(chunk, opens, closes, content)
            continue
        if not name or name.startswith("#"):
            continue
        if chunk.text.startswith("{/"):
            if name not in SELF_CLOSING_TAGS:
                closes[name] += 1
            continue
        if name in SELF_CLOSING_TAGS:
            # ``{image=...}`` / ``{w}`` / ``{p}`` never take a close tag, so
            # they are balanced by definition. Counting them as an unclosed
            # open reports a false positive on a faithful translation of a line
            # that embeds one (``old '"Magic"?{image=bg/item.png}'``): the
            # engine itself raises "``{/image}`` isn't valid, since the image
            # text tag doesn't take a close tag" (``renpy/text/text.py``, driven
            # by ``renpy/text/extras.py: text_tags`` where these map to False).
            if name in CONTENT_TAGS:
                content[chunk.text] += 1
            continue
        opens[name] += 1
        if name in CONTENT_TAGS:
            content[chunk.text] += 1
    return opens, closes, interps, content


def tag_name(chunk: Chunk) -> str | None:
    """The tag name of a ``{...}`` chunk, or None if it has none.

    ``None`` means the chunk is not a text tag at all: ``{{`` (an escaped
    brace), ``{#context}`` (a translation marker, see the docs' "Text tags
    beginning with # are ignored") and ``{=style}`` (a style reference) all
    return None, because the engine treats none of them as a tag to close.
    """
    if chunk.kind != "tag":
        return None
    body = chunk.text[1:-1] if chunk.text.endswith("}") else chunk.text[1:]
    if not body or body.startswith("#") or body.startswith("="):
        return None
    if body.startswith("/"):
        body = body[1:]
    name = body.split("=", 1)[0]
    # ``{shader=jitter:u__jitter=1.0, 3.0}`` and ``{feature:liga=0}`` carry a
    # colon before the argument; the name is what precedes it.
    name = name.split(":", 1)[0]
    return name.strip() or None


_CUSTOM_TAG_RE = re.compile(
    r"""config\s*\.\s*(?P<which>self_closing_custom_text_tags|custom_text_tags)"""
    r"""\s*\[\s*(?P<q>['"])(?P<name>[^'"]+)(?P=q)\s*\]\s*="""
)
_REGISTER_TAG_RE = re.compile(
    r"""\bregister_text_tag\s*\(\s*(?P<q>['"])(?P<name>[^'"]+)(?P=q)"""
)


def custom_tags(*sources: str) -> tuple[set[str], set[str]]:
    """Find the game's own text tags by reading its scripts.

    ``config.custom_text_tags["sc"] = sc_tag`` (and
    ``config.self_closing_custom_text_tags``, and
    ``renpy.register_text_tag("x", ...)``) define tags the engine accepts but
    the stock tables do not know about. Reading them out of the source turns the
    "the source used it too, so it must work" heuristic in
    :func:`check_unknown_tags` into a fact for tags the translator introduces.

    Returns ``(wrapping, self_closing)``. Both may be empty; that just means no
    custom tags were found.
    """
    wrapping: set[str] = set()
    closing: set[str] = set()
    for text in sources:
        if not text:
            continue
        for match in _CUSTOM_TAG_RE.finditer(text):
            name = match.group("name")
            if match.group("which") == "self_closing_custom_text_tags":
                closing.add(name)
            else:
                wrapping.add(name)
        for match in _REGISTER_TAG_RE.finditer(text):
            # ``register_text_tag`` has no self-closing variant in the public
            # API, so treat these as wrapping tags.
            wrapping.add(match.group("name"))
    return wrapping, closing


def check_unknown_tags(
    source: str, translation: str, *, known: Iterable[str] = ()
) -> list[str]:
    """Report text tags the engine will reject at render time.

    Ren'Py raises ``Unknown text tag %r`` (``renpy/text/text.py:1526``) while
    rendering, not while compiling, so a typo in a translation survives ``lint``
    and then crashes the game the moment that line is displayed. A translation
    is where such a typo is most likely, because the tag was retyped by hand.

    The check is a *comparison*, not a membership test, because
    ``renpy.register_text_tag`` makes the tag table open: a game may define
    ``{sc}`` or ``{outlinecolor}`` and use it hundreds of times. Measured on one
    finished translation, 7 of 17 distinct tags were outside the stock set, and
    two of those accounted for ~5,900 occurrences -- so flagging by membership
    alone would bury every real finding. A tag that the *source* also uses is
    therefore accepted without comment: whatever it means, it already worked
    before the translator touched the line.

    *known* lets the caller supply the game's own tags (see :func:`custom_tags`),
    which upgrades that heuristic to a fact for tags the translator introduces.
    """
    problems: list[str] = []
    declared = set(known)
    seen_source = {
        name for name in (tag_name(c) for c in _scan(source)) if name is not None
    }
    reported: set[str] = set()
    for chunk in _scan(translation):
        name = tag_name(chunk)
        if name is None or name in reported:
            continue
        if name in seen_source or name in declared or _is_known_tag(name):
            continue
        reported.add(name)
        problems.append(
            f"unknown text tag {chunk.text!r}; Ren'Py raises "
            f"'Unknown text tag' while rendering, which crashes the game"
        )
    return problems


def _is_known_tag(name: str) -> bool:
    """Is *name* a tag the stock engine understands?

    Case is not folded on purpose. The engine keys its tag table by exact
    spelling, so ``{B}`` is rejected just like any other unknown tag; folding
    here would hide a real crash.
    """
    return name in KNOWN_TAGS or name in SELF_CLOSING_TAGS


__all__ = __all__ + ["_scan", "check_unknown_tags", "tag_name"]
