/** training-multi-course.test.ts — 多课程并行训练的互斥证伪测试。

 *  plan：`plan/multi-course-parallel-training.md`（P0-W1/W3/W4/W5）。
 *
 *  打开节奏（红测试先落盘、逐阶段去 skip）：
 *    - W1 双课程 spec 隔离（端口 / 日志路径 / 锁名）→ **P1a 已绿**；
 *    - W3 加法校验 checkCapacity                    → **P1a 已绿**；
 *    - W5① hub_port 单一归宿                        → **P1a 已绿**；
 *    - W4 spec 重建快照（逐字段一致）               → **P1b 已绿**；
 *    - W5② 账本枚举单一归宿                         → **P1b 已绿**。
 *
 *  纪律：断言里的容量/端口一律从**测试自造的配置夹具**推导，**不写死数字**
 *  （F-B2：配置会漂，写死即谎言），也**不读**线上 `nn-training/rl-config.json`
 *  （本机工作配置不是测试基准，2026-09-15）。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'fs'
import os from 'os'
import path from 'path'
import { DASHBOARD_ROOT, REPO_ROOT } from '../src/core/paths'
import {
  HUB_SERVER_ENTRY,
  cfTunnelArgs,
  cloudflaredSpec,
  hubServerSpec,
  resolveCfTunnel,
  trainingLoopSpec,
} from '../src/stack/specs'
import {
  entryForCourse,
  loadRegistry,
  registryComponents,
  registryTriples,
  saveCourseComponent,
} from '../src/core/registry'
import { loadConfig, saveConfig, writeRemoteHubUrl } from '../src/core/config'
import { drainStaleJobs, supersedeLegacyInstances } from '../src/stack/hub'
import { killPid, pidAlive } from '../src/core/net'
import { seedWeightsFromBc } from '../src/stack/courses'
import { restartSpecFor } from '../src/server/actions'
import {
  allSlotPorts,
  capacityError,
  checkCapacity,
  lockName,
  sharedHubPort,
  sharedTunnelMetricsPort,
  slotError,
  slotOf,
  slotPort,
} from '../src/core/slots'
import type { Registry, RlConfig } from '../src/core/types'

/** 测试用临时目录（账本/控制台状态重定向——绝不写线上 tmp/training-start）。 */
const SCRATCH_DIRS: string[] = []
afterAll(() => {
  for (const d of SCRATCH_DIRS) {
    try {
      rmSync(d, { recursive: true, force: true })
    } catch {
      /* noop */
    }
  }
})

// ────────────────────────── 测试自造 rl-config（**不读**线上配置） ──────────────────────────

/**
 * 2026-09-15：这里原本读本机 `nn-training/rl-config.json`（真实隧道 URL / 节点表 /
 * 配额，随人手改动而漂）——测试等于挂在一台机器的工作配置上（F-B2 只想避免「写死
 * 会漂的数字」，但把夹具换成生产文件是更糟的解）。改为测试自持的常量夹具：
 * 值由本文件钉死（workers=8 ⇒ 裸机容量 8），断言仍从夹具推导，不写死数字。
 */
const FIXTURE_CFG = {
  version: 1,
  nodes: [
    {
      id: 'self',
      url: 'http://127.0.0.1:8443',
      authKey: 'fixture-self-key',
      concurrency: 4,
      enabled: true,
    },
    {
      id: 'mac',
      url: 'http://127.0.0.1:8444',
      authKey: 'fixture-mac-key',
      concurrency: 2,
      enabled: true,
    },
  ],
  rl: {
    hub_port: 18787,
    agent_port: 18443,
    local_slots: 0,
    workers: 8,
    torch_threads: 8,
    stream: 0,
    remote_token: 'fixture-token',
  },
}

/** 一份全新夹具（深拷贝，调用方随意改，不串味）。 */
function cfgFixture(): RlConfig {
  return JSON.parse(JSON.stringify(FIXTURE_CFG)) as RlConfig
}

import { SLOT_COUNT } from '../src/core/slots'

/** 裸机容量 = max(rl.workers, rl.local_slots)（plan §1.1「裸机容量」）。 */
function bareCapacity(cfg: RlConfig): number {
  const rl = cfg.rl as Record<string, unknown>
  return Math.max(Number(rl.workers ?? 0), Number(rl.local_slots ?? 0))
}

/** 双课程测试配置：两课各占一个槽位（§1.3 配置 schema）。 */
function dualCourseCfg(): RlConfig {
  const cfg = cfgFixture()
  return {
    ...cfg,
    courses: {
      'course-a': { slot: 0, workers: 1, local_slots: 1 },
      'course-b': { slot: 1, workers: 1, local_slots: 1 },
    },
  }
}

function cmdPort(spec: { cmd: string[] }): number | null {
  for (let i = 0; i < spec.cmd.length; i++) {
    if (spec.cmd[i] === '--port') return Number(spec.cmd[i + 1])
  }
  return null
}

// ────────────────────────── W1：双课程 spec 隔离 ──────────────────────────

