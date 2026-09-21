/**
 * training-shared-trainer.test.ts — 共享 trainer（`run_rl_cluster.py --serve`，2026-09-19 / R3-5）。
 *
 *  用户口径：「hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程，就能同时支持
 *  所有并行训练课程」。本文件钉的是**训练侧之外的每一半**（python 那一半由
 *  nn-training/tests/test_serve_course_overrides.py · test_serve_wiring.py 承担）：
 *
 *   ① **形状**：共享槽（`scopeOf` 归一为 `''`）+ `restartSpecFor` 重建出来的 argv 必须是
 *      「发现模式」（**不给 `--courses`**、给 `--traj-root`/`--cluster-lock`）——把课程写进
 *      argv 就等于给进程绑死了课程表，「先起 trainer、后加课」当场失效；
 *   ② **旧形状换代**：每课条目（`trainingLoops[<课>]`）**拒重建**（用共享 spec 重建一个每课
 *      条目 = 两套调度器抢同一批 traj），只能被启动时的显式换代接管收掉；
 *   ③ **课程准备已迁出启动路径**（2026-09-20 用户口径：「服务进程启动不应与课程绑定。
 *      进程启动时不要自动开启课程训练」）：机器侧旋钮与「账本文件 = 课程发现判据」现在住在
 *      `actions/course-lifecycle.ts`（开课），启动路径**一个字都不写课程**
 *      （否则「起个进程」又要先选一门课，还会撞上「hub 还不认识这门课」）；
 *   ④ **停止语义**：停 trainer = 停**所有**课程的训练（消息里必须说出来，不然操作员会以为
 *      只是停了当前查看的那门课），并清共享槽。
 *
 *  纪律：不 spawn 任何进程。账本（registry）/ rl-config / 课程目录全部重定向到临时目录；
 *  机器侧旋钮断言只读**临时** rl-config（绝不碰线上那份）。
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from 'bun:test'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { saveAnyComponent } from '../src/core/registry'
import { pruneLegacyCourseKnobs } from '../src/stack/course-knobs'
import { DASHBOARD_ROOT, REPO_ROOT } from '../src/core/paths'
import type { RlConfig } from '../src/core/types'
import * as actions from '../src/server/actions'

/** 真实存在的非 BC 课程（启动路径会 validateCourseArg；夹具不重定向课程目录）。 */
const COURSE = 'c5-gae'

let scratch = ''
let tmpRegistry = ''
let tmpConfig = ''
const prevRegistry = process.env.BCITY_REGISTRY_FILE
const prevConfig = process.env.BCITY_RL_CONFIG

beforeAll(() => {
  scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-shared-trainer-'))
  tmpRegistry = path.join(scratch, 'registry.json')
  tmpConfig = path.join(scratch, 'rl-config.json')
  // 夹具自造配置（不读线上 rl-config）：组件 spec 的构造要经 `loadConfig`
  writeFileSync(
    tmpConfig,
    JSON.stringify(
      {
        version: 1,
        rl: { agent_port: 8990, remote_token: 'tok' },
        nodes: [],
        courses: {},
      },
      null,
      2,
    ),
  )
  process.env.BCITY_REGISTRY_FILE = tmpRegistry
  process.env.BCITY_RL_CONFIG = tmpConfig
})

afterAll(() => {
  if (prevRegistry === undefined) delete process.env.BCITY_REGISTRY_FILE
  else process.env.BCITY_REGISTRY_FILE = prevRegistry
  if (prevConfig === undefined) delete process.env.BCITY_RL_CONFIG
  else process.env.BCITY_RL_CONFIG = prevConfig
  rmSync(scratch, { recursive: true, force: true })
})

afterEach(() => {
  writeFileSync(tmpRegistry, '{}')
  actions.busy.clear()
  actions.busySince.clear()
})

/** 读源文件（形状断言用；与 local-worker.test.ts 同一手法）。 */
function src(rel: string): string {
  return readFileSync(path.join(DASHBOARD_ROOT, rel), 'utf-8')
}

/** 剥掉注释后的源码：防回流断言查**代码**。
 *  「已迁走的东西」在注释里被反复点名（那正是它们迁移的记录），扫原文会把
 *  「写明它已搬去 course-lifecycle」当成「它还在 start.ts」。 */
function codeOnly(rel: string): string {
  return src(rel)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/[^\n]*/g, '')
}

