"""把 CLI 的整理类命令跑在真实文件上，确认落盘字节正确。

这些用例走的是「子命令 → 磁盘文件 → 重新读回来」的全链路，
而不是直接调模块函数：汉化工程里真正出事的从来是写盘那一层
（BOM 丢了、行尾统一了、备份没生成）。
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _util import temp_dir, write

from renpy_toolkit import cli


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:  # argparse --help
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


SCREENS = 'screen main_menu():\n    textbutton "Start Game" action Start()\n'

SCRIPT = 'label start:\n    e "Hello there."\n'

TL_UNSORTED = """\
translate Chinese start_aaa:

    # e "one"
    e "一"

# game/script.rpy:30
translate chinese start_bbb:

    # e "two"
    e "二"

# game/script.rpy:10
translate chinese start_ccc:

    # e "three"
    e "三"
"""

TL_MULTI = """\
translate Chinese strings:

    old "Start Game"
    new "开始游戏"
    old "Not In Sources"
    new "源里没有"
"""


class TestSort(unittest.TestCase):
    def test_dry_run_leaves_file_alone(self) -> None:
        with temp_dir() as root:
            tl_dir = os.path.join(root, "game", "tl", "Chinese")
            path = os.path.join(tl_dir, "script.rpy")
            write(path, TL_UNSORTED)
            code, out, _ = _run("tl", "sort", tl_dir)
            self.assertEqual(code, cli.EXIT_OK)
            self.assertIn("would write", out)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), TL_UNSORTED)

    def test_write_orders_blocks_by_source_line(self) -> None:
        with temp_dir() as root:
            tl_dir = os.path.join(root, "game", "tl", "Chinese")
            path = os.path.join(tl_dir, "script.rpy")
            write(path, TL_UNSORTED)
            code, _, err = _run("tl", "sort", tl_dir, "--write", "--no-backup")
            self.assertEqual(code, cli.EXIT_OK, err)
            with open(path, encoding="utf-8") as handle:
                body = handle.read()
        self.assertLess(body.index("start_ccc"), body.index("start_bbb"))

    def test_missing_directory_is_not_an_error(self) -> None:
        with temp_dir() as root:
            code, out, _ = _run("tl", "sort", os.path.join(root, "nope"))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("nothing to sort", out)


class TestSplit(unittest.TestCase):
    def test_split_resolves_anchors_and_marks_misses(self) -> None:
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(os.path.join(game, "screens.rpy"), SCREENS)
            tl_dir = os.path.join(game, "tl", "Chinese")
            path = os.path.join(tl_dir, "strings.rpy")
            write(path, TL_MULTI)
            code, out, err = _run("tl", "split", tl_dir, game, "--write", "--no-backup")
            self.assertEqual(code, cli.EXIT_OK, err)
            with open(path, encoding="utf-8") as handle:
                body = handle.read()
        self.assertEqual(body.count("translate Chinese strings:"), 2)
        self.assertIn("# game/screens.rpy:2", body)
        self.assertIn("# (no source match)", body)


class TestHardcoded(unittest.TestCase):
    def test_scan_writes_ledger_and_apply_marks_blocks(self) -> None:
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(os.path.join(game, "screens.rpy"), SCREENS)
            tl_dir = os.path.join(game, "tl", "Chinese")
            write(os.path.join(tl_dir, "strings.rpy"), TL_MULTI)

            # ``--from-tl`` is the fallback path: with no probe dump at hand the
            # ledger is rebuilt from the strings blocks already in the TL tree.
            code, _, err = _run(
                "tl", "hardcoded", "scan", tl_dir, "--language", "Chinese",
                "--from-tl", "--write",
            )
            self.assertEqual(code, cli.EXIT_OK, err)
            ledger_file = os.path.join(tl_dir, "hardcoded.json")
            self.assertTrue(os.path.exists(ledger_file))
            with open(ledger_file, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["language"], "Chinese")
            self.assertEqual(
                sorted(e["old"] for e in payload["entries"]),
                ["Not In Sources", "Start Game"],
            )

            # ``split`` is a precondition: one block holding two pairs cannot be
            # annotated, because the marker is per-block and there is only one.
            code, _, err = _run(
                "tl", "split", tl_dir, game, "--language", "Chinese", "--write",
                "--no-backup",
            )
            self.assertEqual(code, cli.EXIT_OK, err)
            code, out, err = _run(
                "tl", "hardcoded", "apply", tl_dir, "--language", "Chinese", "--write"
            )
            self.assertEqual(code, cli.EXIT_OK, err)
            with open(os.path.join(tl_dir, "strings.rpy"), encoding="utf-8") as handle:
                body = handle.read()
        self.assertEqual(body.count("# hardcoded:"), 2)
        # The anchor must stay the first comment above its block, and the marker
        # must sit between the anchor and the ``translate`` header.
        lines = body.splitlines()
        # Locate the header by the ``old`` line below it, not by the header
        # itself: the header text and the literal are on different lines.
        old_line = next(i for i, l in enumerate(lines) if l.strip() == 'old "Start Game"')
        header = max(i for i in range(old_line) if lines[i].startswith("translate "))
        anchor = next(i for i in range(header) if lines[i].startswith("# game/"))
        marker = next(
            (i for i in range(header) if lines[i].startswith("# hardcoded:")), -1
        )
        self.assertGreaterEqual(marker, 0)
        self.assertLess(marker, header)
        # The anchor comment is attachment metadata for the block, so it may sit
        # above or below the marker, but never further than the marker itself.
        self.assertIn(anchor, range(max(header - 3, 0), header))

    def test_apply_without_ledger_is_an_error(self) -> None:
        with temp_dir() as root:
            tl_dir = os.path.join(root, "game", "tl", "Chinese")
            write(os.path.join(tl_dir, "strings.rpy"), TL_MULTI)
            code, _, err = _run(
                "tl", "hardcoded", "apply", tl_dir, "--language", "Chinese"
            )
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertIn("no ledger", err)

    def test_apply_is_idempotent(self) -> None:
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(os.path.join(game, "screens.rpy"), SCREENS)
            tl_dir = os.path.join(game, "tl", "Chinese")
            write(os.path.join(tl_dir, "strings.rpy"), TL_MULTI)
            _run("tl", "hardcoded", "scan", tl_dir, "--language", "Chinese",
                 "--from-tl", "--write")
            _run("tl", "hardcoded", "apply", tl_dir, "--language", "Chinese", "--write")
            with open(os.path.join(tl_dir, "strings.rpy"), encoding="utf-8") as handle:
                first = handle.read()
            code, out, _ = _run(
                "tl", "hardcoded", "apply", tl_dir, "--language", "Chinese", "--write"
            )
            with open(os.path.join(tl_dir, "strings.rpy"), encoding="utf-8") as handle:
                second = handle.read()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(first, second)
        self.assertIn("no block matched", out)

    def test_scan_from_dump_keeps_only_unwrapped_literals(self) -> None:
        """The ledger must match what ``tl gen`` wrote: no ``_()``, no engine rows."""
        with temp_dir() as root:
            tl_dir = os.path.join(root, "game", "tl", "Chinese")
            os.makedirs(tl_dir, exist_ok=True)
            dump = os.path.join(root, "rtk_strings.json")
            write(dump, json.dumps({"rows": 3, "data": [
                # A hardcoded screen label: kept.
                {"file": "screens.rpy", "line": 10, "text": "Are you sure?",
                 "how": "sl", "screen": "confirm", "engine": False,
                 "wrapped": False},
                # Wrapped in _(): an ordinary strings entry, not hardcoding.
                {"file": "screens.rpy", "line": 20, "text": "Start Game",
                 "how": "sl", "screen": "main_menu", "engine": False,
                 "wrapped": True},
                # Engine-owned: belongs in common.rpy, never in this ledger.
                {"file": "renpy/common/00keymap.rpy", "line": 30, "text": "Open",
                 "how": "sl", "screen": "_exception_actions", "engine": True,
                 "wrapped": True},
            ]}))
            code, out, err = _run(
                "tl", "hardcoded", "scan", tl_dir, "--language", "Chinese",
                "--dump", dump, "--write",
            )
            self.assertEqual(code, cli.EXIT_OK, err)
            with open(os.path.join(tl_dir, "hardcoded.json"), encoding="utf-8") as fh:
                payload = json.load(fh)
        self.assertEqual([e["old"] for e in payload["entries"]], ["Are you sure?"])
        entry = payload["entries"][0]
        self.assertEqual(entry["source"], "game/screens.rpy:10")
        self.assertEqual(entry["context"], "confirm")

    def test_scan_without_dump_or_from_tl_is_an_error(self) -> None:
        with temp_dir() as root:
            tl_dir = os.path.join(root, "game", "tl", "Chinese")
            write(os.path.join(tl_dir, "strings.rpy"), TL_MULTI)
            code, _, err = _run(
                "tl", "hardcoded", "scan", tl_dir, "--language", "Chinese"
            )
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertIn("rtk_strings.json", err)

    def test_rescan_keeps_hand_edited_fields_and_flags_removed(self) -> None:
        with temp_dir() as root:
            tl_dir = os.path.join(root, "game", "tl", "Chinese")
            os.makedirs(tl_dir, exist_ok=True)
            ledger = os.path.join(tl_dir, "hardcoded.json")
            write(ledger, json.dumps({"version": 1, "language": "Chinese", "entries": [
                {"old": "Gone", "new": "旧的", "source": "game/a.rpy:1",
                 "context": "hand note", "added": "2026-01-01",
                 "removed": False, "kind": "hardcoded"},
            ]}, ensure_ascii=False))
            dump = os.path.join(root, "rtk_strings.json")
            write(dump, json.dumps({"rows": 1, "data": [
                {"file": "b.rpy", "line": 5, "text": "Fresh", "how": "pycode",
                 "screen": None, "engine": False, "wrapped": False},
            ]}))
            code, out, err = _run(
                "tl", "hardcoded", "scan", tl_dir, "--language", "Chinese",
                "--dump", dump, "--write",
            )
            self.assertEqual(code, cli.EXIT_OK, err)
            # The "gone" notice goes to stderr, alongside the other warnings.
            self.assertIn("no longer appear", err)
            with open(ledger, encoding="utf-8") as fh:
                payload = json.load(fh)
        by_old = {e["old"]: e for e in payload["entries"]}
        # The vanished entry is kept (not deleted) and flagged instead.
        self.assertTrue(by_old["Gone"]["removed"])
        self.assertEqual(by_old["Gone"]["added"], "2026-01-01")
        self.assertEqual(by_old["Gone"]["context"], "hand note")
        self.assertFalse(by_old["Fresh"]["removed"])


class TestUntranslatable(unittest.TestCase):
    """``tl untranslatable`` reports f-strings without touching the source."""

    def dump(self, root: str, fstrings: list[dict]) -> str:
        path = os.path.join(root, "rtk_strings.json")
        write(path, json.dumps({"rows": 0, "data": [], "fstrings": fstrings}))
        return path

    def test_reports_prose_fstring(self) -> None:
        with temp_dir() as root:
            path = self.dump(root, [
                {"file": "shop.rpy", "line": 189, "how": "pycode",
                 "screen": None, "text": 'f"You bought 1 {item_name}!"'},
            ])
            code, out, _ = _run("tl", "untranslatable", path)
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertIn("shop.rpy:189", out)
        self.assertIn("You bought 1", out)
        # The report must point at the command that fixes it without editing
        # the game's source; editing is only the fallback for manual rows.
        self.assertIn("tl fix-fstrings", out)
        self.assertIn("_(\"You bought 1 [item]!\")", out)

    def test_drops_asset_path_fstring(self) -> None:
        # ``f"{name}.png"`` is a path, not prose; reporting it would be noise.
        with temp_dir() as root:
            path = self.dump(root, [
                {"file": "images.rpy", "line": 7, "how": "pycode",
                 "screen": None, "text": 'f"{name}.png"'},
            ])
            code, out, _ = _run("tl", "untranslatable", path)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("no untranslatable", out)

    def test_missing_dump_is_an_error(self) -> None:
        with temp_dir() as root:
            code, _, err = _run(
                "tl", "untranslatable", os.path.join(root, "nope.json"))
        # InvalidRequestError maps to EXIT_FINDINGS across the whole CLI, not
        # to EXIT_USAGE: a bad path is reported like any other finding.
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertIn("no such dump", err)

    def test_show_all_lists_every_row(self) -> None:
        rows = [
            {"file": "a.rpy", "line": i, "how": "pycode", "screen": None,
             "text": f'f"line {i} of text"'}
            for i in range(1, 26)
        ]
        with temp_dir() as root:
            path = self.dump(root, rows)
            code, out, _ = _run("tl", "untranslatable", path, "--show-all")
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertIn("a.rpy:25", out)
        self.assertNotIn("more (use --show-all", out)


class TestContext(unittest.TestCase):
    """``tl context`` reports strings that mean different things per screen."""

    def dump(self, root: str, rows: list[dict]) -> str:
        path = os.path.join(root, "rtk_strings.json")
        write(path, json.dumps({"rows": len(rows), "data": rows}))
        return path

    def rows(self, text: str, screens: list[str]) -> list[dict]:
        return [
            {"file": "screens.rpy", "line": i + 1, "how": "sl",
             "screen": screen, "text": text, "engine": False, "wrapped": False}
            for i, screen in enumerate(screens)
        ]

    def test_reports_a_word_used_on_two_screens(self) -> None:
        rows = self.rows("Continue", ["main_menu", "gallery"]) \
            + self.rows("Quit", ["main_menu"])
        with temp_dir() as root:
            path = self.dump(root, rows)
            code, out, _ = _run("tl", "context", path, "--language", "chinese")
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertIn("Continue", out)
        self.assertNotIn("Quit", out)

    def test_ignore_removes_known_identifiers(self) -> None:
        rows = self.rows("viewport", ["a_screen", "b_screen"]) \
            + self.rows("Continue", ["main_menu", "gallery"])
        with temp_dir() as root:
            path = self.dump(root, rows)
            code, out, _ = _run("tl", "context", path, "--language", "chinese",
                                "--ignore", "viewport")
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertNotIn("viewport", out)
        self.assertIn("Continue", out)

    def test_ignore_file_is_read_and_comments_skipped(self) -> None:
        rows = self.rows("viewport", ["a_screen", "b_screen"]) \
            + self.rows("Continue", ["main_menu", "gallery"])
        with temp_dir() as root:
            path = self.dump(root, rows)
            names = os.path.join(root, "ignore.txt")
            write(names, "# words that are identifiers\nviewport\n\n")
            code, out, _ = _run("tl", "context", path, "--language", "chinese",
                                "--ignore-file", names)
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertNotIn("viewport", out)

    def test_truncated_list_still_reports_the_real_total(self) -> None:
        # 130 ambiguities printed "20 string(s)" before: the truncated length
        # was used as the headline, so --ignore looked like a no-op.
        rows = []
        for i in range(30):
            rows.extend(self.rows(f"word{i}", ["left", "right"]))
        with temp_dir() as root:
            path = self.dump(root, rows)
            code, out, _ = _run("tl", "context", path, "--language", "chinese")
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertIn("30 string(s)", out)
        self.assertIn("showing 20", out)
        self.assertIn("--show-all", out)

    def test_tag_only_literal_is_not_an_ambiguity(self) -> None:
        # ``{w=0.5}{nw}`` renders a pause; splitting it would mint two keys that
        # look identical on screen, so there is nothing for a translator to do.
        rows = self.rows("{w=0.5}{nw}", ["s1", "s2"]) \
            + self.rows("{image=check_text}", ["s3", "s4"])
        with temp_dir() as root:
            path = self.dump(root, rows)
            code, out, _ = _run("tl", "context", path, "--language", "chinese")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("no ambiguous strings", out)

    def test_missing_ignore_file_is_an_error(self) -> None:
        with temp_dir() as root:
            path = self.dump(root, [])
            code, _, err = _run("tl", "context", path, "--language", "chinese",
                                "--ignore-file", os.path.join(root, "no.txt"))
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertIn("no such ignore file", err)


class TestRpaCli(unittest.TestCase):
    """``rpa pack/list/extract`` end to end.

    These three commands were the only ones with no test coverage at all, and
    all three were broken in ways that only showed up on a real run: ``pack``
    without ``--key`` crashed after writing the header, ``list`` read
    ``archive.path`` (the attribute is ``file``) and ``list --long`` called the
    ``entries`` dict.
    """

    def make(self, root: str) -> tuple[str, str]:
        src = os.path.join(root, "src")
        os.makedirs(src)
        write(os.path.join(src, "a.txt"), "hello archive")
        write(os.path.join(src, "b.txt"), "second")
        return os.path.join(root, "out.rpa"), src

    def test_pack_without_key_roundtrips(self) -> None:
        with temp_dir() as root:
            target, src = self.make(root)
            code, out, err = _run("rpa", "pack", target, src, "--version", "3")
            self.assertEqual(code, cli.EXIT_OK, msg=err)
            code, out, err = _run("rpa", "list", target)
        self.assertEqual(code, cli.EXIT_OK, msg=err)
        self.assertIn("a.txt", out)
        self.assertIn("2 entrie(s)", out)

    def test_list_long_prints_sizes(self) -> None:
        with temp_dir() as root:
            target, src = self.make(root)
            _run("rpa", "pack", target, src, "--version", "3")
            code, out, err = _run("rpa", "list", target, "--long")
        self.assertEqual(code, cli.EXIT_OK, msg=err)
        self.assertIn("13  a.txt", out)
        self.assertIn("6  b.txt", out)

    def test_pack_with_explicit_key_roundtrips(self) -> None:
        with temp_dir() as root:
            target, src = self.make(root)
            _run("rpa", "pack", target, src, "--version", "3",
                 "--key", "1234abcd")
            code, out, err = _run("rpa", "list", target)
        self.assertEqual(code, cli.EXIT_OK, msg=err)
        self.assertIn("a.txt", out)

    def test_extract_writes_the_bytes_back(self) -> None:
        with temp_dir() as root:
            target, src = self.make(root)
            _run("rpa", "pack", target, src, "--version", "3")
            dest = os.path.join(root, "ex")
            code, out, err = _run("rpa", "extract", target, dest)
            self.assertEqual(code, cli.EXIT_OK, msg=err)
            with open(os.path.join(dest, "a.txt"), "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "hello archive")


class TestSelftest(unittest.TestCase):
    """``--selftest`` is what the portable zip offers instead of a manual.

    A user who unzips a folder and runs one file needs a single command that
    either says "this works" or names the broken piece. Its contract is also
    unusually strict -- exit 0 while writing nothing -- so it is tested rather
    than living on as an unverified convenience.
    """

    def test_selftest_passes_on_this_machine(self) -> None:
        code, out, err = _run("--selftest")
        self.assertEqual(code, cli.EXIT_OK, msg=err)
        self.assertIn("self-check passed", out)

    def test_selftest_reports_each_area(self) -> None:
        _code, out, _err = _run("--selftest")
        for area in ("python", "import", "rpa", "tl parse", "commands"):
            self.assertIn(area, out)

    def test_selftest_names_the_version(self) -> None:
        _code, out, _err = _run("--selftest")
        self.assertIn("renpy-toolkit", out)

    def test_selftest_writes_nothing_into_the_cwd(self) -> None:
        with temp_dir() as root:
            before = sorted(os.listdir(root))
            _run("--selftest")
            self.assertEqual(sorted(os.listdir(root)), before)


if __name__ == "__main__":
    unittest.main()
