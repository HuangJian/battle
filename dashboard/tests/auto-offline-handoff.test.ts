/**
 * auto-offline-handoff.test.ts — hub → 控制台的**自动交接**：只导任务包（plan §1.6-F1）。
 *
 *  触发者不是人：自主 worker 在 hub 上 claim 了一门在训课，而盘上没有包（或包比权重/源码旧）
 *  ⇒ hub POST `/api/autoOfflineHandoff`，控制台导出任务包。包到手 + claim 成功才建立**接管
 *  （hold）**——「这门课归谁」自此只由 hub 的 hold 回答。
 *
 *  本文件钉两半：
 *   ① `autoBundleDecision` 的**全表**（纯函数：规则表逐条 —— 逃生阀 / 导出忙 / 缺起点权重 /
 *      已有包不早于权重与源码 / 包过期 ⇒ 作废重导 / 缺包可导）；
 *   ② `autoOfflineHandoff` 的接线**只以「不起子进程」为代价的**那几条：未开课拒收、
 *      空课名拒收、逃生阀是**正常结局**（ok，不是失败）、缺起点权重是「导不了」（ok=false，
 *      那半状态靠 hub 的 stalled 告警兜）、源码哨兵（决定说导 ⇒ 动作真的调了导出）。
 *
 *  真起导出（`run_rl` 子进程）不在这里跑——`launchTaskBundleExport` 自己的 argv / 作废 /
 *  恢复由 `server-api-task-bundle` 套件覆盖。这里用假盘（仓 `tmp/` 下的一次性假课程名，
 *  收尾删掉）钉住最容易写歪的两条：**有包不动**、**缺权重不导**。
 *
 *  ★M4（plan/worker-type-dispatch-model §3-M4）：本套件原为 `course-mode-bundle.test.ts`
 *  （「切离线必须顺手导包」）。那颗开关（`setCourseMode`）随模式语义退役，而**导包这条腿
 *  必须活着**——hub 的 `pending_export` 触发链要靠它出包（F1 定案）。所以本文件留下规则表
 *  与假盘两半，删掉「模式推送 / hub 接受与否 / 意图回执」那一整套（它们的前提没了）。
 */

import { afterAll, beforeEach, describe, expect, it } from 'bun:test'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  utimesSync,
  writeFileSync,
} from 'fs'
import os from 'os'
import path from 'path'
import { REPO_ROOT, tmpCourseDir as coreTmpCourseDir } from '../src/core/paths'

const DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-auto-handoff-'))
process.env.BCITY_CONSOLE_STATE = path.join(DIR, 'console-state.json')
process.env.BCITY_REGISTRY_FILE = path.join(DIR, 'registry.json')
process.env.BCITY_RL_CONFIG = path.join(DIR, 'rl-config.json')
writeFileSync(
  process.env.BCITY_RL_CONFIG,
  JSON.stringify({ version: 1, nodes: [], rl: { hub_port: 18787, remote_token: 'tok' } }),
  'utf-8',
)

import {
  autoBundleDecision,
  autoOfflineHandoff,
  bundleFacts,
  codeDimensionStale,
  ensureFreshTaskBundle,
} from '../src/server/actions/auto-offline-handoff'
import { busy, release } from '../src/server/actions/result'
import {
  backfillHubRun,
  CODE_EXCLUDE_DIRS,
  CODE_EXCLUDE_FILES,
  CODE_SNAPSHOT_MAGIC,
  CODE_SNAPSHOT_PROTO,
  bundleMetaMagic,
  taskBundleStatusMagic,
  taskBundleStatusProto,
  taskBundleStatusFileName,
  taskBundleBusyKey,
  writeTaskBundleStatus,
  EXPORT_TIMEOUT_MS,
  codeSnapshotDir,
  codeSnapshotMetaPath,
  newestCodeMtimeMs,
  readClusterSnapshot,
  stalePackDir,
  taskBundleFileName,
  taskBundleInfo,
  taskBundleMeta,
  taskBundleMetaPath,
  taskBundlePath,
  readTaskBundleStatus,
  exportStatusLine,
  taskBundleStatusPath,
} from '../src/server/bundles'

