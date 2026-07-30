import type { Replay, ReplayID, ReplayType } from '../../replay/types'

// ================================================================
// Replay Browser (plan/replay.md §11)
//
// Lists every stored replay (victory / defeat) as a timeline of
// moments — thumbnail, type, stage, score, kills, play time and
// creation time — plus PLAY / FAVORITE / DELETE actions. The square
// thumbnail is the frame captured at the victory/defeat moment.
//
// Pure DOM component: reads replays through a provider callback and
// reports Play / Delete / Favorite intents back to Game. Never
// touches the World.
// ================================================================

export interface ReplayBrowserCallbacks {
  /** Replay list provider (already sorted newest-first). */
  getReplays: () => Replay[]
  /** Play a replay (Game swaps input + drives the sim). */
  onPlay: (id: ReplayID) => void
  /** Delete a replay. */
  onDelete: (id: ReplayID) => void
  /** Toggle favorite. Return value reflects the new state (false = blocked). */
  onToggleFavorite: (id: ReplayID) => boolean
  onClose: () => void
  /** Return estimated storage usage for replays (bytes). */
  getStorageBytes?: () => Promise<number>
  /** Import a .replay file (transient, in-memory only). */
  onImport?: (replay: Replay) => void
  /** Export a replay as .replay file download. */
  onExport?: (id: ReplayID) => void
}

const TYPE_LABELS: Record<ReplayType, string> = {
  clear: 'CLEAR',
  base: 'BASE DOWN',
  died: 'DIED',
  timeout: 'TIMEOUT',
}

/** Toggle-group filters. */
type FilterKey = 'all' | ReplayType | 'favorite'

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'ALL' },
  { key: 'clear', label: 'CLEAR' },
  { key: 'base', label: 'BASE DOWN' },
  { key: 'died', label: 'DIED' },
  { key: 'timeout', label: 'TIMEOUT' },
  { key: 'favorite', label: 'FAV ★' },
]

