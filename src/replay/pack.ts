import type { Direction } from '../constants'
import type { InputFrame } from './types'
import { FRAME_SCHEMA_VERSION } from './config'

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
 */
export function packFrames(frames: InputFrame[]): Uint8Array {
  const packed = new Uint8Array(frames.length + 1)
  packed[0] = FRAME_SCHEMA_VERSION
  for (let i = 0; i < frames.length; i++) {
    packed[i + 1] = packFrame(frames[i])
  }
  return packed
}

/**
 * Unpack a Uint8Array (prefixed with schema version byte) into InputFrames.
 * Returns null if the schema version is unrecognized.
 */
export function unpackFrames(data: Uint8Array): InputFrame[] | null {
  if (data.length < 1) return null
  const schemaVersion = data[0]
  if (schemaVersion !== FRAME_SCHEMA_VERSION) return null

  const frames: InputFrame[] = []
  for (let i = 1; i < data.length; i++) {
    frames.push(unpackFrame(data[i]))
  }
  return frames
}
