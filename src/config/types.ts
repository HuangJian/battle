import type { TankKind } from '../types'

/**
 * config/types.ts — data-contract types for the config layer (§2.6).
 *
 * These describe pure DATA: difficulty profiles, stage grids, and theme
 * palettes. They moved here from the root `src/types.ts` (which keeps
 * compatibility re-exports); the config modules under `src/config/` are their
 * canonical home, matching AGENTS §2.4 "data over code".
 */

export interface DifficultyConfig {
  name: string
  /**
   * Difficulty affects enemy AI ONLY through the spawn-time tier distribution
   * (`DIFFICULTY_TIER_DISTRIBUTION` in src/ai/config.ts). Tier capability
   * numbers are FIXED — difficulty never scales them, and it must NEVER scale
   * enemy combat stats (armor / speed / bullet speed / HP), which is explicitly
   * forbidden by DECISIONS.md. Lives and the player's starting star level are
   * player-side resources, not enemy combat power.
   *
   * Scoped carve-out [D10]: a Commander-tier spawn receives the +15% elite
   * combat boost (`applyEliteModifier`), and the Commander probability is
   * difficulty-driven. Provisional — see plan/AI-Tier-System-Revision.md §5.3.
   */
  startLives: number
  playerStartLevel: number
}

export interface StageData {
  id: number
  name: string
  /** 26×26 grid (one char per 16px sub-block): '.', 'b', 's', 'w', 'f', 'i', 'E' */
  tiles: string[]
  /** Enemy queue: list of tank kinds */
  enemies: TankKind[]
  /**
   * Optional override for the total enemy count this stage (plan/God-AI-Curriculum
   * §3 Gap A). When omitted, falls back to `ENEMIES_PER_STAGE` (20). The enemy
   * `enemies` array still determines the *kind* queue (cycled if shorter); this
   * field controls how many enemies spawn in total. Data-over-code (AGENTS §2.4) —
   * existing stages are unaffected (they don't set this field).
   */
  enemyCount?: number
  /**
   * Optional override for the player spawn position in sub-block coords (plan §3.5
   * 影响 1). When omitted, falls back to `PLAYER_SPAWN`. Curriculum arenas use this
   * to place the player inside the open area instead of the default bottom-left.
   */
  playerSpawn?: { col: number; row: number }
  /**
   * Optional override for enemy spawn positions in sub-block coords (plan §3.5
   * 影响 1). When omitted, falls back to `ENEMY_SPAWNS`. Curriculum arenas use this
   * to place enemies inside the open area.
   */
  enemySpawns?: { col: number; row: number }[]
}

export interface ThemeColors {
  bg: string
  /** Optional vertical gradient [top, bottom] used as the play-field background. */
  bgGradient?: [string, string]
  brick: string
  brickDark: string
  steel: string
  steelDark: string
  water: string
  waterDark: string
  forest: string
  forestDark: string
  ice: string
  base: string
  baseDark: string
  // Tank colors
  playerBody: string
  playerTurret: string
  playerBody2: string // level 2+
  playerBody3: string // level 3
  enemyBasic: string
  enemyFast: string
  enemyPower: string
  enemyArmor: string
  enemyArmorFlash: string
  // UI — canvas
  hudBg: string
  hudText: string
  hudAccent: string
  // Effects
  explosion1: string
  explosion2: string
  explosion3: string
  bullet: string
  bulletGlow: string
  powerUp: string
  powerUpGlow: string
  powerUpFence: string
  powerUpFenceGlow: string
  powerUpBoat: string
  powerUpBoatGlow: string
  spawn: string
  // UI — HTML overlay
  panelBg: string
  panelBorder: string
  panelShadow: string
  textPrimary: string
  textSecondary: string
  textMuted: string
  accentPrimary: string
  accentSecondary: string
  buttonBg: string
  buttonHover: string
  buttonActive: string
  overlayBg: string
  danger: string
  success: string
  // Ambient
  vignetteColor: string
}

/** Theme definition with metadata */
export interface ThemeDefinition {
  key: string
  name: string
  description: string
  colors: ThemeColors
}
