/**
 * export-rl-rollout `--corpus-fmt=replay` + replay-to-shard 的 P1 契约测试
 * （plan/replay-corpus.plan.md P1 Gate）：
 *
 *   ① full 路径 shard 仍是 obs/scalars/extra + 标签；replay 路径不产这三张张量，
 *      只产 replay.json/replay_meta.json + 非 obs 标签，且标签字节与 full 逐字节相同。
 *   ② replay → obs/scalars/extra 重建与 full 路径**逐字节相同**（identity 主判）。
 *   ③ 坏 replay_meta（重复 decision tick / obsHash 不符 / 缺 replay.json）⇒ 转换器
 *      响亮失败且不写半成品（确定性失败零容差）。
 */
import { describe, it, expect, beforeAll, afterAll } from 'bun:test'
import {
  cpSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { unpackContainer } from '../../tools/sim/pack-container'
import { METRICS_COLUMN_NAMES } from '../../tools/sim/export-rl-rollout'

const WEIGHTS = 'nn-training/weights/g4-mobility/g4-mobility.it380.20261114-194719.json'
const LEVEL = 'nn-training/levels/classic35-safe-base.jsonc'
const MAX_TICKS = '600'
const SHARD = 'rl_s0_seed1'

let root = ''
let fullShard = ''
let replayShard = ''

function runRollout(outDir: string, extra: string[], stage = '0'): void {
  const p = Bun.spawnSync(
    [
      'bun',
      'tools/sim/export-rl-rollout.ts',
      '--out',
      outDir,
      '--stages',
      stage,
      '--seeds',
      '1',
      '--max-ticks',
      MAX_TICKS,
      '--lives-override',
      '3',
      '--weights',
      WEIGHTS,
      ...extra,
    ],
    { cwd: process.cwd(), stdout: 'pipe', stderr: 'pipe' },
  )
  if (p.exitCode !== 0) {
    throw new Error(`rollout exit ${p.exitCode}: ${p.stderr.toString().slice(-800)}`)
  }
}

function runConvert(shardDir: string, outDir: string): ReturnType<typeof Bun.spawnSync> {
  return Bun.spawnSync(
    ['bun', 'tools/sim/replay-to-shard.ts', '--shard-dir', shardDir, '--out', outDir],
    { cwd: process.cwd(), stdout: 'pipe', stderr: 'pipe' },
  )
}

function sameBytes(a: string, b: string): boolean {
  return readFileSync(a).equals(readFileSync(b))
}

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), 'rollout-replay-'))
  runRollout(join(root, 'full'), [])
  runRollout(join(root, 'replay'), ['--corpus-fmt', 'replay'])
  fullShard = join(root, 'full', SHARD)
  replayShard = join(root, 'replay', SHARD)
}, 120_000)

afterAll(() => {
  if (root) rmSync(root, { recursive: true, force: true })
})

