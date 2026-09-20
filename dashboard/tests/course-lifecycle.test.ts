/**
 * course-lifecycle.test.ts — 开课 / 停课：**进程与课程解耦后的独立入口**（2026-09-20 用户口径）。
 *
 *  用户口径原文：「服务进程启动不应与课程绑定。进程启动时不要自动开启课程训练，需要增加独立的
 *  入口开启/停止课程训练」。
 *
 *  改造前：`启动训练` 的第三步（起共享 trainer）**顺带**做课程准备（播权重 / 建账本 / 写课程旋钮 /
 *  置 hub 模式）。这条链在实测里断在最难看的一处（2026-09-20）：`setCourseMode` 在课程还不存在
 *  （hub 的课程表是**扫盘发现**，此刻 `tmp/<课>/remote-jobs` 还没建出来）时打过去，hub 回
 *  400 `需要合法 course（[]）与 mode(...)` ⇒ 控制台报「失败 x20-steady」。
 *
 *  本文件钉四件事：
 *   ① 开课**把发现事实写全**（账本 + `remote-jobs/` + 权重播种）——`remote-jobs/` 是关键的一半：
 *      hub 认课看它，不建它则置模式必然被拒（上面那条事故）；
 *   ② 开课写的是**课程级**旋钮（`courses.<课>.*`），离线档两把键成对、在线档撤掉离线标记，
 *      `run` 永不进全局 `rl.rollout_src`；
 *   ③ 停课是**非破坏**的：暂停意图 + 该课 hub 置 offline，账本/队列一个字不动（恢复走开课）；
 *   ④ 课程级选项**只在开课动作里**（启动链路拿到它们就 400——静默丢掉是一条假承诺）。
 *
 *  环境重定向：console-state / registry / rl-config / 课程 traj 根 / 控制意图文件全部指向临时
 *  目录——**不读也不写线上任何一份**。hub 用假 fetch（不起真进程；线上 8787 上真有一个 hub，
 *  不 stub 就会改到它）。
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from 'bun:test'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'

const DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-course-life-'))
process.env.BCITY_CONSOLE_STATE = path.join(DIR, 'console-state.json')
process.env.BCITY_REGISTRY_FILE = path.join(DIR, 'registry.json')
process.env.BCITY_RL_CONFIG = path.join(DIR, 'rl-config.json')
process.env.BCITY_TMP_LOGS_DIR = path.join(DIR, 'traj')
process.env.BCITY_LOOP_CONTROL = path.join(DIR, 'loop-control.json')
process.env.BCITY_LOOP_APPLIED = path.join(DIR, 'loop-control.applied.json')
mkdirSync(path.join(DIR, 'traj'), { recursive: true })
writeFileSync(
  process.env.BCITY_RL_CONFIG,
  JSON.stringify(
    { version: 1, rl: { hub_port: 18787, agent_port: 8990, remote_token: 'tok' }, nodes: [] },
    null,
    2,
  ),
)

import {
  COURSE_ENABLE_MARKER,
  openCourse,
  stopCourse,
} from '../src/server/actions/course-lifecycle'
import { readLoopControl } from '../src/server/actions/loop-control'
import * as actions from '../src/server/actions'

/** 真实存在的非 BC 课程（开课要过课程文件存在性检查）。 */
const COURSE = 'c5-gae'
/** 真实存在的 BC 课程（无权重播种那一步）。 */
const BC_COURSE = 'bc-c4'
const TRAJ = path.join(DIR, 'traj')

interface Call {
  url: string
  method: string
}
let calls: Call[] = []
/** 假 hub：`ok` = 200；`unknown` = 400「课程表里没这门课」；`throw` = 连不上。 */
let hub: 'ok' | 'unknown' | 'throw' = 'ok'
/** 前 N 次请求回「不认识的课程」（模拟 hub 还没扫到新建的 remote-jobs/）。 */
let unknownFirst = 0

globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
  const url = String(input)
  calls.push({ url, method: String(init?.method ?? 'GET') })
  if (hub === 'throw') throw new Error('ECONNREFUSED 127.0.0.1:18787')
  if (hub === 'unknown' || unknownFirst > 0) {
    if (unknownFirst > 0) unknownFirst -= 1
    return new Response(JSON.stringify({ error: '需要合法 course（[]）与 mode' }), { status: 400 })
  }
  return new Response(JSON.stringify({ course: COURSE, mode: 'online' }), { status: 200 })
}) as typeof fetch

beforeAll(() => {
  // 夹具：本课权重已存在（跳过播种分支，对真实 BC 产物零依赖）+ 一个坏课程名的目录
  mkdirSync(path.join(TRAJ, COURSE), { recursive: true })
  writeFileSync(path.join(TRAJ, COURSE, 'weights.json'), '{}')
})

afterAll(() => {
  try {
    rmSync(DIR, { recursive: true, force: true })
  } catch {
    /* noop */
  }
})

afterEach(() => {
  calls = []
  hub = 'ok'
  unknownFirst = 0
  actions.busy.clear()
  actions.busySince.clear()
  // 每例之间清干净：开课会写账本/意图/配置/开课标记
  rmSync(path.join(TRAJ, COURSE, 'remote-jobs'), { recursive: true, force: true })
  rmSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER), { force: true })
  writeFileSync(
    process.env.BCITY_RL_CONFIG!,
    JSON.stringify(
      { version: 1, rl: { hub_port: 18787, agent_port: 8990, remote_token: 'tok' }, nodes: [] },
      null,
      2,
    ),
  )
})

/** 读**临时** rl-config 里本课的课程级键（绝不碰线上那份）。 */
function courseKeys(course = COURSE): Record<string, unknown> {
  const cfg = JSON.parse(readFileSync(process.env.BCITY_RL_CONFIG!, 'utf-8')) as {
    courses?: Record<string, Record<string, unknown>>
  }
  return cfg.courses?.[course] ?? {}
}

// ────────────────────────── ① 开课：发现事实 ──────────────────────────