function formatPlayTime(ms: number): string {
  const total = Math.floor(ms / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return m > 0 ? `${String(m).padStart(2, '0')}m` : `${s}s`
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

export class ReplayBrowser {
  readonly screen: HTMLElement
  private listEl: HTMLElement
  private storageEl: HTMLElement | null = null
  private callbacks: ReplayBrowserCallbacks | null = null
  private openFlag = false
  /** Entry pending delete confirmation (two-step delete). */
  private confirmingDelete: ReplayID | null = null
  /** Active list filter (toggle group). */
  private filter: FilterKey = 'all'

  constructor() {
    this.screen = document.createElement('div')
    this.screen.className = 'ui-screen ui-replays'
    this.screen.innerHTML = `
      <div class="snap-panel">
        <div class="snap-header">
          <h2 class="ui-title">REPLAY BROWSER</h2>
          <div class="snap-filters" data-replay="filters"></div>
          <div class="snap-header-right">
            <span class="snap-storage" data-replay="storage"></span>
            <button class="controls-btn snap-import" data-replay="import" type="button">Import</button>
            <button class="controls-btn snap-close" data-replay="close" type="button">✕ Close <kbd>Esc</kbd></button>
          </div>
        </div>
        <div class="snap-list" data-replay="list"></div>
      </div>
    `
    this.listEl = this.screen.querySelector('[data-replay="list"]')!
    this.storageEl = this.screen.querySelector('[data-replay="storage"]')
    const closeBtn = this.screen.querySelector('[data-replay="close"]') as HTMLElement
    closeBtn.addEventListener('click', () => this.requestClose())

    // Import button — triggers file picker for .replay files
    const importBtn = this.screen.querySelector('[data-replay="import"]') as HTMLElement
    importBtn.addEventListener('click', () => this.triggerImport())

    // Drag-and-drop overlay
    this.setupDragDrop()

    // Build the filter toggle group (ALL | VICTORY | DEFEAT | FAV ★).
    const filtersEl = this.screen.querySelector('[data-replay="filters"]')!
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

  init(callbacks: ReplayBrowserCallbacks): void {
    this.callbacks = callbacks
  }

  isOpen(): boolean {
    return this.openFlag
  }

  open(): void {
    if (this.openFlag) return
    this.openFlag = true
    this.confirmingDelete = null
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

  /** Rebuild the replay list from the provider, honouring the active filter. */
  refresh(): void {
    if (!this.callbacks) return
    const all = this.callbacks.getReplays()
    const replays =
      this.filter === 'all'
        ? all
        : this.filter === 'favorite'
          ? all.filter((r) => r.isFavorite)
          : all.filter((r) => r.type === this.filter)
    const total = all.length
    const shown = replays.length

    // Update tab button labels to show per-type counts.
    const countMap = new Map<string, number>()
    countMap.set('all', total)
    for (const r of all) {
      countMap.set(r.type, (countMap.get(r.type) ?? 0) + 1)
    }
    // Also count favorites.
    const favCount = all.filter((r) => r.isFavorite).length
    this.screen.querySelectorAll<HTMLElement>('.snap-filter').forEach((b) => {
      const key = b.dataset.filter!
      const count = key === 'favorite' ? favCount : (countMap.get(key) ?? 0)
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
        const label =
          this.filter === 'favorite'
            ? 'favorited'
            : this.filter in TYPE_LABELS
              ? TYPE_LABELS[this.filter as ReplayType]
              : ''
        empty.textContent = label
          ? `No ${label} replays — pick another filter.`
          : 'No replays match this filter.'
      } else {
        empty.textContent = 'No replays yet — finish a stage (win or lose) to record one.'
      }
      this.listEl.appendChild(empty)
      return
    }

    for (const replay of replays) {
      this.listEl.appendChild(this.buildEntry(replay))
    }
  }

  /** Trigger file picker for .replay import. */
  private triggerImport(): void {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.replay'
    input.multiple = true
    input.addEventListener('change', () => {
      for (const file of input.files ?? []) {
        this.importFile(file)
      }
    })
    input.click()
  }

  /** Handle drag-and-drop overlay for .replay import. */
  private setupDragDrop(): void {
    let dragCount = 0
    const overlay = document.createElement('div')
    overlay.className = 'snap-drag-overlay'
    overlay.textContent = '拖放 .replay 文件到此处'
    overlay.style.cssText =
      'display:none;position:absolute;inset:0;background:rgba(0,0,0,0.3);color:#fff;font-size:1.2em;display:none;align-items:center;justify-content:center;z-index:10;pointer-events:none;'
    const panel = this.screen.querySelector('.snap-panel')! as HTMLElement
    panel.style.position = 'relative'
    panel.appendChild(overlay)

    panel.addEventListener('dragenter', (e) => {
      e.preventDefault()
      dragCount++
      overlay.style.display = 'flex'
    })
    panel.addEventListener('dragover', (e) => e.preventDefault())
    panel.addEventListener('dragleave', () => {
      dragCount--
      if (dragCount <= 0) {
        dragCount = 0
        overlay.style.display = 'none'
      }
    })
    panel.addEventListener('drop', (e) => {
      e.preventDefault()
      dragCount = 0
      overlay.style.display = 'none'
      for (const file of e.dataTransfer?.files ?? []) {
        if (file.name.endsWith('.replay')) this.importFile(file)
      }
    })
  }

  /** Read and import a .replay file. */
  private async importFile(file: File): Promise<void> {
    try {
      const text = await file.text()
      const { parseReplayFile } = await import('../../replay/file')
      const result = parseReplayFile(text)
      if ('error' in result) {
        console.warn(`[replay] import failed: ${result.error}`)
        return
      }
      result.replay.transient = true
      this.callbacks?.onImport?.(result.replay)
      this.refresh()
    } catch (err) {
      console.warn('[replay] import error:', err)
    }
  }

  private buildEntry(replay: Replay): HTMLElement {
    const m = replay.metadata
    const entry = document.createElement('div')
    entry.className = 'snap-entry'
    entry.dataset.type = replay.type

    const thumb = document.createElement('div')
    thumb.className = 'snap-thumb'
    if (replay.thumbnail) {
      const img = document.createElement('img')
      img.src = replay.thumbnail
      img.alt = `Stage ${m.stage + 1} replay`
      img.draggable = false
      thumb.appendChild(img)
    } else {
      thumb.classList.add('snap-thumb-empty')
      thumb.textContent = 'NO PREVIEW'
    }

    const info = document.createElement('div')
    info.className = 'snap-info'
    const typeLabel = TYPE_LABELS[replay.type] ?? String(replay.type).toUpperCase()
    const star = m.playerLevel > 0 ? '★'.repeat(m.playerLevel) : '—'
    info.innerHTML = `
      <div class="snap-info-top">
        <span class="snap-type snap-type-${replay.type}">${typeLabel}</span>
        <span class="snap-stage">Stage ${String(m.stage + 1).padStart(2, '0')} · ${m.stageName}</span>
        <span class="snap-created">${formatCreated(replay.createdAt)}</span>
      </div>
      <div class="snap-stats">
        <span title="Score">⚑ ${m.score}</span>
        <span title="Kills">☠ ${m.killCount}</span>
        <span title="Player level">${star}</span>
        <span title="Lives left">♥ ${m.lives}</span>
        <span title="Duration">⏱ ${formatPlayTime(replay.durationMs)}</span>
        ${replay.isFavorite ? '<span class="snap-commander" title="Favorited">★ FAV</span>' : ''}
      </div>
    `

    const actions = document.createElement('div')
    actions.className = 'snap-actions'

    const playBtn = document.createElement('button')
    playBtn.type = 'button'
    playBtn.className = 'controls-btn controls-btn-primary snap-load'
    playBtn.textContent = 'PLAY'
    playBtn.addEventListener('click', () => {
      // Close first (state becomes 'playing' action state), then play so the
      // vsync rAF loop re-arms from inside startPlayback().
      this.close()
      this.callbacks?.onPlay(replay.id)
    })

    const favBtn = document.createElement('button')
    favBtn.type = 'button'
    favBtn.className = 'controls-btn snap-fav' + (replay.isFavorite ? ' snap-fav-on' : '')
    favBtn.textContent = replay.isFavorite ? '★ FAV' : '☆ FAV'
    favBtn.addEventListener('click', () => {
      const nowFav = this.callbacks?.onToggleFavorite(replay.id) ?? false
      replay.isFavorite = nowFav
      favBtn.textContent = nowFav ? '★ FAV' : '☆ FAV'
      favBtn.classList.toggle('snap-fav-on', nowFav)
      // Update the inline badge in the info row.
      const badge = info.querySelector('.snap-commander')
      if (nowFav && !badge) {
        const b = document.createElement('span')
        b.className = 'snap-commander'
        b.title = 'Favorited'
        b.textContent = '★ FAV'
        info.querySelector('.snap-stats')!.appendChild(b)
      } else if (!nowFav && badge) {
        badge.remove()
      }
      // A filter of 'favorite' now hides this entry if unfavorited.
      if (this.filter === 'favorite' && !nowFav) this.refresh()
    })

    const delBtn = document.createElement('button')
    delBtn.type = 'button'
    delBtn.className = 'controls-btn snap-delete'
    delBtn.textContent = 'DELETE'
    delBtn.addEventListener('click', () => {
      // Two-step: first click arms, second click confirms.
      if (this.confirmingDelete === replay.id) {
        this.confirmingDelete = null
        this.callbacks?.onDelete(replay.id)
        this.refresh()
      } else {
        this.confirmingDelete = replay.id
        delBtn.textContent = 'SURE?'
        delBtn.classList.add('snap-delete-arm')
      }
    })

    actions.appendChild(playBtn)

    // Export button (download .replay file) — right after PLAY
    // Imported replays (transient) are also exportable: they live in the
    // same store and serialize identically to native recordings.
    if (this.callbacks?.onExport) {
      const exportBtn = document.createElement('button')
      exportBtn.type = 'button'
      exportBtn.className = 'controls-btn snap-export'
      exportBtn.textContent = 'Export'
      exportBtn.title = 'Export .replay'
      exportBtn.addEventListener('click', () => this.callbacks?.onExport!(replay.id))
      actions.appendChild(exportBtn)
    }

    actions.appendChild(favBtn)
    actions.appendChild(delBtn)

    entry.appendChild(thumb)
    entry.appendChild(info)
    entry.appendChild(actions)
    return entry
  }
}
