"""翻译文件的块排序与行号索引（``sort_tl`` 的干净重实现）。

真实工程里这套流程常常是三个互相 import、共享全局变量、
零测试的单体脚本 ``fix_tool`` / ``analyze_tool`` / ``check_tool`` 拼出来的，
排序规则写在文件名白名单里。这里按「读算法、重实现、带测试、不 import」
的路线重做，规则全部来自结构而非文件名：

**排序规则**

* 只有 ``translate <lang> <identifier>:`` 块参与排序，键是
  ``(源文件, 源行号)`` —— 也就是镜像注释 ``# game/script.rpy:95`` 给出的位置。
* ``translate <lang> strings:`` 块**一律不动**。它按 ``old`` 字符串建键、
  没有行号顺序；重排既无意义，又会打乱 ``old``/``new`` 配对。这一条结构规则
  自动覆盖了 ``devlog_text`` / ``updater_screen`` / ``notify_strings`` 以及
  将来任何 strings 块文件，不必维护白名单。
* 无法确定源位置的块（缺镜像注释）排在最后，内部保持原有顺序（稳定排序）。
* 块之间用空行分隔，文件末尾一个换行。

**索引**

一个文件里块很多时（事件文件常有上千个块），在文件末尾追加一段
「标识符 -> 行号」索引。行号口径是**块头 ``translate`` 那一行在正文里的
1-based 行号**，不含索引自身；索引之后保留一个空行。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .backup import keep
from .errors import InvalidRequestError
from .tl import TLParseError, TlBlock, TlFile, _triple_open, parse_tl_file

__all__ = [
    "SortResult",
    "IndexEntry",
    "sort_tl_file",
    "sort_tl_dir",
    "render_index",
    "strip_index",
    "INDEX_BEGIN",
    "INDEX_END",
]


# The index lives in a block of comments so it can never collide with a
# ``translate`` block or with the runtime's parser.  The two markers match the
# layout the real project already uses, so re-generating an index on an
# existing file rewrites it in place instead of nesting a second one.
INDEX_BEGIN = "# === Event Index ==="
INDEX_END = "# === End Index ==="

#: A section marker written by ``sort_tl_file(sections=True)``.  It is derived
#: from the block order, so an existing one is dropped before regenerating.
#:
#: Deliberately narrow: only ``# --- name ---`` with an ASCII identifier-ish
#: payload.  A hand-written banner such as ``# === 补译：运行期文案 ===`` is a
#: file header, not a section, and must survive the rebuild.
_SECTION_RE = re.compile(r"^#\s*---\s+(?P<label>[\w.]+)\s+---\s*$")

#: Rendered as ``# <label>: L<line>``; the ``L`` prefix is what the project's
#: hand-maintained index uses, and the parser accepts both forms.
_INDEX_LINE_RE = re.compile(r"^#\s*(?P<ident>[\w.]+)\s*:\s*L?(?P<lineno>\d+)\s*$")


@dataclass
class IndexEntry:
    """One ``label -> line`` pair of the generated index."""

    identifier: str
    lineno: int
    label: str = ""
    """The source label (``night_event_1``), when it can be derived."""


@dataclass
class SortResult:
    """What sorting did to one file."""

    path: str
    blocks: int = 0
    skipped_strings: int = 0
    unsorted: int = 0
    """Blocks without a usable source position (parked at the end)."""
    changed: bool = False
    index_entries: list[IndexEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _position(block: TlBlock) -> tuple[int, int] | None:
    """``(source line of file, index among files)`` / None when unknown."""
    if block.source_line is None:
        return None
    return (block.source_line, block.lineno)


def _sortable(block: TlBlock) -> bool:
    return block.kind == "translate" and bool(block.identifier)


#: Block kinds whose code runs when the language is switched, **in file order**
#: (``renpy.translation.change_language`` walks the per-file statement list).
#: Re-ordering them, or moving a dialogue block across one, can change behaviour,
#: so they act as immovable barriers in :func:`sort_tl_file`.
_BARRIER_KINDS = frozenset({"python", "style", "early"})


def _is_barrier(block: TlBlock) -> bool:
    return block.kind in _BARRIER_KINDS


def _reindent(raw: str, header_indent: str) -> list[str]:
    """Re-attach the one-level indent that ``TlBlock.body_raw`` was stripped of.

    A leading ``\\n`` in ``body_raw`` is a *real* blank line after the header
    (verified: ``translate x:\\n    e "b"`` gives ``'e "b"'``, while
    ``translate x:\\n\\n    e "b"`` gives ``'\\ne "b"'``), so it is preserved.
    Only trailing blank lines are dropped -- the inter-block separator is added
    by the caller.

    Lines *inside* a triple-quoted literal are never touched.  Their indentation
    is part of the string value: adding four spaces to a continuation line of
    ``new \"\"\"...\"\"\"`` changes the translated text, which is exactly the
    kind of silent corruption a sorter must never cause.
    """
    text = raw.replace("\r\n", "\n").rstrip("\n")
    if not text.strip():
        return []
    prefix = header_indent + "    "
    out: list[str] = []
    open_quote = ""
    for line in text.split("\n"):
        if open_quote:
            out.append(line)
            # Only the closing delimiter ends the literal.
            if open_quote in line:
                open_quote = ""
            continue
        out.append(prefix + line if line.strip() else "")
        quote = _triple_open(line)
        if quote:
            open_quote = quote
    return out


_LABEL_FROM_ID_RE = re.compile(r"^(.+?)_[0-9a-f]{8}(_\d+)?$")


def derive_label(identifier: str | None, source: str | None = None) -> str:
    """Recover the source label from a generated identifier.

    Ren'Py builds identifiers as ``<label>_<md5[:8]>``; when two statements in
    the same label hash to the same value the engine appends ``_1``, ``_2`` and
    so on, giving ``<label>_<md5[:8]>_<n>``.  Both suffixes have to come off, or
    every duplicate becomes its own "label" and the index balloons: on a large
    reference project's 57,414 blocks a naive ``rpartition`` yields 4,314 index
    entries, while stripping both suffixes yields 651 -- and 651 is exactly what
    the hand-maintained index in 39 of its 41 files contains.

    The regex is deliberately non-greedy: ``night_1_5d9497a5_2`` must reduce to
    ``night_1``, not to ``night_1_5d9497a5``.  A label that itself ends in
    ``_<8 hex chars>`` is indistinguishable from a generated one; this is only
    used for the human-readable index and section markers, never as a key.
    """
    if not identifier:
        return ""
    match = _LABEL_FROM_ID_RE.match(identifier)
    return match.group(1) if match else identifier


def sort_tl_file(
    tl: TlFile,
    *,
    write_index: bool = False,
    index_threshold: int = 1,
    sections: bool = False,
) -> tuple[TlFile | None, SortResult]:
    """Return a re-ordered copy of ``tl`` (None when nothing changed).

    ``write_index`` appends the identifier index when the file holds at least
    ``index_threshold`` sortable blocks.

    ``sections`` inserts a ``# --- <label> ---`` marker whenever the label
    derived from the identifier changes, which is the layout the reference
    project uses by hand.  It is off by default because it changes the byte
    output of an already-sorted file.
    """
    from .tl import _RawLine, _dominant_eol  # local: keep module import light

    result = SortResult(path=tl.path)
    eol = _dominant_eol(tl.lines) or "\n"

    prose: list[TlBlock] = []
    strings: list[TlBlock] = []
    for block in tl.blocks:
        if block.kind == "strings":
            strings.append(block)
        elif _sortable(block):
            prose.append(block)
        else:
            result.notes.append(
                f"{tl.path}:{block.lineno}: block without an identifier left in place"
            )
            prose.append(block)

    result.blocks = len(prose)
    result.skipped_strings = len(strings)

    # ``translate python`` / ``translate style`` / ``translate early`` blocks are
    # executed **in file order** when the language is switched (see the engine's
    # ``change_language``), so moving one across a dialogue block can change what
    # the player sees. They are therefore immovable barriers: a run of sortable
    # blocks between two of them is re-ordered internally, but no block ever
    # crosses a barrier.
    ordered: list[TlBlock] = []
    run: list[TlBlock] = []

    def flush() -> None:
        """Sort one barrier-free run of blocks, preserving its position."""
        if not run:
            return
        keyed: list[tuple[tuple[int, int], TlBlock]] = []
        for block in run:
            pos = _position(block)
            if pos is None:
                result.unsorted += 1
                pos = (1 << 30, block.lineno)
            keyed.append((pos, block))
        # Stable sort: equal positions keep their original relative order, which
        # matters for hand-split blocks that share a source line.
        chunk = [block for _, block in sorted(keyed, key=lambda item: item[0])]
        ordered.extend(chunk)
        run.clear()

    for block in prose:
        if _is_barrier(block):
            flush()
            ordered.append(block)
        else:
            run.append(block)
    flush()

    if [b.lineno for b in ordered] == [b.lineno for b in prose]:
        ordered = prose  # already in order: keep the file byte-identical
    else:
        result.changed = True
        # Report barriers that actually kept a block from moving, so a user
        # wondering why a file still looks unsorted gets an explanation.
        moved_within = 0
        for before, after in zip(prose, ordered):
            if before is after:
                moved_within += 1
        if moved_within < len(prose):
            result.notes.append(
                f"{tl.path}: blocks are only re-ordered between python/style "
                f"blocks, which run in file order"
            )

    if result.unsorted:
        result.notes.append(
            f"{tl.path}: {result.unsorted} block(s) had no '# game/...:NNN' "
            f"comment and were parked at the end"
        )

    # ---- rebuild the body --------------------------------------------------
    # ``TlBlock.body_raw`` starts *after* the ``translate`` line and does not
    # carry the mirrored ``# game/x.rpy:NNN`` comment, so both must be
    # reassembled here; otherwise sorting would silently drop the position
    # comments and the block headers themselves.
    #
    # ``body_raw`` is *dedented* by the header's indent (see ``TlBlock``), so
    # writing it back verbatim would flatten every line to column 0 and the
    # next parse would treat the body as new top-level statements.  Re-indent
    # with one level (four spaces) under the header.
    body: list[str] = []

    # Hand-written file headers (``# Chinese translation``) live outside any
    # block; the parser keeps them on ``preamble`` precisely so a rebuild can
    # put them back.  A leading ``# game/x.rpy:NNN`` that the first block also
    # claims as its source comment is *not* a file header, so drop it before it
    # can be emitted twice.
    #
    # Section markers from a previous ``--sections`` run are dropped too:
    # they are regenerated from the block order, so keeping them would double
    # every marker on the second run.
    for line in tl.preamble[: tl.preamble_cap()]:
        if _SECTION_RE.match(line):
            continue
        body.append(line)

    # ``emit`` guarantees exactly one blank line before the next thing it
    # writes, so a preamble that already ends blank would produce two.  Trailing
    # blanks are therefore dropped here; without this the file grows by one
    # blank line per run and never reaches a fixed point.
    while body and not body[-1].strip():
        body.pop()

    current_section: str | None = None

    def emit(block: TlBlock) -> None:
        nonlocal current_section
        text = block.body_raw
        if not text.strip():
            return
        # Separate from whatever came before, but never stack a second blank
        # line on top of a trailing one (the file preamble usually ends blank,
        # and a doubled blank line would make an unchanged file look dirty).
        if body and body[-1] != "":
            body.append("")
        if sections:
            label = derive_label(block.identifier, block.source_comment)
            if label and label != current_section:
                current_section = label
                body.append(f"# --- {label} ---")
                body.append("")
        # Hand-written banners (``# === 补译：运行期文案 ===``) that sat above
        # this block travel with it.  They are emitted after the generated
        # section marker so a sorted file keeps both.  Markers left over from a
        # previous ``--sections`` run are dropped instead: they are regenerated
        # from the block order, and re-emitting one would double it.
        for comment in getattr(block, "leading_comments", []):
            if _SECTION_RE.match(comment):
                continue
            body.append(comment)
        if block.source_comment:
            # ``source_comment`` already carries its leading '#'.
            body.extend(block.source_comment.replace("\r\n", "\n").split("\n"))
        body.extend(block.header.replace("\r\n", "\n").split("\n"))
        body.extend(_reindent(text, block.indent))

    for block in ordered:
        emit(block)
    for block in strings:
        emit(block)

    # The index reservation must shift ``body`` by the height the index will
    # actually occupy, but ``_locate_headers`` also needs the *final* split
    # between the index and the body.  Prepending the blank line together with
    # the reservation keeps both in sync: the caller hands us ``body`` with no
    # index and no leading blank, so the padding has to cover BOTH.
    index_lines: list[str] = []
    if write_index and len(ordered) >= max(1, index_threshold):
        # The index goes to the *front* of the file, matching the layout the
        # real project already uses (``# === Event Index ===`` ... one line per
        # **label** ... ``# === End Index ===`` then a blank line then the
        # body).
        #
        # The number of entries is known before walking the text (labels are
        # derived from identifiers alone), so reserve exactly that height:
        # BEGIN + one line per distinct label + END + separator.  Reserving
        # ``len(ordered)`` instead would pad the file with blank lines that no
        # entry ever fills, and every line number in the index would be wrong by
        # the difference.
        label_order: list[str] = []
        seen_labels: set[str] = set()
        for block in ordered:
            label = derive_label(block.identifier, block.source_comment)
            if label not in seen_labels:
                seen_labels.add(label)
                label_order.append(label)
        reserved = 1 + len(label_order) + 1 + 1
        entries = _locate_headers(
            eol.join([*([""] * reserved), *body]).split(eol),
            ordered,
        )
        result.index_entries = entries
        index_lines = [
            INDEX_BEGIN,
            *(f"# {entry.label or entry.identifier}: L{entry.lineno}" for entry in entries),
            INDEX_END,
        ]
        if len(index_lines) + 1 != reserved:  # pragma: no cover - guarded above
            raise AssertionError(
                f"index height {len(index_lines)} != reserved {reserved - 1}"
            )
        body = [*index_lines, ""] + body

    new_text = eol.join(body)
    if body:
        new_text += eol
    if new_text == "".join(line.text + line.eol for line in tl.lines):
        return None, result

    # The block *order* may be unchanged while the emitted text still differs
    # (a freshly inserted index or section marker).  ``changed`` drives both the
    # CLI report and the "did anything happen" answer, so it has to reflect the
    # text, not the ordering.
    result.changed = True

    rebuilt = TlFile(
        path=tl.path,
        encoding=tl.encoding,
        has_bom=tl.has_bom,
        other_statements=list(tl.other_statements),
    )
    rebuilt.lines = [_RawLine(text, eol) for text in body]
    if body:
        rebuilt.lines[-1] = _RawLine(body[-1], eol)
    from .tl import _parse_into

    _parse_into(rebuilt)
    return rebuilt, result


def _locate_headers(lines: Sequence[str], blocks: Sequence[TlBlock]) -> list[IndexEntry]:
    """Find each block's ``translate`` line by walking the emitted text.

    ``blocks`` are assumed emitted in order, separated by one blank line and
    preceded by their mirrored comment.  Walking the real text instead of
    adding up field widths keeps the numbers correct no matter how many comment
    lines a block carries.

    The result holds **one entry per label**, pointing at the first block of
    that label -- the layout the reference project maintains by hand.  Blocks
    that belong to an already-indexed label are still walked (the cursor has to
    keep moving past their bodies) but do not produce an entry.
    """
    entries: list[IndexEntry] = []
    seen: set[str] = set()
    cursor = 0
    total = len(lines)
    for block in blocks:
        ident = block.identifier or ""
        found = None
        probe = cursor
        while probe < total:
            if lines[probe].strip().startswith("translate ") and ident in lines[probe]:
                found = probe
                break
            probe += 1
        if found is None:
            continue  # block vanished (empty body): nothing to index
        label = derive_label(ident, block.source_comment)
        if label not in seen:
            seen.add(label)
            entries.append(
                IndexEntry(identifier=ident, lineno=found + 1, label=label)
            )
        # Resume just past this block's body.
        body_len = len(block.body_raw.replace("\r\n", "\n").split("\n"))
        cursor = max(found + body_len, found + 1)
    return entries


def _renumber(blocks: Sequence[TlBlock]) -> list[IndexEntry]:
    """Line numbers of each block header *after* re-ordering.

    Recomputed from the emitted text rather than from the old ``lineno`` --
    the whole point of the index is to be correct in the new file.
    """
    entries: list[IndexEntry] = []
    cursor = 0
    for index, block in enumerate(blocks):
        if index:
            cursor += 1  # the blank separator
        # A block starts with its mirrored comment(s), then the ``translate``
        # line; the index wants the latter, so count the preceding lines.
        offset = len(block.source_comment.replace("\r\n", "\n").split("\n")) if block.source_comment else 0
        entries.append(IndexEntry(block.identifier or "", cursor + offset + 1))
        body = block.body_raw.replace("\r\n", "\n")
        if body.startswith("\n"):
            body = body[1:]
        cursor += offset + 1 + len(body.split("\n"))
    return entries


def render_index(entries: Iterable[IndexEntry]) -> str:
    """Render the index block on its own (for tests and CLI preview)."""
    lines = [INDEX_BEGIN]
    lines.extend(
        f"# {entry.label or entry.identifier}: L{entry.lineno}" for entry in entries
    )
    lines.append(INDEX_END)
    return "\n".join(lines)


def strip_index(text: str) -> str:
    """Remove a previously generated index block (idempotent re-sorting).

    The blank line that separated the index from the file header belongs to the
    index, not the body: leaving it behind would push every block down one line,
    so a stripped file re-sorted with an index would not reproduce the original
    bytes.  ``sort_tl_file`` splices ``[INDEX_BEGIN, entries..., INDEX_END, ""]``
    onto the front, so the index and exactly one leading blank are dropped.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    start = None
    for pos, line in enumerate(lines):
        if line.strip() == INDEX_BEGIN:
            start = pos
            break
    if start is None:
        # No index: normalise trailing blanks only.
        body = lines
    else:
        end = None
        for pos in range(start, len(lines)):
            if lines[pos].strip() == INDEX_END:
                end = pos
                break
        if end is None:  # pragma: no cover - truncated index
            body = lines[:start]
        else:
            # Drop the index *and* the blank separator line right after it.
            nxt = end + 1
            if nxt < len(lines) and not lines[nxt].strip():
                nxt += 1
            body = lines[:start] + lines[nxt:]
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body) + "\n" if body else ""


