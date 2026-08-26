/**
 * export-eval-game.ts — 干净评估：固定语料贪心单局 runner（RL checkpoint 实力评分）。
 *
 * 为什么是独立文件而不是给 export-rl-rollout.ts 加 --greedy：
 *   codeHash 红线（plan/distributed-rollup v3.3 M4）覆盖 `src/nn/**` +
 *   `tools/sim/export-rl-rollout.ts`，trainer 与 agent 逐字节一致才准入 rollout。
 *   动采集脚本会让全部远程节点在同步代码前被剔除（rollout 塌缩成本机）。
 *   本文件不在哈希集内 —— 旧 agent 忽略 eval 任务（ping 无 evalSupport 即跳过），
 *   新 agent 同步后逐节点灰度点亮，采集主链路零影响。
 *
 * 与训练 rollout 的语义差异（有意为之）：
 *   - 动作 = 掩码 argmax（无探索噪声）；整局零随机决策 → 同 (权重, 关, seed) 逐 tick 确定。
 *   - 只写 `_eval_report.json`（outcome/ticks/win/score/dims），不产 trajectory shards。
 *   - 打分 = 纯 v7（V7_SCORE_CONFIG 原值，不做 F3 门控、不败局带剔 lives）
 *     ——与 God-AI 全部基线可比（评估口径 godai-score.ts 保持不动）。
 *
 * 口径同步契约：telemetry 采样节拍/事件判据/维度定义复制自 export-rl-rollout.ts
 * （其本身逐字段对齐 simulation-runner.ts）。若打分口径变更，三个文件须双侧同步，
 * 否则跨版本比较失效。
 *
 * Usage:
 *   bun tools/sim/export-eval-game.ts --weights <weights.json> \
 *       --stage 7 --seed 860001 --difficulty hard --max-ticks 12000 \
 *       --wver <sha> [--node-label self] --out <gameDir>
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, ENEMIES_PER_STAGE, BASE_POS, CELL, GRID } from '../../src/constants'
import { type Direction } from '../../src/constants'
import { ObsEncoder, computeMasks } from '../../src/nn/obs-encoder'
import { buildModelFromText } from '../../src/nn/infer'
import { IntentExecutor } from '../../src/nn/intent-executor'
import { RNG } from '../../src/utils/RNG'
import { writeFileSync, mkdirSync, readFileSync } from 'fs'
import { scoreRun, V7_SCORE_CONFIG, type DimensionKey } from '../eval/godai-score'
import type { RunTelemetry } from './simulation-runner'
import { buildPack } from './pack-container'

const MAX_TICKS = 36000
const K = 10
const TELEMETRY_SAMPLE_TICKS = 6
const BASE_PRESSURE_RADIUS = 12

const MOVE_DECODE: Direction[] = ['up', 'down', 'left', 'right']

/** 受控输入：与 export-rl-rollout.ScriptedInput 同实现（动作施加门控一致）。 */
class ScriptedInput {
  moveDir: Direction | null = null
  lastDir: Direction = 'up'
  firing = false

  setAction(move: number, fire: number): void {
    if (move === 0) this.moveDir = this.lastDir
    else {
      this.lastDir = MOVE_DECODE[move - 1]
      this.moveDir = this.lastDir
    }
    this.firing = fire === 1
  }

  getMoveDirection(): Direction | null {
    return this.moveDir
  }

  isFiring(): boolean {
    return this.firing
  }

  wasItemPressed(): false {
    return false
  }

  endFrame(): void {
    // no per-tick pulses in v2
  }

  reset(): void {
    this.moveDir = null
    this.lastDir = 'up'
    this.firing = false
  }
}

interface RolloutModel {
  forward(obs: Uint8Array, scalars: Float32Array): void
  readonly moveLogits: Float32Array
  readonly fireLogits: Float32Array
  readonly valueOut: Float32Array
}

