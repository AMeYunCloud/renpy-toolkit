"""Tests for :mod:`renpy_toolkit.tlgen` -- the probe and its string filters.

The probe itself only runs inside a real Ren'Py game, so these tests cover the
two halves that can be exercised offline:

* :func:`renpy_toolkit.tlgen.filter_strings` / ``filter_fstrings`` -- what the
  plugin decides to write into ``translate <lang> strings:``;
* the f-string -> ``[var]`` template rewrite, which is the only way an
  f-string can ever become translatable.

The rewrite rules are pinned here because getting them wrong is silent: a bad
template simply never matches at runtime.
"""

from __future__ import annotations

import ast
import unittest

from _util import temp_dir  # noqa: F401  (puts the plugin root on sys.path)

from renpy_toolkit import tlgen


def _probe():
    """Return the helper functions defined inside ``PROBE_SOURCE``.

    The probe is a *Ren'Py* script (``init -1000 python:`` followed by an
    indented block), so ``ast.parse`` cannot read it directly. Its helpers are
    plain Python though, so they are lifted out by indentation, dedented, and
    compiled here -- that way the rules under test are the shipped ones, not a
    copy that can drift.
    """
    lines = tlgen.PROBE_SOURCE.splitlines()
    wanted = ("fstring_source", "fstring_has_field")
    chunks: dict[str, list[str]] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        name = None
        for candidate in wanted:
            if stripped.startswith(f"def {candidate}("):
                name = candidate
                break
        if name is None or name in chunks:
            continue
        indent = len(line) - len(line.lstrip())
        body = [line[indent:]]
        for follower in lines[index + 1:]:
            if not follower.strip():
                body.append("")
                continue
            if len(follower) - len(follower.lstrip()) <= indent:
                break
            body.append(follower[indent:])
        while body and not body[-1].strip():
            body.pop()
        chunks[name] = body
    missing = [n for n in wanted if n not in chunks]
    if missing:
        raise AssertionError(f"probe is missing helpers: {missing}")
    source = "\n\n".join("\n".join(chunks[n]) for n in wanted)
    namespace: dict[str, object] = {"ast": ast}
    exec(source, namespace)  # noqa: S102  (test-only, own source)
    return namespace


class TestFstringDetection(unittest.TestCase):
    """Which f-strings the probe reports as untranslatable.

    An f-string with fields is assembled at runtime, so no ``strings`` entry can
    ever match it. The probe reports those instead of writing a ``[var]``
    template: such a template would look translatable but never be looked up,
    which is exactly the dead weight the reference project carries.
    """

    @classmethod
    def setUpClass(cls):
        cls.helpers = _probe()

    def has_field(self, source: str) -> bool:
        node = ast.parse(source, mode="eval").body
        return self.helpers["fstring_has_field"](node)

    def test_text_only_fstring_has_no_field(self):
        # ``f"Level up!"`` is an ordinary constant; ``literals`` already
        # collects and translates it, so warning about it would be a false
        # positive.
        self.assertFalse(self.has_field('f"just text"'))

    def test_field_detected(self):
        self.assertTrue(self.has_field('f"You bought 1 {n}!"'))

    def test_format_spec_still_counts_as_field(self):
        # ``f"{n:.0f}"`` interpolates, so it is unreachable and must be
        # reported even though its format spec cannot be expressed.
        self.assertTrue(self.has_field('f"{n:.0f}"'))

    def test_expression_field_counts(self):
        self.assertTrue(self.has_field('f"{a + b}"'))

    def test_source_renders_back(self):
        node = ast.parse('f"You bought 1 {n}!"', mode="eval").body
        text = self.helpers["fstring_source"](node)
        self.assertIn("You bought 1", text)
        self.assertIn("n", text)


class TestRunReport(unittest.TestCase):
    """The run report has to say whether hardcoded text produced entries."""

    def _result(self, **kw):
        return tlgen.GenerateResult(language="chinese", outdir="x", **kw)

    def test_summary_reports_hardcoded_entries(self):
        text = self._result(blocks=10, strings=40, hardcoded_strings=7).summary()
        self.assertIn("\u786c\u7f16\u7801\u6587\u672c\u5757 7 \u6761", text)

    def test_summary_reports_zero_when_skipped(self):
        text = self._result(blocks=10, strings=33, hardcoded_skipped=7).summary()
        self.assertIn("--no-hardcoded \u8df3\u8fc7 7 \u6761", text)

    def test_summary_reports_zero_when_there_are_none(self):
        text = self._result(blocks=10, strings=33).summary()
        self.assertIn("\u786c\u7f16\u7801\u6587\u672c\u5757 0 \u6761", text)


