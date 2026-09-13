/**
 * training-console-busy.test.ts — 控制台动作 busy 互斥的**键同源**回归。
 *
 * 事故（2026-09-14）：startComponent / stopComponent / smokeComponent 的 `guard` 用
 * 按课键（`<op>:<组件>:<课程>`，S10/P5-W3 引入），`finally` 的 `release` 却仍在用旧
 * 无课键（`start:trainingLoop`）。两者不同源 ⇒ 按课键**永不释放** ⇒ 某课程第一次
 * 启动后，该课程组件此后的每次启动/停止/冒烟都被 409 挡掉，页面 toast 恒为
 * 「该组件正在执行另一动作（启动/停止/冒烟），请稍候再试」。
 * 同一根因的页面侧症状：api.componentViews 读的也是无课键 ⇒ busy 恒 false ⇒ 按钮
 * 不禁用，点了必然 409。修复只认一条纪律：**guard 与 release 与展示三处同源**。
 *
 * 本文件不 spawn 任何进程：
 *  - start：账本里预置一个「存活」条目（PID = 本测试进程）⇒ 命中「已在运行」早退分支；
 *  - stop / smoke：用不存在的课程名（这两条路径不校验课程名）⇒ 无登记、早退。
 * 账本经 BCITY_REGISTRY_FILE 重定向到临时目录，不碰线上 registry.json。
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync } from 'fs'
import os from 'os'
import path from 'path'
import * as actions from '../src/server/actions'
import { saveAnyComponent } from '../src/core/registry'

/** 真实存在的非 BC 课程（start 路径会 validateCourseArg，不存在则 process.exit）。 */
const COURSE = 'c5-gae'
/** 不存在的课程名：stop / smoke 路径不校验课程名，用它可以命中「无登记」早退。 */
const GHOST_COURSE = 'no such course!'

let tmpRegistry = ''
const prevRegistry = process.env.BCITY_REGISTRY_FILE

beforeAll(() => {
  tmpRegistry = path.join(mkdtempSync(path.join(os.tmpdir(), 'bcity-busy-')), 'registry.json')
  process.env.BCITY_REGISTRY_FILE = tmpRegistry
})

afterAll(() => {
  if (prevRegistry === undefined) delete process.env.BCITY_REGISTRY_FILE
  else process.env.BCITY_REGISTRY_FILE = prevRegistry
  rmSync(path.dirname(tmpRegistry), { recursive: true, force: true })
})

afterEach(() => {
  actions.busy.clear()
  actions.busySince.clear()
})

describe('busy 键同源：guard 与 release 必须同键', () => {
  it('startComponent 结束后按课键被释放（否则该课程永久 409）', async () => {
    // 预置存活条目（PID = 自己）⇒ 命中「已在运行」早退，不 spawn、不改真账本
    saveAnyComponent('trainingLoop', COURSE, {
      pid: process.pid,
      entry: 'run_rl.py',
      course: COURSE,
    })
    const r = await actions.startComponent('trainingLoop', { course: COURSE, trainerPpo: 'local' })
    expect(r.message).toContain('已在运行')
    expect([...actions.busy]).toEqual([])
  })

  it('stopComponent 结束后按课键被释放', async () => {
    const r = await actions.stopComponent('trainingLoop', GHOST_COURSE)
    expect(r.ok).toBe(true) // 无登记 → 「未在运行」
    expect([...actions.busy]).toEqual([])
  })

  it('smokeComponent 结束后按课键被释放', async () => {
    // trainingLoop 冒烟的「进程存活」非致命 ⇒ ok 可能为 true；这里只关心 busy 是否释放
    await actions.smokeComponent('trainingLoop', { course: GHOST_COURSE, trainerPpo: 'local' })
    expect([...actions.busy]).toEqual([])
  })

  it('按课键控：两课可同时动作（c5 的启动不挡 c6）', () => {
    actions.busy.add(actions.busyKey('start', 'trainingLoop', 'c5'))
    expect(actions.componentBusy('trainingLoop', 'c5')).toBe(true)
    expect(actions.componentBusy('trainingLoop', 'c6')).toBe(false)
  })
})

describe('busy 展示与 409 判定同源', () => {
  it('componentBusy 认按课键（页面禁用 = 服务端 409）', () => {
    actions.busy.add('start:trainingLoop:c5')
    expect(actions.componentBusy('trainingLoop', 'c5')).toBe(true)
    // 无课程视图 / 其它课程不误报
    expect(actions.componentBusy('trainingLoop')).toBe(false)
    expect(actions.componentBusy('trainingLoop', 'c6')).toBe(false)
  })

  it('单例组件沿用旧键（selfNode 无课程后缀）', () => {
    expect(actions.busyKey('stop', 'selfNode', 'c5')).toBe('stop:selfNode')
    actions.busy.add('stop:selfNode')
    expect(actions.componentBusy('selfNode', 'c5')).toBe(true)
  })
})

describe('busy 自愈：漏 release 的键超过 TTL 自动解锁', () => {
  /** 触发一次 guard（setMode 无 catch：未知模式键直接抛 ActionError，故吞掉）。 */
  async function probeGuard(): Promise<void> {
    await actions.setMode('rl.__probe__', '1').catch(() => {})
  }

  it('过期键在下一次动作前被清扫（组件不会永久锁死）', async () => {
    const stale = actions.busyKey('start', 'trainingLoop', COURSE)
    actions.busy.add(stale)
    actions.busySince.set(stale, Date.now() - 6 * 60 * 1000) // 6 分钟前上锁
    await probeGuard()
    expect(actions.busy.has(stale)).toBe(false)
    // 清扫后同课程可再次启动（不被 409 挡 ⇒ 消息是「已在运行」而非「动作进行中」）
    saveAnyComponent('trainingLoop', COURSE, {
      pid: process.pid,
      entry: 'run_rl.py',
      course: COURSE,
    })
    const again = await actions.startComponent('trainingLoop', {
      course: COURSE,
      trainerPpo: 'local',
    })
    expect(again.message).not.toContain('动作进行中')
  })

  it('未过期的键不被清扫（真在跑的动作不会被抢锁）', async () => {
    const fresh = 'stop:hubServer:c5'
    actions.busy.add(fresh)
    actions.busySince.set(fresh, Date.now())
    await probeGuard()
    expect(actions.busy.has(fresh)).toBe(true)
  })
})
