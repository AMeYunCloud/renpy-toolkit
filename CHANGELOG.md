# Changelog

Notable changes to **renpy-toolkit**. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-10-24

First public release. Everything below was verified against a shipping game
whose `game/` directory holds 50 `.rpyc` files and no `.rpy` sources at all.

The `docs/` set (nine documents plus an index) is written for readers of the
toolkit: every rule the code enforces is traced back to an engine fact or a
format specification, so the documents can be used to re-verify the behaviour
after a Ren'Py upgrade.

### Deprecated

**Windows exe line**

- The Windows exe (GUI) release line is **no longer maintained**: it receives no
  fixes and is not shipped with new versions. `exe/` (`gui.py`,
  `ui/index.html`, `tests/`), `tools/build_exe.py` and `tools/exe_entry.py` are
  kept for reference only and are marked deprecated in place. The plugin/pip and
  agent-skill lines are unaffected — neither imports nor invokes the GUI, and
  their published artifacts contain no UI code.

### Changed

**Three independent release lines**

- The GUI moved out of the Python package: `renpy_toolkit/gui.py` and
  `renpy_toolkit/data/ui/index.html` now live in the repository-level `exe/`
  (`exe/gui.py`, `exe/ui/index.html`, `exe/tests/`). The package dropped from 28
  to 27 modules and its suite from 542 to 531 cases; the GUI's 39 pure-logic
  tests run separately with `python -m unittest discover -s exe\tests -t exe\tests`.
- Consequence, and the reason for the move: `dist/plugin`, the portable zip and
  the skill package no longer contain any UI code, and neither the plugin nor the
  skill contains or invokes the exe. The exe is built only by
  `tools/build_exe.py`, which now points `--add-data` at `exe/ui` and puts `exe/`
  on `--paths`.
- The three artifacts are therefore released independently: plugin/pip, agent
  skill, and the Windows exe.

**Coexistence with other tools**

- A project usually already has other translators in it: the official
  `renpy translate` command, `unrpyc`/`rpatool`, hand-written `.rpy` files, the
  user's own git repository. The toolkit is documented (README "与其他工具共存")
  to work inside such a project: generated `.rpy` uses exactly the official format,
  `tl gen --write` only adds files that are missing, `--backup-mode git` writes to a
  shadow repository (`<tl>/.renpy-toolkit.git`, or `RTK_BACKUP_DIR`) and never
  commits to the user's repository, temporary directories have fixed names and
  `--clean-scratch` only removes its own, and every subcommand is dry-run unless
  `--write` is given.

### Added

**Generation options (`tl gen`)**

- Hardcoded text — literals the engine never looks up because nothing wraps them
  in `_()` (UI labels, button captions, tooltips, item names written straight into
  a `text`/`textbutton`) — is collected into `strings` by default, since a
  `strings` entry is the only way to translate it without editing the game.
  `--no-hardcoded` turns that off. The run report always states the outcome:
  `... ; 硬编码文本块 11 条（已收入 strings）`, or `0 条（按 --no-hardcoded 跳过 N 条）`.
- `--sections` and `--index` add the `# --- <label> ---` markers and the
  `# === Event Index ===` block while generating the skeleton. Both are **off by
  default**: the labels are derived from the label each block was compiled from,
  so they are a reading aid and can drift from the actual script. Every file that
  gets them carries a reminder line saying the source is the authority, and a
  second run is a fixed point.

**Ren'Py-only target validation**