class TestFilterStrings(unittest.TestCase):
    """Which rows reach ``translate <lang> strings:``."""

    def row(self, text, **kw):
        base = {"file": "script.rpy", "line": 1, "text": text, "how": "sl",
                "screen": None, "origin": "", "engine": False, "wrapped": False}
        base.update(kw)
        return base

    def test_keeps_ordinary_prose(self):
        rows = [self.row("Start Game")]
        self.assertEqual(len(tlgen.filter_strings(rows)), 1)

    def test_drops_asset_path(self):
        rows = [self.row("bg/icons/quest.png")]
        self.assertEqual(tlgen.filter_strings(rows), [])

    def test_drops_tl_file_rows(self):
        # A row anchored in tl/ is a translation, not a source string; keeping
        # it would make the skeleton translate itself.
        rows = [self.row("Start Game", file="tl/chinese/script.rpy")]
        self.assertEqual(tlgen.filter_strings(rows), [])

    def test_drops_dev_tool_files(self):
        rows = [self.row("Please type value", file="devtools/scene_tool.rpy")]
        self.assertEqual(tlgen.filter_strings(rows), [])

    def test_dev_tool_dir_keeps_wrapped_text(self):
        # A folder that carries a tool-like name is not proof the content is
        # tooling -- a shipping game may put a story route under ``devtools/``.
        # Text the game itself marked ``_()`` is player-visible by the author's
        # own declaration, so the path must not override that.
        rows = [self.row("Unlock the bonus gallery",
                         file="devtools/scene_tool.rpy", wrapped=True)]
        self.assertEqual(len(tlgen.filter_strings(rows)), 1)

    def test_dev_tool_dir_ignores_filename_only(self):
        # ``devtools.rpy`` at the top level is an ordinary script; only a
        # *directory* component marks tooling.
        rows = [self.row("Please type value", file="devtools.rpy")]
        self.assertEqual(len(tlgen.filter_strings(rows)), 1)

    def test_dev_tool_dirs_accepts_a_custom_folder_name(self):
        # The default set holds only unambiguous tooling names. A game whose
        # tooling lives under its own folder name opts in per call instead of
        # that name being baked into the default -- otherwise every unrelated
        # project inherits a rule about a folder it will never have.
        rows = [self.row("Please type value", file="stageeditor/x.rpy")]
        self.assertEqual(len(tlgen.filter_strings(rows)), 1)
        self.assertEqual(
            tlgen.filter_strings(rows, dev_tool_dirs=("stageeditor",)), []
        )

    def test_engine_half_requires_wrapping(self):
        # The engine marks its own translatable strings with _(); without that
        # filter the engine half is 2000+ developer internals.
        rows = [self.row("Size", engine=True, wrapped=False)]
        self.assertEqual(tlgen.filter_strings(rows, engine=True), [])
        rows = [self.row("Size", engine=True, wrapped=True)]
        self.assertEqual(len(tlgen.filter_strings(rows, engine=True)), 1)

    def test_exclude_removes_already_claimed(self):
        # One global string table: the same literal in both halves would raise
        # "A translation for ... already exists".
        rows = [self.row("Load")]
        self.assertEqual(tlgen.filter_strings(rows, exclude={"Load"}), [])

    def test_screen_row_wins_ordering(self):
        # One global string table means one surviving row per literal, so the
        # sort has to put the context-carrying row first -- otherwise which of
        # the two survives depends on file order and the screen name is lost.
        rows = [
            self.row("Load", line=5, screen=None),
            self.row("Load", line=5, screen="navigation"),
        ]
        kept = tlgen.filter_strings(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["screen"], "navigation")

    def test_identifier_token_dropped_by_default(self):
        # ``night_room`` is a jump target; a large game has ~1200 such
        # tokens and translates almost none of them.
        rows = [self.row("night_room")]
        self.assertEqual(tlgen.filter_strings(rows), [])

    def test_identifier_token_can_be_readmitted(self):
        # But a few of a project's strings are such keys used as button
        # labels (``scene_editor`` -> "场景编辑器"), and no syntactic test tells
        # them apart. The escape hatch has to exist.
        rows = [self.row("scene_editor")]
        kept = tlgen.filter_strings(rows, identifiers=True)
        self.assertEqual(len(kept), 1)

    def test_include_identifiers_does_not_admit_asset_paths(self):
        # Widening the identifier rule must not also widen the asset rule.
        rows = [self.row("bg/icons/quest.png")]
        self.assertEqual(tlgen.filter_strings(rows, identifiers=True), [])

    def test_multiline_python_source_is_not_text(self):
        # A ``Python`` block arrives as one literal covering a whole function
        # body. Its first line is a real sentence, which is why the earlier
        # length-only rules let it through.
        body = (
            "\n    # Template tag function to copy off of.\n"
            "    def TEMPLATE_tag(tag, args):\n"
            "        return tag\n"
        )
        rows = [self.row(body)]
        self.assertEqual(tlgen.filter_strings(rows), [])

    def test_sentence_mentioning_def_is_kept(self):
        # The code-blob rule needs a keyword followed by a name, not the bare
        # prefix: this translated line's second physical line starts with
        # "defeated".
        text = ("{size=-20}A dagger that helps retrieve \n"
                "more loot from \n"
                "defeated monsters.")
        rows = [self.row(text)]
        self.assertEqual(len(tlgen.filter_strings(rows)), 1)


