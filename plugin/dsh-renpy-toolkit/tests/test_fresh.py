"""Tests for :mod:`renpy_toolkit.fresh` -- cloning a tl/ tree to a new language."""

import os
import unittest

import _util as u
from renpy_toolkit import fresh
from renpy_toolkit.errors import ToolkitError
from renpy_toolkit.tl import parse_tl_file


SPANISH = """\
# game/script.rpy:7
translate Spanish start_636ae3f5:

    # e "Thank you for taking a look at the Ren'Py translation framework."
    e "Gracias por echar un vistazo al marco de traduccion de Ren'Py."

# game/script.rpy:9
translate Spanish start_9e4cbe22:

    # e "You have {image=heart.png} [points] points."
    e "Tienes {image=heart.png} [points] puntos."

translate Spanish strings:

    old "Start Game"
    new "Empezar"
"""

PLAIN = """\
translate Spanish start_deadbeef:

    # e "A line."
    e "Una linea."
"""


def _project(root):
    """Build a fake distribution: game/tl/Spanish/*.rpy and nothing else."""
    game = os.path.join(root, "game")
    os.makedirs(os.path.join(game, "tl", "Spanish"), exist_ok=True)
    u.write(os.path.join(game, "tl", "Spanish", "script.rpy"), SPANISH)
    u.write(os.path.join(game, "tl", "Spanish", "other.rpy"), PLAIN)
    return root


class TestIdentifierFor(unittest.TestCase):
    def test_matches_known_engine_identifier(self):
        # Real value taken from the Ren'Py docs' translation example:
        #   label start:
        #       e "Thank you for taking a look at the Ren'Py translation framework."
        #   => start_636ae3f5
        self.assertEqual(
            fresh.identifier_for(
                "start", "e", "Thank you for taking a look at the Ren'Py translation framework."
            ),
            "start_636ae3f5",
        )

    def test_matches_identifier_in_a_shipped_translation(self):
        # And one taken from a real shipped ``tl/`` file:
        #   label night_visit: t "She fell asleep reading?"
        self.assertEqual(
            fresh.identifier_for("night_visit", "t", "She fell asleep reading?"),
            "night_visit_00144250",
        )

    def test_narration_has_no_who(self):
        got = fresh.identifier_for("start", None, "Hello, world!")
        self.assertTrue(got.startswith("start_"))
        self.assertEqual(len(got), len("start_") + 8)

    def test_narration_and_sayer_differ(self):
        self.assertNotEqual(
            fresh.identifier_for("start", None, "Hi"),
            fresh.identifier_for("start", "e", "Hi"),
        )

    def test_label_dots_become_underscores(self):
        self.assertTrue(
            fresh.identifier_for("chapter.one", "e", "Hi").startswith("chapter_one_")
        )

    def test_arguments_change_the_hash(self):
        plain = fresh.identifier_for("start", "e", "Hi")
        tagged = fresh.identifier_for("start", "e", "Hi", arguments="(multiple=2)")
        self.assertNotEqual(plain, tagged)


class TestEncodeSayString(unittest.TestCase):
    def test_runs_of_spaces_escape_the_second_space(self):
        # Ren'Py escapes the second space of every pair so the lexer, which
        # collapses spaces, keeps the original spacing.
        self.assertEqual(fresh._encode_say_string("a  b"), '"a \\ b"')

    def test_triple_space_escapes_every_second(self):
        # ``re.sub`` never rescans its own replacement, so the pair straddling
        # the escaped space is not re-examined: space 2 is escaped, space 3 is
        # left bare.  (Verified against ``renpy.translation.encode_say_string``.)
        self.assertEqual(fresh._encode_say_string("a   b"), '"a \\ \\ b"')

    def test_quotes_and_newlines_escape(self):
        self.assertEqual(fresh._encode_say_string('say "x"\n'), '"say \\"x\\"\\n"')

    def test_backslash_first(self):
        self.assertEqual(fresh._encode_say_string("a\\b"), '"a\\\\b"')


