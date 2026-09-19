/**
 * local-worker.test.ts — 本机 PPO 拆成独立 worker（2026-09-15）的形态与接线门禁。
 *
 * 交付物三件事，本文件逐条守：
 *  ① 组件 `localWorker` = **云端 worker 同一个入口**（`python -m remote_worker --poll 本课
 *     hub`），per-course、受管（账本 + 监督 + 整树停止）；
 *  ② 控制台 `local` 预设 = hubServer → localWorker → trainer(--ppo remote + 本机 hub)，
 *     进程内 PPO（`--ppo local` / run_bc `--local`）不再是控制台的编排选项；
 *  ③ 传输必须**钉死 pull**：某课用过 push 后 `courses.<课>.push_node_url` 会留在
 *     rl-config 里，不钉死则训练器把 job 推去云机，本机 worker 永远空转。
 *
 * 纪律（同 training-multi-course.test.ts）：端口/容量一律从真实 rl-config 推导，不写死数字。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync } from 'fs'
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
  entryForCourse,
  loadRegistry,
  registryTriples,
  saveCourseComponent,
} from '../src/core/registry'
import { restartSpecFor } from '../src/server/actions'
import { slotPort } from '../src/core/slots'
import { resolveVenvPython } from '../src/core/venv'
import type { RlConfig } from '../src/core/types'

const SCRATCH: string[] = []
afterAll(() => {
  for (const d of SCRATCH) {
    try {
      rmSync(d, { recursive: true, force: true })
    } catch {
      /* noop */
    }
  }
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

// ────────────────────────── ① 组件形态 ──────────────────────────

