"""Ren'Py 翻译文件（``game/tl/<language>/*.rpy``）解析与回填。

用途
----
把 ``translate <lang> <identifier>:`` 块、``translate <lang> strings:`` 块、
``translate <lang> python:`` / ``style`` / ``early`` 块解析成结构化对象，
并在不破坏原文件的前提下替换其中某条译文。

依赖：仅标准库。

为什么这么设计
--------------
Ren'Py 的 translate 文件是 **缩进敏感 + 保留原文注释** 的纯文本，而不是可
往返解析的 AST：

* 块头上一行 ``# game/script.rpy:95`` 是给人看的源位置，Ren'Py 忽略它；
* 块体里 ``    # e "Thank you."`` 是被注释掉的原文，靠人眼比对，Ren'Py 也忽略；
* 真正生效的只有没被注释的行（``e ""``、``$ x = 1``、``pass``）；
* ``translate <lang> python:`` 里的缩进 **是有意义的 Python 代码缩进**
  （块内两行 Python 的相对缩进决定块结构），所以「整块 dedent/indent」这种
  改写方式会改变语义——必须只改目标行，绝不重排。

因此本模块的策略是：

1. **逐文件字节级读取**，记录每个文件的 BOM、行尾（逐行）、结尾是否有换行，
   写回时原样复原，只替换被改的那几行（``TlFile.render`` 只拼行列表）。
2. **每一行都记住它的原始文本与行尾**（``_RawLine``），任何未被主动修改的行
   在输出里逐字节相同。
3. 解析是「宽容的」：无法识别的行保留为 ``raw``，不抛异常；只有 **结构上
   有歧义到无法确定块体范围** 时才抛 :class:`TLParseError`。
4. 回写是「保守的」：要求目标行满足精确的格式假设（见 :func:`replace_new`），
   否则抛 :class:`UnsafeWriteError` 而不是猜。

已核实的格式事实（见 ``docs/official-cli-and-translation.md`` §5、§7）
--------------------------------------------------------------------
``translate <lang> <identifier>:`` 块体::

    # game/script.rpy:95
    translate piglatin start_636ae3f5:

        # e "Thank you for taking a look at the Ren'Py translation framework."
        e ""

块体里可以有多句（把一句拆成多行）、``pass``（删除该句），或插入 ``$`` 语句。
非 1:1 时 **注释原文与生效语句数量不相等**，这是合法的，不能当成错误。

``translate <lang> strings:`` 块体::

    translate piglatin strings:

        # game/script.rpy:12
        old "Eileen"
        new "Eileenway"

也可以出现空 ``new ""``。

本模块对「注释原文 ↔ 生效语句」的对齐规则（**假设**，均来自官方生成器的
实际输出形态）::

    A1 注释行形如 ``<indent># <say 语句原文>``，其中 say 语句原文以「角色名
       或引号」开头，例如 ``# e "..."``、``# "..."``、``# nvl_narrator "..."``。
       Ren'Py 生成器不会在注释里写 ``# e ""`` 之外的修饰（id/with 等会保留）。
    A2 生效 say 行形如 ``<indent><who> "<text>"``，其中 who 可以省略（旁白），
       可以带 store 前缀 ``store.e``、可以带属性 ``e happy "..."``。
    A3 一个块内注释行的顺序与生效 say 行的顺序一致；数量可以不等。
       对齐时按顺序消费：每遇到一行注释就把「下一条尚未配对的生效 say 行」
       与之配对。若注释比 say 多，多出来的注释记为 unpaired（只记录不报错）。
    A4 ``pass`` 出现在块体里即视为「该块删除翻译」，并记录 ``deleted=True``。
    A5 块体结束于下一个「缩进 <= 块头缩进」的非空行（空行与纯注释行不影响
       归属，但会先归入当前块再重新判断）。

预期行为示例
------------
- ``blocks = parse_tl_file("script.rpy")``；``blocks[0].kind == "translate"``、
  ``blocks[0].identifier == "start_636ae3f5"``、``blocks[0].say_lines[0].new``
  是块体里 ``e ""`` 的 ``""``，而 ``blocks[0].say_lines[0].old`` 是注释里的原文。
- ``replace_new(path, "start_636ae3f5", "你好", dry_run=True)`` 返回新文本，
  且原文的 CRLF / BOM / 未改行逐字节不变；同一 identifier 出现两次时抛
  :class:`UnsafeWriteError`（需用 ``index=`` 指定）。
- ``find_missing(tl_dir)`` 返回 ``new == ""`` 且非 ``pass`` 的条目列表。
"""

from __future__ import annotations

import codecs
import os
import re
from dataclasses import dataclass, field
from typing import Iterator, Literal, Sequence

from .errors import TLError, TLParseError, UnsafeWriteError

__all__ = [
    "BlockKind",
    "FileFormat",
    "SayLine",
    "StringPair",
    "TLError",
    "TLParseError",
    "TlBlock",
    "TlFile",
    "apply_new_text",
    "dump_tl_file",
    "find_missing",
    "format_value",
    "iter_tl_files",
    "make_translate_block",
    "parse_tl_file",
    "parse_tl_text",
    "replace_new",
    "replace_string",
    "set_string_new",
    "unescape_value",
]

BlockKind = Literal["translate", "strings", "python", "style", "early", "unknown"]

#: Encodings/BOM/newline combinations we can round-trip.
FileFormat = Literal["utf-8", "utf-8-sig"]

_LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$")

# ``translate <lang> <rest>:`` -- lang may be ``None`` for the main language.
_TRANSLATE_HEAD_RE = re.compile(
    r"^(?P<indent>[ \t]*)translate[ \t]+(?P<lang>[A-Za-z_][A-Za-z0-9_\.]*|None)"
    r"[ \t]+(?P<rest>.+?)[ \t]*:[ \t]*(?:\#.*)?$"
)
# A bare ``translate <identifier>:`` (the ``None`` language written without the
# language word).  Ren'Py's own generator always writes the language, but
# hand-written files use this form; identifier must look like ``label_hash``.
_TRANSLATE_HEAD_NOLANG_RE = re.compile(
    r"^(?P<indent>[ \t]*)translate[ \t]+(?P<rest>[A-Za-z_][A-Za-z0-9_]*_[0-9A-Za-z]+)"
    r"[ \t]*:[ \t]*(?:\#.*)?$"
)

_OLD_RE = re.compile(r"^(?P<indent>[ \t]*)old(?P<sp>[ \t]+)(?P<value>.*?)[ \t]*$")
_NEW_RE = re.compile(r"^(?P<indent>[ \t]*)new(?P<sp>[ \t]+)(?P<value>.*?)[ \t]*$")

#: Characters Ren'Py allows in a say-statement's text. A comment line whose
#: content does not start with one of these is not a mirrored source line (it
#: is a human comment such as ``# TODO: 术语待统一``).
_SAY_TEXT_START = ('"', "\u201c", "\u201d")


# ``TLError`` lives in :mod:`errors` so that ``except ToolkitError`` catches it
# from any module. It is re-exported here (see the import block above) because
# older call sites write ``from .tl import TLError``.


