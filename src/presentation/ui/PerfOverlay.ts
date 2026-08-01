import type { World } from '../../game/World'
import type { GameRenderer } from '../renderer/GameRenderer'
import type { ParticleSystem } from '../ParticleSystem'

/** Per-frame timing + state samples fed to {@link PerfOverlay.update}. */
export interface PerfTimings {
  /** Loop FPS (from `Game.fps`). */
  fps: number
  /** Total loop-body wall time (ms). */
  frameMs: number
  /** Simulation tick wall time (ms). */
  simMs: number
  /** Presentation render wall time (ms). */
  renderMs: number
  /** HUD update wall time (ms). */
  uiMs: number
  /** Whether Performance Mode (DPR + render-FPS cap) is on. */
  perfMode: boolean
}

/**
 * Warn thresholds for the 60 FPS performance budget. Frame/Sim/Render are
 * compared against their p95 (rolling) values; Draw calls against the
 * instantaneous per-frame count. When a metric is sustained above its
 * threshold it is flagged "over budget" and recorded in the breach log.
 */
const THRESHOLDS = {
  frameMs: 16.67, // 60 FPS frame budget
  simMs: 5, // simulation tick wall time
  renderMs: 12, // presentation render wall time
  drawCalls: 600, // on-screen draw primitives per frame
} as const

interface BreachRecord {
  t: number // epoch ms
  metric: string // human label
  value: string // formatted value (number + unit)
  limit: string // formatted limit (number + unit)
}

const MAX_BREACHES = 40
const BREACH_THROTTLE_MS = 1500

/**
 * Performance Observatory — a developer-only, read-only debug HUD.
 *
 * Surfaces engine-computed numbers plus a few cheap per-frame counters as a
 * compact, fixed-position HTML panel (top-right of the viewport). Toggled with
 * `Alt+D` (also via the Control Center). It never mutates the World and never
 * draws on the game canvas (AGENTS §2.5 — UI is HTML/CSS).
 *
 * Every timing probe is gated on {@link active}, so when the overlay is off it
 * costs nothing — no wrapped methods, no `performance.now()` calls, no DOM
 * writes (Plan: Performance-Observatory.md §3 "overlay introduces negligible
 * overhead").
 *
 * When a metric stays above its warn threshold it is highlighted in red, a
 * breach is recorded (throttled), the panel border turns red, and a Copy
 * report button lets a developer hand the captured diagnostics to an agent.
 */
export class PerfOverlay {
  /** Whether the overlay is currently visible. */
  private _active = false
  /** The root panel element (caller appends to the DOM). */
  readonly el: HTMLElement
  /** Called after a report is copied (e.g. to surface a toast). */
  onCopied: (() => void) | null = null

  /** Per-metric value cells, keyed by metric id. */
  private readonly values = new Map<string, HTMLElement>()
  /** The GC metric wrapper (hidden when GC observation is unsupported). */
  private gcMetricEl: HTMLElement | null = null

  // Rolling sample windows (~120 frames) for p95 timing.
  private readonly frameSamples: number[] = []
  private readonly simSamples: number[] = []
  private readonly renderSamples: number[] = []
  private readonly uiSamples: number[] = []
  private readonly maxSamples = 120

  // GC observation (best-effort; V8-only, hidden if unsupported).
  private gcObserver: PerformanceObserver | null = null
  private gcCount = 0
  private gcSupported = false

  // Breach tracking.
  private readonly breaches: BreachRecord[] = []
  private readonly lastBreachAt = new Map<string, number>()
  private breachBadge: HTMLElement
  private copyBtn: HTMLButtonElement
  private copyBtnIcon: HTMLElement

  // Session frame accounting — drives the breach-rate warning gate (only
  // surfaces when over-budget frames exceed 1% of total frames) and the
  // start/end timestamps + frame count recorded in the Copy report.
  private sessionStart = 0
  private sessionFrames = 0
  private sessionBreached = 0

  // Last computed values, kept for the Copy report (refreshed each visible frame).
  private last = {
    fps: 0,
    frame: 0,
    sim: 0,
    render: 0,
    ui: 0,
    idle: 0,
    draws: 0,
    sprites: 0,
    bullets: 0,
    tanks: 0,
    particles: 0,
    gc: 'n/a',
    perfMode: false,
  }

