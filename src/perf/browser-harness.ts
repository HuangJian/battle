/**
 * browser-harness.ts — in-browser performance harness for Battle City Web.
 *
 * Drives the REAL Game (real Simulation + real Renderer) and measures the
 * numbers that actually decide "60 FPS with the fan off":
 *
 *   - Loop FPS        : read from `game.fps`. Action states (playing / stageclear
 *                       / recovery) run the vsync rAF loop at ~60; static states
 *                       (menu / paused / gameover / victory) are 0-loop idle — the
 *                       loop is fully event-driven, so FPS reads 0 there by design
 *                       and that is the *good* outcome (fan off).
 *   - Frame cost (ms) : per-frame JS + render time, captured by wrapping
 *                       requestAnimationFrame around the game's loop. p95 must
 *                       stay < 16.67 ms to hold 60 FPS.
 *   - Slow frames     : frames whose work exceeded 16.67 ms (60 FPS budget).
 *   - Long tasks      : real browser `longtask` entries (> 50 ms main-thread
 *                       blocks) — the #1 "fan spins up" signal.
 *   - Busy %          : estimated main-thread time spent in game frames per
 *                       second — lower is better for battery / fan.
 *
 * Scenarios:
 *   - "Menu idle"  : sit on the menu (0-loop idle should engage — FPS 0, fan off).
 *   - "Active"     : start a game, no extra load (baseline 60 FPS).
 *   - "Stress"     : start a game and inject many tanks + bullets (worst case).
 *
 * How to run:  `bun run dev`, then open http://localhost:8956/perf.html
 *
 * This is the source of truth for the energy goal — the headless sim bench
 * can't see rendering, but this can.
 */
import { Game } from '../game/Game'
import { genId } from '../game/World'
import { BULLET, CELL, FIELD } from '../constants'

// --- Wrap rAF to time each game frame's JS + render cost ---------------------
const frameSamples: number[] = []
const MAX_SAMPLES = 1200
const origRAF = window.requestAnimationFrame.bind(window)
window.requestAnimationFrame = (cb: FrameRequestCallback): number =>
  origRAF((t: number) => {
    const s = performance.now()
    cb(t)
    const d = performance.now() - s
    frameSamples.push(d)
    if (frameSamples.length > MAX_SAMPLES) frameSamples.splice(0, frameSamples.length - MAX_SAMPLES)
  }) as unknown as number

// --- Long-task observer (real main-thread jank signal) -----------------------
let longTasks = 0
try {
  new PerformanceObserver((list) => {
    longTasks += list.getEntries().length
  }).observe({ entryTypes: ['longtask'] })
} catch {
  /* longtask not supported */
}

const LOW_POWER = new Set(['menu', 'paused', 'gameover', 'victory'])

let game: Game | null = null
let topUpTimer: ReturnType<typeof setInterval> | null = null

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
async function boot(): Promise<void> {
  const root = document.getElementById('game-root')!
  game = new Game(root)
  await game.start()
  setInterval(report, 1000)
  report()
}

// ---------------------------------------------------------------------------
// Stress injection (mirrors the headless bench, but on the live game)
// ---------------------------------------------------------------------------
const ENEMY_KINDS = ['basic', 'fast', 'power', 'armor'] as const

function spawnStress(enemies: number, bullets: number): void {
  if (!game) return
  const w = game.world
  const spots: Array<[number, number]> = []
  for (let r = 0; r < 26; r += 2) for (let c = 0; c < 13; c += 2) spots.push([c * CELL, r * CELL])
  for (let i = 0; i < enemies; i++) {
    const [x, y] = spots[i % spots.length]
    const t = w.createTank(ENEMY_KINDS[i % ENEMY_KINDS.length], x, y, 'down')
    t.alive = true
    w.tanks.push(t)
  }
  for (let i = 0; i < bullets; i++) {
    const dirs = ['up', 'down', 'left', 'right'] as const
    w.addBullet({
      id: genId(),
      x: (i * 37) % (FIELD - BULLET),
      y: (i * 53) % (FIELD - BULLET),
      w: BULLET,
      h: BULLET,
      dir: dirs[i % 4],
      alive: true,
      ownerId: -1 - i,
      ownerKind: 'basic',
      isPlayer: i % 2 === 0,
      speed: 6,
      power: 1,
      damage: 100,
    })
  }
}

function startTopUp(bullets: number): void {
  if (topUpTimer) clearInterval(topUpTimer)
  topUpTimer = setInterval(() => {
    if (!game) return
    const w = game.world
    let alive = 0
    for (const b of w.bullets) if (b.alive) alive++
    let i = 0
    while (alive < bullets) {
      const dirs = ['up', 'down', 'left', 'right'] as const
      w.addBullet({
        id: genId(),
        x: (i * 37) % (FIELD - BULLET),
        y: (i * 53) % (FIELD - BULLET),
        w: BULLET,
        h: BULLET,
        dir: dirs[i % 4],
        alive: true,
        ownerId: -1000 - i,
        ownerKind: 'basic',
        isPlayer: i % 2 === 0,
        speed: 6,
        power: 1,
        damage: 100,
      })
      alive++
      i++
    }
  }, 100)
}

function stopTopUp(): void {
  if (topUpTimer) clearInterval(topUpTimer)
  topUpTimer = null
}

