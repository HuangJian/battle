import { World } from './World'
import { Simulation } from './Simulation'
import { Input, DEFAULT_KEYS } from './Input'
import { PresentationLayer } from '../presentation/PresentationLayer'
import { AudioManager } from '../audio/AudioManager'
import { DIFFICULTIES, DIFFICULTY_KEYS } from '../config/difficulty'
import { THEMES, DEFAULT_THEME } from '../config/theme'
import { STAGES } from '../config/stages'
import { TICK_MS } from '../constants'
import type { GameSettings } from '../types'

const SETTINGS_KEY = 'bc_settings'
const THEME_KEYS = Object.keys(THEMES)

/**
 * Game — top-level orchestrator.
 * Owns the game loop, wires all systems together.
 */
export class Game {
  world: World
  input: Input
  simulation: Simulation
  presentation: PresentationLayer
  audio: AudioManager

  private lastTime = 0
  private accumulator = 0
  private running = false
  private rafId = 0

  settings: GameSettings
  private difficultyIndex = 1 // classic
  private themeIndex = 0

  constructor(root: HTMLElement) {
    this.settings = this.loadSettings()
    this.world = new World()
    this.input = new Input(this.settings.keys)
    this.simulation = new Simulation(this.world, this.input)
    this.presentation = new PresentationLayer(root)
    this.audio = new AudioManager()
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

  start(): void {
    this.input.attach(window)
    this.running = true
    this.lastTime = performance.now()
    this.loop(this.lastTime)
  }

  stop(): void {
    this.running = false
    cancelAnimationFrame(this.rafId)
    this.input.detach(window)
  }

  private loop = (time: number): void => {
    if (!this.running) return

    const dt = Math.min(time - this.lastTime, 100) // cap at 100ms
    this.lastTime = time
    this.accumulator += dt

    // Handle menu/game state input
    this.handleStateInput()

    // Fixed timestep simulation
    let steps = 0
    while (this.accumulator >= TICK_MS && steps < 5) {
      if (
        this.world.state === 'playing' ||
        this.world.state === 'stageclear' ||
        this.world.state === 'gameover'
      ) {
        this.simulation.tick()
      }
      this.accumulator -= TICK_MS
      steps++
    }

    // Process events — pass to both audio and presentation
    const events = this.world.consumeEvents()
    this.audio.handleEvents(events)
    this.presentation.handleEvents(events)

    // Render
    this.presentation.render(this.world, dt)

    // Clear per-frame input state
    this.input.endFrame()

    this.rafId = requestAnimationFrame(this.loop)
  }

  // ---- State Input ----

  private handleStateInput(): void {
    const w = this.world

    if (w.state === 'menu') {
      const rowCount = 3
      // Move cursor between rows (DIFFICULTY / THEME / STAGE)
      if (this.input.isUpPressed()) {
        w.menuCursor = (w.menuCursor - 1 + rowCount) % rowCount
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
      }
      if (this.input.isDownPressed()) {
        w.menuCursor = (w.menuCursor + 1) % rowCount
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
      }
      // Change value of the selected row
      const left = this.input.wasPressed('ArrowLeft') || this.input.wasPressed('KeyA')
      const right = this.input.wasPressed('ArrowRight') || this.input.wasPressed('KeyD')
      if (left || right) {
        const dir = left ? -1 : 1
        if (w.menuCursor === 0) {
          this.difficultyIndex =
            (this.difficultyIndex + dir + DIFFICULTY_KEYS.length) % DIFFICULTY_KEYS.length
          w.difficultyKey = DIFFICULTY_KEYS[this.difficultyIndex]
          w.difficulty = DIFFICULTIES[w.difficultyKey]
        } else if (w.menuCursor === 1) {
          this.themeIndex = (this.themeIndex + dir) % THEME_KEYS.length
          w.themeKey = THEME_KEYS[this.themeIndex]
          w.theme = THEMES[w.themeKey]
        } else {
          w.selectedStage = (w.selectedStage + dir + STAGES.length) % STAGES.length
        }
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
      }
      // Theme shortcut (T key)
      if (this.input.wasPressed('KeyT')) {
        this.themeIndex = (this.themeIndex + 1) % THEME_KEYS.length
        w.themeKey = THEME_KEYS[this.themeIndex]
        w.theme = THEMES[w.themeKey]
        w.menuCursor = 1
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
      }
      // Start game at the selected stage
      if (this.input.isConfirmPressed()) {
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
        w.startGame(w.difficultyKey, w.themeKey, w.selectedStage)
        this.saveSettings()
      }
      return
    }

    if (w.state === 'playing' || w.state === 'paused') {
      if (this.input.isPausePressed()) {
        this.simulation.togglePause()
        this.audio.playPause()
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

  resetToMenu(): void {
    this.world.state = 'menu'
    this.world.player = null
    this.world.tanks = []
    this.world.bullets = []
    this.world.powerUps = []
    this.world.explosions = []
    this.world.popups = []
    this.world.spawnQueue = []
    this.presentation.reset()
    this.audio.playMenuSelect()
  }

  // ---- Settings ----

  private loadSettings(): GameSettings {
    const defaults: GameSettings = {
      volume: 0.3,
      difficulty: 'classic',
      theme: DEFAULT_THEME,
      screenScale: 1,
      keys: { ...DEFAULT_KEYS },
    }

    try {
      const raw = localStorage.getItem(SETTINGS_KEY)
      if (raw) {
        const saved = JSON.parse(raw)
        return { ...defaults, ...saved, keys: { ...defaults.keys, ...saved.keys } }
      }
    } catch {
      /* ignore */
    }
    return defaults
  }

  saveSettings(): void {
    this.settings.difficulty = this.world.difficultyKey
    this.settings.theme = this.world.themeKey
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(this.settings))
    } catch {
      /* ignore */
    }
  }

  setVolume(v: number): void {
    this.settings.volume = v
    this.audio.setVolume(v)
    this.saveSettings()
  }
}
