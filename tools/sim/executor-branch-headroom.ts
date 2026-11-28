/**
 * executor-branch-headroom.ts — P′-2b1：executor→executor 单拍 headroom（零训练）。
 *
 * 问题：在 **E2′ 执行器自己的轨迹**上（`intent-option`：oracle 全链给 option，exec 走
 * `nnOption` 委托子链），把某一拍的动作替换成 24 个动作之一 / 把当前 option 换成 8 类
 * 之一并保持 H 拍，能比执行器实际行为好多少？这是 P′-4b 低层 residual 的 headroom
 * （plan/nn-hierarchical-options.plan.md §9.5）。
 *
 * 契约：
 *   · 动作臂：强制拍走正式钩子 `ForcedTickInput`（`decideFirst: true` —— inner 的
 *     决策状态照常推进，只覆盖对外动作；executor 有 tracker，跳过一拍 think 会分叉）；
 *   · option 臂：`execBranch`（同种子 GodAIInput）从 `st.exec` 恢复，注入 branched
 *     option 并**保持 H 拍**（不跑 oracle 重规划）—— 基线与候选都同法；
 *   · 每分支 = `cloneWorld` + `IntentOptionProbe.captureDecisionState` 起跑，结束
 *     `restoreWorld` + `restoreDecisionState`（CRN：世界 RNG 共享快照；执行器 RNG
 *     同种子独立）；
 *   · self-check：实际动作的强制分支必须与不强制分支在 H 拍后逐字段一致；
 *   · 评分主读数 = Σ γ^k·ΔΦ + γ^H·V(s_H)（Φ 同 `tools/sim/rl-reward.ts`；V 用 NN 的
 *     value 头做 bootstrap，与 b0 同一权重）；副读数 = raw terminal（无终局 = 0）。
 *   · 状态分层（opening_300 / base_pressure / ring_enemy / combat / free_nav）配额采样。
 *
 * 语料纪律（AGENTS §15）：pilot 与 confirm 用不相交的 seed 段（`--seed0` 推移），
 * 且不复用 b0 已用过的 seed 段。
 *
 * Usage:
 *   bun tools/sim/executor-branch-headroom.ts --weights <file> --states 500 --horizon 60 \
 *       --out tmp/p2b1/pilot-h60.jsonl --seed0 1000000031 --games 20
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, ENEMIES_PER_STAGE } from '../../src/constants'
import type { Direction } from '../../src/constants'
import { IntentOptionProbe } from '../../src/nn/intent-option-probe'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import type { NNOptionCommand } from '../../src/ai/god/NNOption'
import { INTENT_IDS } from '../../src/ai/intent/vocab'
import { NNInput } from '../../src/nn/policy-input'
import { MOVE4_DECODE } from '../../src/nn/action-space-v6'
import { sampleBasePressure } from '../../src/nn/danger-metrics'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import type { InputLike } from '../../src/game/Input'
import { RNG } from '../../src/utils/RNG'
import { executorSeed } from './simulation-runner'
import { ForcedTickInput } from './forced-input'
import { BASE_LOSS_MULT, RL_GAMMA, REWARD_SCALE, countBaseWall } from './rl-reward'
import { scoreRun, V7_SCORE_CONFIG } from '../eval/godai-score'
import {
  ACTION_COUNT,
  STRATA,
  classify,
  meanCi,
  outcomeOf,
  phiOf,
  worldSig,
  type Stratum,
  type Tel,
} from './nn-branch-headroom'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

const MAX_TICKS = 36000
const DECISION_K = 10
const PRESSURE_SAMPLE_TICKS = 6
const OPTION_SET: ReadonlySet<string> = new Set(['nnOption'])

/** 第一拍实际动作（control 分支录制；用于动作编码与 self-check）。 */
interface ActionRecord {
  moveDir: Direction | null
  fire: boolean
  holding: boolean
  fireBeforeTurn: boolean
}

/** 只代理 + 录制第一拍动作（其余语义原样透传）。 */
class RecordingInput implements InputLike {
  readonly first: ActionRecord = {
    moveDir: null,
    fire: false,
    holding: false,
    fireBeforeTurn: false,
  }
  private recorded = false

  constructor(private readonly inner: InputLike) {}

  getMoveDirection(): Direction | null {
    const d = this.inner.getMoveDirection()
    if (!this.recorded) this.first.moveDir = d
    return d
  }

  isFiring(): boolean {
    const f = this.inner.isFiring()
    if (!this.recorded) this.first.fire = f
    return f
  }

