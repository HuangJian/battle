import { t } from '../../i18n'
import { BAND_TABLE, type ProbeBand } from '../../probe/verdict'
import type { ProbeUiSnapshot } from '../../game/ProbeController'

// ================================================================
// ProbeBar — session bar for the human-opening probe
// (human-opening-probe.plan v7 §T3 / §T4)
//
// HTML/CSS only — the canvas stays playfield-only (AGENTS §2.5). Pure
// presentation: read-only on the World, every action is a callback into the
// Game-layer controller. The band buttons come from the ONE band table
// (`probe/verdict.ts`), so the bar can never drift from the aggregation rule.
// ================================================================

export interface ProbeBarCallbacks {
  onVerdict: (band: ProbeBand, reason: string) => void
  onNavigate: (delta: -1 | 1) => void
  onRetry: () => void
  onEndSession: () => void
  onExit: () => void
}

export class ProbeBar {
  readonly el: HTMLElement
  private cb: ProbeBarCallbacks | null = null
  private title: HTMLElement
  private progress: HTMLElement
  private target: HTMLElement
  private budget: HTMLElement
  private status: HTMLElement
  private message: HTMLElement
  private reason: HTMLTextAreaElement
  private bandButtons = new Map<ProbeBand, HTMLButtonElement>()
  private prevBtn: HTMLButtonElement
  private nextBtn: HTMLButtonElement
  /** Last reflected outcome — see `isParked`. */
  private parked = false

  constructor() {
    this.el = document.createElement('div')
    this.el.className = 'probe-bar'
    this.el.hidden = true
    this.el.innerHTML = `
      <div class="probe-bar__head">
        <span class="probe-bar__title" data-probe="title">${t('probe.bar.title')}</span>
        <span class="probe-bar__progress" data-probe="progress"></span>
        <span class="probe-bar__spacer"></span>
        <button class="cc-btn probe-bar__exit" data-probe="exit" type="button">${t('probe.bar.exit')}</button>
      </div>
      <div class="probe-bar__target" data-probe="target"></div>
      <div class="probe-bar__budget" data-probe="budget"></div>
      <div class="probe-bar__status" data-probe="status"></div>
      <div class="probe-bar__bands" data-probe="bands"></div>
      <textarea class="probe-bar__reason" data-probe="reason" rows="2" placeholder="${t('probe.bar.reason')}"></textarea>
      <div class="probe-bar__nav">
        <button class="cc-btn" data-probe="prev" type="button">${t('probe.bar.prev')}</button>
        <button class="cc-btn" data-probe="retry" type="button">${t('probe.bar.retry')}</button>
        <button class="cc-btn" data-probe="next" type="button">${t('probe.bar.next')}</button>
        <span class="probe-bar__spacer"></span>
        <button class="cc-btn" data-probe="end" type="button">${t('probe.bar.end')}</button>
      </div>
      <div class="probe-bar__message" data-probe="message"></div>
    `

    const q = <T extends HTMLElement>(sel: string): T => this.el.querySelector(sel) as T
    this.title = q('[data-probe="title"]')
    this.progress = q('[data-probe="progress"]')
    this.target = q('[data-probe="target"]')
    this.budget = q('[data-probe="budget"]')
    this.status = q('[data-probe="status"]')
    this.message = q('[data-probe="message"]')
    this.reason = q<HTMLTextAreaElement>('[data-probe="reason"]')
    this.prevBtn = q<HTMLButtonElement>('[data-probe="prev"]')
    this.nextBtn = q<HTMLButtonElement>('[data-probe="next"]')

    const bands = q('[data-probe="bands"]')
    for (const spec of BAND_TABLE) {
      const btn = document.createElement('button')
      btn.type = 'button'
      btn.className = 'cc-btn probe-bar__band'
      btn.dataset.band = spec.band
      btn.title = `${spec.kills} — ${spec.meaning}`
      btn.textContent = t(`probe.band.${spec.band}`)
      btn.addEventListener('click', () => {
        btn.blur()
        this.cb?.onVerdict(spec.band, this.reason.value)
      })
      bands.appendChild(btn)
      this.bandButtons.set(spec.band, btn)
    }

    q<HTMLButtonElement>('[data-probe="prev"]').addEventListener('click', () =>
      this.cb?.onNavigate(-1),
    )
    q<HTMLButtonElement>('[data-probe="next"]').addEventListener('click', () =>
      this.cb?.onNavigate(1),
    )
    q<HTMLButtonElement>('[data-probe="retry"]').addEventListener('click', () => this.cb?.onRetry())
    q<HTMLButtonElement>('[data-probe="end"]').addEventListener('click', () =>
      this.cb?.onEndSession(),
    )
    q<HTMLButtonElement>('[data-probe="exit"]').addEventListener('click', () => this.cb?.onExit())
  }

  init(cb: ProbeBarCallbacks): void {
    this.cb = cb
  }

  show(): void {
    this.el.hidden = false
  }

  hide(): void {
    this.el.hidden = true
    this.parked = false
    this.setMessage('')
  }

  /**
   * A probe run is ACTIVE and has ENDED — the world is parked in 'paused' by
   * `ProbeController.finishRun` (not a user pause). UIManager consults this to
   * keep the battlefield visible instead of showing the PAUSED overlay.
   */
  get isParked(): boolean {
    return this.parked
  }

  /** One-line notice (e.g. "pack refused: missing reason"). */
  setMessage(text: string): void {
    this.message.textContent = text
  }

  /** Reflect the controller's snapshot. `null` hides the bar. */
  update(snap: ProbeUiSnapshot | null): void {
    if (!snap) {
      this.hide()
      return
    }
    this.show()
    this.parked = snap.outcome !== null
    this.title.textContent = t('probe.bar.title')
    this.progress.textContent = `${t('probe.bar.game', {
      n: String(snap.game + 1),
      total: String(snap.totalGames),
    })} · ${t('probe.bar.verdicts', { n: String(snap.verdictsDone) })}`
    this.target.textContent = `${t('probe.bar.stage', {
      stage: String(snap.stage),
      seed: String(snap.seed),
    })} · ${snap.tag}`
    this.budget.textContent = t('probe.bar.ticks', {
      ticks: String(snap.ticks),
      max: String(snap.maxTicks),
    })

    const parts: string[] = [t('probe.bar.attempts', { n: String(snap.attempts) })]
    if (snap.outcome) {
      parts.push(t(`probe.bar.outcome.${snap.outcome}`))
      parts.push(
        t('probe.bar.kills', {
          kills: String(snap.kills),
          deathTick: snap.deathTick === null ? '—' : String(snap.deathTick),
        }),
      )
    }
    this.status.textContent = parts.join('  ·  ')

    const chosen = snap.verdict?.best.band ?? null
    for (const [band, btn] of this.bandButtons) {
      const on = band === chosen
      btn.classList.toggle('selected', on)
      btn.setAttribute('aria-pressed', String(on))
    }
    // Prefill the reason box once a verdict comes back with one (e.g. after
    // switching games) without clobbering what the human is typing.
    if (snap.verdict && this.reason.value === '' && snap.verdict.best.reason) {
      this.reason.value = snap.verdict.best.reason
    }

    this.prevBtn.disabled = snap.game === 0
    this.nextBtn.disabled = snap.game >= snap.totalGames - 1
  }
}
