import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { readIterMetrics, readPairedReferee } from '../tools/training/console/iters'
import {
  fmtPaired,
  PAIRED_COL_TITLES,
  pairedBaselineOf,
  pairedTone,
  pairedVerdictText,
} from '../tools/training/ui/view'

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
  const mkTraj = (): string => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'paired-rows-'))
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
      })
    writeFileSync(path.join(dir, 'training_log.jsonl'), [T(5), T(10)].join('\n') + '\n')
    writeFileSync(
      path.join(dir, 'eval_log.jsonl'),
      [
        E(5, 0, false),
        E(5, 1, true),
        E(5, 2, true),
        E(10, 0, true),
        E(10, 1, true),
        E(10, 2, false),
        S(5, 2),
        S(10, 2),
      ].join('\n') + '\n',
    )
    return dir
  }

  it('基线轮 null、后轮挂 b01/b10', () => {
    const { rows } = readIterMetrics(mkTraj())
    const r5 = rows.find((r) => r.iter === 5)
    const r10 = rows.find((r) => r.iter === 10)
    expect(r5?.evalData?.pairedVsFirst ?? null).toBeNull()
    expect(r10?.evalData?.pairedVsFirst).toMatchObject({ b01: 1, b10: 1, paired: 3, deltaPp: 0 })
    expect(r10?.evalData?.pairedVsFirst?.verdict).toBe('flat')
  })
})