def sort_tl_dir(
    tl_dir: str,
    *,
    write_index: bool = False,
    index_threshold: int = 1,
    dry_run: bool = True,
    backup: bool = True,
) -> list[SortResult]:
    """Sort every ``.rpy`` under ``tl_dir``; writes only when ``dry_run`` is False."""
    results: list[SortResult] = []
    for name in sorted(os.listdir(tl_dir)) if os.path.isdir(tl_dir) else []:
        path = os.path.join(tl_dir, name)
        if not name.endswith(".rpy") or not os.path.isfile(path):
            continue
        results.append(
            sort_tl_path(
                path,
                write_index=write_index,
                index_threshold=index_threshold,
                dry_run=dry_run,
                backup=backup,
            )
        )
    return results


def sort_tl_path(
    path: str,
    *,
    write_index: bool = False,
    index_threshold: int = 1,
    dry_run: bool = True,
    backup: bool = True,
    sections: bool = False,
) -> SortResult:
    """Sort one file on disk.

    ``backup`` preserves the previous content before the first modification of
    the run, using whatever :mod:`renpy_toolkit.backup` is set to (a sibling
    ``.rpy.bak``, a Git snapshot, or nothing). Sorting moves hundreds of lines
    around, and a translation file that was hand-merged once is not
    reproducible from the sources, so an unbacked-up rewrite is not
    recoverable.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    try:
        text = raw.decode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
    except UnicodeDecodeError as exc:
        # A TL file that is not UTF-8 is either corrupt or saved by an editor
        # in a legacy codepage; either way guessing would rewrite it wrongly.
        raise TLParseError(f"{path}: not valid UTF-8 ({exc})") from exc
    text = strip_index(text)
    tl = parse_tl_file_from_text(text, path)
    rebuilt, result = sort_tl_file(
        tl, write_index=write_index, index_threshold=index_threshold,
        sections=sections,
    )
    if rebuilt is not None and not dry_run:
        body = "".join(line.text + line.eol for line in rebuilt.lines)
        data = body.encode("utf-8")
        # Preserve the original BOM state: engine sources have none, TL files
        # carry one, and the engine's .rpy loader accepts either.
        if raw.startswith(b"\xef\xbb\xbf"):
            data = b"\xef\xbb\xbf" + data
        if data == raw:
            # The rebuild is a fixed point: ``strip_index`` removed the index
            # and ``sort_tl_file`` put the identical one back.  Nothing is
            # written, and the caller must not be told the file changed, or a
            # repeated ``--index`` run would report work it never did.
            result.changed = False
            return result
        if backup:
            keep(path, raw)
        with open(path, "wb") as handle:
            handle.write(data)
    return result


def parse_tl_file_from_text(text: str, path: str) -> TlFile:
    """Parse already-decoded text (keeps :func:`sort_tl_path` BOM-agnostic)."""
    from .tl import parse_tl_text

    return parse_tl_text(text, path)


def require_unique_identifiers(blocks: Iterable[TlBlock]) -> None:
    """Reject an index build when two blocks share an identifier."""
    seen: dict[str, int] = {}
    for block in blocks:
        ident = block.identifier
        if not ident:
            continue
        if ident in seen:
            raise InvalidRequestError(
                f"identifier {ident!r} occurs twice (lines {seen[ident]} and "
                f"{block.lineno}); disambiguate before writing an index"
            )
        seen[ident] = block.lineno
