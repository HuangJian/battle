import { World } from './World'
import { RNG } from '../utils/RNG'
import { Simulation } from './Simulation'
import { Input } from './Input'
import type { InputLike } from './Input'
import { SnapshotManager } from '../snapshot/SnapshotManager'
import { createDefaultStorage } from '../snapshot/storage'
import { RecoveryController } from '../snapshot/RecoveryController'
import { PresentationLayer } from '../presentation/PresentationLayer'
import { AudioManager } from '../audio/AudioManager'
import { DIFFICULTY_KEYS } from '../config/difficulty'
import { THEME_KEYS } from '../config/theme'
import { PERF_MODE_RENDER_FPS, SEED_HASH, P2_SEED_OFFSET } from '../constants'
import type { GameSettings } from '../types'
import type { GameSnapshot } from '../snapshot/types'
import { InputRecorder } from '../replay/InputRecorder'
import { ReplayManager } from '../replay/ReplayManager'
import type { PlaybackController, PlaybackSpeed } from '../replay/PlaybackController'
import { GodAIInput } from '../ai/GodAIInput'
import { AutoFireInput } from './AutoFireInput'
import { canToggleCoop, canToggleSpectate } from './uiFlowGates'
import { cycleBattleSpeed } from './battleSpeed'
import type { BattleSpeed } from './battleSpeed'
import { createReplayStorage } from '../replay/storage'
import { t } from '../i18n'
import { loadSettings, persistSettings } from './settings'
import type { ReplayType } from '../replay/types'
import { LoopController } from './GameLoop'
import { MenuController } from './GameMenu'
import { SnapshotController } from './GameSnapshot'
import { ReplayController } from './GameReplay'

