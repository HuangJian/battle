import type { TankKind, PowerUpType } from '../types'
import type { Direction } from '../constants'
import { SCORE_DROP_INTERVAL, ITEM_SCORE } from './score'
import { SUPER_POWERUP_DROP_CHANCE } from './powerups'
import {
  BASE_SPEED_CPS,
  PLAYER_SPEED_PER_STAR_CPS,
  BASE_BULLET_SPEED_CPS,
  PLAYER_BULLET_SPEED_PER_STAR_CPS,
} from './speed'

/**
 * GameplayRules — the single, difficulty-selected rules object that lets the
 * `classic` mode play like the 1985 FC *Battle City* WITHOUT rewriting systems.
 *
 * Every knob here is *data*, not an engine branch (MANIFEST §2.4 Data Over
 * Code). The modern modes (relax / hard / chaos) use `DEFAULT_RULES`, which
 * reproduces today's exact behavior field-by-field; only `classic` swaps in a
 * faithful profile. The object is stored on the World (`world.rules`) — never
 * in a module global — so it survives snapshot rewind automatically and there
 * is no hidden state (MANIFEST §2.2 / §4).
 */

/** Functional star perks used by `starModel: 'functional'`. */
export type StarPerk = 'fastBullet' | 'doubleShot' | 'steelPierce'

/**
 * Functional star-ladder perk query. Perks are CUMULATIVE in FC: once a star
 * grants a perk (e.g. 1★ `fastBullet`), it stays for all higher levels. So we
 * scan every level from 1..level, not just the current one — the raw
 * `starPerks[level]` table lists perks *introduced* at that level, which would
 * otherwise make a 2★ tank (only `doubleShot` listed) lose the fast bullet it
 * earned at 1★. Non-functional (`universal`) modes never carry perks, so this
 * returns false for them.
 */
export function hasStarPerk(rules: GameplayRules, level: number, perk: StarPerk): boolean {
  if (rules.starModel !== 'functional') return false
  const lv = level ?? 0
  for (let l = 1; l <= lv; l++) {
    if ((rules.starPerks[l] ?? []).includes(perk)) return true
  }
  return false
}

export interface GameplayRules {
  // ── Combat / TTK ──────────────────────────────────────────────
  /** 'instant' = flat per-bullet damage + hits-to-kill table (FC). */
  combatModel: 'instant' | 'pool'
  /** Non-1 entries override the HP for a kind under 'instant' (e.g. armor: 4). */
  hitsToKill: Partial<Record<TankKind, number>>
  /** Flat per-bullet damage AND HP scale base under 'instant'. */
  referenceDamage: number

  // ── Fire rate ────────────────────────────────────────────────
  /** 'bulletCap' = gate on-screen bullets; 'cooldown' = time-only gate. */
  fireModel: 'cooldown' | 'bulletCap'
  /** On-screen bullet cap per kind (under 'bulletCap'). */
  maxBullets: Partial<Record<TankKind, number>>
  /** Star level that raises the player's bullet cap by +1 (2★ double-shot). */
  playerDoubleShotLevel: number
  /** Minimum cooldown (ms) between shots in 'bulletCap' mode. Even though
   *  the bullet cap is the primary limiter, this floor prevents instant
   *  refire when a bullet resolves at close range — a single-frame gap
   *  is too fast and feels like a machine gun. 0 = no minimum (pure cap). */
  bulletCapMinCooldownMs: number

  // ── Star progression ─────────────────────────────────────────
  /** 'universal' = all-dim growth (modern); 'functional' = FC ladder. */
  starModel: 'universal' | 'functional'
  /** Per-level functional perks (FC: 1★ fast / 2★ double / 3★ steel). */
  starPerks: Record<number, StarPerk[]>
  /** Bullet-speed multiplier applied when the 'fastBullet' perk is held. */
  fastBulletMult: number

