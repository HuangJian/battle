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
import { DIFFICULTIES, DIFFICULTY_KEYS } from '../config/difficulty'
import { THEMES, THEME_KEYS } from '../config/theme'
import { PERF_MODE_RENDER_FPS } from '../constants'
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

/** Constructor type for the Game mixin chain (base = GameCore). */
export type GameConstructor<T = GameCore> = new (...args: any[]) => T

/**
 * Game — top-level orchestrator (base layer).
 *
 * Holds every field plus the cross-cutting wiring: the constructor, the
 * coop/spectate mode toggles, battle speed, settings persistence and
 * `resetToMenu`. The four subsystem mixins — GameLoop, GameMenu,
 * GameSnapshot, GameReplay — provide the methods stubbed at the bottom of
 * this class; `Game.ts` composes them into the final `Game` class.
 *
 * The stubs exist so that cross-mixin calls (and the constructor's wiring)
 * type-check against this base. The composed `Game` always installs every
 * mixin, so a stub is never reached at runtime; throwing loudly if one IS
 * reached makes a broken composition fail fast instead of silently no-op'ing.
 */
export class GameCore {
  world: World
  input: Input
  simulation: Simulation
  presentation: PresentationLayer
  audio: AudioManager
  snapshots: SnapshotManager
  recovery: RecoveryController
  replays: ReplayManager
  protected recorder: InputRecorder
  /** Presence flag — NOT a world state. When non-null, playback is active. */
  playback: PlaybackController | null = null

  protected lastTime = 0
  protected accumulator = 0
  protected running = false
  protected rafId = 0
  protected prevStageIndex = -1
  /** Previous world state, used to detect the transition into `playing`. */
  protected prevWorldState: World['state'] = 'menu'
  /** Timestamp of the last canvas repaint (for the render-FPS throttle). */
  protected _lastRenderTime = 0
  /** Render FPS cap (0 = uncapped). Driven by Performance Mode. */
  protected renderFpsCap = 0
  /**
   * Live battle-speed multiplier (督战 Alt+</> shortcuts). Presentation/loop
   * concern only — it scales the accumulator's ms deposition, never the
   * fixed-timestep ticks themselves, so determinism is untouched (AGENTS §2.3).
   */
  protected battleSpeed: BattleSpeed = 1
  /** True while the tab is hidden (loop paused by visibilitychange). */
  protected _hidden = false
  /**
   * Tracks whether we were in fullscreen on the previous frame so we can
   * suppress the Esc-triggered pause when the browser exits fullscreen
   * (plan §5.2: Esc double-trigger).
   */
  protected _wasFullscreen = false
  protected prevRecoveryPhase = 'idle'
  protected prevCountdown = 0
  /** The last manually-saved snapshot, if any — offered as the default RESUME on the start screen. */
  protected resumeSnapshot: GameSnapshot | null = null

  // ---- Lie-Back-Win-Mode (coop) ----
  /** God AI input for player2 — created on demand when coop is toggled. */
  protected godInput: GodAIInput | null = null
  /** Auto-fire wrapper around the human input — re-armed each stage. */
  protected autoFireInput: AutoFireInput | null = null

  /**
   * The player-1 input the LIVE simulation must consume right now.
   * In Lie-Back-Win-Mode the raw keyboard is decorated by AutoFireInput, and
   * that decorated object — not `this.input` — is what the sim ticks on and
   * what the recorder taps (DECISIONS #75).
   */
  protected get liveInput(): InputLike {
    // 督战 (supervise) mode: God AI drives player1; the human keyboard is
    // disconnected entirely — nobody is at the controls.
    if (this.world.spectate && this.godInput) return this.godInput
    return this.autoFireInput ?? this.input
  }

  /** The player-2 input the LIVE simulation must consume — null unless coop. */
  protected get liveInput2(): InputLike | null {
    // Spectate is strictly single-player (God AI as P1) — never a second tank.
    if (this.world.spectate) return null
    return this.godInput
  }

  /**
   * Point the simulation back at the live inputs. Single source of truth for
   * the wiring, so no exit path can restore only half of it (DECISIONS #76).
   * Call AFTER `godInput` / `autoFireInput` have been set to their new values.
   */
  protected wireLiveInputs(): void {
    this.simulation.input = this.liveInput
    this.simulation.input2 = this.liveInput2
  }

