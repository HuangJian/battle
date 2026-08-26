#!/usr/bin/env bun
/**
 * export-human-obs.ts — M5-B 臂语料导出器：人像重放 → obs 帧 + 签名意图标签
 * (plan §5.2 / §7-M5；B 臂数据 = A 臂数据 ∪ 人像签名标签)。
 *
 * 采样口径与 A 臂（export-intent-labels）一致：replan 网格（30 tick）∪ 段首边界；
 * 签名意图来自 signature.ts（宁缺勿错，CLEAR 等 <200 窗口类别在 B 臂自然缺量，
 * 由 A 臂补齐）；bucket 用 divergence-probe 三桶同款谓词。
 *
 * 输出 shards/<file>#<game>/  obs.npy / scalars.npy / intent.npy / bucket.npy /
 * frame.npy / manifest.json（同 export-intent-labels 布局）。
 *
 * 用法：bun tools/sim/export-human-obs.ts [--out tmp/human-obs]
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { parseReplayFile } from '../../src/replay/file'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import { unpackFrames } from '../../src/replay/pack'
import { ObsEncoder, SCALAR_DIM } from '../../src/nn/obs-encoder'
import { writeNpy } from '../../src/nn/npy'
import { signatureIntent } from '../../src/ai/intent/signature'
import { segmentIntentSeq, INTENT_IDS, type IntentId } from '../../src/ai/intent/vocab'
import { probeBucketOf } from './intent-label-core'
import type { Direction } from '../../src/constants'
import { STAGES } from '../../src/config/stages'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { Glob } from 'bun'
import { mkdirSync, writeFileSync } from 'fs'

const GRID = 26
const CELL = 16
const REPLAN_EVERY = 30
const OBS_N = 14 * GRID * GRID

function ctxOf(
  world: import('../../src/game/World').World,
  f: { direction: Direction | null; firing: boolean },
) {
  const p = world.player
  if (!p || !p.alive) return null
  const pc = { col: Math.floor((p.x + p.w / 2) / CELL), row: Math.floor((p.y + p.h / 2) / CELL) }
  let ne: { col: number; row: number; dist: number } | null = null
  let baseThreat = false
  for (const t of world.tanks) {
    if (!t.alive || t.isPlayer || t.spawnTimer > 0) continue
    const tc = { col: Math.floor((t.x + 16) / CELL), row: Math.floor((t.y + 16) / CELL) }
    const d = Math.abs(tc.col - pc.col) + Math.abs(tc.row - pc.row)
    if (!ne || d < ne.dist) ne = { ...tc, dist: d }
    if (Math.abs(tc.col - 12) + Math.abs(tc.row - 24) <= 12) baseThreat = true
  }
  let enemyAligned = false
  if (ne) enemyAligned = ne.col === pc.col || ne.row === pc.row
  const facing = f.direction ?? p.dir
  const ahead = { col: pc.col, row: pc.row }
  if (facing === 'up') ahead.row -= 1
  else if (facing === 'down') ahead.row += 1
  else if (facing === 'left') ahead.col -= 1
  else ahead.col += 1
  let wallAhead = false
  if (ahead.col >= 0 && ahead.col < GRID && ahead.row >= 0 && ahead.row < GRID) {
    const t = world.tileMap.get(ahead.col, ahead.row)
    wallAhead = t === 'brick' || t === 'steel'
  }
  let pickupNear = false
  for (const pu of world.powerUps) {
    if (!pu.alive) continue
    const puc = { col: Math.floor(pu.x / CELL), row: Math.floor(pu.y / CELL) }
    if (Math.abs(puc.col - pc.col) + Math.abs(puc.row - pc.row) <= 4) pickupNear = true
  }
  return {
    playerCell: pc,
    moveDir: f.direction,
    facingDir: facing,
    firing: f.firing,
    nearestEnemy: ne,
    enemyAligned,
    wallAhead,
    baseThreat,
    baseDist: Math.abs(pc.col - 12) + Math.abs(pc.row - 24),
    pickupNear,
  }
}

async function main(): Promise<void> {
  const outRoot = 'tmp/human-obs'
  mkdirSync(outRoot, { recursive: true })
  const g = new Glob('*.ndjson')
  const files = [...g.scanSync('nn-demo')].map((f) => `nn-demo/${f}`)

  const encoder = new ObsEncoder()
  let games = 0
  let samples = 0

  for (const file of files) {
    const text = await Bun.file(file).text()
    const lines = text.split('\n').filter((l) => l.trim().length > 0)
    for (let i = 0; i < lines.length; i++) {
      try {
        const parsed = parseReplayFile(lines[i])
        if ('error' in parsed) continue
        const replay = parsed.replay
        const un = unpackFrames(replay.frames)
        const frames = un?.p1 ?? []
        if (frames.length === 0) continue

        const world = new World()
        const snap = replay.initialSnapshot
        const dkey = replay.metadata?.difficulty || snap.difficultyKey || 'classic'
        world.difficultyKey = dkey
        world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
        world.rules = RULES[dkey] ?? DEFAULT_RULES
        const stageIdx = snap.stageIndex ?? replay.metadata?.stage ?? 0
        world.loadStageData(STAGES[stageIdx] ?? STAGES[0], stageIdx)
        restoreWorld(world, replay.initialSnapshot)
        world.state = 'playing'

        const input = new ReplayInput(replay.frames)
        const sim = new Simulation(world, input)
        sim.input = input
        sim.input2 = input.input2 ?? null

        const intentSeq: (IntentId | null)[] = []
        const obsFrames: Uint8Array[] = []
        const scalarFrames: Float32Array[] = []
        const frameIdx: number[] = []
        const bucketIdx: number[] = []

        const maxTicks = Math.min(frames.length, 36000)
        for (let t = 0; t < maxTicks && !input.isFinished; t++) {
          if (world.state !== 'playing') break
          sim.tick()
          input.advance()
          const f = frames[t] ?? { direction: null as Direction | null, firing: false }
          const sig = ctxOf(world, f)
          intentSeq.push(sig ? (signatureIntent(sig) as IntentId | null) : null)
          world.consumeEvents?.()
          if (t % REPLAN_EVERY === 0) {
            encoder.encode(world)
            obsFrames.push(encoder.obs.slice())
            scalarFrames.push(encoder.scalars.slice())
            bucketIdx.push(probeBucketOf(world, encoder.scalars))
            frameIdx.push(t)
          }
        }

        const segs = segmentIntentSeq(intentSeq)
        const expanded = new Array<IntentId | null>(intentSeq.length).fill(null)
        for (const s of segs) for (let t = s.start; t <= s.end; t++) expanded[t] = s.intent

        const idxSel = frameIdx.filter((t) => expanded[t] != null)
        const n = idxSel.length
        if (n === 0) continue
        const dirName = `${outRoot}/s${String(stageIdx + 1).padStart(2, '0')}-human-${games}`
        mkdirSync(dirName, { recursive: true })

        const obs = new Uint8Array(n * OBS_N)
        const scalars = new Float32Array(n * SCALAR_DIM)
        const intents = new Uint8Array(n)
        const buckets = new Uint8Array(n)
        const fIdx = new Float64Array(n)
        for (let k = 0; k < n; k++) {
          const oi = frameIdx.indexOf(idxSel[k])
          obs.set(obsFrames[oi], k * OBS_N)
          scalars.set(scalarFrames[oi], k * SCALAR_DIM)
          intents[k] = INTENT_IDS.indexOf(expanded[idxSel[k]] as IntentId)
          buckets[k] = bucketIdx[oi]
          fIdx[k] = idxSel[k]
        }
        writeNpy(`${dirName}/obs.npy`, obs, [n, 14, GRID, GRID], 'u1')
        writeNpy(`${dirName}/scalars.npy`, scalars, [n, SCALAR_DIM], 'f4')
        writeNpy(`${dirName}/intent.npy`, intents, [n], 'u1')
        writeNpy(`${dirName}/bucket.npy`, buckets, [n], 'u1')
        writeNpy(`${dirName}/frame.npy`, fIdx, [n], 'f8')
        writeFileSync(
          `${dirName}/manifest.json`,
          JSON.stringify({
            exporter: 'export-human-obs',
            source: `${file}#${i}`,
            stage: stageIdx + 1,
            sampled: n,
            segments: segs.length,
          }),
        )
        games++
        samples += n
      } catch {
        // 跳过坏局（与 verify-demos 产出率口径一致）
      }
    }
  }
  console.log(`[human-obs] games=${games} samples=${samples} -> ${outRoot}`)
}

await main()
