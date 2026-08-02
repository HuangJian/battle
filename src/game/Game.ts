import { GameCore } from './GameCore'
import { GameLoopMixin } from './GameLoop'
import { GameSnapshotMixin } from './GameSnapshot'
import { GameMenuMixin } from './GameMenu'
import { GameReplayMixin } from './GameReplay'

/**
 * Game — top-level orchestrator. Owns the game loop, wires all systems.
 *
 * The original 2096-line orchestrator was split into a base layer plus four
 * subsystem mixins, composed here (see DECISIONS for the refactor):
 *
 * - {@link GameCore} — fields, constructor, coop/spectate toggles, settings,
 *   `resetToMenu`, plus protected stubs for every mixin-provided method.
 * - {@link GameLoopMixin} — the vsync rAF loop driver + event handlers.
 * - {@link GameMenuMixin} — menu/pause input + theme/menu actions.
 * - {@link GameSnapshotMixin} — snapshot framework wiring + recovery flow.
 * - {@link GameReplayMixin} — replay recording/playback/browser/export.
 *
 * The stub methods on GameCore exist only so cross-mixin calls type-check
 * against the base; every mixin is always installed, so a stub is never
 * reached at runtime.
 */
export class Game extends GameReplayMixin(
  GameMenuMixin(GameSnapshotMixin(GameLoopMixin(GameCore))),
) {}
