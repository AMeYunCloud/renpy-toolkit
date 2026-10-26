/**
 * `renpy_inspect`: read-only reconnaissance of a Ren'Py tree.
 *
 * One tool instead of four, because every question here is "what is in this
 * directory" — a game's translation coverage, a TL tree's block counts, the
 * contents of a `.rpa` archive, or whether a folder is a project, a shipped
 * build, or a bare `tl/` directory. Splitting those apart would make the model
 * guess which name to call before it can even tell what the path is.
 *
 * @module dsh-renpy-toolkit/tools/inspect
 */

import fs from 'node:fs'
import path from 'node:path'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { classifyTarget, listLanguages, runToolkitChecked } from '../python.js'

const PARAMETERS = {
  target: {
    type: 'string',
    required: true,
    description:
      'Path to inspect. A Ren\'Py project (the folder holding game/), a shipped game folder (the one holding game/tl/), a tl/ directory, a .rpa archive, or a language directory such as game/tl/chinese.',
  },
  detail: {
    type: 'string',
    enum: ['overview', 'languages', 'coverage', 'archive'],
    default: 'overview',
    description:
      'overview: what the path is and what it contains. languages: which tl/<language> directories exist. coverage: per-file block counts and translation coverage of one language. archive: list the entries inside a .rpa file.',
  },
  language: {
    type: 'string',
    description: 'Which language to report on for coverage. Defaults to the only language present, and is required when several exist.',
  },
}

const OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    kind: { type: 'string', required: true, description: 'What the target turned out to be.' },
    target: { type: 'string', required: true },
    gameDir: { type: 'string', required: true },
    languages: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string', required: true, description: 'The CLI report, verbatim.' },
  },
}

/**
 * Split the CLI's `tl stats` table into the numbers worth repeating in a card.
 *
 * The table is the deliverable; this only pulls a headline out of it so a
 * reader who never opens the tool result still learns the coverage figure.
 *
 * @param {string} text
 * @returns {{files: number|null, blocks: number|null, strings: number|null, coverage: string|null}}
 */
export function parseStats(text) {
  const answer = { files: null, blocks: null, strings: null, coverage: null }
  const tail = /(\d+)\s+file\(s\)[^\n]*?(\d+)\s+translate block\(s\)[^\n]*?(\d+)\s+string\(s\)/.exec(text)
  if (tail) {
    answer.files = Number(tail[1])
    answer.blocks = Number(tail[2])
    answer.strings = Number(tail[3])
  }
  const coverage = /coverage\s+([\d,]+\/[\d,]+\s*\([\d.]+%\))/.exec(text)
  if (coverage) answer.coverage = coverage[1]
  return answer
}

/**
 * Choose the language a coverage report should describe.
 *
 * A `tl/` tree with one language is unambiguous, and asking the user to name it
 * anyway is pure friction. With several, guessing would silently answer the
 * wrong question, so the tool names them and asks.
 *
 * @param {string[]} languages
 * @param {string|undefined} requested
 * @returns {string}
 * @throws when the choice is ambiguous or the named language is absent
 */
export function pickLanguage(languages, requested) {
  if (requested) {
    if (languages.length > 0 && !languages.includes(requested)) {
      throw new Error(`No translation directory for "${requested}". Present: ${languages.join(', ')}.`)
    }
    return requested
  }
  if (languages.length === 1) return languages[0]
  if (languages.length === 0) {
    throw new Error('This directory has no translation language folders yet. Run renpy_translate with mode=generate to create one.')
  }
  throw new Error(`Several languages are present (${languages.join(', ')}); pass language to say which one to report on.`)
}

function renderInspect(args, value) {
  const head = [`${value.kind}: ${value.target}`]
  if (value.languages.length > 0) head.push(`languages: ${value.languages.join(', ')}`)
  return [{ type: 'text', text: `${head.join('\n')}\n\n${value.summary}` }]
}

function renderOutcome(args, result) {
  // `result` is the settled ToolResult: its `meta` is what this tool's
  // presentationMeta produced, and it is absent on a failed call or a nested
  // dispatch, in which case the generic card is the right answer.
  const record = result && typeof result === 'object' ? result.meta : undefined
  if (!record || typeof record !== 'object') return undefined
  if (typeof record.kind !== 'string') return undefined
  return { card: 'generic', title: `Ren'Py ${record.kind}: ${path.basename(String(record.target || args.target))}` }
}

