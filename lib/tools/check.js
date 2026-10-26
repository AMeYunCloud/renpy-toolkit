/**
 * `renpy_check`: verify a translation tree and find what cannot be translated.
 *
 * A finished-looking TL tree fails in ways that produce no error at all: an empty
 * `new` renders as a blank dialogue box, a mistyped `{color=...}` tag drops the
 * formatting, a duplicate `old` crashes the game at startup with "A translation
 * for X already exists". None of those show up until someone plays the game in
 * that language, so they have to be checked mechanically before shipping.
 *
 * @module dsh-renpy-toolkit/tools/check
 */

import fs from 'node:fs'
import path from 'node:path'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { classifyTarget, listLanguages, runToolkitChecked } from '../python.js'

const PARAMETERS = {
  target: {
    type: 'string',
    required: true,
    description: 'The tl/ directory to check, or a language directory such as game/tl/chinese.',
  },
  language: {
    type: 'string',
    description: 'Language directory name when target is the tl/ folder itself.',
  },
  expectDialogue: {
    type: 'integer',
    description: 'Assert the tree holds exactly this many dialogue blocks. Take the number from the game\'s own `lint` output — it is the only authority on how many blocks exist.',
  },
  gameDir: {
    type: 'string',
    description: 'The game\'s script directory. Passing it lets the checker read custom text tags the project registers, so a legal project tag is not reported as a typo.',
  },
  strict: {
    type: 'boolean',
    default: false,
    description: 'Treat warnings as failures too. Off by default because an untranslated skeleton is thousands of warnings by design.',
  },
  fullwidth: {
    type: 'boolean',
    default: false,
    description: 'Also flag fullwidth punctuation, which Ren\'Py renders inconsistently in some fonts.',
  },
  allowEmpty: {
    type: 'boolean',
    default: false,
    description: 'Do not treat empty译文 as errors. Use this on a freshly generated skeleton, where every block is still untranslated.',
  },
  maxItems: {
    type: 'integer',
    default: 80,
    description: 'How many individual findings to list. The totals are always reported in full.',
  },
}

const OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    ok: { type: 'boolean', required: true, description: 'Whether the check passed at the requested severity.' },
    files: { type: 'integer', description: 'TL files examined.' },
    blocks: { type: 'integer', description: 'Dialogue blocks found.' },
    strings: { type: 'integer', description: 'UI string entries found.' },
    coverage: { type: 'string', description: 'Translated-over-total, as reported.' },
    errors: { type: 'integer', required: true },
    warnings: { type: 'integer', required: true },
    report: { type: 'string', required: true, description: 'The CLI output verbatim.' },
  },
}

function count(pattern, text) {
  const match = pattern.exec(text)
  return match ? Number(match[1]) : undefined
}

export function registerCheckTool(ctx) {
  ctx.tools.register(
    defineTool({
      name: 'renpy_check',
      description: [
        'Check a Ren\'Py translation tree for the failures that are silent until a player sees them: empty译文 that blank a dialogue box, broken or unknown text tags, unbalanced tags, placeholder mismatches between source and译文, duplicate entries that crash the game at startup, and stray statements that fell out of a block.',
        'Pass expectDialogue with the block count from the game\'s own lint output to prove the tree is complete rather than merely parseable.',
        'Pass gameDir so project-registered custom tags are recognised. Read-only.',
      ].join(' '),
      parameters: PARAMETERS,
      output: {
        schema: OUTPUT_SCHEMA,
        render: (args, value) => {
          const head = value.ok ? 'check: OK' : 'check: findings'
          const numbers = [`${value.errors} error(s)`, `${value.warnings} warning(s)`]
          if (value.blocks !== undefined) numbers.push(`${value.blocks} block(s)`)
          if (value.coverage) numbers.push(`coverage ${value.coverage}`)
          return [{ type: 'text', text: `${head} — ${numbers.join(', ')}\n\n${value.report}` }]
        },
        presentationMeta: (args, value) => ({ ok: value.ok, errors: value.errors, warnings: value.warnings }),
      },
      presentCall: (args) => ({
        card: 'generic',
        kind: 'search',
        title: `Check translation: ${path.basename(args.target)}`,
        rawInput: args.target,
      }),
      presentResult: (args, result) => {
        const meta = result && typeof result === 'object' ? result.meta : undefined
        if (!meta || typeof meta !== 'object') return undefined
        if (typeof meta.errors !== 'number') return undefined
        const verdict = meta.ok ? 'clean' : `${meta.errors} error(s)`
        return { card: 'generic', title: `Check translation: ${verdict}, ${meta.warnings} warning(s)` }
      },
      isConcurrencySafe: () => true,
      async execute(args, exec) {
        const target = path.resolve(args.target)
        if (!fs.existsSync(target)) throw new Error(`No such path: ${target}`)

        const info = classifyTarget(target)
        const languages = listLanguages(info.tlDir)
        let dir = target
        // A bare `tl/` folder holds one subfolder per language, and checking the
        // parent would just count them as files. Resolve down to one language.
        if (fs.statSync(target).isDirectory() && !info.language) {
          const nested = target === info.tlDir || info.kind === 'tl-dir'
          if (nested) {
            const name = args.language || (languages.length === 1 ? languages[0] : null)
            if (!name) {
              throw new Error(
                languages.length === 0
                  ? 'That directory holds no language folders; point at a language directory instead.'
                  : `Several languages are present (${languages.join(', ')}); pass language to pick one.`,
              )
            }
            dir = path.join(target, name)
          }
        }

        const argv = ['tl', 'verify', dir, '--max-items', String(Math.max(1, Math.min(2000, args.maxItems || 80)))]
        if (args.strict) argv.push('--strict')
        if (args.fullwidth) argv.push('--fullwidth')
        if (args.allowEmpty) argv.push('--allow-empty')
        if (args.expectDialogue) argv.push('--expect-dialogue', String(args.expectDialogue))
        const gameDir = args.gameDir ? path.resolve(args.gameDir) : info.gameDir
        if (gameDir && fs.existsSync(gameDir)) argv.push('--game-dir', gameDir)

        const result = await runToolkitChecked(argv, { okCodes: [0, 1], signal: exec.signal })
        const text = `${result.stdout}\n${result.stderr}`.trim()
        const errors = count(/(\d+)\s+error\(s\)/, text) ?? 0
        const warnings = count(/(\d+)\s+warning\(s\)/, text) ?? 0

        return {
          ok: result.code === 0,
          files: count(/(\d+)\s+file\(s\)/, text),
          blocks: count(/(\d+)\s+translate block\(s\)/, text),
          strings: count(/(\d+)\s+string\(s\)/, text),
          coverage: (/coverage\s+([\d,]+\/[\d,]+\s*\([\d.]+%\))/.exec(text) || [])[1],
          errors,
          warnings,
          report: text,
        }
      },
    }),
  )
}
