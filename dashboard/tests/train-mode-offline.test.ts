/**
 * train-mode-offline.test.ts — 离线训练模式（2026-09-19 用户口径）
 *
 * 「启动课程训练时，需指定 在线/离线 模式，缺省在线。」本文件守这条链路的四段：
 *
 *   ① **域换算**：模式 → 课程级 rl-config 键（`stack/specs.ts::trainModeKnobs` 是唯一推导点）。
 *      离线 = `{rollout_src:'run', run_iters:-1}`（声明 + 段长，缺一不可）；在线 = 撤掉离线标记。
 *   ② **落盘**：`preset.ts` 把换算结果写进 `courses.<课>.*`，且离线档的 `run` **绝不进**
 *      全局 `rl.rollout_src`（那会把所有课一起拖进离线）。
 *   ③ **入口**：route 白名单 + 弹窗控件（缺省在线；服务端生效值 `run` ⇒ 打开就已选中离线，
 *      否则重新开弹窗再点启动 = 静默把离线课拉回在线）。
 *   ④ **读面**：`/admin/offline` 的逐轮产物 → 总览行（段内那几轮**不在课程账本里**，
 *      只有这条通道能回答「云机在跑还是挂了」）。
 *
 * 为什么全是源码断言：跨文件链路（弹窗 → app → route → preset）tsc 抓不到丢参/漏键，
 * 而这类「点了不生效」的假成功在本仓反复出现过（`web-train-launch-wiring.test.ts` 有前案）。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { trainModeKnobs } from '../src/stack/specs'
import { buildCourseRows, offlineSummary, parseOfflineProgress } from '../src/web/view'

const DASHBOARD_ROOT = join(import.meta.dir, '..')
const readSrc = (rel: string): string =>
  readFileSync(join(DASHBOARD_ROOT, ...rel.split('/')), 'utf8').replace(/\s+/g, ' ')

// ────────────────────────── ① 域换算 ──────────────────────────

describe('trainModeKnobs：模式 → 课程级键（唯一推导点）', () => {
  it('离线 ⇒ 声明 + 段长两把键（只给 run 不给段长 = 训练侧静默退化成本机采样）', () => {
    expect(trainModeKnobs('offline', 'local')).toEqual({ rolloutSrc: 'run', runIters: -1 })
    // 段长与选中的 rollout 源无关（离线就是整段，`-1` = 直到课程末尾）
    expect(trainModeKnobs('offline', 'node')).toEqual({ rolloutSrc: 'run', runIters: -1 })
  })

  it('在线 ⇒ 保留选中的源，段长要求删除（`null`，不是 0）', () => {
    expect(trainModeKnobs('online', 'local')).toEqual({ rolloutSrc: 'local', runIters: null })
    expect(trainModeKnobs('online', 'node')).toEqual({ rolloutSrc: 'node', runIters: null })
    expect(trainModeKnobs('online', 'auto')).toEqual({ rolloutSrc: 'auto', runIters: null })
  })

  it('在线不接受 `run`：那是离线模式的产物，留在在线档位是自相矛盾的状态', () => {
    expect(trainModeKnobs('online', 'run')).toEqual({ rolloutSrc: 'local', runIters: null })
  })
})

// ────────────────────────── ② 落盘（preset） ──────────────────────────

describe('preset：离线落课程级键 + hub 该课置 offline', () => {
  const src = readSrc('src/server/actions/preset.ts')

  it('换算只走 trainModeKnobs（第二处推导 = 两处一定会漂开）', () => {
    expect(src).toContain("trainModeKnobs(opts.trainMode, opts.rolloutSrc ?? 'local')")
    expect(src).toContain('row.rollout_src = knobs.rolloutSrc')
    expect(src).toContain('row.run_iters = knobs.runIters ?? -1')
  })

  it('离线档的 `run` 绝不写进全局 rl.rollout_src（否则全部课程一起离线）', () => {
    // 全局面取 knobs.rolloutSrc，而离线算出来的就是 run ⇒ 置空
    expect(src).toContain("knobs.rolloutSrc === 'run' ? undefined : knobs.rolloutSrc")
    expect(src).toContain('cfgT.rl.rollout_src = globalRolloutSrc')
    // 反向：没有一处把 opts.rolloutSrc 直接写进全局键
    expect(src).not.toContain('cfgT.rl.rollout_src = opts.rolloutSrc')
  })

  it('切回在线 = 撤掉离线标记（段长必删；只删 `run` 而不删段长 = 半状态）', () => {
    expect(src).toContain('delete row.run_iters')
    expect(src).toContain("if (row.rollout_src === 'run') delete row.rollout_src")
    // 不得顺手清掉别的课程级覆盖（显式写的 node 是另一个理由）
    expect(src).not.toContain('delete row.rollout_src\n')
  })

  it('启动时把该课 hub 模式一起放对：离线 ⇒ 只让带标 worker 领；在线 ⇒ 恢复派发', () => {
    expect(src).toContain(
      "setCourseMode(course, opts.trainMode === 'offline' ? 'offline' : 'online')",
    )
    // 结果要回话（静默切模式 = 「我明明选了在线却不动」）
    expect(src).toContain('modeNote')
  })
})

// ────────────────────────── ③ 入口（route / app / 弹窗） ──────────────────────────

describe('入口接线：route 白名单 + app 透传 + 弹窗控件', () => {
  it('route：trainMode 走白名单，非法值 400', () => {
    const src = readSrc('src/server/api/route.ts')
    expect(src).toContain("const trainMode = bodyStr(body, 'trainMode')")
    expect(src).toContain("if (trainMode && !['online', 'offline'].includes(trainMode))")
    expect(src).toContain('trainMode: (trainMode || undefined) as TrainMode | undefined')
  })

  it('app.tsx：trainMode 进 preset body（漏了 = 选了离线却照旧本机跑）', () => {
    const src = readSrc('src/web/app/app.tsx')
    expect(src).toContain('if (opts?.trainMode) body.trainMode = opts.trainMode')
  })

  it('弹窗：训练模式控件 + 选项带出去 + 选中即记住', () => {
    const src = readSrc('src/web/app/panels/TrainLaunchModal.tsx')
    expect(src).toContain("const TC_TRAIN_MODE = 'tc.trainMode'")
    expect(src).toContain('ariaLabel="训练模式"')
    expect(src).toContain("{ value: 'offline', label: '离线' }")
    expect(src).toMatch(/onLaunch\(\{[^}]*\btrainMode\b/)
    expect(src).toContain('trainMode: TrainMode')
    expect(src).toContain('writeLocal(TC_TRAIN_MODE, trainMode)')
    // 缺省在线（用户口径）
    expect(src).toContain("['online', 'offline'] as const, 'online'")
  })

  it('弹窗：服务端生效值 `run` ⇒ 打开就选中离线（否则再点启动 = 静默拉回在线）', () => {
    const src = readSrc('src/web/app/panels/TrainLaunchModal.tsx')
    expect(src).toContain("modes.rolloutSrc === 'run' ? 'offline'")
    // 且 `run` 不再是 rollout 源的选项之一
    expect(src).not.toContain("{ value: 'run', label:")
    expect(src).toContain("rolloutSrc === 'run' ? 'local' : rolloutSrc")
  })
})

// ────────────────────────── ④ 读面（/admin/offline） ──────────────────────────

describe('parseOfflineProgress：hub 侧段内进度 → 视图', () => {
  it('正常形状：逐 run 的轮次升序 + 最近 mtime', () => {
    const parsed = parseOfflineProgress({
      progress: {
        c5: {
          'run-1': { its: [9, 7, 8], count: 3, last_mtime: 1758.5 },
        },
      },
    })
    expect(parsed).toEqual({
      c5: { 'run-1': { its: [7, 8, 9], count: 3, lastMtime: 1758.5 } },
    })
  })

  it('宽容解析：坏形状退化成空/0，不抛（hub 可能差一个版本）', () => {
    expect(parseOfflineProgress(null)).toBeNull()
    expect(parseOfflineProgress({})).toBeNull()
    expect(parseOfflineProgress({ progress: { c5: 'nope' } })).toEqual({})
    const p = parseOfflineProgress({
      progress: { c5: { 'run-1': { its: [3, 'x', null], count: 'bad' } } },
    })
    expect(p).toEqual({ c5: { 'run-1': { its: [3], count: 1, lastMtime: 0 } } })
  })

  it('offlineSummary：跨 run 累加轮数、取最新轮号与最大 mtime', () => {
    expect(offlineSummary(undefined)).toEqual({ rounds: 0, lastIter: null, lastMtime: 0 })
    expect(
      offlineSummary({
        a: { its: [1, 2], count: 2, lastMtime: 100 },
        b: { its: [7], count: 1, lastMtime: 50 },
      }),
    ).toEqual({ rounds: 3, lastIter: 7, lastMtime: 100 })
  })

  it('buildCourseRows：段内进度进行（账本 iter 与段内轮次是两个数）', () => {
    const rows = buildCourseRows({
      courses: ['c5', 'c4'],
      training: ['c5'],
      queue: null,
      iters: { c5: 6, c4: 42 },
      offline: { c5: { 'run-1': { its: [7, 8], count: 2, lastMtime: 900 } } },
    })
    expect(rows[0]).toMatchObject({
      course: 'c5',
      iter: 6, // 账本尾行（本机那一轮）
      offlineRounds: 2, // 段内（云机跑的，账本里没有）
      offlineLastIter: 8,
      offlineLastMtime: 900,
    })
    expect(rows[1]).toMatchObject({ course: 'c4', offlineRounds: 0, offlineLastIter: null })
  })

  it('overview 把 /admin/offline 接进视图（不然 UI 永远拿到 null）', () => {
    const src = readSrc('src/server/api/overview.ts')
    expect(src).toContain('hubOfflineProgress(live.url, token)')
    expect(src).toContain('offlineProgress: admin.offline')
    expect(src).toContain('offline: admin.offline,')
    // 客户端侧确实打的是 /admin/offline
    expect(readSrc('src/stack/hub-admin.ts')).toContain('/admin/offline')
  })
})
