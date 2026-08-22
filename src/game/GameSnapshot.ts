// ================================================================
// SnapshotController — extracted from the former GameSnapshot.ts mixin
// (plan/refactor.agy.md §1.1 mixin→composition). Bodies moved verbatim:
// `this.<slice-own>` stayed, everything else goes through the Game
// orchestrator back-reference (`this.g`). Cross-slice entry points are
// delegated on Game itself.
// ================================================================
import { RECOVERY_OPTIONS, RECOVERY_OPTION_COUNT } from '../snapshot/RecoveryController'
import { localizedStageName } from '../config/stages'
import { canOpenControls } from './uiFlowGates'
import { t } from '../i18n'
import type { Game } from './Game'

export class SnapshotController {
  constructor(private g: Game) {}
  /** Wire the Snapshot Browser + Control Center callbacks into the framework. */
  wireSnapshotUI(): void {
    const ui = this.g.presentation.ui

    ui.snapshotBrowser.init({
      getSnapshots: () => this.g.snapshots.getAll(),
      onLoad: (id) => {
        if (this.g.recovery.beginLoad(id, this.g.world)) {
          this.g.audio.playRecoveryStart()
        }
        // The browser closes itself before calling onLoad (state is now
        // 'recovery', an action state), so re-arm the vsync rAF loop. The
        // 0-loop idle path can't do this because onStaticKey bails out while
        // the browser was open.
        this.g.scheduleFrame()
      },
      onDelete: (id) => {
        this.g.snapshots.delete(id)
        ui.notify(t('toast.snapshotDeleted'))
      },
      onClose: () => {
        // If the browser was opened from the recovery menu, the menu is
        // still active underneath — nothing to do. Elsewhere (paused /
        // menu) the regular screen sync resumes automatically.
      },
      getStorageBytes: () => Promise.resolve(this.g.snapshots.estimateBytes()),
    })

    ui.controlCenter.init({
      onManualSave: () => this.manualSnapshot(),
      onOpenBrowser: () => this.openSnapshotBrowser(),
      onOpenReplays: () => this.g.openReplayBrowser(),
      onOpenLocalReplay: () => this.g.openLocalReplay(),
      onTogglePerf: () => ui.togglePerfOverlay(),
      onToggleFullscreen: () => this.g.presentation.toggleFullscreen(),
      onTogglePerformance: () => this.g.setPerformanceMode(!this.g.settings.performanceMode),
      onToggleCoop: () => this.g.requestCoopToggle(),
      onCycleSpectate: () => this.g.cycleSpectate(),
      onOpenControls: () => {
        const s = this.g.world.state
        // The panel is a static modal; it opens over any static screen (menu /
        // paused / MISSION FAILED recovery / classic game over). ('paused' is
        // reached when clicking Key Bindings during play: the Control Center
        // auto-pauses before invoking this callback, so a static screen is
        // already underneath the modal.) Elsewhere the live world is running
        // and the panel can't be shown over it.
        if (canOpenControls(s)) {
          ui.openControls()
        } else {
          ui.notify(t('toast.keyBindingsPaused'), 'warn')
        }
      },
      onThemePause: () => this.g.themePause(),
      onThemeCycle: () => this.g.themeCycle(),
      onSelectTheme: (key: string) => this.g.selectThemeByKey(key),
      getCounts: () => ({
        total: this.g.snapshots.count(),
        manual: this.g.snapshots.count('manual'),
        manualLimit: this.g.snapshots.policyFor('manual').limit,
      }),
      getReplayCounts: () => ({
        total: this.g.replays.count(),
        favorites: this.g.replays.favoriteCount(),
      }),
      isPlaying: () => this.g.world.state === 'playing',
      onPause: () => {
        if (this.g.world.state === 'playing') {
          this.g.simulation.togglePause()
          this.g.snapshots.create('pause', this.g.world)
          this.g.audio.playPause()
        }
      },
    })
  }

  // ---- Snapshots (plan §3, §10, §12) ----

