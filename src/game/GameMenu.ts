import { DIFFICULTIES, DIFFICULTY_KEYS } from '../config/difficulty'
import { THEMES, THEME_KEYS } from '../config/theme'
import { STAGES } from '../config/stages'
import { LOW_POWER_STATES, PERF_MODE_RENDER_FPS } from '../constants'
import { i18n, t, AVAILABLE_LOCALES } from '../i18n'
import type { Locale } from '../i18n/types'
import type { GameConstructor, GameCore } from './GameCore'

/**
 * GameMenuMixin — menu / pause / game-over state input plus the mouse-driven
 * menu actions (difficulty / theme / language / stage rows) and theme handling.
 *
 * Composes onto {@link GameCore} (via the GameLoop mixin). See `Game.ts` for
 * the final mixin order. Cross-mixin calls (snapshot/replay flows, loop
 * re-arming) resolve to the stubs declared on `GameCore`.
 */
export function GameMenuMixin<TBase extends GameConstructor<GameCore>>(Base: TBase) {
  return class GameMenu extends Base {
    protected handleStateInput(): void {
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
        // Full order: RESUME? / DIFFICULTY / THEME / LANGUAGE / STAGE / NEW GAME / CONTROLS
        const rowCount = this.resumeSnapshot ? 7 : 6
        // Move cursor between rows (RESUME? / DIFFICULTY / THEME / LANGUAGE / STAGE)
        // Canvas preview only switches for RESUME (0), STAGE (off+3), NEW GAME (off+4).
        const off = this.resumeSnapshot ? 1 : 0
        if (this.input.isUpPressed()) {
          w.menuCursor = (w.menuCursor - 1 + rowCount) % rowCount
          if (w.menuCursor === 0 || w.menuCursor === off + 3 || w.menuCursor === off + 4) {
            this.applyMenuPreview()
          }
          this.audio.init()
          this.audio.resume()
          this.audio.playMenuSelect()
        }
        if (this.input.isDownPressed()) {
          w.menuCursor = (w.menuCursor + 1) % rowCount
          if (w.menuCursor === 0 || w.menuCursor === off + 3 || w.menuCursor === off + 4) {
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
            // LANGUAGE row — cycle to the next available locale.
            i18n.cycleLocale()
            this.presentation.ui.notify(
              t('toast.languageSet', { name: i18n.name(i18n.locale) }),
              'info',
            )
            changed = true
          } else if (w.menuCursor === off + 3) {
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
        // NEW GAME (off + 4) starts a fresh game;
        // CONTROLS (off + 5) opens the key-bindings panel.
        const controlsIdx = off + 5
        if (this.input.isConfirmPressed()) {
          if (this.resumeSnapshot && w.menuCursor === 0) {
            this.menuResume()
          } else if (w.menuCursor === off + 4) {
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
        // Theme cycle — Alt+T (configurable). Pauses the game and advances to
        // the next theme. Re-binding to a key other than Alt+T is supported.
        if (this.input.isThemePressed()) {
          this.themeCycle()
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
    protected menuSelectDifficulty(key: string): void {
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
    protected menuSelectTheme(key: string): void {
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
    protected menuCycleStage(dir: -1 | 1): void {
      if (this.world.state !== 'menu') return
      this.world.selectedStage = (this.world.selectedStage + dir + STAGES.length) % STAGES.length
      this.world.menuCursor = this.resumeSnapshot ? 4 : 3
      // Swap the battle-field preview to the newly selected stage's layout.
      this.applyMenuPreview()
      this.audio.init()
      this.audio.resume()
      this.audio.playMenuSelect()
      this.refreshStaticScreen()
    }

    /** Mouse: select a specific stage from the dropdown list. */
    protected menuSelectStage(index: number): void {
      if (this.world.state !== 'menu') return
      if (index < 0 || index >= STAGES.length) return
      this.world.selectedStage = index
      this.world.menuCursor = this.resumeSnapshot ? 4 : 3
      this.applyMenuPreview()
      this.audio.init()
      this.audio.resume()
      this.audio.playMenuSelect()
      this.refreshStaticScreen()
    }

    /** Mouse: pick a language option. */
    protected menuSelectLanguage(code: string): void {
      if (this.world.state !== 'menu') return
      if (!(AVAILABLE_LOCALES as string[]).includes(code)) return
      i18n.setLocale(code as Locale)
      this.presentation.ui.notify(
        t('toast.languageSet', { name: i18n.name(code as Locale) }),
        'info',
      )
      this.audio.init()
      this.audio.resume()
      this.audio.playMenuSelect()
    }

    /**
     * Pause so the theme dropdown / cycle can be shown. Idempotent: only pauses
     * a live game that is currently 'playing' (a 'pause' snapshot is captured,
     * matching the normal P-pause), or a replay that is currently playing. If
     * the game is already paused (or the replay already paused, or we're on the
     * menu) it is a no-op, so repeated Alt+T presses or a dropdown open never
     * un-pause the player.
     */
    protected themePause(): void {
      if (this.playback) {
        if (!this.playback.isPaused) {
          this.playback.togglePause()
          this.presentation.ui.setReplayMode(true, true, this.playback.replay?.metadata.difficulty)
        }
      } else if (this.world.state === 'playing') {
        this.simulation.togglePause()
        this.snapshots.create('pause', this.world)
        this.audio.playPause()
      }
      this.presentation.markNeedsRender()
      this.presentation.updateUI(this.world)
    }

    /** Alt+T (or theme-cycle button): pause, then advance to the next theme. */
    protected themeCycle(): void {
      this.themePause()
      const current = THEME_KEYS.indexOf(this.world.themeKey)
      const next = (current + 1) % THEME_KEYS.length
      this.applyThemeAndRepaint(next, !this.playback)
    }

    /** Dropdown pick: pause, then switch to a specific theme by config key. */
    protected selectThemeByKey(key: string): void {
      const idx = THEME_KEYS.indexOf(key)
      if (idx < 0) return
      this.themePause()
      this.applyThemeAndRepaint(idx, !this.playback)
    }

    /**
     * Set the world theme to `index` and repaint. The HTML UI theme variables
     * are re-applied automatically by `PresentationLayer.updateUI` (which calls
     * `applyThemeIfChanged`) on the next frame; we additionally force an
     * immediate repaint when the loop would otherwise be idle (menu / paused /
     * ended replay with no rAF driver) so the change is visible at once.
     *
     * `persist` writes the choice to saved settings — skipped during replay so a
     * replay's visual theme never pollutes the player's default theme.
     */
    private applyThemeAndRepaint(index: number, persist: boolean): void {
      this.themeIndex = index
      this.world.themeKey = THEME_KEYS[index]
      this.world.theme = THEMES[this.world.themeKey]
      this.presentation.markNeedsRender()
      this.audio.init()
      this.audio.resume()
      this.audio.playMenuSelect()
      if (persist) this.saveSettings()
      // When there is no rAF loop driving repaints (idle/low-power states, and
      // not mid-replay where the loop is alive) render the new theme now.
      if (!this.playback && LOW_POWER_STATES.has(this.world.state)) {
        this.presentation.updateUI(this.world)
        if (this.presentation.shouldRender(this.world)) {
          this.presentation.render(this.world, 0)
        }
      } else {
        this.presentation.updateUI(this.world)
      }
    }

    /** Mouse: start button — same as the keyboard confirm (Enter/Space). */
    protected menuStart(): void {
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
    protected menuResume(): void {
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
    protected setPerformanceMode(on: boolean): void {
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
        this.settings.performanceMode ? t('toast.perfModeOn') : t('toast.perfModeOff'),
        'info',
      )
    }

    protected applyMenuPreview(): void {
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
  }
}
