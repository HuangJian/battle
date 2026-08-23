import type { World } from '../../game/World'
import { DIFFICULTIES, DIFFICULTY_KEYS } from '../../config/difficulty'
import { THEME_DEFINITIONS } from '../../config/theme'
import { STAGES, localizedStageName } from '../../config/stages'
import { i18n, t } from '../../i18n'
import { menuRowIndex, type MenuRowKey } from '../../game/UIState'

/**
 * Menu action callbacks registered by Game so mouse clicks on the start
 * screen can mutate World state through the same code paths as keyboard
 * input (the UI layer itself stays read-only on the World).
 */
export interface MenuActions {
  /** Select a difficulty by its config key (e.g. 'easy'). */
  selectDifficulty(key: string): void
  /** Select a theme by its config key (e.g. 'default'). */
  selectTheme(key: string): void
  /** Select a language/locale by its code (e.g. 'en', 'zh'). */
  selectLanguage(code: string): void
  /** Step the stage selector by -1 (prev) or +1 (next). */
  cycleStage(dir: -1 | 1): void
  /** Select a specific stage by index. */
  selectStage(index: number): void
  /** Start the game with the current menu selections. */
  start(): void
  /** Resume from the last manually-saved snapshot (only offered when one exists). */
  resume(): void
  /** Open the controls / key-bindings panel. */
  openControls(): void
}

/**
 * MenuScreen — the start menu: layout, config-row option lists, the stage
 * dropdown, RESUME target presentation, and per-frame highlight sync
 * (plan/refactor.agy.md §2.4). Extracted verbatim from UIManager (§256
 * slice pattern); UIManager keeps the public API and orchestrates.
 */
export class MenuScreen {
  /** The `ui-screen ui-menu` root element (appended to the overlay by UIManager). */
  readonly el: HTMLElement

  private diffOptions: HTMLElement[] = []
  private themeOptions: HTMLElement[] = []
  private langOptions: HTMLElement[] = []
  private rows: HTMLElement[] = []
  private stageValue: HTMLElement | null = null
  private stageNameEl: HTMLElement | null = null
  private stageDropdown: HTMLElement | null = null
  private dropdownOpen = false
  private hiScoreEl: HTMLElement | null = null
  private resumeStageEl: HTMLElement | null = null
  private resumeInfoEl: HTMLElement | null = null
  private startBtnEl: HTMLElement | null = null
  private resumeHint: HTMLElement | null = null
  private startHint: HTMLElement | null = null
  private controlsHint: HTMLElement | null = null

  /** Whether a resumable manual snapshot exists (set after boot hydration). */
  private hasResume = false
  private resumeTarget: { stage: number; stageName: string; score: number } | null = null

  /**
   * Cursor index of a row by its `data-menu` key (-1 = not a cursor row).
   * Delegates to the shared MENU_ROW_KEYS mapping in src/game/UIState.ts —
   * the single source consumed by BOTH this highlighter and GameMenu's
   * cursor-value logic (遗留 #6: previously encoded here AND there as
   * independent `off + N` arithmetic).
   */
  private rowIndexFor(menuKey: string): number {
    return menuRowIndex(menuKey as MenuRowKey, this.hasResume)
  }

  // Change guards (avoid unnecessary DOM writes every frame)
  private lastMenuCursor = -1
  private lastDifficultyKey = ''
  private lastThemeKeyMenu = ''
  private lastSelectedStage = -1
  private lastHighScoreMenu = -1

  /** Menu action callbacks (mouse support). Registered by Game. */
  private actions: MenuActions | null = null

  constructor(
    createElement: (tag: string, className: string) => HTMLElement,
    private readonly openControlsPanel: () => void,
  ) {
    this.el = this.build(createElement)
  }

