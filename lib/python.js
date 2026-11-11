/**
 * Locating and running the Python implementation of the toolkit.
 *
 * The Python package ships inside the plugin, so a tool call never depends on
 * anything the user installed: no pip, no venv, no PATH entry. What it does
 * depend on is a Python 3 interpreter. DSH bundles one for its own use, and
 * that is tried first; then `python`/`python3` from PATH; then the interpreter
 * a Ren'Py install ships (the same one `sdk` commands drive).
 *
 * Every invocation passes the toolkit's `cli.py` path explicitly and sets
 * PYTHONPATH to the package root, so the child works from any cwd.
 *
 * @module dsh-renpy-toolkit/python
 */

import { execFile } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/** Package root: `lib/python.js` -> package root. Keeps the bundle relocatable. */
export const PACKAGE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/**
 * Directories that may hold the `renpy_toolkit` package.
 *
 * The repository keeps the Python side under `dsh-plugin/dsh-renpy-toolkit/` so
 * that directory stays a self-contained unit a user can copy elsewhere and
 * test on its own; the plugin used to be published as `python/`. Both are
 * tried, in that order, so the same code runs from either layout and a future
 * move does not need this file edited again.
 *
 * @returns absolute candidate directories
 */
export function pythonRootCandidates() {
  return [
    path.join(PACKAGE_ROOT, 'dsh-plugin', 'dsh-renpy-toolkit'),
    path.join(PACKAGE_ROOT, 'python'),
  ]
}

/**
 * The directory that actually contains `renpy_toolkit/`.
 *
 * @returns {string} absolute path
 * @throws when the package is missing, naming every place that was searched
 */
export function resolvePythonRoot() {
  const candidates = pythonRootCandidates()
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, 'renpy_toolkit', '__init__.py'))) return candidate
  }
  throw new Error(
    [
      'The bundled Python implementation is missing, so no tool can run.',
      'Looked for renpy_toolkit/__init__.py in:',
      ...candidates.map((line) => `  - ${line}`),
      'This usually means the package was repacked without its Python sources.',
    ].join('\n'),
  )
}

/**
 * Same as {@link resolvePythonRoot} but returns null instead of throwing, for
 * callers that need to report a status rather than abort.
 *
 * @returns {string|null}
 */
export function pythonRootOrNull() {
  try {
    return resolvePythonRoot()
  } catch {
    return null
  }
}

/** Hard cap on one CLI run. `tl gen` on a large release takes ~20-30s. */
const DEFAULT_TIMEOUT_MS = 30 * 60 * 1000

/** Big enough for a full `tl gen` log; the CLI is chatty on purpose. */
const MAX_BUFFER = 32 * 1024 * 1024

/**
 * Candidate Python interpreters, most trustworthy first.
 *
 * A candidate is only accepted once `--version` actually answers, because a
 * stale path in `DSH_HOME` (or an App Execution Alias stub on Windows that
 * exits with "install Python from the Store") looks like a file on disk but
 * is not an interpreter.
 *
 * @returns candidate absolute paths, plus bare names to be resolved by PATH.
 */
export function pythonCandidates() {
  const candidates = []
  const push = (value) => {
    if (typeof value === 'string' && value.trim() !== '' && !candidates.includes(value)) {
      candidates.push(value)
    }
  }

  push(process.env.DSH_RENPY_TOOLKIT_PYTHON)
  push(process.env.DSH_PYTHON)

  // The interpreter DSH runs its own tools with. This is the one present on
  // every install, so it is the default answer rather than a fallback.
  const home = process.env.DSH_HOME || path.join(os.homedir(), '.dsh')
  for (const runtime of ['dsh-primary-runtime', 'dsh-runtime']) {
    push(path.join(home, 'dsh-runtimes', runtime, 'dependencies', 'python', 'python.exe'))
    push(path.join(home, 'dsh-runtimes', runtime, 'dependencies', 'python', 'bin', 'python3'))
  }

  // PATH lookup, Windows first because that is where this ships.
  if (process.platform === 'win32') {
    push('python.exe')
    push('py.exe')
    push('python3.exe')
  } else {
    push('python3')
    push('python')
  }

  return candidates
}

