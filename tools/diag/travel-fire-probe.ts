#!/usr/bin/env bun
/**
 * travel-fire-probe.ts — M5 诊断先行: measure the travel-phase fire-line
 * opportunity space in baseline losses (协议 §6.3 决策点前移方向).
 *
 * 输入: 一个 A/B corpus (ab-param --json 格式: "base|si|seed" → {o,t}),
 * 选择 baseline 败局 (o !== 'stage_clear') 做确定性 replay, 逐 tick 记录:
 *   旅行分支(committed travel: defense branches + hunt set) + 目标存在 +
 *   killSlack > 转弯窗(13t) + 对齐 + 射线全清(环/基地/任何地形) +
 *   未面向目标(需一次转弯 — 已面向则 baseline 本就会开火) + 不在冷却
 * 记为一个 M5 机会 tick。若机会稀少(<10% 败局), 候选无收益空间(§212 先例),
 * 记录诚实阴性; 否则候选值得实现。
 *
 * 纯只读: 不用 shouldFireInDirImpl(含 aimError RNG roll — 会扰动 replay),
 * 只用几何谓词(fireRayBlocked / 走廊扫描 / killAssessment / csb/cbr)。
 *
 * Usage:
 *   bun tools/diag/travel-fire-probe.ts --from-json tmp/m4-candidate-ab.json
 *         [--label base] [--limit 200]
 */
import { STAGES } from '../../src/config/stages'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { RNG } from '../../src/utils/RNG'
import { GRID } from '../../src/constants'
import { isDefenseBranch } from './failure-classifier'
import { killAssessment, tankCenterCell } from '../../src/ai/god/ThreatBudget'
import { fireRayBlocked } from '../../src/ai/god/ActionCandidates'
import { enemyCanShootBase, enemyCanBreachRing } from '../../src/ai/god/SmartThreatModel'

function arg(name: string, def?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : def
}

const fromJson = arg('from-json')
if (!fromJson) {
  console.error('usage: travel-fire-probe.ts --from-json corpus.json [--label base] [--limit N]')
  process.exit(1)
}
const label = arg('label', 'base')
const limit = Number(arg('limit') ?? '0')
const replayStageIndex = 0 // 官方口径 (corpus caliber)
const difficultyArg = () => arg('difficulty', 'hard') ?? 'hard'

const HUNT_BRANCHES = new Set(['aggressive', 'engage', 'hunt'])
const TURN_WINDOW_TICKS = 13 // one turn window (200ms @60fps) — the detour cost

/** 走廊扫描 — mirror of think.ts laneCorridorBlocked (single source of truth
 *  comment there): any non-empty terrain between two aligned cells blocks. */
function laneCorridorBlocked(
  tm: World['tileMap'],
  c: number,
  r: number,
  tc: number,
  tr: number,
): number {
  const g = tm.grid
  if (c === tc) {
    if (r === tr) return 0
    if (r < 0 || r >= GRID || tr < 0 || tr >= GRID || c < 0 || c >= GRID) return 999
    const step = r < tr ? 1 : -1
    for (let rr = r + step; rr !== tr; rr += step) {
      if (g[rr][c] !== 'empty') return rr < r ? r - rr : rr - r
    }
    return 0
  }
  if (r === tr) {
    if (c === tc) return 0
    if (r < 0 || r >= GRID || c < 0 || c >= GRID || tc < 0 || tc >= GRID) return 999
    const step = c < tc ? 1 : -1
    for (let cc = c + step; cc !== tc; cc += step) {
      if (g[r][cc] !== 'empty') return cc < c ? c - cc : cc - c
    }
    return 0
  }
  return -1
}

function setupRun(
  stageIdx: number,
  seed: number,
): { world: World; sim: Simulation; input: GodAIInput } {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficultyArg()
  world.difficulty = DIFFICULTIES[difficultyArg()] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficultyArg()] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? 3
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS }, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], replayStageIndex)
  input.reset()
  return { world, sim, input }
}

interface RunReport {
  key: string
  outcome: string
  ticks: number
  lossKind: string // base_destroyed | lives_exhausted | timeout
  travelTicks: number
  oppTicks: number
  gateFail: Record<string, number>
  firstOppTick: number
  firstBaseDamageTick: number
  branchMix: Record<string, number>
  targetStatus: Record<string, number>
  deterministic: boolean
}

