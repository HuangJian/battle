/**
 * export-nn-replays.ts — write .replay files for the whole-world RL student
 * policy (buildModelFromText + masked argmax, v7-pure), on curriculum-arena or
 * real stages, driven by a rage/latest weights JSON.
 *
 * Faithful to export-eval-game.ts's 'nn' path (SAME decision cadence K, SAME
 * argmaxCat, SAME per-tick forward gating) so the replay's move/fire stream is
 * byte-identical to what the distributed eval/rollout actually played. It adds
 * InputRecorder recording on top and lands .replay via writeReplayFile.
 *
 * Standalone by design: export-eval-game.ts / export-rl-rollout.ts are
 * codeHash-pinned for distributed rollout — this tool is NOT part of that set,
 * so editing it never invalidates remote node sync.
 *
 * Usage (bun; deterministic — no Math.random, no wall-clock in the policy):
 *   bun run tools/sim/export-nn-replays.ts \
 *     --weights tmp/s3-cap2/weights.json \
 *     --out tmp/replays-nn-s3 \
 *     --difficulty hard --max-ticks 3600 \
 *     --run 1020:860001 --run 1021:860002 --run 1022:860003
 *
 * Stage ids: si >= 1000 resolved via ARENA_LADDER (S3 = 1020/1021/1022);
 * si < 1000 resolved as real STAGES[si]. stageIndex of the envelope is 0 for
 * arenas (same fix as export-rl-rollout — killScore scaling must not enter
 * arena replays).
 */

import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { allEnemiesCleared } from '../../src/game/SimulationEffects'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { isArenaId, resolveArenaStage, arenaLevelOfId } from '../../src/nn/arena-ladder'
import { START_LIVES, ENEMIES_PER_STAGE } from '../../src/constants'
import { ObsEncoder, computeMasks } from '../../src/nn/obs-encoder'
import { buildModelFromText, type ModelLike } from '../../src/nn/infer'
import type { InputLike } from '../../src/game/Input'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { InputRecorder } from '../../src/replay/InputRecorder'
import { serializeReplayFile, buildReplayFilename } from '../../src/replay/file'
import type { SimOutcome } from './simulation-runner'
import { createHash } from 'node:crypto'
import { writeFileSync, readFileSync, mkdirSync, existsSync } from 'fs'

const K = 10

/** 掩码 argmax：并列取最小索引（确定性）；全掩码时退化为末位（对齐 eval-game）。 */
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

function buildModel(weightsText: string): ModelLike {
  return buildModelFromText(weightsText)
}

interface RunOut {
  outcome: SimOutcome
  cleared: boolean
  ticks: number
  killCount: number
  lives: number
  playerLevel: number
  replay: { initialSnapshot: unknown; frames: Uint8Array; tickCount: number }
}

function runOne(
  stage: any,
  loadIndex: number,
  seed: number,
  difficulty: string,
  maxTicks: number,
  model: ModelLike,
  arenaId?: number,
): RunOut {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  // 一命覆写（plan/dodge-item-curriculum.md §1a）：S-Dodge 强制 1 命，与训练/eval 口径一致。
  if (arenaId !== undefined && isArenaId(arenaId) && arenaLevelOfId(arenaId) === 'S-Dodge') {
    world.lives = 1
  }

  const scripted = new ScriptedInput()
  const ai: InputLike = scripted
  const sim = new Simulation(world, ai as never)
  world.loadStageData(stage, loadIndex)
  ai.reset()

  // Recording wiring mirrors simulation-runner: loadStageData → ai.reset() →
  // startNew(world), then recordFrame(ai) after each sim.tick().
  const recorder = new InputRecorder()
  recorder.startNew(world)

  const encoder = new ObsEncoder()
  let firstKillTick: number | undefined

  let t = 0
  let outcome: SimOutcome = 'max_ticks'

  while (t < maxTicks) {
    encoder.encode(world)
    if (t % K === 0) {
      model.forward(encoder.obs, encoder.scalars)
      const masks = computeMasks(world)
      const mv = argmaxCat(model.moveLogits, masks.move)
      const fr = argmaxCat(model.fireLogits, masks.fire)
      scripted.setAction(mv, fr)
    }
    sim.tick()
    recorder.recordFrame(ai)
    ai.endFrame()
    t++

    for (const e of world.consumeEvents()) {
      if (e.type === 'tank_destroyed') {
        if ((e as any).by === 'player' && firstKillTick === undefined) firstKillTick = t - 1
      }
    }

    if (world.state === 'stageclear' || world.state === 'victory' || world.state === 'gameover') {
      if (world.state === 'gameover') {
        outcome = 'gameover'
      } else {
        outcome = 'stage_clear'
      }
      break
    }
  }

  const rec = recorder.finalize()
  if (!rec) throw new Error(`recorder empty for seed ${seed}`)

  return {
    outcome,
    cleared: allEnemiesCleared(world),
    ticks: t,
    killCount: world.killCount,
    lives: world.lives,
    playerLevel: world.playerLevel,
    replay: {
      initialSnapshot: rec.snapshot,
      frames: rec.frames,
      tickCount: rec.tickCount,
    },
  }
}

