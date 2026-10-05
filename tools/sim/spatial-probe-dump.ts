#!/usr/bin/env bun
/**
 * spatial-probe-dump.ts — S0′ 六探针的离线存档（plan/policy-spatial-head.plan.md Step 1 前置）。
 *
 * 跑指定权重（默认 hu150）在 ladder 关上的**贪心局**，逐决策点存档：
 *   obs.npy    (N,16,26,26) u1   —— ObsEncoder 原样（probe 侧用它批处理主干前向得 bufA）
 *   scalars.npy(N,30) f4
 *   extra.npy  (N,9) f4          —— POLICY_EXTRA（同时也是 S0′-1/2 的标签源）
 *   pooled.npy (N,64) f4         —— TS 主干 GAP 输出（与 torch 侧批处理对账用）
 *   index.npy  (N,3) i8          —— [stageId, seed, tick]
 *   games.jsonl                  —— 每局一行（outcome/ticks/kills/nSamples）
 *   meta.json                    —— 全局头（weights sha16 / 关卡 / 段 / 计数）
 *
 * bufA 不落盘（64×676 f32/样本 ≈ 173KB，40 局 ≈ 1GB+）：按 plan 的「obs.npy 存档 +
 * 主干前向批处理」取后者，python 探针从 obs 批量重算（省 ~16× 磁盘且顺带对账 pooled）。
 *
 * 采样点与判决链同规（export-eval-game.ts / export-rl-rollout.ts）：`decisionDue(t)` 在
 * `sim.tick()` **之前**，uniform K（本 dump 不开 decision-events —— 课程为 false）。
 *
 * Usage:
 *   bun tools/sim/spatial-probe-dump.ts \
 *     --weights nn-training/weights/x20-adv-hurt/x20-adv-hurt.it150.20261004-060616.json \
 *     --level nn-training/levels/ladder-c20-lives1.jsonc \
 *     --stages 2000-2003 --seed0 810000 --seeds 10 --out tmp/spatial-probe/hu150
 */
import { readFileSync, mkdirSync, writeFileSync, rmSync } from 'fs'
import { createHash } from 'crypto'
import { join } from 'path'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { decodeStageGrid, type StageJson } from '../../src/nn/config-stage'
import { buildModelFromText, type ModelLike } from '../../src/nn/infer'
import { ObsEncoder, computeMasks } from '../../src/nn/obs-encoder'
import { POLICY_EXTRA_DIM, computePolicyExtra } from '../../src/nn/policy-extra'
import {
  createDecisionGateConfig,
  createDecisionGateState,
  decisionDue,
} from '../../src/nn/decision-gate'
import { decodeMove } from '../../src/nn/action-space'
import { writeNpy } from '../../src/nn/npy'
import { parseJsonc } from '../probe/jsonc'
import type { Direction } from '../../src/constants'
import type { InputLike } from '../../src/game/Input'

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

/** 持有式输入（与 export-eval-game 的 ScriptedInput 同语义）。 */
class HeldInput implements InputLike {
  private dir: Direction | null = null
  private firing = false
  setAction(dir: Direction | null, firing: boolean): void {
    this.dir = dir
    this.firing = firing
  }
  getMoveDirection(): Direction | null {
    return this.dir
  }
  isFiring(): boolean {
    return this.firing
  }
  wasItemPressed(): false {
    return false
  }
  endFrame(): void {}
  reset(): void {
    this.dir = null
    this.firing = false
  }
}

/** 掩码 argmax（并列取最小索引；与 export-eval-game.argmaxCat 同式）。 */
function argmaxCat(logits: Float32Array, mask: number[]): number {
  let best = -Infinity
  let bi = logits.length - 1
  for (let i = 0; i < logits.length; i++) {
    const v = mask[i] !== 1 ? -1e9 : logits[i]
    if (v > best) {
      best = v
      bi = i
    }
  }
  return bi
}

interface LevelFile {
  stages: Array<Record<string, unknown> & { name?: string }>
  difficulty?: string
  max_ticks?: number
  player?: { lives?: number; level?: number }
}

function parseStageRange(spec: string): number[] {
  const out: number[] = []
  for (const part of spec.split(',')) {
    const m = part.trim().match(/^(\d+)\s*-\s*(\d+)$/)
    if (m) {
      for (let v = parseInt(m[1], 10); v <= parseInt(m[2], 10); v++) out.push(v)
    } else if (/^\d+$/.test(part.trim())) {
      out.push(parseInt(part.trim(), 10))
    }
  }
  return out
}

