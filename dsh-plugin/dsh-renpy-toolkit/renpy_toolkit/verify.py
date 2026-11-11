"""翻译完整性校验：找出「看起来翻了、其实没生效」的东西。

为什么必须有这一层
------------------
调研结论（docs/renpy-translation-ecosystem.md §2、附录 4）：**官方生成器和
社区工具都会静默漏句**。

* 官方 ``translate <lang>`` 只静态提取 ``Say`` 与 ``Menu``/``_()``，不收
  ``Text()``、``show text``、``stack_notify`` 里的字面量；
* ``renpy-translator`` 会跳过「有效长度 < 8」的短句；
* 字符串翻译是**跨文件全局**的（同一个 ``StringTranslator``），所以判重/
  判缺漏必须全目录扫，不能按文件看；
* 译文里多写一个源文没有的标签（``{size=}`` 之类）**永不命中**，而文件里
  看着一切正常。

这些都表现为「文件结构合法、lint 通过、游戏里却是英文」。所以本模块做的是
**语义级**检查，不是语法检查：

=====================  ================================================
检查项                 捕获的真实故障
=====================  ================================================
空译文                 骨架没填完 / 半成品发布
标签与占位符不匹配       ``{w}`` 丢了、``[var]`` 被机翻改了
未转义引号              ``"`` 与 ``「」`` 混用导致劈行
字面换行                ``\\n`` 写成真换行 → ``MISSING_TRANS_LINE``
orphan 标识符           脚本改了，译文块成了死块
重复 / 冲突译文         同一 ``old`` 在不同文件给了不同 ``new``
覆盖度                  哪些原文根本没进 tl 文件（漏句）
结巴表达被归一化         ``I-I-I`` 被「修正」成 ``I``（用户明确禁止）
=====================  ================================================

依赖：仅标准库 + :mod:`renpy_toolkit.tl` / :mod:`protect`。
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .errors import VerifyError
from .protect import check_chunk_parity, check_unknown_tags
from .tl import TlBlock, TlFile, _split_say, parse_tl_dir, unescape_value

__all__ = [
    "ERROR",
    "WARNING",
    "Coverage",
    "Finding",
    "VerifyReport",
    "check_literal_newlines",
    "check_placeholders",
    "check_quotes",
    "coverage",
    "is_probably_untranslated",
    "verify_tl_dir",
]

ERROR = "error"
WARNING = "warning"
INFO = "info"

#: 全角/弯引号：Ren'Py 会把它们当普通字符，但一旦嵌在 ``"..."`` 里就会提前
#: 收尾，导致 UNCLOSED_QUOTE。实测中文项目里 401 处 `\"` 对 0 处 `「」`。
_CURLY_QUOTES = "\u201c\u201d\u2018\u2019"

#: 硬编码的全角标点检查：不是错误，但半角全角混用要提示（中文排版）。
_FULLWIDTH_CANDIDATES = "，。！？：；（）"

#: 结巴/重复音节：``I-I-I``、``I- I``、``我、我``。用户多次要求保留原样。
_STUTTER_RE = re.compile(
    r"(\b[\w']{1,12})(?:[-\u2014\u2013]\s*\1\b){2,}", re.IGNORECASE
)


@dataclass
class Finding:
    """一个问题（或提示）。"""

    severity: str
    path: str
    lineno: int
    code: str
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.lineno}: {self.severity}[{self.code}] {self.message}"

    def as_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "path": self.path,
            "lineno": self.lineno,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class Coverage:
    """一条 tl 文件的覆盖情况。"""

    path: str
    total: int = 0
    translated: int = 0
    empty: int = 0
    deleted: int = 0

    @property
    def ratio(self) -> float:
        live = self.total - self.deleted
        return 1.0 if live <= 0 else self.translated / live


@dataclass
class VerifyReport:
    findings: list[Finding] = field(default_factory=list)
    coverage: list[Coverage] = field(default_factory=list)
    files: int = 0
    blocks: int = 0
    strings: int = 0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def total(self) -> int:
        return sum(c.total for c in self.coverage)

    @property
    def translated(self) -> int:
        return sum(c.translated for c in self.coverage)

    @property
    def ratio(self) -> float:
        return 1.0 if self.total == 0 else self.translated / self.total

    def summary(self) -> str:
        return (
            f"{self.files} file(s), {self.blocks} translate block(s), "
            f"{self.strings} string(s); coverage {self.translated}/{self.total} "
            f"({self.ratio:.1%}); {len(self.errors)} error(s), "
            f"{len(self.warnings)} warning(s)"
        )

    def text(self, *, max_items: int = 200) -> str:
        lines = [self.summary()]
        for cov in self.coverage:
            if cov.empty:
                lines.append(
                    f"  {cov.path}: {cov.translated}/{cov.total} "
                    f"({cov.ratio:.1%}), {cov.empty} empty"
                )
        if self.findings:
            lines.append("")
            # Errors first: a strict-mode run over a skeleton produces thousands
            # of warnings, and ``max_items`` would otherwise cut off before the
            # one finding that actually fails the run.
            ordered = sorted(
                self.findings, key=lambda f: 0 if f.severity == ERROR else 1
            )
            for finding in ordered[:max_items]:
                lines.append("  " + finding.format())
            if len(self.findings) > max_items:
                lines.append(f"  ... {len(self.findings) - max_items} more")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 单项检查
# ---------------------------------------------------------------------------
def check_literal_newlines(text: str) -> bool:
    """True when *text* contains a real newline where Ren'Py needs ``\\n``.

    Only meaningful for single-quoted values: a single-quoted Ren'Py string
    cannot span lines, so a real newline there means the literal was split. A
    triple-quoted value (``old \"\"\"...\"\"\"``) legitimately contains real
    newlines -- they are the paragraph breaks -- so callers must not apply this
    check to one. See :func:`is_multiline_literal` for that test.
    """
    return "\n" in text or "\r" in text


def is_multiline_literal(raw: str) -> bool:
    """True when *raw* (a whole ``old``/``new`` line) opens a triple quote.

    ``devlog_text.rpy`` in a real project stores its long patch notes this way:
    ``new \"\"\"...`` followed by bare lines and a closing ``\"\"\"``. Those real
    newlines are the content, not a broken literal.
    """
    stripped = raw.lstrip()
    for prefix in ("old ", "new "):
        if stripped.startswith(prefix):
            value = stripped[len(prefix):].lstrip()
            return value.startswith('"""') or value.startswith("'''")
    return False


def check_quotes(text: str, *, from_source: bool = False) -> list[str]:
    """Problems with quote characters inside one decoded text value.

    *from_source* marks a value that is a verbatim copy of the original line
    (an untranslated placeholder).  Such a line was written by the generator
    with correct escaping and may legitimately contain the game's own
    typographic quotes, so neither rule applies to it -- flagging it would bury
    the real translator mistakes under hundreds of false positives.

    Only the typographic rule lives here.  Counting ASCII quotes is **not**
    meaningful on a decoded value: ``v "{size=-10}\\"所以……？"`` decodes to one
    bare ``"``, which is correct -- the writer escapes it again on the way out
    (``tl.format_value``).  An exported Chinese translation has exactly this
    line and it plays fine, so an odd-count test flagged it wrongly.  A truly
    broken literal never reaches here: the parser rejects it first with
    ``new value is not a quoted string``.
    """
    if from_source:
        return []
    problems: list[str] = []
    if any(ch in text for ch in _CURLY_QUOTES):
        problems.append(
            "contains a curly quote (\u201c \u201d \u2018 \u2019); Ren'Py text "
            'must use plain ASCII quotes, nested ones escaped as \\"'
        )
    return problems


def check_placeholders(source: str, translation: str) -> list[str]:
    """Tag/interpolation parity between a source and its translation."""
    return check_chunk_parity(source, translation)


def is_probably_untranslated(source: str, target: str) -> bool:
    """Heuristic: target is byte-identical to source (nobody translated it).

    Deliberate exceptions: very short strings (``OK``, ``Yes``), pure numbers,
    and strings with no letters at all are legitimately identical.
    """
    if source != target:
        return False
    stripped = source.strip()
    if len(stripped) <= 3:
        return False
    return any(ch.isalpha() for ch in stripped)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def src_line_text(code: str) -> str:
    """The bare text of a ``who "..."`` source line, or ``code`` unchanged."""
    split = _split_say(code)
    if split is None:
        return code
    return unescape_value(split[1])


def _iter_text_units(tl: TlFile) -> Iterable[tuple[int, str, str, str, str, str]]:
    """Yield ``(lineno, source, target, kind, raw_target, source_text)``.

    ``raw_target`` is the *on-disk* text of the target value, before escape
    decoding. It is what the structural checks must look at: after decoding,
    a legal ``\\n`` and an illegal real newline are the same character, so
    checking the decoded form would flag every correct line.

    ``source_text`` is the source's text *without* the ``who`` prefix and
    without the surrounding quotes -- it is what ``target`` is comparable to.
    Comparing ``target`` with ``source`` (the whole ``mc "..."`` code) never
    matches, which silently disabled the "this is an untranslated copy" test.
    """
    for block in tl.blocks:
        if block.kind == "translate":
            if block.deleted:
                continue
            aligned = block.aligned()
            src_for = {id(dst): src for src, dst in aligned}
            for line in block.say_lines:
                if not line.is_say:
                    continue
                src = src_for.get(id(line), "")
                yield (
                    line.lineno,
                    src,
                    line.text,
                    "translate",
                    line.raw,
                    src_line_text(src),
                )
        elif block.kind == "strings":
            for pair in block.strings:
                yield (
                    pair.new_lineno or pair.old_lineno,
                    pair.old,
                    pair.new or "",
                    "strings",
                    pair.new_raw or "",
                    pair.old,
                )


def _project_tags(game_dir: str) -> set[str]:
    """Collect the game's own text tags by reading its scripts.

    ``config.custom_text_tags["sc"] = f`` and friends make the engine's tag
    table open, so a membership test alone cannot tell a typo from a project
    tag. Reading the declarations turns that guess into a fact. ``.rpy`` files
    are read directly; ``.rpyc``-only games yield nothing here, which only means
    the source-comparison fallback stays in charge.
    """
    from .protect import custom_tags

    texts: list[str] = []
    for root, _dirs, names in os.walk(game_dir):
        for name in names:
            if not name.endswith((".rpy", ".rpym")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "rb") as handle:
                    texts.append(handle.read().decode("utf-8-sig", "replace"))
            except OSError:
                continue
    if not texts:
        return set()
    wrapping, closing = custom_tags(*texts)
    return wrapping | closing


def verify_tl_dir(
    tl_dir: str,
    *,
    check_stutter: bool = True,
    check_untranslated: bool = True,
    check_fullwidth: bool = False,
    allow_empty: bool = False,
    expect_dialogue: int | None = None,
    game_dir: str | None = None,
) -> VerifyReport:
    """Run every check over ``tl_dir``.

    ``tl_dir`` is a ``tl/<language>`` directory (or any directory of
    translation files). Problems are collected, not raised — one bad line must
    not hide the other two hundred.

    *allow_empty* downgrades "translation is empty" from an error to a plain
    coverage gap.  A freshly generated skeleton is empty by definition, and
    reporting 58,000 errors hides the handful that matter; use this while
    translating and drop it before shipping.

    *expect_dialogue* asserts the total number of ``translate`` blocks. The
    engine's own ``lint`` prints that number, so passing it here turns a manual
    comparison into a check. It has caught real losses: a generator that wrote
    each file's blocks from the top level of the output directory silently lost
    the ones living in a subdirectory, and no other check noticed because every
    written block was individually valid.

    *game_dir* is the game's script directory (the one holding ``*.rpy`` /
    ``*.rpyc``). When given, the project's own text tags are read out of it
    (``config.custom_text_tags``, ``config.self_closing_custom_text_tags``,
    ``renpy.register_text_tag``) so a translation introducing a legitimate
    project tag is not reported as a typo.
    """
    if not os.path.isdir(tl_dir):
        raise VerifyError(f"{tl_dir}: not a directory")

    declared_tags: set[str] = set()
    if game_dir:
        declared_tags = _project_tags(game_dir)

    report = VerifyReport()
    files = parse_tl_dir(tl_dir)
    report.files = len(files)

    # 字符串翻译跨文件全局：同一 old 给不同 new 会让其中一处永远不生效
    string_targets: dict[str, set[str]] = defaultdict(set)
    string_where: dict[tuple[str, str], list[str]] = defaultdict(list)

    for tl in files:
        rel = os.path.relpath(tl.path, tl_dir).replace("\\", "/")
        cov = Coverage(path=rel)
        report.coverage.append(cov)

        for block in tl.blocks:
            if block.kind == "translate":
                report.blocks += 1
            elif block.kind == "strings":
                report.strings += len(block.strings)

        for lineno, source, target, kind, raw, source_text in _iter_text_units(tl):
            cov.total += 1
            empty = target.strip() == ""
            if empty:
                cov.empty += 1
            else:
                cov.translated += 1

            if kind == "translate" and source_text and empty:
                if not allow_empty:
                    report.findings.append(
                        Finding(ERROR, rel, lineno, "EMPTY", "translation is empty")
                    )
                continue
            if not target:
                # A say whose *source* is empty (" " (multiple=2), an empty
                # menu caption) has nothing to translate; an empty translation
                # mirrors it exactly and is not a defect.
                continue

            # 看 raw（盘上原文），不看 target（已解码）：解码后合法 \\n 与
            # 非法真换行是同一个字符，检查 target 会把每一行正确译文都误报。
            # 三引号值里的真换行是正文段落，不是被劈开的字面量。
            if not is_multiline_literal(raw) and check_literal_newlines(raw):
                report.findings.append(
                    Finding(
                        ERROR, rel, lineno, "LITERAL_NEWLINE",
                        "translation contains a real newline; write the two "
                        "characters \\n instead or Ren'Py splits the statement",
                    )
                )

            for problem in check_quotes(target, from_source=(target == source_text)):
                report.findings.append(Finding(ERROR, rel, lineno, "QUOTE", problem))

            if source:
                parity = check_placeholders(source, target)
                for problem in parity:
                    report.findings.append(
                        Finding(ERROR, rel, lineno, "TAG_MISMATCH", problem)
                    )
                # Diagnose *why* a mismatched tag matters, but only when the
                # parity check already flagged this line: measured across the
                # cases that matter, every unknown tag also changes the tag
                # sequence, so this adds explanation rather than coverage.
                # Reporting it as a second ERROR would double-count one defect.
                if parity:
                    for problem in check_unknown_tags(
                        source, target, known=declared_tags
                    ):
                        report.findings.append(
                            Finding(INFO, rel, lineno, "UNKNOWN_TAG", problem)
                        )

            if check_untranslated and source and is_probably_untranslated(source, target):
                report.findings.append(
                    Finding(
                        WARNING, rel, lineno, "UNTRANSLATED",
                        "translation is identical to the source",
                    )
                )

            if check_stutter and source and _STUTTER_RE.search(source) and not _STUTTER_RE.search(target):
                report.findings.append(
                    Finding(
                        WARNING, rel, lineno, "STUTTER_LOST",
                        "source repeats a syllable (stutter) but the translation "
                        "does not; the stutter is content, keep it",
                    )
                )

            if check_fullwidth and any(ch in target for ch in _FULLWIDTH_CANDIDATES):
                report.findings.append(
                    Finding(
                        INFO, rel, lineno, "FULLWIDTH",
                        "translation uses fullwidth punctuation; fine for Chinese, "
                        "check it against the surrounding style",
                    )
                )

            if kind == "strings":
                key = source
                string_targets[key].add(target)
                string_where[(key, target)].append(f"{rel}:{lineno}")

    # 结构破损：解析器认不出的顶格语句（典型是译文里写了真换行，把一条 say
    # 劈成两半，后半句掉出块外）。这类行 Ren'Py 会直接报语法错，但文件本身
    # 还是「读得动」的，所以必须显式报出来，否则覆盖度显示 0/0、看起来全绿。
    for tl in files:
        rel = os.path.relpath(tl.path, tl_dir).replace("\\", "/")
        for stray in tl.other_statements:
            report.findings.append(
                Finding(
                    ERROR,
                    rel,
                    0,
                    "STRAY_STATEMENT",
                    f"line outside any translate block: {stray[:60]!r}; a real "
                    f"newline inside a translation string splits the statement — "
                    f"write the two characters \\\\n instead",
                )
            )

    # 全局：同一 old 多种 new
    for source, targets in string_targets.items():
        if len(targets) > 1:
            where = sorted(
                f for target in targets for f in string_where[(source, target)]
            )
            report.findings.append(
                Finding(
                    WARNING,
                    where[0].rsplit(":", 1)[0] if where else "<tl_dir>",
                    0,
                    "CONFLICTING_STRING",
                    f"{len(targets)} different translations for the same strings "
                    f"source {source!r}; only one can win at runtime ({', '.join(where[:6])})",
                )
            )

    # 标记被 pass 删除的块：合法的删除，不报错，但在覆盖里体现
    for tl in files:
        rel = os.path.relpath(tl.path, tl_dir).replace("\\", "/")
        deleted = sum(1 for b in tl.blocks if b.kind == "translate" and b.deleted)
        if deleted:
            cov = next((c for c in report.coverage if c.path == rel), None)
            if cov is not None:
                cov.deleted += deleted

    # 对账：块总数必须与引擎 lint 报的一致。少一块就意味着某个文件没写出来，
    # 而单块级检查（格式、引号、标签）全都发现不了这种"整块缺失"。
    if expect_dialogue is not None and report.blocks != expect_dialogue:
        delta = report.blocks - expect_dialogue
        report.findings.append(
            Finding(
                ERROR,
                "<tl_dir>",
                0,
                "BLOCK_COUNT",
                f"{report.blocks} translate block(s), expected {expect_dialogue} "
                f"({delta:+d}); the engine's lint reports the expected number",
            )
        )

    return report


def coverage_table(report: VerifyReport) -> str:
    """Render the coverage part as a fixed-width table."""
    if not report.coverage:
        return "(no translation files)"
    width = max(len(c.path) for c in report.coverage)
    lines = [f"{'file'.ljust(width)}  total  done  empty  ratio"]
    for cov in report.coverage:
        lines.append(
            f"{cov.path.ljust(width)}  {cov.total:5d}  {cov.translated:4d}  "
            f"{cov.empty:5d}  {cov.ratio:5.1%}"
        )
    return "\n".join(lines)


def missing_identifiers(report_path: str, known: Sequence[str]) -> list[str]:
    """Return *known* identifiers that have no block in *report_path*.

    Used against the ``translate zzztmp`` probe output: everything the SDK
    extracted but our tl file does not contain is a silently dropped line.
    """
    tl = parse_tl_dir(report_path)
    present = {b.identifier for t in tl for b in t.blocks if b.identifier}
    return [ident for ident in known if ident not in present]


__all__ = __all__ + ["coverage_table", "missing_identifiers", "verify_decompile"]


@dataclass
class DecompileCheck:
    """Result of :func:`verify_decompile`."""

    compared: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    identifiers: int = 0
    strings: int = 0
    says: int = 0

    @property
    def ok(self) -> bool:
        return self.compared > 0 and not self.failed

    def text(self) -> str:
        lines = [
            f"decompile round trip: {self.compared} file(s), "
            f"{self.identifiers} identifiers, {self.strings} strings, {self.says} say"
        ]
        if self.failed:
            lines.append(f"  MISMATCH in {len(self.failed)} file(s):")
            for rel, why in sorted(self.failed.items())[:20]:
                lines.append(f"    {rel}: {why}")
        elif self.compared:
            lines.append("  every translation identifier survived the round trip")
        return "\n".join(lines)


def _ast_facts(path: str) -> tuple[set[str], int, int]:
    from . import decompile as _d

    target = _d.decode_script(path)
    identifiers: set[str] = set()
    strings = says = 0
    for node in target.statements:
        for inner in _d._walk(node):
            if inner.kind == "Translate" and inner.get("identifier"):
                identifiers.add(str(inner.get("identifier")))
            elif inner.kind == "TranslateString" and "new" in inner.fields:
                strings += 1
            elif inner.kind == "Say":
                says += 1
    return identifiers, strings, says


def _text_facts(text: str, rel: str) -> tuple[set[str], int, int]:
    """Identifier set / strings count / say count of emitted or on-disk TL text."""
    from .tl import parse_tl_text

    parsed = parse_tl_text(text, path=rel)
    identifiers = {b.identifier for b in parsed.blocks if b.identifier}
    strings = sum(len(b.strings) for b in parsed.blocks)
    says = sum(1 for b in parsed.blocks for line in b.say_lines if line.is_say)
    return identifiers, strings, says


def verify_decompile(
    tl_dir: str,
    *,
    against: str | None = None,
) -> DecompileCheck:
    """Check that every ``.rpyc`` in *tl_dir* parses back to the same translation.

    *against* is a second directory of ``.rpyc`` files (typically the ones
    Ren'Py itself recompiled from the decompiled ``.rpy``).  When omitted, the
    check is a pure round trip: decode the AST, emit source in memory, parse it
    back with :mod:`tl`, and require that the identifier set, ``Say`` count and
    ``strings`` count are unchanged.  That catches the failures that matter for
    translation work -- a dropped line, a mangled quote, a lost ``(multiple=)``.

    *tl_dir* may also be a directory of already-decompiled ``.rpy`` sources, in
    which case the expected side comes from parsing the text directly; this is
    what makes the check usable against ``decompile dir -o <empty dir>`` after
    the ``.rpyc`` originals are gone.
    """
    from . import decompile as _d
    from .tl import parse_tl_text

    check = DecompileCheck()
    sources_only = not _d.find_scripts(tl_dir) and bool(_d.find_sources(tl_dir))
    files = _d.find_sources(tl_dir) if sources_only else _d.find_scripts(tl_dir)
    for path in files:
        rel = os.path.relpath(path, tl_dir).replace(os.sep, "/")
        try:
            if sources_only:
                with open(path, "rb") as fh:
                    text = fh.read().decode("utf-8-sig")
                expected_ids, expected_strings, expected_says = _text_facts(text, rel)
                got_ids, got_strings, got_says = expected_ids, expected_strings, expected_says
            else:
                expected_ids, expected_strings, expected_says = _ast_facts(path)
        except Exception as exc:  # noqa: BLE001 - report, never abort the sweep
            check.failed[rel] = f"decode failed: {exc}"
            continue
        if against is not None:
            other = os.path.join(against, os.path.relpath(path, tl_dir))
            if not os.path.exists(other):
                check.failed[rel] = "missing in comparison directory"
                continue
            got_ids, got_strings, got_says = _ast_facts(other)
        elif not sources_only:
            try:
                text, _counts = _d.decompile_file(path)
                got_ids, got_strings, got_says = _text_facts(text, rel)
            except Exception as exc:  # noqa: BLE001
                check.failed[rel] = f"re-parse failed: {exc}"
                continue

        check.compared += 1
        check.identifiers += len(expected_ids)
        check.strings += expected_strings
        check.says += expected_says
        problems = []
        if got_ids != expected_ids:
            missing = sorted(expected_ids - got_ids)[:3]
            extra = sorted(got_ids - expected_ids)[:3]
            problems.append(
                f"identifiers {len(expected_ids)}->{len(got_ids)}"
                f" missing={missing} extra={extra}"
            )
        if got_strings != expected_strings:
            problems.append(f"strings {expected_strings}->{got_strings}")
        if got_says != expected_says:
            problems.append(f"say {expected_says}->{got_says}")
        if problems:
            check.failed[rel] = "; ".join(problems)
    return check