describe('① 形状：共享 trainer = 一个进程 + 发现模式', () => {
  it('restartSpecFor(trainingLoop, 共享槽) 重建出的 argv 不绑课程表', () => {
    saveAnyComponent('trainingLoop', '', {
      pid: 999999999, // 死 pid：只用于让条目存在，restartSpecFor 不探活
      entry: 'nn-training/run_rl_cluster.py',
      course: '',
    })
    const spec = actions.restartSpecFor('trainingLoop', COURSE)
    expect(spec).not.toBeNull()
    expect(spec!.key).toBe('trainingLoop')
    expect(spec!.course).toBe('')
    const cmd = spec!.cmd.join(' ')
    expect(cmd).toContain('run_rl_cluster.py')
    expect(cmd).toContain('--serve')
    // 发现模式：不给课程表（课程 = `<traj-root>/<课>/training_log.jsonl` 这个文件系统事实）
    expect(cmd).not.toContain('--courses')
    expect(cmd).not.toContain('--course ')
    // traj 根必须绝对（相对路径会指到控制台 cwd）
    expect(cmd).toContain(`--traj-root ${path.join(REPO_ROOT, 'tmp')}`)
    // 进程级单实例锁：一个进程服务所有课程，双开 = 两套调度器抢同一批 traj
    expect(cmd).toContain('--cluster-lock')
    // 控制文件（暂停意图）：绝对路径，不靠 cwd
    expect(cmd).toContain('--control-file')
    expect(spec!.log).toContain('trainer-cluster.log')
  })

  it('旧形状（每课一条 trainingLoop 记录）→ 拒重建（守「同一批 traj 只有一个跑者」）', () => {
    saveAnyComponent('trainingLoop', COURSE, {
      pid: 999999999,
      entry: 'nn-training/run_rl.py',
      course: COURSE,
    })
    expect(actions.restartSpecFor('trainingLoop', COURSE)).toBeNull()
  })

  it('启动路径不再存在任何「每课一个 trainer 进程」的分支（源码级围栏）', () => {
    const start = src(path.join('src', 'server', 'actions', 'start.ts'))
    expect(start).toContain('trainerServeSpec')
    // 旧形状的每课 spec / BC 专属启动器都已下线：留着就是一条「看起来还在用」的每课路径
    expect(start).not.toContain('trainingLoopSpec')
    expect(start).not.toContain('bcLoopSpec')
    expect(start).not.toContain('startBcLoop')
    // 换代接管 + 进程级锁是启动的两道前置门
    expect(start).toContain('supersedeLegacyInstances')
    expect(start).toContain('runClusterLockHolder')
  })
})

