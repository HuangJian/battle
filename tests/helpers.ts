import { World, genId } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { DIFFICULTIES } from '../src/config/difficulty'
import { THEMES } from '../src/config/theme'
import { RNG } from '../src/utils/RNG'
import { CELL, GRID, BASE_POS, TANK, BULLET } from '../src/constants'
import type { Direction } from '../src/constants'
import type { Tank, TankKind, Bullet, PowerUp, StageData } from '../src/types'

/**
 * Shared test fixtures (plan/refactor.agy.md §3.4).
 *
 * One canonical implementation per common setup pattern. Semantics are
 * frozen to the dominant historical copy-paste variant — do NOT "improve"
 * them: dozens of tests assert geometry relative to these exact numbers.
 * If a test needs a different setup shape, prefer a local helper over
 * widening these.
 */

/** Options for {@link createTestWorld}. */
export interface TestWorldOptions {
  /** Seed for `world.rng` (default 42 — the historical test seed). */
  rngSeed?: number
}

/** Fresh World with a seeded RNG; no stage loaded (`state` stays 'menu'). */
export function createTestWorld(opts: TestWorldOptions = {}): World {
  const world = new World()
  world.rng = new RNG(opts.rngSeed ?? 42)
  return world
}

/**
 * Wipe every tile to 'empty' and restore the base eagle 2×2 at BASE_POS —
 * the standard "clean slate but win/lose conditions intact" arena used by
 * ~30 simulation tests.
 */
export function clearArena(world: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [BASE_POS.col, BASE_POS.col + 1]) world.tileMap.grid[r][c] = 'base'
  }
}

/**
 * Spawn an enemy directly onto the field (bypassing the spawn queue):
 * `createTank` at cell (col,row), spawn animation skipped, pushed into
 * `world.tanks`. Returns the tank so callers can tweak AI state.
 */
export function placeEnemy(
  world: World,
  col: number,
  row: number,
  kind: TankKind = 'basic',
  dir: Direction = 'down',
): Tank {
  const enemy = world.createTank(kind, col * CELL, row * CELL, dir)
  enemy.spawnTimer = 0
  world.tanks.push(enemy)
  return enemy
}

/**
 * Teleport the live player tank to cell (col,row) with spawn/shield timers
 * cleared. Cell coords → top-left pixel mapping is `col*CELL` (the dominant
 * test convention).
 */
export function positionPlayer(world: World, col: number, row: number, dir?: Direction): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
  p.spawnTimer = 0
  p.shieldTimer = 0
  if (dir) p.dir = dir
}

// ── Composite fixtures (plan/refactor.zcode.md §1.1) ────────────────────────
// Incremental adoption: new tests should reach for these instead of copying
 // setup boilerplate; existing local copies are NOT rewritten.
//
// 口径差异表 — known historical local variants that intentionally differ from
// these shared fixtures. Do NOT blindly swap them for the shared versions:
// - `makeBullet`: dodge-m12 / suicide-return default `damage = 100` (lethal-
//   shot fixtures); guard-ally uses a `9000 + id` scheme with speed 6;
//   threat-assessor parameterizes `ownerKind`. Shared default: enemy bullet,
//   ownerKind 'fast', speed 4, damage 1, dir 'up'.
// - `openArena` (local in tactical-ai.test.ts): hardcodes the eagle at cols
//   [12,13] (== BASE_POS) and calls rebuildBaseCache(); base-hp.test.ts also
//   empties tanks/bullets/queue. Shared clearArena() only rewrites tiles.
// - `emptyArena` / `clearArena` (local in godai-stall-exposure /
//   godai-turn-snap-guard): wipe ALL tiles including the base eagle and
//   rebuildBaseCache() → a NO-BASE arena. Shared clearArena() wipes then
//   RESTORES the base 2×2 — win/lose conditions stay intact.
// - `addEnemy` (godai-candidates / coverage / intent / target-value /
//   travel-fire): **1-BASED** cell coords (`(col-1)*CELL`) facing 'up' —
//   these mirror FC stage-grid coordinates, not helpers.placeEnemy's
//   0-based top-left convention. godai-action-contract / godai-threat-budget
//   share the 1-based flavor but skip the `world.tanks.push` (caller pushes).
// - `placeEnemy` (local copies in base-clear-shot-threat / base-damage-recall /
//   battlement-* / chokepoint / close-pickup / counter-fire / …): **PARAMETER
//   ORDER TRAP** — local 3rd arg is `dir` ('down' default), shared
//   helpers.placeEnemy's 3rd arg is `kind`. NOT byte-identical → not adopted
//   (refactor.zcode.md §2.2 optional increment rejected: a blind swap would
//   silently turn `placeEnemy(w,c,r,'up')` into kind='up'). Check the local
//   signature before adding a 4-arg call to any of these files.
// - `positionPlayer` / `placePlayer` (~18 local copies, three dialects):
//     ① cell → top-left pixel `col*CELL` (= shared helpers.positionPlayer);
//     ② cell → CENTERED pixel `col*16-8` (e.g. base-alert — cell index is
//        the tank's center column per `floor((x+16)/16)`);
//     ③ raw PIXEL args named `x,y` (dodge-centroid, dodge-m12, and the
//        remaining x,y-signature copies — no cell math at all).
//   Check the local body before assuming any of them matches dialect ①.
// - `makeCoopWorld`: replay-coop-autofire / replay-seek build a full stage +
//   AutoFireInput + GodAI(P2) sim wiring for replay determinism; lie-back-win-m2/m6
//   hand-place a player2 tank at pixel (300,300). The shared version mirrors
//   Game.requestCoopToggle via world.enablePlayer2().

