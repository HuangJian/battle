import { describe, it, expect } from 'bun:test'
import { World } from '../../src/game/World'
import {
  makeArena,
  makeMazeStage,
  stratifiedSampleKinds,
  stageLayoutHash,
  ARENA_LADDER,
  ARENA_ID_BASE,
  isArenaId,
  resolveArenaStage,
  arenaLevelOfId,
} from '../../src/nn/arena-ladder'
import { GRID, BASE_POS } from '../../src/constants'
import { STAGES } from '../../src/config/stages'
import { ObsEncoder, CH } from '../../src/nn/obs-encoder'
import type { StageData } from '../../src/types'

// ============================================================
// arena-ladder（plan/goal-nn-action.md 卡 A0a）
// 课程学习玩具竞技场：layoutSeed 布局变异 / maze 敌种分层抽样 /
// 编号命名空间 1000+n / obs 幻影基地修复的端到端验证。
// ============================================================

describe('arena-ladder: 默认布局逐字节一致（A0a 验收③）', () => {
  // 冻结基线：与 HEAD 版生成器（改造前）实测逐字节一致的布局散列
  // （tmp/layout-parity.ts 对拍输出，2026-08-29）。
  const FROZEN: Array<[string, string]> = [
    ['arena-12-1', '15e64f3f'],
    ['arena-14-3', 'b0821a8b'],
    ['arena-20-20', '2654bf7f'],
    ['maze-nobase', '4ded40c5'],
    ['maze-base', '4d537c51'],
  ]
  for (const [name, hash] of FROZEN) {
    it(`${name} hash == ${hash}`, () => {
      const stage =
        name === 'maze-nobase'
          ? makeMazeStage({ base: false })
          : name === 'maze-base'
            ? makeMazeStage({ base: true })
            : makeArena({
                size: parseInt(name.split('-')[1], 10),
                enemyCount: parseInt(name.split('-')[2], 10),
              })
      expect(stageLayoutHash(stage)).toBe(hash)
    })
  }
})

describe('arena-ladder: layoutSeed 布局变异（§2.3a）', () => {
  it('3 个变体互不相同且逐字节可复现', () => {
    const hashes = [11, 23, 37].map((s) =>
      stageLayoutHash(makeArena({ size: 12, enemyCount: 1, layoutSeed: s })),
    )
    expect(new Set(hashes).size).toBe(3)
    // 确定性：同 seed 重跑一致
    expect(stageLayoutHash(makeArena({ size: 12, enemyCount: 1, layoutSeed: 23 }))).toBe(hashes[1])
  })

  it('变体不改变 size/enemyCount/base（grid 形状与敌队不变）', () => {
    const base = makeArena({ size: 14, enemyCount: 3, enemyKinds: ['basic', 'fast'] })
    for (const seed of [11, 23, 37]) {
      const v = makeArena({
        size: 14,
        enemyCount: 3,
        enemyKinds: ['basic', 'fast'],
        layoutSeed: seed,
      })
      expect(v.tiles.length).toBe(GRID)
      expect(v.enemyCount).toBe(3)
      expect(v.enemies).toEqual(base.enemies)
      expect(v.tiles.map((r) => r.replace(/s/g, ''))).toEqual(
        base.tiles.map((r) => r.replace(/s/g, '')),
      )
    }
  })

  it('扰动后的 spawn 都落在开放区内且不在 base 足印上', () => {
    const stage = makeArena({ size: 12, enemyCount: 1, base: false, layoutSeed: 11 })
    const offset = Math.floor((GRID - 12) / 2)
    const inOpen = (p: { col: number; row: number }): boolean =>
      p.col >= offset && p.col < offset + 12 && p.row >= offset && p.row < offset + 12
    expect(inOpen(stage.playerSpawn!)).toBe(true)
    for (const e of stage.enemySpawns!) expect(inOpen(e)).toBe(true)
    // base=true 变体：玩家/敌人 spawn 不占 base 足印
    const bstage = makeArena({ size: 14, enemyCount: 3, base: true, layoutSeed: 23 })
    const bCol = BASE_POS.col
    const bRow = BASE_POS.row
    const notOnBase = (p: { col: number; row: number }): boolean =>
      !(p.col >= bCol - 1 && p.col <= bCol + 2 && p.row >= bRow - 1 && p.row <= bRow + 1)
    expect(notOnBase(bstage.playerSpawn!)).toBe(true)
    for (const e of bstage.enemySpawns!) expect(notOnBase(e)).toBe(true)
  })

  it('显式 spawn 覆盖优先于 layoutSeed 扰动', () => {
    const stage = makeArena({
      size: 12,
      enemyCount: 1,
      playerSpawn: { col: 10, row: 15 },
      enemySpawns: [{ col: 8, row: 8 }],
      layoutSeed: 11,
    })
    expect(stage.playerSpawn).toEqual({ col: 10, row: 15 })
    expect(stage.enemySpawns).toEqual([{ col: 8, row: 8 }])
  })
})