@dataclass
class _RawLine:
    """One physical line, keeping its exact original bytes-as-text.

    ``text`` never contains the newline; ``eol`` is the terminator that followed
    it (``"\\n"`` / ``"\\r\\n"`` / ``"\\r"`` / ``""`` for a last line without a
    trailing newline).
    """

    text: str
    eol: str


@dataclass
class SayLine:
    """A live (non-commented) statement inside a ``translate`` block.

    ``who`` / ``text`` are only filled for lines that parse as a say statement;
    ``pass`` is set for the ``pass`` line that deletes the translation. Python
    (``$ ...``), ``voice``, ``nvl`` etc. stay as ``raw``.
    """

    index: int
    lineno: int
    indent: str
    who: str
    text: str
    rest: str
    raw: str
    is_say: bool = True


@dataclass
class StringPair:
    """One ``old``/``new`` pair inside a ``translate <lang> strings:`` block."""

    lineno: int
    old_lineno: int
    new_lineno: int | None
    indent: str
    old: str
    new: str | None
    old_raw: str
    new_raw: str | None
    context: str | None = None
    """``{#context}`` value extracted from ``old`` (None when absent)."""


@dataclass
class TlBlock:
    """One ``translate``-family block."""

    kind: BlockKind
    language: str | None
    identifier: str | None
    lineno: int = 0
    """1-based line number of the block header."""

    end_lineno: int = 0
    """1-based line number of the last line of the block body."""

    header: str = ""
    source_comment: str | None = None
    """Content of a ``# game/script.rpy:95`` comment directly above the header."""

    source_file: str | None = None
    source_line: int | None = None
    comments: list[str] = field(default_factory=list)
    """Every full-line comment inside the body, in order (text without the '#')."""

    leading_comments: list[str] = field(default_factory=list)
    """Free-standing comment lines that sit *between* the previous block and
    this one (verbatim, '#' included).

    A hand-maintained TL file groups blocks under banners such as
    ``# === 补译：运行期文案 ===``.  Those lines belong to no block body, so
    without this field a rebuild silently deletes them.  They travel with the
    block they introduce, which keeps a banner attached to its first entry even
    after sorting.
    """

    say_lines: list[SayLine] = field(default_factory=list)
    old_comments: list[str] = field(default_factory=list)
    """Mirrored source lines (``# e "..."`` -> ``e "..."``), in order."""

    strings: list[StringPair] = field(default_factory=list)
    body_raw: str = ""
    """Verbatim body text (dedented by the block header's indent)."""

    indent: str = ""
    deleted: bool = False
    """A ``pass`` line exists in the body: the translation is dropped."""

    problems: list[str] = field(default_factory=list)
    unpaired_comments: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ views
    @property
    def is_empty(self) -> bool:
        """True when every live say line has an empty text and no strings exist."""
        if self.strings:
            return all((p.new or "") == "" for p in self.strings)
        if not self.say_lines:
            return not self.deleted
        return all(line.text == "" for line in self.say_lines)

    @property
    def old_text(self) -> str:
        """Best-effort single source text (first mirrored comment / first old)."""
        for pair in self.strings:
            return pair.old
        if self.say_lines:
            first = self.say_lines[0]
            if first.is_say:
                return self._source_for(first)
        if self.old_comments:
            return self.old_comments[0]
        return ""

    @property
    def new_text(self) -> str:
        """Best-effort single translation (all say texts joined by newline)."""
        if self.strings:
            return "\n".join(p.new or "" for p in self.strings)
        if not self.say_lines:
            return ""
        return "\n".join(line.text for line in self.say_lines if line.is_say)

    def _source_for(self, line: SayLine) -> str:
        """Return the mirrored source line paired with *line*, if any."""
        aligned = self.aligned()
        for src, dst in aligned:
            if dst is line:
                return src
        return ""

    def aligned(self) -> list[tuple[str, SayLine]]:
        """Pair mirrored comments with live say lines (rule A3 in the docstring).

        The pairing is computed fresh each call (it is cheap) but the list of
        leftover comments is only *appended to*, so repeated calls report each
        unpaired comment once.
        """
        pairs: list[tuple[str, SayLine]] = []
        pending = [line for line in self.say_lines if line.is_say]
        cursor = 0
        for comment in self.old_comments:
            if cursor >= len(pending):
                if comment not in self.unpaired_comments:
                    self.unpaired_comments.append(comment)
                continue
            pairs.append((comment, pending[cursor]))
            cursor += 1
        return pairs

    def sort_key(self) -> tuple[int, str]:
        return (self.lineno, self.identifier or "")


@dataclass
class TlFile:
    """A parsed ``.rpy`` translation file plus everything needed to rewrite it."""

    path: str
    lines: list[_RawLine] = field(default_factory=list)
    encoding: FileFormat = "utf-8"
    has_bom: bool = False
    blocks: list[TlBlock] = field(default_factory=list)
    other_statements: list[str] = field(default_factory=list)
    """Non-comment, non-blank top-level lines outside any translate block."""

    preamble: list[str] = field(default_factory=list)
    """Verbatim lines before the first block (file header comments, BOM strip,
    blank separator).  ``_parse_into`` skips leading comments, so without this
    every rebuild would silently delete a hand-written file header."""

    _orig_lines: list[_RawLine] = field(default_factory=list, repr=False)
    _dirty: bool = False

    # --------------------------------------------------------------- preamble
    def preamble_cap(self) -> int:
        """How many leading lines belong to the file header.

        A ``# game/script.rpy:95`` line that sits *directly* above the first
        ``translate`` header is that block's mirrored source comment, not a
        hand-written file header: ``_fill_source_comment`` reads it back off
        the lines above the header, and re-emitting it once from the block and
        once from ``preamble`` would duplicate the comment on every sort.

        Returns the number of preamble lines to keep (0..len(preamble)).
        """
        if not self.blocks:
            return len(self.preamble)
        first = self.blocks[0]
        if not first.source_comment:
            return len(self.preamble)
        probe = len(self.preamble) - 1
        while probe >= 0 and _is_blank(self.preamble[probe]):
            probe -= 1
        if probe < 0:
            return len(self.preamble)
        if self.preamble[probe].strip() != first.source_comment:
            return len(self.preamble)
        # Keep everything above the comment; the comment itself is re-emitted
        # from ``block.source_comment``.
        return probe

    # -------------------------------------------------------------- rendering
    def render(self) -> str:
        """Serialize back to text. Unmodified lines are byte-identical."""
        return "".join(line.text + line.eol for line in self.lines)

    def to_bytes(self) -> bytes:
        text = self.render()
        if self.has_bom:
            return codecs.BOM_UTF8 + text.encode("utf-8")
        return text.encode("utf-8")

    def is_dirty(self) -> bool:
        if self._dirty or len(self.lines) != len(self._orig_lines):
            return True
        for before, after in zip(self._orig_lines, self.lines):
            if before.text != after.text or before.eol != after.eol:
                return True
        return False

    def write(self, target: str | None = None, *, backup: bool = True) -> str:
        """Write the file back.

        Returns the path written. Skips the write when the bytes on disk are
        already identical (a no-op run never touches the filesystem). The
        comparison is on content, not on an in-memory dirty flag: callers
        routinely parse a rendered string into a *fresh* TlFile and write that,
        and a dirty-flag check silently dropped every such rewrite.

        A ``.bak`` copy of the previous content is created next to the target
        when it exists and ``backup`` is true.
        """
        dest = target or self.path
        data = self.to_bytes()
        rendered = data.decode("utf-8", "replace")

        # Safety net: a rendered file that no longer has the same number of
        # lines, or whose endings mix, must never reach disk.
        if len(split_lines(rendered)) != len(self.lines):
            raise UnsafeWriteError(
                f"{dest}: refusing to write, line count changed "
                f"({len(self.lines)} -> {len(split_lines(rendered))})"
            )
        if "\r\r" in rendered.replace("\r\n", "\n"):
            raise UnsafeWriteError(f"{dest}: refusing to write, line endings look corrupted")

        if os.path.exists(dest):
            with open(dest, "rb") as src:
                previous = src.read()
            if previous == data:
                return dest
            if backup:
                with open(dest + ".bak", "wb") as dst:
                    dst.write(previous)
        with open(dest, "wb") as fh:
            fh.write(data)
        return dest

    # ---------------------------------------------------------------- lookup
    def find(self, identifier: str) -> list[TlBlock]:
        return [b for b in self.blocks if b.identifier == identifier]

    def one(self, identifier: str) -> TlBlock:
        found = self.find(identifier)
        if not found:
            raise KeyError(identifier)
        if len(found) > 1:
            raise UnsafeWriteError(
                f"{self.path}: identifier {identifier!r} appears {len(found)} times; "
                "pass index= to disambiguate"
            )
        return found[0]

    def counts(self) -> dict[str, int]:
        out = {"files": 1, "translate": 0, "strings": 0, "python": 0, "other": 0}
        for block in self.blocks:
            if block.kind in ("translate",):
                out["translate"] += 1
            elif block.kind == "strings":
                out["strings"] += 1
            else:
                out["other"] += 1
        return out