/** Options for {@link setupGodGame}. */
export interface GodGameOptions {
  /** Difficulty key passed to `world.startGame` (default 'hard'). */
  difficulty?: string
  /** God AI param overrides merged over DEFAULT_GOD_AI_PARAMS. */
  params?: Partial<typeof DEFAULT_GOD_AI_PARAMS>
  /** Seed for `world.rng` (default 42 — the historical test seed). */
  seed?: number
}

/** Result of {@link setupGodGame}. */
export interface GodGame {
  world: World
  input: GodAIInput
  sim: Simulation
}

/**
 * World + GodAIInput + Simulation wired and ready: startGame on a real stage,
 * arena cleared to empty + base eagle at BASE_POS, base caches rebuilt,
 * state='playing', input.hasBase synced, input.reset() applied.
 * Adoption status (refactor.zcode.md §2.2): ~24 local `setupWorld()` copies
 * remain across tests/ (mostly dialect carriers — see 口径差异表 above);
 * 3 files use this shared version. Migration is opt-in, not forced (§260
 * lesson: byte-level fixture semantics beat DRY when tests pin geometry).
 */
export function setupGodGame(opts: GodGameOptions = {}): GodGame {
  const world = createTestWorld({ rngSeed: opts.seed ?? 42 })
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...opts.params })
  const sim = new Simulation(world, new Input())
  world.startGame(opts.difficulty ?? 'hard', 'modern', 0)
  clearArena(world)
  world.tileMap.rebuildBaseCache()
  world.state = 'playing'
  input.hasBase = world.tileMap.hasBase()
  input.reset()
  return { world, input, sim }
}

/**
 * Fully-populated Bullet with the dominant test defaults; pass overrides for
 * everything else (`makeBullet({ x: 8 * CELL, dir: 'down' })`).
 */
export function makeBullet(over: Partial<Bullet> = {}): Bullet {
  return {
    id: genId(),
    x: 0,
    y: 0,
    w: BULLET,
    h: BULLET,
    dir: 'up',
    alive: true,
    ownerId: -1,
    ownerKind: 'fast',
    isPlayer: false,
    allegiance: 'enemy',
    speed: 4,
    power: 1,
    damage: 1,
    ...over,
  }
}

/**
 * Fully-populated Tank with the dominant test defaults (frozen to the
 * byte-identical local copy that lived in five test files — 遗留 #5);
 * pass overrides for everything else (`makeTank({ x: 3 * CELL })`).
 */
export function makeTank(over: Partial<Tank> = {}): Tank {
  return {
    id: 0,
    kind: 'basic',
    x: 100,
    y: 100,
    w: TANK,
    h: TANK,
    dir: 'up',
    speed: 1,
    moving: false,
    alive: true,
    hp: 1,
    maxHp: 1,
    level: 0,
    spawnTimer: 0,
    shieldTimer: 0,
    lastFire: 0,
    nextFireInterval: 500,
    fireCooldown: 0,
    fireCount: 0,
    bulletPower: 1,
    damage: 1,
    bulletSpeed: 3,
    vx: 0,
    vy: 0,
    profile: {
      firepower: 50,
      projectileSpeed: 50,
      fireControl: 50,
      mobility: 50,
      armor: 50,
      special: 50,
    },
    allegiance: 'player',
    isPlayer: true,
    ...over,
  }
}

/**
 * PowerUp centered on cell (col,row) via the col*CELL convention; pure
 * factory — push into `world.powerUps` yourself when needed.
 */
export function makePowerUp(
  col: number,
  row: number,
  type: PowerUp['type'],
  over: Partial<PowerUp> = {},
): PowerUp {
  return {
    id: genId(),
    type,
    x: col * CELL,
    y: row * CELL,
    w: TANK,
    h: TANK,
    alive: true,
    blinkTimer: 0,
    lifeTimer: 0,
    ...over,
  }
}

/** 26×26 all-empty grid with just the base eagle ('EE') at rows 24-25. */
export function makeEmptyStage(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += '.'
    if (r === 24 || r === 25) row = row.slice(0, BASE_POS.col) + 'EE' + row.slice(BASE_POS.col + 2)
    tiles.push(row)
  }
  return { id: 9999, name: 'Empty Arena', tiles, enemies: ['basic'] }
}

/** Empty arena plus a steel box (walls on cols/rows 1..4, top-left). */
export function makeBoxedArena(): StageData {
  const grid = makeEmptyStage().tiles.map((row) => row.split(''))
  for (let c = 1; c <= 4; c++) {
    grid[1][c] = 's'
    grid[4][c] = 's'
  }
  for (let r = 1; r <= 4; r++) {
    grid[r][1] = 's'
    grid[r][4] = 's'
  }
  return { id: 9998, name: 'Boxed Arena', tiles: grid.map((r) => r.join('')), enemies: ['basic'] }
}

/**
 * Co-op World: classic difficulty/theme, state='playing', Player 2 brought
 * online through the canonical `world.enablePlayer2()` path (mirrors
 * Game.requestCoopToggle). Spawn/shield timers are cleared so tests can
 * teleport P2 immediately.
 */
export function makeCoopWorld(seed = 42): World {
  const world = createTestWorld({ rngSeed: seed })
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.themeKey = 'classic'
  world.theme = THEMES['classic']
  world.state = 'playing'
  world.enablePlayer2()
  if (world.player2) {
    world.player2.spawnTimer = 0
    world.player2.shieldTimer = 0
  }
  return world
}
