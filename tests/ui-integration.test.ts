import { describe, it, expect } from 'bun:test'
import { UIManager } from '../src/presentation/ui/UIManager'
import type { MenuActions } from '../src/presentation/ui/MenuScreen'
import { MenuScreen } from '../src/presentation/ui/MenuScreen'
import { World } from '../src/game/World'
import { DEFAULT_PAD_BINDINGS, PAD_ACTIONS } from '../src/game/settings'
import { i18n, t } from '../src/i18n'

/**
 * Full UI boot integration test under happy-dom's real DOM
 * (DECISIONS §2026-09-08-ps2-happydom). This is the seam the previous
 * hand-rolled fake DOM could not cover: UIManager assembles all five UI
 * slices through bare `document.createElement` / `document.body` calls, so
 * only a real DOM emulator can exercise the whole tree, the boot
 * localization pass, and menu → controls click routing end-to-end.
 */

function makeStubActions(): MenuActions & { calls: Array<[string, ...unknown[]]> } {
  const calls: Array<[string, ...unknown[]]> = []
  const record =
    (name: string) =>
    (...args: unknown[]): void => {
      calls.push([name, ...args])
    }
  return {
    calls,
    selectDifficulty: record('selectDifficulty'),
    selectTheme: record('selectTheme'),
    selectLanguage: record('selectLanguage'),
    cycleStage: record('cycleStage'),
    selectStage: record('selectStage'),
    start: record('start'),
    resume: record('resume'),
    openControls: record('openControls'),
  }
}

function mount(): { root: HTMLElement; ui: UIManager } {
  const root = document.createElement('div')
  document.body.appendChild(root)
  const ui = new UIManager(root)
  return { root, ui }
}

describe('UIManager full boot under happy-dom', () => {
  it('assembles the whole UI tree and lands on the menu', () => {
    const { root } = mount()

    // Canvas, HUD bar, menu screen all present in the tree.
    expect(root.querySelector('.game-canvas')).toBeInstanceOf(HTMLCanvasElement)
    expect(root.querySelector('.hud-bar')).not.toBeNull()
    const menu = root.querySelector('.ui-menu') as HTMLElement
    expect(menu).not.toBeNull()
    expect(menu.classList.contains('active')).toBe(true)

    // On the menu the HUD and the footer hints are hidden.
    expect(root.querySelector('.hud-bar')!.classList.contains('visible')).toBe(false)
    expect(root.querySelector('.footer')!.classList.contains('visible')).toBe(false)

    // Boot localization reached every data-i18n node: switch to zh and the
    // footer hint follows (subscribe → refreshText → localizeRoot).
    const pauseHint = root.querySelector('[data-i18n="footer.pause"]')!
    i18n.setLocale('zh')
    expect(pauseHint.textContent).toBe('暂停')
    i18n.setLocale('en')
    expect(pauseHint.textContent).toBe(t('footer.pause'))

    // Controls modal exists in the tree, inert until opened.
    const controls = root.querySelector('.ui-controls') as HTMLElement
    expect(controls).not.toBeNull()
    expect(controls.classList.contains('active')).toBe(false)

    // Constructor is idempotent-safe: booting a second manager on a fresh
    // root must not touch the first manager's tree.
    root.remove()
  })

  it('drives HUD + footer visibility from the world state via update()', () => {
    const { root, ui } = mount()
    const world = new World()

    world.state = 'playing'
    ui.update(world)
    expect(root.querySelector('.hud-bar')!.classList.contains('visible')).toBe(true)
    expect(root.querySelector('.footer')!.classList.contains('visible')).toBe(true)

    world.state = 'menu'
    ui.update(world)
    expect(root.querySelector('.hud-bar')!.classList.contains('visible')).toBe(false)
    expect(root.querySelector('.footer')!.classList.contains('visible')).toBe(false)

    root.remove()
  })

  it('routes real menu clicks: option selection and controls open', () => {
    const { root, ui } = mount()
    const actions = makeStubActions()
    ui.initMenuActions(actions)

    // Click the "hard" difficulty option → selectDifficulty('hard').
    const diffOption = root.querySelector('.menu-option[data-value="hard"]') as HTMLElement
    expect(diffOption).not.toBeNull()
    diffOption.click()
    expect(actions.calls).toContainEqual(['selectDifficulty', 'hard'])

    // Click the CONTROLS row → the controls modal opens (gets .active).
    const controlsBtn = root.querySelector('[data-menu="controls"]') as HTMLElement
    expect(controlsBtn).not.toBeNull()
    controlsBtn.click()
    expect(root.querySelector('.ui-controls')!.classList.contains('active')).toBe(true)

    root.remove()
  })

  it('renders the gamepad legend chips into the menu DOM once pads are wired', () => {
    const { root, ui } = mount()
    const menu = (ui as unknown as { menu: MenuScreen }).menu
    menu.setPadBindings(DEFAULT_PAD_BINDINGS)

    const rows = root.querySelector('[data-menu="pad-legend-rows"]')!
    const chips = rows.querySelectorAll('kbd')
    expect(chips.length).toBe(PAD_ACTIONS.length)
    // Every chip carries the localized action name + a readable button label.
    for (const chip of Array.from(chips)) {
      expect(chip.textContent).toContain('Pad ')
    }

    root.remove()
  })
})
