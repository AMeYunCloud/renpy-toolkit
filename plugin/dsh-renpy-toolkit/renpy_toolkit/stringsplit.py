"""把「一个 strings 块塞多条 old/new」的手工写法规范化成标准格式。

真实工程里 ``notify_strings.rpy`` 就是这样写的：一个
``translate Chinese strings:`` 块里塞了几十条 ``old``/``new``，每条都没有
``# game/...:NNN`` 源位置注释，只靠 ``# —— 分类 ——`` 之类的人工分隔。这种写法
Ren'Py 能跑，但：

* 看不出每条字符串出自哪个源文件哪一行，源文一改就无从对照；
* ``old``/``new`` 一错位，整块一起烂掉（这正是排序工具当初把这类文件排除在
  外的原因）；
* 无法按源文件拆分成与源码对应的 TL 文件。

本模块只做一件事：**一条字符串一个块**，每条带自己的源位置注释，与
``common.rpy`` 等由引擎生成的标准形态一致。

源位置的取得方式：拿 ``old`` 里的「可搜索文本」（去掉 ``{tag}`` / ``[插值]``
后剩下的部分）到 ``game/`` 下的 ``.rpy`` 源码里查找。找不到就写
``# (no source match)`` —— 绝不编造行号。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterator, Sequence

from .errors import InvalidRequestError
from .tl import StringPair, TlBlock, TlFile, parse_tl_file

__all__ = [
    "SourceHit",
    "SourceIndex",
    "SplitResult",
    "build_source_index",
    "is_standard_strings_layout",
    "normalise_strings_block",
    "normalise_tl_file",
    "searchable_text",
    "NO_SOURCE_COMMENT",
]

#: Written when a literal cannot be found in any source file.  Deliberately
#: explicit so a later pass can tell "not searched" from "not found".
NO_SOURCE_COMMENT = "# (no source match)"

# ``{tag}`` / ``{/tag}`` / ``[interpolation]`` -- text that cannot be matched
# verbatim against the source because the engine substitutes it.
_TAG_RE = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]")
_QUOTE_RE = re.compile(r'"')
_SOURCE_LINE_RE = re.compile(
    r"^(?P<comment>#\s*(?P<file>game/[\w./\\-]+\.rpy):(?P<line>\d+))"
)


def searchable_text(text: str) -> str:
    """Strip ``{tag}``s and ``[interp]``s, returning what can be grepped verbatim."""
    return _TAG_RE.sub("", text)


@dataclass
class SourceHit:
    """Where a string literal was found in the game sources."""

    path: str
    """Path relative to ``game/``, e.g. ``screens.rpy``."""
    lineno: int
    raw: str

    def comment(self) -> str:
        return f"# game/{self.path}:{self.lineno}"


class SourceIndex:
    """A crude but fast literal index over ``game/**/*.rpy``.

    Keyed by a *line-level* search: for every call we scan the candidate lines
    once and compare with ``in``.  A project the size of the real one has a few
    hundred thousand lines, and a strings file has at most a few hundred
    literals, so a per-literal scan is still cheap and avoids an index that
    would have to be invalidated every time a source file changes.
    """

    def __init__(self, game_dir: str, *, include_tl: bool = False) -> None:
        self.game_dir = os.path.abspath(game_dir)
        self.include_tl = include_tl
        self._files: list[tuple[str, list[str]]] = []
        self._loaded = False
        #: ``(path, reason)`` for sources that could not be read.  Reported so a
        #: hit that "does not exist" is not mistaken for a missing literal.
        self.unreadable: list[tuple[str, str]] = []

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for root, dirs, files in os.walk(self.game_dir):
            # ``tl/`` holds our own translations; matching against them would
            # find the block we are trying to move.
            if not self.include_tl and os.path.basename(root) == "tl":
                dirs[:] = []
                continue
            for name in files:
                if not name.endswith(".rpy"):
                    continue
                path = os.path.join(root, name)
                try:
                    with open(path, "rb") as handle:
                        text = handle.read().decode("utf-8-sig", "replace")
                except OSError as exc:
                    self.unreadable.append((path, str(exc)))
                    continue
                rel = os.path.relpath(path, self.game_dir).replace(os.sep, "/")
                self._files.append((rel, text.splitlines()))

    def find(self, literal: str, *, prefer: Sequence[str] = ()) -> SourceHit | None:
        """Return the first source line that contains *literal*.

        ``prefer`` lists relative file names to search first (the ``B4`` rule:
        a same-named file wins over the rest).
        """
        self._load()
        needle = searchable_text(literal)
        if not needle.strip():
            return None
        order = sorted(
            self._files,
            key=lambda item: (0 if item[0] in prefer else 1, item[0]),
        )
        for rel, lines in order:
            for index, line in enumerate(lines, start=1):
                if needle not in line:
                    continue
                # Skip the mirrored ``# e "..."`` comments -- the live line is
                # the one without a leading '#'.
                if line.lstrip().startswith("#"):
                    continue
                return SourceHit(path=rel, lineno=index, raw=line)
        return None


def build_source_index(game_dir: str) -> SourceIndex:
    """Build a source index rooted at ``game_dir`` (the ``game/`` folder)."""
    if not os.path.isdir(game_dir):
        raise InvalidRequestError(f"not a directory: {game_dir}")
    index = SourceIndex(game_dir)
    index._load()
    return index


@dataclass
class SplitResult:
    """Outcome of normalising one file's strings blocks."""

    path: str
    blocks_in: int = 0
    pairs_in: int = 0
    blocks_out: int = 0
    unresolvable: int = 0
    """Pairs whose literal was not found in the sources."""
    text: str = ""
    notes: list[str] = field(default_factory=list)


