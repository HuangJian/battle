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
 *  - start：账本里预置一个「存活」条目（PID = 本测试进程）⇒ 命中「已在运行」早退分支
 *    （**共享槽**：trainer 自 2026-09-19 / R3-5 起是一个进程服务所有课程，槽恒 `''`）；
 *  - stop / smoke：用不存在的课程名（这两条路径不校验课程名）⇒ 无登记、早退。
 * 账本经 BCITY_REGISTRY_FILE 重定向到临时目录，不碰线上 registry.json。
 *
 *  ★ R3-5 起「早退分支」不再是纯读：它照样做**本课准备**（机器侧旋钮写 rl-config、建课程账本、
 *  RL 课缺 weights.json 时从 BC 播种）。故夹具必须把这两处也重定向——否则这个用例会把断言
 *  挂在「本机 tmp/c5-gae 恰好有没有权重文件」上（2026-09-19 实测：真机上 BC 产物缺失 ⇒ 播种抛
 *  『初始权重缺失』⇒ 幂等早退变成失败，测的已经不是 busy 键了）。
 *  预置权重 + 临时 rl-config 之后，准备阶段全部落在临时目录、零外部依赖。
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import * as actions from '../src/server/actions'
import { clearAnyComponent, saveAnyComponent } from '../src/core/registry'

/** 真实存在的非 BC 课程（start 路径会 validateCourseArg，不存在则 process.exit）。 */
const COURSE = 'c5-gae'
/** 不存在的课程名：stop / smoke 路径不校验课程名，用它可以命中「无登记」早退。 */
const GHOST_COURSE = 'no such course!'

let tmpRegistry = ''
const prevRegistry = process.env.BCITY_REGISTRY_FILE
const prevTmpLogs = process.env.BCITY_TMP_LOGS_DIR
const prevRlConfig = process.env.BCITY_RL_CONFIG

beforeAll(() => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-busy-'))
  tmpRegistry = path.join(dir, 'registry.json')
  process.env.BCITY_REGISTRY_FILE = tmpRegistry
  // ④ 本课准备面（R3-5）：traj 根 + rl-config 各自重定向到临时目录；本课权重预置
  // ⇒ 播种分支短路（对真实 BC 产物零依赖），旋钮写盘只落临时 rl-config。
  process.env.BCITY_TMP_LOGS_DIR = dir
  process.env.BCITY_RL_CONFIG = path.join(dir, 'rl-config.json')
  writeFileSync(
    process.env.BCITY_RL_CONFIG,
    JSON.stringify(
      { version: 1, rl: { agent_port: 8990, remote_token: 'tok' }, nodes: [], courses: {} },
      null,
      2,
    ),
  )
  mkdirSync(path.join(dir, COURSE), { recursive: true })
  writeFileSync(path.join(dir, COURSE, 'weights.json'), '{}')
})

afterAll(() => {
  if (prevRegistry === undefined) delete process.env.BCITY_REGISTRY_FILE
  else process.env.BCITY_REGISTRY_FILE = prevRegistry
  if (prevTmpLogs === undefined) delete process.env.BCITY_TMP_LOGS_DIR
  else process.env.BCITY_TMP_LOGS_DIR = prevTmpLogs
  if (prevRlConfig === undefined) delete process.env.BCITY_RL_CONFIG
  else process.env.BCITY_RL_CONFIG = prevRlConfig
  rmSync(path.dirname(tmpRegistry), { recursive: true, force: true })
})

afterEach(() => {
  actions.busy.clear()
  actions.busySince.clear()
})

