#!/usr/bin/env bun
/**
 * annotate.ts — offline annotation for a probe session pack
 * (human-opening-probe.plan v7 §T6).
 *
 *   bun tools/probe/annotate.ts <session-pack.zip> [--out ann.jsonl] [--manifest <path>]
 *
 * The pack (built by "End session" in the browser) is a STORE zip holding
 * `session.json`, `<game>.replay` (existing replay format, tickHashes included)
 * and `verdicts.jsonl`. This tool replays each recording headlessly and emits
 * one annotation JSONL line per record:
 *
 *   {"type":"header", "game", "stage", "seed", "lives", "outcome", "kills",
 *    "ticks", "endTick", "endReason"}
 *   {"type":"kill"|"hit"|"pickup"|"death"|"baseHit", "game", "tick", ...}
 *   {"type":"sample", "game", "tick", "player":{pos,dir,hp}, "enemiesByKind",
 *    "bullets":[{pos,dir,ownerKind}], "baseHp"}
 *
 * The plan's §1.2 header fields are all present; the explicit `type` discriminator
 * is added so a reader never has to guess which shape a line is. Samples cover
 * the opening 300 ticks + the 600 ticks before the end, every 10 ticks.
 *
 * Replay wiring is the authoritative `tools/replay/verify-replay.ts:93-145`
 * recipe (reseed → difficulty/rules → load → restoreWorld → fresh Simulation).
 * The stage is loaded from the manifest by id when available (custom stages
 * 2000+ are NOT in `STAGES`); `verify-replay.ts`'s `STAGES[meta.stage] ??
 * STAGES[0]` fallback is reproduced only when the manifest is missing, with a
 * warning — the snapshot restore covers the layout either way.
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { parseReplayFile } from '../../src/replay/file'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import { STAGES } from '../../src/config/stages'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { readStoreZip } from '../../src/probe/zip'

/** Read world.state without letting TS narrow it to the last assigned literal. */
const stateOf = (w: World) => w.state
import { parseProbeManifestText, type ProbeManifest } from '../../src/probe/manifest'
import type { GameEvent } from '../../src/types'

const DEFAULT_MANIFEST = 'public/probe/x20-opening.json'
const OPENING_TICKS = 300
const TAIL_TICKS = 600
const SAMPLE_EVERY = 10

interface SessionJson {
  course: string
  courseSha: string
  startedAt: string
  games: Array<{ game: number; stage: number; seed: number }>
}

export type AnnotationLine = Record<string, unknown>

function arg(flag: string): string | undefined {
  const args = process.argv.slice(2)
  const i = args.indexOf(flag)
  return i >= 0 ? args[i + 1] : undefined
}

function loadManifest(): ProbeManifest | null {
  const path = arg('--manifest') ?? DEFAULT_MANIFEST
  try {
    return parseProbeManifestText(readFileSync(path, 'utf8'))
  } catch (err) {
    console.warn(`annotate: manifest 不可用（${path}）— 关卡退回 STAGES 口径: ${String(err)}`)
    return null
  }
}

/** Map one World event to its annotation record(s). */
function annotateEvent(game: number, tick: number, ev: GameEvent): AnnotationLine[] {
  switch (ev.type) {
    case 'tank_destroyed':
      // Player death vs enemy kill: the destroyed tank's faction decides.
      if (ev.tank.isPlayer || ev.tank.allegiance === 'player') {
        return [{ type: 'death', game, tick, kind: ev.tank.kind, by: ev.by }]
      }
      return [
        {
          type: 'kill',
          game,
          tick,
          kind: ev.tank.kind,
          by: ev.by,
          killerId: ev.byId ?? null,
        },
      ]
    case 'powerup_collected':
      return [{ type: 'pickup', game, tick, item: ev.powerUp, by: ev.by }]
    case 'base_destroyed':
      return [{ type: 'baseHit', game, tick, fatal: true, by: ev.by }]
    case 'player_hit':
      return [{ type: 'hit', game, tick, target: 'player' }]
    case 'player_damage':
      return [{ type: 'hit', game, tick, target: 'player', damage: ev.damage }]
    case 'enemy_hit':
      return [
        { type: 'hit', game, tick, target: 'enemy', damage: ev.damage, targetKind: ev.targetKind },
      ]
    default:
      // bullet_fired / terrain_destroyed / explosion / stage_clear are not part
      // of the probe's event vocabulary (plan §1.2) — deliberately dropped.
      return []
  }
}

/** One window sample of the world (read-only). */
function sampleWorld(game: number, tick: number, world: World): AnnotationLine {
  const enemiesByKind: Record<string, number> = {}
  for (const t of world.tanks) enemiesByKind[t.kind] = (enemiesByKind[t.kind] ?? 0) + 1
  return {
    type: 'sample',
    game,
    tick,
    player: world.player
      ? { pos: [world.player.x, world.player.y], dir: world.player.dir, hp: world.player.hp }
      : null,
    enemiesByKind,
    bullets: world.bullets.map((b) => ({
      pos: [b.x, b.y],
      dir: b.dir,
      ownerKind: b.ownerKind,
    })),
    baseHp: world.baseHp,
  }
}

