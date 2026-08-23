/**
 * SpriteKeyMaps — registry-derived key maps (plan/refactor.zcode.md §2.2).
 *
 * Single source of truth is SPRITE_URLS (src/assets/sprites). These maps are
 * DERIVED from it via prefix filtering, so registering a new SVG propagates
 * everywhere automatically — the hand-copied lists this replaces had already
 * drifted (5 registered items were never pre-rasterized; tank.player2 took
 * the slow per-frame SVG path).
 *
 * This is a LEAF module (imports only the asset registry): the maps are built
 * at module-init time, so it must stay outside the SpriteArtistCore ↔ slice
 * import cycle.
 */
import { spriteKeys } from '../../assets/sprites'

/**
 * Enemy tank kind → sprite key: every `tank.*` registry key except the four
 * special player/ally hulls. A new enemy SVG added to the registry joins this
 * map automatically.
 */
const NON_ENEMY_TANK_KEYS = ['tank.player1', 'tank.player2', 'tank.ally', 'tank.decoy']
export const TANK_KEY_MAP: Record<string, string> = Object.fromEntries(
  spriteKeys('tank.')
    .filter((k) => !NON_ENEMY_TANK_KEYS.includes(k))
    .map((k) => [k.slice('tank.'.length), k]),
)

/**
 * Power-up type → sprite key: every `item.*` registry key. Types without
 * registered art (e.g. 'rewind') are intentionally unmapped and render via
 * the procedural pentagon fallback in drawPowerUp.
 */
export const ITEM_KEY_MAP: Record<string, string> = Object.fromEntries(
  spriteKeys('item.').map((k) => [k.slice('item.'.length), k]),
)
