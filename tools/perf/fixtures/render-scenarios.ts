/**
 * render-scenarios.ts — deterministic render benchmark worlds.
 *
 * Four fixed scenarios, each a frozen World state driven through the render path
 * many times. Entities are placed deterministically (fixed RNG seed + explicit
 * coordinates); particles/explosions are seeded without Math.random so the draw-
 * call stream is byte-identical run-to-run (plan §5.4 / review C2).
 *
 * The render orchestration (PresentationLayer.render) is replicated here as a
 * thin harness because UIManager cannot be instantiated headlessly (it builds a
 * full DOM). The replication matches PresentationLayer 612–636 verbatim for the
 * visual-state sync and the 457–461 system updates.
 */
import { World, genId } from '../../../src/game/World'
import { Camera } from '../../../src/presentation/Camera'
import { AnimationSystem } from '../../../src/presentation/AnimationSystem'
import { ParticleSystem } from '../../../src/presentation/ParticleSystem'
import { EffectsSystem } from '../../../src/presentation/EffectsSystem'
import { GameRenderer } from '../../../src/presentation/renderer/GameRenderer'
import { SpriteCache } from '../../../src/presentation/renderer/SpriteCache'
import { FIELD, CELL, BULLET } from '../../../src/constants'
import type { Direction } from '../../../src/constants'
import type { Tank, PowerUp, Explosion, Particle } from '../../../src/types'
import { buildLib, createRenderTarget, type RenderTarget } from '../headless-canvas'

export const DT = 1000 / 60

export interface BenchContext {
  world: World
  camera: Camera
  anim: AnimationSystem
  particles: ParticleSystem
  effects: EffectsSystem
  renderer: GameRenderer
  target: RenderTarget
}

export function buildBaseWorld(seed: number): World {
  const world = new World()
  world.rng.reseed(seed)
  // themeKey MUST be 'modern' — any other value sets `skipSvg=true` in
  // GameRenderer.setTheme, which bypasses every SVG sprite + SpriteCache path
  // and falls through to the procedural fallback. A bench on the fallback path
  // measures nothing about production rendering (plan review §A).
  world.startGame('classic', 'modern', 0)
  world.state = 'playing'
  return world
}

/**
 * @param counting Pass false to bypass the draw-call counting Proxy — see
 * `createRenderTarget`. Use it when micro-timing a single stage.
 */
export function createBenchContext(
  world: World,
  spriteMap: Map<string, any>,
  dpr = 1,
  counting = true,
): BenchContext {
  const lib = buildLib(spriteMap)
  const cache = new SpriteCache(dpr)
  cache.build(lib)
  const camera = new Camera()
  const anim = new AnimationSystem()
  const particles = new ParticleSystem()
  const effects = new EffectsSystem()
  const target = createRenderTarget(FIELD, dpr, counting)
  const renderer = new GameRenderer(target.fakeCanvas, camera, anim, particles, effects, dpr, lib)
  renderer.setSpriteCache(cache)
  return { world, camera, anim, particles, effects, renderer, target }
}

/**
 * Verbatim replica of `PresentationLayer.updateVisualState` (612–636), including
 * the R1/P1-B threading of the pre-computed `world.allTanks` buffer. Shared by
 * render-bench and pixdiff so the two harnesses can never drift apart.
 */
export function updateVisualState(world: World, anim: AnimationSystem, tanks: Tank[]): void {
  const frame = world.frame
  for (let ti = 0; ti < tanks.length; ti++) {
    const tank = tanks[ti]
    if (!tank.alive) continue
    const vc = anim.getOrCreate(tank.id, 'tank', tank.dir, tank.level ?? 0)
    vc.direction = tank.dir
    vc.level = tank.level ?? 0
    vc.flash = (tank.flashTimer ?? 0) > 0
    vc.lastSeenFrame = frame
    if (tank.spawnTimer > 0) anim.setAnimation(vc, 'spawn')
    else if (tank.moving) anim.setAnimation(vc, 'move')
    else anim.setAnimation(vc, 'idle')
  }
  anim.cleanup(frame)
}

// --- entity helpers --------------------------------------------------------

function mkTank(world: World, kind: Tank['kind'], x: number, y: number, dir: Direction): Tank {
  const t = world.createTank(kind, x, y, dir)
  t.spawnTimer = 0 // steady-state: skip spawn animation, representative of mid-game
  t.moving = false
  return t
}

/** Deterministic particle fill — no Math.random (plan §5.4 / review C2). */
function seedParticles(ps: ParticleSystem, n: number): void {
  ps.clear()
  // All five *real* draw types. An earlier revision listed 'shard', which the
  // renderer has no pass for — 1/5 of the burst load was silently invisible and
  // the scene under-stressed the particle stage. Keep this list in sync with
  // `Particle['type']`.
  const types: Particle['type'][] = ['spark', 'debris', 'ring', 'smoke', 'flash']
  const colors = ['#ffe040', '#ff8c00', '#00ffff', '#ffffff', '#ff4444']
  for (let i = 0; i < n; i++) {
    const p = ps.pool[i]
    p.x = (i * 37) % (FIELD - 20)
    p.y = (i * 53) % (FIELD - 20)
    p.vx = (i % 5) - 2
    p.vy = -((i % 7) - 3)
    p.life = 1e9 // never expire during the bench (deterministic stream)
    p.maxLife = 1e9
    p.size = 2 + (i % 4)
    p.color = colors[i % colors.length]
    p.type = types[i % types.length]
    p.gravity = 0.02
    p.drag = 0.98
    p.rotation = 0
    p.rotSpeed = 0.1
    p.active = true
  }
  ps.activeCount = n
}