describe('localWorker 组件形态', () => {
  it('spec = 云端同一入口：python -m remote_worker --poll 本课 hub --device cpu', () => {
    const cfg = dualCourseCfg()
    const spec = localWorkerSpec(cfg, VENV, 'course-a')
    expect(spec.key).toBe('localWorker')
    expect(spec.course).toBe('course-a')
    // 入口命令与云端 notebook 那条完全同源（不是另写一份本机 PPO 实现）
    expect(spec.cmd.slice(0, 4)).toEqual(['python', '-u', '-m', 'remote_worker'])
    expect(flag(spec, '--poll')).toBe(`http://127.0.0.1:${slotPort(cfg, 'course-a', 'hub')}`)
    expect(flag(spec, '--out')).toBe('tmp/local-worker-course-a')
    expect(flag(spec, '--device')).toBe('cpu')
    expect(flag(spec, '--token')).toBe(cfg.rl.remote_token)
    expect(LOCAL_WORKER_ENTRY).toBe('nn-training/remote_worker.py')
    // 哨兵 = 入口 + 真正的执行链（remote/worker.py 是全部逻辑、protocol.py 是线路格式）
    expect(spec.sentinels).toContain(LOCAL_WORKER_ENTRY)
    expect(spec.sentinels.some((s) => s.endsWith('remote/worker.py'))).toBe(true)
    expect(spec.sentinels.some((s) => s.endsWith('remote/protocol.py'))).toBe(true)
  })

  it('整树停止：killTree 标记 + COMPONENT_KILL_TREE 登记（父+子进程监督器）', () => {
    // 云端 remote_worker = 父 supervise_worker + 子 worker_loop；只杀父进程会留下继续
    // 轮询 hub 抢 job 的孤儿 —— 「随时启停」全靠这一条。
    expect(localWorkerSpec(dualCourseCfg(), VENV, 'c').killTree).toBe(true)
    expect(COMPONENT_KILL_TREE.has('localWorker')).toBe(true)
    // 共享 trainer（2026-09-19 / R3-5）同样入列：它之下有 rollout / 本机 PPO 子进程，
    // 只杀父进程会留下**还在写同一批 traj** 的孤儿（比 localWorker 更贵——它们会真跑一轮）。
    expect(COMPONENT_KILL_TREE.has('trainingLoop')).toBe(true)
    // 集合与 spec 标记不许漂移：集合里恰好是这两个（多一个少一个都是回归——
    // 少一个？整树杀退化成裸 kill；多一个？没标 killTree 的 spec 被整树杀）。
    expect([...COMPONENT_KILL_TREE].sort()).toEqual(['localWorker', 'trainingLoop'])
  })

  it('双课隔离：work 目录 / 日志互不相同；**poll 目标相同**（共享 hub）', () => {
    const cfg = dualCourseCfg()
    const a = localWorkerSpec(cfg, VENV, 'course-a')
    const b = localWorkerSpec(cfg, VENV, 'course-b')
    // 共享 hub（2026-09-18）：一个作业中枢服务所有课程，两个本机 worker 轮询同一地址
    // （领到哪门课的 job 就干哪门课的活：job 自带课程快照，结果按 job_id 回家）。
    expect(flag(a, '--poll')).toBe(sharedHubUrl(cfg))
    expect(flag(b, '--poll')).toBe(sharedHubUrl(cfg))
    // 隔离面 = 工作目录（双课同机时 job payload 归档不得互踩）与日志
    expect(flag(a, '--out')).not.toBe(flag(b, '--out'))
    expect(a.log).not.toBe(b.log)
  })

  it('torch 线程：配了 rl.torch_threads 才透传（0/缺省 = torch 默认，云端同语义）', () => {
    const base = dualCourseCfg()
    // 真实配置读值推导（不写死）：配了正数就透传，0/缺省不带旗标
    const configured = Number((base.rl as Record<string, unknown>).torch_threads ?? 0)
    expect(flag(localWorkerSpec(base, VENV, 'c'), '--threads')).toBe(
      configured > 0 ? String(configured) : null,
    )
    const zeroed = { ...base, rl: { ...base.rl, torch_threads: 0 } } as RlConfig
    expect(flag(localWorkerSpec(zeroed, VENV, 'c'), '--threads')).toBeNull()
  })

  it('注册为按课程键控组件（监督/停止/日志三链共用 CourseComponent 约定）', () => {
    expect((COURSE_COMPONENTS as readonly string[]).includes('localWorker')).toBe(true)
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-lw-'))
    SCRATCH.push(scratch)
    const prev = process.env.BCITY_REGISTRY_FILE
    process.env.BCITY_REGISTRY_FILE = path.join(scratch, 'registry.json')
    try {
      const cfg = dualCourseCfg()
      // 重建走真实 venv 解析（restartSpecFor 内部用 resolveVenvPython）——用同一份
      // 解析结果构造对照 spec，比对才比的是「重建是否复现原 spec」而非两条 venv 路径。
      const spec = localWorkerSpec(cfg, resolveVenvPython(), 'course-a')
      saveCourseComponent('localWorker', 'course-a', {
        pid: 424242,
        course: 'course-a',
        entry: LOCAL_WORKER_ENTRY,
        log: spec.log,
      })
      expect(entryForCourse(loadRegistry(), 'localWorker', 'course-a')?.pid).toBe(424242)
      // 枚举顺序：同课内 hubServer 先于 localWorker（作业中枢 → 执行者）
      const triples = registryTriples(loadRegistry()).filter((t) => t.course === 'course-a')
      expect(triples.map((t) => t.key).indexOf('hubServer')).toBeLessThan(
        triples.map((t) => t.key).indexOf('localWorker'),
      )
      // 重建逐字段一致（监督器重启的数据源——不等于原 spec 就会把 poll 目标换掉）
      const fresh = restartSpecFor('localWorker', 'course-a')
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
        expect(fresh![k]).toEqual(spec[k])
      }
    } finally {
      if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
      else process.env.BCITY_REGISTRY_FILE = prev
    }
  })
})

// ────────────────────────── ② trainer 编排：local = 本机 worker ──────────────────────────

