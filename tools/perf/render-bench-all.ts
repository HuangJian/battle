/**
 * render-bench-all.ts — scenario-per-process driver for render-bench.
 *
 * Each scene is run in its OWN bun process. This is deliberate: the @napi-rs/canvas
 * (Skia) software backend crashes natively (exit 127) when a single process renders
 * many different Worlds on one shared surface — a harness/environment artifact, not a
 * game bug (single-scene runs are stable; see plan §5.4 note). Running each scene in a
 * fresh process also matches the natural CI shape (one job per scenario).
 *
 * Default dimension is DPR=2 (real browsers). The draw-call regression signal is
 * DPR-invariant, so a single DPR captures it. `--both` additionally runs DPR=1 and
 * prints the perFrame pixel-pressure ratio (a diagnostic for choosing R5 candidates).
 *
 * Usage: bun tools/perf/render-bench-all.ts [--frames=2000] [--warmup=200] [--repeat=3]
 *        [--dpr=2] [--both]
 */
import { spawnSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const SCENES = ['idle', 'combat', 'burst', 'pan']
const FRAMES = process.argv.find((a) => a.startsWith('--frames='))?.split('=')[1] ?? '2000'
const WARMUP = process.argv.find((a) => a.startsWith('--warmup='))?.split('=')[1] ?? '200'
const REPEAT = process.argv.find((a) => a.startsWith('--repeat='))?.split('=')[1] ?? '3'
const BOTH = process.argv.includes('--both')
const LOW_QUALITY = process.argv.includes('--low-quality')
const DPR_DEFAULT = Number(process.argv.find((a) => a.startsWith('--dpr='))?.split('=')[1] ?? 2)
const DPRS = BOTH ? [1, 2] : [DPR_DEFAULT]

const dir = import.meta.dir
const root = join(dir, '../..')
const combined: Record<string, any> = {}
let allDeterministic = true

for (const s of SCENES) {
  combined[s] = {}
  for (const dpr of DPRS) {
    const tag = dpr === 1 ? '' : `@dpr${dpr}`
    console.error(
      `[render-bench-all] running ${s} (dpr=${dpr}, frames=${FRAMES} repeat=${REPEAT})...`,
    )
    const res = spawnSync(
      'bun',
      [
        'tools/perf/render-bench.ts',
        `--only=${s}`,
        `--dpr=${dpr}`,
        `--frames=${FRAMES}`,
        `--warmup=${WARMUP}`,
        `--repeat=${REPEAT}`,
        ...(LOW_QUALITY ? ['--low-quality'] : []),
      ],
      { cwd: root, encoding: 'utf8' },
    )
    if (res.status !== 0) {
      console.error(`[render-bench-all] ${s}@dpr${dpr} FAILED (exit ${res.status})`)
      console.error((res.stderr || '').split('\n').slice(-25).join('\n'))
      process.exit(1)
    }
    const json = JSON.parse(
      readFileSync(join(dir, 'results', `${s}${tag}.json`), 'utf8'),
    ) as Record<string, any>
    combined[s][`dpr${dpr}`] = json[s]
    if (!json[s].deterministic) allDeterministic = false
  }
  // DPR-invariance cross-check (only meaningful when both ran): draw/f must match.
  if (BOTH) {
    const d1 = combined[s].dpr1.drawCallsPerFrame
    const d2 = combined[s].dpr2.drawCallsPerFrame
    if (d1 !== d2) {
      console.error(`[render-bench-all] WARN ${s}: draw/f differs across DPR (${d1} vs ${d2})`)
    }
  }
}

if (BOTH) {
  console.log(
    `\n=== render-bench (per-scene processes) | frames=${FRAMES} warmup=${WARMUP} repeat=${REPEAT} | DPR=1 vs DPR=2 ===`,
  )
  console.log(
    `${'scene'.padEnd(8)}${'frames'.padStart(7)}${'pF@1'.padStart(10)}${'pF@2'.padStart(10)}${'pRatio'.padStart(8)}${'draw/f'.padStart(9)}${'saveRest/f'.padStart(11)}${'determ'.padStart(9)}`,
  )
  for (const s of SCENES) {
    const a = combined[s].dpr1
    const b = combined[s].dpr2
    const ratio = b.perFrameMs / a.perFrameMs
    console.log(
      `${s.padEnd(8)}${String(b.frames).padStart(7)}${a.perFrameMs.toFixed(4).padStart(10)}${b.perFrameMs.toFixed(4).padStart(10)}${ratio.toFixed(2) + 'x'.padStart(3)}${a.drawCallsPerFrame.toFixed(1).padStart(9)}${a.saveRestorePerFrame.toFixed(1).padStart(11)}${(a.deterministic && b.deterministic ? 'OK' : 'MISMATCH').padStart(9)}`,
    )
  }
} else {
  const dpr = DPR_DEFAULT
  const lqTag = LOW_QUALITY ? ' | LOW-QUALITY' : ''
  console.log(
    `\n=== render-bench (per-scene processes) | frames=${FRAMES} warmup=${WARMUP} repeat=${REPEAT} | dpr=${dpr}${lqTag} ===`,
  )
  console.log(
    `${'scene'.padEnd(8)}${'frames'.padStart(7)}${'wall(ms)'.padStart(11)}${'perFrame(ms)'.padStart(13)}${'p95(ms)'.padStart(10)}${'draw/f'.padStart(9)}${'saveRest/f'.padStart(11)}${'determ'.padStart(9)}`,
  )
  for (const s of SCENES) {
    const r = combined[s][`dpr${dpr}`]
    const wall = r.perFrameMs * r.frames
    console.log(
      `${s.padEnd(8)}${String(r.frames).padStart(7)}${wall.toFixed(1).padStart(11)}${r.perFrameMs.toFixed(4).padStart(13)}${r.p95Ms.toFixed(4).padStart(10)}${r.drawCallsPerFrame.toFixed(1).padStart(9)}${r.saveRestorePerFrame.toFixed(1).padStart(11)}${(r.deterministic ? 'OK' : 'MISMATCH').padStart(9)}`,
    )
  }
}

writeFileSync(join(dir, 'results', 'render-bench-all.json'), JSON.stringify(combined, null, 2))
console.log(
  allDeterministic
    ? `\n[render-bench-all] OK: all scenes deterministic${BOTH ? ' at both DPRs' : ` at dpr=${DPR_DEFAULT}`}`
    : '\n[render-bench-all] FAIL: non-deterministic',
)
process.exit(allDeterministic ? 0 : 1)
