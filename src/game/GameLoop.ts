// ================================================================
// LoopController — extracted from the former GameLoop.ts mixin
// (plan/refactor.agy.md §1.1 mixin→composition). Bodies moved verbatim:
// `this.<slice-own>` stayed, everything else goes through the Game
// orchestrator back-reference (`this.g`). Cross-slice entry points are
// delegated on Game itself.
// ================================================================
import { LOW_POWER_STATES, MAX_LIVE_STEPS, TICK_MS, SEED_HASH } from '../constants'
import { spriteLibrary } from '../presentation/renderer/SpriteLibrary'
import { RNG } from '../utils/RNG'
import { GodAIInput } from '../ai/GodAIInput'
import { AutoFireInput } from './AutoFireInput'
import { cycleBattleSpeed } from './battleSpeed'
import { t } from '../i18n'
import type { Game } from './Game'

export class LoopController {
  constructor(private g: Game) {}
  async start(): Promise<void> {
    this.g.input.attach(window)
    // Static-screen (menu / pause / game-over / victory) keyboard input is
    // event-driven: a single keydown listener processes it the instant a key
    // is pressed so the loop can stay fully asleep (0-loop idle) on those
    // screens. Registered AFTER input.attach so Input.onKeyDown populates the
    // polled `justPressed` set before we read it.
    window.addEventListener('keydown', this.onStaticKey)
    // Developer Performance Observatory hotkey (Alt+D). Toggle only — never
    // consumes the key during gameplay (F6 is bound to the frenzy super-item).
    window.addEventListener('keydown', this.onPerfKey)
    // 督战 battle-speed hotkeys (Alt+> faster / Alt+< slower) — live play AND
    // replay playback, so the same shortcuts work wherever ticks are running.
    window.addEventListener('keydown', this.onSpeedKey)
    // Load persisted snapshots (IndexedDB) — snapshots survive reloads.
    await this.g.snapshots.hydrate()
    await this.g.replays.hydrate()
    // Default-load behaviour: if a manual snapshot exists, surface it as the
    // start screen's RESUME target so reopening the page continues from it.
    this.g.resumeSnapshot = this.g.snapshots.latest({ type: 'manual' })
    this.g.presentation.ui.setResumeTarget(
      this.g.resumeSnapshot
        ? {
            stage: this.g.resumeSnapshot.metadata.stage,
            stageName: this.g.resumeSnapshot.metadata.stageName,
            score: this.g.resumeSnapshot.metadata.score,
          }
        : null,
    )
    // Open the menu on its default row and render the matching battlefield:
    // the RESUME target's saved content (if a manual snapshot exists) or the
    // selected stage's starting layout otherwise.
    this.g.world.ui.menuCursor = 0
    this.g.applyMenuPreview()
    // Preload the SVG asset library so sprites are ready for the first frame.
    await spriteLibrary.load()
    // Pre-rasterize sprites to canvas bitmaps for fast rendering
    this.g.presentation.initSpriteCache(spriteLibrary)
    this.g.running = true
    this.g.lastTime = performance.now()
    document.addEventListener('visibilitychange', this.onVisibility)
    this.loop(this.g.lastTime)
  }

  stop(): void {
    this.g.running = false
    cancelAnimationFrame(this.g.rafId)
    this.g.rafId = 0
    document.removeEventListener('visibilitychange', this.onVisibility)
    window.removeEventListener('keydown', this.onStaticKey)
    window.removeEventListener('keydown', this.onPerfKey)
    window.removeEventListener('keydown', this.onSpeedKey)
    this.g.input.detach(window)
  }

