#!/usr/bin/env bun
/**
 * anchor-report.ts — M0a 锚点契约 35 关 × 16 role 解析报表（plan/Intent-Policy-NN-
 * Intent-Policy-NN-Plan.md §3.4 / Q4 / 预注册 #2）。
 *
 * 每 role 在本关必须解析出**唯一坐标**；本工具在关卡加载态（fresh World + GodAIInput
 * reset，与 simulation-runner 同一装载序列）逐关解析并报告「0/1/回退」三态：
 *   ok      — 现有实现（聚合封装，无新轮子）直接命中
 *   fallback— 预注册确定性几何回退（就近可用格）接管
 *   miss    — 回退也无可站格（该 role 本关不可用 → 执行器契约要求 fallback 至
 *             INTERCEPT/撤退，禁止站死；M6 锚点失效测试的对象）
 *
 * 用法：bun tools/intent/anchor-report.ts [--json tmp/anchor-report.json]
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import {
  rankBaseGuardAnchorsImpl,
  getDefaultDefensePositionImpl,
} from '../../src/ai/god/StrategyPlanner'
import { findLaneDefensePointImpl, findParryHoldCellImpl } from '../../src/ai/god/PathCarve'
import { STAGES } from '../../src/config/stages'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { BASE_POS, GRID, PLAYER_SPAWN } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { ANCHOR_ROLE_IDS } from '../../src/ai/intent/vocab'

interface ResolvedCell {
  col: number
  row: number
}

interface RoleResolution {
  stage: number
  roleId: string
  status: 'ok' | 'fallback' | 'miss'
  cell: ResolvedCell | null
}

// ─── 确定性几何回退（预注册，镜像进 tagger）────────────────────────────────

/** 足迹可站（tank 2×2 footprint 无 brick/steel/water/base）。 */
function standable2x2(world: World, c: number, r: number): boolean {
  for (let dr = 0; dr <= 1; dr++) {
    for (let dc = 0; dc <= 1; dc++) {
      const t = world.tileMap.get(c + dc, r + dr)
      if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') return false
    }
  }
  return true
}

/** 就近可用格螺旋搜索（行主序半径扩张——确定性，禁 Math.random）。 */
function nearestStandable(world: World, c0: number, r0: number): ResolvedCell | null {
  for (let radius = 0; radius <= GRID; radius++) {
    for (let r = r0 - radius; r <= r0 + radius; r++) {
      for (let c = c0 - radius; c <= c0 + radius; c++) {
        if (Math.max(Math.abs(c - c0), Math.abs(r - r0)) !== radius) continue
        if (standable2x2(world, c, r)) return { col: c, row: r }
      }
    }
  }
  return null
}

const ENEMY_SPAWN_COLS = [0, 12, 6]

function resolveRoles(input: GodAIInput, world: World, stageIdx: number): RoleResolution[] {
  const out: RoleResolution[] = []
  const push = (roleId: string, status: RoleResolution['status'], cell: ResolvedCell | null) =>
    out.push({ stage: stageIdx, roleId, status, cell })

  // BASE_GUARD_0..3：排名解析器 top-4；缺位回退默认防守位 → 最近可用格。
  const guards = input.hasBase ? rankBaseGuardAnchorsImpl(input, 4) : []
  for (let i = 0; i < 4; i++) {
    const g = guards[i]
    if (g) push(`BASE_GUARD_${i}`, 'ok', g)
    else {
      const def = input.hasBase ? getDefaultDefensePositionImpl(input) : null
      const fb =
        def && standable2x2(world, def.col, def.row)
          ? def
          : nearestStandable(world, BASE_POS.col, BASE_POS.row - 3)
      push(`BASE_GUARD_${i}`, fb ? 'fallback' : 'miss', fb)
    }
  }

  // LANE_HOLD_0..2：对消持枪点（现役 impl 命中为 ok）；1/2 号沿基地列向上取
  // 次级可站格（几何回退口径，footprint 检查）。可达性以玩家出生格为锚。
  const pc = { col: PLAYER_SPAWN.col, row: PLAYER_SPAWN.row }
  const lane = input.hasBase ? findLaneDefensePointImpl(input, pc) : null
  if (lane) push('LANE_HOLD_0', 'ok', lane)
  else {
    const fb = nearestStandable(world, BASE_POS.col, BASE_POS.row - 4)
    push('LANE_HOLD_0', fb ? 'fallback' : 'miss', fb)
  }
  let found = 0
  for (let r = BASE_POS.row - 1; r >= 0 && found < 2; r--) {
    if (standable2x2(world, BASE_POS.col, r)) {
      push(`LANE_HOLD_${1 + found}`, 'fallback', { col: BASE_POS.col, row: r })
      found++
    }
  }
  for (let i = found; i < 2; i++) push(`LANE_HOLD_${1 + i}`, 'miss', null)

  // MID_FLANK_0/1：列旁对消格（§164 现役 impl 命中为 ok）；次级 = 列旁 ±2 列。
  const flank = input.hasBase ? findParryHoldCellImpl(input, pc) : null
  if (flank) push('MID_FLANK_0', 'ok', flank)
  else {
    const fb = nearestStandable(world, BASE_POS.col - 2, 6)
    push('MID_FLANK_0', fb ? 'fallback' : 'miss', fb)
  }
  const fbMid = nearestStandable(world, BASE_POS.col + 2, 6)
  push('MID_FLANK_1', fbMid ? 'fallback' : 'miss', fbMid)

  // SPAWN_WATCH_0/1：外侧两个敌出生道下方的瞭望格。
  const watchCols = [ENEMY_SPAWN_COLS[0], ENEMY_SPAWN_COLS[1]]
  for (let i = 0; i < 2; i++) {
    const fb = nearestStandable(world, watchCols[i] + 1, 4)
    push(`SPAWN_WATCH_${i}`, 'fallback', fb)
  }

  // RETREAT_0/1：出生侧安全格（玩家出生点两侧）。
  for (let i = 0; i < 2; i++) {
    const c0 = i === 0 ? 6 : 10
    const fb = nearestStandable(world, c0, 23)
    push(`RETREAT_${i}`, 'fallback', fb)
  }

  // RESERVE_0..2：四分位巡逻点。
  const reservePts: Array<[number, number]> = [
    [6, 12],
    [19, 12],
    [12, 18],
  ]
  for (let i = 0; i < reservePts.length; i++) {
    const [c0, r0] = reservePts[i]
    const fb = nearestStandable(world, c0, r0)
    push(`RESERVE_${i}`, 'fallback', fb)
  }

  return out
}

