/**
 * redo-ppo.test.ts — ★M3「重发本轮 PPO」的控制台半边（plan/ppo-job-lifecycle-ownership §4）
 *
 * 分层：src/server/actions/redo-ppo.ts（意图文件写 + 回执读）
 *
 * python 侧契约（`nn-training/trainer/loop_control.py::parse_redo/consume_redo`，由
 * `nn-training/tests/trainer/test_ppo_redo.py` 钉死）与本层是**同一份 JSON 的两个半边**：
 * 控制台写、训练进程读。这里逐条对齐的是「写出去的形状」与「什么时候一次都不写盘」：
 *
 *  ① 形状 = `{version, it, at}`：`at` 是**去重键**（训练侧按它判断「同一份意图只执行一次」）
 *     ——写丢了它，训练进程每一拍都会再撤一次刚重发的活（无限重发）；
 *  ② `it<=0` = 对当前轮生效（控制台缺省）；回填面板行上的落后 iter 只会让意图永远等不到
 *     那一轮 ⇒ 按钮「点了没反应」（判据见 `redoPpo` 的注释）；
 *  ③ 坏课程名**一次都不写盘**（写侧挡住，不给训练侧留坏文件）；
 *  ④ 原子写：训练侧每拍都在读——读到半个 JSON 等于读到坏文件（那边保守不动作 = 静默失效）。
 */

import { describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, readdirSync, writeFileSync, existsSync, mkdirSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import {
  REDO_APPLIED_NAME,
  REDO_NAME,
  REDO_VERSION,
  readRedoApplied,
  redoAppliedPath,
  redoIntentPath,
  redoPpo,
  writeRedoIntent,
} from '../src/server/actions/redo-ppo'

function tmpRoot(): string {
  return mkdtempSync(path.join(tmpdir(), 'tc-redoppo-'))
}

describe('writeRedoIntent（控制台写 → 训练进程读的形状）', () => {
  it('★写出的形状 = python parse_redo 读得懂的：{version, it, at}', () => {
    const root = tmpRoot()
    const { err, at } = writeRedoIntent('x27-obs', 0, 1758000000.25, root)
    expect(err).toBe('')
    expect(at).toBe(1758000000.25)
    const p = redoIntentPath('x27-obs', root)
    expect(p).toBe(path.join(root, 'x27-obs', REDO_NAME))
    const doc = JSON.parse(readFileSync(p, 'utf8')) as Record<string, unknown>
    expect(doc.version).toBe(REDO_VERSION)
    expect(doc.it).toBe(0) // 0 = 当前轮（控制台缺省：不回填面板上落后的 iter）
    expect(doc.at).toBe(1758000000.25) // 去重键：训练侧按它判「这份意图只执行一次」
  })

  it('原子写：目录里不留临时文件（训练侧只会读到完整 JSON 或旧内容）', () => {
    const root = tmpRoot()
    writeRedoIntent('c5', 0, 1.0, root)
    const files = readdirSync(path.join(root, 'c5'))
    expect(files).toEqual([REDO_NAME])
  })

  it('★坏课程名（路径穿透/空/非法字符）⇒ 一次都不写盘', () => {
    const root = tmpRoot()
    for (const bad of ['', '   ', '../etc', 'a/b', 'a\\b', 'c5 课']) {
      const { err } = writeRedoIntent(bad, 0, 1.0, root)
      expect(err).not.toBe('')
    }
    expect(readdirSync(root)).toEqual([])
  })
})

describe('readRedoApplied（回执：读到没有 + 何时）', () => {
  it('回执不存在 ⇒ 空（永不抛）', () => {
    const root = tmpRoot()
    expect(readRedoApplied('x27-obs', root)).toEqual({
      it: 0,
      at: 0,
      pid: 0,
      alive: false,
      ts: 0,
    })
  })

  it('坏回执 / 形状不对 ⇒ 空（观测面坏了不该带崩面板）', () => {
    const root = tmpRoot()
    mkdirSync(path.join(root, 'c5'), { recursive: true })
    writeFileSync(redoAppliedPath('c5', root), '{ not json', 'utf8')
    expect(readRedoApplied('c5', root).at).toBe(0)
  })

  it('★pid 存活核对：残留回执（进程已死）不算「在读」', () => {
    const root = tmpRoot()
    mkdirSync(path.join(root, 'c5'), { recursive: true })
    writeFileSync(
      redoAppliedPath('c5', root),
      JSON.stringify({ version: 1, it: 188, at: 1758000000.5, pid: 4242, ts: 1758000001 }),
      'utf8',
    )
    expect(readRedoApplied('c5', root, () => true)).toMatchObject({
      it: 188,
      at: 1758000000.5,
      pid: 4242,
      alive: true,
    })
    expect(readRedoApplied('c5', root, () => false).alive).toBe(false)
  })
})

/** 把 `tmpLogsDir()`（惰性取值）临时重定向到临时根——动作面默认写那里，单测不能污染仓根 tmp/。 */
function withRoot<T>(root: string, fn: () => T): T {
  const prev = process.env.BCITY_TMP_LOGS_DIR
  process.env.BCITY_TMP_LOGS_DIR = root
  try {
    return fn()
  } finally {
    if (prev === undefined) delete process.env.BCITY_TMP_LOGS_DIR
    else process.env.BCITY_TMP_LOGS_DIR = prev
  }
}

describe('redoPpo（动作面：写意图 + 把「读到没有」说穿）', () => {
  it('★成功路径：意图落到 `tmp/<课>/redo-ppo.json`；回执没到 = 还没进程读过任何重发意图', () => {
    const root = tmpRoot()
    const r = withRoot(root, () => redoPpo('x27-noleash', 0, 1758000000.0))
    expect(r.ok).toBe(true)
    expect(r.message).toContain('已请求重发本轮 PPO')
    expect(r.detail?.some((d) => d.includes(redoIntentPath('x27-noleash', root)))).toBe(true)
    expect(r.detail?.some((d) => d.includes('不硬杀在算的云机'))).toBe(true)
    expect(r.detail?.some((d) => d.includes('还没有（'))).toBe(true)
    expect(existsSync(redoIntentPath('x27-noleash', root))).toBe(true)
  })

  it('坏课程名 ⇒ ok:false 且不写盘（与 writeRedoIntent 同判据）', () => {
    const root = tmpRoot()
    const r = withRoot(root, () => redoPpo('../etc', 0, 1758000000.0))
    expect(r.ok).toBe(false)
    expect(r.message).toContain('课程名非法')
    expect(readdirSync(root)).toEqual([])
  })
})

describe('与训练侧的去重键对账（读到 / 还没轮到）', () => {
  it('★同 at 的回执 ⇒ 「已读到你刚写的那一份」（判据 = at 相等，与 python consume_redo 同一把尺）', () => {
    const root = tmpRoot()
    const at = 1758000123.0
    mkdirSync(path.join(root, 'c5'), { recursive: true })
    writeFileSync(
      redoAppliedPath('c5', root),
      JSON.stringify({ version: 1, it: 12, at, pid: process.pid, ts: at + 1 }),
      'utf8',
    )
    const r = withRoot(root, () => redoPpo('c5', 0, at))
    expect(r.ok).toBe(true)
    expect(r.detail?.some((d) => d.includes('已读到你刚写的那一份'))).toBe(true)
  })

  it('回执的 at 与本次不同 ⇒ 还没轮到（下一拍）——两个 at 就是两个去重键', () => {
    const root = tmpRoot()
    mkdirSync(path.join(root, 'c5'), { recursive: true })
    writeFileSync(
      redoAppliedPath('c5', root),
      JSON.stringify({ version: 1, it: 11, at: 1758000100.0, pid: 1, ts: 0 }),
      'utf8',
    )
    const r = withRoot(root, () => redoPpo('c5', 0, 1758000200.0))
    expect(r.detail?.some((d) => d.includes('还不是你刚写的这一份'))).toBe(true)
    expect(REDO_APPLIED_NAME).toBe('redo-ppo.applied.json')
  })

  it('残留回执（写它的进程已死）⇒ 如实点名，不假装在读', () => {
    const root = tmpRoot()
    mkdirSync(path.join(root, 'c5'), { recursive: true })
    writeFileSync(
      redoAppliedPath('c5', root),
      JSON.stringify({ version: 1, it: 7, at: 1758000100.0, pid: 999999999, ts: 0 }),
      'utf8',
    )
    const r = withRoot(root, () => redoPpo('c5', 0, 1758000100.0))
    expect(r.detail?.some((d) => d.includes('已退出（残留回执）'))).toBe(true)
  })
})
