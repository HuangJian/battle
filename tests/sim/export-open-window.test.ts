import { describe, it, expect, afterAll } from 'bun:test'
import { readFileSync, rmSync, mkdtempSync, readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { shouldKeepDecision } from '../../tools/sim/export-replay-labels'

/** §55 Opening-BC：tick 窗＋tick0 剔除（`export-replay-labels --tick-max/--drop-tick0`）。 */

describe('shouldKeepDecision', () => {
  it('缺省全量：任何 tick 都保留', () => {
    expect(shouldKeepDecision(0, Infinity, false)).toBe(true)
    expect(shouldKeepDecision(599, Infinity, false)).toBe(true)
    expect(shouldKeepDecision(99999, Infinity, false)).toBe(true)
  })
  it('tick 窗：tick < max 保留，边界精确', () => {
    expect(shouldKeepDecision(599, 600, false)).toBe(true)
    expect(shouldKeepDecision(600, 600, false)).toBe(false)
    expect(shouldKeepDecision(601, 600, false)).toBe(false)
  })
  it('dropTick0 只剔 tick==0 那一拍', () => {
    expect(shouldKeepDecision(0, 600, true)).toBe(false)
    expect(shouldKeepDecision(0, Infinity, true)).toBe(false)
    expect(shouldKeepDecision(10, 600, true)).toBe(true)
  })
})

describe('export CLI（集成：fixture replay）', () => {
  const tmpRoots: string[] = []
  afterAll(() => {
    for (const d of tmpRoots) rmSync(d, { recursive: true, force: true })
  })

  function readManifests(dir: string): any[] {
    const out: any[] = []
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      if (!e.isDirectory()) continue
      const mp = join(dir, e.name, 'manifest.json')
      if (existsSync(mp)) out.push(JSON.parse(readFileSync(mp, 'utf8')))
    }
    return out
  }

  it('开窗跑通：manifest 记窗口，保真不跳过 fixture 局', () => {
    const dir = mkdtempSync(join(tmpdir(), 'openwin-'))
    tmpRoots.push(dir)
    const p = Bun.spawnSync(
      [
        'bun',
        'tools/sim/export-replay-labels.ts',
        '--replays',
        'replays',
        '--min-kills',
        '0',
        '--out',
        dir,
        '--tick-max',
        '600',
        '--drop-tick0',
      ],
      { cwd: process.cwd(), stdout: 'pipe', stderr: 'pipe' },
    )
    expect(p.exitCode).toBe(0)
    const ms = readManifests(dir)
    expect(ms.length).toBeGreaterThan(0)
    for (const m of ms) {
      expect(m.tickWindow).toEqual({ max: 600, dropTick0: true })
      expect(m.nSamples).toBeGreaterThan(0)
    }
  })

  it('默认全量：manifest 无 tickWindow 键（与改前字节兼容）', () => {
    const dir = mkdtempSync(join(tmpdir(), 'openwin-'))
    tmpRoots.push(dir)
    const p = Bun.spawnSync(
      [
        'bun',
        'tools/sim/export-replay-labels.ts',
        '--replays',
        'replays',
        '--min-kills',
        '0',
        '--out',
        dir,
      ],
      { cwd: process.cwd(), stdout: 'pipe', stderr: 'pipe' },
    )
    expect(p.exitCode).toBe(0)
    const ms = readManifests(dir)
    expect(ms.length).toBeGreaterThan(0)
    for (const m of ms) expect('tickWindow' in m).toBe(false)
  })
})
