import type { World } from './World'
import type { TacticalIntelligence } from '../ai/TacticalIntelligence'
import type { SpawnSystem } from './SimulationSpawn'
import type { PlayerSystem } from './SimulationPlayer'
import type { EnemiesSystem } from './SimulationEnemies'
import type { CombatSystem } from './SimulationCombat'
import type { PowerUpSystem } from './SimulationPowerUps'
import type { EffectsSystem } from './SimulationEffects'

/**
 * SimulationSystems — the inter-system dependency registry (§1.1).
 *
 * One mutable bundle shared by every subsystem. Constructed empty in
 * `Simulation`'s constructor, populated immediately after — the systems only
 * dereference their siblings when methods RUN (never during construction), so
 * the Player↔PowerUps / Enemies↔Effects cycles are safe without setters.
 *
 * Type-only module: importing it creates no runtime cycle.
 */
export interface SimulationSystems {
  world: World
  spawn: SpawnSystem
  player: PlayerSystem
  enemies: EnemiesSystem
  combat: CombatSystem
  powerUps: PowerUpSystem
  effects: EffectsSystem
  /** Tactical Intelligence Framework — owns all enemy decision-making. */
  ai: TacticalIntelligence
}
