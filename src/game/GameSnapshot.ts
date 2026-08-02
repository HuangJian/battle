import { RECOVERY_OPTIONS, RECOVERY_OPTION_COUNT } from '../snapshot/RecoveryController'
import { localizedStageName } from '../config/stages'
import { canOpenControls } from './uiFlowGates'
import { t } from '../i18n'
import type { GameConstructor, GameCore } from './GameCore'

/**
 * GameSnapshotMixin — the Snapshot Management framework wiring (Snapshot
 * Browser + Control Center callbacks), manual snapshots, and the failure
 * Recovery flow (startRecovery / handleRecoveryInput).
 *
 * Composes onto {@link GameCore} (via the GameLoop + GameMenu mixins). See
 * `Game.ts` for the final mixin order. Cross-mixin calls (loop re-arming,
 * replay browser, theme actions) resolve to the stubs declared on `GameCore`.
 */
export function GameSnapshotMixin<TBase extends GameConstructor<GameCore>>(Base: TBase) {
  return class GameSnapshot extends Base {
    /** Wire the Snapshot Browser + Control Center callbacks into the framework. */
    protected wireSnapshotUI(): void {
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
          ui.notify(t('toast.snapshotDeleted'))
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
        onOpenLocalReplay: () => this.openLocalReplay(),
        onTogglePerf: () => ui.togglePerfOverlay(),
        onToggleFullscreen: () => this.presentation.toggleFullscreen(),
        onTogglePerformance: () => this.setPerformanceMode(!this.settings.performanceMode),
        onToggleCoop: () => this.requestCoopToggle(),
        onToggleSpectate: () => this.requestSpectateToggle(),
        onOpenControls: () => {
          const s = this.world.state
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
        onThemePause: () => this.themePause(),
        onThemeCycle: () => this.themeCycle(),
        onSelectTheme: (key: string) => this.selectThemeByKey(key),
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

    // ---- Snapshots (plan §3, §10, §12) ----

    /**
     * Create a Manual snapshot (Alt+S by default / Control Center button). Manual
     * snapshots are never overwritten — when all 100 slots are used, the
     * player is asked to clean up instead (plan §3).
     */
    protected manualSnapshot(): void {
      const w = this.world
      if (w.state !== 'playing' && w.state !== 'paused') return

      const snap = this.snapshots.create('manual', w)
      const ui = this.presentation.ui
      if (snap) {
        const m = snap.metadata
        ui.notify(
          t('toast.snapshotSaved', { stage: m.stage + 1, name: localizedStageName(m.stage) }),
        )
        this.audio.playMenuSelect()
      } else {
        const limit = this.snapshots.policyFor('manual').limit
        ui.notify(t('toast.snapshotFull', { n: limit }), 'warn')
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
    protected startRecovery(): void {
      this.recovery.start(this.world)
      // Publish option availability to the menu UI (greyed-out options).
      this.presentation.ui.setRecoveryAvailability(
        RECOVERY_OPTIONS.map((opt) => this.recovery.isOptionAvailable(opt, this.world)),
      )
    }

    /** Handle keyboard navigation in the recovery menu. */
    protected handleRecoveryInput(): void {
      const w = this.world
      if (!this.recovery.isMenuPhase()) return
      // The Snapshot Browser overlays the recovery menu when opened via
      // "Choose a Snapshot…" — it owns all input until closed.
      if (this.presentation.ui.snapshotBrowser.isOpen()) return
      // The Replay Browser can be opened over the recovery menu from the
      // Control Center — it owns all key input while open, so don't let arrow
      // keys move the recovery cursor underneath it.
      if (this.presentation.ui.replayBrowser.isOpen()) return
      // The Key Bindings panel can also be opened over the recovery menu from
      // the Control Center — don't let arrow keys move the recovery cursor
      // underneath it.
      if (this.presentation.ui.isControlsOpen()) return

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
  }
}
