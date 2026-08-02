import { GameRendererCore } from './GameRendererCore'
import { GameRendererTerrainMixin } from './GameRendererTerrain'
import { GameRendererEntitiesMixin } from './GameRendererEntities'
import { GameRendererEffectsMixin } from './GameRendererEffects'

/**
 * GameRenderer — renders the game world to a canvas.
 *
 * Giant-file split: the class is composed from `GameRendererCore` (fields,
 * constructor, public API, `render()` orchestrator) plus three subsystem
 * mixins — Terrain (terrain/water caches + blits), Entities (tanks/bullets/
 * power-ups), Effects (explosions/particles/popups/vignette). Pure
 * relocation — runtime behavior is identical to the pre-split single class.
 */
export class GameRenderer extends GameRendererEffectsMixin(
  GameRendererEntitiesMixin(GameRendererTerrainMixin(GameRendererCore)),
) {}
