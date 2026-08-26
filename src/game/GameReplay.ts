// ================================================================
// ReplayController — extracted from the former GameReplay.ts mixin
// (plan/refactor.agy.md §1.1 mixin→composition). Bodies moved verbatim:
// `this.<slice-own>` stayed, everything else goes through the Game
// orchestrator back-reference (`this.g`). Cross-slice entry points are
// delegated on Game itself.
// ================================================================
import { PlaybackController } from '../replay/PlaybackController'
import type { PlaybackSpeed } from '../replay/PlaybackController'
import { ReplayInput } from '../replay/ReplayInput'
import type { Replay, ReplayType } from '../replay/types'
import { GAME_VERSION } from '../snapshot/config'
import { serializeReplayFile, buildReplayFilename } from '../replay/file'
import { REPLAY_HASH_INTERVAL } from '../replay/config'
import { SEED_HASH } from '../constants'
import { cloneWorld, restoreWorld } from '../snapshot/WorldSerializer'
import { STAGES, localizedStageName } from '../config/stages'
import { isReplayBrowserBlocked } from './uiFlowGates'
import { t } from '../i18n'
import { GodAIInput } from '../ai/GodAIInput'
import { AutoFireInput } from './AutoFireInput'
import { RNG } from '../utils/RNG'
import type { Game } from './Game'

export class ReplayController {
  constructor(private g: Game) {}
  // ---- Replay System (plan/replay.md) ----

