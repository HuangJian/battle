import type { StageData, TankKind } from '../types'

// ================================================================
// Human-opening probe — offline manifest contract
// (human-opening-probe.plan v7 §1.1)
//
// `public/probe/x20-opening.json` is a BUILD ARTIFACT derived from
// `nn-training/levels/ladder-c20-lives1.jsonc` + `tools/probe/x20-opening.games.json`
// by `tools/probe/flatten-manifest.ts`. This module is the runtime side of that
// contract: validate-and-parse (fail loud, never default a field) + lookups.
//
// Every stage field is EXPLICIT. `enemySpawns` in particular must never be
// omitted — falling back to the engine default (`ENEMY_SPAWNS`) silently runs a
// different layout than the training course and makes `layoutHash` (which folds
// spawn points in) mismatch.
//
// Pure module: no DOM, no fs, no RNG.
// ================================================================

/** One manifest stage = a full `StageData` plus the layout identity hash. */
export interface ProbeStage extends StageData {
  id: number
  name: string
  tiles: string[]
  enemies: TankKind[]
  enemyCount: number
  playerSpawn: { col: number; row: number }
  enemySpawns: { col: number; row: number }[]
  /** `stageLayoutHash()` of this stage (FNV-1a over tiles + spawns + kinds + count). */
  layoutHash: string
}

/** One probe game = (stage, seed) pair + its archived tick-0 hash. */
export interface ProbeGame {
  game: number
  stage: number
  seed: number
  tag: string
  /**
   * `worldTickHash(world)` right after the probe run is set up (T3 steps ①–⑤),
   * archived at generation time. `tests/probe-run.test.ts` re-derives it from
   * the same shared setup function — a drift here means the browser and the
   * headless twin no longer agree on tick 0.
   */
  tick0Hash: string
}

export interface ProbeManifest {
  course: string
  courseSha: string
  difficulty: string
  max_ticks: number
  lives: number
  level: number
  stages: ProbeStage[]
  games: ProbeGame[]
}

export class ProbeManifestError extends Error {
  constructor(message: string) {
    super(`probe manifest: ${message}`)
    this.name = 'ProbeManifestError'
  }
}

const HEX8 = /^[0-9a-f]{8}$/
const HEX64 = /^[0-9a-f]{64}$/
const TILES_ROW = /^[.bswfiE]{26}$/
const TANK_KINDS: ReadonlySet<string> = new Set(['player', 'basic', 'fast', 'power', 'armor'])

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function fail(msg: string): never {
  throw new ProbeManifestError(msg)
}

function reqString(o: Record<string, unknown>, key: string, where: string): string {
  const v = o[key]
  if (typeof v !== 'string' || v === '') fail(`${where}.${key}: 缺失或非字符串（探针要求显式）`)
  return v
}

function reqInt(o: Record<string, unknown>, key: string, where: string, min: number): number {
  const v = o[key]
  if (typeof v !== 'number' || !Number.isInteger(v) || v < min) {
    fail(`${where}.${key}: 必须是 >= ${min} 的整数（收到 ${JSON.stringify(v)}）`)
  }
  return v
}

const GRID = 26

/** Validate one `{col,row}` spawn point out of an arbitrary record. */
function parseSpawn(v: unknown, where: string): { col: number; row: number } {
  if (!isRecord(v)) fail(`${where}: 不是 {col,row} 对象`)
  const col = v.col
  const row = v.row
  if (
    typeof col !== 'number' ||
    typeof row !== 'number' ||
    !Number.isInteger(col) ||
    !Number.isInteger(row)
  ) {
    fail(`${where}: col/row 必须是整数`)
  }
  if (col < 0 || col >= GRID || row < 0 || row >= GRID) {
    fail(`${where}: (${col},${row}) 超出 26×26 边界`)
  }
  return { col, row }
}

/** Validate an explicit, non-empty spawn-point array field. */
function parseSpawnList(
  o: Record<string, unknown>,
  key: string,
  where: string,
  nonEmptyReason: string,
): { col: number; row: number }[] {
  const arr = o[key]
  if (!Array.isArray(arr) || arr.length === 0)
    fail(`${where}.${key}: 缺失或为空 —— ${nonEmptyReason}`)
  return arr.map((v, i) => parseSpawn(v, `${where}.${key}[${i}]`))
}

