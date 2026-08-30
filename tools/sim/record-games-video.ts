/**
 * record-games-video.ts — 用给定 RL 权重在指定 (关, 种子) 上逐 tick 渲染成 mp4 录像。
 *
 * 策略双口径(浅份):
 *   --mode greedy  (缺省) export-eval-game 同款贪心 NN(掩码 argmax,零探索);
 *   --mode rollout export-rl-rollout.ts 同款采样策略——逐字节复制其 sampleCat 的
 *                   masked softmax 采样 + mulberry32((seed ^ 0x85ebca6b)>>>0) 种子,
 *                   与训练 rollout 完全同分布同轨迹(同一权重+种子 ⇒ 逐 tick 复现
 *                   该轮训练的真实对局)。
 * 只在每次 sim.tick 后把 world 画到 @napi-rs/canvas(Skia) 上存 PNG 帧,再用 ffmpeg 编码。
 * 不建 dev server(AGENTS §5 硬规则);渲染管线复用 render-bench 的 headless 基座。
 *
 * Usage:
 *   bun tools/sim/record-games-video.ts --weights <weights.json> \
 *       --games s1010:860011,s1011:860012 --mode rollout \
 *       --difficulty hard --max-ticks 3600 --dpr 2 --out tmp/vrecord
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

/** mulberry32(seed ^ 0x85ebca6b)——与 export-rl-rollout.ts 采样 RNG 逐字节一致。 */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 与 export-rl-rollout.sampleCat 逐字节一致的 masked softmax 采样。 */
function sampleCat(logits: Float32Array, mask: number[] | null, rng: () => number): number {
  const n = logits.length
  let max = -Infinity
  for (let i = 0; i < n; i++) {
    const v = mask && mask[i] !== 1 ? -1e9 : logits[i]
    if (v > max) max = v
  }
  const ps = new Float32Array(n)
  let sum = 0
  for (let i = 0; i < n; i++) {
    const v = mask && mask[i] !== 1 ? -1e9 : logits[i]
    ps[i] = Math.exp(v - max)
    sum += ps[i]
  }
  for (let i = 0; i < n; i++) ps[i] /= sum
  const u = rng()
  let c = 0
  for (let i = 0; i < n; i++) {
    c += ps[i]
    if (u <= c) return i
  }
  return n - 1
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
  mode: 'greedy' | 'rollout',
  dumpPath: string | null,
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
  const rng = mode === 'rollout' ? mulberry32((game.seed ^ 0x85ebca6b) >>> 0) : null
  const acted: string[] = []
  let t = 0
  let outcome = 'timeout'

  while (t < maxTicks) {
    encoder.encode(world)
    if (t % K === 0) {
      model.forward(encoder.obs, encoder.scalars)
      const masks = computeMasks(world)
      let aMove: number
      let aFire: number
      if (mode === 'rollout') {
        aMove = sampleCat(model.moveLogits, masks.move, rng!)
        aFire = sampleCat(model.fireLogits, masks.fire, rng!)
      } else {
        aMove = argmaxCat(model.moveLogits, masks.move)
        aFire = argmaxCat(model.fireLogits, masks.fire)
      }
      scripted.setAction(aMove, aFire)
      acted.push(`${t},${aMove},${aFire}`)
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
  if (dumpPath) writeFileSync(dumpPath, acted.join('\n') + '\n')
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
  let mode: 'greedy' | 'rollout' = 'greedy'
  let dumpPath: string | null = null
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--weights') weightsPath = argv[++i]
    else if (argv[i] === '--games') gamesSpec = argv[++i]
    else if (argv[i] === '--difficulty') difficulty = argv[++i]
    else if (argv[i] === '--max-ticks') maxTicks = parseInt(argv[++i], 10)
    else if (argv[i] === '--dpr') dpr = parseInt(argv[++i], 10)
    else if (argv[i] === '--out') outDir = argv[++i]
    else if (argv[i] === '--dump-actions') dumpPath = argv[++i]
    else if (argv[i] === '--mode') {
      const m = argv[++i]
      if (m !== 'greedy' && m !== 'rollout') {
        console.error(`[record-games-video] unknown --mode ${m} (greedy|rollout)`)
        process.exit(2)
      }
      mode = m
    }
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
    const res = recordOne(g, difficulty, maxTicks, weightsText, mode, dumpPath, snaps, framesDir)
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
  console.log(`[record] weightsSha=${weightsSha} difficulty=${difficulty} mode=${mode} dpr=${dpr}`)
}

await (async () => {
  if (import.meta.main) {
    await main()
  }
})()
