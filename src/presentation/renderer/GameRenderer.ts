import { GameRendererCore } from './GameRendererCore'

/**
 * GameRenderer — renders the game world to a canvas.
 *
 * §1.1 composition: the former three-mixin chain (Terrain/Entities/Effects +
 * throwing stubs) became explicit slice objects constructed INSIDE
 * {@link GameRendererCore}'s constructor (TerrainRenderSlice /
 * EntityRenderSlice / EffectsRenderSlice). The stub methods on Core are now
 * real delegators to those slices. Pure relocation — runtime behavior is
 * identical to both the pre-split single class and the mixin era.
 */
export class GameRenderer extends GameRendererCore {}
