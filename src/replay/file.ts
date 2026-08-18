import type { WorldSnapshot } from '../snapshot/types'
import { GAME_VERSION } from '../snapshot/config'
import { FRAME_SCHEMA_VERSION, REPLAY_HASH_INTERVAL, isSupportedFrameSchema } from './config'
import { frameSchemaVersionOf, packFrames, unpackFrames } from './pack'
import type { Replay, ReplayMetadata, ReplayType } from './types'
import { generateUUID } from './uuid'

// ================================================================
// Replay File Format — .replay envelope serialization
// (plan/God-AI-Replay-Visualization §3)
//
// Pure functions, no DOM, no fs. Shared between browser and bun.
// ================================================================

const FORMAT_ID = 'bc-replay'
const FORMAT_VERSION = 1

// ---- Envelope types (internal, not exported) ----

interface SimEnvelope {
  seed: number
  difficulty: string
  stageIndex: number
  stageName: string
  outcome: string
  status: string
  maxTicks: number
  godAIParams?: Record<string, unknown>
}

interface FinalStateEnvelope {
  score: number
  lives: number
  killCount: number
  ticks: number
}

interface ReplayEnvelope {
  initialSnapshot: WorldSnapshot
  framesBase64: string
  totalTicks: number
  metadata: ReplayMetadata
  seed: number
  /** Desync-locator chain — world hash per hashInterval ticks. Absent in legacy files. */
  tickHashes?: string[]
  hashInterval?: number
}

interface FileEnvelope {
  format: string
  formatVersion: number
  gameVersion: string
  frameSchemaVersion: number
  source: 'sim' | 'browser'
  sim?: SimEnvelope
  finalState?: FinalStateEnvelope
  replay: ReplayEnvelope
}

// ---- base64 helpers ----

/** Bun/Node: Buffer-based base64. */
function uint8ArrayToBase64Node(data: Uint8Array): string {
  return Buffer.from(data).toString('base64')
}

/** Browser: standard base64 without Buffer dependency. */
function uint8ArrayToBase64Browser(data: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < data.length; i++) {
    binary += String.fromCharCode(data[i])
  }
  return btoa(binary)
}

/** Bun/Node: Buffer-based base64 decode. */
function base64ToUint8ArrayNode(b64: string): Uint8Array {
  return new Uint8Array(Buffer.from(b64, 'base64'))
}

/** Browser: standard base64 decode without Buffer dependency. */
function base64ToUint8ArrayBrowser(b64: string): string {
  return atob(b64)
}

const toBase64: (data: Uint8Array) => string =
  typeof Buffer !== 'undefined' ? uint8ArrayToBase64Node : uint8ArrayToBase64Browser

const fromBase64: (b64: string) => Uint8Array =
  typeof Buffer !== 'undefined'
    ? base64ToUint8ArrayNode
    : (b64: string) => {
        const binary = base64ToUint8ArrayBrowser(b64)
        const bytes = new Uint8Array(binary.length)
        for (let i = 0; i < binary.length; i++) {
          bytes[i] = binary.charCodeAt(i)
        }
        return bytes
      }

// ---- Serialize ----

export interface SerializeInput {
  source: 'sim' | 'browser'
  /** RNG seed of the run — surfaces in the .replay filename / round-trips. */
  seed: number
  /** Simulation metadata (only for source === 'sim'). */
  sim?: {
    seed: number
    difficulty: string
    stageIndex: number
    stageName: string
    outcome: string
    status: ReplayType
    maxTicks: number
    godAIParams?: Record<string, unknown>
  }
  /** Desync detection summary. */
  finalState?: FinalStateEnvelope
  /** Replay data. */
  initialSnapshot: WorldSnapshot
  frames: Uint8Array
  totalTicks: number
  metadata: ReplayMetadata
  /** Desync-locator chain — written only when present (new recordings). */
  tickHashes?: string[]
  /** Ticks between hash checkpoints. */
  hashInterval?: number
}

/**
 * Serialize a replay into the .replay file format (JSON envelope).
 * Returns a string ready for Blob / Bun.write.
 */
export function serializeReplayFile(input: SerializeInput): string {
  // Declare the schema the BYTES actually use, not the newest one this build
  // knows. A single-stream recording is packed as v1 (packFrames / InputRecorder
  // downgrade for backward compat), so stamping the envelope with 0x02 was a
  // lie that made the file unreadable to v1-era readers for no reason.
  const blobSchema = frameSchemaVersionOf(input.frames)
  const envelope: FileEnvelope = {
    format: FORMAT_ID,
    formatVersion: FORMAT_VERSION,
    gameVersion: GAME_VERSION,
    frameSchemaVersion: isSupportedFrameSchema(blobSchema) ? blobSchema : FRAME_SCHEMA_VERSION,
    source: input.source,
    replay: {
      initialSnapshot: input.initialSnapshot,
      framesBase64: toBase64(input.frames),
      totalTicks: input.totalTicks,
      metadata: input.metadata,
      seed: input.seed,
    },
  }

  // Tick-hash chain (plan/Replay-TickHash-Chain.md) — written only for
  // recordings that carry it; legacy files stay byte-identical.
  if (input.tickHashes && input.tickHashes.length > 0) {
    envelope.replay.tickHashes = input.tickHashes
    envelope.replay.hashInterval = input.hashInterval ?? REPLAY_HASH_INTERVAL
  }

  if (input.source === 'sim' && input.sim) {
    envelope.sim = { ...input.sim }
  }
  if (input.finalState) {
    envelope.finalState = { ...input.finalState }
  }

  return JSON.stringify(envelope)
}

// ---- Parse ----

export interface ParseSuccess {
  replay: Replay
  envelope: FileEnvelope
}