  private build(createElement: (tag: string, className: string) => HTMLElement): HTMLElement {
    const screen = createElement('div', 'ui-screen ui-menu')
    screen.innerHTML = `
      <div class="menu-panel">
        <div class="menu-header">
          <h1 class="menu-title" data-i18n="menu.title">BATTLE CITY</h1>
          <p class="menu-subtitle" data-i18n="menu.subtitle">Faithful to the classic. Designed for the future.</p>
        </div>
        <div class="menu-section">
          <div class="menu-row menu-resume" data-menu="resume">
            <div class="menu-resume-main">
              <span class="menu-resume-label" data-i18n="menu.resume.label">RESUME</span>
              <span class="menu-resume-stage" data-menu="resume-stage">STAGE 01</span>
            </div>
            <div class="menu-resume-info" data-menu="resume-info" data-i18n="menu.resume.info">Continue from your last manual save</div>
            <span class="menu-enter-hint" data-menu="resume-hint" data-i18n="menu.resume.hint">Enter ↵</span>
          </div>
          <div class="menu-row" data-menu="difficulty">
            <span class="menu-label" data-i18n="menu.difficulty">DIFFICULTY</span>
            <div class="menu-options" data-difficulty="options"></div>
          </div>
          <div class="menu-row" data-menu="theme">
            <span class="menu-label" data-i18n="menu.theme">THEME</span>
            <div class="menu-options" data-theme="options"></div>
          </div>
          <div class="menu-row" data-menu="language">
            <span class="menu-label" data-i18n="menu.language">LANGUAGE</span>
            <div class="menu-options" data-language="options"></div>
          </div>
          <div class="menu-row" data-menu="stage">
            <span class="menu-label" data-i18n="menu.stage">STAGE</span>
            <div class="menu-stage-dropdown" data-stage="dropdown">
              <div class="menu-stage-trigger" data-stage="trigger">
                <span class="menu-stage-value" data-stage="value">01 / 35</span>
                <span class="menu-stage-name" data-stage="name">Outpost</span>
                <span class="menu-stage-chevron">▾</span>
              </div>
              <div class="menu-stage-list" data-stage="list"></div>
            </div>
          </div>
          <div class="menu-row" data-menu="start-row">
          <div class="menu-start-button" data-menu="start" data-i18n="menu.start.newGame">NEW GAME</div>
          <span class="menu-enter-hint" data-menu="start-hint" data-i18n="menu.start.hint">Enter ↵</span>
        </div>
        <div class="menu-row" data-menu="controls">
          <div class="menu-controls-button" data-i18n="menu.controls">⚙ CONTROLS</div>
          <span class="menu-enter-hint" data-menu="controls-hint" data-i18n="menu.controls.hint">Enter ↵</span>
        </div>
        <div class="menu-controls">
          <span data-i18n="menu.nav.select">↑ ↓ Select</span>
          <span data-i18n="menu.nav.change">← → Change</span>
        </div>
        <div class="menu-hiscore">
          <span data-i18n="menu.hiscoreLabel">High Score:</span> <span data-menu="hiscore">0</span>
        </div>
      </div>
    `

    // Populate difficulty options
    const diffContainer = screen.querySelector('[data-difficulty="options"]')!
    for (const key of DIFFICULTY_KEYS) {
      const diff = DIFFICULTIES[key]
      const opt = createElement('div', 'menu-option')
      opt.dataset.value = key
      opt.textContent = t(`difficulty.${key}`) || diff.name
      opt.addEventListener('click', () => this.actions?.selectDifficulty(key))
      diffContainer.appendChild(opt)
    }

    // Populate theme options
    const themeContainer = screen.querySelector('[data-theme="options"]')!
    for (const def of THEME_DEFINITIONS) {
      const opt = createElement('div', 'menu-option')
      opt.dataset.value = def.key
      opt.textContent = t(`theme.${def.key}`) || def.name
      opt.addEventListener('click', () => this.actions?.selectTheme(def.key))
      themeContainer.appendChild(opt)
    }

    // Populate language options — native names, cycleable via the menu.
    const langContainer = screen.querySelector('[data-language="options"]')!
    for (const code of i18n.available) {
      const opt = createElement('div', 'menu-option')
      opt.dataset.value = code
      opt.textContent = i18n.name(code)
      opt.addEventListener('click', () => this.actions?.selectLanguage(code))
      langContainer.appendChild(opt)
    }

    // Stage dropdown — populate and wire
    const stageList = screen.querySelector('[data-stage="list"]') as HTMLElement | null
    const stageTrigger = screen.querySelector('[data-stage="trigger"]') as HTMLElement | null
    if (stageList) {
      for (let i = 0; i < STAGES.length; i++) {
        const item = createElement('div', 'menu-stage-item')
        item.dataset.stageIndex = String(i)
        item.innerHTML = `<span class="menu-stage-item-num">${String(i + 1).padStart(2, '0')}</span><span class="menu-stage-item-name">${localizedStageName(i)}</span>`
        item.addEventListener('click', (e) => {
          e.stopPropagation()
          this.actions?.selectStage(i)
          this.closeStageDropdown()
        })
        stageList.appendChild(item)
      }
    }
    stageTrigger?.addEventListener('click', (e) => {
      e.stopPropagation()
      this.toggleStageDropdown()
    })
    // Close dropdown when clicking outside
    screen.addEventListener('click', () => this.closeStageDropdown())

    // Start button — mouse equivalent of Enter/Space (new game)
    const startBtn = screen.querySelector('[data-menu="start"]') as HTMLElement | null
    startBtn?.addEventListener('click', () => this.actions?.start())

    // Resume row — mouse equivalent of the default confirm when a manual
    // snapshot exists (routes through Game.menuResume → recovery.beginLoad).
    const resumeRow = screen.querySelector('[data-menu="resume"]') as HTMLElement | null
    resumeRow?.addEventListener('click', () => this.actions?.resume())

    // Open the controls panel
    const controlsBtn = screen.querySelector('[data-menu="controls"]') as HTMLElement | null
    if (controlsBtn) {
      controlsBtn.addEventListener('click', () => this.openControlsPanel())
    }

    return screen
  }