  /**
   * Create a Manual snapshot (Alt+S by default / Control Center button). Manual
   * snapshots are never overwritten — when all 100 slots are used, the
   * player is asked to clean up instead (plan §3).
   */
  manualSnapshot(): void {
    const w = this.g.world
    if (w.state !== 'playing' && w.state !== 'paused') return

    const snap = this.g.snapshots.create('manual', w)
    const ui = this.g.presentation.ui
    if (snap) {
      const m = snap.metadata
      ui.notify(t('toast.snapshotSaved', { stage: m.stage + 1, name: localizedStageName(m.stage) }))
      this.g.audio.playMenuSelect()
    } else {
      const limit = this.g.snapshots.policyFor('manual').limit
      ui.notify(t('toast.snapshotFull', { n: limit }), 'warn')
    }
  }

  /** Open the Snapshot Browser (Control Center / recovery menu). */
  openSnapshotBrowser(): void {
    // Playing → pause first so the world doesn't run behind the modal.
    if (this.g.world.state === 'playing') {
      this.g.simulation.togglePause()
      this.g.snapshots.create('pause', this.g.world)
    }
    this.g.presentation.ui.snapshotBrowser.open()
  }

  // ---- Failure Recovery (plan §11) ----

  /**
   * Intercept game-over and transition to the recovery flow.
   * The Simulation has already set state='gameover' and saved the
   * high score; we redirect to 'recovery' so the player can choose
   * to rewind time instead of accepting defeat.
   */
  startRecovery(): void {
    this.g.recovery.start(this.g.world)
    // Publish option availability to the menu UI (greyed-out options).
    this.g.presentation.ui.setRecoveryAvailability(
      RECOVERY_OPTIONS.map((opt) => this.g.recovery.isOptionAvailable(opt, this.g.world)),
    )
  }

  /** Handle keyboard navigation in the recovery menu. */
  handleRecoveryInput(): void {
    const w = this.g.world
    if (!this.g.recovery.isMenuPhase()) return
    // The Snapshot Browser overlays the recovery menu when opened via
    // "Choose a Snapshot…" — it owns all input until closed.
    if (this.g.presentation.ui.snapshotBrowser.isOpen()) return
    // The Replay Browser can be opened over the recovery menu from the
    // Control Center — it owns all key input while open, so don't let arrow
    // keys move the recovery cursor underneath it.
    if (this.g.presentation.ui.replayBrowser.isOpen()) return
    // The Key Bindings panel can also be opened over the recovery menu from
    // the Control Center — don't let arrow keys move the recovery cursor
    // underneath it.
    if (this.g.presentation.ui.isControlsOpen()) return

    // Navigate up/down
    if (this.g.input.isUpPressed()) {
      w.ui.recoveryCursor =
        (w.ui.recoveryCursor - 1 + RECOVERY_OPTION_COUNT) % RECOVERY_OPTION_COUNT
      this.g.audio.playMenuSelect()
    }
    if (this.g.input.isDownPressed()) {
      w.ui.recoveryCursor = (w.ui.recoveryCursor + 1) % RECOVERY_OPTION_COUNT
      this.g.audio.playMenuSelect()
    }

    // Confirm selection
    if (this.g.input.isConfirmPressed()) {
      const option = RECOVERY_OPTIONS[w.ui.recoveryCursor]
      const result = this.g.recovery.select(option, w)
      switch (result.kind) {
        case 'transition':
          this.g.audio.playRecoveryStart()
          break
        case 'browse':
          this.g.presentation.ui.snapshotBrowser.open()
          this.g.audio.playMenuSelect()
          break
        case 'continue':
          this.g.audio.playMenuSelect()
          break
        case 'none':
          // Option unavailable — soft "denied" beep
          this.g.audio.playMenuSelect()
          break
      }
    }

    // Allow abandoning recovery and returning to menu
    if (this.g.input.isResetPressed()) {
      this.g.resetToMenu()
    }
  }
}