/**
 * Game — top-level orchestrator. Owns the game loop, wires all systems.
 *
 * Composition, not inheritance (plan/refactor.agy.md §1.1): the former
 * four-mixin chain + 27 throwing stubs became explicit controllers wired
 * through back-references to this orchestrator:
 *
 * - {@link LoopController}     — vsync rAF loop driver + event handlers.
 * - {@link MenuController}     — menu/pause input + theme/menu actions.
 * - {@link SnapshotController} — snapshot framework wiring + recovery flow.
 * - {@link ReplayController}   — replay record/playback/browser/export.
 *
 * Controllers read/write orchestrator state via `this.g`; cross-slice entry
 * points are real delegators below instead of type-checker appeasing stubs.
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
  recorder: InputRecorder
  /** Presence flag — NOT a world state. When non-null, playback is active. */
  playback: PlaybackController | null = null

  lastTime = 0
  accumulator = 0
  running = false
  rafId = 0
  prevStageIndex = -1
  /** Previous world state, used to detect the transition into `playing`. */
  prevWorldState: World['state'] = 'menu'
  /** Timestamp of the last canvas repaint (for the render-FPS throttle). */
  _lastRenderTime = 0
  /** Render FPS cap (0 = uncapped). Driven by Performance Mode. */
  renderFpsCap = 0
  /**
   * Live battle-speed multiplier (督战 Alt+</> shortcuts). Presentation/loop
   * concern only — it scales the accumulator's ms deposition, never the
   * fixed-timestep ticks themselves, so determinism is untouched (AGENTS §2.3).
   */
  battleSpeed: BattleSpeed = 1
  /** True while the tab is hidden (loop paused by visibilitychange). */
  _hidden = false
  /**
   * Tracks whether we were in fullscreen on the previous frame so we can
   * suppress the Esc-triggered pause when the browser exits fullscreen
   * (plan §5.2: Esc double-trigger).
   */
  _wasFullscreen = false
  prevRecoveryPhase = 'idle'
  prevCountdown = 0
  /** The last manually-saved snapshot, if any — offered as the default RESUME on the start screen. */
  resumeSnapshot: GameSnapshot | null = null

  // ---- Lie-Back-Win-Mode (coop) ----
  /** God AI input for player2 — created on demand when coop is toggled. */
  godInput: GodAIInput | null = null
  /** 督战双玩家: second God AI input for player1 in dual spectate mode. */
  godInput2: GodAIInput | null = null
  /** Auto-fire wrapper around the human input — re-armed each stage. */
  autoFireInput: AutoFireInput | null = null

  /**
   * The player-1 input the LIVE simulation must consume right now.
   * In Lie-Back-Win-Mode the raw keyboard is decorated by AutoFireInput, and
   * that decorated object — not `this.input` — is what the sim ticks on and
   * what the recorder taps (DECISIONS #75).
   */
  get liveInput(): InputLike {
    // 督战 (supervise) mode: God AI drives player1; the human keyboard is
    // disconnected entirely — nobody is at the controls.
    if (this.world.spectate && this.godInput) return this.godInput
    return this.autoFireInput ?? this.input
  }

  /** The player-2 input the LIVE simulation must consume — null unless coop. */
  get liveInput2(): InputLike | null {
    // 督战双玩家 (dual spectate): God AI also drives player2.
    if (this.world.spectateDual && this.godInput2) return this.godInput2
    // Spectate is strictly single-player (God AI as P1) — never a second tank.
    if (this.world.spectate) return null
    return this.godInput
  }

  /**
   * Point the simulation back at the live inputs. Single source of truth for
   * the wiring, so no exit path can restore only half of it (DECISIONS #76).
   * Call AFTER `godInput` / `autoFireInput` have been set to their new values.
   */
  wireLiveInputs(): void {
    this.simulation.input = this.liveInput
    this.simulation.input2 = this.liveInput2
  }

  /** Rolling FPS (updated once per second) — cheap regression signal. */
  fps = 0
  _frameCount = 0
  _fpsLastTime = 0
  _slowSeconds = 0

  settings: GameSettings
  difficultyIndex = 1 // classic
  themeIndex = 0

  // ---- Subsystem controllers (§1.1 composition) ----
  private readonly loopCtl: LoopController
  private readonly menuCtl: MenuController
  private readonly snapCtl: SnapshotController
  private readonly replayCtl: ReplayController

  constructor(root: HTMLElement) {
    // Controllers take a back-reference; their bodies never run during
    // construction, so partially-initialized `g` is safe here.
    this.loopCtl = new LoopController(this)
    this.menuCtl = new MenuController(this)
    this.snapCtl = new SnapshotController(this)
    this.replayCtl = new ReplayController(this)
    this.settings = loadSettings()
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
      selectLanguage: (code) => this.menuSelectLanguage(code),
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
    // §190: Wire the spectate takeover button — the user clicks "Take Over"
    // while paused in spectate mode to take control of player1.
    this.presentation.ui.onSpectateTakeover = () => this.takeOverFromSpectate()

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

    this.world.selectDifficulty(DIFFICULTY_KEYS[this.difficultyIndex])
    this.world.selectTheme(THEME_KEYS[this.themeIndex])
  }

  // ---- Lie-Back-Win-Mode: coop toggle ----

  /**
   * Toggle coop mode on/off. Available from both menu and paused states.
   * When enabled: spawns God AI as player2 with initial difficulty stats.
   * When disabled: removes God AI (no life donation per Q5).
   */
  requestCoopToggle(): void {
    const w = this.world
    // Available from the menu, a paused game, and the MISSION FAILED (recovery)
    // screen so co-op can be armed before retrying. (A live 'playing' game and
    // terminal 'gameover'/'victory' states intentionally fall through to no-op.)
    if (!canToggleCoop(w.state)) return

    if (w.coop) {
      // Disable coop: World mutation deferred to Simulation (One-Author).
      this.simulation.requestCoopToggle(false)
      // Apply immediately since we are paused/menu (no tick will fire).
      w.coop = false
      w.disablePlayer2()
      // Wire AI inputs
      this.godInput = null
      this.autoFireInput = null
      this.wireLiveInputs()
      this.presentation.ui.notify(t('toast.coopOff'), 'info')
    } else {
      // Enable coop: World mutation deferred to Simulation (One-Author).
      this.simulation.requestCoopToggle(true)
      // 督战 (God AI as P1) and co-op (God AI as P2) are mutually exclusive —
      // enabling co-op turns supervise off so the two never fight over input.
      if (w.spectate) {
        this.simulation.requestSpectateToggle(false)
        w.spectate = false
        w.spectateDual = false
        this.godInput2 = null
        this.presentation.ui.controlCenter.setSpectateState('off')
      }
      // Apply immediately since we are paused/menu (no tick will fire).
      w.coop = true
      w.enablePlayer2()
      // Wire AI inputs
      const rng = new RNG((w.seed ^ SEED_HASH) >>> 0)
      this.godInput = new GodAIInput(w, undefined, rng, (world) => world.player2)
      this.godInput.reset()
      this.autoFireInput = new AutoFireInput(this.input)
      this.wireLiveInputs()
      this.presentation.ui.notify(t('toast.coopOn'), 'info')
      this.audio.player2Id = w.player2?.id ?? null
    }
    this.presentation.ui.controlCenter.setCoopState(w.coop)
    this.presentation.updateUI(w)
    this.presentation.markNeedsRender()
  }

  // ---- 督战 (supervise) mode: God AI fights as player1, no human input ----

  /** Derive the current 督战 UI mode from world state. */
  private spectateMode(): 'off' | 'single' | 'dual' {
    if (!this.world.spectate) return 'off'
    return this.world.spectateDual ? 'dual' : 'single'
  }

  /**
   * Cycle 督战 (supervise) mode through OFF → 单玩家 (x1) → 双玩家 (x2) → OFF.
   * A single Control-Center button drives all three states; the world gate
   * (menu / paused / recovery) is enforced by the underlying toggle.
   */
  cycleSpectate(): void {
    const w = this.world
    if (!canToggleSpectate(w.state)) return
    if (!w.spectate) {
      this.requestSpectateToggle(false) // OFF → x1
    } else if (!w.spectateDual) {
      this.requestSpectateToggle(true) // x1 → x2
    } else {
      this.requestSpectateToggle(true) // x2 → OFF (dual already matches the request)
    }
  }

  /**
   * Toggle supervise mode. When `dual` is true both P1 and P2 are driven by
   * God AI (督战双玩家). Supports in-place single↔dual switching (P1 God AI
   * stays alive) and turns OFF when the requested mode already matches.
   */
  requestSpectateToggle(dual = false): void {
    const w = this.world
    if (!canToggleSpectate(w.state)) return

    if (w.spectate) {
      if (dual === w.spectateDual) {
        this.disableSpectate()
      } else {
        // Switch single <-> dual in place (P1 God AI stays alive).
        this.simulation.requestSpectateToggle(true)
        this.simulation.requestSpectateDualToggle(dual)
        w.spectateDual = dual
        if (dual) {
          this.enableSpectateDual()
          this.presentation.ui.notify(t('toast.spectateDualOn'), 'info')
        } else {
          this.disableSpectateDual()
          this.presentation.ui.notify(t('toast.spectateOn'), 'info')
        }
      }
    } else {
      this.enableSpectate(dual)
    }

    // Re-wire the live inputs after every spectate transition. The in-place
    // single↔dual switch above creates/disables `godInput2` but does NOT call
    // wireLiveInputs() on its own (unlike enableSpectate/disableSpectate).
    // Without this, simulation.input2 stays null in 督战双玩家 and player2 is
    // never driven — it sits idle live AND the InputRecorder captures an all-idle
    // P2 stream, so the replay desyncs. Idempotent for the other branches.
    this.wireLiveInputs()

    this.presentation.ui.controlCenter.setSpectateState(this.spectateMode())
    this.presentation.updateUI(w)
    this.presentation.markNeedsRender()
  }

  /** Enable supervise (God AI as P1). Sets `spectateDual` per `dual`. */
  private enableSpectate(dual: boolean): void {
    const w = this.world
    this.simulation.requestSpectateToggle(true)
    this.simulation.requestSpectateDualToggle(dual)
    // 督战 and co-op are mutually exclusive — exit co-op first.
    if (w.coop) {
      this.simulation.requestCoopToggle(false)
      w.coop = false
      w.disablePlayer2()
      this.presentation.ui.controlCenter.setCoopState(false)
    }
    // Apply immediately since we are paused/menu (no tick will fire).
    w.spectate = true
    // God AI drives player1 (default controlledTank = `w => w.player`).
    const rng = new RNG((w.seed ^ SEED_HASH) >>> 0)
    this.godInput = new GodAIInput(w, undefined, rng)
    this.godInput.reset()
    this.autoFireInput = null
    if (dual) {
      w.spectateDual = true
      this.enableSpectateDual()
    } else {
      w.spectateDual = false
      this.godInput2 = null
      this.audio.player2Id = null
    }
    this.wireLiveInputs()
    this.presentation.ui.notify(t(dual ? 'toast.spectateDualOn' : 'toast.spectateOn'), 'info')
  }

  /** Spawn player2 + create the second God AI (督战双玩家). Safe to call when
   *  player2 / godInput2 already exist (idempotent). */
  private enableSpectateDual(): void {
    const w = this.world
    if (!w.player2) {
      w.enablePlayer2()
    }
    if (!this.godInput2) {
      const rng2 = new RNG((w.seed ^ SEED_HASH ^ P2_SEED_OFFSET) >>> 0)
      this.godInput2 = new GodAIInput(w, undefined, rng2, (world) => world.player2)
      this.godInput2.reset()
    }
    this.audio.player2Id = w.player2?.id ?? null
  }

  /** Tear down the second God AI (dual→single or OFF). P1 God AI is untouched. */
  private disableSpectateDual(): void {
    const w = this.world
    w.spectateDual = false
    w.disablePlayer2()
    this.godInput2 = null
    this.audio.player2Id = null
  }

  private disableSpectate(): void {
    const w = this.world
    this.simulation.requestSpectateToggle(false)
    this.simulation.requestSpectateDualToggle(false)
    // Apply immediately since we are paused/menu (no tick will fire).
    w.spectate = false
    const wasDual = w.spectateDual
    w.spectateDual = false
    // Clean up dual spectate player2
    if (wasDual) {
      w.disablePlayer2()
    }
    // Wire AI inputs
    this.godInput = null
    this.godInput2 = null
    this.autoFireInput = null
    this.wireLiveInputs()
    this.audio.player2Id = null
    this.presentation.ui.notify(t('toast.spectateOff'), 'info')
  }

  /**
   * §190: Take over player1 from spectate mode while paused. Disables
   * spectate, wires the human keyboard as player1 input, and unpauses.
   * The God AI is cleaned up; if it was dual spectate, player2 is removed.
   */
  takeOverFromSpectate(): void {
    const w = this.world
    if (!w.spectate) return
    if (w.state !== 'paused') return
    const wasDual = w.spectateDual

    // Disable spectate — human controls P1 now.
    this.simulation.requestSpectateToggle(false)
    this.simulation.requestSpectateDualToggle(false)
    w.spectate = false
    w.spectateDual = false

    // Capture the dual-spectate P2 God AI before tearing it down — it becomes
    // the 躺赢 (coop) partner when taking over from 2x督战.
    const p2Ai = wasDual ? this.godInput2 : null
    this.godInput = null
    this.godInput2 = null
    this.autoFireInput = null

    if (wasDual && p2Ai && w.player2) {
      // 2x督战 → 躺赢模式 (coop): human drives P1, the God AI keeps driving P2.
      // Keep the already-spawned player2 (lives2 / playerLevel2 survive).
      // Setting `coop = true` also stops the deferred spectate-off apply
      // (SimulationCore.updatePlaying) from stripping player2 on the next tick.
      w.coop = true
      this.godInput = p2Ai // coop partner is `godInput`, bound to player2
      this.autoFireInput = new AutoFireInput(this.input)
      this.presentation.ui.controlCenter.setCoopState(true)
      this.presentation.ui.notify(t('toast.coopOn'), 'info')
    }

    this.wireLiveInputs()
    this.audio.player2Id = w.player2?.id ?? null
    this.presentation.ui.controlCenter.setSpectateState('off')
    // Unpause the game.
    this.simulation.togglePause()
    this.presentation.ui.showScreen('playing')
    this.presentation.ui.notify(t('toast.takeoverSuccess'), 'info')
    this.presentation.markNeedsRender()
    this.scheduleFrame()
  }

  // ---- Battle speed (Alt+> faster / Alt+< slower) ----

  /** Step the live battle speed one notch up (+1) or down (−1). */
  adjustBattleSpeed(dir: 1 | -1): void {
    const next = cycleBattleSpeed(this.battleSpeed, dir)
    if (next === this.battleSpeed) return
    this.battleSpeed = next
    this.presentation.ui.setBattleSpeed(next)
    this.presentation.ui.notify(t('toast.battleSpeed', { speed: next }), 'info')
  }

  /**
   * Re-arm the 督战 God AI after a replay whose snapshot carried spectate=true
   * but whose live session was never spectating (opened from the Replay
   * Browser). Without this the keyboard would silently take over player1 while
   * the SPECTATE badge still shows. No-op when spectate is off or already armed.
   */
  rearmSpectateGodInput(): void {
    const w = this.world
    if (!w.spectate || this.godInput || !w.player) return
    const rng = new RNG((w.seed ^ SEED_HASH) >>> 0)
    this.godInput = new GodAIInput(w, undefined, rng)
    this.godInput.reset()
    this.autoFireInput = null
    // 督战双玩家: re-arm second God AI for player2
    if (w.spectateDual && w.player2 && !this.godInput2) {
      const rng2 = new RNG((w.seed ^ SEED_HASH ^ P2_SEED_OFFSET) >>> 0)
      this.godInput2 = new GodAIInput(w, undefined, rng2, (world) => world.player2)
      this.godInput2.reset()
      this.audio.player2Id = w.player2.id
    } else {
      this.audio.player2Id = null
    }
    this.wireLiveInputs()
    this.presentation.ui.controlCenter.setSpectateState(this.spectateMode())
  }

  resetToMenu(): void {
    this.world.resetToMenu()
    this.godInput = null
    this.godInput2 = null
    this.autoFireInput = null
    this.simulation.clearPendingCoopToggle()
    this.simulation.clearPendingSpectateToggle()
    this.wireLiveInputs()
    this.audio.player2Id = null
    this.presentation.ui.controlCenter.setCoopState(false)
    this.presentation.ui.controlCenter.setSpectateState('off')
    // 督战 battle speed is a per-session viewing aid — return to ×1 on menu so
    // a fresh run never starts fast by accident.
    this.battleSpeed = 1
    this.presentation.ui.setBattleSpeed(1)
    this.recovery.reset()
    this.stopPlayback()
    this.recorder.reset()
    this.presentation.ui.snapshotBrowser.close()
    this.presentation.ui.replayBrowser.close()
    this.prevStageIndex = -1
    // Re-open the menu on its default row and render the matching battlefield.
    this.world.ui.menuCursor = 0
    this.applyMenuPreview()
    this.presentation.reset()
    this.audio.playMenuSelect()
  }

  // ---- Settings ----

  saveSettings(): void {
    this.settings.difficulty = this.world.difficultyKey
    this.settings.theme = this.world.themeKey
    persistSettings(this.settings)
  }

  setVolume(v: number): void {
    this.settings.volume = v
    this.audio.setVolume(v)
    this.saveSettings()
  }

  // ------------------------------------------------------------------
  // Cross-slice entry points — one-line delegators replacing the former
  // throwing stubs. Each routes to the owning subsystem controller.
  // ------------------------------------------------------------------

  // LoopController
  async start(): Promise<void> {
    return this.loopCtl.start()
  }

  stop(): void {
    this.loopCtl.stop()
  }

  requestFrame(): void {
    this.loopCtl.requestFrame()
  }

  scheduleFrame(): void {
    this.loopCtl.scheduleFrame()
  }

  refreshStaticScreen(): void {
    this.loopCtl.refreshStaticScreen()
  }

  // MenuController
  handleStateInput(): void {
    this.menuCtl.handleStateInput()
  }

  menuSelectDifficulty(key: string): void {
    this.menuCtl.menuSelectDifficulty(key)
  }

  menuSelectTheme(key: string): void {
    this.menuCtl.menuSelectTheme(key)
  }

  menuSelectLanguage(code: string): void {
    this.menuCtl.menuSelectLanguage(code)
  }

  menuCycleStage(dir: -1 | 1): void {
    this.menuCtl.menuCycleStage(dir)
  }

  menuSelectStage(index: number): void {
    this.menuCtl.menuSelectStage(index)
  }

  menuStart(): void {
    this.menuCtl.menuStart()
  }

  menuResume(): void {
    this.menuCtl.menuResume()
  }

  themePause(): void {
    this.menuCtl.themePause()
  }

  themeCycle(): void {
    this.menuCtl.themeCycle()
  }

  selectThemeByKey(key: string): void {
    this.menuCtl.selectThemeByKey(key)
  }

  setPerformanceMode(on: boolean): void {
    this.menuCtl.setPerformanceMode(on)
  }

  applyMenuPreview(): void {
    this.menuCtl.applyMenuPreview()
  }

  // SnapshotController
  wireSnapshotUI(): void {
    this.snapCtl.wireSnapshotUI()
  }

  manualSnapshot(): void {
    this.snapCtl.manualSnapshot()
  }

  startRecovery(): void {
    this.snapCtl.startRecovery()
  }

  handleRecoveryInput(): void {
    this.snapCtl.handleRecoveryInput()
  }

  // ReplayController
  finalizeRecording(type: ReplayType): void {
    this.replayCtl.finalizeRecording(type)
  }

  finishPlayback(): void {
    this.replayCtl.finishPlayback()
  }

  handlePlaybackInput(): void {
    this.replayCtl.handlePlaybackInput()
  }

  stopPlayback(): void {
    this.replayCtl.stopPlayback()
  }

  setPlaybackSpeed(speed: PlaybackSpeed): void {
    this.replayCtl.setPlaybackSpeed(speed)
  }

  wireReplayUI(): void {
    this.replayCtl.wireReplayUI()
  }

  openLocalReplay(): void {
    this.replayCtl.openLocalReplay()
  }

  openReplayBrowser(): void {
    this.replayCtl.openReplayBrowser()
  }
}
