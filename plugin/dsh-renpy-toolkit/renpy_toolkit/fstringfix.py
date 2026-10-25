"""Fix strings that are assembled at runtime by f-strings.

Why this module exists
----------------------
:func:`renpy_toolkit.tlgen.literals` reports f-strings instead of adding them to
the translation table, because a template key can never match.  The engine's
lookup order makes that certain::

    # renpy/substitutions.py
    def substitute(s, scope=None, force=False, translate=True):
        if translate:
            s = renpy.translation.translate_string(s)   # <- lookup happens here
        ...
        s = interpolate(s, variables)                    # <- [var] expanded here

``f"You bought 1 {name}!"`` is therefore already ``"You bought 1 Sandwich!"``
by the time the table is consulted, and ``StringTranslator.translate`` only
strips ``{#context}`` tags -- it never expands ``[var]``.  Measured on a
release build: registering ``old "You bought 1 [item_name]!"`` leaves the
finished sentence in English.

Two ways out, neither of which edits the game
---------------------------------------------
``exact``
    The finished sentence *is* looked up again when a screen renders it
    (``renpy/text/text.py``: ``set_text`` calls ``substitutions.substitute``
    for every string it is handed), so entries keyed on the exact results do
    work.  This needs the set of results, which the caller supplies with
    ``--values field=a,b,c``.

``pattern``
    A ``translate <language> python:`` block wraps
    ``renpy.translation.translate_string`` and rewrites the finished sentence
    from a pattern.  Works when the result set is open-ended (item names the
    caller cannot enumerate).

Editing the source line (``_("...[item]...").format(item=item)``) stays
possible and is the right answer when translation needs full control, but it
is *not* required, and it changes the game.

Both generated files are additive and reversible: delete the file and the game
is back to its original behaviour.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field

from .errors import InvalidRequestError, ToolkitError

__all__ = [
    "FstringFix",
    "PATCH_MARKER",
    "STRINGS_MARKER",
    "analyse",
    "build_pattern_patch",
    "build_strings_block",
    "classify",
    "existing_olds",
    "parse_values",
    "patch_filename",
    "strings_filename",
    "write_fixes",
]

#: Marker written into both generated files. :func:`write_fixes` only
#: overwrites a file that carries it, so a hand-written file that happens to
#: share the name is never destroyed.
PATCH_MARKER = "# renpy-toolkit: f-string patch"
STRINGS_MARKER = "# renpy-toolkit: f-string strings"

_LANGUAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")

#: ``exact``: entries for enumerated results.  ``pattern``: a regex shim.
#: ``manual``: cannot be expressed either way (format spec, conversion, or a
#: compound expression in the field) -- the source line has to change.
ROUTES = ("exact", "pattern", "manual")


@dataclass
class FstringFix:
    """One f-string, classified and ready to render."""

    file: str
    line: int
    source: str
    #: Template with each field replaced by ``{0}``, ``{1}``... or ``None``
    #: when the f-string cannot be templated at all.
    template: str | None = None
    #: Field expressions in order (``item_name``, ``to_mb(done_bytes)``...).
    fields: list[str] = field(default_factory=list)
    route: str = "manual"
    #: Why this route was chosen; shown to the caller, so keep it concrete.
    reason: str = ""
    #: Concrete results, filled in when the caller enumerates every field.
    values: list[str] = field(default_factory=list)


def _language_ok(language: str) -> str:
    if not _LANGUAGE_RE.match(language or ""):
        raise InvalidRequestError(
            "invalid language name %r: expected a plain identifier" % (language,)
        )
    return language


def _py_str(text: str) -> str:
    """A Python string literal safe to embed in generated source."""
    body = text.replace("\\", "\\\\").replace('"', '\\"')
    body = body.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + body + '"'


def _tl_str(text: str) -> str:
    """A Ren'Py ``"..."`` literal for a strings block."""
    from .tl import format_value

    return '"' + format_value(text) + '"'


