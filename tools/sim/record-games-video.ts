/**
 * record-games-video.ts — 用给定 RL 权重在指定 (关, 种子) 上逐 tick 渲染成 mp4 录像。
 *
 * 策略 = export-eval-game 同一个贪心 NN(掩码 argmax,零探索)——与评估口径一致,
 * 只在每次 sim.tick 后把 world 画到 @napi-rs/canvas(Skia) 上存 PNG 帧,再用 ffmpeg 编码。
 * 不建 dev server(AGENTS §5 硬规则);渲染管线复用 render-bench 的 headless 基座。
 *
 * Usage:
 *   bun tools/sim/record-games-video.ts --weights <weights.json> \
 *       --games s1010:860011,s1011:860012 \
 *       --difficulty hard --max-ticks 1200 --dpr 2 --out tmp/vrecord
 */
import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { join } from 'node:path'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { isArenaId, resolveArenaStage } from '../../src/nn/arena-ladder'
import { START_LIVES, FIELD } from '../../src/constants'
import type { Direction } from '../../src/constants'
import { ObsEncoder, computeMasks } from '../../src/nn/obs-encoder'
import { buildModelFromText } from '../../src/nn/infer'
import type { InputLike } from '../../src/game/Input'
import { Camera } from '../../src/presentation/Camera'
import { AnimationSystem } from '../../src/presentation/AnimationSystem'
import { ParticleSystem } from '../../src/presentation/ParticleSystem'
import { EffectsSystem } from '../../src/presentation/EffectsSystem'
import { GameRenderer } from '../../src/presentation/renderer/GameRenderer'
import { SpriteCache } from '../../src/presentation/renderer/SpriteCache'
import {
  installHeadlessShims,
  loadSpritesFromDisk,
  buildLib,
  createRenderTarget,
} from '../perf/headless-canvas'
import { updateVisualState, DT } from '../perf/fixtures/render-scenarios'

const K = 10
const MOVE_DECODE: Direction[] = ['up', 'down', 'left', 'right']

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
  endFrame(): void {}
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
}

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

interface GameSpec {
  stage: number
  seed: number
}

function parseGames(spec: string): GameSpec[] {
  return spec
    .split(',')
    .filter(Boolean)
    .map((s) => {
      const m = /^s?(\d+):(\d+)$/.exec(s)
      if (!m) throw new Error(`bad game spec: ${s}`)
      return { stage: parseInt(m[1], 10), seed: parseInt(m[2], 10) }
    })
}

function ffmpeg(): string {
  const cands = [process.env.FFMPEG ?? 'ffmpeg', 'D:/tool/ffmpeg/bin/ffmpeg.exe']
  for (const c of cands) {
    const r = spawnSync(c, ['-version'], { windowsHide: true, encoding: 'utf8' })
    if (!r.error) return c
  }
  throw new Error('ffmpeg not found (set FFMPEG or install to PATH)')
}

