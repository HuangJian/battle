/**
 * local-worker.test.ts — 本机 PPO worker 的形态与接线门禁。
 *
 * 2026-09-19 起它是**共享进程**（用户口径：「localWorker 也不应绑定课程，它和云端 worker 一样，
 * 只与 hub 通信（pull/push），领到任务后直接执行，完成后回传结果」）。理由不是需求而是事实：
 * hub 的取活面**从来不看课程** —— job 由 hub 按队列分发、manifest 自带课程快照、结果按
 * job_id 回家（hub 侧 `course` 只是随包告知的观测字段）。按课程键控因而只产生三样副作用：
 * 一个进程只能服务一门课、同机多份进程抢同一批 job、以及「这门课的 worker」这个不存在的归属感。
 *
 * 本文件守四件事：
 *  ① **形态**：与云端 worker 同一个入口/同一套协议，且 spec **与课程无关**（单 work 目录、
 *     单日志、槽恒 `''`）；
 *  ② **共享语义**：`componentScope` 判据、槽归一、旧形状（每课一条）换代接管与拒重建；
 *  ③ **不连坐**：离开 local 模式时只在「本课是最后一门 local 课」才停那份唯一进程；
 *  ④ **接线门禁**：启动/停止/重建/冒烟/日志/标签五处都在（缺一处 = UI 无声失败）。
 *
 * 行为层（一个进程领多门课的活）由 python 侧假 hub 用例逐条演示：
 * `nn-training/tests/test_local_worker_multi_course.py`。
 *
 * 纪律：端口/容量一律从真实 rl-config 推导（不写死数字）；本文件不 spawn 任何进程。
 */