  /**
   * Finalize the current recording and save as a replay.
   * Called on stage clear (victory) or game over (defeat).
   */
  finalizeRecording(type: ReplayType): void {
    const result = this.g.recorder.finalize()
    if (!result) return // empty recording

    const w = this.g.world
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
      coop: w.coop,
      spectate: w.spectate,
      spectateDual: w.spectateDual,
    }
    const replay = this.g.replays.create(
      type,
      result.snapshot,
      result.frames,
      result.tickCount,
      metadata,
      w.seed,
      result.frames2,
      result.tickHashes,
      REPLAY_HASH_INTERVAL,
    )
    this.g.replays.enqueueThumbnail(replay.id)
  }

  /**
   * Start replay playback. Operates on Game's own world and simulation.
   * Returns false (with a toast) when the replay's frame format is not
   * playable by this build (plan/replay.md §17.2).
   */
  startPlayback(replay: Replay): boolean {
    if (!this.g.replays.canPlay(replay)) {
      this.g.presentation.ui.notify(t('toast.replayUnsupported'), 'warn')
      return false
    }
    if (replay.gameVersion !== GAME_VERSION) {
      // Different simulation build — playable, but determinism is not
      // guaranteed. Warn instead of silently desyncing.
      this.g.presentation.ui.notify(
        t('toast.replayVersionMismatch', { version: replay.gameVersion }),
        'warn',
      )
    }
    this.preparePlayback(replay)
    this.bindReplayUi(replay)
    // Pre-compute thumbnail keyframes for instant hover preview
    this.buildThumbnailKeyframes()
    return true
  }

  /**
   * Atomically replace the live game with the replay's recorded world and
   * re-arm the frame loop. Presentation is rebuilt from scratch (AGENTS §2.5).
   */
  private preparePlayback(replay: Replay): void {
    // Exit any existing playback first
    this.stopPlayback()
    // Discard any in-progress recording
    this.g.recorder.reset()
    this.g.recovery.reset()
    this.g.presentation.ui.snapshotBrowser.close()
    this.g.presentation.ui.replayBrowser.close()
    this.g.playback = new PlaybackController(replay)
    this.g.playback.start(this.g.world, this.g.simulation)
    // Lie-Back-Win-Mode: attenuate God (player2) shots in coop replays.
    // playback.start() restored the world from the replay snapshot, so
    // world.player2.id is now the replay's God tank id.
    this.g.audio.player2Id = this.g.world.player2?.id ?? null
    // Replay playback is a sound-producing action, but unlike live gameplay
    // its entry points (clicking Play / Replay-Again in the browser) never
    // go through the menu handlers that call audio.init()/resume(). If the
    // AudioContext was auto-suspended (idle, backgrounded tab, or the user's
    // first interaction was just opening the replay browser) we'd get no sound
    // — intermittently. Resume it here, inside the user gesture that started
    // playback, so audio is always live. (Matches every other interactive
    // entry point in the file.)
    this.g.audio.init()
    this.g.audio.resume()
    // Presentation is disposable (AGENTS §2.5): the world was atomically
    // replaced — rebuild all visual state (particles, camera, animations).
    this.g.presentation.reset()
    this.g.presentation.markNeedsRender()
    this.g.prevWorldState = this.g.world.state
    this.g.prevStageIndex = this.g.world.stageIndex
    this.g.accumulator = 0
    this.g.lastTime = performance.now()
    this.g.scheduleFrame()
  }

  /** Wire HUD badge, canvas input, and the video-player controller callbacks. */
  private bindReplayUi(replay: Replay): void {
    // Show persistent REPLAY badge in HUD + video player controller
    this.g.presentation.ui.setReplayMode(true, false, replay.metadata.difficulty)
    this.g.presentation.ui.setReplaySpeed(this.g.playback!.currentSpeed)
    this.g.presentation.ui.notify(t('toast.replayEscExit'))
    // Wire canvas click/mousemove for playback interaction
    this.g.presentation.ui.canvas.addEventListener('click', this.onReplayCanvasClick)
    this.g.presentation.ui.canvas.addEventListener('mousemove', this.onReplayCanvasMouseMove)
    // Wire the video player controller callbacks
    this.g.presentation.ui.replayController.init({
      onPlayPause: () => {
        if (!this.g.playback) return
        this.g.playback.togglePause()
        this.g.presentation.ui.setReplayMode(
          true,
          this.g.playback.isPaused,
          this.g.playback.replay?.metadata.difficulty,
        )
      },
      onSeek: (progress: number) => {
        if (!this.g.playback) return
        this.g.playback.seekTo(this.g.world, this.g.simulation, progress)
        this.g.presentation.ui.setReplayMode(
          true,
          true,
          this.g.playback.replay?.metadata.difficulty,
        )
        this.g.presentation.markNeedsRender()
      },
      onSpeedChange: (speed: number) => {
        this.setPlaybackSpeed(speed as PlaybackSpeed)
      },
      onExit: () => {
        this.stopPlayback()
        this.g.resetToMenu()
      },
      onReplayAgain: () => {
        // Replay the same replay from the beginning
        if (!this.g.playback) return
        const replay = this.g.playback.replay
        this.stopPlayback()
        if (replay) this.startPlayback(replay)
      },
      onBackToMenu: () => {
        this.stopPlayback()
        this.g.resetToMenu()
      },
      onExport: () => {
        this.exportReplay(this.g.playback?.replay)
      },
      onProgressHover: (progress: number) => {
        // Instant thumbnail from pre-computed keyframes — no simulation replay
        if (!this.g.playback || this.g.playback.isEnded) return
        const thumbData = this.g.playback.getThumbnailAt(progress)
        if (thumbData) {
          const thumbCanvas = this.g.presentation.ui.replayController.getThumbnailCanvas()
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
    // Surface the Take Over entry on the HUD (consistent with 督战 / spectate)
    // instead of the old button inside the replay controller bar.
    this.g.presentation.ui.onReplayTakeover = () => this.takeOverFromReplay()
  }

  /**
   * Build thumbnail keyframes for the current replay by replaying the
   * simulation once and capturing the canvas at regular intervals.
   * Runs synchronously — the brief freeze is acceptable for instant hover.
   */
  buildThumbnailKeyframes(): void {
    if (!this.g.playback) return
    this.g.playback.buildKeyframes(
      this.g.world,
      this.g.simulation,
      (w) => {
        if (this.g.presentation.shouldRender(w)) {
          this.g.presentation.render(w, 0)
        }
      },
      () => {
        // Capture 160×160 thumbnail from the main canvas
        const canvas = this.g.presentation.ui.canvas
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
   * Render an imported replay's final frame to a thumbnail data URL so it
   * shows a battlefield preview in the Replay Browser (native recordings
   * already capture one at game-over). Drives the LIVE simulation to the end
   * on a throwaway input, captures the canvas, then restores the live world
   * exactly (cloneWorld/restoreWorld clear transient events and re-derive the
   * difficulty/theme profile). The intermediate canvas paint is never shown
   * because all of this runs inside one task and we re-render the restored
   * world before returning.
   */
  renderImportedReplayThumbnail(replay: Replay): string | null {
    if (!replay.frames || replay.frames.length === 0) return null
    // Save the live world + input + state so we can hand the stage back.
    const savedSnap = cloneWorld(this.g.world)
    const savedInput = this.g.simulation.input
    const savedInput2 = this.g.simulation.input2
    const savedState = this.g.world.state

    let thumb: string | null = null
    try {
      // Restore the replay's starting world and swap to its recorded input.
      restoreWorld(this.g.world, replay.initialSnapshot)
      const input = new ReplayInput(replay.frames)
      this.g.simulation.input = input
      // Lie-Back-Win-Mode: wire replay input2 for coop replays.
      this.g.simulation.input2 = input.input2 ?? null
      this.g.world.state = 'playing'
      // Fast-forward to the final frame (no rendering during the loop).
      while (!input.isFinished) {
        this.g.simulation.tick()
        input.advance()
      }
      // Paint the final frame and capture it as a thumbnail data URL.
      this.g.presentation.render(this.g.world, 0)
      thumb = this.g.presentation.captureThumbnail()
    } catch (err) {
      console.warn('[replay] thumbnail render failed:', err)
    } finally {
      // Hand the stage back to the live game, exactly as we found it.
      restoreWorld(this.g.world, savedSnap)
      this.g.simulation.input = savedInput
      this.g.simulation.input2 = savedInput2
      this.g.world.state = savedState
      // Repaint the restored world so the canvas never holds the replay frame.
      this.g.presentation.render(this.g.world, 0)
    }
    return thumb
  }

  /**
   * Stop playback, restore real Input, clean up.
   */
  stopPlayback(): void {
    if (!this.g.playback) return
    // Hand back the LIVE inputs, decoration included — see wireLiveInputs().
    this.g.playback.exit(this.g.simulation, this.g.liveInput, this.g.liveInput2)
    this.g.playback = null
    // Clear replay-sourced audio attenuation (no God tank outside playback).
    this.g.audio.player2Id = null
    // 督战: a spectate replay's snapshot carries spectate=true even if the
    // LIVE session was never spectating (opened from the browser). Re-arm the
    // God AI so the restored world stays AI-driven — otherwise the keyboard
    // would silently take over player1 while the SPECTATE badge still shows.
    this.g.rearmSpectateGodInput()
    // Hide the persistent REPLAY badge from the HUD
    this.g.presentation.ui.setReplayMode(false)
    // Drop the HUD Take Over callback — replay mode is over.
    this.g.presentation.ui.onReplayTakeover = null
    // Remove canvas listeners
    this.g.presentation.ui.canvas.removeEventListener('click', this.onReplayCanvasClick)
    this.g.presentation.ui.canvas.removeEventListener('mousemove', this.onReplayCanvasMouseMove)
    this.g.accumulator = 0
    this.g.lastTime = performance.now()
  }

  /**
   * §190: Take over player1 from a paused replay — switch from replay mode
   * to live play. The world stays at the current replay frame; the human
   * keyboard takes over player1. If the replay was a coop replay, the God
   * AI is re-armed for player2. If it was a spectate replay, spectate mode
   * is disabled so the human controls P1.
   */
  takeOverFromReplay(): void {
    if (!this.g.playback || !this.g.playback.isPaused) return
    // Save coop / dual-spectate state before we modify spectate flags.
    const wasCoop = this.g.world.coop
    const wasDual = this.g.world.spectateDual
    // Disable spectate if active — the human controls P1 now, not the God AI.
    if (this.g.world.spectate) {
      this.g.world.spectate = false
      this.g.world.spectateDual = false
      this.g.godInput = null
      this.g.godInput2 = null
      this.g.autoFireInput = null
      this.g.audio.player2Id = null
      this.g.presentation.ui.controlCenter.setSpectateState('off')
    }
    // Stop playback — swaps replay inputs back to live inputs.
    // rearmSpectateGodInput() inside stopPlayback is a no-op since spectate is false.
    this.stopPlayback()
    // Replay was coop OR dual-spectate: the human takes over P1 and player2
    // keeps fighting as a live God AI partner (躺赢 / coop mode). For a coop
    // replay `coop` is already true; for a dual-spectate replay we flip it on
    // so the recorded P2 becomes a live God AI instead of a frozen tank.
    if ((wasCoop || wasDual) && this.g.world.player2) {
      // §4.1: routed through the Simulation entry point.
      this.g.simulation.applyTakeover(true)
      const rng = new RNG((this.g.world.seed ^ SEED_HASH) >>> 0)
      this.g.godInput = new GodAIInput(this.g.world, undefined, rng, (world) => world.player2)
      this.g.godInput.reset()
      this.g.autoFireInput = new AutoFireInput(this.g.input)
      this.g.audio.player2Id = this.g.world.player2.id
      this.g.presentation.ui.controlCenter.setCoopState(true)
    }
    this.g.wireLiveInputs()
    // Ensure the world is in playing state.
    this.g.world.state = 'playing'
    // Reset presentation state for live play.
    this.g.presentation.reset()
    this.g.presentation.markNeedsRender()
    // Re-arm the loop.
    this.g.accumulator = 0
    this.g.lastTime = performance.now()
    this.g.prevWorldState = this.g.world.state
    this.g.scheduleFrame()
    // Start a fresh recording from this point.
    this.g.recorder.startNew(this.g.world)
    this.g.presentation.ui.notify(t('toast.takeoverSuccess'), 'info')
  }

  /**
   * The replay consumed all frames — stop playback but stay on the last frame.
   * The controller stays visible so the user can scrub back or exit manually.
   * We keep the rAF loop alive by NOT entering idle mode (playback acts as
   * a sentinel), so the canvas keeps rendering the final frame.
   */
  finishPlayback(): void {
    if (!this.g.playback) return
    const replay = this.g.playback.replay
    // Exit the playback controller but keep it as a sentinel so scheduleFrame()
    // keeps the rAF loop alive (the world may be in a LOW_POWER state like
    // 'gameover' which would otherwise stop the loop).
    this.g.playback.exit(this.g.simulation, this.g.liveInput, this.g.liveInput2)
    // 督战: same re-arm contract as stopPlayback — the ended replay's world
    // may carry spectate=true without a live God AI.
    this.g.rearmSpectateGodInput()
    // Hide the REPLAY badge but keep the controller visible (persistent mode)
    this.g.presentation.ui.setReplayMode(false)
    // Populate end overlay with replay metadata
    if (replay) {
      const m = replay.metadata
      const stageLabel = t('replay.endStage', {
        n: m.stage + 1,
        name: localizedStageName(m.stage),
      })
      const resultLabel =
        replay.type === 'clear' ? t('replay.result.victory') : t('replay.result.defeat')
      const durationSec = Math.floor(replay.durationMs / 1000)
      const durMin = Math.floor(durationSec / 60)
      const durSec = durationSec % 60
      const durationStr = `${durMin}:${String(durSec).padStart(2, '0')}`
      const detailParts = [
        resultLabel,
        `${t('replay.detail.score')}: ${String(m.score).padStart(6, '0')}`,
        durationStr,
        `${t('replay.detail.kills')}: ${m.killCount}/${m.enemiesTotal}`,
      ]
      this.g.presentation.ui.replayController.setEndMetadata({
        title: stageLabel,
        details: detailParts.join('  ·  '),
        result: replay.type as 'clear' | 'base' | 'died',
      })
    }
    this.g.presentation.ui.replayController.showPersistent()
    this.g.presentation.markNeedsRender()
    this.g.presentation.ui.notify(t('toast.replayFinished'))
    // DO NOT null out this.g.playback — it acts as a sentinel to keep the loop alive.
    // The loop will continue to render the final frame without ticking.
  }

  /**
   * Dedicated handler for playback keyboard (ESC, pause, speed).
   * Replaces handleStateInput() during playback so live-game shortcuts
   * (Alt+S, Alt+R, KeyP) don't fire on the replay world.
   */
  handlePlaybackInput(): void {
    if (!this.g.playback) return
    if (this.g.input.wasPressed('Escape')) {
      this.stopPlayback()
      this.g.resetToMenu()
      return
    }
    // Theme cycle — Alt+T (configurable). Pauses the replay and advances to
    // the next theme. (Replay playback is presentation-only, so switching the
    // visual theme never disturbs the replay's determinism.)
    if (this.g.input.isThemePressed()) {
      this.g.themeCycle()
    }
  }

  /** Canvas click during replay → toggle play/pause and show controller. */
  onReplayCanvasClick = (): void => {
    if (!this.g.playback) return
    this.g.playback.togglePause()
    this.g.presentation.ui.setReplayMode(
      true,
      this.g.playback.isPaused,
      this.g.playback.replay?.metadata.difficulty,
    )
  }

  /** Canvas mousemove during replay → show controller and reset auto-hide. */
  onReplayCanvasMouseMove = (): void => {
    if (!this.g.playback || this.g.playback.isEnded) return
    this.g.presentation.ui.replayController.show()
    this.g.presentation.ui.setReplayMode(
      true,
      this.g.playback.isPaused,
      this.g.playback.replay?.metadata.difficulty,
    )
  }

  setPlaybackSpeed(speed: PlaybackSpeed): void {
    if (!this.g.playback || this.g.playback.currentSpeed === speed) return
    this.g.playback.setSpeed(speed)
    this.g.presentation.ui.setReplaySpeed(speed)
  }

  /** Wire the Replay Browser + Control Center replay entry. */
  wireReplayUI(): void {
    const ui = this.g.presentation.ui

    ui.replayBrowser.init({
      getReplays: () => this.g.replays.getAll(),
      onPlay: (id) => {
        const replay = this.g.replays.get(id)
        if (replay) this.startPlayback(replay)
      },
      onDelete: (id) => {
        this.g.replays.delete(id)
        ui.notify(t('toast.replayDeleted'))
      },
      onToggleFavorite: (id) => {
        const replay = this.g.replays.get(id)
        if (!replay) return false
        const wasFavorite = replay.isFavorite
        const nowFavorite = this.g.replays.toggleFavorite(id)
        if (!wasFavorite && !nowFavorite) {
          ui.notify(t('toast.favoritesFull'), 'warn')
        }
        return nowFavorite
      },
      onClose: () => {
        // Regular screen sync resumes automatically (mirrors SnapshotBrowser).
      },
      getStorageBytes: () => Promise.resolve(this.g.replays.estimateBytes()),
      onImport: (replay) => {
        this.g.replays.addReplay(replay)
        // Imported replays carry no captured thumbnail (the browser only
        // snapshots native recordings at game-over). Render the replay's
        // final frame so the Replay Browser shows a battlefield preview.
        if (!replay.thumbnail) {
          const thumb = this.renderImportedReplayThumbnail(replay)
          if (thumb) {
            replay.thumbnail = thumb
            this.g.replays.persist(replay)
          }
        }
        ui.notify(
          t('toast.replayImported', {
            stage: replay.metadata.stage + 1,
            name: replay.metadata.stageName,
          }),
        )
      },
      onExport: (id) => {
        this.exportReplay(this.g.replays.get(id))
      },
      onExportAll: () => {
        this.exportAllReplays()
      },
    })
  }

  /**
   * Serialize a replay into the .replay file format and trigger a browser
   * download. Shared by the Replay Browser (gallery) and the in-playback
   * ReplayController export button.
   */
  exportReplay(replay: Replay | null | undefined): void {
    const ui = this.g.presentation.ui
    if (!replay) {
      ui.notify(t('toast.noReplayExport'), 'warn')
      return
    }
    const envelope = serializeReplayFile({
      source: 'browser',
      seed: replay.seed,
      initialSnapshot: replay.initialSnapshot,
      frames: replay.frames,
      totalTicks: replay.totalTicks,
      metadata: replay.metadata,
      tickHashes: replay.tickHashes,
      hashInterval: replay.hashInterval,
    })
    const filename = buildReplayFilename({
      difficulty: replay.metadata.difficulty,
      stageIndex: replay.metadata.stage,
      status: replay.type,
      lives: replay.metadata.lives,
      totalTicks: replay.totalTicks,
      seed: replay.seed,
    })
    const blob = new Blob([envelope], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    // Deferred revoke: Safari aborts large downloads if the object URL is
    // revoked synchronously after the click.
    setTimeout(() => URL.revokeObjectURL(url), 10_000)
    ui.notify(t('toast.replayExported', { filename }))
  }

  /**
   * Export every stored replay as ONE NDJSON download (one serialized
   * .replay envelope per line). Bulk path for demonstration-corpus
   * collection: N individual downloads would need N click-throughs.
   */
  exportAllReplays(): void {
    const ui = this.g.presentation.ui
    const replays = this.g.replays.getAll()
    if (replays.length === 0) {
      ui.notify(t('toast.noReplayExport'), 'warn')
      return
    }
    const lines = replays.map((replay) =>
      serializeReplayFile({
        source: 'browser',
        seed: replay.seed,
        initialSnapshot: replay.initialSnapshot,
        frames: replay.frames,
        totalTicks: replay.totalTicks,
        metadata: replay.metadata,
        tickHashes: replay.tickHashes,
        hashInterval: replay.hashInterval,
      }),
    )
    const date = new Date().toISOString().slice(0, 10)
    const filename = `bc-replays-all-${date}-${replays.length}.ndjson`
    const blob = new Blob([lines.join('\n')], { type: 'application/x-ndjson' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    // Deferred revoke (same reason as exportReplay — Safari large downloads).
    setTimeout(() => URL.revokeObjectURL(url), 10_000)
    ui.notify(t('toast.replaysExportedAll', { count: replays.length, filename }))
  }

  /** Open a local .replay file for playback (not imported to database). */
  openLocalReplay(): void {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.replay'
    input.addEventListener('change', async () => {
      const file = input.files?.[0]
      if (!file) return
      try {
        const text = await file.text()
        const { parseReplayFile } = await import('../replay/file')
        const result = parseReplayFile(text)
        if ('error' in result) {
          this.g.presentation.ui.notify(
            t('toast.replayParseError', { error: result.error }),
            'warn',
          )
          return
        }
        this.startPlayback(result.replay)
      } catch (err) {
        this.g.presentation.ui.notify(t('toast.replayReadError'), 'warn')
        console.warn('[replay] local load error:', err)
      }
    })
    input.click()
  }

  /** Open the Replay Browser (Control Center button). */
  openReplayBrowser(): void {
    // The browser is never blocked by the current screen — it layers over any
    // static state (menu / paused / MISSION FAILED recovery / gameover) as a
    // fixed z-index-30 modal, and a live game is paused below first. This
    // guard is a regression pin: it once early-returned on 'recovery'.
    if (isReplayBrowserBlocked(this.g.world.state)) return
    // A replay is playing/paused/ended → leave it and return to the menu
    // before showing the browser. Mirrors the Escape-during-playback path
    // (stopPlayback + resetToMenu). This is required: clearing this.g.playback
    // without resetting would let the LIVE simulation take over from the
    // replay's world state with the real input the very next frame.
    // resetToMenu() also closes any open modal and stops playback internally.
    if (this.g.playback) {
      this.g.resetToMenu()
    } else if (this.g.world.state === 'playing') {
      // Live game → pause first so the world doesn't run behind the modal.
      this.g.simulation.togglePause()
      this.g.snapshots.create('pause', this.g.world)
    }
    // Allowed on the MISSION FAILED (recovery) screen too: the browser is a
    // z-index-30 fixed modal and its onClose is a no-op there, so it layers
    // cleanly over the recovery menu and returns to it when closed.
    this.g.presentation.ui.replayBrowser.open()
  }
}
