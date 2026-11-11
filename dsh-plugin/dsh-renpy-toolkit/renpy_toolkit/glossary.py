"""术语字典：读写 ``terms.tsv``，并检查译文里的译名用得对不对。

这些字典放在游戏目录之外（``work/glossary/``），是**人改的**、被 git 跟踪的
文本文件。放到游戏里就变成了又一份没人维护的 TL 文件；放在 dict/list 字面量
里则改一次得跑一次脚本 —— 一次性 ``_fix_*.py`` 脚本堆积就是这么来的。

作用域（``scope`` 列）
    一份字典里混着两种条目：**通用**的（普通名词、按钮文字、敬称）和**只对
    某个作品成立**的（某个任务名、某个角色的口头禅）。通用条目可以跨工程复用；
    作品专属条目换个游戏就全是噪声。所以每一行都带 ``scope``：

    - ``common`` —— 只依赖英文语法/常识，任何 Ren'Py 游戏都适用；
    - ``project`` —— 只对当前工程成立（任务名、事件名、角色专属梗）。

    ``load_terms(..., scope=...)`` 过滤；CLI 的 ``--scope`` 透传。默认**不过滤**
    （把整个文件都当成有效规则），因为人手工维护的字典默认就该整体生效。

检查项（都对着真实译料调过误报）：

``TERM_BANNED``
    译文出现了字典里标为禁用的写法（``恶魔族``、``魔族``）。**error**：
    这类词一旦混进去就是全项目不一致，玩家能直接看出来。
``TERM_MISSING``
    原文出现了术语，译文里既没有规定译名也没有任何已知变体 —— 说明这句
    没翻这个词（或者翻成了字典没记录的新说法）。**warning**：需要人看，
    但也常常是"这个词在这句里本来就不该直译"。
``TERM_DRIFT``
    译文用了**另一个已知译法**（字典的 ``banned`` 列里记着，或同一 source
    在不同文件出现了不同 target）。**warning**。

关键约束：``source`` 必须与游戏源码里的字面量完全一致（大小写、空格都要对），
否则永远匹配不上 —— 「``old`` 多写一个标签就永不命中」是同一类问题。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

from .errors import InvalidRequestError
from .tl import TlBlock, TlFile, parse_tl_dir

__all__ = [
    "CATEGORIES",
    "DEFAULT_HEADER",
    "DEFAULT_SCOPE",
    "SCOPES",
    "UI_TERMS_PATH",
    "Term",
    "TermFinding",
    "TermReport",
    "apply_rename",
    "check_terms",
    "load_terms",
    "parse_terms",
    "render_terms",
    "save_terms",
    "term_hits",
]

#: The leading comment lines of a freshly written ``terms.tsv``.
DEFAULT_HEADER = (
    "# Ren'Py 术语表：source<TAB>target<TAB>category<TAB>banned<TAB>scope<TAB>note",
    "# source 必须与游戏源码里的字面量完全一致；target 是规定译名。",
    "# banned 用 | 分隔，列出明确禁用的写法（出现即报错）。",
    "# scope: common（任何游戏都适用）/ project（只对本工程成立）。",
    "# 只改 target / banned / scope / note；改 source 等于新增一个术语。",
)

#: 允许的分类标签。刻意保持封闭：多一个拼错的分类会让 ``--category`` 静默失效。
CATEGORIES = (
    "character",
    "quest",
    "item",
    "place",
    "faction",
    "enemy",
    "title",
    "magic",
    "common",
    "ui",
    "other",
)

#: 允许的作用域。``common`` 可跨工程复用，``project`` 只对当前作品成立。
#: 分开的理由很实在：把某部作品的任务名写进通用字典，换一个游戏后这些规则
#: 要么永不命中（白占位置），要么命中同形异义的普通词（制造误报）。
SCOPES = ("common", "project")

#: 老字典没有 ``scope`` 列时的默认值。选 ``project`` 而不是 ``common``：
#: 一条规则被误当成通用规则会扩散到别的工程，反过来只是少复用一次。
DEFAULT_SCOPE = "project"

#: 译文里出现这些标记说明该术语是「按语境」的，检查时只提示不报错。
_CONTEXT_MARKERS = ("按语境", "可选", "视语境")

#: 内置的 Ren'Py 标准 UI 术语表（引擎自带界面文字的中文译名）。
#: 这些串来自 ``renpy/common/``，任何 Ren'Py 游戏都会出现，可以跨工程复用。
UI_TERMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ui-terms.tsv")


@dataclass
class Term:
    """One dictionary entry."""

    source: str
    target: str
    category: str = "other"
    banned: tuple[str, ...] = ()
    scope: str = DEFAULT_SCOPE
    note: str = ""

    @property
    def is_context_dependent(self) -> bool:
        """True when the note says the rendering depends on the sentence."""
        return any(marker in self.note for marker in _CONTEXT_MARKERS)

    @property
    def is_common(self) -> bool:
        """True when the entry is reusable outside the project it came from."""
        return self.scope == "common"


@dataclass
class TermFinding:
    """One problem found while checking a translation directory."""

    severity: str
    path: str
    lineno: int
    code: str
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.lineno}: {self.severity}[{self.code}] {self.message}"


@dataclass
class TermReport:
    """What :func:`check_terms` found."""

    files: int = 0
    blocks: int = 0
    terms: int = 0
    checked: int = 0
    findings: list[TermFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[TermFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[TermFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    def text(self, *, max_items: int = 40) -> str:
        ordered = sorted(self.findings, key=lambda f: (f.severity != "error", f.path, f.lineno))
        lines = [
            f"{self.files} file(s), {self.blocks} block(s) checked against "
            f"{self.terms} term(s); {len(self.errors)} error(s), "
            f"{len(self.warnings)} warning(s)"
        ]
        for finding in ordered[:max_items]:
            lines.append("  " + finding.format())
        if len(ordered) > max_items:
            lines.append(f"  ... and {len(ordered) - max_items} more")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# parse / render
# ---------------------------------------------------------------------------
def _split_line(line: str) -> list[str] | None:
    if not line or line.startswith("#"):
        return None
    cells = line.split("\t")
    if len(cells) < 2:
        raise InvalidRequestError(
            f"not a TSV row: {line!r} (expected at least source<TAB>target)"
        )
    return cells


#: ``\\n`` written as two characters inside a TSV cell. A real newline would
#: split the row in two and the second half would be parsed as a new term --
#: engine UI strings such as ``"Contacting App Store\nPlease Wait..."`` do
#: contain newlines, so the escape is not hypothetical.
_ESCAPES = (("\\", "\\\\"), ("\t", "\\t"), ("\r", "\\r"), ("\n", "\\n"))


def _escape_cell(value: str) -> str:
    """Make *value* safe for one TSV cell (tabs and newlines become escapes)."""
    for raw, encoded in _ESCAPES:
        value = value.replace(raw, encoded)
    return value


def _unescape_cell(value: str) -> str:
    """Reverse :func:`_escape_cell`."""
    if "\\" not in value:
        return value
    out = []
    i = 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "r":
                out.append("\r")
                i += 2
                continue
            if nxt == "t":
                out.append("\t")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(char)
        i += 1
    return "".join(out)


def parse_terms(text: str) -> list[Term]:
    """Parse the contents of a ``terms.tsv``.

    Two column layouts are accepted, decided purely by the cell count:

    ``source target category banned note``
        the original 5-column layout; ``scope`` takes :data:`DEFAULT_SCOPE`
        (``project``), which is the safe default -- a rule wrongly treated as
        reusable spreads to other projects, while the opposite merely means it
        is not reused.
    ``source target category banned scope note``
        the current layout.

    Raises :class:`InvalidRequestError` on a malformed row rather than skipping
    it: a silently dropped term means the checker stops protecting it.
    """
    terms: list[Term] = []
    seen: dict[str, Term] = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.rstrip("\r")
        cells = _split_line(line)
        if cells is None:
            continue
        width = len(cells)
        if width >= 6:
            source, target, category, banned, scope, note = (
                c.strip() for c in (cells + [""] * 6)[:6]
            )
        else:
            padded = (cells + [""] * 5)[:5]
            source, target, category, banned, note = (c.strip() for c in padded)
            scope = DEFAULT_SCOPE
        source = _unescape_cell(source)
        target = _unescape_cell(target)
        note = _unescape_cell(note)
        if not source:
            raise InvalidRequestError(f"line {number}: empty source column")
        if not target:
            raise InvalidRequestError(
                f"line {number}: empty target for {source!r} "
                "(delete the row instead of leaving it blank)"
            )
        category = category or "other"
        if category not in CATEGORIES:
            raise InvalidRequestError(
                f"line {number}: unknown category {category!r}; "
                f"expected one of {', '.join(CATEGORIES)}"
            )
        scope = scope or DEFAULT_SCOPE
        if scope not in SCOPES:
            raise InvalidRequestError(
                f"line {number}: unknown scope {scope!r}; expected one of {', '.join(SCOPES)}"
            )
        if source in seen:
            raise InvalidRequestError(
                f"line {number}: duplicate source {source!r} "
                f"(first seen for target {seen[source].target!r})"
            )
        term = Term(
            source=source,
            target=target,
            category=category,
            banned=tuple(b for b in (x.strip() for x in banned.split("|")) if b),
            scope=scope,
            note=note,
        )
        seen[source] = term
        terms.append(term)
    return terms


def load_terms(path: str, *, scope: str | None = None) -> list[Term]:
    """Read a ``terms.tsv`` from disk.

    *scope* narrows the result to ``"common"`` or ``"project"``; the default
    loads every row. See :func:`parse_terms` for why ``scope`` exists.
    """
    if scope is not None and scope not in SCOPES:
        raise InvalidRequestError(
            f"unknown scope {scope!r}; expected one of {', '.join(SCOPES)}"
        )
    with open(path, encoding="utf-8-sig") as fh:
        terms = parse_terms(fh.read())
    if scope is None:
        return terms
    return [t for t in terms if t.scope == scope]


def render_terms(terms: Sequence[Term], *, header: Sequence[str] | None = None) -> str:
    """Render *terms* back to TSV text (trailing newline included)."""
    lines = list(header if header is not None else DEFAULT_HEADER)
    for term in terms:
        lines.append(
            "\t".join(
                (
                    _escape_cell(term.source),
                    _escape_cell(term.target),
                    term.category,
                    "|".join(term.banned),
                    term.scope,
                    _escape_cell(term.note),
                )
            )
        )
    return "\n".join(lines) + "\n"


def save_terms(terms: Sequence[Term], path: str, *, header: Sequence[str] | None = None) -> str:
    """Write *terms* to *path* (UTF-8, LF, no BOM). Returns the path."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_terms(terms, header=header))
    return path


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------
_ASCII_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9' \-.]*$")