describe('② 开课（course-lifecycle）：账本（发现判据）与机器侧旋钮', () => {
  /** 夹具配置（每例自造，因为准备过程会回写 rl-config）。 */
  function fixture(): RlConfig {
    return {
      version: 1,
      rl: { agent_port: 8990, remote_token: 'tok' },
      nodes: [],
      courses: {},
    } as unknown as RlConfig
  }

  /** 读回**临时** rl-config 里本课的机器侧旋钮（绝不碰线上那份）。 */
  function knobsOnDisk(course: string): Record<string, unknown> {
    const cfg = JSON.parse(readFileSync(tmpConfig, 'utf-8')) as RlConfig
    return (cfg.courses?.[course] ?? {}) as Record<string, unknown>
  }

  /** 把课程 traj 根重定向到临时目录（并预置权重文件 ⇒ 跳过播种路径）。 */
  function withTmpLogs<T>(fn: (dir: string) => T): T {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-traj-'))
    const prev = process.env.BCITY_TMP_LOGS_DIR
    process.env.BCITY_TMP_LOGS_DIR = dir
    try {
      mkdirSync(path.join(dir, COURSE), { recursive: true })
      writeFileSync(path.join(dir, COURSE, 'weights.json'), '{}')
      return fn(dir)
    } finally {
      if (prev === undefined) delete process.env.BCITY_TMP_LOGS_DIR
      else process.env.BCITY_TMP_LOGS_DIR = prev
      rmSync(dir, { recursive: true, force: true })
    }
  }

  it('开课**不写任何执行面裁决**（课程与 worker 节点正交，§3）', () => {
    withTmpLogs(() => {
      writeFileSync(tmpConfig, JSON.stringify(fixture(), null, 2))
      actions.writeCourseConfigForOpen(COURSE, { trainMode: 'online' })
      // 课程不带「走哪条路 / 打哪个 hub / 钉哪台机器 / 怎么降级算力」——
      // 执行面全交由 `rl.hub_push` + 登记节点 + hub 队列（§3：「无 fallback」）。
      for (const key of [
        'remote_transport',
        'remote_hub_url',
        'push_node_url',
        'hub_push',
        'remote_degrade_after',
      ]) {
        expect(knobsOnDisk(COURSE)[key]).toBeUndefined()
      }
      writeFileSync(tmpConfig, JSON.stringify(fixture(), null, 2))
    })
  })

  it('开课时只**清**旧的传输耦合键（prune），绝不重新写回去', () => {
    withTmpLogs(() => {
      const cfg = fixture()
      // 类型表里这两个键已删 ⇒ 用旧形状（Record）造历史配置，模拟线上 rl-config.json 的残留值。
      cfg.courses = {
        [COURSE]: { remote_transport: 'pull', remote_hub_url: 'https://old.example' },
      } as unknown as RlConfig['courses']
      writeFileSync(tmpConfig, JSON.stringify(cfg, null, 2))
      const r = pruneLegacyCourseKnobs(cfg)
      const onDisk = knobsOnDisk(COURSE)
      expect(onDisk.remote_transport).toBeUndefined()
      expect(onDisk.remote_hub_url).toBeUndefined()
      expect(r.removed.length).toBeGreaterThan(0)
      writeFileSync(tmpConfig, JSON.stringify(fixture(), null, 2))
    })
  })

  it('开课为本课建账本 **与 `remote-jobs/`**（两个发现判据：trainer 认账本，hub 认目录）', () => {
    withTmpLogs((dir) => {
      const r = actions.prepareCourseForOpen(COURSE)
      expect(existsSync(path.join(dir, COURSE, 'training_log.jsonl'))).toBe(true)
      // hub 的 `_course_dir_live` 只认 `{remote-jobs,offline}` 目录 + 新鲜 mtime：
      // 不先建它，开课时置 hub 模式必然拿到「需要合法 course（[]）」（2026-09-20 实测）
      expect(existsSync(path.join(dir, COURSE, 'remote-jobs'))).toBe(true)
      expect(r.notes.join('\n')).toContain('已建课程账本')
      // 账本已存在时不重复建（幂等）：再跑一次不出那条说明
      const again = actions.prepareCourseForOpen(COURSE)
      expect(again.notes.join('\n')).not.toContain('已建课程账本')
      writeFileSync(tmpConfig, JSON.stringify(fixture(), null, 2))
    })
  })

  it('★ 启动进程不再为任何课程建账本（源码级围栏：准备只住 course-lifecycle）', () => {
    const start = codeOnly(path.join('src', 'server', 'actions', 'start.ts'))
    // 启动路径不得再碰课程事实/旋钮（回流 = 「起进程先选一门课」当场复活）
    expect(start).not.toContain('training_log.jsonl')
    expect(start).not.toContain('prepareCourseForSharedTrainer')
    expect(start).not.toContain('seedWeightsFromBc')
    expect(start).not.toContain('writeCourseMachineKnobs')
    // 课程发现判据必须与训练侧同一处措辞（greppable 的交叉引用）——住在开课模块里
    const life = src(path.join('src', 'server', 'actions', 'course-lifecycle.ts'))
    expect(life).toContain('training_log.jsonl')
    expect(life).toContain('remote-jobs')
    expect(life).toContain('discover_courses')
  })

  it('stop 路径扫旧形状的每课 trainer + 清进程级锁（「停 trainer」= 停所有训练）', () => {
    const stop = src(path.join('src', 'server', 'actions', 'stop.ts'))
    expect(stop).toContain('supersedeLegacyInstances')
    expect(stop).toContain('releaseClusterLock')
    expect(stop).toContain('所有课程的训练随之停止')
  })
})

describe('③ 停止：共享槽被清 + 语义说清楚', () => {
  it('停 trainer 报「所有课程的训练随之停止」，并清掉共享槽', async () => {
    saveAnyComponent('trainingLoop', '', {
      pid: 999999999, // 死 pid ⇒ 走「已退出」分支，不 kill 任何真进程
      entry: 'nn-training/run_rl_cluster.py',
      course: '',
    })
    const r = await actions.stopComponent('trainingLoop', COURSE)
    expect(r.ok).toBe(true)
    expect(r.message).toContain('所有课程的训练随之停止')
    // 槽已清（再停一次 = 「未在运行」）
    const again = await actions.stopComponent('trainingLoop', COURSE)
    expect(again.message).toContain('未在运行')
  })
})

describe('④ 冒烟预演：共享 trainer 在跑时响亮拒绝（预演要独占训练栈）', () => {
  it('共享 trainer 存活 ⇒ 预演不启动、消息说清原因', async () => {
    saveAnyComponent('trainingLoop', '', {
      pid: process.pid, // 本进程 = 必活
      entry: 'nn-training/run_rl_cluster.py',
      course: '',
    })
    const r = await actions.smokeTrain(COURSE)
    expect(r.ok).toBe(false)
    expect(r.message).toContain('共享 trainer 在跑')
    expect(r.message).toContain('独占')
    // 预演占的就是**那一个** trainer 角色：它的账本槽必须与共享槽同源（scopeOf），
    // 否则卡片会显示「没人在跑」而预演进程正在烧 GPU（源码级断言，免 spawn）。
    const ts = src(path.join('src', 'server', 'actions', 'train-smoke.ts'))
    expect(ts).toContain("scopeOf('trainingLoop', course)")
  })
})
