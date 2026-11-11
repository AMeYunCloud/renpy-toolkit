"""字体与字形覆盖检查（中文汉化必备）。

一个中文字体缺字 = 游戏里出豆腐块 ``□``，而这件事在翻译阶段完全看不出来。
本模块只回答三个问题：

1. 这个字体文件里有没有我译文用到的每个字？（:func:`coverage`）
2. 一组字体 + 一组译文，全部覆盖了吗？（:func:`check_fonts`）
3. 缺哪些、码位是多少、给出可直接交给人改的报告（:func:`missing_report`）

依赖：`fontTools.ttLib.ttFont.TTFont`（可选）。没装时抛
:class:`renpy_toolkit.errors.DependencyError`，消息里带 pip 命令 —— 本模块
刻意不做「解析字体的土法实现」，因为 sfnt 表（cmap 的 format 4/6/12、
 glyf/CFF）不是能安全猜的格式。

顺带说明 Ren'Py 侧的两个真实陷阱（都是官方文档行为，见
docs/renpy-translation-ecosystem.md §5）：

* ``config.font_replacement_map`` 在变量查找时**只查一次**，所以运行时按
  语言换字体不能靠它，得在 translate python 里重建 Text.displayable 用的
  那个样式属性。
* 字体文件名必须是**纯 ASCII**。非 ASCII 文件名的字体在某些平台
  （尤其 Android 的 APK 资源）加载不到。
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

from .errors import DependencyError, FontError

__all__ = [
    "FontCoverage",
    "MissingGlyphs",
    "check_fonts",
    "coverage",
    "codepoint_hex",
    "missing_report",
    "text_codepoints",
]

try:  # pragma: no cover - the import is the feature test
    from fontTools.ttLib import TTFont as _TTFont
except ImportError:  # pragma: no cover
    _TTFont = None  # type: ignore[assignment]


def _require_fonttools(feature: str) -> None:
    if _TTFont is None:
        raise DependencyError(feature, "fonttools")


#: Codepoints we never need a glyph for.
_IGNORED_CODEPOINTS = frozenset(
    {
        0x0009,  # tab
        0x000A,  # line feed
        0x000D,  # carriage return
        0x00A0,  # no-break space -> rendered as a space
        0x2028,
        0x2029,
        0xFEFF,  # BOM / zero-width no-break space
    }
)


def codepoint_hex(char: str) -> str:
    """``'好' -> 'U+597D'`` — the form a font report should use."""
    return f"U+{ord(char):04X}"


def text_codepoints(texts: Iterable[str]) -> set[str]:
    """Every non-ignored character used in *texts*."""
    out: set[str] = set()
    for text in texts:
        for ch in text:
            if ord(ch) in _IGNORED_CODEPOINTS:
                continue
            if unicodedata.category(ch) in ("Zs", "Zl", "Zp", "Cc", "Cf"):
                # spaces and format characters do not need glyphs
                continue
            out.add(ch)
    return out


@dataclass
class FontCoverage:
    """What one font covers of what a text needs."""

    font: str
    supported: set[str] = field(default_factory=set)
    missing: set[str] = field(default_factory=set)
    error: str | None = None

    @property
    def complete(self) -> bool:
        return not self.missing and self.error is None

    @property
    def ratio(self) -> float:
        total = len(self.supported) + len(self.missing)
        return 1.0 if total == 0 else len(self.supported) / total

    def summary(self) -> str:
        if self.error:
            return f"{self.font}: unreadable ({self.error})"
        if not self.missing:
            return f"{self.font}: {len(self.supported)} char(s), no missing glyphs"
        sample = " ".join(sorted(self.missing)[:8])
        more = "" if len(self.missing) <= 8 else f" (+{len(self.missing) - 8} more)"
        return f"{self.font}: {len(self.missing)} missing: {sample}{more}"


@dataclass
class MissingGlyphs:
    """One character missing from one or more fonts."""

    char: str
    codepoint: str
    name: str
    fonts: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "char": self.char,
            "codepoint": self.codepoint,
            "name": self.name,
            "fonts": list(self.fonts),
        }


def _font_cmap(path: str) -> set[str]:
    """Codepoints present in *path*'s cmap, or raise FontError."""
    _require_fonttools("font coverage checking")
    # Open the file ourselves instead of handing fontTools a path.  With a path
    # it opens the file internally and, when the font turns out to be corrupt
    # (a truncated download, a .ttf that is really an HTML error page -- both
    # happen), it raises *after* opening, so the handle is orphaned inside the
    # exception and only reclaimed at GC.  On Windows that keeps the file locked
    # in the meantime.  Owning the handle means we can always close it.
    handle = None
    try:
        handle = open(path, "rb")
        font = _TTFont(handle, fontNumber=0, lazy=True)
        tables = font["cmap"].tables
        out: set[str] = set()
        for table in tables:
            try:
                out.update(chr(cp) for cp in table.cmap)
            except Exception:  # noqa: BLE001 - a weird subtable must not abort
                continue
        return out
    except OSError as exc:
        raise FontError(f"{path}: cannot open ({exc})") from exc
    except FontError:
        raise
    except Exception as exc:  # noqa: BLE001 - fontTools raises many types
        # Covers both "cannot open as a font" and "no usable 'cmap' table";
        # callers only want one FontError either way.
        raise FontError(f"{path}: cannot read cmap ({exc})") from exc
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - closing must never raise here
                pass


