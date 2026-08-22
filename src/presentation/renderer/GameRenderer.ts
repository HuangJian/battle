import { GameRendererCore } from './GameRendererCore'

/**
 * GameRenderer — renders the game world to a canvas.
 *
 * §1.1 composition: the former three-mixin chain (Terrain/Entities/Effects +
 * throwing stubs) became explicit slice objects constructed INSIDE
 * {@link GameRendererCore}'s constructor (TerrainRenderSlice /
 * EntityRenderSlice / EffectsRenderSlice). Core's `render()` orchestrates the
 * slices directly — there are no per-subsystem methods left on Core to
 * override (plan/refactor.zcode.md §2.1). Pure relocation — runtime behavior
 * is identical to both the pre-split single class and the mixin era.
 */
export class GameRenderer extends GameRendererCore {}