  constructor() {
    this.el = document.createElement('div')
    this.el.className = 'perf-overlay'
    this.el.hidden = true

    const grid = document.createElement('div')
    grid.className = 'perf-grid'

    // Two-column layout — pairs are appended left, then right (plan §3).
    this.addMetric(grid, 'fps', 'FPS')
    this.addMetric(grid, 'frame', 'Frame')
    this.addMetric(grid, 'sim', 'Sim')
    this.addMetric(grid, 'render', 'Render')
    this.addMetric(grid, 'ui', 'UI')
    this.addMetric(grid, 'idle', 'Idle')
    this.addMetric(grid, 'draws', 'Draw calls')
    this.addMetric(grid, 'sprites', 'Sprites')
    this.addMetric(grid, 'bullets', 'Bullets')
    this.addMetric(grid, 'tanks', 'Tanks')
    this.gcMetricEl = this.addMetric(grid, 'gc', 'GC')
    this.addMetric(grid, 'breach', 'Breach')
    this.addMetric(grid, 'quality', 'Quality')
    this.addMetric(grid, 'perf', 'PerfMode')

    this.el.appendChild(grid)

    // Footer: breach badge + Copy report button (for agent debugging).
    const footer = document.createElement('div')
    footer.className = 'perf-footer'
    this.breachBadge = document.createElement('span')
    this.breachBadge.className = 'perf-breach-badge'
    this.breachBadge.hidden = true
    this.copyBtn = document.createElement('button')
    this.copyBtn.className = 'perf-copy'
    this.copyBtn.type = 'button'
    this.copyBtnIcon = document.createElement('span')
    this.copyBtnIcon.className = 'perf-copy-icon'
    this.copyBtnIcon.textContent = '⚠'
    this.copyBtnIcon.hidden = true
    const copyLabel = document.createElement('span')
    copyLabel.textContent = 'Copy report'
    this.copyBtn.append(this.copyBtnIcon, copyLabel)
    this.copyBtn.addEventListener('click', () => this.copyReport())
    footer.append(this.breachBadge, this.copyBtn)
    this.el.appendChild(footer)

    this.setupGcObserver()
  }

  /** True while the overlay is visible (and timing probes are active). */
  get active(): boolean {
    return this._active
  }

  /** Toggle visibility. Disables itself (clearing sample windows) when off. */
  toggle(): void {
    this._active = !this._active
    this.el.hidden = !this._active
    if (this._active) {
      // Fresh session: anchor the report's start time + reset frame counters.
      this.sessionStart = Date.now()
      this.sessionFrames = 0
      this.sessionBreached = 0
    } else {
      this.frameSamples.length = 0
      this.simSamples.length = 0
      this.renderSamples.length = 0
      this.uiSamples.length = 0
      this.lastBreachAt.clear()
      this.breaches.length = 0
      this.sessionFrames = 0
      this.sessionBreached = 0
      this.el.classList.remove('breaching')
      this.breachBadge.hidden = true
      this.copyBtnIcon.hidden = true
      this.copyBtn.classList.remove('has-breaches')
    }
  }