def _parse(text: str) -> ast.JoinedStr | None:
    """Parse the dump's ``f'...'`` source back into an AST node."""
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError:
        return None
    return node if isinstance(node, ast.JoinedStr) else None


def classify(row: dict) -> FstringFix:
    """Decide how one dump row can be repaired.

    A field with a format spec (``{n:.1f}``) or a conversion (``{x!r}``) is
    rejected: a pattern would capture the *formatted* value and the replacement
    could not reproduce it, so emitting one would silently change how numbers
    render.
    """
    source = str(row.get("text") or "")
    fix = FstringFix(
        file=str(row.get("file") or ""),
        line=int(row.get("line") or 0),
        source=source,
    )
    node = _parse(source)
    if node is None:
        fix.reason = "the source cannot be parsed back into an f-string"
        return fix

    pieces: list[str] = []
    unusable = ""
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            pieces.append(part.value)
            continue
        if not isinstance(part, ast.FormattedValue):
            fix.reason = "unsupported f-string part"
            return fix
        expr = ast.unparse(part.value)
        # Number by field, not by position in ``values``: the literal chunks sit
        # between the fields and would shift the indices.
        pieces.append("{%d}" % len(fix.fields))
        fix.fields.append(expr)
        if unusable:
            continue
        if part.conversion not in (-1, None):
            unusable = "field %r has a conversion (!r/!s/!a); a pattern cannot reproduce it" % (expr,)
        elif part.format_spec is not None:
            unusable = "field %r has a format spec; a pattern cannot reproduce it" % (expr,)
        elif not isinstance(part.value, ast.Name):
            unusable = ("field %r is an expression; a pattern would capture its "
                        "rendered form" % (expr,))

    fix.template = "".join(pieces)
    if not fix.fields:
        fix.reason = "no interpolated field, so it is an ordinary constant"
        return fix
    if unusable:
        # Keep the template: the caller still needs the fixed text to decide
        # whether this row is prose worth reporting at all.
        fix.route = "manual"
        fix.reason = unusable
        return fix

    fix.route = "pattern"
    fix.reason = "open-ended result set: matched with a pattern"
    return fix


def _is_prose(template: str) -> bool:
    """Is the fixed part of the template text a player could read?

    ``f'{percent}% — {to_mb(done):.0f} MB'`` has prose around its fields;
    ``f'{base}{frame}{ext}'`` does not.  The existing heuristic already knows
    how to answer that, so reuse it instead of inventing a second rule set.
    """
    from .tlgen import looks_translatable

    return looks_translatable(template)


def parse_values(specs: list[str] | None) -> dict[str, list[str]]:
    """Turn ``["item_name=a,b", "who=x"]`` into a mapping."""
    mapping: dict[str, list[str]] = {}
    for spec in specs or []:
        name, sep, raw = spec.partition("=")
        name = name.strip()
        if not sep or not name:
            raise InvalidRequestError(
                "--values needs FIELD=v1,v2,... but got %r" % (spec,)
            )
        values = [item.strip() for item in raw.split(",") if item.strip()]
        if not values:
            raise InvalidRequestError("--values %s listed no values" % (name,))
        mapping[name] = values
    return mapping