  // ── Power-ups ────────────────────────────────────────────────
  /** 0 disables 强力道具 (super power-ups) entirely. */
  superDropChance: number
  /** Filtered normal drop pool. */
  allowedPowerups: PowerUpType[]
  /** 'fixed' = drop only on fixedDropKillIndices; 'modern' = current rules. */
  dropSchedule: 'modern' | 'fixed'
  /** 1-based SPAWN indices of power-up carrier enemies (FC: [4, 11, 18]). In
   *  classic these enemies bear the red bonus marker AND drop when destroyed
   *  (faithful: the flashing red enemy IS the drop, regardless of kill order).
   *  Also drives the classic drop trigger in Simulation.kill-drop. */
  fixedDropKillIndices: number[]
  /** Drop every N kills (0 = off). */
  dropOnEveryNKills: number
  /** Drop every N score points (0 = off). */
  dropOnScoreMilestone: number
  /** Drop on elite (commander-tier) kill. */
  dropOnEliteKill: boolean
  /** Modern only: every Nth SPAWNED enemy is a bonus carrier (0 = off).
   *  Classic uses `fixedDropKillIndices` instead, so this is 0 there. */
  bonusEnemyEveryNSpawns: number

  // ── Speed jitter ─────────────────────────────────────────────
  /** Gates BOTH tank + bullet ±5% deterministic jitter. */
  speedJitter: boolean

  // ── Movement speed (cells/sec per kind) ──────────────────────
  /** Per-kind base movement speed in cells/sec. Classic carries the faithful
   *  FC table (basic/power/armor 0.5, fast 1.0 px/frame); modern carries the
   *  differentiated table. Consumed in config/combat.ts → profileToStats. */
  speedCps: Record<TankKind, number>
  /** Player 0-star uses speedCps.player; each star adds this (cells/sec).
   *  Classic: 1.25 ⇒ T1 3.75 → T4 7.5 cps (= 0.5 → 1.0 px/frame). */
  playerSpeedPerStarCps: number

  // ── Bullet speed (cells/sec per kind) ───────────────────────
  /** Per-kind base bullet speed in cells/sec. Classic carries the faithful FC
   *  table (most tanks 2 px/frame = 15 cps; Power 4 px/frame = 30 cps); modern
   *  carries the differentiated ×4 table (BASE_BULLET_SPEED_CPS). Consumed in
   *  config/combat.ts → profileToStats AND at fire time via
   *  spawnBulletSpeedPxPerTick — so the IN-FLIGHT bullet also respects rules.
   *  The player's per-star LINEAR growth here is 0 in classic: the FC "1★
   *  fast bullet" jump is realized by `fastBulletMult`, not linear growth. */
  bulletSpeedCps: Record<TankKind, number>
  /** Player per-star LINEAR bullet-speed growth in cells/sec. Classic sets 0
   *  (growth is perk-driven via `fastBulletMult`); modern uses the universal
   *  +0.5/star increment (PLAYER_BULLET_SPEED_PER_STAR_CPS). */
  playerBulletSpeedPerStarCps: number

  // ── Scoring ──────────────────────────────────────────────────
  /** 'byKind' = FC per-kind scores; 'flat' = modern 100×1.05^N. */
  scoreModel: 'flat' | 'byKind'
  /** Per-kind kill score (under 'byKind'). */
  scoreByKind: Partial<Record<TankKind, number>>
  /** Score for collecting a power-up. */
  itemScore: number
  /** Score threshold that grants an extra life (0 = disabled). */
  extraLifeScore: number
  /** Stage-clear score multiplier base (1.05^N modern, 1.0 FC). */
  scoreStageFactor: number

  // ── Enemy behavior (classic none-tier branch) ────────────────
  /** 'phased' is a NON-faithful modern extra; classic stays 'wander'. */
  enemyBehavior: 'wander' | 'phased'
  /** Direction-weight table for classic wander bias (updateNoneTank → pickClassicDir).
   *  Keys are cardinal directions; higher weight = more likely to be chosen.
   *  FC-1985: enemies wander with a VERY slight downward bias (the eagle sits
   *  at the bottom), but lateral directions are nearly equal. */
  classicDirWeights: Record<Direction, number>
  /** If true, classic none-tier enemies ONLY re-roll direction on collision
   *  with terrain/bounds — no timer-based re-roll. FC-1985 faithful behavior.
   *  If false, enemies also re-roll periodically (modern convenience that
   *  prevents permanent jams). */
  turnOnCollisionOnly: boolean

