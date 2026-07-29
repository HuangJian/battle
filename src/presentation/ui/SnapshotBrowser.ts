import type { GameSnapshot, SnapshotID } from '../../snapshot/types'

// ================================================================
// Snapshot Browser (plan §12)
//
// Displays all stored snapshots as a timeline of moments — thumbnail,
// type, stage, lives, star level, HP, score, kills, play time and
// creation time. The square thumbnail (already a faithful preview of the
// 416×416 playfield) is shown inline; there is no separate hover preview.
//
// Pure DOM component: reads snapshots through a provider callback and
// reports Load / Delete intents back to Game. Never touches the World.
// ================================================================

export interface SnapshotBrowserCallbacks {
  /** Snapshot list provider (already sorted newest-first). */
  getSnapshots: () => GameSnapshot[]
  onLoad: (id: SnapshotID) => void
  onDelete: (id: SnapshotID) => void
  onClose: () => void
  /** Return estimated storage usage for snapshots (bytes). */
  getStorageBytes?: () => Promise<number>
}

const TYPE_LABELS: Record<string, string> = {
  'stage-start': 'STAGE',
  pause: 'PAUSE',
  auto: 'AUTO',
  manual: 'MANUAL',
}

/** Toggle-group filters: ALL shows everything; the rest map to a SnapshotType. */
type FilterKey = 'all' | 'stage-start' | 'pause' | 'auto' | 'manual'

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'ALL' },
  { key: 'manual', label: 'MANUAL' },
  { key: 'pause', label: 'PAUSE' },
  { key: 'stage-start', label: 'STAGE' },
  { key: 'auto', label: 'AUTO' },
]

function formatPlayTime(ms: number): string {
  const total = Math.floor(ms / 1000)
  const m = Math.floor(total / 60)
  return `${String(m).padStart(2, '0')}m`
}

