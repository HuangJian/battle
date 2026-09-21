#!/usr/bin/env bun
/**
 * flatten-manifest.ts — generate a probe course's manifest + the course index
 * (human-opening-probe.plan v7 §T1).
 *
 *   bun tools/probe/flatten-manifest.ts            # the default course (x20)
 *   bun tools/probe/flatten-manifest.ts \
 *     --games tmp/probe/<course>.games.json \
 *     --level nn-training/levels/<level>.jsonc \
 *     --out   public/probe/<course>.json           # any other course
 *
 * Inputs:
 *   nn-training/levels/ladder-c20-lives1.jsonc   — the training level file (COMMITTED)
 *   tmp/probe/x20-opening.games.json             — the game list (SCRATCH, gitignored)
 * Output (both generated, both gitignored — nothing here is versioned):
 *   public/probe/x20-opening.json                — served course manifest
 *   public/probe/index.json                      — the served course list
 *
 * A probe course is a SESSION, not a repo artifact: a human sits down, judges N
 * games, and the durable record is the session pack (zip) — the list is scratch
 * the moment the run is over. So the game list lives under `tmp/` and the
 * served files under the gitignored `public/probe/`; the only committed input
 * is the level file itself (which belongs to the training stack anyway).
 *
 * One course per manifest file. The four paths are `--games/--level/--out/
 * --index`, defaulting to the constants below. The course name is NOT a
 * parameter — it comes from the games file, and the manifest's basename has to
 * be that same name for `?probe=<course>` to find it.
 *
 * The INDEX is scanned, never hand-maintained: it lists the course manifests
 * actually sitting in `public/probe/`, so "add a course" = generate its
 * manifest and the index follows. The `_howto` field of a games file describes
 * the operator flow.
 *
 * Decoding is NOT re-implemented: stage grids go through
 * `src/nn/config-stage.ts::decodeStageGrid` and the layout identity through
 * `src/nn/arena-ladder.ts::stageLayoutHash`, exactly like the training stack.
 * The tick-0 world hash comes from the SAME `applyProbeRun` the browser uses,
 * so the archived hash and the runtime run are two calls of one function.
 *
 * Output is deterministic: keys are sorted recursively, no timestamps, no
 * absolute paths. `courseSha` hashes the level file with CRLF normalised to LF
 * so a Windows/Unix checkout of the same commit produces the same manifest.
 * CI re-runs this command and byte-compares the result.
 */

import { createHash } from 'node:crypto'
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { World } from '../../src/game/World'
import { decodeStageGrid, CUSTOM_STAGE_BASE, type StageJson } from '../../src/nn/config-stage'
import { stageLayoutHash } from '../../src/nn/arena-ladder'
import { worldTickHash } from '../../src/replay/tickHash'
import { applyProbeRun } from '../../src/probe/setup'
import { PROBE_DIR, probeManifestPath } from '../../src/probe/index'
import type { ProbeStage } from '../../src/probe/manifest'
import { parseJsonc } from './jsonc'

const LEVEL_PATH = 'nn-training/levels/ladder-c20-lives1.jsonc'
const GAMES_PATH = 'tmp/probe/x20-opening.games.json'
const OUT_PATH = 'public/probe/x20-opening.json'
/** `public/probe/<course>.json` sits next to it — one file name to know. */
const INDEX_PATH = `${PROBE_DIR}/index.json`
const INDEX_NAME = 'index.json'

interface LevelFile {
  name: string
  stages: unknown[]
  difficulty?: string
  max_ticks?: number
  player?: { lives?: number; level?: number }
}

interface GamesFile {
  course: string
  level: string
  games: Array<{ game: number; stage: number; seed: number; tag: string }>
}

function fail(msg: string): never {
  throw new Error(`[flatten-manifest] ${msg}`)
}

/** Recursive key-sorted JSON — deterministic regardless of insertion order. */
export function stableStringify(value: unknown, indent = 2): string {
  const render = (v: unknown, depth: number): string => {
    const pad = ' '.repeat(indent * depth)
    const padIn = ' '.repeat(indent * (depth + 1))
    if (v === null || typeof v !== 'object') return JSON.stringify(v) ?? 'null'
    if (Array.isArray(v)) {
      if (v.length === 0) return '[]'
      return `[\n${v.map((x) => padIn + render(x, depth + 1)).join(',\n')}\n${pad}]`
    }
    const obj = v as Record<string, unknown>
    const keys = Object.keys(obj).sort()
    if (keys.length === 0) return '{}'
    return `{\n${keys
      .map((k) => `${padIn}${JSON.stringify(k)}: ${render(obj[k], depth + 1)}`)
      .join(',\n')}\n${pad}}`
  }
  return render(value, 0)
}

/** sha256 of the level file with CRLF normalised (checkout-stable). */
export function courseShaOf(text: string): string {
  return createHash('sha256').update(text.replace(/\r\n/g, '\n'), 'utf8').digest('hex')
}

export interface FlattenResult {
  json: string
  stageCount: number
  gameCount: number
}