def analyse(rows: list[dict], *, values: dict[str, list[str]] | None = None,
            only: set[tuple[str, int]] | None = None,
            already: set[str] | None = None) -> list[FstringFix]:
    """Classify every row, dropping the ones that never need translating.

    *only* restricts the work to ``(file, line)`` pairs, so a caller can act on
    one reported row without touching the rest of the game.

    Rows whose fixed text is not prose (``f'{percent}%'``, ``f'{base}{frame}'``)
    are dropped: they render numbers and file names, and entries for them would
    only pad the table.

    Enumerating every field through *values* promotes a row from ``pattern`` to
    ``exact``.  Enumerating only some fields does not: a partial product would
    translate some results and leave the rest in English, which is worse than a
    pattern that covers all of them.

    *already* holds ``old`` values the game's other TL files register.  A second
    entry for one of them is not a merge, it is a startup crash (the engine
    raises ``A translation for "..." already exists``), so those results are
    dropped here rather than written and discovered later.
    """
    values = values or {}
    already = already or set()
    fixes: list[FstringFix] = []
    for row in rows:
        pair = (str(row.get("file") or ""), int(row.get("line") or 0))
        if only is not None and pair not in only:
            continue
        fix = classify(row)
        if not fix.fields:
            # A field-free f-string is a plain constant the engine already
            # translates; there is nothing to repair.
            continue
        if fix.template is not None and not _is_prose(fix.template):
            continue
        if fix.template is not None:
            pools = [values.get(name) for name in fix.fields]
            if pools and all(pool is not None for pool in pools):
                products = _expand(fix.template, list(pools))  # type: ignore[arg-type]
                fresh = [text for text in products if text not in already]
                if fresh:
                    fix.route = "exact"
                    fix.values = fresh
                    fix.reason = "every field was enumerated: %d exact result(s)" % len(fresh)
                    if len(fresh) < len(products):
                        fix.reason += " (%d already translated elsewhere)" % (
                            len(products) - len(fresh))
                elif products:
                    # Every result is already registered by another file; a
                    # second entry would crash the engine at startup.
                    fix.route = "manual"
                    fix.reason = ("every result is already translated by another "
                                  "TL file; a second entry would be a duplicate")
        fixes.append(fix)
    return fixes


def _expand(template: str, pools: list[list[str]]) -> list[str]:
    """Every combination of *pools*, substituted into *template*."""
    results = [template]
    for index, pool in enumerate(pools):
        grown: list[str] = []
        marker = "{%d}" % index
        for prefix in results:
            for value in pool:
                grown.append(prefix.replace(marker, value, 1))
        results = grown
    return [item for item in results if "{" not in item and "}" not in item]


def build_strings_block(language: str, fixes: list[FstringFix], *,
                        header: str | None = None) -> str:
    """Render the ``exact`` fixes as a ``translate <language> strings:`` block.

    The ``new`` value repeats the ``old`` one, matching the skeleton policy used
    everywhere else in this toolkit: an empty translation would blank the text
    at runtime (``StringTranslator.translate`` returns any value that is not
    ``None``, including ``""``).  Translators fill these in like any other
    entry.
    """
    _language_ok(language)
    exact = [fix for fix in fixes if fix.route == "exact" and fix.values]
    if not exact:
        raise ToolkitError("no exact-result fixes to write")

    lines: list[str] = []
    if header:
        for line in header.splitlines():
            lines.append(("# " + line).rstrip())
    lines.append(STRINGS_MARKER)
    lines.append("#")
    lines.append("# Strings that f-strings assemble at runtime.  The engine looks these")
    lines.append("# up only after the sentence is built, so the template form can never")
    lines.append("# match; the exact results below do.  Translate them like any other")
    lines.append("# strings entry.")
    lines.append("#")
    lines.append("# Generated file: rewrite it with the toolkit rather than editing it, and")
    lines.append("# delete it to fall back to English.")
    lines.append("")
    lines.append("translate %s strings:" % language)
    lines.append("")
    # Several f-strings can expand to the same result ("You bought 1 ...!"
    # appears at three sites).  Emitting one entry per site makes the engine
    # raise ``A translation for "..." already exists`` and the game will not
    # start, so each literal is written once and later sites just point at it.
    emitted: dict[str, tuple[str, int]] = {}
    for fix in exact:
        fresh = [value for value in fix.values if value not in emitted]
        if not fresh:
            lines.append("    # %s:%d  (every result already covered above)"
                         % (fix.file, fix.line))
            lines.append("")
            continue
        lines.append("    # %s:%d" % (fix.file, fix.line))
        for value in fresh:
            emitted[value] = (fix.file, fix.line)
            lines.append("    old %s" % _tl_str(value))
            lines.append("    new %s" % _tl_str(value))
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _to_regex(template: str) -> str:
    """Turn a ``{0}``-template into an anchored pattern.

    The literal parts are escaped and each field becomes ``(.+)``.  Anchoring
    both ends keeps a short result from matching a longer sentence.

    ``re.escape`` leaves ``\\n`` as a backslash followed by a real newline inside
    the pattern, which the regex engine rejects as an incomplete escape; the
    escape sequences are therefore written out explicitly.
    """
    chunks = re.split(r"\{\d+\}", template)
    if len(chunks) < 2:
        raise ToolkitError("template has no field: %r" % (template,))
    escaped = [_escape_literal(chunk) for chunk in chunks]
    return "^" + "(.+)".join(escaped) + "$"