describe('busy 键同源：guard 与 release 必须同键', () => {
  it('startComponent 结束后按课键被释放（否则该课程永久 409）', async () => {
    // 预置存活条目（PID = 自己）⇒ 命中「已在运行」早退，不 spawn、不改真账本。
    // 槽 = `scopeOf('trainingLoop', …)`：共享 trainer 的条目只活在空串槽里。
    saveAnyComponent('trainingLoop', '', {
      pid: process.pid,
      entry: 'run_rl_cluster.py',
      course: '',
    })
    const r = await actions.startComponent('trainingLoop', { course: COURSE })
    expect(r.message).toContain('已在运行')
    // 幂等分支也要能回答「它服务谁」——一个进程服务所有课程
    expect(r.message).toContain('服务所有课程')
    expect([...actions.busy]).toEqual([])
    // 清掉这条**存活**条目：它是共享槽且 PID = 本进程，留着会被下面的 stop 用例
    // 当成真在跑的 trainer 杀掉（共享槽的语义就是「停它 = 停本进程」）。
    clearAnyComponent('trainingLoop', '')
  })

  it('本课准备失败不得说成「启动失败」——trainer 在跑这个事实必须留住', async () => {
    // 造一个**确定性**的准备失败：traj 根指向一个普通文件 ⇒ 播种/建目录/建账本三路都必抛，
    // 与「本机恰好有没有 BC 产物」无关（否则这条用例会变成环境断言）。
    const bogus = path.join(path.dirname(tmpRegistry), 'not-a-dir')
    writeFileSync(bogus, 'x')
    const prevLogs = process.env.BCITY_TMP_LOGS_DIR
    process.env.BCITY_TMP_LOGS_DIR = bogus
    try {
      saveAnyComponent('trainingLoop', '', {
        pid: process.pid, // 必活：命中幂等早退分支
        entry: 'run_rl_cluster.py',
        course: '',
      })
      const r = await actions.startComponent('trainingLoop', { course: COURSE })
      expect(r.ok).toBe(false)
      // ① 事实留住：进程在跑、服务所有课程。少了这句，操作员会去停/重启 trainer ——
      //    而停共享 trainer = 停掉**所有**课程的训练。
      expect(r.message).toContain('已在运行')
      expect(r.message).toContain('服务所有课程')
      // ② 失败归因到**本课**，不是「trainer 起不来」（那是另一条分支的话术）
      expect(r.message).toContain('本课')
      expect(r.message).not.toContain('启动失败')
      expect([...actions.busy]).toEqual([])
    } finally {
      process.env.BCITY_TMP_LOGS_DIR = prevLogs
      clearAnyComponent('trainingLoop', '')
    }
  })

  it('stopComponent 结束后按课键被释放', async () => {
    // 空账本：停在「无登记 → 未在运行」分支（不碰任何真进程，也不去清 nn-training 下的锁）
    writeFileSync(tmpRegistry, '{}')
    const r = await actions.stopComponent('trainingLoop', GHOST_COURSE)
    expect(r.ok).toBe(true) // 无登记 → 「未在运行」
    expect([...actions.busy]).toEqual([])
  })

  it('smokeComponent 结束后按课键被释放', async () => {
    // trainingLoop 冒烟的「进程存活」非致命 ⇒ ok 可能为 true；这里只关心 busy 是否释放
    await actions.smokeComponent('trainingLoop', { course: GHOST_COURSE })
    expect([...actions.busy]).toEqual([])
  })

  it('按课键控：两课可同时动作（c5 的 localWorker 启动不挡 c6）', () => {
    // 用 localWorker 举例而不是 trainer：trainer 已收敛为**进程级一份**（2026-09-19 / R3-5），
    // 它的键无课程后缀（c5 的启动**应该**挡住 c6——同一件事）。
    actions.busy.add(actions.busyKey('start', 'localWorker', 'c5'))
    expect(actions.componentBusy('localWorker', 'c5')).toBe(true)
    expect(actions.componentBusy('localWorker', 'c6')).toBe(false)
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
    // trainer 的 guard 键 = `scopeOf` 归一后的（共享角色无课程后缀）：夹具必须同源，
    // 否则测的是「一个永远不会被上锁的键」（假绿）。
    const stale = actions.busyKey('start', 'trainingLoop', '')
    actions.busy.add(stale)
    actions.busySince.set(stale, Date.now() - 6 * 60 * 1000) // 6 分钟前上锁
    await probeGuard()
    expect(actions.busy.has(stale)).toBe(false)
    // 清扫后同课程可再次启动（不被 409 挡 ⇒ 消息是「已在运行」而非「动作进行中」）
    saveAnyComponent('trainingLoop', '', {
      pid: process.pid,
      entry: 'run_rl_cluster.py',
      course: '',
    })
    const again = await actions.startComponent('trainingLoop', { course: COURSE })
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
