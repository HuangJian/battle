import { SimulationCore } from './SimulationCore'
import { SimulationSpawnMixin } from './SimulationSpawn'
import { SimulationPlayerMixin } from './SimulationPlayer'
import { SimulationEnemiesMixin } from './SimulationEnemies'
import { SimulationCombatMixin } from './SimulationCombat'
import { SimulationPowerUpsMixin } from './SimulationPowerUps'
import { SimulationEffectsMixin } from './SimulationEffects'

/**
 * Simulation — the only layer allowed to modify the World. Runs all game
 * systems in a fixed timestep.
 *
 * The original 2449-line simulation was split into a base layer plus five
 * subsystem mixins, composed here (see DECISIONS for the refactor):
 *
 * - {@link SimulationCore} — fields, constructor, deferred coop/spectate
 *   toggles, `tick` dispatch, the `updatePlaying` orchestrator, `togglePause`,
 *   plus protected stubs for every mixin-provided method.
 * - {@link SimulationSpawnMixin} — spawn timers + queue-driven spawning.
 * - {@link SimulationPlayerMixin} — player input, frenzy, decoy, mines.
 * - {@link SimulationEnemiesMixin} — allied guards, sacrifice AoE, enemy AI.
 * - {@link SimulationCombatMixin} — movement, fire, bullets, base damage.
 * - {@link SimulationPowerUpsMixin} — drops, pickups, fence/boat/repair.
 * - {@link SimulationEffectsMixin} — explosions, popups, win/lose conditions.
 *
 * The stub methods on SimulationCore exist only so cross-mixin calls (and the
 * `updatePlaying` orchestrator) type-check against the base; every mixin is
 * always installed, so a stub is never reached at runtime.
 */
export class Simulation extends SimulationEffectsMixin(
  SimulationPowerUpsMixin(
    SimulationCombatMixin(
      SimulationEnemiesMixin(SimulationPlayerMixin(SimulationSpawnMixin(SimulationCore))),
    ),
  ),
) {}
