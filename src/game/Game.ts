import { World } from './World'
import { Simulation } from './Simulation'
import { Input, DEFAULT_KEYS } from './Input'
import { SnapshotManager } from '../snapshot/SnapshotManager'
import { createDefaultStorage } from '../snapshot/storage'
import {
  RecoveryController,
  RECOVERY_OPTIONS,
  RECOVERY_OPTION_COUNT,
} from '../snapshot/RecoveryController'
import { PresentationLayer } from '../presentation/PresentationLayer'
import { spriteLibrary } from '../presentation/renderer/SpriteLibrary'
import { AudioManager } from '../audio/AudioManager'
import { DIFFICULTIES, DIFFICULTY_KEYS } from '../config/difficulty'
import { THEMES, DEFAULT_THEME } from '../config/theme'
import { STAGES } from '../config/stages'
import { TICK_MS, MAX_RENDER_FPS } from '../constants'
import type { GameSettings } from '../types'
import type { GameSnapshot } from '../snapshot/types'

const SETTINGS_KEY = 'bc_settings'
const THEME_KEYS = Object.keys(THEMES)

/**
 * True idle for static screens — event-driven 0-loop.
 *
 * The game must hold 60 FPS while *playing* (the vsync rAF loop). But on the
 * menu / pause / game-over / victory screens nothing animates at 60 FPS, and
 * the on-demand render gate already skips the canvas repaint there, so the
 * GPU is already idle. Running even a coarse setTimeout loop (the previous
 * 10 FPS compromise) still wakes the main thread 10×/sec for no visual
 * benefit — exactly what keeps a laptop's fan spinning.
 *
 * These static states are therefore fully *event-driven*: a single `keydown`
 * listener (`onStaticKey`) processes menu/pause/recovery input the instant a
 * key is pressed — `Input`'s own `keydown` handler (registered first) has
 * already populated the polled `justPressed` set, so we read it, repaint on
 * demand, then stop. No rAF, no setTimeout: the main thread goes fully to
 * sleep. The very same input handlers are re-used by the loop for action
 * states, so behaviour is identical; only the *driver* differs.
 *
 * The moment input changes the state (start → playing, snapshot load →
 * recovery, unpause → playing) `scheduleFrame()` re-arms the vsync rAF loop
 * with zero perceptible delay. Mouse-driven menu actions take the same path
 * via `refreshStaticScreen()`.
 */
const LOW_POWER_STATES = new Set(['menu', 'paused', 'gameover', 'victory'])

/**
 * Game — top-level orchestrator.
 * Owns the game loop, wires all systems together.
 */
export class Game {
  world: World
  input: Input
  simulation: Simulation
  presentation: PresentationLayer
  audio: AudioManager
  snapshots: SnapshotManager
  recovery: RecoveryController

  private lastTime = 0
  private accumulator = 0
  private running = false
  private rafId = 0
  private prevStageIndex = -1
  /** Timestamp of the last canvas repaint (for MAX_RENDER_FPS throttle). */
  private _lastRenderTime = 0
  /** True while the tab is hidden (loop paused by visibilitychange). */
  private _hidden = false
  private prevRecoveryPhase = 'idle'
  private prevCountdown = 0
  /** The last manually-saved snapshot, if any — offered as the default RESUME on the start screen. */
  private resumeSnapshot: GameSnapshot | null = null

  /** Rolling FPS (updated once per second) — cheap regression signal. */
  fps = 0
  private _frameCount = 0
  private _fpsLastTime = 0
  private _slowSeconds = 0

  settings: GameSettings
  private difficultyIndex = 1 // classic
  private themeIndex = 0