  /**
   * Pause the loop when the tab is hidden (stops all GPU/CPU work — the single
   * biggest energy saver for a backgrounded game) and resume cleanly on return.
   */
  onVisibility = (): void => {
    if (document.hidden) {
      if (!this.g._hidden) {
        this.g._hidden = true
        cancelAnimationFrame(this.g.rafId)
      }
    } else if (this.g._hidden) {
      this.g._hidden = false
      if (this.g.running) {
        // The AudioContext is often auto-suspended while the tab is hidden;
        // resume it when we come back so a running replay (or live game)
        // doesn't fall silent. resume() is a no-op if already running.
        this.g.audio.resume()
        this.g.lastTime = performance.now()
        const lowPower = LOW_POWER_STATES.has(this.g.world.state)
        // A paused replay (or an ended replay kept alive by the playback
        // sentinel) sits in a LOW_POWER state yet MUST keep the rAF loop
        // alive: play / resume / seek and the progress bar are driven by the
        // loop, so going truly idle here freezes the replay controls after the
        // tab returns from the background. Only sleep when there is no
        // playback at all.
        const idle = lowPower && !this.g.playback
        if (lowPower) {
          // No loop runs while idle — repaint once so the canvas isn't blank
          // after the tab was hidden (browsers may discard the backing store).
          this.g.presentation.markNeedsRender()
          this.g.presentation.updateUI(this.g.world)
          if (this.g.presentation.shouldRender(this.g.world)) {
            this.g.presentation.render(this.g.world, 0)
          }
        }
        if (!idle) {
          this.g.presentation.markNeedsRender()
          this.scheduleFrame()
        }
      }
    }
  }

