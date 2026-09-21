import type { World } from './World'
import type { InputRecorder } from '../replay/InputRecorder'
import { serializeReplayFile } from '../replay/file'
import { REPLAY_HASH_INTERVAL } from '../replay/config'
import type { ReplayMetadata } from '../replay/types'
import {
  parseProbeManifest,
  parseProbeManifestText,
  stageOfGame,
  type ProbeManifest,
} from '../probe/manifest'
import { resolveProbeTarget, type ProbeTargetRejection } from '../probe/query'
import { applyProbeRun } from '../probe/setup'
import { buildSessionPack, type ProbeReplayFile, type ProbeSessionMeta } from '../probe/session'
import {
  aggregateSeedVerdict,
  isProbeBand,
  suggestBand,
  validateProbeVerdict,
  type ProbeBand,
  type ProbeVerdict,
} from '../probe/verdict'

// ================================================================
// ProbeController — Game-layer lifecycle for the human-opening probe
// (human-opening-probe.plan v7 §T3 / §T4)
//
// Owns: the run sequence (shared with the headless twin via `applyProbeRun`),
// per-game recording, verdict collection, and the session pack. Owns NO DOM —
// the session bar is a thin presentation surface driven by `onChange`.
//
// One Author (§2.1): lifecycle transitions live here (Game layer), exactly like
// GameMenu/RecoveryController. The per-tick gameplay mutation stays in
// Simulation.
//
// Session state lives on this controller, NOT in `world.ui` — `UIState` is
// documented as pure menu/overlay bookkeeping that is never serialized, while
// verdicts/attempts are session data. It is also not module-level (§2.2).
// ================================================================

export type ProbeRunOutcome = 'clear' | 'gameover' | 'timeout'

export interface ProbeRunRecord {
  game: number
  stage: number
  seed: number
  outcome: ProbeRunOutcome
  kills: number
  deathTick: number | null
  replayFile: string
  replayText: string
}

export interface ProbeUiSnapshot {
  course: string
  game: number
  totalGames: number
  stage: number
  seed: number
  tag: string
  ticks: number
  maxTicks: number
  attempts: number
  outcome: ProbeRunOutcome | null
  kills: number
  deathTick: number | null
  suggestedBand: ProbeBand | null
  verdict: ProbeVerdict | null
  verdictsDone: number
}

/** Minimal host surface — keeps the controller testable without a Game/DOM. */
export interface ProbeHost {
  world: World
  recorder: InputRecorder
  /** Injectable clock (tests pin `startedAt`). */
  now?: () => Date
}

export class ProbeBootError extends Error {
  constructor(
    readonly reason: ProbeTargetRejection,
    message: string,
  ) {
    super(message)
    this.name = 'ProbeBootError'
  }
}

export class ProbeController {
  private manifest: ProbeManifest | null = null
  private game = 0
  private active = false
  private ticks = 0
  private budgetSpent = false
  private current: ProbeRunRecord | null = null
  private attempts = new Map<number, number>()
  private verdicts = new Map<number, ProbeVerdict>()
  private replays = new Map<number, ProbeReplayFile>()
  private startedAt = ''
  private listener: ((snap: ProbeUiSnapshot | null) => void) | null = null

  constructor(private readonly host: ProbeHost) {}

  // ---- wiring ----

  onChange(fn: ((snap: ProbeUiSnapshot | null) => void) | null): void {
    this.listener = fn
  }

  get isActive(): boolean {
    return this.active
  }

  get manifestOrNull(): ProbeManifest | null {
    return this.manifest
  }

  /** Parse + install a manifest (throws `ProbeManifestError` on a bad payload). */
  installManifest(raw: unknown): ProbeManifest {
    const manifest = parseProbeManifest(raw)
    this.manifest = manifest
    return manifest
  }

  /** Same, from manifest text (the browser fetches JSON text, not an object). */
  installManifestText(text: string): ProbeManifest {
    const manifest = parseProbeManifestText(text)
    this.manifest = manifest
    return manifest
  }

  // ---- boot ----