def is_standard_strings_layout(tl: TlFile) -> bool:
    """True when every strings block holds at most one ``old``/``new`` pair.

    Standard layout is what the engine's own ``translate`` command produces:
    one ``translate <lang> strings:`` block per literal, each preceded by its
    ``# game/x.rpy:NNN`` comment.
    """
    for block in tl.blocks:
        if block.kind != "strings":
            continue
        if len(block.strings) > 1:
            return False
    return True


def _render_pair(language: str, pair: StringPair, comment: str | None) -> list[str]:
    """Render one pair as a self-contained ``translate ... strings:`` block.

    ``old_raw``/``new_raw`` already carry their keyword (and the original
    indent), so they are emitted verbatim; re-indenting them here would double
    the keyword up as ``old     old "..."``.
    """
    out: list[str] = []
    if comment:
        out.append(comment)
    out.append(f"translate {language} strings:")
    out.append(pair.old_raw if pair.old_raw.strip().startswith("old") else f'    old "{pair.old}"')
    if pair.new_raw is not None and pair.new_raw.strip().startswith("new"):
        out.append(pair.new_raw)
    elif pair.new is not None:
        out.append(f'    new "{pair.new}"')
    else:
        out.append("    new \"\"")
    return out


def normalise_strings_block(
    block: TlBlock,
    *,
    language: str,
    source_index: SourceIndex | None = None,
    prefer: Sequence[str] = (),
) -> tuple[list[str], SplitResult]:
    """Split one multi-pair ``strings`` block into standard blocks.

    ``language`` overrides the block's own language (``block.language`` is used
    when it matches).  Each returned line list already carries its comment; the
    caller joins them with a blank line between blocks.
    """
    result = SplitResult(path=f"<line {block.lineno}>")
    result.blocks_in = 1
    result.pairs_in = len(block.strings)
    rendered: list[str] = []

    lang = block.language or language
    if not lang.isidentifier():
        raise InvalidRequestError(f"language must be an identifier, got {lang!r}")

    for pair in block.strings:
        comment: str | None = None
        if source_index is not None:
            hit = source_index.find(pair.old, prefer=prefer)
            if hit is not None:
                comment = hit.comment()
            else:
                result.unresolvable += 1
                comment = NO_SOURCE_COMMENT
                result.notes.append(
                    f"{language}: no source match for {pair.old!r}"
                )
        rendered.extend(_render_pair(lang, pair, comment))
        result.blocks_out += 1
        rendered.append("")

    while rendered and not rendered[-1].strip():
        rendered.pop()
    result.text = "\n".join(rendered)
    return rendered, result


def normalise_tl_file(
    tl: TlFile,
    *,
    game_dir: str | None = None,
    prefer: Sequence[str] = (),
) -> SplitResult:
    """Rewrite every multi-pair ``strings`` block of one file.

    Blocks already in standard layout are emitted verbatim; prose blocks are
    left untouched (sorting is a separate concern in :mod:`sorttl`).
    """
    language = next(
        (b.language for b in tl.blocks if b.language), None
    )
    if not language:
        raise InvalidRequestError(f"{tl.path}: no language could be determined")

    index = build_source_index(game_dir) if game_dir else None
    out: list[str] = []
    result = SplitResult(path=tl.path)

    for block in tl.blocks:
        if out:
            out.append("")
        if block.kind != "strings":
            out.extend(_render_block(block))
            continue
        if len(block.strings) <= 1:
            out.extend(_render_block(block))
            result.blocks_out += 1
            continue
        lines, sub = normalise_strings_block(
            block, language=language, source_index=index, prefer=prefer
        )
        out.extend(lines)
        result.blocks_in += 1
        result.pairs_in += sub.pairs_in
        result.blocks_out += sub.blocks_out
        result.unresolvable += sub.unresolvable
        result.notes.extend(sub.notes)

    result.text = "\n".join(out).rstrip("\n") + "\n"
    return result


def _render_block(block: TlBlock) -> list[str]:
    """Render a block verbatim, restoring the indent ``body_raw`` was stripped of."""
    from .sorttl import _reindent

    lines: list[str] = []
    if block.source_comment:
        lines.extend(block.source_comment.replace("\r\n", "\n").split("\n"))
    lines.extend(block.header.replace("\r\n", "\n").split("\n"))
    lines.extend(_reindent(block.body_raw, block.indent))
    return lines


def iter_source_files(game_dir: str) -> Iterator[str]:
    """Yield every ``.rpy`` path under ``game_dir`` except ``tl/``."""
    for root, dirs, files in os.walk(game_dir):
        if os.path.basename(root) == "tl":
            dirs[:] = []
            continue
        for name in files:
            if name.endswith(".rpy"):
                yield os.path.join(root, name)