# ---------------------------------------------------------------------------
# value (string literal fragment) helpers
# ---------------------------------------------------------------------------
def format_value(value: str) -> str:
    """Render *value* as the inside of a Ren'Py ``"..."`` literal.

    Ren'Py's own generator writes the Python-escaped form: backslash, tab and
    every non-printable character become escapes, ``"`` becomes ``\\"``, while
    newlines are written as the two characters ``\\n``. Unicode text is kept
    verbatim (files are UTF-8).

    Assumption: the caller passes already-unescaped *text*, not source. Use
    :func:`unescape_value` first when starting from source.
    """
    out: list[str] = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\x%02x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


_SIMPLE_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "\\": "\\",
    '"': '"',
    "'": "'",
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
}


def unescape_value(source: str) -> str:
    """Inverse of :func:`format_value` for the ``\\``-escapes Ren'Py emits.

    Unknown escapes (``\\N`` for ``{/i}``-style tags, ``\\[``) keep their leading
    backslash so no information is lost. ``{`` and ``[`` are *not* unescaped:
    Ren'Py's ``[[`` / ``{{`` doubling happens in the text layer, not here.
    """
    out: list[str] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = source[i + 1]
        if nxt in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[nxt])
            i += 2
        elif nxt == "x" and re.fullmatch(r"[0-9A-Fa-f]{2}", source[i + 2 : i + 4]):
            out.append(chr(int(source[i + 2 : i + 4], 16)))
            i += 4
        elif nxt == "u" and re.fullmatch(r"[0-9A-Fa-f]{4}", source[i + 2 : i + 6]):
            out.append(chr(int(source[i + 2 : i + 6], 16)))
            i += 6
        else:
            out.append(ch)
            out.append(nxt)
            i += 2
    return "".join(out)


def _split_quoted(rest: str) -> tuple[str, str, str] | None:
    """Split ``"text" trailing`` into (raw_body, tail, quote_char).

    Returns None when *rest* does not start with a complete quoted literal.
    The body is raw (escapes untouched).

    A triple quote (``\"\"\"`` / ``'''``) is a *different* literal form, not an
    empty string followed by another string: Ren'Py's ``parse_string`` hands the
    whole fragment to ``eval``, so ``old \"\"\"a\\nb\"\"\"`` is one value that
    spans lines. Callers that can meet a multi-line value must therefore use
    :func:`_scan_multiline_value` instead of this helper.
    """
    rest = rest.lstrip()
    if not rest or rest[0] not in "\"'":
        return None
    quote = rest[0]
    if rest.startswith(quote * 3):
        # Never report a triple quote as a closed empty literal.
        return None
    i = 1
    body_start = 1
    while i < len(rest):
        ch = rest[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return rest[body_start:i], rest[i + 1 :], quote
        i += 1
    return None


#: A ``old``/``new`` line whose value starts a triple-quoted literal that does
#: not close on the same line. The literal may be wrapped in a call such as
#: ``_p(...)`` -- the official docs show ``new _p("""...""")`` for values that
#: should be re-flowed at display time.
_TRIPLE_OPEN_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<keyword>old|new)(?P<sp>[ \t]+)"
    r"(?P<call>(?:_p|__|___|_)[ \t]*\([ \t]*)?(?P<quote>\"\"\"|''')"
)


def _triple_open(line: str) -> str:
    """The triple-quote delimiter that *line* opens, or ``""``.

    Used to keep a block body together while its content is written at column 0.
    """
    match = _TRIPLE_OPEN_RE.match(line)
    return match.group("quote") if match is not None else ""


def _scan_multiline_value(
    lines: Sequence[str], start: int, stop: int, keyword: str
) -> tuple[str, int] | None:
    """Read a triple-quoted ``old``/``new`` value spanning lines.

    Returns ``(unescaped_value, last_line_index)`` -- the index of the line that
    closes the literal -- or None when *start* is not a multi-line triple-quoted
    value. The value is decoded exactly as a single-line one would be, minus the
    ``\\n`` escapes Ren'Py would otherwise need: inside ``\"\"\"`` the engine keeps
    the physical newlines, which is what makes these blocks readable.

    Ren'Py's own ``parse_string`` compiles the fragment with ``eval``, so the
    literal is a real Python string: the newline immediately after the opening
    quotes, and the one before the closing quotes, are part of the value. We
    reproduce that verbatim rather than trimming, because the reference project
    relies on the exact spacing (the devlog card is laid out by it).
    """
    match = _TRIPLE_OPEN_RE.match(lines[start])
    if match is None or match.group("keyword") != keyword:
        return None
    quote = match.group("quote")
    # ``new _p("""...""")`` closes with ``""")`` -- the wrapper's own bracket has
    # to be accepted after the closing quotes, or every such block reports a
    # parse problem and the translation is dropped on the next write.
    trailing = (")", "),") if match.group("call") else ("",)
    head = lines[start]
    body_start = match.end("quote")
    # Everything after the opening quotes on this line.
    first = head[body_start:]
    close = first.rfind(quote)
    if close >= 0 and first[close + 3:].strip() in trailing:
        return unescape_value(first[:close]), start
    parts: list[str] = [first]
    for idx in range(start + 1, stop):
        text = lines[idx]
        close = text.rfind(quote)
        if close >= 0 and text[close + 3:].strip() in trailing:
            parts.append(text[:close])
            return unescape_value("\n".join(parts)), idx
        parts.append(text)
    return None


