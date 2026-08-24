/**
 * collect-godai.ts — God-AI 作为「先行教师」采集 (obs, action) 数据集 (plan P1.5).
 *
 * 目的：在 RL 教师落地前，用现有 God-AI（hard ~0.73-0.77 胜率）驱动 headless
 * 仿真，在每个决策 tick（K=10，与 NN 玩家同节拍）编码 obs+scalars，并提取
 * God-AI 的 (move/fire/item) 动作标签，写入与 BC 管线完全兼容的 npy shards
 * （obs/14×26×26 u1, scalars/24 f4, actions/3 u1, masks/10 u1, conditions/1 u1）。
 *
 * 下游：nn-training/distill_godai.py 用这些 shard 训练小 ConvMixer 学生，验证
 * 「小模型可表征 God-AI 策略」这一蒸馏管线（动作准确率保留率）。胜率保留率
 * 的端到端验收待 P4（infer.ts 支持 ConvMixer）后由 TS 仿真回归。
 *
 * 注意：动作标签完全复用 export-observations.ts 的规范（decisionTick +
 * actionFromFrame + computeMasks），保证与 BC 训练数据同源同构。item 为边沿
 * 标签（guard/frenzy 为一次性激活），与 BC replay 标注一致——item 稀疏是已知
 * 特性，不影响 move/fire 主导信号的验证。
 *
 * Usage:
 *   bun tools/sim/collect-godai.ts --out tmp/godai --difficulty hard \
 *       --stages 0-4 --seeds 0-9
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { RNG } from '../../src/utils/RNG'
import { START_LIVES } from '../../src/constants'
import {
  ObsEncoder,
  decisionTick,
  actionFromFrame,
  computeMasks,
  OBS_SCHEMA_MAJOR,
} from '../../src/nn/obs-encoder'
import { writeShard } from '../../src/nn/npy'
import { writeFileSync, mkdirSync } from 'fs'

const MAX_TICKS = 36000
const K = 10

interface Acc {
  obs: Uint8Array[]
  scalars: Float32Array[]
  actions: number[]
  masks: number[]
  conditions: number[]
  n: number
}

function newAcc(): Acc {
  return { obs: [], scalars: [], actions: [], masks: [], conditions: [], n: 0 }
}

type Outcome = 'stage_clear' | 'base_destroyed' | 'lives_exhausted' | 'timeout'

function runOne(
  stageIdx: number,
  stage: any,
  seed: number,
  difficulty: string,
  maxTicks: number,
): {
  acc: Acc
  outcome: Outcome
  ticks: number
} {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(stage, stageIdx)
  input.reset()

  const encoder = new ObsEncoder()
  const acc = newAcc()
  let prevDir: any = null
  let prevGuard = false
  let prevFrenzy = false
  let t = 0
  let outcome: Outcome = 'timeout'

  while (t < maxTicks) {
    // obs(t): 决策前的世界状态（God-AI 本 tick 看到的）
    encoder.encode(world)
    sim.tick()
    // 本 tick 决策（forensics 同款采样点：tick 后、endFrame 前）
    const dir = input.getMoveDirection()
    const firing = input.isFiring()
    const g = input.wasItemPressed('guard')
    const fr = input.wasItemPressed('frenzy')

    const { isDecision, condition } = decisionTick(
      t,
      world,
      prevDir,
      dir,
      prevGuard,
      g,
      prevFrenzy,
      fr,
      K,
    )
    if (isDecision) {
      const label = actionFromFrame({ direction: dir, firing, guard: g, frenzy: fr })
      const masks = computeMasks(world)
      acc.obs.push(encoder.obs.slice())
      acc.scalars.push(encoder.scalars.slice())
      acc.actions.push(label.move, label.fire, label.item)
      acc.masks.push(...masks.move, ...masks.fire, ...masks.item)
      acc.conditions.push(condition)
      acc.n++
    }

    input.endFrame()
    prevDir = dir
    prevGuard = g
    prevFrenzy = fr
    t++

    const st: string = world.state
    if (st === 'stageclear' || st === 'victory') {
      outcome = 'stage_clear'
      break
    }
    if (st === 'gameover') {
      outcome = world.tileMap.isBaseDestroyed() ? 'base_destroyed' : 'lives_exhausted'
      break
    }
  }

  return { acc, outcome, ticks: t }
}

function flushShard(acc: Acc, dir: string, manifest: unknown): void {
  const N = acc.n
  if (N === 0) return
  const obs = new Uint8Array(N * 14 * 26 * 26)
  const scalars = new Float32Array(N * 24)
  const actions = new Uint8Array(N * 3)
  const masks = new Uint8Array(N * 10)
  const conditions = new Uint8Array(N)
  for (let i = 0; i < N; i++) {
    obs.set(acc.obs[i], i * 14 * 26 * 26)
    scalars.set(acc.scalars[i], i * 24)
    actions[i * 3] = acc.actions[i * 3]
    actions[i * 3 + 1] = acc.actions[i * 3 + 1]
    actions[i * 3 + 2] = acc.actions[i * 3 + 2]
    for (let j = 0; j < 10; j++) masks[i * 10 + j] = acc.masks[i * 10 + j]
    conditions[i] = acc.conditions[i]
  }
  writeShard(dir, { obs, scalars, actions, masks, conditions }, manifest)
}

function parseRange(s: string): number[] {
  const out: number[] = []
  for (const part of s.split(',')) {
    if (part.includes('-')) {
      const [a, b] = part.split('-').map((x) => parseInt(x, 10))
      for (let i = a; i <= b; i++) out.push(i)
    } else out.push(parseInt(part, 10))
  }
  return out
}

function main(): void {
  const args = process.argv.slice(2)
  let outDir = 'tmp/godai'
  let difficulty = 'hard'
  let stagesStr = '0-4'
  let seedsStr = '0-9'
  let maxTicks = MAX_TICKS
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--out') outDir = args[++i]
    else if (args[i] === '--difficulty') difficulty = args[++i]
    else if (args[i] === '--stages') stagesStr = args[++i]
    else if (args[i] === '--seeds') seedsStr = args[++i]
    else if (args[i] === '--max-ticks') maxTicks = parseInt(args[++i], 10)
  }
  const stages = parseRange(stagesStr)
  const seeds = parseRange(seedsStr)
  mkdirSync(outDir, { recursive: true })

  const outcomes: Record<string, number> = {}
  let totalSamples = 0
  let totalTicks = 0
  const perGame: string[] = []

  for (const si of stages) {
    const stage = STAGES[si]
    if (!stage) {
      perGame.push(`[SKIP] stage ${si}: not found`)
      continue
    }
    for (const seed of seeds) {
      const { acc, outcome, ticks } = runOne(si, stage, seed, difficulty, maxTicks)
      outcomes[outcome] = (outcomes[outcome] ?? 0) + 1
      const shardName = `godai_s${si}_seed${seed}`
      const manifest = {
        schemaMajor: OBS_SCHEMA_MAJOR,
        teacher: 'GodAI',
        difficulty,
        stage: si,
        seed,
        outcome,
        ticks,
        nSamples: acc.n,
        k: K,
      }
      if (acc.n > 0) flushShard(acc, `${outDir}/${shardName}`, manifest)
      totalSamples += acc.n
      totalTicks += ticks
      perGame.push(`[OK] s${si} seed${seed} samples=${acc.n} outcome=${outcome} ticks=${ticks}`)
    }
  }

  const total = seeds.length * stages.length
  const wins = outcomes['stage_clear'] ?? 0
  const winRate = total > 0 ? wins / total : 0
  const summary = {
    difficulty,
    stages,
    seeds,
    games: total,
    outcomes,
    winRate: +winRate.toFixed(4),
    totalSamples,
    totalTicks,
  }
  console.log(perGame.join('\n'))
  console.log(`\n=== God-AI teacher collection (P1.5) ===`)
  console.log(`games=${total} outcomes=${JSON.stringify(outcomes)} winRate=${winRate.toFixed(4)}`)
  console.log(`totalSamples=${totalSamples} totalTicks=${totalTicks}`)
  console.log(`shards under: ${outDir}`)
  writeFileSync(`${outDir}/_collect_report.json`, JSON.stringify(summary, null, 2))
}

if (import.meta.main) main()