describe('W1 双课程 spec 隔离', () => {
  it('共享 hub：spec 与课程无关（一个进程服务所有课程，端口/日志只有一份）', () => {
    // 2026-09-18 用户指令：hubserver 只开一个进程就同时支持所有并行课程。它是**代替**
    // 「每课一 hub」的（角色相同：同一棵 job 目录树），所以这里断言的不再是「隔离」，
    // 而是「唯一」+「课程表不靠控制台给」。
    const cfg = dualCourseCfg()
    const spec = hubServerSpec(cfg)
    expect(cmdPort(spec)).toBe(sharedHubPort(cfg))
    expect(spec.course).toBe('')
    const argv = spec.cmd.map(String)
    // 课程表来源 = 盘（hub 自己扫）：不给 --course，也不给某一门课的 job-root
    expect(argv).toContain('--discover')
    expect(argv[argv.indexOf('--traj-root') + 1]).toBe(path.join(REPO_ROOT, 'tmp'))
    expect(argv).not.toContain('--job-root')
    expect(argv).not.toContain('--jsonl')
    // 同一个 cfg ⇒ 逐字段同一份 spec（没有第二个参数能把它变成「另一门课的 hub」）。
    // healthy/ownsResource 是闭包，逐可比字段断言（同 W4）。
    const again = hubServerSpec(cfg)
    for (const k of ['key', 'name', 'course', 'cmd', 'cwd', 'env', 'log', 'sentinels'] as const) {
      expect(again[k]).toEqual(spec[k])
    }
  })

  it('本机伪节点（冒烟预演专用）仍按课程隔离端口与 work 目录', () => {
    // 它 2026-09-19 退出了受管组件（没有 ProcSpec/账本键/卡片，只服务 trainingLoop 冒烟
    // 预演）——但**双课同冒**这条约束还在：两门课的预演同时跑时，伪节点不得互踩端口/payload。
    const src = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'stack', 'push.ts'), 'utf-8')
    expect(src).toContain("slotPort(ctx.cfg, ctx.course, 'push')")
    expect(src).toContain('tmp/remote-worker-serve-${ctx.course}')
    expect(src).not.toContain('workerServeSpec')
    // 端口本身也确实按课程分开（槽位算术，唯一来源 core/slots）
    const cfg = dualCourseCfg()
    expect(slotPort(cfg, 'course-a', 'push')).not.toBe(slotPort(cfg, 'course-b', 'push'))
  })

  it('两门课程的锁文件名互不相同（无课程沿用旧文件名，默认行为零变化）', () => {
    expect(lockName('course-a', 'run_rl')).not.toBe(lockName('course-b', 'run_rl'))
    expect(lockName('course-a', 'train_loop')).not.toBe(lockName('course-b', 'train_loop'))
    expect(lockName('', 'run_rl')).toBe('.run_rl.lock')
    expect(lockName('', 'train_loop')).toBe('.train_loop.lock')
  })
})

// ────────────────────────── W3：加法校验（纯函数） ──────────────────────────

describe('W3 checkCapacity 加法校验', () => {
  it('两课各占满裸机容量 → 超量拒绝，并点名超量课程', () => {
    const cap = bareCapacity(cfgFixture())
    expect(cap).toBeGreaterThan(1)
    const r = checkCapacity(
      { a: { workers: cap, local_slots: 0 }, b: { workers: 0, local_slots: cap } },
      cap,
    )
    expect(r.ok).toBe(false)
    expect(r.used).toBe(2 * cap)
    expect(r.capacity).toBe(cap)
    expect(r.over.length).toBeGreaterThan(0)
  })

  it('Σ max(workers, local_slots) 恰等于容量 → 放行（eff 取 max 再求和）', () => {
    const cap = bareCapacity(cfgFixture())
    const r = checkCapacity({ a: { workers: cap, local_slots: 0 } }, cap)
    expect(r.ok).toBe(true)
    expect(r.used).toBe(cap)
    // 0 是合法值（语义 = 关闭该课本机直跑），不参与 eff
    const z = checkCapacity({ a: { workers: 0, local_slots: 0 } }, cap)
    expect(z.ok).toBe(true)
    expect(z.used).toBe(0)
  })
})

// ────────────────────────── W4：spec 重建快照（§1.4 契约，P1b） ──────────────────────────

describe('W4 spec 重建逐字段一致（旧占位）', () => {
  it('共享 hub spec 重建逐字段一致（且**按视图课程**调用也解析到共享槽）', () => {
    // 账本重定向到临时文件（同 exit-watchdog.test.ts），不碰线上账本；
    // 用内联 set/restore（不用模块顶层赋值），与并行跑的其它测试文件互不干扰。
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-p0w4-'))
    const prev = process.env.BCITY_REGISTRY_FILE
    const prevCfg = process.env.BCITY_RL_CONFIG
    process.env.BCITY_REGISTRY_FILE = path.join(scratch, 'registry.json')
    // restartSpecFor 从**磁盘** loadConfig 重建 spec —— 配置也必须重定向到临时文件，
    // 否则重建侧读的是线上 rl-config（端口/token 与夹具不同 ⇒ 逐字段比对必然红）。
    process.env.BCITY_RL_CONFIG = path.join(scratch, 'rl-config.json')
    try {
      const cfg = cfgFixture()
      writeFileSync(process.env.BCITY_RL_CONFIG, JSON.stringify(cfg, null, 2))
      const course = 'course-a' // = 「操作员正在看哪门课」，不是 hub 的归属
      const spec = hubServerSpec(cfg)
      // 登记形状与 hub.ts::stepHubServer 的写入端同构（固定 `''` 槽）。
      saveCourseComponent('hubServer', '', {
        pid: 424242,
        course: '',
        entry: HUB_SERVER_ENTRY,
        log: spec.log,
        url: `http://127.0.0.1:${cmdPort(spec) ?? 0}`,
      })
      // 登记 round-trip：共享槽原样回来（监督/重启的数据源）。
      expect(entryForCourse(loadRegistry(), 'hubServer', '')).toMatchObject({ course: '' })
      expect(entryForCourse(loadRegistry(), 'hubServer', course)).toBeUndefined()
      // 重建 spec **按视图课程**调用也解析到共享槽（scopeOf 归一）且逐字段一致。
      const fresh = restartSpecFor('hubServer', course)
      expect(fresh).not.toBeNull()
      for (const k of ['key', 'name', 'course', 'cmd', 'env', 'log', 'sentinels'] as const) {
        expect(fresh![k]).toEqual(spec[k])
      }
    } finally {
      if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
      else process.env.BCITY_REGISTRY_FILE = prev
      if (prevCfg === undefined) delete process.env.BCITY_RL_CONFIG
      else process.env.BCITY_RL_CONFIG = prevCfg
      rmSync(scratch, { recursive: true, force: true })
    }
  })
})