- Every entry point now refuses a directory that is not a Ren'Py game
  (`renpy_toolkit/engine.py`). The check looks for evidence, never at directory
  names: the `RENPY RPC2` magic in `game/*.rpyc`, the `RPA-3.0` archive magic,
  a `renpy/` runtime, a bundled `lib/py3-*` interpreter, a launcher that imports
  `renpy`, or Ren'Py source structure inside `game/`. Pointing the tool at any
  other folder used to produce a wall of incomprehensible errors (and, for
  `tl gen`, could write a skeleton into someone else's `game/`).
- `tl gen`, `project audit` and `decompile dir` gained `--allow-non-renpy` as an
  escape hatch; the GUI simply refuses and says which evidence was missing.

**Windows exe (GUI)**

- The window gained the two generation switches: 「分块注释与索引」 (off by default;
  maps to `--sections --index`) and 「硬编码文本」 (on; `--no-hardcoded` turns it off).
  Both are remembered in `config.json`.
- The hardcoded entry count is surfaced after a generate run: a dedicated log line
  (`本次生成：硬编码文本 11 条（已收入 strings 块）`) plus a toast, read from the CLI's
  own report rather than recomputed by the UI.
- Common CLI output lines are translated for display (`wrote N file(s) to ...`,
  `N file(s) already present, left untouched`, the coverage/error summary). The log
  file written to disk keeps the original English text, so bug reports stay useful.
- Error hints now name the button that solves the problem inside the package
  (not a Ren'Py game, `No module named`, duplicate `old`, permission errors, missing git).

### Fixed

**Windows exe (GUI)**

- Closing the page now really stops the tool. The service lifetime used to be 40 s
  without a browser process and 120 s when one had been launched — but closing a tab
  leaves the browser process alive, so the 120 s branch was chosen and it looked like
  the tool never exited. It is now a single `QUIET_SECONDS = 20.0` heartbeat timeout
  that ignores browser processes entirely (an in-flight task still finishes first).
- A dedicated window is the default again (`--app`); `--tab` opts into a tab in the
  user's own browser. Both modes show a 「退出工具」 button.


- Closing the browser page now shuts the tool down. The page sends a heartbeat
  every four seconds and fires `sendBeacon('/api/bye')` on `pagehide`; the server
  exits when no request arrives for forty seconds, so it no longer lingers in
  the background after the tab is closed.

- The single-file build now extracts to its own directory (`--runtime-tmpdir .`).
  The PyInstaller bootloader creates its temporary directory with a custom
  security descriptor and on some machines that call fails outright with
  `Could not create temporary directory!` — which looked like "the first launch
  always fails, the second one sometimes works".
- The UI opens in the user's own browser as a normal tab by default; a private
  `--app` window (with a fresh profile, which drags in every installed
  extension) is only the fallback when no request arrives within ten seconds.
- The fallback profile lives in `renpy-toolkit-数据/临时/browser` instead of
  `%LOCALAPPDATA%`: creating directories under `C:\\Users` can block
  indefinitely under a restricted token, which froze the whole window.
- Every startup step is logged to `renpy-toolkit-启动.log`, and every startup
  failure raises a visible dialog instead of exiting silently.
- The HTTP service no longer dies with the browser process. Launchers that hand
  the window over to an already-running instance exit immediately, and the old
  `proc.wait()` therefore shut the server down while the page was still loading
  (the page then showed the bare `127.0.0.1/` address).
- Every button raised `ImportError: attempted relative import with no known
  parent package` once `gui.py` left the package: two leftovers
  (`from . import cli`, `from . import __version__`) became
  `from renpy_toolkit import ...`. This is why most buttons did nothing in the
  first build of the separate exe line.
- `decompile dir` always failed at the end with `AttributeError:
  'DecompileResult' object has no attribute 'summary'` — it now prints
  `result.text()`. Both the CLI and the GUI were affected.
- `font check` needs at least one font path, so the button always exited with
  usage error 2. It now picks fonts out of the game's `game/` tree and explains
  itself when there are none.
- `apply` / `port` passed an empty string when no Excel or reference directory
  had been chosen; they now say so before running anything.
- The hardcoded-text button relied on the probe dump, which is deleted when
  `tl gen` finishes; it now rebuilds the ledger from the TL (`--from-tl`).
- `rpa list` on a game without `.rpa` (and `font map` on a game with no font
  mappings) exited 1 and was reported as "checks failed"; these are now treated
  as "nothing to do".
- The UI is a light, high-contrast theme (the first one was dark-on-dark and
  hard to read), and the parameters card gained Excel-file and old-translation
  directory inputs, plus a "use the latest export" button.
- The first launch always failed and the second one worked: a single-file build
  unpacks ~14 MB and is scanned before the server exists, but the browser was
  opened immediately. The tool now waits until the port is listening, and the
  page retries the read-only calls (`bootstrap`, `scan`, `rescan`, `select`)
  for 30 seconds. Write actions are deliberately not retried.
- With Edge extensions installed, the app window came up alongside a pile of
  extension pages, because a non-ASCII `--user-data-dir` (the temp directory
  name contained Chinese) was ignored and Edge fell back to the user profile.
  The profile directory is now `browser-…` (ASCII), and the launch flags
  include `--disable-extensions`, `--no-first-run`, `--no-default-browser-check`.
  A new `--tab` mode opens the UI as an ordinary tab in the browser the user
  is already running (no second profile, no extension pages); the page then
  shows a "退出工具" button that shuts the server down.
- Three guards run before anything else: a refusal to run from a drive root
  (a modal with a 10-second countdown, a single "退出" button, and no way to
  close it otherwise), a single-instance mutex so a second copy does not start
  a second server, and the wait-for-port check above.
- With no game found, the page now says to move `renpy-toolkit.exe` next to a
  game folder (same level as it, or one level above).
- `--no-browser` now waits on the same stop event as `--tab`, so an automated
  check can shut the server down instead of being killed.
- `rpa list` / `rpa extract` only ever looked at one archive, so a game shipping
  `archive.rpa` **and** `archive2.rpa` failed with "no such entry" for everything
  in the second file. Archives are now discovered and merged (`MultiArchive`), and
  `rpa list` reports how many archives it spans.

### Added

**Translation scaffolding**

- `tl gen` — generates a complete `game/tl/<lang>/` skeleton by running the
  game itself and reading `renpy.game.script.translator.file_translates` plus
  the SL2 screen tree from memory. The engine's own `translate` command only
  walks `.rpy` files on disk, so it produces nothing for `.rpyc`-only
  releases. Measured 58,249 dialogue blocks in 19.6 s, and the engine's own
  `lint` reports the same 58,249 figure for both the original and the
  translation.
- Skeleton bodies use the **source text as the placeholder**, never `""`.
  `StringTranslator.translate()` reads
  `new = self.translations.get(old, None); if new is not None: return new`,
  so an empty string is a valid translation that renders as blank text.
- String extraction by walking the compiled statement graph: dialogue,
  `show text`/`stack_notify` payloads, SL2 screen literals (with the screen
  name recorded for disambiguation), and `_()`-wrapped literals inside
  `Python` nodes. 2,737 strings on the reference release, versus 191 that
  `translator.additional_strings` alone exposes.
- `tl init` — offline fallback that builds the same skeleton from decompiled
  scripts, for machines without the game's interpreter.

**Binary formats**

- `.rpyc` decompiler built on a `pickletools` opcode whitelist, never
  `pickle.loads`. Round-trip verified on 67,453 translation identifiers with
  zero differences against the engine's own in-memory strings.
- `.rpa` reader/writer for RPAv2 and RPAv3, including XOR obfuscation, prefix
  slices and custom keys. Index loading goes through a whitelisting
  `Unpickler`; decompression is capped at 64 MiB; entry names are checked
  against path traversal.

**Translation quality**

- `tl verify` — empty translations, tag and placeholder parity, mixed line
  endings, duplicate strings, lost stutter, fullwidth quotes, statements
  stranded outside a block, unknown text tags, `{#context}` collisions.
  Every check was tuned against a finished translation to remove false
  positives before being turned on.
- `tl port` — carries an existing translation onto a freshly generated
  skeleton, keyed by identifier *and* source text, so blocks whose source
  changed are not overwritten. Moved 53,093 of 58,249 blocks on a real
  update.
- `tl glossary` — terminology consistency, driven by a six-column TSV. A
  `scope` column separates terms that apply to any Ren'Py game from terms
  that only make sense in one title. Ships 167 engine UI terms in
  `data/ui-terms.tsv`.
- `tl sort` / `tl split` / `tl hardcoded` — reordering that is byte-stable
  across runs, strings-block splitting, and annotation of hardcoded text.
  `--index` writes one index entry per label rather than per block, which
  compressed a 57,414-block index from 1.8 MB to 15.3 KB.

**Text that resists translation**

- `tl context` — finds strings whose meaning changes by screen, then emits a
  `translate <lang> python:` patch that consults the current screen. The
  official escape hatch is `{#context}` tags, which requires editing the
  game's source; this does not.
- `tl untranslatable` / `tl fix-fstrings` — reports strings assembled with
  f-strings. Such strings are evaluated *before* the translation table is
  consulted, so a `strings` entry for the template can never match. The fix
  either registers the exact resulting strings or emits a runtime regex
  patch — again without editing the game.
- `font map` — audits hardcoded font paths and emits a
  `config.font_replacement_map` block.
- `font check` — font coverage against the characters a translation actually
  uses.

**Project tooling**

- `project new` — a runnable Ren'Py skeleton with a translation directory
  that already follows the placeholder rule above.
- `project audit` — missing images and files, duplicate image definitions,
  duplicate string translations, `config.force_archives` (which makes the
  engine prefer an archive over a loose file *silently*), and
  `config.translate_additional_strings_callbacks` (runtime-registered
  strings that static extraction cannot see).
- `decompile` — `file` / `dir` / `verify`, dry-run by default.
- `sdk` — drives a game's own bundled interpreter to run `translate`,
  `lint`, `dialogue`, `extract_strings` and `merge_strings`.
- `--selftest` — one command that checks a copy actually works on the current
  machine (Python version, package import, optional dependencies, an `.rpa`
  write/read round-trip, parsing a translation file, the command table).
  Writes only into a temporary directory. Exists because the portable zip
  needs an answer to "did I unzip this correctly" that is not a stack trace.
  The module and sub-command counts it prints are read from the package and
  the argument parser rather than written out by hand: a hard-coded number
  went stale the first time a module was added, and then the one command
  whose job is to say "this copy is intact" reported something false.

**Backups**

- Every write goes through `backup.keep()`, and `--backup-mode`
  (`bak` / `git` / `none` / `auto`, or `RTK_BACKUP`) decides what happens to
  the old bytes. The default is `bak`, deliberately not `auto`: a default that
  depends on whether git happens to be installed is a default that behaves
  differently on two machines running the same command.
- `git` mode writes into a **shadow repository** at `<tl>/.renpy-toolkit.git`
  rather than the project's own repo. A translation directory is often already
  inside a git checkout, and committing there would rewrite someone else's
  history. The shadow repo is a bare `--git-dir`, so nothing but that dot
  directory appears next to the translations — no `.git`, no working tree for
  `git clean` to wander into — and commits are assembled with `hash-object -w`
  + `read-tree` + `commit-tree` without touching an index the user can see.
- `tl backup list` / `show` / `restore` / `prune` / `size`. Snapshots store
  complete trees, so any revision restores whole rather than replaying diffs.
- `RTK_BACKUP_DIR` moves the repository without moving the work tree, and it is
  now honoured by the `tl backup` commands as well as by per-write backups. The
  GUI sets it so snapshots of a game the user only has read access to land in
  `renpy-toolkit-数据\备份\` beside the exe instead of failing on a read-only
  `game/tl`.
- Without git, `--backup-mode git` degrades to `bak` and says so on stderr; the
  command still finishes with its usual exit code. `.bak` is a usable backup and
  asking for a snapshot is no reason to block a translation edit on a missing
  program. Snapshot *inspection* cannot degrade — `tl backup list/show/restore/
  save/prune/size` exit 3 without git, because there is no `.bak` spelling of
  "show me revision 2930389e". The degradation is loud on purpose: a silent one
  would look exactly like a snapshot history that is quietly accumulating.
- Three byte-fidelity traps, all covered by round-trip tests: Git for Windows
  ships `core.autocrlf=true` system-wide, which rewrites CRLF to LF on the way
  into a blob (defeated with `--no-filters` plus `-c core.autocrlf=false`);
  `git show` applies smudge filters (`cat-file blob` is used instead); and
  reading through `text=True` collapses line endings on the way back out.
- `.rpa` archives can be several gigabytes, so `bak` mode copies in 1 MiB
  chunks instead of loading the file; `git` mode hashes the whole file, which
  is the price of a restorable snapshot and the caller's choice.

**Distribution**

- Three shapes from one source tree: `pip install .` (console command),
  `skills/` (agent skill), and `dist/portable/*.zip` (unzip and run, no pip and
  no environment variables). `tools/build_portable.py` produces the last one
  with platform launchers for Windows (`.cmd`) and POSIX (`.sh`), plus
  `自检.cmd` / `selfcheck.sh` that call `--selftest`.
- The launchers contain no logic of their own; they delegate to
  `renpy-toolkit.py`. Batch files are written with CRLF, because with LF
  endings `cmd.exe` joins unterminated lines, `goto :label` cannot find its
  target, and the script collapses into a stream of "not recognized as an
  internal or external command" errors.
- `tools/make_release.py` publishes both archives as GitHub release assets.
  Archives stay out of git on purpose: a binary zip committed per version
  bloats the repository and fills history with blobs nobody can diff. The
  script first proves that `plugin/dsh-renpy-toolkit/VERSION`, `__version__`, `pyproject.toml`
  and the CHANGELOG section agree, because a release whose tag and contents
  disagree is worse than no release.
- `tools/check_portable.py` validates both archives from a clean unzip, running
  from a working directory unrelated to either the repository or the unpacked
  folder: version, `--selftest`, `tl stats`/`verify`/`table`, exit codes,
  manifest digests, CRLF launchers, and that the skill launcher reaches the
  toolkit through `RPTK_HOME`. Each of those checks exists because the
  corresponding mistake happened at least once during development.

**DSH plugin**

- The repository is also a DeepSeek Harness plugin: `lib/index.js` registers
  three tools (`renpy_inspect`, `renpy_translate`, `renpy_check`) that shell
  out to the Python package, plus a skill provider that serves
  `skills/renpy-toolkit/SKILL.md`. The Python side is not reimplemented in
  JavaScript — one implementation, two front doors.
- `cordis.patch.yml` inserts the plugin under its own package name, which is
  what Node module resolution needs to find it from the profile's
  `node_modules`. Inter-plugin ordering lives in the `inject` array in
  `lib/index.js`, not in the yml.
- The skill provider re-reads `SKILL.md` on every `list()` and carries
  `rank: 600`: without the rank a project-local skill of the same name
  shadows the bundled one silently, and a cached read means editing the
  document does nothing until the host restarts.
- The JS half has zero runtime dependencies; it imports only the host's
  `@deepseek-ai/*` peers. `package.json` `files` lists `lib`, the Python
  package, `skills` and `cordis.patch.yml` — nothing else is published.

**Windows GUI (exe)**

- `renpy_toolkit/gui.py` — a window for people who will not touch a
  command line. It reimplements nothing: every button becomes one
  `cli.main(argv)` call, so the window and the CLI cannot drift apart on
  `--write` semantics, backup mode or exit codes.
- The window is a local HTTP server plus the system browser: `gui.py` serves
  `data/ui/index.html` from `127.0.0.1` (standard library only) and opens it
  with `--app=` so there is no address bar. The earlier native WebView binding
  (pywebview/pythonnet) was dropped: once frozen by PyInstaller it has to
  resolve the CLR, and that fails on machines where the netfx/coreclr choice
  cannot be satisfied — the browser route has no native dependency at all.
- On start it scans the exe's own directory plus one level down for Ren'Py
  games (a directory with `game/` plus a `.py`/`.exe` launcher, a bundled
  `lib/py3-*/python.exe`, or `.rpy`/`.rpyc` inside `game/`) and always shows
  the picker — zero, one or many games. One game is processed at a time and
  nothing outside it is touched.
- Anything that writes runs its dry-run first and shows what will be written
  and where; the write only happens after a confirmation. Language directory
  name and backup mode are chosen in the window and remembered in
  `config.json` next to the exe.
- Only the translation skeleton lands inside the game (`game/tl/<language>/`).
  Everything else the tool produces — exported spreadsheets, decompiled
  scripts, backup snapshots, logs, scratch space — goes to a single
  `renpy-toolkit-数据/` folder beside the exe, so a translation run does not
  litter a game the user did not choose.
- `tools/build_exe.py` builds a single-file exe with PyInstaller and verifies
  it by running `--selftest` on the frozen binary. `tools/exe_entry.py` opens
  the window with no arguments and dispatches to the CLI when the first
  argument looks like an option, which is also what makes the frozen
  self-check possible.
- The exe bundles `fontTools` and `openpyxl` (both MIT), so font-coverage and
  XLSX work out of the box there; the portable zip and the skill stay
  standard-library-only. A windowed build has no console, so CLI output is
  redirected to `renpy-toolkit-cli.log` beside the exe instead of crashing on
  a `None` stdout.
- The onefile form extracts itself into the system temp directory on every
  launch. Where policy forbids creating that directory (locked-down tokens,
  some endpoint security), it fails before the app starts; `--verify` reports
  that as an environment skip rather than a broken build, and
  `--runtime-tmpdir DIR` rebuilds it against a directory you control.

### Security

Four audit rounds were run before release. Fixed:

- **Critical** — `tl gen --outdir` used to `rmtree` the directory it was
  pointed at, deleting hand-written translations. It now only ever creates
  and appends.
- **Critical** — string blocks were appended unconditionally, so re-running
  the generator registered the same `old` twice and the engine refused to
  start with `A translation for "..." already exists`.
- **High** — path traversal via archive entry names (Zip Slip) in both the
  library and the CLI.
- **High** — `pickle.loads` on archive indexes, replaced with a whitelisting
  `Unpickler`.
- **High** — CSV and XLSX exports passed attacker-controlled strings
  straight into spreadsheet cells; values starting with `=`, `+`, `-`, `@`,
  tab or CR are now defused.
- **Medium** — unbounded `zlib.decompress` (now capped), unvalidated
  `--language` in a path, missing `.bak` on `--force`, silently swallowed
  decompile failures.

### Known limitations

- `tl gen` needs the game to be runnable on the machine doing the
  extraction. `tl init` is the offline fallback and is less complete,
  because decompiled screens come through as `# TODO: unsupported node`.
- Strings registered at runtime via
  `config.translate_additional_strings_callbacks` cannot be extracted
  statically. `project audit` reports their presence instead of pretending
  to cover them.
- Single lowercase words (`back`, `display`) cannot be classified as UI text
  versus identifiers by syntax. `tl context` exposes `--ignore` rather than
  guessing.
- Byte-compiled caches cannot be kept out of a checkout permanently; "clean
  release" means no cache at the moment of delivery.

[1.0.0]: https://github.com/AMeYunCloud/renpy-toolkit/releases/tag/v1.0.0