  constructor(root: HTMLElement) {
    this.settings = this.loadSettings()
    this.world = new World()
    this.input = new Input(this.settings.keys)
    this.simulation = new Simulation(this.world, this.input)
    this.presentation = new PresentationLayer(root)
    // Wire the live key-bindings object + persistence into the controls panel.
    this.presentation.ui.initControls(this.settings.keys, () => this.saveSettings())

    // Wire mouse-click handlers for the start screen (same World-mutating
    // paths as the keyboard menu input).
    this.presentation.ui.initMenuActions({
      selectDifficulty: (key) => this.menuSelectDifficulty(key),
      selectTheme: (key) => this.menuSelectTheme(key),
      cycleStage: (dir) => this.menuCycleStage(dir),
      start: () => this.menuStart(),
      resume: () => this.menuResume(),
      openControls: () => {
        if (this.world.state === 'menu') {
          this.presentation.ui.openControls()
        }
      },
    })
    this.audio = new AudioManager()

    // Snapshot Management Framework (plan/Snapshot-Management-Framework.md)
    this.snapshots = new SnapshotManager({ backend: createDefaultStorage() })
    this.recovery = new RecoveryController(this.snapshots)
    this.wireSnapshotUI()

    this.audio.setVolume(this.settings.volume)

    // Apply saved settings
    const savedDiffIdx = DIFFICULTY_KEYS.indexOf(this.settings.difficulty)
    if (savedDiffIdx >= 0) this.difficultyIndex = savedDiffIdx
    const savedThemeIdx = THEME_KEYS.indexOf(this.settings.theme)
    if (savedThemeIdx >= 0) this.themeIndex = savedThemeIdx

    this.world.difficultyKey = DIFFICULTY_KEYS[this.difficultyIndex]
    this.world.difficulty = DIFFICULTIES[this.world.difficultyKey]
    this.world.themeKey = THEME_KEYS[this.themeIndex]
    this.world.theme = THEMES[this.world.themeKey]
  }

  /** Wire the Snapshot Browser + Control Center callbacks into the framework. */
  private wireSnapshotUI(): void {
    const ui = this.presentation.ui

    ui.snapshotBrowser.init({
      getSnapshots: () => this.snapshots.getAll(),
      onLoad: (id) => {
        if (this.recovery.beginLoad(id, this.world)) {
          this.audio.playRecoveryStart()
        }
        // The browser closes itself before calling onLoad (state is now
        // 'recovery', an action state), so re-arm the vsync rAF loop. The
        // 0-loop idle path can't do this because onStaticKey bails out while
        // the browser was open.
        this.scheduleFrame()
      },
      onDelete: (id) => {
        this.snapshots.delete(id)
        ui.notify('Snapshot deleted')
      },
      onClose: () => {
        // If the browser was opened from the recovery menu, the menu is
        // still active underneath — nothing to do. Elsewhere (paused /
        // menu) the regular screen sync resumes automatically.
      },
    })

    ui.controlCenter.init({
      onManualSave: () => this.manualSnapshot(),
      onOpenBrowser: () => this.openSnapshotBrowser(),
      onOpenControls: () => {
        if (this.world.state === 'menu') {
          ui.openControls()
        } else {
          ui.notify('Key bindings are available from the main menu', 'warn')
        }
      },
      getCounts: () => ({
        total: this.snapshots.count(),
        manual: this.snapshots.count('manual'),
        manualLimit: this.snapshots.policyFor('manual').limit,
      }),
    })
  }

  async start(): Promise<void> {
    this.input.attach(window)
    // Static-screen (menu / pause / game-over / victory) keyboard input is
    // event-driven: a single keydown listener processes it the instant a key
    // is pressed so the loop can stay fully asleep (0-loop idle) on those
    // screens. Registered AFTER input.attach so Input.onKeyDown populates the
    // polled `justPressed` set before we read it.
    window.addEventListener('keydown', this.onStaticKey)
    // Load persisted snapshots (IndexedDB) — snapshots survive reloads.
    await this.snapshots.hydrate()
    // Default-load behaviour: if a manual snapshot exists, surface it as the
    // start screen's RESUME target so reopening the page continues from it.
    this.resumeSnapshot = this.snapshots.latest({ type: 'manual' })
    this.presentation.ui.setResumeTarget(
      this.resumeSnapshot
        ? {
            stage: this.resumeSnapshot.metadata.stage,
            stageName: this.resumeSnapshot.metadata.stageName,
            score: this.resumeSnapshot.metadata.score,
          }
        : null,
    )
    // Open the menu on its default row and render the matching battlefield:
    // the RESUME target's saved content (if a manual snapshot exists) or the
    // selected stage's starting layout otherwise.
    this.world.menuCursor = 0
    this.applyMenuPreview()
    // Preload the SVG asset library so sprites are ready for the first frame.
    await spriteLibrary.load()
    // Pre-rasterize sprites to canvas bitmaps for fast rendering
    this.presentation.initSpriteCache(spriteLibrary)
    this.running = true
    this.lastTime = performance.now()
    document.addEventListener('visibilitychange', this.onVisibility)
    this.loop(this.lastTime)
  }

