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
// 锁文件目录：开课的前置检查**会读它**（另一份按课 runner 在跑 ⇒ 拒开课），而
// `assertCourseExists` 又要求课程是真课程 ⇒ 不重定向就会往仓库 nn-training/ 写锁。
process.env.BCITY_LOCKS_DIR = path.join(DIR, 'locks')
// 离线开课会自动触发任务包导出（真起 run_rl 子进程）——本套件盯的是开课生命周期，
// 关掉这个副作用（导出正确性由 server-api-task-bundle 套件覆盖）。
process.env.BCITY_NO_AUTO_TASK_BUNDLE = '1'
// 同理关掉「离线开课自动补 it0 基线评估」（2026-09-24）：那是 evalA 真子进程（几百局游戏）。
// 它自己的 argv/三态由 eval-a-baseline 套件钉；本套件只保证**它不再被真起**。
process.env.BCITY_NO_AUTO_BASELINE_EVAL = '1'
mkdirSync(path.join(DIR, 'traj'), { recursive: true })
mkdirSync(path.join(DIR, 'locks'), { recursive: true })
writeFileSync(
  process.env.BCITY_RL_CONFIG,
  JSON.stringify(
    { version: 1, rl: { hub_port: 18787, agent_port: 8990, remote_token: 'tok' }, nodes: [] },
    null,
    2,
  ),
)

import {
  courseRunnerFacts,
  openCourse,
  prepareCourseForOpen,
  stopCourse,
} from '../src/server/actions/course-lifecycle'
import { COURSE_ENABLE_MARKER } from '../src/stack/courses'
import { readLoopControl, setCoursePaused } from '../src/server/actions/loop-control'
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
  rmSync(process.env.BCITY_LOOP_CONTROL!, { force: true }) // 意图文件：每例从「无意图」起（否则上一例的暂停会漏到下一例）
  rmSync(LOCKS, { recursive: true, force: true })
  mkdirSync(LOCKS, { recursive: true })
  writeFileSync(
    process.env.BCITY_RL_CONFIG!,
    JSON.stringify(
      { version: 1, rl: { hub_port: 18787, agent_port: 8990, remote_token: 'tok' }, nodes: [] },
      null,
      2,
    ),
  )
})

/** 锁目录（重定向到临时目录；见文件头 BCITY_LOCKS_DIR）。 */
const LOCKS = path.join(DIR, 'locks')

/** 写一个锁文件（内容形状与 python 侧一致：`pid|exe|ts`）。 */
function writeLock(kind: 'run_rl' | 'run_bc' | 'run_cluster', course: string, pid: number): void {
  const name = course ? `.${kind}.${course}.lock` : `.${kind}.lock`
  writeFileSync(path.join(LOCKS, name), `${pid}|C:\\python.exe|1789800000`, 'utf-8')
}

/** 读**临时** rl-config 里本课的课程级键（绝不碰线上那份）。 */
function courseKeys(course = COURSE): Record<string, unknown> {
  const cfg = JSON.parse(readFileSync(process.env.BCITY_RL_CONFIG!, 'utf-8')) as {
    courses?: Record<string, Record<string, unknown>>
  }
  return cfg.courses?.[course] ?? {}
}

/** 往 rl-config 写本课的**旧形状**残留键（模拟历史开课弹窗留下的值）。
 *
 *  为什么走 Record 视图：这些键在类型表里已删 ⇒ 只有「文件里的残留值」这条路径还在。
 */
