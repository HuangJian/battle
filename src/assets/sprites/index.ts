// URL registry for the "Modern Retro" SVG asset library.
// Keys are consumed by SpriteArtist / SpriteLibrary.
// Vite emits these as hashed asset URLs via the `?url` suffix.
import player1 from './player1.svg?url'
import player2 from './player2.svg?url'
import enemy_basic from './enemy_basic.svg?url'
import enemy_fast from './enemy_fast.svg?url'
import enemy_power from './enemy_power.svg?url'
import enemy_armor from './enemy_armor.svg?url'
import tank_ally from './tank.ally.svg?url'
import tank_decoy from './tank.decoy.svg?url'
import base from './base.svg?url'
import base_ruins from './base_ruins.svg?url'
import bullet from './bullet.svg?url'
import brick from './brick.svg?url'
import water from './water.svg?url'
import forest from './forest.svg?url'
import item_star from './item_star.svg?url'
import item_bomb from './item_bomb.svg?url'
import item_shield from './item_shield.svg?url'
import item_freeze from './item_freeze.svg?url'
import item_tank from './item_tank.svg?url'
import item_fence from './item_fence.svg?url'
import item_boat from './item_boat.svg?url'
import item_frenzy from './item_frenzy.svg?url'
import item_sacrifice from './item_sacrifice.svg?url'
import item_guard from './item_guard.svg?url'
import item_repair from './item_repair.svg?url'
import item_decoy from './item_decoy.svg?url'
import explosion from './explosion.svg?url'
import fx_shield from './fx_shield.svg?url'
import fx_starbuf1 from './fx_starbuf1.svg?url'
import fx_starbuf2 from './fx_starbuf2.svg?url'
import fx_starbuf3 from './fx_starbuf3.svg?url'
import fx_hit0 from './fx_hit0.svg?url'
import fx_hit1 from './fx_hit1.svg?url'
import fx_hit2 from './fx_hit2.svg?url'
import fx_hit3 from './fx_hit3.svg?url'
import fx_hit4 from './fx_hit4.svg?url'
import fx_insignia_rookie from './fx_insignia_rookie.svg?url'
import fx_insignia_soldier from './fx_insignia_soldier.svg?url'
import fx_insignia_veteran from './fx_insignia_veteran.svg?url'

export const SPRITE_URLS: Record<string, string> = {
  // Tanks (face "up"; renderer rotates per direction)
  'tank.player1': player1,
  'tank.player2': player2,
  'tank.basic': enemy_basic,
  'tank.fast': enemy_fast,
  'tank.power': enemy_power,
  'tank.armor': enemy_armor,
  'tank.ally': tank_ally,
  'tank.decoy': tank_decoy,
  // Terrain
  'terrain.base': base,
  'terrain.base_ruins': base_ruins,
  'terrain.brick': brick,
  'terrain.water': water,
  'terrain.forest': forest,
  // Projectiles / items
  bullet,
  'item.star': item_star,
  'item.bomb': item_bomb,
  'item.shield': item_shield,
  'item.freeze': item_freeze,
  'item.tank': item_tank,
  'item.fence': item_fence,
  'item.boat': item_boat,
  'item.frenzy': item_frenzy,
  'item.sacrifice': item_sacrifice,
  'item.guard': item_guard,
  'item.repair': item_repair,
  'item.decoy': item_decoy,
  // Effects
  'fx.explosion': explosion,
  'fx.shield': fx_shield,
  'fx.starbuf1': fx_starbuf1,
  'fx.starbuf2': fx_starbuf2,
  'fx.starbuf3': fx_starbuf3,
  'fx.hit0': fx_hit0,
  'fx.hit1': fx_hit1,
  'fx.hit2': fx_hit2,
  'fx.hit3': fx_hit3,
  'fx.hit4': fx_hit4,
  // Rank insignia (Rookie / Soldier / Veteran): chevron overlays
  // composited on the enemy hull, drawn after the hull + hit overlay
  // and before the commander crown (plan §6 — crown-xor-insignia: a
  // Commander draws the crown INSTEAD, never both; None draws nothing).
  'fx.insignia.rookie': fx_insignia_rookie,
  'fx.insignia.soldier': fx_insignia_soldier,
  'fx.insignia.veteran': fx_insignia_veteran,
}

export type SpriteKey = keyof typeof SPRITE_URLS

/**
 * All registered sprite keys with the given prefix (`'item.'`, `'tank.'`, …).
 * Single-source derivation for the key maps / pre-rasterization lists in
 * SpriteCache and SpriteArtist — adding an SVG here automatically propagates
 * to every consumer (plan/refactor.trae.md §2.2).
 */
export function spriteKeys(prefix: string): string[] {
  return Object.keys(SPRITE_URLS).filter((k) => k.startsWith(prefix))
}
