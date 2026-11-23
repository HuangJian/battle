/**
 * server-iters-eval-summary-cache.test.ts — 切课卡顿的根因回归（2026-11-23 用户报障）
 *
 * 症状：dashboard 首页在 g9-def ↔ g11-ringkill 间切换，页面卡 3–4s 才更新。
 * 根因：`/api/state` 每拍都走 `readIterMetrics → readEvalSummaries`，而后者每拍从
 * offset 0 全量重扫 `eval_log.jsonl`（g9-def 408MB/145k 行 = 实测 3.4–3.8s）。
 *
 * 本用例钉住新契约：
 *   ① 指纹未变 ⇒ 直接复用同一份 Map（零行解析）；
 *   ② 只追加新字节 ⇒ 只读增量（旧轮读数不变、新轮出现）；
 *   ③ 同 size + 同 mtimeMs 的重写 ⇒ 锚点识破，不吃旧账。
 */
import { describe, expect, it } from 'bun:test'
import { appendFileSync, mkdtempSync, rmSync, statSync, utimesSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'

import {
  parseEvalSummariesAndWinsText,
  parseEvalSummariesText,
  parseEvalWinsText,
  readEvalGames,
  readEvalRoundOptions,
  readEvalSummaries,
} from '../src/server/iters'

function withTraj(): { dir: string; done: () => void } {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-evalsum-cache-'))
  return {
    dir,
    done: () => rmSync(dir, { recursive: true, force: true }),
  }
}

const logOf = (dir: string): string => path.join(dir, 'eval_log.jsonl')

function summaryLine(iter: number, wver: string, wins: number): string {
  return JSON.stringify({
    event: 'eval_summary',
    iter,
    wver,
    time: '2026-11-23 09:00:00',
    games: 10,
    wins,
    winRate: wins / 10,
    sec: 1,
    outcomes: {},
  })
}

function gameLine(iter: number, wver: string, kills: number): string {
  return JSON.stringify({
    event: 'eval',
    iter,
    wver,
    stage: 2000,
    seed: iter,
    win: kills > 0,
    ticks: 100 + kills,
    kills,
    score: kills * 100,
    playerDamageTaken: 20,
  })
}

function round(iter: number, wver: string, kills: number): string {
  return [gameLine(iter, wver, kills), summaryLine(iter, wver, kills > 0 ? 1 : 0)].join('\n') + '\n'
}

describe('readEvalSummaries 的指纹 + 锚点 memo（切课不再全量重扫）', () => {
  it('指纹未变时复用同一份 Map；账本追加后旧轮读数不变、新轮出现', () => {
    const traj = withTraj()
    try {
      const log = logOf(traj.dir)
      writeFileSync(log, round(1, 'w1', 3))
      const first = readEvalSummaries(traj.dir)
      expect(first.get(1)?.totalKills).toBe(3)

      // 同一份字节再读一拍：返回同一个 Map 对象 = 没有重新 JSON.parse 14 万行。
      expect(readEvalSummaries(traj.dir)).toBe(first)

      appendFileSync(log, round(2, 'w1', 7))
      const before = JSON.stringify(first.get(1))
      const second = readEvalSummaries(traj.dir)
      expect(second.get(2)?.totalKills).toBe(7)
      expect(second.get(2)?.wins).toBe(1)
      expect(JSON.stringify(second.get(1))).toBe(before)
    } finally {
      traj.done()
    }
  })

  it('同 size + 同 mtimeMs 的重写靠锚点识破（不串旧课数据）', () => {
    const traj = withTraj()
    try {
      const log = logOf(traj.dir)
      // 用整秒时间戳：mtimeMs 从小数秒往返会掉精度（全量套件里 0.0001ms 的差让指纹
      // 直接失效，测试就绕过了「锚点这一层」——那不是它该守的东西）。
      const fixedMtimeSec = 1_700_000_000
      const old = summaryLine(1, 'w1', 3)
      writeFileSync(log, old + '\n')
      utimesSync(log, fixedMtimeSec, fixedMtimeSec)
      expect(statSync(log).mtimeMs).toBe(fixedMtimeSec * 1000)
      const first = readEvalSummaries(traj.dir)
      expect(first.get(1)?.wins).toBe(3)

      // 长度必须完全一致，指纹才会撞：`wins:3` → `wins:7` 一个字符的改动。
      const rewritten = summaryLine(1, 'w1', 7)
      expect(rewritten.length).toBe(old.length)
      writeFileSync(log, rewritten + '\n')
      utimesSync(log, fixedMtimeSec, fixedMtimeSec)
      expect(statSync(log).mtimeMs).toBe(fixedMtimeSec * 1000)
      // 指纹被强制对齐后，锚点必须成为最后一道闸。
      const second = readEvalSummaries(traj.dir)
      expect(second).not.toBe(first)
      expect(second.get(1)?.wins).toBe(7)
    } finally {
      traj.done()
    }
  })
})

describe('replay 弹窗数据源不再重复扫账本', () => {
  it('readEvalGames 复用已缓存 summary：只剩逐局行那一趟 parse', () => {
    const traj = withTraj()
    try {
      const log = logOf(traj.dir)
      writeFileSync(log, round(1, 'w1', 3))
      readEvalSummaries(traj.dir) // /api/state 已经算过一遍（memo 命中）

      const origParse = JSON.parse
      let parses = 0
      ;(JSON as { parse: typeof JSON.parse }).parse = ((s: string) => {
        parses++
        return origParse(s)
      }) as typeof JSON.parse
      try {
        expect(readEvalGames(traj.dir)?.rows.length).toBe(1)
      } finally {
        ;(JSON as { parse: typeof JSON.parse }).parse = origParse
      }
      // 旧实现两趟全扫（summary 头 + 逐局行）：一行 eval + 一行 summary 会被 parse 4 次；
      // 现在只在逐局那一趟各 parse 一次 = 2 次（summary 头直接吃缓存）。
      expect(parses).toBe(2)
    } finally {
      traj.done()
    }
  })

  it('readEvalGames 结果按 (账本, iter) 复用，追加新轮后可取新轮', () => {
    const traj = withTraj()
    try {
      const log = logOf(traj.dir)
      writeFileSync(log, round(1, 'w1', 3))
      const first = readEvalGames(traj.dir)
      expect(readEvalGames(traj.dir)).toBe(first)

      appendFileSync(log, round(2, 'w1', 7))
      const second = readEvalGames(traj.dir)
      expect(second?.iter).toBe(2)
      expect(second?.rows[0]?.kills).toBe(7)
      expect(readEvalGames(traj.dir, 1)?.rows[0]?.kills).toBe(3)
    } finally {
      traj.done()
    }
  })

  it('readEvalRoundOptions 指纹命中复用同一数组；追加新轮后轮列表更新', () => {
    const traj = withTraj()
    try {
      const log = logOf(traj.dir)
      writeFileSync(log, round(1, 'w1', 3))
      const first = readEvalRoundOptions(traj.dir)
      expect(first.map((r) => r.iter)).toEqual([1])
      expect(readEvalRoundOptions(traj.dir)).toBe(first)

      appendFileSync(log, round(2, 'w1', 7))
      expect(readEvalRoundOptions(traj.dir).map((r) => r.iter)).toEqual([2, 1])
    } finally {
      traj.done()
    }
  })

  it('冷存一趟解析与两趟分解析逐字段相等', () => {
    const traj = withTraj()
    try {
      const text = [gameLine(1, 'w1', 3), summaryLine(1, 'w1', 1)].join('\n') + '\n'
      const combined = parseEvalSummariesAndWinsText(text, 'o2-demo')
      const summaries = parseEvalSummariesText(text, 'o2-demo')
      const wins = parseEvalWinsText(text)
      expect([...combined.summaries]).toEqual([...summaries])
      expect([...combined.wins]).toEqual([...wins])
    } finally {
      traj.done()
    }
  })
})