  /**
   * Boot from a launch query + the manifest TEXT. Throws `ProbeBootError` for
   * `course-mismatch` / `game-not-integer` / `game-out-of-range`; returns false
   * when the query simply isn't a probe launch (`absent`).
   */
  bootFromText(search: string | URLSearchParams, manifestText: string): boolean {
    const manifest = parseProbeManifestText(manifestText)
    this.manifest = manifest
    const target = resolveProbeTarget(search, manifest)
    if (!target.ok) {
      if (target.reason === 'absent') return false
      throw new ProbeBootError(target.reason, `probe 拒绝启动：${target.reason}`)
    }
    this.startedAt = (this.host.now?.() ?? new Date()).toISOString()
    this.active = true
    this.startGame(target.game)
    return true
  }

  // ---- run lifecycle ----

  /** ①–⑤ shared setup, ⑥ fresh recording, ⑦ budget armed. */
  startGame(index: number): void {
    const manifest = this.requireManifest()
    if (index < 0 || index >= manifest.games.length) {
      throw new ProbeBootError('game-out-of-range', `probe: game ${index} 越界`)
    }
    const game = manifest.games[index]
    const stage = stageOfGame(manifest, index)

    applyProbeRun(
      this.host.world,
      { stage, game },
      {
        difficulty: manifest.difficulty,
        lives: manifest.lives,
        level: manifest.level,
      },
    )

    // ⑥ The stage-change detector in GameLoop only re-arms the recorder when
    // `stageIndex` changes; probe runs load with index 0 and a re-run keeps the
    // same index, so the recorder must be armed explicitly.
    this.host.recorder.startNew(this.host.world)

    this.game = index
    this.ticks = 0
    this.budgetSpent = false
    this.current = null
    this.attempts.set(index, (this.attempts.get(index) ?? 0) + 1)
    this.emit()
  }

  /** Called once per completed live tick by the loop (cheap when inactive). */
  noteTick(): void {
    if (!this.active) return
    this.ticks++
    if (this.ticks >= this.maxTicks) this.budgetSpent = true
  }

  get maxTicks(): number {
    return this.manifest?.max_ticks ?? 0
  }

  get tickBudgetSpent(): boolean {
    return this.budgetSpent
  }

  /**
   * True once the current run has finished (the run is parked in 'paused' from
   * `finishRun`). The loop's pause key is refused while this holds — resuming a
   * parked run would tick a world whose outcome is already recorded.
   */
  get runEnded(): boolean {
    return this.current !== null
  }

  /**
   * End the run: finalize the recording into the session's replay slot.
   * Called on stage clear / game over / tick-budget exhaustion instead of the
   * normal recovery flow (⑧ — a 1-life probe dies constantly; letting
   * `startRecovery()` hijack that would both break the "1 life" semantics and
   * lose the run).
   */
  finishRun(outcome: ProbeRunOutcome): void {
    if (!this.active || this.current) return
    const manifest = this.requireManifest()
    const stage = stageOfGame(manifest, this.game)
    const world = this.host.world

    const result = this.host.recorder.finalize()
    let replayText = ''
    if (result) {
      const metadata: ReplayMetadata = {
        // Risk (plan §4 rule 4): `metadata.stage` is the source of truth for the
        // replay's stage identity — `stageIndex` is the per-index scoring caliber
        // and must NOT carry 2000+ (the `1.05^index` scaling incident).
        stage: stage.id,
        stageName: stage.name,
        difficulty: world.difficultyKey,
        lives: world.lives,
        playerLevel: world.playerLevel,
        score: world.score,
        killCount: world.killCount,
        enemiesTotal: world.enemiesTotal,
        playTimeMs: world.playTimeMs,
        coop: world.coop,
        spectate: world.spectate,
        spectateDual: world.spectateDual,
      }
      replayText = serializeReplayFile({
        source: 'browser',
        seed: world.seed,
        initialSnapshot: result.snapshot,
        frames: result.frames,
        totalTicks: result.tickCount,
        metadata,
        tickHashes: result.tickHashes,
        hashInterval: REPLAY_HASH_INTERVAL,
      })
    }

    const record: ProbeRunRecord = {
      game: this.game,
      stage: stage.id,
      seed: manifest.games[this.game].seed,
      outcome,
      kills: world.killCount,
      deathTick: outcome === 'clear' ? null : this.ticks,
      replayFile: `${this.game}.replay`,
      replayText,
    }
    this.current = record
    this.replays.set(this.game, { file: record.replayFile, text: replayText })
    // Reset the recorder so a follow-up run cannot inherit frames.
    this.host.recorder.reset()
    // Park the world in 'paused'. Without this the loop would keep ticking
    // ('gameover'/'stageclear' are tick-able states) and a 'stageclear' timer
    // would advance the world into the NEXT stage — a probe run must end
    // exactly where it ended. Lifecycle transition, Game-layer owned (§2.1).
    this.host.world.state = 'paused'
    this.emit()
  }

