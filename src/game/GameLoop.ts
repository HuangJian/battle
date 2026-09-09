// ================================================================
// LoopController — extracted from the former GameLoop.ts mixin
// (plan/refactor.agy.md §1.1 mixin→composition). Bodies moved verbatim:
// `this.<slice-own>` stayed, everything else goes through the Game
// orchestrator back-reference (`this.g`). Cross-slice entry points are
// delegated on Game itself.
// ================================================================
import { LOW_POWER_STATES, MAX_LIVE_STEPS, TICK_MS } from '../constants'
import { spriteLibrary } from '../presentation/renderer/SpriteLibrary'
import { cycleBattleSpeed } from './battleSpeed'
import { t } from '../i18n'
import type { Game } from './Game'

/**
 * Static-screen pad-poll cadence (2p-review R2-P1): a pure-pad user on a
 * static screen (menu / paused / gameover / victory) has no keyboard keydowns
 * to wake the event-driven static path, so the GamepadManager is never polled
 * and pad edges freeze. When a pad is present we run a minimal interval that
 * polls + processes input like the keydown path would. ~10 Hz is plenty for
 * menu-confirm / pause edges and keeps the idle cost trivial.
 */
export const STATIC_PAD_POLL_MS = 100

/**
 * R2-P1 pure decision: should the static-screen pad-poll interval run?
 * True iff the game is live (not stopped / hidden / mid-playback) AND a pad
 * is present AND the world is on a loop-idle (static) screen — exactly the
 * conditions under which the event-driven static input path never runs.
 * Keyboard-only users keep the 0-loop idle (no interval, fan off); pad users
 * pay the tiny poll cost only while a static screen is actually up.
 */
export function wantsStaticPadPoll(o: {
  running: boolean
  hidden: boolean
  playback: unknown
  padPresent: boolean
  worldState: string
}): boolean {
  return o.running && !o.hidden && !o.playback && o.padPresent && LOW_POWER_STATES.has(o.worldState)
}

/**
 * R2-P1 pure presence scan over a getGamepads() result: any connected pad?
 * Undefined (API unavailable — headless / older browsers) reads as no pads.
 */
export function anyGamepadConnected(pads: readonly (Gamepad | null)[] | undefined): boolean {
  return (pads ?? []).some((p) => !!p && p.connected)
}

