/**
 * course-mode.test.ts — R3-2：每课 hub 派发模式（在线/离线）——**意图落盘 + 回灌**。
 *
 * 为什么单开一份：hub 的 `mode` 是 volatile（重启回启动参数），所以这条链上真正要钉住的
 * 是控制台那一半——① 热切确实打到 `POST /admin/courses?course=&mode=`；② hub 拒绝/不可达
 * 时**意图照样落盘**并如实报告（运维的决定不因 hub 没起来而蒸发）；③ 起 hub 时回灌
 * **两种模式都发**（只补 offline 会让「我点过在线」悄悄失效）。
 *
 * 环境重定向（top-level，早于被 import 的模块取值）：console-state / registry / rl-config
 * 全部指向临时文件——**不读线上** nn-training/rl-config.json（F-B2）。
 */

import { afterAll, beforeEach, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'

const DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-cmode-'))
process.env.BCITY_CONSOLE_STATE = path.join(DIR, 'console-state.json')
process.env.BCITY_REGISTRY_FILE = path.join(DIR, 'registry.json')
process.env.BCITY_RL_CONFIG = path.join(DIR, 'rl-config.json')
writeFileSync(
  process.env.BCITY_RL_CONFIG,
  JSON.stringify({ version: 1, nodes: [], rl: { hub_port: 18787, remote_token: 'tok' } }),
  'utf-8',
)
afterAll(() => {
  try {
    rmSync(DIR, { recursive: true, force: true })
  } catch {
    /* noop */
  }
})

import { loadConsoleState, saveConsoleState } from '../src/server/actions/console-state'
import {
  readCourseModes,
  restoreCourseModes,
  restoreCourseModesNote,
  setCourseMode,
} from '../src/server/actions/course-mode'

interface Call {
  url: string
  method: string
  auth: string
}

let calls: Call[] = []
/** 假 hub：`ok` = 200；`reject` = 400 + error；`throw` = 连不上。 */
let mode: 'ok' | 'reject' | 'throw' = 'ok'

globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
  const url = String(input)
  const headers = (init?.headers ?? {}) as Record<string, string>
  calls.push({ url, method: String(init?.method ?? 'GET'), auth: headers.Authorization ?? '' })
  if (mode === 'throw') throw new Error('ECONNREFUSED 127.0.0.1:18787')
  if (mode === 'reject') {
    return new Response(JSON.stringify({ error: '需要合法 course（[c5, c6]）与 mode' }), {
      status: 400,
    })
  }
  return new Response(JSON.stringify({ course: 'c5', mode: 'offline' }), { status: 200 })
}) as typeof fetch

beforeEach(() => {
  calls = []
  mode = 'ok'
  saveConsoleState({ courseModes: {} })
})

describe('setCourseMode（热切 + 落意图）', () => {
  it('切离线：POST 带 course/mode 与 Bearer，意图落盘，文案说清语义', async () => {
    const r = await setCourseMode('c5', 'offline')
    expect(r.ok).toBe(true)
    expect(calls).toHaveLength(1)
    expect(calls[0].method).toBe('POST')
    expect(calls[0].url).toContain('/admin/courses?course=c5&mode=offline')
    expect(calls[0].auth).toBe('Bearer tok')
    expect(readCourseModes()).toEqual({ c5: 'offline' })
    expect(r.message).toContain('已切离线')
    expect(r.message).toContain('只接收')
  })

  it('切回在线：意图改成 online（hub 侧由它自己复位）', async () => {
    saveConsoleState({ courseModes: { c5: 'offline' } })
    const r = await setCourseMode('c5', 'online')
    expect(r.ok).toBe(true)
    expect(calls[0].url).toContain('mode=online')
    expect(readCourseModes()).toEqual({ c5: 'online' })
    expect(r.message).toContain('恢复实时派发')
  })

  it('hub 拒绝（400）：意图照样落盘 + 报告原因 + 指向回灌（不静默丢意图）', async () => {
    mode = 'reject'
    const r = await setCourseMode('c9', 'offline')
    expect(r.ok).toBe(false)
    expect(r.message).toContain('意图已记录')
    expect(r.message).toContain('需要合法 course')
    expect(r.message).toContain('起 hub 时会按意图回灌')
    expect(readCourseModes()).toEqual({ c9: 'offline' })
  })

  it('hub 不可达（连接被拒）：不抛、意图落盘、文案是网络错误', async () => {
    mode = 'throw'
    const r = await setCourseMode('c5', 'offline')
    expect(r.ok).toBe(false)
    expect(r.message).toContain('意图已记录')
    expect(r.message).toContain('ECONNREFUSED')
    expect(readCourseModes()).toEqual({ c5: 'offline' })
  })

  it('非法模式 / 空课程：**一次都不打 hub**，也不落意图', async () => {
    const bad = await setCourseMode('c5', 'paused')
    expect(bad.ok).toBe(false)
    expect(bad.message).toContain('模式非法')
    const empty = await setCourseMode('', 'offline')
    expect(empty.ok).toBe(false)
    expect(empty.message).toContain('需要课程')
    expect(calls).toHaveLength(0)
    expect(readCourseModes()).toEqual({})
  })

  it('重复点同一模式：幂等重发，文案说「已经是」（不假装发生了切换）', async () => {
    await setCourseMode('c5', 'offline')
    const again = await setCourseMode('c5', 'offline')
    expect(again.ok).toBe(true)
    expect(again.message).toContain('已经是')
    expect(calls).toHaveLength(2)
  })
})

describe('restoreCourseModes（起 hub 后回灌）', () => {
  it('两种模式都发（只补 offline 会让「我点过在线」失效）', async () => {
    saveConsoleState({ courseModes: { c5: 'offline', c6: 'online' } })
    const r = await restoreCourseModes({
      version: 1,
      nodes: [],
      rl: { hub_port: 18787, remote_token: 'tok' },
    } as never)
    expect(r.restored).toBe(2)
    expect(r.failed).toEqual([])
    const urls = calls.map((c) => c.url).sort()
    expect(urls[0]).toContain('course=c5&mode=offline')
    expect(urls[1]).toContain('course=c6&mode=online')
  })

  it('全部失败：逐课点名，摘要含「失败」（不谎报已回灌）', async () => {
    saveConsoleState({ courseModes: { c5: 'offline' } })
    mode = 'reject'
    const note = await restoreCourseModesNote({
      version: 1,
      nodes: [],
      rl: { hub_port: 18787, remote_token: 'tok' },
    } as never)
    expect(note).toContain('失败')
    expect(note).toContain('c5')
  })

  it('无意图：摘要为空串（调用方不该为「什么都没做」编文案）', async () => {
    const note = await restoreCourseModesNote({
      version: 1,
      nodes: [],
      rl: { hub_port: 18787, remote_token: 'tok' },
    } as never)
    expect(note).toBe('')
    expect(calls).toHaveLength(0)
  })
})

describe('console-state 的 courseModes（additive）', () => {
  it('旧 state 文件（无该键）→ 读作空表，不炸', () => {
    writeFileSync(
      process.env.BCITY_CONSOLE_STATE as string,
      JSON.stringify({ course: 'c5' }),
      'utf-8',
    )
    expect(loadConsoleState().courseModes).toEqual({})
    expect(readCourseModes()).toEqual({})
  })

  it('形状不对的键被归一化丢掉（手改文件不该让动作层乱传）', () => {
    saveConsoleState({
      courseModes: { good: 'offline', bad: 'paused', '': 'online' } as never,
    })
    expect(readCourseModes()).toEqual({ good: 'offline' })
  })
})
