import type { TankKind, PowerUpType } from '../types'
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

  // ── Enemy behavior ───────────────────────────────────────────
  /** 'phased' is a NON-faithful modern extra; classic stays 'wander'. */
  enemyBehavior: 'wander' | 'phased'

  // ── Terrain (stretch, not implemented) ───────────────────────
  brickGranularity: 'cell' | 'quarter'

  // ── Cadence ──────────────────────────────────────────────────
  /** Delay between enemy spawns (ms). */
  spawnIntervalMs: number
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

  starModel: 'universal',
  starPerks: {},
  fastBulletMult: 1.0, // unused in 'universal'

  superDropChance: SUPER_POWERUP_DROP_CHANCE, // 0.1 — current 强力道具 roll
  allowedPowerups: ['star', 'bomb', 'shield', 'freeze', 'tank', 'fence', 'boat'],
  dropSchedule: 'modern',
  fixedDropKillIndices: [],
  dropOnEveryNKills: 10, // isTenthKill
  dropOnScoreMilestone: SCORE_DROP_INTERVAL, // 5000
  dropOnEliteKill: true, // isElite
  bonusEnemyEveryNSpawns: 4, // every 4th spawned enemy is a bonus carrier

  speedJitter: true, // current ±5% jitter ON

  speedCps: BASE_SPEED_CPS, // current differentiated table (2.5/3.0/2.375/2.125/2.625)
  playerSpeedPerStarCps: PLAYER_SPEED_PER_STAR_CPS, // +0.125/star (→ 3.0 cps at 3★)

  bulletSpeedCps: BASE_BULLET_SPEED_CPS, // current ×4 differentiated table
  playerBulletSpeedPerStarCps: PLAYER_BULLET_SPEED_PER_STAR_CPS, // +0.5/star (→ 12.0 cps at 3★)

  scoreModel: 'flat',
  scoreByKind: {},
  itemScore: ITEM_SCORE, // 100
  extraLifeScore: 0, // current: no score→life (life only via 'tank')
  scoreStageFactor: 1.05, // current 1.05^stage

  enemyBehavior: 'wander', // current 100%-none constant wander
  brickGranularity: 'cell', // current 16px cell destruction
  spawnIntervalMs: 1500, // Simulation.ts:329
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
    brickGranularity: 'cell', // stretch (see plan Phase 8)
    spawnIntervalMs: 1800, // ~1.8s, closer to FC's ~1.8–2.0s (issue #8)
  },
  relax: DEFAULT_RULES,
  hard: DEFAULT_RULES,
  chaos: DEFAULT_RULES,
}