// ────────────────────────── W6：同槽位 cloudflared 隧道接管（2026-09-14 事故） ──────────────────────────

describe('W6 旧形状（每课一 hub/隧道）换代接管', () => {
  /** 真实存活子进程（pidAlive 为真），充当「旧隧道进程」。 */
  function livePid(): { proc: Bun.Subprocess; pid: number } {
    const proc = Bun.spawn([process.execPath, '-e', 'setInterval(() => {}, 1000)'], {
      stdout: 'ignore',
      stderr: 'ignore',
      windowsHide: true,
    })
    return { proc, pid: proc.pid }
  }

  it('接管并杀所有旧形状隧道条目（活则杀、死则清账）', async () => {
    // 复现（2026-09-14，每课一隧道时代）：c6-chip 残留隧道长期占住 slot0 metrics 口，
    // bc-c4-v3 隧道 bind 失败 12s 退出、控制台「启动失败」。共享单隧道后判据变宽：
    // 服务同一件事的所有旧实例都是冲突方（不再有「异槽位就不动」这回事）。
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-p0w6-'))
    const prev = process.env.BCITY_REGISTRY_FILE
    process.env.BCITY_REGISTRY_FILE = path.join(scratch, 'registry.json')
    const stale = livePid() // c6-chip 残留：同槽位 0 —— 必须被接管杀掉
    const other = livePid() // bc-c4：异槽位 1 —— 不得误伤
    try {
      const cfg = cfgFixture()
      const m0 = slotPort(cfg, 0, 'metrics')
      const m1 = slotPort(cfg, 1, 'metrics')
      saveCourseComponent('cloudflared', 'c6-chip', {
        pid: stale.pid,
        course: 'c6-chip',
        slot: 0,
        metrics: m0,
        log: '',
      })
      saveCourseComponent('cloudflared', 'bc-c4', {
        pid: other.pid,
        course: 'bc-c4',
        slot: 1,
        metrics: m1,
        log: '',
      })
      // 待启动课程自己的旧死条目（真实 world：bc-c4-v3 前次失败 PID 8628）不应被本轮处理
      saveCourseComponent('cloudflared', 'bc-c4-v3', {
        pid: 8628,
        course: 'bc-c4-v3',
        slot: 0,
        metrics: m0,
        log: '',
      })

      // 换代接管：**所有**非共享槽的旧形状条目都是冲突方（共享单隧道服务所有课）
      const killed = await supersedeLegacyInstances('cloudflared')
      expect(killed.sort()).toEqual(['bc-c4', 'bc-c4-v3', 'c6-chip']) // 含已死条目（清账也是接管）

      const reg = loadRegistry()
      expect(reg.cloudflareds?.['c6-chip']).toBeUndefined() // 已清账
      expect(reg.cloudflareds?.['bc-c4']).toBeUndefined() // 已清账（不再有「异槽位」概念）
      expect(reg.cloudflareds?.['bc-c4-v3']).toBeUndefined()
      expect(pidAlive(stale.pid)).toBe(false) // 旧隧道已死
      expect(pidAlive(other.pid)).toBe(false) // 旧隧道已死（同一件事只留一个实例）
    } finally {
      try {
        await killPid(stale.pid)
      } catch {
        /* already dead */
      }
      try {
        await killPid(other.pid)
      } catch {
        /* already dead */
      }
      if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
      else process.env.BCITY_REGISTRY_FILE = prev
      rmSync(scratch, { recursive: true, force: true })
    }
  })

  it('**已死 pid** 的旧形状条目 → 清账（无进程可杀，但账必须清）', async () => {
    // 2026-09-17 事故：原实现对死 pid 直接 `continue` —— 作者意图是"死进程没什么可杀"，
    // 但**清账被一起跳过**了 ⇒ 09-14 的 cloudflared[x2-acbc] 条目活到今天，还在账本层
    // 占着角色；共享实例接管后，它就成了"PID 已死、服务仍在应答"的幽灵，看门狗每 8s
    // 刷屏（restart.ts 已对非空槽 fail-closed，但账仍必须清）。
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-p0w6b-'))
    const prev = process.env.BCITY_REGISTRY_FILE
    process.env.BCITY_REGISTRY_FILE = path.join(scratch, 'registry.json')
    const ghost = livePid()
    try {
      const cfg = cfgFixture()
      const m0 = slotPort(cfg, 0, 'metrics')
      await killPid(ghost.pid) // 造一个**确证已死**的 pid（不用硬编码历史值：万一被复用会误杀）
      expect(pidAlive(ghost.pid)).toBe(false)
      saveCourseComponent('cloudflared', 'x2-acbc', {
        pid: ghost.pid,
        course: 'x2-acbc',
        slot: 0,
        metrics: m0,
        log: '',
      })
      const struck = await supersedeLegacyInstances('cloudflared')
      expect(struck).toEqual(['x2-acbc'])
      expect(loadRegistry().cloudflareds?.['x2-acbc']).toBeUndefined() // 陈旧条目已清
    } finally {
      try {
        await killPid(ghost.pid)
      } catch {
        /* already dead */
      }
      if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
      else process.env.BCITY_REGISTRY_FILE = prev
      rmSync(scratch, { recursive: true, force: true })
    }
  })

  it('接线：hub/隧道启动都在 spawn 前换代接管（防只管 helper 忘接线）', () => {
    // 功能由上面的单测覆盖，但调用点被删会让 helper 形同虚设——grep 门禁守住接线。
    const src = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'stack', 'hub.ts'), 'utf-8')
    const hubStep = src.slice(src.indexOf('export async function stepHubServer'))
    const hubSpawn = hubStep.indexOf('launchSpec')
    const hubCall = hubStep.indexOf("supersedeLegacyInstances('hubServer')")
    expect(hubCall).toBeGreaterThan(-1)
    expect(hubCall).toBeLessThan(hubSpawn) // 旧 hub 与共享 hub 服务同一棵树，必须先收
    const cfStep = src.slice(src.indexOf('export async function stepCloudflared'))
    const cfSpawn = cfStep.indexOf('spawnBg')
    const cfCall = cfStep.indexOf("supersedeLegacyInstances('cloudflared')")
    expect(cfCall).toBeGreaterThan(-1)
    expect(cfCall).toBeLessThan(cfSpawn) // 必须先清口再起新隧道
  })
})