def coverage(font_path: str, text: str) -> FontCoverage:
    """Check whether *font_path* can render every character in *text*.

    A missing/failed font is reported through ``FontCoverage.error`` rather
    than raised, so :func:`check_fonts` can produce one report covering many
    files.
    """
    needed = text_codepoints([text])
    result = FontCoverage(font=font_path)
    try:
        available = _font_cmap(font_path)
    except FontError as exc:
        result.error = str(exc)
        return result
    result.supported = needed & available
    result.missing = needed - available
    return result


def _read_text(path: str) -> str:
    """Read any text file we can plausibly be handed as a translation."""
    with open(path, "rb") as handle:
        raw = handle.read()
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def check_fonts(
    font_paths: Sequence[str],
    texts: Iterable[str],
) -> tuple[list[MissingGlyphs], list[FontCoverage]]:
    """Check several fonts against one pool of text.

    Returns ``(missing, per_font)``. ``missing`` is the union of glyphs absent
    from *any* font (each row lists the fonts that lack it); ``per_font`` is
    the per-file detail including unreadable files.
    """
    _require_fonttools("font coverage checking")
    needed = text_codepoints(texts)
    per_font: list[FontCoverage] = []
    for path in font_paths:
        result = FontCoverage(font=path)
        try:
            available = _font_cmap(path)
        except FontError as exc:
            result.error = str(exc)
            result.missing = set(needed)
            per_font.append(result)
            continue
        result.supported = needed & available
        result.missing = needed - available
        per_font.append(result)

    union: set[str] = set()
    for entry in per_font:
        union |= entry.missing
    missing: list[MissingGlyphs] = []
    for char in sorted(union):
        lacking = [e.font for e in per_font if char in e.missing]
        try:
            name = unicodedata.name(char)
        except ValueError:
            name = "<unnamed>"
        missing.append(MissingGlyphs(char, codepoint_hex(char), name, lacking))
    return missing, per_font


def missing_report(missing: Sequence[MissingGlyphs], per_font: Sequence[FontCoverage]) -> str:
    """Human-readable report: which glyph is missing from which font."""
    lines: list[str] = []
    for entry in per_font:
        lines.append(entry.summary())
    if not missing:
        if lines:
            lines.append("")
        lines.append("All required glyphs are present.")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"{len(missing)} character(s) missing:")
    lines.append("")
    width = max(len(e.codepoint) for e in missing)
    for entry in missing:
        fonts = ", ".join(os.path.basename(f) for f in entry.fonts) or "-"
        lines.append(
            f"  {entry.codepoint:<{width}}  {entry.char}  {entry.name}  "
            f"missing from: {fonts}"
        )
    lines.append("")
    lines.append(
        "Tip: Ren'Py only consults config.font_replacement_map once per variable\n"
        "lookup, so switching fonts at runtime needs a translate python block\n"
        "that rebuilds the style property, not that map. Font filenames must\n"
        "also be pure ASCII (Android resource loading)."
    )
    return "\n".join(lines)


def iter_project_texts(tl_dir: str) -> Iterator[str]:
    """Yield translated text from a ``tl/<language>`` tree, for check_fonts().

    Late import keeps ``fonts`` free of a dependency on the parser module.
    """
    from .tl import parse_tl_dir

    for tl in parse_tl_dir(tl_dir):
        for block in tl.blocks:
            if block.kind == "translate":
                for line in block.say_lines:
                    if line.is_say and line.text:
                        yield line.text
            elif block.kind == "strings":
                for pair in block.strings:
                    if pair.new:
                        yield pair.new


__all__ = __all__ + ["iter_project_texts"]