/** 掩码 argmax：并列取最小索引（确定性）；全掩码时与 sampleCat 同退化为末位。 */
function argmaxCat(logits: Float32Array, mask: number[] | null): number {
  let best = -Infinity
  let bi = logits.length - 1
  for (let i = 0; i < logits.length; i++) {
    const v = mask && mask[i] !== 1 ? -1e9 : logits[i]
    if (v > best) {
      best = v
      bi = i
    }
  }
  return bi
}

// ---- per-game telemetry（逐字段复制自 export-rl-rollout.ts，勿单独演化）----
interface Telemetry {
  enemyTotal: number
  startLives: number
  playerDeaths: number
  playerShots: number
  powerUpsSpawned: number
  powerUpsCollected: number
  starsCollected: number
  baseWallTotal: number
  baseWallIntact: number
  basePressureSum: number
  basePressureSamples: number
  cellsVisited: Set<number>
  firstKillTick: number | undefined
}

function countBaseWall(world: World): number {
  // 与 runner 的 countBaseWall 同一环格定义（8 格保护圈，brick/steel 算完整）。
  const bc = BASE_POS.col
  const br = BASE_POS.row
  let n = 0
  for (let col = bc - 1; col <= bc + 2; col++) {
    if (isSolid(world, col, br - 1)) n++
  }
  for (let row = br; row <= br + 1; row++) {
    if (isSolid(world, bc - 1, row)) n++
    if (isSolid(world, bc + 2, row)) n++
  }
  return n
}

function isSolid(world: World, col: number, row: number): boolean {
  const t = world.tileMap.get(col, row)
  return t === 'brick' || t === 'steel'
}

function sampleBasePressure(world: World): number {
  if (!world.tileMap.hasBase()) return 0
  let worst = 0
  for (const t of world.tanks) {
    if (!t.alive || t.spawnTimer > 0) continue
    const col = Math.floor((t.x + t.w / 2) / CELL)
    const row = Math.floor((t.y + t.h / 2) / CELL)
    const dist = Math.abs(col - BASE_POS.col) + Math.abs(row - BASE_POS.row)
    const p = 1 - dist / BASE_PRESSURE_RADIUS
    if (p > worst) worst = p
  }
  return worst > 0 ? Math.min(1, worst) : 0
}

interface EvalResult {
  outcome: string
  ticks: number
  win: boolean
  score: number
  quality: number
  dims: Record<string, { value: number | null; raw: number }>
  /** 完整 ScorableRun 输入（v3.7，供 m1-eval --dist-nodes 复用本地聚合零改动）。 */
  scorable: Record<string, unknown>
}

