#!/usr/bin/env bun
/**
 * positioning-dose-probe.ts — P-1b C-family / positioning dose screen
 * (plan/nn-input-inference-rules.plan.md §4.1.7).
 *
 * Runs the BARE policy (NNInput) and records, at every decision instant, how
 * often each candidate rule's precondition is present. No rule is applied.
 *
 * Covered:
 *   · C1  threat-lane exposure (threatLaneSources > 0; turn/advance while in lane)
 *   · C4  safe-window exposure (shield / freeze; pickup or enemy nearby)
 *   · E1  residual-enemy / blocked-facing exposure (needs BFS navigation)
 *   · B4  ice exposure (on ice; turn/hold while on ice)
 *   · C2  decision-gate readout (events / n / avgDt; needs --decision-events)
 *
 * Usage:
 *   bun tools/diag/positioning-dose-probe.ts \
 *     --weights tmp/g2-ckpts/ep80.json \
 *     --stage-json-file tmp/classic35-safe-base-stages.json \
 *     --stages 0-34 --seeds 2000002001-2000002005 \
 *     --out tmp/positioning-dose-pilot.jsonl
 */
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { NNInput, type NNInputOptions } from '../../src/nn/policy-input'
import { decodeStageGrid, type StageJson } from '../../src/nn/config-stage'
import {
  brickAheadWithin,
  brickBlocksAdvance,
  c4MoveBias,
  firstBulletBlocker,
  steelOnlyShot,
  turnWillCommit,
  usefulFire,
} from '../../src/nn/inference-rules'
import { leadHitForDir } from '../../src/nn/policy-extra'
import { isFireReady } from '../../src/nn/obs-encoder'
import { bfsDistance } from '../../src/nn/goal-mask'
import { iceGlideAdjust } from '../../src/ai/god/Navigator'
import {
  THREAT_ALIGN_BAND_PX,
  corridorCells,
  nearestEnemyDistPx,
  threatLaneSources,
} from '../../src/nn/danger-metrics'
import { CELL, GRID, START_LIVES, TICK_MS, POWERUP_TIMEOUT_MS } from '../../src/constants'
import type { StageData } from '../../src/config/types'

interface PosDose {
  type: 'game'
  stage: number
  seed: number
  c1Weight: number
  c4Bias: number
  e1Bias: number
  skipSteelOnlyShots: boolean
  outcome: string
  win: boolean
  kills: number
  lives: number
  clearTick: number
  ticks: number
  decisions: number
  // C1
  threatDecisions: number
  threatSourcesSum: number
  threatTurnDecisions: number
  threatAdvanceDecisions: number
  // C4
  shieldDecisions: number
  freezeDecisions: number
  safeWindowDecisions: number
  shieldPickupDecisions: number
  freezePickupDecisions: number
  shieldNearEnemyDecisions: number
  freezeNearEnemyDecisions: number
  c4OpportunityDecisions: number
  steelOnlyShotDecisions: number
  // B2（拆砖）
  brickBlockedDecisions: number
  brickAheadDecisions: number
  brickFireOpportunities: number
  // B4（冰面滑行）
  iceGlideDecisions: number
  iceReverseDecisions: number
  iceReverseOpportunities: number
  // backlog/dormant 批次（B8/B10/B11/C3/C5/C7/C8/D1）
  b8FireOpportunities: number
  b10LeadFireOpportunities: number
  b11SteelBlockedMoves: number
  c3PincerOpportunities: number
  c5FreezeOpportunities: number
  c7DuelOpportunities: number
  c8CrossfireOpportunities: number
  d1ReachablePickupOpportunities: number
  ruleApplications: number
  // E1
  enemyRemainDecisions: number
  farEnemyDecisions: number
  blockedFacingDecisions: number
  blockedFarDecisions: number
  // B4
  iceDecisions: number
  iceTurnDecisions: number
  iceHoldDecisions: number
  // C2
  decisionN: number
  decisionEvents: number
  decisionAvgDt: number
  decisionMinDt: number
  decisionMaxDt: number
  wallSec: number
}

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

function parseIntRange(spec: string): number[] {
  const [a, b] = spec.split('-').map((s) => parseInt(s, 10))
  if (!Number.isFinite(a)) return []
  if (b === undefined || !Number.isFinite(b)) return [a]
  const out: number[] = []
  for (let i = a; i <= b; i++) out.push(i)
  return out
}