function seedExplosions(world: World, n: number): void {
  for (let i = 0; i < n; i++) {
    const exp: Explosion = {
      id: genId(),
      x: (8 + i * 4) * CELL,
      y: (10 + i * 2) * CELL,
      size: 48,
      timer: 1e9,
      maxTimer: 1e9,
      kind: 'big',
    }
    world.addExplosion(exp)
  }
}

// --- scenario world population --------------------------------------------

/** idle: 6 tanks, 0 bullets, 0 particles, 0 powerups — steady-state baseline. */
function populateIdle(world: World): void {
  world.player = mkTank(world, 'player', 8 * CELL, 24 * CELL, 'up')
  world.player.level = 1
  const ally = mkTank(world, 'basic', 10 * CELL, 24 * CELL, 'down')
  ally.allegiance = 'ally'
  world.allies.push(ally)
  const kinds: Tank['kind'][] = ['basic', 'fast', 'power', 'armor']
  for (let i = 0; i < 4; i++) {
    const t = mkTank(world, kinds[i], (4 + i * 4) * CELL, (4 + i * 3) * CELL, 'down')
    world.tanks.push(t)
  }
}

/** combat: 6 tanks (commander / ally / varied hpLevel) + 8 bullets + 2 power-ups. */
function populateCombat(world: World): void {
  populateIdle(world)
  const player = world.player!
  player.level = 2 // star buffer overlay (drawSvgCentered starbuf branch)
  player.shieldTimer = 0
  const ally = world.allies[0]
  ally.hp = 150
  ally.maxHp = 150 // hpLevel 2
  const t0 = world.tanks[0]
  t0.hp = 250
  t0.maxHp = 250 // hpLevel 3
  const t1 = world.tanks[1]
  t1.hp = 450
  t1.maxHp = 450 // hpLevel 5
  const t2 = world.tanks[2]
  t2.hp = 150
  t2.maxHp = 150 // hpLevel 2
  t2.hitCount = 2 // hit overlay
  const t3 = world.tanks[3]
  t3.hp = 200
  t3.maxHp = 200 // hpLevel 2
  if (t3.aiState) t3.aiState.isCommander = true // crown branch (not insignia)

  const dirs: Direction[] = ['up', 'down', 'left', 'right']
  for (let i = 0; i < 8; i++) {
    const b: any = {
      id: genId(),
      x: (i * 37) % (FIELD - BULLET),
      y: (i * 53) % (FIELD - BULLET),
      w: BULLET,
      h: BULLET,
      dir: dirs[i % 4],
      alive: true,
      ownerId: -1 - i,
      ownerKind: 'basic',
      isPlayer: i % 2 === 0,
      allegiance: 'enemy',
      speed: 6,
      power: 1,
      damage: 1,
    }
    world.addBullet(b)
  }

  const pu1: PowerUp = {
    id: genId(),
    type: 'star',
    x: 5 * CELL,
    y: 5 * CELL,
    w: CELL,
    h: CELL,
    alive: true,
    blinkTimer: 0,
    lifeTimer: 20000,
  }
  const pu2: PowerUp = {
    id: genId(),
    type: 'shield',
    x: 15 * CELL,
    y: 10 * CELL,
    w: CELL,
    h: CELL,
    alive: true,
    blinkTimer: 0,
    lifeTimer: 20000,
  }
  world.addPowerUp(pu1)
  world.addPowerUp(pu2)
}

export interface ScenarioDef {
  desc: string
  /** Populate an already-built BenchContext's world + particle system. */
  populate: (ctx: BenchContext) => void
  /** Whether the camera should pan across the field each frame. */
  pan?: boolean
}

export const SCENARIOS: Record<string, ScenarioDef> = {
  idle: {
    desc: '6 tanks, 0 bullets, 0 particles — steady-state baseline',
    populate: (ctx) => populateIdle(ctx.world),
  },
  combat: {
    desc: '6 tanks (commander/ally/varied hpLevel) + 8 bullets + 2 powerups',
    populate: (ctx) => populateCombat(ctx.world),
  },
  burst: {
    desc: 'combat + 3 explosions + ~60 particles (particle 5-pass stress)',
    populate: (ctx) => {
      populateCombat(ctx.world)
      seedParticles(ctx.particles, 60)
      seedExplosions(ctx.world, 3)
    },
  },
  pan: {
    desc: 'combat with a continuously moving camera (full-screen blit cost)',
    pan: true,
    populate: (ctx) => populateCombat(ctx.world),
  },
}
