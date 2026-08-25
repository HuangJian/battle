/**
 * export-dagger-labels.ts — DAgger 在线蒸馏采集器 (plan P1.5, 修复射击漂移).
 *
 * 动机（来自 nn.progress 的 0% 复盘）：
 *   学生模型在「自己部署产生的状态」上预测 release 占主导（即便 ready=true
 *   也几乎不开火），而教师(God-AI)在「自己的状态」上 95% 开火。这是典型
 *   的模仿学习分布漂移——BC 只用「教师在教师状态下」的标号训练，学生一
 *   旦走出教师分布，就进入教师从未标号的 (state, fire) 区域。
 *
 * DAgger 修复（Ross et al. 2011）：
 *   用当前学生策略 π_θ 驱动仿真，采集学生「实际访问到的状态」s，再用教师
 *   B 对这些 s 重新标号 a_B(s)，将 {(s, a_B(s))} 并入训练集重训。关键是教师
 *   必须在「学生走过的真实轨迹」上保持一致记忆（path/camp/hunt-commit 都是
 *   逐帧演化的），所以本采集器：
 *     - 玩家由 NNInput（学生）驱动，是唯一改写 World 的输入；
 *     - GodAIInput（教师）每帧作为【纯观察者】think()+endFrame()，维护连贯
 *       记忆，但从不驱动 World；
 *     - 在每个决策 tick（K=10，与 BC 同节拍），在「决策前世界状态」上读取
 *       教师的 (move/fire/item) 动作作为标号——即对学生所见状态的教师动作。
 *
 * 输出与 collect-godai.ts（BC 教师采集）【完全同构】的 npy shards：
 *   obs/14×26×26 u1, scalars/19 f4, actions/2 u1, masks/7 u1, conditions/1 u1
 * （v2 = OBS_SCHEMA_MAJOR 2：item 头删除，actions/masks 缩减为 move+fire）
 * 因此 train_bc.py 只需把 --data-dir 指向同时含 godai/ + dagger/ 两个子目录
 * 的根目录即可混合重训（见文件头用法示例）。manifest 额外标注
 *   teacher='GodAI', collector='DAgger', policy='nn-student'
 * 以便后续分析区分数据来源。
 *
 * 注意：obs 在 sim.tick() 之前编码（= 学生模型本 tick 实际看到的状态）；教师
 * 标号也在同一决策前世界上读取。两者严格对齐，避免 1-tick 偏移歧义。
 *
 * Usage:
 *   bun tools/sim/export-dagger-labels.ts --out tmp/dagger --difficulty hard \
 *       --stages 0-4 --seeds 0-9 \
 *       --weights-dir tmp/student-weights-full
 *   # 混合重训：
 *   #   mkdir -p tmp/mix && cp -r tmp/godai/* tmp/mix/ && cp -r tmp/dagger/* tmp/mix/
 *   #   python nn-training/train_bc.py --data-dir tmp/mix --arch student \
 *   #       --out tmp/student-weights-dagger/weights.json
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
import { NNInput, type NNInputOptions } from '../../src/nn/policy-input'
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

interface RunResult {
  acc: Acc
  outcome: Outcome
  ticks: number
  // 学生驱动下的结果（用于报告学生实际访问了多少状态才结束）
  studentWin: boolean
}

function runOne(
  stageIdx: number,
  stage: any,
  seed: number,
  difficulty: string,
  maxTicks: number,
  nnOpts: NNInputOptions,
): RunResult {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  // 教师：与 BC 采集同种子的独立 RNG，保证可复现（不消耗 world.rng）。
  const teacherRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const teacher = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, teacherRng)
  // 学生：驱动 World 的唯一输入。
  const student = new NNInput(world, nnOpts)

  const sim = new Simulation(world, student)
  world.loadStageData(stage, stageIdx)
  teacher.reset()
  student.reset()

  const encoder = new ObsEncoder()
  const acc = newAcc()
  let prevDir: any = null
  let prevGuard = false
  let prevFrenzy = false
  let t = 0
  let outcome: Outcome = 'timeout'

  while (t < maxTicks) {
    // (1) 学生「决策前」的世界状态 = obs（与学生模型本 tick 看到的状态一致）。
    encoder.encode(world)

    // (2) 教师作为纯观察者，在【同一决策前世界】上给出标号。
    //     每帧 think 维护教师记忆连贯（path/camp/hunt-commit 逐帧演化），
    //     endFrame 推进其 per-tick 缓存；教师只读 World，从不改写。
    const tDir = teacher.getMoveDirection()
    const tFiring = teacher.isFiring()
    const tGuard = teacher.wasItemPressed('guard')
    const tFrenzy = teacher.wasItemPressed('frenzy')
    teacher.endFrame()

    // (3) 决策 tick 采样（与 BC 同节拍 K=10）。
    const { isDecision, condition } = decisionTick(
      t,
      world,
      prevDir,
      tDir,
      prevGuard,
      tGuard,
      prevFrenzy,
      tFrenzy,
      K,
    )
    if (isDecision) {
      const label = actionFromFrame({
        direction: tDir,
        firing: tFiring,
      })
      const masks = computeMasks(world)
      acc.obs.push(encoder.obs.slice())
      acc.scalars.push(encoder.scalars.slice())
      acc.actions.push(label.move, label.fire)
      acc.masks.push(...masks.move, ...masks.fire)
      acc.conditions.push(condition)
      acc.n++
    }

    // (4) 学生驱动 World 前进一步（学生内部在 sim.tick 内 think 并决策）。
    sim.tick()
    student.endFrame()

    // (5) bookkeeping
    prevDir = tDir
    prevGuard = tGuard
    prevFrenzy = tFrenzy
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

  const studentWin = outcome === 'stage_clear'
  return { acc, outcome, ticks: t, studentWin }
}

function flushShard(acc: Acc, dir: string, manifest: unknown): void {
  const N = acc.n
  if (N === 0) return
  const obs = new Uint8Array(N * 14 * 26 * 26)
  const scalars = new Float32Array(N * 19)
  const actions = new Uint8Array(N * 2)
  const masks = new Uint8Array(N * 7)
  const conditions = new Uint8Array(N)
  for (let i = 0; i < N; i++) {
    obs.set(acc.obs[i], i * 14 * 26 * 26)
    scalars.set(acc.scalars[i], i * 19)
    actions[i * 2] = acc.actions[i * 2]
    actions[i * 2 + 1] = acc.actions[i * 2 + 1]
    for (let j = 0; j < 7; j++) masks[i * 7 + j] = acc.masks[i * 7 + j]
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
  let outDir = 'tmp/dagger'
  let difficulty = 'hard'
  let stagesStr = '0-4'
  let seedsStr = '0-9'
  let maxTicks = MAX_TICKS
  let weightsDir = 'tmp/student-weights-full'
  let weightsPath: string | undefined
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--out') outDir = args[++i]
    else if (args[i] === '--difficulty') difficulty = args[++i]
    else if (args[i] === '--stages') stagesStr = args[++i]
    else if (args[i] === '--seeds') seedsStr = args[++i]
    else if (args[i] === '--max-ticks') maxTicks = parseInt(args[++i], 10)
    else if (args[i] === '--weights-dir') weightsDir = args[++i]
    else if (args[i] === '--weights') weightsPath = args[++i]
  }
  const stages = parseRange(stagesStr)
  const seeds = parseRange(seedsStr)
  mkdirSync(outDir, { recursive: true })

  const nnOpts: NNInputOptions = { weightsDir, weightsPath }
  const outcomes: Record<string, number> = {}
  let totalSamples = 0
  let totalTicks = 0
  let studentWins = 0
  const perGame: string[] = []

  for (const si of stages) {
    const stage = STAGES[si]
    if (!stage) {
      perGame.push(`[SKIP] stage ${si}: not found`)
      continue
    }
    for (const seed of seeds) {
      const { acc, outcome, ticks, studentWin } = runOne(
        si,
        stage,
        seed,
        difficulty,
        maxTicks,
        nnOpts,
      )
      outcomes[outcome] = (outcomes[outcome] ?? 0) + 1
      if (studentWin) studentWins++
      const shardName = `dagger_s${si}_seed${seed}`
      const manifest = {
        schemaMajor: OBS_SCHEMA_MAJOR,
        teacher: 'GodAI',
        collector: 'DAgger',
        policy: 'nn-student',
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
      perGame.push(
        `[OK] s${si} seed${seed} samples=${acc.n} outcome=${outcome} ticks=${ticks} studentWin=${studentWin}`,
      )
    }
  }

  const total = seeds.length * stages.length
  const winRate = total > 0 ? studentWins / total : 0
  const summary = {
    collector: 'DAgger',
    teacher: 'GodAI',
    policy: 'nn-student',
    difficulty,
    stages,
    seeds,
    games: total,
    studentWinRate: +winRate.toFixed(4),
    outcomes,
    totalSamples,
    totalTicks,
  }
  console.log(perGame.join('\n'))
  console.log(`\n=== DAgger online-distillation collection (P1.5) ===`)
  console.log(
    `games=${total} studentWinRate=${winRate.toFixed(4)} outcomes=${JSON.stringify(outcomes)}`,
  )
  console.log(`totalSamples=${totalSamples} totalTicks=${totalTicks}`)
  console.log(`shards under: ${outDir}  (mix with godai shards, then train_bc.py --arch student)`)
  writeFileSync(`${outDir}/_dagger_report.json`, JSON.stringify(summary, null, 2))
}

if (import.meta.main) main()