  // ── Terrain (stretch, not implemented) ───────────────────────
  brickGranularity: 'cell' | 'quarter'

  // ── Drop position randomization ─────────────────────────────
  /** Probability weights for near/mid/far drop positions relative to the
   *  killed enemy position. Weights are normalized internally. Near: 0-1 cells
   *  offset; Mid: 1-2 cells; Far: 2-3 cells. */
  dropPositionWeights: { near: number; mid: number; far: number }
  /** Maximum offset distance in cells for each tier. */
  dropPositionRanges: { near: number; mid: number; far: number }

  // ── Cadence ──────────────────────────────────────────────────
  /** Delay between enemy spawns (ms). */
  spawnIntervalMs: number

  // ── §86c: Turn cooldown ───────────────────────────────
  /** Minimum time (ms) between direction changes for ANY tank (player + enemy).
   *  0 = OFF (byte-identical to pre-§86c — tanks can turn every tick). >0 = the
   *  minimum turn period. At 60fps, 160ms ≈ 9.6 ticks (~360 APM — superhuman
   *  turn rate, so a skilled human is never actually constrained). This blocks
   *  the God AI's per-tick direction oscillation at the source (the simulation
   *  refuses to turn faster than this) — the canonical §86c fix. The
   *  `dodgeOscillationCounterFire` counter-fire (§86) is a no-op in the common
   *  case and only catches rare cooldown-boundary oscillation; it is the
   *  defense-in-depth fallback, not the primary fix. */
  turnCooldownMs: number
}

/**
 * DEFAULT_RULES — reproduces TODAY's exact modern behavior so every other
 * difficulty is untouched by the classic plan. Field-by-field mapping to the
 * current code:
 *   combatModel 'pool'         → armor×5 / firepower×2 blood pool (combat.ts)
 *   fireModel 'cooldown'       → time-only gate (Simulation.tryFire)
 *   starModel 'universal'      → 6-dim universal growth (combat.playerProfile)
 *   superDropChance 0.1        → SUPER_POWERUP_DROP_CHANCE
 *   allowedPowerups +boat      → POWERUP_TYPES (the full normal pool)
 *   dropOnEveryNKills 10       → isTenthKill (Simulation.ts)
 *   dropOnScoreMilestone 5000  → SCORE_DROP_INTERVAL
 *   dropOnEliteKill true       → isElite (Simulation.ts)
 *   bonusEnemyEveryNSpawns 4    → spawn-queue `i % 4 === 3` bonus carriers
 *   speedJitter true           → ±5% tank + bullet jitter ON
 *   scoreModel 'flat'          → killScore flat 100×1.05^N
 *   itemScore 100              → ITEM_SCORE
 *   scoreStageFactor 1.05      → levelFactor base
 *   spawnIntervalMs 1500       → hardcoded spawn timer (Simulation.ts:329)
 */