def _split_say(line: str) -> tuple[str, str, str] | None:
    """Split a say statement into (who, raw_text, tail).

    ``who`` is ``""`` for narration (``"..."``) and may contain attributes
    (``e happy``). Returns None when the line is not a say statement.
    """
    stripped = line.strip()
    if not stripped:
        return None
    first = stripped[0]
    if first in "\"'":
        split = _split_quoted(stripped)
        if split is None:
            return None
        return ("", split[0], split[1])
    if first in _SAY_TEXT_START:
        split = _split_quoted(stripped)
        if split is None:
            return None
        return ("", split[0], split[1])
    # ``who "text"``: find the first quote that is preceded by a space and is
    # not itself escaped.
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if ch == "\\":
            i += 2
            continue
        if ch in "\"'" and i > 0 and stripped[i - 1] in " \t":
            who = stripped[:i].strip()
            if not _looks_like_who(who):
                return None
            split = _split_quoted(stripped[i:])
            if split is None:
                return None
            return (who, split[0], split[1])
        i += 1
    return None


_WHO_NAME_RE = re.compile(r"^[A-Za-z_\u0080-\uffff][\w\u0080-\uffff]*(?:\.[A-Za-z_][\w]*)*$")


def _looks_like_who(who: str) -> bool:
    """True for ``e``, ``store.e``, ``nvl_narrator``, ``e happy`` but not ``$ x``.

    Assumption A2: a say-statement speaker is one or more identifiers separated
    by dots, optionally followed by image attributes (more identifiers), and
    never starts with ``$``/``if``/``voice`` etc.
    """
    if not who:
        return False
    if who[0] in "$#:":
        return False
    for part in who.split():
        if not _WHO_NAME_RE.match(part):
            return False
    return True


_NON_SAY_KEYWORDS = frozenset(
    {
        "voice",
        "nvl",
        "show",
        "scene",
        "hide",
        "with",
        "play",
        "stop",
        "queue",
        "window",
        "pause",
        "if",
        "elif",
        "else",
        "while",
        "for",
        "call",
        "jump",
        "return",
        "python",
        "pass",
        "menu",
        "label",
        "define",
        "default",
        "init",
        "transform",
        "screen",
        "image",
        "style",
        "translate",
        "use",
        "$",
    }
)


# ---------------------------------------------------------------------------
# tokenizing
# ---------------------------------------------------------------------------
def split_lines(text: str) -> list[_RawLine]:
    """Split *text* into lines keeping each line's own terminator."""
    return [_RawLine(m.group().rstrip("\r\n"), m.group()[len(m.group().rstrip("\r\n")) :])
            for m in _LINE_RE.finditer(text)]


def _decode(data: bytes, path: str) -> tuple[str, FileFormat, bool]:
    has_bom = data.startswith(codecs.BOM_UTF8)
    try:
        text = data.decode("utf-8-sig" if has_bom else "utf-8")
    except UnicodeDecodeError as exc:
        raise TLParseError(path, f"not valid UTF-8 ({exc.reason} at byte {exc.start})") from exc
    return text, ("utf-8-sig" if has_bom else "utf-8"), has_bom


def _indent_width(line: str) -> int:
    """Width of the leading whitespace (tab = 8, like Ren'Py's lexer)."""
    width = 0
    for ch in line:
        if ch == " ":
            width += 1
        elif ch == "\t":
            width += 8 - (width % 8)
        else:
            break
    return width


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _is_blank(line: str) -> bool:
    return not line.strip()


def _is_comment(line: str) -> bool:
    return line.lstrip(" \t").startswith("#")


_SOURCE_COMMENT_RE = re.compile(r"^#\s*(?P<file>[^:\n]+?\.(?:rpy|rpym|rpyc)?):(?P<line>\d+)\s*$")


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def parse_tl_text(text: str, path: str = "<memory>") -> TlFile:
    """Parse already-decoded *text*."""
    raw_lines = split_lines(text)
    tl = TlFile(path=path, lines=list(raw_lines), encoding="utf-8", has_bom=False)
    tl._orig_lines = list(raw_lines)
    _parse_into(tl)
    return tl