const d = JSON.parse(await Bun.file(fromJson).text())
const runs: Array<{ stageIdx: number; seed: number; o: string; t: number }> = []
for (const [k, v] of Object.entries(d) as Array<[string, { o: string; t: number }]>) {
  const [lab, si, seed] = k.split('|')
  if (lab !== label) continue
  if (v.o === 'stage_clear') continue
  runs.push({ stageIdx: Number(si), seed: Number(seed), o: v.o, t: v.t })
}
const selected = limit > 0 ? runs.slice(0, limit) : runs

const reports: RunReport[] = []
for (const { stageIdx, seed, o } of selected) {
  const { world, sim, input } = setupRun(stageIdx, seed)
  const key = `${label}|${stageIdx}|${seed}`
  const rep: RunReport = {
    key,
    outcome: '',
    ticks: 0,
    lossKind: '',
    travelTicks: 0,
    oppTicks: 0,
    gateFail: { aligned: 0, facing: 0, cooldown: 0, slack: 0, corridor: 0, ray: 0, band: 0 },
    firstOppTick: -1,
    firstBaseDamageTick: -1,
    branchMix: {},
    targetStatus: {},
    deterministic: true,
  }
  let baseHp = world.baseHp
  for (let frame = 0; frame < 36000; frame++) {
    sim.tick()
    input.endFrame()
    rep.ticks = frame + 1
    if (baseHp > world.baseHp && rep.firstBaseDamageTick < 0) rep.firstBaseDamageTick = frame + 1
    baseHp = world.baseHp
    if (world.state !== 'playing') break
    const p = world.player
    if (!p || !p.alive || p.spawnTimer > 0) continue
    const branch = input._lastBranch
    const onCooldown = world.frame * (1000 / 60) - p.lastFire < p.nextFireInterval
    const travel = isDefenseBranch(branch) || HUNT_BRANCHES.has(branch)
    if (!travel) continue
    rep.travelTicks++
    // 目标代理 (mirror UNIFIED_CANDIDATES): 最后 selectTarget 目标, 否则最近敌。
    const list = input._enemies.length > 0 ? input._enemies : world.tanks
    let hunt: (typeof list)[number] | null = null
    let nearest: (typeof list)[number] | null = null
    let nearestD = Infinity
    const pc = tankCenterCell(p)
    for (let i = 0; i < list.length; i++) {
      const t = list[i]
      if (!t.alive || t.spawnTimer > 0 || t.isPlayer) continue
      if (t.id === input._lastSelectTargetId) hunt = t
      const tc = tankCenterCell(t)
      const dd = Math.abs(tc.col - pc.col) + Math.abs(tc.row - pc.row)
      if (dd < nearestD) {
        nearestD = dd
        nearest = t
      }
    }
    const target = hunt ?? nearest
    if (!target) continue
    // 机会条件:
    const tc = tankCenterCell(target)
    const aligned = tc.col === pc.col || tc.row === pc.row
    if (!aligned) {
      rep.gateFail.aligned++
      continue
    }
    const dir =
      tc.col === pc.col ? (tc.row > pc.row ? 'down' : 'up') : tc.col > pc.col ? 'right' : 'left'
    if (p.dir === dir) {
      rep.gateFail.facing++
      continue
    } // 已面向 — baseline 本就会开火, 不是 M5 的增量
    if (onCooldown) {
      rep.gateFail.cooldown++
      continue
    }
    const slack = killAssessment(world, p, target).killSlack
    if (!(slack > TURN_WINDOW_TICKS)) {
      rep.gateFail.slack++
      continue
    }
    if (laneCorridorBlocked(world.tileMap, pc.col, pc.row, tc.col, tc.row) !== 0) {
      rep.gateFail.corridor++
      continue
    }
    if (fireRayBlocked(world, p, target)) {
      rep.gateFail.ray++
      continue
    }
    const csb = enemyCanShootBase(input, target)
    const cbr = !csb && enemyCanBreachRing(input, target)
    // fb 目标只在基地逼近带内算机会 (S3s46 类游走威胁) — 地图任意位置的对齐
    // 敌人都算机会的话, 95% tick 都是 (fb deadline 大, slack 恒正), 无信号。
    const inBand = tc.row >= 20 && Math.abs(tc.col - 12) <= 6
    if (!csb && !cbr && !inBand) {
      rep.gateFail.band++
      continue
    }
    // 机会成立
    rep.oppTicks++
    if (rep.firstOppTick < 0) rep.firstOppTick = frame + 1
    rep.branchMix[branch] = (rep.branchMix[branch] ?? 0) + 1
    const st = csb ? 'csb' : cbr ? 'cbr' : 'fb'
    rep.targetStatus[st] = (rep.targetStatus[st] ?? 0) + 1
  }
  rep.outcome =
    world.state === 'stageclear'
      ? 'stage_clear'
      : world.state === 'gameover'
        ? 'gameover'
        : world.state === 'victory'
          ? 'victory'
          : 'playing'
  if (rep.outcome === 'gameover') {
    rep.lossKind =
      world.baseHp <= 0 ? 'base_destroyed' : world.lives < 0 ? 'lives_exhausted' : 'timeout'
  } else if (rep.outcome !== 'playing') {
    rep.lossKind = 'timeout'
  }
  rep.deterministic = rep.outcome === o || (o === 'gameover' && rep.outcome === 'gameover')
  reports.push(rep)
}

