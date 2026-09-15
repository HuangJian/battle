import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { readIterMetrics, readPairedReferee } from '../src/server/iters'
import {
  fmtPaired,
  heroMainRows,
  PAIRED_COL_TITLES,
  pairedBaselineOf,
  pairedTone,
  pairedVerdictText,
  type IterRow,
} from '../src/web/view'

/** fixture eval_log.jsonl：rows = [{iter, stage, seed, win}]。 */
function fixtureDir(
  name: string,
  rows: Array<{ iter: number; seed: number; win: boolean }>,
): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), `paired-${name}-`))
  const lines = rows.map((r) =>
    JSON.stringify({
      event: 'eval',
      iter: r.iter,
      wver: `wver${r.iter}`,
      stage: 2000,
      seed: r.seed,
      win: r.win,
    }),
  )
  writeFileSync(path.join(dir, 'eval_log.jsonl'), lines.join('\n') + '\n')
  return dir
}

const dirs: string[] = []
afterAll(() => {
  for (const d of dirs) rmSync(d, { recursive: true, force: true })
})

/** 最小 traj 夹具：training_log 的 it5/it10 + 对应 eval 逐局/汇总行。
 *
 *  `withBase = true` 追加 it0 基线行（bc 权重干净评估，2026-09-12）——it0 只有
 *  eval 逐局/汇总，没有 iteration 事件，控制台据此合成基线行。 */
function mkTrajFixture(withBase: boolean): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), withBase ? 'paired-it0-' : 'paired-rows-'))
  dirs.push(dir)
  const T = (iter: number) =>
    JSON.stringify({
      event: 'iteration',
      iter,
      time: `2026-09-12 1${iter}:00:00`,
      winRate: 0.4,
      score_mean: 0.5,
      score_std: 0.1,
      samples: 100,
      rollout_sec: 10,
      ppo_sec: 10,
      kl: 0.01,
      entropy: 0.4,
      policy: 0.001,
      value: 0.5,
      mean_ret: 0,
      lr: 0.00005,
      expectedGames: 10,
      halted: false,
      dim_means: {},
      ticks: 1000,
    })
  // it5: F,T,T · it10: T,T,F → b01=1(seed0) b10=1(seed2)
  const E = (iter: number, seed: number, win: boolean) =>
    JSON.stringify({ event: 'eval', iter, wver: `w${iter}`, stage: 2000, seed, win })
  const S = (iter: number, wins: number) =>
    JSON.stringify({
      event: 'eval_summary',
      iter,
      wver: `w${iter}`,
      games: 3,
      wins,
      winRate: wins / 3,
      time: `2026-09-12 0${iter}:00:00`,
    })
  const evals = [
    ...(withBase ? [E(0, 0, false), E(0, 1, true)] : []),
    E(5, 0, false),
    E(5, 1, true),
    E(5, 2, true),
    E(10, 0, true),
    E(10, 1, true),
    E(10, 2, false),
  ]
  const summaries = [...(withBase ? [S(0, 1)] : []), S(5, 2), S(10, 2)]
  writeFileSync(path.join(dir, 'training_log.jsonl'), [T(5), T(10)].join('\n') + '\n')
  writeFileSync(path.join(dir, 'eval_log.jsonl'), [...evals, ...summaries].join('\n') + '\n')
  return dir
}