  stop(): void {
    this.running = false
    cancelAnimationFrame(this.rafId)
    this.rafId = 0
    document.removeEventListener('visibilitychange', this.onVisibility)
    window.removeEventListener('keydown', this.onStaticKey)
    this.input.detach(window)
  }

  /**
   * Pause the loop when the tab is hidden (stops all GPU/CPU work — the single
   * biggest energy saver for a backgrounded game) and resume cleanly on return.
   */
  private onVisibility = (): void => {
    if (document.hidden) {
      if (!this._hidden) {
        this._hidden = true
        cancelAnimationFrame(this.rafId)
      }
    } else if (this._hidden) {
      this._hidden = false
      if (this.running) {
        this.lastTime = performance.now()
        if (LOW_POWER_STATES.has(this.world.state)) {
          // No loop runs while idle — repaint once so the canvas isn't blank
          // after the tab was hidden (browsers may discard the backing store).
          this.presentation.markNeedsRender()
          this.presentation.updateUI(this.world)
          if (this.presentation.shouldRender(this.world)) {
            this.presentation.render(this.world, 0)
          }
          // Stay idle (no driver scheduled).
        } else {
          this.presentation.markNeedsRender()
          this.scheduleFrame()
        }
      }
    }
  }

  /**
   * Pick the loop driver: vsync rAF for action states (smooth 60 FPS play),
   * coarse setTimeout for static low-power states (menu/pause/game-over/
   * victory — ~83% less main-thread work, fan stays off). Only one driver is
   * ever pending; we clear both before scheduling to avoid stragglers when the
   * state flips mid-frame.
   */
  /**
   * Pick the loop driver: vsync rAF for action states (smooth 60 FPS play),
   * or nothing for the static low-power states (menu / pause / game-over /
   * victory) — those are event-driven, so the main thread genuinely sleeps.
   * Only one driver is ever pending; we clear both before (re)arming to avoid
   * a straggler rAF/timer waking the thread after the state has flipped.
   */
  private scheduleFrame(): void {
    if (!this.running) return
    cancelAnimationFrame(this.rafId)
    this.rafId = 0
    if (LOW_POWER_STATES.has(this.world.state)) {
      // True idle: no loop at all. Static-screen input is handled by
      // `onStaticKey` / mouse handlers, and the on-demand render gate keeps
      // the canvas correct, so the main thread stays fully asleep — fan off.
      return
    }
    this.rafId = requestAnimationFrame(this.loop)
  }

  /**
   * Public re-arm hook for external drivers (the perf harness, automated
   * tests). The 0-loop idle design only re-arms `loop` from inside
   * `scheduleFrame()`, which the static input path never reaches. When a
   * driver changes `world.state` directly to an action state it must call this
   * to kick the vsync rAF loop; for static states it is a no-op (they stay idle).
   * Safe to call any time — it cancels any pending driver first.
   */
  requestFrame(): void {
    this.scheduleFrame()
  }

