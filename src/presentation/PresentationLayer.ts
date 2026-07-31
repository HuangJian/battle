import type { World } from '../game/World'
import type { GameEvent, EmitterConfig, Tank } from '../types'
import { FIELD, TANK } from '../constants'
import { THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT, THUMBNAIL_QUALITY } from '../snapshot/config'
import { Camera } from './Camera'
import { AnimationSystem } from './AnimationSystem'
import { ParticleSystem } from './ParticleSystem'
import { EffectsSystem } from './EffectsSystem'
import { GameRenderer } from './renderer/GameRenderer'
import { spriteLibrary } from './renderer/SpriteLibrary'
import { SpriteCache } from './renderer/SpriteCache'
import { UIManager } from './ui/UIManager'

/**
 * PresentationLayer — the main presentation orchestrator.
 * Sits between the World (read-only) and the display.
 * Manages camera, animations, particles, effects, and UI.
 * Never modifies the World.
 */
export class PresentationLayer {
  camera: Camera
  animations: AnimationSystem
  particles: ParticleSystem
  effects: EffectsSystem
  renderer: GameRenderer
  ui: UIManager
  spriteCache: SpriteCache
  private dpr: number
  /** Root element (#app) — stored for fullscreen API calls. */
  private root: HTMLElement

  /** Throttle window-resize → canvas resize via one rAF per burst. */
  private _resizeRaf = 0

  // ---- On-demand render gating (energy: skip full canvas repaint when idle) ----
  /** Force a repaint on the next shouldRender() check (set on resume/reset). */
  private _needRender = true
  /** Cheap signature of everything that affects painted pixels. */
  private _lastSceneSig = 0
  /**
   * Cached `world.allTanks` buffer fetched inside `computeSceneSig`, consumed
   * by the next `render()` call (P2). Null after consumption or before first
   * use. Safe because `_allTanksBuf` is only mutated by the getter itself, and
   * no sim tick runs between `shouldRender` and `render`.
   */
  private _sigTanks: Tank[] | null = null
  // Structural / UI-driving fields whose change must force a repaint.
  private _lastState = ''
  private _lastThemeKey = ''
  private _lastMenuCursor = -1
  private _lastSelectedStage = -1
  private _lastRecoveryCursor = -1
  private _lastRecoveryCountdown = -1

  constructor(root: HTMLElement, performanceMode = false) {
    this.root = root
    this.camera = new Camera()
    this.animations = new AnimationSystem()
    this.particles = new ParticleSystem()
    this.effects = new EffectsSystem()

    // Create UI first — it creates the canvas
    this.ui = new UIManager(root)

    // DPR cap: Performance Mode pins the render scale at 1 (the browser
    // upscales with `image-rendering: pixelated`); otherwise cap at 2×.
    this.dpr = performanceMode ? 1 : Math.min(window.devicePixelRatio || 1, 2)

    this.spriteCache = new SpriteCache(this.dpr)

    this.renderer = new GameRenderer(
      this.ui.canvas,
      this.camera,
      this.animations,
      this.particles,
      this.effects,
      this.dpr,
      spriteLibrary,
    )
    // Sync initial low-quality state with the initial performance mode.
    this.renderer.lowQuality = performanceMode

    // Size the canvas to fill the viewport (largest possible square) and
    // keep it responsive to window resizes. Pure presentation — never
    // touches the World or the canvas' internal (fixed) buffer.
    this.resizeCanvas()
    window.addEventListener('resize', this.onResize)
    window.addEventListener('orientationchange', this.onResize)
    // Listen for fullscreen changes to re-layout the canvas.
    document.addEventListener('fullscreenchange', this.onFullscreenChange)
    // Re-run once layout has settled (fonts / stylesheet) so the first frame
    // is already correctly sized rather than flashing the CSS fallback size.
    requestAnimationFrame(() => this.resizeCanvas())
  }