function hasAlivePickup(world: World): boolean {
  const list = world.powerUps
  for (let i = 0; i < list.length; i++) if (list[i].alive) return true
  return false
}

function processDecision(
  world: World,
  input: NNInput,
  d: PosDose,
  c4PickupRadius: number,
  c4EnemyRadius: number,
): void {
  const p = world.player
  if (!p || !p.alive || p.spawnTimer > 0) return
  const dir = input.getMoveDirection()
  const hold = input.isHolding()
  const threat = threatLaneSources(world)
  const turning = dir !== null && dir !== p.prevMoveDir
  const shield = (p.shieldTimer ?? 0) > 0
  const freeze = world.freezeTimer > 0
  const pickup = hasAlivePickup(world)
  const nearEnemy = nearestEnemyDistPx(world) <= 4 * CELL
  const enemies = world.enemyCount > 0
  const farEnemy = nearestEnemyDistPx(world) > 8 * CELL
  const blockedFacing = dir !== null && corridorCells(world, p, dir, 1) === 0
  const cx = Math.floor((p.x + p.w / 2) / CELL)
  const cy = Math.floor((p.y + p.h / 2) / CELL)
  const onIce = world.tileMap.get(cx, cy) === 'ice'

  d.decisions++
  if (threat > 0) {
    d.threatDecisions++
    d.threatSourcesSum += threat
    if (turning) d.threatTurnDecisions++
    if (!hold) d.threatAdvanceDecisions++
  }
  if (shield) d.shieldDecisions++
  if (freeze) d.freezeDecisions++
  if (shield || freeze) d.safeWindowDecisions++
  if (shield && pickup) d.shieldPickupDecisions++
  if (freeze && pickup) d.freezePickupDecisions++
  if (shield && nearEnemy) d.shieldNearEnemyDecisions++
  if (freeze && nearEnemy) d.freezeNearEnemyDecisions++
  if (shield && c4MoveBias(world, p, c4PickupRadius, c4EnemyRadius) >= 0) {
    d.c4OpportunityDecisions++
  }
  // skipSteelOnlyShots opportunity: the committed shot would be a pure steel shot.
  const intent = !input.isFiring() ? 0 : input.isFireBeforeTurn() ? 1 : 2
  if (intent !== 0) {
    let shotDir = null
    if (intent === 1) {
      shotDir = p.prevMoveDir ?? p.dir
    } else if (turnWillCommit(world, p, input.getMoveDirection(), (world.frame + 1) * TICK_MS)) {
      shotDir = input.getMoveDirection()
    }
    if (shotDir && steelOnlyShot(world, p, shotDir)) d.steelOnlyShotDecisions++
    if (shotDir) {
      const first = firstBulletBlocker(world, p, shotDir)
      const pcx = p.x + p.w / 2
      const pcy = p.y + p.h / 2
      if (!usefulFire(world, p, shotDir) && first !== 'brick') d.b8FireOpportunities++
      if (leadHitForDir(world, shotDir, pcx, pcy) === 0 && first !== 'brick') {
        d.b10LeadFireOpportunities++
      }
    }
  }
  // B2 拆砖剂量：①面前砖挡路（hold 时最典型）；②前进方向 2 格内有砖；
  // ③策略本身没开火（= 规则真正会改变动作的机会）。
  const brickBlocked = dir !== null && brickBlocksAdvance(world, p, dir)
  const brickAhead = !hold && dir !== null && brickAheadWithin(world, p, dir, 2)
  if (brickBlocked) d.brickBlockedDecisions++
  if (brickAhead) d.brickAheadDecisions++
  if ((brickBlocked || brickAhead) && intent === 0) d.brickFireOpportunities++
  // B4 冰面剂量：滑行中（|v|≥0.3）请求反向 = 会真倒车/过冲的机会。
  const iceGlide = onIce && Math.abs(p.vx) + Math.abs(p.vy) >= 0.3
  const iceReverse =
    iceGlide && dir !== null && iceGlideAdjust(dir, onIce, p.vx, p.vy, 0.3) === null
  if (iceGlide) d.iceGlideDecisions++
  if (iceReverse) d.iceReverseDecisions++
  if (iceReverse && !hold && intent === 0) d.iceReverseOpportunities++
  // B11 钢挡路：所选方向 1 格内是钢，且存在别的可走方向。
  if (
    dir !== null &&
    corridorCells(world, p, dir, 1) === 0 &&
    firstBulletBlocker(world, p, dir) === 'steel'
  ) {
    const dirs = ['up', 'down', 'left', 'right'] as const
    for (const d2 of dirs) {
      if (d2 !== dir && corridorCells(world, p, d2, 1) > 0) {
        d.b11SteelBlockedMoves++
        break
      }
    }
  }
  // C3 包夹：4 格内敌人在左右或上下两侧同时出现。
  let pincerL = 0
  let pincerR = 0
  let pincerU = 0
  let pincerD = 0
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  for (const t of world.allTanks) {
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const dx = t.x + t.w / 2 - pcx
    const dy = t.y + t.h / 2 - pcy
    if (Math.abs(dx) > 4 * CELL || Math.abs(dy) > 4 * CELL) continue
    if (Math.abs(dx) >= Math.abs(dy)) dx < 0 ? pincerL++ : pincerR++
    else dy < 0 ? pincerU++ : pincerD++
  }
  if ((pincerL > 0 && pincerR > 0) || (pincerU > 0 && pincerD > 0)) {
    d.c3PincerOpportunities++
  }
  // C5 冻结期：敌人被冻且 8 格内有活敌。
  if (freeze && enemies && nearestEnemyDistPx(world) <= 8 * CELL) d.c5FreezeOpportunities++
  // C7 对射：玩家 fire-ready，正前方 13 格内敌人也正对玩家。
  if (isFireReady(world) && enemies) {
    const opp: Record<string, string> = { up: 'down', down: 'up', left: 'right', right: 'left' }
    const fx = p.dir === 'left' ? -1 : p.dir === 'right' ? 1 : 0
    const fy = p.dir === 'up' ? -1 : p.dir === 'down' ? 1 : 0
    for (const t of world.allTanks) {
      if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
      const dx = t.x + t.w / 2 - pcx
      const dy = t.y + t.h / 2 - pcy
      const axial = dx * fx + dy * fy
      const lateral = Math.abs(dx * fy) + Math.abs(dy * fx)
      if (axial > 0 && axial <= 13 * CELL && lateral < THREAT_ALIGN_BAND_PX) {
        if (t.dir === opp[p.dir]) {
          d.c7DuelOpportunities++
          break
        }
      }
    }
  }
  // C8 交叉火力 + 紧迫：≥2 条威胁车道且 4 格内有活敌。
  if (threat >= 2 && enemies && nearestEnemyDistPx(world) <= 4 * CELL) {
    d.c8CrossfireOpportunities++
  }
  // D1 可达道具：BFS 步数 × CELL / speed ≤ 剩余存活 tick。
  if (enemies || world.powerUps.some((pu) => pu.alive)) {
    for (const pu of world.powerUps) {
      if (!pu.alive) continue
      const dc = Math.floor((pu.x + pu.w / 2) / CELL)
      const dr = Math.floor((pu.y + pu.h / 2) / CELL)
      const steps = bfsDistance(world, dc, dr)[cy * GRID + cx]
      if (steps <= 0) continue
      const etaTicks = (steps * CELL) / Math.max(p.speed, 0.01)
      const remainTicks = (POWERUP_TIMEOUT_MS - pu.lifeTimer) / TICK_MS
      if (etaTicks <= remainTicks) {
        d.d1ReachablePickupOpportunities++
        break
      }
    }
  }
  if (enemies) {
    d.enemyRemainDecisions++
    if (farEnemy) d.farEnemyDecisions++
    if (blockedFacing) d.blockedFacingDecisions++
    if (farEnemy && blockedFacing) d.blockedFarDecisions++
  }
  if (onIce) {
    d.iceDecisions++
    if (turning) d.iceTurnDecisions++
    if (hold) d.iceHoldDecisions++
  }
}