class TestFilterFstrings(unittest.TestCase):
    """The f-string report keeps prose and drops asset paths."""

    def row(self, text, **kw):
        base = {"file": "script.rpy", "line": 1, "how": "pycode",
                "screen": None, "text": text}
        base.update(kw)
        return base

    def test_keeps_sentence(self):
        rows = [self.row('f"You bought 1 {item_name}!"')]
        kept = tlgen.filter_fstrings(rows)
        self.assertEqual(len(kept), 1)

    def test_drops_asset_path(self):
        rows = [self.row('f"{name}.png"')]
        self.assertEqual(tlgen.filter_fstrings(rows), [])

    def test_drops_dev_tool_files(self):
        rows = [self.row('f"You bought 1 {n}!"', file="devtools/x.rpy")]
        self.assertEqual(tlgen.filter_fstrings(rows), [])

    def test_dev_tool_dirs_accepts_a_custom_folder_name(self):
        # A game whose tooling lives under its own folder name opts in rather
        # than that name being baked into the default set.
        rows = [self.row('f"You bought 1 {n}!"', file="stageeditor/x.rpy")]
        self.assertEqual(len(tlgen.filter_fstrings(rows)), 1)
        self.assertEqual(
            tlgen.filter_fstrings(rows, dev_tool_dirs=("stageeditor",)), []
        )

    def test_sorted_by_location(self):
        rows = [
            self.row('f"second {n}"', file="b.rpy", line=2),
            self.row('f"first {n}"', file="a.rpy", line=9),
        ]
        kept = tlgen.filter_fstrings(rows)
        self.assertEqual([r["file"] for r in kept], ["a.rpy", "b.rpy"])


class TestLoaders(unittest.TestCase):
    """``load_strings`` / ``load_fstrings`` read the probe dump."""

    def roundtrip(self, payload, loader):
        """Write *payload* as a dump, load it, and return the result.

        The read has to happen inside the fixture: ``temp_dir()`` deletes the
        directory when it exits, so returning the path would hand back a file
        that no longer exists.
        """
        import json

        with temp_dir() as root:
            path = f"{root}/dump.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            return loader(path)

    def test_load_strings_reads_data(self):
        payload = {"rows": 1, "data": [{"text": "a"}]}
        self.assertEqual(self.roundtrip(payload, tlgen.load_strings),
                         [{"text": "a"}])

    def test_load_strings_accepts_bare_list(self):
        self.assertEqual(
            self.roundtrip([{"text": "a"}], tlgen.load_strings), [{"text": "a"}])

    def test_load_fstrings_missing_key(self):
        # An older dump has no f-strings; that must read as "none", not crash.
        payload = {"rows": 0, "data": []}
        self.assertEqual(self.roundtrip(payload, tlgen.load_fstrings), [])

    def test_load_fstrings_reads_entries(self):
        payload = {"fstrings": [{"file": "a.rpy", "line": 3}]}
        self.assertEqual(self.roundtrip(payload, tlgen.load_fstrings),
                         [{"file": "a.rpy", "line": 3}])


