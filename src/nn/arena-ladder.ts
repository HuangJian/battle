/**
 * arena-ladder.ts — 课程学习玩具竞技场（plan/goal-nn-action.md §2 / 卡 A0a）。
 *
 * RL 课程训练的环境阶梯：S1 开火命中 → S2 闪避走位 → S3 砖墙+道具 → S4a 有基地
 * maze → S4b 真实关卡。本模块是 arena 布局的**唯一权威源**（curriculum.ts re-export
 * 兼容旧消费方），刻意放在 `src/nn/` 下：分布式 rollout 的 codeHash 只覆盖
 * `src/nn/**` + 两个 rollout 导出器（dist_common.py / sampler-agent.ts 双语契约），
 * arena 定义进这个集合才能保证 dist 节点代码同步（plan 卡 A1 步骤 4）。
 *
 * 硬纪律（plan §0.2 / §2.3）：
 *   * 纯数据生成器——不触引擎代码、无 World 依赖；同一参数逐字节一致。
 *   * `layoutSeed` 缺省时输出与改造前逐字节一致（A0a 验收 ③）。
 *   * 每级 3 张布局变异（写死 3，不许中途加，§2.3a）——arena 无地图级随机性，
 *     只有 agent 级随机性，背图过门靠这 3 张变异 + P1 探针免疫。
 *   * arena 编号命名空间 1000+n，与真实 stage 下标 0..34 不相交（卡 A1）；
 *     该整数原样流经 course.py → run_rl.py → queue.py → sampler-agent →
 *     导出器解析层，六环节只在导出器解析层落地。
 */

import { GRID } from '../constants'
import type { StageData, TankKind } from '../types'
import { STAGES } from '../config/stages'

// ============================================================
// PRNG（模块内私有 mulberry32——布局生成是纯函数，与 world.rng 无关）
// ============================================================

function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// ============================================================
// makeArena —— 开放竞技场（钢环 + size×size 空地）
// ============================================================

export interface ArenaOpts {
  /** Edge length (in sub-blocks) of the open area inside the steel ring. */
  size: number
  /** Whether to include a 2×2 base at the bottom-center of the arena. */
  base?: boolean
  /** Total enemy count for this stage. */
  enemyCount: number
  /** Enemy kind queue (cycled if shorter than enemyCount). */
  enemyKinds?: TankKind[]
  /** Player spawn in sub-block coords (default: bottom-center of arena). */
  playerSpawn?: { col: number; row: number }
  /** Enemy spawns in sub-block coords (default: spread across top of arena). */
  enemySpawns?: { col: number; row: number }[]
  /**
   * 布局变异种子（§2.3a）：扰动默认 playerSpawn / enemySpawns（显式传入的
   * spawn 覆盖优先于扰动）。缺省 ⇒ 默认布局，与改造前逐字节一致。
   */
  layoutSeed?: number
}

/**
 * Programatically generate a 26×26 open arena surrounded by steel walls.
 *
 * The open area is `size × size` sub-blocks, centered in the 26×26 grid. A
 * 1-cell-thick steel ring encloses it. If `base` is true, a 2×2 base is placed
 * at the bottom-center of the arena. Spawn points default to inside the open
 * area: player at bottom-center, enemies spread across the top.
 *
 * This is a pure data generator — no engine code is touched. The returned
 * `StageData` feeds directly into `runSimulation` / `World.loadStageData`.
 */
