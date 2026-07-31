/**
 * headless-canvas.ts — headless rendering harness substrate for render-bench.
 *
 * Provides three things the headless benchmark needs, all isolated to tools/perf
 * so they can never leak into production app code (AGENTS §5 / plan §5.2 / review B4):
 *
 *  1. `installHeadlessShims()` — installs a `globalThis.OffscreenCanvas` backed by
 *     @napi-rs/canvas (Skia) so `utils/canvas.ts` works under Bun, and sets the
 *     `__RENDER_BENCH__` scope flag (double-init guard).
 *  2. `loadSpritesFromDisk()` — decodes the SVG sprite library from disk into
 *     CanvasImageSources, bypassing `index.ts`'s Vite `?url` imports (Bun can't
 *     resolve those) and the `SpriteLibrary.load()` DOM path.
 *  3. `createRenderTarget()` — a fake canvas whose 2D context is a counting Proxy
 *     that tallies draw-calls and save/restore pairs on the MAIN render context
 *     (offscreen caches are intentionally NOT proxied — plan §5.3).
 */
import { createCanvas, loadImage } from '@napi-rs/canvas'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SpriteLibrary } from '../../src/presentation/renderer/SpriteLibrary'

// ---------------------------------------------------------------------------
// Scope guard + OffscreenCanvas shim
// ---------------------------------------------------------------------------

export function installHeadlessShims(): void {
  if ((globalThis as any).__RENDER_BENCH__) {
    throw new Error('[headless-canvas] render-bench shim already installed (double init?)')
  }
  ;(globalThis as any).__RENDER_BENCH__ = true
  // utils/canvas.ts:23 branches on `typeof OffscreenCanvas !== 'undefined'`. Under
  // Bun there is none, so we provide the Skia-backed one. In a real browser this
  // branch is never reached because this module is never imported by app code.
  if (typeof (globalThis as any).OffscreenCanvas === 'undefined') {
    ;(globalThis as any).OffscreenCanvas = createCanvas
  }
}

// ---------------------------------------------------------------------------
// Sprite disk loader — mirrors src/assets/sprites/index.ts SPRITE_URLS keys.
// ---------------------------------------------------------------------------

export const SPRITE_FILES: Record<string, string> = {
  'tank.player1': 'player1.svg',
  'tank.player2': 'player2.svg',
  'tank.basic': 'enemy_basic.svg',
  'tank.fast': 'enemy_fast.svg',
  'tank.power': 'enemy_power.svg',
  'tank.armor': 'enemy_armor.svg',
  'tank.ally': 'tank.ally.svg',
  'terrain.base': 'base.svg',
  'terrain.base_ruins': 'base_ruins.svg',
  'terrain.brick': 'brick.svg',
  'terrain.water': 'water.svg',
  'terrain.forest': 'forest.svg',
  bullet: 'bullet.svg',
  'item.star': 'item_star.svg',
  'item.bomb': 'item_bomb.svg',
  'item.shield': 'item_shield.svg',
  'item.freeze': 'item_freeze.svg',
  'item.tank': 'item_tank.svg',
  'item.fence': 'item_fence.svg',
  'item.boat': 'item_boat.svg',
  'item.frenzy': 'item_frenzy.svg',
  'item.sacrifice': 'item_sacrifice.svg',
  'item.guard': 'item_guard.svg',
  'fx.explosion': 'explosion.svg',
  'fx.shield': 'fx_shield.svg',
  'fx.starbuf1': 'fx_starbuf1.svg',
  'fx.starbuf2': 'fx_starbuf2.svg',
  'fx.starbuf3': 'fx_starbuf3.svg',
  'fx.hit0': 'fx_hit0.svg',
  'fx.hit1': 'fx_hit1.svg',
  'fx.hit2': 'fx_hit2.svg',
  'fx.hit3': 'fx_hit3.svg',
  'fx.hit4': 'fx_hit4.svg',
  'fx.insignia.rookie': 'fx_insignia_rookie.svg',
  'fx.insignia.soldier': 'fx_insignia_soldier.svg',
  'fx.insignia.veteran': 'fx_insignia_veteran.svg',
}

export async function loadSpritesFromDisk(): Promise<Map<string, any>> {
  const dir = join(import.meta.dir, '../../src/assets/sprites')
  const map = new Map<string, any>()
  for (const [key, file] of Object.entries(SPRITE_FILES)) {
    const buf = readFileSync(join(dir, file))
    try {
      map.set(key, await loadImage(buf))
    } catch (e) {
      console.warn(`[headless-canvas] sprite decode failed: ${file} — ${(e as Error).message}`)
    }
  }
  return map
}

/**
 * Build a real SpriteLibrary fed from disk-decoded sprites via the
 * `loadFromSources` injection point (plan §5.2). This exercises the exact
 * production sprite-loading code path, so benchmark draw counts reflect what
 * the running game does — not a parallel duck-typed clone.
 */
export function buildLib(spriteMap: Map<string, any>): SpriteLibrary {
  const lib = new SpriteLibrary()
  const sources: Record<string, any> = {}
  for (const [k, v] of spriteMap) sources[k] = v
  lib.loadFromSources(sources)
  return lib
}

// ---------------------------------------------------------------------------
// Counting 2D context (main render context only)
// ---------------------------------------------------------------------------

const DRAW_METHODS = new Set<string>([
  'drawImage',
  'fillRect',
  'strokeRect',
  'clearRect',
  'fill',
  'stroke',
  'putImageData',
])

export interface RenderTarget {
  /** Real napi canvas — used for `--snapshot` PNG export and pixel-diff capture. */
  realCanvas: any
  /** The real Skia 2D context (draws land here; read pixels back via getImageData). */
  realCtx: any
  /** Fake canvas handed to GameRenderer (getContext returns the proxy). */
  fakeCanvas: any
  /** Proxy 2D context (all draw methods forwarded to the real ctx). */
  ctx: any
  /** Per-frame draw-call tallies. Reset by the bench before each frame. */
  counts: { draw: number; saveRestore: number }
}

/**
 * @param counting When false the renderer is handed the *real* Skia context
 * instead of the counting Proxy. The Proxy intercepts every property get/set on
 * the 2D context, which adds a fixed overhead to every canvas state write — fine
 * when measuring whole frames or counting draw calls, but it swamps the signal
 * when micro-timing a single render stage (`stage-bench.ts`). Draw counts stay
 * at zero in this mode.
 */
export function createRenderTarget(field: number, dpr: number, counting = true): RenderTarget {
  const realCanvas = createCanvas(field * dpr, field * dpr)
  const realCtx = realCanvas.getContext('2d')
  const counts = { draw: 0, saveRestore: 0 }
  if (!counting) {
    return {
      realCanvas,
      realCtx,
      fakeCanvas: { width: field * dpr, height: field * dpr, style: {}, getContext: () => realCtx },
      ctx: realCtx,
      counts,
    }
  }
  const handler: ProxyHandler<any> = {
    get(t: any, p: any) {
      const v = t[p]
      if (typeof v === 'function') {
        return (...a: any[]) => {
          if (p === 'save' || p === 'restore') counts.saveRestore++
          else if (DRAW_METHODS.has(p as string)) counts.draw++
          return v.apply(t, a)
        }
      }
      return v
    },
    set(t: any, p: any, val: any) {
      t[p] = val
      return true
    },
  }
  const ctx = new Proxy(realCtx, handler)
  const fakeCanvas = {
    width: field * dpr,
    height: field * dpr,
    style: {},
    getContext: () => ctx,
  }
  return { realCanvas, realCtx, fakeCanvas, ctx, counts }
}