def parse_tl_file(path: str) -> TlFile:
    """Read and parse one ``.rpy`` translation file.

    Raises :class:`TLParseError` only for undecodable bytes; structural oddities
    are reported on the returned :class:`TlFile` instead.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    text, encoding, has_bom = _decode(data, path)
    tl = TlFile(path=path, lines=split_lines(text), encoding=encoding, has_bom=has_bom)
    tl._orig_lines = list(tl.lines)
    _parse_into(tl)
    return tl


def _parse_into(tl: TlFile) -> None:
    lines = [line.text for line in tl.lines]
    n = len(lines)
    i = 0
    # Everything before the first block is a verbatim preamble: the parser
    # skips leading comments, so it must be captured here or a rebuild drops it.
    while i < n:
        text = lines[i]
        if _is_blank(text) or _is_comment(text):
            tl.preamble.append(text)
            i += 1
            continue
        head = _match_header(text)
        if head is not None or not text.strip():
            break
        # A non-comment top-level statement also ends the preamble.
        break
    capped = tl.preamble_cap()
    tl.preamble = tl.preamble[:capped]
    # Comment lines seen since the last block ended.  A hand-written banner
    # between two blocks belongs to no body; it is attached to the block that
    # follows it so a rebuild can put it back in the right place.
    pending_comments: list[str] = []
    while i < n:
        text = lines[i]
        if _is_blank(text):
            i += 1
            continue
        if _is_comment(text):
            pending_comments.append(text)
            i += 1
            continue
        head = _match_header(text)
        if head is None:
            tl.other_statements.extend(pending_comments)
            pending_comments = []
            tl.other_statements.append(text.strip())
            i += 1
            continue
        kind, language, identifier = head
        indent = _leading_ws(text)
        header_width = _indent_width(text)
        # Collect the block body: everything until a non-blank line whose indent
        # is <= the header's indent. Blank lines and comment lines *inside* the
        # body belong to the block until we hit the next real statement.
        body_start = i + 1
        j = i + 1
        open_quote = ""
        while j < n:
            probe = lines[j]
            if open_quote:
                # Inside a triple-quoted literal the content is written at
                # column 0, so the indent rule below would end the block in the
                # middle of the string. Only the closing delimiter can end it.
                if probe.rfind(open_quote) >= 0:
                    open_quote = ""
                j += 1
                continue
            if _is_blank(probe):
                j += 1
                continue
            if _indent_width(probe) <= header_width:
                break
            opened = _triple_open(probe)
            if opened and probe.count(opened) % 2 == 1:
                open_quote = opened
            j += 1
        block = TlBlock(
            kind=kind,
            language=language,
            identifier=identifier,
            lineno=i + 1,
            end_lineno=j,
            header=text,
            indent=indent,
        )
        _fill_source_comment(block, lines, i)
        # The line directly above the header may be the block's mirrored
        # ``# game/x.rpy:NNN`` source comment, which ``_fill_source_comment``
        # already reads back off the text.  Emitting it from both places would
        # duplicate it on every rebuild.
        if block.source_comment and pending_comments:
            if pending_comments[-1].strip() == block.source_comment:
                pending_comments = pending_comments[:-1]
        block.leading_comments = pending_comments
        pending_comments = []
        _parse_body(block, lines, body_start, j)
        tl.blocks.append(block)
        # ``j`` is always > ``i`` (it starts at i+1), so this always advances.
        i = j


def _match_header(line: str) -> tuple[BlockKind, str | None, str | None] | None:
    if not line.lstrip(" \t").startswith("translate"):
        return None
    m = _TRANSLATE_HEAD_RE.match(line)
    if m is not None:
        language = m.group("lang")
        rest = m.group("rest")
        if rest == "strings":
            return ("strings", language, None)
        if rest == "python":
            return ("python", language, None)
        if rest == "early":
            return ("early", language, None)
        style = re.fullmatch(r"style\s+([A-Za-z_][\w]*)", rest)
        if style is not None:
            return ("style", language, style.group(1))
        ident = re.fullmatch(r"([A-Za-z0-9_][\w]*)", rest)
        if ident is not None:
            return ("translate", language, ident.group(1))
        return ("unknown", language, rest)
    m = _TRANSLATE_HEAD_NOLANG_RE.match(line)
    if m is not None:
        return ("translate", None, m.group("rest"))
    return None


def _fill_source_comment(block: TlBlock, lines: Sequence[str], header_index: int) -> None:
    """Look for ``# game/script.rpy:95`` on the lines directly above the header."""
    k = header_index - 1
    while k >= 0 and _is_blank(lines[k]):
        k -= 1
    if k < 0:
        return
    probe = lines[k].strip()
    m = _SOURCE_COMMENT_RE.match(probe)
    if m is None:
        return
    block.source_comment = probe
    block.source_file = m.group("file").strip()
    try:
        block.source_line = int(m.group("line"))
    except ValueError:  # pragma: no cover - regex guarantees digits
        block.source_line = None


def _parse_body(block: TlBlock, lines: Sequence[str], start: int, stop: int) -> None:
    body_parts: list[str] = []
    pending_old: StringPair | None = None
    say_index = 0
    # Lines already consumed by a multi-line (triple-quoted) value. The loop
    # still visits them so ``body_raw`` keeps every physical line, but they must
    # not be parsed again as ``old``/``new`` of their own.
    _skip_lines: list[int] = []
    skip = set()

    for idx in range(start, stop):
        text = lines[idx]
        lineno = idx + 1
        body_parts.append(text)
        # Te the skip set *before* testing it: the range captured by a
        # multi-line value starts at the current line + 1, and every line in it
        # must fall straight through to the next iteration.
        if _skip_lines:
            skip.update(_skip_lines)
            _skip_lines.clear()
        if idx in skip:
            continue

        if _is_blank(text):
            continue
        stripped = text.lstrip(" \t")
        if stripped.startswith("#"):
            comment = stripped[1:]
            if comment.startswith(" "):
                comment = comment[1:]
            block.comments.append(comment)
            if _looks_like_mirrored_line(comment):
                block.old_comments.append(comment)
            continue

        if block.kind == "strings":
            pair = _parse_old(text, lineno)
            if pair is not None:
                if pending_old is not None and pending_old.new is None:
                    block.problems.append(
                        f"line {lineno}: old without new (previous old on line {pending_old.lineno})"
                    )
                block.strings.append(pair)
                pending_old = pair
                continue
            multiline = _scan_multiline_value(lines, idx, stop, "old")
            if multiline is not None:
                value, last = multiline
                if pending_old is not None and pending_old.new is None:
                    block.problems.append(
                        f"line {lineno}: old without new (previous old on line {pending_old.lineno})"
                    )
                pair = StringPair(
                    lineno=lineno,
                    old_lineno=lineno,
                    new_lineno=None,
                    indent=_leading_ws(text),
                    old=value,
                    new=None,
                    old_raw="\n".join(lines[idx : last + 1]),
                    new_raw=None,
                    context=_extract_context(value),
                )
                block.strings.append(pair)
                pending_old = pair
                _skip_lines.extend(range(idx + 1, last + 1))
                continue
            new_match = _NEW_RE.match(text)
            if new_match is not None:
                if pending_old is None or pending_old.new is not None:
                    block.problems.append(f"line {lineno}: new without a matching old")
                    continue
                value = _unescape_quoted(new_match.group("value"))
                if value is None:
                    # A triple-quoted value never closes on its own line; fall
                    # through to the multi-line reader instead of complaining.
                    multiline = _scan_multiline_value(lines, idx, stop, "new")
                    if multiline is None:
                        block.problems.append(
                            f"line {lineno}: new value is not a quoted string")
                        continue
                    value, last = multiline
                    pending_old.new = value
                    pending_old.new_lineno = lineno
                    pending_old.new_raw = "\n".join(lines[idx : last + 1])
                    _skip_lines.extend(range(idx + 1, last + 1))
                    continue
                pending_old.new = value
                pending_old.new_lineno = lineno
                pending_old.new_raw = text
                continue
            multiline = _scan_multiline_value(lines, idx, stop, "new")
            if multiline is not None:
                value, last = multiline
                if pending_old is None or pending_old.new is not None:
                    block.problems.append(f"line {lineno}: new without a matching old")
                    _skip_lines.extend(range(idx + 1, last + 1))
                    continue
                pending_old.new = value
                pending_old.new_lineno = lineno
                pending_old.new_raw = "\n".join(lines[idx : last + 1])
                _skip_lines.extend(range(idx + 1, last + 1))
                continue
            block.problems.append(f"line {lineno}: unexpected line in strings block")
            continue

        # translate / python / style / early / unknown
        if block.kind == "unknown":
            continue
        if stripped.split(" ", 1)[0].rstrip(":") == "pass":
            block.deleted = True
            continue
        if block.kind in ("python", "style", "early"):
            # Body is preserved verbatim in ``body_raw`` below; the indentation
            # is meaningful Python, so it is never restructured here.
            continue
        say = _split_say(stripped)
        if say is None:
            block.say_lines.append(
                SayLine(
                    index=say_index,
                    lineno=lineno,
                    indent=_leading_ws(text),
                    who="",
                    text="",
                    rest="",
                    raw=stripped,
                    is_say=False,
                )
            )
            say_index += 1
            continue
        who, raw_text, tail = say
        block.say_lines.append(
            SayLine(
                index=say_index,
                lineno=lineno,
                indent=_leading_ws(text),
                who=who,
                text=unescape_value(raw_text),
                rest=tail.strip(),
                raw=stripped,
                is_say=True,
            )
        )
        say_index += 1

    if pending_old is not None and pending_old.new is None:
        block.problems.append(
            f"line {pending_old.lineno}: old without new at end of block"
        )
    # Body text dedented by the block header's indent, for python/style.
    base = _indent_width(block.header)
    dedented: list[str] = []
    # Continuation lines of a triple-quoted literal are part of the *value*.
    # Dedenting them would silently rewrite the translation, so they are kept
    # exactly as written (and ``_reindent`` must likewise leave them alone).
    open_quote = ""
    for raw in body_parts:
        if open_quote:
            dedented.append(raw)
            if open_quote in raw:
                open_quote = ""
            continue
        if not raw.strip():
            dedented.append("")
            continue
        width = _indent_width(raw)
        excess = max(0, width - base)
        # Remove exactly `excess` columns of leading whitespace.
        consumed = 0
        cut = 0
        while cut < len(raw) and consumed < excess:
            ch = raw[cut]
            if ch == " ":
                consumed += 1
            elif ch == "\t":
                consumed += 8 - (consumed % 8)
            else:
                break
            cut += 1
        dedented.append(raw[cut:])
        quote = _triple_open(raw)
        if quote:
            open_quote = quote
    block.body_raw = "\n".join(dedented)
    block.aligned()  # populate unpaired_comments


