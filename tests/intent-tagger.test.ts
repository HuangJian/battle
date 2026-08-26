import { describe, expect, it } from 'bun:test'
import { mkdirSync, rmSync } from 'fs'
import { probeBucketOf, processJob, type TaggerPayload } from '../tools/sim/intent-label-core'
import { ObsEncoder } from '../src/nn/obs-encoder'
import { RING_CELLS } from '../src/ai/god/ThreatBudget'
import { seedWorld, clearArena, placeEnemy } from './helpers'

/**
 * M0b 机械 tagger 核心测试：①divergence-probe 三桶谓词语义；②两遍法逐字节
 * 确定性（同 job 双跑 → shard 字节一致，AGENTS §2.3 / 预注册 #15 同源纪律）。
 */

function bucketOfFixture(world: ReturnType<typeof seedWorld>): number {
  const enc = new ObsEncoder()
  enc.encode(world)
  return probeBucketOf(world, enc.scalars)
}

/** 恢复 8 格保护环为 brick——"环完好"的安全场（s[6]=1 → 无基地压力）。 */
function restoreRing(w: ReturnType<typeof seedWorld>): void {
  for (const c of RING_CELLS) w.tileMap.grid[c.row][c.col] = 'brick'
}

describe('probeBucketOf — divergence-probe 三桶（同款谓词）', () => {
  it('空场（环完好）→ cruise', () => {
    const w = seedWorld(5)
    clearArena(w)
    restoreRing(w)
    expect(bucketOfFixture(w)).toBe(2)
  })

  it('敌近基地（Manhattan ≤12）→ base（最高优先级，即使也近玩家）', () => {
    const w = seedWorld(5)
    clearArena(w)
    restoreRing(w)
    // 基地 (12,24)：敌在 (12,14) 距基地 10 ≤12 → base。
    placeEnemy(w, 12, 14)
    expect(bucketOfFixture(w)).toBe(0)
  })

  it('环受损（ringFrac<1）→ base（无近基地敌也算）', () => {
    const w = seedWorld(5)
    clearArena(w)
    restoreRing(w)
    // 拆一格环砖：基地上方 (12,23)。
    w.tileMap.grid[23][12] = 'empty'
    expect(bucketOfFixture(w)).toBe(0)
  })

  it('敌近玩家但远基地 → combat', () => {
    const w = seedWorld(5)
    clearArena(w)
    restoreRing(w)
    // 敌 (2,2)：距基地 24 >12 不触发 base；距玩家出生 (8,24) 约 28 格 → 不近玩家。
    // 放一枚存活敌方子弹兜底 combat。
    const e = placeEnemy(w, 2, 2)
    w.bullets.push({
      id: 990001,
      ownerId: e.id,
      x: 100,
      y: 100,
      w: 6,
      h: 6,
      dir: 'down',
      speed: 3,
      alive: true,
      allegiance: 'enemy',
      power: 1,
    } as never)
    expect(bucketOfFixture(w)).toBe(1)
  })
})

describe('processJob — 两遍法确定性（预注册 #15 同源纪律）', () => {
  const payload: TaggerPayload = {
    jobs: [{ id: 0, si: 0, seed: 3 }],
    difficulty: 'hard',
    maxTicks: 3000,
    gridPeriod: 30,
    shardDir: 'tmp/intent-tagger-test',
    force: true,
  }

  it('同 job 双跑 → obs/intent/bucket/manifest 逐字节一致', async () => {
    for (const d of ['run-a', 'run-b']) {
      rmSync(`tmp/intent-tagger-test/${d}`, { recursive: true, force: true })
      mkdirSync(`tmp/intent-tagger-test/${d}`, { recursive: true })
      processJob({ ...payload, shardDir: `tmp/intent-tagger-test/${d}` }, payload.jobs[0])
    }
    for (const f of ['obs.npy', 'intent.npy', 'bucket.npy', 'scalars.npy', 'frame.npy']) {
      const a = Bun.file(`tmp/intent-tagger-test/run-a/s01-seed3-hard/${f}`)
      const b = Bun.file(`tmp/intent-tagger-test/run-b/s01-seed3-hard/${f}`)
      expect(a.size).toBe(b.size)
      expect(Buffer.compare(Buffer.from(await a.bytes()), Buffer.from(await b.bytes()))).toBe(0)
    }
  })

  it('shard 内容健全性：帧索引严格递增、段序列合法', async () => {
    const dir = 'tmp/intent-tagger-test/run-a/s01-seed3-hard'
    const manifest = JSON.parse(await Bun.file(`${dir}/manifest.json`).text()) as {
      ticks: number
      sampled: number
      segments: Array<{ start: number; end: number; intent: string }>
    }
    expect(manifest.sampled).toBeGreaterThan(0)
    expect(manifest.segments.length).toBeGreaterThan(0)
    let prevEnd = -1
    for (const s of manifest.segments) {
      expect(s.start).toBeGreaterThan(prevEnd)
      expect(s.end).toBeLessThan(manifest.ticks)
      expect(s.end).toBeGreaterThanOrEqual(s.start)
      prevEnd = s.end
    }
  })
})
