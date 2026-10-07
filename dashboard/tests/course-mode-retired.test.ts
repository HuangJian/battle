/**
 * course-mode-retired.test.ts — **模式语义已退役**的接线守门（★M2 写面 + ★M4 控制台）。
 *
 *  历史：2026-09-19 的口径是「启动课程训练时需指定 在线/离线 模式」。两刀把它拆掉：
 *   · **M2**（plan/worker-type-dispatch-model §3-M2）：模式**不再**翻译成
 *     `courses.<课>.{rollout_src:'run', run_iters:-1}`——「这门课归云机」是 hub 的 **hold**
 *     事实（云机 claim 成功 + 有进度才建立），训练侧每轮边界问一次（`trainer/loop_hold.py`）；
 *   · **M4**（同 plan §3-M4）：控制台那一半——`course-mode.ts` 整模块、`console-state.courseModes`
 *     / `courseRolloutSrc`、起 hub 回灌、开课弹窗的「训练模式」格、权威/modeDrift 徽标、
 *     切离线/切换成在线/交还自动池三颗钮、`intent-drift` 一档——全部退役。
 *
 *  本文件反向钉住「别把它们请回来」，并钉住取代它们的三个新出口：
 *   ① **不留现成配方**：`trainModeKnobs` / `applyTrainModeToConfig` / `TrainMode` 类型 /
 *      `course-mode.ts` 文件本身都不在了（留一份配方 = 下次重构把旧语义写回来）；
 *   ② **入口响亮拒绝**：旧客户端发 `trainMode` ⇒ 400（静默忽略 = 一条不会发生的承诺），
 *      rollout 白名单收窄到活域（`run` 已退役）；
 *   ③ **新出口就位**：hub 触发的自动导包（`autoOfflineHandoff`）仍在、`releaseCourseHold`
 *      是 UI 上唯一的人工接管出口、hold 派生（词/徽标/接管列）在视图层单点。
 *
 *  为什么全是源码断言：跨文件链路（弹窗 → app → route → 动作层）tsc 抓不到丢参/漏键，而这类
 *  「点了不生效」的假成功在本仓反复出现过（`web-train-launch-wiring.test.ts` 有前案）。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { buildCourseRows, offlineSummary, parseOfflineProgress } from '../src/web/view'

const DASHBOARD_ROOT = join(import.meta.dir, '..')
const readSrc = (rel: string): string =>
  readFileSync(join(DASHBOARD_ROOT, ...rel.split('/')), 'utf8').replace(/\s+/g, ' ')

// ────────────────────────── ① 配方与类型都不留 ──────────────────────────

describe('★M2/M4：模式 → rl-config 的写面与「模式」这个概念一起退役', () => {
  it('写面与它的推导函数一起消失（留一份现成配方 = 下次重构把旧语义写回来）', () => {
    for (const rel of [
      'src/stack/specs.ts',
      'src/server/actions/course-lifecycle.ts',
      'src/server/actions/auto-offline-handoff.ts',
      'src/server/api/route.ts',
    ]) {
      expect(readSrc(rel)).not.toMatch(/function trainModeKnobs/)
      expect(readSrc(rel)).not.toMatch(/function applyTrainModeToConfig/)
      expect(readSrc(rel)).not.toMatch(/applyTrainModeToConfig\(/)
    }
    // 文件本身已删（读它必须炸——不是「还在、只是没人 import」）
    expect(() => readSrc('src/server/actions/train-mode.ts')).toThrow()
    expect(() => readSrc('src/server/actions/course-mode.ts')).toThrow()
  })

  it('★M4：`TrainMode` 类型与它的消费面都没了；`run` 只作**容忍读**（新类型 `RolloutSrcRetired`）', () => {
    const types = readSrc('src/core/types.ts')
    expect(types).not.toMatch(/export type TrainMode/)
    expect(types).toContain("export type RolloutSrcRetired = 'run'")
    // 活域与 python `loop_transport.ROLLOUT_SRCS` 同字面量（不含 run）
    expect(types).toContain("export type RolloutSrcMode = 'local' | 'node' | 'auto'")
    // 容忍读映射成 local（与训练侧 `_note_retired_source` 同口径）
    expect(readSrc('src/stack/specs.ts')).toContain(
      "raw === 'node' || raw === 'auto' ? raw : 'local'",
    )
  })

  it('★M2 前盘上的残留（`run`/`run_iters`）由训练侧容忍读兜住，控制台不再产生它', () => {
    // 容忍读的正面用例在 python 侧（`tests/worker/test_run_segment.py`）：
    // `run` ⇒ 映射成 local + 一行 WARN（不 brick 课程）。这里只钉控制台不再写它。
    const src = readSrc('src/server/actions/course-lifecycle.ts')
    expect(src).not.toContain("row.rollout_src = 'run'")
    expect(src).not.toContain('row.run_iters =')
    // 开课仍会**就地清**退役键（存量盘的收尾在本机配置面上做一次）
    expect(src).toContain("if (row.rollout_src === 'run')")
    expect(src).toContain('delete row.run_iters')
  })
})

// ────────────────────────── ② 入口：响亮拒绝 + 白名单收窄 ──────────────────────────

describe('入口接线：旧字段响亮拒绝、新出口就位', () => {
  it('route：`trainMode` ⇒ 400（不静默忽略）；rollout 白名单不含退役的 `run`', () => {
    const src = readSrc('src/server/api/route.ts')
    expect(src).toContain('if (body.trainMode !== undefined)')
    expect(src).toContain('trainMode 已退役')
    expect(src).toContain("if (rolloutSrc && !['auto', 'local', 'node'].includes(rolloutSrc))")
    expect(src).not.toContain("'node', 'run'")
    // 旧模式动作名整个消失（留着 = 一条会 500/静默无效的假承诺）
    expect(src).not.toContain("case 'setCourseMode'")
    expect(src).not.toContain("case 'unsetCourseMode'")
    expect(src).not.toContain('restoreCourseModes')
  })

  it('★M4：新的人工出口 = `releaseCourseHold`（hub 的 `release_hold=1`）', () => {
    expect(readSrc('src/server/api/route.ts')).toContain("case 'releaseCourseHold'")
    expect(readSrc('src/server/actions/course-lifecycle.ts')).toContain(
      'export async function releaseCourseHold',
    )
    const hub = readSrc('src/stack/hub-admin.ts')
    expect(hub).toContain('export async function hubReleaseCourseHold')
    expect(hub).toContain('release_hold=1')
  })

  it('★M4：hub → 控制台的反向调用只剩「出包」（F1 定案）', () => {
    const src = readSrc('src/server/actions/auto-offline-handoff.ts')
    expect(src).toContain('控制台只负责把任务包导出来')
    // 不写配置、不落意图、不推 hub 课程表
    expect(src).not.toContain('writeCourseConfigForOpen')
    expect(src).not.toContain('saveConsoleState')
    // 动作层桶导出的也是它（旧模块名不得复活）
    const index = readSrc('src/server/actions/index.ts')
    expect(index).toContain("'./auto-offline-handoff'")
    expect(index).not.toContain("'./course-mode'")
  })

  it('★M4：启动不再回灌模式意图（`start.ts` 里那两条调用整个删掉）', () => {
    const src = readSrc('src/server/actions/start.ts')
    expect(src).not.toContain('restoreCourseMode')
    expect(src).not.toContain('CourseMode')
  })

  it('★M4：console-state 不再有 intent/逐课 rollout 源两个键（旧盘上的残留不进内存态）', () => {
    const src = readSrc('src/server/actions/console-state.ts')
    expect(src).not.toMatch(/courseModes\s*:/)
    expect(src).not.toMatch(/courseRolloutSrc\s*:/)
    // 读面也不下发它们（留着就会有旧客户端拿它做漂移判断）——按**字段**判（注释里点名退役键是允许的）
    const sv = readSrc('src/server/api/state-view.ts')
    expect(sv).not.toMatch(/courseRolloutSrc\s*:/)
    expect(sv).not.toMatch(/courseModeIntents\s*:/)
  })
})

// ────────────────────────── ③ 控制台 UI：无模式控件 + hold 单点派生 ──────────────────────────

describe('★M4：控制台 UI 的模式控件与漂移徽标全部退役', () => {
  it('开课弹窗：没有「训练模式」那一格，但 rollout 位置仍在（它与接管正交）', () => {
    const src = readSrc('src/web/app/panels/OpenCourseModal.tsx')
    expect(src).not.toContain('TC_OPEN_TRAIN_MODE')
    expect(src).not.toContain('ariaLabel="训练模式"')
    expect(src).not.toContain("label: '离线'")
    expect(src).not.toMatch(/trainMode/)
    expect(src).toContain('ariaLabel="rollout 执行位置"')
    // 接管不在这里选（自主 worker 自己 claim）——文案明说，免得人到处找那颗开关
    expect(src).toContain('云机接管不在这里选')
  })

  it('app.tsx：开课 body 不再带 trainMode（带了会被服务端 400 响亮拒）', () => {
    const src = readSrc('src/web/app/app.tsx')
    expect(src).not.toContain('body.trainMode')
    expect(src).not.toContain('trainMode: opts.trainMode')
    expect(src).not.toContain('TrainMode')
    expect(src).toContain('...(opts.rolloutSrc ? { rolloutSrc: opts.rolloutSrc } : {})')
  })

  it('课程矩阵 / 课程管理页：三颗模式钮与漂移徽标消失，换成「强制解除接管」', () => {
    for (const rel of [
      'src/web/app/panels/CourseMatrix.tsx',
      'src/web/app/panels/CourseAdmin.tsx',
    ]) {
      const src = readSrc(rel)
      // 旧动作**字符串**不再发出（注释里点名它们是允许的——那是在说「为什么删」）。
      expect(src).not.toMatch(/onAction\('(?:set|unset)CourseMode'/)
      expect(src).not.toContain('canToggleMode')
      expect(src).not.toMatch(/modeDrift\s*[:=]/)
      expect(src).not.toContain('意图未生效')
      expect(src).toContain('强制解除接管')
      expect(src).toContain("'releaseCourseHold'")
    }
    // 面板不再接「意图 / 逐课 rollout 源」两个入参（按**字段/入参**判；注释里点名它们是允许的）
    expect(readSrc('src/web/app/panels/CourseMatrix.tsx')).not.toMatch(/modeIntents\s*[?:]/)
    expect(readSrc('src/web/app/panels/TrainingPills.tsx')).not.toMatch(/modeIntents\s*[?:]/)
  })

  it('视图层：漂移函数消失，hold 派生单点（词 / 徽标 / 接管列）', () => {
    const matrix = readSrc('src/web/view/course-matrix.ts')
    expect(matrix).not.toMatch(/modeDrift/)
    expect(matrix).toContain('export function holdBadgeOf')
    expect(matrix).toContain('export function holdCell')
    expect(matrix).toContain('canReleaseHold: hubOnline &&')
    expect(matrix).not.toContain('canToggleMode')
    const status = readSrc('src/web/view/course-status.ts')
    expect(status).toContain("'autonomous-stale'")
    expect(status).toContain('export function holdWord')
    expect(status).not.toContain("'offline-wait'")
    expect(status).not.toContain("| 'intent-drift'")
    // 训练侧只剩 `held` 一档等待词（旧 `offline` 已删）
    const queue = readSrc('src/web/view/loop-queue.ts')
    expect(queue).toContain(
      "export type LoopWaitKind = 'inflight' | 'collect' | 'idle' | 'ready' | 'blocked' | 'held'",
    )
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
    expect(src).toContain('offlineStalled: admin.offlineStalled')
    // 注：顶层旧 `offline` 键的删除不在这里按字符串判——`buildCourseRows` 的入参键同名。
    // 那条由 `server-api-overview.test.ts` 的 `Object.keys(ov)` 行为断言守着（真值面，不是字面量）。
    // ★M4：逐课行带 **hold**（接管）与 pending_export；顶层带自主盘的最近露面面
    expect(src).toContain('holdFacts(live?.queue ?? null)')
    expect(src).toContain('offlineDisks')
    // 客户端侧确实打的是 /admin/offline
    expect(readSrc('src/stack/hub-admin.ts')).toContain('/admin/offline')
  })
})
