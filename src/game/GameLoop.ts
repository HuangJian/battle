import { LOW_POWER_STATES, MAX_LIVE_STEPS, TICK_MS, SEED_HASH } from '../constants'
import { spriteLibrary } from '../presentation/renderer/SpriteLibrary'
import { RNG } from '../utils/RNG'
import { GodAIInput } from '../ai/GodAIInput'
import { AutoFireInput } from './AutoFireInput'
import { cycleBattleSpeed } from './battleSpeed'
import { t } from '../i18n'
import type { GameConstructor, GameCore } from './GameCore'

/**
 * GameLoopMixin — the vsync rAF loop driver plus the event-driven static-screen
 * handlers (visibility, static key, battle-speed hotkey, perf hotkey).
 *
 * Composes onto {@link GameCore} (which owns the fields and the constructor).
 * See `Game.ts` for the final mixin order. Cross-mixin calls (menu actions,
 * snapshot/replay flows, playback input) resolve to the stubs declared on
 * `GameCore`; the composed `Game` always installs every mixin.
 */
export function GameLoopMixin<TBase extends GameConstructor<GameCore>>(Base: TBase) {
  return class GameLoop extends Base {
    async start(): Promise<void> {
      this.input.attach(window)
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
      window.removeEventListener('keydown', this.onSpeedKey)
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
          // The AudioContext is often auto-suspended while the tab is hidden;
          // resume it when we come back so a running replay (or live game)
          // doesn't fall silent. resume() is a no-op if already running.
          this.audio.resume()
          this.lastTime = performance.now()
          const lowPower = LOW_POWER_STATES.has(this.world.state)
          // A paused replay (or an ended replay kept alive by the playback
          // sentinel) sits in a LOW_POWER state yet MUST keep the rAF loop
          // alive: play / resume / seek and the progress bar are driven by the
          // loop, so going truly idle here freezes the replay controls after the
          // tab returns from the background. Only sleep when there is no
          // playback at all.
          const idle = lowPower && !this.playback
          if (lowPower) {
            // No loop runs while idle — repaint once so the canvas isn't blank
            // after the tab was hidden (browsers may discard the backing store).
            this.presentation.markNeedsRender()
            this.presentation.updateUI(this.world)
            if (this.presentation.shouldRender(this.world)) {
              this.presentation.render(this.world, 0)
            }
          }
          if (!idle) {
            this.presentation.markNeedsRender()
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
    protected scheduleFrame(): void {
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
     * 督战 battle-speed hotkeys: Alt+> faster, Alt+< slower (US-layout `>` is
     * Shift+Period, `<` is Shift+Comma). Event-driven so it fires during live
     * play, pause, AND replay playback regardless of which driver owns the loop.
     * Live speed is a Game field (never World state — cadence only, AGENTS §2.3);
     * during playback it routes to the replay's own speed control.
     */
    private onSpeedKey = (e: KeyboardEvent): void => {
      if (!e.altKey) return
      let dir: 1 | -1 | null = null
      if (e.code === 'Period') dir = 1
      else if (e.code === 'Comma') dir = -1
      if (dir === null) return
      // Speed is a live-play viewing aid — ignore it on menu / game-over /
      // victory / recovery screens so a stray press can't leak a non-×1 speed
      // into the next fresh run (menuStart never resets it — only resetToMenu
      // does). Paused stays allowed so the player can set speed before resuming.
      const s = this.world.state
      if (!this.playback && s !== 'playing' && s !== 'paused' && s !== 'stageclear') return
      e.preventDefault()
      if (this.playback) {
        const cur = this.playback.currentSpeed
        this.setPlaybackSpeed(cycleBattleSpeed(cur, dir))
      } else {
        this.adjustBattleSpeed(dir)
      }
    }

    /**
     * Toggle the developer Performance Observatory (Alt+D). The overlay is a
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
    protected refreshStaticScreen(): void {
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

    /**
     * Compute the frame delta, cap it, and deposit it into the fixed-timestep
     * accumulator scaled by the 督战 battle speed. Ticks themselves are
     * untouched, so determinism (AGENTS §2.3) is preserved — only cadence.
     */
    private computeDelta(time: number): number {
      const dt = Math.min(time - this.lastTime, 100) // cap at 100ms
      this.lastTime = time
      this.accumulator += dt * this.battleSpeed
      return dt
    }

    /**
     * Arm the Performance Observatory probes for this frame (gated: zero cost
     * when the overlay is off). Returns the reusable timing buffer.
     */
    private beginPerfProbe(): void {
      const probe = this._probe
      probe.active = this.presentation.ui.perfOverlay.active
      if (probe.active) {
        probe.frameT0 = performance.now()
        // Re-arm the dev draw-call counter (early-returns if already armed).
        this.presentation.renderer.setDrawCallCounting(true)
      }
    }

    /**
     * Route frame input: replay transport keys during playback, menu/state
     * keys otherwise.
     */
    private handleFrameInput(): void {
      if (this.playback) {
        this.handlePlaybackInput()
      } else {
        this.handleStateInput()
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
    private stepSimulation(dt: number): void {
      const probe = this._probe
      if (probe.active) probe.simT0 = performance.now()
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
        let steps = 0
        let enteredGameOver = false
        while (this.accumulator >= TICK_MS && steps < MAX_LIVE_STEPS) {
          if (
            this.world.state === 'playing' ||
            this.world.state === 'stageclear' ||
            this.world.state === 'gameover'
          ) {
            this.simulation.tick()
            // Record THIS tick's input (one frame per tick).
            //
            // MUST record `this.simulation.input` / `this.simulation.input2` —
            // the exact objects the tick above consumed — NOT the raw
            // `this.input` / `this.godInput` fields. In Lie-Back-Win-Mode the
            // human input is decorated by AutoFireInput, so the sim fires every
            // tick while the raw keyboard reports "not firing". Recording the
            // raw input dropped every auto-fired shot, desyncing playback from
            // tick 0 (the replay looked like the player suicided into its own
            // base). See AutoFireInput's contract: the decorated input is what
            // the replay records.
            this.recorder.recordFrame(this.simulation.input, this.simulation.input2)

            // Detect stage change → Stage Start snapshot (plan §3, §10)
            if (this.world.stageIndex !== this.prevStageIndex && this.world.state === 'playing') {
              this.snapshots.create('stage-start', this.world)
              this.snapshots.resetAutoTimer()
              this.prevStageIndex = this.world.stageIndex
              // Start a new recording session for the new stage
              this.recorder.startNew(this.world)
              // Lie-Back-Win-Mode §3.4: re-arm auto-fire each stage.
              if (this.autoFireInput) this.autoFireInput.reset()
              // §190: reset God AI per-stage caches (centralBreachRisk, stage-
              // adapted params) for coop P2 and spectate P1/P2.
              this.godInput?.reset()
              this.godInput2?.reset()
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
        // Anti-spiral clamp: a >1× speed (or a frame hitch at any speed) can
        // deposit more ms than the step cap drains; drop the excess instead of
        // fast-forwarding forever (mirrors PlaybackController.update).
        if (this.accumulator > TICK_MS) this.accumulator = TICK_MS

        // Manual "时光宝盒" rewind — consume the pending flag set by
        // Simulation.activateRewind (F7). The actual fade→restore→countdown
        // is owned by RecoveryController (same flow as Load Latest). Stock was
        // already spent in activateRewind; refund it if the rewind can't start.
        if (this.world.rewindPending) {
          this.world.rewindPending = false
          const canStart = this.recovery.phase === 'idle' && this.world.state === 'playing'
          if (canStart && this.recovery.beginManualRewind(this.world)) {
            this.audio.playRecoveryStart()
            this.presentation.ui.notify(t('toast.rewindActivated'), 'info')
          } else {
            this.world.rewindStock++
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
    private stepRecovery(dt: number): void {
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
          this.rebuildAfterRestore()
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
    }

    /**
     * Post-restore rebuild at the recovery fading→countdown boundary: reset
     * Presentation, restart recording from the deterministic restore boundary,
     * silence audio, and re-wire God AI / auto-fire inputs to the restored
     * run profile (coop P2, spectate P1, or plain single-player).
     */
    private rebuildAfterRestore(): void {
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
      // Lie-Back-Win-Mode: if the restored snapshot has coop enabled but
      // godInput was cleared (e.g. loaded a coop snapshot from browser while
      // coop was off), re-create the God AI input for player2.
      if (this.world.coop && !this.godInput && this.world.player2) {
        const rng = new RNG((this.world.seed ^ SEED_HASH) >>> 0)
        this.godInput = new GodAIInput(this.world, undefined, rng, (w) => w.player2)
        this.godInput.reset()
        // Lie-Back-Win-Mode §3.4: re-create auto-fire on recovery restore.
        this.autoFireInput = new AutoFireInput(this.input)
        this.wireLiveInputs()
        this.audio.player2Id = this.world.player2?.id ?? null
        this.presentation.ui.controlCenter.setCoopState(true)
      } else if (this.world.spectate && !this.godInput && this.world.player) {
        // 督战: restored snapshot has spectate but godInput was cleared
        // (e.g. loaded a spectate snapshot from the browser while spectate
        // was off) — re-create the God AI for player1 (default
        // controlledTank = `w.player`). No auto-fire: nobody is human here.
        this.rearmSpectateGodInput()
      } else if (!this.world.coop && !this.world.spectate) {
        // Snapshot restored without coop/spectate — ensure inputs are cleared.
        this.godInput = null
        this.godInput2 = null
        this.autoFireInput = null
        this.wireLiveInputs()
        this.audio.player2Id = null
        this.presentation.ui.controlCenter.setCoopState(false)
        this.presentation.ui.controlCenter.setSpectateState('off')
      }
    }

    /**
     * Persistence upkeep: auto snapshots every 30 s of live gameplay (guarded
     * by !playback — replays drive a synthetic world that must never trigger
     * persistence side-effects) plus pre-render replay thumbnail capture (the
     * canvas still shows the previous clean frame — no overlay, no flash).
     */
    private stepSnapshots(dt: number): void {
      if (this.world.state === 'playing' && !this.playback) {
        this.snapshots.updateAuto(this.world, dt)
      }
      if (this.replays.hasPendingThumbnails) {
        this.replays.capturePendingThumbnails(() => this.presentation.captureThumbnail())
      }
    }

    /**
     * Consume the World event queue and route events to audio + presentation.
     */
    private dispatchWorldEvents(): void {
      const events = this.world.consumeEvents()
      this.audio.handleEvents(events)
      this.presentation.handleEvents(events)
    }

    /**
     * On-demand render: repaint only when the visible scene changed
     * (PresentationLayer.shouldRender) and the renderFpsCap throttle allows it
     * (0 = uncapped). Keeps the GPU idle during menu/pause/game-over/idle lulls;
     * input, simulation, and the HUD still run every frame.
     *
     * @returns whether a repaint actually happened (drives thumbnail capture).
     */
    private stepRender(time: number, dt: number): boolean {
      const probe = this._probe
      const wantRender = this.presentation.shouldRender(this.world)
      const canRender =
        this.renderFpsCap <= 0 || time - this._lastRenderTime >= 1000 / this.renderFpsCap
      let rendered = false
      if (probe.active) {
        probe.renderT0 = performance.now()
        // Reset the dev draw-call counter; it re-accumulates only if we actually
        // repaint this frame (on-demand idle frames stay at 0 — accurate).
        this.presentation.renderer.debugDrawCalls = 0
      }
      if (wantRender && canRender) {
        this.presentation.render(this.world, dt)
        this._lastRenderTime = time
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
    private captureSnapshotThumbnails(rendered: boolean): void {
      if (!this.snapshots.hasPendingThumbnails) return
      if (rendered) {
        this.snapshots.capturePendingThumbnails(() => this.presentation.captureThumbnail())
      } else {
        this.presentation.markNeedsRender()
      }
    }

    /**
     * HTML HUD sync every frame (cheap, internally guarded) so menu/pause
     * overlays stay live even when the canvas repaint is skipped, plus replay
     * progress bar/time during playback.
     */
    private syncUI(): void {
      const probe = this._probe
      if (probe.active) probe.uiT0 = performance.now()
      this.presentation.updateUI(this.world)
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
      if (probe.active) probe.uiMs = performance.now() - probe.uiT0
    }

    /**
     * Clear per-frame input edges (keyboard + both God AI caches).
     */
    private endFrameInputs(): void {
      this.input.endFrame()
      // Lie-Back-Win-Mode: invalidate God AI per-tick caches.
      this.godInput?.endFrame()
      // 督战双玩家: invalidate second God AI per-tick caches.
      this.godInput2?.endFrame()
    }

    /**
     * FPS sampler (regression guard, allocation-free) and the Performance
     * Observatory per-frame sample publish (overlay only).
     */
    private samplePerformance(time: number): void {
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
      const probe = this._probe
      if (probe.active) {
        const frameDt = performance.now() - probe.frameT0
        this.presentation.ui.perfOverlay.update(
          this.world,
          this.presentation.renderer,
          this.presentation.particles,
          {
            fps: this.fps,
            frameMs: frameDt,
            simMs: probe.simMs,
            renderMs: probe.renderMs,
            uiMs: probe.uiMs,
            perfMode: this.settings.performanceMode,
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
    private updateStateTracking(): void {
      if (this.world.state === 'playing' && this.prevWorldState !== 'playing') {
        this.refocusGame()
      }
      this.prevWorldState = this.world.state
    }

    /**
     * Reusable per-frame timing buffers for the Performance Observatory.
     * Diagnostics-only state (never gameplay) — allocated once per GameLoop
     * instance and reused every frame so the hot path stays allocation-free
     * (AGENTS §14.1).
     */
    private _probe = {
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
    private loop = (time: number): void => {
      if (!this.running) return

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
    private refocusGame(): void {
      try {
        this.presentation.ui.canvas.focus({ preventScroll: true })
      } catch {
        /* focus() is a no-op / throws in unsupported or headless contexts */
      }
    }
  }
}