// ────────────────────────── W5：grep 门禁（纯文本扫描） ──────────────────────────

/** dashboard/src/** 下全部 .ts/.tsx（排除 tests 与生成物）。 */
function trainingSources(): Array<{ rel: string; text: string }> {
  const root = path.join(DASHBOARD_ROOT, 'src')
  const out: Array<{ rel: string; text: string }> = []
  const walk = (dir: string): void => {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      const abs = path.join(dir, ent.name)
      if (ent.isDirectory()) {
        if (ent.name === 'node_modules' || ent.name === 'data') continue
        walk(abs)
        continue
      }
      if (!/\.tsx?$/.test(ent.name)) continue
      out.push({
        rel: path.relative(root, abs).split(path.sep).join('/'),
        text: readFileSync(abs, 'utf-8'),
      })
    }
  }
  walk(root)
  return out
}

describe('W5 grep 门禁', () => {
  const srcs = trainingSources()

  it('门禁① hub_port 唯一归宿 = slots.ts（+ types.ts 的字段声明）', () => {
    // 单课时代 specs/push/proc/hub/actions/api/smoke 七处各自算术端口（+1 / +2），
    // 多课时代每一处都是串线风险。现在一律经 slots.ts 的槽位算术，base 只在那一处读。
    const allowed = new Set(['slots.ts', 'types.ts'])
    // 扫属性访问（`.hub_port`）——文档/消息里提到键名不违规，真的读才违规。
    const offenders = srcs
      .filter((s) => /\.hub_port\b/.test(s.text) && !allowed.has(path.basename(s.rel)))
      .map((s) => s.rel)
    expect(offenders).toEqual([])

    // 派生端口不许手写偏移（必须走 portForSlot/slotPort 的 kind）；slots.ts 自己
    // 就是算术模块（含文档字符串），豁免。
    const handArith = srcs
      .filter((s) => /hub_port\s*\+\s*\d/.test(s.text) && !allowed.has(path.basename(s.rel)))
      .map((s) => s.rel)
    expect(handArith).toEqual([])
  })

  it('门禁② 账本枚举唯一归宿 = registry.ts（枚举走 registryComponents 三元组）（P1b 打开）', () => {
    const offenders = srcs
      .filter(
        (s) => /Object\.keys\(\s*reg\b/.test(s.text) && path.basename(s.rel) !== 'registry.ts',
      )
      .map((s) => s.rel)
    expect(offenders).toEqual([])
  })

  it('门禁自身有效：扫描确实覆盖到源码（防 glob 静默为空）', () => {
    expect(srcs.length).toBeGreaterThan(10)
    expect(srcs.some((s) => s.rel === 'stack/specs.ts')).toBe(true)
    // 防止 walk 只扫到目录本身
    expect(srcs.every((s) => statSync(path.join(DASHBOARD_ROOT, 'src', s.rel)).isFile())).toBe(true)
  })
})

// ────────────────────────── P2：双 hub 隔离（M3 结构证据 + F-B6 种子） ──────────────────────────

describe('P2 共享 hub 的每课隔离（布局不变，隔离由 hub 自己保证）', () => {
  it('共享 hub 不被钉死到任何一门课的 job 目录（每课目录由 --traj-root 派生）', () => {
    // 隔离的新形式：不再是「两个 hub 各自看自己的目录」，而是「**一个** hub 按 course
    // 路由到 <traj-root>/<课>/...」（磁盘布局与每课一 hub 时代逐字节相同）。
    // 这行断言守的是旧形状的回归：一旦有人把某一门课的 --job-root/--jsonl 塞回去，
    // 共享 hub 就只看得见那一门课——其余全部饿死。
    const argv = hubServerSpec(dualCourseCfg()).cmd.map(String)
    expect(argv).not.toContain('--job-root')
    expect(argv).not.toContain('--jsonl')
    expect(argv).toContain('--discover')
  })

  it('M3：drainStaleJobs 只下架本课 jobRoot，B 课 pending 原样保留', () => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'bcity-p2m3-'))
    SCRATCH_DIRS.push(root)
    const mk = (course: string, jid: string) => {
      const jobRoot = path.join(root, course, 'remote-jobs')
      const jdir = path.join(jobRoot, jid)
      mkdirSync(jdir, { recursive: true })
      writeFileSync(path.join(jdir, 'payload.tar.xz'), 'x')
      const jsonl = path.join(root, course, 'training_log.jsonl')
      writeFileSync(jsonl, `${JSON.stringify({ event: 'job_pending', job_id: jid })}\n`)
      return { jobRoot, jsonl }
    }
    const A = mk('course-a', 'jid-a')
    const B = mk('course-b', 'jid-b')
    const bJsonlBefore = readFileSync(B.jsonl, 'utf-8')
    drainStaleJobs(A.jobRoot, A.jsonl)
    expect(existsSync(path.join(A.jobRoot, 'jid-a', 'payload.tar.xz'))).toBe(false)
    expect(existsSync(path.join(B.jobRoot, 'jid-b', 'payload.tar.xz'))).toBe(true)
    expect(readFileSync(B.jsonl, 'utf-8')).toBe(bJsonlBefore)
  })

  it('F-B6：seedWeightsFromBc 逐字节复制（sha 相等），缺文件 fail loud', () => {
    // 夹具全自造（BCITY_CURRICULA_DIR 注入临时课程目录，paths.ts 惰性取值）：
    // 此前引用真实课程 c6-dmgfix 的 bc → weights/c6-pickup/it35（仅训练机存在），
    // 其余机器全红——测试不得依赖机器上的真实数据文件。
    const root = mkdtempSync(path.join(os.tmpdir(), 'bcity-p2w3-'))
    SCRATCH_DIRS.push(root)
    const curricula = path.join(root, 'curricula')
    mkdirSync(curricula, { recursive: true })
    const prev = process.env.BCITY_CURRICULA_DIR
    process.env.BCITY_CURRICULA_DIR = curricula
    try {
      const bc = path.join(root, 'bc-seed.json')
      writeFileSync(bc, '{"fake":"weights-bytes"}')
      // resolveCourseBc 以 REPO_ROOT 为基准 join bc 字段 → 用 REPO_ROOT 相对路径
      writeFileSync(
        path.join(curricula, 'f-b6-fake.jsonc'),
        JSON.stringify({ bc: path.relative(REPO_ROOT, bc) }),
      )
      const dst = path.join(root, 'weights.json')
      const used = seedWeightsFromBc('f-b6-fake', dst)
      expect(readFileSync(dst).equals(readFileSync(used))).toBe(true)
      // bc 声明的文件缺失 → fail loud（§384 事故回归防线），绝不静默回退
      writeFileSync(
        path.join(curricula, 'f-b6-missing.jsonc'),
        JSON.stringify({ bc: path.join(root, 'nope.json') }),
      )
      expect(() => seedWeightsFromBc('f-b6-missing', path.join(root, 'w2.json'))).toThrow()
    } finally {
      if (prev === undefined) delete process.env.BCITY_CURRICULA_DIR
      else process.env.BCITY_CURRICULA_DIR = prev
    }
  })
})