import { afterAll, beforeAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { CONFIG_PATH, DASHBOARD_ROOT } from '../src/core/paths'
import { sharedHubUrl } from '../src/core/slots'
import {
  LOCAL_WORKER_ENTRY,
  bcLoopSpec,
  localWorkerSpec,
  trainingLoopSpec,
} from '../src/stack/specs'
import { COMPONENT_KILL_TREE } from '../src/core/types'
import {
  COURSE_COMPONENTS,
  SHARED_COMPONENTS,
  componentScope,
  entryForCourse,
  loadRegistry,
  registryTriples,
  saveAnyComponent,
  scopeOf,
} from '../src/core/registry'
import { restartSpecFor } from '../src/server/actions'
import { supersedeLegacyInstances } from '../src/stack/hub'
import { resolveVenvPython } from '../src/core/venv'
import type { RlConfig } from '../src/core/types'

let scratch = ''
let tmpRegistry = ''
const prevRegistry = process.env.BCITY_REGISTRY_FILE

beforeAll(() => {
  scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-lw-'))
  tmpRegistry = path.join(scratch, 'registry.json')
  process.env.BCITY_REGISTRY_FILE = tmpRegistry
})

afterAll(() => {
  if (prevRegistry === undefined) delete process.env.BCITY_REGISTRY_FILE
  else process.env.BCITY_REGISTRY_FILE = prevRegistry
  rmSync(scratch, { recursive: true, force: true })
})

function loadRealConfig(): RlConfig {
  return JSON.parse(readFileSync(CONFIG_PATH, 'utf-8')) as RlConfig
}

/** 双课程配置（两课各占一槽；端口断言从它推导，不写死）。 */
function dualCourseCfg(): RlConfig {
  const cfg = loadRealConfig()
  return {
    ...cfg,
    courses: {
      'course-a': { slot: 0, workers: 1, local_slots: 1 },
      'course-b': { slot: 1, workers: 1, local_slots: 1 },
    },
  }
}

const VENV = { python: 'python', sitePackages: 'sp' }

/** argv 取旗标值（无该旗标 → null）。 */
function flag(spec: { cmd: string[] }, name: string): string | null {
  const i = spec.cmd.indexOf(name)
  return i < 0 ? null : (spec.cmd[i + 1] ?? null)
}

/** 读 dashboard 源码（接线 grep 门禁用）。 */
function src(rel: string): string {
  return readFileSync(path.join(DASHBOARD_ROOT, 'src', rel), 'utf-8')
}

/** 清空账本（用例互不依赖：账本是全局状态，一条测试的遗留就是下一条的噪音）。 */
function resetRegistry(): void {
  writeFileSync(tmpRegistry, '{}')
}

/** 登记一条共享（`''` 槽）实例条目。 */
function saveShared(pid = 424242): void {
  saveAnyComponent('localWorker', '', { pid, course: '', entry: LOCAL_WORKER_ENTRY })
}

// ────────────────────────── ① 形态：与课程无关 ──────────────────────────

describe('localWorker 形态（共享进程）', () => {
  it('spec = 云端同一入口：python -m remote_worker --poll 共享 hub --device cpu', () => {
    const cfg = dualCourseCfg()
    const spec = localWorkerSpec(cfg, VENV)
    expect(spec.key).toBe('localWorker')
    // 共享实例不属于任何单门课：course 为空串 = 槽 `''`
    expect(spec.course).toBe('')
    // 入口命令与云端 notebook 那条完全同源（不是另写一份本机 PPO 实现）
    expect(spec.cmd.slice(0, 4)).toEqual(['python', '-u', '-m', 'remote_worker'])
    expect(flag(spec, '--poll')).toBe(sharedHubUrl(cfg))
    expect(flag(spec, '--out')).toBe('tmp/local-worker')
    expect(flag(spec, '--device')).toBe('cpu')
    expect(flag(spec, '--token')).toBe(cfg.rl.remote_token)
    expect(LOCAL_WORKER_ENTRY).toBe('nn-training/remote_worker.py')
    // 哨兵 = 入口 + 真正的执行链（remote/worker.py 是全部逻辑、protocol.py 是线路格式）
    expect(spec.sentinels).toContain(LOCAL_WORKER_ENTRY)
    expect(spec.sentinels.some((s) => s.endsWith('remote/worker.py'))).toBe(true)
    expect(spec.sentinels.some((s) => s.endsWith('remote/protocol.py'))).toBe(true)
  })

  it('spec 与课程数量无关：多课程配置下逐字段相同（没有课程参数可传）', () => {
    const cfg = dualCourseCfg()
    const a = localWorkerSpec(cfg, VENV)
    const b = localWorkerSpec(cfg, VENV)
    // 逐字段比对（`healthy` 是函数，连引用一起比会假红——比的是「同一份 spec」的可序列化面）
    for (const k of [
      'key',
      'name',
      'course',
      'cmd',
      'env',
      'log',
      'sentinels',
      'killTree',
    ] as const) {
      expect(a[k]).toEqual(b[k])
    }
    expect(a.cmd).not.toContain('--course')
    // 日志唯一（不再 per-course 分目录）——与 component-meta.ts 的读面同源
    expect(a.log.endsWith('local-worker.log')).toBe(true)
    expect(path.dirname(a.log)).not.toContain('course')
  })

  it('整树停止：killTree 标记 + COMPONENT_KILL_TREE 登记（父+子进程监督器）', () => {
    // 云端 remote_worker = 父 supervise_worker + 子 worker_loop；只杀父进程会留下继续
    // 轮询 hub 抢 job 的孤儿 —— 「随时启停」全靠这一条。
    expect(localWorkerSpec(dualCourseCfg(), VENV).killTree).toBe(true)
    expect(COMPONENT_KILL_TREE.has('localWorker')).toBe(true)
    // 共享 trainer（2026-09-19 / R3-5）同样入列：它之下有 rollout / 本机 PPO 子进程，
    // 只杀父进程会留下**还在写同一批 traj** 的孤儿（比 localWorker 更贵——它们会真跑一轮）。
    expect(COMPONENT_KILL_TREE.has('trainingLoop')).toBe(true)
    // 集合与 spec 标记不许漂移：集合里恰好是这两个（多一个少一个都是回归——
    // 少一个？整树杀退化成裸 kill；多一个？没标 killTree 的 spec 被整树杀）。
    expect([...COMPONENT_KILL_TREE].sort()).toEqual(['localWorker', 'trainingLoop'])
  })

  it('torch 线程：配了 rl.torch_threads 才透传（0/缺省 = torch 默认，云端同语义）', () => {
    const base = dualCourseCfg()
    // 真实配置读值推导（不写死）：配了正数就透传，0/缺省不带旗标
    const configured = Number((base.rl as Record<string, unknown>).torch_threads ?? 0)
    expect(flag(localWorkerSpec(base, VENV), '--threads')).toBe(
      configured > 0 ? String(configured) : null,
    )
    const zeroed = { ...base, rl: { ...base.rl, torch_threads: 0 } } as RlConfig
    expect(flag(localWorkerSpec(zeroed, VENV), '--threads')).toBeNull()
  })
})

// ────────────────────────── ② 共享语义：槽归一 / 换代 / 拒重建 ──────────────────────────

describe('localWorker 共享语义', () => {
  it('判据：componentScope = shared；槽归一恒为空串（两课同一份）', () => {
    expect(SHARED_COMPONENTS).toContain('localWorker')
    expect(componentScope('localWorker')).toBe('shared')
    expect(scopeOf('localWorker', 'course-a')).toBe('')
    expect(scopeOf('localWorker', 'course-b')).toBe('')
    // 仍在组件键全集里（顺序 = 枚举顺序：hub 先于 localWorker 先于 trainer）
    expect((COURSE_COMPONENTS as readonly string[]).includes('localWorker')).toBe(true)
  })

  it('账本：共享条目住 `localWorkers[""]`，按课槽查不到（读面不会拿到别课的进程）', () => {
    resetRegistry()
    saveShared()
    const reg = loadRegistry()
    expect(entryForCourse(reg, 'localWorker', '')?.pid).toBe(424242)
    expect(entryForCourse(reg, 'localWorker', 'course-a')).toBeUndefined()
    // 枚举**看得见**它（course = ''）——监督器/看门狗靠三元组遍历，看不见 = 静默失监督。
    // 注意：它与旧账本里的每课条目（course = 课名）是两条不同的记录，枚举时不会混同。
    expect(registryTriples(reg).some((t) => t.key === 'localWorker' && t.course === '')).toBe(true)
  })

  it('重建：共享 spec 逐字段复现；只有旧形状条目时**什么都不重建**（返回 null）', async () => {
    // 旧形状先单独验：`localWorkers['course-a']` 是换代前的残留。共享槽为空 ⇒ 两个调用
    // 都必须返回 null —— 绝不允许凭一条每课残留就把共享 worker 「重建」出来（那等于
    // 操作员从未同意过的第二个进程）。
    resetRegistry()
    saveAnyComponent('localWorker', 'course-a', {
      pid: 999999999,
      course: 'course-a',
      entry: LOCAL_WORKER_ENTRY,
    })
    expect(restartSpecFor('localWorker')).toBeNull()
    expect(restartSpecFor('localWorker', 'course-a')).toBeNull()

    resetRegistry()
    saveShared()
    // 重建走真实 venv 解析（restartSpecFor 内部用它）——两边同一份解析结果，比的是
    // 「重建是否复现原 spec」而不是两条 venv 路径。
    const want = localWorkerSpec(loadRealConfig(), resolveVenvPython())
    const fresh = restartSpecFor('localWorker')
    expect(fresh).not.toBeNull()
    for (const k of [
      'key',
      'name',
      'course',
      'cmd',
      'env',
      'log',
      'sentinels',
      'killTree',
    ] as const) {
      expect(fresh![k]).toEqual(want[k])
    }
  })

  it('换代接管：收掉旧形状的每课 worker，**不碰**共享实例（死条目清账、活实例停掉）', async () => {
    resetRegistry()
    saveShared(777)
    for (const c of ['course-a', 'course-b']) {
      saveAnyComponent('localWorker', c, {
        pid: 999999999, // 死 pid：只验清账，不 kill 任何真进程
        course: c,
        entry: LOCAL_WORKER_ENTRY,
      })
    }
    const taken = await supersedeLegacyInstances('localWorker')
    expect(taken.sort()).toEqual(['course-a', 'course-b'])
    // 账已清（否则旧条目在账本层永久占着同一个角色：每课一条 + 共享一条）
    expect(entryForCourse(loadRegistry(), 'localWorker', 'course-a')).toBeUndefined()
    expect(entryForCourse(loadRegistry(), 'localWorker', 'course-b')).toBeUndefined()
    // ⚠ 共享实例（`''` 槽）必须原封不动 —— 换代接管是「收旧的」，不是「连新的一起收」
    // （错杀它 = 启动共享 worker 时把自己刚起的进程一起杀掉）
    expect(entryForCourse(loadRegistry(), 'localWorker', '')?.pid).toBe(777)
    // 幂等：再跑一次没有可接管的
    expect(await supersedeLegacyInstances('localWorker')).toEqual([])
  })

  // ★ 2026-09-19：`coursesInLocalMode`（判据 = 本课的 `remote_transport` + `remote_hub_url`）
  //   已随「课程与 worker 节点正交」删除：本机 worker 与云机 worker 逐字同权，不再有
  //   「哪门课跑在本机」这个概念。停它 = 本机不再执行**任何**课程的 PPO job（与停云机同语义），
  //   而它是终端操作员显式点的那一个按钮——不再需要从配置反推「还有谁在 local」。
})

// ────────────────────────── ③ 预设与启动/停止接线 ──────────────────────────

describe('local 预设与启动接线（共享形状）', () => {
  it('启动分支不按课：entryOf 无课程参数、startLocalWorker 只吃 cfg+venv', () => {
    const s = src(path.join('server', 'actions', 'start.ts'))
    expect(s).toContain("case 'localWorker'")
    expect(s).toContain("entryOf('localWorker')")
    expect(s).toContain('await startLocalWorker({ cfg, venv })')
    // 不再有「按课查/按课建」的痕迹
    expect(s).not.toContain("entryOf('localWorker', ctx.course)")
    expect(s).not.toContain('startLocalWorker({ course:')
  })

  it('启动步骤内部先换代接管再 spawn，并登记到共享槽', () => {
    const s = src(path.join('stack', 'local-worker.ts'))
    expect(s).toContain("supersedeLegacyInstances('localWorker')")
    expect(s).toContain("saveAnyComponent('localWorker', ''")
    // 换代必须在 spawn 之前（先杀旧的再起新的，否则两份进程同时抢活）
    expect(s.indexOf('supersedeLegacyInstances')).toBeLessThan(s.indexOf('launchSpec(spec)'))
    expect(s).not.toContain('courseLogDir')
  })

  it('停止：停共享实例同时收旧形状每课实例，并把「本机不再执行任何课程的 job」说出来', () => {
    const s = src(path.join('server', 'actions', 'stop.ts'))
    expect(s).toContain('isSharedComponent(key)')
    expect(s).toContain('supersedeLegacyInstances(key)')
    expect(s).toContain('本机不再执行任何课程的 PPO job')
  })

  it('启动编排不再有 local 分支（launch 不选模式，本机 worker 是自己的一张卡片）', () => {
    const p = src(path.join('server', 'actions', 'preset.ts'))
    expect(p).not.toContain("mode === 'push'")
    expect(p).not.toContain("mode === 'local'")
    expect(p).not.toContain("mode !== 'local'")
    // 编排恒一条：本机 agent → 共享 hub → 共享 trainer
    expect(p).toContain("'selfNode',")
    expect(p).toContain("'hubServer',")
    expect(p).toContain("'trainingLoop',")
  })

  // ── trainer 侧（本地 PPO = 本机 worker 领活）──
  it('RL：不注入 --ppo（§3 单一 PPO 路径）也不注入 --remote-transport（交回训练侧 auto 裁决）', () => {
    const cfg = dualCourseCfg()
    const hub = sharedHubUrl(cfg)
    const spec = trainingLoopSpec(cfg, { course: 'course-a', hubUrl: hub, venv: VENV })
    // ★ §3：`--ppo` 已删除 ⇒ argv 里不该再有它（传了 = argparse 直接拒启）
    expect(spec.cmd).not.toContain('--ppo')
    expect(flag(spec, '--remote-transport')).toBeNull()
    expect(flag(spec, '--remote-hub-url')).toBe(hub)
    // 绝不出现进程内 PPO 旗标
    expect(spec.cmd).not.toContain('--local')
  })

  it('BC：恒 --remote，同样不注入 --remote-transport', () => {
    const cfg = dualCourseCfg()
    const hub = sharedHubUrl(cfg)
    const spec = bcLoopSpec(cfg, { course: 'course-a', hubUrl: hub, venv: VENV })
    expect(spec.cmd).toContain('--remote')
    expect(spec.cmd).not.toContain('--local')
    expect(flag(spec, '--remote-transport')).toBeNull()
    expect(flag(spec, '--remote-hub-url')).toBe(hub)
    // hub 仍缺省读 rl-config（不传 hubUrl 时不带旗标）
    const noHub = bcLoopSpec(cfg, { course: 'course-a', venv: VENV })
    expect(flag(noHub, '--remote-hub-url')).toBeNull()
  })
})

// ────────────────────────── ④ 接线门禁（防「只改 helper 忘接线」） ──────────────────────────

describe('接线门禁', () => {
  it('三条杀进程路径都走整树（stop / 全部停止 / 监督重启），不是裸 killPid', () => {
    for (const rel of [
      path.join('server', 'actions', 'stop.ts'),
      path.join('core', 'proc.ts'),
      path.join('server', 'server.ts'),
    ]) {
      const s = src(rel)
      expect(`${rel}: ${s.includes('COMPONENT_KILL_TREE')}`).toBe(`${rel}: true`)
      expect(`${rel}: ${s.includes('killPidTree')}`).toBe(`${rel}: true`)
    }
  })

  it('localWorker 有启动/重建/冒烟/日志/标签五项登记（缺一项则 UI 无声失败）', () => {
    expect(src(path.join('server', 'actions', 'start.ts'))).toContain("case 'localWorker'")
    expect(src(path.join('server', 'actions', 'restart.ts'))).toContain('localWorkerSpec')
    expect(src(path.join('server', 'actions', 'smoke.ts'))).toContain("case 'localWorker'")
    expect(src(path.join('server', 'api', 'component-meta.ts'))).toContain("'localWorker'")
    expect(src(path.join('server', 'api', 'logs.ts'))).toContain('local-worker')
    expect(src(path.join('server', 'actions', 'labels.ts'))).toContain('localWorker')
  })

  it('读面日志与写面同源：唯一一份 local-worker.log（不再按课程分目录）', () => {
    const meta = src(path.join('server', 'api', 'component-meta.ts'))
    expect(meta).toContain("localWorker: () => path.join(LOG_DIR, 'local-worker.log')")
    // 冒烟的日志兜底也必须是那一条路径
    const smoke = src(path.join('server', 'actions', 'smoke.ts'))
    expect(smoke).toContain("path.join(LOG_DIR, 'local-worker.log')")
    // 且不得再出现按课程拼日志的旧写法（课程目录已不属于共享实例）
    expect(smoke).not.toContain("ctx.course || loadConsoleState().course, 'local-worker.log'")
  })
})