  /**
   * Event-driven keyboard handler for the static (idle) screens.
   *
   * Registered as a `keydown` listener — and AFTER `Input.attach`, so `Input`'s
   * own handler has already recorded this event into its polled `justPressed`
   * set before we read it. For menu / pause / game-over / victory we process
   * the key exactly as the rAF loop would, repaint only if the visible scene
   * changed, then return — leaving the main thread asleep. This is the "true
   * 0-loop" idle: no rAF, no setTimeout, no periodic wake-ups.
   *
   * Action states (playing / stageclear / recovery) are intentionally NOT
   * handled here — the vsync rAF loop owns them — so a stray keydown during
   * play can never double-fire with the loop.
   */
  private onStaticKey = (_e: KeyboardEvent): void => {
    if (!this.running || this._hidden) return
    if (!LOW_POWER_STATES.has(this.world.state)) return
    // UI modals own their own keyboard handling; never double-process.
    if (this.presentation.ui.snapshotBrowser.isOpen()) return
    if (this.presentation.ui.isControlsOpen()) return

    // Process the key via the same code path the loop uses, then clear the
    // per-frame input edges so a single press is consumed exactly once.
    this.handleStateInput()
    this.input.endFrame()
    // Repaint on demand + (re)arm the loop driver if the state changed.
    this.refreshStaticScreen()
  }

  /**
   * Repaint the canvas only if the scene actually changed, sync the HUD,
   * capture any pending snapshot thumbnail, and (re)arm the right loop driver
   * for the current state. Shared by `onStaticKey` and the mouse-driven menu
   * actions so both paths behave identically under 0-loop idle.
   */
  private refreshStaticScreen(): void {
    this.presentation.updateUI(this.world)
    if (this.presentation.shouldRender(this.world)) {
      this.presentation.render(this.world, 0)
      this._lastRenderTime = performance.now()
    }
    // A manual snapshot taken while paused enqueues a thumbnail the loop would
    // normally grab; capture it now so it isn't lost under 0-loop idle. The
    // canvas already shows the frozen paused frame, so capture from live pixels.
    if (this.snapshots.hasPendingThumbnails) {
      this.snapshots.capturePendingThumbnails(() => this.presentation.captureThumbnail())
    }
    // If input left the static set (start → playing, load → recovery,
    // unpause → playing) this re-arms vsync rAF; otherwise it stays idle.
    this.scheduleFrame()
  }