function seedCourseKnobs(keys: Record<string, unknown>, course = COURSE): void {
  const cfg = JSON.parse(readFileSync(process.env.BCITY_RL_CONFIG!, 'utf-8')) as {
    courses?: Record<string, Record<string, unknown>>
  }
  // 展开 `undefined` 本就是 no-op（unicorn/no-useless-fallback-in-spread），故不写 `?? {}`。
  cfg.courses = { ...cfg.courses, [course]: { ...cfg.courses?.[course], ...keys } }
  writeFileSync(process.env.BCITY_RL_CONFIG!, JSON.stringify(cfg, null, 2))
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

// ────────────────────────── ①b 开课：离线模式预校验（2026-09-22 事故回归） ──────────────────────────

describe('封存起点（G4-①）', () => {
  it('prepareCourseForOpen(seedPath) ⇒ 从该文件播种（不落 BC）', () => {
    const src = path.join(DIR, 'seed-weights.json')
    writeFileSync(src, '{"seed":1}')
    const r = prepareCourseForOpen('x-seedtest', src)
    const dst = path.join(TRAJ, 'x-seedtest', 'weights.json')
    expect(readFileSync(dst, 'utf8')).toBe('{"seed":1}')
    expect(r.notes.join('\n')).toContain('封存起点')
    rmSync(path.join(TRAJ, 'x-seedtest'), { recursive: true, force: true })
  })

  it('seedFrom 解析不到 ⇒ 开课前响亮拒绝、零副作用', async () => {
    await expect(
      openCourse(COURSE, { seedFrom: { sourceCourse: 'no-such-arch', it: 3 } }),
    ).rejects.toThrow(/起点不可用/)
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(false)
  })
})

describe('openCourse：离线（云机接手）要求课程声明有限 iters', () => {
  const FIX = path.join(DIR, 'curricula-fix') // 夹具课程目录（临时 BCITY_CURRICULA_DIR）
  const trajOf = (c: string) => path.join(DIR, 'traj', c)

  // 惰性 curriculaDir() 每次调用读 env ⇒ 测试内改 env 即生效；用毕删掉恢复真课程目录。
  afterEach(() => {
    delete process.env.BCITY_CURRICULA_DIR
  })

  function fixtureCourses(): void {
    mkdirSync(FIX, { recursive: true })
    // iters=0：事故原形（x20-demo-mix 就是这一形状）——普通 RL 课
    writeFileSync(
      path.join(FIX, 'x-iter0.jsonc'),
      '{\n  "iters": 0,\n  "mode": "per-tick"\n}\n',
      'utf-8',
    )
    // 未声明 iters：BC 课形状（兼作「在线不受闸约束」的夹具）
    writeFileSync(path.join(FIX, 'x-noiters.bc.jsonc'), '{\n  "name": "x-noiters"\n}\n', 'utf-8')
  }

  it('iters=0 ⇒ 响亮 ActionError（可捕获，**不是** process.exit），文案给具体原因', async () => {
    fixtureCourses()
    process.env.BCITY_CURRICULA_DIR = FIX
    await expect(
      openCourse('x-iter0', { trainMode: 'offline', hubMode: { attempts: 1, delayMs: 0 } }),
    ).rejects.toThrow(/没有终点/)
    await expect(
      openCourse('x-iter0', { trainMode: 'offline', hubMode: { attempts: 1, delayMs: 0 } }),
    ).rejects.toThrow(/iters=0/)
  })

  it('未声明 iters ⇒ 同样拒绝（没有终点就不叫整段）', async () => {
    fixtureCourses()
    process.env.BCITY_CURRICULA_DIR = FIX
    await expect(
      openCourse('x-noiters', { trainMode: 'offline', hubMode: { attempts: 1, delayMs: 0 } }),
    ).rejects.toThrow(/没有终点/)
  })

  it('拒绝 = 零副作用：不写开课标记 / 账本 / remote-jobs / 课程旋钮', async () => {
    fixtureCourses()
    process.env.BCITY_CURRICULA_DIR = FIX
    await expect(openCourse('x-iter0', { trainMode: 'offline' })).rejects.toThrow(/没有终点/)
    expect(existsSync(trajOf('x-iter0'))).toBe(false) // prepareCourseForOpen 未被触达
    expect(Object.keys(courseKeys('x-iter0'))).toHaveLength(0) // 旋钮没写
  })

  it('在线开课不受这道闸约束（在线 = 跑到手动停，不需要有限终点）', async () => {
    fixtureCourses()
    process.env.BCITY_CURRICULA_DIR = FIX
    const r = await openCourse('x-noiters', {
      trainMode: 'online',
      hubMode: { attempts: 1, delayMs: 0 },
    })
    expect(r.ok).toBe(true)
    expect(r.message).toContain('已开课')
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
    const r = await openCourse(COURSE, {
      trainMode: 'offline',
      hubMode: { attempts: 1, delayMs: 0 },
    })
    expect(courseKeys()).toMatchObject({ rollout_src: 'run', run_iters: -1 })
    // ★ 全局键（所有课共用的默认面）一个字不动——离线是**这门课**的决定
    const cfg = JSON.parse(readFileSync(process.env.BCITY_RL_CONFIG!, 'utf-8')) as {
      rl: Record<string, unknown>
    }
    expect(cfg.rl.rollout_src).toBeUndefined()
    // 逃生阀置位 ⇒ **不**补 it0 基线（回执里没有那行 note、互斥键没被占 = 没起 evalA 子进程）；
    // 反向（真补）由 eval-a-baseline 套件按 argv/三态钉，不在用例里真跑几百局。
    expect(r.detail!.join('\n')).not.toContain('it0 基线')
    expect(actions.busy.has('eval:A')).toBe(false)
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

  it('★ 降级本机（T7）已删除（§3）：开课**不再**写该键，历史残留值被 prune 清掉', async () => {
    // 先说结论：单一 PPO 路径下没有「降级本机」档位（loop 没有计算能力）⇒
    // 旧开课弹窗写过的 `remote_degrade_after` 已无读者，开课时随 legacy 清理一并删掉。
    seedCourseKnobs({ remote_degrade_after: 3 })
    expect(courseKeys().remote_degrade_after).toBe(3)
    await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(courseKeys().remote_degrade_after).toBeUndefined()
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

  it('离线开课 ⇒ 推的是 offline（该课停车：不再实时派发）', async () => {
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
    // ★ 2026-09-24（plan §2.2 F9）：停课**不是**「云机接手」——它的 hub 推送走 `pushCourseMode`
    //（只推 hub + 落意图），绝不动 `courses.<课>`。误译成写 run/run_iters 会让「停课」把本机
    // 采样也关掉（而停课的定义是非破坏：随时开课接着跑）。
    expect(courseKeys(COURSE)).toEqual({})
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

// ────────────────────────── ④ 开课前置检查：锁的身份 ──────────────────────────
//
// 2026-09-20 用户报障：「❌ x20-steady 未开课：run_rl 锁被 PID 18364 持有」——而 18364 就是
// 控制台自己起的**共享 trainer**（`run_rl_cluster.py --serve`）。它服务多课，每开一门就取该课
// 自己的 per-course 锁 ⇒ 旧判据「锁活着就拒」把**自己人**当成冲突，每次开课都被拒。
// 更糟的是检查排在写面**之后**：被拒的那一次照样写了旋钮 + **开课标记**（= 训练侧/hub 的
// 「在训」闸）⇒ 回执说「未开课」，盘上却已经是开课状态、而暂停意图还留着。

describe('开课前置检查：锁的身份（不是「活着就拒」）', () => {
  it('共享 trainer 握着本课的按课锁 ⇒ **开课成功**（它服务多课，这是正常持有）', async () => {
    writeLock('run_rl', COURSE, process.pid) // 本课锁持有人 ==
    writeLock('run_cluster', '', process.pid) // 共享 trainer 进程级锁持有人
    const r = await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(r.ok).toBe(true)
    expect(r.detail!.join('\n')).toContain('正常持有')
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(true)
    expect(readLoopControl().paused).not.toContain(COURSE)
  })

  it('另一份按课 runner（持有人 ≠ 共享 trainer）⇒ 拒开，且**零副作用**', async () => {
    writeLock('run_rl', COURSE, process.pid) // 按课的另一份 runner（活着的别的进程）
    // 没有共享 trainer 锁 ⇒ 两者不是同一个进程 ⇒ 真冲突
    const r = await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(r.ok).toBe(false)
    expect(r.message).toContain('另一份按课 runner')
    expect(r.detail!.join('\n')).toContain('未写任何东西')
    // ★ 零副作用：没有开课标记 / 没有课程级旋钮 / 没有发现事实 / 没有 hub 动作
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(false)
    expect(courseKeys(COURSE)).toEqual({})
    expect(existsSync(path.join(TRAJ, COURSE, 'remote-jobs'))).toBe(false)
    expect(readLoopControl().paused).not.toContain(COURSE)
    expect(calls).toEqual([]) // 连 hub 都没碰（置模式是写面的一步）
  })

  it('陈旧锁（持有人已死）不拦：照常开课', async () => {
    writeLock('run_rl', COURSE, 999_999_999)
    const r = await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(r.ok).toBe(true)
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(true)
  })

  it('courseRunnerFacts：三态判据（无锁 / 共享 trainer / 真冲突），BC 同规', () => {
    expect(courseRunnerFacts(COURSE, false)).toEqual({
      holder: null,
      cluster: null,
      conflict: null,
    })

    writeLock('run_rl', COURSE, process.pid)
    expect(courseRunnerFacts(COURSE, false)).toEqual({
      holder: process.pid,
      cluster: null,
      conflict: process.pid,
    })

    writeLock('run_cluster', '', process.pid)
    expect(courseRunnerFacts(COURSE, false).conflict).toBeNull()

    // BC 课同判据：锁名换 run_bc，比较的那把仍是共享 trainer 的进程级锁
    writeLock('run_bc', BC_COURSE, process.pid)
    expect(courseRunnerFacts(BC_COURSE, true)).toEqual({
      holder: process.pid,
      cluster: process.pid,
      conflict: null,
    })
    // 共享 trainer 不在 ⇒ 同一把 run_bc 锁立刻变成真冲突（锁名选错就漏拦）
    rmSync(path.join(LOCKS, '.run_cluster.lock'), { force: true })
    expect(courseRunnerFacts(BC_COURSE, true).conflict).toBe(process.pid)
    expect(courseRunnerFacts(BC_COURSE, false).conflict).toBeNull() // 查错了锁名（RL）⇒ 无锁
  })
})

// ────────────────────────── ⑤ 开课的写序：被拒 = **零副作用** ──────────────────────────
//
// 用户那次报障的完整形状值得钉死：旧代码把「会拒绝的检查」排在写面**之后** ⇒ 回执说「未开课」，
// 盘上却已写了旋钮 + **开课标记**（= 训练侧/hub 的「在训」闸），而暂停意图还留着。
// 于是三种口径并存：控制台说没开课、hub/云机说这课在训、调度器按暂停意图一拍不推。
// 现在：会拒的判据都在 ①（锁身份 + 意图文件健康），写面按「会抛的最前 / 开课标记最后」。

describe('开课的写序：被拒 = 零副作用', () => {
  it('暂停意图文件坏了 ⇒ 拒开课，且盘上一个字都没写（意图文件也不覆盖）', async () => {
    const controlFile = process.env.BCITY_LOOP_CONTROL!
    writeFileSync(controlFile, '{ 这不是 JSON')
    const r = await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(r.ok).toBe(false)
    expect(r.message).toContain('控制文件有问题')
    expect(r.detail!.join('\n')).toContain('未写任何东西')
    // 保守契约：坏文件**不被覆盖**（可能有人在手改/另一份工具在写）
    expect(readFileSync(controlFile, 'utf-8')).toBe('{ 这不是 JSON')
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(false)
    expect(courseKeys(COURSE)).toEqual({})
    expect(existsSync(path.join(TRAJ, COURSE, 'remote-jobs'))).toBe(false)
    expect(calls).toEqual([])
  })

  it('旋钮写不进去（saveConfig 抛）⇒ 零变化：不带开课标记、连暂停意图都没解', async () => {
    // 注入失败：把 rl-config 路径指到一个**目录**上 ⇒ loadConfig/saveConfig 必抛（EISDIR）。
    setCoursePaused(COURSE, true) // 先置成暂停态：这一态在失败后必须**原样保留**
    const real = process.env.BCITY_RL_CONFIG!
    process.env.BCITY_RL_CONFIG = DIR
    try {
      await expect(openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })).rejects.toThrow()
    } finally {
      process.env.BCITY_RL_CONFIG = real
    }
    expect(readLoopControl().paused).toContain(COURSE) // 会抛的一步在最前 ⇒ 意图没被解掉
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(false)
    expect(existsSync(path.join(TRAJ, COURSE, 'remote-jobs'))).toBe(false)
    expect(calls).toEqual([])
  })

  it('开课成功：回执里「暂停意图」写在开课标记之前（写序与盘上一致）', async () => {
    setCoursePaused(COURSE, true)
    const r = await openCourse(COURSE, { hubMode: { attempts: 1, delayMs: 0 } })
    expect(r.ok).toBe(true)
    const lines = r.detail!
    expect(lines.findIndex((l) => l.startsWith('暂停意图'))).toBeLessThan(
      lines.findIndex((l) => l.includes(COURSE_ENABLE_MARKER)),
    )
    expect(readLoopControl().paused).not.toContain(COURSE)
    expect(existsSync(path.join(TRAJ, COURSE, COURSE_ENABLE_MARKER))).toBe(true)
  })

  it('开课回执带 §5.3 起点-基线对照行（C 例：起点贴基线却配缺省 kk=1）', async () => {
    // 事故里没人被告知的**就是这一行**：本腿恢复的权重已经在课程 bc 权重那一档，
    // 却要以 kk=1 满额锚复活（it1 kl=0.90 连烧 30 轮）。回执必须把它说出口。
    const prev = process.env.BCITY_CURRICULA_DIR
    const cur = path.join(DIR, 'curricula')
    const KK = 'kk-receipt-fixture'
    const PEER = 'kk-receipt-peer'
    mkdirSync(cur, { recursive: true })
    // 课程文件：ref 开、缺省初值（= C 事故那一档配置）+ 配对 V（§2.3 的口径）
    writeFileSync(
      path.join(cur, `${KK}.jsonc`),
      JSON.stringify(
        {
          name: KK,
          kickstart_ref: true,
          warmup_iters: 0,
          mode: 'per-tick',
          paired_rotate_seed: 20260921,
        },
        null,
        2,
      ),
    )
    // 配对对端：同一把 V（机器口径的「兄弟」）+ 账本末条 run_start = 同一把 V
    writeFileSync(
      path.join(cur, `${PEER}.jsonc`),
      JSON.stringify({ name: PEER, mode: 'per-tick', paired_rotate_seed: 20260921 }, null, 2),
    )
    mkdirSync(path.join(TRAJ, PEER), { recursive: true })
    writeFileSync(
      path.join(TRAJ, PEER, 'training_log.jsonl'),
      `${JSON.stringify({ event: 'run_start', iter: 0, rotateSeed: 20260921 })}\n`,
    )
    process.env.BCITY_CURRICULA_DIR = cur
    try {
      mkdirSync(path.join(TRAJ, KK), { recursive: true })
      writeFileSync(path.join(TRAJ, KK, 'weights.json'), '{}')
      writeFileSync(
        path.join(TRAJ, KK, 'eval_log.jsonl'),
        `${[
          JSON.stringify({ event: 'eval_summary', iter: 0, winRate: 0.355, games: 400 }),
          JSON.stringify({ event: 'eval_summary', iter: 57, winRate: 0.352, games: 400 }),
        ].join('\n')}\n`,
      )
      const r = await openCourse(KK, { hubMode: { attempts: 1, delayMs: 0 } })
      expect(r.ok).toBe(true)
      const text = r.detail!.join('\n')
      expect(text).toContain('kk 初值 1（来源：缺省 1.0 ——')
      expect(text).toContain('起点-基线对照：起点 35.2%（账本末次评估 it57')
      expect(text).toContain('★ 起点与基线只差 0.3pp')
      // §2.5：配对核对行（声明 V + 对端读数）必须在开课回执里
      expect(text).toContain('配对 rotateSeed：V=20260921')
      expect(text).toContain(`${PEER} 账本 run_start.rotateSeed=20260921 ✓ 同 V`)
    } finally {
      if (prev === undefined) delete process.env.BCITY_CURRICULA_DIR
      else process.env.BCITY_CURRICULA_DIR = prev
    }
  })
})