async function main(): Promise<void> {
  const jsonOut = process.argv[process.argv.indexOf('--json') + 1]
  const all: RoleResolution[] = []

  for (let si = 0; si < STAGES.length; si++) {
    const stage = STAGES[si]
    const world = new World()
    world.rng.reseed(1)
    world.difficultyKey = 'hard'
    world.difficulty = DIFFICULTIES['hard'] ?? DIFFICULTIES['classic']
    world.rules = RULES['hard'] ?? DEFAULT_RULES
    const input = new GodAIInput(
      world,
      { ...DEFAULT_GOD_AI_PARAMS },
      new RNG((1 ^ 0x9e3779b9) >>> 0),
    )
    new Simulation(world, input)
    world.loadStageData(stage, si)
    input.reset()
    all.push(...resolveRoles(input, world, si))
  }

  // ── 报表 ──
  const byRole = new Map<string, { ok: number; fallback: number; miss: number }>()
  for (const roleId of ANCHOR_ROLE_IDS) byRole.set(roleId, { ok: 0, fallback: 0, miss: 0 })
  for (const r of all) {
    const b = byRole.get(r.roleId)!
    b[r.status]++
  }

  console.log('=== 35 关 × 16 role 解析报表（加载态，difficulty=hard）===')
  console.log('role            ok  fback  miss')
  for (const roleId of ANCHOR_ROLE_IDS) {
    const b = byRole.get(roleId)!
    console.log(
      `${roleId.padEnd(15)} ${String(b.ok).padStart(3)}  ${String(b.fallback).padStart(4)}  ${String(b.miss).padStart(4)}`,
    )
  }
  const total = { ok: 0, fallback: 0, miss: 0 }
  for (const b of byRole.values()) {
    total.ok += b.ok
    total.fallback += b.fallback
    total.miss += b.miss
  }
  const n = STAGES.length * ANCHOR_ROLE_IDS.length
  console.log(
    `\n合计 ${n} 解析点：ok ${((total.ok / n) * 100).toFixed(1)}% · fallback ${((total.fallback / n) * 100).toFixed(1)}% · miss ${((total.miss / n) * 100).toFixed(1)}%`,
  )
  console.log('\n逐关明细（仅 fallback/miss 行）：')
  for (const r of all) {
    if (r.status === 'ok') continue
    console.log(
      `S${String(r.stage + 1).padStart(2)} ${r.roleId.padEnd(15)} ${r.status.padEnd(9)} ${
        r.cell ? `(${r.cell.col},${r.cell.row})` : '∅'
      }`,
    )
  }

  if (jsonOut) {
    await Bun.write(
      jsonOut,
      JSON.stringify({ generatedBy: 'tools/intent/anchor-report.ts', rows: all }, null, 2),
    )
    console.error(`\njson -> ${jsonOut}`)
  }
}

await main()
