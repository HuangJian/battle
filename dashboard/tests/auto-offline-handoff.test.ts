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
import { REPO_ROOT } from '../src/core/paths'

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
  codeDimensionStale,
} from '../src/server/actions/auto-offline-handoff'
import {
  CODE_EXCLUDE_DIRS,
  CODE_EXCLUDE_FILES,
  CODE_SNAPSHOT_MAGIC,
  CODE_SNAPSHOT_PROTO,
  bundleMetaMagic,
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
} from '../src/server/bundles'

/** 假课程名：进的是仓 `tmp/` 之下，必须一眼看出是测试残留（收尾删整目录）。 */
const WITH_PACK = '__ahand-with-pack__'
const NO_WEIGHTS = '__ahand-no-weights__'
const FAKE_COURSES = [WITH_PACK, NO_WEIGHTS]

/** 包 mtime 的两个人造档位（**秒**，`utimesSync` 的单位）：未来 = 刚导出；过去 = 过期。 */
const future = (Date.now() + 60_000) / 1000
const past = (Date.now() - 3_600_000) / 1000

const livePack = (course: string): string => taskBundlePath(course)
const tmpCourseDir = (course: string): string => path.join(REPO_ROOT, 'tmp', course)
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
    const body = py.slice(i, i + 3000) // 打包器的规则区（含「包含/排除」注释与名单）
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

  it('跨语言契约：后缀/魔数与 `remote/bundle.py` 同表（读 python 源对账）', () => {
    const py = readFileSync(path.join(REPO_ROOT, 'nn-training/remote/bundle.py'), 'utf-8')
    expect(py).toContain('BUNDLE_META_SUFFIX = ".meta.json"')
    expect(py).toContain('BUNDLE_META_MAGIC = "battle2-task-bundle-meta"')
    // 落点规则必须是「包路径 + 后缀」（两边都得能从包路径推出旁挂件路径）
    expect(py).toContain('def bundle_meta_path')
    expect(py).toContain('Path(str(out_zip) + BUNDLE_META_SUFFIX)')
    // 控制台的「当前快照 sha」读的是同一份元数据（python: code_snapshot.SNAPSHOT_MAGIC）
    const csSrc = readFileSync(path.join(REPO_ROOT, 'nn-training/remote/code_snapshot.py'), 'utf-8')
    expect(csSrc).toContain('SNAPSHOT_MAGIC = "battle2-code-snapshot"')
    expect(csSrc).toContain('META_NAME = "snapshot.json"')
    expect(csSrc).toContain('SNAPSHOT_DIR_NAME = ".code-snapshot"')
  })
})