// ---- 汇总 ----
const total = reports.length
const detOk = reports.filter((r) => r.deterministic).length
const withOpp = reports.filter((r) => r.oppTicks > 0)
const oppBeforeDmg = withOpp.filter(
  (r) => r.firstOppTick < r.firstBaseDamageTick || r.firstBaseDamageTick < 0,
)
const branchMix: Record<string, number> = {}
const targetStatus: Record<string, number> = {}
let oppTicksTotal = 0
for (const r of withOpp) {
  for (const [b, n] of Object.entries(r.branchMix)) branchMix[b] = (branchMix[b] ?? 0) + n
  for (const [s, n] of Object.entries(r.targetStatus)) targetStatus[s] = (targetStatus[s] ?? 0) + n
  oppTicksTotal += r.oppTicks
}
console.log(`corpus=${fromJson} label=${label} losses=${total} deterministic=${detOk}/${total}`)
console.log(
  `runs with >=1 travel-fire opportunity: ${withOpp.length}/${total} (${((withOpp.length / total) * 100).toFixed(1)}%)`,
)
console.log(
  `travel ticks total: ${reports.reduce((s, r) => s + r.travelTicks, 0)} (mean ${(reports.reduce((s, r) => s + r.travelTicks, 0) / Math.max(1, total)).toFixed(0)}/run)`,
)
console.log(
  `opp ticks total: ${oppTicksTotal} (mean ${(oppTicksTotal / Math.max(1, withOpp.length)).toFixed(1)} per opp-run)`,
)
console.log(
  `opp precedes first base damage: ${oppBeforeDmg.length}/${withOpp.length} (${((oppBeforeDmg.length / Math.max(1, withOpp.length)) * 100).toFixed(0)}%)`,
)
console.log('branch mix at opp:', JSON.stringify(branchMix))
console.log('target status at opp:', JSON.stringify(targetStatus))
const kinds: Record<string, number> = {}
for (const r of reports) kinds[r.lossKind] = (kinds[r.lossKind] ?? 0) + 1
console.log('loss kinds:', JSON.stringify(kinds))
const gf: Record<string, number> = {}
for (const r of reports) for (const [g, n] of Object.entries(r.gateFail)) gf[g] = (gf[g] ?? 0) + n
console.log(
  'gate fails per run:',
  JSON.stringify(
    Object.fromEntries(Object.entries(gf).map(([k, v]) => [k, +(v / total).toFixed(1)])),
  ),
)
if (withOpp.length) {
  const t = withOpp.map((r) => r.firstOppTick).sort((a, b) => a - b)
  console.log(
    `firstOppTick median ${t[Math.floor(t.length / 2)]} p25 ${t[Math.floor(t.length * 0.25)]} p75 ${t[Math.floor(t.length * 0.75)]}`,
  )
}
