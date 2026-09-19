/**
 * loop-control.test.ts — 「暂停/恢复某课」的控制意图文件（R2d 操作面）
 *
 * 分层：src/server/actions/loop-control.ts（意图文件读写）+ core/paths.ts（路径常量）
 *
 * python 侧契约（`nn-training/rl/loop_control.py`，由 `nn-training/tests/test_loop_control.py`
 * 钉死）与本层是**同一份 JSON 的两个半边**：控制台写、训练进程读。所以这里逐条对齐的是
 * 「写出去的形状」「坏文件怎么办」「什么时候一次都不写盘」，而不是 UI 文案。
 *
 * 三条不可交易的性质：
 *  ① **保守方向 = 继续训练**：读不到/解析失败一律当「没有暂停意图」（控制面坏掉不该停掉整条腿）；
 *  ② **原子写**：训练进程每拍都在读，读到半个 JSON 等于读到坏文件（那是静默失效）；
 *  ③ **幂等且不误伤**：重复点一个方向不会把别的课程从表里挤掉。
 */

import { describe, expect, it } from 'bun:test'
import { existsSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import { loopAppliedPath, loopControlPath } from '../src/core/paths'
import {
  LOOP_CONTROL_VERSION,
  parseLoopControl,
  readLoopApplied,
  readLoopControl,
  readPauseFacts,
  setCoursePaused,
  writeLoopControl,
} from '../src/server/actions'

function tmpFile(name = 'loop-control.json'): string {
  return path.join(mkdtempSync(path.join(tmpdir(), 'tc-loopctl-')), name)
}

// ────────────────────────── 解析（纯函数） ──────────────────────────

describe('parseLoopControl（与 python parse_control 同判据）', () => {
  it('文档形状：paused 数组里是课程名，其余键忽略（控制台可以自由加注释字段）', () => {
    const c = parseLoopControl({ version: 1, paused: ['c5', 'c4-dodge'], updatedAt: 123 })
    expect(c.paused).toEqual(['c5', 'c4-dodge'])
    expect(c.found).toBe(true)
    expect(c.error).toBe('')
  })

  it('缺 paused / null ⇒ 空表且不是错误（「没有意图」是合法稳态）', () => {
    expect(parseLoopControl({ version: 1 })).toEqual({ paused: [], found: true, error: '' })
    expect(parseLoopControl({ paused: null }).paused).toEqual([])
  })

  it('paused 不是数组 ⇒ 空表 + 错误（保守：宁可不暂停）', () => {
    const c = parseLoopControl({ paused: 'c5' })
    expect(c.paused).toEqual([])
    expect(c.error).toContain('不是数组')
  })

  it('根不是对象 ⇒ 空表 + 错误', () => {
    expect(parseLoopControl(null).error).toBe('控制文件根不是对象')
    expect(parseLoopControl([]).error).toBe('控制文件根不是对象')
    expect(parseLoopControl('c5').paused).toEqual([])
  })

  it('非法课程名被忽略，合法的照常生效（一个坏名字不该让整份意图作废）', () => {
    const c = parseLoopControl({ paused: ['c5', '', '../etc', 'a/b', 'a b', 7, 'c5'] })
    expect(c.paused).toEqual(['c5'])
    expect(c.error).toContain('非法课程名')
  })
})

// ────────────────────────── 读盘 ──────────────────────────

describe('readLoopControl（永不抛）', () => {
  it('文件不存在 ⇒ 空意图、found=false、无错误', () => {
    const c = readLoopControl(tmpFile('nope.json'))
    expect(c).toEqual({ paused: [], found: false, error: '' })
  })

  it('坏 JSON ⇒ 空意图 + 原因（保守：继续训练）', () => {
    const f = tmpFile()
    writeFileSync(f, '{ 这不是 JSON', 'utf8')
    const c = readLoopControl(f)
    expect(c.paused).toEqual([])
    expect(c.error).toContain('控制文件读失败')
  })

  it('路径常量默认落在 tmp/ 且可被 BCITY_* 覆盖（单测重定向，生产零变化）', () => {
    expect(loopControlPath()).toContain(path.join('tmp', 'loop-control.json'))
    expect(loopAppliedPath()).toContain(path.join('tmp', 'loop-control.applied.json'))
    const old = { a: process.env.BCITY_LOOP_CONTROL, b: process.env.BCITY_LOOP_APPLIED }
    process.env.BCITY_LOOP_CONTROL = '/x/ctl.json'
    process.env.BCITY_LOOP_APPLIED = '/x/app.json'
    try {
      expect(loopControlPath()).toBe('/x/ctl.json')
      expect(loopAppliedPath()).toBe('/x/app.json')
    } finally {
      if (old.a === undefined) delete process.env.BCITY_LOOP_CONTROL
      else process.env.BCITY_LOOP_CONTROL = old.a
      if (old.b === undefined) delete process.env.BCITY_LOOP_APPLIED
      else process.env.BCITY_LOOP_APPLIED = old.b
    }
  })
})

// ────────────────────────── 写盘 ──────────────────────────

describe('writeLoopControl（原子写）', () => {
  it('写出可被 python 读的形状（version + paused），且不留 tmp 残渣', () => {
    const f = tmpFile()
    expect(writeLoopControl(['c5'], f)).toBeNull()
    const body = JSON.parse(readFileSync(f, 'utf8')) as Record<string, unknown>
    expect(body).toEqual({ version: LOOP_CONTROL_VERSION, paused: ['c5'] })
    // 原子性：同目录不留半个文件（训练侧每拍都在读）
    expect(readdirSync(path.dirname(f)).filter((n) => n.includes('.tmp'))).toEqual([])
  })

  it('目录不可写 ⇒ 返回人读错误而不抛（动作层原样上报）', () => {
    const err = writeLoopControl([], path.join(tmpFile(), 'nope', 'x.json'))
    expect(err).toBeTruthy()
  })
})

describe('setCoursePaused（幂等 + 不误伤）', () => {
  it('暂停一门课：写进表里；再暂停一次不重复（幂等）', () => {
    const f = tmpFile()
    expect(setCoursePaused('c5', true, f).ok).toBe(true)
    const again = setCoursePaused('c5', true, f)
    expect(again.ok).toBe(true)
    expect(again.message).toContain('幂等')
    expect(readLoopControl(f).paused).toEqual(['c5'])
  })

  it('已有别的课暂停 ⇒ 新暂停/恢复都不碰它（按课程独立，操作域不扩大）', () => {
    const f = tmpFile()
    setCoursePaused('c4-dodge', true, f)
    setCoursePaused('c5-tick', true, f)
    expect(readLoopControl(f).paused).toEqual(['c4-dodge', 'c5-tick'])
    setCoursePaused('c4-dodge', false, f)
    expect(readLoopControl(f).paused).toEqual(['c5-tick'])
  })

  it('恢复一门没暂停的课 ⇒ 幂等成功（不编造错误）', () => {
    const f = tmpFile()
    const r = setCoursePaused('c5', false, f)
    expect(r.ok).toBe(true)
    expect(readLoopControl(f).paused).toEqual([])
  })

  it('空课程名 / 非法课程名 ⇒ 拒绝且**一次都不写盘**', () => {
    const f = tmpFile()
    for (const bad of ['', '  ', '../etc', 'a/b', 'a b']) {
      const r = setCoursePaused(bad, true, f)
      expect(r.ok).toBe(false)
    }
    expect(existsSync(f)).toBe(false) // 非法输入不该在盘上留下任何东西
  })

  it('意图文件已坏 ⇒ 拒绝覆盖（可能有人手改/另一份工具在写，先让人看明白）', () => {
    const f = tmpFile()
    writeFileSync(f, '{ broken', 'utf8')
    const r = setCoursePaused('c5', true, f)
    expect(r.ok).toBe(false)
    expect(r.message).toContain('未改动')
    expect(readFileSync(f, 'utf8')).toBe('{ broken') // 原样保留
  })

  it('消息里说清生效时机（下一拍 / 队列与账本保留）', () => {
    const f = tmpFile()
    expect(setCoursePaused('c5', true, f).message).toContain('下一拍生效')
    expect(setCoursePaused('c5', false, f).message).toContain('恢复')
  })
})

// ────────────────────────── 回执（意图 ≠ 事实） ──────────────────────────

describe('readLoopApplied（训练进程写的回执，按 pid 存活过滤）', () => {
  it('文件不存在 ⇒ 空且 alive=false', () => {
    expect(readLoopApplied(tmpFile('nope.json'))).toEqual({
      paused: [],
      pid: 0,
      alive: false,
      at: 0,
    })
  })

  it('进程活着 ⇒ 回执就是事实（哪几门课真的被停着）', () => {
    const f = tmpFile('applied.json')
    writeFileSync(f, JSON.stringify({ version: 1, at: 1234, pid: 4242, paused: ['c5'] }), 'utf8')
    const a = readLoopApplied(f, () => true)
    expect(a).toEqual({ paused: ['c5'], pid: 4242, alive: true, at: 1234 })
  })

  it('★ 进程已死 ⇒ 残留文件不作数（否则界面会永远显示「已暂停」）', () => {
    const f = tmpFile('applied.json')
    writeFileSync(f, JSON.stringify({ pid: 999999, paused: ['c5'] }), 'utf8')
    const a = readLoopApplied(f, () => false)
    expect(a.paused).toEqual([])
    expect(a.alive).toBe(false)
  })

  it('形状坏/pid 缺失 ⇒ 空（永不让观测面炸）', () => {
    const f = tmpFile('applied.json')
    writeFileSync(f, '{ nope', 'utf8')
    expect(readLoopApplied(f, () => true).alive).toBe(false)
    writeFileSync(f, JSON.stringify({ paused: ['c5'] }), 'utf8')
    expect(readLoopApplied(f, () => true)).toMatchObject({ paused: [], pid: 0, alive: false })
  })

  it('回执里的非法课程名被丢掉（与意图同一条合法性判据）', () => {
    const f = tmpFile('applied.json')
    writeFileSync(f, JSON.stringify({ pid: 1, paused: ['c5', '../x', 7] }), 'utf8')
    expect(readLoopApplied(f, () => true).paused).toEqual(['c5'])
  })
})

describe('readPauseFacts（一次读两侧）', () => {
  it('返回意图 + 已生效，两个来源各不相同', () => {
    const intentFile = process.env.BCITY_LOOP_CONTROL
    const appliedFile = process.env.BCITY_LOOP_APPLIED
    const a = tmpFile('applied.json')
    const i = path.join(path.dirname(a), 'loop-control.json')
    writeFileSync(i, JSON.stringify({ paused: ['c5', 'c6'] }), 'utf8')
    writeFileSync(a, JSON.stringify({ pid: process.pid, paused: ['c5'] }), 'utf8')
    process.env.BCITY_LOOP_CONTROL = i
    process.env.BCITY_LOOP_APPLIED = a
    try {
      const f = readPauseFacts()
      expect(f.intent.sort()).toEqual(['c5', 'c6'])
      // 当前进程 pid 一定活着 ⇒ c5 是「已生效」，c6 是「待生效」（还没被施加）
      expect(f.applied).toEqual(['c5'])
    } finally {
      if (intentFile === undefined) delete process.env.BCITY_LOOP_CONTROL
      else process.env.BCITY_LOOP_CONTROL = intentFile
      if (appliedFile === undefined) delete process.env.BCITY_LOOP_APPLIED
      else process.env.BCITY_LOOP_APPLIED = appliedFile
    }
  })
})