  private loop = (time: number): void => {
    if (!this.running) return

    const dt = Math.min(time - this.lastTime, 100) // cap at 100ms
    this.lastTime = time
    this.accumulator += dt

    // Handle menu/game state input
    this.handleStateInput()

    // Fixed timestep simulation
    let steps = 0
    let enteredGameOver = false
    while (this.accumulator >= TICK_MS && steps < 5) {
      if (
        this.world.state === 'playing' ||
        this.world.state === 'stageclear' ||
        this.world.state === 'gameover'
      ) {
        this.simulation.tick()

        // Detect stage change → Stage Start snapshot (plan §3, §10)
        if (this.world.stageIndex !== this.prevStageIndex && this.world.state === 'playing') {
          this.snapshots.create('stage-start', this.world)
          this.snapshots.resetAutoTimer()
          this.prevStageIndex = this.world.stageIndex
        }

        // Detect game over → intercept for recovery
        if (this.world.state === 'gameover' && !enteredGameOver) {
          enteredGameOver = true
          this.startRecovery()
          break // stop ticking — simulation is now suspended
        }
      }
      this.accumulator -= TICK_MS
      steps++
    }

    // Recovery flow update (fade, countdown) — runs while state is 'recovery'
    if (this.world.state === 'recovery') {
      // Drain accumulator so the simulation doesn't burst-forward
      // when gameplay resumes after the countdown.
      this.accumulator = 0

      this.handleRecoveryInput()
      this.recovery.update(this.world, dt)

      // When the fade completes the snapshot is restored internally.
      // At that transition we must rebuild all presentation state
      // (particles, camera, animations) — Presentation is disposable.
      if (this.recovery.phase === 'countdown' && this.prevRecoveryPhase === 'fading') {
        this.presentation.reset()
        this.audio.stopAll()
      }

      // Countdown beeps — play a tone each time the number changes
      if (this.world.recoveryCountdown !== this.prevCountdown) {
        if (this.world.recoveryCountdown > 0) {
          this.audio.playCountdownBeep()
        } else if (this.prevCountdown > 0) {
          // Countdown just finished → resume
          this.audio.playCountdownGo()
        }
      }
      this.prevCountdown = this.world.recoveryCountdown
      this.prevRecoveryPhase = this.recovery.phase
    } else {
      this.prevRecoveryPhase = 'idle'
      this.prevCountdown = 0
    }

    // Auto snapshots — every 30 s of real gameplay (plan §3, §10)
    if (this.world.state === 'playing') {
      this.snapshots.updateAuto(this.world, dt)
    }

    // Process events — pass to both audio and presentation
    const events = this.world.consumeEvents()
    this.audio.handleEvents(events)
    this.presentation.handleEvents(events)

    // Render — on-demand energy saver. The full canvas repaint is skipped
    // unless the visible scene changed (PresentationLayer.shouldRender) and the
    // MAX_RENDER_FPS throttle allows it. This keeps the GPU idle — instead of
    // repainting 60×/sec — during menu, pause, game-over, and idle lulls, so
    // the fan stays off. Input, simulation, and the HUD still run every frame.
    const wantRender = this.presentation.shouldRender(this.world)
    const canRender = MAX_RENDER_FPS <= 0 || time - this._lastRenderTime >= 1000 / MAX_RENDER_FPS
    let rendered = false
    if (wantRender && canRender) {
      this.presentation.render(this.world, dt)
      this._lastRenderTime = time
      rendered = true
    }

    // Thumbnail capture (plan §8) — only right after a repaint, so the
    // preview always shows the snapshot's own frame, never a stale one.
    if (this.snapshots.hasPendingThumbnails) {
      if (rendered) {
        this.snapshots.capturePendingThumbnails(() => this.presentation.captureThumbnail())
      } else {
        // Force a repaint next frame so the pending previews can be taken.
        this.presentation.markNeedsRender()
      }
    }

    // Update the HTML HUD every frame (cheap, internally guarded) so menu/pause
    // overlays stay live even when the canvas repaint is skipped.
    this.presentation.updateUI(this.world)

    // Clear per-frame input state
    this.input.endFrame()

    // --- Performance sampler (regression guard, allocation-free) ---
    this._frameCount++
    if (time - this._fpsLastTime >= 1000) {
      this.fps = this._frameCount
      this._frameCount = 0
      this._fpsLastTime = time
      // Only warn during active play — static screens run a deliberate
      // low-power cadence (10 FPS) by design, so a low count there is expected.
      if (this.fps < 45 && !LOW_POWER_STATES.has(this.world.state)) {
        this._slowSeconds++
        if (this._slowSeconds === 3) {
          console.warn(`[perf] sustained low frame rate: ${this.fps} fps`)
        }
      } else {
        this._slowSeconds = 0
      }
    }

    this.scheduleFrame()
  }

  // ---- State Input ----