  /**
   * Refresh the panel. Gated on {@link active} by the caller — this is only
   * ever called from the game loop while the overlay is visible, so it is
   * allocation-free in the common (off) case by construction.
   */
  update(world: World, renderer: GameRenderer, particles: ParticleSystem, t: PerfTimings): void {
    if (!this._active) return

    this.push(this.frameSamples, t.frameMs)
    this.push(this.simSamples, t.simMs)
    this.push(this.renderSamples, t.renderMs)
    this.push(this.uiSamples, t.uiMs)

    const fP = percentile(this.frameSamples, 95)
    const sP = percentile(this.simSamples, 95)
    const rP = percentile(this.renderSamples, 95)
    const uP = percentile(this.uiSamples, 95)
    const idle = Math.max(0, fP - sP - rP - uP)

    let aliveBullets = 0
    for (const b of world.bullets) if (b.alive) aliveBullets++
    let aliveTanks = 0
    for (const tk of world.tanks) if (tk.alive) aliveTanks++

    const sprites = world.tanks.length + world.bullets.length + particles.activeCount
    const draws = renderer.debugDrawCalls

    const overFrame = fP > THRESHOLDS.frameMs
    const overSim = sP > THRESHOLDS.simMs
    const overRender = rP > THRESHOLDS.renderMs
    const overDraws = draws > THRESHOLDS.drawCalls

    this.set('fps', String(t.fps), false)
    this.set('frame', fmtMs(fP), overFrame)
    this.set('sim', fmtMs(sP), overSim)
    this.set('render', fmtMs(rP), overRender)
    this.set('ui', fmtMs(uP), false)
    this.set('idle', fmtMs(idle), false)
    this.set('draws', String(draws), overDraws)
    this.set('sprites', String(sprites), false)
    this.set('bullets', String(aliveBullets), false)
    this.set('tanks', String(aliveTanks), false)
    this.set('particles', String(particles.activeCount), false)
    this.set('gc', this.gcSupported ? String(this.gcCount) : 'n/a', false)
    this.set('quality', 'High', false)
    this.set('perf', t.perfMode ? 'ON' : 'OFF', false)

    // Record breaches (throttled so a sustained breach logs ~once / 1.5 s).
    if (overFrame) this.recordBreach('frame', 'Frame p95', fP, ' ms', THRESHOLDS.frameMs)
    if (overSim) this.recordBreach('sim', 'Sim p95', sP, ' ms', THRESHOLDS.simMs)
    if (overRender) this.recordBreach('render', 'Render p95', rP, ' ms', THRESHOLDS.renderMs)
    if (overDraws) this.recordBreach('draws', 'Draw calls', draws, '', THRESHOLDS.drawCalls)

    const active =
      (overFrame ? 1 : 0) + (overSim ? 1 : 0) + (overRender ? 1 : 0) + (overDraws ? 1 : 0)

    // Session frame accounting → drives the breach-rate warning gate. The
    // warning flag (red border / badge / Copy icon) only appears once
    // over-budget frames exceed 1% of the total frames observed this session.
    this.sessionFrames++
    if (active > 0) this.sessionBreached++
    const hasFrames = this.sessionFrames > 0
    const breachRate = hasFrames ? this.sessionBreached / this.sessionFrames : 0
    const showWarn = hasFrames && breachRate > 0.01

    this.el.classList.toggle('breaching', showWarn)
    if (showWarn) {
      this.breachBadge.hidden = false
      this.breachBadge.textContent = `⚠ ${(breachRate * 100).toFixed(1)}% over budget`
    } else {
      this.breachBadge.hidden = true
    }

    // Warning marker on Copy report tracks the same gate.
    this.copyBtnIcon.hidden = !showWarn
    this.copyBtn.classList.toggle('has-breaches', showWarn)

    // Breach-rate readout (sits after the GC row).
    this.set('breach', `${(breachRate * 100).toFixed(2)}%`, showWarn)

    // Stash for the Copy report.
    this.last = {
      fps: t.fps,
      frame: fP,
      sim: sP,
      render: rP,
      ui: uP,
      idle,
      draws,
      sprites,
      bullets: aliveBullets,
      tanks: aliveTanks,
      particles: particles.activeCount,
      gc: this.gcSupported ? String(this.gcCount) : 'n/a',
      perfMode: t.perfMode,
    }
  }

  // ---- breach log / report ----

  private recordBreach(
    key: string,
    label: string,
    value: number,
    unit: string,
    limit: number,
  ): void {
    const now = performance.now()
    const last = this.lastBreachAt.get(key) ?? -Infinity
    if (now - last < BREACH_THROTTLE_MS) return
    this.lastBreachAt.set(key, now)
    this.breaches.push({
      t: Date.now(),
      metric: label,
      value: `${value.toFixed(2)}${unit}`,
      limit: `${limit}${unit}`,
    })
    if (this.breaches.length > MAX_BREACHES) this.breaches.shift()
  }

