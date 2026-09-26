/**
 * bench-nn-infer.ts — P0 empirical throughput benchmark for the pure-TS
 * NN forward pass (plan/RL-Net-Selection.md §2.3 / §9 P0-1).
 *
 * Measures the REAL wall-clock cost of `NNModel.forward` using the existing
 * BC weights (52K / 30.77M MACs). This replaces the previously-fabricated
 * "BC already runs in the browser" assertion with a measured constant.
 *
 * Scope: forward pass ONLY (model arithmetic). The obs-encoder cost is a
 * separate measurement (plan: estimated <1% of forward, to be confirmed
 * when the NN player is wired into the runtime).
 *
 * Run:  bun tools/bench-nn-infer.ts [weightsPath]
 *       bun tools/bench-nn-infer.ts --print-params [weightsPath]   # JSON budget
 * Default weights: the best BC v2 (val 0.9984).
 */
import { readFileSync } from 'fs'
import { buildModelFromText } from '../src/nn/infer.ts'

// BC model arithmetic (MAdds = MACs), verified against nn-training/model.py
const BC_MADDS = 30.77e6

const DEFAULT_WEIGHTS = 'nn-training/weights/weights.20260819-163911_ep40_val0.9984.json'

/** Count parameters in one weights tensor: nested arrays, or {shape,data} form. */
function countLeaves(x: unknown): number {
  if (Array.isArray(x)) return x.reduce<number>((a, v) => a + countLeaves(v), 0)
  if (typeof x === 'number') return 1
  const shape = (x as { shape?: unknown } | null)?.shape
  if (Array.isArray(shape)) {
    return shape.reduce<number>((a, d) => a * (typeof d === 'number' ? d : 0), 1)
  }
  return 0
}

function main() {
  const argv = process.argv.slice(2)
  const printParams = argv.includes('--print-params')
  const path = argv.find((a) => !a.startsWith('--')) ?? DEFAULT_WEIGHTS
  if (!printParams) console.log(`[bench] weights: ${path}`)

  const text = readFileSync(path, 'utf8')
  const json = JSON.parse(text) as {
    arch?: Record<string, unknown>
    params?: Record<string, unknown>
    num_params?: number
  }
  const arch = json.arch ?? {}

  // Deployment-budget artifact (plan/new-era-stop.plan.md #11): the actor's
  // parameter count is definitive from the weights (num_params or the sum of
  // tensor leaves); MAdds is only known for the reference BC arch (30.77e6),
  // else null. B案 is +0 params — this prints the baseline the deploy gate
  // compares against. Pure metadata: it never builds the model, so it works on
  // any weights file.
  if (printParams) {
    const params =
      typeof json.num_params === 'number'
        ? json.num_params
        : Object.values(json.params ?? {}).reduce<number>((a, v) => a + countLeaves(v), 0)
    const isBc = arch.kind === undefined || arch.kind === 'bc'
    const out = {
      weights: path,
      kind: arch.kind ?? 'bc',
      inCh: arch.in_ch ?? 0,
      board: arch.board ?? 0,
      scalarDim: arch.scalar_dim ?? 0,
      params,
      madds: isBc ? BC_MADDS : null,
      madds_source: isBc
        ? 'reference BC arch (verified vs nn-training/model.py)'
        : 'unknown arch — compute from the net before gating',
    }
    process.stdout.write(JSON.stringify(out, null, 2) + '\n')
    return
  }

  const model = buildModelFromText(text)

  console.log(
    `[bench] model built: kind=${arch.kind ?? 'bc'} ` +
      `inCh=${model.inCh} board=${model.board} scalar=${model.scalarDim}`,
  )

  const obsLen = model.inCh * model.board * model.board
  const obs = new Uint8Array(obsLen)
  const scalars = new Float32Array(model.scalarDim)
  // Deterministic fill (values don't affect fixed-cost conv/FC timing).
  for (let i = 0; i < obsLen; i++) obs[i] = (i * 7 + 3) & 0xff
  for (let i = 0; i < scalars.length; i++) scalars[i] = ((i % 10) + 1) / 10

  // Warmup (JIT / cache settle).
  const WARMUP = Number(process.env.WARMUP ?? 200)
  for (let i = 0; i < WARMUP; i++) model.forward(obs, scalars)

  // Timed loop.
  const ITERS = Number(process.env.ITERS ?? 4000)
  const t0 = performance.now()
  for (let i = 0; i < ITERS; i++) {
    model.forward(obs, scalars)
  }
  const t1 = performance.now()
  const totalMs = t1 - t0
  const perMs = totalMs / ITERS
  const fps = 1000 / perMs
  const macPerSec = BC_MADDS / (perMs / 1000)

  // Sanity: logits are finite.
  const mv = model.moveLogits
  let finite = true
  for (let i = 0; i < mv.length; i++) if (!Number.isFinite(mv[i])) finite = false

  console.log('--- results ---')
  console.log(`warmup iters     : ${WARMUP}`)
  console.log(`timed iters      : ${ITERS}`)
  console.log(`total time (ms)  : ${totalMs.toFixed(1)}`)
  console.log(`per forward (ms) : ${perMs.toFixed(4)}`)
  console.log(`forward FPS      : ${fps.toFixed(1)}`)
  console.log(
    `implied MAC/s    : ${(macPerSec / 1e6).toFixed(0)} M  (${(macPerSec / 1e9).toFixed(2)} G)`,
  )
  console.log(`logits finite    : ${finite}`)

  // Map to the plan's decision budget.
  // K=10 decision gate => 166.7ms budget per decision (60fps/10).
  const budget166 = 166.7
  console.log('--- against decision budget (K=10 => 166.7ms) ---')
  console.log(
    `forward fits K=10 : ${perMs <= budget166 ? 'YES' : 'NO'} ` +
      `(margin ${(budget166 / perMs).toFixed(2)}x)`,
  )
  // K=1 hard constraint => 16.6ms budget.
  const budget16 = 16.6
  const need16 = perMs / budget16
  console.log(
    `forward fits K=1  : ${perMs <= budget16 ? 'YES' : 'NO'} ` +
      `(needs ${need16.toFixed(2)}x more throughput => SIMD)`,
  )
}

main()
