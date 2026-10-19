import type { Direction } from '../constants'
import type { InputFrame } from './types'
import { FRAME_SCHEMA_V1, FRAME_SCHEMA_V2, FRAME_SCHEMA_V3, FRAME_SCHEMA_VERSION } from './config'

// ================================================================
// Input Frame Packing — InputFrame ↔ Uint8Array
// (plan/replay.md §3.1, §3.2, §3.3)
//
// v1/v2 bit layout per byte:
//   bits 0–3: direction (0 = none, 1 = up, 2 = down, 3 = left, 4 = right)
//   bit 4:    firing
//   bit 5:    guard
//   bit 6:    frenzy
//   bit 7:    reserved
//
// v3 reclaims the unused direction bit (only 0–4 are valid) for the two
// NN-only semantics, keeping the blob at one byte per tick:
//   bits 0–2: direction (0..4)
//   bit 3:    firing
//   bit 4:    guard
//   bit 5:    frenzy
//   bit 6:    hold            (turn-only: request dir, do not advance)
//   bit 7:    fireBeforeTurn  (fire along the pre-turn facing, then turn)
//
// v2 adds a flags byte after the version byte:
//   bit 0:    hasP2 — whether the replay includes a second input stream
//   bits 1–7: reserved
//
// v3 keeps the same flags byte, so `[0x03][flags][p1][p2?]…` stays
// byte-for-byte compatible with the v2 container layout. A single stream
// that carries hold/fireBeforeTurn must be written as v3 (a v1 header
// would silently drop both bits); a stream with only legacy semantics
// stays v1 for older readers.
// ================================================================

/** Direction → packed value mapping. */
const DIR_TO_PACKED: Record<Direction, number> = {
  up: 1,
  down: 2,
  left: 3,
  right: 4,
}

/** Packed value → direction mapping. */
const PACKED_TO_DIR: Record<number, Direction | null> = {
  0: null,
  1: 'up',
  2: 'down',
  3: 'left',
  4: 'right',
}

// v1/v2 layout
const DIR_MASK = 0x0f
const FIRE_BIT = 1 << 4
const GUARD_BIT = 1 << 5
const FRENZY_BIT = 1 << 6

// v3 layout
const V3_DIR_MASK = 0x07
const V3_FIRE_BIT = 1 << 3
const V3_GUARD_BIT = 1 << 4
const V3_FRENZY_BIT = 1 << 5
const V3_HOLD_BIT = 1 << 6
const V3_FIRE_BEFORE_TURN_BIT = 1 << 7

/** V2/V3 flags byte bits */
const HAS_P2_BIT = 1 << 0
/** V3-only: the stream was recorded from an input using hold/fire(3) semantics. */
const P1_HOLD_FIRE3_BIT = 1 << 1
const P2_HOLD_FIRE3_BIT = 1 << 2

export interface FrameActionFlags {
  /** P1 was recorded from an input declaring `usesHoldFire3()`. */
  p1HoldFire3?: boolean
  /** P2 was recorded from an input declaring `usesHoldFire3()` (coop only). */
  p2HoldFire3?: boolean
}

const IDLE_FRAME: InputFrame = {
  direction: null,
  firing: false,
  guard: false,
  frenzy: false,
  hold: false,
  fireBeforeTurn: false,
}

/** True when any frame uses a v3-only bit. */
function needsV3(frames: InputFrame[]): boolean {
  for (const f of frames) {
    if (f.hold || f.fireBeforeTurn) return true
  }
  return false
}

/**
 * Pack an InputFrame into a single byte.
 *
 * Defaults to the current (v3) layout; pass an older schema to write the
 * historical bit layout (used by tests and compatibility fixtures).
 */
export function packFrame(frame: InputFrame, schema: number = FRAME_SCHEMA_VERSION): number {
  if (schema >= FRAME_SCHEMA_V3) {
    let b = frame.direction !== null ? DIR_TO_PACKED[frame.direction] : 0
    if (frame.firing) b |= V3_FIRE_BIT
    if (frame.guard) b |= V3_GUARD_BIT
    if (frame.frenzy) b |= V3_FRENZY_BIT
    if (frame.hold) b |= V3_HOLD_BIT
    if (frame.fireBeforeTurn) b |= V3_FIRE_BEFORE_TURN_BIT
    return b
  }
  let b = frame.direction !== null ? DIR_TO_PACKED[frame.direction] : 0
  if (frame.firing) b |= FIRE_BIT
  if (frame.guard) b |= GUARD_BIT
  if (frame.frenzy) b |= FRENZY_BIT
  return b
}

/**
 * Unpack a single byte into an InputFrame.
 *
 * Defaults to the current (v3) layout; pass the blob's schema version for
 * v1/v2 blobs.
 */
export function unpackFrame(packed: number, schema: number = FRAME_SCHEMA_VERSION): InputFrame {
  if (schema >= FRAME_SCHEMA_V3) {
    return {
      direction: PACKED_TO_DIR[packed & V3_DIR_MASK] ?? null,
      firing: (packed & V3_FIRE_BIT) !== 0,
      guard: (packed & V3_GUARD_BIT) !== 0,
      frenzy: (packed & V3_FRENZY_BIT) !== 0,
      hold: (packed & V3_HOLD_BIT) !== 0,
      fireBeforeTurn: (packed & V3_FIRE_BEFORE_TURN_BIT) !== 0,
    }
  }
  return {
    direction: PACKED_TO_DIR[packed & DIR_MASK] ?? null,
    firing: (packed & FIRE_BIT) !== 0,
    guard: (packed & GUARD_BIT) !== 0,
    frenzy: (packed & FRENZY_BIT) !== 0,
    hold: false,
    fireBeforeTurn: false,
  }
}