def _escape_literal(text: str) -> str:
    """Regex-escape a literal chunk, spelling control characters as escapes."""
    out: list[str] = []
    for ch in text:
        if ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def build_pattern_patch(language: str, fixes: list[FstringFix], *,
                        header: str | None = None,
                        translations: dict[str, str] | None = None) -> str:
    """Render the ``pattern`` fixes as a ``translate <language> python:`` shim.

    Each pattern captures the fields in order and the replacement refers to them
    as ``{0}``, ``{1}``.  Captured text is passed through the previous
    ``translate_string`` first, so an item name that already has a table entry
    comes out in the target language too.

    A ``translations`` entry keyed on the template replaces the placeholder
    replacement for that pattern.  Without one the replacement repeats the
    original sentence, which is a no-op that keeps the file valid while telling
    the translator exactly what to fill in.

    ``translate <language> python:`` runs on switching *to* the language and is
    not executed on switching away, so the wrapper re-checks the live language
    before rewriting anything.
    """
    _language_ok(language)
    pattern_fixes = [fix for fix in fixes if fix.route == "pattern" and fix.template]
    if not pattern_fixes:
        raise ToolkitError("no pattern fixes to write")
    translations = translations or {}

    lines: list[str] = []
    if header:
        for line in header.splitlines():
            lines.append(("# " + line).rstrip())
    lines.append(PATCH_MARKER)
    lines.append("#")
    lines.append("# Strings that f-strings assemble at runtime cannot be reached by the")
    lines.append("# translation table: the engine translates the finished sentence, long")
    lines.append("# after the template is gone.  This block rewrites those sentences as")
    lines.append("# they are looked up.")
    lines.append("#")
    lines.append("# {0}, {1}... are the captured fields, each translated on its own first,")
    lines.append("# so put the field back exactly where the target language wants it.")
    lines.append("# Delete this file to fall back to English.")
    lines.append("")
    lines.append("translate %s python:" % language)
    lines.append("    import re as _rtk_re")
    lines.append("    import renpy.translation as _rtk_translation")
    lines.append("")
    lines.append("    _rtk_patterns = [")
    seen: set[str] = set()
    for fix in pattern_fixes:
        template = fix.template or ""
        # The same sentence is often assembled at several call sites (one
        # f-string per shop, say).  Compiling it once is enough, and repeating
        # it would make the table look like it handles more than it does.
        if template in seen:
            lines.append("        # %s:%d  (same sentence as above)"
                         % (fix.file, fix.line))
            continue
        seen.add(template)
        regex = _to_regex(template)
        replacement = translations.get(template, template)
        lines.append("        # %s:%d  %s" % (fix.file, fix.line,
                                           fix.source.replace("\n", "\\n")))
        if template not in translations:
            lines.append("        # TODO: translate the right-hand side.")
        lines.append("        (_rtk_re.compile(%s), %s),"
                     % (_py_str(regex), _py_str(replacement)))
    lines.append("    ]")
    lines.append("")
    lines.append("    _rtk_previous = _rtk_translation.translate_string")
    lines.append("")
    lines.append("    def _rtk_translate_string(s, language=_rtk_translation.Default):")
    lines.append("        if not isinstance(s, str):")
    lines.append("            return _rtk_previous(s, language)")
    # The shim stays installed after the player switches away, so guard on the
    # live language rather than trusting the block to run once.
    lines.append("        if renpy.game.preferences.language != %s:" % _py_str(language))
    lines.append("            return _rtk_previous(s, language)")
    lines.append("        for _pattern, _replacement in _rtk_patterns:")
    lines.append("            _match = _pattern.match(s)")
    lines.append("            if _match is None:")
    lines.append("                continue")
    lines.append("            _fields = [_rtk_previous(_g, language)")
    lines.append("                       for _g in _match.groups()]")
    lines.append("            try:")
    lines.append("                return _replacement.format(*_fields)")
    lines.append("            except (IndexError, KeyError, ValueError):")
    lines.append("                return _rtk_previous(s, language)")
    lines.append("        return _rtk_previous(s, language)")
    lines.append("")
    lines.append("    _rtk_translation.translate_string = _rtk_translate_string")
    lines.append("")
    return "\n".join(lines) + "\n"


