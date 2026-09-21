/**
 * nn-import-boundary.test.ts — `src/nn/` must stay out of the shipped bundle.
 *
 * `src/nn/` is the offline NN layer (arena ladder, obs encoder, weights, intent
 * policy). It is imported by TOOLS (generators, evaluators, bench, the probe's
 * manifest flattener) and by nothing under `src/` — if a runtime module reached
 * into it, the whole NN layer would land in the game bundle and inflate it.
 *
 * This is the guard human-opening-probe.plan v7 §5 DoD asks for ("无 src/nn 进
 * 游戏包（import 方向单测）"). It is a static-import scan, so it fails on a new
 * `import ... from '../nn/...'` the moment it is written, not at build time.
 *
 * The second test is the POSITIVE CONTROL: `tools/probe/flatten-manifest.ts` is
 * allowed to (and does) import `src/nn/arena-ladder` — if the scan ever went
 * blind (bad glob, wrong cwd), the positive control would fail too.
 */

import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'

const ROOT = join(import.meta.dir, '..')

/** Every `.ts` under `src/`, excluding the `src/nn/` layer itself. */
function runtimeFiles(): string[] {
  return [...new Bun.Glob('src/**/*.ts').scanSync({ cwd: ROOT, onlyFiles: true })]
    .map((p) => p.replace(/\\/g, '/'))
    .filter((p) => !p.startsWith('src/nn/'))
}

/** Static import / re-export / dynamic-import specifiers in a file's text. */
function specifiers(text: string): string[] {
  const out: string[] = []
  const re = /(?:\bfrom|\bimport|\brequire)\s*\(?\s*['"]([^'"]+)['"]/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) out.push(m[1])
  return out
}

/** A relative specifier resolved to a repo-relative path (extension-less ok). */
function resolvesIntoNn(file: string, spec: string): boolean {
  if (!spec.startsWith('.')) return false
  const abs = resolve(ROOT, dirname(file), spec)
  return relative(ROOT, abs).replace(/\\/g, '/').startsWith('src/nn/')
}

describe('src/nn import boundary (plan §5 DoD)', () => {
  it('no runtime module under src/ imports src/nn/', () => {
    const offenders: string[] = []
    for (const file of runtimeFiles()) {
      const text = readFileSync(join(ROOT, file), 'utf8')
      for (const spec of specifiers(text)) {
        if (resolvesIntoNn(file, spec)) offenders.push(`${file} -> ${spec}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('the scan is not vacuous — the probe generator is allowed to import src/nn/', () => {
    const gen = readFileSync(join(ROOT, 'tools/probe/flatten-manifest.ts'), 'utf8')
    const nn = specifiers(gen).filter((s) => resolvesIntoNn('tools/probe/flatten-manifest.ts', s))
    expect(nn.length).toBeGreaterThan(0)
  })
})
