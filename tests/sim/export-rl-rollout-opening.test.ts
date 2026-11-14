import { describe, it, expect } from 'bun:test'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'

const WEIGHTS = 'nn-training/weights/g4-mobility/g4-mobility.it380.20261114-194719.json'
const LEVEL = 'nn-training/levels/classic35-safe-base.jsonc'

/** 读 npy（u1 / N 维）——只够本测试用，不引第三方解析器。 */
function readNpyU8(path: string): number[] {
  const buf = new Uint8Array(readFileSync(path))
  const magic = String.fromCharCode(...buf.slice(0, 6))
  if (magic !== '\x93NUMPY') throw new Error(`not npy: ${path}`)
  const major = buf[6]
  const headerLen =
    major === 1
      ? buf[8] | (buf[9] << 8)
      : buf[8] | (buf[9] << 8) | (buf[10] << 16) | (buf[11] << 24)
  const headerStart = major === 1 ? 10 : 12
  return Array.from(buf.slice(headerStart + headerLen))
}

function run(stage: number, seed: number, extra: string[]): string {
  const out = mkdtempSync(join(tmpdir(), 'rollout-opening-'))
  const args = [
    'bun',
    'tools/sim/export-rl-rollout.ts',
    '--out',
    out,
    '--stages',
    String(stage),
    '--seeds',
    String(seed),
    '--max-ticks',
    '2000',
    '--lives-override',
    '3',
    '--weights',
    WEIGHTS,
    ...extra,
  ]
  const p = Bun.spawnSync(args, { cwd: process.cwd(), stdout: 'pipe', stderr: 'pipe' })
  if (p.exitCode !== 0) {
    throw new Error(`rollout exit ${p.exitCode}: ${p.stderr.toString().slice(-500)}`)
  }
  return out
}

describe('export-rl-rollout: hybrid opening policy_mask', () => {
  it('--opening-all（A/B 全局臂）：落 policy_mask.npy，0 在前 1 在后', () => {
    const out = run(0, 1, ['--opening-connect', '--opening-all'])
    try {
      const mask = readNpyU8(join(out, 'rl_s0_seed1', 'policy_mask.npy'))
      expect(mask.length).toBeGreaterThan(10)
      expect(mask.every((v) => v === 0 || v === 1)).toBe(true)
      const first1 = mask.indexOf(1)
      expect(first1).toBeGreaterThan(0)
      expect(mask.slice(first1).every((v) => v === 1)).toBe(true)
    } finally {
      rmSync(out, { recursive: true, force: true })
    }
  }, 50_000)

  it('默认条件接管：stage 0 路本来就连通 ⇒ 不抢方向盘，不落 policy_mask', () => {
    const out = run(0, 1, ['--opening-connect'])
    try {
      expect(existsSync(join(out, 'rl_s0_seed1', 'policy_mask.npy'))).toBe(false)
    } finally {
      rmSync(out, { recursive: true, force: true })
    }
  }, 50_000)

  it('默认条件接管：Checkers 路被堵死 ⇒ 接管并落 policy_mask（含 0）', async () => {
    const level = Bun.JSONC.parse(await Bun.file(LEVEL).text()) as { stages: unknown[] }
    const stageJson = JSON.stringify(level.stages[20])
    const out = run(2020, 1, ['--opening-connect', '--stage-json', stageJson])
    try {
      const p = join(out, 'rl_s2020_seed1', 'policy_mask.npy')
      expect(existsSync(p)).toBe(true)
      const mask = readNpyU8(p)
      expect(mask.length).toBeGreaterThan(10)
      expect(mask.some((v) => v === 0)).toBe(true)
    } finally {
      rmSync(out, { recursive: true, force: true })
    }
  }, 50_000)
})