describe('console 配对裁判 readPairedReferee', () => {
  it('胜负互换一局 → flat，delta=0，p=1', () => {
    const dir = fixtureDir('flat', [
      { iter: 5, seed: 0, win: false },
      { iter: 5, seed: 1, win: true },
      { iter: 10, seed: 0, win: true },
      { iter: 10, seed: 1, win: false },
    ])
    dirs.push(dir)
    const ref = readPairedReferee(dir)
    expect(ref?.vsFirst).not.toBeNull()
    expect(ref?.vsFirst?.b01).toBe(1)
    expect(ref?.vsFirst?.b10).toBe(1)
    expect(ref?.vsFirst?.paired).toBe(2)
    expect(ref?.vsFirst?.deltaPp).toBe(0)
    expect(ref?.vsFirst?.p).toBe(1.0)
    expect(ref?.vsFirst?.verdict).toBe('flat')
  })

  it('7-0 碾压 → up；不成对局只计 unpaired', () => {
    const rows: Array<{ iter: number; seed: number; win: boolean }> = []
    for (let s = 0; s < 7; s++) {
      rows.push({ iter: 5, seed: s, win: false })
      rows.push({ iter: 10, seed: s, win: true })
    }
    rows.push({ iter: 5, seed: 7, win: true })
    rows.push({ iter: 5, seed: 8, win: true }) // it10 缺席 → unpaired
    rows.push({ iter: 10, seed: 9, win: false }) // it5 缺席 → unpaired
    const dir = fixtureDir('up', rows)
    dirs.push(dir)
    const ref = readPairedReferee(dir)
    expect(ref?.vsFirst?.b01).toBe(7)
    expect(ref?.vsFirst?.b10).toBe(0)
    expect(ref?.vsFirst?.paired).toBe(7)
    expect(ref?.vsFirst?.unpaired).toBe(3)
    expect(ref?.vsFirst?.verdict).toBe('up')
  })

  it('三轮 → vsFirst 取首末、vsPrev 取末两轮；单轮 → null', () => {
    const dir = fixtureDir('three', [
      { iter: 5, seed: 0, win: false },
      { iter: 10, seed: 0, win: false },
      { iter: 15, seed: 0, win: true },
    ])
    dirs.push(dir)
    const ref = readPairedReferee(dir)
    expect(ref?.vsFirst?.baseIter).toBe(5)
    expect(ref?.vsFirst?.ckptIter).toBe(15)
    expect(ref?.vsFirst?.verdict).toBe('flat') // n=1, p=1
    expect(ref?.vsPrev?.baseIter).toBe(10)
    expect(ref?.vsPrev?.ckptIter).toBe(15)
    const single = fixtureDir('single', [{ iter: 5, seed: 0, win: true }])
    dirs.push(single)
    expect(readPairedReferee(single)).toBeNull()
  })

  it('缺文件 → null（不阻断 state）', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'paired-empty-'))
    dirs.push(dir)
    expect(readPairedReferee(dir)).toBeNull()
  })
})

describe('配对文案 fmtPaired', () => {
  it('c6 式 +7pp/p=0.31 → 灰文案', () => {
    const s = fmtPaired(
      {
        baseIter: 1,
        ckptIter: 40,
        paired: 100,
        unpaired: 0,
        b01: 21,
        b10: 14,
        deltaPp: 7.0,
        p: 0.31,
        verdict: 'flat',
      },
      'vs开腿it1',
    )
    expect(s).toContain('+7.0pp')
    expect(s).toContain('p=0.31')
    expect(s).toContain('21/14')
    expect(s).toContain('方向对，证据不够')
  })

  it('空 → 数据不足；verdict 文案三态', () => {
    expect(fmtPaired(null, 'vs开腿')).toContain('数据不足')
    expect(pairedVerdictText('up')).toBe('显著涨')
    expect(pairedVerdictText('down')).toBe('显著跌')
  })
})

describe('列头 tooltip 文案 PAIRED_COL_TITLES', () => {
  it('四键齐全、大白话、非空', () => {
    expect(Object.keys(PAIRED_COL_TITLES).sort()).toEqual(['b01', 'b10', 'delta', 'p'])
    for (const v of Object.values(PAIRED_COL_TITLES)) {
      expect(v.length).toBeGreaterThan(10)
    }
    expect(PAIRED_COL_TITLES.b01).toContain('新学会')
    expect(PAIRED_COL_TITLES.b10).toContain('学费')
    expect(PAIRED_COL_TITLES.p).toContain('0.05')
    expect(PAIRED_COL_TITLES.delta).toContain('b01−b10')
  })

  it('pairedTone：up绿/down红/flat灰；baseline 定位', () => {
    expect(pairedTone('up')).toBe('g')
    expect(pairedTone('down')).toBe('r')
    expect(pairedTone('flat')).toBe('gray')
    expect(pairedBaselineOf([null, undefined])).toBeNull()
    expect(
      pairedBaselineOf([
        null,
        {
          baseIter: 5,
          ckptIter: 10,
          paired: 3,
          unpaired: 0,
          b01: 1,
          b10: 1,
          deltaPp: 0,
          p: 1,
          verdict: 'flat',
        },
      ]),
    ).toBe(5)
  })
})

describe('逐轮 pairedVsFirst 装配 readIterMetrics', () => {
  it('基线轮 null、后轮挂 b01/b10', () => {
    const { rows } = readIterMetrics(mkTrajFixture(false))
    const r5 = rows.find((r) => r.iter === 5)
    const r10 = rows.find((r) => r.iter === 10)
    expect(r5?.evalData?.pairedVsFirst ?? null).toBeNull()
    expect(r10?.evalData?.pairedVsFirst).toMatchObject({ b01: 1, b10: 1, paired: 3, deltaPp: 0 })
    expect(r10?.evalData?.pairedVsFirst?.verdict).toBe('flat')
  })
})