export function makeArena(opts: ArenaOpts): StageData {
  const { size, base = false, enemyCount, enemyKinds = ['basic'] as TankKind[] } = opts
  const offset = Math.floor((GRID - size) / 2)

  // Build 26×26 grid: steel ring + open interior.
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let line = ''
    for (let c = 0; c < GRID; c++) {
      const isRing =
        r === offset - 1 || r === offset + size || c === offset - 1 || c === offset + size
      const inOpen = r >= offset && r < offset + size && c >= offset && c < offset + size
      if (isRing) {
        line += 's'
      } else if (inOpen) {
        // Base at bottom-center (2×2), only if requested.
        const baseCol = offset + Math.floor(size / 2) - 1
        const baseRow = offset + size - 2
        if (base && c >= baseCol && c <= baseCol + 1 && r >= baseRow && r <= baseRow + 1) {
          line += 'E'
        } else {
          line += '.'
        }
      } else {
        // Outside the ring — fill with steel to prevent escape.
        line += 's'
      }
    }
    tiles.push(line)
  }

  // Default spawn points inside the open area.
  const centerCol = offset + Math.floor(size / 2)
  const playerSpawn = opts.playerSpawn ?? {
    col: centerCol,
    row: offset + size - 2,
  }
  const enemySpawns = opts.enemySpawns ?? [
    { col: offset + 1, row: offset },
    { col: centerCol, row: offset },
    { col: offset + size - 2, row: offset },
  ]

  // layoutSeed 扰动（§2.3a）：只动"默认"spawn——显式覆盖优先（见上）。
  if (opts.layoutSeed !== undefined && opts.playerSpawn === undefined) {
    const rng = mulberry32(opts.layoutSeed * 0x9e3779b1 + size * 97 + enemyCount * 31)
    const baseCol = offset + Math.floor(size / 2) - 1
    const baseRow = offset + size - 2
    const onBase = (c: number, r: number): boolean =>
      base && c >= baseCol && c <= baseCol + 1 && r >= baseRow && r <= baseRow + 1
    // 玩家：下半场（与默认 spawn 同带），避开 base 足印。
    const pRowLo = offset + Math.floor(size / 2)
    const pRowHi = offset + size - 2
    for (let tries = 0; tries < 64; tries++) {
      const c = offset + Math.floor(rng() * size)
      const r = pRowLo + Math.floor(rng() * (pRowHi - pRowLo + 1))
      if (!onBase(c, r)) {
        playerSpawn.col = c
        playerSpawn.row = r
        break
      }
    }
    // 敌人：上半场 3 点，点间距 ≥3、离玩家 ≥4（曼哈顿，格坐标）。
    const eRowHi = offset + Math.floor(size / 2) - 1
    const pts: { col: number; row: number }[] = []
    for (let i = 0; i < 3; i++) {
      for (let tries = 0; tries < 64; tries++) {
        const c = offset + Math.floor(rng() * size)
        const r = offset + Math.floor(rng() * (eRowHi - offset + 1))
        const ok =
          !onBase(c, r) &&
          Math.abs(c - playerSpawn.col) + Math.abs(r - playerSpawn.row) >= 4 &&
          pts.every((p) => Math.abs(c - p.col) + Math.abs(r - p.row) >= 3)
        if (ok) {
          pts.push({ col: c, row: r })
          break
        }
      }
      if (pts.length <= i) {
        pts.push({ col: offset + 1 + i * 2, row: offset }) // 确定性兜底（极少触发）
      }
    }
    if (opts.enemySpawns === undefined) {
      enemySpawns!.length = 0
      enemySpawns!.push(...pts)
    }
  }

  // Build enemy queue (cycled if shorter).
  const enemies: TankKind[] = []
  for (let i = 0; i < enemyCount; i++) {
    enemies.push(enemyKinds[i % enemyKinds.length])
  }

  return {
    id: -1,
    name: `arena-${size}x${size}-${enemyCount}enemies${base ? '-base' : ''}`,
    tiles,
    enemies,
    enemyCount,
    playerSpawn,
    enemySpawns,
  }
}

// ============================================================
// makeMazeStage —— 真实关卡 0 的 maze 变体（S3 / S4a）
// ============================================================

/**
 * 敌种分层抽样（§2.3b）：按各 kind 在原队列中的占比配额（floor + 最大余数），
 * 再按原队列顺序保留——**绝不能截断队列前 N 个**（ENEMY_FORCES[0] 的 2 个 fast
 * 在队尾，截断会把它们切光，S3 退化成纯 basic）。
 */
export function stratifiedSampleKinds(queue: TankKind[], n: number): TankKind[] {
  if (n >= queue.length) return [...queue]
  const counts = new Map<TankKind, number>()
  for (const k of queue) counts.set(k, (counts.get(k) ?? 0) + 1)
  const entries = [...counts.entries()] // 首现序，稳定
  const total = queue.length
  const quotas = entries.map(([k, c]) => {
    const exact = (c / total) * n
    return { k, q: Math.floor(exact), frac: exact - Math.floor(exact) }
  })
  let assigned = 0
  for (const e of quotas) assigned += e.q
  // 最大余数法补齐（余数并列取先首现的 kind——确定性）。
  const order = [...quotas].sort((a, b) => b.frac - a.frac)
  for (let i = 0; assigned < n; i++) {
    order[i % order.length].q++
    assigned++
  }
  const left = new Map<TankKind, number>(quotas.map((e) => [e.k, e.q]))
  const out: TankKind[] = []
  for (const k of queue) {
    const rem = left.get(k) ?? 0
    if (rem > 0) {
      out.push(k)
      left.set(k, rem - 1)
    }
  }
  return out
}