function formatCreated(epochMs: number): string {
  const d = new Date(epochMs)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export class SnapshotBrowser {
  readonly screen: HTMLElement
  private listEl: HTMLElement
  private callbacks: SnapshotBrowserCallbacks | null = null
  private openFlag = false
  /** Entry pending delete confirmation (two-step delete). */
  private confirmingDelete: SnapshotID | null = null
  private storageEl: HTMLElement | null = null
  /** Active list filter (toggle group). */
  private filter: FilterKey = 'manual'

  constructor() {
    this.screen = document.createElement('div')
    this.screen.className = 'ui-screen ui-snapshots'
    this.screen.innerHTML = `
      <div class="snap-panel">
        <div class="snap-header">
          <h2 class="ui-title">SNAPSHOT BROWSER</h2>
          <div class="snap-filters" data-snap="filters"></div>
          <div class="snap-header-right">
            <span class="snap-storage" data-snap="storage"></span>
            <button class="controls-btn snap-close" data-snap="close" type="button">✕ Close <kbd>Esc</kbd></button>
          </div>
        </div>
        <div class="snap-list" data-snap="list"></div>

      </div>
    `
    this.listEl = this.screen.querySelector('[data-snap="list"]')!
    this.storageEl = this.screen.querySelector('[data-snap="storage"]')
    const closeBtn = this.screen.querySelector('[data-snap="close"]') as HTMLElement
    closeBtn.addEventListener('click', () => this.requestClose())

    // Build the filter toggle group (ALL | MANUAL | PAUSE | STAGE | AUTO).
    const filtersEl = this.screen.querySelector('[data-snap="filters"]')!
    for (const f of FILTERS) {
      const btn = document.createElement('button')
      btn.type = 'button'
      btn.className = 'snap-filter' + (f.key === this.filter ? ' active' : '')
      btn.textContent = f.label
      btn.dataset.filter = f.key
      btn.addEventListener('click', () => this.setFilter(f.key))
      filtersEl.appendChild(btn)
    }

    // Own Esc while open (capture phase, before the game Input sees it).
    window.addEventListener(
      'keydown',
      (e) => {
        if (!this.openFlag) return
        e.preventDefault()
        e.stopImmediatePropagation()
        if (e.code === 'Escape') this.requestClose()
      },
      true,
    )
  }

  init(callbacks: SnapshotBrowserCallbacks): void {
    this.callbacks = callbacks
  }

  isOpen(): boolean {
    return this.openFlag
  }

  open(): void {
    if (this.openFlag) return
    this.openFlag = true
    this.confirmingDelete = null
    // Default to the MANUAL tab.
    this.filter = 'manual'
    this.screen.querySelectorAll<HTMLElement>('.snap-filter').forEach((b) => {
      b.classList.toggle('active', b.dataset.filter === 'manual')
    })
    this.refresh()
    this.screen.classList.add('active')
  }

  close(): void {
    if (!this.openFlag) return
    this.openFlag = false
    this.confirmingDelete = null
    this.screen.classList.remove('active')
  }

  private requestClose(): void {
    this.close()
    this.callbacks?.onClose()
  }

  /** Switch the active filter and re-render the list. */
  private setFilter(key: FilterKey): void {
    if (this.filter === key) return
    this.filter = key
    this.screen.querySelectorAll<HTMLElement>('.snap-filter').forEach((b) => {
      b.classList.toggle('active', b.dataset.filter === key)
    })
    this.refresh()
  }

  /** Rebuild the snapshot list from the provider, honouring the active filter. */
  refresh(): void {
    if (!this.callbacks) return
    const all = this.callbacks.getSnapshots()
    const snaps = this.filter === 'all' ? all : all.filter((s) => s.type === this.filter)
    const total = all.length
    const shown = snaps.length

    // Update tab button labels to show per-type counts.
    const countMap = new Map<string, number>()
    countMap.set('all', total)
    for (const s of all) {
      countMap.set(s.type, (countMap.get(s.type) ?? 0) + 1)
    }
    this.screen.querySelectorAll<HTMLElement>('.snap-filter').forEach((b) => {
      const key = b.dataset.filter!
      const count = countMap.get(key) ?? 0
      const base = FILTERS.find((f) => f.key === key)?.label ?? key.toUpperCase()
      b.textContent = count > 0 ? `${base} (${count})` : base
    })

    // Update storage size header.
    if (this.storageEl && this.callbacks.getStorageBytes) {
      this.callbacks.getStorageBytes().then((bytes) => {
        if (!this.storageEl) return
        this.storageEl.textContent = formatBytes(bytes)
      })
    }

    this.listEl.textContent = ''

    if (shown === 0) {
      const empty = document.createElement('div')
      empty.className = 'snap-empty'
      if (total > 0) {
        const label = this.filter === 'all' ? '' : (TYPE_LABELS[this.filter] ?? '')
        empty.textContent = label
          ? `No ${label} snapshots — pick another filter.`
          : 'No snapshots match this filter.'
      } else {
        empty.textContent = 'No snapshots yet — play a stage, pause, or press Alt+S to save one.'
      }
      this.listEl.appendChild(empty)
      return
    }

    for (const snap of snaps) {
      this.listEl.appendChild(this.buildEntry(snap))
    }
  }

  private buildEntry(snap: GameSnapshot): HTMLElement {
    const m = snap.metadata
    const entry = document.createElement('div')
    entry.className = 'snap-entry'
    entry.dataset.type = snap.type

    const thumb = document.createElement('div')
    thumb.className = 'snap-thumb'
    if (snap.thumbnail) {
      const img = document.createElement('img')
      img.src = snap.thumbnail
      img.alt = `Stage ${m.stage + 1} snapshot`
      img.draggable = false
      thumb.appendChild(img)
    } else {
      thumb.classList.add('snap-thumb-empty')
      thumb.textContent = 'NO PREVIEW'
    }

    const info = document.createElement('div')
    info.className = 'snap-info'
    const typeLabel = TYPE_LABELS[snap.type] ?? String(snap.type).toUpperCase()
    const stars = m.starLevel > 0 ? '★'.repeat(m.starLevel) : '—'
    info.innerHTML = `
      <div class="snap-info-top">
        <span class="snap-type snap-type-${snap.type}">${typeLabel}</span>
        <span class="snap-stage">Stage ${String(m.stage + 1).padStart(2, '0')} · ${m.stageName}</span>
        <span class="snap-created">${formatCreated(snap.createdAt)}</span>
      </div>
      <div class="snap-stats">
        <span title="Lives">♥ ${m.lives}</span>
        <span title="Star level">${stars}</span>
        <span title="HP">HP ${m.hp}/${m.maxHp}</span>
        <span title="Score">⚑ ${m.score}</span>
        <span title="Kills">☠ ${m.killCount}</span>
        <span title="Enemies remaining">⚔ ${m.enemiesRemaining}</span>
        <span title="Play time">⏱ ${formatPlayTime(m.playTimeMs)}</span>
        ${m.commanderPresent ? '<span class="snap-commander" title="Commander on field">CMD</span>' : ''}
      </div>
    `

    const actions = document.createElement('div')
    actions.className = 'snap-actions'

    const loadBtn = document.createElement('button')
    loadBtn.type = 'button'
    loadBtn.className = 'controls-btn controls-btn-primary snap-load'
    loadBtn.textContent = 'LOAD'
    loadBtn.addEventListener('click', () => {
      this.close()
      this.callbacks?.onLoad(snap.id)
    })

    const delBtn = document.createElement('button')
    delBtn.type = 'button'
    delBtn.className = 'controls-btn snap-delete'
    delBtn.textContent = 'DELETE'
    delBtn.addEventListener('click', () => {
      // Two-step: first click arms, second click confirms.
      if (this.confirmingDelete === snap.id) {
        this.confirmingDelete = null
        this.callbacks?.onDelete(snap.id)
        this.refresh()
      } else {
        this.confirmingDelete = snap.id
        delBtn.textContent = 'SURE?'
        delBtn.classList.add('snap-delete-arm')
      }
    })

    actions.appendChild(loadBtn)
    actions.appendChild(delBtn)

    entry.appendChild(thumb)
    entry.appendChild(info)
    entry.appendChild(actions)
    return entry
  }
}