/**
 * Run one command with a fixed argv (never a shell string), collecting output.
 *
 * @param {string} command - absolute path or bare name to resolve via PATH.
 * @param {string[]} args - arguments, passed through untouched.
 * @param {{cwd?: string, timeoutMs?: number, env?: Record<string, string>, signal?: AbortSignal}} [options]
 * @returns {Promise<{code: number, stdout: string, stderr: string, timedOut: boolean}>}
 */
function spawnCollect(command, args, options = {}) {
  const { cwd, timeoutMs = DEFAULT_TIMEOUT_MS, env, signal } = options
  return new Promise((resolve) => {
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      resolve(value)
    }

    let child
    try {
      child = execFile(
        command,
        args,
        {
          cwd: cwd || PACKAGE_ROOT,
          timeout: timeoutMs,
          killSignal: 'SIGKILL',
          maxBuffer: MAX_BUFFER,
          windowsHide: true,
          encoding: 'utf8',
          env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1', ...(env || {}) },
          signal,
        },
        (error, stdout, stderr) => {
          const text = String(stdout || '')
          const err = String(stderr || '')
          if (error) {
            const timedOut = error.killed === true || error.signal === 'SIGKILL'
            const code = typeof error.code === 'number' ? error.code : -1
            finish({ code: timedOut ? 124 : code, stdout: text, stderr: err || String(error.message || error), timedOut })
            return
          }
          finish({ code: 0, stdout: text, stderr: err, timedOut: false })
        },
      )
    } catch (error) {
      finish({ code: -1, stdout: '', stderr: String(error && error.message ? error.message : error), timedOut: false })
      return
    }
    child.on('error', (error) => {
      finish({ code: -1, stdout: '', stderr: String(error && error.message ? error.message : error), timedOut: false })
    })
  })
}

/** Cached interpreter probe result, so a session pays for detection once. */
let cachedInterpreter = null

/**
 * Probe one candidate.
 *
 * @param {string} candidate
 * @returns {Promise<{python: string, version: string} | null>}
 */
async function probeInterpreter(candidate) {
  const result = await spawnCollect(candidate, ['-c', 'import sys; print(sys.version_info[0], sys.version_info[1], sys.executable)'], {
    timeoutMs: 20000,
  })
  if (result.code !== 0) return null
  const parts = result.stdout.trim().split(/\s+/)
  if (parts.length < 3) return null
  const major = Number(parts[0])
  const minor = Number(parts[1])
  if (!Number.isFinite(major) || !Number.isFinite(minor)) return null
  if (major < 3 || (major === 3 && minor < 8)) return null
  return { python: candidate, version: `${major}.${minor}`, executable: parts.slice(2).join(' ') }
}

/**
 * Resolve a usable Python 3 interpreter, or explain why there is none.
 *
 * @param {{refresh?: boolean}} [options]
 * @returns {Promise<{python: string, version: string, executable: string}>}
 */
export async function resolveInterpreter(options = {}) {
  if (cachedInterpreter && !options.refresh) return cachedInterpreter
  const tried = []
  for (const candidate of pythonCandidates()) {
    const found = await probeInterpreter(candidate)
    tried.push(`${candidate}${found ? ` (${found.version})` : ' — no answer'}`)
    if (found) {
      cachedInterpreter = found
      return found
    }
  }
  const error = new Error(
    [
      'No usable Python 3 interpreter found, so the toolkit cannot run.',
      'Tried:',
      ...tried.map((line) => `  - ${line}`),
      'Fix: install Python 3.8+ and put it on PATH, or set DSH_RENPY_TOOLKIT_PYTHON to its full path.',
    ].join('\n'),
  )
  error.code = 'NO_PYTHON'
  error.tried = tried
  throw error
}

/** Drop the cached interpreter (tests, and after the user installs Python). */
export function forgetInterpreter() {
  cachedInterpreter = null
}

/**
 * Run the toolkit CLI once.
 *
 * @param {string[]} args - CLI arguments, e.g. `['tl', 'stats', 'game/tl/chinese']`.
 * @param {{cwd?: string, timeoutMs?: number, signal?: AbortSignal}} [options]
 * @returns {Promise<{code: number, stdout: string, stderr: string, timedOut: boolean}>}
 */