interface CliRun {
  stageId: number
  seed: number
}

function main(): void {
  const argv = process.argv.slice(2)
  let outDir = 'tmp/replays-nn'
  let difficulty = 'hard'
  let maxTicks = 3600
  let weightsPath = ''
  const runs: CliRun[] = []
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--out') outDir = argv[++i]
    else if (argv[i] === '--difficulty') difficulty = argv[++i]
    else if (argv[i] === '--max-ticks') maxTicks = parseInt(argv[++i], 10)
    else if (argv[i] === '--weights') weightsPath = argv[++i]
    else if (argv[i] === '--run') {
      const id = argv[++i]
      const [stageId, seed] = id.split(':').map((x) => parseInt(x, 10))
      if (!Number.isInteger(stageId) || !Number.isInteger(seed)) {
        console.error(`[export-nn-replays] bad --run ${id} (expected stage:seed)`)
        process.exit(2)
      }
      runs.push({ stageId, seed })
    }
  }
  if (!weightsPath || !existsSync(weightsPath) || runs.length === 0) {
    console.error('[export-nn-replays] --weights <file> and >=1 --run stage:seed required')
    process.exit(2)
  }
  const weightsText = readFileSync(weightsPath, 'utf8')
  const model = buildModel(weightsText)
  const weightsSha = createHash('sha256').update(weightsText).digest('hex').slice(0, 12)
  mkdirSync(outDir, { recursive: true })

  for (const r of runs) {
    const isArena = isArenaId(r.stageId)
    const stage = isArena ? resolveArenaStage(r.stageId)! : STAGES[r.stageId]
    if (!stage) {
      console.error(`[export-nn-replays] unknown stage ${r.stageId}`)
      continue
    }
    const loadIndex = isArena ? 0 : r.stageId
    const stageName = (stage as { name?: string })?.name ?? `${r.stageId}`
    const out = runOne(
      stage,
      loadIndex,
      r.seed,
      difficulty,
      maxTicks,
      model,
      isArena ? r.stageId : undefined,
    )

    // Filename 用真实竞技场号（s1020/1021/1022，便于区分），envelope 的
    // stageIndex 用 0（arena 语义，同 export-rl-rollout 的 killScore 修复）。
    const status: 'clear' | 'base' | 'died' | 'timeout' =
      out.outcome === 'stage_clear' ? 'clear' : out.outcome === 'gameover' ? 'died' : 'timeout'
    const filename = buildReplayFilename({
      difficulty,
      stageIndex: r.stageId - 1,
      status,
      lives: out.lives,
      totalTicks: out.ticks,
      seed: r.seed,
    })
    const text = serializeReplayFile({
      source: 'sim',
      seed: r.seed,
      sim: {
        seed: r.seed,
        difficulty,
        stageIndex: isArena ? 0 : r.stageId,
        stageName,
        outcome: out.outcome,
        status,
        maxTicks,
        godAIParams: DEFAULT_GOD_AI_PARAMS as unknown as Record<string, unknown>,
      },
      finalState: {
        score: out.killCount,
        lives: out.lives,
        killCount: out.killCount,
        ticks: out.ticks,
      },
      initialSnapshot: out.replay.initialSnapshot as never,
      frames: out.replay.frames,
      totalTicks: out.replay.tickCount,
      metadata: {
        stage: isArena ? 0 : r.stageId,
        stageName,
        difficulty,
        lives: out.lives,
        playerLevel: out.playerLevel,
        score: out.killCount,
        killCount: out.killCount,
        enemiesTotal: (stage as { enemyCount?: number })?.enemyCount ?? ENEMIES_PER_STAGE,
        playTimeMs: out.ticks * (1000 / 60),
      },
    })
    const path = `${outDir}/${filename}`
    writeFileSync(path, text)
    console.log(
      `[export-nn-replays] s${r.stageId} seed${r.seed} weights=${weightsSha} outcome=${out.outcome} ` +
        `ticks=${out.ticks} kills=${out.killCount} path=${path}`,
    )
  }
}

// ---- Held-action ScriptedInput (index 0 = keep current heading) ----
import type { Direction } from '../../src/constants'

const MOVE_DECODE: Direction[] = ['up', 'down', 'left', 'right']

class ScriptedInput implements InputLike {
  private moveDir: Direction | null = null
  private lastDir: Direction = 'up'
  private firing = false

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

  wasItemPressed(_item: 'guard' | 'frenzy'): boolean {
    return false
  }

  endFrame(): void {}

  reset(): void {
    this.moveDir = null
    this.lastDir = 'up'
    this.firing = false
  }
}

if (import.meta.main) main()