function main(): void {
  const weightsPath = arg('weights')
  if (!weightsPath) throw new Error('--weights <json> is required')
  const levelPath = arg('level', 'nn-training/levels/ladder-c20-lives1.jsonc')!
  const stageIds = parseStageRange(arg('stages', '2000-2003')!)
  const seed0 = parseInt(arg('seed0', '810000')!, 10)
  const seedsPerStage = parseInt(arg('seeds', '10')!, 10)
  const outDir = arg('out', 'tmp/spatial-probe/dump')!

  const level = parseJsonc(readFileSync(levelPath, 'utf8')) as LevelFile
  const difficulty = level.difficulty ?? 'hard'
  const maxTicks = level.max_ticks ?? 12900
  const lives = level.player?.lives ?? DIFFICULTIES[difficulty]?.startLives ?? 3
  const playerLevel = level.player?.level ?? 0

  const weightsText = readFileSync(weightsPath, 'utf8')
  const weightsSha = createHash('sha256').update(weightsText).digest('hex').slice(0, 16)
  const model: ModelLike = buildModelFromText(weightsText)
  const pooledView = (model as unknown as { pooled?: Float32Array }).pooled
  if (!pooledView) throw new Error('dump: 模型没有 pooled 缓冲（架构不符，需 StudentModel）')

  const gateCfg = createDecisionGateConfig(false)
  const obsRows: Uint8Array[] = []
  const scalarRows: Float32Array[] = []
  const extraRows: Float32Array[] = []
  const pooledRows: Float32Array[] = []
  const idxRows: number[] = []
  const gameLines: string[] = []

  for (const stageId of stageIds) {
    const stageLocal = stageId - 2000
    const stageJson = level.stages[stageLocal]
    if (!stageJson)
      throw new Error(`dump: 关卡 ${stageId} 不在 ${levelPath}（stages[${stageLocal}] 缺）`)
    for (let k = 0; k < seedsPerStage; k++) {
      const seed = seed0 + k
      const stage = decodeStageGrid(stageJson as unknown as StageJson, stageId, seed)
      const world = new World()
      world.rng.reseed(seed)
      world.difficultyKey = difficulty
      world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
      world.rules = RULES[difficulty] ?? DEFAULT_RULES
      world.playerLevel = playerLevel
      world.lives = lives
      const held = new HeldInput()
      const sim = new Simulation(world, held)
      world.loadStageData(stage, stageLocal)
      held.reset()
      const gate = createDecisionGateState()
      const enc = new ObsEncoder()
      const extra = new Float32Array(POLICY_EXTRA_DIM)

      let t = 0
      let nSamples = 0
      const decisionCounts = { moves: [0, 0, 0, 0, 0], fires: [0, 0] }
      while (t < maxTicks) {
        if (decisionDue(t, world, gate, gateCfg)) {
          enc.encode(world)
          computePolicyExtra(world, extra)
          model.forward(enc.obs, enc.scalars)
          // 存档（必须复制——编码器/模型缓冲复用）。
          obsRows.push(enc.obs.slice())
          scalarRows.push(enc.scalars.slice())
          extraRows.push(extra.slice())
          pooledRows.push(pooledView.slice())
          idxRows.push(stageId, seed, t)
          nSamples++
          const masks = computeMasks(world)
          const mv = argmaxCat(model.moveLogits, masks.move)
          const fr = model.fireLogits[1] > model.fireLogits[0] && masks.fire[1] === 1 ? 1 : 0
          decisionCounts.moves[mv]++
          decisionCounts.fires[fr]++
          held.setAction(decodeMove(mv), fr === 1)
        }
        sim.tick()
        held.endFrame()
        t++
        if (world.state !== 'playing') break
      }
      const p = world.player
      gameLines.push(
        JSON.stringify({
          stageId,
          seed,
          outcome: world.state,
          ticks: t,
          nSamples,
          score: world.score,
          playerAlive: !!p?.alive,
          decisionCounts,
        }),
      )
      process.stderr.write(`[dump] s${stageId} seed=${seed} ticks=${t} samples=${nSamples}\n`)
    }
  }

  const N = idxRows.length / 3
  if (N === 0) throw new Error('dump: 0 样本——权重/关卡/循环有问题')
  rmSync(outDir, { recursive: true, force: true })
  mkdirSync(outDir, { recursive: true })
  const nObs = obsRows.reduce((a, r) => a + r.length, 0)
  const flatObs = new Uint8Array(nObs)
  {
    let off = 0
    for (const r of obsRows) {
      flatObs.set(r, off)
      off += r.length
    }
  }
  writeNpy(join(outDir, 'obs.npy'), flatObs, [N, 16, 26, 26], 'u1')
  const cat = <T extends Float32Array>(rows: T[]): Float32Array => {
    const len = rows.reduce((a, r) => a + r.length, 0)
    const out = new Float32Array(len)
    let off = 0
    for (const r of rows) {
      out.set(r, off)
      off += r.length
    }
    return out
  }
  writeNpy(join(outDir, 'scalars.npy'), cat(scalarRows), [N, 30], 'f4')
  writeNpy(join(outDir, 'extra.npy'), cat(extraRows), [N, POLICY_EXTRA_DIM], 'f4')
  writeNpy(join(outDir, 'pooled.npy'), cat(pooledRows), [N, 64], 'f4')
  // index 用 f8（npy writer 无 i8 dtype；整数 < 2^53 精确无损）。
  writeNpy(
    join(outDir, 'index.npy'),
    Float64Array.from(idxRows, (v) => v),
    [N, 3],
    'f8',
  )
  writeFileSync(join(outDir, 'games.jsonl'), gameLines.join('\n') + '\n')
  writeFileSync(
    join(outDir, 'meta.json'),
    JSON.stringify(
      {
        weights: weightsPath,
        weightsSha16: weightsSha,
        level: levelPath,
        difficulty,
        maxTicks,
        lives,
        playerLevel,
        stageIds,
        seed0,
        seedsPerStage,
        games: gameLines.length,
        samples: N,
        note: 'S0′ dump: decisionDue(t) before sim.tick(); uniform K; bufA 由 obs 批处理后端重算',
      },
      null,
      2,
    ),
  )
  process.stderr.write(`[dump] done: N=${N} games=${gameLines.length} → ${outDir}\n`)
}

main()