def _looks_like_mirrored_line(comment: str) -> bool:
    """True when *comment* is a commented-out source line (assumption A1)."""
    body = comment.strip()
    if not body:
        return False
    if body.startswith(_SAY_TEXT_START):
        return True
    say = _split_say(body)
    if say is None:
        return False
    who = say[0].split()[0] if say[0] else ""
    return who.split(".")[-1] not in _NON_SAY_KEYWORDS


def _unescape_quoted(source: str) -> str | None:
    split = _split_quoted(_strip_call_wrapper(source))
    if split is None:
        return None
    return unescape_value(split[0])


#: ``_p("...")`` wrapping a ``new`` value. The official translation docs show
#: this form for multi-line values (``_p`` re-flows the text at display time):
#:
#:     old "These two lines will be combined together to form a long line."
#:     new _p("""
#:         These two lines will be combined together to form a long line.
#:         """)
#:
#: Without recognising the wrapper the value parses as None and the next write
#: replaces the translation with the source text -- silently losing work.
_P_CALL_RE = re.compile(r"^(?P<fn>_p|__|___|_)\s*\((?P<inner>.*)\)\s*$", re.DOTALL)


def _strip_call_wrapper(rest: str) -> str:
    """Unwrap ``_p("...")`` / ``_("...")`` down to the inner literal.

    Only single-argument calls with a literal are unwrapped; anything else is
    returned unchanged so the caller reports a real parse problem instead of
    silently inventing a value.
    """
    match = _P_CALL_RE.match(rest.strip())
    if match is None:
        return rest
    inner = match.group("inner").strip()
    if not inner or inner[0] not in "\"'":
        return rest
    return inner


def _extract_context(value: str) -> str | None:
    """``"{#gui}Select"`` -> ``"gui"``; None when the value has no context tag."""
    ctx = re.search(r"\{#([^}]*)\}", value)
    return ctx.group(1) if ctx is not None else None


def _parse_old(text: str, lineno: int) -> StringPair | None:
    m = _OLD_RE.match(text)
    if m is None:
        return None
    value = _unescape_quoted(m.group("value"))
    if value is None:
        return None
    return StringPair(
        lineno=lineno,
        old_lineno=lineno,
        new_lineno=None,
        indent=m.group("indent"),
        old=value,
        new=None,
        old_raw=text,
        new_raw=None,
        context=_extract_context(value),
    )


# ---------------------------------------------------------------------------
# directory walking
# ---------------------------------------------------------------------------
def iter_tl_files(tl_dir: str, *, recursive: bool = True) -> Iterator[str]:
    """Yield ``.rpy`` files under a ``tl/<language>`` directory, sorted.

    ``.rpyc``/``.rpymc`` and ``.bak`` files are skipped: they are build output
    and our own backups, not translation source.
    """
    if os.path.isfile(tl_dir):
        yield tl_dir
        return
    found: list[str] = []
    if recursive:
        for root, dirs, files in os.walk(tl_dir):
            dirs.sort()
            for name in files:
                if _is_tl_source(name):
                    found.append(os.path.join(root, name))
    else:
        for name in sorted(os.listdir(tl_dir)):
            candidate = os.path.join(tl_dir, name)
            if os.path.isfile(candidate) and _is_tl_source(name):
                found.append(candidate)
    for path in sorted(found):
        yield path


def _is_tl_source(name: str) -> bool:
    lower = name.lower()
    if lower.endswith(".bak"):
        return False
    return lower.endswith(".rpy") or lower.endswith(".rpym")


def iter_tl_languages(tl_root: str) -> list[str]:
    """Language directory names under a ``game/tl`` root, sorted."""
    if not os.path.isdir(tl_root):
        return []
    return sorted(
        name
        for name in os.listdir(tl_root)
        if os.path.isdir(os.path.join(tl_root, name)) and not name.startswith(".")
    )


def parse_tl_dir(tl_dir: str) -> list[TlFile]:
    """Parse every translation file in *tl_dir* (errors propagate)."""
    results: list[TlFile] = []
    for path in iter_tl_files(tl_dir):
        results.append(parse_tl_file(path))
    return results


# ---------------------------------------------------------------------------
# mutation: replace the translation of one identifier
# ---------------------------------------------------------------------------
def replace_new(
    path: str,
    identifier: str,
    new_text: str,
    *,
    index: int = 0,
    who_forced: str | None = None,
    dry_run: bool = True,
) -> str:
    """Replace the translation text of ``identifier`` in *path*.

    Returns the *new file text* (also written when ``dry_run`` is False).
    Raises :class:`UnsafeWriteError` when the target shape is not exactly the
    one we know how to rewrite (see :func:`apply_new_text`).
    """
    tl = parse_tl_file(path)
    apply_new_text(tl, identifier, new_text, index=index, who_forced=who_forced)
    text = tl.render()
    if not dry_run:
        tl.write()
    return text


def replace_string(
    path: str,
    old: str,
    new_text: str,
    *,
    language: str | None = None,
    dry_run: bool = True,
) -> int:
    """Replace every ``old``-matches-*old* ``new`` value; returns how many changed.

    Use :func:`set_string_new` when you want to target exactly one entry (and
    get the modified :class:`StringPair` back) instead of all of them.
    """
    tl = parse_tl_file(path)
    changed = 0
    for block in tl.blocks:
        if block.kind != "strings":
            continue
        if language is not None and block.language != language:
            continue
        for pair in block.strings:
            if pair.old == old:
                _set_string_new(tl, pair, new_text)
                changed += 1
    if changed and not dry_run:
        tl.write()
    return changed