  /**
   * Register the menu action callbacks (mouse support). Called once from
   * Game so clicks on the start screen route through the same World-mutating
   * code paths as keyboard input. The UI layer itself stays read-only.
   */
  initMenuActions(actions: MenuActions): void {
    this.actions = actions
  }

  /** The RESUME row's cached target (needed by UIManager.refreshText bridging). */
  get currentResumeTarget(): { stage: number; stageName: string; score: number } | null {
    return this.resumeTarget
  }

  /**
   * Tell the menu about the last manually-saved snapshot (called once after
   * boot hydration). When one exists the start screen shows a prominent
   * RESUME row (the default highlighted action) and relabels the bottom button
   * to NEW GAME; when none exists the resume row is hidden and the original
   * start behaviour is preserved.
   */
  setResumeTarget(target: { stage: number; stageName: string; score: number } | null): void {
    this.resumeTarget = target
    this.hasResume = !!target
    this.el.classList.toggle('has-resume', this.hasResume)

    if (this.resumeStageEl) {
      this.resumeStageEl.textContent = target
        ? t('menu.resume.stageFormat', { n: String(target.stage + 1).padStart(2, '0') })
        : t('menu.resume.stageFormat', { n: '01' })
    }
    if (this.resumeInfoEl) {
      this.resumeInfoEl.textContent = target
        ? t('menu.resume.infoDetailed', {
            stage: String(target.stage + 1),
            name: localizedStageName(target.stage),
            score: target.score,
          })
        : t('menu.resume.info')
    }
    if (this.startBtnEl) {
      this.startBtnEl.textContent = this.hasResume
        ? t('menu.start.newGame')
        : t('menu.start.startGame')
    }
  }

  /**
   * Re-localize the parts of the menu built once from config/live data:
   * difficulty/theme option labels, language highlight, stage dropdown items,
   * and the RESUME-target strings. Called from UIManager.refreshText on boot
   * and whenever the active locale changes.
   */
  refreshLocalized(): void {
    // Difficulty / theme option labels are built once from config; re-apply
    // the catalog strings so they follow the active locale.
    for (const opt of this.diffOptions) {
      const key = opt.dataset.value
      if (key) {
        const label = t(`difficulty.${key}`)
        opt.textContent = label === `difficulty.${key}` ? DIFFICULTIES[key].name : label
      }
    }
    for (const opt of this.themeOptions) {
      const key = opt.dataset.value
      if (key) {
        const label = t(`theme.${key}`)
        opt.textContent = label === `theme.${key}` ? key : label
      }
    }
    // Language option labels are native names — no re-localization needed,
    // but the selected highlight must follow the active locale.
    this.updateLanguageHighlight()
    // Stage list + resume-target names are built once; re-localize them so a
    // live language switch updates the menu without a full rebuild.
    this.refreshStageList()
    if (this.resumeTarget) this.setResumeTarget(this.resumeTarget)
  }

  /** Cache the menu DOM elements (once, after the el is in the document —
   *  querySelector works either way; called by UIManager right after build). */
  cacheElements(): void {
    this.diffOptions = Array.from(
      this.el.querySelectorAll('[data-difficulty="options"] .menu-option'),
    ) as HTMLElement[]
    this.themeOptions = Array.from(
      this.el.querySelectorAll('[data-theme="options"] .menu-option'),
    ) as HTMLElement[]
    this.langOptions = Array.from(
      this.el.querySelectorAll('[data-language="options"] .menu-option'),
    ) as HTMLElement[]
    this.rows = Array.from(this.el.querySelectorAll('.menu-row')) as HTMLElement[]
    this.stageValue = this.el.querySelector('[data-stage="value"]')
    this.stageNameEl = this.el.querySelector('[data-stage="name"]')
    this.stageDropdown = this.el.querySelector('[data-stage="dropdown"]')
    this.hiScoreEl = this.el.querySelector('[data-menu="hiscore"]')
    this.resumeStageEl = this.el.querySelector('[data-menu="resume-stage"]')
    this.resumeInfoEl = this.el.querySelector('[data-menu="resume-info"]')
    this.startBtnEl = this.el.querySelector('[data-menu="start"]')
    this.resumeHint = this.el.querySelector('[data-menu="resume-hint"]')
    this.startHint = this.el.querySelector('[data-menu="start-hint"]')
    this.controlsHint = this.el.querySelector('[data-menu="controls-hint"]')
  }

