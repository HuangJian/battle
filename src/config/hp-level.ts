/**
 * hp-level.ts — Tank HP Level visual configuration and level resolver.
 *
 * According to the HP level rules:
 * Base damage per hit = 100 HP.
 * Level 1: HP <= 100 (1 hit to kill) -> No extra visual aura.
 * Level 2: 100 < HP <= 200 (2 hits to kill) -> Single thin circle aura.
 * Level 3: 200 < HP <= 300 (3 hits to kill) -> Double pulsing ring aura.
 * Level 4: 300 < HP <= 400 (4 hits to kill) -> Diamond / Gear pulsing aura.
 * Level 5: 400 < HP <= 500 (5 hits to kill) -> Hexagon shield aura.
 * Level 6: 500 < HP <= 600 (6 hits to kill) -> Solar / Flame double radiation aura (elite/boss).
 */

export interface HpLevelConfig {
  level: number
  color: string
  shape: 'none' | 'square' | 'double-square' | 'jagged-square' | 'hexagon-square' | 'solar-square'
}

/**
 * Maps HP level (2~6) to visual styling config.
 * Colors use curated modern retro palette for high contrast and readability.
 */
export const HP_LEVEL_CONFIGS: Record<number, HpLevelConfig> = {
  1: { level: 1, color: '', shape: 'none' },
  2: { level: 2, color: '#2ecc71', shape: 'square' }, // Emerald Green (2 hits)
  3: { level: 3, color: '#3498db', shape: 'double-square' }, // Sky Blue (3 hits)
  4: { level: 4, color: '#9b59b6', shape: 'jagged-square' }, // Amethyst Purple (4 hits)
  5: { level: 5, color: '#e67e22', shape: 'hexagon-square' }, // Flame Orange (5 hits)
  6: { level: 6, color: '#e74c3c', shape: 'solar-square' }, // Crimson Red (6 hits)
}

/**
 * Calculate tank HP level based on current remaining HP.
 * Assumes 100 HP per standard hit (baseDamage = 100).
 *
 * @param hp Current remaining tank HP.
 * @param baseDamage Base damage per hit (default 100).
 * @returns HP level from 1 to 6.
 */
export function getHpLevel(hp: number, baseDamage = 100): number {
  if (hp <= 0) return 1
  const level = Math.ceil(hp / baseDamage)
  return Math.min(6, Math.max(1, level))
}