def apply_new_text(
    tl: TlFile,
    identifier: str,
    new_text: str,
    *,
    index: int = 0,
    who_forced: str | None = None,
) -> TlBlock:
    """In-place variant of :func:`replace_new` operating on a parsed file.

    Rules (deliberately conservative):

    * The block must be a ``translate`` block (``kind == "translate"``).
    * The block must contain **exactly one** live say line. Blocks with two or
      more say lines were split on purpose by a translator; merging them back
      into one line would change the script's structure, so we refuse.
    * Blocks with zero say lines (only ``pass`` / ``$``) are refused for the same
      reason: there is no text slot to fill.
    * The rewrite only swaps the characters between the opening and closing
      quote; the speaker, attributes (``id``/``with``) and the indentation are
      preserved verbatim.
    * A block that is a single physical line (``translate x id: e "y"``) is
      refused: rewriting it would need re-indenting.

    Returns the modified block. Raises :class:`UnsafeWriteError` on any of the
    refusals above, :class:`KeyError` when the identifier is absent.
    """
    candidates = tl.find(identifier)
    if not candidates:
        raise KeyError(identifier)
    if index < 0 or index >= len(candidates):
        raise IndexError(
            f"{tl.path}: index {index} out of range, {identifier!r} occurs "
            f"{len(candidates)} time(s)"
        )
    block = candidates[index]
    if block.kind != "translate":
        raise UnsafeWriteError(
            f"{tl.path}:{block.lineno}: {identifier!r} is a {block.kind} block, not a "
            "translate block; use the strings/table API for it"
        )
    lives = [line for line in block.say_lines if line.is_say]
    if not lives:
        raise UnsafeWriteError(
            f"{tl.path}:{block.lineno}: {identifier!r} has no live say line "
            f"(deleted={block.deleted}); rewrite it manually"
        )
    if len(lives) > 1:
        raise UnsafeWriteError(
            f"{tl.path}:{block.lineno}: {identifier!r} contains {len(lives)} say lines; "
            "refusing to collapse a deliberately split translation"
        )
    target = lives[0]
    if block.end_lineno <= block.lineno:
        raise UnsafeWriteError(
            f"{tl.path}:{block.lineno}: single-line block; refusing to rewrite"
        )
    line_index = target.lineno - 1
    if line_index >= len(tl.lines):
        raise UnsafeWriteError(f"{tl.path}:{target.lineno}: line vanished during parse")

    original = tl.lines[line_index].text
    rebuilt = _rewrite_say_line(original, new_text, who_forced=who_forced)
    tl.lines[line_index] = _RawLine(rebuilt, tl.lines[line_index].eol)
    target.text = new_text
    target.raw = rebuilt.strip()
    tl._dirty = True
    return block


def _rewrite_say_line(original: str, new_text: str, *, who_forced: str | None = None) -> str:
    """Replace the text literal inside *original*, keeping everything else."""
    indent = _leading_ws(original)
    stripped = original[len(indent) :]
    if not stripped:
        raise UnsafeWriteError("cannot rewrite an empty say line")

    if stripped[0] in "\"'":
        split = _split_quoted(stripped)
        if split is None:
            raise UnsafeWriteError(f"unterminated quote in say line: {original!r}")
        quote = split[2]
        tail = split[1]
        who = who_forced or ""
    else:
        # ``who "text"``: find the opening quote exactly like _split_say does.
        opener = _find_quote_start(stripped)
        if opener is None:
            raise UnsafeWriteError(f"say line has no text literal: {original!r}")
        split = _split_quoted(stripped[opener:])
        if split is None:
            raise UnsafeWriteError(f"unterminated quote in say line: {original!r}")
        quote = split[2]
        tail = split[1]
        who = who_forced if who_forced is not None else stripped[:opener].rstrip()

    if quote == "'":
        # Re-escape for a single-quoted literal: a bare ``'`` must be escaped and
        # a ``"`` need not be. The order matters -- escape the quotes before
        # unescapaping the ``\\"`` sequences Ren'Py never writes inside a
        # single-quoted literal.
        body = format_value(new_text)
        body = body.replace("\\'", "'")
        body = body.replace("'", "\\'")
        body = body.replace('\\"', '"')
    else:
        body = format_value(new_text)
    head = f"{who} " if who else ""
    return f"{indent}{head}{quote}{body}{quote}{tail}"


def _find_quote_start(stripped: str) -> int | None:
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if ch == "\\":
            i += 2
            continue
        if ch in "\"'" and i > 0 and stripped[i - 1] in " \t":
            return i
        i += 1
    return None


def set_string_new(
    tl: TlFile,
    old: str,
    new_text: str,
    *,
    language: str | None = None,
    index: int = 0,
) -> StringPair:
    """Set the ``new`` value of the strings entry whose ``old`` is *old*.

    This is the counterpart of :func:`apply_new_text` for ``strings`` blocks.
    Assigning ``pair.new = ...`` on a :class:`StringPair` does **not** write
    anything: :meth:`TlFile.render` rebuilds the file from its line list, and
    ``StringPair.new`` is a parsed value, not a render source.  Use this
    function (or :func:`replace_string`) instead.

    *index* picks among several entries with the same ``old`` (Ren'Py forbids
    that inside one language, but hand-written files do it by mistake; a
    positive index lets you fix the second occurrence deliberately).

    Returns the modified pair.  Raises :class:`KeyError` when no block has that
    ``old``, :class:`IndexError` when *index* is out of range.
    """
    matches: list[tuple[TlBlock, StringPair]] = []
    for block in tl.blocks:
        if block.kind != "strings":
            continue
        if language is not None and block.language != language:
            continue
        for pair in block.strings:
            if pair.old == old:
                matches.append((block, pair))
    if not matches:
        where = f" in language {language!r}" if language is not None else ""
        raise KeyError(f"{tl.path}: no strings entry with old {old!r}{where}")
    if index < 0 or index >= len(matches):
        raise IndexError(
            f"{tl.path}: index {index} out of range, old {old!r} occurs "
            f"{len(matches)} time(s)"
        )
    _block, pair = matches[index]
    _set_string_new(tl, pair, new_text)
    return pair


def _set_string_new(tl: TlFile, pair: StringPair, new_text: str) -> None:
    """Rewrite one ``new "..."`` line of a strings block."""
    if pair.new_lineno is None or pair.new_raw is None:
        raise UnsafeWriteError(
            f"{tl.path}:{pair.old_lineno}: old {pair.old!r} has no new line to rewrite"
        )
    line_index = pair.new_lineno - 1
    if line_index >= len(tl.lines):
        raise UnsafeWriteError(f"{tl.path}:{pair.new_lineno}: line vanished during parse")
    original = tl.lines[line_index].text
    indent = _leading_ws(original)
    match = _NEW_RE.match(original)
    if match is None:
        raise UnsafeWriteError(f"{tl.path}:{pair.new_lineno}: not a new line")
    # A multi-line value (``new """..."""`` or ``new _p("""...""")``) spans
    # several lines. Replace all of them with one single-line value, otherwise
    # the leftover continuation lines become stray text in the block and the
    # file no longer parses.
    span = pair.new_raw.count("\n") + 1
    eol = tl.lines[line_index].eol
    quote_split = _split_quoted(_strip_call_wrapper(match.group("value")))
    quote = quote_split[2] if quote_split else '"'
    rebuilt = f"{indent}new {quote}{format_value(new_text)}{quote}"
    tl.lines[line_index] = _RawLine(rebuilt, eol)
    if span > 1:
        del tl.lines[line_index + 1 : line_index + span]
    pair.new = new_text
    pair.new_raw = rebuilt
    tl._dirty = True