describe('it0 bc 权重基线（2026-09-12）', () => {
  it('readPairedReferee 以 it0 为开腿基准；无 it0 才退回首个 eval 轮', () => {
    const with0 = fixtureDir('iter0', [
      { iter: 0, seed: 0, win: false },
      { iter: 0, seed: 1, win: false },
      { iter: 5, seed: 0, win: true },
      { iter: 5, seed: 1, win: false },
    ])
    dirs.push(with0)
    const ref = readPairedReferee(with0)
    expect(ref?.vsFirst?.baseIter).toBe(0)
    expect(ref?.vsFirst?.ckptIter).toBe(5)
    expect(ref?.vsFirst?.b01).toBe(1)
    // 老腿（it0 上线前已跑完，日志里没有 0 行）→ 首个 eval 轮仍是基准
    const noZero = fixtureDir('nobase', [
      { iter: 5, seed: 0, win: false },
      { iter: 10, seed: 0, win: true },
    ])
    dirs.push(noZero)
    expect(readPairedReferee(noZero)?.vsFirst?.baseIter).toBe(5)
  })

  it('readIterMetrics 合成 it0 行：有 evalData、无 rollout（派生字段为缺口）', () => {
    const { rows } = readIterMetrics(mkTrajFixture(true))
    const r0 = rows.find((r) => r.iter === 0)
    expect(r0).toBeDefined()
    expect(r0?.evalData?.wins).toBe(1)
    expect(r0?.evalData?.games).toBe(3)
    expect(r0?.evalData?.pairedVsFirst ?? null).toBeNull()
    expect(Number.isNaN(r0?.winRate ?? 0)).toBe(true)
    expect(r0?.actuals ?? null).toBeNull()
    // 基线恒定 = it0：it5/it10 都与 it0 配对（it5 不再被当成基线）
    expect(rows.find((r) => r.iter === 5)?.evalData?.pairedVsFirst?.baseIter).toBe(0)
    expect(rows.find((r) => r.iter === 10)?.evalData?.pairedVsFirst?.baseIter).toBe(0)
    expect(pairedBaselineOf(rows.map((r) => r.evalData?.pairedVsFirst))).toBe(0)
    // 排序仍是 iter 降序（it0 在表尾）
    expect(rows[rows.length - 1].iter).toBe(0)
  })

  it('无 it0 summary 时不合成基线行（老腿逐字节兼容）', () => {
    const { rows } = readIterMetrics(mkTrajFixture(false))
    expect(rows.some((r) => r.iter === 0)).toBe(false)
    expect(pairedBaselineOf(rows.map((r) => r.evalData?.pairedVsFirst))).toBe(5)
  })
})

describe('Hero 主表行 heroMainRows（it0 合成行不得漏入）', () => {
  /** 最小 IterRow（字段值无意义，只验选择/排序/截断）。 */
  const mkRow = (iter: number): IterRow => ({
    iter,
    time: `t${iter}`,
    winRate: 0.4,
    scoreMean: 0.5,
    scoreStd: 0.1,
    samples: 100,
    rolloutSec: 10,
    ppoSec: 10,
    pureCollectSec: null,
    ppoCloudSec: null,
    distPhaseSec: null,
    kl: 0.01,
    entropy: 0.4,
    policyLoss: 0.001,
    valueLoss: 0.5,
    meanRet: 0,
    lr: 0.00005,
    expectedGames: 10,
    halted: false,
    topDims: '',
    avgTicks: 100,
    accuracy: 0.9,
    loot: 0.1,
    kills: 1.5,
    actuals: null,
    evalData: null,
  })

  it('it0 合成行被排除；其余 iter 倒序', () => {
    const rows = [mkRow(10), mkRow(0), mkRow(5), mkRow(1)]
    expect(heroMainRows(rows).map((r) => r.iter)).toEqual([10, 5, 1])
  })

  it('截前 6 轮；真实行不足 6 时也不拿 it0 凑数', () => {
    const seven = [7, 6, 5, 4, 3, 2, 1].map(mkRow)
    expect(heroMainRows([...seven, mkRow(0)]).map((r) => r.iter)).toEqual([7, 6, 5, 4, 3, 2])
    expect(heroMainRows([mkRow(2), mkRow(1), mkRow(0)]).map((r) => r.iter)).toEqual([2, 1])
  })

  it('端到端：readIterMetrics 合成的 it0 行（NaN 字段）进不了主表行集', () => {
    const { rows } = readIterMetrics(mkTrajFixture(true))
    expect(rows.some((r) => r.iter === 0)).toBe(true)
    expect(heroMainRows(rows).some((r) => r.iter === 0)).toBe(false)
  })
})
