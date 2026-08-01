import type { Direction } from '../constants'
import type { InputFrame } from './types'
import { FRAME_SCHEMA_VERSION, FRAME_SCHEMA_V1 } from './config'

// ================================================================
// Input Frame Packing — InputFrame ↔ Uint8Array
// (plan/replay.md §3.1, §3.2, §3.3)
//
// Bit layout per byte:
//   bits 0–3: direction (0 = none, 1 = up, 2 = down, 3 = left, 4 = right)
//   bit 4:    firing
//   bit 5:    guard
//   bit 6:    frenzy
//   bit 7:    reserved
//
// v2 adds a flags byte after the version byte:
//   bit 0:    hasP2 — whether the replay includes a second input stream
//   bits 1–7: reserved
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

const DIR_MASK = 0x0f
const FIRE_BIT = 1 << 4
const GUARD_BIT = 1 << 5
const FRENZY_BIT = 1 << 6

/** V2 flags byte bits */
const HAS_P2_BIT = 1 << 0

/**
 * Pack an InputFrame into a single byte.
 */
export function packFrame(frame: InputFrame): number {
  let b = frame.direction !== null ? DIR_TO_PACKED[frame.direction] : 0
  if (frame.firing) b |= FIRE_BIT
  if (frame.guard) b |= GUARD_BIT
  if (frame.frenzy) b |= FRENZY_BIT
  return b
}

/**
 * Unpack a single byte into an InputFrame.
 */
export function unpackFrame(packed: number): InputFrame {
  return {
    direction: PACKED_TO_DIR[packed & DIR_MASK] ?? null,
    firing: (packed & FIRE_BIT) !== 0,
    guard: (packed & GUARD_BIT) !== 0,
    frenzy: (packed & FRENZY_BIT) !== 0,
  }
}

/**
 * Build a packed Uint8Array from an array of InputFrames, prefixed with
 * the schema version byte.
 *
 * v1 (no coop): [0x01][frame0][frame1]...
 * v2 (coop):    [0x02][flags:0x01][p1_frame0][p2_frame0][p1_frame1][p2_frame1]...
 */
export function packFrames(frames: InputFrame[], frames2?: InputFrame[] | null): Uint8Array {
  const hasP2 = frames2 && frames2.length > 0
  if (!hasP2) {
    // v1 format — single stream, no flags byte
    const packed = new Uint8Array(frames.length + 1)
    packed[0] = FRAME_SCHEMA_V1
    for (let i = 0; i < frames.length; i++) {
      packed[i + 1] = packFrame(frames[i])
    }
    return packed
  }

  // v2 format — flags byte + interleaved dual streams
  const tickCount = frames.length
  // [version][flags][p1_0][p2_0][p1_1][p2_1]... = 2 + tickCount * 2
  const packed = new Uint8Array(2 + tickCount * 2)
  packed[0] = FRAME_SCHEMA_VERSION
  packed[1] = HAS_P2_BIT
  for (let i = 0; i < tickCount; i++) {
    const base = 2 + i * 2
    packed[base] = packFrame(frames[i])
    packed[base + 1] = packFrame(
      frames2![i] ?? { direction: null, firing: false, guard: false, frenzy: false },
    )
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
export function unpackFrames(
  data: Uint8Array,
): { p1: InputFrame[]; p2: InputFrame[] | null } | null {
  if (data.length < 1) return null
  const schemaVersion = data[0]

  if (schemaVersion === FRAME_SCHEMA_V1) {
    // v1: single stream
    const frames: InputFrame[] = []
    for (let i = 1; i < data.length; i++) {
      frames.push(unpackFrame(data[i]))
    }
    return { p1: frames, p2: null }
  }

  if (schemaVersion === FRAME_SCHEMA_VERSION) {
    // v2: check flags byte
    if (data.length < 2) return null
    const flags = data[1]
    const hasP2 = (flags & HAS_P2_BIT) !== 0

    if (!hasP2) {
      // v2 without P2 — single stream after flags byte
      const frames: InputFrame[] = []
      for (let i = 2; i < data.length; i++) {
        frames.push(unpackFrame(data[i]))
      }
      return { p1: frames, p2: null }
    }

    // v2 with interleaved dual streams
    const tickCount = (data.length - 2) / 2
    const p1: InputFrame[] = []
    const p2: InputFrame[] = []
    for (let i = 0; i < tickCount; i++) {
      const base = 2 + i * 2
      p1.push(unpackFrame(data[base]))
      p2.push(unpackFrame(data[base + 1]))
    }
    return { p1, p2 }
  }

  return null
}
