import type { WorldSnapshot } from '../snapshot/types'
import { GAME_VERSION } from '../snapshot/config'
import { FRAME_SCHEMA_VERSION } from './config'
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
}

/**
 * Serialize a replay into the .replay file format (JSON envelope).
 * Returns a string ready for Blob / Bun.write.
 */
export function serializeReplayFile(input: SerializeInput): string {
  const envelope: FileEnvelope = {
    format: FORMAT_ID,
    formatVersion: FORMAT_VERSION,
    gameVersion: GAME_VERSION,
    frameSchemaVersion: FRAME_SCHEMA_VERSION,
    source: input.source,
    replay: {
      initialSnapshot: input.initialSnapshot,
      framesBase64: toBase64(input.frames),
      totalTicks: input.totalTicks,
      metadata: input.metadata,
    },
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
  if (env.frameSchemaVersion !== FRAME_SCHEMA_VERSION) {
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

  // Rebuild Replay object
  const metadata = (replay.metadata ?? {}) as Partial<ReplayMetadata>
  const type: ReplayType = (env.sim as SimEnvelope | undefined)?.status as ReplayType ?? 'clear'
  const durationMs = (replay.totalTicks as number) * (1000 / 60)

  const built: Replay = {
    id: generateUUID(),
    type,
    createdAt: Date.now(),
    gameVersion: (env.gameVersion as string) ?? GAME_VERSION,
    schemaVersion: FRAME_SCHEMA_VERSION,
    initialSnapshot: replay.initialSnapshot as WorldSnapshot,
    frames,
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