describe('arena-ladder: maze 变体与敌种分层抽样（§2.3b）', () => {
  it('stratifiedSampleKinds 保住队尾 fast（截断会把它们切光）', () => {
    const queue = STAGES[0].enemies // ENEMY_FORCES[0]: 18 basic + 2 fast（fast 在队尾）
    expect(queue.filter((k) => k === 'fast').length).toBe(2)
    for (const n of [6, 8, 10, 15]) {
      const sampled = stratifiedSampleKinds(queue, n)
      expect(sampled.length).toBe(n)
      // 分层配额（floor + 最大余数）：fast 的占比 × n ≥ 1 ⇒ 队尾 fast 不会被切光
      // （余数并列时首现的 basic 优先——n=15 是 tie 案，fast=1 仍是合法配额）。
      expect(sampled.filter((k) => k === 'fast').length).toBeGreaterThanOrEqual(1)
      expect(sampled.filter((k) => k === 'fast').length).toBeLessThanOrEqual(2)
      // 原队列相对顺序保持
      let qi = 0
      for (const k of sampled) {
        while (queue[qi] !== k) qi++
        qi++
      }
    }
  })

  it('makeMazeStage enemyCount 覆盖：spawn 队列 = 覆盖值且敌种队列同步替换', () => {
    const stage = makeMazeStage({ base: false, enemyCount: 8 })
    expect(stage.enemyCount).toBe(8)
    expect(stage.enemies!.length).toBe(8) // 敌种队列也换成 8（否则 World 取前 8 = 队首截断）
    expect(stage.enemies!.filter((k) => k === 'fast').length).toBeGreaterThanOrEqual(1)
    const world = new World()
    world.loadStageData(stage, 0)
    expect(world.spawnQueue.length).toBe(8)
    expect(world.enemiesTotal).toBe(8)
  })

  it('makeMazeStage layoutSeed：carve-only 扰动（不新增墙、不碰保护区）', () => {
    const base0 = makeMazeStage({ base: false })
    const v = makeMazeStage({ base: false, layoutSeed: 11 })
    // carve-only：变体的墙/钢不多于基线
    const solid = (s: StageData): number =>
      s.tiles
        .join('')
        .split('')
        .filter((c) => c === 'b' || c === 's').length
    expect(solid(v)).toBeLessThanOrEqual(solid(base0))
    // 确定性
    expect(stageLayoutHash(makeMazeStage({ base: false, layoutSeed: 11 }))).toBe(stageLayoutHash(v))
    // 变异间互异（3 张不同布局）
    const hashes = [11, 23, 37].map((s) =>
      stageLayoutHash(makeMazeStage({ base: false, layoutSeed: s })),
    )
    expect(new Set(hashes).size).toBe(3)
  })
})