/**
 * Build a packed Uint8Array from an array of InputFrames, prefixed with
 * the schema version byte.
 *
 * v1 (no coop, legacy semantics):   [0x01][frame0][frame1]...
 * v2 (coop, legacy semantics):      [0x02][flags:0x01][p1_0][p2_0]...
 * v3 (coop or NN-only bits):        [0x03][flags:hasP2][p1_0][p2_0?]...
 *
 * `flags` is present in v2/v3; only bit 0 (hasP2) is currently defined.
 * A single stream carrying hold/fireBeforeTurn is written as v3 with
 * hasP2=0 — writing v1 would silently erase both bits (review P0-1).
 */
export function packFrames(
  frames: InputFrame[],
  frames2?: InputFrame[] | null,
  actionFlags?: FrameActionFlags,
): Uint8Array {
  const hasP2 = !!(frames2 && frames2.length > 0)
  // `hold`/`fireBeforeTurn` bits imply v3 semantics; coop alone only implies
  // the v3 container. The flags byte records the *action contract* separately
  // so a legacy God-AI coop stream is not replayed through the NN fire path.
  const p1V3 = !!actionFlags?.p1HoldFire3 || needsV3(frames)
  const p2V3 = !!actionFlags?.p2HoldFire3 || (frames2 ? needsV3(frames2) : false)
  const v3 = hasP2 || p1V3 || p2V3

  if (!v3) {
    // v1 format — single stream, no flags byte
    const packed = new Uint8Array(frames.length + 1)
    packed[0] = FRAME_SCHEMA_V1
    for (let i = 0; i < frames.length; i++) {
      packed[i + 1] = packFrame(frames[i], FRAME_SCHEMA_V1)
    }
    return packed
  }

  // v3 format — flags byte + optional interleaved dual streams
  const tickCount = frames.length
  const flags =
    (hasP2 ? HAS_P2_BIT : 0) | (p1V3 ? P1_HOLD_FIRE3_BIT : 0) | (p2V3 ? P2_HOLD_FIRE3_BIT : 0)
  if (!hasP2) {
    const packed = new Uint8Array(2 + tickCount)
    packed[0] = FRAME_SCHEMA_V3
    packed[1] = flags
    for (let i = 0; i < tickCount; i++) {
      packed[2 + i] = packFrame(frames[i], FRAME_SCHEMA_V3)
    }
    return packed
  }

  // [version][flags][p1_0][p2_0][p1_1][p2_1]... = 2 + tickCount * 2
  const packed = new Uint8Array(2 + tickCount * 2)
  packed[0] = FRAME_SCHEMA_V3
  packed[1] = flags
  for (let i = 0; i < tickCount; i++) {
    const base = 2 + i * 2
    packed[base] = packFrame(frames[i], FRAME_SCHEMA_V3)
    packed[base + 1] = packFrame(frames2![i] ?? IDLE_FRAME, FRAME_SCHEMA_V3)
  }
  return packed
}

/**
 * The schema version a packed-frame blob actually carries — its leading byte.
 *
 * This is the ONLY authoritative answer to "what layout are these bytes?".
 * Envelope fields and `Replay.schemaVersion` are descriptive metadata that
 * historically drifted from the blob (older builds always wrote 0x02, even
 * for a v1 single-stream blob). Returns 0 for an empty/missing blob.
 */
export function frameSchemaVersionOf(data: Uint8Array | null | undefined): number {
  return data && data.length > 0 ? data[0] : 0
}

/**
 * Unpack a Uint8Array (prefixed with schema version byte) into InputFrames.
 * Returns { p1, p2 } where p2 is null for v1 replays or when no coop data.
 * Returns null if the schema version is unrecognized.
 */
export function unpackFrames(data: Uint8Array): {
  p1: InputFrame[]
  p2: InputFrame[] | null
  /** v3 streams recorded from a hold/fire(3) input; legacy streams are false. */
  p1HoldFire3: boolean
  p2HoldFire3: boolean
} | null {
  if (data.length < 1) return null
  const schemaVersion = data[0]

  if (schemaVersion === FRAME_SCHEMA_V1) {
    // v1: single stream
    const frames: InputFrame[] = []
    for (let i = 1; i < data.length; i++) {
      frames.push(unpackFrame(data[i], FRAME_SCHEMA_V1))
    }
    return { p1: frames, p2: null, p1HoldFire3: false, p2HoldFire3: false }
  }

  if (schemaVersion === FRAME_SCHEMA_V2 || schemaVersion === FRAME_SCHEMA_V3) {
    // v2/v3: flags byte (bit 0 = hasP2, bits 1/2 = per-player hold/fire3)
    if (data.length < 2) return null
    const flags = data[1]
    const hasP2 = (flags & HAS_P2_BIT) !== 0
    const v3 = schemaVersion === FRAME_SCHEMA_V3
    const p1HoldFire3 = v3 && (flags & P1_HOLD_FIRE3_BIT) !== 0
    const p2HoldFire3 = v3 && (flags & P2_HOLD_FIRE3_BIT) !== 0

    if (!hasP2) {
      // single stream after flags byte
      const frames: InputFrame[] = []
      for (let i = 2; i < data.length; i++) {
        frames.push(unpackFrame(data[i], schemaVersion))
      }
      return { p1: frames, p2: null, p1HoldFire3, p2HoldFire3 }
    }

    // interleaved dual streams
    const tickCount = (data.length - 2) / 2
    const p1: InputFrame[] = []
    const p2: InputFrame[] = []
    for (let i = 0; i < tickCount; i++) {
      const base = 2 + i * 2
      p1.push(unpackFrame(data[base], schemaVersion))
      p2.push(unpackFrame(data[base + 1], schemaVersion))
    }
    return { p1, p2, p1HoldFire3, p2HoldFire3 }
  }

  return null
}