// ────────────────────────── P3：每课一隧道（URL 归属 + 槽位 + push 注入） ──────────────────────────

describe('P3 共享单隧道', () => {
  it('writeRemoteHubUrl 只写单键 remote_hub_url（URL 是全局事实）', () => {
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-p3cfg-'))
    SCRATCH_DIRS.push(scratch)
    const prev = process.env.BCITY_RL_CONFIG
    process.env.BCITY_RL_CONFIG = path.join(scratch, 'rl-config.json')
    try {
      writeFileSync(process.env.BCITY_RL_CONFIG, JSON.stringify(cfgFixture()))
      writeRemoteHubUrl('https://hub.trycloudflare.com')
      const cfg = loadConfig()
      expect(cfg.rl.remote_hub_url).toBe('https://hub.trycloudflare.com')
      // 旧形状的 per-course 键不再被写（也不被读：python 侧回填已删）——
      // 留着它就会变成「指向已不存在的每课隧道」的第二事实源。
      expect(cfg.rl.remote_hubs ?? {}).toEqual({})
      // 同值重写不落盘（mtime 不变）
      const m1 = statSync(process.env.BCITY_RL_CONFIG).mtimeMs
      writeRemoteHubUrl('https://hub.trycloudflare.com')
      expect(statSync(process.env.BCITY_RL_CONFIG).mtimeMs).toBe(m1)
    } finally {
      if (prev === undefined) delete process.env.BCITY_RL_CONFIG
      else process.env.BCITY_RL_CONFIG = prev
    }
  })

  it('cloudflaredSpec 指向共享 hub 端口与共享 metrics 口（与课程无关）', () => {
    const cfg = dualCourseCfg()
    const spec = cloudflaredSpec(cfg, { pid: 1, course: 'course-b', slot: 1 })
    expect(spec.course).toBe('')
    expect(spec.cmd.join(' ')).toContain(`127.0.0.1:${sharedTunnelMetricsPort(cfg)}`)
    expect(spec.cmd.join(' ')).toContain(`localhost:${sharedHubPort(cfg)}`)
    // 旧形状的 per-course 端口不再出现（槽位 1 的 hub/metrics 口与共享地址不同）
    expect(spec.cmd.join(' ')).not.toContain(`localhost:${slotPort(cfg, 1, 'hub')}`)
    expect(spec.cmd.join(' ')).not.toContain(`127.0.0.1:${slotPort(cfg, 1, 'metrics')}`)
  })

  it('M1 cfTunnelArgs：缺省 http2/4；只有 rl.*（单隧道没有 per-course 覆盖）；auto = 不传旗标', () => {
    const cfg = dualCourseCfg()
    // 未配 → 缺省 http2 / 4
    expect(cfTunnelArgs(cfg)).toEqual(['--protocol', 'http2', '--edge-ip-version', '4'])
    // rl.* 全局生效
    cfg.rl.cf_protocol = 'quic'
    cfg.rl.cf_edge_ip = '6'
    expect(cfTunnelArgs(cfg)).toEqual(['--protocol', 'quic', '--edge-ip-version', '6'])
    // per-course 覆盖是「每课一隧道」时代的旋钮：一条隧道没有「谁的配置说了算」的问题，
    // 故 courses 块里的值**不再被读**（不报错、不生效）。
    cfg.courses!['course-b'] = {
      ...cfg.courses!['course-b'],
      cf_protocol: 'http2',
      cf_edge_ip: '4',
    }
    expect(cfTunnelArgs(cfg)).toEqual(['--protocol', 'quic', '--edge-ip-version', '6'])
    expect(resolveCfTunnel(cfg)).toEqual({ protocol: 'quic', edgeIp: '6' })
    // auto = 逐字节回到旧行为（不传任何旗标）
    cfg.rl.cf_protocol = 'auto'
    cfg.rl.cf_edge_ip = 'auto'
    expect(cfTunnelArgs(cfg)).toEqual([])
    expect(resolveCfTunnel(cfg)).toEqual({ protocol: 'auto', edgeIp: 'auto' })
  })

  it('M1 cloudflaredSpec 带隧道旗标（与 hub.ts 的 spawn 两半同步）', () => {
    const joined = cloudflaredSpec(dualCourseCfg(), { pid: 1, course: '' }).cmd.join(' ')
    expect(joined).toContain('--protocol http2')
    expect(joined).toContain('--edge-ip-version 4')
  })

  it('M1 hub.ts：隧道 spawn 用共用 cfTunnelArgs，且复用前做配置变更检测', () => {
    const src = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'stack', 'hub.ts'), 'utf8').replace(
      /\s+/g,
      ' ',
    )
    // 两半同步：spawn 必须走共用 helper，不得再自拼 --protocol
    expect(src).toContain('...cfTunnelArgs(cfg)')
    // 变更检测（防「改了选项不生效」的假成功）：登记值与当前配置不一致即杀旧起新
    expect(src).toContain('resolveCfTunnel(cfg)')
    expect(src).toContain('prevProtocol !== wantTunnel.protocol')
    expect(src).toContain('cfProtocol: wantTunnel.protocol')
  })

  it('trainingLoopSpec 注入本课 REMOTE_PUSH_NODE（缺省不注，默认行为零变化）', () => {
    const cfg = dualCourseCfg()
    const venv = { python: 'python', sitePackages: 'sp' }
    const withPush = trainingLoopSpec(cfg, {
      course: 'course-a',
      ppo: 'remote',
      pushNodeUrl: 'https://w.example',
      venv,
    })
    expect(withPush.env?.REMOTE_PUSH_NODE).toBe('https://w.example')
    const legacy = trainingLoopSpec(cfg, { course: 'course-a', ppo: 'local', venv })
    expect(legacy.env?.REMOTE_PUSH_NODE).toBeUndefined()
  })
})