export interface MazeOpts {
  /** Whether to keep the 2×2 base (default false = strip to open floor). */
  base?: boolean
  /** 敌人总数覆盖位（缺省 = STAGES[0] 全套 20）；分层抽样自原队列（§2.3b）。 */
  enemyCount?: number
  /** 布局变异种子：扰动 spawns（限 '.' 空格）+ 少量 carve-only 地形扰动。 */
  layoutSeed?: number
}

/**
 * Generate a 26×26 maze stage (plan §4 stage 4/5). Uses the real stage 0
 * layout but optionally strips the base. This tests `directMove` wall-breaking
 * and navigation in a realistic brick-maze environment.
 *
 * layoutSeed 扰动（A0a）：① spawns 落到随机 '.' 空格（玩家下半场 / 敌人上半场，
 * 点间距约束同 makeArena）；② 地形扰动只 **carve**（b→.，抽 6 格）——移除障碍
 * 永不破坏连通性，加墙则可能封死走廊（有基地变体还避开 base 环保护区）。
 */
export function makeMazeStage(opts: MazeOpts): StageData {
  const real = STAGES[0]
  const base = opts.base ?? false
  const tiles = real.tiles.map((line) => line.replace(/E/g, base ? 'E' : '.'))

  let playerSpawn = real.playerSpawn
  let enemySpawns = real.enemySpawns
  let enemies = real.enemies
  let enemyCount = real.enemyCount

  if (opts.enemyCount !== undefined) {
    // 敌种队列与计数必须同时覆盖：World.loadStageData 用 enemies[i % len] 取前
    // enemyCount 个——只改 enemyCount 不换队列 = 队首截断（§2.3b 明令禁止）。
    enemies = stratifiedSampleKinds(real.enemies, opts.enemyCount)
    enemyCount = opts.enemyCount
  }

  if (opts.layoutSeed !== undefined) {
    const rng = mulberry32(opts.layoutSeed * 0x85ebca77 + (base ? 7 : 3))
    // base 保护区（有基地变体）：ring + 鹰足印（行 22+ / 列 11-14）禁扰动。
    const protectedCell = (c: number, r: number): boolean => base && r >= 22 && c >= 11 && c <= 14
    const at = (c: number, r: number): string =>
      c < 0 || c >= GRID || r < 0 || r >= GRID ? 's' : tiles[r][c]
    // ① carve-only 地形扰动：抽 6 个砖格 → '.'（避开保护区）。
    const bricks: { c: number; r: number }[] = []
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        if (tiles[r][c] === 'b' && !protectedCell(c, r)) bricks.push({ c, r })
      }
    }
    const carved = new Set<number>()
    for (let i = 0; i < 6 && bricks.length > 0; i++) {
      const idx = Math.floor(rng() * bricks.length)
      const { c, r } = bricks[idx]
      const key = r * GRID + c
      if (!carved.has(key)) {
        carved.add(key)
        tiles[r] = tiles[r].slice(0, c) + '.' + tiles[r].slice(c + 1)
      }
    }
    // ② spawn 扰动：空 '.' 格采样（确定性；64 次尝试后兜底回原 spawn）。
    const pickSpawn = (
      rowLo: number,
      rowHi: number,
      taken: { col: number; row: number }[],
      minSep: number,
    ): { col: number; row: number } | null => {
      for (let tries = 0; tries < 64; tries++) {
        const c = 1 + Math.floor(rng() * (GRID - 2))
        const r = rowLo + Math.floor(rng() * (rowHi - rowLo + 1))
        if (at(c, r) !== '.' || protectedCell(c, r)) continue
        if (taken.every((p) => Math.abs(c - p.col) + Math.abs(r - p.row) >= minSep)) {
          return { col: c, row: r }
        }
      }
      return null
    }
    const bottom = pickSpawn(14, 24, [], 0)
    if (bottom) playerSpawn = bottom
    const pts: { col: number; row: number }[] = []
    for (let i = 0; i < 3; i++) {
      const p = pickSpawn(0, 10, [...pts, bottom ?? { col: -9, row: -9 }], i === 0 ? 4 : 3)
      if (p) pts.push(p)
    }
    if (pts.length === 3) enemySpawns = pts
  }

  return {
    ...real,
    id: -1,
    name: `maze-${base ? 'base' : 'nobase'}`,
    tiles,
    ...(opts.enemyCount !== undefined ? { enemies, enemyCount } : {}),
    ...(playerSpawn !== real.playerSpawn || enemySpawns !== real.enemySpawns
      ? { playerSpawn, enemySpawns }
      : {}),
  }
}