class TestProbeExclusion(unittest.TestCase):
    """``keep_file`` must reject the probe's own scripts.

    The probe lives in a scratch tree the toolkit names, and the *game's*
    interpreter picks where that tree goes -- sometimes inside the game. When
    the exclusion matched the wrong spelling, the probe scanned ``rtk_gen_probe``
    itself and reported its own f-strings as untranslatable author text.
    """

    def keep_file(self):
        """Lift ``keep_file`` out of the probe source (see ``_probe``)."""
        lines = tlgen.PROBE_SOURCE.splitlines()
        start = None
        for index, line in enumerate(lines):
            if line.strip().startswith("def keep_file("):
                start = index
                break
        if start is None:  # pragma: no cover - guards the fixture itself
            self.fail("keep_file not found in PROBE_SOURCE")
        base = len(lines[start]) - len(lines[start].lstrip())
        block = [lines[start]]
        for line in lines[start + 1:]:
            if line.strip() and (len(line) - len(line.lstrip())) <= base:
                break
            block.append(line)
        import textwrap

        def elide(fn):
            """Stand-in for the probe's ``elide``: path relative to ``game/``.

            ``keep_file`` reasons about game-relative paths, so a stub that
            returns the argument unchanged would test the wrong thing.
            """
            rel = fn.replace("\\", "/")
            for prefix in ("game/", "./game/"):
                if rel.startswith(prefix):
                    return rel[len(prefix):], False
            return rel, False

        namespace = {"elide": elide}
        exec(textwrap.dedent("\n".join(block)), namespace)
        return namespace["keep_file"]

    def test_rejects_dotted_scratch_tree(self):
        keep = self.keep_file()
        self.assertFalse(keep("game/.renpy-toolkit-gen/renpy-toolkit-gen/abc/probe/x.rpy"))

    def test_rejects_undotted_scratch_tree(self):
        # The regression: the scratch dir is spelled without the leading dot
        # whenever ``_scratch_dir`` builds it, so matching only the dotted form
        # let the probe see itself.
        keep = self.keep_file()
        self.assertFalse(keep("game/renpy-toolkit-gen/bfd1ae2a54d1/probe/rtk_gen_probe.rpy"))

    def test_rejects_translation_files(self):
        keep = self.keep_file()
        self.assertFalse(keep("game/tl/chinese/script.rpy"))

    def test_rejects_unknown_location(self):
        keep = self.keep_file()
        self.assertFalse(keep("?"))

    def test_keeps_ordinary_script(self):
        keep = self.keep_file()
        self.assertTrue(keep("game/script.rpy"))


class TestScratchOwnership(unittest.TestCase):
    """``scratch_is_ours`` gates the cleanup of a derived directory."""

    def test_accepts_toolkit_tree(self):
        self.assertTrue(tlgen.scratch_is_ours("/tmp/renpy-toolkit-gen"))

    def test_accepts_dotted_toolkit_tree(self):
        self.assertTrue(tlgen.scratch_is_ours("C:/game/.renpy-toolkit-gen/"))

    def test_rejects_user_directory(self):
        self.assertFalse(tlgen.scratch_is_ours("/tmp/some-game"))

    def test_rejects_temp_root(self):
        self.assertFalse(tlgen.scratch_is_ours("/tmp"))


class TestCleanScratchTree(unittest.TestCase):
    """``clean_scratch_tree`` must delete the tree and nothing above it."""

    def build(self, root):
        """Create ``<root>/renpy-toolkit-gen/<digest>/probe/x.rpy``."""
        import os

        scratch = os.path.join(root, "renpy-toolkit-gen", "abc123def456")
        os.makedirs(os.path.join(scratch, "probe"))
        with open(os.path.join(scratch, "probe", "x.rpy"), "w") as handle:
            handle.write("init python:\n    pass\n")
        return scratch

    def test_removes_toolkit_tree_but_keeps_base(self):
        import os

        with temp_dir() as root:
            scratch = self.build(root)
            self.assertTrue(tlgen.clean_scratch_tree(scratch))
            self.assertFalse(os.path.isdir(os.path.join(root, "renpy-toolkit-gen")))
            # The base is the caller's directory and must survive: with
            # RTK_SCRATCH=<game> this is the game itself.
            self.assertTrue(os.path.isdir(root))

    def test_removes_dotted_in_project_tree(self):
        import os

        with temp_dir() as root:
            tree = os.path.join(root, ".renpy-toolkit-gen")
            os.makedirs(os.path.join(tree, "abc", "probe"))
            self.assertTrue(tlgen.clean_scratch_tree(os.path.join(tree, "abc")))
            self.assertFalse(os.path.isdir(tree))
            self.assertTrue(os.path.isdir(root))

    def test_refuses_foreign_directory(self):
        import os

        with temp_dir() as root:
            keep = os.path.join(root, "some-game", "abc")
            os.makedirs(keep)
            self.assertFalse(tlgen.clean_scratch_tree(keep))
            self.assertTrue(os.path.isdir(keep))

    def test_not_owned_is_a_no_op(self):
        import os

        with temp_dir() as root:
            scratch = self.build(root)
            self.assertFalse(tlgen.clean_scratch_tree(scratch, owned=False))
            self.assertTrue(os.path.isdir(scratch))

    def test_empty_path_is_a_no_op(self):
        self.assertFalse(tlgen.clean_scratch_tree(""))


