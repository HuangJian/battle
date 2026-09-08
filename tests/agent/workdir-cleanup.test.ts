import { describe, expect, it } from 'bun:test'
import {
  STALE_ORPHAN_MS,
  WEIGHT_FILES_KEEP,
  WEIGHT_RE,
  staleOrphanPlan,
  sweepWeightFilePlan,
  type DirEntry,
} from '../../tools/agent/workdir-cleanup'

/** 16 位 hex sha 假名。 */
const sha = (n: number): string => n.toString(16).padStart(16, '0')

const weight = (kind: string, n: number, mtimeMs: number): DirEntry => ({
  name: `weights-${kind}-${sha(n)}.json`,
  mtimeMs,
})

describe('WEIGHT_RE（§374 回归：v3.7 kind 分桶命名）', () => {
  it('匹配 weights-<kind>-<16hex>.json（旧正则 /^weights-[0-9a-f]{16}\\.json$/ 匹配不到 → 清扫空操作）', () => {
    expect(WEIGHT_RE.test('weights-rollout-008498531e3ff4c9.json')).toBe(true)
    expect(WEIGHT_RE.test('weights-intent-0d6a5771ca262608.json')).toBe(true)
    expect(WEIGHT_RE.test('weights-goal-9980da2178141576.json')).toBe(true)
    // 旧命名（无 kind 段）不再产生，也不应被新正则误匹配
    expect(WEIGHT_RE.test('weights-008498531e3ff4c9.json')).toBe(false)
  })
})

describe('sweepWeightFilePlan', () => {
  it('kind 命名文件能匹配并按 mtime 收敛到最新 KEEP 份（回归：旧正则删 0 个）', () => {
    const entries = Array.from({ length: 10 }, (_, i) => weight('rollout', i, 1000 + i))
    const del = sweepWeightFilePlan(entries, new Set())
    expect(del).toHaveLength(10 - WEIGHT_FILES_KEEP)
    // 最新 KEEP 份（mtime 最大）必须保留
    const kept = new Set(entries.map((e) => e.name).filter((n) => !del.includes(n)))
    for (let i = 10 - WEIGHT_FILES_KEEP; i < 10; i++)
      expect(kept.has(`weights-rollout-${sha(i)}.json`)).toBe(true)
    // 最旧的一律删
    expect(del).toContain(`weights-rollout-${sha(0)}.json`)
    expect(del).toContain(`weights-rollout-${sha(5)}.json`)
  })

  it('每 kind 独立保留 KEEP 份（rollout + intent 并存各留 4）', () => {
    const entries = [
      ...Array.from({ length: 6 }, (_, i) => weight('rollout', i, 1000 + i)),
      ...Array.from({ length: 6 }, (_, i) => weight('intent', i, 2000 + i)),
    ]
    const del = sweepWeightFilePlan(entries, new Set())
    expect(del).toHaveLength(2 * (6 - WEIGHT_FILES_KEEP))
    expect(del).not.toContain(`weights-intent-${sha(5)}.json`)
    expect(del).not.toContain(`weights-rollout-${sha(5)}.json`)
    expect(del).toContain(`weights-intent-${sha(0)}.json`)
    expect(del).toContain(`weights-rollout-${sha(0)}.json`)
  })

  it('在飞权重桶引用的文件永不删（即使超出 keep）', () => {
    const entries = Array.from({ length: 6 }, (_, i) => weight('rollout', i, 1000 + i))
    const live = new Set([`weights-rollout-${sha(0)}.json`]) // 最旧但仍在飞
    const del = sweepWeightFilePlan(entries, live)
    expect(del).not.toContain(`weights-rollout-${sha(0)}.json`)
    expect(del).toContain(`weights-rollout-${sha(1)}.json`)
  })

  it('非权重文件与坏命名不动', () => {
    const entries: DirEntry[] = [
      weight('rollout', 1, 1000),
      { name: 'agent.pid', mtimeMs: 1 },
      { name: 'game-9756-1', mtimeMs: 1 },
      { name: 'node-bundle', mtimeMs: 1 },
      { name: 'weights-rollout-xyz.json', mtimeMs: 1 },
    ]
    expect(sweepWeightFilePlan(entries, new Set())).toEqual([])
  })
})

describe('staleOrphanPlan', () => {
  const now = 1_000_000

  it('只删早于年龄门的 game-* 与 pid 文件', () => {
    const entries: DirEntry[] = [
      { name: 'game-9756-1', mtimeMs: now - 60 * 60_000 }, // 1h 前：被杀进程残留
      { name: 'game-1234-7', mtimeMs: now - 2_000 }, // 2s 前：父进程在飞局，须保留
      { name: 'agent.pid', mtimeMs: now - 24 * 60 * 60_000 }, // 陈旧
      { name: 'agent-child.pid', mtimeMs: now - 1_000 }, // 刚写（restart 交接），保留
      { name: 'node-bundle', mtimeMs: 1 },
      { name: 'weights-rollout-008498531e3ff4c9.json', mtimeMs: 1 },
    ]
    const { games, pids } = staleOrphanPlan(entries, now)
    expect(games).toEqual(['game-9756-1'])
    expect(pids).toEqual(['agent.pid'])
  })

  it('now 推进过年龄门后才删除（年龄门 = STALE_ORPHAN_MS）', () => {
    const entries: DirEntry[] = [
      { name: 'game-9756-1', mtimeMs: now - STALE_ORPHAN_MS - 1 },
      { name: 'game-9756-2', mtimeMs: now - STALE_ORPHAN_MS + 1 },
    ]
    const { games } = staleOrphanPlan(entries, now)
    expect(games).toEqual(['game-9756-1'])
  })
})