describe('export-rl-rollout: --corpus-fmt=replay', () => {
  it('replay 不产 obs/scalars/extra，只产 replay + 非 obs 标签', () => {
    const full = new Set(readdirSync(fullShard))
    const replay = new Set(readdirSync(replayShard))
    expect(full.has('obs.npy')).toBe(true)
    expect(full.has('scalars.npy')).toBe(true)
    expect(full.has('extra.npy')).toBe(true)
    expect(full.has('replay.json')).toBe(false)

    expect(replay.has('obs.npy')).toBe(false)
    expect(replay.has('scalars.npy')).toBe(false)
    expect(replay.has('extra.npy')).toBe(false)
    expect(replay.has('replay.json')).toBe(true)
    expect(replay.has('replay_meta.json')).toBe(true)
    // 标签成员仍在，且与 full 路径逐字节相同（replay 只改传输形态，不改语料语义）。
    for (const name of ['a_move.npy', 'a_fire.npy', 'lp_move.npy', 'value.npy', 'metrics.npy']) {
      expect(replay.has(name)).toBe(true)
      expect(sameBytes(join(fullShard, name), join(replayShard, name))).toBe(true)
    }
  })

  it('replay_meta 与 decisionTicks 自洽', () => {
    const meta = JSON.parse(readFileSync(join(replayShard, 'replay_meta.json'), 'utf8')) as {
      corpus_fmt: string
      nSamples: number
      decisionTicks: number
      decisionTicksList: number[]
      obsHash: string
      recordMs: number
      bytes: number
    }
    expect(meta.corpus_fmt).toBe('replay')
    expect(meta.decisionTicks).toBe(meta.nSamples)
    expect(meta.decisionTicksList.length).toBe(meta.nSamples)
    expect(meta.obsHash).toMatch(/^sha256:[0-9a-f]{64}$/)
    expect(meta.recordMs).toBeGreaterThanOrEqual(0)
    expect(meta.bytes).toBeGreaterThan(0)
    // 严格递增 + 落在 [0, ticks) —— 转换器会在重建前再校验一次。
    for (let i = 1; i < meta.decisionTicksList.length; i++) {
      expect(meta.decisionTicksList[i]).toBeGreaterThan(meta.decisionTicksList[i - 1])
    }
  })

  it('replay → obs/scalars/extra 与 full 逐字节相同（identity 主判）', () => {
    const out = join(root, 'converted')
    const p = runConvert(replayShard, out)
    expect(p.exitCode).toBe(0)
    for (const name of ['obs.npy', 'scalars.npy', 'extra.npy']) {
      expect(existsSync(join(out, name))).toBe(true)
      expect(sameBytes(join(fullShard, name), join(out, name))).toBe(true)
    }
  }, 60_000)

  it('metrics.npy 列主序（fortran_order=True）且逻辑值仍按 [行, 列] 取', () => {
    // payload 瘦身 B（docs/nn/remote-transport.md §154 ⑤）：列主序只为压缩率，
    // **逻辑数组不变**。独立解码（不 import 写端）：头里 fortran_order=True、
    // 元素 (i,j) 在 `i + j*rows`，最后一列与 manifest.metricValues 逐列对得上。
    const buf = readFileSync(join(replayShard, 'metrics.npy'))
    const hlen = buf.readUInt16LE(8)
    const header = buf.toString('latin1', 10, 10 + hlen)
    const meta = JSON.parse(readFileSync(join(replayShard, 'replay_meta.json'), 'utf8')) as {
      nSamples: number
    }
    expect(header).toContain("'fortran_order': True")
    const shape = (/'shape': \(([^)]*)\)/.exec(header)?.[1] ?? '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .map(Number)
    expect(shape).toEqual([meta.nSamples + 1, METRICS_COLUMN_NAMES.length])
    const rows = shape[0]!
    const at = (i: number, j: number): number => buf.readFloatLE(10 + hlen + 4 * (i + j * rows))
    const manifest = JSON.parse(readFileSync(join(replayShard, 'manifest.json'), 'utf8')) as {
      metricValues: Record<string, number>
    }
    const cols = Object.entries(manifest.metricValues)
    expect(cols.length).toBe(METRICS_COLUMN_NAMES.length)
    for (const [name, v] of cols) {
      const j = METRICS_COLUMN_NAMES.indexOf(name)
      expect(j).toBeGreaterThanOrEqual(0)
      // f4 存储 vs manifest 的 JS number（终局行 = metrics 最后一行）。
      expect(Math.abs(at(rows - 1, j) - v)).toBeLessThan(1e-6)
    }
  })

  it('--code-sha 透传：codeSha 逐字符等于 launcher 给的值（导出器跳过整树遍历）', () => {
    const out = join(root, 'codesha')
    const sha = 'ab'.repeat(32)
    runRollout(out, ['--corpus-fmt', 'replay', '--code-sha', sha])
    const meta = JSON.parse(readFileSync(join(out, SHARD, 'replay_meta.json'), 'utf8')) as {
      codeSha: string
    }
    expect(meta.codeSha).toBe(sha)
  }, 60_000)

  it('--code-sha 非法 ⇒ 响亮失败（绝不写半截身份）', () => {
    const p = Bun.spawnSync(
      [
        'bun',
        'tools/sim/export-rl-rollout.ts',
        '--out',
        join(root, 'bad-sha'),
        '--stages',
        '0',
        '--seeds',
        '1',
        '--weights',
        WEIGHTS,
        '--lives-override',
        '3',
        '--corpus-fmt',
        'replay',
        '--code-sha',
        'NOT-HEX',
      ],
      { cwd: process.cwd(), stdout: 'pipe', stderr: 'pipe' },
    )
    expect(p.exitCode).not.toBe(0)
    expect(p.stderr?.toString() ?? '').toContain('--code-sha')
  }, 60_000)

  it('--pack-memory：replay 走内存容器，不创建 shard 目录（零落盘 dispatch 腿）', () => {
    const out = join(root, 'mem')
    const pack = join(root, 'mem.bcv2')
    runRollout(out, ['--corpus-fmt', 'replay', '--pack-memory', '--pack', pack])
    expect(existsSync(join(out, SHARD))).toBe(false)
    const { manifest, entries } = unpackContainer(readFileSync(pack))
    expect(manifest.corpus_fmt).toBe('replay')
    expect(entries.has('replay.json')).toBe(true)
    expect(entries.has('replay_meta.json')).toBe(true)
    expect(entries.has('obs.npy')).toBe(false)
    expect(entries.has('scalars.npy')).toBe(false)
    expect(entries.has('extra.npy')).toBe(false)
    // 磁盘腿与内存腿共用同一份序列化字节（同一游戏、同一 seed）。
    expect(entries.get('replay.json')!.equals(readFileSync(join(replayShard, 'replay.json')))).toBe(
      true,
    )
    const memMeta = JSON.parse(entries.get('replay_meta.json')!.toString('utf8')) as Record<
      string,
      unknown
    >
    const diskMeta = JSON.parse(
      readFileSync(join(replayShard, 'replay_meta.json'), 'utf8'),
    ) as Record<string, unknown>
    delete memMeta.recordMs
    delete diskMeta.recordMs
    expect(memMeta).toEqual(diskMeta)
  }, 60_000)

  it('hybrid opening 接管段：policy_mask 与 replay identity 均成立', async () => {
    const level = Bun.JSONC.parse(await Bun.file(LEVEL).text()) as { stages: unknown[] }
    const stageJson = JSON.stringify(level.stages[20])
    const out = join(root, 'opening')
    const base = ['--stage-json', stageJson, '--opening-connect', '--max-ticks', '2000']
    runRollout(join(out, 'full'), base, '2020')
    runRollout(join(out, 'replay'), [...base, '--corpus-fmt', 'replay'], '2020')
    const fullShardO = join(out, 'full', 'rl_s2020_seed1')
    const replayShardO = join(out, 'replay', 'rl_s2020_seed1')

    // policy_mask 属于标签，两条路径必须逐字节相同，且确有 controller 接管段（0）。
    expect(existsSync(join(replayShardO, 'policy_mask.npy'))).toBe(true)
    expect(
      sameBytes(join(fullShardO, 'policy_mask.npy'), join(replayShardO, 'policy_mask.npy')),
    ).toBe(true)
    const pm = readFileSync(join(replayShardO, 'policy_mask.npy'))
    expect([...pm].includes(0)).toBe(true)

    const conv = join(out, 'converted')
    const p = runConvert(replayShardO, conv)
    expect(p.exitCode).toBe(0)
    for (const name of ['obs.npy', 'scalars.npy', 'extra.npy']) {
      expect(sameBytes(join(fullShardO, name), join(conv, name))).toBe(true)
    }

    const meta = JSON.parse(readFileSync(join(replayShardO, 'replay_meta.json'), 'utf8')) as {
      codeSha: string
      opening: { wanted: boolean; done: boolean; doneTick: number | null; phase: string | null }
    }
    expect(meta.codeSha).toMatch(/^[0-9a-f]{16,64}$/)
    expect(meta.opening.wanted).toBe(true)
    expect(meta.opening.done).toBe(true)
    expect(meta.opening.doneTick).toBeGreaterThanOrEqual(0)
    expect(meta.opening.phase).toBe('done')
  }, 60_000)

  it('坏 replay_meta 响亮失败且不写半成品', () => {
    const bad = join(root, 'bad')
    cpSync(replayShard, bad, { recursive: true })
    const metaPath = join(bad, 'replay_meta.json')
    const meta = JSON.parse(readFileSync(metaPath, 'utf8')) as { decisionTicksList: number[] }
    meta.decisionTicksList[1] = meta.decisionTicksList[0] // 重复 tick
    writeFileSync(metaPath, JSON.stringify(meta))

    const out = join(root, 'bad-out')
    const p = runConvert(bad, out)
    expect(p.exitCode).not.toBe(0)
    expect(p.stderr?.toString() ?? '').toContain('not strictly increasing')
    expect(existsSync(join(out, 'obs.npy'))).toBe(false)
  }, 60_000)

  it('obsHash 不符 ⇒ 响亮失败', () => {
    const bad = join(root, 'bad-hash')
    cpSync(replayShard, bad, { recursive: true })
    const metaPath = join(bad, 'replay_meta.json')
    const meta = JSON.parse(readFileSync(metaPath, 'utf8')) as { obsHash: string }
    meta.obsHash = 'sha256:' + '0'.repeat(64)
    writeFileSync(metaPath, JSON.stringify(meta))

    const p = runConvert(bad, join(root, 'bad-hash-out'))
    expect(p.exitCode).not.toBe(0)
    expect(p.stderr?.toString() ?? '').toContain('obsHash mismatch')
  }, 60_000)

  it('缺 replay.json ⇒ 响亮失败', () => {
    const bad = join(root, 'bad-missing')
    cpSync(replayShard, bad, { recursive: true })
    rmSync(join(bad, 'replay.json'))

    const p = runConvert(bad, join(root, 'bad-missing-out'))
    expect(p.exitCode).not.toBe(0)
    expect(p.stderr?.toString() ?? '').toContain('missing')
  }, 60_000)
})