function runOne(
  world: World,
  stage: StageData,
  stageIndex: number,
  loadIndex: number,
  seed: number,
  difficulty: string,
  weightsPath: string,
  maxTicks: number,
  decisionK: number,
  decisionEvents: boolean,
  c1Weight: number,
  c4Bias: number,
  c4PickupRadius: number,
  c4EnemyRadius: number,
  e1Bias: number,
  skipSteelOnlyShots: boolean,
  breakBricksOnPath: boolean,
  releaseReverseOnIce: boolean,
  ruleOpts: NNInputOptions,
): PosDose {
  const t0 = Date.now()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  const input = new NNInput(world, {
    weightsPath,
    decisionK,
    decisionEvents,
    c1Weight,
    c4Bias,
    c4PickupRadius,
    c4EnemyRadius,
    e1Bias,
    skipSteelOnlyShots,
    breakBricksOnPath,
    releaseReverseOnIce,
    ...ruleOpts,
  })
  if (!input.usesHoldFire3()) {
    throw new Error('positioning-dose-probe requires a v6 checkpoint (usesHoldFire3)')
  }
  const sim = new Simulation(world, input)
  world.loadStageData(stage, loadIndex)
  input.reset()

  const d: PosDose = {
    type: 'game',
    stage: stageIndex,
    seed,
    c1Weight,
    c4Bias,
    e1Bias,
    skipSteelOnlyShots,
    outcome: 'max_ticks',
    win: false,
    kills: 0,
    lives: 0,
    clearTick: -1,
    ticks: 0,
    decisions: 0,
    threatDecisions: 0,
    threatSourcesSum: 0,
    threatTurnDecisions: 0,
    threatAdvanceDecisions: 0,
    shieldDecisions: 0,
    freezeDecisions: 0,
    safeWindowDecisions: 0,
    shieldPickupDecisions: 0,
    freezePickupDecisions: 0,
    shieldNearEnemyDecisions: 0,
    freezeNearEnemyDecisions: 0,
    c4OpportunityDecisions: 0,
    steelOnlyShotDecisions: 0,
    brickBlockedDecisions: 0,
    brickAheadDecisions: 0,
    brickFireOpportunities: 0,
    iceGlideDecisions: 0,
    iceReverseDecisions: 0,
    iceReverseOpportunities: 0,
    b8FireOpportunities: 0,
    b10LeadFireOpportunities: 0,
    b11SteelBlockedMoves: 0,
    c3PincerOpportunities: 0,
    c5FreezeOpportunities: 0,
    c7DuelOpportunities: 0,
    c8CrossfireOpportunities: 0,
    d1ReachablePickupOpportunities: 0,
    ruleApplications: 0,
    enemyRemainDecisions: 0,
    farEnemyDecisions: 0,
    blockedFacingDecisions: 0,
    blockedFarDecisions: 0,
    iceDecisions: 0,
    iceTurnDecisions: 0,
    iceHoldDecisions: 0,
    decisionN: 0,
    decisionEvents: 0,
    decisionAvgDt: 0,
    decisionMinDt: 0,
    decisionMaxDt: 0,
    wallSec: 0,
  }

  // The reset() decision is already committed on the fresh stage.
  processDecision(world, input, d, c4PickupRadius, c4EnemyRadius)
  let lastDecisions = input.readDecisionStats().n

  let t = 0
  while (t < maxTicks) {
    sim.tick()
    input.endFrame()
    const decisions = input.readDecisionStats().n
    if (decisions > lastDecisions) {
      processDecision(world, input, d, c4PickupRadius, c4EnemyRadius)
      lastDecisions = decisions
    }
    t++
    const st = world.state
    if (st === 'stageclear' || st === 'victory' || st === 'gameover') {
      d.outcome = st === 'gameover' ? 'gameover' : 'stage_clear'
      break
    }
  }

  const readout = input.readDecisionStats()
  d.ticks = t
  d.decisionN = readout.n
  d.decisionEvents = readout.events
  d.decisionAvgDt = readout.avgDt
  d.decisionMinDt = readout.minDt
  d.decisionMaxDt = readout.maxDt
  d.wallSec = (Date.now() - t0) / 1000
  d.ruleApplications = input.readRuleApplications()
  if (d.outcome === 'max_ticks' && world.state === 'stageclear') d.outcome = 'stage_clear'
  d.win = world.state === 'stageclear' || world.state === 'victory'
  d.kills = world.killCount
  d.lives = world.lives
  d.clearTick = d.win ? t : -1
  return d
}