// ────────────────────────── P4：配额加法校验接入保存路径 ──────────────────────────

describe('P4 控制台保存路径接 checkCapacity', () => {
  it('超量配额：capacityError 点名超量课程并含超量数', () => {
    const cap = bareCapacity(cfgFixture())
    const cfg = cfgFixture()
    const over: RlConfig = {
      ...cfg,
      courses: { a: { workers: cap, local_slots: 0 }, b: { workers: 0, local_slots: cap } },
    }
    const msg = capacityError(over)
    expect(msg).not.toBeNull()
    expect(msg).toContain('b') // 被点名（累计越界的那门）
    expect(msg).toContain(String(2 * cap))
  })

  it('恰等于容量：capacityError 通过；无 courses 块为空操作', () => {
    const cap = bareCapacity(cfgFixture())
    const cfg = cfgFixture()
    expect(capacityError({ ...cfg, courses: { a: { workers: cap, local_slots: 0 } } })).toBeNull()
    expect(capacityError({ ...cfg, courses: {} })).toBeNull()
    expect(capacityError(cfg)).toBeNull()
  })

  it('saveConfig 超量时拒绝落盘（fail-fast，磁盘保持原样）', () => {
    const cap = bareCapacity(cfgFixture())
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-p4cfg-'))
    SCRATCH_DIRS.push(scratch)
    const p = path.join(scratch, 'rl-config.json')
    const base = JSON.stringify(cfgFixture(), null, 2)
    writeFileSync(p, base)
    const over: RlConfig = {
      ...cfgFixture(),
      courses: { a: { workers: cap, local_slots: 0 }, b: { workers: cap, local_slots: 0 } },
    }
    expect(() => saveConfig(over, p)).toThrow()
    expect(readFileSync(p, 'utf-8')).toBe(base) // 没有半截/超量落盘
    // 合法配置正常写回
    const valid: RlConfig = { ...cfgFixture(), courses: { a: { workers: 1, local_slots: 1 } } }
    saveConfig(valid, p)
    expect(JSON.parse(readFileSync(p, 'utf-8')).courses.a.workers).toBe(1)
  })
})