describe('arena-ladder: 编号命名空间（卡 A1）', () => {
  it('arena 编号 ∩ 真实 stage 下标 = ∅', () => {
    const ids = [...ARENA_LADDER.keys()]
    expect(ids.length).toBe(18) // 6 级 × 3 变体（写死 3，§2.3a）
    for (const id of ids) {
      expect(id).toBeGreaterThanOrEqual(ARENA_ID_BASE)
      expect(isArenaId(id)).toBe(true)
    }
    for (let s = 0; s < STAGES.length; s++) expect(isArenaId(s)).toBe(false)
  })

  it('resolveArenaStage 全表可解析、级标签正确', () => {
    const levels = new Set<string>()
    for (const [id, spec] of ARENA_LADDER) {
      const stage = resolveArenaStage(id)
      expect(stage).not.toBeNull()
      expect(stage!.tiles.length).toBe(GRID)
      expect(arenaLevelOfId(id)).toBe(spec.level)
      levels.add(spec.level)
    }
    expect(levels).toEqual(new Set(['S1', 'S2', 'S3', 'S3H', 'S4a', 'S-Dodge']))
    expect(resolveArenaStage(34)).toBeNull() // 真实 stage 不经 arena 解析
    expect(arenaLevelOfId(34)).toBeNull()
  })

  it('S1/S2 是开放场（无 base），S4a 是有基地 maze', () => {
    const s1 = resolveArenaStage(ARENA_ID_BASE)!
    const world1 = new World()
    world1.loadStageData(s1, 0)
    expect(world1.tileMap.hasBase()).toBe(false)
    const s4a = resolveArenaStage(ARENA_ID_BASE + 40)!
    const world4 = new World()
    world4.loadStageData(s4a, 0)
    expect(world4.tileMap.hasBase()).toBe(true)
  })

  it('S-Dodge 规格验证（plan/dodge-item-curriculum.md §1）', () => {
    const ids = [...ARENA_LADDER.keys()].filter((id) => arenaLevelOfId(id) === 'S-Dodge')
    expect(ids.length).toBe(3) // 3 布局变异
    for (const id of ids) {
      const stage = resolveArenaStage(id)!
      expect(stage.enemyCount).toBe(20)
      expect(stage.tiles.length).toBe(GRID)
      // 所有行长度一致
      expect(stage.tiles.every((row) => row.length === GRID)).toBe(true)
      // 无基地（E 字符）
      expect(stage.tiles.some((row) => row.includes('E'))).toBe(false)
      // 开放场：中心区域是空地（.），周围钢环（s）
      const offset = Math.floor((GRID - 20) / 2)
      // 钢环外是钢
      expect(stage.tiles[offset - 1].slice(offset, offset + 20)).toBe('s'.repeat(20))
      // 钢环内是空地
      expect(stage.tiles[offset + 1][offset + 1]).toBe('.')
      // 敌种频率：basic 占 40%（5 元循环 basic×2）
      const kinds = stage.enemies.slice(0, 20)
      const basicCount = kinds.filter((k) => k === 'basic').length
      expect(basicCount).toBe(8)
      // 敌种数 = 20
      expect(stage.enemies.length).toBe(20)
    }
  })
})

describe('obs-encoder: 幻影基地修复（A0a 验收②）', () => {
  /** 在真实 World 上编码并返回 ch5 占用与 base 标量。 */
  function encodeOf(stage: StageData): { ch5: number[]; s: Float32Array } {
    const world = new World()
    world.rng.reseed(42)
    world.loadStageData(stage, 0)
    const enc = new ObsEncoder()
    enc.encode(world)
    const ch5: number[] = []
    const board = enc.obs.slice(CH.base * GRID * GRID, (CH.base + 1) * GRID * GRID)
    for (let i = 0; i < board.length; i++) if (board[i] !== 0) ch5.push(board[i])
    return { ch5, s: enc.scalars.slice() }
  }

  it('无基地场：ch5 全 0 且 s1/s6/s17/s18 全 0', () => {
    for (const stage of [makeArena({ size: 12, enemyCount: 1 }), makeMazeStage({ base: false })]) {
      const { ch5, s } = encodeOf(stage)
      expect(ch5).toEqual([])
      expect(s[1]).toBe(0)
      expect(s[6]).toBe(0)
      expect(s[17]).toBe(0)
      expect(s[18]).toBe(0)
    }
  })

  it('有基地场：ch5 鹰=2 / ring 有值，s6>0（行为不变）', () => {
    const { ch5, s } = encodeOf(makeMazeStage({ base: true }))
    expect(ch5).toContain(2) // eagle alive
    expect(ch5.length).toBeGreaterThan(1) // ring cells lit
    expect(s[6]).toBeGreaterThan(0)
    // s17/s18：基地相对量回到旧公式口径（有限值）
    expect(Number.isFinite(s[17])).toBe(true)
  })
})
