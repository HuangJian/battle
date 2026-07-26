/**
 * Regression test for the "base loses a piece" rendering bug.
 *
 * The base (eagle) is drawn as a SINGLE 2×2 crystal originating from its
 * top-left cell. The terrain cache uses incremental redraws: when a brick is
 * destroyed, only the changed cell + its orthogonal neighbours are repainted.
 *
 * If a destroyed brick sits next to a NON-top-left base cell, an older build
 * cleared that 16×16 base cell (erasing the chunk of the crystal covering it)
 * but then drew nothing there (because it wasn't the top-left), permanently
 * deleting a corner of the base art.
 *
 * This test drives the real GameRenderer+TileMap through two renders (a full
 * rebuild, then an incremental redraw after destroying the adjacent brick) and
 * asserts the full 2×2 base crystal is repainted — i.e. NO cleared gap.
 */
import { describe, it, expect, beforeAll } from 'bun:test'
import { mock } from 'bun:test'

const FAKE_BASE_IMG = { __base: true } as unknown as CanvasImageSource

// ---- Recording fake 2D context -------------------------------------------
interface Recorder {
  baseDraws: Array<{ x: number; y: number; w: number; h: number }>
}
function makeFakeCtx(rec: Recorder): any {
  const noop = () => {}
  return {
    // properties (writable, ignored)
    imageSmoothingEnabled: true,
    globalAlpha: 1,
    fillStyle: '#000',
    strokeStyle: '#000',
    lineWidth: 1,
    font: '',
    textAlign: 'left',
    // recording draw
    drawImage(img: any, x: number, y: number, w: number, h: number) {
      if (img === FAKE_BASE_IMG) rec.baseDraws.push({ x, y, w, h })
    },
    // everything else is a no-op
    clearRect: noop,
    fillRect: noop,
    strokeRect: noop,
    beginPath: noop,
    moveTo: noop,
    lineTo: noop,
    closePath: noop,
    fill: noop,
    stroke: noop,
    save: noop,
    restore: noop,
    translate: noop,
    rotate: noop,
    scale: noop,
    setTransform: noop,
    ellipse: noop,
    arc: noop,
    fillText: noop,
    measureText: () => ({ width: 0 }),
    createLinearGradient: () => ({ addColorStop: noop }),
    createRadialGradient: () => ({ addColorStop: noop }),
  }
}

const rec: Recorder = { baseDraws: [] }
const fakeCtx = makeFakeCtx(rec)

// Mock the offscreen canvas factory so we control the 2D context.
// Use the absolute realpath so it matches GameRenderer's resolved import.
mock.module('/Users/hj/dev/github/battle/src/utils/canvas', () => ({
  createOffscreenCanvas: (_w: number, _h: number, _s?: number) => ({
    canvas: {} as any,
    ctx: fakeCtx,
  }),
}))

// NOTE: GameRenderer is imported dynamically *inside* the test (after
// mock.module registers the canvas mock) because static imports are hoisted
// above mock.module and would load the real module first.
import { TileMap } from '../src/game/TileMap'
import type { StageData } from '../src/types'
import { CELL, GRID } from '../src/constants'

function buildStage(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
  const set = (c: number, r: number, ch: string) => {
    const row = tiles[r].split('')
    row[c] = ch
    tiles[r] = row.join('')
  }
  // Base (eagle) = 2×2 block at cols 12-13, rows 24-25.
  set(12, 24, 'E')
  set(13, 24, 'E')
  set(12, 25, 'E')
  set(13, 25, 'E')
  // Brick directly ABOVE the non-top-left base cell (13,24).
  set(13, 23, 'b')
  return { id: 1, name: 'bug', tiles, enemies: [] }
}

function makeWorld(theme: any, tileMap: TileMap) {
  return {
    theme,
    tileMap,
    frame: 0,
    allTanks: [],
    bullets: [],
    powerUps: [],
    explosions: [],
    popups: [],
  } as any
}

beforeAll(() => {
  // Reset recorder before any assertions.
  rec.baseDraws = []
})

describe('base crystal integrity during incremental redraw', () => {
  it('repaints the full 2×2 base when an adjacent non-top-left brick is destroyed', async () => {
    const { GameRenderer } = await import('../src/presentation/renderer/GameRenderer')
    const theme = { bg: '#000' } as any
    const tileMap = new TileMap()
    tileMap.loadStage(buildStage())

    const fakeCanvas: any = {
      width: 0,
      height: 0,
      getContext: () => fakeCtx,
    }
    const lib: any = { get: (k: string) => (k === 'terrain.base' ? FAKE_BASE_IMG : undefined) }

    const renderer = new GameRenderer(
      fakeCanvas,
      { getOffset: () => ({ x: 0, y: 0 }) } as any,
      {} as any,
      { pool: [], activeCount: 0 } as any,
      { getFlash: () => null } as any,
      1,
      lib,
    )
    renderer.setTheme(theme)

    const world = makeWorld(theme, tileMap)

    // Render #1 → full terrain-cache rebuild.
    rec.baseDraws = []
    renderer.render(world)
    // Expect exactly one full-base draw from the top-left cell (col 12, row 24).
    expect(rec.baseDraws).toEqual([{ x: 12 * CELL, y: 24 * CELL, w: CELL * 2, h: CELL * 2 }])

    // Destroy the brick above the non-top-left base cell (13,24).
    // This pushes the brick's cell into dirtyCells; the incremental path
    // expands to its orthogonal neighbour (13,24) which is a base cell.
    tileMap.destroy(13, 23)

    // Render #2 → incremental redraw (NOT a full rebuild).
    rec.baseDraws = []
    renderer.render(world)

    // The fix must repaint the WHOLE 2×2 base crystal from its top-left
    // origin, so no corner is left as a cleared gap.
    expect(rec.baseDraws).toContainEqual({
      x: 12 * CELL,
      y: 24 * CELL,
      w: CELL * 2,
      h: CELL * 2,
    })
  })
})