// ────────────────────────── P5-R2：旧扁平账本键搬迁 + 读兼容移除 ──────────────────────────

/** 在临时账本上跑一段，并确保不读线上 console-state（否则 no-course 回填取真实课程，非确定）。 */
function withScratchRegistry<T>(fn: (file: string) => T): T {
  const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-r2-'))
  SCRATCH_DIRS.push(scratch)
  const file = path.join(scratch, 'registry.json')
  const prevReg = process.env.BCITY_REGISTRY_FILE
  const prevState = process.env.BCITY_CONSOLE_STATE
  process.env.BCITY_REGISTRY_FILE = file
  process.env.BCITY_CONSOLE_STATE = path.join(scratch, 'absent-console-state.json')
  try {
    return fn(file)
  } finally {
    if (prevReg === undefined) delete process.env.BCITY_REGISTRY_FILE
    else process.env.BCITY_REGISTRY_FILE = prevReg
    if (prevState === undefined) delete process.env.BCITY_CONSOLE_STATE
    else process.env.BCITY_CONSOLE_STATE = prevState
  }
}

describe('P5-R2 旧扁平账本键搬迁（migration）+ 读兼容移除', () => {
  it('升级机旧账本（仅扁平单键）：首尝 loadRegistry 搬进 per-course 表、删键并补 course/slot', () => {
    withScratchRegistry((file) => {
      // 旧形状：无 course/slot 的扁平单键（无 console-state → course 回填 ''）
      writeFileSync(
        file,
        JSON.stringify({
          selfNode: { pid: 11 },
          hubServer: { pid: 22, log: 'hub.log' },
          trainingLoop: { pid: 33, course: 'p3-rd1' },
        }),
        'utf-8',
      )
      const reg = loadRegistry()
      // 落盘形状：扁平键消失，per-course 表就位（slot 缺省 0），selfNode 单例不动
      const onDisk = JSON.parse(readFileSync(file, 'utf-8')) as Record<string, unknown>
      expect(onDisk.hubServer).toBeUndefined()
      expect(onDisk.trainingLoop).toBeUndefined()
      expect(onDisk.selfNode).toMatchObject({ pid: 11 })
      expect(onDisk.hubServers).toMatchObject({ '': { pid: 22, slot: 0, course: '' } })
      expect(onDisk.trainingLoops).toMatchObject({ 'p3-rd1': { pid: 33, slot: 0 } })
      // 读路径严格按课：无课程 = '' 槽（不是猜课程）
      expect(entryForCourse(reg, 'hubServer', '')?.pid).toBe(22)
      expect(entryForCourse(reg, 'trainingLoop', 'p3-rd1')?.pid).toBe(33)
      // 枚举不丢监督：搬迁后的条目全部可见（含三元组归属）
      const triples = registryComponents()
      expect(
        triples.some((t) => t.key === 'hubServer' && t.course === '' && t.entry.pid === 22),
      ).toBe(true)
      expect(triples.some((t) => t.key === 'trainingLoop' && t.course === 'p3-rd1')).toBe(true)
    })
  })

  it('幂等：二次 loadRegistry 不再改写（搬迁只发生一次，无重复落盘）', () => {
    withScratchRegistry((file) => {
      writeFileSync(file, JSON.stringify({ hubServer: { pid: 7 } }), 'utf-8')
      loadRegistry()
      const afterFirst = readFileSync(file, 'utf-8')
      loadRegistry()
      expect(readFileSync(file, 'utf-8')).toBe(afterFirst)
      // 二次读到的依然是 per-course 条目
      expect(entryForCourse(loadRegistry(), 'hubServer', '')?.pid).toBe(7)
    })
  })

  it('per-course 表已有同课新条目时：保留新条目、只丢陈旧扁平键（升级不倒退）', () => {
    withScratchRegistry((file) => {
      writeFileSync(
        file,
        JSON.stringify({
          hubServer: { pid: 1 }, // 陈旧
          hubServers: { '': { pid: 2, course: '', slot: 0 } }, // 新写入路径
        }),
        'utf-8',
      )
      const reg = loadRegistry()
      expect(entryForCourse(reg, 'hubServer', '')?.pid).toBe(2)
      const onDisk = JSON.parse(readFileSync(file, 'utf-8')) as Record<string, unknown>
      expect(onDisk.hubServer).toBeUndefined()
    })
  })

  it('读兼容移除：账本里残留的扁平键不再被任何读路径看见（严格 per-course）', () => {
    const stale = { hubServer: { pid: 99 } } as unknown as Registry
    expect(entryForCourse(stale, 'hubServer', '')).toBeUndefined()
    expect(entryForCourse(stale, 'hubServer', 'a')).toBeUndefined()
    expect(registryTriples(stale)).toEqual([])
    const good: Registry = { hubServers: { '': { pid: 1 }, a: { pid: 2 } } }
    expect(entryForCourse(good, 'hubServer', '')?.pid).toBe(1)
    expect(entryForCourse(good, 'hubServer', 'a')?.pid).toBe(2)
  })
})

// ──────────────────── W7：课程上限与越界槽位（R3-1 修端口撞车） ────────────────────

/** 用户口径（2026-09-18）：**并行课程上限先设为 5**。
 *
 *  这是**需求**、不是实现常量：故意写成字面量（不引用 `SLOT_COUNT`），否则实现把上限
 *  改回 4 时「5 门课」的用例会自己缩成 4 门、永远绿（判据跟着实现跑 = 假绿）。 */
const REQUIRED_COURSES = 5

