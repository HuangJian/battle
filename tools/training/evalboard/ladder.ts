/** ladder.ts — 评估阶梯（plan/rl-eval-system.md §4）。
 *
 * 1–8 关定死（D1），9+ 留接口。维度引入顺序：敌数 → 命数 → 地形 → 基地 →
 * 关卡泛化，每阶只加一个维度，失败即可定位到维度（§4.1）。
 * rung 自带可执行 stage 载荷 + mapHash（§4.2）；门控推进是纯函数（§4.3/§4.4）。
 */

import { createHash } from 'node:crypto'
import { STAGES } from '../../../src/config/stages'
import { makeArena } from '../../../src/nn/arena-ladder'
import type { StageJson } from '../../../src/nn/config-stage'
import type { StageData } from '../../../src/config/types'
import type { TankKind } from '../../../src/types'
import type { TierMetrics } from './stats'

// ────────────────────────── §2.6 / §4.4 常量 ──────────────────────────

/** 主门系数（待校准，§11-2；校准前用此值并标 TBD）。 */
export const MAIN_GATE_RATIO = 0.7
/** 代价门上界（待校准）。 */
export const COST_GATE_UPPER = 13.5
/** 超时门（§4.4）。 */
export const TIMEOUT_GATE = 0.15
/** provisional 阈值：God < 30% 的 rung 穿过不判（§4.5）。 */
export const PROVISIONAL_THRESHOLD = 0.3
/** 阶梯版本（ladder.json 文件名 + God 基线键的一部分）。 */
export const LADDER_VERSION = 'v1'
/** 全阶梯 max_ticks = 12000（= 训练/A 层口径 cli.py:215；§4.2 例中 2400 只是 schema
 * 示意。实测 god 在 s1 rung 2400 tick 仅 6 杀被截断——截断局压胜率、抬超时门，
 * 且与训练口径不可比。probe_key 含 t<maxTicks>，改值自动隔离旧局）。 */
export const RUNG_MAX_TICKS = 12000

/** rung id（1–8 定死，§4.1）。 */
export type RungId = 'c4l1' | 'c6l1' | 'c8l2' | 'c10l2' | 'c14l3' | 'c20l3' | 's1l3b0' | 's1l3b1'

export interface RungGod {
  winRate: number | null
  lifePrice: number | null
  n: number
  provisional: boolean | null
}

export interface LadderRung {
  id: RungId
  idx: number
  stage: StageData
  lives: number
  level: number
  difficulty: 'hard'
  max_ticks: number
  mapHash: string
  god: RungGod
  /** 新引入维度（§4.1 定位用）。 */
  dimension: string
}

/** stage 载荷规范指纹（改 grid 不改 id 不会复用旧局，§4.2）。 */
export function mapHashOf(stage: StageData): string {
  const canonical = JSON.stringify({
    tiles: stage.tiles,
    enemies: stage.enemies,
    enemyCount: stage.enemyCount ?? null,
    playerSpawn: stage.playerSpawn ?? null,
    enemySpawns: stage.enemySpawns ?? null,
  })
  return createHash('sha256').update(canonical).digest('hex').slice(0, 16)
}

/** 经典第 1 关去基地实体（§4.2：由 ladder.ts 从 src/config/stages 派生并冻结存 JSON）。 */
export function stageS1NoBase(): StageData {
  const s1 = STAGES[0]
  return {
    ...s1,
    id: s1.id,
    name: `${s1.name}-nobase`,
    tiles: s1.tiles.map((row) => row.replace(/E/g, '.')),
    enemyCount: 20,
  }
}

/** 经典第 1 关原样（20 敌，基地防守）。 */
export function stageS1(): StageData {
  const s1 = STAGES[0]
  return { ...s1, enemyCount: 20 }
}

function openRung(
  id: RungId,
  idx: number,
  count: number,
  lives: number,
  dimension: string,
  base = false,
): LadderRung {
  const stage = makeArena({ size: 13, base, enemyCount: count })
  return {
    id,
    idx,
    stage,
    lives,
    level: 0,
    difficulty: 'hard',
    max_ticks: RUNG_MAX_TICKS,
    mapHash: mapHashOf(stage),
    god: { winRate: null, lifePrice: null, n: 0, provisional: null },
    dimension,
  }
}

