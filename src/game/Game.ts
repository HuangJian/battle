import { World } from './World'
import { Simulation } from './Simulation'
import { Input, DEFAULT_KEYS } from './Input'
import { Renderer } from '../render/Renderer'
import { AudioManager } from '../audio/AudioManager'
import { DIFFICULTIES, DIFFICULTY_KEYS } from '../config/difficulty'
import { TICK_MS, CANVAS_WIDTH, CANVAS_HEIGHT } from '../constants'
import type { GameSettings } from '../types'

const SETTINGS_KEY = 'bc_settings'

/**
 * Game — top-level orchestrator.
 * Owns the game loop, wires all systems together.
 */
export class Game {
  canvas: HTMLCanvasElement
  ctx: CanvasRenderingContext2D
  world: World
  input: Input
  simulation: Simulation
  renderer: Renderer
  audio: AudioManager

  private lastTime = 0
  private accumulator = 0
  private running = false
  private rafId = 0

  settings: GameSettings
  private difficultyIndex = 1 // classic

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    canvas.width = CANVAS_WIDTH
    canvas.height = CANVAS_HEIGHT
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('Canvas 2D not supported')
    this.ctx = ctx

    this.settings = this.loadSettings()
    this.world = new World()
    this.input = new Input(this.settings.keys)
    this.simulation = new Simulation(this.world, this.input)
    this.renderer = new Renderer(canvas)
    this.audio = new AudioManager()
    this.audio.setVolume(this.settings.volume)
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

    // Process audio events
    const events = this.world.consumeEvents()
    this.audio.handleEvents(events)

    // Render
    this.renderer.render(this.world)

    // Clear per-frame input state (moved here from Simulation.tick
    // so menu/victory states also get cleared)
    this.input.endFrame()

    this.rafId = requestAnimationFrame(this.loop)
  }

  // ---- State Input ----

  private handleStateInput(): void {
    const w = this.world

    if (w.state === 'menu') {
      // Difficulty selection
      if (
        this.input.isUpPressed() ||
        this.input.wasPressed('ArrowLeft') ||
        this.input.wasPressed('KeyA')
      ) {
        this.difficultyIndex =
          (this.difficultyIndex - 1 + DIFFICULTY_KEYS.length) % DIFFICULTY_KEYS.length
        w.difficultyKey = DIFFICULTY_KEYS[this.difficultyIndex]
        w.difficulty = DIFFICULTIES[w.difficultyKey]
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
      }
      if (
        this.input.isDownPressed() ||
        this.input.wasPressed('ArrowRight') ||
        this.input.wasPressed('KeyD')
      ) {
        this.difficultyIndex = (this.difficultyIndex + 1) % DIFFICULTY_KEYS.length
        w.difficultyKey = DIFFICULTY_KEYS[this.difficultyIndex]
        w.difficulty = DIFFICULTIES[w.difficultyKey]
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
      }
      // Start game
      if (this.input.isConfirmPressed()) {
        this.audio.init()
        this.audio.resume()
        this.audio.playMenuSelect()
        w.startGame(w.difficultyKey, w.themeKey)
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
    this.audio.playMenuSelect()
  }

  // ---- Settings ----

  private loadSettings(): GameSettings {
    const defaults: GameSettings = {
      volume: 0.3,
      difficulty: 'classic',
      theme: 'classic',
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
