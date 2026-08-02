import { SpriteArtistCore } from './SpriteArtistCore'
import { SpriteArtistTerrainMixin } from './SpriteArtistTerrain'
import { SpriteArtistTanksMixin } from './SpriteArtistTanks'
import { SpriteArtistEffectsMixin } from './SpriteArtistEffects'

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
 * Giant-file split: the class is composed from `SpriteArtistCore` (module
 * helpers, fields, constructor, setters, `drawSvgCentered`, stub API) plus
 * three subsystem mixins — Terrain (brick/steel/water/forest/ice/base),
 * Tanks (shadow/tank/player/enemy/ally/aura/insignia), Effects (bullet/
 * power-up/spawn/shield/explosion/HP-level-aura/commander-aura). Pure
 * relocation — runtime behavior is identical to the pre-split single class.
 */
export class SpriteArtist extends SpriteArtistEffectsMixin(
  SpriteArtistTanksMixin(SpriteArtistTerrainMixin(SpriteArtistCore)),
) {}