/** 8 rung（§4.1 表格逐字落地）。 */
export function buildLadder(): LadderRung[] {
  const s1nb = stageS1NoBase()
  const s1 = stageS1()
  return [
    openRung('c4l1', 0, 4, 1, '基准（c4-margin 在此）'),
    openRung('c6l1', 1, 6, 1, '敌数'),
    openRung('c8l2', 2, 8, 2, '敌数 + 命数'),
    openRung('c10l2', 3, 10, 2, '敌数'),
    openRung('c14l3', 4, 14, 3, '敌数 + 命数'),
    openRung('c20l3', 5, 20, 3, '经典关敌量级'),
    {
      id: 's1l3b0',
      idx: 6,
      stage: s1nb,
      lives: 3,
      level: 0,
      difficulty: 'hard',
      max_ticks: RUNG_MAX_TICKS,
      mapHash: mapHashOf(s1nb),
      god: { winRate: null, lifePrice: null, n: 0, provisional: null },
      dimension: '地形（基地关闭，隔离变量）',
    },
    {
      id: 's1l3b1',
      idx: 7,
      stage: s1,
      lives: 3,
      level: 0,
      difficulty: 'hard',
      max_ticks: RUNG_MAX_TICKS,
      mapHash: mapHashOf(s1),
      god: { winRate: null, lifePrice: null, n: 0, provisional: null },
      dimension: '基地防守',
    },
  ]
}

/** God 基线写入后标 provisional（God < 30% 穿过不判，§4.5）。 */
export function markProvisional(rungs: LadderRung[]): LadderRung[] {
  return rungs.map((r) =>
    r.god.winRate === null
      ? r
      : { ...r, god: { ...r.god, provisional: r.god.winRate < PROVISIONAL_THRESHOLD } },
  )
}

const KIND_CHAR: Record<TankKind, string> = {
  basic: 'a',
  fast: 'b',
  power: 'c',
  armor: 'd',
  player: 'a',
}

/**
 * rung → 可执行 stageJson 载荷（§4.2：派发侧 stageJsonHash 的输入）。
 * tiles26 直传（open13 钢环非 2×2 对齐，数字瓦格无法往返）；forces 还原 kind 队列。
 */
export function stageJsonOf(rung: LadderRung): string {
  const st = rung.stage
  const payload: StageJson = {
    name: st.name,
    grid: [],
    tiles26: [...st.tiles],
    forces: st.enemies.map((k) => KIND_CHAR[k] ?? 'a').join(''),
    count: st.enemyCount ?? st.enemies.length,
    ...(st.playerSpawn ? { player_spawn: { ...st.playerSpawn } } : {}),
    ...(st.enemySpawns ? { enemy_spawns: st.enemySpawns.map((s) => ({ ...s })) } : {}),
  }
  return JSON.stringify(payload)
}

// ────────────────────────── §4.3 门控推进（纯函数） ──────────────────────────

export interface GateInput {
  student: TierMetrics
  /** 同窗 400 seed 的 God 率（§4.4：学生 400 seed 是 God 1600 的子集，取同窗消除段间偏差）。 */
  god: TierMetrics
  godWinRate1600: number | null
}

/** 过门四门（§4.4）逐项结果。 */
export interface GateResult {
  main: boolean
  cost: boolean
  style: boolean
  credible: boolean
  pass: boolean
  /** 主门绝对下限（待校准 §11-2；null = 未启用）。 */
  absoluteFloor: number | null
}

/** 代价门（§4.4）：`lifePrice ≤ clamp(3×God, God, 13.5)`；wins=0 ⇒ +∞ 自动不过。 */
export function costGate(studentLifePrice: number, godLifePrice: number): boolean {
  const lo = godLifePrice
  const hi = COST_GATE_UPPER
  const gate = Math.min(Math.max(3 * godLifePrice, lo), hi)
  return studentLifePrice <= gate
}

export function evaluateGate(input: GateInput): GateResult {
  const { student, god } = input
  const main = student.winRate >= MAIN_GATE_RATIO * god.winRate
  const cost = costGate(student.lifePrice, god.lifePrice)
  const style = student.timeoutRate <= TIMEOUT_GATE
  const credible = student.n >= 400 && god.n >= 400
  return {
    main,
    cost,
    style,
    credible,
    pass: main && cost && style && credible,
    absoluteFloor: null,
  }
}

export type GateAdvance =
  | { action: 'pass'; next: number }
  | { action: 'stay' }
  | { action: 'partial' }
  | { action: 'provisional-pass'; next: number }
  | { action: 'fuse-stop'; reason: string }

/**
 * 门控推进（§4.3）：窗满才下结论；provisional 记录+穿过；熔断停+告警。
 * `fused` = S2/S3/S4/S5 任一命中（单批即可触发，§7.2）。
 */
export function nextPos(
  ladderPos: number,
  rung: LadderRung,
  gate: GateResult | null,
  opts?: { fused?: string | null; partial?: boolean },
): GateAdvance {
  if (opts?.fused) return { action: 'fuse-stop', reason: opts.fused }
  // provisional 先于 partial：穿过不判（§4.5）——记录即穿过，不停等窗满；
  // 推进由下一个非 provisional rung 的过门判定驱动。
  if (rung.god.provisional) return { action: 'provisional-pass', next: ladderPos + 1 }
  if (opts?.partial || gate === null) return { action: 'partial' }
  if (gate.pass) return { action: 'pass', next: ladderPos + 1 }
  return { action: 'stay' }
}