function parseStage(raw: unknown, index: number): ProbeStage {
  const where = `stages[${index}]`
  if (!isRecord(raw)) fail(`${where}: 不是对象`)
  const id = reqInt(raw, 'id', where, 0)
  const name = reqString(raw, 'name', where)

  const tilesRaw = raw.tiles
  if (!Array.isArray(tilesRaw) || tilesRaw.length !== GRID) {
    fail(
      `${where}.tiles: 必须 26 行（收到 ${Array.isArray(tilesRaw) ? tilesRaw.length : typeof tilesRaw}）`,
    )
  }
  const tiles = tilesRaw.map((r, i) => {
    if (typeof r !== 'string' || !TILES_ROW.test(r)) {
      fail(`${where}.tiles[${i}]: 必须 26 字符（字符集 .bswfiE）`)
    }
    return r
  })

  const enemiesRaw = raw.enemies
  if (!Array.isArray(enemiesRaw) || enemiesRaw.length === 0) {
    fail(`${where}.enemies: 必须是非空敌种队列`)
  }
  const enemies = enemiesRaw.map((k, i) => {
    if (typeof k !== 'string' || !TANK_KINDS.has(k))
      fail(`${where}.enemies[${i}]: 非法敌种 ${JSON.stringify(k)}`)
    return k as TankKind
  })

  const enemyCount = reqInt(raw, 'enemyCount', where, 1)
  // Both spawn fields are REQUIRED and explicit: the engine's defaults
  // (PLAYER_SPAWN / ENEMY_SPAWNS) describe a different layout than the
  // training course, and `layoutHash` folds the points in.
  const playerSpawn =
    raw.playerSpawn === undefined
      ? fail(`${where}.playerSpawn: 缺失 —— 探针要求显式玩家出生点`)
      : parseSpawn(raw.playerSpawn, `${where}.playerSpawn`)
  const enemySpawns = parseSpawnList(raw, 'enemySpawns', where, '落回引擎默认 ENEMY_SPAWNS 即 bug')

  const layoutHash = reqString(raw, 'layoutHash', where)
  if (!HEX8.test(layoutHash)) fail(`${where}.layoutHash: 必须是 8 位十六进制`)

  return { id, name, tiles, enemies, enemyCount, playerSpawn, enemySpawns, layoutHash }
}

function parseGame(raw: unknown, index: number, stageIds: ReadonlySet<number>): ProbeGame {
  const where = `games[${index}]`
  if (!isRecord(raw)) fail(`${where}: 不是对象`)
  const game = reqInt(raw, 'game', where, 0)
  if (game !== index) fail(`${where}.game: 必须等于下标 ${index}（收到 ${game}）`)
  const stage = reqInt(raw, 'stage', where, 0)
  if (!stageIds.has(stage)) fail(`${where}.stage: ${stage} 不在 stages 表里`)
  const seed = reqInt(raw, 'seed', where, 0)
  const tag = reqString(raw, 'tag', where)
  const tick0Hash = reqString(raw, 'tick0Hash', where)
  if (!HEX8.test(tick0Hash)) fail(`${where}.tick0Hash: 必须是 8 位十六进制`)
  return { game, stage, seed, tag, tick0Hash }
}

/**
 * Validate + parse a manifest payload. Throws `ProbeManifestError` on the first
 * violation — a manifest that silently defaults a field is exactly the bug the
 * schema guard exists to catch.
 */
export function parseProbeManifest(raw: unknown): ProbeManifest {
  if (!isRecord(raw)) fail('根节点不是对象')
  const course = reqString(raw, 'course', 'manifest')
  const courseSha = reqString(raw, 'courseSha', 'manifest')
  if (!HEX64.test(courseSha)) fail('courseSha: 必须是 64 位十六进制 sha256')
  const difficulty = reqString(raw, 'difficulty', 'manifest')
  const maxTicks = reqInt(raw, 'max_ticks', 'manifest', 1)
  const lives = reqInt(raw, 'lives', 'manifest', 1)
  const level = reqInt(raw, 'level', 'manifest', 0)

  const stagesRaw = raw.stages
  if (!Array.isArray(stagesRaw) || stagesRaw.length === 0) fail('stages: 必须是非空数组')
  const stages = stagesRaw.map((s, i) => parseStage(s, i))
  const stageIds = new Set(stages.map((s) => s.id))

  const gamesRaw = raw.games
  if (!Array.isArray(gamesRaw) || gamesRaw.length === 0) fail('games: 必须是非空数组')
  const games = gamesRaw.map((g, i) => parseGame(g, i, stageIds))

  return { course, courseSha, difficulty, max_ticks: maxTicks, lives, level, stages, games }
}

/** The manifest as a `JSON.parse` payload (throws `ProbeManifestError`). */
export function parseProbeManifestText(text: string): ProbeManifest {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch (e) {
    fail(`不是合法 JSON: ${String(e)}`)
  }
  return parseProbeManifest(raw)
}

/** Stage entry for a game index. Throws if the game index is out of range. */
export function stageOfGame(manifest: ProbeManifest, game: number): ProbeStage {
  const entry = manifest.games[game]
  if (!entry) fail(`game index ${game} 越界（0..${manifest.games.length - 1}）`)
  const stage = manifest.stages.find((s) => s.id === entry.stage)
  if (!stage) fail(`game ${game} 指向不存在的 stage ${entry.stage}`)
  return stage
}
