/**
 * Canvas factory — creates offscreen canvases that work in both main thread
 * and Web Worker environments.
 *
 * Uses OffscreenCanvas when available (more efficient — no DOM overhead),
 * falls back to HTMLCanvasElement on older browsers.
 */

export interface OffscreenCanvasResult {
  /** The canvas element (OffscreenCanvas or HTMLCanvasElement) */
  canvas: CanvasImageSource
  /** The 2D rendering context */
  ctx: CanvasRenderingContext2D
}

/**
 * Create an offscreen canvas with a 2D context.
 * The canvas is suitable for caching rendered content (terrain, sprites, etc.)
 * and can be passed to `ctx.drawImage()` as a source.
 */
export function createOffscreenCanvas(w: number, h: number, scale?: number): OffscreenCanvasResult {
  let canvas: any
  if (typeof OffscreenCanvas !== 'undefined') {
    canvas = new OffscreenCanvas(w, h)
  } else {
    canvas = document.createElement('canvas')
    canvas.width = w
    canvas.height = h
  }
  const ctx = canvas.getContext('2d') as CanvasRenderingContext2D
  if (scale) ctx.scale(scale, scale)
  ctx.imageSmoothingEnabled = true
  return { canvas, ctx }
}
