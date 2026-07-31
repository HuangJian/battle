/**
 * render-bench.ts — headless render benchmark (R0 of plan/render-performance.plan.md)
 *
 * Drives GameRenderer.render() over four deterministic World fixtures and reports,
 * per scene: wall-time distribution (median/p95/IQR/min), draw-calls/frame and
 * save/restore-pairs/frame. The draw-call/saveRestore tallies are backend-agnostic
 * (counted by a Proxy on the main 2D context) and must be byte-identical across
 * repeated runs — that is the regression signal used by the CI gate (plan §8).
 *
 * Run:  bun tools/perf/render-bench.ts [--frames=2000] [--warmup=200] [--repeat=3]
 *       [--snapshot] [--list]
 */
import { writeFileSync, mkdirSync } from 'node:fs'
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

const FRAMES = Number(process.argv.find((a) => a.startsWith('--frames='))?.split('=')[1] ?? 2000)
const WARMUP = Number(process.argv.find((a) => a.startsWith('--warmup='))?.split('=')[1] ?? 200)
const REPEAT = Number(process.argv.find((a) => a.startsWith('--repeat='))?.split('=')[1] ?? 3)
const SNAP = process.argv.includes('--snapshot')
const LIST = process.argv.includes('--list')
const ONLY = process.argv.find((a) => a.startsWith('--only='))?.split('=')[1]
const LOW_QUALITY = process.argv.includes('--low-quality')
// Real browsers render at DPR=2 (the default here). The target low-end machine
// (e.g. X220i / Intel HD 3000) is exactly where per-pixel blit cost at high
// resolution hurts most, so DPR=2 is the representative dimension for wall-time.
// The draw-call regression gate is DPR-invariant (the renderer issues the same
// number of draw calls regardless of DPR — only the pixel area changes), so the
// CI signal is identical at any DPR. DPR=1 remains available via --dpr=1 purely
// as a manual cross-check of that invariance.
const DPR = Number(process.argv.find((a) => a.startsWith('--dpr='))?.split('=')[1] ?? 2)

// --- stats ------------------------------------------------------------------

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0
  const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))
  return sorted[idx]
}

function stats(arr: number[]) {
  const s = [...arr].sort((a, b) => a - b)
  const q1 = percentile(s, 25)
  const q3 = percentile(s, 75)
  return {
    min: s[0],
    median: percentile(s, 50),
    p95: percentile(s, 95),
    mean: s.reduce((a, b) => a + b, 0) / s.length,
    iqr: q3 - q1,
  }
}

// --- main -------------------------------------------------------------------

