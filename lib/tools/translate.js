/**
 * `renpy_translate`: produce and repair a translation tree.
 *
 * The three modes are the three things a localizer actually does, in order:
 * generate a skeleton from whatever the release shipped, port finished work into
 * a regenerated skeleton after a game update, and tidy the files so a human can
 * find things. They share a target classifier and a Python launcher, and each one
 * means something different for the filesystem, so they stay one tool with a mode
 * rather than three names to remember.
 *
 * Every mode is dry-run unless `write` is true; that default is deliberate,
 * because the generator rewrites thousands of files.
 *
 * @module dsh-renpy-toolkit/tools/translate
 */

import fs from 'node:fs'
import path from 'node:path'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { classifyTarget, resolveLanguageDir, runToolkitChecked } from '../python.js'

const PARAMETERS = {
  mode: {
    type: 'string',
    enum: ['generate', 'port', 'sort'],
    required: true,
    description: [
      'generate: build a full translate-block skeleton for a language from the game\'s own scripts (works on compiled-only releases that have no .rpy sources).',
      'port: copy finished translations from an older TL tree into a freshly generated one, matching blocks by translation identifier so reordered or edited scenes do not land the译文 on the wrong line.',
      'sort: reorder blocks within each file by identifier, optionally adding a per-label index and section markers.',
    ].join(' '),
  },
  target: {
    type: 'string',
    required: true,
    description: 'The game folder (the one holding game/), or a tl/ directory. For port, this is the destination tree that receives the译文.',
  },
  language: {
    type: 'string',
    description: 'Language directory name, e.g. chinese. Defaults to the language named by target if the path already points inside one.',
  },
  outdir: {
    type: 'string',
    description: 'generate only: write the skeleton here instead of into <game>/tl/<language>. Use a scratch directory to inspect output before touching the game.',
  },
  source: {
    type: 'string',
    description: 'port only: the older TL directory holding finished translations (for example a previous release\'s tl/chinese).',
  },
  write: {
    type: 'boolean',
    default: false,
    description: 'Actually modify files. Without this every mode only reports what it would do.',
  },
  withStrings: {
    type: 'boolean',
    default: true,
    description: 'generate only: also collect UI strings, menu labels and hardcoded text literals. Passing false gives dialogue blocks alone.',
  },
  index: {
    type: 'boolean',
    default: false,
    description: 'sort only: prepend an Event Index comment block listing each label and the line its first block starts on.',
  },
  sections: {
    type: 'boolean',
    default: false,
    description: 'sort only: insert "# --- <label> ---" markers between label groups so a large file can be navigated.',
  },
  timeoutSeconds: {
    type: 'integer',
    default: 1800,
    description: 'Give up after this long. Full generation over a 58k-block game takes roughly 20-30 seconds; a cold start on a large game can take a few minutes.',
  },
}

const OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    mode: { type: 'string', required: true },
    target: { type: 'string', required: true },
    language: { type: 'string', required: true },
    wrote: { type: 'boolean', required: true, description: 'Whether files were actually modified or created.' },
    report: { type: 'string', required: true, description: 'The CLI output, verbatim.' },
  },
}

function summarize(mode, text) {
  const parts = []

  if (mode === 'sort') {
    // Each file gets a line: `would write script.rpy: 2 block(s), 1 strings block(s) kept`
    const files = [...text.matchAll(/^(?:would write|wrote)\s+([^:\n]+):\s*(\d+)\s+block\(s\)(?:,\s*(\d+)\s+strings block\(s\) kept)?/gm)]
    if (files.length > 0) {
      const blocks = files.reduce((total, line) => total + Number(line[2]), 0)
      const strings = files.reduce((total, line) => total + Number(line[3] || 0), 0)
      parts.push(`${files.length} file(s)`, `${blocks} block(s)`)
      if (strings > 0) parts.push(`${strings} strings block(s)`)
    }
  } else {
    // generate: `<lang>: 58249 translate block(s), 2705 string(s) in 52 file(s)`
    // port:     same shape, plus a separate `<n> block(s) ported` line
    const counts = /(\d+)\s+translate block\(s\)(?:,\s*(\d+)\s+string\(s\))?(?:\s+in\s+(\d+)\s+file\(s\))?/.exec(text)
    if (counts) {
      parts.push(`${counts[1]} block(s)`)
      if (counts[2]) parts.push(`${counts[2]} string(s)`)
      // The summary line's own file count reads 0 under --no-strings; the line
      // that states what would be written is the reliable one.
      const planned = /(?:would write|wrote)\s+(\d+)\s+file\(s\)/.exec(text)
      const files = planned ? planned[1] : counts[3]
      if (files) parts.push(`${files} file(s)`)
    }
    if (mode === 'port') {
      const ported = /(\d+)\s+(?:block\(s\)\s+)?ported/i.exec(text)
      if (ported) parts.push(`${ported[1]} ported`)
    }
  }

  return parts.length === 0 ? mode : parts.join(', ')
}