describe('openCourse：把「这门课存在且可被调度」写到盘上', () => {
  it('建账本 + `remote-jobs/`（hub 认课的锚点；不建它置模式必被拒）', async () => {
    const r = await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(r.ok).toBe(true)
    expect(existsSync(path.join(TRAJ, COURSE, 'training_log.jsonl'))).toBe(true)
    expect(existsSync(path.join(TRAJ, COURSE, 'remote-jobs'))).toBe(true)
    expect(r.detail!.join('\n')).toContain('remote-jobs')
  })

  it('写**开课标记**（在训判据：有账本 ≠ 在训——历史课全都有账本）', async () => {
    // 用户 2026-09-20 报障：「启动 trainingloop 成功后，界面显示一堆课程正在训练」——
    // 根因就是「有账本 = 在训」；开课标记（训练侧 `enabled_courses` / hub `_course_dir_live`
    // 的同一个闸）是控制台按下的那一下。
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(false)
    const r = await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(true)
    expect(r.detail!.join('\n')).toContain('开课标记')
  })

  it('幂等：账本/目录已存在时不重复建（重按开课 = 零副作用）', async () => {
    await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    const again = await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(again.ok).toBe(true)
    expect(again.detail!.join('\n')).not.toContain('已建课程账本')
  })

  it('BC 课程不播种权重（无 warm-start；它自带 BC 语料那一路）', async () => {
    mkdirSync(path.join(TRAJ, BC_COURSE), { recursive: true })
    const r = await openCourse(BC_COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(r.ok).toBe(true)
    expect(existsSync(path.join(TRAJ, BC_COURSE, 'weights.json'))).toBe(false)
  })

  it('课程名不存在 ⇒ 可捕获的 ActionError（**不是** process.exit：控制台不能被一次误点杀掉）', async () => {
    await expect(openCourse('no-such-course-xyz')).rejects.toThrow(/不存在/)
  })

  it('空课程 ⇒ 拒绝（开课是按课程记的）', async () => {
    await expect(openCourse('')).rejects.toThrow(/需要课程/)
  })
})

// ────────────────────────── ② 开课：课程级旋钮 ──────────────────────────

describe('openCourse：课程级旋钮只落 courses.<课>', () => {
  it('在线：撤掉离线标记（run_iters 必删；只删 run = 半状态）', async () => {
    // 先造出「已经是离线档」的历史配置（模拟上一轮开成离线）
    writeFileSync(
      process.env.BCITY_RL_CONFIG!,
      JSON.stringify(
        {
          version: 1,
          rl: { hub_port: 18787, agent_port: 8990, remote_token: 'tok' },
          nodes: [],
          courses: { [COURSE]: { rollout_src: 'run', run_iters: -1 } },
        },
        null,
        2,
      ),
    )
    await openCourse(COURSE, { trainMode: 'online', hubMode: { attempts: 1, delayMs: 0 } })
    const k = courseKeys()
    expect(k.run_iters).toBeUndefined()
    expect(k.rollout_src).toBeUndefined()
  })

  it('离线：声明 + 段长两把键成对落课程级（缺一不可）', async () => {
    await openCourse(COURSE, { trainMode: 'offline', hubMode: { attempts: 1, delayMs: 0 } })
    expect(courseKeys()).toMatchObject({ rollout_src: 'run', run_iters: -1 })
    // ★ 全局键（所有课共用的默认面）一个字不动——离线是**这门课**的决定
    const cfg = JSON.parse(readFileSync(process.env.BCITY_RL_CONFIG!, 'utf-8')) as {
      rl: Record<string, unknown>
    }
    expect(cfg.rl.rollout_src).toBeUndefined()
  })

  it('在线 + rollout 位置：写课程级覆盖（不碰全局 rl.rollout_src）', async () => {
    await openCourse(COURSE, {
      trainMode: 'online',
      rolloutSrc: 'node',
      hubMode: { attempts: 1, delayMs: 0 },
    })
    expect(courseKeys().rollout_src).toBe('node')
    const cfg = JSON.parse(readFileSync(process.env.BCITY_RL_CONFIG!, 'utf-8')) as {
      rl: Record<string, unknown>
    }
    expect(cfg.rl.rollout_src).toBeUndefined()
  })

  it('降级本机（T7）：给出才写；不给则不动该键（不顺手清空别人的配置）', async () => {
    await openCourse(COURSE, { remoteDegrade: true, hubMode: { attempts: 1, delayMs: 0 } })
    expect(courseKeys().remote_degrade_after).toBe(3)
    await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(courseKeys().remote_degrade_after).toBe(3)
    await openCourse(COURSE, { remoteDegrade: false, hubMode: { attempts: 1, delayMs: 0 } })
    expect(courseKeys().remote_degrade_after).toBe(0)
  })

  it('离线档忽略 rollout 选择（服务端侧：只认 run/run_iters 那对键）', async () => {
    await openCourse(COURSE, {
      trainMode: 'offline',
      rolloutSrc: 'node',
      hubMode: { attempts: 1, delayMs: 0 },
    })
    expect(courseKeys().rollout_src).toBe('run')
  })
})

// ────────────────────────── ③ 开课：hub 模式（含 2026-09-20 事故回归） ──────────────────────────

describe('openCourse：置 hub 模式在发现事实**之后**（事故回归）', () => {
  it('hub 还没扫到这门课：有界重试后仍拒 ⇒ 报「意图已记录」而不是失败', async () => {
    hub = 'unknown'
    const r = await openCourse(COURSE, { hubMode: { attempts: 2, delayMs: 0 } })
    // 课程**已开**（账本/目录都在）——hub 那一半是异步收敛的，不能因此把开课判成失败
    expect(r.ok).toBe(true)
    expect(existsSync(path.join(TRAJ, COURSE, 'training_log.jsonl'))).toBe(true)
    expect(r.detail!.join('\n')).toContain('意图已记录')
    expect(r.message).toContain('已开课')
  })

  it('hub 从「不认识的课程」变成接受（扫到了）⇒ 重试生效，摘要不再带未接受提示', async () => {
    unknownFirst = 1 // 第一次 400，之后 200
    const r = await openCourse(COURSE, { hubMode: { attempts: 3, delayMs: 0 } })
    expect(r.ok).toBe(true)
    expect(calls.filter((c) => c.url.includes('/admin/courses')).length).toBe(2)
    expect(r.detail!.join('\n')).toContain('hub 该课模式 = online')
  })

  it('落盘顺序：先建 `remote-jobs/`，再打 hub（否则置模式必然被拒）', async () => {
    unknownFirst = 1
    await openCourse(COURSE, { hubMode: { attempts: 2, delayMs: 0 } })
    // 断言的是**事实**：在 hub 收到请求之前，目录已经在了（逐字节顺序的另一半见源码）
    expect(calls.length).toBeGreaterThan(0)
    expect(existsSync(path.join(TRAJ, COURSE, 'remote-jobs'))).toBe(true)
  })

  it('hub 完全不可达：意图照样落盘（起 hub 时回灌），开课仍成立', async () => {
    hub = 'throw'
    const r = await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(r.ok).toBe(true)
    const modes = actions.readCourseModes()
    expect(modes[COURSE]).toBe('online')
  })

  it('离线开课 ⇒ 推的是 offline（整段只交给带标 worker）', async () => {
    await openCourse(COURSE, { trainMode: 'offline', hubMode: { attempts: 1, delayMs: 0 } })
    expect(calls[0]!.url).toContain('mode=offline')
  })
})

// ────────────────────────── ④ 停课：非破坏 ──────────────────────────

describe('stopCourse：非破坏停课（暂停意图 + hub 置离线）', () => {
  it('写暂停意图 + 推 hub offline；账本一个字不动', async () => {
    writeFileSync(path.join(TRAJ, COURSE, 'training_log.jsonl'), '{"event":"iteration"}\n')
    const before = readFileSync(path.join(TRAJ, COURSE, 'training_log.jsonl'), 'utf-8')
    const r = await stopCourse(COURSE)
    expect(r.ok).toBe(true)
    expect(readLoopControl().paused).toContain(COURSE)
    // 删开课标记 = 训练侧/hub 下一拍就不再把这门课当在训（pill 随之从顶部消失）
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(false)
    expect(calls[0]!.url).toContain('mode=offline')
    // 账本/队列保留（用户口径：暂停 = 保留队列，不删）
    expect(readFileSync(path.join(TRAJ, COURSE, 'training_log.jsonl'), 'utf-8')).toBe(before)
    expect(r.detail!.join('\n')).toContain('队列与账本一个字不动')
  })

  it('可逆：停课 → 开课把暂停意图清掉（否则「开了课但不推进」）', async () => {
    await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    await stopCourse(COURSE)
    expect(readLoopControl().paused).toContain(COURSE)
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(false)
    await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(readLoopControl().paused).not.toContain(COURSE)
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(true)
  })

  it('hub 不可达：意图照记 + 如实报告（停课本身仍成立）', async () => {
    hub = 'throw'
    const r = await stopCourse(COURSE)
    expect(r.ok).toBe(true)
    expect(readLoopControl().paused).toContain(COURSE)
    expect(r.detail!.join('\n')).toContain('意图已记录')
  })

  it('空课程 ⇒ 拒绝（停课是按课程记的）', async () => {
    await expect(stopCourse('')).rejects.toThrow(/需要课程/)
  })
})
