import type { World } from '../game/World'
import { DIFFICULTIES } from '../config/difficulty'
import { RULES, DEFAULT_RULES } from '../config/rules'
import { THEMES, DEFAULT_THEME } from '../config/theme'
import type { ProbeGame, ProbeStage } from './manifest'

// ================================================================
// Human-opening probe — run setup (plan v7 T3 steps ①–⑤)
//
// THE single setup sequence, shared by the browser (ProbeController) and the
// headless twin (`tools/probe/probe-tick0.ts`) + the manifest generator. Sharing
// the function (rather than "copy these five steps") is what makes the T5(a)
// tick-0 hash comparison structural instead of aspirational.
//
// Why each step exists:
//   ① run reset — `loadStageData` only rebuilds map/entities/queue. `score`,
//      `killCount`, `playTimeMs`, pending drops and the super-power-up
//      inventory survive it, so playing game N+1 on the same page would carry
//      game N's kills into the verdict evidence (kills ARE the evidence).
//   ② reseed — `world.seed` surfaces as the replay filename seed and
//      `world.rng.reseed` resets the stream, so whatever the Game consumed
//      during boot/menu is irrelevant and tick 0 is deterministic.
//   ③ difficulty trio — `loadStageData` reads `difficultyKey` for `baseMaxHp`
//      (classic = 1) and the commander quota; entities read `difficulty`.
//      Writing only one or two of the three silently lands on classic numbers.
//      The theme is presentation-only and pinned to the default.
//   ④ lives/level — set BEFORE `loadStageData` so the spawned player tank gets
//      them (`startGame` would reset lives back to `difficulty.startLives`).
//   ⑤ `loadStageData(stage, 0)` — index 0 explicitly, never `loadStage()`
//      (which resolves through `STAGES` and would score-scale by index).
//
// One Author: this is a lifecycle transition called from the Game layer (AGENTS
// §2.1 — controller state transitions are a documented exemption; per-tick
// gameplay mutation stays Simulation-only).
// ================================================================

export interface ProbeRun {
  stage: ProbeStage
  /**
   * Only the seed is consumed. Deliberately narrower than `ProbeGame`: the
   * manifest generator builds this BEFORE it can compute `tick0Hash`, and the
   * browser hands it the real manifest game — both satisfy `{ seed }`.
   */
  game: Pick<ProbeGame, 'seed'>
}

/** Probe runs always use the difficulty baked into the manifest. */
export interface ProbeRunOptions {
  /** `difficulty` field of the manifest (e.g. 'hard'). */
  difficulty: string
  /** `lives` field of the manifest (the task definition — never relaxed). */
  lives: number
  /** `level` field of the manifest (player star level). */
  level: number
}

/**
 * Reset `world` to a clean menu-time state, then load the probe run.
 * Deterministic: the same (run, options) always leaves the same tick-0 world.
 */
export function applyProbeRun(world: World, run: ProbeRun, options: ProbeRunOptions): void {
  const { stage, game } = run
  const dkey = options.difficulty

  // ① run reset — every field that survives `loadStageData` is returned to its
  // fresh-`World` value, so tick 0 is identical no matter how much the same
  // World played before (that is what makes "retry" comparable to the
  // archived tick-0 hash, and what keeps game N's kills out of game N+1's
  // verdict evidence). `highScore` is deliberately NOT reset — it is the
  // persisted all-time best, not run state.
  world.state = 'menu'
  world.score = 0
  world.score2 = 0
  world.killCount = 0
  world.playTimeMs = 0
  world.coop = false
  world.spectate = false
  world.spectateDual = false
  // Both player tanks must be gone BEFORE `loadStageData` spawns the new one.
  // `spawnPlayer` relocates to the nearest free cell when the spawn cell is
  // occupied, and `allTanks` still holds the PREVIOUS run's player (loadStageData
  // never nulls it) — so a second run on the same World would silently spawn the
  // player one cell off the manifest spawn point and its tick-0 hash would drift.
  world.player = null
  world.player2 = null
  world.pendingDrops = []
  world.guardStock = 0
  world.frenzyStock = 0
  world.sacrificeStock = 0
  world.rewindStock = 0
  world.rewindPending = false
  world.mines = []
  world.empTimer = 0
  world.frame = 0
  world.bulletSeq = 0
  world.spawnSeqCounter = 0
  world.directiveSeqCounter = 0
  world.activeCommanderId = null
  world.stuckTicks = 0
  world.prevStuckCell = null

  // ② seed + RNG stream
  world.seed = game.seed
  world.rng.reseed(game.seed)

  // ③ difficulty trio (+ presentation-only theme)
  world.difficultyKey = dkey
  world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
  world.rules = RULES[dkey] ?? DEFAULT_RULES
  world.themeKey = DEFAULT_THEME
  world.theme = THEMES[DEFAULT_THEME]

  // ④ task-definition fields, before the player tank is spawned
  world.lives = options.lives
  world.lives2 = 0
  world.playerLevel = options.level
  world.playerLevel2 = options.level

  // ⑤ load the manifest stage with an explicit index 0
  world.loadStageData(stage, 0)
}
