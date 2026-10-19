/**
 * server-api-error-scan.test.ts — 采集层（plan/error-page-denoise-and-triage v2 Phase 0/6）。
 *
 * 钉五件事：
 *   ① 访问日志 2xx/3xx/4xx 不产事件；5xx 产 1 条 `remote-failure`（不再靠路径关键词）；
 *   ② traceback 续行（M3/M4）不单独成事件，detail 仍含整块；
 *   ③ 全局组件日志**只扫一次**（不是每门课一遍）；
 *   ④ 采集不再以 `training-enabled.txt` 为前置；旧 mtime 的课标 `historical`；
 *   ⑤ 每文件最多 20 个不同 fp（去闸后的内存上界）。
 */

import { afterAll, beforeAll, describe, expect, it } from 'bun:test'
import { mkdirSync, rmSync, utimesSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { loadConfigSafe } from '../src/server/api/config'
import {
  collectLocalLogEvents,
  logScanCacheEntryCount,
  resetErrorScanCaches,
  scanLocalLogEvents,
} from '../src/server/api/error-scan'

const scope = (over: Partial<Parameters<typeof collectLocalLogEvents>[1]> = {}) => ({
  component: 'hubServer',
  scope: '',
  refTarget: 'hubServer',
  ...over,
})

describe('collectLocalLogEvents — 去噪与分类', () => {
  it('访问日志：200 的 /admin/errors 与 worker-error 不产事件', () => {
    const lines = [
      '127.0.0.1 - - [19/Oct/2026 12:00:00] "GET /admin/errors?since=0 HTTP/1.1" 200 -',
      '127.0.0.1 - - [19/Oct/2026 12:00:00] "POST /admin/worker-error HTTP/1.1" 200 -',
    ]
    expect(collectLocalLogEvents(lines, scope(), 1_000)).toEqual([])
  })

  it('访问日志 5xx 产出 1 条 remote-failure（真问题不能一起被跳掉）', () => {
    const lines = ['127.0.0.1 - - [19/Oct/2026 12:00:00] "GET /admin/queue HTTP/1.1" 500 -']
    const events = collectLocalLogEvents(lines, scope(), 1_000)
    expect(events.length).toBe(1)
    expect(events[0]!.classification).toBe('remote-failure')
  })

  it('traceback 续行不单独成事件；命中行 detail 仍含整块', () => {
    const lines = [
      '[09:00:00] [run_rl] start',
      'Traceback (most recent call last):',
      '  File "biz/x.py", line 3, in <module>',
      'During handling of the above exception, another exception occurred:',
      'Exception occurred during processing of request from ("127.0.0.1", 5000)',
      'RuntimeError: 缺 BC 参考',
      '[09:00:02] [run_rl] next',
    ]
    const events = collectLocalLogEvents(
      lines,
      scope({ component: 'trainingLoop', refTarget: 'trainingLoop' }),
      777,
    )
    expect(events.length).toBe(1)
    expect(events[0]!.message).toContain('RuntimeError')
    expect(events[0]!.detail).toContain('Traceback')
    expect(events[0]!.detail).toContain('During handling')
    expect(events[0]!.classification).toBe('unknown')
  })

  it('每文件最多 20 个不同 fp（同 fp 的重复行仍计次）', () => {
    const words = Array.from({ length: 25 }, (_, i) => `alpha${String.fromCharCode(97 + i)}`)
    const lines = words.map((w, i) => `[09:00:${String(i).padStart(2, '0')}] fail ${w}`)
    const events = collectLocalLogEvents(lines, scope(), 1)
    expect(events.length).toBe(20)
    expect(new Set(events.map((e) => e.fp)).size).toBe(20)
  })
})

let dir = ''
let tmp = ''
let prevTmp: string | undefined
let prevCurricula: string | undefined
let prevRegistry: string | undefined

beforeAll(() => {
  dir = path.join(os.tmpdir(), `bcity-errscan-${process.pid}-${Date.now()}`)
  tmp = path.join(dir, 'tmp')
  const curricula = path.join(dir, 'curricula')
  mkdirSync(tmp, { recursive: true })
  mkdirSync(curricula, { recursive: true })
  prevTmp = process.env.BCITY_TMP_LOGS_DIR
  prevCurricula = process.env.BCITY_CURRICULA_DIR
  prevRegistry = process.env.BCITY_REGISTRY_FILE
  process.env.BCITY_TMP_LOGS_DIR = tmp
  process.env.BCITY_CURRICULA_DIR = curricula
  // 组件日志解析会先看 registry 里记录的 `log` 路径：不重定向就会读到**真账本**里
  // 上一次进程留下的绝对路径（实测：直接 `bun test <本文件>` 会扫到仓根 tmp 的
  // `cloudflared-*.log`，缓存条目多 1 条）。没预载时也要自成一体。
  process.env.BCITY_REGISTRY_FILE = path.join(dir, 'registry.json')
  resetErrorScanCaches()
})

afterAll(() => {
  if (prevTmp === undefined) delete process.env.BCITY_TMP_LOGS_DIR
  else process.env.BCITY_TMP_LOGS_DIR = prevTmp
  if (prevCurricula === undefined) delete process.env.BCITY_CURRICULA_DIR
  else process.env.BCITY_CURRICULA_DIR = prevCurricula
  if (prevRegistry === undefined) delete process.env.BCITY_REGISTRY_FILE
  else process.env.BCITY_REGISTRY_FILE = prevRegistry
  resetErrorScanCaches()
  rmSync(dir, { recursive: true, force: true })
})

function writeCourse(name: string, body: string): void {
  const d = path.join(tmp, name)
  mkdirSync(d, { recursive: true })
  writeFileSync(path.join(d, 'training_log.jsonl'), '', 'utf8')
  writeFileSync(path.join(d, 'training-loop.log'), body, 'utf8')
}

describe('scanLocalLogEvents — 全局去重 / 无开课闸 / 活动度', () => {
  it('全局组件只扫一次；无 training-enabled 的课也采；旧 mtime ⇒ historical', () => {
    writeFileSync(
      path.join(tmp, 'hub-server.out'),
      [
        '127.0.0.1 - - [19/Oct/2026 12:00:00] "GET /admin/errors?since=0 HTTP/1.1" 200 -',
        '127.0.0.1 - - [19/Oct/2026 12:00:00] "GET /admin/queue HTTP/1.1" 500 -',
      ].join('\n'),
      'utf8',
    )
    writeCourse('course-live', '[09:00:00] ModuleNotFoundError: numpy\n')
    writeCourse('course-old', '[09:00:00] ModuleNotFoundError: numpy\n')
    const old = (Date.now() - 3 * 24 * 60 * 60 * 1000) / 1000
    utimesSync(path.join(tmp, 'course-old', 'training-loop.log'), old, old)

    const now = Date.now()
    const events = scanLocalLogEvents(loadConfigSafe(), now)
    const hub = events.filter((e) => e.component === 'hubServer')
    expect(hub.length).toBe(1) // ★ 不是 ×2（全局日志不按课重复）
    expect(hub[0]!.classification).toBe('remote-failure')

    const live = events.find((e) => e.scope === 'course-live')!
    const historic = events.find((e) => e.scope === 'course-old')!
    expect(live.activity).toBe('active')
    expect(live.classification).toBe('dependency')
    expect(historic.activity).toBe('historical')
    expect(historic.firstAt).toBeLessThan(now - 24 * 60 * 60 * 1000)
  })

  it('课程目录消失 ⇒ 缓存淘汰，事件随之消失（全局件仍复用缓存）', () => {
    const now = Date.now()
    rmSync(path.join(tmp, 'course-old'), { recursive: true, force: true })
    const events = scanLocalLogEvents(loadConfigSafe(), now)
    expect(events.some((e) => e.scope === 'course-old')).toBe(false)
    expect(events.filter((e) => e.component === 'hubServer').length).toBe(1)
  })

  it('185 门课 × 3 拍：缓存条目 = 文件数（全局件不按课复制），收缩扫描面即淘汰', () => {
    // 清掉上一条用例的课，保证条目数可精确断言（hub 日志保留）。
    rmSync(path.join(tmp, 'course-live'), { recursive: true, force: true })
    resetErrorScanCaches()
    const n = 185
    for (let i = 0; i < n; i++)
      writeCourse(`bulk-${String(i).padStart(3, '0')}`, '[09:00:00] ModuleNotFoundError: numpy\n')

    const now = Date.now()
    const first = scanLocalLogEvents(loadConfigSafe(), now)
    expect(first.filter((e) => e.component === 'hubServer').length).toBe(1) // ★ 不是 ×185
    expect(first.filter((e) => e.component === 'trainingLoop').length).toBe(n)
    // 1 份全局日志 + 每课 1 份 trainer 日志 = n + 1 条；重复采集会在这里 ×4/×185。
    expect(logScanCacheEntryCount()).toBe(n + 1)

    // 拍 2/3：签名未变 ⇒ 复用缓存（不新增条目，事件数不涨）。
    scanLocalLogEvents(loadConfigSafe(), now + 10_000)
    const third = scanLocalLogEvents(loadConfigSafe(), now + 20_000)
    expect(third.length).toBe(first.length)
    expect(logScanCacheEntryCount()).toBe(n + 1)

    // 扫描面收缩（100 门课被清理）⇒ 对应缓存条目当拍淘汰。
    for (let i = 0; i < 100; i++) {
      rmSync(path.join(tmp, `bulk-${String(i).padStart(3, '0')}`), { recursive: true, force: true })
    }
    scanLocalLogEvents(loadConfigSafe(), now + 30_000)
    expect(logScanCacheEntryCount()).toBe(n - 100 + 1)
  })
})
