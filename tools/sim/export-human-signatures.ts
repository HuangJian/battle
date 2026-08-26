#!/usr/bin/env bun
/**
 * export-human-signatures.ts — M2 人像签名标签导出器（plan §5.2 / §5.3 标签层）。
 *
 * 对 nn-demo/*.ndjson（97 局，可重放子集 93.3%）逐局重放（restoreWorld+ReplayInput+
 * Simulation，与 verify-demos 同管线），按帧组装 SigContext（world 状态 + 帧输入），
 * signatureIntent() 逐帧打标 → segmentIntents 分段（共享实现，四件套）。
 *
 * 输出：
 *   tmp/human-intents/report.json  — 每局意图窗口分布 + 类聚合（two-oracle 对照）
 *   tmp/human-intents/signatures.ndjson — 逐局逐段：{file, stage, outcome,
 *     segments:[{start,end,intent}]}
 *   （帧级 obs 不导出——人像 obs 层按 §5.3 双层封存走 seal-premerge/obs 快照 +
 *     新引擎重编由 M5 决定；M5 B 臂先用帧级签名展开的意图标签 + 重放时逐 tick 编码）
 *
 * 用法：bun tools/sim/export-human-signatures.ts [--files nn-demo/*.ndjson ...]
 *       [--out tmp/human-intents]
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { parseReplayFile } from '../../src/replay/file'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import { unpackFrames } from '../../src/replay/pack'
import { signatureIntent } from '../../src/ai/intent/signature'
import { segmentIntentSeq, type IntentId } from '../../src/ai/intent/vocab'
import type { Direction } from '../../src/constants'
import { STAGES as STAGES_IMPL } from '../../src/config/stages'
import { DIFFICULTIES as DIFFICULTIES_IMPL } from '../../src/config/difficulty'
import { RULES as RULES_IMPL, DEFAULT_RULES as DEFAULT_RULES_IMPL } from '../../src/config/rules'
import { Glob } from 'bun'
import { mkdirSync, writeFileSync } from 'fs'

const ARENA = 26

interface HumanRow {
  file: string
  stage: number
  outcome: string
  ticks: number
  segments: Array<{ start: number; end: number; intent: string }>
}

async function main(): Promise<void> {
  const argFiles = process.argv.filter((a) => a.startsWith('--files'))
  const outRoot = 'tmp/human-intents'
  mkdirSync(outRoot, { recursive: true })

  let files: string[] = []
  if (argFiles.length > 0) {
    const spec = argFiles[0].split('=')[1] ?? argFiles[1]
    files = spec.split(',')
  } else {
    const g = new Glob('*.ndjson')
    files = [...g.scanSync('nn-demo')].map((f) => `nn-demo/${f}`)
  }
  const outs: HumanRow[] = []
  const windowCounts: Record<string, number> = {}
  const outcomes: Record<string, number> = {}

  for (const file of files) {
    const text = await Bun.file(file).text()
    const lines = text.split('\n').filter((l) => l.trim().length > 0)
    for (let i = 0; i < lines.length; i++) {
      try {
        const row = await tagOne(lines[i], file, i)
        if (!row) continue
        outs.push(row)
        outcomes[row.outcome] = (outcomes[row.outcome] ?? 0) + 1
        for (const s of row.segments) windowCounts[s.intent] = (windowCounts[s.intent] ?? 0) + 1
      } catch (e) {
        console.error(`[human-sig] ${file}:${i} skip: ${(e as Error).message}`)
      }
    }
  }

  writeFileSync(`${outRoot}/signatures.ndjson`, outs.map((r) => JSON.stringify(r)).join('\n'))
  const total = Object.values(windowCounts).reduce((a, b) => a + b, 0)
  const report = {
    generatedBy: 'tools/sim/export-human-signatures.ts',
    files: files.length,
    games: outs.length,
    outcomes,
    totalSegments: total,
    perIntentWindows: windowCounts,
    preregistered: { minWindowsPerClass: 200, verdictBasis: 'human natural distribution' },
  }
  writeFileSync(`${outRoot}/report.json`, JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report, null, 2))
}

async function tagOne(line: string, file: string, idx: number): Promise<HumanRow | null> {
  const parsed = parseReplayFile(line)
  if ('error' in parsed) throw new Error(`parse: ${parsed.error}`)
  const replay = parsed.replay
  const un = unpackFrames(replay.frames)
  const frames = un?.p1 ?? []
  if (frames.length === 0) return null

  const world = new World()
  const snap = replay.initialSnapshot
  const dkey = replay.metadata?.difficulty || snap.difficultyKey || 'classic'
  world.difficultyKey = dkey
  world.difficulty = DIFFICULTIES_IMPL[dkey] ?? DIFFICULTIES_IMPL['classic']
  world.rules = RULES_IMPL[dkey] ?? DEFAULT_RULES_IMPL
  const stageIdx = snap.stageIndex ?? replay.metadata?.stage ?? 0
  world.loadStageData(STAGES_IMPL[stageIdx] ?? STAGES_IMPL[0], stageIdx)
  restoreWorld(world, replay.initialSnapshot)
  world.state = 'playing'

  const input = new ReplayInput(replay.frames)
  const sim = new Simulation(world, input)
  sim.input = input
  sim.input2 = input.input2 ?? null

  let outcome = 'timeout'
  const maxTicks = Math.min(frames.length, 36000)
  const intentSeq: (IntentId | null)[] = []
  for (let t = 0; t < maxTicks && !input.isFinished; t++) {
    if (world.state !== 'playing') break
    sim.tick()
    input.advance()
    const f = frames[t] ?? { direction: null as Direction | null, firing: false }
    const sig = assemblyContext(world, f)
    intentSeq.push(sig ? (signatureIntent(sig) as IntentId | null) : null)
    world.consumeEvents?.()
    const st: string = world.state
    if (st === 'stageclear') {
      outcome = 'stage_clear'
      break
    }
    if (st === 'gameover') {
      outcome = world.tileMap.isBaseDestroyed() ? 'base_destroyed' : 'lives_exhausted'
      break
    }
  }

  // 签名意图序列 → 共享分段（四件套，M1/M2 同步）。
  const segs = segmentIntentSeq(intentSeq).map((s) => ({
    start: s.start,
    end: s.end,
    intent: s.intent,
  }))
  return {
    file: `${file}#${idx}`,
    stage: stageIdx + 1,
    outcome,
    ticks: intentSeq.length,
    segments: segs,
  }
}

const CELL = 16

function assemblyContext(
  world: import('../../src/game/World').World,
  f: { direction: Direction | null; firing: boolean },
) {
  const p = world.player
  if (!p || !p.alive) return null
  const pc = { col: Math.floor((p.x + p.w / 2) / CELL), row: Math.floor((p.y + p.h / 2) / CELL) }
  // 最近敌
  let ne: { col: number; row: number; dist: number } | null = null
  let baseThreat = false
  for (const t of world.tanks) {
    if (!t.alive || t.isPlayer || t.spawnTimer > 0) continue
    const tc = { col: Math.floor((t.x + 16) / CELL), row: Math.floor((t.y + 16) / CELL) }
    const d = Math.abs(tc.col - pc.col) + Math.abs(tc.row - pc.row)
    if (!ne || d < ne.dist) ne = { ...tc, dist: d }
    const dBase = Math.abs(tc.col - 12) + Math.abs(tc.row - 24)
    if (dBase <= 12) baseThreat = true
  }
  // 对齐
  let enemyAligned = false
  if (ne) enemyAligned = ne.col === pc.col || ne.row === pc.row
  // 面前墙
  let wallAhead = false
  const facing = f.direction ?? p.dir
  const ahead = { col: pc.col, row: pc.row }
  if (facing === 'up') ahead.row -= 1
  else if (facing === 'down') ahead.row += 1
  else if (facing === 'left') ahead.col -= 1
  else ahead.col += 1
  if (ahead.col >= 0 && ahead.col < ARENA && ahead.row >= 0 && ahead.row < ARENA) {
    const t = world.tileMap.get(ahead.col, ahead.row)
    wallAhead = t === 'brick' || t === 'steel'
  }
  // 道具近
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

await main()