/** 假课程名：进的是仓 `tmp/` 之下，必须一眼看出是测试残留（收尾删整目录）。 */
const WITH_PACK = '__ahand-with-pack__'
const NO_WEIGHTS = '__ahand-no-weights__'
const FAKE_COURSES = [WITH_PACK, NO_WEIGHTS]

/** 包 mtime 的两个人造档位（**秒**，`utimesSync` 的单位）：未来 = 刚导出；过去 = 过期。 */
const future = (Date.now() + 60_000) / 1000
const past = (Date.now() - 3_600_000) / 1000

const livePack = (course: string): string => taskBundlePath(course)
const tmpCourseDir = (course: string): string => coreTmpCourseDir(course)
/** 开课标记（`courseEnabled` 的判据）：自动交接只接**在训**的课。 */
const enableMarker = (course: string): string =>
  path.join(tmpCourseDir(course), 'training-enabled.txt')

afterAll(() => {
  for (const c of FAKE_COURSES) rmSync(tmpCourseDir(c), { recursive: true, force: true })
  try {
    rmSync(DIR, { recursive: true, force: true })
  } catch {
    /* noop */
  }
})

beforeEach(() => {
  delete process.env.BCITY_NO_AUTO_TASK_BUNDLE
  for (const c of FAKE_COURSES) rmSync(tmpCourseDir(c), { recursive: true, force: true })
})

/** 造一门「盘上已经有包」的课（顺带给出起点权重，让 `exportGuard` 也通过）。
 *
 *  ★ 2026-10-04 起包要**不早于权重与源码**才算「可复用」——测试机上的源码刚被动过
 *  （mtime 必新于这里现造的临时文件），所以把包的 mtime 显式拨到「60s 之后」= 刚导出完
 *  的形态；要测「过期」请用 `seedStalePack`。
 */
function seedPack(course: string, opts: { weights?: boolean; enabled?: boolean } = {}): string {
  const dir = tmpCourseDir(course)
  mkdirSync(dir, { recursive: true })
  if (opts.weights !== false) writeFileSync(path.join(dir, 'weights.json'), '{}', 'utf-8')
  if (opts.enabled !== false) writeFileSync(enableMarker(course), '', 'utf-8')
  const p = livePack(course)
  writeFileSync(p, 'FAKE-PACK-BYTES', 'utf-8')
  utimesSync(p, future, future)
  return p
}

/** 把包拨回「过去」= **过期包**（权重/源码都比它新）。 */
function seedStalePack(course: string): string {
  const p = seedPack(course)
  utimesSync(p, past, past)
  return p
}

const facts = (over: Partial<Parameters<typeof autoBundleDecision>[0]> = {}) => ({
  valve: false,
  busy: false,
  guardReason: null,
  pack: taskBundleInfo('__ahand-never-exported__'),
  weightsMtimeMs: 0,
  // 缺省走**回落**口径（mtime 0 = 不比包新）——快照口径的用例显式传 code
  code: { kind: 'mtime' as const, mtimeMs: 0 },
  ...over,
})

