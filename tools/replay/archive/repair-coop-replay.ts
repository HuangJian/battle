/**
 * repair-coop-replay.ts — salvage coop (Lie-Back-Win-Mode) `.replay` files
 * recorded before the AutoFireInput recording fix.
 *
 * The old Game.ts recorded the RAW keyboard input instead of the
 * AutoFireInput-decorated input the Simulation actually consumed, so every
 * auto-fired shot was dropped from the P1 stream.
 *
 * The loss is exactly reconstructible, because AutoFireInput is deterministic:
 *
 *   armed = true at stage start (one recording session == one stage)
 *   while armed  -> isFiring() === true  on EVERY tick
 *   armed clears -> permanently, on the first tick the human really pressed fire
 *   after that   -> isFiring() === inner.isFiring()   (== what was recorded)
 *
 * So if T is the first tick with a recorded P1 fire bit (the human's first real
 * press), the true stream is `fire = true` for ticks 0..T-1 and the recorded
 * value from T onward. If the human never pressed fire, every tick was
 * auto-fire. Movement / item bits were always pass-through and are untouched.
 *
 * Usage:
 *   bun tools/replay/repair-coop-replay.ts <in.replay> [-o out.replay]
 */

const FIRE_BIT = 0x10

export interface RepairReport {
  repaired: boolean
  reason: string
  totalTicks: number
  firstHumanFireTick: number | null
  bitsAdded: number
}

/** Repair the packed v2 frame buffer in place. Returns a report. */
export function repairFrames(frames: Uint8Array): RepairReport {
  if (frames.length < 2 || frames[0] !== 2) {
    return {
      repaired: false,
      reason: 'not a v2 frame stream (coop replays are v2)',
      totalTicks: 0,
      firstHumanFireTick: null,
      bitsAdded: 0,
    }
  }
  if ((frames[1] & 0x01) !== 0x01) {
    return {
      repaired: false,
      reason: 'hasP2 flag not set — not a coop recording',
      totalTicks: 0,
      firstHumanFireTick: null,
      bitsAdded: 0,
    }
  }

  const ticks = (frames.length - 2) / 2
  // Locate the human's first real fire press.
  let firstFire = -1
  for (let i = 0; i < ticks; i++) {
    if (frames[2 + i * 2] & FIRE_BIT) {
      firstFire = i
      break
    }
  }
  if (firstFire === 0) {
    return {
      repaired: false,
      reason: 'P1 already fires on tick 0 — stream looks already correct',
      totalTicks: ticks,
      firstHumanFireTick: 0,
      bitsAdded: 0,
    }
  }

  const upTo = firstFire === -1 ? ticks : firstFire
  let added = 0
  for (let i = 0; i < upTo; i++) {
    const idx = 2 + i * 2
    if ((frames[idx] & FIRE_BIT) === 0) {
      frames[idx] |= FIRE_BIT
      added++
    }
  }
  return {
    repaired: added > 0,
    reason:
      firstFire === -1
        ? 'human never pressed fire — auto-fire was armed for the whole stage'
        : `auto-fire was armed for ticks 0..${firstFire - 1}`,
    totalTicks: ticks,
    firstHumanFireTick: firstFire === -1 ? null : firstFire,
    bitsAdded: added,
  }
}

function toBase64(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin)
}

function fromBase64(b64: string): Uint8Array {
  const bin = atob(b64)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

/** Repair a `.replay` file's JSON text. Returns null when nothing to do. */
export function repairReplayText(text: string): { text: string; report: RepairReport } | null {
  const env = JSON.parse(text)
  const b64 = env?.replay?.framesBase64
  if (typeof b64 !== 'string') return null
  const frames = fromBase64(b64)
  const report = repairFrames(frames)
  if (!report.repaired) return { text, report }
  env.replay.framesBase64 = toBase64(frames)
  return { text: JSON.stringify(env), report }
}

if (import.meta.main) {
  const args = process.argv.slice(2)
  const oIdx = args.indexOf('-o')
  const out = oIdx >= 0 ? args[oIdx + 1] : null
  const file = args.filter((a, i) => !a.startsWith('-') && !(oIdx >= 0 && i === oIdx + 1))[0]
  if (!file) {
    console.error('usage: bun tools/replay/repair-coop-replay.ts <in.replay> [-o out.replay]')
    process.exit(2)
  }
  const res = repairReplayText(await Bun.file(file).text())
  if (!res) {
    console.error('could not read frames from', file)
    process.exit(2)
  }
  console.log(
    `${file}\n  ticks=${res.report.totalTicks} firstHumanFire=${res.report.firstHumanFireTick}\n  ${res.report.reason}\n  fire bits restored: ${res.report.bitsAdded}`,
  )
  if (res.report.repaired) {
    const dest = out ?? file.replace(/\.replay$/, '.repaired.replay')
    await Bun.write(dest, res.text)
    console.log(`  written: ${dest}`)
  }
}