function runEvalOne(
  stageIdx: number,
  stage: any,
  seed: number,
  difficulty: string,
  maxTicks: number,
  weightsText: string,
  policy = 'nn',
  intentWeightsText = '',
): EvalResult {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  // v3.7：policy='intent-exec' → 意图执行器驱动（NN 选意图 + God-AI 白名单子链），
  // 替代 RL 学生模型贪心。intent-weights 经 --intent-weights 单独提供。
  const isIntent = policy === 'intent-exec'
  const model = isIntent ? null : (buildModelFromText(weightsText) as unknown as RolloutModel)
  const scripted = new ScriptedInput()
  let exec: IntentExecutor | null = null
  if (isIntent) {
    exec = new IntentExecutor(world, {
      weightsText: intentWeightsText,
      rng: new RNG((seed ^ 0x9e3779b9) >>> 0), // §47：执行器内部 God-AI 独立 RNG
    })
  }
  const sim = new Simulation(world, (exec ?? scripted) as any)
  world.loadStageData(stage, stageIdx)
  scripted.reset()

  const encoder = new ObsEncoder()

  const tel: Telemetry = {
    enemyTotal: (stage as any)?.enemyCount ?? ENEMIES_PER_STAGE,
    startLives: world.difficulty?.startLives ?? START_LIVES,
    playerDeaths: 0,
    playerShots: 0,
    powerUpsSpawned: 0,
    powerUpsCollected: 0,
    starsCollected: 0,
    baseWallTotal: countBaseWall(world),
    baseWallIntact: countBaseWall(world),
    basePressureSum: 0,
    basePressureSamples: 0,
    cellsVisited: new Set<number>(),
    firstKillTick: undefined,
  }
  const seenPuIds = new Set<number>()
  let prevLivePuIds = new Set<number>()

  let t = 0
  let outcome = 'timeout'

  while (t < maxTicks) {
    // v3.7：意图执行器每 tick 内部自决（replan 帧跑 NN），无需手动 forward。
    if (!isIntent) {
      encoder.encode(world)
      if (t % K === 0) {
        model!.forward(encoder.obs, encoder.scalars)
        const masks = computeMasks(world)
        const mv = argmaxCat(model!.moveLogits, masks.move)
        const fr = argmaxCat(model!.fireLogits, masks.fire)
        scripted.setAction(mv, fr)
      }
    }
    sim.tick()
    if (isIntent) exec!.endFrame()
    else scripted.endFrame()
    t++

    // ---- telemetry（语义对齐 simulation-runner / export-rl-rollout）----
    let collectedThisTick = 0
    for (const e of world.consumeEvents()) {
      if (e.type === 'tank_destroyed') {
        if ((e as any).by === 'player' && tel.firstKillTick === undefined) tel.firstKillTick = t - 1
        if ((e as any).tank?.isPlayer) tel.playerDeaths++
      } else if (e.type === 'bullet_fired' && (e as any).bullet?.isPlayer) {
        tel.playerShots++
      } else if (e.type === 'powerup_collected') {
        collectedThisTick++
        tel.powerUpsCollected++
        if ((e as any).powerUp === 'star') tel.starsCollected++
      }
    }
    // power-up census（seen-ids + same-tick pickup 对账，镜像 runner）
    {
      const live = new Set<number>()
      for (const pu of world.powerUps) {
        live.add(pu.id)
        if (!seenPuIds.has(pu.id)) {
          seenPuIds.add(pu.id)
          tel.powerUpsSpawned++
        }
      }
      let vanished = 0
      for (const id of prevLivePuIds) if (!live.has(id)) vanished++
      tel.powerUpsSpawned += Math.max(0, collectedThisTick - vanished)
      prevLivePuIds = live
    }
    if (t % TELEMETRY_SAMPLE_TICKS === 0) {
      tel.basePressureSum += sampleBasePressure(world)
      tel.basePressureSamples++
      if (world.player?.alive) {
        const col = Math.floor((world.player.x + world.player.w / 2) / CELL)
        const row = Math.floor((world.player.y + world.player.h / 2) / CELL)
        tel.cellsVisited.add(row * GRID + col)
      }
    }

    if (world.state === 'stageclear' || world.state === 'victory' || world.state === 'gameover') {
      outcome =
        world.state === 'gameover'
          ? world.tileMap.isBaseDestroyed()
            ? 'base_destroyed'
            : 'lives_exhausted'
          : 'stage_clear'
      break
    }
  }

  // ---- 纯 v7 打分（评估口径，无 F3 门控、败局带保留 lives）----
  const scorable = {
    outcome,
    ticks: t,
    finalState: {
      killCount: world.killCount,
      lives: world.lives,
      baseAlive: !world.tileMap.isBaseDestroyed(),
    },
    firstKillTick: tel.firstKillTick,
    telemetry: {
      enemyTotal: tel.enemyTotal,
      startLives: tel.startLives,
      playerDeaths: tel.playerDeaths,
      playerShots: tel.playerShots,
      powerUpsSpawned: tel.powerUpsSpawned,
      powerUpsCollected: tel.powerUpsCollected,
      starsCollected: tel.starsCollected,
      finalPlayerLevel: world.playerLevel,
      baseWallIntact: tel.baseWallIntact,
      baseWallTotal: tel.baseWallTotal,
      basePressureMean:
        tel.basePressureSamples > 0 ? tel.basePressureSum / tel.basePressureSamples : 0,
      basePressureSamples: tel.basePressureSamples,
      cellsVisited: tel.cellsVisited.size,
      deaths: [],
    } satisfies Omit<RunTelemetry, 'deaths'> & { deaths: never[] },
  } as any
  const scored = scoreRun(scorable, V7_SCORE_CONFIG)

  const dims: Record<string, { value: number | null; raw: number }> = {}
  for (const k of Object.keys(scored.dims) as DimensionKey[]) {
    dims[k] = { value: scored.dims[k].value, raw: scored.dims[k].raw }
  }
  return {
    outcome,
    ticks: t,
    win: outcome === 'stage_clear',
    score: scored.score,
    quality: scored.quality,
    dims,
    scorable: {
      outcome,
      ticks: t,
      finalState: scorable.finalState,
      firstKillTick: scorable.firstKillTick,
      telemetry: scorable.telemetry,
    },
  }
}