class TestTranslateDirFor(unittest.TestCase):
    def test_accepts_project_root(self):
        with u.temp_dir() as root:
            os.makedirs(os.path.join(root, "game"))
            got = fresh.translate_dir_for(root, "chinese")
            self.assertEqual(got, os.path.join(os.path.abspath(root), "game", "tl", "chinese"))

    def test_accepts_game_dir_itself(self):
        with u.temp_dir() as root:
            game = os.path.join(root, "game")
            os.makedirs(game)
            self.assertEqual(
                fresh.translate_dir_for(game, "chinese"),
                os.path.join(os.path.abspath(game), "tl", "chinese"),
            )

    def test_rejects_non_project(self):
        with u.temp_dir() as root:
            with self.assertRaises(ToolkitError):
                fresh.translate_dir_for(root, "chinese")


class TestPlanFreshTl(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with u.temp_dir() as root:
            _project(root)
            result = fresh.plan_fresh_tl(root, "chinese", dry_run=True)
            self.assertEqual(result.source, "tl/Spanish")
            self.assertGreater(result.blocks, 0)
            self.assertEqual(result.files, [])
            self.assertFalse(os.path.exists(os.path.join(root, "game", "tl", "chinese")))

    def test_writes_retargeted_skeleton(self):
        with u.temp_dir() as root:
            _project(root)
            result = fresh.plan_fresh_tl(root, "chinese", dry_run=False)
            self.assertEqual(len(result.files), 2)
            self.assertEqual(result.blocks, 3)
            self.assertEqual(result.strings, 1)

            target = os.path.join(root, "game", "tl", "chinese", "script.rpy")
            tl = parse_tl_file(target)
            for block in tl.blocks:
                self.assertEqual(block.language, "chinese")
            # Every translation is empty, but the source text survives.
            for block in tl.blocks:
                if block.kind == "translate":
                    self.assertTrue(block.is_empty)
            text = "\n".join(line.text for line in tl.lines)
            self.assertIn('e ""', text)
            self.assertIn(
                '# e "Thank you for taking a look at the Ren\'Py translation framework."',
                text,
            )
            self.assertNotIn("Spanish", text)
            self.assertNotIn("Gracias", text)

    def test_source_comments_survive(self):
        with u.temp_dir() as root:
            _project(root)
            fresh.plan_fresh_tl(root, "chinese", dry_run=False)
            text = u.read_text(os.path.join(root, "game", "tl", "chinese", "script.rpy"))
            self.assertIn(
                '# e "Thank you for taking a look at the Ren\'Py translation framework."',
                text,
            )
            self.assertIn('# e "You have {image=heart.png} [points] points."', text)

    def test_identifiers_and_linenos_preserved(self):
        with u.temp_dir() as root:
            _project(root)
            fresh.plan_fresh_tl(root, "chinese", dry_run=False)
            src = parse_tl_file(os.path.join(root, "game", "tl", "Spanish", "script.rpy"))
            dst = parse_tl_file(os.path.join(root, "game", "tl", "chinese", "script.rpy"))
            self.assertEqual(
                [b.identifier for b in src.blocks],
                [b.identifier for b in dst.blocks],
            )
            self.assertEqual(
                [b.source_comment for b in src.blocks],
                [b.source_comment for b in dst.blocks],
            )

    def test_second_run_keeps_existing_files(self):
        with u.temp_dir() as root:
            _project(root)
            fresh.plan_fresh_tl(root, "chinese", dry_run=False)
            target = os.path.join(root, "game", "tl", "chinese", "script.rpy")
            u.write(target, "# hand edited\n")

            again = fresh.plan_fresh_tl(root, "chinese", dry_run=False)
            self.assertEqual(u.read_text(target), "# hand edited\n")
            self.assertEqual(again.files, [])  # other.rpy already exists too
            self.assertEqual(len(again.skipped_existing), 2)

    def test_force_overwrites(self):
        with u.temp_dir() as root:
            _project(root)
            fresh.plan_fresh_tl(root, "chinese", dry_run=False)
            target = os.path.join(root, "game", "tl", "chinese", "script.rpy")
            u.write(target, "# hand edited\n")
            fresh.plan_fresh_tl(root, "chinese", force=True, dry_run=False)
            self.assertNotEqual(u.read_text(target), "# hand edited\n")

    def test_bom_and_line_endings_kept(self):
        with u.temp_dir() as root:
            game = os.path.join(root, "game")
            os.makedirs(os.path.join(game, "tl", "None"))
            crlf = SPANISH.replace("\n", "\r\n")
            u.write(os.path.join(game, "tl", "None", "common.rpym"), "\ufeff" + crlf)
            fresh.plan_fresh_tl(root, "chinese", dry_run=False)
            raw = u.read_bytes(os.path.join(game, "tl", "chinese", "common.rpym"))
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"\r\n", raw)

    def test_explicit_source(self):
        with u.temp_dir() as root:
            _project(root)
            other = os.path.join(root, "elsewhere")
            u.write(os.path.join(other, "only.rpy"), SPANISH)
            result = fresh.plan_fresh_tl(
                root, "chinese", source=other, dry_run=False
            )
            self.assertEqual(
                [os.path.basename(p) for p in result.files], ["only.rpy"]
            )

    def test_explicit_source_dry_run_lists_only_planned(self):
        # ``planned`` is what a dry run reports; ``files`` stays about writes.
        with u.temp_dir() as root:
            _project(root)
            other = os.path.join(root, "elsewhere")
            u.write(os.path.join(other, "only.rpy"), SPANISH)
            result = fresh.plan_fresh_tl(root, "chinese", source=other, dry_run=True)
            self.assertEqual(
                [os.path.basename(p) for p in result.planned], ["only.rpy"]
            )
            self.assertEqual(result.files, [])
            self.assertGreater(result.blocks, 0)
            self.assertFalse(
                os.path.exists(os.path.join(root, "game", "tl", "chinese"))
            )

    def test_missing_source_raises(self):
        with u.temp_dir() as root:
            os.makedirs(os.path.join(root, "game"))
            with self.assertRaises(ToolkitError) as ctx:
                fresh.plan_fresh_tl(root, "chinese")
            self.assertIn("translate", str(ctx.exception))

    def test_prefers_real_translation_over_none(self):
        with u.temp_dir() as root:
            game = os.path.join(root, "game")
            os.makedirs(os.path.join(game, "tl", "None"))
            os.makedirs(os.path.join(game, "tl", "Spanish"))
            u.write(os.path.join(game, "tl", "None", "common.rpym"), PLAIN)
            u.write(os.path.join(game, "tl", "Spanish", "a.rpy"), SPANISH)
            u.write(os.path.join(game, "tl", "Spanish", "b.rpy"), PLAIN)
            self.assertEqual(fresh.plan_fresh_tl(root, "chinese", dry_run=True).source, "tl/Spanish")

    def test_skips_backup_directories(self):
        with u.temp_dir() as root:
            _project(root)
            backup = os.path.join(root, "game", "tl", "Spanish", "_backup")
            u.write(os.path.join(backup, "old.rpy"), PLAIN)
            result = fresh.plan_fresh_tl(root, "chinese", dry_run=True)
            self.assertFalse(any("_backup" in p for p in result.files))

    def test_compiled_only_template_falls_back_to_decompile(self):
        # Shipped games keep only .rpyc.  The clone must decode into a scratch
        # dir rather than writing .rpy next to the game's own files -- a stray
        # .rpy in game/tl/ would be loaded by the engine.
        with u.temp_dir() as root:
            game = os.path.join(root, "game")
            tl_dir = os.path.join(game, "tl", "Spanish")
            os.makedirs(tl_dir)
            u.write(os.path.join(tl_dir, "script.rpyc"), "not really an rpyc")
            with self.assertRaises(ToolkitError) as ctx:
                fresh.plan_fresh_tl(root, "chinese", dry_run=True)
            self.assertIn("nothing decompilable", str(ctx.exception))
            # The failed decode must not have littered the source tree.
            self.assertEqual(sorted(os.listdir(tl_dir)), ["script.rpyc"])


if __name__ == "__main__":
    unittest.main()
