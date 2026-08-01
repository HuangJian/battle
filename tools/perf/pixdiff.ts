/**
 * pixdiff.ts — pixel-identity verification harness for the render optimizations.
 *
 * The render-performance plan (R1–R4) demands *behaviour-preserving* refactors:
 * fewer draw calls / fewer state changes / fewer allocations, but **the rendered
 * image must not change by a single byte**. `render-bench.ts` guards the draw-call
 * *count*; this tool guards the *pixels*.
 *
 * How it works:
 *   1. Build each deterministic scenario exactly as render-bench does.
 *   2. Step the presentation systems frame-by-frame through the same schedule.
 *   3. At fixed checkpoint frames, read the real Skia framebuffer back with
 *      `getImageData` and SHA-256 the raw RGBA bytes.
 *   4. Compare that hash vector against a stored reference (`results/pixref.json`).
 *
 * Usage:
 *   bun tools/perf/pixdiff.ts --write     # capture the reference (do this on the
 *                                         # pre-optimization commit)
 *   bun tools/perf/pixdiff.ts             # verify current code == reference
 *   bun tools/perf/pixdiff.ts --dump      # also emit PNGs of every checkpoint
 *
 * Exit code 1 on any mismatch, so it can be wired into the same gate as the bench.
 */
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { installHeadlessShims, loadSpritesFromDisk } from './headless-canvas'
import {
  SCENARIOS,
  createBenchContext,
  buildBaseWorld,
  updateVisualState,
  DT,
  type BenchContext,
} from './fixtures/render-scenarios'
import { FIELD } from '../../src/constants'

const WRITE = process.argv.includes('--write')
const DUMP = process.argv.includes('--dump')
const ONLY = process.argv.find((a) => a.startsWith('--only='))?.split('=')[1]
const DPR = Number(process.argv.find((a) => a.startsWith('--dpr='))?.split('=')[1] ?? 2)
/** Total frames stepped per scene. Long enough to cycle every animation phase. */
const FRAMES = Number(process.argv.find((a) => a.startsWith('--frames='))?.split('=')[1] ?? 121)
/** Capture a hash every N frames (plus frame 0). */
const EVERY = Number(process.argv.find((a) => a.startsWith('--every='))?.split('=')[1] ?? 12)

const RESULTS_DIR = join(import.meta.dir, 'results')
const REF_PATH = join(RESULTS_DIR, `pixref@dpr${DPR}.json`)

function hashPixels(ctx: any, w: number, h: number): string {
  const img = ctx.getImageData(0, 0, w, h)
  const bytes: Uint8Array = new Uint8Array(
    img.data.buffer,
    img.data.byteOffset,
    img.data.byteLength,
  )
  return createHash('sha256').update(bytes).digest('hex').slice(0, 32)
}

async function main(): Promise<void> {
  installHeadlessShims()
  const sprites = await loadSpritesFromDisk()
  mkdirSync(RESULTS_DIR, { recursive: true })

  const px = FIELD * DPR
  const current: Record<string, string[]> = {}

  console.log(
    `\n=== pixdiff | dpr=${DPR} | frames=${FRAMES} every=${EVERY} | mode=${WRITE ? 'WRITE REF' : 'VERIFY'} ===`,
  )

  for (const [name, def] of Object.entries(SCENARIOS)) {
    if (ONLY && name !== ONLY) continue
    // One fresh surface per scene: pixdiff runs few frames, so the Skia
    // native-resource pressure that forced render-bench to share a surface
    // does not apply here, and a fresh surface removes cross-scene bleed.
    const ctx: BenchContext = createBenchContext(buildBaseWorld(0xc0ffee), sprites, DPR)
    def.populate(ctx)

    const hashes: string[] = []
    for (let f = 0; f < FRAMES; f++) {
      if (def.pan) ctx.camera.setOffset(Math.sin(f / 30) * 8, Math.cos(f / 25) * 8)
      const tanks = ctx.world.allTanks
      updateVisualState(ctx.world, ctx.anim, tanks)
      ctx.anim.update(DT)
      ctx.particles.update(DT)
      ctx.camera.update(DT)
      ctx.effects.update(DT)
      ctx.renderer.render(ctx.world, tanks)

      if (f % EVERY === 0) {
        hashes.push(hashPixels(ctx.target.realCtx, px, px))
        if (DUMP) {
          writeFileSync(
            join(RESULTS_DIR, `pix_${name}_f${String(f).padStart(3, '0')}.png`),
            ctx.target.realCanvas.encode('png'),
          )
        }
      }
      ctx.world.frame++
    }
    current[name] = hashes
    console.log(`${name.padEnd(8)} ${hashes.length} checkpoints  first=${hashes[0]}`)
  }

  if (WRITE) {
    // Preserve scenes not covered by --only so partial writes don't wipe the ref.
    const merged = existsSync(REF_PATH)
      ? { ...JSON.parse(readFileSync(REF_PATH, 'utf8')), ...current }
      : current
    writeFileSync(REF_PATH, JSON.stringify(merged, null, 2))
    console.log(`\n[pixdiff] reference written -> ${REF_PATH}`)
    return
  }

  if (!existsSync(REF_PATH)) {
    console.error(`\n[pixdiff] FAIL: no reference at ${REF_PATH} (run with --write first)`)
    process.exit(1)
  }
  const ref: Record<string, string[]> = JSON.parse(readFileSync(REF_PATH, 'utf8'))
  let bad = 0
  for (const [name, hashes] of Object.entries(current)) {
    const r = ref[name]
    if (!r) {
      console.error(`  ${name}: MISSING from reference`)
      bad++
      continue
    }
    if (r.length !== hashes.length) {
      console.error(`  ${name}: checkpoint count ${hashes.length} != ref ${r.length}`)
      bad++
      continue
    }
    const diffs = hashes.map((h, i) => (h === r[i] ? -1 : i)).filter((i) => i >= 0)
    if (diffs.length) {
      console.error(
        `  ${name}: MISMATCH at checkpoint(s) ${diffs.join(',')} (frames ${diffs
          .map((i) => i * EVERY)
          .join(',')})`,
      )
      for (const i of diffs.slice(0, 3)) {
        console.error(`      ref=${r[i]}  got=${hashes[i]}`)
      }
      bad++
    } else {
      console.log(`  ${name}: pixel-identical (${hashes.length} checkpoints)`)
    }
  }
  if (bad) {
    console.error(`\n[pixdiff] FAIL: ${bad} scene(s) differ from reference`)
    process.exit(1)
  }
  console.log('\n[pixdiff] OK: all scenes pixel-identical to reference')
}

main()