  /** Rolling FPS (updated once per second) — cheap regression signal. */
  fps = 0
  protected _frameCount = 0
  protected _fpsLastTime = 0
  protected _slowSeconds = 0

  settings: GameSettings
  protected difficultyIndex = 1 // classic
  protected themeIndex = 0

  constructor(root: HTMLElement) {
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
      w.player2 = null
      w.lives2 = 0
      w.playerLevel2 = 0
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
        this.presentation.ui.controlCenter.setSpectateState(false)
      }
      // Apply immediately since we are paused/menu (no tick will fire).
      w.coop = true
      const d = w.difficulty
      w.lives2 = d?.startLives ?? 3
      w.playerLevel2 = d?.playerStartLevel ?? 0
      const p1Col = w.playerSpawnPoint?.col ?? 8
      w.player2SpawnPoint = { col: 24 - p1Col, row: 24 }
      w.spawnPlayer2()
      // Wire AI inputs
      const rng = new RNG((w.seed ^ 0x9e3779b9) >>> 0)
      this.godInput = new GodAIInput(w, undefined, rng, (world) => world.player2)
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

  /**
   * Toggle supervise mode on/off. Available from the menu, a paused game, and
   * the MISSION FAILED (recovery) screen (same gate as co-op).
   * When enabled: God AI takes control of PLAYER1 (default controlledTank =
   * `w.player`) and the human keyboard is disconnected from gameplay entirely.
   * When disabled: control returns to the keyboard.
   */
  requestSpectateToggle(): void {
    const w = this.world
    if (!canToggleSpectate(w.state)) return

    if (w.spectate) {
      // Disable spectate: World mutation deferred to Simulation (One-Author).
      this.simulation.requestSpectateToggle(false)
      // Apply immediately since we are paused/menu (no tick will fire).
      w.spectate = false
      // Wire AI inputs
      this.godInput = null
      this.autoFireInput = null
      this.wireLiveInputs()
      this.audio.player2Id = null
      this.presentation.ui.notify(t('toast.spectateOff'), 'info')
    } else {
      // Enable spectate: World mutation deferred to Simulation (One-Author).
      this.simulation.requestSpectateToggle(true)
      // 督战 and co-op are mutually exclusive — exit co-op first.
      if (w.coop) {
        this.simulation.requestCoopToggle(false)
        w.coop = false
        w.player2 = null
        w.lives2 = 0
        w.playerLevel2 = 0
        this.presentation.ui.controlCenter.setCoopState(false)
      }
      // Apply immediately since we are paused/menu (no tick will fire).
      w.spectate = true
      // God AI drives player1 (default controlledTank = `w => w.player`).
      const rng = new RNG((w.seed ^ 0x9e3779b9) >>> 0)
      this.godInput = new GodAIInput(w, undefined, rng)
      this.autoFireInput = null
      this.wireLiveInputs()
      this.audio.player2Id = null
      this.presentation.ui.notify(t('toast.spectateOn'), 'info')
    }
    this.presentation.ui.controlCenter.setSpectateState(w.spectate)
    this.presentation.updateUI(w)
    this.presentation.markNeedsRender()
  }

  // ---- Battle speed (Alt+> faster / Alt+< slower) ----