  /**
   * Apply Performance Mode at runtime. When ON: cap render DPR at 1 and let the
   * browser upscale the canvas with `image-rendering: pixelated` (nearest
   * neighbor — looks more retro AND cuts GPU fill-rate ~4× on Retina). When OFF:
   * restore DPR (capped at 2×). Rebuilds the sprite cache + renderer backing
   * store at the new resolution and forces a repaint. Safe to call any time; if
   * the target DPR is unchanged it only flips the CSS upscale flag.
   */
  applyPerformanceMode(on: boolean): void {
    const newDpr = on ? 1 : Math.min(window.devicePixelRatio || 1, 2)
    this.ui.canvas.style.imageRendering = on ? 'pixelated' : 'auto'
    if (newDpr !== this.dpr) {
      this.spriteCache = new SpriteCache(newDpr)
      this.spriteCache.build(spriteLibrary)
      this.renderer.artist.setSpriteCache(this.spriteCache)
      this.renderer.setDpr(newDpr)
      this.dpr = newDpr
    }
    // Low-quality mode: skip decorative rendering (vignette blit, tank shadows).
    // On software rasterizers (old machines without GPU), the vignette alone is
    // the single most expensive operation (~1.4ms/frame on Skia, estimated
    // 7–14ms on a 20-year-old machine). Gated behind Performance Mode so normal
    // play is unaffected.
    this.renderer.lowQuality = on
    this.markNeedsRender()
  }

  /** Handle window resize/orientationchange, throttled to one rAF per burst. */
  private onResize = (): void => {
    if (this._resizeRaf) return
    this._resizeRaf = requestAnimationFrame(() => {
      this._resizeRaf = 0
      this.resizeCanvas()
    })
  }

  /** Handle fullscreen change — re-layout canvas and sync UI state. */
  private onFullscreenChange = (): void => {
    this.resizeCanvas()
    this.markNeedsRender()
    // Sync the Control Center button label (ON/OFF).
    this.ui.controlCenter.setFullscreenState(this.isFullscreen)
  }

  /** Whether the Fullscreen API is supported on the root element. */
  isFullscreenSupported(): boolean {
    return 'requestFullscreen' in (this.root as HTMLElement)
  }