describe('autoBundleDecision（纯函数：规则表逐条）', () => {
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

  it('盘上已有包（不早于权重与源码）⇒ 不导也不作废，回执给路径与「要重打」的指路', () => {
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

  it('包比权重旧（训练已推进）⇒ 作废 + 重导（★ 2026-10-04 用户口径）', () => {
    const p = seedStalePack(WITH_PACK)
    try {
      const info = taskBundleInfo(WITH_PACK)
      const r = autoBundleDecision(
        facts({
          pack: info,
          weightsMtimeMs: info.mtimeMs + 1000,
          code: { kind: 'mtime', mtimeMs: 0 },
        }),
      )
      expect(r.started).toBe(true)
      expect(r.note).toContain('已过期')
      expect(r.note).toContain('训练已推进')
      expect(r.note).toContain('404')
      expect(existsSync(p)).toBe(true) // 纯函数只做决定；作废是 launchTaskBundleExport 的事
    } finally {
      rmSync(tmpCourseDir(WITH_PACK), { recursive: true, force: true })
    }
  })

  it('包比源码旧（导出后改过代码 / hub 带新代码重启过）⇒ 作废 + 重导', () => {
    const p = seedStalePack(WITH_PACK)
    try {
      const info = taskBundleInfo(WITH_PACK)
      const r = autoBundleDecision(
        facts({
          pack: info,
          weightsMtimeMs: 0,
          code: { kind: 'mtime', mtimeMs: info.mtimeMs + 1000 },
        }),
      )
      expect(r.started).toBe(true)
      expect(r.note).toContain('代码在导出之后改过')
      expect(r.note).toContain('code.zip')
      expect(existsSync(p)).toBe(true)
    } finally {
      rmSync(tmpCourseDir(WITH_PACK), { recursive: true, force: true })
    }
  })

  it('两个维度都旧 ⇒ 一行里两条都点名（排障时知道等的是什么）', () => {
    seedStalePack(WITH_PACK)
    try {
      const info = taskBundleInfo(WITH_PACK)
      const r = autoBundleDecision(
        facts({
          pack: info,
          weightsMtimeMs: info.mtimeMs + 1000,
          code: { kind: 'mtime', mtimeMs: info.mtimeMs + 2000 },
        }),
      )
      expect(r.started).toBe(true)
      expect(r.note).toContain('训练已推进')
      expect(r.note).toContain('代码在导出之后改过')
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

describe('autoOfflineHandoff 的接线（假盘；不起子进程）', () => {
  it('空课程名 ⇒ ok=false（hub 的自动交接按课触发——没有课就没有这件事）', async () => {
    const r = await autoOfflineHandoff('  ')
    expect(r.ok).toBe(false)
    expect(r.message).toContain('需要 course')
  })

  it('未开课（没有开课标记）⇒ ok=false，并说清 hub 侧该停在等待', async () => {
    // 有包也不接：停课的课连认课标记都没了，云机领不走（导包只会白烧一次子进程）。
    seedPack(NO_WEIGHTS)
    rmSync(enableMarker(NO_WEIGHTS), { force: true })
    try {
      const r = await autoOfflineHandoff(NO_WEIGHTS)
      expect(r.ok).toBe(false)
      expect(r.message).toContain('未开课')
    } finally {
      rmSync(tmpCourseDir(NO_WEIGHTS), { recursive: true, force: true })
    }
  })

  it('逃生阀置位 ⇒ ok=true（正常结局）且不导（既有套件的兼容路径：用例不该起真子进程）', async () => {
    process.env.BCITY_NO_AUTO_TASK_BUNDLE = '1'
    seedPack(NO_WEIGHTS, { weights: false })
    try {
      const r = await autoOfflineHandoff(NO_WEIGHTS)
      expect(r.ok).toBe(true)
      expect(r.message).toContain('逃生阀')
      expect(r.message).toContain('自动交接')
    } finally {
      rmSync(tmpCourseDir(NO_WEIGHTS), { recursive: true, force: true })
    }
  })

  it('缺起点权重 ⇒ ok=false（「导不了」不是好消息）+ 原样转述原因 + 指路', async () => {
    const p = seedPack(NO_WEIGHTS, { weights: false })
    try {
      const r = await autoOfflineHandoff(NO_WEIGHTS)
      expect(r.ok).toBe(false)
      expect(r.message).toContain('weights.json')
      expect(r.message).toContain('起点权重')
      expect(existsSync(p)).toBe(true)
    } finally {
      rmSync(tmpCourseDir(NO_WEIGHTS), { recursive: true, force: true })
    }
  })

  it('B0：回灌在**判包之前**跑（否则规则表只会说「已有任务包，云机可直接取」）', async () => {
    // 顺序是承重的：段末那一刻盘上只有**刚跑完那一段**的旧包，而本机账本停在段起点 ——
    // 回灌把账本推进到段末之后，同一张规则表才命中 ④'（包比权重旧 ⇒ 作废重导）。
    seedPack(WITH_PACK)
    const calls: string[] = []
    try {
      const r = await autoOfflineHandoff(WITH_PACK, {
        backfill: async (course: string) => {
          calls.push(course)
          return { ok: true, ran: true, note: '已回灌段末产物 seg-1（it1 → it3）——本机账本已推进' }
        },
      })
      expect(calls).toEqual([WITH_PACK])
      expect(r.message).toContain('已回灌段末产物')
    } finally {
      rmSync(tmpCourseDir(WITH_PACK), { recursive: true, force: true })
    }
  })

  it('B0：逃生阀置位 ⇒ 真回灌路径也不起 python（本机预演/用例共用同一个阀）', async () => {
    process.env.BCITY_NO_AUTO_TASK_BUNDLE = '1'
    const course = '__ahand-valve-backfill__'
    const runDir = path.join(coreTmpCourseDir(course), 'deliver', 'seg-1')
    try {
      mkdirSync(path.join(runDir, 'it-003'), { recursive: true })
      writeFileSync(path.join(runDir, 'it-003', 'weights.json'), '{}', 'utf-8')
      const r = await backfillHubRun(course, (() => {
        throw new Error('逃生阀下不该调 python')
      }) as never)
      expect(r.ran).toBe(false)
      expect(r.note).toContain('逃生阀')
    } finally {
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('已有新鲜包 ⇒ ok=true、说「已有任务包」、**包没被挪走**', async () => {
    const p = seedPack(WITH_PACK)
    try {
      const r = await autoOfflineHandoff(WITH_PACK)
      expect(r.ok).toBe(true)
      expect(r.message).toContain('已有任务包')
      // ★ 这条是最容易写歪的地方：`launchTaskBundleExport` 会先把旧包作废，
      //   所以「有包不动」必须体现在**不调用**，而不是调用后再恢复。
      expect(existsSync(p)).toBe(true)
      expect(existsSync(stalePackDir(WITH_PACK))).toBe(false)
    } finally {
      rmSync(tmpCourseDir(WITH_PACK), { recursive: true, force: true })
    }
  })

  it('源码哨兵：started 的分支必须真的调 `launchTaskBundleExport`（决定与动作不许写岔）', () => {
    // 「决定说导、动作却没导」是本改动最隐蔽的失败态（回执会撒谎）。
    // 纯函数表已钉住决定；这里钉住**那个分支里确实有调用**。
    const src = readFileSync(
      path.join(REPO_ROOT, 'dashboard/src/server/actions/auto-offline-handoff.ts'),
      'utf-8',
    )
    expect(src).toContain('if (bundle.started) {')
    expect(src).toContain('launchTaskBundleExport(c)')
    // 只导包：一个字都不写 rl-config / console-state，也不打 hub 课程表（F1 定案的另一半）。
    // 旧写法是「写 `courses.<课>.rollout_src=run` 停本机 + 导包」——那条腿正是 Q1 要拆的耦合：
    // 本机停不停跑现在由 hub 的 hold 回答；而写意图会被回灌把自动翻的离线推回 online。
    expect(src).not.toContain('writeCourseConfigForOpen')
    expect(src).not.toContain('saveConsoleState')
    expect(src).not.toContain('/admin/courses')
  })
})

// ────────── ensureFreshTaskBundle：开课顺手保新鲜（plan/offline-segment-restart §3.3） ──────────

describe('ensureFreshTaskBundle（开课那一刻换掉已被消化的包）', () => {
  it('盘上还没有包 ⇒ 不导、**不预造**（★M4：包只为真实提出需求的云机而造）', () => {
    const r = ensureFreshTaskBundle('__ahand-never-exported__')
    expect(r.started).toBe(false)
    expect(r.note).toContain('不预造')
    expect(r.note).toContain('自动交接')
  })

  it('空课程名 ⇒ 不导（保新鲜是按课做的）', () => {
    expect(ensureFreshTaskBundle('  ').started).toBe(false)
  })

  it('已有新鲜包 ⇒ 不导、包没被挪走、指路「要重打请点导出任务包」', () => {
    const p = seedPack(WITH_PACK)
    try {
      const r = ensureFreshTaskBundle(WITH_PACK)
      expect(r.started).toBe(false)
      expect(r.note).toContain('已有任务包')
      expect(existsSync(p)).toBe(true)
      expect(existsSync(stalePackDir(WITH_PACK))).toBe(false)
    } finally {
      rmSync(tmpCourseDir(WITH_PACK), { recursive: true, force: true })
    }
  })

  it('逃生阀置位 ⇒ 不导（既有套件的兼容路径：用例不该起真子进程）', () => {
    process.env.BCITY_NO_AUTO_TASK_BUNDLE = '1'
    seedStalePack(NO_WEIGHTS)
    try {
      const r = ensureFreshTaskBundle(NO_WEIGHTS)
      expect(r.started).toBe(false)
      expect(r.note).toContain('逃生阀')
    } finally {
      rmSync(tmpCourseDir(NO_WEIGHTS), { recursive: true, force: true })
    }
  })

  it('源码哨兵：缺包早退**在**决定之前；started 分支真的调 launchTaskBundleExport', () => {
    // 「决定说导、动作却没导」是最隐蔽的失败态（回执会撒谎）；「缺包也预造」则违反 ★M4。
    const src = readFileSync(
      path.join(REPO_ROOT, 'dashboard/src/server/actions/auto-offline-handoff.ts'),
      'utf-8',
    )
    const body = src.slice(src.indexOf('export function ensureFreshTaskBundle'))
    const early = body.indexOf('if (!taskBundleInfo(c).exists)')
    expect(early).toBeGreaterThan(-1)
    expect(early).toBeLessThan(body.indexOf('autoBundleDecision('))
    expect(body).toContain('if (!bundle.started) return bundle')
    expect(body).toContain('launchTaskBundleExport(c)')
  })
})

/** 任务包文件名与路径的既有约定（本套件靠它造/删假盘，改约定要一起改）。 */
describe('假盘用的路径约定（防止约定漂移把上面几条悄悄变成空跑）', () => {
  it('包名 = task-<课>.zip，落点在 tmp/<课>/ 之下', () => {
    expect(taskBundleFileName(WITH_PACK)).toBe(`task-${WITH_PACK}.zip`)
    expect(livePack(WITH_PACK).endsWith(path.join(WITH_PACK, `task-${WITH_PACK}.zip`))).toBe(true)
  })
})

/** ★ 2026-10-04：包是否过期要看**代码**——判据与 `pack_code_zip` 的打包范围同口径。 */
describe('代码新鲜度扫描（与 python 打包器逐名对账）', () => {
  it('newestCodeMtimeMs() 在真仓里读得到一个正数（扫描没有空转）', () => {
    expect(newestCodeMtimeMs()).toBeGreaterThan(0)
  })

  it('排除表/后缀与 `pack_code_zip` 同步（读 python 源逐名对账，防两边漂开）', () => {
    const py = readFileSync(path.join(REPO_ROOT, 'nn-training/remote/hub_client.py'), 'utf-8')
    const i = py.indexOf('def pack_code_zip')
    expect(i).toBeGreaterThan(0)
    // 区间 = **整个函数体**（到下一个顶层 `def` / `async def`），不是一个写死的字数窗口。
    //
    // ★ 2026-10-15 修（原先写 `py.slice(i, i + 3000)`，并注为「规则区」）：窗口是一条**会过期**
    //   的锚——`eb26884b`（2026-10-14 离线宽度/代码权威腿）把该函数的 docstring 加长后，
    //   排除名单被推到 3000 字之外，这条用例在 HEAD 上**已经是红的**（与本次改动无关）；
    //   而它实际想判的是「这两个名单能在打包器的**规则区**里逐个找到」——那是整个函数体的事，
    //   不是某 3000 个字的事。改后判据更强（覆盖面只增不减），也不再会因注释变长而假红。
    const rest = py.slice(i)
    const end = rest.slice(1).search(/\n(?:async )?def /)
    const body = end < 0 ? rest : rest.slice(0, end + 1)
    expect(body).toContain('def pack_code_zip')
    for (const name of [...CODE_EXCLUDE_DIRS, ...CODE_EXCLUDE_FILES]) {
      expect(body).toContain(`"${name}"`)
    }
    expect(body).toContain('.py')
    expect(body).toContain('.jsonc')
  })
})

/** ★ 2026-10-10（plan/cluster-code-snapshot §4.2）：代码维度**换锚**。
 *
 *  旧判据「源文件 mtime 比包新」在集群代码快照冻结后是**错的**：改了源码但不重启 ⇒ 重导也
 *  只能拿到同一份代码，而旧判据会作废 + 重导（白烧一次分钟级导出，并让云机多等一段 404
 *  等包窗口）。新判据 = 「包里的 `code.zip` 是不是当前集群快照那一份」。
 */
describe('代码维度换锚（集群代码快照口径）', () => {
  const SNAP_SHA = 'ab'.repeat(32)

  it('快照同 sha ⇒ **不**判旧（源码 mtime 更新也没关系）', () => {
    const p = seedPack(WITH_PACK)
    try {
      const r = autoBundleDecision(
        facts({
          pack: taskBundleInfo(WITH_PACK),
          code: { kind: 'snapshot', same: true, sha: SNAP_SHA },
        }),
      )
      expect(r.started).toBe(false)
      expect(r.note).toContain('当前集群代码快照')
      expect(existsSync(p)).toBe(true)
    } finally {
      rmSync(tmpCourseDir(WITH_PACK), { recursive: true, force: true })
    }
  })

  it('快照 sha 变（重启过 hub/trainer）⇒ 作废 + 重导，且回执点名代码维度', () => {
    // 关键：包按 mtime 是**新鲜**的（seedPack 拨到未来）——只有快照口径看得见它旧了。
    const p = seedPack(WITH_PACK)
    try {
      const r = autoBundleDecision(
        facts({
          pack: taskBundleInfo(WITH_PACK),
          code: { kind: 'snapshot', same: false, sha: SNAP_SHA },
        }),
      )
      expect(r.started).toBe(true)
      expect(r.note).toContain('已过期')
      expect(r.note).toContain('集群代码快照已换')
      expect(r.note).toContain('404')
      expect(existsSync(p)).toBe(true) // 纯函数只做决定；作废是 launchTaskBundleExport 的事
    } finally {
      rmSync(tmpCourseDir(WITH_PACK), { recursive: true, force: true })
    }
  })

  it('两套口径的判据本身（纯函数）', () => {
    expect(codeDimensionStale({ kind: 'snapshot', same: true, sha: SNAP_SHA }, 1)).toBe(false)
    expect(codeDimensionStale({ kind: 'snapshot', same: false, sha: SNAP_SHA }, 1)).toBe(true)
    expect(codeDimensionStale({ kind: 'mtime', mtimeMs: 100 }, 100)).toBe(false)
    expect(codeDimensionStale({ kind: 'mtime', mtimeMs: 101 }, 100)).toBe(true)
  })
})

/** 集群快照的**只读**读取（`bundles/snapshot.ts`）与旁挂件的跨语言契约。 */
describe('集群快照读取 / 旁挂元数据', () => {
  const SNAP_ROOT = mkdtempSync(path.join(os.tmpdir(), 'bcity-snap-'))
  afterAll(() => rmSync(SNAP_ROOT, { recursive: true, force: true }))

  const writeSnapMeta = (body: string): void => {
    mkdirSync(codeSnapshotDir(SNAP_ROOT), { recursive: true })
    writeFileSync(codeSnapshotMetaPath(SNAP_ROOT), body, 'utf-8')
  }

  it('没有元数据 ⇒ null（不抛）', () => {
    expect(readClusterSnapshot(path.join(SNAP_ROOT, 'nope'))).toBeNull()
  })

  it('坏 JSON / 魔数不符 / 协议不认 ⇒ null（不猜、不迁移）', () => {
    writeSnapMeta('{ not json')
    expect(readClusterSnapshot(SNAP_ROOT)).toBeNull()
    writeSnapMeta(
      JSON.stringify({ magic: 'other', proto: CODE_SNAPSHOT_PROTO, zip: 'a', sha256: 'b' }),
    )
    expect(readClusterSnapshot(SNAP_ROOT)).toBeNull()
    writeSnapMeta(
      JSON.stringify({ magic: CODE_SNAPSHOT_MAGIC, proto: 99, zip: 'code.x.zip', sha256: 'b' }),
    )
    expect(readClusterSnapshot(SNAP_ROOT)).toBeNull()
  })

  it('合法元数据 ⇒ 字段齐；anchorAlive 按 pid 存活（廉价口径）', () => {
    writeSnapMeta(
      JSON.stringify({
        magic: CODE_SNAPSHOT_MAGIC,
        proto: CODE_SNAPSHOT_PROTO,
        zip: 'code.abababababab.zip',
        sha256: 'ab'.repeat(32),
        packed_at_epoch: 123,
        anchor: { kind: 'hub', pid: process.pid, cmdline_sha12: 'x' },
      }),
    )
    const s = readClusterSnapshot(SNAP_ROOT)
    expect(s?.sha256).toBe('ab'.repeat(32))
    expect(s?.zip).toBe('code.abababababab.zip')
    expect(s?.anchorKind).toBe('hub')
    expect(s?.anchorAlive).toBe(true)
    writeSnapMeta(
      JSON.stringify({
        magic: CODE_SNAPSHOT_MAGIC,
        proto: CODE_SNAPSHOT_PROTO,
        zip: 'code.abababababab.zip',
        sha256: 'ab'.repeat(32),
        anchor: { kind: 'hub', pid: 0, cmdline_sha12: 'x' },
      }),
    )
    expect(readClusterSnapshot(SNAP_ROOT)?.anchorAlive).toBe(false)
  })

  it('旁挂元数据：没有 / 没魔数 ⇒ null；合法 ⇒ 读得到 code_sha256', () => {
    mkdirSync(tmpCourseDir(WITH_PACK), { recursive: true })
    try {
      expect(taskBundleMeta(WITH_PACK)).toBeNull()
      writeFileSync(taskBundleMetaPath(WITH_PACK), JSON.stringify({ code_sha256: 'cd'.repeat(32) }))
      expect(taskBundleMeta(WITH_PACK)).toBeNull() // 没魔数 = 不认
      writeFileSync(
        taskBundleMetaPath(WITH_PACK),
        JSON.stringify({ magic: bundleMetaMagic, proto: 1, code_sha256: 'cd'.repeat(32), it: 3 }),
      )
      const m = taskBundleMeta(WITH_PACK)
      expect(m?.codeSha256).toBe('cd'.repeat(32))
      expect(m?.it).toBe(3)
    } finally {
      rmSync(tmpCourseDir(WITH_PACK), { recursive: true, force: true })
    }
  })

  it('导出状态三态：没有文件 = 不可知（**不是**成功）；失败带第一手原因', () => {
    // 为什么三态必须是三个值（2026-10-15，J3）：旧读面把「还没跑完」「被 kill」「失败」
    // 全折进同一个「没什么好看的」——于是失败看起来与成功无别。
    const wipe = (): void => rmSync(taskBundleStatusPath(WITH_PACK), { force: true })
    mkdirSync(tmpCourseDir(WITH_PACK), { recursive: true }) // 假课目录（收尾随 FAKE_COURSES 一起删）
    wipe()
    // ① 不可知
    expect(readTaskBundleStatus(WITH_PACK)).toBeNull()
    expect(exportStatusLine(WITH_PACK)).toContain('不可知')
    try {
      // ② 失败（原因就是那个进程手上的异常原文）
      writeFileSync(
        taskBundleStatusPath(WITH_PACK),
        JSON.stringify({
          magic: 'battle2-export-status',
          proto: 1,
          ok: false,
          exit_code: 1,
          reason: 'SystemExit: [run_rl] --export-bundle 需要 --run-iters 说明整段长度',
          iters: 0,
          run_iters: 0,
          pid: 4242,
          at: 1_800_000_000,
        }),
        'utf-8',
      )
      const st = readTaskBundleStatus(WITH_PACK)
      expect(st?.ok).toBe(false)
      expect(st?.exitCode).toBe(1)
      expect(st?.reason).toContain('--run-iters')
      expect(exportStatusLine(WITH_PACK)).toContain('上次导出失败')
      // 魔数不符 ⇒ 当它不存在（不猜、不迁移）——半写的/别的工具产的文件不得当成真状态
      writeFileSync(
        taskBundleStatusPath(WITH_PACK),
        JSON.stringify({ magic: 'other', ok: true }),
        'utf-8',
      )
      expect(readTaskBundleStatus(WITH_PACK)).toBeNull()
      // ③ 成功
      writeFileSync(
        taskBundleStatusPath(WITH_PACK),
        JSON.stringify({
          magic: 'battle2-export-status',
          ok: true,
          exit_code: 0,
          reason: '',
          at: 1_800_000_000,
          iters: 0,
        }),
        'utf-8',
      )
      expect(readTaskBundleStatus(WITH_PACK)?.ok).toBe(true)
      expect(exportStatusLine(WITH_PACK)).toContain('上次导出成功')
    } finally {
      wipe()
    }
  })

  it('running 四态：活 pid = 进行中；死 pid / 超时 = 不可知；永不冒充成功', () => {
    mkdirSync(tmpCourseDir(WITH_PACK), { recursive: true })
    const side = taskBundleStatusPath(WITH_PACK)
    try {
      writeTaskBundleStatus(WITH_PACK, {
        ok: false,
        running: true,
        reason: '导出进行中',
        pid: process.pid,
      })
      const live = readTaskBundleStatus(WITH_PACK)
      expect(live?.running).toBe(true)
      expect(live?.expired).toBe(false)
      expect(exportStatusLine(WITH_PACK)).toContain('正在导出')

      writeTaskBundleStatus(WITH_PACK, {
        ok: false,
        running: true,
        reason: '导出进行中',
        pid: 999_999_999,
      })
      const dead = readTaskBundleStatus(WITH_PACK)
      // `running` = 文件说进行中（第一手）；`expired` = 这份进行中已不可信。两者分开，
      // 渲染/判锁先看 `expired`——`running && !expired` 才算“真的在跑”。
      expect(dead?.running).toBe(true)
      expect(dead?.expired).toBe(true)
      expect(exportStatusLine(WITH_PACK)).toContain('不可知')
      expect(exportStatusLine(WITH_PACK)).not.toContain('上次导出成功')

      writeTaskBundleStatus(WITH_PACK, {
        ok: false,
        running: true,
        reason: '导出进行中',
        pid: process.pid,
        at: Date.now() / 1000 - EXPORT_TIMEOUT_MS / 1000 - 10,
      })
      expect(readTaskBundleStatus(WITH_PACK)?.expired).toBe(true)
    } finally {
      rmSync(side, { force: true })
    }
  })

  it('导出 busy 按课分键：A 在导不否决 B', () => {
    const key = taskBundleBusyKey(WITH_PACK)
    busy.add(key)
    try {
      expect(bundleFacts(WITH_PACK).busy).toBe(true)
      expect(bundleFacts(NO_WEIGHTS).busy).toBe(false)
    } finally {
      release(key)
    }
  })

  it('跨语言契约：后缀/魔数与 `remote/bundle.py` 同表（读 python 源对账）', () => {
    const py = readFileSync(path.join(REPO_ROOT, 'nn-training/remote/bundle.py'), 'utf-8')
    expect(py).toContain('BUNDLE_META_SUFFIX = ".meta.json"')
    expect(py).toContain('BUNDLE_META_MAGIC = "battle2-task-bundle-meta"')
    // 落点规则必须是「包路径 + 后缀」（两边都得能从包路径推出旁挂件路径）
    expect(py).toContain('def bundle_meta_path')
    expect(py).toContain('Path(str(out_zip) + BUNDLE_META_SUFFIX)')
    // 导出**成败状态**旁挂件（2026-10-15，plan/offline-open-ended-iters-and-loud-export §3.3 J3）：
    // 导出是 detached 子进程（控制台拿不到退出码）⇒ 成败只能由那个进程自己落盘。
    expect(py).toContain('EXPORT_STATUS_NAME = "export-status.json"')
    expect(py).toContain('EXPORT_STATUS_MAGIC = "battle2-export-status"')
    expect(py).toContain('EXPORT_STATUS_PROTO = 1')
    expect(taskBundleStatusProto).toBe(1)
    expect(py).toContain('def export_status_path')
    expect(taskBundleStatusFileName).toBe('export-status.json')
    expect(taskBundleStatusMagic).toBe('battle2-export-status')
    // 落点必须与 python 同源（python: `export_status_path(Path(out).parent)`；这里是包的同目录）
    expect(taskBundleStatusPath(WITH_PACK)).toBe(
      path.join(path.dirname(taskBundlePath(WITH_PACK)), 'export-status.json'),
    )
    // 控制台的「当前快照 sha」读的是同一份元数据（python: code_snapshot.SNAPSHOT_MAGIC）
    const csSrc = readFileSync(path.join(REPO_ROOT, 'nn-training/remote/code_snapshot.py'), 'utf-8')
    expect(csSrc).toContain('SNAPSHOT_MAGIC = "battle2-code-snapshot"')
    expect(csSrc).toContain('META_NAME = "snapshot.json"')
    expect(csSrc).toContain('SNAPSHOT_DIR_NAME = ".code-snapshot"')
  })
})