  isHolding(): boolean {
    const h = this.inner.isHolding?.() ?? false
    if (!this.recorded) this.first.holding = h
    return h
  }

  isFireBeforeTurn(): boolean {
    const b = this.inner.isFireBeforeTurn?.() ?? false
    if (!this.recorded) this.first.fireBeforeTurn = b
    return b
  }

  usesHoldFire3(): boolean {
    return this.inner.usesHoldFire3?.() ?? false
  }

  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    return this.inner.wasItemPressed(kind)
  }

  endFrame(): void {
    this.recorded = true
    this.inner.endFrame()
  }

  reset(): void {
    this.inner.reset()
  }
}

export function actionIndexOf(rec: ActionRecord): number {
  // 执行器口径：hold=1 ⇔ 不移动（moveDir=null）；hold=0 ⇔ 沿 moveIdx 移动。
  const hold = rec.moveDir === null ? 1 : 0
  const moveIdx = rec.moveDir ? MOVE4_DECODE.indexOf(rec.moveDir) : 0
  const fireIdx = rec.fire ? (rec.fireBeforeTurn ? 1 : 2) : 0
  return hold * 12 + Math.max(0, moveIdx) * 3 + fireIdx
}

/**
 * 执行器 24 动作编码（与 b0 的 NN 口径同 factorization，但 hold 的语义按执行器解释）：
 *   hold=1 ⇒ `moveDir=null`（原地不动；GodAIInput 的 hold 输出就是 null，且 Simulation
 *   不消费 isHolding）；hold=0 ⇒ 沿 move4 之一移动。fire3 同 b0。
 * b0 的 `actionOf` 把 hold=1 仍展开成方向 —— 对 NN 有效，对 GodAIInput 会让
 * 「强制实际动作 ≡ 不强制」自检在 null-move 拍分叉。
 */
export function executorActionOf(index: number): {
  moveDir: Direction | null
  fire: boolean
  holding: boolean
  fireBeforeTurn: boolean
} {
  const holdIdx = Math.floor(index / 12)
  const rem = index % 12
  const moveIdx = Math.floor(rem / 3)
  const fireIdx = rem % 3
  return {
    moveDir: holdIdx === 1 ? null : (MOVE4_DECODE[moveIdx] as Direction),
    fire: fireIdx !== 0,
    fireBeforeTurn: fireIdx === 1,
    holding: holdIdx === 1,
  }
}

/** option 分支命令：只换 option（targetKind='none' = 纯委托，与 probe 同口径）。 */
function optionCmd(index: number): NNOptionCommand {
  return {
    option: INTENT_IDS[index],
    targetKind: 'none',
    target: null,
    pathMode: 'direct',
    param: 0,
  }
}

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

function flag(name: string): boolean {
  return process.argv.includes(`--${name}`)
}

interface BranchResult {
  score: number
  termScore: number
  v: number | null
  terminal: string | null
  kills: number
  deaths: number
  wallLoss: number
  sig: string
}

interface SampleRow {
  stage: number
  seed: number
  tick: number
  stratum: Stratum
  actualAction: number
  bestAction: number
  hrAction: number
  hrActionTerm: number
  actualOption: number | null
  bestOption: number | null
  hrOption: number | null
  hrOptionTerm: number | null
  vActual: number | null
  scoresAction: number[]
  scoresOption: number[]
  termScoresAction: number[]
  termScoresOption: number[]
}

interface RunOpts {
  horizon: number
  gamma: number
  maxTicks: number
  difficulty: string
  playerLevel: number
  weightsPath: string
  quotas: Record<Stratum, number>
  used: Record<Stratum, number>
  maxStatesPerGame: number
  selfCheck: boolean
}