  /**
   * Toggle fullscreen mode. Uses the Fullscreen API when available;
   * falls back to a CSS `.is-maximized` class for iOS Safari and other
   * browsers that don't support the API.
   */
  toggleFullscreen(): void {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {
        // Ignore — browser may reject if no user gesture
      })
      return
    }
    if (this.isFullscreenSupported()) {
      this.root.requestFullscreen().catch(() => {
        // Fullscreen rejected (no user gesture? iframe?) → CSS fallback
        this.root.classList.toggle('is-maximized')
        this.resizeCanvas()
        this.markNeedsRender()
        this.ui.controlCenter.setFullscreenState(this.isFullscreen)
      })
    } else {
      // iOS Safari / unsupported → CSS fallback
      this.root.classList.toggle('is-maximized')
      this.resizeCanvas()
      this.markNeedsRender()
      this.ui.controlCenter.setFullscreenState(this.isFullscreen)
    }
  }

  /** Whether we are currently in fullscreen (API or CSS fallback). */
  get isFullscreen(): boolean {
    return !!(document.fullscreenElement || this.root.classList.contains('is-maximized'))
  }

  /**
   * Set the canvas' on-screen size to the largest square that fits the
   * current viewport, accounting for the HUD bar, footer, and (on wide
   * screens) the Control Center sidebar so the playfield is never hidden
   * behind it. The canvas' internal buffer stays fixed at FIELD×dpr; only
   * its CSS display box changes, so this is allocation-free and safe to run
   * on every resize.
   */
  resizeCanvas(): void {
    const canvas = this.ui.canvas
    if (!canvas) return

    const PAD_X = 0 // #app has no padding — canvas reaches the window edges
    const PAD_Y = 0 // HUD/footer frame the top/bottom with no extra margin

    // Control Center sidebar overlaps the left edge on wide screens
    // (hidden under 900px). Keep the playfield clear of it.
    let leftReserve = 0
    const cc = this.ui.controlCenter?.el
    if (cc) {
      const style = window.getComputedStyle(cc)
      if (style.display !== 'none') {
        leftReserve = cc.getBoundingClientRect().width + 24 // width + gap
      }
    }

    const hud = this.ui.hudBarEl
    const footer = this.ui.footerEl

    // Two passes: setting HUD/footer widths can change their heights (e.g. the
    // footer wraps to two lines on narrow screens), which feeds back into the
    // available height. Apply widths once, re-measure, recompute. Converges.
    let side = 1
    for (let pass = 0; pass < 2; pass++) {
      const hudH = hud.offsetHeight
      const footerH = footer.offsetHeight

      const availW = Math.max(0, window.innerWidth - PAD_X * 2 - leftReserve)
      const availH = Math.max(0, window.innerHeight - PAD_Y * 2 - hudH - footerH)
      side = Math.max(1, Math.floor(Math.min(availW, availH)))

      // HUD/footer match the canvas' framed width (canvas + 1px game-container
      // border on each side), never the whole window.
      const frameW = side + 2
      hud.style.width = frameW + 'px'
      footer.style.width = frameW + 'px'
      canvas.style.width = side + 'px'
      canvas.style.height = side + 'px'
    }
  }

  /** Build the sprite cache after the sprite library has loaded. */
  initSpriteCache(lib: typeof spriteLibrary): void {
    this.spriteCache.build(lib)
    this.renderer.setSpriteCache(this.spriteCache)
  }

  /** Process game events for visual effects */
  handleEvents(events: GameEvent[]): void {
    for (const event of events) {
      switch (event.type) {
        case 'explosion':
          this.onExplosion(event.x, event.y, event.kind)
          break
        case 'tank_destroyed':
          this.onTankDestroyed(event.tank, event.by)
          break
        case 'base_destroyed':
          this.onBaseDestroyed()
          break
        case 'powerup_collected':
          this.onPowerUpCollected(event.powerUp)
          break
        case 'stage_clear':
          this.effects.triggerFlash('#ffffff', 0.4)
          break
        case 'player_hit':
          this.camera.shakeScreen(10)
          this.effects.triggerFlash('#ff4040', 0.35)
          this.effects.triggerHitPause(80)
          break
        case 'bullet_fired':
          // Small muzzle flash particles
          this.onBulletFired(event.bullet)
          break
      }
    }
  }

  private onExplosion(x: number, y: number, kind: 'small' | 'big'): void {
    if (kind === 'big') {
      this.effects.triggerFlash('#ffaa40', 0.15)

      // Flash particle
      this.particles.emit(this.makeFlashEmitter(x, y))

      // Expanding ring
      this.particles.emit(this.makeRingEmitter(x, y))

      // Sparks
      this.particles.emit(this.makeSparkEmitter(x, y, 12, 2, 5))

      // Debris
      this.particles.emit(this.makeDebrisEmitter(x, y, 8))

      // Smoke
      this.particles.emit(this.makeSmokeEmitter(x, y, 5))
    } else {
      // Small sparks
      this.particles.emit(this.makeSparkEmitter(x, y, 5, 1, 3))
    }
  }

  private onTankDestroyed(
    tank: { x: number; y: number; w: number; h: number; isPlayer?: boolean },
    by: 'player' | 'enemy' | 'self',
  ): void {
    const cx = tank.x + tank.w / 2
    const cy = tank.y + tank.h / 2

    if (tank.isPlayer) {
      this.camera.shakeScreen(14)
      this.effects.triggerFlash('#ff4040', 0.4)
      this.effects.triggerHitPause(120)
      // Extra debris for player
      this.particles.emit(this.makeDebrisEmitter(cx, cy, 12))
    } else if (by === 'player') {
      // Debris from destroyed enemy
      this.particles.emit(this.makeDebrisEmitter(cx, cy, 6))
    }
  }

  private onBaseDestroyed(): void {
    this.camera.shakeScreen(20)
    this.effects.triggerFlash('#ff2020', 0.6)
    this.effects.triggerHitPause(200)

    // Big particle burst at base location
    const cx = FIELD / 2
    const cy = FIELD - TANK
    this.particles.emit(this.makeSparkEmitter(cx, cy, 20, 3, 7))
    this.particles.emit(this.makeDebrisEmitter(cx, cy, 15))
    this.particles.emit(this.makeSmokeEmitter(cx, cy, 8))
  }

  private onPowerUpCollected(_type: string): void {
    // Sparkle effect will be at player position, handled in updateVisualState
  }

  private onBulletFired(bullet: { x: number; y: number; dir: string; isPlayer: boolean }): void {
    if (bullet.isPlayer) {
      // Small muzzle flash
      this.particles.emit({
        x: bullet.x,
        y: bullet.y,
        count: 3,
        speedMin: 0.5,
        speedMax: 1.5,
        lifeMin: 100,
        lifeMax: 200,
        sizeMin: 1,
        sizeMax: 2,
        colors: ['#ffe060', '#ffaa40'],
        type: 'spark',
        gravity: 0,
        drag: 0.9,
        angleMin: 0,
        angleMax: Math.PI * 2,
        spread: 3,
      })
    }
  }

  // ---- Emitter configs ----

  private makeSparkEmitter(
    x: number,
    y: number,
    count: number,
    speedMin: number,
    speedMax: number,
  ): EmitterConfig {
    return {
      x,
      y,
      count,
      speedMin: speedMin * 0.5,
      speedMax: speedMax * 0.5,
      lifeMin: 200,
      lifeMax: 500,
      sizeMin: 1,
      sizeMax: 3,
      colors: ['#ffe040', '#ff8020', '#ff4020', '#ffffff'],
      type: 'spark',
      gravity: 0.05,
      drag: 0.92,
      angleMin: 0,
      angleMax: Math.PI * 2,
      spread: 4,
    }
  }

  private makeDebrisEmitter(x: number, y: number, count: number): EmitterConfig {
    return {
      x,
      y,
      count,
      speedMin: 1,
      speedMax: 3,
      lifeMin: 400,
      lifeMax: 800,
      sizeMin: 2,
      sizeMax: 4,
      colors: ['#808080', '#606060', '#a0a0a0', '#404040'],
      type: 'debris',
      gravity: 0.15,
      drag: 0.95,
      angleMin: 0,
      angleMax: Math.PI * 2,
      spread: 8,
    }
  }

  private makeSmokeEmitter(x: number, y: number, count: number): EmitterConfig {
    return {
      x,
      y,
      count,
      speedMin: 0.2,
      speedMax: 0.8,
      lifeMin: 600,
      lifeMax: 1000,
      sizeMin: 4,
      sizeMax: 8,
      colors: ['#606060', '#404040', '#808080'],
      type: 'smoke',
      gravity: -0.02,
      drag: 0.96,
      angleMin: -Math.PI / 2 - 0.5,
      angleMax: -Math.PI / 2 + 0.5,
      spread: 6,
    }
  }

  private makeFlashEmitter(x: number, y: number): EmitterConfig {
    return {
      x,
      y,
      count: 1,
      speedMin: 0,
      speedMax: 0,
      lifeMin: 150,
      lifeMax: 150,
      sizeMin: 20,
      sizeMax: 20,
      colors: ['#ffffff'],
      type: 'flash',
      gravity: 0,
      drag: 1,
      angleMin: 0,
      angleMax: 0,
      spread: 0,
    }
  }

  private makeRingEmitter(x: number, y: number): EmitterConfig {
    return {
      x,
      y,
      count: 1,
      speedMin: 0,
      speedMax: 0,
      lifeMin: 300,
      lifeMax: 300,
      sizeMin: 8,
      sizeMax: 8,
      colors: ['#ffe040'],
      type: 'ring',
      gravity: 0,
      drag: 1,
      angleMin: 0,
      angleMax: 0,
      spread: 0,
    }
  }

  // ---- Update & Render ----

  /** Update visual state from world, then render everything */
  render(world: World, dt: number): void {
    // `world.allTanks` is a getter that rebuilds a shared buffer on every access.
    // Nothing in the presentation path mutates the World, so one rebuild per
    // frame is enough — thread it through the two consumers (R1/P1-B).
    //
    // P2: when `shouldRender` already fetched the buffer for `computeSceneSig`,
    // reuse that reference instead of re-fetching. Skips the second ~6-10 entry
    // array write per repainted frame. Safe because `_allTanksBuf` is only
    // mutated by the getter itself, and no sim tick runs between shouldRender
    // and render.
    const tanks = this._sigTanks ?? world.allTanks
    this._sigTanks = null // consume; next shouldRender will re-fetch

    // Update visual state from world
    this.updateVisualState(world, tanks)

    // Update presentation systems
    this.animations.update(dt)
    this.particles.update(dt)
    this.camera.update(dt)
    this.effects.update(dt)

    // Apply theme to UI — only when the theme key changes (avoids 16 CSS var writes/frame)
    this.ui.applyThemeIfChanged(world.theme, world.themeKey)

    // Render game world
    this.renderer.render(world, tanks)
  }

  /**
   * Update the HTML HUD only. Kept separate from `render()` so the canvas
   * repaint can be skipped (on-demand) without freezing the HUD. Cheap and
   * internally guarded against redundant DOM writes.
   */
  updateUI(world: World): void {
    this.ui.update(world)
  }

  /**
   * Decide whether the canvas needs a full repaint this frame.
   *
   * Returns true when anything that affects the painted pixels changed:
   *  - time-based effects that animate every frame (particles, explosions,
   *    screen flash, hit-pause, camera shake, score popups)
   *  - structural / UI-driving state (game state, theme, menu/recovery
   *    navigation, terrain cache dirty)
   *  - the static scene signature changed (water phase + coarse bullet/tank
   *    positions + tank state bits + camera offset)
   *
   * When false, `Game` skips `render()` entirely — the GPU goes idle instead
   * of repainting 60×/sec. Input, simulation, and the HUD still run every
   * frame, so responsiveness is untouched.
   */
  shouldRender(world: World): boolean {
    if (this._needRender) {
      this._needRender = false
      this.recordRendered(world)
      return true
    }
    // Time-based effects: animating every frame → must repaint.
    if (this.particles.activeCount > 0) return this.forceRender(world)
    if (world.explosions.length > 0) return this.forceRender(world)
    if (this.effects.getFlash() !== null) return this.forceRender(world)
    if (this.effects.isPaused) return this.forceRender(world)
    if (this.camera.state.shake > 0.01) return this.forceRender(world)
    if (world.popups.length > 0) return this.forceRender(world)
    // Structural / UI-driving changes.
    if (world.state !== this._lastState) return this.forceRender(world)
    if (world.themeKey !== this._lastThemeKey) return this.forceRender(world)
    if (world.menuCursor !== this._lastMenuCursor) return this.forceRender(world)
    if (world.selectedStage !== this._lastSelectedStage) return this.forceRender(world)
    if (world.recoveryCursor !== this._lastRecoveryCursor) return this.forceRender(world)
    if (world.recoveryCountdown !== this._lastRecoveryCountdown) return this.forceRender(world)
    const tm = world.tileMap
    if (tm.dirty || tm.dirtyCells.length > 0) return this.forceRender(world)
    // Static scene signature.
    const sig = this.computeSceneSig(world)
    if (sig !== this._lastSceneSig) {
      // Hand the freshly computed signature to recordRendered so it does not
      // walk every tank/bullet/power-up a second time (R1/P1-A).
      this.recordRendered(world, sig)
      return true
    }
    return false
  }

  /** Force a repaint on the next frame (used on resume / state reset). */
  markNeedsRender(): void {
    this._needRender = true
  }

  private forceRender(world: World): boolean {
    this.recordRendered(world)
    return true
  }

  /**
   * Snapshot the structural/UI fields and scene signature as "last painted".
   *
   * @param sig Pre-computed scene signature. `shouldRender`'s signature branch
   * has already computed it for this exact World state; passing it in avoids a
   * second full walk of tanks + bullets + power-ups (R1/P1-A). Omit it on the
   * force paths, where no signature has been computed yet.
   */
  private recordRendered(world: World, sig?: number): void {
    this._lastSceneSig = sig ?? this.computeSceneSig(world)
    this._lastState = world.state
    this._lastThemeKey = world.themeKey
    this._lastMenuCursor = world.menuCursor
    this._lastSelectedStage = world.selectedStage
    this._lastRecoveryCursor = world.recoveryCursor
    this._lastRecoveryCountdown = world.recoveryCountdown
  }

  /**
   * Cheap signature of the visible scene. Coarse (8px buckets) so tiny
   * sub-pixel jitter never falsely triggers a repaint, but any real movement
   * (a tank/bullet shifting cells, a spawn/shield/bonus/flash state flip, the
   * water phase advancing, or the camera offset changing) changes it.
   */
  private computeSceneSig(world: World): number {
    let sig = 0
    const frame = world.frame
    // Water animates at ~3Hz (frame/20); include its phase so idle water alone
    // only forces a repaint a few times per second.
    const waterPhase = Math.floor(frame / 20) % 2
    sig = (sig * 31 + waterPhase) | 0
    // Bullets — coarse position captures movement.
    const bullets = world.bullets
    for (let i = 0; i < bullets.length; i++) {
      const b = bullets[i]
      if (!b.alive) continue
      sig = (sig * 31 + ((b.x >> 3) + (b.y >> 3) * 64)) | 0
    }
    // Tanks. P2: cache the fetched buffer so `render()` can reuse it instead of
    // re-fetching `world.allTanks` (which rebuilds the same `_allTanksBuf`).
    const tanks = (this._sigTanks = world.allTanks)
    const animPhase = Math.floor(frame / 4) // spawn(0-3)/shield(0-1)/flash(0-1) cadence
    for (let i = 0; i < tanks.length; i++) {
      const t = tanks[i]
      if (!t.alive) continue
      const spawning = t.spawnTimer > 0
      const shielded = !!(t.shieldTimer && t.shieldTimer > 0)
      const flashing = !!(t.flashTimer && t.flashTimer > 0)
      let bits = 0
      if (spawning) bits |= 1
      if (shielded) bits |= 2
      if (flashing) bits |= 4
      if (t.bonus) bits |= 8
      if (t.moving) bits |= 16
      let s =
        (sig * 31 +
          ((t.x >> 3) + (t.y >> 3) * 64) +
          t.dir.charCodeAt(0) +
          bits * 131 +
          (t.level ?? 0) * 17) |
        0
      // Fold the native animation phase into the signature so spawn/shield/
      // hit-flash still play at their designed rate — we repaint only on phase
      // flips and skip the in-between frames, instead of freezing when the
      // scene is otherwise idle.
      if (spawning) s = (s * 31 + (animPhase % 4)) | 0
      if (shielded) s = (s * 31 + (animPhase % 2)) | 0
      if (flashing) s = (s * 31 + (animPhase % 2)) | 0
      sig = s
    }
    // Power-ups (coarse position + type captures spawn/despawn/type change).
    const pus = world.powerUps
    for (let i = 0; i < pus.length; i++) {
      const pu = pus[i]
      if (!pu.alive) continue
      sig = (sig * 31 + ((pu.x >> 3) + (pu.y >> 3) * 64) + pu.type.charCodeAt(0) * 7) | 0
    }
    // Camera offset (integer; shake is handled separately as a forced render).
    const cam = this.camera.getOffset()
    sig = (sig * 31 + (Math.round(cam.x) + Math.round(cam.y) * 64)) | 0
    return sig
  }

  /** Sync visual components with simulation entities */
  private updateVisualState(world: World, tanks: Tank[]): void {
    const frame = world.frame

    for (let ti = 0; ti < tanks.length; ti++) {
      const tank = tanks[ti]
      if (!tank.alive) continue

      const vc = this.animations.getOrCreate(tank.id, 'tank', tank.dir, tank.level ?? 0)
      vc.direction = tank.dir
      vc.level = tank.level ?? 0
      vc.flash = (tank.flashTimer ?? 0) > 0
      // Frame stamp for allocation-free cleanup (see AnimationSystem.cleanup).
      vc.lastSeenFrame = frame

      if (tank.spawnTimer > 0) {
        this.animations.setAnimation(vc, 'spawn')
      } else if (tank.moving) {
        this.animations.setAnimation(vc, 'move')
      } else {
        this.animations.setAnimation(vc, 'idle')
      }
    }

    // Clean up visual components for dead entities (allocation-free sweep).
    this.animations.cleanup(frame)
  }

  // ---- Snapshot thumbnails (Snapshot Management Framework §8) ----

  /** Reusable offscreen canvas for thumbnail capture (lazy). */
  private _thumbCanvas: HTMLCanvasElement | null = null

  /**
   * Capture a square preview of the current playfield as a JPEG data URL.
   * The field is 416×416 (square); the thumbnail is square too, so the frame
   * is scaled uniformly with no crop and no stretch — the game's aspect
   * ratio is preserved exactly. Returns null when the canvas has no pixels
   * yet (e.g. before the first frame).
   */
  captureThumbnail(): string | null {
    const src = this.ui.canvas
    if (!src || src.width === 0 || src.height === 0) return null
    try {
      if (!this._thumbCanvas) {
        this._thumbCanvas = document.createElement('canvas')
        this._thumbCanvas.width = THUMBNAIL_WIDTH
        this._thumbCanvas.height = THUMBNAIL_HEIGHT
      }
      const dst = this._thumbCanvas
      const ctx = dst.getContext('2d')
      if (!ctx) return null
      ctx.fillStyle = '#000'
      ctx.fillRect(0, 0, dst.width, dst.height)
      const scale = Math.max(dst.width / src.width, dst.height / src.height)
      const dw = src.width * scale
      const dh = src.height * scale
      ctx.drawImage(src, (dst.width - dw) / 2, (dst.height - dh) / 2, dw, dh)
      return dst.toDataURL('image/jpeg', THUMBNAIL_QUALITY)
    } catch {
      return null
    }
  }

  /** Reset presentation state (e.g., when returning to menu) */
  reset(): void {
    this.animations.clear()
    this.particles.clear()
    this.effects.reset()
    this.camera.reset()
    // Presentation was wiped — force a repaint so the next frame is fresh.
    this._needRender = true
  }
}