export async function runToolkit(args, options = {}) {
  const interpreter = await resolveInterpreter()
  const lib = resolvePythonRoot()
  const existing = process.env.PYTHONPATH
  const env = {
    PYTHONPATH: existing ? `${lib}${path.delimiter}${existing}` : lib,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    // The CLI prints Chinese help text; a legacy code page would mangle it
    // and, worse, mangle paths echoed back in an error message.
    PYTHONDONTWRITEBYTECODE: '1',
  }
  if (process.env.DSH_RENPY_TOOLKIT_DEBUG) {
    process.stderr.write(`[renpy-toolkit] ${interpreter.python} -m renpy_toolkit ${args.join(' ')}\n`)
    process.stderr.write(`[renpy-toolkit] PYTHONPATH=${env.PYTHONPATH}\n`)
  }
  return spawnCollect(interpreter.python, ['-m', 'renpy_toolkit', ...args], { ...options, env })
}

/**
 * Run the CLI and treat a non-zero exit as a failure with the CLI's own message.
 *
 * Exit codes are meaningful here: 0 is clean, 1 means "the command ran and
 * found problems" (that is how `tl verify` reports), 2 is a usage error. Both
 * 1 and 2 carry their explanation on stdout/stderr, so the message is returned
 * rather than thrown away.
 *
 * @param {string[]} args
 * @param {{cwd?: string, timeoutMs?: number, signal?: AbortSignal, okCodes?: number[]}} [options]
 * @returns {Promise<{code: number, stdout: string, stderr: string}>}
 */
export async function runToolkitChecked(args, options = {}) {
  const okCodes = options.okCodes || [0]
  const result = await runToolkit(args, options)
  if (result.timedOut) {
    throw new Error(`renpy-toolkit ${args.join(' ')} timed out. It may be a large release; try a narrower path.`)
  }
  if (!okCodes.includes(result.code)) {
    const detail = (result.stderr || result.stdout || '').trim()
    const error = new Error(detail === '' ? `renpy-toolkit ${args.join(' ')} failed with exit code ${result.code}.` : detail)
    error.exitCode = result.code
    error.args = args
    throw error
  }
  return result
}

/**
 * Where a game directory's translation trees live, and whether a path is one.
 *
 * Ren'Py accepts two layouts and this toolkit distinguishes them everywhere:
 * `<game>/game/tl/<language>/` for a shipped build, and `<project>/tl/...`
 * for a bare project tree. A tool that guesses wrong reports "no files" on a
 * perfectly good directory, so the check is centralized here.
 *
 * @param {string} target
 * @returns {{kind: 'game' | 'project' | 'tl-dir', gameDir: string, tlDir: string|null, language: string|null}}
 */
export function classifyTarget(target) {
  const resolved = path.resolve(target)
  const segments = resolved.split(/[\\/]+/).filter(Boolean)
  const last = segments[segments.length - 1]
  const secondLast = segments[segments.length - 2]

  // A language folder (`game/tl/chinese`) is itself a tl directory for the CLI,
  // but the tree above it is what knows the language names. Report both so a
  // caller never has to re-derive which `tl/` it is looking at.
  if (secondLast === 'tl') {
    const gameDir = path.dirname(path.dirname(resolved))
    return { kind: 'tl-dir', gameDir, tlDir: path.dirname(resolved), language: last }
  }
  if (last === 'tl') {
    return { kind: 'tl-dir', gameDir: path.dirname(resolved), tlDir: resolved, language: null }
  }
  if (last === 'game' && fs.existsSync(path.join(resolved, 'tl'))) {
    return { kind: 'game', gameDir: resolved, tlDir: path.join(resolved, 'tl'), language: null }
  }
  const inner = path.join(resolved, 'game')
  if (fs.existsSync(inner) && fs.existsSync(path.join(inner, 'tl'))) {
    return { kind: 'project', gameDir: inner, tlDir: path.join(inner, 'tl'), language: null }
  }
  const bare = path.join(resolved, 'tl')
  if (fs.existsSync(bare)) {
    return { kind: 'game', gameDir: resolved, tlDir: bare, language: null }
  }
  // A directory of `.rpy` files that are themselves translation blocks. This is
  // the shape of a TL tree kept outside the game (a work directory, a previous
  // release's tl/chinese, a scratch copy), which every command that takes a
  // "TL directory" accepts. Without this check such a path looked like a
  // project with no translations at all, and callers were told to name a
  // language that no directory could ever hold.
  if (holdsTranslateBlocks(resolved)) {
    return { kind: 'tl-dir', gameDir: path.dirname(resolved), tlDir: path.dirname(resolved), language: last, leaf: true }
  }
  return { kind: 'project', gameDir: inner, tlDir: null, language: null }
}