/** 单局：执行器基线跑图 + 采样点做动作/option 分支。 */
function runGame(stageIndex: number, seed: number, opts: RunOpts): SampleRow[] {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = opts.difficulty
  world.difficulty = DIFFICULTIES[opts.difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[opts.difficulty] ?? DEFAULT_RULES
  world.playerLevel = opts.playerLevel
  world.lives = world.difficulty?.startLives ?? START_LIVES

  const probe = new IntentOptionProbe(world, { seed })
  const execBranch = new GodAIInput(
    world,
    { ...DEFAULT_GOD_AI_PARAMS },
    new RNG(executorSeed(seed)),
  )
  const vProbe = new NNInput(world, { weightsPath: opts.weightsPath, decisionK: DECISION_K })
  const sim = new Simulation(world, probe)
  const stage = STAGES[stageIndex]
  world.loadStageData(stage, stageIndex)
  probe.reset()
  execBranch.reset()
  vProbe.reset()

  const rows: SampleRow[] = []
  const tel: Tel = {
    enemyTotal: stage.enemyCount ?? ENEMIES_PER_STAGE,
    startLives: world.lives,
    playerShots: 0,
    powerUpsSpawned: 0,
    powerUpsCollected: 0,
    baseWallTotal: countBaseWall(world),
    basePressureSum: 0,
    basePressureSamples: 0,
    firstKillTick: undefined,
  }
  const seenPowerUps = new Set<number>()
  let statesThisGame = 0

  for (let t = 0; t < opts.maxTicks; t++) {
    if (world.state !== 'playing') break
    sim.tick()
    probe.endFrame()
    const events = world.consumeEvents()
    for (const e of events) {
      if (e.type === 'bullet_fired' && e.bullet.isPlayer) tel.playerShots++
      else if (e.type === 'powerup_collected') tel.powerUpsCollected++
      else if (e.type === 'tank_destroyed' && e.by === 'player' && tel.firstKillTick === undefined)
        tel.firstKillTick = world.frame
    }
    for (let i = 0; i < world.powerUps.length; i++) seenPowerUps.add(world.powerUps[i].id)
    tel.powerUpsSpawned = seenPowerUps.size
    if (world.frame % PRESSURE_SAMPLE_TICKS === 0) {
      tel.basePressureSum += sampleBasePressure(world)
      tel.basePressureSamples++
    }
    if (world.state !== 'playing') break

    const strat = classify(world)
    if (opts.used[strat] >= opts.quotas[strat]) continue
    if (statesThisGame >= opts.maxStatesPerGame) continue
    if (world.frame % DECISION_K !== 0) continue

    // 采样点：本拍事件已消费；分支 restore 清事件不会丢 telemetry。
    const snap = cloneWorld(world)
    const st = probe.captureDecisionState()
    const baseCmd = probe.readOptionCommand()

    const runBranch = (
      arm: 'action' | 'option',
      index: number,
      force: boolean,
      record = false,
    ): { res: BranchResult; rec: ActionRecord | null } => {
      restoreWorld(world, snap)
      probe.restoreDecisionState(st)
      let rec: ActionRecord | null = null

      if (arm === 'option') {
        execBranch.restoreDecisionState(st.exec)
        execBranch._optionCmd = index >= 0 ? optionCmd(index) : baseCmd
        execBranch._candidateOverride = OPTION_SET
      }

      const telB: Tel = { ...tel }
      let prevPhi = phiOf(telB, world)
      let ret = 0
      let termRet = 0
      let stepped = 0
      let terminal: string | null = null
      let wallNow = countBaseWall(world)
      let wallLoss = 0
      const kills0 = world.killCount
      const lives0 = world.lives

      for (let k = 0; k < opts.horizon; k++) {
        if (arm === 'action') {
          if (force && k === 0) {
            const forced = new ForcedTickInput(probe, executorActionOf(index), 1, {
              decideFirst: true,
            })
            sim.input = forced
            sim.tick()
            forced.endFrame()
            sim.input = probe
          } else if (record && k === 0) {
            const recorder = new RecordingInput(probe)
            sim.input = recorder
            sim.tick()
            recorder.endFrame()
            rec = { ...recorder.first }
            sim.input = probe
          } else {
            sim.tick()
            probe.endFrame()
          }
        } else {
          sim.input = execBranch
          sim.tick()
          execBranch.endFrame()
        }
        stepped++

        const ev = world.consumeEvents()
        for (const e of ev) {
          if (e.type === 'bullet_fired' && e.bullet.isPlayer) telB.playerShots++
          else if (e.type === 'powerup_collected') telB.powerUpsCollected++
          else if (
            e.type === 'tank_destroyed' &&
            e.by === 'player' &&
            telB.firstKillTick === undefined
          )
            telB.firstKillTick = world.frame
        }
        if (world.frame % PRESSURE_SAMPLE_TICKS === 0) {
          telB.basePressureSum += sampleBasePressure(world)
          telB.basePressureSamples++
        }
        const wall = countBaseWall(world)
        if (wall < wallNow) {
          wallLoss += wallNow - wall
          wallNow = wall
        }
        const phi = phiOf(telB, world)
        ret += Math.pow(opts.gamma, k) * (phi - prevPhi)
        prevPhi = phi
        if (world.state !== 'playing') {
          terminal = outcomeOf(world)
          const scored = scoreRun(
            {
              outcome: terminal,
              ticks: world.frame,
              finalState: {
                killCount: world.killCount,
                lives: world.lives,
                baseAlive: !world.tileMap.isBaseDestroyed(),
              },
              firstKillTick: telB.firstKillTick,
            },
            V7_SCORE_CONFIG,
          )
          const gated = terminal === 'base_destroyed' ? scored.score * BASE_LOSS_MULT : scored.score
          termRet = REWARD_SCALE * gated
          ret += Math.pow(opts.gamma, stepped) * termRet
          break
        }
      }

      const vEnd = terminal === null ? vProbe.valueEstimate() : null
      if (vEnd !== null) ret += Math.pow(opts.gamma, stepped) * vEnd
      if (arm === 'option') sim.input = probe

      return {
        rec,
        res: {
          score: ret,
          termScore: termRet,
          v: vEnd,
          terminal,
          kills: world.killCount - kills0,
          deaths: Math.max(0, lives0 - world.lives),
          wallLoss,
          sig: worldSig(world),
        },
      }
    }

    // ---- 动作臂：control 录制实际动作，再跑 24 分支 ----
    const control = runBranch('action', -1, false, true)
    const actualAction = actionIndexOf(control.rec!)

    const scoresAction: number[] = []
    const termScoresAction: number[] = []
    let bestAction = 0
    let bestScore = -Infinity
    let actualActionRes: BranchResult | null = null
    for (let a = 0; a < ACTION_COUNT; a++) {
      const { res } = runBranch('action', a, true)
      scoresAction.push(res.score)
      termScoresAction.push(res.termScore)
      if (a === actualAction) actualActionRes = res
      if (res.score > bestScore) {
        bestScore = res.score
        bestAction = a
      }
    }
    if (opts.selfCheck && control.res.sig !== actualActionRes!.sig) {
      throw new Error(
        `[b1] self-check failed at stage ${stageIndex} seed ${seed} tick ${world.frame}: ` +
          `forced-actual != no-force`,
      )
    }

    // ---- option 臂（可选：尚未提交过 option 的窗口跳过） ----
    let actualOption: number | null = null
    let bestOption: number | null = null
    let hrOption: number | null = null
    let hrOptionTerm: number | null = null
    const scoresOption: number[] = []
    const termScoresOption: number[] = []
    if (baseCmd) {
      actualOption = INTENT_IDS.indexOf(baseCmd.option)
      const base = runBranch('option', -1, false)
      let bestOptScore = -Infinity
      let bestOpt = 0
      const baseTerm = base.res.termScore
      for (let o = 0; o < INTENT_IDS.length; o++) {
        const { res } = runBranch('option', o, true)
        scoresOption.push(res.score)
        termScoresOption.push(res.termScore)
        if (res.score > bestOptScore) {
          bestOptScore = res.score
          bestOpt = o
        }
      }
      bestOption = bestOpt
      hrOption = bestOptScore - base.res.score
      hrOptionTerm = Math.max(...termScoresOption) - baseTerm
    }

    // 恢复主现场（分支循环最后一次已 restore；再保险一次）。
    restoreWorld(world, snap)
    probe.restoreDecisionState(st)
    sim.input = probe

    rows.push({
      stage: stageIndex,
      seed,
      tick: world.frame,
      stratum: strat,
      actualAction,
      bestAction,
      hrAction: bestScore - scoresAction[actualAction],
      hrActionTerm: Math.max(...termScoresAction) - termScoresAction[actualAction],
      actualOption,
      bestOption,
      hrOption,
      hrOptionTerm,
      vActual: actualActionRes!.v,
      scoresAction,
      scoresOption,
      termScoresAction,
      termScoresOption,
    })
    opts.used[strat]++
    statesThisGame++
    if (STRATA.every((s) => opts.used[s] >= opts.quotas[s])) break
  }
  return rows
}

function main(): void {
  const weights = arg('weights')
  if (!weights) {
    process.stderr.write('[b1] --weights is required（V bootstrap 用 value 头）\n')
    process.exit(2)
  }
  const states = parseInt(arg('states', '500')!, 10)
  const horizon = parseInt(arg('horizon', '60')!, 10)
  const games = parseInt(arg('games', '20')!, 10)
  const seed0 = parseInt(arg('seed0', '1000000031')!, 10)
  const stageStart = parseInt(arg('stage-start', '2000')!, 10)
  const stageCount = parseInt(arg('stage-count', '35')!, 10)
  const maxStatesPerGame = parseInt(arg('max-states-per-game', '40')!, 10)
  const maxTicks = parseInt(arg('max-ticks', String(MAX_TICKS))!, 10)
  const difficulty = arg('difficulty', 'hard')!
  const playerLevel = parseInt(arg('player-level', '0')!, 10)
  const gamma = parseFloat(arg('gamma', String(RL_GAMMA))!)
  const out = arg('out', 'tmp/p2b1/b1.jsonl')!
  const selfCheck = !flag('no-self-check')
  if (!Number.isInteger(states) || states < 1 || !Number.isInteger(horizon) || horizon < 1) {
    process.stderr.write('[b1] --states/--horizon must be positive integers\n')
    process.exit(2)
  }
  const quota = Math.ceil(states / STRATA.length)
  const quotas = Object.fromEntries(STRATA.map((s) => [s, quota])) as Record<Stratum, number>
  const used = Object.fromEntries(STRATA.map((s) => [s, 0])) as Record<Stratum, number>

  const t0 = Date.now()
  const rows: SampleRow[] = []
  for (let g = 0; g < games; g++) {
    if (STRATA.every((s) => used[s] >= quotas[s])) break
    const stageIndex = stageStart - 2000 + (g % stageCount)
    const seed = seed0 + Math.floor(g / stageCount)
    const got = runGame(stageIndex, seed, {
      horizon,
      gamma,
      maxTicks,
      difficulty,
      playerLevel,
      weightsPath: weights,
      quotas,
      used,
      maxStatesPerGame,
      selfCheck,
    })
    rows.push(...got)
    process.stderr.write(
      `[b1] game ${g + 1}/${games} s${stageIndex + 2000} seed${seed}: +${got.length} states ` +
        `(${rows.length}/${states}) elapsed ${((Date.now() - t0) / 1000).toFixed(0)}s\n`,
    )
  }

  mkdirSync(dirname(out), { recursive: true })
  writeFileSync(out, rows.map((r) => JSON.stringify(r)).join('\n') + '\n', 'utf8')

  const z = states >= 1000 ? 1.96 : 1.645 // 筛查 90% / 确认 95%（预注册 p2b）
  const byStratum: Record<string, unknown> = {}
  for (const s of STRATA) {
    const sub = rows.filter((r) => r.stratum === s)
    const withOption = sub.filter((r) => r.hrOption !== null)
    byStratum[s] = {
      action: {
        ...meanCi(
          sub.map((r) => r.hrAction),
          z,
        ),
        positiveRate:
          sub.length > 0
            ? +(sub.filter((r) => r.bestAction !== r.actualAction).length / sub.length).toFixed(4)
            : 0,
      },
      option:
        withOption.length > 0
          ? {
              ...meanCi(
                withOption.map((r) => r.hrOption as number),
                z,
              ),
              positiveRate: +(
                withOption.filter((r) => r.bestOption !== r.actualOption).length / withOption.length
              ).toFixed(4),
            }
          : null,
      meanV: meanCi(
        sub.map((r) => r.vActual ?? 0),
        z,
      ).mean,
    }
  }
  const optionRows = rows.filter((r) => r.hrOption !== null)
  const summary = {
    weights,
    states: rows.length,
    targetStates: states,
    horizon,
    gamma,
    seed0,
    games,
    strata: byStratum,
    overall: {
      action: meanCi(
        rows.map((r) => r.hrAction),
        z,
      ),
      actionTerminal: meanCi(
        rows.map((r) => r.hrActionTerm),
        z,
      ),
      option:
        optionRows.length > 0
          ? meanCi(
              optionRows.map((r) => r.hrOption as number),
              z,
            )
          : null,
      optionTerminal:
        optionRows.length > 0
          ? meanCi(
              optionRows.map((r) => r.hrOptionTerm as number),
              z,
            )
          : null,
      optionStates: optionRows.length,
    },
    elapsedSec: +((Date.now() - t0) / 1000).toFixed(1),
    statesPerSec: rows.length > 0 ? +(rows.length / ((Date.now() - t0) / 1000)).toFixed(2) : 0,
    out,
  }
  console.log(JSON.stringify(summary, null, 2))
}

if (import.meta.main) main()
