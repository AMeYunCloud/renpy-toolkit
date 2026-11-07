# Changelog

Notable changes to **renpy-toolkit**. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-10-24

First public release. Everything below was verified against a shipping game
whose `game/` directory holds 50 `.rpyc` files and no `.rpy` sources at all.

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