/**
 * Whether a directory's `.rpy` files are translation blocks.
 *
 * Only the first few files are read: one `translate <language> <identifier>:`
 * or `translate <language> strings:` header settles it, and a whole scan would
 * cost seconds on a 50-file tree.
 *
 * @param {string} directory
 * @returns {boolean}
 */
export function holdsTranslateBlocks(directory) {
  let names
  try {
    names = fs.readdirSync(directory).filter((name) => name.endsWith('.rpy'))
  } catch {
    return false
  }
  if (names.length === 0) return false
  for (const name of names.slice(0, 5)) {
    let text
    try {
      text = fs.readFileSync(path.join(directory, name), 'utf8')
    } catch {
      continue
    }
    if (/^translate\s+\S+\s+\S+\s*:/m.test(text)) return true
  }
  return false
}

/**
 * List the languages that actually have a translation directory.
 *
 * @param {string|null} tlDir
 * @returns {string[]}
 */
export function listLanguages(tlDir) {
  if (!tlDir || !fs.existsSync(tlDir)) return []
  return fs
    .readdirSync(tlDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort((a, b) => a.localeCompare(b))
}

/**
 * Find the directory holding one language's files, and name it.
 *
 * Three target shapes all reach this: a path already inside a language folder,
 * a `tl/` root that has to be narrowed to one language, and a work directory
 * kept outside the game that *is* one language. Callers need the directory to
 * hand to the CLI and the name only for reporting, so both come from here.
 *
 * @param {{tlDir: string|null, language: string|null, leaf?: boolean}} info - a classifyTarget result
 * @param {string} target - the resolved path the caller passed
 * @param {string} [requested] - the caller's language, if any
 * @param {{allowNew?: boolean}} [options] - allowNew permits a language that
 *   does not exist yet, which is exactly what generating one requires.
 * @returns {{dir: string, language: string}}
 * @throws when the choice is ambiguous, absent, or names a missing directory
 */
export function resolveLanguageDir(info, target, requested, options = {}) {
  // The target is already one language's files; its own name is the answer and
  // renaming it would describe a directory that does not exist.
  if (info.leaf) return { dir: path.resolve(target), language: info.language }

  if (info.language && info.tlDir) {
    return { dir: path.join(info.tlDir, info.language), language: info.language }
  }

  const languages = listLanguages(info.tlDir)
  const language = requested || null
  if (language) {
    // Language names are case-sensitive identifiers inside Ren'Py: `translate
    // Chinese` is not `translate chinese`. Windows nevertheless makes `tl/Chinese`
    // and `tl/chinese` the same folder, so match without regard to case and then
    // use the spelling the folder actually has.
    const known = languages.find((name) => name.toLowerCase() === language.toLowerCase())
    if (!known && !options.allowNew) {
      throw new Error(
        languages.length > 0
          ? `No translation directory for "${language}". Present: ${languages.join(', ')}. ` +
            'Language names are case-sensitive, so check the exact spelling of the folder.'
          : `No translation directory for "${language}" yet.`,
      )
    }
    const chosen = known || language
    return { dir: path.join(info.tlDir || path.join(info.gameDir, 'tl'), chosen), language: chosen }
  }
  if (languages.length === 1) return { dir: path.join(info.tlDir, languages[0]), language: languages[0] }
  if (languages.length === 0) {
    throw new Error('This tree has no translation language folders yet. Pass language to name one, and use mode=generate to create it.')
  }
  throw new Error(`Several languages are present (${languages.join(', ')}); pass language to choose one.`)
}

/**
 * Point at a `.rpa` archive to read.
 *
 * Archives are found by extension rather than assumed to be `archive.rpa`,
 * because a hand-rolled build can name them anything.
 *
 * @param {string} gameDir
 * @returns {string|null}
 */
export function findArchive(gameDir) {
  let names
  try {
    names = fs.readdirSync(gameDir)
  } catch {
    return null
  }
  const hit = names.find((name) => name.toLowerCase().endsWith('.rpa'))
  return hit === undefined ? null : path.join(gameDir, hit)
}

export { spawnCollect }