  private handleStateInput(): void {
    const w = this.world

    // The Snapshot Browser is a UI-modal that owns key input while open
    // (Esc is captured by the browser itself); skip game state input.
    if (this.presentation.ui.snapshotBrowser.isOpen()) return

    if (w.state === 'menu') {
      // The controls panel is a UI-modal that owns all key input while open;
      // skip menu navigation so it doesn't fight the panel.
      if (this.presentation.ui.isControlsOpen()) return

      // Open the controls / key-bindings panel (mouse users use the menu button).
      if (this.input.wasPressed('KeyC')) {
        this.presentation.ui.openControls()
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
        return
      }

      // Row count grows by one when a resumable manual snapshot is offered
      // (the RESUME row sits at index 0, pushing the config rows down).
      const rowCount = this.resumeSnapshot ? 4 : 3
      // Move cursor between rows (RESUME? / DIFFICULTY / THEME / STAGE)
      if (this.input.isUpPressed()) {
        w.menuCursor = (w.menuCursor - 1 + rowCount) % rowCount
        this.applyMenuPreview()
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
      }
      if (this.input.isDownPressed()) {
        w.menuCursor = (w.menuCursor + 1) % rowCount
        this.applyMenuPreview()
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
      }
      // Change value of the selected row (off = index of the first config row)
      const off = this.resumeSnapshot ? 1 : 0
      const left = this.input.wasPressed('ArrowLeft') || this.input.wasPressed('KeyA')
      const right = this.input.wasPressed('ArrowRight') || this.input.wasPressed('KeyD')
      if (left || right) {
        const dir = left ? -1 : 1
        let changed = false
        if (w.menuCursor === off) {
          this.difficultyIndex =
            (this.difficultyIndex + dir + DIFFICULTY_KEYS.length) % DIFFICULTY_KEYS.length
          w.difficultyKey = DIFFICULTY_KEYS[this.difficultyIndex]
          w.difficulty = DIFFICULTIES[w.difficultyKey]
          changed = true
        } else if (w.menuCursor === off + 1) {
          this.themeIndex = (this.themeIndex + dir) % THEME_KEYS.length
          w.themeKey = THEME_KEYS[this.themeIndex]
          w.theme = THEMES[w.themeKey]
          changed = true
        } else if (w.menuCursor === off + 2) {
          w.selectedStage = (w.selectedStage + dir + STAGES.length) % STAGES.length
          changed = true
        }
        if (changed) {
          // Swap the battle-field preview to match the new selection immediately
          // (e.g. moving to a different stage must repaint the canvas at once).
          this.applyMenuPreview()
          this.audio.init()
          this.audio.resume()
          this.audio.playMenuSelect()
        }
      }
      // Theme shortcut (Shift+T by default — see KeyBindings)
      if (this.input.isThemePressed()) {
        this.themeIndex = (this.themeIndex + 1) % THEME_KEYS.length
        w.themeKey = THEME_KEYS[this.themeIndex]
        w.theme = THEMES[w.themeKey]
        w.menuCursor = off + 1
        this.applyMenuPreview()
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
      }
      // Confirm — default action depends on the highlighted row:
      // on the RESUME row (index 0, only when a snapshot exists) it resumes;
      // otherwise it starts a fresh game with the current selections.
      if (this.input.isConfirmPressed()) {
        if (this.resumeSnapshot && w.menuCursor === 0) {
          this.menuResume()
        } else {
          this.audio.init()
          this.audio.resume()
          this.audio.playMenuSelect()
          this.recovery.reset()
          this.prevStageIndex = -1
          w.startGame(w.difficultyKey, w.themeKey, w.selectedStage)
          // Drop the confirm keypress (Space/Enter) so it can't bleed into the
          // gameplay fire input and make the player auto-fire on the first frame.
          this.input.reset()
          this.saveSettings()
        }
      }
      return
    }

    if (w.state === 'playing' || w.state === 'paused') {
      if (this.input.isPausePressed()) {
        this.simulation.togglePause()
        this.audio.playPause()
        // Entering pause → Pause snapshot (plan §3: created on pause,
        // captures the exact moment for a safe later return).
        if (w.state === 'paused') {
          this.snapshots.create('pause', w)
        }
      }
      // Manual snapshot — Shift+S by default (plan §3, Manual); rebindable.
      if (this.input.isSnapshotPressed()) {
        this.manualSnapshot()
      }
      if (this.input.isResetPressed()) {
        this.resetToMenu()
      }
    }

    if (w.state === 'gameover' || w.state === 'victory') {
      if (this.input.isResetPressed() || this.input.isConfirmPressed()) {
        this.resetToMenu()
      }
    }
  }

  // ---- Mouse-driven menu actions (mirror the keyboard 'menu' branch) ----

  /** Mouse: pick a difficulty option. */
  private menuSelectDifficulty(key: string): void {
    if (this.world.state !== 'menu') return
    const idx = DIFFICULTY_KEYS.indexOf(key)
    if (idx < 0) return
    this.difficultyIndex = idx
    this.world.difficultyKey = DIFFICULTY_KEYS[idx]
    this.world.difficulty = DIFFICULTIES[this.world.difficultyKey]
    this.world.menuCursor = this.resumeSnapshot ? 1 : 0
    this.applyMenuPreview()
    this.audio.init()
    this.audio.resume()
    this.audio.playMenuSelect()
    this.refreshStaticScreen()
  }

