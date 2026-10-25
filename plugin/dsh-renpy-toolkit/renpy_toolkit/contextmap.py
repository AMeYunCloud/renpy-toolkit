"""Context-aware string translation for Ren'Py.

Ren'Py's string translation table is *global*: one ``old`` string maps to
exactly one ``new`` string for the whole game, and registering the same
``old`` twice raises ``A translation for "..." already exists``.  That is
fine until the same short word means different things in different places --
``Enter`` is "come in" in dialogue but "the Enter key" on the keyboard help
screen, ``Continue`` is "proceed" in a tutorial but "OK" in a warning box.

The engine has an official escape hatch, documented as *context tags*: a
literal may embed ``{#name}``, which is stripped before the text is rendered
but is kept as part of the translation key.  ``old "Enter{#hw}"`` and
``old "Enter"`` are therefore two distinct keys that coexist happily.

Using it means editing the ``.rpy`` source so the literal carries the tag,
which is unavailable when a game ships only ``.rpyc``.  For those games this
module installs the tag at *runtime*.  A ``translate <lang> python:`` block
wraps :func:`renpy.translation.translate_string`; while a screen is being
evaluated the override checks the current screen name and returns a
screen-specific translation when one is registered.  Ren'Py pushes the
screen onto :data:`renpy.display.screen._current_screen` in
``ScreenDisplayable.update`` *before* it builds the ``Text`` displayables,
so the hook sees the right screen and nothing has to touch the game files.

Two halves, then:

* :func:`find_ambiguities` -- which literals need disambiguation at all.
* :func:`build_patch_source` / :func:`write_patch` -- the runtime shim.

Both are pure text operations, so they can be tested without starting a
game.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .errors import ToolkitError

__all__ = [
    "AmbiguousText",
    "find_ambiguities",
    "build_patch_source",
    "patch_filename",
    "write_patch",
    "load_rows",
    "strip_context",
    "has_visible_words",
    "tag_literal",
    "context_tag",
    "PATCH_MARKER",
]

#: Written into the generated file so a re-run can recognise (and replace)
#: its own output without guessing from the filename alone.
PATCH_MARKER = "# renpy-toolkit: context patch"

#: The patch file the runtime shim lives in.  Kept separate from the normal
#: ``tl/<lang>/*.rpy`` files so deleting it cleanly disables the feature.
PATCH_BASENAME = "_rtk_context"

#: A screen name is the argument of a ``screen`` statement: identifier chars
#: plus dots, since Ren'Py allows nested names like ``store.foo``.
_SCREEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

#: Tags that carry no meaning for the lookup and would otherwise make two
#: otherwise-identical strings compare unequal.  Only the leading
#: ``{#context}`` is meaningful here, so it is the only one stripped.
_CONTEXT_TAG_RE = re.compile(r"\{#[^}]*\}")

#: A literal that is nothing but text tags (``{w=0.2}{nw}``,
#: ``{image=check_text}``) has no visible words to disambiguate: splitting it by
#: screen would create two keys that render identically. Such literals are
#: still translatable in principle, so this is only used to keep them out of
#: the *ambiguity report*.
_NO_WORDS_RE = re.compile(r"^(?:\{[^{}]*\}|\s)+$")


def has_visible_words(text: str) -> bool:
    """True when *text* renders something a translator could word differently.

    A pure tag run (``{w=0.5}{nw}``) or a lone ``{image=...}`` renders a pause
    or a picture, never a word, so it can have exactly one correct "translation"
    and belongs in no ambiguity report.
    """
    bare = _CONTEXT_TAG_RE.sub("", text or "")
    return not _NO_WORDS_RE.match(bare)


def strip_context(text: str) -> str:
    """Return *text* with any ``{#context}`` tags removed.

    Used to decide whether two occurrences are "the same string"; the tag is
    exactly what makes them different keys, so it must not take part.
    """
    return _CONTEXT_TAG_RE.sub("", text)


@dataclass(frozen=True)
class AmbiguousText:
    """A literal that appears on more than one screen.

    ``screens`` is sorted for stable output.  A literal appearing on several
    screens does **not** automatically need a context tag -- the reference
    project translates ``{size=+2}BACK`` identically on all 27 statistics
    screens -- so the caller decides, and this record only reports the facts.
    """

    text: str
    screens: tuple[str, ...]
    rows: tuple[dict, ...] = field(repr=False, default=())

    @property
    def count(self) -> int:
        return len(self.screens)

    def describe(self) -> str:
        names = ", ".join(self.screens[:5])
        if self.count > 5:
            names += ", ... (%d total)" % self.count
        return "%r -> %s" % (self.text, names)


def load_rows(path: str) -> list[dict]:
    """Read a probe dump, accepting either the wrapped or the bare shape."""
    import json

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return list(rows or [])


def find_ambiguities(rows: list[dict], *, min_screens: int = 2,
                     ignore: set[str] | None = None,
                     keep: set[str] | None = None) -> list[AmbiguousText]:
    """Group *rows* by literal and return the ones spanning several screens.

    *rows* is the raw screen dump from the strings probe: each row has
    ``text``, ``screen`` and ``file``.  Rows without a screen name (plain
    ``$ var = "..."`` assignments, say) cannot be disambiguated at runtime
    and are ignored.

    Pass the **unfiltered** rows.  The natural companion,
    :func:`renpy_toolkit.tlgen.filter_strings`, collapses to one row per text
    because the string table is global -- feed its output here and every text
    appears once, hiding the ambiguity completely.  Use *keep* instead: give
    it the set of texts that survived filtering, and the screen union is still
    computed over every occurrence.

    *ignore* holds literals the caller has already decided about, so a
    project-specific whitelist can suppress noise.

    The returned list is sorted most-ambiguous first, then by text, which
    makes the output stable across runs.
    """
    skip = ignore or set()
    allowed = keep
    by_text: dict[str, list[dict]] = {}
    for row in rows:
        screen = row.get("screen")
        text = row.get("text")
        if not screen or not isinstance(text, str):
            continue
        if not _SCREEN_RE.match(screen):
            continue
        if text in skip:
            continue
        if allowed is not None and text not in allowed:
            continue
        # A tag-only literal (``{w=0.5}{nw}``, ``{image=check_text}``) renders a
        # pause or a picture. Splitting it by screen would mint two keys with
        # the same render, and the caller has nothing to decide -- so it is not
        # an ambiguity. It stays in the string table; only this report skips it.
        if not has_visible_words(text):
            continue
        by_text.setdefault(text, []).append(row)

    out: list[AmbiguousText] = []
    for text, group in by_text.items():
        screens = sorted({str(r["screen"]) for r in group})
        if len(screens) < max(2, min_screens):
            continue
        out.append(AmbiguousText(text=text, screens=tuple(screens),
                                 rows=tuple(group)))
    out.sort(key=lambda a: (-a.count, a.text))
    return out


def patch_filename(language: str) -> str:
    """Name of the generated shim inside ``tl/<language>/``."""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", language)
    return "%s_%s.rpy" % (PATCH_BASENAME, safe)


def context_tag(screen: str, *, prefix: str = "") -> str:
    """Build the ``{#...}`` tag that disambiguates *screen*.

    Ren'Py parses the tag as ``{#anything}`` and strips it at render time, so
    the only requirement is that it is unique per screen and cannot collide
    with a tag the game already uses.  The ``rtk-`` prefix keeps it clearly
    ours when someone reads the source.
    """
    if not _SCREEN_RE.match(screen):
        raise ToolkitError("invalid screen name: %r" % (screen,))
    safe = re.sub(r"[^A-Za-z0-9_]", "_", screen)
    return "{#%s%s}" % (prefix, safe)


def tag_literal(literal: str, screen: str, *, prefix: str = "") -> str:
    """Return *literal* with a context tag inserted, for source-side tagging.

    The tag goes immediately inside the opening quote, before any existing
    markup: ``"Continue"`` becomes ``"{#rtk-main_menu}Continue"``.  If the
    literal already starts with a context tag it is left alone, so the
    operation is idempotent and safe to re-run over an already-tagged tree.
    """
    if literal.startswith("{#"):
        return literal
    return context_tag(screen, prefix=prefix) + literal


def _py_literal(text: str) -> str:
    """A Python string literal safe to embed in generated source.

    ``repr`` handles quotes, backslashes and newlines correctly, but it emits
    ``\\u`` escapes for non-ASCII only when the caller asks; keeping CJK
    readable matters because the user reads and edits this file.
    """
    body = text.replace("\\", "\\\\").replace('"', '\\"')
    body = body.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + body + '"'


def build_patch_source(language: str, overrides: dict[tuple[str, str], str],
                       *, header: str | None = None) -> str:
    """Return the source of the runtime context shim.

    *overrides* maps ``(screen_name, original_text)`` to the translation to
    use while that screen is being evaluated.  Entries whose translation is
    empty are dropped: they would be indistinguishable from "not set".

    The generated block is a ``translate <language> python:`` statement, so
    Ren'Py runs it as part of switching to that language and undoes it when
    switching away.  That is the reason to use a ``translate`` block rather
    than ``init python`` -- an ``init`` hack would stay installed after a
    language change and mis-translate the other language.
    """
    if not overrides:
        raise ToolkitError("no context overrides to write")
    if not _SCREEN_RE.match(language or ""):
        raise ToolkitError("invalid language name: %r" % (language,))

    lines: list[str] = []
    if header:
        for line in header.splitlines():
            lines.append(("# " + line).rstrip())
    lines.append(PATCH_MARKER)
    lines.append("#")
    lines.append("# Generated from the screen dump: when the same English string means")
    lines.append("# different things on different screens, look up the translation by screen.")
    lines.append("# Delete this file to fall back to one translation per string.")
    lines.append("")
    lines.append("translate %s python:" % language)
    lines.append("    import renpy.translation as _rtk_translation")
    lines.append("    import renpy.display.screen as _rtk_screen")
    lines.append("")
    # Group by screen so the file reads like a table and stays diff-friendly:
    # one block per screen, one line per string.
    grouped: dict[str, list[tuple[str, str]]] = {}
    for (screen, text), new in sorted(overrides.items()):
        if not new:
            continue
        grouped.setdefault(screen, []).append((text, new))
    if not grouped:
        raise ToolkitError("every context override had an empty translation")

    lines.append("    _rtk_by_screen = {}")
    for screen in sorted(grouped):
        lines.append("    _rtk_by_screen[%s] = {" % _py_literal(screen))
        for text, new in grouped[screen]:
            lines.append("        %s: %s," % (_py_literal(text), _py_literal(new)))
        lines.append("    }")
    lines.append("")
    lines.append("    _rtk_previous = _rtk_translation.translate_string")
    lines.append("")
    lines.append("    def _rtk_translate_string(s, language=_rtk_translation.Default):")
    lines.append("        _screen = _rtk_screen._current_screen")
    lines.append("        if _screen is not None:")
    # ``screen_name`` is the rendered name, so it is a plain string here. Only
    # the SL2 AST stores it as a list of path parts, and joining *that* would
    # split the name into characters ("x m a s _ o p t i o n").  Accept both
    # shapes rather than guessing.
    lines.append("            _name = _screen.screen_name")
    lines.append("            if not isinstance(_name, str):")
    lines.append('                _name = ".".join(str(_p) for _p in _name)')
    lines.append("            if _name:")
    lines.append("                _group = _rtk_by_screen.get(_name)")
    lines.append("                if _group is not None:")
    lines.append("                    _hit = _group.get(s)")
    lines.append("                    if _hit is not None:")
    lines.append("                        return _hit")
    lines.append("        return _rtk_previous(s, language)")
    lines.append("")
    lines.append("    _rtk_translation.translate_string = _rtk_translate_string")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_patch(directory: str, language: str,
                overrides: dict[tuple[str, str], str], *,
                header: str | None = None, dry_run: bool = True) -> str:
    """Write the shim into *directory* and return its path.

    An existing file is only overwritten when it carries :data:`PATCH_MARKER`;
    a hand-written file that happens to share the name is left alone and the
    call fails loudly instead of destroying work.
    """
    path = os.path.join(directory, patch_filename(language))
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as handle:
            existing = handle.read()
        if PATCH_MARKER not in existing:
            raise ToolkitError(
                "%s exists and was not generated by this tool; refusing to "
                "overwrite it" % path
            )
    source = build_patch_source(language, overrides, header=header)
    if dry_run:
        return path
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\ufeff")
        handle.write(source)
    return path
