#!/usr/bin/env bun
/**
 * export-godai-bc.ts — 单局 God-AI BC 语料导出（BC 云-HUB-LAN 整合，2026-09-13）。
 *
 * sampler-agent 的 mode=bc 任务体：跑**一局** God-AI 教师对局（复用
 * export-godai-labels.ts::exportGame 纯函数——与批量导出器同一采集语义：
 * decision-tick (obs, teacher label, mask, condition, returns)），把 npy shard
 * 打成 BCV2 容器写到 --pack（与 export-rl-rollout 的 --pack 收尾同构）。
 *
 * 与批量导出器（export-godai-labels.ts）的分工：本文件只做「单局 + 容器」——
 * 批量/确定性校验/覆盖报告仍归批量导出器；(stage, seed) 的编排水在
 * nn-training/rl/bc_dispatch.py。
 *
 * wins-only 败局不是错误：容器 manifest 带 `kept:false`、零文件条目——Python 侧
 * dist_common.validate_result 认定合法"跳过"（plan/bc-cloud-integration.plan.md §5）。
 *
 * Determinism: same (stage, seed, difficulty, stageJson) → identical container bytes
 * （God-AI driver 独立 RNG `seed ^ 0x9e3779b9`，与批量导出器一致）。
 *
 * Usage (agent 内部调用约定):
 *   bun tools/sim/export-godai-bc.ts --stage 0 --seed 7 --difficulty hard \
 *     --max-ticks 2400 --wins 1 --near-miss-times 3 --pack <out>/_result.pack
 */
import { writeFileSync, mkdirSync } from 'fs'
import path from 'path'
import { exportGame } from './export-godai-labels'
import {
  OBS_SCHEMA_MAJOR,
  OBS_CHANNELS,
  BOARD,
  SCALAR_DIM,
  SCHEMA_FINGERPRINT,
} from '../../src/nn/obs-encoder'
import { npyBytes } from '../../src/nn/npy'
import { buildPack } from './pack-container'

const EXPORTER_VERSION = '1.0.0'