  /** Mouse: pick a theme option. */
  private menuSelectTheme(key: string): void {
    if (this.world.state !== 'menu') return
    const idx = THEME_KEYS.indexOf(key)
    if (idx < 0) return
    this.themeIndex = idx
    this.world.themeKey = THEME_KEYS[idx]
    this.world.theme = THEMES[this.world.themeKey]
    this.world.menuCursor = this.resumeSnapshot ? 2 : 1
    this.applyMenuPreview()
    this.audio.init()
    this.audio.resume()
    this.audio.playMenuSelect()
    this.refreshStaticScreen()
  }

  /** Mouse: step the stage selector (dir = -1 prev / +1 next). */
  private menuCycleStage(dir: -1 | 1): void {
    if (this.world.state !== 'menu') return
    this.world.selectedStage =
      (this.world.selectedStage + dir + STAGES.length) % STAGES.length
    this.world.menuCursor = this.resumeSnapshot ? 3 : 2
    // Swap the battle-field preview to the newly selected stage's layout.
    this.applyMenuPreview()
    this.audio.init()
    this.audio.resume()
    this.audio.playMenuSelect()
    this.refreshStaticScreen()
  }

  /** Mouse: start button — same as the keyboard confirm (Enter/Space). */
  private menuStart(): void {
    if (this.world.state !== 'menu') return
    this.audio.init()
    this.audio.resume()
    this.audio.playMenuSelect()
    this.recovery.reset()
    this.prevStageIndex = -1
    this.world.startGame(this.world.difficultyKey, this.world.themeKey, this.world.selectedStage)
    // Drop the click so it can't bleed into the first-frame fire input.
    this.input.reset()
    this.saveSettings()
    this.refreshStaticScreen()
  }

  /** Mouse / default-confirm: resume from the last manual snapshot. */
  private menuResume(): void {
    if (this.world.state !== 'menu' || !this.resumeSnapshot) return
    this.audio.init()
    this.audio.resume()
    this.audio.playMenuSelect()
    // Avoid a spurious stage-start snapshot when the resumed stage begins.
    this.prevStageIndex = this.resumeSnapshot.metadata.stage
    this.recovery.beginLoad(this.resumeSnapshot.id, this.world)
    // Drop the input so the click/keypress can't bleed into gameplay.
    this.input.reset()
    this.refreshStaticScreen()
  }

  /**
   * Render the correct battlefield behind the menu for the current cursor row:
   * the RESUME row (index 0, only when a manual snapshot exists) shows that
   * snapshot's saved content (terrain + tanks + bullets); any config row shows
   * the selected stage's starting layout. Called whenever the highlighted row
   * changes so the canvas always reflects the active selection.
   */
  private applyMenuPreview(): void {
    const w = this.world
    if (this.resumeSnapshot && w.menuCursor === 0) {
      w.previewSnapshot(this.resumeSnapshot.world)
    } else {
      w.previewStage(w.selectedStage)
    }
    // Guarantee the canvas repaints to reflect the swapped battlefield,
    // regardless of the on-demand signature check.
    this.presentation.markNeedsRender()
  }

  // ---- Snapshots (plan §3, §10, §12) ----

  /**
   * Create a Manual snapshot (Shift+S by default / Control Center button). Manual
   * snapshots are never overwritten — when all 100 slots are used, the
   * player is asked to clean up instead (plan §3).
   */
  private manualSnapshot(): void {
    const w = this.world
    if (w.state !== 'playing' && w.state !== 'paused') return

    const snap = this.snapshots.create('manual', w)
    const ui = this.presentation.ui
    if (snap) {
      const m = snap.metadata
      ui.notify(`Snapshot saved — Stage ${String(m.stage + 1).padStart(2, '0')} · ${m.stageName}`)
      this.audio.playMenuSelect()
    } else {
      const limit = this.snapshots.policyFor('manual').limit
      ui.notify(`Manual slots full (${limit}/${limit}) — delete old snapshots in the Browser`, 'warn')
    }
  }

