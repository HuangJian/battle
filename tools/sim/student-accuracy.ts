#!/usr/bin/env bun
/**
 * student-accuracy.ts — TS-side inference parity / action-accuracy check.
 *
 * Loads the exported student weights via src/nn/infer.ts (the SAME runtime the
 * sim uses), replays the God-AI-labeled decision-tick shards, and computes the
 * per-head action accuracy. This is the no-torch parity signal: if TS reproduces
 * the Python training report (move ~0.53 / fire ~0.93 / item ~1.00), inference
 * is byte-faithful and a 0% deployment win rate is a model/deployment issue, not
 * a code bug. If TS accuracy collapses, infer.ts / policy-input.ts has a bug.
 *
 * Usage:
 *   bun tools/sim/student-accuracy.ts --weights tmp/student-weights-full/weights.json --data tmp/godai
 */
import { readFileSync, readdirSync, existsSync } from 'fs'
import { join } from 'path'
import { buildModelFromText, type ModelLike } from '../../src/nn/infer'

interface Npy {
  shape: number[]
  u8: Uint8Array
  f32: Float32Array
}

function readNpy(path: string): Npy {
  const buf = readFileSync(path)
  const hlen = buf.readUInt16LE(8) // bytes 0-5 magic, 6-7 version, 8-9 hlen
  const header = buf.subarray(10, 10 + hlen).toString('latin1')
  const shapeM = header.match(/'shape':\s*\(([^)]*)\)/)
  const descrM = header.match(/'descr':\s*'([^']+)'/)
  const shape = shapeM![1]
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map(Number)
  const dtype = descrM![1] // e.g. '<u1' or '<f4'
  const raw = buf.subarray(10 + hlen)
  if (dtype === '<u1' || dtype === '|u1') {
    return {
      shape,
      u8: new Uint8Array(raw.buffer, raw.byteOffset, raw.byteLength),
      f32: new Float32Array(0),
    }
  }
  // f4 (float32), aligned because NumPy pads header to 64 bytes.
  return {
    shape,
    u8: new Uint8Array(0),
    f32: new Float32Array(raw.buffer, raw.byteOffset, raw.byteLength >> 2),
  }
}

function argmax(a: Float32Array, n: number): number {
  let b = 0
  let bv = a[0]
  for (let i = 1; i < n; i++)
    if (a[i] > bv) {
      bv = a[i]
      b = i
    }
  return b
}

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

function main(): void {
  const weightsPath = arg('weights', 'tmp/student-weights-full/weights.json')!
  const dataDir = arg('data', 'tmp/godai')!
  const maxSamples = parseInt(arg('max', '100000000')!, 10)

  const text = readFileSync(weightsPath, 'utf8')
  const model: ModelLike = buildModelFromText(text)
  const archKind = (JSON.parse(text) as any).arch?.kind
  process.stderr.write(`[student-accuracy] model arch=${archKind} weights=${weightsPath}\n`)
  process.stderr.write(
    `[student-accuracy] inCh=${model.inCh} board=${model.board} scalarDim=${model.scalarDim}\n`,
  )

  // Collect shard directories (each contains obs.npy/scalars.npy/actions.npy).
  const entries = readdirSync(dataDir)
  const shardDirs: string[] = []
  for (const e of entries) {
    const full = join(dataDir, e)
    if (existsSync(join(full, 'obs.npy')) && existsSync(join(full, 'actions.npy')))
      shardDirs.push(full)
  }
  shardDirs.sort()
  process.stderr.write(`[student-accuracy] ${shardDirs.length} shards in ${dataDir}\n`)

  let n = 0
  let moveOk = 0
  let fireOk = 0
  let itemOk = 0
  const movePredDist = [0, 0, 0, 0, 0]
  const moveLabDist = [0, 0, 0, 0, 0]
  const C = 26 * 26
  let logged = 0

  for (const dir of shardDirs) {
    if (n >= maxSamples) break
    const obsNpy = readNpy(join(dir, 'obs.npy'))
    const scNpy = readNpy(
      join(dir, 'scalars' + (existsSync(join(dir, 'scalars.npy')) ? '.npy' : '')),
    )
    const actNpy = readNpy(join(dir, 'actions.npy'))
    const N = obsNpy.shape[0]
    const obs = obsNpy.u8
    const sc = scNpy.f32
    const act = actNpy.u8
    for (let i = 0; i < N && n < maxSamples; i++) {
      const oBase = i * 14 * C
      const obsSample = obs.subarray(oBase, oBase + 14 * C)
      const sBase = i * 24
      const scSample = sc.subarray(sBase, sBase + 24)
      model.forward(obsSample, scSample)
      const mv = argmax(model.moveLogits, 5)
      const fr = argmax(model.fireLogits, 2)
      const it = argmax(model.itemLogits, 3)
      const lm = act[i * 3 + 0]
      const lf = act[i * 3 + 1]
      const li = act[i * 3 + 2]
      if (mv === lm) moveOk++
      if (fr === lf) fireOk++
      if (it === li) itemOk++
      movePredDist[mv]++
      moveLabDist[lm]++
      n++
      if (logged < 5) {
        logged++
        process.stderr.write(
          `  sample#${n} pred[mv=${mv} fr=${fr} it=${it}] label[mv=${lm} fr=${lf} it=${li}] ` +
            `moveLogits=[${Array.from(model.moveLogits)
              .map((x) => x.toFixed(2))
              .join(',')}]\n`,
        )
      }
    }
  }

  const pct = (x: number) => ((x / n) * 100).toFixed(2) + '%'
  console.log(`samples=${n}`)
  console.log(`move_acc=${pct(moveOk)} (${moveOk}/${n})`)
  console.log(`fire_acc=${pct(fireOk)} (${fireOk}/${n})`)
  console.log(`item_acc=${pct(itemOk)} (${itemOk}/${n})`)
  console.log(`move_pred_dist=${JSON.stringify(movePredDist)}`)
  console.log(`move_label_dist=${JSON.stringify(moveLabDist)}`)
  process.stderr.write(
    `[student-accuracy] DONE move=${pct(moveOk)} fire=${pct(fireOk)} item=${pct(itemOk)}\n`,
  )
}

main()
