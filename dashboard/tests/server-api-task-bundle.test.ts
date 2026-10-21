/**
 * server-api-task-bundle.test.ts — 任务包导出 / 训练产物导入（`task-<课程>.zip` /
 * `deliver-<课程>.zip`）。
 *
 * 分层：src/server/bundles/** + src/server/api/route.ts 的 exportTaskBundle 动作
 *
 * 两侧各测自己那一半（不叠第二层端到端）：
 *   * python 一侧的三道门（zip-slip / 拿错包 / 轮次发现）由
 *     `nn-training/tests/remote/test_deliver_zip.py` 钉死；
 *   * 这里钉**控制台这一侧**：argv 形状、产出文件信息、拒启理由、上传体的门、
 *     与 python 共享的跨语言常量（直接读 python 源码对账——改一边忘另一边会红）、
 *     以及「导入成功后要不要接着起评估」。
 *
 * 一律不真跑 python / 不真起 evalA（前者慢且依赖 venv，后者会真跑几十局游戏）。
 */

import { describe, expect, it } from 'bun:test'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'fs'
import os from 'os'
import path from 'path'
import { REPO_ROOT, tmpCourseDir } from '../src/core/paths'
import {
  DELIVER_MAX_BYTES,
  deliverFileNamePrefix,
  deliverImportJsonMark,
  deliverImportRoot,
  deliverUploadDir,
  importDeliverZip,
  launchPostImportEval,
  parseDeliverImportJson,
  pruneDeliverUploads,
  handleDeliverUpload,
  validateDeliverUpload,
  backfillHubRun,
  endItReachedForRun,
  landedHubRuns,
  segmentNotCourseEnd,
  segEndKindForRun,
  writeRunCompleteWithPython,
  type DeliverImportPayload,
  type DeliverUploadDeps,
  type DeliverZipRunner,
} from '../src/server/bundles'
import {
  exportBusyReason,
  exportGuardReason,
  exportPidState,
  exportStatusLine,
  RUN_MAX_ITERS_HARD_CAP,
  invalidateTaskBundle,
  latestStalePack,
  launchTaskBundleExport,
  readTaskBundleStatus,
  restorePackIfMissing,
  restoreTaskBundle,
  stalePackDir,
  taskBundleArgs,
  taskBundleBusyKey,
  taskBundleFileName,
  taskBundleInfo,
  taskBundleLogPath,
  taskBundlePath,
  watchExportExit,
  writeTaskBundleStatus,
} from '../src/server/bundles/export'
import { busy, release } from '../src/server/actions/result'
import { killPid, pidAlive } from '../src/core/net'

const COURSE = 'x1-rebirth-a2'

/** 测试沙箱（`destRoot` + `logFile`）：`importDeliverZip` 缺省会写真实 `tmp/<课程>/`
 *  （生产语义）——测试必须指到临时目录，否则一次 `bun test` 就在仓里留下 deliver/ 与
 *  一份追写过的 deliver-import.log。这里用 `mkdtempSync` 造一次性目录。 */
function sandboxPaths(): { destRoot: string; logFile: string } {
  const d = mkdtempSync(path.join(os.tmpdir(), 'bcity-bundle-'))
  return { destRoot: path.join(d, 'deliver'), logFile: path.join(d, 'import.log') }
}

// ────────────────────────── 跨语言常量（改一边忘另一边 → 红） ──────────────────────────

describe('与 python 共享的跨语言常量', () => {
  const py = readFileSync(path.join(REPO_ROOT, 'nn-training', 'remote', 'deliver_zip.py'), 'utf8')

  it('IMPORT_JSON_MARK 与 remote/deliver_zip.py 逐字节相同', () => {
    // 两边写岔了是**静默**的：导入器打的那行没人认得出 → 控制台报「导入失败」，
    // 而真因（解包失败？标记改名？）不在这一侧。
    expect(py).toContain(`IMPORT_JSON_MARK = "${deliverImportJsonMark}"`)
  })

  it('DELIVER_PREFIX 与 python 一致（文件名约定是同一条契约）', () => {
    expect(py).toContain(`DELIVER_PREFIX = "${deliverFileNamePrefix}"`)
  })
})

// ────────────────────────── 导出侧（argv / 路径 / 拒启理由） ──────────────────────────

