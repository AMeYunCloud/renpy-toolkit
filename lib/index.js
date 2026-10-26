/**
 * DSH plugin entry for renpy-toolkit.
 *
 * The toolkit itself is Python (standard library only) and stays that way: it
 * reads `.rpyc`/`.rpa` byte formats and walks Ren'Py's own AST, none of which is
 * better off rewritten in JS. This plugin contributes the agent-facing surface —
 * tools that shell out to the CLI, and the bundled skill that explains the
 * workflow — plus the discovery of a Python interpreter the host already ships.
 *
 * @module dsh-renpy-toolkit
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { registerInspectTool } from './tools/inspect.js'
import { registerTranslateTool } from './tools/translate.js'
import { registerCheckTool } from './tools/check.js'
import { resolvePythonRoot, resolveInterpreter } from './python.js'

export const name = 'renpy-toolkit'

/**
 * Services this plugin reads: `tools` to publish the tools, `skills` to publish
 * the bundled skill. Both are host-provided, so nothing is imported for them.
 */
export const inject = ['tools', 'skills']

const SKILL_PROVIDER = 'renpy-toolkit'
const SKILL_NAME = 'renpy-toolkit'

/**
 * Locate the bundled SKILL.md.
 *
 * Two layouts exist: the repository (skill/renpy-toolkit/SKILL.md) and the
 * published package (skills/renpy-toolkit/SKILL.md). Both are relative to the
 * package root, so try each instead of failing on one.
 *
 * @returns {string|null}
 */
function findSkillFile() {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
  const candidates = [
    path.join(root, 'skills', SKILL_NAME, 'SKILL.md'),
    path.join(root, 'skill', SKILL_NAME, 'SKILL.md'),
  ]
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate
  }
  return null
}

/**
 * Split a SKILL.md into frontmatter fields and body.
 *
 * The registry wants `name` and `description` as separate fields; the file keeps
 * them in YAML frontmatter so the same document also works as a plain skill on
 * disk. Only the two keys that matter are read — a general YAML parser would be
 * a dependency for no benefit.
 *
 * @param {string} text
 * @returns {{name: string, description: string, whenToUse: string|undefined, content: string}}
 */
function parseSkillDocument(text) {
  const record = { name: SKILL_NAME, description: '', whenToUse: undefined, content: text }
  if (!text.startsWith('---')) return record

  const end = text.indexOf('\n---', 3)
  if (end === -1) return record

  const front = text.slice(3, end)
  record.content = text.slice(text.indexOf('\n', end + 1) + 1)

  // `description:` may be a folded block (`>`), which continues on indented
  // lines until the next top-level key.
  const lines = front.split(/\r?\n/)
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const match = /^([A-Za-z_][\w-]*):\s*(.*)$/.exec(line)
    if (!match) continue
    const key = match[1]
    let value = match[2].trim()

    if (value === '>' || value === '|' || value === '>-' || value === '|-') {
      const collected = []
      for (let next = index + 1; next < lines.length; next += 1) {
        const continuation = lines[next]
        if (continuation.trim() === '') continue
        if (!/^\s/.test(continuation)) break
        collected.push(continuation.trim())
        index = next
      }
      value = collected.join(value.startsWith('|') ? '\n' : ' ')
    } else if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1)
    }

    if (key === 'name') record.name = value || SKILL_NAME
    else if (key === 'description') record.description = value
    else if (key === 'whenToUse' || key === 'when_to_use') record.whenToUse = value
  }
  return record
}

/**
 * Register the bundled skill so the model can load it like any other skill.
 *
 * @param {import('@deepseek-ai/cordis').Context} ctx
 */
function registerSkill(ctx) {
  if (!ctx || typeof ctx.skills?.registerProvider !== 'function') return

  const file = findSkillFile()
  if (file === null) {
    ctx.logger?.warn?.('renpy-toolkit: SKILL.md not found next to the package; skipping skill registration')
    return
  }

  const base = path.dirname(file)

  const provider = {
    name: SKILL_PROVIDER,
    list() {
      // Reading on every list() keeps an edited SKILL.md visible without a
      // restart, and the file is small enough that caching it would be the
      // only thing that ever goes stale.
      let document
      try {
        document = parseSkillDocument(fs.readFileSync(file, 'utf8'))
      } catch (error) {
        throw new Error(`renpy-toolkit: cannot read ${file}: ${error instanceof Error ? error.message : String(error)}`)
      }
      return Promise.resolve([
        {
          name: document.name,
          description: document.description,
          ...(document.whenToUse === undefined ? {} : { whenToUse: document.whenToUse }),
          invocation: { modelInvocable: true, userInvocable: true },
          provider: SKILL_PROVIDER,
          source: 'bundled',
          resourceBase: { kind: 'directory', path: base },
          // 600 is the documented rank for packaged skill providers; leaving it
          // out would let a project-local skill shadow this one silently.
          rank: 600,
          locator: file,
        },
      ])
    },
    get(candidate) {
      const target = typeof candidate?.locator === 'string' ? candidate.locator : file
      const document = parseSkillDocument(fs.readFileSync(target, 'utf8'))
      return Promise.resolve({
        name: document.name,
        description: document.description,
        ...(document.whenToUse === undefined ? {} : { whenToUse: document.whenToUse }),
        invocation: { modelInvocable: true, userInvocable: true },
        provider: SKILL_PROVIDER,
        source: 'bundled',
        resourceBase: { kind: 'directory', path: path.dirname(target) },
        content: document.content,
      })
    },
  }

  ctx.skills.registerProvider(() => provider)
}

/**
 * Plugin apply hook.
 *
 * @param {import('@deepseek-ai/cordis').Context} ctx
 */
export function apply(ctx) {
  registerInspectTool(ctx)
  registerTranslateTool(ctx)
  registerCheckTool(ctx)
  registerSkill(ctx)

  // Resolve the interpreter once at startup so a missing Python shows up in the
  // log now rather than as the first tool call failing later.
  resolveInterpreter()
    .then((interpreter) => {
      ctx.logger?.info?.(`renpy-toolkit: using python ${interpreter.version.join('.')} at ${interpreter.python}`)
      ctx.logger?.info?.(`renpy-toolkit: python library at ${resolvePythonRoot()}`)
    })
    .catch((error) => {
      ctx.logger?.warn?.(
        `renpy-toolkit: no usable Python interpreter found (${error instanceof Error ? error.message : String(error)}); ` +
          'the tools will retry on each call. Set DSH_RENPY_TOOLKIT_PYTHON to point at one.',
      )
    })
}

export { parseSkillDocument, findSkillFile }