export interface ParseError {
  error: string
}

/**
 * Parse a .replay file string back into a Replay object.
 * Validates format, version, and structure. Returns error on any failure.
 */
export function parseReplayFile(text: string): ParseSuccess | ParseError {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    return { error: 'Invalid JSON' }
  }

  const env = raw as Record<string, unknown>

  // Hard validations
  if (env.format !== FORMAT_ID) {
    return { error: `Unknown format: ${String(env.format)}` }
  }
  if (env.formatVersion !== FORMAT_VERSION) {
    return { error: `Unsupported format version: ${String(env.formatVersion)}` }
  }
  // Accept every schema this build can decode — NOT just the newest one.
  // v1 files are still perfectly playable (unpackFrames / ReplayInput handle
  // them), so rejecting them here orphaned real artifacts (DECISIONS #76).
  if (!isSupportedFrameSchema(env.frameSchemaVersion)) {
    return { error: `Unsupported frame schema version: ${String(env.frameSchemaVersion)}` }
  }

  // Soft warning: gameVersion mismatch is non-blocking (L3).
  // The caller can check envelope.gameVersion after a successful parse.

  // Structure validation
  const replay = env.replay as Record<string, unknown> | undefined
  if (!replay) {
    return { error: 'Missing replay section' }
  }
  if (!replay.initialSnapshot) {
    return { error: 'Missing initialSnapshot' }
  }
  if (typeof replay.framesBase64 !== 'string') {
    return { error: 'Missing or invalid framesBase64' }
  }
  if (typeof replay.totalTicks !== 'number') {
    return { error: 'Missing or invalid totalTicks' }
  }

  // Decode frames
  let frames: Uint8Array
  try {
    frames = fromBase64(replay.framesBase64)
  } catch {
    return { error: 'Invalid base64 frames data' }
  }

  // The blob's leading byte is authoritative — the envelope field is only a
  // declaration and older writers got it wrong. Gate on what we must decode.
  const blobSchema = frameSchemaVersionOf(frames)
  if (!isSupportedFrameSchema(blobSchema)) {
    return { error: `Unsupported frame schema version: ${blobSchema}` }
  }

  // Re-derive the standalone P2 stream so an imported coop replay carries the
  // same `frames2` an in-session recording would (InputRecorder produces it).
  const streams = unpackFrames(frames)
  const frames2 = streams?.p2 && streams.p2.length > 0 ? packFrames(streams.p2) : null

  // Rebuild Replay object
  const metadata = (replay.metadata ?? {}) as Partial<ReplayMetadata>
  const type: ReplayType = ((env.sim as SimEnvelope | undefined)?.status as ReplayType) ?? 'clear'
  const durationMs = (replay.totalTicks as number) * (1000 / 60)

  // Tick-hash chain — tolerant of legacy files (absent → undefined) and of
  // malformed values (non-string entries dropped).
  const tickHashes = Array.isArray(replay.tickHashes)
    ? (replay.tickHashes as unknown[]).filter((h): h is string => typeof h === 'string')
    : undefined
  const hashInterval =
    typeof replay.hashInterval === 'number' && (replay.hashInterval as number) > 0
      ? (replay.hashInterval as number)
      : undefined

  const built: Replay = {
    id: generateUUID(),
    type,
    createdAt: Date.now(),
    gameVersion: (env.gameVersion as string) ?? GAME_VERSION,
    schemaVersion: blobSchema,
    seed: (replay.seed as number) ?? (env.sim as SimEnvelope | undefined)?.seed ?? 0,
    initialSnapshot: replay.initialSnapshot as WorldSnapshot,
    frames,
    frames2,
    totalTicks: replay.totalTicks as number,
    durationMs,
    metadata: {
      stage: metadata.stage ?? 0,
      stageName: metadata.stageName ?? '',
      difficulty: metadata.difficulty ?? '',
      lives: metadata.lives ?? 0,
      playerLevel: metadata.playerLevel ?? 0,
      score: metadata.score ?? 0,
      killCount: metadata.killCount ?? 0,
      enemiesTotal: metadata.enemiesTotal ?? 20,
      playTimeMs: metadata.playTimeMs ?? durationMs,
    },
    thumbnail: null,
    isFavorite: false,
    favoriteAt: null,
    tickHashes,
    hashInterval,
  }

  // Reconcile the snapshot's stage index with the authoritative metadata.
  // Sim-generated replays historically recorded the stage via
  // loadStageData(stage, 0), leaving initialSnapshot.stageIndex === 0 even
  // though metadata.stage is correct. Without this, playback restores
  // stageIndex 0 and the HUD shows "STAGE 01" for a later stage (bug: import
  // 的 S32 replay 播放时显示 STAGE 01). metadata.stage is the source of truth.
  if (built.initialSnapshot && built.initialSnapshot.stageIndex !== built.metadata.stage) {
    built.initialSnapshot.stageIndex = built.metadata.stage
  }

  return { replay: built, envelope: env as unknown as FileEnvelope }
}

// ---- Filename builder ----

export interface FilenameParts {
  difficulty: string
  stageIndex: number
  status: ReplayType
  lives: number
  totalTicks: number
  seed: number
}

/**
 * Build a .replay filename from game parameters.
 * Format: <mode>-s<stage>-<status>-l<lifes>-t<seconds>-seed<seed>.replay
 */
export function buildReplayFilename(parts: FilenameParts): string {
  const stage = String(parts.stageIndex + 1).padStart(2, '0')
  const seconds = Math.round(parts.totalTicks / 60)
  return `${parts.difficulty}-s${stage}-${parts.status}-l${parts.lives}-t${seconds}-seed${parts.seed}.replay`
}