describe('任务包导出', () => {
  it('argv 走 trainer 自己的 --export-bundle（不在这侧重造包内容）', () => {
    const args = taskBundleArgs(COURSE)
    expect(args[0]).toBe('nn-training/trainer/run_rl.py')
    expect(args).toContain(COURSE)
    // 整段剩余：-1（跑到课程末尾）；★ §3 起 argv 不再带 --ppo（单一 PPO 路径）
    expect(args[args.indexOf('--run-iters') + 1]).toBe('-1')
    expect(args).not.toContain('--ppo')
    expect(args[args.indexOf('--export-bundle') + 1]).toBe(taskBundlePath(COURSE))
  })

  it('产出路径/文件名/日志都在课程目录下（人一眼能找到）', () => {
    expect(taskBundleFileName(COURSE)).toBe(`task-${COURSE}.zip`)
    expect(taskBundlePath(COURSE)).toBe(path.join(tmpCourseDir(COURSE), `task-${COURSE}.zip`))
    expect(taskBundleLogPath(COURSE).endsWith(path.join(COURSE, 'export-bundle.log'))).toBe(true)
  })

  it('没导出过时 info.exists=false（行内据此显示「未导出」）', () => {
    const info = taskBundleInfo('__never-exported-course__')
    expect(info.exists).toBe(false)
    expect(info.bytes).toBe(0)
    expect(info.name).toBe('task-__never-exported-course__.zip')
  })

  it('拒启理由只剩「缺起点权重」（★2026-09-22：训练运行中也要允许随时导出——锁不再挡）', () => {
    expect(exportGuardReason({ weightsExists: true })).toBeNull()
    const noW = exportGuardReason({ weightsExists: false })
    expect(noW).toContain('weights.json')
  })

  // ────────────────────── J4（D1）：静态拒因在 spawn 之前判 ──────────────────────

  it('J4 静态预判：无剩余轮次 / 轮数 > 硬上界 都**在点击那一刻**被拒（不 spawn、不烧快照）', () => {
    // 现场（2026-10-14 用户报障）：`iters=2000` 点下去 → spawn → 烧一次 code-snapshot +
    // 一次 git push + 约 150s 的轮内重试，最后才在日志里报「拒发」。静态判据把这三笔代价
    // 换成即时拒绝（真闸仍在 python 侧，预判只能多拦、不得放行一份必然失败的任务）。
    expect(
      exportGuardReason({ weightsExists: true, declaredIters: 2000, lastCompletedIt: 0 }),
    ).toContain('硬上界')
    expect(
      exportGuardReason({ weightsExists: true, declaredIters: 10, lastCompletedIt: 10 }),
    ).toContain('没有剩余轮次')
    expect(
      exportGuardReason({ weightsExists: true, declaredIters: 10, lastCompletedIt: 11 }),
    ).toContain('没有剩余轮次')
    // 边界：恰剩一格不拒（锚点= `last_completed`，与 python 的 `it-1` 同一班，差一就漏掉一轮）
    expect(
      exportGuardReason({ weightsExists: true, declaredIters: 10, lastCompletedIt: 9 }),
    ).toBeNull()
    expect(
      exportGuardReason({ weightsExists: true, declaredIters: 501, lastCompletedIt: 0 }),
    ).toContain('硬上界')
    expect(
      exportGuardReason({ weightsExists: true, declaredIters: 501, lastCompletedIt: 1 }),
    ).toBeNull()
  })

  it('J4 静态预判：课程无限（iters<=0）与「不可知」都**不拒**（J1 后无限是合法的一档）', () => {
    expect(
      exportGuardReason({ weightsExists: true, declaredIters: 0, lastCompletedIt: 9999 }),
    ).toBeNull()
    expect(
      exportGuardReason({ weightsExists: true, declaredIters: -1, lastCompletedIt: 9999 }),
    ).toBeNull()
    // 课程文件读不到 / 没声明 ⇒ 不可知，不判（旧课程与 BC 课程的兼容面）
    expect(
      exportGuardReason({ weightsExists: true, declaredIters: null, lastCompletedIt: 0 }),
    ).toBeNull()
    expect(exportGuardReason({ weightsExists: true })).toBeNull()
  })

  it('跨语言常量对账：TS 的段长上界 == python 的 RUN_MAX_ITERS_HARD_CAP', () => {
    // 两边漂开 = 「控制台说能导、真闸拒发」（或者反过来：预放行一份必然失败的任务）。
    // 与 CODE_EXCLUDE_DIRS 对账 pack_code_zip 同一手法：读 python 源逐字对。
    const py = readFileSync(path.join(REPO_ROOT, 'nn-training/common/manifest.py'), 'utf8')
    expect(py).toContain(`RUN_MAX_ITERS_HARD_CAP = ${RUN_MAX_ITERS_HARD_CAP}`)
  })

  it('旧任务包作废：挪进 stale-packs（不是删除），盘上原路径不再有包可递', () => {
    // 用户 2026-09-22：「重启离线课程时，需要把最新代码重新打进任务包，因为代码可能已经
    // 发生了变化」。作废这一步是必需的——hub 递的是**盘上那一刻的文件**，不作废的话云机在
    // 导出窗口（几分钟）里探到的还是旧代码的包，而它会拿旧代码跑完整段、看起来完全正常。
    const course = '__stale-pack-course__'
    const live = path.join(tmpCourseDir(course), taskBundleFileName(course))
    const stale = stalePackDir(course)
    try {
      mkdirSync(path.dirname(live), { recursive: true })
      writeFileSync(live, 'OLD-CODE', { encoding: 'utf-8', flag: 'w' })
      // 没有包时：不作废、不报错（云机会等新导出的那份）
      expect(invalidateTaskBundle('__no-such-pack-course__').invalidated).toBe(false)
      const r = invalidateTaskBundle(course)
      expect(r.invalidated).toBe(true)
      expect(existsSync(live)).toBe(false) // hub /offline/task-pack 从此 404（云机会等新包）
      expect(r.archived.startsWith(stale)).toBe(true)
      expect(readFileSync(r.archived, 'utf-8')).toBe('OLD-CODE') // 不丢件：导出失败时它还是唯一能跑的
      // 幂等：再作废一次 = 没有包可作废（不抛）
      expect(invalidateTaskBundle(course).invalidated).toBe(false)
    } finally {
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('作废 + 导出没产出 ⇒ 旧包被恢复（作废不能把课变成「没有包」）', () => {
    // 「先作废、再导出」有个必须堵死的前提：导出**失败**时不能留下「这门课没有包」——
    // 旧包是此刻唯一还能跑的东西（用户点「训练」就是要立刻有东西能跑）。
    const course = '__stale-restore-course__'
    const live = path.join(tmpCourseDir(course), taskBundleFileName(course))
    try {
      mkdirSync(path.dirname(live), { recursive: true })
      writeFileSync(live, 'OLD-CODE', { encoding: 'utf-8', flag: 'w' })
      const r = invalidateTaskBundle(course)
      expect(r.invalidated).toBe(true)
      expect(existsSync(live)).toBe(false)
      // 无归档的课：安全返回 null（不瞎恢复）
      expect(latestStalePack('__no-such-stale-course__')).toBe(null)
      expect(restorePackIfMissing('__no-such-stale-course__')).toBe(null)
      // 导出没产出新包（线上路径空）⇒ 兜底把归档放回线上
      expect(restorePackIfMissing(course)).toBeTruthy()
      expect(existsSync(live)).toBe(true)
      expect(readFileSync(live, 'utf-8')).toBe('OLD-CODE')
      // 线上已有包 ⇒ 不动归档（不覆盖刚导出的新包）
      expect(restorePackIfMissing(course)).toBe(null)
      // 显式恢复：线上已有包时不覆盖
      expect(restoreTaskBundle(course, latestStalePack(course) ?? '').restored).toBe(false)
    } finally {
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('上一次导出还在跑 → 拒启（互斥键由子进程退出释放，不在请求里删）', () => {
    // 这一条盯的是**互斥键的生命期**：若键在请求返回时就 `delete`（最初的写法），
    // 第二次点击会起第二个 run_rl 去抢同一门课的锁——锁是 python 侧的，控制台只
    // 会看到「启动成功」而日志里多一段锁冲突。键的释放路径是轮询 pid，见
    // `watchExportExit`；这里只需要它**在键存在时拒启**，且理由先于业务校验。
    const key = taskBundleBusyKey(COURSE)
    expect(key).toBe(`bundle:export:${COURSE}`)
    busy.add(key)
    try {
      const r = launchTaskBundleExport(COURSE)
      expect(r.ok).toBe(false)
      expect(r.message).toContain('还没跑完')
    } finally {
      release(key)
    }
  })

  it('两门课各一把锁：A 在导不阻断 B', () => {
    const key = taskBundleBusyKey('__export-course-a__')
    busy.add(key)
    try {
      expect(exportBusyReason('__export-course-a__')).toContain('还没跑完')
      expect(exportBusyReason('__export-course-b__')).toBeNull()
    } finally {
      release(key)
    }
  })

  it('同课 running 状态（活 pid）挡住第二次；死 pid 放行', () => {
    const course = '__running-export-course__'
    try {
      writeTaskBundleStatus(course, {
        ok: false,
        running: true,
        reason: '导出进行中',
        pid: process.pid,
      })
      expect(exportBusyReason(course)).toContain('还在跑')
      expect(exportStatusLine(course)).toContain('正在导出')

      writeTaskBundleStatus(course, {
        ok: false,
        running: true,
        reason: '导出进行中',
        pid: 999_999_999,
      })
      expect(exportBusyReason(course)).toBeNull()
      expect(readTaskBundleStatus(course)?.expired).toBe(true)
      expect(exportStatusLine(course)).toContain('不可知')
      expect(exportStatusLine(course)).not.toContain('上次导出成功')
    } finally {
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('状态早于包作废（stale 文件名戳按 UTC）⇒ 不可知，不说成功', () => {
    const course = '__stale-status-course__'
    const live = taskBundlePath(course)
    try {
      mkdirSync(path.dirname(live), { recursive: true })
      const stale = path.join(
        stalePackDir(course),
        `${taskBundleFileName(course)}.stale-2026-10-21T05-53-22`,
      )
      mkdirSync(path.dirname(stale), { recursive: true })
      writeFileSync(stale, 'OLD-CODE', 'utf-8')
      // 11:41:32 (+08) = 03:41:32Z；早于 13:53 (+08) = 05:53:22Z 的作废时刻。
      writeTaskBundleStatus(course, {
        ok: true,
        running: false,
        reason: '',
        at: Date.UTC(2026, 9, 21, 3, 41, 32) / 1000,
      })
      expect(readTaskBundleStatus(course)?.stale).toBe(true)
      expect(exportStatusLine(course)).toContain('不可知')
      expect(exportStatusLine(course)).not.toContain('上次导出成功')
    } finally {
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('PID 被别的进程复用 ⇒ 不当「还在导」：立刻放行，并说不可知', () => {
    // 用户口径（2026-10-21）：「等 30 分钟不可接受」。running 状态 + pid 活着但命令行不是本课导出
    // ⇒ 那个 pid 已换主，这份 running 描述的是死进程 ⇒ 点一下就能重导，不必等超时。
    const course = '__reused-pid-course__'
    try {
      writeTaskBundleStatus(course, {
        ok: false,
        running: true,
        reason: '导出进行中',
        pid: process.pid,
        at: Date.now() / 1000 - 60, // 已写下 60s ⇒ 过了「刚写入不验」的门
      })
      // 本测试进程的命令行里没有 run_rl.py / --export-bundle。
      expect(exportPidState(process.pid, course)).toBe('mismatch')
      const st = readTaskBundleStatus(course)
      expect(st?.running).toBe(true)
      expect(st?.expired).toBe(true)
      expect(exportBusyReason(course)).toBeNull()
      expect(exportStatusLine(course)).toContain('不可知')
      expect(exportStatusLine(course)).not.toContain('上次导出成功')
    } finally {
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('身份三态：验不明（unknown）不放行；刚写入的 running（<5s）根本不去验', () => {
    const course = '__unknown-pid-course__'
    expect(exportPidState(0, course, () => 'whatever')).toBe('unknown')
    expect(exportPidState(1, course, () => '')).toBe('unknown')
    expect(exportPidState(1, course, () => '/usr/bin/python run_rl.py --course x')).toBe('mismatch')
    expect(
      exportPidState(1, course, () => `python run_rl.py --export-bundle ${taskBundlePath(course)}`),
    ).toBe('match')
    try {
      writeTaskBundleStatus(course, {
        ok: false,
        running: true,
        reason: '导出进行中',
        pid: process.pid,
        at: Date.now() / 1000 - 60,
      })
      // 验不出来 ⇒ 保守：仍算进行中（宁可少放一次，不让同课两个导出并发写同一个 .tmp）。
      const unknown = readTaskBundleStatus(course, { pidState: () => 'unknown' })
      expect(unknown?.expired).toBe(false)
      expect(unknown?.running).toBe(true)
      expect(exportBusyReason(course)).toBeNull() // 真读面：本测试进程恰好是 mismatch

      // 刚写下的 running 描述的就是刚启动的进程：不读进程表（抛错证明没被调到）。
      writeTaskBundleStatus(course, {
        ok: false,
        running: true,
        reason: '导出进行中',
        pid: process.pid,
      })
      const fresh = readTaskBundleStatus(course, {
        pidState: () => {
          throw new Error('刚写入的状态不该去验身份')
        },
      })
      expect(fresh?.running).toBe(true)
      expect(fresh?.expired).toBe(false)
    } finally {
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('超时 ⇒ 杀进程 + release + 恢复旧包（三个副作用都要发生）', async () => {
    const course = '__timeout-export-course__'
    const live = taskBundlePath(course)
    const key = taskBundleBusyKey(course)
    const child = Bun.spawn([process.execPath, '-e', 'setTimeout(() => {}, 60000)'])
    const pid = child.pid
    try {
      mkdirSync(path.dirname(live), { recursive: true })
      writeFileSync(live, 'OLD-CODE', 'utf-8')
      expect(invalidateTaskBundle(course).invalidated).toBe(true)
      busy.add(key)
      await watchExportExit(pid, course, {
        timeoutMs: 50,
        pollMs: 5,
        matchesPid: () => true,
        startedAtMs: Date.now(),
      })
      expect(pidAlive(pid)).toBe(false) // 杀进程
      expect(busy.has(key)).toBe(false) // release
      expect(existsSync(live)).toBe(true) // 恢复旧包
      expect(readFileSync(live, 'utf-8')).toBe('OLD-CODE')
      const st = readTaskBundleStatus(course)
      expect(st?.running).toBe(false)
      expect(st?.reason).toContain('超时')
    } finally {
      if (pidAlive(pid)) await killPid(pid)
      release(key)
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('包已产出（看门狗后到）⇒ 不把成功改写成失败', async () => {
    const course = '__produced-export-course__'
    const live = taskBundlePath(course)
    const key = taskBundleBusyKey(course)
    try {
      mkdirSync(path.dirname(live), { recursive: true })
      writeFileSync(live, 'NEW-CODE', 'utf-8') // 本次产出的包（mtime = 现在）
      writeTaskBundleStatus(course, {
        ok: false,
        running: true,
        reason: '导出进行中',
        pid: 999_999_999, // 进程已退出（死 pid）：循环立即结束，走「产出后收尾」
      })
      busy.add(key)
      await watchExportExit(999_999_999, course, {
        timeoutMs: 50,
        pollMs: 5,
        startedAtMs: Date.now() - 60_000,
      })
      const st = readTaskBundleStatus(course)
      expect(st?.ok).toBe(true)
      expect(st?.running).toBe(false)
      expect(st?.reason).toBe('')
      expect(readFileSync(live, 'utf-8')).toBe('NEW-CODE')
      expect(busy.has(key)).toBe(false)
    } finally {
      release(key)
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('进程退出但没落状态、也没产出包 ⇒ 写失败终态（不留 running 幻觉）', async () => {
    const course = '__crashed-export-course__'
    const key = taskBundleBusyKey(course)
    try {
      mkdirSync(tmpCourseDir(course), { recursive: true })
      writeTaskBundleStatus(course, {
        ok: false,
        running: true,
        reason: '导出进行中',
        pid: 999_999_999,
      })
      busy.add(key)
      await watchExportExit(999_999_999, course, {
        timeoutMs: 50,
        pollMs: 5,
        startedAtMs: Date.now() - 60_000,
      })
      const st = readTaskBundleStatus(course)
      expect(st?.running).toBe(false)
      expect(st?.ok).toBe(false)
      expect(st?.expired).toBe(false)
      expect(st?.reason).toContain('没有落状态')
      expect(exportStatusLine(course)).toContain('上次导出失败')
    } finally {
      release(key)
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('超时但 pid 身份不匹配 ⇒ 不杀，只 release', async () => {
    const course = '__unverified-export-course__'
    const key = taskBundleBusyKey(course)
    const child = Bun.spawn([process.execPath, '-e', 'setTimeout(() => {}, 60000)'])
    const pid = child.pid
    try {
      busy.add(key)
      await watchExportExit(pid, course, {
        timeoutMs: 30,
        pollMs: 5,
        matchesPid: () => false,
        startedAtMs: Date.now(),
      })
      expect(pidAlive(pid)).toBe(true) // 没杀
      expect(busy.has(key)).toBe(false) // 但锁要放
      expect(readTaskBundleStatus(course)?.reason).toContain('超时')
    } finally {
      if (pidAlive(pid)) await killPid(pid)
      release(key)
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })
})

// ────────────────────────── 导入侧（上传门 / 结果转述 / 自动评估） ──────────────────────────

describe('产物导入', () => {
  it('文件名必须与当前课程一致（拿错课产物是不可逆的读数错位）', () => {
    expect(validateDeliverUpload(`deliver-${COURSE}.zip`, COURSE)).toBeNull()
    expect(validateDeliverUpload('deliver-other.zip', COURSE)).toContain('other')
    expect(validateDeliverUpload('artifact.zip', COURSE)).toBeNull() // 不按习惯命名 → 不猜
    expect(validateDeliverUpload('deliver-x1.zip', '')).toContain('课程')
    expect(validateDeliverUpload('deliver-x1.tar.gz', COURSE)).toContain('.zip')
  })

  it('导出名的 `@<run>-it<N>` 附注不当课程（同名包留历史）；课程段仍要比对', () => {
    // 规则两处同一条（python：`deliver_zip.course_from_filename`）——单边改就是静默错位。
    expect(validateDeliverUpload(`deliver-${COURSE}@seg-1-it40.zip`, COURSE)).toBeNull()
    const bad = validateDeliverUpload('deliver-other@seg-1-it40.zip', COURSE)
    expect(bad).toContain('other')
    expect(bad).not.toContain('seg-1-it40') // 报错里指到的那门课就是课程段（否则人照着切也切不对）
  })

  it('落位计数写进结论（重导同名 run 时「新增 0 轮」与「导入了 5 轮」不能长得一样）', async () => {
    const runner = (() => ({
      code: 0,
      stdout: `${deliverImportJsonMark}${JSON.stringify({
        run_id: 'r1',
        dir: deliverImportRoot(COURSE) + '/r1',
        iters: [1, 2],
        last_it: 2,
        ckpt: deliverImportRoot(COURSE) + '/r1/it-002/weights.json',
        state: 'complete',
        rows: 2,
        course: COURSE,
        source_zip: '/x/z.zip',
        bytes: 3,
        kept_rounds: 1,
        skipped_rounds: 3,
        healed_files: 2,
      })}`,
      stderr: '',
      timeout: false,
    })) as unknown as Parameters<typeof importDeliverZip>[1]
    const r = await importDeliverZip(
      { course: COURSE, zipPath: '/x/deliver.zip', ...sandboxPaths() },
      runner,
    )
    expect(r.message).toContain('新增 1 轮、跳过 3 轮')
    expect(r.message).toContain('先到者为准')
    expect(r.message).toContain('补齐缺件 2 件')
  })

  it('体检报告在成功路径上也看得见（全绿 / 未过项 / 半段）', async () => {
    const make = (checks: Array<{ id: string; ok: boolean; detail: string }>, partial = false) =>
      (() => ({
        code: 0,
        stdout: `${deliverImportJsonMark}${JSON.stringify({
          run_id: 'r1',
          dir: deliverImportRoot(COURSE) + '/r1',
          iters: [1, 2],
          last_it: 2,
          ckpt: deliverImportRoot(COURSE) + '/r1/it-002/weights.json',
          state: 'complete',
          rows: 2,
          course: COURSE,
          source_zip: '/x/z.zip',
          bytes: 3,
          checks,
          checks_ok: checks.every((c) => c.ok),
          partial,
        })}`,
        stderr: '',
        timeout: false,
      })) as unknown as Parameters<typeof importDeliverZip>[1]
    const green = [
      { id: 'shape', ok: true, detail: 'it1 → it2（2 轮）' },
      { id: 'continuity', ok: true, detail: '连续' },
    ]
    const okRun = await importDeliverZip(
      { course: COURSE, zipPath: '/x/deliver.zip', ...sandboxPaths() },
      make(green),
    )
    expect(okRun.message).toContain('体检 2/2 全绿')
    // 未过项要逐项点名（「导进去了但读数不可信」是这张表存在的理由）
    const badRun = await importDeliverZip(
      { course: COURSE, zipPath: '/x/deliver.zip', ...sandboxPaths() },
      make([...green, { id: 'state', ok: false, detail: '半段快照' }], true),
    )
    expect(badRun.message).toContain('体检未过：state')
    expect(badRun.message).toContain('半段')
    // 整屏展开（面板读 detail）
    const r = await importDeliverZip(
      { course: COURSE, zipPath: '/x/deliver.zip', ...sandboxPaths() },
      make(green),
    )
    expect(r.payload?.checks?.length).toBe(2)
  })

  it('解析机器可读结果行（人读日志在前，标记行在末）', () => {
    const payload = {
      run_id: 'r1',
      dir: '/x/r1',
      iters: [1, 2],
      last_it: 2,
      ckpt: '/x/r1/it-002/weights.json',
      state: 'complete',
      rows: 2,
      course: COURSE,
      source_zip: '/x/deliver.zip',
      bytes: 10,
    }
    const stdout = `[deliver] 导入完成：…\n[deliver] 末轮权重：…\n${deliverImportJsonMark}${JSON.stringify(payload)}\n`
    expect(parseDeliverImportJson(stdout)?.last_it).toBe(2)
    expect(parseDeliverImportJson('[deliver] 只有人读日志\n')).toBeNull()
    expect(parseDeliverImportJson(`${deliverImportJsonMark}{坏 json`)).toBeNull()
  })

  it('python 导入器 argv：--zip/--dest/--course，dest 是课程下的 deliver 根', async () => {
    const calls: Array<{ module: string; args: string[] }> = []
    const runner = ((module: string, args: string[]) => {
      calls.push({ module, args })
      return {
        code: 0,
        stdout: `${deliverImportJsonMark}${JSON.stringify({
          run_id: 'r1',
          dir: deliverImportRoot(COURSE) + '/r1',
          iters: [3, 4],
          last_it: 4,
          ckpt: deliverImportRoot(COURSE) + '/r1/it-004/weights.json',
          state: 'complete',
          rows: 2,
          course: COURSE,
          source_zip: '/x/z.zip',
          bytes: 3,
        })}`,
        stderr: '',
        timeout: false,
      }
    }) as unknown as Parameters<typeof importDeliverZip>[1]
    const r = await importDeliverZip(
      { course: COURSE, zipPath: '/x/deliver.zip', ...sandboxPaths() },
      runner,
    )
    expect(r.ok).toBe(true)
    expect(r.payload?.last_it).toBe(4)
    expect(calls).toHaveLength(1)
    expect(calls[0].module).toBe('remote.deliver_zip')
    // argv 形状 = 「zip / 落地根 / 课程」；落地根**缺省**就是课程目录下的 deliver/
    // （生产语义——上面注入的 destRoot 只是测试沙箱）。
    expect(calls[0].args.slice(0, 2)).toEqual(['--zip', '/x/deliver.zip'])
    expect(calls[0].args[2]).toBe('--dest')
    expect(calls[0].args[4]).toBe('--course')
    expect(calls[0].args[5]).toBe(COURSE)
    expect(deliverImportRoot(COURSE)).toBe(path.join(tmpCourseDir(COURSE), 'deliver'))
  })

  it('★ DoD-5a：8+ 类坏包各跑一次 —— 结论必须是可执行的人话，不丢裸异常给人', async () => {
    // 现场形状（2026-10-16）：`导入失败：PermissionError: [WinError 5] Access is denied: 'D:\gi…`
    // ——路径被 UI 截断、无任何指引。这张表把「挂在哪道门 + 下一步」写死，且**不许**把裸
    // `WinError`/`Traceback`/`PermissionError` 当结论上屏（原文只进 detail）。
    const cases: Array<[string, string, string]> = [
      [
        'WinError 5（句柄占住）',
        "[deliver] PermissionError: [WinError 5] Access is denied: 'D:\\github\\battle2\\tmp'",
        '占住',
      ],
      [
        'WinError 32（文件被占用）',
        '[deliver] OSError: [WinError 32] The process cannot access the file',
        '占住',
      ],
      [
        'WinError 183（目标已存在）',
        '[deliver] FileExistsError: [WinError 183] Cannot create a file',
        '已存在',
      ],
      [
        '不是 zip（文件被截断）',
        '[deliver] 不是合法的 zip: deliver-x.zip: File is not a zip file',
        '不是 zip',
      ],
      [
        '拿错成任务包',
        '[deliver] 这是**任务包**（含 BUNDLE_INDEX），不是训练产物——任务包给云机跑',
        '任务包',
      ],
      [
        '不是训练产物（没有逐轮权重）',
        "[deliver] 这个包里没有 it-NNN/weights.json（或压缩版 weights.delta）——不是训练产物 zip（包内顶层: ['README.txt', 'nope']）",
        '逐轮权重',
      ],
      [
        '课程对不上',
        '[deliver] 文件名里的课程（c6-gae）与控制台当前课程（x1-rebirth-a2）不一致——切到 c6-gae 再导',
        '切到 c6-gae 再导',
      ],
      ['磁盘满', '[deliver] OSError: [Errno 28] No space left on device', '盘满'],
    ]
    for (const [name, stderr, want] of cases) {
      const runner = (() => ({
        code: 2,
        stdout: '',
        stderr: `${stderr}\n`,
        timeout: false,
      })) as unknown as Parameters<typeof importDeliverZip>[1]
      const r = await importDeliverZip(
        { course: COURSE, zipPath: '/x/bad.zip', ...sandboxPaths() },
        runner,
      )
      expect(r.ok, name).toBe(false)
      expect(r.message, name).toContain(want)
      expect(r.message, `${name}：结论里不许出现裸异常`).not.toMatch(
        /WinError|Traceback|PermissionError|BadZipFile/,
      )
      const detail = (r.detail ?? []).join('\n')
      expect(detail, name).toContain('挂在的门：')
      expect(detail, name).toContain('完整日志（绝对路径）：')
    }
  })

  it('超时不是「导入失败」：同一张表，另一种结论句（并说清已导入到哪）', async () => {
    const runner = (() => ({
      code: null,
      stdout: '[deliver] 导入完成：…（盘上 3 轮）\n',
      stderr: '',
      timeout: true,
    })) as unknown as Parameters<typeof importDeliverZip>[1]
    const r = await importDeliverZip(
      { course: COURSE, zipPath: '/x/big.zip', ...sandboxPaths() },
      runner,
    )
    expect(r.ok).toBe(false)
    expect(r.message).toContain('超时')
    expect((r.detail ?? []).join('\n')).toContain('落位是逐轮的')
  })

  it('未分类的异常也有出路：日志绝对路径 + 末尾八行（人不怕未知，怕看不见）', async () => {
    const runner = (() => ({
      code: 3,
      stdout: '',
      stderr: 'something totally unknown happened\n',
      timeout: false,
    })) as unknown as Parameters<typeof importDeliverZip>[1]
    const r = await importDeliverZip(
      { course: COURSE, zipPath: '/x/x.zip', ...sandboxPaths() },
      runner,
    )
    const detail = (r.detail ?? []).join('\n')
    expect(r.message).toContain('导入失败')
    expect(detail).toContain('something totally unknown happened')
    expect(detail).toContain('完整日志（绝对路径）：')
  })

  it('python 报错 → 控制台把它的最后一行转成人话（不吞掉真因）', async () => {
    const runner = (() => ({
      code: 2,
      stdout: '',
      stderr: '[deliver] 导入失败：这个包里没有 it-NNN/weights.json——不是训练产物 zip\n',
      timeout: false,
    })) as unknown as Parameters<typeof importDeliverZip>[1]
    const r = await importDeliverZip(
      { course: COURSE, zipPath: '/x/bad.zip', ...sandboxPaths() },
      runner,
    )
    expect(r.ok).toBe(false)
    expect(r.message).toContain('weights.json')
  })

  // 真造一份「末轮权重」：`launchPostImportEval` 的存在性门是生产逻辑的一部分
  // （目录被动过 = 不静默），测试就让它真的过门。
  const ckptDir = mkdtempSync(path.join(os.tmpdir(), 'bcity-deliver-'))
  const ckptPath = path.join(ckptDir, 'it-004-weights.json')
  writeFileSync(ckptPath, '{}')

  const deps = (over: Partial<DeliverUploadDeps> = {}): DeliverUploadDeps => ({
    save: async () => '/tmp/kept.zip',
    // 留证目录清理替身（§6.2-5）：**必须**注入 —— 不注入就会真去动 `tmp/<课>/deliver-uploads/`
    // （那是留证目录，测试里删掉别人的原件是最坏的一种副作用）。
    pruneUploads: () => ({ removed: [], bytes: 0 }),
    // 异步替身（与生产同形：导入器是异步子进程，调用方 await 它的结果）
    importZip: async () => ({
      ok: true,
      message: '已导入 r1（2 轮，it3 → it4）',
      payload: {
        run_id: 'r1',
        dir: '/tmp/r1',
        iters: [3, 4],
        last_it: 4,
        ckpt: ckptPath,
        state: 'complete',
        rows: 2,
        course: COURSE,
        source_zip: '/tmp/kept.zip',
        bytes: 3,
      } satisfies DeliverImportPayload,
    }),
    launchEval: (course: string, ckpt: string, iter: number) => {
      launched.push({ course, ckpt, iter })
      return { ok: true, message: `evalA 已启动 it${iter}` }
    },
    // T6 默认：hub 不可达（不转交 + 说明一句）——需要转交的用例自己注入摘要与替身。
    offlineResults: async () => ({ results: null, hubUp: false }),
    writeRunComplete: async () => ({ ok: true, message: '已完成（run_complete 已落账）' }),
    ...over,
  })
  const launched: Array<{ course: string; ckpt: string; iter: number }> = []

  const upload = async (fileName: string, bytes = 'x', d = deps()) => {
    const form = new FormData()
    form.append('file', new File([bytes], fileName))
    return handleDeliverUpload(
      new Request('http://x/api/deliverUpload', { method: 'POST', body: form }),
      COURSE,
      d,
    )
  }

  it('上传成功后**接着起评估**（按课程语料，评末轮），响应含结论与产物目录', async () => {
    launched.length = 0
    const r = await upload(`deliver-${COURSE}.zip`)
    const body = (await r.json()) as Record<string, unknown>
    expect(r.status).toBe(200)
    expect(body.ok).toBe(true)
    expect(String(body.message)).toContain('evalA 已启动 it4')
    expect(body.evalStarted).toBe(true)
    expect(launched).toHaveLength(1)
    expect(launched[0].iter).toBe(4)
  })

  it('评估起不来不算导入失败（产物已落地可读，那是这一半的全部价值）', async () => {
    const r = await upload(
      `deliver-${COURSE}.zip`,
      'x',
      deps({ launchEval: () => ({ ok: false, message: 'evalA 已在运行' }) }),
    )
    const body = (await r.json()) as Record<string, unknown>
    expect(r.status).toBe(200)
    expect(body.ok).toBe(true)
    expect(body.evalStarted).toBe(false)
    expect(String(body.message)).toContain('evalA 已在运行')
  })

  // ──────────── §7 / DoD-6a：课停了也能导入（不看开课标记、不看 hub 活没活）────────────

  it('★ 课已停（无开课标记 + hub 已关）⇒ 照导：ok:true，两句原因都上屏', async () => {
    // 口径（§7-1）：导入链上**没有一处**读「课程在不在训/有没有开课标记」——解包、并账本、
    // 留证都是文件层的事。课停之后人恰恰最需要把最后一段产物收进来（而 hub 通常正是关着的）。
    // 这里把两个会「假设课在跑」的读数都设成最坏：hub 连不上 + 评估起不来。
    const r = await upload(
      `deliver-${COURSE}.zip`,
      'x',
      deps({
        offlineResults: async () => {
          throw new Error('ECONNREFUSED 127.0.0.1:8899（hub 已关）')
        },
        launchEval: () => ({ ok: false, message: 'evalA 启动失败：课已停（没有可跑的语料）' }),
      }),
    )
    const body = (await r.json()) as Record<string, unknown>
    expect(r.status).toBe(200)
    expect(body.ok).toBe(true)
    expect(body.evalStarted).toBe(false)
    const msg = String(body.message)
    expect(msg).toContain('已导入 r1') // 导入本身不受影响（权重落地 = 这一半的全部价值）
    expect(msg).toContain('evalA 启动失败：课已停') // 评估没起来：原因照原话说
    expect(msg).toContain('段末横幅未落账：hub 不可达（不影响导入）')
  })

  it('拿错课的文件名 / 非 zip / 超大 → 拒收且不调 python', async () => {
    let called = 0
    const d = deps({
      importZip: async () => {
        called += 1
        return { ok: false, message: '不该被调用' }
      },
    })
    expect((await upload('deliver-other.zip', 'x', d)).status).toBe(400)
    expect((await upload('deliver-x.zip.tar.gz', 'x', d)).status).toBe(400)
    expect(called).toBe(0)
    expect(DELIVER_MAX_BYTES).toBeGreaterThan(0)
  })

  it('没有文件 → 400（面板先说清要选文件）', async () => {
    const form = new FormData()
    const r = await handleDeliverUpload(
      new Request('http://x/api/deliverUpload', { method: 'POST', body: form }),
      COURSE,
      deps(),
    )
    expect(r.status).toBe(400)
  })

  it('上传原件落在课程下的 deliver-uploads/（留证，不覆盖历史）', () => {
    expect(deliverUploadDir(COURSE)).toBe(path.join(tmpCourseDir(COURSE), 'deliver-uploads'))
    expect(deliverImportRoot(COURSE)).toBe(path.join(tmpCourseDir(COURSE), 'deliver'))
  })

  it('★ §6.2-5：留证目录只留最近 3 份（删时响亮一行，不默默删）', () => {
    // 现场 43MB/次、只涨不落 ⇒ 加一条保留策略。判据 = 按名字（ISO 时间戳前缀）倒序取前 N。
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-uploads-'))
    for (const stamp of [
      '2026-10-01T00-00-00',
      '2026-10-02T00-00-00',
      '2026-10-03T00-00-00',
      '2026-10-04T00-00-00',
      '2026-10-05T00-00-00',
    ]) {
      writeFileSync(path.join(dir, `${stamp}-deliver-x1.zip`), 'z'.repeat(3))
    }
    writeFileSync(path.join(dir, 'notes.txt'), '不是留证原件')
    const got = pruneDeliverUploads(COURSE, 3, dir)
    expect(got.removed).toEqual([
      '2026-10-02T00-00-00-deliver-x1.zip',
      '2026-10-01T00-00-00-deliver-x1.zip',
    ])
    expect(got.bytes).toBe(6)
    const left = readdirSync(dir).sort()
    expect(left).toContain('2026-10-05T00-00-00-deliver-x1.zip')
    expect(left).toContain('notes.txt')
    expect(left.filter((n) => n.endsWith('.zip'))).toHaveLength(3)
    rmSync(dir, { recursive: true, force: true })
  })

  it('清理发生在响应里看得见（成功与失败两条路都在）', async () => {
    const d = deps({
      pruneUploads: () => ({ removed: ['2026-10-01T00-00-00-deliver-x1.zip'], bytes: 9 }),
    })
    const body = (await (await upload(`deliver-${COURSE}.zip`, 'x', d)).json()) as Record<
      string,
      unknown
    >
    expect((body.detail as string[]).join('\n')).toContain('留证目录已清理：删掉 1 份旧原件')
  })

  it('包内自述上屏（§6.2-2）：课程/run/来源 —— 不静默切课，但看得见', async () => {
    const d = deps({
      importZip: async () => ({
        ok: true,
        message: '已导入 r1（2 轮，it3 → it4）',
        payload: {
          run_id: 'r1',
          dir: '/tmp/r1',
          iters: [3, 4],
          last_it: 4,
          ckpt: ckptPath,
          state: 'complete',
          rows: 2,
          course: COURSE,
          source_zip: '/tmp/kept.zip',
          bytes: 3,
          package_course: COURSE,
          package_run_id: 'run-seg2',
          package_source: 'drawer',
        } satisfies DeliverImportPayload,
      }),
    })
    const body = (await (
      await upload(`deliver-${COURSE}@run-seg2-it4.zip`, 'x', d)
    ).json()) as Record<string, unknown>
    expect(body.ok).toBe(true)
    const detail = (body.detail as string[]).join('\n')
    expect(detail).toContain('包内自述：课程 x1-rebirth-a2·run run-seg2·来源 drawer')
  })

  it('一键包的附注名（`@<run>-it<N>`）不破课程对账（§6.2-3）', () => {
    expect(validateDeliverUpload(`deliver-${COURSE}@run-seg2-it40.zip`, COURSE)).toBeNull()
    const bad = validateDeliverUpload('deliver-other@r-it1.zip', COURSE)
    expect(bad).toContain('other') // 两个课名 + 动作都要在（DoD-5b 不退化）
    expect(bad).toContain(`deliver-${COURSE}.zip`)
  })

  it('末轮权重不在盘上 → 不静默：明确说「导入目录被动过」', () => {
    const r = launchPostImportEval(
      COURSE,
      {
        run_id: 'r',
        dir: '/nope',
        iters: [1],
        last_it: 1,
        ckpt: '/nope/it-001/weights.json',
        state: '',
        rows: 1,
        course: COURSE,
        source_zip: '',
        bytes: 0,
      },
      { launchEval: () => ({ ok: true, message: '不该被调用' }) },
    )
    expect(r.ok).toBe(false)
    expect(r.message).toContain('不在盘上')
  })

  // ────────────────────────── T6：跑满 ⇒ run_complete（灰横幅）转交 ──────────────────────────

  it('hub 自报跑满且 run_id/末轮号一致 ⇒ 让 python 落 run_complete', async () => {
    const calls: Array<{ course: string; runId: string }> = []
    const r = await upload(
      `deliver-${COURSE}.zip`,
      'x',
      deps({
        offlineResults: async () => ({
          hubUp: true,
          results: {
            r1: { itEnd: 4, state: 'complete', endItReached: true, segEndKind: '', receivedAt: 1 },
          },
        }),
        writeRunComplete: async (course: string, runId: string) => {
          calls.push({ course, runId })
          return { ok: true, message: '已完成（run_complete 已落账）' }
        },
      }),
    )
    const body = (await r.json()) as Record<string, unknown>
    expect(r.status).toBe(200)
    expect(body.endItReached).toBe(true)
    expect(body.runComplete).toBe(true)
    expect(calls).toEqual([{ course: COURSE, runId: 'r1' }])
    expect(String(body.message)).toContain('run_complete')
  })

  it('半段 / 未跑满 / run_id 不匹配 ⇒ 不转交（不调第二趟 python，横幅不亮）', async () => {
    const shapes: Array<
      Record<
        string,
        {
          itEnd: number
          state: string
          endItReached: boolean
          segEndKind: string
          receivedAt: number
        }
      >
    > = [
      { r1: { itEnd: 4, state: 'budget', endItReached: false, segEndKind: '', receivedAt: 1 } }, // 云机没跑满
      { r1: { itEnd: 2, state: 'complete', endItReached: true, segEndKind: '', receivedAt: 1 } }, // 半段导入
      { other: { itEnd: 4, state: 'complete', endItReached: true, segEndKind: '', receivedAt: 1 } }, // run_id 不匹配
    ]
    let calls = 0
    for (const hub of shapes) {
      const r = await upload(
        `deliver-${COURSE}.zip`,
        'x',
        deps({
          offlineResults: async () => ({ results: hub, hubUp: true }),
          writeRunComplete: async () => {
            calls += 1
            return { ok: true, message: '不该被调用' }
          },
        }),
      )
      expect(((await r.json()) as Record<string, unknown>).endItReached).toBe(false)
    }
    expect(calls).toBe(0)
  })

  it('hub 不可达 ⇒ 无从判决，不转交（不可知 ≠ 没跑满），且**说出来**横幅为何没落账', async () => {
    let calls = 0
    const r = await upload(
      `deliver-${COURSE}.zip`,
      'x',
      deps({
        offlineResults: async () => ({ results: null, hubUp: false }),
        writeRunComplete: async () => {
          calls += 1
          return { ok: true, message: '不该被调用' }
        },
      }),
    )
    const body = (await r.json()) as Record<string, unknown>
    expect(body.endItReached).toBe(false)
    expect(body.ok).toBe(true)
    // ★ §7-2：旧的静默 catch 只会让 endItReached=false，人看不出为什么没有任何横幅
    expect(String(body.message)).toContain('段末横幅未落账：hub 不可达（不影响导入）')
    expect(calls).toBe(0)
  })

  it('hub 应答了、只是没有这一段的摘要 ⇒ **不说**「hub 不可达」（原因不许编）', async () => {
    // §7-2 的反探针：把 hubUp 与 results 混成一个 null 的写法会把这条判成「hub 不可达」。
    const r = await upload(
      `deliver-${COURSE}.zip`,
      'x',
      deps({ offlineResults: async () => ({ results: null, hubUp: true }) }),
    )
    const body = (await r.json()) as Record<string, unknown>
    expect(body.endItReached).toBe(false)
    expect(String(body.message)).not.toContain('hub 不可达')
  })

  it('转交失败不改导入 ok（权重已落地可评估；少的只是横幅）', async () => {
    const r = await upload(
      `deliver-${COURSE}.zip`,
      'x',
      deps({
        offlineResults: async () => ({
          hubUp: true,
          results: {
            r1: { itEnd: 4, state: 'complete', endItReached: true, segEndKind: '', receivedAt: 1 },
          },
        }),
        writeRunComplete: async () => ({ ok: false, message: '完成横幅未落账（boom）' }),
      }),
    )
    const body = (await r.json()) as Record<string, unknown>
    expect(r.status).toBe(200)
    expect(body.ok).toBe(true)
    expect(body.runComplete).toBe(false)
    expect(String(body.message)).toContain('未落账')
  })

  it('判据是纯函数：三个条件缺一不可（单测钉住，不靠 HTTP 链路）', () => {
    const payload = { run_id: 'r1', last_it: 4 }
    const ok = {
      r1: { itEnd: 4, state: 'complete', endItReached: true, segEndKind: '', receivedAt: 0 },
    }
    expect(endItReachedForRun(payload, ok)).toBe(true)
    expect(endItReachedForRun(payload, null)).toBe(false)
    expect(endItReachedForRun(payload, { r1: { ...ok.r1, endItReached: false } })).toBe(false)
    expect(endItReachedForRun(payload, { r1: { ...ok.r1, itEnd: 3 } })).toBe(false)
    expect(endItReachedForRun(payload, { other: ok.r1 })).toBe(false)
  })

  // ──────────── J2（P1-3）：无限课/部分段的「本段完成」不得冒充课末 ────────────

  const segResult = (kind: string) => ({
    r1: { itEnd: 4, state: 'complete', endItReached: true, segEndKind: kind, receivedAt: 1 },
  })

  it('无限课的段末（open）⇒ **不**转交 run_complete，另报「本段完成」（O5 灰横幅不得每段都亮）', async () => {
    let calls = 0
    const r = await upload(
      `deliver-${COURSE}.zip`,
      'x',
      deps({
        offlineResults: async () => ({ results: segResult('open'), hubUp: true }),
        writeRunComplete: async () => {
          calls += 1
          return { ok: true, message: '不该被调用' }
        },
      }),
    )
    const body = (await r.json()) as Record<string, unknown>
    expect(body.endItReached).toBe(true)
    expect(body.segmentComplete).toBe(true)
    expect(body.runComplete).toBe(false)
    expect(String(body.message)).toContain('本段完成')
    expect(String(body.message)).toContain('开区间段')
    expect(calls).toBe(0)
  })

  it('有限课被截短的一段（segment）⇒ 同样不转交（它不是课末）', async () => {
    const r = await upload(
      `deliver-${COURSE}.zip`,
      'x',
      deps({
        offlineResults: async () => ({ results: segResult('segment'), hubUp: true }),
        writeRunComplete: async () => ({ ok: true, message: '不该被调用' }),
      }),
    )
    const body = (await r.json()) as Record<string, unknown>
    expect(body.segmentComplete).toBe(true)
    expect(body.runComplete).toBe(false)
  })

  it('旧 hub / 旧云机（没有 seg_end_kind）⇒ 照旧转交（不可知 ≠ 抑制）', async () => {
    // 兼容口径：字段缺席 = 「不可知」，按旧行为转交 —— 否则真课末会因为一次版本错位
    // 静默少打一次「已完成」横幅（而那是 O5 的唯一可见面）。
    let calls = 0
    const r = await upload(
      `deliver-${COURSE}.zip`,
      'x',
      deps({
        offlineResults: async () => ({
          hubUp: true,
          results: {
            r1: { itEnd: 4, state: 'complete', endItReached: true, segEndKind: '', receivedAt: 1 },
          },
        }),
        writeRunComplete: async () => {
          calls += 1
          return { ok: true, message: '已完成（run_complete 已落账）' }
        },
      }),
    )
    const body = (await r.json()) as Record<string, unknown>
    expect(body.segmentComplete).toBe(false)
    expect(body.runComplete).toBe(true)
    expect(calls).toBe(1)
  })

  it('三态纯函数：只有 open/segment 算「不是课末」', () => {
    expect(segmentNotCourseEnd('open')).toBe(true)
    expect(segmentNotCourseEnd('segment')).toBe(true)
    expect(segmentNotCourseEnd('course')).toBe(false)
    expect(segmentNotCourseEnd('')).toBe(false) // 不可知 ⇒ 按旧行为
    const hits = { r1: segResult('open').r1 }
    expect(segEndKindForRun({ run_id: 'r1' }, hits)).toBe('open')
    expect(segEndKindForRun({ run_id: 'nope' }, hits)).toBe('')
    expect(segEndKindForRun({ run_id: 'r1' }, null)).toBe('')
  })

  // ──────────── B0 回灌腿：hub 已收 run → 本机账本 ────────────

  it('B0：从 <traj>/deliver 或 remote-jobs/offline 取段末产物，argv 交给 python 导入器', async () => {
    delete process.env.BCITY_NO_AUTO_TASK_BUNDLE
    const course = '__backfill-course__'
    const runDir = path.join(tmpCourseDir(course), 'remote-jobs', 'offline', 'seg-1')
    try {
      mkdirSync(path.join(runDir, 'it-003'), { recursive: true })
      writeFileSync(path.join(runDir, 'it-003', 'weights.json'), '{}')
      expect(landedHubRuns(course).map((x) => [x.runId, x.lastIt])).toEqual([['seg-1', 3]])
      const seen: string[][] = []
      const runner = ((_m: string, args: string[]) => {
        seen.push(args)
        return {
          code: 0,
          stdout:
            `${deliverImportJsonMark}{"run_id":"seg-1","iters":[1,2,3],"last_it":3,` +
            '"imported":true}',
          stderr: '',
          timeout: false,
        }
      }) as unknown as DeliverZipRunner
      const r = await backfillHubRun(course, runner)
      expect(r.ok).toBe(true)
      expect(r.note).toContain('已回灌')
      expect(seen[0]).toEqual([
        '--from-run',
        runDir,
        '--run-id',
        'seg-1',
        '--dest',
        deliverImportRoot(course),
        '--course',
        course,
      ])
    } finally {
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('B0：没有落地产物 ⇒ 不跑 python（ran=false 不是失败）；已到段末 ⇒ 「无需回灌」', async () => {
    delete process.env.BCITY_NO_AUTO_TASK_BUNDLE
    const quiet = await backfillHubRun('__no-landed-runs-course__', (() => {
      throw new Error('不该被调用')
    }) as unknown as DeliverZipRunner)
    expect(quiet).toEqual({ ok: true, ran: false, note: '' })
    const course = '__backfill-idempotent__'
    const runDir = path.join(tmpCourseDir(course), 'deliver', 'seg-9')
    try {
      mkdirSync(path.join(runDir, 'it-050'), { recursive: true })
      writeFileSync(path.join(runDir, 'it-050', 'weights.json'), '{}')
      const runner = (() => ({
        code: 0,
        stdout: `${deliverImportJsonMark}{"run_id":"seg-9","iters":[45,50],"last_it":50,"imported":false,"reason":"本机账本已到 it50"}`,
        stderr: '',
        timeout: false,
      })) as unknown as DeliverZipRunner
      const r = await backfillHubRun(course, runner)
      expect(r.ok).toBe(true)
      expect(r.note).toContain('无需回灌')
    } finally {
      rmSync(tmpCourseDir(course), { recursive: true, force: true })
    }
  })

  it('writeRunCompleteWithPython：第二趟 argv 形状（不重新解包 zip）', async () => {
    const seen: string[][] = []
    const runner = ((_m: string, args: string[]) => {
      seen.push(args)
      return { code: 0, stdout: '', stderr: '', timeout: false }
    }) as unknown as DeliverZipRunner
    const r = await writeRunCompleteWithPython(
      COURSE,
      'r1',
      { destRoot: '/tmp/dest', logFile: '/tmp/import.log' },
      runner,
    )
    expect(r.ok).toBe(true)
    expect(seen[0]).toEqual([
      '--end-it-reached',
      '--run-id',
      'r1',
      '--dest',
      '/tmp/dest',
      '--course',
      COURSE,
    ])
  })

  it('TS 侧不碰账本（T6）：转交只经 python 的 --end-it-reached', () => {
    const src = readFileSync(path.join(REPO_ROOT, 'dashboard/src/server/bundles/import.ts'), 'utf8')
    // 转交 = 把判决当参数交给 python（账本唯一写者），不在 TS 里落任何事件。
    expect(src).toContain("'--end-it-reached'")
    expect(src).not.toContain('write_run_complete(')
    // TS 侧没有任何「追加到课程账本」的写法（出现即说明有人在这儿复刻了第二个写者）
    expect(src).not.toContain('appendFileSync')
  })
})