export const DEFAULT_RULES: GameplayRules = {
  combatModel: 'pool',
  hitsToKill: {},
  referenceDamage: 100, // unused in 'pool'

  fireModel: 'cooldown',
  maxBullets: {},
  playerDoubleShotLevel: 2, // unused in 'cooldown'
  bulletCapMinCooldownMs: 0, // unused in 'cooldown'

  starModel: 'universal',
  starPerks: {},
  fastBulletMult: 1.0, // unused in 'universal'

  superDropChance: SUPER_POWERUP_DROP_CHANCE, // 0.1 — current 强力道具 roll
  allowedPowerups: [
    'star',
    'bomb',
    'shield',
    'freeze',
    'tank',
    'fence',
    'boat',
    'repair',
    'emp',
    'decoy',
    'mine',
  ],
  dropSchedule: 'modern',
  fixedDropKillIndices: [],
  dropOnEveryNKills: 5, // Phase 0: every-5-kills drop
  dropOnScoreMilestone: SCORE_DROP_INTERVAL, // 5000
  dropOnEliteKill: true, // isElite
  bonusEnemyEveryNSpawns: 4, // every 4th spawned enemy is a bonus carrier

  dropPositionWeights: { near: 0.5, mid: 0.3, far: 0.2 }, // 50/30/20%
  dropPositionRanges: { near: 1, mid: 2, far: 3 }, // cells offset

  speedJitter: true, // current ±5% jitter ON

  speedCps: BASE_SPEED_CPS, // balanced=classic(3.75), others keep original ratios
  playerSpeedPerStarCps: PLAYER_SPEED_PER_STAR_CPS, // +0.25/star (→ 4.6875 cps at 3★)

  bulletSpeedCps: BASE_BULLET_SPEED_CPS, // balanced=classic(15), others keep original ratios
  playerBulletSpeedPerStarCps: PLAYER_BULLET_SPEED_PER_STAR_CPS, // +0.5/star (→ 17.25 cps at 3★)

  scoreModel: 'flat',
  scoreByKind: {},
  itemScore: ITEM_SCORE, // 100
  extraLifeScore: 0, // current: no score→life (life only via 'tank')
  scoreStageFactor: 1.05, // current 1.05^stage

  enemyBehavior: 'wander', // current 100%-none constant wander
  classicDirWeights: { down: 3, left: 1, right: 1, up: 0.35 }, // modern strong downward bias
  turnOnCollisionOnly: false, // modern: timer + collision both trigger re-roll
  brickGranularity: 'cell', // current 16px cell destruction
  spawnIntervalMs: 1500, // Simulation.ts:329
  // §86c: Turn cooldown — 100ms minimum turn period (~6 ticks at 60fps).
  // §95 A/B (35×60 classic): 100ms + halt-during-cooldown is the best of the
  // 50/100/160 sweep — net +5 flips (91.2%) vs the 50ms drift baseline
  // (91.0%), suite 0.7561→0.7741. 160ms + halt regresses to 89.8% (the AI's
  // per-tick decision model assumes instant turns; at ~9.6 ticks the dodge /
  // T2a aim / turn-and-fire cadence degrades). 50ms + halt regresses to
  // 88.9% (the halt stops the tank in the bullet's path — at 3 ticks the old
  // drift was better for dodging). 100ms is the sweet spot: no maze overshoot
  // (the halt), minimal dodge-loss (only 6 ticks).
  turnCooldownMs: 100,
}

/**
 * RULES.classic — the faithful 1985 FC *Battle City* profile.
 *
 * Map/hp/score constants that were ALREADY faithful (base 1-HP instakill,
 * spawn shield 3000ms, power-up durations, 35 stage layouts, victory after
 * stage 35) are intentionally NOT duplicated here (issue #9) — they are
 * untouched by this plan.
 */