function main(): void {
  const weightsPath = arg('weights')
  if (!weightsPath) {
    console.error('positioning-dose-probe: --weights <path> is required')
    process.exit(2)
  }
  const difficulty = arg('difficulty', 'hard')!
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const decisionK = parseInt(arg('decision-k', '10')!, 10)
  const decisionEvents = process.argv.includes('--decision-events')
  const c1Weight = parseFloat(arg('c1-weight', '0')!)
  const c4Bias = parseFloat(arg('c4-bias', '0')!)
  const c4PickupRadius = parseInt(arg('c4-pickup-radius', '6')!, 10)
  const c4EnemyRadius = parseInt(arg('c4-enemy-radius', '5')!, 10)
  const e1Bias = parseFloat(arg('e1-bias', '0')!)
  const skipSteelOnlyShots = process.argv.includes('--skip-steel-only-shots')
  const breakBricksOnPath = process.argv.includes('--break-bricks-on-path')
  const releaseReverseOnIce = process.argv.includes('--release-reverse-on-ice')
  const ruleOpts: NNInputOptions = {
    suppressUselessFire: process.argv.includes('--suppress-useless-fire'),
    suppressLeadMissFire: process.argv.includes('--suppress-lead-miss-fire'),
    avoidSteelPath: process.argv.includes('--avoid-steel-path'),
    breakPincer: process.argv.includes('--break-pincer') ? 0.5 : 0,
    attackWhileFrozen: process.argv.includes('--attack-while-frozen') ? 0.5 : 0,
    counterFireFirst: process.argv.includes('--counter-fire-first'),
    crossfireAwareness: process.argv.includes('--crossfire-awareness') ? 0.5 : 0,
    pickupReachBias: process.argv.includes('--pickup-reach-bias') ? 0.5 : 0,
  }
  const ruleTag = Object.entries(ruleOpts)
    .filter(([, v]) => v !== false && v !== 0)
    .map(([k]) => k)
    .join(',')
  const stages = parseIntRange(arg('stages', '0-34')!)
  const seeds = parseIntRange(arg('seeds', '2000002001-2000002005')!)
  const stageJsonFile = arg('stage-json-file', '')!
  const outPath = arg('out', '')!

  let custom: StageJson[] | null = null
  if (stageJsonFile) {
    const raw = JSON.parse(readFileSync(stageJsonFile, 'utf8'))
    custom = Array.isArray(raw) ? (raw as StageJson[]) : [raw as StageJson]
  }

  const lines: string[] = []
  if (outPath) writeFileSync(outPath, '')
  const t0 = Date.now()
  for (const si of stages) {
    if (custom && !custom[si]) {
      console.error(`positioning-dose-probe: stage json ${si} not found`)
      process.exit(2)
    }
    const stage = custom ? decodeStageGrid(custom[si], si, seeds[0]) : STAGES[si]
    if (!stage) {
      console.error(`positioning-dose-probe: stage ${si} not found`)
      process.exit(2)
    }
    const loadIndex = custom ? 0 : si
    for (const seed of seeds) {
      const world = new World()
      const d = runOne(
        world,
        stage,
        si,
        loadIndex,
        seed,
        difficulty,
        weightsPath,
        maxTicks,
        decisionK,
        decisionEvents,
        c1Weight,
        c4Bias,
        c4PickupRadius,
        c4EnemyRadius,
        e1Bias,
        skipSteelOnlyShots,
        breakBricksOnPath,
        releaseReverseOnIce,
        ruleOpts,
      )
      const line = JSON.stringify(d)
      lines.push(line)
      if (outPath) appendFileSync(outPath, line + '\n')
      console.error(
        `[pos-dose] stage=${si} seed=${seed} out=${d.outcome} ticks=${d.ticks} dec=${d.decisions} ` +
          `threat=${d.threatDecisions} shield=${d.shieldDecisions} freeze=${d.freezeDecisions} ` +
          `shieldPU=${d.shieldPickupDecisions} freezePU=${d.freezePickupDecisions} ` +
          `farEnemy=${d.farEnemyDecisions} blocked=${d.blockedFacingDecisions} ` +
          `brickBlocked=${d.brickBlockedDecisions} brickOpp=${d.brickFireOpportunities} ` +
          `iceRev=${d.iceReverseOpportunities} ` +
          `ice=${d.iceDecisions} wall=${d.wallSec.toFixed(2)}s`,
      )
    }
  }

  const wall = (Date.now() - t0) / 1000
  const sum = lines.reduce(
    (a, l) => {
      const d = JSON.parse(l) as PosDose
      for (const k of Object.keys(a) as (keyof typeof a)[]) a[k] += d[k] as number
      return a
    },
    {
      decisions: 0,
      threatDecisions: 0,
      threatSourcesSum: 0,
      threatTurnDecisions: 0,
      threatAdvanceDecisions: 0,
      shieldDecisions: 0,
      freezeDecisions: 0,
      safeWindowDecisions: 0,
      shieldPickupDecisions: 0,
      freezePickupDecisions: 0,
      shieldNearEnemyDecisions: 0,
      freezeNearEnemyDecisions: 0,
      c4OpportunityDecisions: 0,
      steelOnlyShotDecisions: 0,
      brickBlockedDecisions: 0,
      brickAheadDecisions: 0,
      brickFireOpportunities: 0,
      iceGlideDecisions: 0,
      iceReverseDecisions: 0,
      iceReverseOpportunities: 0,
      b8FireOpportunities: 0,
      b10LeadFireOpportunities: 0,
      b11SteelBlockedMoves: 0,
      c3PincerOpportunities: 0,
      c5FreezeOpportunities: 0,
      c7DuelOpportunities: 0,
      c8CrossfireOpportunities: 0,
      d1ReachablePickupOpportunities: 0,
      ruleApplications: 0,
      enemyRemainDecisions: 0,
      farEnemyDecisions: 0,
      blockedFacingDecisions: 0,
      blockedFarDecisions: 0,
      iceDecisions: 0,
      iceTurnDecisions: 0,
      iceHoldDecisions: 0,
      decisionN: 0,
      decisionEvents: 0,
    },
  )
  const rate = (a: number, b: number): number => (b > 0 ? a / b : 0)
  const games = lines.map((l) => JSON.parse(l) as PosDose)
  const wins = games.filter((g) => g.win).length
  const winGames = games.filter((g) => g.win && g.clearTick >= 0)
  const avgClearTick = winGames.length
    ? winGames.reduce((a, g) => a + g.clearTick, 0) / winGames.length
    : 0
  const avgKills = games.length ? games.reduce((a, g) => a + g.kills, 0) / games.length : 0
  const avgLives = games.length ? games.reduce((a, g) => a + g.lives, 0) / games.length : 0
  const summary = {
    type: 'summary',
    games: lines.length,
    c1Weight,
    rules: ruleTag,
    wins,
    winRate: rate(wins, games.length),
    avgClearTick,
    avgKills,
    avgLives,
    wallSec: wall,
    ...sum,
    threatRate: rate(sum.threatDecisions, sum.decisions),
    threatTurnRate: rate(sum.threatTurnDecisions, sum.threatDecisions),
    threatAdvanceRate: rate(sum.threatAdvanceDecisions, sum.threatDecisions),
    safeWindowRate: rate(sum.safeWindowDecisions, sum.decisions),
    shieldRate: rate(sum.shieldDecisions, sum.decisions),
    freezeRate: rate(sum.freezeDecisions, sum.decisions),
    shieldPickupRate: rate(sum.shieldPickupDecisions, sum.shieldDecisions),
    freezePickupRate: rate(sum.freezePickupDecisions, sum.freezeDecisions),
    shieldNearEnemyRate: rate(sum.shieldNearEnemyDecisions, sum.shieldDecisions),
    freezeNearEnemyRate: rate(sum.freezeNearEnemyDecisions, sum.freezeDecisions),
    c4OpportunityRate: rate(sum.c4OpportunityDecisions, sum.decisions),
    c4OpportunityAmongShield: rate(sum.c4OpportunityDecisions, sum.shieldDecisions),
    steelOnlyShotRate: rate(sum.steelOnlyShotDecisions, sum.decisions),
    brickBlockedRate: rate(sum.brickBlockedDecisions, sum.decisions),
    brickAheadRate: rate(sum.brickAheadDecisions, sum.decisions),
    brickFireOpportunityRate: rate(sum.brickFireOpportunities, sum.decisions),
    brickOpportunityAmongBlocked: rate(sum.brickFireOpportunities, sum.brickBlockedDecisions),
    brickOpportunityAmongAhead: rate(sum.brickFireOpportunities, sum.brickAheadDecisions),
    iceGlideRate: rate(sum.iceGlideDecisions, sum.decisions),
    iceReverseRate: rate(sum.iceReverseDecisions, sum.iceGlideDecisions),
    iceReverseOpportunityRate: rate(sum.iceReverseOpportunities, sum.decisions),
    iceReverseOpportunityAmongGlide: rate(sum.iceReverseOpportunities, sum.iceGlideDecisions),
    b8FireRate: rate(sum.b8FireOpportunities, sum.decisions),
    b10LeadFireRate: rate(sum.b10LeadFireOpportunities, sum.decisions),
    b11SteelBlockedRate: rate(sum.b11SteelBlockedMoves, sum.decisions),
    c3PincerRate: rate(sum.c3PincerOpportunities, sum.decisions),
    c5FreezeRate: rate(sum.c5FreezeOpportunities, sum.decisions),
    c7DuelRate: rate(sum.c7DuelOpportunities, sum.decisions),
    c8CrossfireRate: rate(sum.c8CrossfireOpportunities, sum.decisions),
    d1ReachablePickupRate: rate(sum.d1ReachablePickupOpportunities, sum.decisions),
    farEnemyRate: rate(sum.farEnemyDecisions, sum.enemyRemainDecisions),
    blockedFacingRate: rate(sum.blockedFacingDecisions, sum.enemyRemainDecisions),
    blockedFarRate: rate(sum.blockedFarDecisions, sum.enemyRemainDecisions),
    iceRate: rate(sum.iceDecisions, sum.decisions),
    iceTurnRate: rate(sum.iceTurnDecisions, sum.iceDecisions),
    iceHoldRate: rate(sum.iceHoldDecisions, sum.iceDecisions),
    decisionEventRate: rate(sum.decisionEvents, sum.decisionN),
  }
  if (outPath) appendFileSync(outPath, JSON.stringify(summary) + '\n')
  else process.stdout.write(lines.join('\n') + '\n' + JSON.stringify(summary) + '\n')
  console.error(
    `[pos-dose] summary games=${summary.games} c1=${c1Weight} c4=${c4Bias} e1=${e1Bias} skipSteel=${skipSteelOnlyShots ? 1 : 0} breakBricks=${breakBricksOnPath ? 1 : 0} releaseReverseIce=${releaseReverseOnIce ? 1 : 0} rules=${ruleTag || 'none'} ruleApply=${summary.ruleApplications} wall=${wall.toFixed(1)}s ` +
      `win=${(summary.winRate * 100).toFixed(2)}% ` +
      `threat=${(summary.threatRate * 100).toFixed(2)}% ` +
      `shield=${(summary.shieldRate * 100).toFixed(2)}% freeze=${(summary.freezeRate * 100).toFixed(2)}% ` +
      `c4Opp=${(summary.c4OpportunityRate * 100).toFixed(2)}% steelOnly=${(summary.steelOnlyShotRate * 100).toFixed(2)}% ` +
      `brickBlock=${(summary.brickBlockedRate * 100).toFixed(2)}% brickAhead=${(summary.brickAheadRate * 100).toFixed(2)}% ` +
      `brickOpp=${(summary.brickFireOpportunityRate * 100).toFixed(2)}% ` +
      `iceGlide=${(summary.iceGlideRate * 100).toFixed(2)}% iceRev=${(summary.iceReverseRate * 100).toFixed(2)}% ` +
      `iceRevOpp=${(summary.iceReverseOpportunityRate * 100).toFixed(2)}% ` +
      `farEnemy=${(summary.farEnemyRate * 100).toFixed(1)}% blocked=${(summary.blockedFacingRate * 100).toFixed(1)}% ` +
      `ice=${(summary.iceRate * 100).toFixed(2)}% events=${(summary.decisionEventRate * 100).toFixed(2)}%`,
  )
}

if (import.meta.main) main()