async function main(): Promise<void> {
  if (LIST) {
    for (const [name, def] of Object.entries(SCENARIOS)) {
      console.log(`${name.padEnd(8)} ${def.desc}`)
    }
    return
  }

  installHeadlessShims()
  const sprites = await loadSpritesFromDisk()

  console.log(
    `\n=== render-bench | sprites=${sprites.size} | dpr=${DPR} | frames=${FRAMES} warmup=${WARMUP} repeat=${REPEAT} ===`,
  )
  console.log(
    `${'scene'.padEnd(8)}${'frames'.padStart(7)}${'wall(ms)'.padStart(11)}${'perFrame(ms)'.padStart(
      13,
    )}${'p95(ms)'.padStart(10)}${'draw/f'.padStart(9)}${'saveRest/f'.padStart(11)}${'determ'.padStart(9)}`,
  )

  const allResults: Record<string, any> = {}

  // ONE render surface for the whole bench. The SpriteCache (≈36 rasterized
  // sprites) and the three offscreen caches are the expensive native resources
  // under the Skia software backend; creating them per-scenario/repeat exhausts
  // native memory and aborts the process (exit 127). We build them exactly once
  // and reuse across every scene, only rebuilding the lightweight World (entities)
  // and resetting the particle/camera state between scenes.
  const surface = createBenchContext(buildBaseWorld(0xc0ffee), sprites, DPR)
  // Apply low-quality mode (skip vignette + shadow) for the perf-mode bench.
  if (LOW_QUALITY) surface.renderer.lowQuality = true

  for (const [name, def] of Object.entries(SCENARIOS)) {
    if (ONLY && name !== ONLY) continue
    // At DPR>1 the Skia software backend in @napi-rs/canvas trips a native crash
    // (exit 127) when the heavy `burst` scene renders many frames at 4x pixel area.
    // This is a headless-harness limit, NOT a game bug (real GPUs handle DPR=2 +
    // dozens of particles trivially). Cap that one scene's frame budget so the
    // baseline stays reproducible; draw counts are unaffected (DPR-invariant).
    const FRAMES_EFF = DPR > 1 && name === 'burst' ? Math.min(FRAMES, 800) : FRAMES
    const repeatDraw: number[] = []
    const repeatSR: number[] = []
    const times: number[] = []

    for (let r = 0; r < REPEAT; r++) {
      const world = buildBaseWorld(0xc0ffee) // identical world across repeats → byte-identical stream
      const ctx: BenchContext = { ...surface, world }
      // Reset cross-scene state on the shared systems (same objects the renderer holds).
      // anim components are keyed by tank id and must be cleared or stale entries from a
      // prior scene's world would be traversed during the next scene's render.
      ctx.particles.clear()
      ctx.camera.reset()
      ;(ctx.anim as any).components?.clear?.()
      def.populate(ctx)
      const counts = ctx.target.counts

      let drawSum = 0
      let srSum = 0
      for (let f = 0; f < FRAMES_EFF + WARMUP; f++) {
        if (def.pan) ctx.camera.setOffset(Math.sin(f / 30) * 8, Math.cos(f / 25) * 8)
        // Mirror PresentationLayer.render: one allTanks rebuild, threaded to
        // both consumers (R1/P1-B).
        const tanks = ctx.world.allTanks
        updateVisualState(ctx.world, ctx.anim, tanks)
        ctx.anim.update(DT)
        ctx.particles.update(DT)
        ctx.camera.update(DT)
        ctx.effects.update(DT)

        counts.draw = 0
        counts.saveRestore = 0
        const t0 = performance.now()
        ctx.renderer.render(ctx.world, tanks)
        const dt = performance.now() - t0

        if (f >= WARMUP) {
          times.push(dt)
          drawSum += counts.draw
          srSum += counts.saveRestore
        }
        ctx.world.frame++
      }
      repeatDraw.push(drawSum)
      repeatSR.push(srSum)
    }

    const st = stats(times)
    const perFrameDraw = repeatDraw.reduce((a, b) => a + b, 0) / (REPEAT * FRAMES_EFF)
    const perFrameSR = repeatSR.reduce((a, b) => a + b, 0) / (REPEAT * FRAMES_EFF)
    const determ =
      repeatDraw.every((v) => v === repeatDraw[0]) && repeatSR.every((v) => v === repeatSR[0])
    const wall = st.median * FRAMES_EFF

    console.log(
      `${name.padEnd(8)}${String(FRAMES_EFF).padStart(7)}${wall.toFixed(1).padStart(11)}${st.median
        .toFixed(4)
        .padStart(
          13,
        )}${st.p95.toFixed(4).padStart(10)}${perFrameDraw.toFixed(1).padStart(9)}${perFrameSR
        .toFixed(1)
        .padStart(11)}${(determ ? 'OK' : 'MISMATCH').padStart(9)}`,
    )

    allResults[name] = {
      desc: def.desc,
      frames: FRAMES_EFF,
      dpr: DPR,
      perFrameMs: +st.median.toFixed(4),
      p95Ms: +st.p95.toFixed(4),
      minMs: +st.min.toFixed(4),
      iqrMs: +st.iqr.toFixed(4),
      drawCallsPerFrame: +perFrameDraw.toFixed(2),
      saveRestorePerFrame: +perFrameSR.toFixed(2),
      deterministic: determ,
      repeatDraw,
      repeatSR,
    }

    if (SNAP) {
      const snapWorld = buildBaseWorld(0xc0ffee)
      const snapCtx = createBenchContext(snapWorld, sprites, DPR)
      def.populate(snapCtx)
      const snapTanks = snapCtx.world.allTanks
      updateVisualState(snapCtx.world, snapCtx.anim, snapTanks)
      snapCtx.renderer.render(snapCtx.world, snapTanks)
      const png = snapCtx.target.realCanvas.encode('png')
      const outDir = join(import.meta.dir, 'results')
      mkdirSync(outDir, { recursive: true })
      const outPath = join(outDir, `${name}.png`)
      writeFileSync(outPath, png)
      console.log(`  snapshot -> ${outPath}`)
    }
  }

  // Persist results for CI / progress tracking. Each scene is also written to its
  // own file so a scenario-per-process driver (render-bench-all.ts) can aggregate
  // them — this avoids the Skia software backend's native crash when one process
  // renders many different Worlds on a single surface (see plan §5.4 / review note).
  const outDir = join(import.meta.dir, 'results')
  mkdirSync(outDir, { recursive: true })
  // dpr=1 keeps the legacy `<scene>.json` name (backward-compatible CI gate);
  // higher DPRs suffix `@dpr<n>` so both coexist as fixed snapshots.
  const dprTag = DPR === 1 ? '' : `@dpr${DPR}`
  for (const [name, r] of Object.entries(allResults)) {
    writeFileSync(join(outDir, `${name}${dprTag}.json`), JSON.stringify({ [name]: r }, null, 2))
  }
  writeFileSync(join(outDir, `render-bench${dprTag}.json`), JSON.stringify(allResults, null, 2))

  // CI gate: draw-call/saveRestore streams must be byte-identical across repeats.
  const allDeterministic = Object.values(allResults).every((r: any) => r.deterministic)
  if (!allDeterministic) {
    console.error('\n[render-bench] FAIL: draw-call stream not deterministic across repeats')
    process.exit(1)
  }
  console.log('\n[render-bench] OK: all scenes deterministic')
}

main()