export class LoopController {
  constructor(private g: Game) {}
  /**
   * Whether ANY gamepad is currently connected (2p-review R2-P1) — kept in
   * sync from gamepadconnected/disconnected events + an initial scan. Gates
   * the static-screen pad-poll interval so keyboard-only users pay nothing.
   */
  private padPresent = false
  /** The static-screen pad-poll interval id (0 = not running). */
  private staticPollId = 0
  async start(): Promise<void> {
    this.g.input.attach(window)
    // 双打 Two-Player: P2's keyboard listens on the same window — independent
    // bindings (WASD + F), its own pressed/justPressed sets.
    this.g.input2.attach(window)
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
    // Gamepad presence (2p-review R2-P1): the browser fires connect/disconnect
    // events, so we know whether a pad exists WITHOUT polling; the static-screen
    // pad-poll interval is gated on this presence flag (keyboard-only users
    // keep the 0-loop idle).
    window.addEventListener('gamepadconnected', this.onGamepadPresenceEvent)
    window.addEventListener('gamepaddisconnected', this.onGamepadPresenceEvent)
    // Pads already connected before page load never fire an event — scan once.
    this.padPresent = this.scanPadPresence()
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
    // Start the presence-gated pad-poll driver if a pad is already connected
    // on a static screen (the first loop frame's scheduleFrame would too, but
    // a static screen never arms rAF, so start it here explicitly).
    this.syncStaticPadPoll()
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
    window.removeEventListener('gamepadconnected', this.onGamepadPresenceEvent)
    window.removeEventListener('gamepaddisconnected', this.onGamepadPresenceEvent)
    // Stop the static-screen pad-poll driver — the game is shutting down.
    this.syncStaticPadPoll()
    this.g.input.detach(window)
    this.g.input2.detach(window)
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
        // A hidden tab must not pay for the static pad-poll interval either.
        this.syncStaticPadPoll()
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
        // Tab visible again + pad + static screen → re-arm the pad-poll driver.
        this.syncStaticPadPoll()
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
    } else {
      this.g.rafId = requestAnimationFrame(this.loop)
    }
    // Presence-gated pad polling for static screens (2p-review R2-P1): start
    // the minimal interval when a static screen + connected pad coexist, stop
    // it on every other transition (action state / pad unplugged / playback).
    this.syncStaticPadPoll()
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
    if (!this.canProcessStaticInput()) return
    this.processStaticInput()
  }

  /**
   * Shared guard list for the static (idle) screens — used by BOTH the
   * keydown handler (keyboard users) and the static pad-poll interval (pure
   * gamepad users, 2p-review R2-P1) so the two drivers can never disagree
   * about when static input may be processed.
   */
  private canProcessStaticInput(): boolean {
    if (!this.g.running || this.g._hidden) return false
    // During playback the vsync rAF loop owns ALL input (handlePlaybackInput)
    // — never double-process here, even if the replay drove the world into a
    // LOW_POWER state (e.g. 'gameover' at the end of a defeat replay).
    if (this.g.playback) return false
    if (!LOW_POWER_STATES.has(this.g.world.state)) return false
    // UI modals own their own keyboard handling; never double-process.
    if (this.g.presentation.ui.snapshotBrowser.isOpen()) return false
    if (this.g.presentation.ui.replayBrowser.isOpen()) return false
    if (this.g.presentation.ui.isControlsOpen()) return false
    return true
  }

  /**
   * One static-screen input frame: poll pads, process the state input via
   * the same code path the rAF loop uses, clear the per-frame input edges so
   * a single press is consumed exactly once, then repaint on demand + (re)arm
   * the right loop driver. Shared by `onStaticKey` and the static pad-poll
   * interval tick — static screens run no rAF loop, so pads are polled here
   * too: a Start press on the menu / pause / game-over screen is consumed
   * exactly once regardless of which driver delivered the frame.
   */
  private processStaticInput(): void {
    this.pollPads()
    this.g.handleStateInput()
    this.g.simulation.input.endFrame()
    this.g.simulation.input2?.endFrame()
    this.g.input.endFrame()
    this.g.input2.endFrame()
    // Repaint on demand + (re)arm the loop driver if the state changed.
    this.refreshStaticScreen()
  }

  /**
   * Presence-gated static-screen pad polling (2p-review R2-P1): when a pad is
   * connected AND the world is on a static (loop-idle) screen, a pure-pad
   * user has NO keyboard keydowns to wake the event-driven static path — pad
   * edges would freeze forever (menu Start can't confirm, pause Start can't
   * resume, gameover Start can't return). Drive the same static-input frame
   * from a minimal interval while that holds, and only then: keyboard-only
   * users keep the 0-loop idle, each user pays for their own input path.
   */
  private syncStaticPadPoll(): void {
    const want = wantsStaticPadPoll({
      running: this.g.running,
      hidden: this.g._hidden,
      playback: this.g.playback,
      padPresent: this.padPresent,
      worldState: this.g.world.state,
    })
    if (want && this.staticPollId === 0) {
      this.staticPollId = window.setInterval(this.onStaticPadTick, STATIC_PAD_POLL_MS)
    } else if (!want && this.staticPollId !== 0) {
      window.clearInterval(this.staticPollId)
      this.staticPollId = 0
    }
  }

  /** The interval driver — one static-input frame per tick, same guards as
   *  the keydown path (a UI modal owns input while open; the loop owns
   *  action states). No-op when the game stopped / went hidden meanwhile. */
  private onStaticPadTick = (): void => {
    if (!this.canProcessStaticInput()) return
    this.processStaticInput()
  }

  /** Re-scan navigator on every connect/disconnect event (the event's own
   *  `gamepad` only tells us about ONE device; others may remain). */
  private onGamepadPresenceEvent = (): void => {
    this.padPresent = this.scanPadPresence()
    this.syncStaticPadPoll()
  }

  /** Any connected pad right now? False when the API is unavailable. */
  private scanPadPresence(): boolean {
    const nav = navigator as Navigator & { getGamepads?: () => (Gamepad | null)[] }
    try {
      return anyGamepadConnected(nav.getGamepads?.())
    } catch {
      return false
    }
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
        // §4.1: flag consumption + stock refund route through Simulation.
        this.g.simulation.clearRewindPending()
        const canStart = this.g.recovery.phase === 'idle' && this.g.world.state === 'playing'
        if (canStart && this.g.recovery.beginManualRewind(this.g.world)) {
          this.g.audio.playRecoveryStart()
          this.g.presentation.ui.notify(t('toast.rewindActivated'), 'info')
        } else {
          this.g.simulation.refundRewind()
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
    // 模式归一化 (2p-review P1-2): collapse the mode inputs to EXACTLY the
    // restored world's flags (coop → P2 God AI + P1 auto-fire; spectate → P1
    // God AI; twoPlayer → P2's human keyboard; plain → none), clearing
    // residues of the OTHER modes — a leftover coop autoFireInput/godInput
    // would make P1 auto-fire in a restored two-player game. All three
    // Control-Center mode lights are set from the flags so no stale light
    // survives a cross-mode restore.
    this.g.normalizeModeInputs()
    this.g.syncModeLights()
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
   * Poll the gamepads once and route connect/disconnect transitions to
   * toasts (§354). Called at the top of each rAF frame, before input is
   * consumed — poll order is load-bearing for edge detection.
   */
  pollPads(): void {
    this.g.pads.poll()
    for (const ev of this.g.pads.consumeEvents()) {
      if (ev.type === 'connected') {
        // 2p-review P2-4: pad[1] only drives player2 in Two-Player mode — in a
        // single-player (or coop/spectate) session the second pad sits unused,
        // so say so instead of promising it joined the battle.
        if (ev.player === 2 && !this.g.world.twoPlayer) {
          this.g.presentation.ui.notify(t('toast.gamepadConnectedP2Idle'), 'info')
        } else {
          this.g.presentation.ui.notify(t('toast.gamepadConnected', { player: ev.player }), 'info')
        }
      } else {
        this.g.presentation.ui.notify(t('toast.gamepadDisconnected', { player: ev.player }), 'info')
      }
    }
  }

  /**
   * Clear per-frame input edges (keyboard + both God AI caches).
   */
  endFrameInputs(): void {
    // The sim consumes the COMPOSITES (keyboard OR gamepad — §354); clearing
    // them covers the inner keyboard refs too (CompositeInput.endFrame
    // delegates to both sources). Raw refs are cleared as well so menu-time
    // reads (handleStateInput) never see stale edges.
    this.g.simulation.input.endFrame()
    this.g.simulation.input2?.endFrame()
    this.g.input.endFrame()
    // 双打 Two-Player: clear P2's per-frame press edges too.
    this.g.input2.endFrame()
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

    // Gamepad polling is a per-RENDER-frame step (§354): the Gamepad API is
    // polled, not event-driven, so edges are diffed here once — N sim ticks
    // in this frame all see the same edges (fixed-timestep catch-up never
    // loses a super-item press). Must run BEFORE handleFrameInput.
    this.pollPads()

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