  /**
   * Pick the loop driver: vsync rAF for action states (smooth 60 FPS play),
   * or nothing for the static low-power states (menu / pause / game-over /
   * victory) — those are event-driven, so the main thread genuinely sleeps.
   * Only one driver is ever pending; we clear both before (re)arming to avoid
   * a straggler rAF/timer waking the thread after the state has flipped.
   */
  scheduleFrame(): void {
    if (!this.g.running) return
    cancelAnimationFrame(this.g.rafId)
    this.g.rafId = 0
    // Playback is an ACTION state regardless of world.state: a replay can
    // drive the world into 'gameover' (∈ LOW_POWER_STATES), and the rAF loop
    // must keep running so PlaybackController.update() and
    // handlePlaybackInput() stay alive (Esc / speed keys / end detection).
    if (!this.g.playback && LOW_POWER_STATES.has(this.g.world.state)) {
      // True idle: no loop at all. Static-screen input is handled by
      // `onStaticKey` / mouse handlers, and the on-demand render gate keeps
      // the canvas correct, so the main thread stays fully asleep — fan off.
      return
    }
    this.g.rafId = requestAnimationFrame(this.loop)
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
  onStaticKey = (_e: KeyboardEvent): void => {
    if (!this.g.running || this.g._hidden) return
    // During playback the vsync rAF loop owns ALL input (handlePlaybackInput)
    // — never double-process here, even if the replay drove the world into a
    // LOW_POWER state (e.g. 'gameover' at the end of a defeat replay).
    if (this.g.playback) return
    if (!LOW_POWER_STATES.has(this.g.world.state)) return
    // UI modals own their own keyboard handling; never double-process.
    if (this.g.presentation.ui.snapshotBrowser.isOpen()) return
    if (this.g.presentation.ui.replayBrowser.isOpen()) return
    if (this.g.presentation.ui.isControlsOpen()) return

    // Process the key via the same code path the loop uses, then clear the
    // per-frame input edges so a single press is consumed exactly once.
    this.g.handleStateInput()
    this.g.input.endFrame()
    // Repaint on demand + (re)arm the loop driver if the state changed.
    this.refreshStaticScreen()
  }

  /**
   * 督战 battle-speed hotkeys: Alt+> faster, Alt+< slower (US-layout `>` is
   * Shift+Period, `<` is Shift+Comma). Event-driven so it fires during live
   * play, pause, AND replay playback regardless of which driver owns the loop.
   * Live speed is a Game field (never World state — cadence only, AGENTS §2.3);
   * during playback it routes to the replay's own speed control.
   */
  onSpeedKey = (e: KeyboardEvent): void => {
    if (!e.altKey) return
    let dir: 1 | -1 | null = null
    if (e.code === 'Period') dir = 1
    else if (e.code === 'Comma') dir = -1
    if (dir === null) return
    // Speed is a live-play viewing aid — ignore it on menu / game-over /
    // victory / recovery screens so a stray press can't leak a non-×1 speed
    // into the next fresh run (menuStart never resets it — only resetToMenu
    // does). Paused stays allowed so the player can set speed before resuming.
    const s = this.g.world.state
    if (!this.g.playback && s !== 'playing' && s !== 'paused' && s !== 'stageclear') return
    e.preventDefault()
    if (this.g.playback) {
      const cur = this.g.playback.currentSpeed
      this.g.setPlaybackSpeed(cycleBattleSpeed(cur, dir))
    } else {
      this.g.adjustBattleSpeed(dir)
    }
  }

  /**
   * Toggle the developer Performance Observatory (Alt+D). The overlay is a
   * read-only debug HUD — toggling it only flips a flag and arms/disarms the
   * renderer's draw-call counter, which is zero-cost while off.
   */
  onPerfKey = (e: KeyboardEvent): void => {
    if (!(e.altKey && e.code === 'KeyD')) return
    e.preventDefault()
    const perf = this.g.presentation.ui.perfOverlay
    perf.toggle()
    // Arm/disarm the dev draw-call counter so it adds no overhead when off.
    this.g.presentation.renderer.setDrawCallCounting(perf.active)
  }

  /**
   * Repaint the canvas only if the scene actually changed, sync the HUD,
   * capture any pending snapshot thumbnail, and (re)arm the right loop driver
   * for the current state. Shared by `onStaticKey` and the mouse-driven menu
   * actions so both paths behave identically under 0-loop idle.
   */
  refreshStaticScreen(): void {
    this.g.presentation.updateUI(this.g.world)
    if (this.g.presentation.shouldRender(this.g.world)) {
      this.g.presentation.render(this.g.world, 0)
      this.g._lastRenderTime = performance.now()
    }
    // A manual snapshot taken while paused enqueues a thumbnail the loop would
    // normally grab; capture it now so it isn't lost under 0-loop idle. The
    // canvas already shows the frozen paused frame, so capture from live pixels.
    if (this.g.snapshots.hasPendingThumbnails) {
      this.g.snapshots.capturePendingThumbnails(() => this.g.presentation.captureThumbnail())
    }
    // If input left the static set (start → playing, load → recovery,
    // unpause → playing) this re-arms vsync rAF; otherwise it stays idle.
    this.scheduleFrame()
  }

  /**
   * Compute the frame delta, cap it, and deposit it into the fixed-timestep
   * accumulator scaled by the 督战 battle speed. Ticks themselves are
   * untouched, so determinism (AGENTS §2.3) is preserved — only cadence.
   */
  computeDelta(time: number): number {
    const dt = Math.min(time - this.g.lastTime, 100) // cap at 100ms
    this.g.lastTime = time
    this.g.accumulator += dt * this.g.battleSpeed
    return dt
  }

  /**
   * Arm the Performance Observatory probes for this frame (gated: zero cost
   * when the overlay is off). Returns the reusable timing buffer.
   */
  beginPerfProbe(): void {
    const probe = this._probe
    probe.active = this.g.presentation.ui.perfOverlay.active
    if (probe.active) {
      probe.frameT0 = performance.now()
      // Re-arm the dev draw-call counter (early-returns if already armed).
      this.g.presentation.renderer.setDrawCallCounting(true)
    }
  }

  /**
   * Route frame input: replay transport keys during playback, menu/state
   * keys otherwise.
   */
  handleFrameInput(): void {
    if (this.g.playback) {
      this.g.handlePlaybackInput()
    } else {
      this.g.handleStateInput()
    }
  }

  /**
   * Fixed-timestep simulation — live ticks or replay playback.
   *
   * Live branch: steps the sim, records consumed input per tick, detects
   * stage changes (Stage Start snapshot + fresh recording session), finalizes
   * recordings on clear/defeat, intercepts game over for recovery, applies
   * the anti-spiral clamp, and consumes the manual-rewind (时光宝盒) signal.
   */
  stepSimulation(dt: number): void {
    const probe = this._probe
    if (probe.active) probe.simT0 = performance.now()
    if (this.g.playback) {
      // Playback mode: PlaybackController drives ticks directly
      this.g.playback.update(dt)
      // Replay ran out of frames → leave playback EXPLICITLY. Without this
      // the replay world (still 'playing'/'stageclear') would fall through
      // to the live branch next frame: the keyboard would take over the
      // replay's tank, and the stage-change detector would start recording
      // a bogus session from mid-replay state.
      if (this.g.playback.isEnded) {
        this.g.finishPlayback()
      }
    } else {
      // Live gameplay: record input per tick, inside the while-loop
      let steps = 0
      let enteredGameOver = false
      while (this.g.accumulator >= TICK_MS && steps < MAX_LIVE_STEPS) {
        if (
          this.g.world.state === 'playing' ||
          this.g.world.state === 'stageclear' ||
          this.g.world.state === 'gameover'
        ) {
          this.g.simulation.tick()
          // Record THIS tick's input (one frame per tick).
          //
          // MUST record `this.g.simulation.input` / `this.g.simulation.input2` —
          // the exact objects the tick above consumed — NOT the raw
          // `this.g.input` / `this.g.godInput` fields. In Lie-Back-Win-Mode the
          // human input is decorated by AutoFireInput, so the sim fires every
          // tick while the raw keyboard reports "not firing". Recording the
          // raw input dropped every auto-fired shot, desyncing playback from
          // tick 0 (the replay looked like the player suicided into its own
          // base). See AutoFireInput's contract: the decorated input is what
          // the replay records.
          this.g.recorder.recordFrame(this.g.simulation.input, this.g.simulation.input2)

          // Detect stage change → Stage Start snapshot (plan §3, §10)
          if (
            this.g.world.stageIndex !== this.g.prevStageIndex &&
            this.g.world.state === 'playing'
          ) {
            this.g.snapshots.create('stage-start', this.g.world)
            this.g.snapshots.resetAutoTimer()
            this.g.prevStageIndex = this.g.world.stageIndex
            // Start a new recording session for the new stage
            this.g.recorder.startNew(this.g.world)
            // Lie-Back-Win-Mode §3.4: re-arm auto-fire each stage.
            if (this.g.autoFireInput) this.g.autoFireInput.reset()
            // §190: reset God AI per-stage caches (centralBreachRisk, stage-
            // adapted params) for coop P2 and spectate P1/P2.
            this.g.godInput?.reset()
            this.g.godInput2?.reset()
          }

          // Detect stage clear → save victory replay
          if (this.g.world.state === 'stageclear' && this.g.prevWorldState !== 'stageclear') {
            this.g.finalizeRecording('clear')
          }

          // Detect game over → intercept for recovery
          if (this.g.world.state === 'gameover' && !enteredGameOver) {
            // Determine specific defeat cause for the four-state ReplayType
            const defeatType = this.g.world.tileMap.isBaseDestroyed() ? 'base' : 'died'
            this.g.finalizeRecording(defeatType)
            enteredGameOver = true
            this.g.startRecovery()
            break // stop ticking — simulation is now suspended
          }
        }
        this.g.accumulator -= TICK_MS
        steps++
      }
      // Anti-spiral clamp: a >1× speed (or a frame hitch at any speed) can
      // deposit more ms than the step cap drains; drop the excess instead of
      // fast-forwarding forever (mirrors PlaybackController.update).
      if (this.g.accumulator > TICK_MS) this.g.accumulator = TICK_MS

      // Manual "时光宝盒" rewind — consume the pending flag set by
      // Simulation.activateRewind (F7). The actual fade→restore→countdown
      // is owned by RecoveryController (same flow as Load Latest). Stock was
      // already spent in activateRewind; refund it if the rewind can't start.
      if (this.g.world.rewindPending) {
        this.g.world.rewindPending = false
        const canStart = this.g.recovery.phase === 'idle' && this.g.world.state === 'playing'
        if (canStart && this.g.recovery.beginManualRewind(this.g.world)) {
          this.g.audio.playRecoveryStart()
          this.g.presentation.ui.notify(t('toast.rewindActivated'), 'info')
        } else {
          this.g.world.rewindStock++
        }
      }
    }
    if (probe.active) probe.simMs = performance.now() - probe.simT0
  }

  /**
   * Recovery flow update (fade, countdown) while state is 'recovery', plus
   * the presentation/recorder rebuild at the fading→countdown boundary and
   * the countdown beeps. Outside recovery, keeps the phase trackers idle.
   */
  stepRecovery(dt: number): void {
    if (this.g.world.state === 'recovery') {
      // Drain accumulator so the simulation doesn't burst-forward
      // when gameplay resumes after the countdown.
      this.g.accumulator = 0

      this.g.handleRecoveryInput()
      this.g.recovery.update(this.g.world, dt)

      // When the fade completes the snapshot is restored internally.
      // At that transition we must rebuild all presentation state
      // (particles, camera, animations) — Presentation is disposable.
      if (this.g.recovery.phase === 'countdown' && this.g.prevRecoveryPhase === 'fading') {
        this.rebuildAfterRestore()
      }

      // Countdown beeps — play a tone each time the number changes
      if (this.g.world.ui.recoveryCountdown !== this.g.prevCountdown) {
        if (this.g.world.ui.recoveryCountdown > 0) {
          this.g.audio.playCountdownBeep()
        } else if (this.g.prevCountdown > 0) {
          // Countdown just finished → resume
          this.g.audio.playCountdownGo()
        }
      }
      this.g.prevCountdown = this.g.world.ui.recoveryCountdown
      this.g.prevRecoveryPhase = this.g.recovery.phase
    } else {
      this.g.prevRecoveryPhase = 'idle'
      this.g.prevCountdown = 0
    }
  }

  /**
   * Post-restore rebuild at the recovery fading→countdown boundary: reset
   * Presentation, restart recording from the deterministic restore boundary,
   * silence audio, and re-wire God AI / auto-fire inputs to the restored
   * run profile (coop P2, spectate P1, or plain single-player).
   */
  rebuildAfterRestore(): void {
    this.g.presentation.reset()
    this.g.audio.stopAll()
    // The world was just atomically restored (or freshly restarted) —
    // this is the exact deterministic boundary a replay must start from.
    // Recording is restarted HERE, never at beginLoad() time: the restore
    // is deferred until the fade completes, so an earlier startNew()
    // would capture the pre-restore world (a corrupted replay). This
    // also revives the recorder after a defeat finalized it (recovery →
    // load/restart must produce a fresh recording session).
    this.g.recorder.startNew(this.g.world)
    // The restored stage is not a "stage change" — keep the detector
    // quiet so it doesn't overwrite this session / snapshot a mid-stage
    // world as 'stage-start'.
    this.g.prevStageIndex = this.g.world.stageIndex
    // Lie-Back-Win-Mode: if the restored snapshot has coop enabled but
    // godInput was cleared (e.g. loaded a coop snapshot from browser while
    // coop was off), re-create the God AI input for player2.
    if (this.g.world.coop && !this.g.godInput && this.g.world.player2) {
      const rng = new RNG((this.g.world.seed ^ SEED_HASH) >>> 0)
      this.g.godInput = new GodAIInput(this.g.world, undefined, rng, (w) => w.player2)
      this.g.godInput.reset()
      // Lie-Back-Win-Mode §3.4: re-create auto-fire on recovery restore.
      this.g.autoFireInput = new AutoFireInput(this.g.input)
      this.g.wireLiveInputs()
      this.g.audio.player2Id = this.g.world.player2?.id ?? null
      this.g.presentation.ui.controlCenter.setCoopState(true)
    } else if (this.g.world.spectate && !this.g.godInput && this.g.world.player) {
      // 督战: restored snapshot has spectate but godInput was cleared
      // (e.g. loaded a spectate snapshot from the browser while spectate
      // was off) — re-create the God AI for player1 (default
      // controlledTank = `w.player`). No auto-fire: nobody is human here.
      this.g.rearmSpectateGodInput()
    } else if (!this.g.world.coop && !this.g.world.spectate) {
      // Snapshot restored without coop/spectate — ensure inputs are cleared.
      this.g.godInput = null
      this.g.godInput2 = null
      this.g.autoFireInput = null
      this.g.wireLiveInputs()
      this.g.audio.player2Id = null
      this.g.presentation.ui.controlCenter.setCoopState(false)
      this.g.presentation.ui.controlCenter.setSpectateState('off')
    }
  }

  /**
   * Persistence upkeep: auto snapshots every 30 s of live gameplay (guarded
   * by !playback — replays drive a synthetic world that must never trigger
   * persistence side-effects) plus pre-render replay thumbnail capture (the
   * canvas still shows the previous clean frame — no overlay, no flash).
   */
  stepSnapshots(dt: number): void {
    if (this.g.world.state === 'playing' && !this.g.playback) {
      this.g.snapshots.updateAuto(this.g.world, dt)
    }
    if (this.g.replays.hasPendingThumbnails) {
      this.g.replays.capturePendingThumbnails(() => this.g.presentation.captureThumbnail())
    }
  }

  /**
   * Consume the World event queue and route events to audio + presentation.
   */
  dispatchWorldEvents(): void {
    const events = this.g.world.consumeEvents()
    this.g.audio.handleEvents(events)
    this.g.presentation.handleEvents(events)
  }

  /**
   * On-demand render: repaint only when the visible scene changed
   * (PresentationLayer.shouldRender) and the renderFpsCap throttle allows it
   * (0 = uncapped). Keeps the GPU idle during menu/pause/game-over/idle lulls;
   * input, simulation, and the HUD still run every frame.
   *
   * @returns whether a repaint actually happened (drives thumbnail capture).
   */
  stepRender(time: number, dt: number): boolean {
    const probe = this._probe
    const wantRender = this.g.presentation.shouldRender(this.g.world)
    const canRender =
      this.g.renderFpsCap <= 0 || time - this.g._lastRenderTime >= 1000 / this.g.renderFpsCap
    let rendered = false
    if (probe.active) {
      probe.renderT0 = performance.now()
      // Reset the dev draw-call counter; it re-accumulates only if we actually
      // repaint this frame (on-demand idle frames stay at 0 — accurate).
      this.g.presentation.renderer.debugDrawCalls = 0
    }
    if (wantRender && canRender) {
      this.g.presentation.render(this.g.world, dt)
      this.g._lastRenderTime = time
      rendered = true
    }
    if (probe.active) probe.renderMs = performance.now() - probe.renderT0
    return rendered
  }

  /**
   * Snapshot thumbnail capture — only right after a repaint, so the preview
   * always shows the snapshot's own frame, never a stale one. If nothing
   * repainted this frame, force a repaint next frame instead.
   */
  captureSnapshotThumbnails(rendered: boolean): void {
    if (!this.g.snapshots.hasPendingThumbnails) return
    if (rendered) {
      this.g.snapshots.capturePendingThumbnails(() => this.g.presentation.captureThumbnail())
    } else {
      this.g.presentation.markNeedsRender()
    }
  }

  /**
   * HTML HUD sync every frame (cheap, internally guarded) so menu/pause
   * overlays stay live even when the canvas repaint is skipped, plus replay
   * progress bar/time during playback.
   */
  syncUI(): void {
    const probe = this._probe
    if (probe.active) probe.uiT0 = performance.now()
    this.g.presentation.updateUI(this.g.world)
    if (this.g.playback) {
      this.g.presentation.ui.setReplayProgress(this.g.playback.progress)
      const replay = this.g.playback.replay
      if (replay) {
        this.g.presentation.ui.setReplayTime(
          Math.round(this.g.playback.progress * replay.durationMs),
          replay.durationMs,
        )
      }
    }
    if (probe.active) probe.uiMs = performance.now() - probe.uiT0
  }

  /**
   * Clear per-frame input edges (keyboard + both God AI caches).
   */
  endFrameInputs(): void {
    this.g.input.endFrame()
    // Lie-Back-Win-Mode: invalidate God AI per-tick caches.
    this.g.godInput?.endFrame()
    // 督战双玩家: invalidate second God AI per-tick caches.
    this.g.godInput2?.endFrame()
  }

  /**
   * FPS sampler (regression guard, allocation-free) and the Performance
   * Observatory per-frame sample publish (overlay only).
   */
  samplePerformance(time: number): void {
    this.g._frameCount++
    if (time - this.g._fpsLastTime >= 1000) {
      this.g.fps = this.g._frameCount
      this.g._frameCount = 0
      this.g._fpsLastTime = time
      // Only warn during active play — static screens run a deliberate
      // low-power cadence (10 FPS) by design, so a low count there is expected.
      if (this.g.fps < 45 && !LOW_POWER_STATES.has(this.g.world.state)) {
        this.g._slowSeconds++
        if (this.g._slowSeconds === 3) {
          console.warn(`[perf] sustained low frame rate: ${this.g.fps} fps`)
        }
      } else {
        this.g._slowSeconds = 0
      }
    }
    const probe = this._probe
    if (probe.active) {
      const frameDt = performance.now() - probe.frameT0
      this.g.presentation.ui.perfOverlay.update(
        this.g.world,
        this.g.presentation.renderer,
        this.g.presentation.particles,
        {
          fps: this.g.fps,
          frameMs: frameDt,
          simMs: probe.simMs,
          renderMs: probe.renderMs,
          uiMs: probe.uiMs,
          perfMode: this.g.settings.performanceMode,
        },
      )
    }
  }

  /**
   * Reclaim keyboard focus whenever we (re)enter active play. After a stage
   * transition, an unpause, a recovery resume, or a fresh start the browser
   * may have moved focus elsewhere (stage-clear overlay, the Alt menu, the
   * address bar), which silently breaks Alt+S/R/T until the player clicks
   * the canvas. Focusing the tabbable canvas restores the document focus so
   * the window-level keydown keeps firing — no manual click required.
   */
  updateStateTracking(): void {
    if (this.g.world.state === 'playing' && this.g.prevWorldState !== 'playing') {
      this.refocusGame()
    }
    this.g.prevWorldState = this.g.world.state
  }

  /**
   * Reusable per-frame timing buffers for the Performance Observatory.
   * Diagnostics-only state (never gameplay) — allocated once per GameLoop
   * instance and reused every frame so the hot path stays allocation-free
   * (AGENTS §14.1).
   */
  _probe = {
    active: false,
    frameT0: 0,
    simT0: 0,
    simMs: 0,
    renderT0: 0,
    renderMs: 0,
    uiT0: 0,
    uiMs: 0,
  }

  /**
   * The vsync rAF driver. Each named step owns one concern; execution ORDER
   * is load-bearing for determinism (AGENTS §2.3) — do not reorder.
   */
  loop = (time: number): void => {
    if (!this.g.running) return

    const dt = this.computeDelta(time)
    this.beginPerfProbe()

    this.handleFrameInput()
    this.stepSimulation(dt)
    this.stepRecovery(dt)
    this.stepSnapshots(dt)
    this.dispatchWorldEvents()
    const rendered = this.stepRender(time, dt)
    this.captureSnapshotThumbnails(rendered)
    this.syncUI()
    this.endFrameInputs()
    this.samplePerformance(time)
    this.updateStateTracking()

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
  refocusGame(): void {
    try {
      this.g.presentation.ui.canvas.focus({ preventScroll: true })
    } catch {
      /* focus() is a no-op / throws in unsupported or headless contexts */
    }
  }
}
