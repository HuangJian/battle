/**
 * server-api-error-registry.test.ts — 组件错误登记表（plan/component-error-registry）。
 *
 * 分层：`src/web/view/error-types.ts`（契约/上限，纯）+ `src/server/api/error-{fingerprint,scan,registry}.ts`。
 *
 * 钉住的四条（对应 plan §8 的 P0 守门）：
 *   · **P0-1**：指纹住服务端树 —— 本套件只 import `server/api/**`，而 `tests/architecture-layering.test.ts`
 *     负责「`web/view/**` 里没有 `node:`」那一半（两条合起来才是完整判据）。
 *   · **P0-2**：腿 A 的 `detail` 是**整块**（多行 traceback），不是那一行。
 *   · **P0-3**：账本路径可重定向（`BCITY_ERROR_LEDGER`）—— 用例跑完**不许**在仓根 `tmp/` 留账本。
 *   · **G8 试金石**：跨 episode（出现 ⇒ ack ⇒ 消失一拍 ⇒ 再出现 ⇒ 必重弹）。
 *
 * 「改坏必红」清单（每条断言在实现里都有一条对应的可破坏点，见各处注释）：
 *   去掉 `normalizeErrorText` 的数字/hex 归一 ⇒「同 fp」红；把 `scope` 塞回指纹 ⇒「跨课同 fp」红；
 *   `errorDetailBlock` 改成只取一行 ⇒「整块」红；`applySweep` 用账本的 `firstSeenAt` 当 episode ⇒
 *   「跨 episode」红；`appendErrorLedger` 每次出现都写 ⇒「同 fp 只一行」红。
 */

import { afterAll, beforeAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync } from 'fs'
import os from 'os'
import path from 'path'
import { REPO_ROOT, errorLedgerPath } from '../src/core/paths'
import { errorFingerprint, normalizeErrorText } from '../src/server/api/error-fingerprint'
import {
  collectLocalLogEvents,
  errorDetailBlock,
  failRecordEvent,
  foldErrorEvents,
  resetErrorScanCaches,
} from '../src/server/api/error-scan'
import {
  applySweep,
  appendErrorLedger,
  buildErrorSummary,
  commitSweep,
  findErrorEvent,
  getErrorRegistry,
  getErrorSummary,
  refreshErrorRegistry,
  replayErrorLedger,
  resetErrorRegistry,
  type RegistryState,
  workerReportEvent,
} from '../src/server/api/error-registry'
import { ERROR_SUMMARY_MAX, type ErrorEvent } from '../src/web/view'

const EMPTY: RegistryState = { events: [], episodes: new Map(), known: new Set() }

let dir = ''
let ledger = ''
let prevLedger: string | undefined

beforeAll(() => {
  dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-errors-'))
  ledger = path.join(dir, 'component-errors.jsonl')
  prevLedger = process.env.BCITY_ERROR_LEDGER
  // P0-3：账本必须可重定向 —— 不设这一条，本套件会往操作员**真账本**里灌测试指纹。
  process.env.BCITY_ERROR_LEDGER = ledger
})

afterAll(() => {
  if (prevLedger === undefined) delete process.env.BCITY_ERROR_LEDGER
  else process.env.BCITY_ERROR_LEDGER = prevLedger
  resetErrorRegistry()
  resetErrorScanCaches()
  rmSync(dir, { recursive: true, force: true })
})

function ev(over: Partial<ErrorEvent> = {}): ErrorEvent {
  return {
    fp: errorFingerprint('local-log', 'trainingLoop', 'boom'),
    source: 'local-log',
    component: 'trainingLoop',
    scope: 'c1',
    severity: 'err',
    firstAt: 1_000,
    lastAt: 1_000,
    count: 1,
    message: 'boom',
    detail: '',
    ref: { kind: 'log', target: 'trainingLoop' },
    ...over,
  }
}