function renderTranslate(args, value) {
  const verb = value.wrote ? 'wrote' : 'dry run'
  return [{ type: 'text', text: `${value.mode} ${value.language} (${verb})\n${value.target}\n\n${value.report}` }]
}

export function registerTranslateTool(ctx) {
  ctx.tools.register(
    defineTool({
      name: 'renpy_translate',
      description: [
        'Build or repair a Ren\'Py translation tree. mode=generate writes a skeleton with every dialogue block and UI string the game has, sourcing them from .rpyc when the release ships no source — that is what makes a compiled game translatable at all.',
        'mode=port moves finished译文 from an older tree into a regenerated one by matching translation identifiers, so a game update does not throw the work away.',
        'mode=sort reorders blocks and can add an Event Index and section markers.',
        'Everything is a dry run unless write=true. Generation over a large game takes about half a minute.',
      ].join(' '),
      parameters: PARAMETERS,
      output: {
        schema: OUTPUT_SCHEMA,
        render: renderTranslate,
        presentationMeta: (args, value) => ({ mode: value.mode, title: summarize(value.mode, value.report) }),
      },
      presentCall: (args) => ({
        card: 'generic',
        kind: 'edit',
        title: `${args.mode === 'generate' ? 'Generate' : args.mode === 'port' ? 'Port' : 'Sort'} translation${args.write ? '' : ' (dry run)'}: ${path.basename(args.target)}`,
        rawInput: args.outdir || args.source || args.target,
      }),
      presentResult: (args, result) => {
        const meta = result && typeof result === 'object' ? result.meta : undefined
        if (meta && typeof meta === 'object' && typeof meta.title === 'string') {
          return { card: 'generic', title: meta.title }
        }
        return undefined
      },
      isConcurrencySafe: () => false,
      async execute(args, exec) {
        const target = path.resolve(args.target)
        if (!fs.existsSync(target)) throw new Error(`No such path: ${target}`)

        const info = classifyTarget(target)
        // Generating creates the language, so naming one that does not exist yet
        // is the normal case there and a typo everywhere else.
        const { dir, language } = resolveLanguageDir(info, target, args.language, { allowNew: args.mode === 'generate' })
        const timeoutMs = Math.max(30, Math.min(7200, args.timeoutSeconds || 1800)) * 1000
        const write = args.write === true

        if (args.mode === 'generate') {
          const outdir = args.outdir ? path.resolve(args.outdir) : dir
          // `gen` takes the game root (the folder holding game/). Derive it from
          // the classified game directory so a `game/` or `tl/` target works too.
          const root = path.dirname(info.gameDir)
          const basedir = fs.existsSync(path.join(root, 'game')) ? root : info.gameDir
          const argv = ['tl', 'gen', basedir, language, '--outdir', outdir]
          if (args.withStrings === false) argv.push('--no-strings')
          // `--write` is what makes the CLI create files at all; without it the
          // command only counts what it would produce.
          if (write) argv.push('--write')
          const result = await runToolkitChecked(argv, { signal: exec.signal, timeoutMs })
          return { mode: args.mode, target, language, wrote: write, report: result.stdout.trim() || result.stderr.trim() }
        }

        if (args.mode === 'port') {
          if (!args.source) throw new Error('mode=port needs source: the older TL directory with finished translations.')
          const source = path.resolve(args.source)
          if (!fs.existsSync(source)) throw new Error(`No such source tree: ${source}`)
          // Positional, not flags: `tl port <skeleton> <reference>`.
          const argv = ['tl', 'port', dir, source]
          if (write) argv.push('--write')
          const result = await runToolkitChecked(argv, { signal: exec.signal, timeoutMs })
          return { mode: args.mode, target, language, wrote: write, report: result.stdout.trim() || result.stderr.trim() }
        }

        // sort
        const argv = ['tl', 'sort', dir]
        if (args.index) argv.push('--index')
        if (args.sections) argv.push('--sections')
        if (write) argv.push('--write')
        const result = await runToolkitChecked(argv, { signal: exec.signal, timeoutMs })
        return { mode: args.mode, target, language, wrote: write, report: result.stdout.trim() || result.stderr.trim() }
      },
    }),
  )
}