/** Pure-ish core (no fs): parse inputs, decode stages, hash tick 0. */
export function flatten(levelText: string, gamesText: string): FlattenResult {
  const level = parseJsonc(levelText) as LevelFile
  const gamesDoc = parseJsonc(gamesText) as GamesFile

  if (typeof level.name !== 'string' || level.name === '') fail('关文件缺少 name')
  if (!Array.isArray(level.stages) || level.stages.length === 0) fail('关文件缺少 stages')
  if (gamesDoc.level !== level.name) {
    fail(`games.json.level=${gamesDoc.level} 与关文件 name=${level.name} 不一致`)
  }

  // Stages — decoded by the shared course decoder (never re-implemented).
  const stages: ProbeStage[] = level.stages.map((stageJson, i) => {
    const id = CUSTOM_STAGE_BASE + i
    const decoded = decodeStageGrid(stageJson as StageJson, id)
    if (!decoded.playerSpawn) fail(`stage ${id}: 关文件缺少 player_spawn`)
    if (!decoded.enemySpawns || decoded.enemySpawns.length === 0) {
      fail(`stage ${id}: 关文件缺少 enemy_spawns（落回引擎默认即 bug）`)
    }
    return {
      id,
      name: decoded.name,
      tiles: decoded.tiles,
      enemies: decoded.enemies,
      enemyCount: decoded.enemyCount ?? decoded.enemies.length,
      playerSpawn: decoded.playerSpawn,
      enemySpawns: decoded.enemySpawns,
      layoutHash: stageLayoutHash(decoded),
    }
  })
  const byId = new Map(stages.map((s) => [s.id, s]))

  const options = {
    difficulty: level.difficulty ?? 'hard',
    lives: level.player?.lives ?? 1,
    level: level.player?.level ?? 0,
  }

  // Games — each one gets its tick-0 world hash, computed through the SAME
  // setup function the browser uses (T5a: this is the archive the runtime is
  // compared against).
  const games = gamesDoc.games.map((g, i) => {
    if (g.game !== i) fail(`games[${i}].game=${g.game} 与下标不符`)
    const stage = byId.get(g.stage)
    if (!stage) fail(`games[${i}]: stage ${g.stage} 不在关文件里`)
    if (!Number.isInteger(g.seed) || g.seed < 0) fail(`games[${i}]: 非法 seed ${g.seed}`)
    const world = new World()
    applyProbeRun(world, { stage, game: g }, options)
    return {
      game: g.game,
      stage: g.stage,
      seed: g.seed,
      tag: g.tag,
      tick0Hash: worldTickHash(world),
    }
  })

  const manifest = {
    course: gamesDoc.course,
    courseSha: courseShaOf(levelText),
    difficulty: options.difficulty,
    max_ticks: level.max_ticks ?? fail('关文件缺少 max_ticks'),
    lives: options.lives,
    level: options.level,
    stages,
    games,
  }

  return {
    json: stableStringify(manifest) + '\n',
    stageCount: stages.length,
    gameCount: games.length,
  }
}

export function generate(args: FlattenArgs = defaultArgs()): FlattenResult {
  const levelText = readFileSync(args.level, 'utf8')
  const gamesText = readFileSync(args.games, 'utf8')
  return flatten(levelText, gamesText)
}

// ================================================================
// CLI — per-course inputs, plus the scanned course index
// ================================================================

/** Where the manifest comes from and goes to (one course per run). */
export interface FlattenArgs {
  /** Course game list: `tools/probe/<course>.games.json`. */
  games: string
  /** Training level file — the stages the course plays. */
  level: string
  /** Output manifest: `public/probe/<course>.json`. */
  out: string
  /** Output course index. */
  index: string
}

/** Defaults = the default course (x20) at its scratch/served paths. */
export function defaultArgs(): FlattenArgs {
  return { games: GAMES_PATH, level: LEVEL_PATH, out: OUT_PATH, index: INDEX_PATH }
}

/** `--flag value` pairs; anything unknown or valueless fails loud. */
export function parseArgs(argv: readonly string[]): FlattenArgs {
  const args = defaultArgs()
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i]
    const value = (): string => argv[++i] ?? fail(`${flag} 缺少取值`)
    if (flag === '--games') args.games = value()
    else if (flag === '--level') args.level = value()
    else if (flag === '--out') args.out = value()
    else if (flag === '--index') args.index = value()
    else fail(`未知参数 ${flag}（允许：--games --level --out --index）`)
  }
  return args
}

/** Course names the served directory actually holds (the index itself aside). */
export function courseNamesIn(
  dir: string = PROBE_DIR,
  readdir: (d: string) => string[] = (d) => readdirSync(d),
): string[] {
  return (
    readdir(dir)
      .filter((name) => name.endsWith('.json') && name !== INDEX_NAME)
      .map((name) => name.slice(0, -'.json'.length))
      // A stray file whose name is not a legal course is not a course: it would
      // be unreachable (`probeManifestPath` refuses it), so listing it would
      // advertise a link nobody can follow.
      .filter((name) => probeManifestPath(name) !== null)
      .sort()
  )
}

/** Canonical index text for the served directory. */
export function courseIndexJson(dir: string = PROBE_DIR): string {
  return stableStringify({ courses: courseNamesIn(dir) }) + '\n'
}

if (import.meta.main) {
  try {
    const args = parseArgs(process.argv.slice(2))
    const result = generate(args)
    mkdirSync(dirname(args.out), { recursive: true })
    writeFileSync(args.out, result.json)
    const indexJson = courseIndexJson()
    mkdirSync(dirname(args.index), { recursive: true })
    writeFileSync(args.index, indexJson)
    console.log(
      `[flatten-manifest] ${args.out} ← ${args.level} + ${args.games} ` +
        `(${result.stageCount} stages, ${result.gameCount} games)`,
    )
    console.log(
      `[flatten-manifest] ${args.index} ← ${PROBE_DIR} 扫描到 ` +
        `${(JSON.parse(indexJson) as { courses: string[] }).courses.join(', ') || '（无）'}`,
    )
  } catch (err) {
    console.error(String(err instanceof Error ? err.message : err))
    process.exit(1)
  }
}
