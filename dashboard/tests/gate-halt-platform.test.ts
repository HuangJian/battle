/**
 * gate-halt-platform.test.ts — 门禁停机模式**平台级**开关（2026-10-01 / plan/gate-halt-platform-level）。
 *
 * 旧实现是**课程级**三写面（顶部开关按 `viewCourse` 读写 `<traj>/gate-halt-mode.txt`、
 * `rl-config courses.<课>.gate_halt_mode`、每课 argv）——同一实验的两条腿门禁行为会不同。
 * 现在只有一个平台文件 `tmp/gate-halt.json`（控制台写、训练侧每轮判定读）+ 训练侧回执。
 *
 * 本文件钉住控制台这一半：
 *   ① 动作写的是**平台**文件（`course` 参数被忽略，旧课程级 txt 不再被写）；
 *   ② `until` 三态（缺省 8h / 显式 null = 不限时 / 正数）；
 *   ③ 非法 mode / untilHours ⇒ 400（不写盘）；
 *   ④ 原子写（不留 `.<name>.<pid>.tmp`）；
 *   ⑤ `/api/state.gateHalt` 两栏（意图 + 回执）；坏文件各自降级（**绝不猜成 notify**）。
 *
 * 夹具：`./helpers/console-fixture`（env 重定向 + 被测模块），另把门禁两份文件重定向到 tmp。
 */

