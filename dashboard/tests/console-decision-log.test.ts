/**
 * console-decision-log.test.ts — 组件级决策必须落盘（2026-09-20 用户指令）。
 *
 * 事故（2026-09-20）：hub 被**人工停掉**后，盘上证据只显示「组件日志断在半分钟前 +
 * 账本条目没了 + 没有『意外退出』标记」——从证据里**分不出**「人工停的」与「自己死的」，
 * 只能反过来去问操作员。根因不是「没写日志」：`core/log.ts` 早就是 stdout + 文件双写，
 * 但**控制台从没 arm 它**（只有 `launch/cli.ts` 调 `initLog`），而监督器/启动对账的判决
 * 又是裸 `console.*`——连双写都绕过。
 *
 * 本文件守两件事：
 *   ① 会话日志本身（会话头 / 追加不截断 / 启动时轮转 / 写不进也不炸）；
 *   ② 接线门禁（`server.ts` 在**任何决策之前** arm；决策站点所在文件里不再有裸
 *      `console.log|warn|error`——漏一处 = 那类决策又只剩终端滚屏）。
 */

import { describe, expect, it } from 'bun:test'
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { DASHBOARD_ROOT, LOG_DIR, consoleLogPath } from '../src/core/paths'
import { routeAction } from '../src/server/api/route'
import { currentLogFile, error, fail, info, initConsoleLog, log, ok, warn } from '../src/core/log'

const SRC = (rel: string): string => readFileSync(path.join(DASHBOARD_ROOT, rel), 'utf-8')

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), 'console-log-'))
}

describe('会话日志（core/log）', () => {
  it('arm 后：会话头 + 六种级别全部落盘，且顺序不变', () => {
    const file = path.join(tmpDir(), 'console.log')
    expect(initConsoleLog(file)).toBe(file)
    expect(currentLogFile()).toBe(file) // sink 已 arm（未 arm 时是空串）
    log('启动服务进程：开始')
    ok('hub-server 启动成功 (PID 123)')
    warn('hub-server 跑的是磁盘上更早的代码——接管并重启')
    fail('hub-server 启动失败（45s 内未就绪）')
    info('已停课 x20-steady')
    error('[console] POST /api/stop failed: boom')
    const text = readFileSync(file, 'utf-8')
    expect(text).toContain('=== console session ')
    expect(text).toContain(`pid=${process.pid}`)
    const idx = [
      '启动服务进程：开始',
      'hub-server 启动成功',
      '跑的是磁盘上更早的代码',
      'hub-server 启动失败',
      '已停课 x20-steady',
      'POST /api/stop failed',
    ].map((needle) => text.indexOf(needle))
    expect(idx.every((i) => i >= 0)).toBe(true)
    expect([...idx].sort((a, b) => a - b)).toEqual(idx)
  })

  it('再次 arm 同一路径 = 追加（换会话不丢上一段）', () => {
    const file = path.join(tmpDir(), 'console.log')
    initConsoleLog(file)
    log('第一个会话的决策')
    initConsoleLog(file)
    log('第二个会话的决策')
    const text = readFileSync(file, 'utf-8')
    expect(text.split('=== console session ').length - 1).toBe(2)
    expect(text).toContain('第一个会话的决策')
    expect(text).toContain('第二个会话的决策')
  })

  it('启动时超过阈值 ⇒ 轮转一代（console.log.1），新文件只留会话头', () => {
    const file = path.join(tmpDir(), 'console.log')
    writeFileSync(file, 'x'.repeat(64), 'utf-8')
    initConsoleLog(file, { rotateBytes: 16 })
    const rotated = readFileSync(`${file}.1`, 'utf-8')
    expect(rotated).toBe('x'.repeat(64))
    const fresh = readFileSync(file, 'utf-8')
    expect(fresh).toContain('=== console session ')
    expect(fresh).not.toContain('x'.repeat(64))
  })

  it('没超阈值不轮转（会话延续）', () => {
    const file = path.join(tmpDir(), 'console.log')
    writeFileSync(file, 'seed\n', 'utf-8')
    initConsoleLog(file, { rotateBytes: 1024 })
    expect(existsSync(`${file}.1`)).toBe(false)
    expect(readFileSync(file, 'utf-8')).toContain('seed')
  })

  it('写不进也不炸（best-effort：日志不是关键路径）', () => {
    const dir = tmpDir()
    const blocker = path.join(dir, 'not-a-dir')
    writeFileSync(blocker, 'x', 'utf-8')
    // 父路径是个文件 ⇒ mkdir/append 必失败；不得抛（控制台要照常跑）
    expect(() => initConsoleLog(path.join(blocker, 'console.log'))).not.toThrow()
    expect(() => log('still alive')).not.toThrow()
  })

  it('每个会话头带时间与 pid（两个会话能从一行分开）', () => {
    const file = path.join(tmpDir(), 'console.log')
    initConsoleLog(file)
    const header = readFileSync(file, 'utf-8').trim()
    expect(header).toMatch(/^=== console session \d{4}-\d{2}-\d{2}T[\d:.]+Z pid=\d+ ===$/)
  })
})

