/**
 * `verify-replay-corpus.ts --game-offset`：切块跑时**局号真的往后挪**。
 *
 * 为什么需要它（2026-11-16 云机实测）：Tier B 350 局单进程跑到第 57 局
 * `RangeError: Out of memory`（本机 40 局 RSS 只到 ~315 MB 就平 ⇒ 是环境 cgroup 上限，
 * 不是某一局的数据大）。切块是唯一不依赖环境的修法，但 harness 的局号是从 0 硬编码的
 * ⇒ 每块都会重跑同一批 `(stage, seed)`，切了等于没切。本用例钉住偏移后的映射：
 * `stage = i % 35`、`seed = 2000000001 + floor(i / 35)`。
 */
import { afterAll, beforeAll, describe, it, expect } from 'bun:test'
import { mkdtempSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { writeZeroStudentWeights } from './zero-student-weights'

const REPO_ROOT = join(import.meta.dir, '..', '..')

// 权重夹具现造（零填充生产架构）：`nn-training/weights/**` 是 gitignore。
let weightsDir = ''
let weightsPath = ''
beforeAll(() => {
  weightsDir = mkdtempSync(join(tmpdir(), 'offset-weights-'))
  weightsPath = writeZeroStudentWeights(weightsDir)
})
afterAll(() => {
  if (weightsDir) rmSync(weightsDir, { recursive: true, force: true })
})

/** 跑一小段并返回写出的 shard 目录名（`s<stage>_seed<seed>`）。 */
function runChunk(games: number, offset: number): string[] {
  const work = mkdtempSync(join(tmpdir(), 'offset-'))
  try {
    const p = Bun.spawnSync(
      [
        process.execPath,
        'tools/sim/verify-replay-corpus.ts',
        '--games',
        String(games),
        '--game-offset',
        String(offset),
        '--max-ticks',
        '60',
        '--runtimes',
        '',
        '--keep',
        '--weights',
        weightsPath,
        '--work',
        work,
      ],
      { cwd: REPO_ROOT, stdout: 'pipe', stderr: 'pipe' },
    )
    if (p.exitCode !== 0) {
      throw new Error(`harness exit ${p.exitCode}: ${p.stderr.toString().slice(-800)}`)
    }
    return readdirSync(join(work, 'shards')).sort()
  } finally {
    rmSync(work, { recursive: true, force: true })
  }
}

describe('verify-replay-corpus --game-offset', () => {
  it('offset 把局号推到下一圈（stage 回绕、seed 进 1）', () => {
    // i = 0,1 ⇒ stage 0/1，seed 2000000001（第一圈）
    expect(runChunk(2, 0)).toEqual(['s0_seed2000000001', 's1_seed2000000001'])
    // i = 35,36 ⇒ stage 0/1，seed 2000000002（第二圈）—— 与不偏移**不重合**
    expect(runChunk(2, 35)).toEqual(['s0_seed2000000002', 's1_seed2000000002'])
  }, 120_000)
})
