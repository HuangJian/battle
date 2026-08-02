import type { World } from '../../game/World'
import type { TileMap } from '../../game/TileMap'
import { CELL, GRID, FIELD } from '../../constants'
import type { ThemeColors, TerrainType } from '../../types'
import type { GameRendererCore } from './GameRendererCore'

type Ctor<T = object> = new (...args: any[]) => T

export function GameRendererTerrainMixin<TBase extends Ctor<GameRendererCore>>(Base: TBase) {
  return class GameRendererTerrain extends Base {
    /**
     * Composite the static terrain layer. Its own method (rather than an inline
     * `drawImage`) so the ablation benchmark can no-op exactly this stage, and so
     * the blit rectangle has one place to change.
     */
    protected blitTerrain(): void {
      this.ctx.drawImage(this.terrainCache, 0, 0, FIELD, FIELD)
    }

    /**
     * Composite the forest layer over the tanks. Skipped entirely when the stage
     * has no forest (`hasForest`); otherwise a single full-field drawImage. Source
     * coordinates are in cache bitmap pixels (the cache is FIELD*dpr wide);
     * destination coordinates are logical, because the main context already
     * carries the DPR transform.
     */
    protected blitForest(): void {
      if (!this.hasForest) return // stage has no forest — nothing to composite
      this.ctx.drawImage(this.forestCache, 0, 0, FIELD, FIELD)
    }

    /**
     * Recompute `hasForest` from the tile map. Cheap (676 cells) and only runs
     * when the terrain/forest cache changes, so it is not a per-frame cost.
     */
    protected recomputeHasForest(tm: TileMap): void {
      for (let r = 0; r < GRID; r++) {
        for (let c = 0; c < GRID; c++) {
          if (tm.get(c, r) === 'forest') {
            this.hasForest = true
            return
          }
        }
      }
      this.hasForest = false
    }

    // ---- Background ----

    protected fillBackground(world: World): void {
      const ctx = this.ctx
      const t = world.theme

      if (t.bgGradient) {
        if (!this.cachedBgGradient || this.cachedTheme !== t) {
          const g = ctx.createLinearGradient(0, -10, 0, FIELD + 10)
          g.addColorStop(0, t.bgGradient[0])
          g.addColorStop(1, t.bgGradient[1])
          this.cachedBgGradient = g
          this.cachedTheme = t
        }
        ctx.fillStyle = this.cachedBgGradient
      } else {
        ctx.fillStyle = t.bg
      }
      ctx.fillRect(-10, -10, FIELD + 20, FIELD + 20)
    }

    /**
     * Paint the background into a rect of the TERRAIN CACHE context (R5-A). The
     * bg is baked into the static cache so the per-frame full-field `fillRect` is
     * skipped when the camera is at rest. Uses the same gradient definition as
     * `fillBackground` (absolute user-space coords `0,-10 → 0,FIELD+10`) so a
     * sub-rect `fillRect(x,y,w,h)` paints the identical slice the main-canvas
     * fill would have produced there. Gradient is cached per-theme on this
     * context (separate from the main ctx's `cachedBgGradient` because
     * `CanvasGradient` is context-bound).
     */
    protected paintCacheBg(
      ctx: CanvasRenderingContext2D,
      x: number,
      y: number,
      w: number,
      h: number,
      theme: ThemeColors,
    ): void {
      if (theme.bgGradient) {
        if (!this.cachedCacheBgGradient || this.cachedCacheTheme !== theme) {
          const g = ctx.createLinearGradient(0, -10, 0, FIELD + 10)
          g.addColorStop(0, theme.bgGradient[0])
          g.addColorStop(1, theme.bgGradient[1])
          this.cachedCacheBgGradient = g
          this.cachedCacheTheme = theme
        }
        ctx.fillStyle = this.cachedCacheBgGradient
      } else {
        ctx.fillStyle = theme.bg
      }
      ctx.fillRect(x, y, w, h)
    }

    // ---- Terrain cache ----

    protected updateTerrainCache(world: World): void {
      const tm = world.tileMap
      if (!this.terrainCacheDirty && !tm.dirty && tm.dirtyCells.length === 0) return

      if (this.terrainCacheDirty || tm.dirty) {
        // Full rebuild — stage load, theme change, or base destruction (ruins).
        this.terrainCacheDirty = false
        tm.dirty = false
        this.rebuildTerrainCache(world)
        this.rebuildForestCache(world)
        this.recomputeHasForest(tm)
        this.scanWaterCells(world)
        tm.dirtyCells.length = 0
      } else {
        // Incremental rebuild — only the cells that actually changed (plus their
        // orthogonal neighbours, so auto-tiled steel/ice re-derive their patch
        // perimeter when a neighbour is destroyed). Turns "a brick got shot" from
        // a full 26×26 cache rebuild into O(changed cells).
        //
        // Zero-allocation path (P1): the previous implementation built a `Set`
        // and a 4-element tuple array per call — short-lived heap objects that
        // triggered minor GC on every terrain-damage frame. We now mark cells in
        // a reusable Uint8Array and collect the unique indices in a reusable
        // number[], then walk that list and clear marks in the same pass.
        const mark = this._dirtyMark
        const list = this._dirtyList
        // Phase 1 — mark dirty cells + orthogonal neighbours (dedup via mark).
        for (let i = 0; i < tm.dirtyCells.length; i++) {
          const idx = tm.dirtyCells[i]
          if (mark[idx] === 0) {
            mark[idx] = 1
            list.push(idx)
          }
          const c = idx % GRID
          const r = (idx - c) / GRID
          // Inline the 4-neighbour scan — avoids allocating a tuple array.
          if (c > 0) {
            const n = idx - 1
            if (mark[n] === 0) {
              mark[n] = 1
              list.push(n)
            }
          }
          if (c < GRID - 1) {
            const n = idx + 1
            if (mark[n] === 0) {
              mark[n] = 1
              list.push(n)
            }
          }
          if (r > 0) {
            const n = idx - GRID
            if (mark[n] === 0) {
              mark[n] = 1
              list.push(n)
            }
          }
          if (r < GRID - 1) {
            const n = idx + GRID
            if (mark[n] === 0) {
              mark[n] = 1
              list.push(n)
            }
          }
        }
        // Phase 2 — repaint each marked cell, then clear its mark in the same
        // iteration so the buffers are clean for the next call.
        const artist = this.artist
        const savedCtx = artist.ctx // restore after — draw helpers use artist.ctx
        for (let i = 0; i < list.length; i++) {
          const idx = list[i]
          mark[idx] = 0 // reset for next call
          const c = idx % GRID
          const r = (idx - c) / GRID
          const type = tm.get(c, r)
          if (type === 'water') continue // water isn't in the terrain cache
          if (type === 'forest') {
            artist.ctx = this.forestCacheCtx
            this.redrawForestCell(c, r)
            // Repaint bg on the terrain cache under the forest overlay (R5-A: the
            // cache is opaque — a `clearRect` would punch a transparent hole and
            // show stale canvas content beneath the blit). Forest hides terrain,
            // so only the bg should appear under the (separately drawn) forest
            // overlay.
            this.paintCacheBg(
              this.terrainCacheCtx,
              c * CELL,
              r * CELL,
              CELL,
              CELL,
              this.artist.theme,
            )
          } else {
            artist.ctx = this.terrainCacheCtx
            this.redrawTerrainCell(c, r, type, tm)
            // Clear any stale forest overlay left at this cell.
            this.forestCacheCtx.clearRect(c * CELL, r * CELL, CELL, CELL)
          }
        }
        list.length = 0
        artist.ctx = savedCtx
        // A destroyed forest cell can shrink the box and a newly drawn one can
        // grow it, so recompute rather than only expanding — otherwise the box
        // would ratchet outward and lose the saving over a long stage.
        this.recomputeHasForest(tm)
        tm.dirtyCells.length = 0
      }
    }

    /**
     * Reusable 4-slot neighbour mask buffer (P4). Avoids allocating a fresh
     * `[boolean, boolean, boolean, boolean]` tuple + the `at` closure on every
     * call to {@link neighborMask}. Reads are `arr[0..3]` = (n, e, s, w).
     * Callers must consume the values before the next call to `neighborMask`.
     */
    private _nmask: boolean[] = [false, false, false, false]

    /**
     * Fill {@link _nmask} with the 4-neighbour same-type flags for cell (c, r).
     *
     * P4: previously returned a fresh 4-tuple AND allocated a closure (`at`) per
     * call. With 4 call sites inside the per-dirty-cell `redrawTerrainCell` /
     * `rebuildTerrainCache` paths, a brick-destroy burst could allocate ~12
     * short-lived objects (4 calls × (1 tuple + 1 closure + destructuring
     * intermediate)). Inlined bounds checks + reusable buffer = zero allocation.
     */
    protected neighborMask(tm: TileMap, c: number, r: number, type: TerrainType): void {
      // North
      const hasN = r > 0 && tm.get(c, r - 1) === type
      // East
      const hasE = c < GRID - 1 && tm.get(c + 1, r) === type
      // South
      const hasS = r < GRID - 1 && tm.get(c, r + 1) === type
      // West
      const hasW = c > 0 && tm.get(c - 1, r) === type
      const m = this._nmask
      m[0] = hasN
      m[1] = hasE
      m[2] = hasS
      m[3] = hasW
    }

    /**
     * Redraw a single terrain cell in place (used for incremental updates).
     * Reproduces exactly what the full rebuild would draw for that cell:
     * flat clear for empty space, or the tile art for a solid tile.
     */
    protected redrawTerrainCell(c: number, r: number, type: TerrainType, tm: TileMap): void {
      const ctx = this.terrainCacheCtx
      const x = c * CELL
      const y = r * CELL
      // Repaint bg for this cell (R5-A: bg is baked into the opaque cache, so a
      // destroyed tile reveals the bg rather than transparency). Equivalent to
      // the old `clearRect` for the visual result, because the cache is composited
      // as an opaque blit — there is no "behind the cache" to show through.
      this.paintCacheBg(ctx, x, y, CELL, CELL, this.artist.theme)

      if (type === 'empty') {
        // Empty space: clean flat ground (bg painted above).
        return
      }

      const artist = this.artist
      switch (type) {
        case 'brick':
          artist.drawBrick(x, y, CELL)
          break
        case 'steel': {
          this.neighborMask(tm, c, r, 'steel')
          const m = this._nmask
          artist.drawSteel(x, y, CELL, m[0], m[1], m[2], m[3])
          break
        }
        case 'ice': {
          this.neighborMask(tm, c, r, 'ice')
          const m = this._nmask
          artist.drawIce(x, y, CELL, m[0], m[1], m[2], m[3])
          break
        }
        case 'base': {
          // The base is ONE crystal spanning 2×2, drawn from the block's
          // TOP-LEFT cell. This cell may be a NON-top-left base cell reached via
          // neighbour expansion (e.g. an adjacent brick was destroyed). If we only
          // repainted this single 16×16 cell and drew nothing (because
          // isBaseTopLeft is false), the chunk of the crystal overlapping this
          // cell would be erased forever — the reported "base loses a piece" bug.
          // So always walk back to the block's top-left and repaint the full
          // crystal.
          let tlC = c
          let tlR = r
          while (tlC > 0 && tm.get(tlC - 1, tlR) === 'base') tlC--
          while (tlR > 0 && tm.get(tlC, tlR - 1) === 'base') tlR--
          artist.drawBase(tlC * CELL, tlR * CELL, CELL * 2, false, this.baseDamageFrac)
          break
        }
      }
    }

    /** Redraw a single forest cell in the forest cache (clear or draw). */
    protected redrawForestCell(c: number, r: number): void {
      const ctx = this.forestCacheCtx
      const x = c * CELL
      const y = r * CELL
      ctx.clearRect(x, y, CELL, CELL)
      this.artist.drawForest(x, y, CELL)
    }

    protected rebuildTerrainCache(world: World): void {
      const ctx = this.terrainCacheCtx
      const tm = world.tileMap
      const artist = this.artist
      const savedCtx = artist.ctx
      artist.ctx = ctx

      // Bake the background into the static cache (R5-A). The cache becomes
      // opaque (bg + tiles), so the per-frame blit is a fast source-copy and the
      // separate full-field `fillRect` is eliminated on the camera-at-rest path.
      // Replaces the old `clearRect(0,0,FIELD,FIELD)` — empty cells now carry the
      // bg colour/gradient instead of transparency, which is what makes the
      // single-blit replacement visually equivalent.
      this.paintCacheBg(ctx, 0, 0, FIELD, FIELD, world.theme)

      // Static terrain only (NO water — water is rendered separately each frame).
      // No grid lines on empty ground — flat cell feel, see DECISIONS.md §29.
      for (let r = 0; r < GRID; r++) {
        for (let c = 0; c < GRID; c++) {
          const type = tm.get(c, r)
          if (type === 'empty' || type === 'forest' || type === 'water') continue

          const x = c * CELL
          const y = r * CELL

          switch (type) {
            case 'brick':
              artist.drawBrick(x, y, CELL)
              break
            case 'steel': {
              this.neighborMask(tm, c, r, 'steel')
              const m = this._nmask
              artist.drawSteel(x, y, CELL, m[0], m[1], m[2], m[3])
              break
            }
            case 'ice': {
              this.neighborMask(tm, c, r, 'ice')
              const m = this._nmask
              artist.drawIce(x, y, CELL, m[0], m[1], m[2], m[3])
              break
            }
            case 'base':
              // Draw the whole 2×2 base as ONE crystal (only from its top-left cell).
              if (tm.isBaseTopLeft(c, r)) {
                artist.drawBase(c * CELL, r * CELL, CELL * 2, false, this.baseDamageFrac)
              }
              break
          }
        }
      }

      // Destroyed base ruins — one shattered crystal across the 2×2 block
      if (tm.isBaseDestroyed()) {
        const bp = tm.getBasePos()
        if (bp) artist.drawBase(bp.x, bp.y, CELL * 2, true)
      }

      artist.ctx = savedCtx
    }

    protected rebuildForestCache(world: World): void {
      const ctx = this.forestCacheCtx
      const tm = world.tileMap
      const artist = this.artist
      const savedCtx = artist.ctx
      artist.ctx = ctx

      ctx.clearRect(0, 0, FIELD, FIELD)

      for (let r = 0; r < GRID; r++) {
        for (let c = 0; c < GRID; c++) {
          const type = tm.get(c, r)
          if (type !== 'forest') continue
          artist.drawForest(c * CELL, r * CELL, CELL)
        }
      }

      artist.ctx = savedCtx
    }

    /** Scan and cache water cell positions for efficient per-frame rendering. */
    protected scanWaterCells(world: World): void {
      this.waterCells = []
      const tm = world.tileMap
      for (let r = 0; r < GRID; r++) {
        for (let c = 0; c < GRID; c++) {
          if (tm.get(c, r) === 'water') {
            this.waterCells.push({ c, r })
          }
        }
      }
    }

    // ---- Water (direct render each frame — animated, few tiles) ----

    protected renderWater(world: World): void {
      const artist = this.artist
      const frame = world.frame
      for (let i = 0; i < this.waterCells.length; i++) {
        const { c, r } = this.waterCells[i]
        artist.drawWater(c * CELL, r * CELL, CELL, frame)
      }
    }
  }
}
