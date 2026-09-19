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
 *   ③ **课程准备**：机器侧旋钮（`courses.<课>.remote_transport`）与「账本文件 = 课程发现判据」
 *      这两件事发生在启动路径里，且 mode → transport 的映射逐条钉住；
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
import { sharedHubUrl } from '../src/core/slots'
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

describe('② 课程准备：账本（发现判据）与机器侧旋钮', () => {
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

  it('mode → 传输裁决：local = pull + **本机 hub**；push/pull 各自定死（不让残留节点改道）', () => {
    withTmpLogs(() => {
      const cfg = fixture()
      const hub = sharedHubUrl(cfg)
      // local：本机独立 worker 的 pull 模式（与旧 per-course spec 逐字段同义）
      const a = actions.prepareCourseForSharedTrainer(cfg, COURSE, {
        course: COURSE,
        trainerPpo: 'local',
        remoteDegrade: true,
      })
      expect(knobsOnDisk(COURSE)).toMatchObject({
        remote_transport: 'pull',
        remote_hub_url: hub,
        remote_degrade_after: 3,
      })
      expect(a.notes.join('\n')).toContain('本课（c5-gae）机器侧传输 = pull')
      // pull：定死 pull，但**不**写本机 hub（hub 地址来自 rl.remote_hub_url）
      writeFileSync(tmpConfig, JSON.stringify(fixture(), null, 2))
      actions.prepareCourseForSharedTrainer(cfg, COURSE, { course: COURSE, trainerPpo: 'pull' })
      expect(knobsOnDisk(COURSE).remote_transport).toBe('pull')
      expect(knobsOnDisk(COURSE).remote_hub_url).toBeUndefined()
      // push：定死 push（auto 会按残留 push_node_url 推给别的节点，而那正是事故源）
      writeFileSync(tmpConfig, JSON.stringify(fixture(), null, 2))
      actions.prepareCourseForSharedTrainer(cfg, COURSE, { course: COURSE, trainerPpo: 'push' })
      expect(knobsOnDisk(COURSE).remote_transport).toBe('push')
      writeFileSync(tmpConfig, JSON.stringify(fixture(), null, 2))
    })
  })

  it('为本课建账本（发现判据）——不建它，这门新课永远不会被共享 trainer 看见', () => {
    withTmpLogs((dir) => {
      const r = actions.prepareCourseForSharedTrainer(fixture(), COURSE, {
        course: COURSE,
        trainerPpo: 'pull',
      })
      expect(existsSync(path.join(dir, COURSE, 'training_log.jsonl'))).toBe(true)
      expect(r.notes.join('\n')).toContain('已建课程账本')
      // 账本已存在时不重复建（幂等）：再跑一次不出那条说明
      const again = actions.prepareCourseForSharedTrainer(fixture(), COURSE, {
        course: COURSE,
        trainerPpo: 'pull',
      })
      expect(again.notes.join('\n')).not.toContain('已建课程账本')
      writeFileSync(tmpConfig, JSON.stringify(fixture(), null, 2))
    })
  })

  it('启动时为本课建账本：没有 `training_log.jsonl` 的课永远不会被发现', () => {
    const start = src(path.join('src', 'server', 'actions', 'start.ts'))
    expect(start).toContain('training_log.jsonl')
    expect(start).toContain('prepareCourseForSharedTrainer')
    // 课程发现判据必须与训练侧同一处措辞（greppable 的交叉引用）
    expect(start).toContain('discover_courses')
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