class TestScratchOrdering(unittest.TestCase):
    """Where the scratch tree is allowed to land.

    A bundled interpreter that cannot reach ``%TEMP%`` reports the *current
    directory* as its temp directory, so ranking ``getcwd()`` early put a third
    party's 58k-line dialogue dump inside this plugin's own install directory.
    The ordering is the thing worth pinning, so it lives in a pure function.
    """

    def _candidates(self):
        import os

        runtime = tlgen.Runtime(python="python", launcher="renpy.py", root=r"C:\games\SomeGame")
        previous = os.environ.pop("RTK_SCRATCH", None)
        try:
            return tlgen.scratch_candidates(runtime, outdir=None)
        finally:
            if previous is not None:
                os.environ["RTK_SCRATCH"] = previous

    def test_cwd_is_last(self):
        import os

        self.assertEqual(self._candidates()[-1], os.getcwd())

    def test_cwd_comes_after_every_owned_directory(self):
        import os

        candidates = self._candidates()
        position = candidates.index(os.getcwd())
        self.assertEqual(position, len(candidates) - 1)
        # The game's own root must be preferred over the caller's cwd.
        game_root = os.path.join(r"C:\games\SomeGame", ".renpy-toolkit-gen")
        self.assertGreater(position, candidates.index(game_root))

    def test_outdir_parent_is_tried_before_cwd(self):
        import os

        runtime = tlgen.Runtime(python="python", launcher="renpy.py", root=r"C:\games\SomeGame")
        previous = os.environ.pop("RTK_SCRATCH", None)
        try:
            candidates = tlgen.scratch_candidates(runtime, outdir=os.path.join("C:\\", "out", "tl"))
        finally:
            if previous is not None:
                os.environ["RTK_SCRATCH"] = previous
        out_parent = os.path.join("C:\\", "out")
        self.assertIn(out_parent, candidates)
        self.assertLess(candidates.index(out_parent), candidates.index(os.getcwd()))

    def test_rtk_scratch_wins_when_set(self):
        import os

        previous = os.environ.get("RTK_SCRATCH")
        os.environ["RTK_SCRATCH"] = r"D:\scratch"
        try:
            runtime = tlgen.Runtime(python="python", launcher="renpy.py", root=r"C:\games\SomeGame")
            self.assertEqual(tlgen.scratch_candidates(runtime, None)[0], r"D:\scratch")
        finally:
            if previous is None:
                os.environ.pop("RTK_SCRATCH", None)
            else:
                os.environ["RTK_SCRATCH"] = previous


class TestScratchLayout(unittest.TestCase):
    """The on-disk shape of the scratch tree, which cleanup depends on."""

    def test_plain_base_gets_one_named_level(self):
        import os

        with temp_dir() as base:
            root = tlgen._scratch_dir("tag", base)
            self.assertEqual(
                os.path.dirname(root),
                os.path.join(base, "renpy-toolkit-gen"),
            )
            self.assertTrue(tlgen.scratch_is_ours(os.path.dirname(root)))

    def test_scratch_root_base_is_not_nested_twice(self):
        """``<game>/.renpy-toolkit-gen`` is already a scratch root.

        Appending ``renpy-toolkit-gen`` to it produced
        ``.renpy-toolkit-gen/renpy-toolkit-gen``, so cleanup removed only the
        inner level and left the outer shell behind.
        """
        import os

        with temp_dir() as game:
            base = os.path.join(game, ".renpy-toolkit-gen")
            root = tlgen._scratch_dir("tag", base)
            self.assertEqual(os.path.dirname(root), base)
            # Exactly one named level: the digest sits directly under the base,
            # which sits directly under the game.
            self.assertEqual(os.path.dirname(base), game)
            self.assertTrue(tlgen.scratch_is_ours(base))

    def test_cleanup_removes_the_whole_tree(self):
        import os

        with temp_dir() as game:
            base = os.path.join(game, ".renpy-toolkit-gen")
            root = tlgen._scratch_dir("tag", base)
            with open(os.path.join(root, "x.rpy"), "w", encoding="utf-8") as handle:
                handle.write("translate x y:\n")
            self.assertTrue(tlgen.clean_scratch_tree(root))
            self.assertFalse(os.path.isdir(base))
            # The game itself must survive: only the scratch root is removed.
            self.assertTrue(os.path.isdir(game))


if __name__ == "__main__":
    unittest.main()