def _collide(terms: Sequence["Term"]) -> set[str]:
    """Sources that differ only by case from another term.

    One real dictionary has two: ``Slime`` -> 史莱姆 (the creature) and
    ``slime`` -> 黏液 (the fluid). Matching those case-insensitively makes the
    creature rule fire on every fluid line, which was 33 false errors on a
    single corpus. Such terms are matched exactly; everything else keeps
    the case-insensitive matcher, because a sentence may say ``demon`` where the
    dictionary says ``Demon``.
    """
    seen: dict[str, set[str]] = {}
    for term in terms:
        seen.setdefault(term.source.lower(), set()).add(term.source)
    return {k for k, v in seen.items() if len(v) > 1}


def _source_re(source: str, *, exact: bool = False) -> re.Pattern[str]:
    """Build the matcher for a *source* term.

    ASCII terms get word boundaries: without them ``Book`` would fire inside
    ``Notebook`` and every such hit is a false positive. A term that starts or
    ends with a non-word character (``Fire!``, ``E-rank``) keeps a plain
    ``re.escape`` match, because ``\\b`` cannot anchor there.
    """
    escaped = re.escape(source)
    flags = 0 if exact else re.IGNORECASE
    if _ASCII_WORD.match(source):
        head = r"(?<![A-Za-z0-9])" if source[:1].isalnum() else ""
        tail = r"(?![A-Za-z0-9])" if source[-1:].isalnum() else ""
        return re.compile(head + escaped + tail, flags)
    return re.compile(escaped, flags)