  /** Open the Snapshot Browser (Control Center / recovery menu). */
  private openSnapshotBrowser(): void {
    // Playing → pause first so the world doesn't run behind the modal.
    if (this.world.state === 'playing') {
      this.simulation.togglePause()
      this.snapshots.create('pause', this.world)
    }
    this.presentation.ui.snapshotBrowser.open()
  }

  // ---- Failure Recovery (plan §11) ----

  /**
   * Intercept game-over and transition to the recovery flow.
   * The Simulation has already set state='gameover' and saved the
   * high score; we redirect to 'recovery' so the player can choose
   * to rewind time instead of accepting defeat.
   */
  private startRecovery(): void {
    this.recovery.start(this.world)
    // Publish option availability to the menu UI (greyed-out options).
    this.presentation.ui.setRecoveryAvailability(
      RECOVERY_OPTIONS.map((opt) => this.recovery.isOptionAvailable(opt, this.world)),
    )
  }

  /** Handle keyboard navigation in the recovery menu. */
  private handleRecoveryInput(): void {
    const w = this.world
    if (!this.recovery.isMenuPhase()) return
    // The Snapshot Browser overlays the recovery menu when opened via
    // "Choose a Snapshot…" — it owns all input until closed.
    if (this.presentation.ui.snapshotBrowser.isOpen()) return

    // Navigate up/down
    if (this.input.isUpPressed()) {
      w.recoveryCursor = (w.recoveryCursor - 1 + RECOVERY_OPTION_COUNT) % RECOVERY_OPTION_COUNT
      this.audio.playMenuSelect()
    }
    if (this.input.isDownPressed()) {
      w.recoveryCursor = (w.recoveryCursor + 1) % RECOVERY_OPTION_COUNT
      this.audio.playMenuSelect()
    }

    // Confirm selection
    if (this.input.isConfirmPressed()) {
      const option = RECOVERY_OPTIONS[w.recoveryCursor]
      const result = this.recovery.select(option, w)
      switch (result.kind) {
        case 'transition':
          this.audio.playRecoveryStart()
          break
        case 'browse':
          this.presentation.ui.snapshotBrowser.open()
          this.audio.playMenuSelect()
          break
        case 'continue':
          this.audio.playMenuSelect()
          break
        case 'none':
          // Option unavailable — soft "denied" beep
          this.audio.playMenuSelect()
          break
      }
    }

    // Allow abandoning recovery and returning to menu
    if (this.input.isResetPressed()) {
      this.resetToMenu()
    }
  }

  resetToMenu(): void {
    this.world.state = 'menu'
    this.world.player = null
    this.world.tanks = []
    this.world.bullets = []
    this.world.powerUps = []
    this.world.explosions = []
    this.world.popups = []
    this.world.spawnQueue = []
    this.world.recoveryCountdown = 0
    this.world.recoveryFading = false
    this.recovery.reset()
    this.presentation.ui.snapshotBrowser.close()
    this.prevStageIndex = -1
    // Re-open the menu on its default row and render the matching battlefield.
    this.world.menuCursor = 0
    this.applyMenuPreview()
    this.presentation.reset()
    this.audio.playMenuSelect()
  }

  // ---- Settings ----

  private loadSettings(): GameSettings {
    const defaults: GameSettings = {
      volume: 0.3,
      difficulty: 'classic',
      theme: DEFAULT_THEME,
      screenScale: 1,
      keys: { ...DEFAULT_KEYS },
    }

    try {
      const raw = localStorage.getItem(SETTINGS_KEY)
      if (raw) {
        const saved = JSON.parse(raw)
        return { ...defaults, ...saved, keys: { ...defaults.keys, ...saved.keys } }
      }
    } catch {
      /* ignore */
    }
    return defaults
  }

  saveSettings(): void {
    this.settings.difficulty = this.world.difficultyKey
    this.settings.theme = this.world.themeKey
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(this.settings))
    } catch {
      /* ignore */
    }
  }

  setVolume(v: number): void {
    this.settings.volume = v
    this.audio.setVolume(v)
    this.saveSettings()
  }
}