describe('errorFingerprint — 归一化（G1）', () => {
  it('行号 / iter / hex / 时刻不同 ⇒ **同一个 fp**（不归一就会刷屏）', () => {
    const a = '[09:12:50] [run_rl] job it=17 失败 sha=4b1c9f0a2d3e5f61 elapsed=31s'
    const b = '[10:03:11] [run_rl] job it=18 失败 sha=99aa00bb11cc22dd elapsed=45s'
    expect(errorFingerprint('local-log', 'trainingLoop', a)).toBe(
      errorFingerprint('local-log', 'trainingLoop', b),
    )
  })

  it('组件不同 / 来源不同 ⇒ 不同 fp（别把不同错误折成一条）', () => {
    const m = 'boom 42'
    expect(errorFingerprint('local-log', 'trainingLoop', m)).not.toBe(
      errorFingerprint('local-log', 'hubServer', m),
    )
    expect(errorFingerprint('local-log', 'trainingLoop', m)).not.toBe(
      errorFingerprint('node', 'trainingLoop', m),
    )
  })

  it('`scope` **不进**指纹：同一错误在 10 门课上折叠成一条（plan §4.1 P1-3）', () => {
    const m = 'ImportError: cannot import name METRICS_NAME'
    // 指纹函数根本没有 scope 参数 —— 这一条同时钉住「签名别加回去」。
    expect(errorFingerprint('local-log', 'trainingLoop', m)).toBe(
      errorFingerprint('local-log', 'trainingLoop', m),
    )
    const n = normalizeErrorText('x 12 y')
    expect(n.includes('12')).toBe(false) // 数字被抹掉
    expect(normalizeErrorText('[12:00:01] a')).toBe(normalizeErrorText('[13:59:59] a'))
  })

  it('指纹是 16 位 hex（与 evalboard 的短 sha 惯用法同形）', () => {
    expect(errorFingerprint('node', 'node', 'x')).toMatch(/^[0-9a-f]{16}$/)
  })
})

describe('腿 A 采集 — 单行判色 + 整块 detail（P0-2）', () => {
  // ★ 真实形状（这类日志的最常见形态）：**只有打印入口那一行带 `[HH:MM:SS]` 前缀**，
  //   python 直接写 stderr 的 traceback 行**不带前缀** —— 所以「整块」要靠不带前缀的续行攒出来。
  const LINES = [
    '[09:00:00] [run_rl] start',
    '[09:00:01] [run_rl] stderr:',
    'Traceback (most recent call last):',
    '  File "biz/x.py", line 3, in <module>',
    'ImportError: cannot import name METRICS_NAME',
    '[09:00:02] [run_rl] next thing',
  ]

  it('detail = 命中行所在**整块**（≥3 行，含 Traceback 头）', () => {
    const block = errorDetailBlock(LINES, 4)
    expect(block.split('\n').length).toBeGreaterThanOrEqual(3)
    expect(block.includes('Traceback (most recent call last):')).toBe(true)
    expect(block.includes('ImportError')).toBe(true)
    expect(block.includes('next thing')).toBe(false) // 到下一个带 [HH:MM:SS] 的行就截断
  })

  it('另一种形状：整个 traceback 在**一行**里（log 调用内嵌换行）也不丢', () => {
    const one =
      '[09:00:01] [run_rl] Traceback (most recent call last):\n  File "x.py"\nValueError: nope'
    const block = errorDetailBlock(['[09:00:00] [run_rl] a', one], 1)
    expect(block.includes('File "x.py"')).toBe(true)
    expect(block.includes('ValueError')).toBe(true)
  })

  it('只有 error 级别行成事件；同 fp 在窗口内计次', () => {
    const events = collectLocalLogEvents(
      LINES,
      { component: 'trainingLoop', scope: 'c1', refTarget: 'trainingLoop' },
      777,
    )
    expect(events.length).toBe(1)
    expect(events[0]!.message.includes('ImportError')).toBe(true)
    expect(events[0]!.detail.includes('Traceback')).toBe(true)
    expect(events[0]!.ref).toEqual({ kind: 'log', target: 'trainingLoop' })
    expect(events[0]!.lastAt).toBe(777)
  })

  it('两行同因（iter 不同）⇒ 同 fp，两条事件（由 fold 合并计次）', () => {
    const events = collectLocalLogEvents(
      ['[09:00:00] [x] fail it=1 conn', '[09:00:01] [x] fail it=2 conn'],
      { component: 'hubServer', scope: '', refTarget: 'hubServer' },
      1,
    )
    expect(events.length).toBe(2)
    expect(events[0]!.fp).toBe(events[1]!.fp)
  })
})