  /** Re-apply localized names to the (once-built) menu stage dropdown items. */
  private refreshStageList(): void {
    const list = this.el.querySelector('[data-stage="list"]')
    if (!list) return
    list.querySelectorAll<HTMLElement>('.menu-stage-item').forEach((item) => {
      const idx = Number(item.dataset.stageIndex)
      const nameEl = item.querySelector<HTMLElement>('.menu-stage-item-name')
      if (nameEl && !Number.isNaN(idx)) nameEl.textContent = localizedStageName(idx)
    })
  }

  /** Highlight the active language option in the LANGUAGE menu row. */
  private updateLanguageHighlight(): void {
    for (const opt of this.langOptions) {
      opt.classList.toggle('selected', opt.dataset.value === i18n.locale)
    }
  }

  // ---- Stage dropdown ----

  private toggleStageDropdown(): void {
    this.dropdownOpen = !this.dropdownOpen
    this.stageDropdown?.classList.toggle('open', this.dropdownOpen)
  }

  closeStageDropdown(): void {
    if (!this.dropdownOpen) return
    this.dropdownOpen = false
    this.stageDropdown?.classList.remove('open')
  }

  /** Per-frame menu highlight sync — only writes DOM when values change. */
  syncWorld(world: World): void {
    // Highlight selected difficulty — only when changed
    if (world.difficultyKey !== this.lastDifficultyKey) {
      this.lastDifficultyKey = world.difficultyKey
      for (const opt of this.diffOptions) {
        opt.classList.toggle('selected', opt.dataset.value === world.difficultyKey)
      }
    }

    // Highlight selected theme — only when changed
    if (world.themeKey !== this.lastThemeKeyMenu) {
      this.lastThemeKeyMenu = world.themeKey
      for (const opt of this.themeOptions) {
        opt.classList.toggle('selected', opt.dataset.value === world.themeKey)
      }
    }

    // Highlight selected language (also re-applied on locale change via
    // refreshText, so this stays correct after a click or menu-cycle).
    this.updateLanguageHighlight()

    // Highlight selected menu row (cursor) — only when changed.
    // Row indices come from ROW_ORDER below — the single source for the
    // template-row ↔ world.ui.menuCursor contract.
    if (world.ui.menuCursor !== this.lastMenuCursor) {
      this.lastMenuCursor = world.ui.menuCursor
      // Close stage dropdown when cursor moves away from STAGE row
      if (world.ui.menuCursor !== this.rowIndexFor('stage')) {
        this.closeStageDropdown()
      }
      for (const row of this.rows) {
        const idx = this.rowIndexFor(row.dataset.menu ?? '')
        row.classList.toggle('selected', idx === world.ui.menuCursor)
      }
      // Show ENTER hint only on RESUME and NEW GAME rows when selected
      if (this.resumeHint) {
        this.resumeHint.classList.toggle('visible', this.hasResume && world.ui.menuCursor === 0)
      }
      if (this.startHint) {
        this.startHint.classList.toggle(
          'visible',
          world.ui.menuCursor === this.rowIndexFor('start-row'),
        )
      }
      if (this.controlsHint) {
        this.controlsHint.classList.toggle(
          'visible',
          world.ui.menuCursor === this.rowIndexFor('controls'),
        )
      }
    }

    // Stage selector display — only when changed
    if (world.ui.selectedStage !== this.lastSelectedStage) {
      this.lastSelectedStage = world.ui.selectedStage
      if (this.stageValue) {
        this.stageValue.textContent = `${String(world.ui.selectedStage + 1).padStart(2, '0')} / ${String(STAGES.length).padStart(2, '0')}`
      }
      if (this.stageNameEl) {
        this.stageNameEl.textContent = localizedStageName(world.ui.selectedStage) ?? ''
      }
    }

    // High score — only when changed
    if (world.highScore !== this.lastHighScoreMenu) {
      this.lastHighScoreMenu = world.highScore
      if (this.hiScoreEl) {
        this.hiScoreEl.textContent = String(world.highScore)
      }
    }
  }
}