function recordOne(
  game: GameSpec,
  difficulty: string,
  maxTicks: number,
  weightsText: string,
  snaps: {
    camera: Camera
    anim: AnimationSystem
    particles: ParticleSystem
    effects: EffectsSystem
    renderer: GameRenderer
    canvas: any
  },
  framesDir: string,
): { outcome: string; ticks: number; win: boolean } {
  const world = new World()
  world.rng.reseed(game.seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const stage = isArenaId(game.stage) ? resolveArenaStage(game.stage)! : STAGES[game.stage]
  const loadIndex = isArenaId(game.stage) ? 0 : game.stage

  const model = buildModelFromText(weightsText) as unknown as RolloutModel
  const scripted = new ScriptedInput()
  const ai = scripted as unknown as InputLike
  const sim = new Simulation(world, ai as any)
  world.loadStageData(stage, loadIndex)
  ai.reset()

  const encoder = new ObsEncoder()
  let t = 0
  let outcome = 'timeout'

  while (t < maxTicks) {
    encoder.encode(world)
    if (t % K === 0) {
      model.forward(encoder.obs, encoder.scalars)
      const masks = computeMasks(world)
      scripted.setAction(
        argmaxCat(model.moveLogits, masks.move),
        argmaxCat(model.fireLogits, masks.fire),
      )
    }
    sim.tick()
    ai.endFrame()
    t++

    const tanks = world.allTanks
    updateVisualState(world, snaps.anim, tanks)
    snaps.anim.update(DT)
    snaps.particles.update(DT)
    snaps.camera.update(DT)
    snaps.effects.update(DT)
    snaps.renderer.render(world, tanks)
    writeFileSync(
      join(framesDir, String(t).padStart(5, '0') + '.png'),
      snaps.canvas.toBuffer('image/png'),
    )

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
  return { outcome, ticks: t, win: outcome === 'stage_clear' }
}

async function main(): Promise<void> {
  const argv = process.argv.slice(2)
  let weightsPath = 'tmp/s2-cap/weights.json'
  let gamesSpec = ''
  let difficulty = 'hard'
  let maxTicks = 1200
  let dpr = 2
  let outDir = 'tmp/vrecord'
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--weights') weightsPath = argv[++i]
    else if (argv[i] === '--games') gamesSpec = argv[++i]
    else if (argv[i] === '--difficulty') difficulty = argv[++i]
    else if (argv[i] === '--max-ticks') maxTicks = parseInt(argv[++i], 10)
    else if (argv[i] === '--dpr') dpr = parseInt(argv[++i], 10)
    else if (argv[i] === '--out') outDir = argv[++i]
  }
  if (!gamesSpec) {
    console.error('[record-games-video] --games required (e.g. s1010:860011,s1011:860012)')
    process.exit(2)
  }
  const games = parseGames(gamesSpec)
  const weightsText = readFileSync(weightsPath, 'utf8')
  const weightsSha = createHash('sha256').update(weightsText).digest('hex').slice(0, 12)
  mkdirSync(outDir, { recursive: true })

  installHeadlessShims()
  const sprites = await loadSpritesFromDisk()
  const lib = buildLib(sprites)
  const cache = new SpriteCache(dpr)
  cache.build(lib)
  const camera = new Camera()
  const anim = new AnimationSystem()
  const particles = new ParticleSystem()
  const effects = new EffectsSystem()
  const target = createRenderTarget(FIELD, dpr, false)
  const renderer = new GameRenderer(target.fakeCanvas, camera, anim, particles, effects, dpr, lib)
  renderer.setSpriteCache(cache)
  const snaps = { camera, anim, particles, effects, renderer, canvas: target.realCanvas }

  const ff = ffmpeg()
  for (const g of games) {
    const tag = `s${g.stage}-seed${g.seed}`
    const framesDir = join(outDir, `.frames-${tag}`)
    rmSync(framesDir, { recursive: true, force: true })
    mkdirSync(framesDir, { recursive: true })
    const res = recordOne(g, difficulty, maxTicks, weightsText, snaps, framesDir)
    const mp4 = join(
      outDir,
      `${tag}-${res.outcome === 'lives_exhausted' ? 'lost' : res.outcome}.mp4`,
    )
    const r = spawnSync(
      ff,
      [
        '-y',
        '-framerate',
        '60',
        '-i',
        join(framesDir, '%05d.png'),
        '-c:v',
        'libx264',
        '-preset',
        'veryfast',
        '-crf',
        '20',
        '-pix_fmt',
        'yuv420p',
        '-movflags',
        '+faststart',
        mp4,
      ],
      { windowsHide: true },
    )
    if (r.status !== 0) {
      console.error(`[record] ffmpeg failed for ${tag}: ${r.stderr?.toString().slice(0, 400)}`)
    }
    rmSync(framesDir, { recursive: true, force: true })
    console.log(
      `[record] ${tag} outcome=${res.outcome} ticks=${res.ticks} win=${res.win} -> ${mp4}${r.status === 0 ? '' : ' (ENCODE FAILED)'}`,
    )
  }
  console.log(`[record] weightsSha=${weightsSha} difficulty=${difficulty} dpr=${dpr}`)
}

await (async () => {
  if (import.meta.main) {
    await main()
  }
})()
