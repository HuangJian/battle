import { SpriteArtistCore } from './SpriteArtistCore'

// Re-export the module-level helpers SpriteCache imports (drawn from the
// pre-split SpriteArtist.ts; relocated to SpriteArtistCore during the
// giant-file split — pure relocation, behavior identical).
export {
  drawWaterTile,
  AURA_BUCKETS,
  AURA_CONFIGS,
  auraBucket,
  drawAllyAuraPaths,
  drawHpLevelAuraPaths,
  drawCommanderAuraPaths,
} from './SpriteArtistCore'
export type { AuraConfig } from './SpriteArtistCore'

/**
 * SpriteArtist — draws all game sprites to a canvas context.
 *
 * §1.1 composition: the former three-mixin chain (Terrain/Tanks/Effects +
 * throwing stubs) became explicit slice objects constructed INSIDE
 * {@link SpriteArtistCore}'s constructor (TerrainSpriteSlice /
 * TankSpriteSlice / EffectSpriteSlice). The stub methods on Core are now real
 * delegators to those slices. Pure relocation — runtime behavior identical.
 */
export class SpriteArtist extends SpriteArtistCore {}