  private buildReport(): string {
    const L = this.last
    const end = Date.now()
    const total = this.sessionFrames
    const breached = this.sessionBreached
    const ratePct = total > 0 ? (breached / total) * 100 : 0
    const lines: string[] = []
    lines.push('=== Battle City — Performance Observatory Report ===')
    lines.push(`Generated : ${new Date(end).toISOString()}`)
    lines.push(
      `Session   : ${new Date(this.sessionStart).toISOString()} → ${new Date(end).toISOString()}`,
    )
    lines.push(`Frames    : ${total}  (over-budget: ${breached}, ${ratePct.toFixed(2)}%)`)
    lines.push(`UA        : ${navigator.userAgent}`)
    lines.push(`PerfMode  : ${L.perfMode ? 'ON' : 'OFF'}`)
    lines.push(`DPR       : ${window.devicePixelRatio}`)
    lines.push('')
    lines.push('--- Thresholds (warn) ---')
    lines.push(`Frame p95  > ${THRESHOLDS.frameMs} ms`)
    lines.push(`Sim p95    > ${THRESHOLDS.simMs} ms`)
    lines.push(`Render p95 > ${THRESHOLDS.renderMs} ms`)
    lines.push(`Draw calls > ${THRESHOLDS.drawCalls}`)
    lines.push('')
    lines.push('--- Current metrics (p95 unless noted) ---')
    lines.push(`FPS        : ${L.fps}`)
    lines.push(`Frame      : ${L.frame.toFixed(2)} ms`)
    lines.push(`Sim        : ${L.sim.toFixed(2)} ms`)
    lines.push(`Render     : ${L.render.toFixed(2)} ms`)
    lines.push(`UI         : ${L.ui.toFixed(2)} ms`)
    lines.push(`Idle       : ${L.idle.toFixed(2)} ms`)
    lines.push(`Draw calls : ${L.draws}`)
    lines.push(`Sprites     : ${L.sprites}`)
    lines.push(`Bullets     : ${L.bullets}`)
    lines.push(`Tanks       : ${L.tanks}`)
    lines.push(`Particles   : ${L.particles}`)
    lines.push(`GC         : ${L.gc}`)
    lines.push('')
    lines.push('--- Breach log (recorded) ---')
    if (this.breaches.length === 0) {
      lines.push('(no breaches recorded)')
    } else {
      for (const b of this.breaches) {
        lines.push(`${new Date(b.t).toISOString()}  ${b.metric}  ${b.value}  (limit ${b.limit})`)
      }
    }
    lines.push('')
    return lines.join('\n')
  }

  private copyReport(): void {
    copyText(this.buildReport())
    this.onCopied?.()
  }

  // ---- internals ----

  private addMetric(grid: HTMLElement, key: string, label: string): HTMLElement {
    const metric = document.createElement('div')
    metric.className = 'perf-metric'
    const l = document.createElement('span')
    l.className = 'perf-l'
    l.textContent = label
    const v = document.createElement('span')
    v.className = 'perf-v'
    v.textContent = '–'
    metric.append(l, v)
    this.values.set(key, v)
    grid.appendChild(metric)
    return metric
  }

  private set(key: string, text: string, over = false): void {
    const el = this.values.get(key)
    if (!el) return
    el.textContent = text
    el.classList.toggle('over', over)
  }

  private push(buf: number[], v: number): void {
    buf.push(v)
    if (buf.length > this.maxSamples) buf.shift()
  }

  private setupGcObserver(): void {
    try {
      this.gcObserver = new PerformanceObserver((list) => {
        this.gcCount += list.getEntries().length
      })
      this.gcObserver.observe({ entryTypes: ['gc'] })
      this.gcSupported = true
    } catch {
      this.gcSupported = false
      this.gcObserver = null
      if (this.gcMetricEl) this.gcMetricEl.hidden = true
    }
  }
}

/** Format a millisecond value with a fixed unit suffix. */
function fmtMs(v: number): string {
  return `${v.toFixed(2)} ms`
}

/** p-th percentile (1–100) of a numeric array; returns 0 for an empty array. */
function percentile(sorted: number[], p: number): number {
  const n = sorted.length
  if (n === 0) return 0
  const copy = sorted.slice().sort((a, b) => a - b)
  const i = Math.min(n - 1, Math.max(0, Math.ceil((p / 100) * n) - 1))
  return copy[i]
}

/** Copy text to the clipboard, with an execCommand fallback for sandboxes. */
function copyText(text: string): void {
  try {
    if (navigator.clipboard?.writeText) {
      void navigator.clipboard.writeText(text)
      return
    }
  } catch {
    /* fall through to legacy path */
  }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.top = '-9999px'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  try {
    document.execCommand('copy')
  } catch {
    /* ignore — clipboard unavailable */
  }
  document.body.removeChild(ta)
}