export interface AnnotateResult {
  lines: AnnotationLine[]
  games: number
}

/** Annotate every `.replay` in the pack. Throws loudly on a malformed pack. */
export function annotatePack(zipBytes: Uint8Array, manifest: ProbeManifest | null): AnnotateResult {
  const entries = readStoreZip(zipBytes)
  const sessionEntry = entries.find((e) => e.name === 'session.json')
  if (!sessionEntry) throw new Error('annotate: pack 里没有 session.json')
  const session = JSON.parse(new TextDecoder().decode(sessionEntry.data)) as SessionJson
  const stageOf = new Map(session.games.map((g) => [g.game, g.stage]))
  const seedOf = new Map(session.games.map((g) => [g.game, g.seed]))

  const lines: AnnotationLine[] = []
  let games = 0

  for (const entry of entries) {
    const m = /^(\d+)\.replay$/.exec(entry.name)
    if (!m) continue
    const game = Number(m[1])
    const text = new TextDecoder().decode(entry.data)
    const parsed = parseReplayFile(text)
    if ('error' in parsed) throw new Error(`annotate: ${entry.name} 解析失败 — ${parsed.error}`)
    const replay = parsed.replay
    const meta = replay.metadata

    // ---- Rebuild the world exactly as verify-replay.ts:93-145 does ----
    const world = new World()
    world.rng.reseed(replay.seed)
    const dkey = meta.difficulty || 'hard'
    world.difficultyKey = dkey
    world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
    world.rules = RULES[dkey] ?? DEFAULT_RULES
    const stageId = stageOf.get(game) ?? meta.stage
    const fromManifest = manifest?.stages.find((s) => s.id === stageId)
    if (!fromManifest)
      console.warn(`annotate: game ${game} stage ${stageId} 不在 manifest —— 退回 STAGES 口径`)
    world.loadStageData(fromManifest ?? STAGES[meta.stage] ?? STAGES[0], 0)
    restoreWorld(world, replay.initialSnapshot)
    const input = new ReplayInput(replay.frames)
    const sim = new Simulation(world, input)
    sim.input = input
    sim.input2 = input.input2 ?? null
    world.state = 'playing'

    // ---- Tick and collect ----
    const events: AnnotationLine[] = []
    const samples: AnnotationLine[] = []
    let tick = 0
    while (!input.isFinished && tick < replay.totalTicks + 10) {
      sim.tick()
      input.advance()
      tick++
      for (const ev of world.consumeEvents()) events.push(...annotateEvent(game, tick, ev))
      if (tick % SAMPLE_EVERY === 0) samples.push(sampleWorld(game, tick, world))
      if (
        stateOf(world) === 'stageclear' ||
        stateOf(world) === 'gameover' ||
        stateOf(world) === 'victory'
      )
        break
    }

    const endTick = tick
    const baseDead = world.tileMap.isBaseDestroyed()
    const playerDead = !world.player || !world.player.alive
    const outcome =
      stateOf(world) === 'stageclear' || stateOf(world) === 'victory' ? 'clear' : 'lose'
    const endReason =
      outcome === 'clear'
        ? 'stage_clear'
        : baseDead
          ? 'base_destroyed'
          : playerDead
            ? 'lives_exhausted'
            : tick >= (manifest?.max_ticks ?? Number.POSITIVE_INFINITY)
              ? 'max_ticks'
              : 'replay_end'

    lines.push({
      type: 'header',
      game,
      stage: stageId,
      seed: seedOf.get(game) ?? replay.seed,
      lives: meta.lives,
      outcome,
      kills: world.killCount,
      ticks: tick,
      endTick,
      endReason,
    })
    lines.push(...events)
    for (const s of samples) {
      const t = s.tick as number
      if (t <= OPENING_TICKS || t >= endTick - TAIL_TICKS) lines.push(s)
    }
    games++
  }

  return { lines, games }
}

if (import.meta.main) {
  const packPath = process.argv.slice(2).find((a) => !a.startsWith('--'))
  if (!packPath) {
    console.error('usage: bun tools/probe/annotate.ts <session-pack.zip> [--out ann.jsonl]')
    process.exit(1)
  }
  try {
    const result = annotatePack(readFileSync(packPath), loadManifest())
    const out = result.lines.map((l) => JSON.stringify(l)).join('\n') + '\n'
    const outPath = arg('--out')
    if (outPath) {
      writeFileSync(outPath, out)
      console.log(`annotate: ${result.games} games → ${outPath} (${result.lines.length} lines)`)
    } else {
      process.stdout.write(out)
    }
  } catch (err) {
    console.error(String(err instanceof Error ? err.message : err))
    process.exit(1)
  }
}