/** N 门课各占一个槽位（slot 0..N-1）——与用户会在 rl-config 里写的形状一致。 */
function nCourseCfg(n: number): RlConfig {
  const courses: Record<string, { slot: number }> = {}
  for (let s = 0; s < n; s++) courses[`course-${s}`] = { slot: s }
  return { ...cfgFixture(), courses }
}

describe('W7 课程上限：第 5 门课不许撞第 1 门课的端口（R3-1）', () => {
  it('实现至少容得下 5 门课（用户口径）', () => {
    expect(SLOT_COUNT).toBeGreaterThanOrEqual(REQUIRED_COURSES)
  })

  it('5 门课各自的 push 端口互异（越界槽位不再静默回落 0）', () => {
    const cfg = nCourseCfg(REQUIRED_COURSES)
    const ports = Array.from({ length: REQUIRED_COURSES }, (_, s) =>
      slotPort(cfg, `course-${s}`, 'push'),
    )
    // 旧实现（SLOT_COUNT=4 + 越界回落 0）：第 5 门课拿到 slot 0 的口 ⇒ 与 course-0 相撞
    expect(new Set(ports).size).toBe(REQUIRED_COURSES)
    expect(slotOf(cfg, `course-${REQUIRED_COURSES - 1}`)).toBe(REQUIRED_COURSES - 1)
  })

  it('越界/非法槽位响亮拒启（点名课程 + 上限），不再静默顶到槽位 0', () => {
    const bad: unknown[] = [REQUIRED_COURSES, -1, 1.5, '2']
    for (const v of bad) {
      const cfg = { ...cfgFixture(), courses: { a: { slot: v } } } as unknown as RlConfig
      expect(() => slotOf(cfg, 'a')).toThrow(/槽位/)
      // 诊断必须能直接定位：课程名 + 越界值 + 上限
      let msg = ''
      try {
        slotOf(cfg, 'a')
      } catch (e) {
        msg = (e as Error).message
      }
      expect(msg).toContain('a')
      expect(msg).toContain(JSON.stringify(v))
      expect(msg).toContain(String(SLOT_COUNT))
      // 端口算术同样响亮（调用方不必各自记得先校验）
      expect(() => slotPort(cfg, 'a', 'push')).toThrow(/槽位/)
    }
  })

  it('未配置 = 仍回落槽位 0（旧单课行为零变化）', () => {
    const cfg = cfgFixture()
    expect(slotOf(cfg, 'any-course')).toBe(0) // courses 块整个缺失
    expect(slotOf({ ...cfg, courses: { z: {} } } as RlConfig, 'z')).toBe(0) // 只配了配额
    expect(slotOf({ ...cfg, courses: { z: { slot: null } } } as unknown as RlConfig, 'z')).toBe(0)
    // 旧单课口径：共享 hub 与槽位无关（配错 slot 不许把 hub 地址一起带偏）
    expect(sharedHubPort(cfg)).toBe(slotPort(cfg, 0, 'hub'))
  })

  it('端口兜底清单随上限扩容（每个槽位 3 口 + agent，无重复）', () => {
    const cfg = nCourseCfg(REQUIRED_COURSES)
    const ports = allSlotPorts(cfg)
    expect(ports.length).toBe(SLOT_COUNT * 3 + 1)
    expect(new Set(ports).size).toBe(ports.length) // 撞口是不会被容忍的
    for (let s = 0; s < SLOT_COUNT; s++) {
      for (const kind of ['hub', 'metrics', 'push'] as const) {
        expect(ports).toContain(slotPort(cfg, s, kind))
      }
    }
  })

  it('两门课配到同一个槽位 = 撞 push 端口 ⇒ 守卫点名两门课', () => {
    const dup = { ...cfgFixture(), courses: { a: { slot: 1 }, b: { slot: 1 } } } as RlConfig
    const msg = slotError(dup)
    expect(msg).not.toBeNull()
    expect(msg).toContain('a')
    expect(msg).toContain('b')
    expect(msg).toContain('撞同一个 push 端口')
    // 两门课的真实端口确实是同一个（这才是病根）
    expect(slotPort(dup, 'a', 'push')).toBe(slotPort(dup, 'b', 'push'))
    // legacy：全都没配槽位 = 旧单课语义，不当错误（升级路上常态）
    expect(slotError({ ...cfgFixture(), courses: { a: {}, b: {} } } as RlConfig)).toBeNull()
  })

  it('saveConfig 把越界槽位挡在落盘之前（点名课程，磁盘保持原样）', () => {
    const cfg = { ...cfgFixture(), courses: { a: { slot: REQUIRED_COURSES } } } as RlConfig
    const msg = slotError(cfg)
    expect(msg).not.toBeNull()
    expect(msg).toContain('a')
    expect(msg).toContain(String(REQUIRED_COURSES))

    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-w7cfg-'))
    SCRATCH_DIRS.push(scratch)
    const p = path.join(scratch, 'rl-config.json')
    const base = JSON.stringify(cfgFixture(), null, 2)
    writeFileSync(p, base)
    expect(() => saveConfig(cfg, p)).toThrow()
    expect(readFileSync(p, 'utf-8')).toBe(base)
    // 合法配置（5 门课各占一槽）照常落盘
    saveConfig(nCourseCfg(REQUIRED_COURSES), p)
    const saved = JSON.parse(readFileSync(p, 'utf-8')) as RlConfig
    expect(Object.keys(saved.courses ?? {})).toHaveLength(REQUIRED_COURSES)
    expect(slotError(nCourseCfg(REQUIRED_COURSES))).toBeNull()
  })
})