function main(): void {
  const t0 = Date.now()
  const argv = process.argv.slice(2)
  let outDir = 'tmp/eval-out'
  let difficulty = 'hard'
  let stageIdx = -1
  let seed = -1
  let maxTicks = MAX_TICKS
  let weightsPath = 'tmp/rl-weights/weights.json'
  let intentWeightsPath = ''
  let policy = 'nn'
  let wver = ''
  let nodeLabel = ''
  // --pack <path>（v3.6）：BCV2 容器输出，语义同 export-rl-rollout（无 shards、空 entries）。
  let packPath = ''
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--out') outDir = argv[++i]
    else if (argv[i] === '--difficulty') difficulty = argv[++i]
    else if (argv[i] === '--stage') stageIdx = parseInt(argv[++i], 10)
    else if (argv[i] === '--seed') seed = parseInt(argv[++i], 10)
    else if (argv[i] === '--max-ticks') maxTicks = parseInt(argv[++i], 10)
    else if (argv[i] === '--weights') weightsPath = argv[++i]
    else if (argv[i] === '--policy') policy = argv[++i]
    else if (argv[i] === '--intent-weights') intentWeightsPath = argv[++i]
    else if (argv[i] === '--wver') wver = argv[++i]
    else if (argv[i] === '--node-label') nodeLabel = argv[++i]
    else if (argv[i] === '--pack') packPath = argv[++i]
  }
  if (!Number.isInteger(stageIdx) || !Number.isInteger(seed)) {
    console.error('[export-eval-game] --stage/--seed required')
    process.exit(2)
  }
  const stage = STAGES[stageIdx]
  mkdirSync(outDir, { recursive: true })
  const weightsText = readFileSync(weightsPath, 'utf8')
  const intentWeightsText = intentWeightsPath ? readFileSync(intentWeightsPath, 'utf8') : ''
  const res = runEvalOne(
    stageIdx,
    stage,
    seed,
    difficulty,
    maxTicks,
    weightsText,
    policy,
    intentWeightsText,
  )
  const report = {
    collector: 'RL-eval',
    rewardScheme: 'v7-pure',
    difficulty,
    stage: stageIdx,
    seed,
    policy,
    outcome: res.outcome,
    ticks: res.ticks,
    win: res.win,
    score: res.score,
    quality: res.quality,
    dims: res.dims,
    scorable: res.scorable,
    ...(wver ? { wver, node: nodeLabel } : {}),
  }
  writeFileSync(`${outDir}/_eval_report.json`, JSON.stringify(report, null, 2))
  if (packPath) {
    // 溯源戳与 v1 agent 主线程所盖戳一致（mode='eval' 是 validate_eval_result 的对账项）。
    const packManifest = {
      ...report,
      mode: 'eval',
      elapsedSec: +((Date.now() - t0) / 1000).toFixed(1),
    }
    writeFileSync(packPath, buildPack(packManifest, []))
  }
  console.log(
    `[eval-game] s${stageIdx} seed${seed} outcome=${res.outcome} ticks=${res.ticks} ` +
      `win=${res.win} score=${res.score.toFixed(3)} kills=${res.dims.progress.raw}`,
  )
}

if (import.meta.main) main()