def term_hits(text: str, terms: Sequence[Term], *, exact: Iterable[str] = ()) -> list[Term]:
    """Return every term whose source occurs in *text*.

    *exact* names the sources that must be matched case-sensitively (see
    :func:`_collide`).
    """
    if not text:
        return []
    exact_set = {s.lower() for s in exact}
    return [
        t
        for t in terms
        if _source_re(t.source, exact=t.source.lower() in exact_set).search(text)
    ]


#: Tags/placeholders that are not part of the words being compared.
_MARKUP_RE = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]")
_EDGE_PUNCT = " \t.,!?;:…\u3002\uff0c\uff01\uff1f\u2026"


def _bare(text: str) -> str:
    """Strip markup, then surrounding whitespace and punctuation."""
    return _MARKUP_RE.sub("", text).strip(_EDGE_PUNCT)


def _is_standalone(text: str, term: Term, *, exact: bool) -> bool:
    """True when *text* is the term itself, not a sentence containing it.

    禁用译法只在**独立词条**上成立：``Go back`` 作为按钮必须译「返回」，但
    ``we have to go back`` 里的 go back 译「回去」完全正确。真实成品译料上
    这个区别就是 33 条假 error 与 0 条真 error 的差别。
    """
    return bool(_source_re(term.source, exact=exact).fullmatch(_bare(text)))


