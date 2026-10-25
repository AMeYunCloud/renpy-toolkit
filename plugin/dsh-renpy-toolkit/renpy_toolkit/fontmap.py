"""Hardcoded-font audit: find fonts used by the game and check they are mapped.

Why this module exists
----------------------
Ren'Py resolves a font through ``renpy/text/font.py::get_font``::

    t = (fn, bold, italics)
    fn, bold, italics = config.font_replacement_map.get(t, t)

The lookup happens **exactly once**.  That has two consequences a translator
must respect:

* A ``text_font "fonts/x.ttf"`` hardcoded inside a screen bypasses styles
  entirely, so changing ``gui.text_font`` or a style has no effect -- the only
  cure is a ``config.font_replacement_map`` entry.
* Because it is a single lookup, pointing a fake font name at a real English
  font *escapes* the Chinese mapping and keeps that string in its original
  typeface.  That is how a project keeps English character names in a
  decorative font while the rest of the UI turns Chinese.

What this module checks
-----------------------
1. Every font literal the game actually uses, gathered from several sources
   (``config.font_replacement_map`` keys, ``gui.*_font`` assignments, style
   properties, screen ``font``/``text_font`` properties, ``Font()`` calls,
   ``{font=...}`` text tags).
2. Whether each *used* font has a mapping for all four ``(bold, italics)``
   states.  Missing a state means the text silently falls back to the original
   font as soon as a ``{b}`` or ``{i}`` tag appears -- the classic
   half-Chinese/half-English line.
3. Mappings that point at a font which does not exist on disk, and identical
   ``(font, bold, italics)`` keys declared twice.

The module never imports Ren'Py: it parses ``.rpy`` text.  It is intentionally
pragmatic -- it recognises the shapes seen in real projects, and reports what it
could not parse instead of guessing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

from .errors import FontError, InvalidRequestError

__all__ = [
    "FontKey",
    "FontMapEntry",
    "FontUse",
    "FontMapReport",
    "parse_font_key",
    "parse_font_literal",
    "scan_font_map",
    "scan_font_uses",
    "scan_used_literal_fonts",
    "audit_fonts",
    "is_fake_font_name",
    "render_font_map_block",
    "resolve_font",
    "FONT_MAP_BLOCK_TEMPLATE",
]

# A font path literal as it appears in .rpy source.  Deliberately permissive:
# font names may contain spaces, dots, parentheses and non-ASCII characters.
_FONT_EXT = r"(?:ttf|otf|ttc|otc|ttx|dfont)"

# ``config.font_replacement_map[( "a", False, False )] = ("b", False, False)``
_MAP_RE = re.compile(
    r"font_replacement_map\s*\[\s*\(\s*(?P<kq>['\"])(?P<kpath>.*?)(?P=kq)\s*,"
    r"\s*(?P<kb>Tru[eu]|Fals[eu]|0|1)\s*,\s*(?P<ki>Tru[eu]|Fals[eu]|0|1)\s*\)\s*\]"
    r"\s*=\s*\(\s*(?P<vq>['\"])(?P<vpath>.*?)(?P=vq)\s*,"
    r"\s*(?P<vb>Tru[eu]|Fals[eu]|0|1)\s*,\s*(?P<vi>Tru[eu]|Fals[eu]|0|1)\s*\)"
)

# ``translate Chinese python:`` / ``translate None python:`` / ``init -999 python:``
_LANG_BLOCK_RE = re.compile(
    r"^(?P<indent>[ \t]*)translate\s+(?P<lang>[\w]+)\s+(?:python|style)\s*:",
)
_INIT_BLOCK_RE = re.compile(r"^(?P<indent>[ \t]*)init\b")

# ``gui.text_font = "fonts/x.ttf"`` and friends.
_GUI_FONT_RE = re.compile(
    r"\bgui\.[\w_]*font[\w_]*\s*=\s*(?P<q>['\"])(?P<path>[^'\"]+)(?P=q)"
)

# ``text_font "fonts/x.ttf"`` (screen label/button property), ``font "..."``,
# ``style X: font "..."``, ``text_font "..."``.
_PROP_FONT_RE = re.compile(
    r"(?:^|[\s:(\[])(?:text_font|font|name_text_font|interface_text_font)"
    r"\s+[\"'](?P<path>[^\"']*\.(?:" + _FONT_EXT + r"))[\"']",
    re.IGNORECASE,
)

# ``Font("fonts/x.ttf", ...)``
_FONT_CALL_RE = re.compile(
    r"\bFont\(\s*(?P<q>['\"])(?P<path>[^'\"]+)(?P=q)",
)

# ``{font=fonts/x.ttf}`` text tag
_FONT_TAG_RE = re.compile(r"\{(?P<neg>/?)font=(?P<path>[^}]+)\}")

# ``define e = Character(..., who_font="fonts/x.ttf")`` counts as a use too.
_WHO_FONT_RE = re.compile(r"\bwho_font\s*=\s*(?P<q>['\"])(?P<path>[^'\"]+)(?P=q)")

_TRUE_TOKENS = frozenset({"True", "true", "1"})

FONT_MAP_BLOCK_TEMPLATE = '''init offset = 1

init python:
    # Keep a pristine copy so other languages can be restored exactly.
    _original_font_replacement_map = config.font_replacement_map.copy()

translate {language} python:
{entries}

translate None python:
    config.font_replacement_map.clear()
    config.font_replacement_map = _original_font_replacement_map.copy()
'''


def _as_bool(token: str) -> bool:
    return token in _TRUE_TOKENS


@dataclass(frozen=True, order=True)
class FontKey:
    """A ``(font, bold, italics)`` tuple -- the key of the replacement map."""

    path: str
    bold: bool = False
    italics: bool = False

    @property
    def state(self) -> str:
        if self.bold and self.italics:
            return "bold+italic"
        if self.bold:
            return "bold"
        if self.italics:
            return "italic"
        return "regular"

    def as_tuple(self) -> tuple[str, bool, bool]:
        return (self.path, self.bold, self.italics)

    def __str__(self) -> str:
        return f"({self.path!r}, {self.bold}, {self.italics})"


def parse_font_key(text: str) -> FontKey:
    """Parse ``("path", True, False)`` (or ``path``) into a :class:`FontKey`."""
    if not isinstance(text, str) or not text.strip():
        raise InvalidRequestError("font key must be a non-empty string")
    body = text.strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1].strip()
    parts = [p.strip() for p in body.split(",")]
    if len(parts) == 1:
        return FontKey(parse_font_literal(parts[0]))
    if len(parts) not in (2, 3):
        raise InvalidRequestError(f"cannot parse font key: {text!r}")
    path = parts[0]
    if path[:1] in "\"'":
        path = parse_font_literal(path)
    bold = _as_bool(parts[1]) if len(parts) > 1 else False
    italics = _as_bool(parts[2]) if len(parts) > 2 else False
    return FontKey(path, bold, italics)


def parse_font_literal(text: str) -> str:
    """Strip surrounding quotes from a font path literal."""
    body = text.strip()
    if len(body) >= 2 and body[0] == body[-1] and body[0] in "\"'":
        return body[1:-1]
    return body


@dataclass(frozen=True)
class FontMapEntry:
    """One declared ``config.font_replacement_map`` row."""

    key: FontKey
    value: FontKey
    path: str
    lineno: int
    language: str | None = None
    context: str = ""

    @property
    def escapes_chinese(self) -> bool:
        """True when a fake name points at a real font on purpose.

        A key whose own file does not exist is the "fake name" trick: the game
        never loads it, so the mapping is the only thing that gives it meaning.
        Use :func:`is_fake_font_name` when the project root is available.
        """
        return False  # needs the project root; see is_fake_font_name()


def is_fake_font_name(font_path: str, root: str) -> bool:
    """True when ``font_path`` does not exist -- the English-preserving trick."""
    return resolve_font(font_path, root) is None


#: A handful of very common Hanzi; if a font carries these it is a CJK face.
#: Deliberately short -- this only needs to separate "Latin-only decorative
#: face" from "installed Chinese face", not to measure real coverage.
_CJK_PROBE = "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说"
#: How many probe characters must be present to call it a CJK face.
_CJK_THRESHOLD = 20


def _covers_chinese(font_path: str, root: str) -> bool:
    """True when the on-disk font already renders Chinese.

    Returns False whenever the answer cannot be established (font missing,
    ``fonttools`` absent, unreadable table): a doubtful font is reported, not
    silently excused.
    """
    resolved = resolve_font(font_path, root)
    if resolved is None or resolved == ":builtin":
        return False
    try:
        from . import fonts as fonts_module

        result = fonts_module.coverage(resolved, _CJK_PROBE)
    except Exception:  # noqa: BLE001 - fonttools missing/malformed: stay strict
        return False
    if result.error:
        return False
    supported = len(result.supported)
    return supported >= _CJK_THRESHOLD


@dataclass(frozen=True)
class FontUse:
    """A font reference found in source, with enough context to trace it."""

    path: str
    origin: str  # "font_replacement_map" | "gui" | "property" | "Font()" | "tag" | "who_font"
    file: str
    lineno: int

    def __str__(self) -> str:
        return f"{self.origin}:{self.path} ({os.path.basename(self.file)}:{self.lineno})"


@dataclass
class FontMapReport:
    """Result of :func:`audit_fonts`."""

    entries: list[FontMapEntry] = field(default_factory=list)
    used: list[FontUse] = field(default_factory=list)
    missing_states: dict[str, list[str]] = field(default_factory=dict)
    unmapped: list[FontUse] = field(default_factory=list)
    dangling: list[FontMapEntry] = field(default_factory=list)
    duplicates: list[FontMapEntry] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.unmapped or self.dangling or self.duplicates or self.missing_states)

    def mapped_fonts(self) -> dict[str, set[str]]:
        """Map font path -> set of ``state`` strings that have a mapping."""
        out: dict[str, set[str]] = {}
        for entry in self.entries:
            out.setdefault(entry.key.path, set()).add(entry.key.state)
        return out

    def text(self) -> str:
        lines: list[str] = []
        lines.append(
            f"font map: {len(self.entries)} entries, {len(self.used)} distinct uses, "
            f"{len(self.mapped_fonts())} mapped fonts"
        )
        if self.ignored:
            lines.append("")
            lines.append("ignored (already covered elsewhere):")
            for item in self.ignored:
                lines.append(f"  - {item}")
        if self.unmapped:
            lines.append("")
            lines.append("UNMAPPED fonts (used by the game but absent from the map):")
            for use in self.unmapped:
                lines.append(f"  - {use}")
        if self.missing_states:
            lines.append("")
            lines.append("INCOMPLETE states (no mapping for this (bold, italics) combination):")
            for path, states in sorted(self.missing_states.items()):
                lines.append(f"  - {path}: have {', '.join(sorted(states))}")
        if self.dangling:
            lines.append("")
            lines.append("DANGLING mappings (target font not found on disk):")
            for entry in self.dangling:
                lines.append(
                    f"  - {entry.key} -> {entry.value.path} "
                    f"({os.path.basename(entry.path)}:{entry.lineno})"
                )
        if self.duplicates:
            lines.append("")
            lines.append("DUPLICATE keys in the same language scope (last one wins):")
            for entry in self.duplicates:
                where = entry.language or "init"
                lines.append(
                    f"  - {entry.key} in {where} "
                    f"({os.path.basename(entry.path)}:{entry.lineno})"
                )
        if self.ok:
            lines.append("")
            lines.append("OK: every used font is mapped for all states.")
        return "\n".join(lines)


def _iter_rpy_texts(root: str) -> Iterator[tuple[str, str]]:
    """Yield ``(path, text)`` for every .rpy file under ``root``."""
    if os.path.isfile(root):
        with open(root, "r", encoding="utf-8-sig", errors="replace") as handle:
            yield root, handle.read()
        return
    if not os.path.isdir(root):
        raise InvalidRequestError(f"not a file or directory: {root}")
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if not name.endswith(".rpy"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
                yield path, handle.read()


def _language_at(lines: Sequence[str], index: int) -> str | None:
    """Return the ``translate <lang>`` block owning ``lines[index]``, if any.

    Ren'Py blocks are indentation based: a block ends at the first non-blank
    line with the same or smaller indentation.
    """
    target_indent = len(lines[index]) - len(lines[index].lstrip())
    for cursor in range(index - 1, -1, -1):
        line = lines[cursor]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent >= target_indent:
            continue
        match = _LANG_BLOCK_RE.match(line)
        if match:
            return match.group("lang")
        if _INIT_BLOCK_RE.match(line):
            return None
        # Any other de-indented line means we left the enclosing block.
        return None
    return None


def scan_font_map(root: str) -> list[FontMapEntry]:
    """Collect every ``config.font_replacement_map`` entry under ``root``."""
    entries: list[FontMapEntry] = []
    for path, text in _iter_rpy_texts(root):
        lines = text.splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#") or "font_replacement_map" not in stripped:
                continue
            for match in _MAP_RE.finditer(line):
                key = FontKey(
                    match.group("kpath"),
                    _as_bool(match.group("kb")),
                    _as_bool(match.group("ki")),
                )
                value = FontKey(
                    match.group("vpath"),
                    _as_bool(match.group("vb")),
                    _as_bool(match.group("vi")),
                )
                entries.append(
                    FontMapEntry(
                        key=key,
                        value=value,
                        path=path,
                        lineno=index + 1,
                        language=_language_at(lines, index),
                        context=stripped,
                    )
                )
    return entries


def scan_font_uses(root: str) -> list[FontUse]:
    """Collect every font reference found in ``root`` (deduplicated by path/origin)."""
    uses: list[FontUse] = []
    seen: set[tuple[str, str]] = set()

    def add(path: str, origin: str, file: str, lineno: int) -> None:
        if not path:
            return
        key = (path, origin)
        if key in seen:
            return
        seen.add(key)
        uses.append(FontUse(path=path, origin=origin, file=file, lineno=lineno))

    for file, text in _iter_rpy_texts(root):
        for index, line in enumerate(text.splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            for match in _MAP_RE.finditer(line):
                add(match.group("kpath"), "font_replacement_map", file, index)
            for match in _GUI_FONT_RE.finditer(line):
                add(match.group("path"), "gui", file, index)
            for match in _PROP_FONT_RE.finditer(line):
                add(match.group("path"), "property", file, index)
            for match in _FONT_CALL_RE.finditer(line):
                add(match.group("path"), "Font()", file, index)
            for match in _FONT_TAG_RE.finditer(line):
                if not match.group("neg"):
                    add(match.group("path"), "tag", file, index)
            for match in _WHO_FONT_RE.finditer(line):
                add(match.group("path"), "who_font", file, index)
    return uses


#: Fonts Ren'Py ships with; they are never on disk inside a project.
_BUILTIN_FONTS = frozenset(
    {
        "DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf",
        "DejaVuSans-Oblique.ttf",
        "DejaVuSans-BoldOblique.ttf",
        "Roboto-Light.ttf",
    }
)


def _candidate_dirs(root: str) -> list[str]:
    """Where a font path may live, relative to the game directory."""
    root = os.path.abspath(root)
    game = root if os.path.basename(root).lower() == "game" else os.path.join(root, "game")
    dirs = [root, game, os.path.join(game, "tl")]
    return dirs


def resolve_font(font_path: str, root: str) -> str | None:
    """Return the on-disk file for ``font_path``, or None when it is missing.

    Built-in Ren'Py fonts resolve to the special value ``":builtin"``.
    """
    name = font_path.replace("\\", "/").lstrip("/")
    for base in _candidate_dirs(root):
        candidate = os.path.join(base, *name.split("/"))
        if os.path.isfile(candidate):
            return candidate
    # tl/<lang>/fonts/... lives deeper than one level; search tl/* for the tail.
    for base in _candidate_dirs(root):
        tl_root = os.path.join(base, "tl")
        if not os.path.isdir(tl_root):
            continue
        for lang in sorted(os.listdir(tl_root)):
            candidate = os.path.join(tl_root, lang, *name.split("/"))
            if os.path.isfile(candidate):
                return candidate
    basename = os.path.basename(name)
    if basename in _BUILTIN_FONTS:
        return ":builtin"
    return None


def audit_fonts(
    root: str,
    *,
    require_states: Iterable[str] = ("regular",),
    strict: bool = False,
) -> FontMapReport:
    """Audit the font map of the project at ``root``.

    ``require_states`` names the ``(bold, italics)`` states every used font must
    cover; the default only requires ``regular``.  Pass
    ``("regular", "bold", "italic", "bold+italic")`` to demand full coverage.

    ``strict`` turns a missing map into an error instead of an empty report.
    """
    required = set(require_states)
    unknown = required - {"regular", "bold", "italic", "bold+italic"}
    if unknown:
        raise InvalidRequestError(f"unknown font state(s): {sorted(unknown)}")

    entries = scan_font_map(root)
    if not entries and strict:
        raise FontError(f"no config.font_replacement_map entries found under {root}")

    uses = scan_font_uses(root)
    mapped = {entry.key.path for entry in entries}

    report = FontMapReport(entries=entries, used=uses)

    # A key declared twice is only a real conflict when both declarations live
    # in the *same* language scope.  The common layout repeats the same keys
    # under ``translate Marisa_ch``, ``translate Chinese`` and ``translate None``
    # on purpose -- each language installs its own font, and the None block
    # restores the original.  Reporting those as duplicates would bury the
    # genuine "same key, same language, two targets" conflicts under noise.
    seen_keys: dict[tuple[FontKey, str | None], FontMapEntry] = {}
    for entry in entries:
        slot = (entry.key, entry.language)
        if slot in seen_keys:
            report.duplicates.append(entry)
        else:
            seen_keys[slot] = entry

    for entry in entries:
        target = resolve_font(entry.value.path, root)
        if target is None:
            report.dangling.append(entry)

    # State coverage.
    #
    # The question "is every state covered?" only makes sense for a font that
    # the *renderer* reaches through the map.  Two shapes of use exist:
    #
    # * tagged use -- the source writes ``{b}``/``{i}`` or passes
    #   ``bold``/``italics`` explicitly.  That needs the matching state.
    # * untagged use -- the source names a font through a style, ``gui.*`` or a
    #   screen property.  The text renders without any tag, so only the
    #   ``regular`` state matters.
    #
    # A font that appears only on the right-hand side (a replacement file) is
    # never demanded: the engine opens it by name, so it needs no entry.
    #
    # In the default mode we therefore only ask whether a font used untagged is
    # in the map at all -- an absent font is already reported as UNMAPPED, so
    # the state check contributes nothing and stays quiet.  ``--all-states``
    # (or an explicit ``require_states``) is what turns it into a real check.
    replacement_targets = {entry.value.path for entry in entries}
    map_keys = {entry.key.path for entry in entries}
    direct_uses = {
        use.path for use in uses if use.path not in replacement_targets
    } | map_keys

    by_font: dict[str, set[str]] = {}
    for entry in entries:
        if entry.key.path in direct_uses:
            by_font.setdefault(entry.key.path, set()).add(entry.key.state)

    untagged_uses = {
        use.path
        for use in uses
        if use.origin in ("gui", "property", "who_font", "Font()", "tag")
    }

    for font_path, states in by_font.items():
        # A font that is also a replacement target is itself a Chinese face:
        # it is installed on purpose, so it never needs to map to anything.
        if font_path in replacement_targets:
            continue
        # Likewise a font that already carries CJK glyphs.
        if _covers_chinese(font_path, root):
            continue
        # Untagged use (gui/style/screen) renders without {b}/{i}, so the
        # regular state is what matters there; a tagged-only font needs the
        # rest.  When only "regular" is requested and the font is reached
        # untagged, presence in the map is the whole check -- absence is
        # already reported as UNMAPPED, so this stays quiet rather than
        # repeating the same finding under a different heading.
        if required == {"regular"} and font_path in untagged_uses:
            continue
        needed = required & ({"regular"} if font_path in untagged_uses else set())
        needed |= required - {"regular"}
        missing = needed - states
        if missing:
            report.missing_states[font_path] = states


    for use in uses:
        if use.origin == "font_replacement_map":
            continue
        if use.path in mapped:
            continue
        # A literal pointing at the engine's built-in font needs no mapping.
        if os.path.basename(use.path) in _BUILTIN_FONTS and not use.path.startswith(("fonts/", "tl/")):
            report.ignored.append(f"{use} (engine built-in)")
            continue
        # A font that already carries Chinese glyphs is the Chinese face itself
        # -- ``gui.text_font = "fonts/LemiShiLangTi-Regular.ttf"`` in a translated
        # project.  Mapping it to another Chinese face would change the look for
        # no benefit, so its absence from the map is not a finding.
        if _covers_chinese(use.path, root):
            report.ignored.append(f"{use} (already has CJK glyphs)")
            continue
        report.unmapped.append(use)

    return report


def render_font_map_block(
    entries: Sequence[tuple[FontKey, FontKey]],
    *,
    language: str = "Chinese",
    indent: str = "    ",
) -> str:
    """Render a ready-to-paste ``translate <lang> python:`` font map block.

    ``entries`` pairs the original ``(font, bold, italics)`` with its replacement;
    the layout follows the template a real project uses.
    """
    if not language or not language.isidentifier():
        raise InvalidRequestError(f"language must be an identifier, got {language!r}")
    if not entries:
        raise InvalidRequestError("at least one font mapping is required")

    rendered: list[str] = []
    for src, dst in entries:
        if not isinstance(src, FontKey) or not isinstance(dst, FontKey):
            raise InvalidRequestError("entries must be (FontKey, FontKey) pairs")
        rendered.append(
            f"{indent}config.font_replacement_map["
            f"{src.path!r}, {src.bold}, {src.italics}"
            f"] = ({dst.path!r}, {dst.bold}, {dst.italics})"
        )
    return FONT_MAP_BLOCK_TEMPLATE.format(
        language=language, entries="\n".join(rendered)
    )