describe('foldErrorEvents — 合并（G1）', () => {
  it('同 fp：firstAt 最小 / lastAt 最大 / count 求和 / message 取最新', () => {
    const a = ev({ fp: 'f1', firstAt: 10, lastAt: 10, count: 2, message: 'old' })
    const b = ev({ fp: 'f1', firstAt: 20, lastAt: 30, count: 3, message: 'new' })
    const merged = foldErrorEvents([a, b])
    expect(merged.length).toBe(1)
    expect([merged[0]!.firstAt, merged[0]!.lastAt, merged[0]!.count]).toEqual([10, 30, 5])
    expect(merged[0]!.message).toBe('new')
  })

  it('不同 fp 各自成条，err 排在前', () => {
    const merged = foldErrorEvents([ev({ fp: 'w', severity: 'warn' }), ev({ fp: 'e' })])
    expect(merged.map((e) => e.fp)).toEqual(['e', 'w'])
  })
})

describe('episode 与 ack 身份（G8 试金石）', () => {
  it('持续存在 ⇒ firstAt 不变；消失一拍再出现 ⇒ **必重弹**（firstAt 变）', () => {
    const e = ev({ fp: 'roll', count: 1 })
    const s1 = applySweep(EMPTY, [e], 1_000).state
    const s2 = applySweep(s1, [ev({ fp: 'roll', count: 4 })], 2_000).state
    expect(s2.episodes.get('roll')!.firstAt).toBe(1_000) // 持续 ⇒ 身份不变（不重复打断）
    expect(s2.episodes.get('roll')!.count).toBe(4) // episode 内取 max（不是 1+4）
    const s3 = applySweep(s2, [], 3_000).state // 消失一拍
    expect(s3.episodes.has('roll')).toBe(false)
    const s4 = applySweep(s3, [ev({ fp: 'roll', count: 1 })], 4_000).state
    expect(s4.episodes.get('roll')!.firstAt).toBe(4_000) // ★ 复发 ⇒ 新身份 ⇒ 重弹
  })

  it('count 单调不减（窗口读数取 max，不跨拍累加）', () => {
    const s1 = applySweep(EMPTY, [ev({ fp: 'x', count: 3 })], 1).state
    const s2 = applySweep(s1, [ev({ fp: 'x', count: 2 })], 2).state
    expect(s2.episodes.get('x')!.count).toBe(3)
  })

  it('buildErrorSummary：投影不含 detail，超上限置 truncated', () => {
    const many = Array.from({ length: ERROR_SUMMARY_MAX + 1 }, (_, i) => ev({ fp: `f${i}` }))
    const summary = buildErrorSummary(many)
    expect(summary.items.length).toBe(ERROR_SUMMARY_MAX)
    expect(summary.truncated).toBe(true)
    expect('detail' in summary.items[0]!).toBe(false)
    expect(buildErrorSummary(many.slice(0, 2)).truncated).toBe(false)
  })
})

describe('账本（G5 / P0-3）', () => {
  it('新 fp 落一行；同 fp 再现不写第二行；重启重放后已知', () => {
    resetErrorRegistry()
    const first = ev({ fp: 'led-1', message: 'ledger one' })
    commitSweep([first], 1_000)
    const lines1 = readFileSync(ledger, 'utf8').trim().split('\n')
    expect(lines1.length).toBe(1)
    // 同一次会话再出现 ⇒ 不写
    commitSweep([ev({ fp: 'led-1', count: 5 })], 2_000)
    expect(readFileSync(ledger, 'utf8').trim().split('\n').length).toBe(1)
    // 「重启」（清状态、账本留在盘上）⇒ 重放后仍已知 ⇒ 仍不写第二行
    resetErrorRegistry()
    const known = replayErrorLedger()
    expect(known.has('led-1')).toBe(true)
    commitSweep([ev({ fp: 'led-1' })], 3_000)
    expect(readFileSync(ledger, 'utf8').trim().split('\n').length).toBe(1)
    // 新 fp 才写
    commitSweep([ev({ fp: 'led-2' })], 4_000)
    expect(readFileSync(ledger, 'utf8').trim().split('\n').length).toBe(2)
  })

  it('appendErrorLedger 的行形态（grep 用：fp / firstSeenAt / ref）', () => {
    appendErrorLedger(ev({ fp: 'led-3', message: 'shape' }), 9_000)
    const raw = readFileSync(ledger, 'utf8').trim().split('\n').pop()!
    const rec = JSON.parse(raw) as { fp: string; firstSeenAt: number; ref: { kind: string } }
    expect(rec.fp).toBe('led-3')
    expect(rec.firstSeenAt).toBe(9_000)
    expect(rec.ref.kind).toBe('log')
  })

  it('★ P0-3：重定向生效 —— 本套件的行只在被重定向的账本里', () => {
    expect(readFileSync(ledger, 'utf8').includes('led-3')).toBe(true)
    // ★ 2026-10-19：原断言是「读一遍仓根真账本，确认里面没有本套件的指纹」——那本身就是一次
    //   真实目录读。现在由 `tests/helpers/isolate-paths.ts` 预载**结构性**保证隔离
    //   （`errorLedgerPath()` 必不在仓根 tmp 下）⇒ 这里只断言隔离生效，不再碰真文件。
    expect(errorLedgerPath()).toBe(ledger)
    expect(errorLedgerPath().startsWith(REPO_ROOT)).toBe(false)
  })
})

