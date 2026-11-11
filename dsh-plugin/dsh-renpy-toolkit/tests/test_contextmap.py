"""Tests for contextmap: ambiguity detection and the runtime context shim."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _util

from renpy_toolkit import contextmap as C
from renpy_toolkit.errors import ToolkitError


def rows(*specs):
    """Build probe-shaped rows from (text, screen, file) triples."""
    out = []
    for spec in specs:
        text, screen, file = spec
        out.append({"text": text, "screen": screen, "file": file,
                    "line": 1, "how": "sl", "engine": False})
    return out


def python_body(source):
    """Extract and dedent the body of the generated ``translate <l> python:``."""
    lines = source.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("translate ") and line.endswith(" python:"):
            start = i + 1
            break
    if start is None:
        raise AssertionError("no translate python block in:\n" + source)
    body = []
    for line in lines[start:]:
        if line.strip() and not line.startswith("    "):
            break
        body.append(line[4:] if line.startswith("    ") else line)
    return "\n".join(body)


def _run_patch(body, *, screen_name):
    """Execute the generated shim body against stand-in ``renpy`` modules.

    The shim is generated as ``translate <lang> python:`` and it imports
    ``renpy.translation`` / ``renpy.display.screen`` at its top, so the test
    supplies lightweight fakes; that keeps this a pure-Python test that can
    run without a Ren'Py install while still executing the real generated
    lookup logic.
    """
    import types

    translation = types.ModuleType("renpy.translation")

    class _Default(object):
        pass

    translation.Default = _Default

    def _fallback(s, language=None):
        return "FALLBACK"

    translation.translate_string = _fallback

    screen_mod = types.ModuleType("renpy.display.screen")

    class _Screen(object):
        pass

    if screen_name is None:
        screen_mod._current_screen = None
    else:
        holder = _Screen()
        holder.screen_name = screen_name
        screen_mod._current_screen = holder

    renpy_mod = types.ModuleType("renpy")
    display_mod = types.ModuleType("renpy.display")
    renpy_mod.translation = translation
    renpy_mod.display = display_mod
    display_mod.screen = screen_mod

    saved = {k: sys.modules.get(k) for k in
             ("renpy", "renpy.translation", "renpy.display", "renpy.display.screen")}
    sys.modules["renpy"] = renpy_mod
    sys.modules["renpy.translation"] = translation
    sys.modules["renpy.display"] = display_mod
    sys.modules["renpy.display.screen"] = screen_mod
    try:
        scope = {}
        exec(compile(body, "<patch>", "exec"), scope)
        return scope
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class TestStripContext(unittest.TestCase):

    def test_removes_leading_tag(self):
        self.assertEqual(C.strip_context("{#hw}Enter"), "Enter")

    def test_removes_tag_anywhere(self):
        self.assertEqual(C.strip_context("a{#x}b"), "ab")

    def test_leaves_other_tags(self):
        self.assertEqual(C.strip_context("{size=+2}BACK"), "{size=+2}BACK")

    def test_no_tag_is_identity(self):
        self.assertEqual(C.strip_context("Continue"), "Continue")


class TestContextTag(unittest.TestCase):

    def test_basic(self):
        self.assertEqual(C.context_tag("main_menu"), "{#main_menu}")

    def test_prefix(self):
        self.assertEqual(C.context_tag("main_menu", prefix="rtk-"),
                         "{#rtk-main_menu}")

    def test_dotted_name(self):
        self.assertEqual(C.context_tag("store.foo"), "{#store_foo}")

    def test_invalid_name_rejected(self):
        with self.assertRaises(ToolkitError):
            C.context_tag("bad name")

    def test_tag_literal_inserts(self):
        self.assertEqual(C.tag_literal("Continue", "main_menu"),
                         "{#main_menu}Continue")

    def test_tag_literal_is_idempotent(self):
        once = C.tag_literal("Continue", "scr")
        self.assertEqual(C.tag_literal(once, "scr"), once)

    def test_tag_literal_keeps_markup_after_tag(self):
        self.assertEqual(C.tag_literal("{size=-10}Yes", "buy"),
                         "{#buy}{size=-10}Yes")


class TestFindAmbiguities(unittest.TestCase):

    def test_single_screen_is_not_ambiguous(self):
        result = C.find_ambiguities(rows(("Continue", "a", "x.rpy")))
        self.assertEqual(result, [])

    def test_two_screens_is_ambiguous(self):
        result = C.find_ambiguities(rows(
            ("Continue", "a", "x.rpy"),
            ("Continue", "b", "y.rpy"),
        ))
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual(item.text, "Continue")
        self.assertEqual(item.screens, ("a", "b"))
        self.assertEqual(item.count, 2)

    def test_same_screen_twice_is_not_ambiguous(self):
        result = C.find_ambiguities(rows(
            ("Continue", "a", "x.rpy"),
            ("Continue", "a", "y.rpy"),
        ))
        self.assertEqual(result, [])

    def test_sorted_by_count_then_text(self):
        result = C.find_ambiguities(rows(
            ("B", "s1", "f"), ("B", "s2", "f"),
            ("A", "s1", "f"), ("A", "s2", "f"), ("A", "s3", "f"),
        ))
        self.assertEqual([r.text for r in result], ["A", "B"])

    def test_ignore_list(self):
        result = C.find_ambiguities(
            rows(("BACK", "s1", "f"), ("BACK", "s2", "f")),
            ignore={"BACK"},
        )
        self.assertEqual(result, [])

    def test_rows_without_screen_skipped(self):
        result = C.find_ambiguities(rows(
            ("Continue", None, "x.rpy"),
            ("Continue", "a", "x.rpy"),
        ))
        self.assertEqual(result, [])

    def test_invalid_screen_name_skipped(self):
        result = C.find_ambiguities(rows(
            ("x", "not a screen", "f"),
            ("x", "good", "f"),
        ))
        self.assertEqual(result, [])

    def test_three_screens_listed(self):
        result = C.find_ambiguities(rows(
            ("Enter", "a", "f"), ("Enter", "b", "f"), ("Enter", "c", "f"),
        ))
        self.assertEqual(result[0].screens, ("a", "b", "c"))

    def test_keep_restricts_but_keeps_screen_union(self):
        # The realistic shape: the same text occurs twice (screen a, screen b)
        # and also once outside a screen.  filter_strings would collapse it to
        # a single row, so grouping must happen over the raw rows.
        raw = rows(
            ("Continue", "a", "x.rpy"),
            ("Continue", "b", "y.rpy"),
            ("Continue", None, "z.rpy"),
            ("Noise", "a", "x.rpy"), ("Noise", "b", "y.rpy"),
        )
        result = C.find_ambiguities(raw, keep={"Continue"})
        self.assertEqual([r.text for r in result], ["Continue"])
        self.assertEqual(result[0].screens, ("a", "b"))

    def test_keep_excludes_texts_not_listed(self):
        raw = rows(("A", "s1", "f"), ("A", "s2", "f"),
                   ("B", "s1", "f"), ("B", "s2", "f"))
        result = C.find_ambiguities(raw, keep={"A"})
        self.assertEqual([r.text for r in result], ["A"])

    def test_dropping_screen_rows_loses_the_ambiguity(self):
        # Regression guard for the bug this parameter exists to prevent:
        # grouping the filtered (one-row-per-text) output finds nothing.
        raw = rows(("Continue", "a", "x.rpy"), ("Continue", "b", "y.rpy"))
        filtered = [raw[0]]  # what filter_strings hands back
        self.assertEqual(C.find_ambiguities(filtered, keep={"Continue"}), [])

    def test_describe_truncates(self):
        result = C.find_ambiguities(rows(*[("X", "s%d" % i, "f") for i in range(8)]))
        self.assertIn("8 total", result[0].describe())


class TestBuildPatchSource(unittest.TestCase):

    def test_header_and_marker(self):
        src = C.build_patch_source("chinese", {("scr", "A"): "甲"})
        self.assertIn(C.PATCH_MARKER, src)
        self.assertIn("translate chinese python:", src)

    def test_body_is_valid_python(self):
        src = C.build_patch_source("chinese", {
            ("main_menu", "Load"): "读取",
            ("main_menu", "Options"): "设置",
            ("navigation", "Load"): "载入",
        })
        compile(python_body(src), "<patch>", "exec")

    def test_groups_by_screen(self):
        src = C.build_patch_source("chinese", {
            ("b", "X"): "1",
            ("a", "Y"): "2",
        })
        self.assertIn('_rtk_by_screen["a"]', src)
        self.assertIn('_rtk_by_screen["b"]', src)
        self.assertLess(src.index('["a"]'), src.index('["b"]'))

    def test_empty_translation_dropped(self):
        src = C.build_patch_source("chinese", {
            ("scr", "A"): "keep",
            ("scr", "B"): "",
        })
        self.assertIn('"A"', src)
        self.assertNotIn('"B"', src)

    def test_all_empty_raises(self):
        with self.assertRaises(ToolkitError):
            C.build_patch_source("chinese", {("scr", "A"): ""})

    def test_no_overrides_raises(self):
        with self.assertRaises(ToolkitError):
            C.build_patch_source("chinese", {})

    def test_invalid_language_raises(self):
        with self.assertRaises(ToolkitError):
            C.build_patch_source("bad lang", {("scr", "A"): "x"})

    def test_hostile_text_cannot_break_or_inject_code(self):
        # Game text is attacker-controlled and it lands inside a Python
        # literal. A quote/backslash/newline in the wrong place would either
        # make the file unimportable (the game dies on language switch) or let
        # the text run code. Every shape must round-trip as plain data.
        nasty = {
            "quote": 'He said "hi"',
            "single": "It's fine",
            "backslash": r"path\to\file",
            "newline": "line1\nline2",
            "crlf": "a\r\nb",
            "tab": "a\tb",
            "brace": "{i}Braced{/i}",
            "interp": "[player] said",
            "unicode": "中文测试 · émoji 🎉",
            "inject": '"); import os; os.system("echo pwned"); ("',
            "inject2": '\\"); raise SystemExit(9); #',
            "ctrl": "a\x01b\x07c",
            "triple": '"""quoted"""',
        }
        overrides = {("screen_a", text): "译文%d" % i
                     for i, text in enumerate(nasty)}
        body = python_body(C.build_patch_source("zzz", overrides))
        scope = _run_patch(body, screen_name="screen_a")  # SystemExit would escape
        table = scope["_rtk_by_screen"]["screen_a"]
        self.assertEqual(table, {text: new for (_s, text), new in overrides.items()})

    def test_hostile_text_is_not_executed(self):
        marker = '"); __import__("sys").exit(7); ("'
        src = C.build_patch_source("zzz", {("s", marker): "x"})
        body = python_body(src)
        # Parsing alone must succeed; a literal that broke out would not parse.
        compile(body, "<patch>", "exec")
        scope = _run_patch(body, screen_name="s")
        self.assertEqual(scope["_rtk_translate_string"](marker), "x")

    def test_screen_name_is_not_rejoined_as_characters(self):
        # ``screen_name`` is a plain string at runtime; joining it would turn
        # "event_option" into "e v e n t _ o p t i o n" and every lookup missed.
        src = C.build_patch_source("chinese", {("event_option", "Continue"): "继续"})
        body = python_body(src)
        self.assertNotIn('" ".join(_screen.screen_name)', body)

        scope = _run_patch(body, screen_name="event_option")
        self.assertEqual(scope["_rtk_translate_string"]("Continue"), "继续")
        self.assertEqual(scope["_rtk_translate_string"]("Absent"), "FALLBACK")

    def test_screen_name_list_shape_still_works(self):
        src = C.build_patch_source("chinese", {("a.b", "X"): "甲"})
        scope = _run_patch(python_body(src), screen_name=["a.b"])
        self.assertEqual(scope["_rtk_translate_string"]("X"), "甲")

    def test_unlisted_screen_falls_back(self):
        src = C.build_patch_source("chinese", {("listed", "X"): "甲"})
        scope = _run_patch(python_body(src), screen_name="other")
        self.assertEqual(scope["_rtk_translate_string"]("X"), "FALLBACK")

    def test_no_screen_falls_back(self):
        src = C.build_patch_source("chinese", {("listed", "X"): "甲"})
        scope = _run_patch(python_body(src), screen_name=None)
        self.assertEqual(scope["_rtk_translate_string"]("X"), "FALLBACK")

    def test_quotes_escaped(self):
        src = C.build_patch_source("chinese", {("scr", 'He said "hi"'): "他说「嗨」"})
        compile(python_body(src), "<patch>", "exec")
        self.assertIn('He said \\"hi\\"', src)

    def test_newlines_escaped(self):
        src = C.build_patch_source("chinese", {("scr", "a\nb"): "x"})
        compile(python_body(src), "<patch>", "exec")

    def test_chinese_stays_readable(self):
        src = C.build_patch_source("chinese", {("scr", "Continue"): "继续"})
        self.assertIn("继续", src)
        self.assertNotIn("\\u7ee7", src)

    def test_restores_previous_function(self):
        body = python_body(C.build_patch_source("chinese", {("s", "A"): "甲"}))
        self.assertIn("_rtk_previous = _rtk_translation.translate_string", body)
        self.assertIn("_rtk_translation.translate_string = _rtk_translate_string", body)
        self.assertIn("return _rtk_previous(s, language)", body)

    def test_checks_current_screen(self):
        body = python_body(C.build_patch_source("chinese", {("s", "A"): "甲"}))
        self.assertIn("_rtk_screen._current_screen", body)
        self.assertIn("_name = _screen.screen_name", body)


class TestPatchFilename(unittest.TestCase):

    def test_simple(self):
        self.assertEqual(C.patch_filename("chinese"), "_rtk_context_chinese.rpy")

    def test_spaces_replaced(self):
        self.assertEqual(C.patch_filename("Schinese trad"),
                         "_rtk_context_Schinese_trad.rpy")

    def test_same_length_as_source(self):
        # The name must not end in an odd character set that Ren'Py treats as
        # a different file type; .rpy is what we want.
        self.assertTrue(C.patch_filename("zh").endswith(".rpy"))


class TestWritePatch(unittest.TestCase):

    def setUp(self):
        self.dir = _util.tempdir_for(self)

    def test_dry_run_writes_nothing(self):
        path = C.write_patch(self.dir, "chinese", {("s", "A"): "甲"}, dry_run=True)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(path.endswith("_rtk_context_chinese.rpy"))

    def test_write_creates_file_with_bom(self):
        path = C.write_patch(self.dir, "chinese", {("s", "A"): "甲"}, dry_run=False)
        with open(path, "rb") as handle:
            raw = handle.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"translate chinese python:", raw)

    def test_rewrite_own_output(self):
        p1 = C.write_patch(self.dir, "chinese", {("s", "A"): "甲"}, dry_run=False)
        p2 = C.write_patch(self.dir, "chinese", {("s", "A"): "乙"}, dry_run=False)
        self.assertEqual(p1, p2)
        with open(p2, "r", encoding="utf-8-sig") as handle:
            self.assertIn("乙", handle.read())

    def test_refuses_foreign_file(self):
        path = os.path.join(self.dir, C.patch_filename("chinese"))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("translate chinese python:\n    hand_written = True\n")
        with self.assertRaises(ToolkitError):
            C.write_patch(self.dir, "chinese", {("s", "A"): "甲"}, dry_run=False)
        # the hand-written file must survive untouched
        with open(path, "r", encoding="utf-8") as handle:
            self.assertIn("hand_written", handle.read())

    def test_header_prefixed_with_hash(self):
        path = C.write_patch(self.dir, "chinese", {("s", "A"): "甲"},
                             header="line one\nline two", dry_run=False)
        with open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
        self.assertIn("# line one", text)
        self.assertIn("# line two", text)


if __name__ == "__main__":
    unittest.main()