def _has_rendering(text: str, term: Term) -> bool:
    return term.target in text


def _banned_hits(text: str, term: Term) -> list[str]:
    return [b for b in term.banned if b in text]


# ---------------------------------------------------------------------------
# checking
# ---------------------------------------------------------------------------
def _iter_units(block: TlBlock) -> Iterator[tuple[int, str, str]]:
    """Yield ``(lineno, source_text, translation)`` for one block."""
    if block.kind == "strings":
        for pair in block.strings:
            yield pair.lineno, pair.old, pair.new or ""
        return
    for source, line in block.aligned():
        yield line.lineno, source, line.text


def check_terms(
    tl_dir: str,
    terms: Sequence[Term],
    *,
    categories: Sequence[str] | None = None,
    files: Sequence[str] | None = None,
    report_missing: bool = True,
    report_drift: bool = False,
) -> TermReport:
    """Check every translation unit in *tl_dir* against *terms*.

    ``report_missing=False`` reports only banned hits, the low-noise mode for a
    large project: a term deliberately dropped from one sentence is not a
    defect.

    ``report_drift=False`` (the default) skips banned renderings that appear
    inside a longer sentence. The distinction is measured, not stylistic: on the
    reference project's finished translation, sentence-embedded hits produced
    31 warnings that are all correct Chinese (``we have to go back`` -> 回去,
    ``a slime`` -> 史莱姆 referring to the creature). Only a standalone label
    carries a usable rule, so that is what an error is reserved for.
    """
    wanted = list(terms)
    if categories:
        wanted = [t for t in wanted if t.category in set(categories)]
    if not wanted:
        return TermReport()

    exact = _collide(wanted)
    scope = set(files) if files else None
    report = TermReport(terms=len(wanted))

    for tl in parse_tl_dir(tl_dir):
        name = os.path.basename(tl.path).replace("\\", "/")
        if scope is not None and name not in scope:
            continue
        report.files += 1
        for block in tl.blocks:
            if block.kind not in ("translate", "strings"):
                continue
            report.blocks += 1
            for lineno, source, target in _iter_units(block):
                if not target:
                    continue
                report.checked += 1
                if target.strip() == source.strip():
                    # A generated skeleton keeps the source text as its
                    # placeholder (``new "apple"``), and ``tl gen`` emits such
                    # identity entries for data-table keys too. That is "not
                    # translated yet", not a terminology defect -- reporting it
                    # here would bury the real findings under hundreds of rows.
                    continue
                for term in term_hits(source, wanted, exact=exact):
                    is_exact = term.source.lower() in exact
                    standalone = _is_standalone(source, term, exact=is_exact)
                    banned = _banned_hits(target, term)
                    if banned and standalone:
                        report.findings.append(
                            TermFinding(
                                "error",
                                tl.path,
                                lineno,
                                "TERM_BANNED",
                                f"{term.source!r} must not be rendered as "
                                f"{', '.join(repr(b) for b in banned)}; "
                                f"use {term.target!r}",
                            )
                        )
                        continue
                    if _has_rendering(target, term):
                        continue
                    if term.is_context_dependent:
                        continue
                    if banned:
                        # Sentence-embedded: the term may be legitimately
                        # phrased differently there. Off unless asked for.
                        if not report_drift:
                            continue
                        report.findings.append(
                            TermFinding(
                                "warning",
                                tl.path,
                                lineno,
                                "TERM_DRIFT",
                                f"{term.source!r} appears in a sentence and is "
                                f"rendered as {', '.join(repr(b) for b in banned)}; "
                                f"check whether {term.target!r} fits here",
                            )
                        )
                        continue
                    if not report_missing:
                        continue
                    if not standalone:
                        # ``Thinking`` is both a say-screen label and an ordinary
                        # English word: "你连圣诞节都在想工作" is a correct
                        # translation of a sentence that happens to contain it.
                        # The rule only means something for a standalone label.
                        continue
                    report.findings.append(
                        TermFinding(
                            "warning",
                            tl.path,
                            lineno,
                            "TERM_MISSING",
                            f"{term.source!r} is in the source line but "
                            f"{term.target!r} is not in the translation "
                            f"(target: {target[:60]!r})",
                        )
                    )
    return report


