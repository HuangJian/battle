import { World } from './World'
import { Simulation } from './Simulation'
import { Input, DEFAULT_KEYS, isModifierCode, parseBinding } from './Input'
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
import { TICK_MS, PERF_MODE_RENDER_FPS } from '../constants'
import type { GameSettings, KeyBindings } from '../types'
import type { GameSnapshot } from '../snapshot/types'
import { InputRecorder } from '../replay/InputRecorder'
import { ReplayManager } from '../replay/ReplayManager'
import { PlaybackController } from '../replay/PlaybackController'
import type { PlaybackSpeed } from '../replay/PlaybackController'
import { createReplayStorage } from '../replay/storage'
import type { Replay, ReplayType } from '../replay/types'
import { GAME_VERSION } from '../snapshot/config'
import { serializeReplayFile, buildReplayFilename } from '../replay/file'

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
  replays: ReplayManager
  private recorder: InputRecorder
  /** Presence flag — NOT a world state. When non-null, playback is active. */
  playback: PlaybackController | null = null

  private lastTime = 0
  private accumulator = 0
  private running = false
  private rafId = 0
  private prevStageIndex = -1
  /** Previous world state, used to detect the transition into `playing`. */
  private prevWorldState: World['state'] = 'menu'
  /** Timestamp of the last canvas repaint (for the render-FPS throttle). */
  private _lastRenderTime = 0
  /** Render FPS cap (0 = uncapped). Driven by Performance Mode. */
  private renderFpsCap = 0
  /** True while the tab is hidden (loop paused by visibilitychange). */
  private _hidden = false
  /**
   * Tracks whether we were in fullscreen on the previous frame so we can
   * suppress the Esc-triggered pause when the browser exits fullscreen
   * (plan §5.2: Esc double-trigger).
   */
  private _wasFullscreen = false
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
    this.presentation = new PresentationLayer(root, this.settings.performanceMode)
    // Wire the live key-bindings object + persistence into the controls panel.
    this.presentation.ui.initControls(this.settings.keys, () => this.saveSettings())

    // Wire mouse-click handlers for the start screen (same World-mutating
    // paths as the keyboard menu input).
    this.presentation.ui.initMenuActions({
      selectDifficulty: (key) => this.menuSelectDifficulty(key),
      selectTheme: (key) => this.menuSelectTheme(key),
      cycleStage: (dir) => this.menuCycleStage(dir),
      selectStage: (index) => this.menuSelectStage(index),
      start: () => this.menuStart(),
      resume: () => this.menuResume(),
      openControls: () => {
        if (this.world.state === 'menu') {
          this.presentation.ui.openControls()
        }
      },
    })

    // Reflect the persisted Performance Mode in the UI (DPR is already applied
    // via the PresentationLayer constructor; here we set the render-FPS cap
    // and the Control Center button state).
    this.renderFpsCap = this.settings.performanceMode ? PERF_MODE_RENDER_FPS : 0
    this.presentation.ui.controlCenter.setPerfModeState(this.settings.performanceMode)

    this.audio = new AudioManager()

    // Snapshot Management Framework (plan/Snapshot-Management-Framework.md)
    this.snapshots = new SnapshotManager({ backend: createDefaultStorage() })
    this.recovery = new RecoveryController(this.snapshots)
    this.wireSnapshotUI()

    // Replay System (plan/replay.md)
    this.replays = new ReplayManager({ backend: createReplayStorage() })
    this.recorder = new InputRecorder()
    this.wireReplayUI()

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
      getStorageBytes: () => Promise.resolve(this.snapshots.estimateBytes()),
    })

    ui.controlCenter.init({
      onManualSave: () => this.manualSnapshot(),
      onOpenBrowser: () => this.openSnapshotBrowser(),
      onOpenReplays: () => this.openReplayBrowser(),
      onTogglePerf: () => ui.togglePerfOverlay(),
      onToggleFullscreen: () => this.presentation.toggleFullscreen(),
      onTogglePerformance: () => this.setPerformanceMode(!this.settings.performanceMode),
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
      getReplayCounts: () => ({
        total: this.replays.count(),
        favorites: this.replays.favoriteCount(),
      }),
      isPlaying: () => this.world.state === 'playing',
      onPause: () => {
        if (this.world.state === 'playing') {
          this.simulation.togglePause()
          this.snapshots.create('pause', this.world)
          this.audio.playPause()
        }
      },
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
    // Developer Performance Observatory hotkey (F6). Toggle only — never
    // consumes the key during gameplay (other F-keys are free, F6 is unbound).
    window.addEventListener('keydown', this.onPerfKey)
    // Load persisted snapshots (IndexedDB) — snapshots survive reloads.
    await this.snapshots.hydrate()
    await this.replays.hydrate()
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
    window.removeEventListener('keydown', this.onPerfKey)
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
    // Playback is an ACTION state regardless of world.state: a replay can
    // drive the world into 'gameover' (∈ LOW_POWER_STATES), and the rAF loop
    // must keep running so PlaybackController.update() and
    // handlePlaybackInput() stay alive (Esc / speed keys / end detection).
    if (!this.playback && LOW_POWER_STATES.has(this.world.state)) {
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
    // During playback the vsync rAF loop owns ALL input (handlePlaybackInput)
    // — never double-process here, even if the replay drove the world into a
    // LOW_POWER state (e.g. 'gameover' at the end of a defeat replay).
    if (this.playback) return
    if (!LOW_POWER_STATES.has(this.world.state)) return
    // UI modals own their own keyboard handling; never double-process.
    if (this.presentation.ui.snapshotBrowser.isOpen()) return
    if (this.presentation.ui.replayBrowser.isOpen()) return
    if (this.presentation.ui.isControlsOpen()) return

    // Process the key via the same code path the loop uses, then clear the
    // per-frame input edges so a single press is consumed exactly once.
    this.handleStateInput()
    this.input.endFrame()
    // Repaint on demand + (re)arm the loop driver if the state changed.
    this.refreshStaticScreen()
  }

  /**
   * Toggle the developer Performance Observatory (F6). The overlay is a
   * read-only debug HUD — toggling it only flips a flag and arms/disarms the
   * renderer's draw-call counter, which is zero-cost while off.
   */
  private onPerfKey = (e: KeyboardEvent): void => {
    if (!(e.altKey && e.code === 'KeyD')) return
    e.preventDefault()
    const perf = this.presentation.ui.perfOverlay
    perf.toggle()
    // Arm/disarm the dev draw-call counter so it adds no overhead when off.
    this.presentation.renderer.setDrawCallCounting(perf.active)
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

    // --- Performance Observatory probes (gated: zero cost when overlay off) ---
    const perfOverlay = this.presentation.ui.perfOverlay
    const renderer = this.presentation.renderer
    const probe = perfOverlay.active
    let frameT0 = 0
    let simT0 = 0
    let simDt = 0
    let renderT0 = 0
    let renderDt = 0
    let uiT0 = 0
    let uiDt = 0
    if (probe) {
      frameT0 = performance.now()
      // Re-arm the dev draw-call counter (early-returns if already armed).
      renderer.setDrawCallCounting(true)
    }

    // Handle menu/game state input
    if (this.playback) {
      this.handlePlaybackInput()
    } else {
      this.handleStateInput()
    }

    // Fixed timestep simulation
    let steps = 0
    let enteredGameOver = false
    if (probe) simT0 = performance.now()
    if (this.playback) {
      // Playback mode: PlaybackController drives ticks directly
      this.playback.update(dt)
      // Replay ran out of frames → leave playback EXPLICITLY. Without this
      // the replay world (still 'playing'/'stageclear') would fall through
      // to the live branch next frame: the keyboard would take over the
      // replay's tank, and the stage-change detector would start recording
      // a bogus session from mid-replay state.
      if (this.playback.isEnded) {
        this.finishPlayback()
      }
    } else {
      // Live gameplay: record input per tick, inside the while-loop
      while (this.accumulator >= TICK_MS && steps < 5) {
        if (
          this.world.state === 'playing' ||
          this.world.state === 'stageclear' ||
          this.world.state === 'gameover'
        ) {
          this.simulation.tick()
          // Record THIS tick's input (one frame per tick)
          this.recorder.recordFrame(this.input)

          // Detect stage change → Stage Start snapshot (plan §3, §10)
          if (this.world.stageIndex !== this.prevStageIndex && this.world.state === 'playing') {
            this.snapshots.create('stage-start', this.world)
            this.snapshots.resetAutoTimer()
            this.prevStageIndex = this.world.stageIndex
            // Start a new recording session for the new stage
            this.recorder.startNew(this.world)
          }

          // Detect stage clear → save victory replay
          if (this.world.state === 'stageclear' && this.prevWorldState !== 'stageclear') {
            this.finalizeRecording('clear')
          }

          // Detect game over → intercept for recovery
          if (this.world.state === 'gameover' && !enteredGameOver) {
            // Determine specific defeat cause for the four-state ReplayType
            const defeatType = this.world.tileMap.isBaseDestroyed() ? 'base' : 'died'
            this.finalizeRecording(defeatType)
            enteredGameOver = true
            this.startRecovery()
            break // stop ticking — simulation is now suspended
          }
        }
        this.accumulator -= TICK_MS
        steps++
      }

      // Manual "时光宝盒" rewind — consume the pending flag set by
      // Simulation.activateRewind (F7). The actual fade→restore→countdown
      // is owned by RecoveryController (same flow as Load Latest). Stock was
      // already spent in activateRewind; refund it if the rewind can't start.
      if (this.world.rewindPending) {
        this.world.rewindPending = false
        const canStart = this.recovery.phase === 'idle' && this.world.state === 'playing'
        if (canStart && this.recovery.beginManualRewind(this.world)) {
          this.audio.playRecoveryStart()
          this.presentation.ui.notify('时光宝盒：时间回溯！', 'info')
        } else {
          this.world.rewindStock++
        }
      }
    }
    if (probe) simDt = performance.now() - simT0

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
        // The world was just atomically restored (or freshly restarted) —
        // this is the exact deterministic boundary a replay must start from.
        // Recording is restarted HERE, never at beginLoad() time: the restore
        // is deferred until the fade completes, so an earlier startNew()
        // would capture the pre-restore world (a corrupted replay). This
        // also revives the recorder after a defeat finalized it (recovery →
        // load/restart must produce a fresh recording session).
        this.recorder.startNew(this.world)
        // The restored stage is not a "stage change" — keep the detector
        // quiet so it doesn't overwrite this session / snapshot a mid-stage
        // world as 'stage-start'.
        this.prevStageIndex = this.world.stageIndex
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

    // Auto snapshots — every 30 s of live gameplay (plan §3, §10).
    // Guarded by !this.playback: replays drive a synthetic world that
    // should never trigger persistence side-effects.
    if (this.world.state === 'playing' && !this.playback) {
      this.snapshots.updateAuto(this.world, dt)
    }

    // Replay thumbnails — capture BEFORE events trigger visual effects
    // (stage-clear flash, camera shake, particles). The canvas still shows
    // the previous frame's clean render at this point, which is exactly
    // what we want for the thumbnail — no overlay, no flash.
    if (this.replays.hasPendingThumbnails) {
      this.replays.capturePendingThumbnails(() => this.presentation.captureThumbnail())
    }

    // Process events — pass to both audio and presentation
    const events = this.world.consumeEvents()
    this.audio.handleEvents(events)
    this.presentation.handleEvents(events)

    // Render — on-demand energy saver. The full canvas repaint is skipped
    // unless the visible scene changed (PresentationLayer.shouldRender) and the
    // renderFpsCap throttle allows it (0 = uncapped). When Performance Mode is
    // on, renderFpsCap = PERF_MODE_RENDER_FPS (30), halving GPU traffic again;
    // when off, it is 0 and gameplay renders at full vsync rate. This keeps the
    // GPU idle — instead of repainting 60×/sec — during menu, pause, game-over,
    // and idle lulls, so the fan stays off. Input, simulation, and the HUD
    // still run every frame.
    const wantRender = this.presentation.shouldRender(this.world)
    const canRender =
      this.renderFpsCap <= 0 || time - this._lastRenderTime >= 1000 / this.renderFpsCap
    let rendered = false
    if (probe) {
      renderT0 = performance.now()
      // Reset the dev draw-call counter; it re-accumulates only if we actually
      // repaint this frame (on-demand idle frames stay at 0 — accurate).
      renderer.debugDrawCalls = 0
    }
    if (wantRender && canRender) {
      this.presentation.render(this.world, dt)
      this._lastRenderTime = time
      rendered = true
    }
    if (probe) renderDt = performance.now() - renderT0

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
    if (probe) uiT0 = performance.now()
    this.presentation.updateUI(this.world)
    // Sync replay progress bar and time during playback
    if (this.playback) {
      this.presentation.ui.setReplayProgress(this.playback.progress)
      const replay = this.playback.replay
      if (replay) {
        this.presentation.ui.setReplayTime(
          Math.round(this.playback.progress * replay.durationMs),
          replay.durationMs,
        )
      }
    }
    if (probe) uiDt = performance.now() - uiT0

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

    // --- Performance Observatory: publish the per-frame sample (overlay only) ---
    if (probe) {
      const frameDt = performance.now() - frameT0
      perfOverlay.update(this.world, renderer, this.presentation.particles, {
        fps: this.fps,
        frameMs: frameDt,
        simMs: simDt,
        renderMs: renderDt,
        uiMs: uiDt,
        perfMode: this.settings.performanceMode,
      })
    }

    // Reclaim keyboard focus whenever we (re)enter active play. After a stage
    // transition, an unpause, a recovery resume, or a fresh start the browser
    // may have moved focus elsewhere (stage-clear overlay, the Alt menu, the
    // address bar), which silently breaks Alt+S/R/T until the player clicks
    // the canvas. Focusing the tabbable canvas restores the document focus so
    // the window-level keydown keeps firing — no manual click required.
    if (this.world.state === 'playing' && this.prevWorldState !== 'playing') {
      this.refocusGame()
    }
    this.prevWorldState = this.world.state

    this.scheduleFrame()
  }

  /**
   * Reclaim keyboard focus for the page by focusing the (now tabbable) canvas.
   *
   * `Input` listens on `window`, so shortcuts like Alt+S only fire while the
   * *document* has keyboard focus. After a stage transition, an unpause, a
   * recovery resume, or a fresh start, the browser may have moved focus
   * elsewhere (stage-clear overlay, the Alt menu, the address bar), which
   * silently breaks Alt+S/R/T until the player clicks the canvas. Focusing a
   * focusable element inside the document is the reliable way to restore focus
   * — and `canvas.focus()` does not require a user gesture, so it works the
   * instant a new stage begins.
   */
  private refocusGame(): void {
    try {
      this.presentation.ui.canvas.focus({ preventScroll: true })
    } catch {
      /* focus() is a no-op / throws in unsupported or headless contexts */
    }
  }

  private handleStateInput(): void {
    const w = this.world

    // The Snapshot / Replay Browsers are UI-modals that own key input while
    // open (Esc is captured by the browser itself); skip game state input.
    if (this.presentation.ui.snapshotBrowser.isOpen()) return
    if (this.presentation.ui.replayBrowser.isOpen()) return

    // Fullscreen toggle — available in all states (menu / playing / paused).
    if (this.input.isFullscreenPressed()) {
      this.presentation.toggleFullscreen()
    }

    if (w.state === 'menu') {
      // The controls panel is a UI-modal that owns all key input while open;
      // skip menu navigation so it doesn't fight the panel.
      if (this.presentation.ui.isControlsOpen()) return

      // Row count grows by one when a resumable manual snapshot is offered
      // (the RESUME row sits at index 0, pushing the config rows down).
      // Full order: RESUME? / DIFFICULTY / THEME / STAGE / NEW GAME / CONTROLS
      const rowCount = this.resumeSnapshot ? 6 : 5
      // Move cursor between rows (RESUME? / DIFFICULTY / THEME / STAGE)
      // Canvas preview only switches for RESUME (0), STAGE (off+2), NEW GAME (off+3).
      const off = this.resumeSnapshot ? 1 : 0
      if (this.input.isUpPressed()) {
        w.menuCursor = (w.menuCursor - 1 + rowCount) % rowCount
        if (w.menuCursor === 0 || w.menuCursor === off + 2 || w.menuCursor === off + 3) {
          this.applyMenuPreview()
        }
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
      }
      if (this.input.isDownPressed()) {
        w.menuCursor = (w.menuCursor + 1) % rowCount
        if (w.menuCursor === 0 || w.menuCursor === off + 2 || w.menuCursor === off + 3) {
          this.applyMenuPreview()
        }
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
      }
      // Change value of the selected row
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
      // Theme shortcut (Alt+T by default — see KeyBindings)
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
      // Confirm — RESUME, NEW GAME, and CONTROLS respond to Enter:
      // RESUME (index 0, only when a snapshot exists) resumes;
      // NEW GAME (off + 3) starts a fresh game;
      // CONTROLS (off + 4) opens the key-bindings panel.
      const controlsIdx = off + 4
      if (this.input.isConfirmPressed()) {
        if (this.resumeSnapshot && w.menuCursor === 0) {
          this.menuResume()
        } else if (w.menuCursor === off + 3) {
          this.menuStart()
        } else if (w.menuCursor === controlsIdx) {
          this.presentation.ui.openControls()
          this.audio.init()
          this.audio.resume()
          this.audio.playMenuSelect()
        }
      }
      return
    }

    // Suppress the Esc-triggered pause when the browser exits fullscreen
    // via its built-in Esc handler (plan §5.2). The browser fires both
    // fullscreenchange AND keydown(Escape); without this guard, exiting
    // fullscreen also pauses the game.
    // Suppress the Esc-triggered pause when the browser exits fullscreen
    // via its built-in Esc handler (plan §5.2). The browser fires both
    // fullscreenchange AND keydown(Escape); without this guard, exiting
    // fullscreen also pauses the game.
    const justExitedFullscreen = this._wasFullscreen && !document.fullscreenElement
    this._wasFullscreen = !!document.fullscreenElement

    if (w.state === 'playing' || w.state === 'paused') {
      if (this.input.isPausePressed()) {
        if (justExitedFullscreen) {
          // Consume the Esc without toggling pause
        } else {
          this.simulation.togglePause()
          this.audio.playPause()
          // Entering pause → Pause snapshot (plan §3: created on pause,
          // captures the exact moment for a safe later return).
          if (w.state === 'paused') {
            this.snapshots.create('pause', w)
          }
        }
      }
      // Manual snapshot — Alt+S by default (plan §3, Manual); rebindable.
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
    this.world.selectedStage = (this.world.selectedStage + dir + STAGES.length) % STAGES.length
    this.world.menuCursor = this.resumeSnapshot ? 3 : 2
    // Swap the battle-field preview to the newly selected stage's layout.
    this.applyMenuPreview()
    this.audio.init()
    this.audio.resume()
    this.audio.playMenuSelect()
    this.refreshStaticScreen()
  }

  /** Mouse: select a specific stage from the dropdown list. */
  private menuSelectStage(index: number): void {
    if (this.world.state !== 'menu') return
    if (index < 0 || index >= STAGES.length) return
    this.world.selectedStage = index
    this.world.menuCursor = this.resumeSnapshot ? 3 : 2
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
    // NOTE: recording is NOT started here — the stage-change detector in
    // loop() starts the session on the first played tick (same as the
    // keyboard start path), so both entry points share one code path.
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
    // NOTE: recording is NOT started here — beginLoad() defers the actual
    // restore until its fade completes, so a startNew() at this point would
    // capture the MENU PREVIEW world as the replay's initial snapshot
    // (corrupted replay). The recording session starts at the recovery
    // fading→countdown transition in loop(), right after restoreWorld.
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
  /**
   * Toggle Performance Mode (persisted). When ON: render DPR is capped at 1
   * (the browser upscales with `image-rendering: pixelated`, which both slashes
   * GPU fill-rate ~4× on Retina and looks more retro) and the render FPS is
   * capped at PERF_MODE_RENDER_FPS. When OFF: full DPR (capped at 2) + uncapped
   * 60 FPS. The simulation timestep is untouched — only the paint path changes.
   */
  private setPerformanceMode(on: boolean): void {
    if (this.settings.performanceMode === on) return
    this.settings.performanceMode = on
    this.renderFpsCap = on ? PERF_MODE_RENDER_FPS : 0
    this.presentation.applyPerformanceMode(on)
    this.presentation.ui.controlCenter.setPerfModeState(on)
    this.presentation.markNeedsRender()
    this.saveSettings()
    this.audio.init()
    this.audio.resume()
    this.audio.playMenuSelect()
    // Confirm the switch in-game (menu / pause) so the player sees the result
    // without an overlay covering the battle field.
    this.presentation.ui.notify(
      this.settings.performanceMode ? 'Performance Mode: ON' : 'Performance Mode: OFF (Quality)',
      'info',
    )
  }

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
   * Create a Manual snapshot (Alt+S by default / Control Center button). Manual
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
      ui.notify(
        `Manual slots full (${limit}/${limit}) — delete old snapshots in the Browser`,
        'warn',
      )
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
    this.stopPlayback()
    this.recorder.reset()
    this.presentation.ui.snapshotBrowser.close()
    this.presentation.ui.replayBrowser.close()
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
      performanceMode: false,
      keys: { ...DEFAULT_KEYS },
    }

    try {
      const raw = localStorage.getItem(SETTINGS_KEY)
      if (raw) {
        const saved = JSON.parse(raw)
        const merged = { ...defaults, ...saved, keys: { ...defaults.keys, ...saved.keys } }
        // Repair any previously-saved binding whose primary key is a pure
        // modifier (e.g. the old "Alt+AltLeft" capture bug). Such a binding can
        // never fire, so we fall back to its default.
        merged.keys = this.sanitizeKeys(merged.keys)
        return merged
      }
    } catch {
      /* ignore */
    }
    return defaults
  }

  /**
   * Reset any binding whose primary key is a pure modifier (Alt/Shift/Ctrl/
   * Meta themselves) — these are un-fireable — back to its default. Guards
   * against the historical rebind bug and any corrupt saved value.
   */
  private sanitizeKeys(keys: KeyBindings): KeyBindings {
    const out: KeyBindings = { ...keys }
    for (const action of Object.keys(DEFAULT_KEYS) as (keyof KeyBindings)[]) {
      const binding = out[action]
      if (!binding || isModifierCode(parseBinding(binding).code)) {
        out[action] = DEFAULT_KEYS[action]
      }
    }
    return out
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

  // ---- Replay System (plan/replay.md) ----

  /**
   * Finalize the current recording and save as a replay.
   * Called on stage clear (victory) or game over (defeat).
   */
  private finalizeRecording(type: ReplayType): void {
    const result = this.recorder.finalize()
    if (!result) return // empty recording

    const w = this.world
    const metadata = {
      stage: w.stageIndex,
      stageName: STAGES[w.stageIndex]?.name ?? '?',
      difficulty: w.difficultyKey,
      lives: w.lives,
      playerLevel: w.playerLevel,
      score: w.score,
      killCount: w.killCount,
      enemiesTotal: w.enemiesSpawned,
      playTimeMs: w.playTimeMs,
    }
    const replay = this.replays.create(
      type,
      result.snapshot,
      result.frames,
      result.tickCount,
      metadata,
    )
    this.replays.enqueueThumbnail(replay.id)
  }

  /**
   * Start replay playback. Operates on Game's own world and simulation.
   * Returns false (with a toast) when the replay's frame format is not
   * playable by this build (plan/replay.md §17.2).
   */
  startPlayback(replay: Replay): boolean {
    if (!this.replays.canPlay(replay)) {
      this.presentation.ui.notify('Replay format not supported by this version', 'warn')
      return false
    }
    if (replay.gameVersion !== GAME_VERSION) {
      // Different simulation build — playable, but determinism is not
      // guaranteed. Warn instead of silently desyncing.
      this.presentation.ui.notify(
        `Recorded on v${replay.gameVersion} — playback may desync`,
        'warn',
      )
    }
    // Exit any existing playback first
    this.stopPlayback()
    // Discard any in-progress recording
    this.recorder.reset()
    this.recovery.reset()
    this.presentation.ui.snapshotBrowser.close()
    this.presentation.ui.replayBrowser.close()
    this.playback = new PlaybackController(replay)
    this.playback.start(this.world, this.simulation)
    // Presentation is disposable (AGENTS §2.5): the world was atomically
    // replaced — rebuild all visual state (particles, camera, animations).
    this.presentation.reset()
    this.presentation.markNeedsRender()
    this.prevWorldState = this.world.state
    this.prevStageIndex = this.world.stageIndex
    this.accumulator = 0
    this.lastTime = performance.now()
    this.scheduleFrame()
    // Show persistent REPLAY badge in HUD + video player controller
    this.presentation.ui.setReplayMode(true, false)
    this.presentation.ui.setReplaySpeed(this.playback.currentSpeed)
    this.presentation.ui.notify('REPLAY — Esc exit')
    // Wire canvas click/mousemove for playback interaction
    this.presentation.ui.canvas.addEventListener('click', this.onReplayCanvasClick)
    this.presentation.ui.canvas.addEventListener('mousemove', this.onReplayCanvasMouseMove)
    // Wire the video player controller callbacks
    this.presentation.ui.replayController.init({
      onPlayPause: () => {
        if (!this.playback) return
        this.playback.togglePause()
        this.presentation.ui.setReplayMode(true, this.playback.isPaused)
      },
      onSeek: (progress: number) => {
        if (!this.playback) return
        this.playback.seekTo(this.world, this.simulation, progress)
        this.presentation.ui.setReplayMode(true, true)
        this.presentation.markNeedsRender()
      },
      onSpeedChange: (speed: number) => {
        this.setPlaybackSpeed(speed as import('../replay/PlaybackController').PlaybackSpeed)
      },
      onExit: () => {
        this.stopPlayback()
        this.resetToMenu()
      },
      onReplayAgain: () => {
        // Replay the same replay from the beginning
        if (!this.playback) return
        const replay = this.playback.replay
        this.stopPlayback()
        if (replay) this.startPlayback(replay)
      },
      onBackToMenu: () => {
        this.stopPlayback()
        this.resetToMenu()
      },
      onProgressHover: (progress: number) => {
        // Instant thumbnail from pre-computed keyframes — no simulation replay
        if (!this.playback || this.playback.isEnded) return
        const thumbData = this.playback.getThumbnailAt(progress)
        if (thumbData) {
          const thumbCanvas = this.presentation.ui.replayController.getThumbnailCanvas()
          const ctx = thumbCanvas.getContext('2d')
          if (ctx) {
            ctx.putImageData(thumbData, 0, 0)
          }
        }
      },
      onHoverStart: () => {
        /* no-op: keyframes are pre-computed at playback start */
      },
      onProgressHoverEnd: () => {
        /* no-op: keyframes are pre-computed at playback start */
      },
    })
    // Pre-compute thumbnail keyframes for instant hover preview
    this.buildThumbnailKeyframes()
    return true
  }

  /**
   * Build thumbnail keyframes for the current replay by replaying the
   * simulation once and capturing the canvas at regular intervals.
   * Runs synchronously — the brief freeze is acceptable for instant hover.
   */
  private buildThumbnailKeyframes(): void {
    if (!this.playback) return
    this.playback.buildKeyframes(
      this.world,
      this.simulation,
      (w) => {
        if (this.presentation.shouldRender(w)) {
          this.presentation.render(w, 0)
        }
      },
      () => {
        // Capture 160×160 thumbnail from the main canvas
        const canvas = this.presentation.ui.canvas
        const tmpCanvas = document.createElement('canvas')
        tmpCanvas.width = 160
        tmpCanvas.height = 160
        const ctx = tmpCanvas.getContext('2d')!
        ctx.drawImage(canvas, 0, 0, 160, 160)
        return ctx.getImageData(0, 0, 160, 160)
      },
    )
  }

  /**
   * Stop playback, restore real Input, clean up.
   */
  stopPlayback(): void {
    if (!this.playback) return
    this.playback.exit(this.simulation, this.input)
    this.playback = null
    // Hide the persistent REPLAY badge from the HUD
    this.presentation.ui.setReplayMode(false)
    // Remove canvas listeners
    this.presentation.ui.canvas.removeEventListener('click', this.onReplayCanvasClick)
    this.presentation.ui.canvas.removeEventListener('mousemove', this.onReplayCanvasMouseMove)
    this.accumulator = 0
    this.lastTime = performance.now()
  }

  /**
   * The replay consumed all frames — stop playback but stay on the last frame.
   * The controller stays visible so the user can scrub back or exit manually.
   * We keep the rAF loop alive by NOT entering idle mode (playback acts as
   * a sentinel), so the canvas keeps rendering the final frame.
   */
  private finishPlayback(): void {
    if (!this.playback) return
    const replay = this.playback.replay
    // Exit the playback controller but keep it as a sentinel so scheduleFrame()
    // keeps the rAF loop alive (the world may be in a LOW_POWER state like
    // 'gameover' which would otherwise stop the loop).
    this.playback.exit(this.simulation, this.input)
    // Hide the REPLAY badge but keep the controller visible (persistent mode)
    this.presentation.ui.setReplayMode(false)
    // Populate end overlay with replay metadata
    if (replay) {
      const m = replay.metadata
      const stageLabel = `Stage ${String(m.stage + 1).padStart(2, '0')}: ${m.stageName}`
      const resultLabel = replay.type === 'clear' ? 'VICTORY' : 'DEFEAT'
      const durationSec = Math.floor(replay.durationMs / 1000)
      const durMin = Math.floor(durationSec / 60)
      const durSec = durationSec % 60
      const durationStr = `${durMin}:${String(durSec).padStart(2, '0')}`
      const detailParts = [
        resultLabel,
        `Score: ${String(m.score).padStart(6, '0')}`,
        durationStr,
        `Kills: ${m.killCount}/${m.enemiesTotal}`,
      ]
      this.presentation.ui.replayController.setEndMetadata({
        title: stageLabel,
        details: detailParts.join('  ·  '),
        result: replay.type as 'clear' | 'base' | 'died',
      })
    }
    this.presentation.ui.replayController.showPersistent()
    this.presentation.markNeedsRender()
    this.presentation.ui.notify('Replay finished')
    // DO NOT null out this.playback — it acts as a sentinel to keep the loop alive.
    // The loop will continue to render the final frame without ticking.
  }

  /**
   * Dedicated handler for playback keyboard (ESC, pause, speed).
   * Replaces handleStateInput() during playback so live-game shortcuts
   * (Alt+S, Alt+R, KeyP) don't fire on the replay world.
   */
  private handlePlaybackInput(): void {
    if (!this.playback) return
    if (this.input.wasPressed('Escape')) {
      this.stopPlayback()
      this.resetToMenu()
      return
    }
  }

  /** Canvas click during replay → toggle play/pause and show controller. */
  private onReplayCanvasClick = (): void => {
    if (!this.playback) return
    this.playback.togglePause()
    this.presentation.ui.setReplayMode(true, this.playback.isPaused)
  }

  /** Canvas mousemove during replay → show controller and reset auto-hide. */
  private onReplayCanvasMouseMove = (): void => {
    if (!this.playback || this.playback.isEnded) return
    this.presentation.ui.replayController.show()
    this.presentation.ui.setReplayMode(true, this.playback.isPaused)
  }

  private setPlaybackSpeed(speed: PlaybackSpeed): void {
    if (!this.playback || this.playback.currentSpeed === speed) return
    this.playback.setSpeed(speed)
    this.presentation.ui.setReplaySpeed(speed)
  }

  /** Wire the Replay Browser + Control Center replay entry. */
  private wireReplayUI(): void {
    const ui = this.presentation.ui

    ui.replayBrowser.init({
      getReplays: () => this.replays.getAll(),
      onPlay: (id) => {
        const replay = this.replays.get(id)
        if (replay) this.startPlayback(replay)
      },
      onDelete: (id) => {
        this.replays.delete(id)
        ui.notify('Replay deleted')
      },
      onToggleFavorite: (id) => {
        const replay = this.replays.get(id)
        if (!replay) return false
        const wasFavorite = replay.isFavorite
        const nowFavorite = this.replays.toggleFavorite(id)
        if (!wasFavorite && !nowFavorite) {
          ui.notify('Favorites are full — unfavorite some replays first', 'warn')
        }
        return nowFavorite
      },
      onClose: () => {
        // Regular screen sync resumes automatically (mirrors SnapshotBrowser).
      },
      getStorageBytes: () => Promise.resolve(this.replays.estimateBytes()),
      onImport: (replay) => {
        this.replays.addReplay(replay)
        ui.notify(`Imported: Stage ${String(replay.metadata.stage + 1).padStart(2, '0')} — ${replay.metadata.stageName}`)
      },
      onExport: (id) => {
        const replay = this.replays.get(id)
        if (!replay) return
        const envelope = serializeReplayFile({
          source: 'browser',
          initialSnapshot: replay.initialSnapshot,
          frames: replay.frames,
          totalTicks: replay.totalTicks,
          metadata: replay.metadata,
        })
        const filename = buildReplayFilename({
          difficulty: replay.metadata.difficulty,
          stageIndex: replay.metadata.stage,
          status: replay.type,
          lives: replay.metadata.lives,
          totalTicks: replay.totalTicks,
          seed: 0,
        })
        const blob = new Blob([envelope], { type: 'application/json' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = filename
        a.click()
        URL.revokeObjectURL(url)
        ui.notify(`Exported: ${filename}`)
      },
    })
  }

  /** Open the Replay Browser (Control Center button). */
  private openReplayBrowser(): void {
    // Never on top of an active playback or the recovery flow.
    if (this.playback) return
    if (this.world.state === 'recovery') return
    // Playing → pause first so the world doesn't run behind the modal.
    if (this.world.state === 'playing') {
      this.simulation.togglePause()
      this.snapshots.create('pause', this.world)
    }
    this.presentation.ui.replayBrowser.open()
  }
}