def strings_filename(language: str) -> str:
    _language_ok(language)
    return "_rtk_fstrings.rpy"


def patch_filename(language: str) -> str:
    _language_ok(language)
    return "_rtk_fstrings_patch.rpy"


def _write_guarded(path: str, source: str, marker: str) -> str:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as handle:
            existing = handle.read()
        if marker not in existing:
            raise ToolkitError(
                "%s exists and was not generated by this tool; refusing to "
                "overwrite it" % path
            )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\ufeff")
        handle.write(source)
    return path


def write_fixes(directory: str, language: str, fixes: list[FstringFix], *,
                header: str | None = None,
                translations: dict[str, str] | None = None,
                dry_run: bool = True) -> list[str]:
    """Write whichever of the two files the fixes call for.

    Returns the paths that would be written (``dry_run``) or were written.  Only
    files with work to do are produced, so a game with no enumerable f-strings
    never grows an empty patch file.
    """
    _language_ok(language)
    written: list[str] = []
    jobs = [
        ("exact", strings_filename(language), STRINGS_MARKER,
         lambda: build_strings_block(language, fixes, header=header)),
        ("pattern", patch_filename(language), PATCH_MARKER,
         lambda: build_pattern_patch(language, fixes, header=header,
                                     translations=translations)),
    ]
    for route, name, marker, render in jobs:
        if not any(fix.route == route for fix in fixes):
            continue
        source = render()
        path = os.path.join(directory, name)
        if dry_run:
            written.append(path)
            continue
        written.append(_write_guarded(path, source, marker))
    return written


def existing_olds(directory: str, *, exclude: tuple[str, ...] = ()) -> set[str]:
    """Collect the ``old`` values already registered by other TL files.

    A ``strings`` block that repeats an ``old`` value already defined elsewhere
    in the same language makes the engine raise ``A translation for "..."
    already exists`` at startup.  Callers use this to drop results the game (or
    an earlier toolkit run) already translates.
    """
    from .tl import parse_tl_file

    found: set[str] = set()
    # The names are relative to *directory*, not to the process working
    # directory: resolving them with a bare ``abspath`` silently excluded
    # nothing.
    skip = {os.path.normcase(os.path.abspath(os.path.join(directory, name)))
            for name in exclude}
    if not os.path.isdir(directory):
        return found
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if not name.endswith(".rpy"):
                continue
            path = os.path.join(root, name)
            if os.path.normcase(os.path.abspath(path)) in skip:
                continue
            try:
                parsed = parse_tl_file(path)
            except Exception:
                continue
            for block in parsed.blocks:
                for pair in block.strings:
                    if pair.old:
                        found.add(pair.old)
    return found