describe('trainer 编排：local = 本机独立 worker（进程内 PPO 已下线）', () => {
  it('RL：local → --ppo remote + 传输钉死 pull + 指名本机 hub', () => {
    const cfg = dualCourseCfg()
    const hub = `http://127.0.0.1:${slotPort(cfg, 'course-a', 'hub')}`
    const spec = trainingLoopSpec(cfg, {
      course: 'course-a',
      ppo: 'local',
      hubUrl: hub,
      venv: VENV,
    })
    expect(flag(spec, '--ppo')).toBe('remote')
    expect(flag(spec, '--remote-transport')).toBe('pull')
    expect(flag(spec, '--remote-hub-url')).toBe(hub)
    // 绝不出现进程内 PPO 旗标
    expect(spec.cmd).not.toContain('--local')
  })

  it('T7：默认 --remote-degrade-after 0；opt-in 时为 3', () => {
    const cfg = dualCourseCfg()
    const hub = `http://127.0.0.1:${slotPort(cfg, 'course-a', 'hub')}`
    const off = trainingLoopSpec(cfg, {
      course: 'course-a',
      ppo: 'local',
      hubUrl: hub,
      venv: VENV,
    })
    expect(flag(off, '--remote-degrade-after')).toBe('0')
    const on = trainingLoopSpec(cfg, {
      course: 'course-a',
      ppo: 'local',
      hubUrl: hub,
      venv: VENV,
      remoteDegrade: true,
    })
    expect(flag(on, '--remote-degrade-after')).toBe('3')
  })

  it('BC：local → --remote 同样钉 pull，不再产出 run_bc 的 --local', () => {
    const cfg = dualCourseCfg()
    const hub = `http://127.0.0.1:${slotPort(cfg, 'course-a', 'hub')}`
    for (const ppo of ['local', 'pull', 'push'] as const) {
      const spec = bcLoopSpec(cfg, { course: 'course-a', ppo, hubUrl: hub, venv: VENV })
      expect(spec.cmd).toContain('--remote')
      expect(spec.cmd).not.toContain('--local')
    }
    const local = bcLoopSpec(cfg, { course: 'course-a', ppo: 'local', hubUrl: hub, venv: VENV })
    expect(flag(local, '--remote-transport')).toBe('pull')
    expect(flag(local, '--remote-hub-url')).toBe(hub)
    // 2026-09-17：pull preset 也钉死传输（hub 仍缺省读 rl-config）——不钉会被
    // rl-config 里残留的 gpu_push 节点劫走（auto = 「本课 gpu_push > hub」），
    // 实测代价：云机 pull 会话 push 530 三连败 → GATE ABORT 停腿。
    const pull = bcLoopSpec(cfg, { course: 'course-a', ppo: 'pull', venv: VENV })
    expect(flag(pull, '--remote-transport')).toBe('pull')
    expect(flag(pull, '--remote-hub-url')).toBeNull()
  })

  it('云端 preset：pull 钉死传输；push 仍不注射（保留 auto = 配置节点优先）', () => {
    const cfg = dualCourseCfg()
    const pull = trainingLoopSpec(cfg, { course: 'course-a', ppo: 'pull', venv: VENV })
    expect(flag(pull, '--ppo')).toBe('remote')
    expect(flag(pull, '--remote-transport')).toBe('pull')
    expect(flag(pull, '--remote-hub-url')).toBeNull()
    // push preset 保持原状：auto 让「config/env 里的 push 节点」生效（本 preset 的
    // 前提就是云机在跑 worker_server）。⚠ 残留不对称：push preset 在无节点时会静默
    // 回落 pull（不响亮报错）——未在本轮改动，留档待议。
    const push = trainingLoopSpec(cfg, { course: 'course-a', ppo: 'push', venv: VENV })
    expect(flag(push, '--remote-transport')).toBeNull()
  })
})

// ────────────────────────── ③ 接线 grep 门禁（防「只改 helper 忘接线」） ──────────────────────────

describe('接线门禁', () => {
  it('local 预设顺序含 hubServer → localWorker → trainingLoop', () => {
    const p = src(path.join('server', 'actions', 'preset.ts'))
    const branch = p.slice(p.indexOf("mode === 'push'"), p.indexOf('const ctx: StartCtx'))
    expect(branch).toContain("['hubServer', 'localWorker', 'trainingLoop']")
  })

  it('离开 local 的预设会停掉残留 localWorker（否则切 pull/push 后卡片仍亮绿点）', () => {
    const p = src(path.join('server', 'actions', 'preset.ts'))
    expect(p).toContain("if (mode !== 'local')")
    expect(p).toContain("await stopComponent('localWorker', course)")
  })

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

  it('localWorker 有启动分支 + 日志/标签/组件表三项登记（缺一项则 UI 无声失败）', () => {
    expect(src(path.join('server', 'actions', 'start.ts'))).toContain("case 'localWorker'")
    expect(src(path.join('server', 'actions', 'restart.ts'))).toContain('localWorkerSpec')
    expect(src(path.join('server', 'actions', 'smoke.ts'))).toContain("case 'localWorker'")
    expect(src(path.join('server', 'api', 'component-meta.ts'))).toContain("'localWorker'")
    expect(src(path.join('server', 'api', 'logs.ts'))).toContain('local-worker')
    expect(src(path.join('server', 'actions', 'labels.ts'))).toContain('localWorker')
  })
})