// ---------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------
function pct(sorted: number[], p: number): number {
  if (!sorted.length) return 0
  const i = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1))
  return sorted[i]
}

interface Report {
  state: string
  mode: string
  fps: number
  frameP50: number
  frameP95: number
  frameP99: number
  slowFrames: number
  busyPct: number
  longTasks: number
}

const history: Report[] = []

function report(): void {
  if (!game) return
  const w = game.world
  const state = w.state
  const mode = LOW_POWER.has(state) ? '0-loop idle (event-driven)' : 'action (rAF 60 FPS)'
  const fps = game.fps

  const s = [...frameSamples].sort((a, b) => a - b)
  const frameP50 = pct(s, 50)
  const frameP95 = pct(s, 95)
  const frameP99 = pct(s, 99)
  const slowFrames = frameSamples.filter((d) => d > 16.67).length
  const busyPct = (frameSamples.reduce((a, b) => a + b, 0) / Math.max(1, frameSamples.length)) * fps

  const r: Report = {
    state,
    mode,
    fps,
    frameP50,
    frameP95,
    frameP99,
    slowFrames,
    busyPct,
    longTasks,
  }
  history.push(r)
  if (history.length > 60) history.shift()

  // keep only the last second of frame samples for the rolling window
  if (frameSamples.length > fps && fps > 0)
    frameSamples.splice(0, frameSamples.length - Math.min(MAX_SAMPLES, fps))

  const overBudget = frameP95 >= 16.67
  const held60 = fps >= 58 && !overBudget
  const verdict = LOW_POWER.has(state)
    ? `IDLE — 0-loop idle, ~${busyPct.toFixed(1)}% busy (fan should be off)`
    : held60
      ? `OK — 60 FPS held, frame p95 ${frameP95.toFixed(2)}ms`
      : overBudget
        ? `WARN — ${fps} FPS, frame p95 ${frameP95.toFixed(2)}ms OVER 16.67ms budget`
        : `WARN — ${fps} FPS, frame p95 ${frameP95.toFixed(2)}ms (loop not armed? re-run scenario)`

  const el = document.getElementById('readout')!
  el.textContent =
    `state      : ${state}  (${mode})\n` +
    `loop FPS   : ${fps}\n` +
    `frame p50  : ${frameP50.toFixed(3)} ms\n` +
    `frame p95  : ${frameP95.toFixed(3)} ms\n` +
    `frame p99  : ${frameP99.toFixed(3)} ms\n` +
    `slow frames: ${slowFrames} (>16.67ms in window)\n` +
    `busy %     : ${busyPct.toFixed(1)}% of 1s (game frames)\n` +
    `long tasks : ${longTasks}\n` +
    `\n${verdict}`

  document.getElementById('verdict')!.textContent = verdict
  document.getElementById('verdict')!.className = held60 || LOW_POWER.has(state) ? 'ok' : 'warn'
}

function exportJSON(): void {
  const blob = new Blob([JSON.stringify(history, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `battle-perf-${Date.now()}.json`
  a.click()
  URL.revokeObjectURL(url)
}

// ---------------------------------------------------------------------------
// Wire up controls (created in JS so perf.html stays tiny)
// ---------------------------------------------------------------------------
function buildPanel(): void {
  const panel = document.createElement('div')
  panel.id = 'perf-panel'
  panel.innerHTML = `
    <h1>Battle City Web — Performance Harness</h1>
    <div class="row">
      <button id="btn-menu">Menu idle</button>
      <button id="btn-active">Active (baseline)</button>
      <button id="btn-stress">Stress (32e/120b)</button>
      <button id="btn-stress2">Stress (64e/240b)</button>
      <button id="btn-export">Export JSON</button>
    </div>
    <pre id="readout"></pre>
    <div id="verdict"></div>
    <p class="hint">Run with <code>bun run dev</code> → open <code>/perf.html</code>. Watch the
    verdict while the menu sits idle (low-power) vs during active/stress play.</p>
  `
  document.body.appendChild(panel)

  document.getElementById('btn-menu')!.onclick = () => {
    // Return to menu: reset to menu state to engage 0-loop idle cadence.
    if (game) {
      ;(game as unknown as { resetToMenu: () => void }).resetToMenu()
      stopTopUp()
      // Re-evaluate the loop driver for the (now static) state — harmless for
      // the idle path (it just confirms nothing is scheduled), and keeps the
      // harness's view consistent if the loop was previously running.
      game.requestFrame()
    }
  }
  document.getElementById('btn-active')!.onclick = () => {
    if (game) {
      game.world.startGame('classic', 'default', 0)
      stopTopUp()
      // In the 0-loop idle architecture, changing state to an action state does
      // NOT auto-start the loop — explicitly re-arm it so FPS is measured.
      game.requestFrame()
    }
  }
  document.getElementById('btn-stress')!.onclick = () => {
    if (game) {
      game.world.startGame('classic', 'default', 0)
      spawnStress(32, 120)
      startTopUp(120)
      game.requestFrame()
    }
  }
  document.getElementById('btn-stress2')!.onclick = () => {
    if (game) {
      game.world.startGame('classic', 'default', 0)
      spawnStress(64, 240)
      startTopUp(240)
      game.requestFrame()
    }
  }
  document.getElementById('btn-export')!.onclick = exportJSON
}

buildPanel()
void boot()