# ---------------------------------------------------------------------------
# global rename
# ---------------------------------------------------------------------------
@dataclass
class RenameResult:
    """What :func:`apply_rename` changed."""

    files: list[str] = field(default_factory=list)
    replaced: int = 0
    blocks: int = 0
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.replaced} occurrence(s) in {self.blocks} unit(s)"]
        if self.files:
            parts.append(f"{len(self.files)} file(s) written")
        if self.problems:
            parts.append(f"{len(self.problems)} problem(s)")
        return ", ".join(parts)


def apply_rename(
    tl_dir: str,
    old: str,
    new: str,
    *,
    dry_run: bool = True,
    backup: bool = True,
    files: Sequence[str] | None = None,
) -> RenameResult:
    """Replace *old* with *new* inside every translation in *tl_dir*.

    字典规则第 6 条要求「改译名必须同步所有出现位置」。手工做这件事在 6 万
    块的项目上是不可能的，所以做成一个原子操作。

    只改译文，绝不碰 ``old``/``# `` 镜像注释/标识符 —— 那些是引擎的键，改了
    就永久失配。
    """
    if not old:
        raise InvalidRequestError("refusing to rename an empty string")
    if old == new:
        raise InvalidRequestError("old and new are identical; nothing to do")

    from .tl import apply_new_text, _set_string_new  # local: avoid a cycle

    scope = set(files) if files else None
    result = RenameResult()

    for tl in parse_tl_dir(tl_dir):
        name = os.path.basename(tl.path).replace("\\", "/")
        if scope is not None and name not in scope:
            continue
        for block in tl.blocks:
            if block.kind == "strings":
                for pair in block.strings:
                    current = pair.new or ""
                    if old not in current:
                        continue
                    try:
                        _set_string_new(tl, pair, current.replace(old, new))
                    except Exception as exc:  # noqa: BLE001 - reported, never fatal
                        result.problems.append(f"{tl.path}:{pair.lineno}: {exc}")
                        continue
                    result.replaced += current.count(old)
                    result.blocks += 1
                continue
            if block.kind != "translate" or not block.identifier:
                continue
            for index, line in enumerate(block.say_lines):
                if not line.is_say or old not in line.text:
                    continue
                # ``apply_new_text`` mutates ``line.text`` in place, so the count
                # has to be taken before the call or it always reports 0.
                hits = line.text.count(old)
                try:
                    apply_new_text(
                        tl, block.identifier, line.text.replace(old, new), index=index
                    )
                except Exception as exc:  # noqa: BLE001
                    result.problems.append(f"{tl.path}:{line.lineno}: {exc}")
                    continue
                result.replaced += hits
                result.blocks += 1

        if tl.is_dirty():
            result.files.append(tl.path)
            if not dry_run:
                tl.write(backup=backup)
    return result
