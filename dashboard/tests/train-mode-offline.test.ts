/**
 * train-mode-offline.test.ts — 离线训练模式（2026-09-19 用户口径；★M2 换模型）
 *
 * 「启动课程训练时，需指定 在线/离线 模式，缺省在线。」本文件守这条链路的四段：
 *
 *   ① **写面已退役**（★M2，plan/worker-type-dispatch-model §3-M2）：模式**不再**翻译成
 *      `courses.<课>.{rollout_src:'run', run_iters:-1}`——接管是 hub 的 **hold** 事实，
 *      训练侧每轮边界问一次（`trainer/loop_hold.py`）。旧推导函数 `trainModeKnobs` 与唯一
 *      写面 `train-mode.ts` 一起删除，本文件反向钉住「别把它们请回来」。
 *   ② **落盘只剩两件事**：开课/切模式不再写 rl-config（离线档）；在线档只落显式选的
 *      `rollout_src` 并就地清退役键（`run_iters` / 残留的 `'run'`）。
 *   ③ **入口**：route 白名单 + 开课弹窗（缺省在线；服务端生效值 `run` ⇒ 打开就已选中离线，
 *      否则重新开课 = 静默把离线课拉回在线）。
 *   ④ **读面**：`/admin/offline` 的逐轮产物 → 总览行（段内那几轮**不在课程账本里**，
 *      只有这条通道能回答「云机在跑还是挂了」）。
 *
 * 为什么全是源码断言：跨文件链路（弹窗 → app → route → preset）tsc 抓不到丢参/漏键，
 * 而这类「点了不生效」的假成功在本仓反复出现过（`web-train-launch-wiring.test.ts` 有前案）。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { buildCourseRows, offlineSummary, parseOfflineProgress } from '../src/web/view'

const DASHBOARD_ROOT = join(import.meta.dir, '..')
const readSrc = (rel: string): string =>
  readFileSync(join(DASHBOARD_ROOT, ...rel.split('/')), 'utf8').replace(/\s+/g, ' ')

// ────────────────────────── ① 写面已退役（★M2） ──────────────────────────

describe('★M2：模式 → rl-config 的写面退役（接管 = hub 的 hold 事实）', () => {
  it('写面与它的推导函数一起消失（留一份现成配方 = 下次重构把旧语义写回来）', () => {
    for (const rel of [
      'src/stack/specs.ts',
      'src/server/actions/course-lifecycle.ts',
      'src/server/actions/course-mode.ts',
    ]) {
      expect(readSrc(rel)).not.toMatch(/function trainModeKnobs/)
      expect(readSrc(rel)).not.toMatch(/function applyTrainModeToConfig/)
      expect(readSrc(rel)).not.toMatch(/applyTrainModeToConfig\(/)
    }
    // 文件本身已删（读它必须炸——不是「还在、只是没人 import」）
    expect(() => readSrc('src/server/actions/train-mode.ts')).toThrow()
  })

  it('★M2 前盘上的残留（`run`/`run_iters`）由训练侧容忍读兜住，控制台不再产生它', () => {
    // 容忍读的正面用例在 python 侧（`tests/worker/test_run_segment.py`）：
    // `run` ⇒ 映射成 local + 一行 WARN（不 brick 课程）。这里只钉控制台不再写它。
    const src = readSrc('src/server/actions/course-lifecycle.ts')
    expect(src).not.toContain("row.rollout_src = 'run'")
    expect(src).not.toContain('row.run_iters =')
  })
})

// ────────────────────────── ② 落盘（开课） ──────────────────────────

describe('course-lifecycle（开课）：离线不写盘 + 在线清退役键 + hub 该课置 offline', () => {
  const src = readSrc('src/server/actions/course-lifecycle.ts')

  it('离线档不再写 rl-config（★M2）：只推 hub 模式 + 导出任务包', () => {
    expect(src).toContain('离线档不再写 rl-config')
    // 清退役键是**在线档**的事（离线档连它都不该碰：那是同一次开课的清理，不是模式语义）
    expect(src).toContain('已清退役键')
    expect(src).toContain('row.rollout_src = opts.rolloutSrc')
  })

  it('任何档都不许写全局 rl.rollout_src（否则全部课程一起离线）', () => {
    expect(src).not.toMatch(/rl\.rollout_src\s*=/)
    // 反向对照：**启动**侧也不碰它（课程级选项不回流向全局默认面）
    const preset = readSrc('src/server/actions/preset.ts')
    expect(preset).not.toMatch(/rl\.rollout_src\s*=/)
  })

  it('切回在线 = 撤掉退役标记（`run_iters` 与残留的 `run` 都清；空节点整条删）', () => {
    expect(src).toContain('delete row.run_iters')
    const guard = src.indexOf("if (row.rollout_src === 'run')")
    expect(guard).toBeGreaterThan(-1)
    expect(src.indexOf('delete row.rollout_src')).toBeGreaterThan(guard)
    expect(src).toContain('空节点不留痕')
  })

  it('开课时把该课 hub 模式一起放对：离线 ⇒ 该课停车；在线 ⇒ 恢复派发', () => {
    expect(src).toContain("opts.trainMode === 'offline' ? 'offline' : 'online'")
    expect(src).toContain('pushHubMode(c, trainMode')
    // 结果要回话（静默切模式 = 「我明明选了在线却不动」）
    expect(src).toContain('hubNote')
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

  it('app.tsx：trainMode 进 **openCourse** body（漏了 = 选了离线却照旧本机跑）', () => {
    const src = readSrc('src/web/app/app.tsx')
    expect(src).toContain("await doAction('openCourse', {")
    expect(src).toContain('trainMode: opts.trainMode')
    // ★ 启动链路不带它（那是「起进程」——不为某门课做决定）
    expect(src).not.toContain('body.trainMode')
  })

  it('开课弹窗：训练模式控件 + 选项带出去 + 选中即记住', () => {
    const src = readSrc('src/web/app/panels/OpenCourseModal.tsx')
    expect(src).toContain("const TC_OPEN_TRAIN_MODE = 'tc.openCourse.trainMode'")
    expect(src).toContain('ariaLabel="训练模式"')
    expect(src).toContain("{ value: 'offline', label: '离线' }")
    expect(src).toMatch(/onConfirm\(\{[^}]*\btrainMode\b/)
    expect(src).toContain('trainMode: TrainMode')
    expect(src).toContain('writeLocal(TC_OPEN_TRAIN_MODE, trainMode)')
  })

  it('开课弹窗：服务端生效值 `run` ⇒ 打开就选中离线（否则再点开课 = 静默拉回在线）', () => {
    const src = readSrc('src/web/app/panels/OpenCourseModal.tsx')
    expect(src).toContain("modes.rolloutSrc === 'run'")
    expect(src).toContain("'offline'")
    // 且 `run` 不作为 rollout 源的选项出现（它是离线档的产物，不是一个可选位置）
    expect(src).not.toContain("{ value: 'run', label:")
    // 离线档忽略 rollout 选择（服务端同样忽略：只认 run/run_iters 那对键）
    expect(src).toContain("trainMode === 'online' ? rolloutSrc : undefined")
  })
})

// ────────────────────────── ③b 热切那颗开关的接线（2026-09-24 L1） ──────────────────────────

describe('热切开关的接线：route → setCourseMode → 面板（三源一致性靠这条链）', () => {
  it('route：setCourseMode 透传 course/mode（那颗开关的唯一入口）', () => {
    const src = readSrc('src/server/api/route.ts')
    expect(src).toContain("case 'setCourseMode'")
    expect(src).toContain("await setCourseMode(bodyStr(body, 'course'), bodyStr(body, 'mode'))")
  })

  it('course-mode：★M2 起一个字都不写 rl-config（推 hub + 落意图）；autoOfflineHandoff 只导包', () => {
    const src = readSrc('src/server/actions/course-mode.ts')
    expect(src).not.toContain('applyTrainModeToConfig')
    expect(src).not.toContain('saveConfig(')
    // 那颗开关仍是「推 hub + 落意图」：pushCourseMode 是原语，setCourseMode 叠 pin/dropJobs
    const pushStart = src.indexOf('export async function pushCourseMode')
    const setStart = src.indexOf('export async function setCourseMode')
    expect(pushStart).toBeGreaterThan(-1)
    expect(setStart).toBeGreaterThan(pushStart)
    // F1：hub → 控制台的反向调用只有「出包」这一件事了
    expect(src).toContain('控制台只负责把任务包导出来')
  })

  it('开课/停课的 hub 推送走 pushCourseMode（不再经过会写配置的 setCourseMode）', () => {
    const src = readSrc('src/server/actions/course-lifecycle.ts')
    expect(src).toContain('pushCourseMode')
    expect(src).not.toContain('await setCourseMode(')
  })

  it('第三源的取数链：stateView 逐课下发 → app 透传 → 矩阵消费', () => {
    const sv = readSrc('src/server/api/state-view.ts')
    expect(sv).toContain('courseRolloutSrc')
    expect(sv).toContain('resolveRolloutSrc(cfg, c)')
    const app = readSrc('src/web/app/app.tsx')
    expect(app).toContain('courseRolloutSrc={stateView?.courseRolloutSrc ?? null}')
    const panel = readSrc('src/web/app/panels/CourseMatrix.tsx')
    expect(panel).toContain('courseRolloutSrc,')
    expect(panel).toContain('配置仍是离线（云机接手）')
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
    // ★ 2026-10-03（T6/T8）：从 hubOfflineProgress 扩成 hubOfflineAdmin——同一个端点的
    //   progress + results + stalled 三段一次取回（分两次取 = 面板与导入看到不同的 hub）。
    expect(src).toContain('hubOfflineAdmin(live.url, token)')
    expect(src).toContain('offlineProgress: admin.offline')
    expect(src).toContain('offline: admin.offline,')
    expect(src).toContain('offlineStalled: admin.offlineStalled')
    // 客户端侧确实打的是 /admin/offline
    expect(readSrc('src/stack/hub-admin.ts')).toContain('/admin/offline')
  })
})