// ============================================================
// Arena 编号命名空间（卡 A1）：1000+n 与真实 stage 下标 0..34 不相交
// ============================================================

export const ARENA_ID_BASE = 1000

export function isArenaId(id: number): boolean {
  return Number.isInteger(id) && id >= ARENA_ID_BASE
}

export type ArenaLevel = 'S1' | 'S2' | 'S3' | 'S3H' | 'S4a' | 'S-Dodge'

export interface ArenaSpec {
  level: ArenaLevel
  variant: 0 | 1 | 2
  /** 该级的训练场构造参数（plan §2.1 表；写死在此，导出器只按 id 解析）。 */
  build: () => StageData
}

// 每级 3 张布局变异的种子（§2.3a 写死 3 个变体；A0 在同样 3 张上锚定）。
const LAYOUT_VARIANT_SEEDS = [11, 23, 37] as const

function arenaSpecs(level: ArenaLevel, levelIdx: number, build: (seed: number) => StageData) {
  return LAYOUT_VARIANT_SEEDS.map((seed, variant) => ({
    id: ARENA_ID_BASE + levelIdx * 10 + variant,
    spec: { level, variant: variant as 0 | 1 | 2, build: () => build(seed + levelIdx * 100) },
  }))
}

const LEVEL_SEED_BASE: Record<ArenaLevel, number> = {
  S1: 0,
  S2: 1,
  S3: 2,
  S3H: 3,
  S4a: 4,
  'S-Dodge': 5,
}

/** 训练/锚定共用 arena 阶梯（plan §2.1 五场 × 3 布局变异）。 */
export const ARENA_LADDER: Map<number, ArenaSpec> = new Map(
  [
    ...arenaSpecs('S1', LEVEL_SEED_BASE.S1, (s) =>
      makeArena({ size: 12, enemyCount: 1, layoutSeed: s }),
    ),
    ...arenaSpecs('S2', LEVEL_SEED_BASE.S2, (s) =>
      makeArena({ size: 14, enemyCount: 3, enemyKinds: ['basic', 'fast'], layoutSeed: s }),
    ),
    ...arenaSpecs('S3', LEVEL_SEED_BASE.S3, (s) =>
      makeMazeStage({ base: false, enemyCount: 8, layoutSeed: s }),
    ),
    // A0 备选场（§2.4）：减半敌人（分层抽样保 fast），锚退化时按预注册换场。
    ...arenaSpecs('S3H', LEVEL_SEED_BASE.S3H, (s) =>
      makeMazeStage({ base: false, enemyCount: 10, layoutSeed: s }),
    ),
    ...arenaSpecs('S4a', LEVEL_SEED_BASE.S4a, (s) => makeMazeStage({ base: true, layoutSeed: s })),
    ...arenaSpecs('S-Dodge', LEVEL_SEED_BASE['S-Dodge'], (s) =>
      makeArena({
        size: 20,
        enemyCount: 20,
        enemyKinds: ['basic', 'basic', 'fast', 'power', 'armor'],
        layoutSeed: s,
      }),
    ),
  ].map(({ id, spec }) => [id, spec] as [number, ArenaSpec]),
)

/** 解析 arena 编号 → StageData；非 arena 编号返回 null（调用方回落真实关卡表）。 */
export function resolveArenaStage(id: number): StageData | null {
  const spec = ARENA_LADDER.get(id)
  return spec ? spec.build() : null
}

/** arena 编号 → 课程级（奖励 scheme / 报告聚合用）；非 arena 编号返回 null。 */
export function arenaLevelOfId(id: number): ArenaLevel | null {
  return ARENA_LADDER.get(id)?.level ?? null
}

// ============================================================
// 布局身份散列（A0 锚定报告 / A1 训练链一致性验收共用）
// ============================================================

/** FNV-1a over tiles + spawns + 敌种队列——arena 布局的紧凑身份。 */
export function stageLayoutHash(stage: StageData): string {
  let h = 0x811c9dc5
  const feed = (s: string): void => {
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i)
      h = Math.imul(h, 0x01000193)
    }
  }
  for (const row of stage.tiles) feed(row)
  feed(`|p${stage.playerSpawn?.col ?? -1},${stage.playerSpawn?.row ?? -1}`)
  for (const e of stage.enemySpawns ?? []) feed(`|e${e.col},${e.row}`)
  for (const k of stage.enemies) feed(`|k${k}`)
  if (stage.enemyCount !== undefined) feed(`|n${stage.enemyCount}`)
  return (h >>> 0).toString(16).padStart(8, '0')
}