export function registerInspectTool(ctx) {
  ctx.tools.register(
    defineTool({
      name: 'renpy_inspect',
      description: [
        'Look at a Ren\'Py tree before changing anything: which translation languages exist, how much dialogue and how many UI strings a language block covers per file, what a .rpa archive contains, or simply what a path is.',
        'Reports block counts and coverage percentages, which is how you tell "the translation is complete" from "the generator wrote a skeleton".',
        'Everything here is read-only. Use it to find the right directory before calling renpy_translate or renpy_check.',
      ].join(' '),
      parameters: PARAMETERS,
      output: {
        schema: OUTPUT_SCHEMA,
        render: renderInspect,
        presentationMeta: (args, value) => ({ kind: value.kind, target: value.target }),
      },
      presentCall: (args) => ({ card: 'generic', kind: 'read', title: `Inspect Ren'Py: ${path.basename(args.target)}`, rawInput: args.target }),
      presentResult: renderOutcome,
      isConcurrencySafe: () => true,
      async execute(args, exec) {
        const target = path.resolve(args.target)
        if (!fs.existsSync(target)) throw new Error(`No such path: ${target}`)

        const info = classifyTarget(target)

        if (args.detail === 'archive') {
          if (!fs.statSync(target).isFile()) throw new Error('detail=archive needs a .rpa file, not a directory.')
          const result = await runToolkitChecked(['rpa', 'list', target], { signal: exec.signal })
          return { kind: 'archive', target, gameDir: info.gameDir, languages: [], summary: result.stdout.trim() }
        }

        if (!fs.statSync(target).isDirectory()) {
          throw new Error('This path is a file. Use detail=archive for a .rpa file, or point at a directory.')
        }

        const languages = listLanguages(info.tlDir)

        // Pointing at `game/tl/chinese` already names the language; requiring it
        // again in `language` would be busywork, so classifyTarget's answer wins
        // unless the caller overrode it.
        const named = info.language || null

        if (args.detail === 'languages') {
          return {
            kind: info.kind,
            target,
            gameDir: info.gameDir,
            languages,
            summary:
              languages.length === 0
                ? `No translation languages yet under ${info.tlDir || '(no tl directory)'}.`
                : languages.map((name) => `- ${name}${name === named ? '  (this directory)' : ''}`).join('\n'),
          }
        }

        if (args.detail === 'coverage') {
          const language = pickLanguage(languages, args.language || named)
          const dir = path.join(info.tlDir, language)
          const result = await runToolkitChecked(['tl', 'stats', dir], { signal: exec.signal })
          return { kind: 'coverage', target, gameDir: info.gameDir, languages, summary: result.stdout.trim() }
        }

        // Overview: describe the shape, then let `tl stats` fill in numbers when
        // there is something to measure.
        const lines = [`kind: ${info.kind}`, `game directory: ${info.gameDir}`]
        if (info.tlDir) lines.push(`translations: ${info.tlDir}`)
        lines.push(`languages: ${languages.length === 0 ? '(none)' : languages.join(', ')}`)

        const gameEntries = fs.existsSync(info.gameDir) ? fs.readdirSync(info.gameDir) : []
        const scripts = gameEntries.filter((name) => name.endsWith('.rpy')).length
        const compiled = gameEntries.filter((name) => name.endsWith('.rpyc')).length
        const archives = gameEntries.filter((name) => name.endsWith('.rpa'))
        if (info.kind !== 'tl-dir') {
          lines.push(`scripts: ${scripts} .rpy, ${compiled} .rpyc`)
          if (archives.length > 0) lines.push(`archives: ${archives.join(', ')}`)
          if (scripts === 0 && compiled > 0) {
            lines.push('note: compiled-only build — no .rpy sources, so translation sourcing works from .rpyc (see renpy_translate mode=generate).')
          }
        }

        if (named || languages.length === 1) {
          const only = named || languages[0]
          const stats = await runToolkitChecked(['tl', 'stats', path.join(info.tlDir, only)], { signal: exec.signal })
          lines.push('', `--- ${only} ---`, stats.stdout.trim())
        } else if (languages.length > 1) {
          lines.push('', 'Several languages are present; pass language to get coverage for one of them.')
        }

        return { kind: info.kind, target, gameDir: info.gameDir, languages, summary: lines.join('\n') }
      },
    }),
  )
}

