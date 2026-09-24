/**
 * course-mode-bundle.test.ts — 「切离线」必须顺手把任务包导出来（plan/offline-switch-auto-bundle）。
 *
 *  用户 2026-09-25 报障：在线课切成离线后云机取包 404，等满 `wait_pack_sec`（30 分钟）才
 *  由一句 `SystemExit` 告诉人。根因是**切离线那颗开关不导出**（只有开课导）。
 *
 *  本文件钉两半：
 *   ① `autoBundleDecision` 的**全表**（纯函数：六种输入逐条对上 plan §3.1 的规则）；
 *   ② `setCourseMode` 的接线**只以「不起子进程」为代价的**那几条：skip 分支零副作用
 *      （盘上的包不被挪进 `stale-packs/`）、回执带上那一行、`ok` 不被派生动作改写。
 *
 *  真起导出（真起 `run_rl` 子进程）不在这里跑——`launchTaskBundleExport` 自己的 argv /
 *  作废 / 恢复由 `server-api-task-bundle` 套件覆盖。这里用假盘（仓 `tmp/` 下的一次性
 *  假课程名，收尾删掉）钉住最容易写歪的两条：**有包不动**、**缺权重不导**。
 */

import { afterAll, beforeEach, describe, expect, it } from 'bun:test'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { REPO_ROOT } from '../src/core/paths'

const DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-cmode-bundle-'))
process.env.BCITY_CONSOLE_STATE = path.join(DIR, 'console-state.json')
process.env.BCITY_REGISTRY_FILE = path.join(DIR, 'registry.json')
process.env.BCITY_RL_CONFIG = path.join(DIR, 'rl-config.json')
writeFileSync(
  process.env.BCITY_RL_CONFIG,
  JSON.stringify({ version: 1, nodes: [], rl: { hub_port: 18787, remote_token: 'tok' } }),
  'utf-8',
)

import { saveConsoleState } from '../src/server/actions/console-state'
import { autoBundleDecision, setCourseMode } from '../src/server/actions/course-mode'
import {
  stalePackDir,
  taskBundleFileName,
  taskBundleInfo,
  taskBundlePath,
} from '../src/server/bundles'

/** 假课程名：进的是仓 `tmp/` 之下，必须一眼看出是测试残留（收尾删整目录）。 */
const WITH_PACK = '__cmode-with-pack__'
const NO_WEIGHTS = '__cmode-no-weights__'
const FAKE_COURSES = [WITH_PACK, NO_WEIGHTS]

const livePack = (course: string): string => taskBundlePath(course)
const tmpCourseDir = (course: string): string => path.join(REPO_ROOT, 'tmp', course)

afterAll(() => {
  for (const c of FAKE_COURSES) rmSync(tmpCourseDir(c), { recursive: true, force: true })
  try {
    rmSync(DIR, { recursive: true, force: true })
  } catch {
    /* noop */
  }
})

/** 假 hub：`mode = 'ok' | 'reject' | 'throw'`。 */
let hub: 'ok' | 'reject' | 'throw' = 'ok'
let calls = 0
globalThis.fetch = (async (_input: string | URL | Request, _init?: RequestInit) => {
  calls += 1
  if (hub === 'throw') throw new Error('ECONNREFUSED 127.0.0.1:18787')
  if (hub === 'reject') {
    return new Response(JSON.stringify({ error: '需要合法 course（[]) 与 mode' }), { status: 400 })
  }
  return new Response(JSON.stringify({ course: 'c', mode: 'offline' }), { status: 200 })
}) as typeof fetch

beforeEach(() => {
  hub = 'ok'
  calls = 0
  saveConsoleState({ courseModes: {}, courseRolloutSrc: {} })
  delete process.env.BCITY_NO_AUTO_TASK_BUNDLE
  for (const c of FAKE_COURSES) rmSync(tmpCourseDir(c), { recursive: true, force: true })
})

/** 造一门「盘上已经有包」的课（顺带给出起点权重，让 `exportGuard` 也通过）。 */
function seedPack(course: string, opts: { weights?: boolean } = {}): string {
  const dir = tmpCourseDir(course)
  mkdirSync(dir, { recursive: true })
  if (opts.weights !== false) writeFileSync(path.join(dir, 'weights.json'), '{}', 'utf-8')
  const p = livePack(course)
  writeFileSync(p, 'FAKE-PACK-BYTES', 'utf-8')
  return p
}

const facts = (over: Partial<Parameters<typeof autoBundleDecision>[0]> = {}) => ({
  mode: 'offline',
  hubAccepted: true,
  valve: false,
  busy: false,
  guardReason: null,
  pack: taskBundleInfo('__cmode-never-exported__'),
  ...over,
})

