// ================================================================
// MenuController — extracted from the former GameMenu.ts mixin
// (plan/refactor.agy.md §1.1 mixin→composition). Bodies moved verbatim:
// `this.<slice-own>` stayed, everything else goes through the Game
// orchestrator back-reference (`this.g`). Cross-slice entry points are
// delegated on Game itself.
// ================================================================
import { DIFFICULTIES, DIFFICULTY_KEYS } from '../config/difficulty'
import { THEMES, THEME_KEYS } from '../config/theme'
import { STAGES } from '../config/stages'
import { LOW_POWER_STATES, PERF_MODE_RENDER_FPS } from '../constants'
import { i18n, t, AVAILABLE_LOCALES } from '../i18n'
import type { Locale } from '../i18n/types'
import type { Game } from './Game'

export class MenuController {
  constructor(private g: Game) {}
  handleStateInput(): void {
    const w = this.g.world

    // The Snapshot / Replay Browsers are UI-modals that own key input while
    // open (Esc is captured by the browser itself); skip game state input.
    if (this.g.presentation.ui.snapshotBrowser.isOpen()) return
    if (this.g.presentation.ui.replayBrowser.isOpen()) return

    // Fullscreen toggle — available in all states (menu / playing / paused).
    if (this.g.input.isFullscreenPressed()) {
      this.g.presentation.toggleFullscreen()
    }

    if (w.state === 'menu') {
      // The controls panel is a UI-modal that owns all key input while open;
      // skip menu navigation so it doesn't fight the panel.
      if (this.g.presentation.ui.isControlsOpen()) return

      // Row count grows by one when a resumable manual snapshot is offered
      // (the RESUME row sits at index 0, pushing the config rows down).
      // Full order: RESUME? / DIFFICULTY / THEME / LANGUAGE / STAGE / NEW GAME / CONTROLS
      const rowCount = this.g.resumeSnapshot ? 7 : 6
      // Move cursor between rows (RESUME? / DIFFICULTY / THEME / LANGUAGE / STAGE)
      // Canvas preview only switches for RESUME (0), STAGE (off+3), NEW GAME (off+4).
      const off = this.g.resumeSnapshot ? 1 : 0
      if (this.g.input.isUpPressed()) {
        w.menuCursor = (w.menuCursor - 1 + rowCount) % rowCount
        if (w.menuCursor === 0 || w.menuCursor === off + 3 || w.menuCursor === off + 4) {
          this.applyMenuPreview()
        }
        this.g.audio.init()
        this.g.audio.resume()
        this.g.audio.playMenuSelect()
      }
      if (this.g.input.isDownPressed()) {
        w.menuCursor = (w.menuCursor + 1) % rowCount
        if (w.menuCursor === 0 || w.menuCursor === off + 3 || w.menuCursor === off + 4) {
          this.applyMenuPreview()
        }
        this.g.audio.init()
        this.g.audio.resume()
        this.g.audio.playMenuSelect()
      }
      // Change value of the selected row
      const left = this.g.input.wasPressed('ArrowLeft') || this.g.input.wasPressed('KeyA')
      const right = this.g.input.wasPressed('ArrowRight') || this.g.input.wasPressed('KeyD')
      if (left || right) {
        const dir = left ? -1 : 1
        let changed = false
        if (w.menuCursor === off) {
          this.g.difficultyIndex =
            (this.g.difficultyIndex + dir + DIFFICULTY_KEYS.length) % DIFFICULTY_KEYS.length
          w.difficultyKey = DIFFICULTY_KEYS[this.g.difficultyIndex]
          w.difficulty = DIFFICULTIES[w.difficultyKey]
          changed = true
        } else if (w.menuCursor === off + 1) {
          this.g.themeIndex = (this.g.themeIndex + dir) % THEME_KEYS.length
          w.themeKey = THEME_KEYS[this.g.themeIndex]
          w.theme = THEMES[w.themeKey]
          changed = true
        } else if (w.menuCursor === off + 2) {
          // LANGUAGE row — cycle to the next available locale.
          i18n.cycleLocale()
          this.g.presentation.ui.notify(
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
          this.g.audio.init()
          this.g.audio.resume()
          this.g.audio.playMenuSelect()
        }
      }
      // Theme shortcut (Alt+T by default — see KeyBindings)
      if (this.g.input.isThemePressed()) {
        this.g.themeIndex = (this.g.themeIndex + 1) % THEME_KEYS.length
        w.themeKey = THEME_KEYS[this.g.themeIndex]
        w.theme = THEMES[w.themeKey]
        w.menuCursor = off + 1
        this.applyMenuPreview()
        this.g.audio.init()
        this.g.audio.resume()
        this.g.audio.playMenuSelect()
      }
      // Confirm — RESUME, NEW GAME, and CONTROLS respond to Enter:
      // RESUME (index 0, only when a snapshot exists) resumes;
      // NEW GAME (off + 4) starts a fresh game;
      // CONTROLS (off + 5) opens the key-bindings panel.
      const controlsIdx = off + 5
      if (this.g.input.isConfirmPressed()) {
        if (this.g.resumeSnapshot && w.menuCursor === 0) {
          this.menuResume()
        } else if (w.menuCursor === off + 4) {
          this.menuStart()
        } else if (w.menuCursor === controlsIdx) {
          this.g.presentation.ui.openControls()
          this.g.audio.init()
          this.g.audio.resume()
          this.g.audio.playMenuSelect()
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
    const justExitedFullscreen = this.g._wasFullscreen && !document.fullscreenElement
    this.g._wasFullscreen = !!document.fullscreenElement

    if (w.state === 'playing' || w.state === 'paused') {
      if (this.g.input.isPausePressed()) {
        if (justExitedFullscreen) {
          // Consume the Esc without toggling pause
        } else {
          this.g.simulation.togglePause()
          this.g.audio.playPause()
          // Entering pause → Pause snapshot (plan §3: created on pause,
          // captures the exact moment for a safe later return).
          if (w.state === 'paused') {
            this.g.snapshots.create('pause', w)
          }
        }
      }
      // Manual snapshot — Alt+S by default (plan §3, Manual); rebindable.
      if (this.g.input.isSnapshotPressed()) {
        this.g.manualSnapshot()
      }
      // Theme cycle — Alt+T (configurable). Pauses the game and advances to
      // the next theme. Re-binding to a key other than Alt+T is supported.
      if (this.g.input.isThemePressed()) {
        this.themeCycle()
      }
      if (this.g.input.isResetPressed()) {
        this.g.resetToMenu()
      }
    }

    if (w.state === 'gameover' || w.state === 'victory') {
      if (this.g.input.isResetPressed() || this.g.input.isConfirmPressed()) {
        this.g.resetToMenu()
      }
    }
  }

  // ---- Mouse-driven menu actions (mirror the keyboard 'menu' branch) ----

  /** Mouse: pick a difficulty option. */
  menuSelectDifficulty(key: string): void {
    if (this.g.world.state !== 'menu') return
    const idx = DIFFICULTY_KEYS.indexOf(key)
    if (idx < 0) return
    this.g.difficultyIndex = idx
    this.g.world.difficultyKey = DIFFICULTY_KEYS[idx]
    this.g.world.difficulty = DIFFICULTIES[this.g.world.difficultyKey]
    this.g.world.menuCursor = this.g.resumeSnapshot ? 1 : 0
    this.applyMenuPreview()
    this.g.audio.init()
    this.g.audio.resume()
    this.g.audio.playMenuSelect()
    this.g.refreshStaticScreen()
  }

  /** Mouse: pick a theme option. */
  menuSelectTheme(key: string): void {
    if (this.g.world.state !== 'menu') return
    const idx = THEME_KEYS.indexOf(key)
    if (idx < 0) return
    this.g.themeIndex = idx
    this.g.world.themeKey = THEME_KEYS[idx]
    this.g.world.theme = THEMES[this.g.world.themeKey]
    this.g.world.menuCursor = this.g.resumeSnapshot ? 2 : 1
    this.applyMenuPreview()
    this.g.audio.init()
    this.g.audio.resume()
    this.g.audio.playMenuSelect()
    this.g.refreshStaticScreen()
  }

  /** Mouse: step the stage selector (dir = -1 prev / +1 next). */
  menuCycleStage(dir: -1 | 1): void {
    if (this.g.world.state !== 'menu') return
    this.g.world.selectedStage = (this.g.world.selectedStage + dir + STAGES.length) % STAGES.length
    this.g.world.menuCursor = this.g.resumeSnapshot ? 4 : 3
    // Swap the battle-field preview to the newly selected stage's layout.
    this.applyMenuPreview()
    this.g.audio.init()
    this.g.audio.resume()
    this.g.audio.playMenuSelect()
    this.g.refreshStaticScreen()
  }

  /** Mouse: select a specific stage from the dropdown list. */
  menuSelectStage(index: number): void {
    if (this.g.world.state !== 'menu') return
    if (index < 0 || index >= STAGES.length) return
    this.g.world.selectedStage = index
    this.g.world.menuCursor = this.g.resumeSnapshot ? 4 : 3
    this.applyMenuPreview()
    this.g.audio.init()
    this.g.audio.resume()
    this.g.audio.playMenuSelect()
    this.g.refreshStaticScreen()
  }

  /** Mouse: pick a language option. */
  menuSelectLanguage(code: string): void {
    if (this.g.world.state !== 'menu') return
    if (!(AVAILABLE_LOCALES as string[]).includes(code)) return
    i18n.setLocale(code as Locale)
    this.g.presentation.ui.notify(
      t('toast.languageSet', { name: i18n.name(code as Locale) }),
      'info',
    )
    this.g.audio.init()
    this.g.audio.resume()
    this.g.audio.playMenuSelect()
  }

  /**
   * Pause so the theme dropdown / cycle can be shown. Idempotent: only pauses
   * a live game that is currently 'playing' (a 'pause' snapshot is captured,
   * matching the normal P-pause), or a replay that is currently playing. If
   * the game is already paused (or the replay already paused, or we're on the
   * menu) it is a no-op, so repeated Alt+T presses or a dropdown open never
   * un-pause the player.
   */
  themePause(): void {
    if (this.g.playback) {
      if (!this.g.playback.isPaused) {
        this.g.playback.togglePause()
        this.g.presentation.ui.setReplayMode(
          true,
          true,
          this.g.playback.replay?.metadata.difficulty,
        )
      }
    } else if (this.g.world.state === 'playing') {
      this.g.simulation.togglePause()
      this.g.snapshots.create('pause', this.g.world)
      this.g.audio.playPause()
    }
    this.g.presentation.markNeedsRender()
    this.g.presentation.updateUI(this.g.world)
  }

  /** Alt+T (or theme-cycle button): pause, then advance to the next theme. */
  themeCycle(): void {
    this.themePause()
    const current = THEME_KEYS.indexOf(this.g.world.themeKey)
    const next = (current + 1) % THEME_KEYS.length
    this.applyThemeAndRepaint(next, !this.g.playback)
  }

  /** Dropdown pick: pause, then switch to a specific theme by config key. */
  selectThemeByKey(key: string): void {
    const idx = THEME_KEYS.indexOf(key)
    if (idx < 0) return
    this.themePause()
    this.applyThemeAndRepaint(idx, !this.g.playback)
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
  applyThemeAndRepaint(index: number, persist: boolean): void {
    this.g.themeIndex = index
    this.g.world.themeKey = THEME_KEYS[index]
    this.g.world.theme = THEMES[this.g.world.themeKey]
    this.g.presentation.markNeedsRender()
    this.g.audio.init()
    this.g.audio.resume()
    this.g.audio.playMenuSelect()
    if (persist) this.g.saveSettings()
    // When there is no rAF loop driving repaints (idle/low-power states, and
    // not mid-replay where the loop is alive) render the new theme now.
    if (!this.g.playback && LOW_POWER_STATES.has(this.g.world.state)) {
      this.g.presentation.updateUI(this.g.world)
      if (this.g.presentation.shouldRender(this.g.world)) {
        this.g.presentation.render(this.g.world, 0)
      }
    } else {
      this.g.presentation.updateUI(this.g.world)
    }
  }

  /** Mouse: start button — same as the keyboard confirm (Enter/Space). */
  menuStart(): void {
    if (this.g.world.state !== 'menu') return
    this.g.audio.init()
    this.g.audio.resume()
    this.g.audio.playMenuSelect()
    this.g.recovery.reset()
    this.g.prevStageIndex = -1
    this.g.world.startGame(
      this.g.world.difficultyKey,
      this.g.world.themeKey,
      this.g.world.selectedStage,
    )
    // NOTE: recording is NOT started here — the stage-change detector in
    // loop() starts the session on the first played tick (same as the
    // keyboard start path), so both entry points share one code path.
    // Drop the click so it can't bleed into the first-frame fire input.
    this.g.input.reset()
    this.g.saveSettings()
    this.g.refreshStaticScreen()
  }

  /** Mouse / default-confirm: resume from the last manual snapshot. */
  menuResume(): void {
    if (this.g.world.state !== 'menu' || !this.g.resumeSnapshot) return
    this.g.audio.init()
    this.g.audio.resume()
    this.g.audio.playMenuSelect()
    // Avoid a spurious stage-start snapshot when the resumed stage begins.
    this.g.prevStageIndex = this.g.resumeSnapshot.metadata.stage
    this.g.recovery.beginLoad(this.g.resumeSnapshot.id, this.g.world)
    // NOTE: recording is NOT started here — beginLoad() defers the actual
    // restore until its fade completes, so a startNew() at this point would
    // capture the MENU PREVIEW world as the replay's initial snapshot
    // (corrupted replay). The recording session starts at the recovery
    // fading→countdown transition in loop(), right after restoreWorld.
    // Drop the input so the click/keypress can't bleed into gameplay.
    this.g.input.reset()
    this.g.refreshStaticScreen()
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
  setPerformanceMode(on: boolean): void {
    if (this.g.settings.performanceMode === on) return
    this.g.settings.performanceMode = on
    this.g.renderFpsCap = on ? PERF_MODE_RENDER_FPS : 0
    this.g.presentation.applyPerformanceMode(on)
    this.g.presentation.ui.controlCenter.setPerfModeState(on)
    this.g.presentation.markNeedsRender()
    this.g.saveSettings()
    this.g.audio.init()
    this.g.audio.resume()
    this.g.audio.playMenuSelect()
    // Confirm the switch in-game (menu / pause) so the player sees the result
    // without an overlay covering the battle field.
    this.g.presentation.ui.notify(
      this.g.settings.performanceMode ? t('toast.perfModeOn') : t('toast.perfModeOff'),
      'info',
    )
  }

  applyMenuPreview(): void {
    const w = this.g.world
    if (this.g.resumeSnapshot && w.menuCursor === 0) {
      w.previewSnapshot(this.g.resumeSnapshot.world)
    } else {
      w.previewStage(w.selectedStage)
    }
    // Guarantee the canvas repaints to reflect the swapped battlefield,
    // regardless of the on-demand signature check.
    this.g.presentation.markNeedsRender()
  }
}