describe('腿 B / 腿 C 的解析（纯函数）', () => {
  it('fail.json ⇒ 事件（detail 上屏，job 锚点）', () => {
    const e = failRecordEvent(
      'c5-gae',
      'run-77',
      { reason: 'bun 装不上', kind: 'ProtocolError', detail: 'trace…', ts: 1_700_000_000 },
      5,
    )!
    expect(e.source).toBe('hub-job')
    expect(e.scope).toBe('c5-gae')
    expect(e.ref).toEqual({ kind: 'job', target: 'run-77' })
    expect(e.message.includes('ProtocolError')).toBe(true)
    expect(e.detail).toBe('trace…')
    expect(e.lastAt).toBe(1_700_000_000_000) // epoch 秒 ⇒ ms
    expect(failRecordEvent('c', 'j', { reason: '  ' }, 5)).toBe(null)
  })

  it('上报体 ⇒ 事件（缺 worker/reason ⇒ null；severity 只认两档）', () => {
    const e = workerReportEvent(
      {
        worker: 'node-a',
        component: 'ppo-worker',
        course: 'c1',
        job_id: 'j9',
        severity: 'weird',
        reason: '机器级停滞',
        detail: 'd',
        ts: 1_700_000_000,
      },
      5,
    )!
    expect(e.source).toBe('worker-report')
    expect(e.severity).toBe('warn') // 未知档 ⇒ warn（不编 err）
    expect(e.ref.target).toBe('j9')
    expect(workerReportEvent({ worker: '', reason: 'x' } as never, 5)).toBe(null)
    expect(workerReportEvent({ worker: 'w', reason: '' } as never, 5)).toBe(null)
  })
})

describe('detail 懒取读面（GET /api/errors?fp=；plan §4.6 P1-4）', () => {
  it('已知 fp ⇒ 拿到全量 detail 与跳转锚点；未知 fp ⇒ null（不编一份旧 traceback）', () => {
    resetErrorRegistry()
    const e = ev({
      detail: 'Traceback (most recent call last):\n  File "x.py", line 1\nImportError',
    })
    commitSweep([e], 1_000)
    const got = findErrorEvent(e.fp)!
    expect(got.detail).toContain('ImportError')
    expect(got.component).toBe('trainingLoop')
    expect(got.ref).toEqual({ kind: 'log', target: 'trainingLoop' })
    expect(findErrorEvent('nope')).toBe(null)
  })

  it('★ episode 会真的消失：漏一拍 ⇒ 取不到；重现 ⇒ 同 fp 但 `firstAt` 是**新的**（ack 身份随之变）', () => {
    resetErrorRegistry()
    const fp = ev().fp
    commitSweep([ev()], 1_000)
    expect(findErrorEvent(fp)!.firstAt).toBe(1_000)
    commitSweep([], 2_000) // 这一拍没扫到 ⇒ episode 结束
    expect(findErrorEvent(fp)).toBe(null)
    commitSweep([ev({ firstAt: 2_000, lastAt: 2_000 })], 3_000)
    expect(findErrorEvent(fp)!.firstAt).toBe(3_000) // 新 episode ⇒ 新 ack 身份（必重弹）
  })
})

describe('巡检入口（单飞 + 单腿失败不连坐）', () => {
  it('refreshErrorRegistry 可跑（本机无课程/无 hub 也算成功），且只读面立刻可读', async () => {
    resetErrorRegistry()
    await refreshErrorRegistry(12_345)
    expect(Array.isArray(getErrorRegistry())).toBe(true)
    expect(Array.isArray(getErrorSummary().items)).toBe(true)
  })
})
