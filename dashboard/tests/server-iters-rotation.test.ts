/**
 * server-iters-rotation.test.ts — 采样子集（窗口）与分关采集账入表
 * （2026-10-28，plan/it-stage-ratation §9）
 *
 * 分层：src/server/iters.ts（`parseIterRotation` / `parseIterVolume` + 读账本接线）
 *
 * 守什么：
 *   ① additive —— 旧账本行（无 `rotation` / `volume_stage_stats` 键）照旧读出，两格为 null
 *      （不是 0，也不是整行被丢）；老课程页零变化靠这条；
 *   ② 对账口径严格 —— 字符串数字 / 缺 window / 缺 stages 都不收（掩盖写端 bug），
 *      一关都读不出 ⇒ null（不半渲染）；
 *   ③ 缺口由 `quota − collected` 现算（与账本里那份只含未达标关的 map 同一个数）；
 *   ④ `rollout_end` 行也带这两格（采集收官即上屏，不等 PPO 回传）。
 */

import os from 'os'
import path from 'path'
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import { parseIterRotation, parseIterVolume, readIterMetrics } from '../src/server/iters'

const ROTATION = {
  mode: 'even',
  per_it: 12,
  pinned: [],
  all: 35,
  window: [3, 4, 5, 6],
}

const VOLUME = {
  rule: 'per-stage-v3',
  est_src: 'trailing',
  est_agg: 'latest',
  halted: false,
  quota: 17,
  stages: {
    5: { collected: 17, games: 2, est_s: 9, est_used: 9, cap_games: 8 },
    3: { collected: 9, games: 1, est_s: 10, cap_games: 8 },
  },
  shortfall: { 3: 8 },
}

describe('parseIterRotation（窗口读数）', () => {
  it('完整子字典逐项入表（键名转驼峰）', () => {
    const r = parseIterRotation({ ...ROTATION, pinned: [2033, 2034] })
    expect(r).toEqual({
      mode: 'even',
      perIt: 12,
      pinned: [2033, 2034],
      all: 35,
      window: [3, 4, 5, 6],
    })
  })

  it('缺 window / all / mode ⇒ null（不半渲染）；形状非法（数组/标量）⇒ null', () => {
    for (const bad of [
      undefined,
      null,
      'x',
      3,
      [],
      { mode: 'even' },
      { mode: 'even', window: [1], all: undefined },
      { window: [1], all: 35 },
      { mode: 'even', window: [1, '2'], all: 35 },
    ]) {
      expect(parseIterRotation(bad)).toBeNull()
    }
  })

  it('pinned 缺席 ⇒ 空数组（UI 判「哪几关恒采」不必再兜一层）', () => {
    const r = parseIterRotation({ mode: 'even', per_it: 2, all: 5, window: [1, 2] })
    expect(r!.pinned).toEqual([])
    expect(r!.perIt).toBe(2)
  })
})

describe('parseIterVolume（分关采集账）', () => {
  it('逐关升序、quota/est/cap 逐格搬运、缺口现算', () => {
    const v = parseIterVolume(VOLUME)
    expect(v!.rule).toBe('per-stage-v3')
    expect(v!.quota).toBe(17)
    expect(v!.estSrc).toBe('trailing')
    expect(v!.halted).toBe(false)
    expect(v!.stages.map((s) => s.stage)).toEqual([3, 5]) // 键序 5,3 → 排序后 3,5
    const [s3, s5] = v!.stages
    expect(s3).toEqual({
      stage: 3,
      collected: 9,
      quota: 17,
      games: 1,
      capGames: 8,
      estS: 10,
      shortfall: 8,
    })
    expect(s5!.shortfall).toBe(0)
    expect(s5!.estS).toBe(9)
  })

  it('缺 stages / 一关都读不出 ⇒ null（不是空表）；无 collected 的关被跳过', () => {
    for (const bad of [undefined, null, [], {}, { stages: [] }, { stages: 'x' }]) {
      expect(parseIterVolume(bad)).toBeNull()
    }
    const v = parseIterVolume({ quota: 5, stages: { 1: { games: 2 }, 2: { collected: 5 } } })
    expect(v!.stages.map((s) => s.stage)).toEqual([2])
  })

  it('缺 quota ⇒ 缺口是 null（不是 0：0 = 真的没缺口）', () => {
    const v = parseIterVolume({ stages: { 1: { collected: 5 } } })
    expect(v!.quota).toBeNull()
    expect(v!.stages[0]!.shortfall).toBeNull()
    expect(v!.stages[0]!.quota).toBeNull()
  })
})

describe('readIterMetrics 接线（旧行零变化 / rollout_end 也带）', () => {
  it('新行带 rotation/volume/capped；旧行两格 null（行照旧可读）', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-rot-'))
    try {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        [
          JSON.stringify({ event: 'iteration', iter: 1, time: '', winRate: 0.1, score_mean: 0 }),
          JSON.stringify({
            event: 'iteration',
            iter: 2,
            time: '',
            winRate: 0.2,
            score_mean: 1,
            rotation: ROTATION,
            volume_stage_stats: VOLUME,
            transitions_capped: true,
          }),
        ].join('\n'),
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows.length).toBe(2)
      const [newRow, oldRow] = rows
      expect(newRow!.rotation!.window).toEqual([3, 4, 5, 6])
      expect(newRow!.volume!.quota).toBe(17)
      expect(newRow!.capped).toBe(true)
      // 旧行：字段存在但为 null（UI 显空态），行本身照旧可读
      expect(oldRow!.rotation).toBeNull()
      expect(oldRow!.volume).toBeNull()
      expect(oldRow!.capped).toBeNull()
      expect(oldRow!.winRate).toBeCloseTo(0.1)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('rollout_end 行也带窗口与分关账（采集收官即上屏，不等 PPO）', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-rot-end-'))
    try {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        JSON.stringify({
          event: 'rollout_end',
          iter: 4,
          time: 't0',
          winRate: 0.4,
          samples: 100,
          rollout_sec: 30,
          rotation: ROTATION,
          volume_stage_stats: VOLUME,
        }) + '\n',
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows[0]!.pendingPpo).toBe(true)
      expect(rows[0]!.rotation!.all).toBe(35)
      expect(rows[0]!.volume!.stages.length).toBe(2)
      // 触顶标记缺席 ⇒ null（不是 false：账本只在为真时落键）
      expect(rows[0]!.capped).toBeNull()
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