  /** Step the live battle speed one notch up (+1) or down (−1). */
  protected adjustBattleSpeed(dir: 1 | -1): void {
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
  protected rearmSpectateGodInput(): void {
    const w = this.world
    if (!w.spectate || this.godInput || !w.player) return
    const rng = new RNG((w.seed ^ 0x9e3779b9) >>> 0)
    this.godInput = new GodAIInput(w, undefined, rng)
    this.autoFireInput = null
    this.wireLiveInputs()
    this.audio.player2Id = null
    this.presentation.ui.controlCenter.setSpectateState(true)
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
    // Lie-Back-Win-Mode: clean up coop state on return to menu.
    this.world.coop = false
    this.world.player2 = null
    this.world.lives2 = 0
    this.world.playerLevel2 = 0
    this.world.score2 = 0
    // 督战 (supervise) mode: clean up spectate state too.
    this.world.spectate = false
    this.godInput = null
    this.autoFireInput = null
    this.simulation.clearPendingCoopToggle()
    this.simulation.clearPendingSpectateToggle()
    this.wireLiveInputs()
    this.audio.player2Id = null
    this.presentation.ui.controlCenter.setCoopState(false)
    this.presentation.ui.controlCenter.setSpectateState(false)
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
    this.world.menuCursor = 0
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
  // Mixin-provided methods (stubs).
  //
  // The four subsystem mixins (GameLoop / GameMenu / GameSnapshot /
  // GameReplay) override these. The stubs exist so that cross-mixin calls
  // (and the constructor's wiring) type-check against this base class; the
  // composed `Game` in Game.ts always installs every mixin, so a stub is
  // never reached at runtime. Throwing loudly if one IS reached makes a
  // broken composition fail fast instead of silently no-op'ing.
  // ------------------------------------------------------------------

  protected scheduleFrame(): void {
    throw new Error('GameCore stub: scheduleFrame() must be provided by GameLoopMixin')
  }

  protected refreshStaticScreen(): void {
    throw new Error('GameCore stub: refreshStaticScreen() must be provided by GameLoopMixin')
  }

  protected handleStateInput(): void {
    throw new Error('GameCore stub: handleStateInput() must be provided by GameMenuMixin')
  }

  protected menuSelectDifficulty(_key: string): void {
    throw new Error('GameCore stub: menuSelectDifficulty() must be provided by GameMenuMixin')
  }

  protected menuSelectTheme(_key: string): void {
    throw new Error('GameCore stub: menuSelectTheme() must be provided by GameMenuMixin')
  }

  protected menuSelectLanguage(_code: string): void {
    throw new Error('GameCore stub: menuSelectLanguage() must be provided by GameMenuMixin')
  }

  protected menuCycleStage(_dir: -1 | 1): void {
    throw new Error('GameCore stub: menuCycleStage() must be provided by GameMenuMixin')
  }

  protected menuSelectStage(_index: number): void {
    throw new Error('GameCore stub: menuSelectStage() must be provided by GameMenuMixin')
  }

  protected menuStart(): void {
    throw new Error('GameCore stub: menuStart() must be provided by GameMenuMixin')
  }

  protected menuResume(): void {
    throw new Error('GameCore stub: menuResume() must be provided by GameMenuMixin')
  }

  protected themePause(): void {
    throw new Error('GameCore stub: themePause() must be provided by GameMenuMixin')
  }

  protected themeCycle(): void {
    throw new Error('GameCore stub: themeCycle() must be provided by GameMenuMixin')
  }

  protected selectThemeByKey(_key: string): void {
    throw new Error('GameCore stub: selectThemeByKey() must be provided by GameMenuMixin')
  }

  protected setPerformanceMode(_on: boolean): void {
    throw new Error('GameCore stub: setPerformanceMode() must be provided by GameMenuMixin')
  }

  protected applyMenuPreview(): void {
    throw new Error('GameCore stub: applyMenuPreview() must be provided by GameMenuMixin')
  }

  protected wireSnapshotUI(): void {
    throw new Error('GameCore stub: wireSnapshotUI() must be provided by GameSnapshotMixin')
  }

  protected manualSnapshot(): void {
    throw new Error('GameCore stub: manualSnapshot() must be provided by GameSnapshotMixin')
  }

  protected startRecovery(): void {
    throw new Error('GameCore stub: startRecovery() must be provided by GameSnapshotMixin')
  }

  protected handleRecoveryInput(): void {
    throw new Error('GameCore stub: handleRecoveryInput() must be provided by GameSnapshotMixin')
  }

  protected finalizeRecording(_type: ReplayType): void {
    throw new Error('GameCore stub: finalizeRecording() must be provided by GameReplayMixin')
  }

  protected finishPlayback(): void {
    throw new Error('GameCore stub: finishPlayback() must be provided by GameReplayMixin')
  }

  protected handlePlaybackInput(): void {
    throw new Error('GameCore stub: handlePlaybackInput() must be provided by GameReplayMixin')
  }

  protected stopPlayback(): void {
    throw new Error('GameCore stub: stopPlayback() must be provided by GameReplayMixin')
  }

  protected setPlaybackSpeed(_speed: PlaybackSpeed): void {
    throw new Error('GameCore stub: setPlaybackSpeed() must be provided by GameReplayMixin')
  }

  protected wireReplayUI(): void {
    throw new Error('GameCore stub: wireReplayUI() must be provided by GameReplayMixin')
  }

  protected openLocalReplay(): void {
    throw new Error('GameCore stub: openLocalReplay() must be provided by GameReplayMixin')
  }

  protected openReplayBrowser(): void {
    throw new Error('GameCore stub: openReplayBrowser() must be provided by GameReplayMixin')
  }
}