  /** Re-run the current game from the same stage+seed (fresh recording). */
  retry(): void {
    if (this.active) this.startGame(this.game)
  }

  /** Move to a neighbouring game in the manifest (out-of-range = no-op). */
  navigate(delta: -1 | 1): void {
    const manifest = this.requireManifest()
    const next = this.game + delta
    if (next < 0 || next >= manifest.games.length) return
    this.startGame(next)
  }

  // ---- verdicts ----

  /** Suggest a band from the finished run (the human may override). */
  suggestedBand(): ProbeBand | null {
    if (!this.current) return null
    return suggestBand(this.current.kills, this.current.outcome === 'clear')
  }

  /**
   * Record the human's read of this game. Throws `ProbeVerdictError` when an
   * `unsolvable` verdict has no reason.
   */
  submitVerdict(band: ProbeBand, reason: string): ProbeVerdict {
    if (!isProbeBand(band)) throw new Error(`probe: 未知 band ${String(band)}`)
    const record = this.current
    if (!record) throw new Error('probe: 本局尚未结束，不能提交 verdict')
    const verdict: ProbeVerdict = {
      game: record.game,
      stage: record.stage,
      seed: record.seed,
      attempts: this.attempts.get(record.game) ?? 1,
      best: { band, reason },
      kills: record.kills,
      deathTick: record.deathTick,
    }
    validateProbeVerdict(verdict)
    this.verdicts.set(record.game, verdict)
    this.emit()
    return verdict
  }

  /** Serialized `.replay` text for a finished game (null when not recorded). */
  replayTextOf(game: number): string | null {
    return this.replays.get(game)?.text ?? null
  }

  /** Seed verdict aggregation over every submitted verdict (plan §0 table). */
  seedVerdict(): 'solvable' | 'unknown' {
    return aggregateSeedVerdict([...this.verdicts.values()].map((v) => v.best.band))
  }

  // ---- session pack ----

  sessionMeta(): ProbeSessionMeta {
    const manifest = this.requireManifest()
    return {
      course: manifest.course,
      courseSha: manifest.courseSha,
      startedAt: this.startedAt || (this.host.now?.() ?? new Date()).toISOString(),
      games: manifest.games.map((g) => ({ game: g.game, stage: g.stage, seed: g.seed })),
    }
  }

  /**
   * Build the downloadable pack. Throws (`ProbeVerdictError`) when a verdict
   * line is invalid — the only refusal path the protocol mandates.
   */
  buildPack(): { filename: string; bytes: Uint8Array } {
    return buildSessionPack({
      meta: this.sessionMeta(),
      replays: [...this.replays.values()],
      verdicts: [...this.verdicts.values()],
    })
  }

  /** Leave probe mode: drop the recording and hide the bar. */
  exit(): void {
    this.active = false
    this.current = null
    this.host.recorder.reset()
    this.emit()
  }

  // ---- UI snapshot ----

  snapshot(): ProbeUiSnapshot | null {
    const manifest = this.manifest
    if (!manifest || !this.active) return null
    const game = manifest.games[this.game]
    const stage = stageOfGame(manifest, this.game)
    return {
      course: manifest.course,
      game: this.game,
      totalGames: manifest.games.length,
      stage: stage.id,
      seed: game.seed,
      tag: game.tag,
      ticks: this.ticks,
      maxTicks: manifest.max_ticks,
      attempts: this.attempts.get(this.game) ?? 1,
      outcome: this.current?.outcome ?? null,
      kills: this.current?.kills ?? this.host.world.killCount,
      deathTick: this.current?.deathTick ?? null,
      suggestedBand: this.suggestedBand(),
      verdict: this.verdicts.get(this.game) ?? null,
      verdictsDone: this.verdicts.size,
    }
  }

  private requireManifest(): ProbeManifest {
    if (!this.manifest) throw new Error('probe: manifest 未装载')
    return this.manifest
  }

  private emit(): void {
    if (this.listener) this.listener(this.snapshot())
  }
}