function main(): void {
  const arg = (name: string, fallback = ''): string => {
    const i = process.argv.indexOf(`--${name}`)
    return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback
  }
  const stage = parseInt(arg('stage', ''), 10)
  const seed = parseInt(arg('seed', ''), 10)
  const difficulty = arg('difficulty', 'hard')
  const maxTicks = parseInt(arg('max-ticks', '36000'), 10)
  const winsOnly = (arg('wins', '1') || '1') === '1'
  const nearMissTimes = parseInt(arg('near-miss-times', '3'), 10)
  const stageJson = arg('stage-json', '')
  const livesRaw = arg('lives-override', '')
  const livesOverride = livesRaw === '' ? undefined : parseInt(livesRaw, 10)
  const levelRaw = arg('player-level', '')
  const playerLevelOverride = levelRaw === '' ? undefined : parseInt(levelRaw, 10)
  const nodeLabel = arg('node-label', '')
  const packPath = arg('pack', '')
  if (!Number.isInteger(stage) || !Number.isInteger(seed) || !packPath) {
    console.error(
      'usage: bun tools/sim/export-godai-bc.ts --stage N --seed N --pack <path> ' +
        '[--difficulty hard] [--max-ticks N] [--wins 0|1] [--near-miss-times N] ' +
        '[--stage-json <json>] [--lives-override N] [--player-level N]',
    )
    process.exit(2)
  }

  const t0 = Date.now()
  // --pack 的父目录由本进程负责创建（agent 传 gameDir 时已存在；手工直跑时不存在）。
  mkdirSync(path.dirname(path.resolve(packPath)), { recursive: true })
  const res = exportGame(
    seed,
    stage,
    difficulty,
    maxTicks,
    winsOnly,
    nearMissTimes,
    stageJson || undefined,
    livesOverride,
    playerLevelOverride,
  )

  if (res === 'loss-skipped') {
    // wins-only 败局：合法跳过（kept:false 空容器）。Python 侧按 loss_skipped 计数。
    const manifest = {
      schemaMajor: OBS_SCHEMA_MAJOR,
      exporterVersion: EXPORTER_VERSION,
      mode: 'bc',
      collector: 'BC-GOD',
      teacher: 'god-ai',
      policy: 'god',
      wver: 'bc',
      stage,
      seed,
      difficulty,
      kept: false,
      outcome: 'loss-skipped',
      ticks: 0,
      nSamples: 0,
      node: nodeLabel,
      elapsedSec: +((Date.now() - t0) / 1000).toFixed(1),
    }
    writeFileSync(packPath, buildPack(manifest, []))
    console.log(`[export-godai-bc] s${stage}/seed${seed}: loss-skipped (wins-only)`)
    return
  }

  // 命局：内存构造 npy 字节（npyBytes 与 writeNpy/numpy.load 字节契约同源——容器内
  // .npy 字节 = 本机直跑 export-godai-labels 的落盘产物）→ BCV2 容器。
  // 条目清单与 nn-training/dist_common.BC_SHARD_FILES 逐一对齐（manifest.json 由
  // Python 侧 write_shard 落盘时重写补血缘键，不随容器携带）。
  const N = res.samples.length
  const OBS_N = OBS_CHANNELS * BOARD * BOARD
  const MASK_DIM = 7
  const obs = new Uint8Array(N * OBS_N)
  const scalars = new Float32Array(N * SCALAR_DIM)
  const actions = new Uint8Array(N * 2)
  const masks = new Uint8Array(N * MASK_DIM)
  const conditions = new Uint8Array(N)
  const returns = new Float32Array(N)
  for (let i = 0; i < N; i++) {
    const s = res.samples[i]
    obs.set(s.obs, i * OBS_N)
    scalars.set(s.scalars, i * SCALAR_DIM)
    actions[i * 2] = s.move
    actions[i * 2 + 1] = s.fire
    for (let j = 0; j < MASK_DIM; j++) masks[i * MASK_DIM + j] = s.masks[j]
    conditions[i] = s.cond
    returns[i] = res.rewards[i]
  }
  const manifest = {
    schemaMajor: OBS_SCHEMA_MAJOR,
    obsSchemaMajor: OBS_SCHEMA_MAJOR,
    schemaFingerprint: SCHEMA_FINGERPRINT,
    exporterVersion: EXPORTER_VERSION,
    mode: 'bc',
    collector: 'BC-GOD',
    teacher: 'god-ai',
    policy: 'god',
    wver: 'bc',
    stage,
    seed,
    difficulty,
    kept: true,
    outcome: res.outcome,
    ticks: res.ticks,
    nSamples: N,
    gatedScore: +res.gatedScore.toFixed(4),
    nearMissFrames: res.nearMissFrames,
    node: nodeLabel,
    elapsedSec: +((Date.now() - t0) / 1000).toFixed(1),
  }
  const entries = [
    { name: 'obs.npy', data: npyBytes(obs, [N, OBS_CHANNELS, BOARD, BOARD], 'u1') },
    { name: 'scalars.npy', data: npyBytes(scalars, [N, SCALAR_DIM], 'f4') },
    { name: 'actions.npy', data: npyBytes(actions, [N, 2], 'u1') },
    { name: 'masks.npy', data: npyBytes(masks, [N, MASK_DIM], 'u1') },
    { name: 'conditions.npy', data: npyBytes(conditions, [N], 'u1') },
    { name: 'returns.npy', data: npyBytes(returns, [N], 'f4') },
  ]
  writeFileSync(packPath, buildPack(manifest, entries))
  console.log(
    `[export-godai-bc] s${stage}/seed${seed}: kept nSamples=${N} outcome=${res.outcome} ` +
      `ticks=${res.ticks} nearMiss=${res.nearMissFrames} elapsed=${((Date.now() - t0) / 1000).toFixed(1)}s`,
  )
}

if (import.meta.main) {
  main()
}
