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
 *  纪律：断言里的容量/端口一律从真实 `rl-config.json` 读值推导，**不写死数字**
 *  （F-B2：分支间配置会漂，写死即谎言）。
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
import { CONFIG_PATH, REPO_ROOT } from '../tools/training/paths'
import {
  HUB_SERVER_ENTRY,
  cloudflaredSpec,
  hubServerSpec,
  trainingLoopSpec,
  workerServeSpec,
} from '../tools/training/specs'
import {
  entryForCourse,
  loadRegistry,
  registryComponents,
  registryTriples,
  saveCourseComponent,
} from '../tools/training/registry'
import { loadConfig, saveConfig, writeRemoteHubUrl } from '../tools/training/config'
import { drainStaleJobs, supersedeSlotTunnels } from '../tools/training/hub'
import { killPid, pidAlive } from '../tools/training/net'
import { seedWeightsFromBc } from '../tools/training/courses'
import { restartSpecFor } from '../tools/training/console/actions'
import { capacityError, checkCapacity, lockName, slotOf, slotPort } from '../tools/training/slots'
import type { Registry, RlConfig } from '../tools/training/types'

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

// ────────────────────────── rl-config 实测值（禁止写死） ──────────────────────────

function loadRealConfig(): RlConfig {
  return JSON.parse(readFileSync(CONFIG_PATH, 'utf-8')) as RlConfig
}

/** 裸机容量 = max(rl.workers, rl.local_slots)（plan §1.1「裸机容量」）。 */
function bareCapacity(cfg: RlConfig): number {
  const rl = cfg.rl as Record<string, unknown>
  return Math.max(Number(rl.workers ?? 0), Number(rl.local_slots ?? 0))
}

/** 双课程测试配置：两课各占一个槽位（§1.3 配置 schema）。 */
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

function cmdPort(spec: { cmd: string[] }): number | null {
  for (let i = 0; i < spec.cmd.length; i++) {
    if (spec.cmd[i] === '--port') return Number(spec.cmd[i + 1])
  }
  return null
}

// ────────────────────────── W1：双课程 spec 隔离 ──────────────────────────