export const RULES: Record<string, GameplayRules> = {
  classic: {
    combatModel: 'instant',
    hitsToKill: { basic: 1, fast: 1, power: 1, armor: 4, player: 1 },
    referenceDamage: 100, // = round(basic.firepower × DAMAGE_SCALE)

    fireModel: 'bulletCap',
    maxBullets: { basic: 1, fast: 1, power: 1, armor: 1, player: 1 },
    playerDoubleShotLevel: 2, // 2★ raises player cap to 2
    // Minimum cooldown between shots: 300ms ≈ 18 frames at 60fps. This
    // prevents instant refire when a bullet resolves at close range —
    // the original FC had a natural ~0.5s cadence because bullets traveled
    // slowly across the full field. At close range the bullet resolves in
    // 1-2 frames, so without this floor the player would fire like a
    // machine gun. 300ms is aggressive enough to feel responsive but slow
    // enough to prevent the "hold fire = instakill" exploit.
    bulletCapMinCooldownMs: 300,

    starModel: 'functional',
    starPerks: {
      1: ['fastBullet'],
      2: ['doubleShot'],
      3: ['steelPierce', 'fastBullet'],
    },
    fastBulletMult: 2.0, // 1★ fastBullet: 15 → 30 cps (= 2 → 4 px/frame), faithful FC

    superDropChance: 0, // no 强力道具 in classic
    allowedPowerups: ['star', 'bomb', 'shield', 'freeze', 'tank', 'fence'], // original 6 (no boat)
    dropSchedule: 'fixed',
    fixedDropKillIndices: [4, 11, 18], // FC fixed drop enemies
    dropOnEveryNKills: 0,
    dropOnScoreMilestone: 0,
    dropOnEliteKill: false,
    bonusEnemyEveryNSpawns: 0, // classic uses fixedDropKillIndices for carriers

    dropPositionWeights: { near: 0.5, mid: 0.3, far: 0.2 }, // 50/30/20%
    dropPositionRanges: { near: 1, mid: 2, far: 3 }, // cells offset

    speedJitter: false, // no ±5% jitter (issue #7: BOTH tank + bullet)

    // Faithful FC movement speeds (cells/sec). Conversion FC px/frame @60fps →
    // px/sec (×60) → FC tiles/sec (÷16, tile=16px) → project cells/sec (×2 because
    // 1 FC tile = 1 FC tank = 16px while 1 project cell = 0.5 project tank, and both
    // fields are 13 tanks wide). Net factor = ×7.5. Thus basic 0.5 px/frame → 3.75
    // cps, fast 1.0 → 7.5 cps; player T1 3.75 → T4 7.5.
    speedCps: { basic: 3.75, fast: 7.5, power: 3.75, armor: 3.75, player: 3.75 },
    playerSpeedPerStarCps: 1.25,

    // Faithful FC bullet speeds (cells/sec). Same ×7.5 px/frame→cps factor as
    // movement. FC bullets are 2 px/frame (slow) for basic/fast/armor/player and
    // 4 px/frame (fast) for Power — NOT a per-kind 1.05/0.95/0.90 spread like
    // modern. Player growth is perk-driven: base 2 px/frame (15 cps); the 1★
    // 'fastBullet' star jumps it to 4 px/frame (30 cps) via fastBulletMult (2.0),
    // and that fast bullet stays for every higher star level (FC keeps the fast
    // bullet once earned). Hence playerBulletSpeedPerStarCps is 0 here.
    bulletSpeedCps: { basic: 15, fast: 15, power: 30, armor: 15, player: 15 },
    playerBulletSpeedPerStarCps: 0,

    scoreModel: 'byKind',
    scoreByKind: { basic: 100, fast: 200, power: 300, armor: 400 },
    itemScore: 500, // FC item = 500
    extraLifeScore: 0, // no score→life; life only via 'tank'
    scoreStageFactor: 1.0, // FC score is flat (no stage scaling)

    enemyBehavior: 'wander', // FAITHFUL: constant random wander, NO chase/push phases
    // FC-1985 direction weights: enemies wander with a VERY slight downward bias
    // (the eagle is at the bottom of the field), but lateral directions are nearly
    // equal. The original game has no strong directional preference — enemies
    // stumble into the base by chance or aggressive forward pressure, not by
    // a weighted bias. The slight down preference (1.2×) reproduces this feel
    // without the modern strong pull (down=3).
    classicDirWeights: { down: 1.2, left: 1.0, right: 1.0, up: 1.0 },
    // FC-1985: enemies ONLY change direction when they hit an obstacle (wall,
    // water, or the edge of the field). There is NO timer-based re-roll.
    // This matches the original's "bump and turn" behavior where enemies
    // walk in straight lines until blocked, then randomly pick a new open
    // direction. The modern timer re-roll (turnOnCollisionOnly: false) is
    // a convenience that prevents permanent jams in tight corridors.
    turnOnCollisionOnly: true,
    brickGranularity: 'cell', // stretch (see plan Phase 8)
    spawnIntervalMs: 1800, // ~1.8s, closer to FC's ~1.8–2.0s (issue #8)
    // §86c: Turn cooldown — 100ms minimum turn period (~6 ticks at 60fps).
    // §95 A/B best of 50/100/160 (see DEFAULT_RULES note).
    turnCooldownMs: 100,
  },
  relax: DEFAULT_RULES,
  hard: DEFAULT_RULES,
  chaos: DEFAULT_RULES,
}