describe('consoleLogPath（惰性取值）', () => {
  it('未设 override ⇒ tmp/training-start/console.log（与其它运行态同住）', () => {
    const prev = process.env.BCITY_CONSOLE_LOG
    delete process.env.BCITY_CONSOLE_LOG
    try {
      expect(consoleLogPath()).toBe(path.join(LOG_DIR, 'training-start', 'console.log'))
    } finally {
      if (prev !== undefined) process.env.BCITY_CONSOLE_LOG = prev
    }
  })

  it('BCITY_CONSOLE_LOG 可重定向（单测不污染仓根 tmp/）', () => {
    const prev = process.env.BCITY_CONSOLE_LOG
    const custom = path.join(tmpDir(), 'x.log')
    process.env.BCITY_CONSOLE_LOG = custom
    try {
      expect(consoleLogPath()).toBe(custom)
    } finally {
      if (prev === undefined) delete process.env.BCITY_CONSOLE_LOG
      else process.env.BCITY_CONSOLE_LOG = prev
    }
  })
})

describe('接线门禁（决策必须走 core/log 的双写）', () => {
  /** 决策站点：这些文件里的每一行都是「谁在何时动了什么」，不许绕过双写。 */
  const DECISION_FILES = [
    'src/server/server.ts', // 监督器重启 / 启动对账接管旧码 / 请求异常
    'src/server/exit-watchdog.ts', // 判死 + 「意外退出/已停车」标记
    'src/stack/hub.ts', // hub 换代接管 / 隧道 / 端口回收
    'src/server/api/route.ts', // 动作结果（启/停/开课/停课…的操作面）
    'src/server/actions/start.ts',
    'src/server/actions/stop.ts',
    'src/server/actions/course-lifecycle.ts', // 开课 / 停课
  ]

  /** 真正自己打日志的文件（`course-lifecycle` 只返回 ActionResult，由 route 层落盘）。 */
  const CORE_LOG_IMPORTERS = [
    'src/server/server.ts',
    'src/server/exit-watchdog.ts',
    'src/stack/hub.ts',
    'src/server/api/route.ts',
    'src/server/actions/stop.ts',
  ]

  it.each(DECISION_FILES)('%s 不残留裸 console.log/warn/error', (rel) => {
    const raw = SRC(rel).match(/console\.(log|warn|error)\(/g) ?? []
    expect(raw).toEqual([])
  })

  it.each(CORE_LOG_IMPORTERS)('%s 从 core/log 取日志出口', (rel) => {
    expect(/from '[^']*core\/log'/.test(SRC(rel))).toBe(true)
  })

  it('route.ts 的动作结果统一落盘（唯一动作出口，新动作自动继承）', () => {
    const src = SRC('src/server/api/route.ts')
    // 出口只有一个：`routeAction` 调 `dispatchAction` 然后落盘
    expect(src).toContain('await dispatchAction(action, body)')
    expect(src).toContain('await logActionDecision(action, body, resp)')
    // 读接口不刷决策日志（高频回读不是决策）
    expect(src).toContain("'getGateHaltMode'")
    expect(src).toContain('READONLY_ACTIONS')
  })

  it('server.ts 在**任何决策之前** arm 会话日志（initConsoleLog 早于 startSupervisor 调用）', () => {
    const src = SRC('src/server/server.ts')
    const arm = src.indexOf('initConsoleLog()')
    // startSupervisor 的**调用**（不是 120 行处的函数声明）必须在 arm 之后
    const supCall = src.indexOf('startSupervisor()', arm)
    expect(arm).toBeGreaterThan(0)
    expect(supCall).toBeGreaterThan(arm)
  })

  it('启动横幅把决策日志路径告诉操作员（不然没人知道去哪看）', () => {
    expect(SRC('src/server/server.ts')).toContain('组件决策日志')
  })
})

describe('真动作的结果落盘（不是只接线）', () => {
  it('失败动作一行 warn、成功动作一行 log，都带动作名与结果', async () => {
    const file = path.join(tmpDir(), 'console.log')
    initConsoleLog(file)
    // 不存在的课程：openCourse 在**写任何东西之前**快速失败（纯校验，零副作用）
    const bad = await routeAction('openCourse', { course: 'no-such-course-zzz' })
    expect(bad?.status).toBe(409)
    // 视图动作（成功面）：console-state 重定向到 tmp，不碰真实会话状态
    const prevState = process.env.BCITY_CONSOLE_STATE
    process.env.BCITY_CONSOLE_STATE = path.join(tmpDir(), 'console-state.json')
    try {
      const good = await routeAction('setCourse', { course: '' })
      expect(good?.status).toBe(200)
    } finally {
      if (prevState === undefined) delete process.env.BCITY_CONSOLE_STATE
      else process.env.BCITY_CONSOLE_STATE = prevState
    }
    const text = readFileSync(file, 'utf-8')
    expect(text).toContain('[action] openCourse no-such-course-zzz → fail')
    expect(text).toContain('[action] setCourse → ok')
  })
})