describe('W1 双课程 spec 隔离', () => {
  it('两门课程的 hub-server 端口与日志路径互不相同', () => {
    const cfg = dualCourseCfg()
    const a = hubServerSpec(cfg, 'course-a')
    const b = hubServerSpec(cfg, 'course-b')
    expect(cmdPort(a)).not.toBeNull()
    expect(cmdPort(a)).not.toBe(cmdPort(b))
    expect(a.log).not.toBe(b.log)
  })

  it('两门课程的 worker_server 端口与 work 目录互不相同', () => {
    const cfg = dualCourseCfg()
    const venv = { python: 'python', sitePackages: 'sp' }
    const a = workerServeSpec(cfg, venv, 'course-a')
    const b = workerServeSpec(cfg, venv, 'course-b')
    expect(cmdPort(a)).not.toBe(cmdPort(b))
    expect(a.log).not.toBe(b.log)
    const workDir = (spec: { cmd: string[] }) => {
      const i = spec.cmd.indexOf('--work')
      return i < 0 ? null : spec.cmd[i + 1]
    }
    expect(workDir(a)).not.toBe(workDir(b))
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
    const cap = bareCapacity(loadRealConfig())
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
    const cap = bareCapacity(loadRealConfig())
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
  it('A 课 hub spec 重建与原 spec 逐字段一致（含 slot/jobRoot/port）', () => {
    // 账本重定向到临时文件（同 exit-watchdog.test.ts），不碰线上账本；
    // 用内联 set/restore（不用模块顶层赋值），与并行跑的其它测试文件互不干扰。
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-p0w4-'))
    const prev = process.env.BCITY_REGISTRY_FILE
    process.env.BCITY_REGISTRY_FILE = path.join(scratch, 'registry.json')
    try {
      const cfg = loadRealConfig()
      const course = 'course-a'
      const jobRoot = path.join(REPO_ROOT, 'tmp', course, 'remote-jobs')
      const spec = hubServerSpec(cfg, course)
      // 登记形状与 hub.ts::stepHubServer 的写入端同构（§1.4 重建完备字段）。
      saveCourseComponent('hubServer', course, {
        pid: 424242,
        course,
        slot: slotOf(cfg, course),
        entry: HUB_SERVER_ENTRY,
        jobRoot,
        log: spec.log,
        url: `http://127.0.0.1:${cmdPort(spec) ?? 0}`,
      })
      // 登记 round-trip：条目按 (key, course) 原样回来（监督/重启的数据源）。
      expect(entryForCourse(loadRegistry(), 'hubServer', course)).toMatchObject({
        course,
        slot: slotOf(cfg, course),
        jobRoot,
      })
      // 重建 spec 与原 spec 逐字段一致（healthy 是闭包，逐可比字段断言）。
      const fresh = restartSpecFor('hubServer', course)
      expect(fresh).not.toBeNull()
      for (const k of ['key', 'name', 'course', 'cmd', 'env', 'log', 'sentinels'] as const) {
        expect(fresh![k]).toEqual(spec[k])
      }
    } finally {
      if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
      else process.env.BCITY_REGISTRY_FILE = prev
      rmSync(scratch, { recursive: true, force: true })
    }
  })
})

// ────────────────────────── W6：同槽位 cloudflared 隧道接管（2026-09-14 事故） ──────────────────────────

describe('W6 同槽位 cloudflared 隧道接管', () => {
  /** 真实存活子进程（pidAlive 为真），充当「旧隧道进程」。 */
  function livePid(): { proc: Bun.Subprocess; pid: number } {
    const proc = Bun.spawn([process.execPath, '-e', 'setInterval(() => {}, 1000)'], {
      stdout: 'ignore',
      stderr: 'ignore',
      windowsHide: true,
    })
    return { proc, pid: proc.pid }
  }

  it('接管并杀同槽位残留隧道；异槽位/死 pid 不动；同课自身条目保留', async () => {
    // 复现（2026-09-14）：c6-chip 残留隧道长期占住 slot0 metrics 口，bc-c4-v3 隧道
    // bind 失败 12s 退出、控制台「启动失败」。修复 = 启动前接管同槽位其它课程的存活隧道。
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-p0w6-'))
    const prev = process.env.BCITY_REGISTRY_FILE
    process.env.BCITY_REGISTRY_FILE = path.join(scratch, 'registry.json')
    const stale = livePid() // c6-chip 残留：同槽位 0 —— 必须被接管杀掉
    const other = livePid() // bc-c4：异槽位 1 —— 不得误伤
    try {
      const cfg = loadRealConfig()
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

      const killed = await supersedeSlotTunnels('bc-c4-v3', 0)
      expect(killed.sort()).toEqual(['c6-chip'])

      const reg = loadRegistry()
      expect(reg.cloudflareds?.['c6-chip']).toBeUndefined() // 已清账
      expect(reg.cloudflareds?.['bc-c4']).toBeDefined() // 异槽位保留
      expect(reg.cloudflareds?.['bc-c4-v3']).toBeDefined() // 同课条目不动
      expect(pidAlive(stale.pid)).toBe(false) // 残留进程已死
      expect(pidAlive(other.pid)).toBe(true) // 异槽位进程存活
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

  it('接线：stepCloudflared 在 spawn 前调用 supersedeSlotTunnels（防只管 helper 忘接线）', () => {
    // 功能由上面的单测覆盖，但调用点被删会让 helper 形同虚设——grep 门禁守住接线。
    const src = readFileSync(path.join(REPO_ROOT, 'tools', 'training', 'hub.ts'), 'utf-8')
    const step = src.slice(src.indexOf('export async function stepCloudflared'))
    const spawnIdx = step.indexOf('spawnBg')
    const callIdx = step.indexOf('supersedeSlotTunnels')
    expect(callIdx).toBeGreaterThan(-1)
    expect(callIdx).toBeLessThan(spawnIdx) // 必须早于 spawn 循环（先清口再起新隧道）
  })
})

// ────────────────────────── W5：grep 门禁（纯文本扫描） ──────────────────────────

/** tools/training/** 下全部 .ts/.tsx（排除 tests 与生成物）。 */
function trainingSources(): Array<{ rel: string; text: string }> {
  const root = path.join(REPO_ROOT, 'tools', 'training')
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
    expect(srcs.some((s) => s.rel === 'specs.ts')).toBe(true)
    // 防止 walk 只扫到目录本身
    expect(
      srcs.every((s) => statSync(path.join(REPO_ROOT, 'tools', 'training', s.rel)).isFile()),
    ).toBe(true)
  })
})

// ────────────────────────── P2：双 hub 隔离（M3 结构证据 + F-B6 种子） ──────────────────────────

describe('P2 双 hub 隔离', () => {
  it('两课 hub 的 --job-root 与 --jsonl 互不相同（调用方传对即隔离，M3）', () => {
    const cfg = dualCourseCfg()
    const a = hubServerSpec(cfg, 'course-a')
    const b = hubServerSpec(cfg, 'course-b')
    const flag = (spec: { cmd: string[] }, name: string) => spec.cmd[spec.cmd.indexOf(name) + 1]
    expect(flag(a, '--job-root')).not.toBe(flag(b, '--job-root'))
    expect(flag(a, '--jsonl')).not.toBe(flag(b, '--jsonl'))
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

describe('P3 每课一隧道', () => {
  it('writeRemoteHubUrl 按课写 remote_hubs，单键同步兼容（Q2/D2 每事实一归宿）', () => {
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-p3cfg-'))
    SCRATCH_DIRS.push(scratch)
    const prev = process.env.BCITY_RL_CONFIG
    process.env.BCITY_RL_CONFIG = path.join(scratch, 'rl-config.json')
    try {
      writeFileSync(process.env.BCITY_RL_CONFIG, JSON.stringify(loadRealConfig()))
      writeRemoteHubUrl('https://a-tunnel.trycloudflare.com', 'course-a')
      writeRemoteHubUrl('https://b-tunnel.trycloudflare.com', 'course-b')
      const cfg = loadConfig()
      expect(cfg.rl.remote_hubs?.['course-a']).toBe('https://a-tunnel.trycloudflare.com')
      expect(cfg.rl.remote_hubs?.['course-b']).toBe('https://b-tunnel.trycloudflare.com')
      // 单键同步最后一次（兼容读不断）
      expect(cfg.rl.remote_hub_url).toBe('https://b-tunnel.trycloudflare.com')
      // 同值重写不落盘（mtime 不变）
      const m1 = statSync(process.env.BCITY_RL_CONFIG).mtimeMs
      writeRemoteHubUrl('https://a-tunnel.trycloudflare.com', 'course-a')
      expect(statSync(process.env.BCITY_RL_CONFIG).mtimeMs).toBe(m1)
    } finally {
      if (prev === undefined) delete process.env.BCITY_RL_CONFIG
      else process.env.BCITY_RL_CONFIG = prev
    }
  })

  it('cloudflaredSpec 按条目 slot/course 取 metrics 与目标 hub 端口（S13）', () => {
    const cfg = dualCourseCfg()
    const spec = cloudflaredSpec(cfg, { pid: 1, course: 'course-b', slot: 1 })
    const metrics = slotPort(cfg, 1, 'metrics')
    const hub = slotPort(cfg, 1, 'hub')
    expect(spec.course).toBe('course-b')
    expect(spec.cmd.join(' ')).toContain(`127.0.0.1:${metrics}`)
    expect(spec.cmd.join(' ')).toContain(`localhost:${hub}`)
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
    const cap = bareCapacity(loadRealConfig())
    const cfg = loadRealConfig()
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
    const cap = bareCapacity(loadRealConfig())
    const cfg = loadRealConfig()
    expect(capacityError({ ...cfg, courses: { a: { workers: cap, local_slots: 0 } } })).toBeNull()
    expect(capacityError({ ...cfg, courses: {} })).toBeNull()
    expect(capacityError(cfg)).toBeNull()
  })

  it('saveConfig 超量时拒绝落盘（fail-fast，磁盘保持原样）', () => {
    const cap = bareCapacity(loadRealConfig())
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-p4cfg-'))
    SCRATCH_DIRS.push(scratch)
    const p = path.join(scratch, 'rl-config.json')
    const base = JSON.stringify(loadRealConfig(), null, 2)
    writeFileSync(p, base)
    const over: RlConfig = {
      ...loadRealConfig(),
      courses: { a: { workers: cap, local_slots: 0 }, b: { workers: cap, local_slots: 0 } },
    }
    expect(() => saveConfig(over, p)).toThrow()
    expect(readFileSync(p, 'utf-8')).toBe(base) // 没有半截/超量落盘
    // 合法配置正常写回
    const valid: RlConfig = { ...loadRealConfig(), courses: { a: { workers: 1, local_slots: 1 } } }
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
