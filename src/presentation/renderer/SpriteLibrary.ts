import { SPRITE_URLS } from '../../assets/sprites/index'

/**
 * SpriteLibrary — preloads the SVG asset library into cached HTMLImageElements.
 *
 * The canvas renderer draws these images (rotating tank sprites per direction).
 * `load()` is idempotent and resolves once every sprite has either loaded or
 * errored, so the game can safely `await` it before the first frame.
 */
export class SpriteLibrary {
  private images = new Map<string, HTMLImageElement>()
  private _ready = false

  get ready(): boolean {
    return this._ready
  }

  async load(): Promise<void> {
    if (this._ready) return
    await Promise.all(
      Object.entries(SPRITE_URLS).map(([key, url]) => this.loadOne(key, url)),
    )
    this._ready = true
  }

  private loadOne(key: string, url: string): Promise<void> {
    return new Promise((resolve) => {
      const img = new Image()
      const done = () => {
        this.images.set(key, img)
        resolve()
      }
      img.onload = done
      img.onerror = () => {
        console.warn(`[SpriteLibrary] failed to load sprite: ${key}`)
        resolve()
      }
      img.src = url
    })
  }

  /** Returns the loaded image, or undefined if not yet available. */
  get(key: string): HTMLImageElement | undefined {
    const img = this.images.get(key)
    if (img && img.complete && img.naturalWidth > 0) return img
    return undefined
  }

  has(key: string): boolean {
    return this.get(key) !== undefined
  }
}

/** Shared singleton used by the renderer. */
export const spriteLibrary = new SpriteLibrary()