# ---------------------------------------------------------------------------
# block creation (used by table import into files that lack an identifier)
# ---------------------------------------------------------------------------
def make_translate_block(
    identifier: str,
    language: str,
    source_text: str,
    new_text: str,
    *,
    who: str = "",
    source_comment: str | None = None,
    eol: str = "\n",
    trailing_blank: bool = False,
) -> list[str]:
    """Render a canonical translation block as a list of lines (no newlines).

    Mirrors the generator's layout exactly: the mirrored source comment, the
    header, then the live line with **no blank line in between** -- Ren'Py
    accepts either shape, and the compact one keeps each block visually
    contiguous::

        # game/script.rpy:95
        translate chinese start_636ae3f5:
            # e "Thank you."
            e "谢谢。"

    Assumption: 4-space indentation, which is what Ren'Py's generator emits.
    """
    out: list[str] = []
    if source_comment:
        out.append(f"# {source_comment}")
    out.append(f"translate {language} {identifier}:")
    body_indent = "    "
    head = f"{who} " if who else ""
    out.append(f"{body_indent}# {head}\"{format_value(source_text)}\"")
    out.append(f"{body_indent}{head}\"{format_value(new_text)}\"")
    if trailing_blank:
        out.append("")
    return out


def append_blocks(tl: TlFile, lines: Sequence[str], *, header: str | None = None) -> None:
    """Append pre-rendered lines to *tl*, reusing the file's dominant line ending."""
    if not lines:
        return
    eol = _dominant_eol(tl.lines) or "\n"
    if tl.lines and not tl.lines[-1].eol:
        tl.lines[-1] = _RawLine(tl.lines[-1].text, eol)
    if tl.lines and tl.lines[-1].text.strip():
        tl.lines.append(_RawLine("", eol))
    if header:
        tl.lines.append(_RawLine(header, eol))
    for line in lines:
        tl.lines.append(_RawLine(line, eol))
    tl._dirty = True


def _dominant_eol(lines: Sequence[_RawLine]) -> str:
    counts: dict[str, int] = {}
    for line in lines:
        if line.eol:
            counts[line.eol] = counts.get(line.eol, 0) + 1
    if not counts:
        return ""
    # Prefer CRLF on a tie: a file that mixes both usually came from Windows.
    return max(counts.items(), key=lambda kv: (kv[1], kv[0] == "\r\n"))[0]


# ---------------------------------------------------------------------------
# reporting helpers shared with verify.py
# ---------------------------------------------------------------------------
@dataclass
class MissingEntry:
    """One untranslated item."""

    path: str
    identifier: str | None
    kind: str
    lineno: int
    old: str
    context: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "identifier": self.identifier,
            "kind": self.kind,
            "lineno": self.lineno,
            "old": self.old,
            "context": self.context,
        }


def find_missing(tl_dir: str, *, language: str | None = None) -> list[MissingEntry]:
    """List every item whose translation is still empty.

    "Empty" means: the live say line's text is ``""`` while a mirrored source
    line exists, or a strings ``new`` is ``""``/absent. Blocks deleted with
    ``pass`` are *not* missing. Blocks whose source text is empty are not
    missing either (nothing to translate).

    *language* filters by the block's language; None means "everything found".
    """
    out: list[MissingEntry] = []
    for tl in parse_tl_dir(tl_dir):
        for block in tl.blocks:
            if language is not None and block.language not in (None, language):
                continue
            if block.kind == "translate":
                if block.deleted:
                    continue
                aligned = block.aligned()
                line_to_source: dict[int, str] = {}
                for source, destination in aligned:
                    line_to_source[id(destination)] = source
                for line in block.say_lines:
                    if not line.is_say:
                        continue
                    if line.text != "":
                        continue
                    src = line_to_source.get(id(line), "")
                    if src == "":
                        # No mirrored comment: we cannot know what the source
                        # text is, so calling this "missing" would be a guess.
                        continue
                    out.append(
                        MissingEntry(
                            path=tl.path,
                            identifier=block.identifier,
                            kind="translate",
                            lineno=line.lineno,
                            old=src,
                        )
                    )
            elif block.kind == "strings":
                for pair in block.strings:
                    if (pair.new or "") == "" and pair.old != "":
                        out.append(
                            MissingEntry(
                                path=tl.path,
                                identifier=None,
                                kind="strings",
                                lineno=pair.new_lineno or pair.old_lineno,
                                old=pair.old,
                                context=pair.context,
                            )
                        )
    return out


def dump_tl_file(
    tl: TlFile | str,
    path: str | None = None,
    blocks: Sequence[TlBlock] | None = None,
    *,
    dry_run: bool = True,
    backup: bool = True,
) -> str | bool:
    """Write a translation file back.

    Two call conventions (the second exists because the task API names
    ``dump_tl_file(path, blocks)``):

    ``dump_tl_file(tl_or_path)``
        Round-trip one already-parsed file. Blocks modified through
        :func:`apply_new_text` are already reflected in the in-memory line list.

    ``dump_tl_file(path, blocks)``
        Open *path*, verify that every block in *blocks* still matches the file
        (same kind/language/identifier/lineno), apply the block's current
        ``new_text`` back onto its say line, and write. Blocks that cannot be
        rewritten raise :class:`UnsafeWriteError`, and nothing is written.

    Returns the path written when ``dry_run`` is False; with ``dry_run`` (the
    default) it returns whether anything would change.
    """
    if blocks is not None and path is None:
        raise TypeError("dump_tl_file(path, blocks) requires a path")
    if path is None:
        if isinstance(tl, str):
            target = parse_tl_file(tl)
        else:
            target = tl
    else:
        target = parse_tl_file(path)
        if blocks:
            for block in blocks:
                _apply_block(target, block)
    if dry_run:
        return target.is_dirty()
    return target.write(backup=backup)


def _apply_block(target: TlFile, block: TlBlock) -> None:
    """Copy the translation text of *block* onto the matching block of *target*."""
    found = target.find(block.identifier) if block.identifier else []
    if not found:
        raise KeyError(f"{target.path}: no block with identifier {block.identifier!r}")
    match = next(
        (b for b in found if b.lineno == block.lineno and b.kind == block.kind),
        found[0],
    )
    if match.kind == "translate":
        if block.new_text:
            apply_new_text(target, block.identifier or "", block.new_text)
    elif match.kind == "strings":
        for pair, source in zip(match.strings, block.strings):
            if source.new is not None:
                _set_string_new(target, pair, source.new)