describe('autoBundleDecision（纯函数：规则表逐条）', () => {
  it('切在线 ⇒ 不动、也不说话（导包只属于离线语义）', () => {
    expect(autoBundleDecision(facts({ mode: 'online' }))).toEqual({ started: false, note: '' })
  })

  it('hub 没接受这次模式 ⇒ 不导（离线意图没落地，包没有消费者）', () => {
    const r = autoBundleDecision(facts({ hubAccepted: false }))
    expect(r.started).toBe(false)
    expect(r.note).toContain('未接受离线意图')
  })

  it('逃生阀 ⇒ 不导，且说明是逃生阀（不是「已经导过了」）', () => {
    const r = autoBundleDecision(facts({ valve: true }))
    expect(r.started).toBe(false)
    expect(r.note).toContain('BCITY_NO_AUTO_TASK_BUNDLE')
  })

  it('导出忙 ⇒ 不导（已有一次在跑，成果一样会被云机取到）', () => {
    const r = autoBundleDecision(facts({ busy: true }))
    expect(r.started).toBe(false)
    expect(r.note).toContain('还在跑')
  })

  it('缺起点权重 ⇒ 不导，**原样转述** exportGuard 的原因并指路', () => {
    const r = autoBundleDecision(facts({ guardReason: '没有起点权重（tmp/<课程>/weights.json）' }))
    expect(r.started).toBe(false)
    expect(r.note).toContain('没有起点权重')
    expect(r.note).toContain('先跑至少一轮')
  })

  it('盘上已有包 ⇒ 不导也不作废（热切不是重开课），回执给路径与「要重打」的指路', () => {
    const p = seedPack(WITH_PACK)
    try {
      const r = autoBundleDecision(facts({ pack: taskBundleInfo(WITH_PACK) }))
      expect(r.started).toBe(false)
      expect(r.note).toContain('已有任务包')
      expect(r.note).toContain(p)
      expect(r.note).toContain('导出任务包')
    } finally {
      rmSync(tmpCourseDir(WITH_PACK), { recursive: true, force: true })
    }
  })

  it('缺包且可导 ⇒ 导（并预告导出窗口里云机会看到 404）', () => {
    const r = autoBundleDecision(facts())
    expect(r.started).toBe(true)
    expect(r.note).toContain('404')
  })
})

describe('setCourseMode 的接线（假盘 + 假 hub；不起子进程）', () => {
  it('切离线 + 盘上已有包 ⇒ ok=true、说「已有任务包」、**包没被挪走**', async () => {
    const p = seedPack(WITH_PACK)
    const r = await setCourseMode(WITH_PACK, 'offline')
    expect(r.ok).toBe(true)
    expect(r.bundle?.started).toBe(false)
    expect(r.message).toContain('已有任务包')
    // ★ 这条是本改动最容易写歪的地方：`launchTaskBundleExport` 会先把旧包作废，
    //   所以「有包不动」必须体现在**不调用**，而不是调用后再恢复。
    expect(existsSync(p)).toBe(true)
    expect(existsSync(stalePackDir(WITH_PACK))).toBe(false)
  })

  it('切离线 + 缺起点权重 ⇒ ok=true、说清原因、不导也不作废', async () => {
    const p = seedPack(NO_WEIGHTS, { weights: false })
    const r = await setCourseMode(NO_WEIGHTS, 'offline')
    expect(r.ok).toBe(true)
    expect(r.bundle?.started).toBe(false)
    expect(r.message).toContain('weights.json')
    expect(r.message).toContain('起点权重')
    expect(existsSync(p)).toBe(true)
  })

  it('切在线 ⇒ 不导、也不多那一行（回执仍是原来的三段）', async () => {
    const r = await setCourseMode(WITH_PACK, 'online')
    expect(r.ok).toBe(true)
    expect(r.bundle).toEqual({ started: false, note: '' })
    expect(r.message).not.toContain('任务包')
  })

  it('逃生阀置位 ⇒ 不导（既有套件的兼容路径：用例不该起真子进程）', async () => {
    process.env.BCITY_NO_AUTO_TASK_BUNDLE = '1'
    const r = await setCourseMode(NO_WEIGHTS, 'offline')
    expect(r.ok).toBe(true)
    expect(r.bundle?.started).toBe(false)
    expect(r.message).toContain('逃生阀')
  })

  it('hub 拒绝（400）⇒ ok=false，且**派生动作照规矩不导**（这条把顺序钉死）', async () => {
    hub = 'reject'
    const r = await setCourseMode(NO_WEIGHTS, 'offline')
    expect(r.ok).toBe(false)
    expect(calls).toBe(1)
    expect(r.bundle?.started).toBe(false)
    expect(r.message).toContain('未接受离线意图')
  })

  it('hub 不可达 ⇒ 同上：模式没生效就不导包（不白起一个没有消费者的导出）', async () => {
    hub = 'throw'
    const r = await setCourseMode(NO_WEIGHTS, 'offline')
    expect(r.ok).toBe(false)
    expect(r.bundle?.started).toBe(false)
    expect(r.message).toContain('未接受离线意图')
  })

  it('源码哨兵：started 的分支必须真的调 `launchTaskBundleExport`（决定与动作不许写岔）', () => {
    // 「决定说导、动作却没导」是本改动最隐蔽的失败态（回执会撒谎）。
    // 纯函数表已钉住决定；这里钉住**那个分支里确实有调用**。
    const src = readFileSync(
      path.join(REPO_ROOT, 'dashboard/src/server/actions/course-mode.ts'),
      'utf-8',
    )
    expect(src).toContain('if (bundle.started) {')
    expect(src).toContain('launchTaskBundleExport(c)')
  })
})

/** 任务包文件名与路径的既有约定（本套件靠它造/删假盘，改约定要一起改）。 */
describe('假盘用的路径约定（防止约定漂移把上面几条悄悄变成空跑）', () => {
  it('包名 = task-<课>.zip，落点在 tmp/<课>/ 之下', () => {
    expect(taskBundleFileName(WITH_PACK)).toBe(`task-${WITH_PACK}.zip`)
    expect(livePack(WITH_PACK).endsWith(path.join(WITH_PACK, `task-${WITH_PACK}.zip`))).toBe(true)
  })
})