import { mkdtempSync, readFileSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { beforeAll, describe, expect, it } from 'bun:test'

const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-gate-halt-'))
const intentPath = path.join(scratch, 'gate-halt.json')
const appliedPath = path.join(scratch, 'gate-halt.applied.json')
process.env.BCITY_GATE_HALT = intentPath
process.env.BCITY_GATE_HALT_APPLIED = appliedPath

const { api, postJson, scratchDir, view } = await import('./helpers/console-fixture')

/** 意图文件的当前内容（不存在 ⇒ null）。 */
function intent(): Record<string, unknown> | null {
  try {
    return JSON.parse(readFileSync(intentPath, 'utf8')) as Record<string, unknown>
  } catch {
    return null
  }
}

describe('console/api 门禁停机平台开关', () => {
  it('setGateHaltMode 写平台文件（halt ⇒ until = null）', async () => {
    const r = await postJson('setGateHaltMode', { mode: 'halt' })
    expect(r.ok).toBe(true)
    const doc = intent()!
    expect(doc.mode).toBe('halt')
    expect(doc.until).toBeNull()
    expect(doc.by).toBe('console')
    expect(typeof doc.at).toBe('number')
  })

  it('notify 缺省 8h；显式 null = 不限时；正数 = 那么多小时', async () => {
    await postJson('setGateHaltMode', { mode: 'notify' })
    const d1 = intent()!
    expect(d1.mode).toBe('notify')
    expect(Number(d1.until) - Math.floor(Date.now() / 1000)).toBeGreaterThan(7.5 * 3600)

    await postJson('setGateHaltMode', { mode: 'notify', untilHours: null })
    expect(intent()!.until).toBeNull()

    await postJson('setGateHaltMode', { mode: 'notify', untilHours: 2 })
    const d3 = intent()!
    const left = Number(d3.until) - Math.floor(Date.now() / 1000)
    expect(left).toBeGreaterThan(1.9 * 3600)
    expect(left).toBeLessThan(2.1 * 3600)
  })

  it('非法 mode / untilHours ⇒ 400 且**不写盘**', async () => {
    await postJson('setGateHaltMode', { mode: 'halt' })
    const before = readFileSync(intentPath, 'utf8')
    for (const body of [
      { mode: 'skip' },
      { mode: 'notify', untilHours: 0 },
      { mode: 'notify', untilHours: -3 },
      { mode: 'notify', untilHours: 'soon' },
    ]) {
      const r = (await postJson('setGateHaltMode', body)) as { __status: number }
      expect(`${JSON.stringify(body)}:${r.__status}`).toBe(`${JSON.stringify(body)}:400`)
    }
    expect(readFileSync(intentPath, 'utf8')).toBe(before)
  })

  it('★ course 参数被忽略：不再写课程级 txt（旧写面不许复活）', async () => {
    const course = 'c5-tick'
    const r = await postJson('setGateHaltMode', { mode: 'notify', untilHours: 4, course })
    expect(r.ok).toBe(true)
    expect(intent()!.mode).toBe('notify')
    // 旧路径（`tmp/<课>/gate-halt-mode.txt`）一个字都不该被写
    const legacy = path.join(scratchDir, course, 'gate-halt-mode.txt')
    expect(() => readFileSync(legacy, 'utf8')).toThrow()
  })

  it('原子写：写完后不留 `.<name>.<pid>.tmp`（tmp + rename，不是直写）', async () => {
    await postJson('setGateHaltMode', { mode: 'notify', untilHours: 1 })
    const { readdirSync } = await import('fs')
    expect(readdirSync(scratch).filter((n) => n.startsWith('.'))).toEqual([])
  })

  it('getGateHaltMode 是只读探针：读平台意图（缺 ⇒ halt）', async () => {
    await postJson('setGateHaltMode', { mode: 'notify', untilHours: 8 })
    expect((await postJson('getGateHaltMode', {})).message).toBe('notify')
    // 坏文件 ⇒ halt（**绝不猜成 notify**——训练侧同一条保守方向）
    writeFileSync(intentPath, '{"mode": "notify"', 'utf8')
    expect((await postJson('getGateHaltMode', {})).message).toBe('halt')
  })
})

describe('view/gate-halt 展示件（web 侧，不得 import stack 那份）', () => {
  it('时长档 = 2/4/8h + 不限时，UI 缺省 8h', () => {
    expect(view.GATE_HALT_DURATIONS.map((d) => d.hours)).toEqual([2, 4, 8, null])
    expect(view.DEFAULT_GATE_HALT_HOURS).toBe(8)
  })

  it('到点文案三态：未到 / 已到（说实话）/ 不限时', () => {
    const now = 1_760_000_000_000
    expect(view.gateHaltUntilText((now + 3600_000) / 1000, now)).toContain('自动回落 halt')
    // 过期后意图文件里那次 notify 还在，但训练侧已经是 halt——不许只显示一个过去的日期
    expect(view.gateHaltUntilText((now - 3600_000) / 1000, now)).toContain('训练侧按 halt')
    expect(view.gateHaltUntilText(null, now)).toContain('不限时')
  })

  it('回执行：没有 ⇒ 空串（不编「应该生效了」）；有 ⇒ effective_mode + 来源', () => {
    expect(view.gateHaltAppliedText(null)).toBe('')
    expect(view.gateHaltAppliedText({ effective_mode: 'halt', source: 'platform' })).toBe(
      '实际：halt（来源 platform）',
    )
  })
})

describe('console/api /api/state 的门禁两栏', () => {
  beforeAll(() => {
    writeFileSync(
      intentPath,
      JSON.stringify({ version: 1, mode: 'notify', until: null, by: 'console', at: 1 }),
      'utf8',
    )
    writeFileSync(
      appliedPath,
      JSON.stringify({
        version: 1,
        at: 2,
        pid: 1234,
        courses: { 'c5-tick': { effective_mode: 'halt', source: 'platform', until: null, at: 2 } },
      }),
      'utf8',
    )
  })

  it('intent + applied 两栏都在（意图 ≠ 事实：这里恰好一个是 notify、一个是 halt）', async () => {
    const view = await api.buildStateView('c5-tick')
    expect(view.gateHalt?.intent?.mode).toBe('notify')
    expect(view.gateHalt?.applied?.['c5-tick']?.effective_mode).toBe('halt')
    expect(view.gateHalt?.applied?.['c5-tick']?.source).toBe('platform')
  })

  it('坏回执 ⇒ 空表（观测面坏不得把 /api/state 带崩）；意图仍在', async () => {
    writeFileSync(appliedPath, 'not json', 'utf8')
    const view = await api.buildStateView('c5-tick')
    expect(view.gateHalt?.applied).toEqual({})
    expect(view.gateHalt?.intent?.mode).toBe('notify')
  })

  it('坏意图 ⇒ intent=null + error（页面显因；训练侧那头一律按缺省 halt 判）', async () => {
    writeFileSync(intentPath, '[1,2]', 'utf8')
    const view = await api.buildStateView('c5-tick')
    expect(view.gateHalt?.intent).toBeNull()
    expect(view.gateHalt?.error).toBeTruthy()
  })
})
