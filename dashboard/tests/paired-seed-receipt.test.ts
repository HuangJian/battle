/**
 * paired-seed-receipt.test.ts — 开课回执的「配对 rotateSeed 核对行」（plan/accident.plan.md §2.5）。
 *
 *  事故形态：两臂同 rotateSeed 靠人手在命令行传，第一次就传漏/传错（1789926833 vs 1789926915）
 *  ⇒ 配对失败返工。修法（§2.3）：V 写进两门课的课程文件。本文件钉开课那一刻要看到的事实：
 *
 *   ① 本课声明的 V（课程文件 `paired_rotate_seed`）；
 *   ② 同 V 的其它课程（= 机器口径的「配对对端」）；没有对端 ⇒ ★ 不许按配对口径结算；
 *   ③ 各臂账本末条 `run_start.rotateSeed` 是否就是那把 V（≠ ⇒ ★；无账本 ⇒ 还没跑过）；
 *   ④ 未声明 ⇒ 单腿口径一行（不打扰既有课程）；
 *   ⑤ 读不到/坏文件**绝不阻塞开课**（降级成一行说明）。
 *
 *  环境重定向：课程目录与 traj 根指向临时目录（`core/paths.ts` 惰性取值）。
 */

import { afterAll, beforeAll, describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import {
  PAIRED_KEY,
  composePairedLines,
  declaredPairedSeed,
  latestRunStartSeed,
  pairedCourses,
  pairedSeedReceipt,
} from '../src/stack/paired-seed-receipt'

const DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-paired-'))
const CUR = path.join(DIR, 'curricula')
const TRAJ = path.join(DIR, 'traj')
process.env.BCITY_CURRICULA_DIR = CUR
process.env.BCITY_TMP_LOGS_DIR = TRAJ

/** 2026-09-21 C 双腿实际用的那把 V（账本实证）。 */
const V = 20260921

function writeCourse(name: string, seed?: number | null): void {
  const body: Record<string, unknown> = { name, mode: 'per-tick', env: {} }
  if (seed !== undefined) body[PAIRED_KEY] = seed
  writeFileSync(path.join(CUR, `${name}.jsonc`), JSON.stringify(body, null, 2), 'utf-8')
}

function writeLedger(course: string, seeds: (number | null)[]): void {
  mkdirSync(path.join(TRAJ, course), { recursive: true })
  const lines = seeds.map((s, i) =>
    s === null
      ? JSON.stringify({ event: 'iteration', iter: i + 1 })
      : JSON.stringify({ event: 'run_start', iter: 0, rotateSeed: s }),
  )
  writeFileSync(path.join(TRAJ, course, 'training_log.jsonl'), `${lines.join('\n')}\n`, 'utf-8')
}

beforeAll(() => {
  mkdirSync(CUR, { recursive: true })
  mkdirSync(TRAJ, { recursive: true })
})

afterAll(() => {
  try {
    rmSync(DIR, { recursive: true, force: true })
  } catch {
    /* noop */
  }
})

describe('declaredPairedSeed / pairedCourses：配对 = 同一把 V', () => {
  it('只认显式数字；未写 / null / 坏文件 ⇒ null（合法性由 python load_course 负责）', () => {
    writeCourse('p-a', V)
    writeCourse('p-none')
    writeCourse('p-null', null)
    writeFileSync(path.join(CUR, 'p-broken.jsonc'), '{ "name": "p-broken", // 半截', 'utf-8')
    expect(declaredPairedSeed('p-a')).toBe(V)
    expect(declaredPairedSeed('p-none')).toBeNull()
    expect(declaredPairedSeed('p-null')).toBeNull()
    expect(declaredPairedSeed('p-broken')).toBeNull()
    expect(declaredPairedSeed('no-such-course')).toBeNull()
  })

  it('扫出同 V 的其它课程：自己不算、别 V 不算、坏文件跳过', () => {
    // 本用例自己造一门**独有**的 V：同一份临时课程目录会被多个用例共用，
    // 断言按「包含/不包含」写，别钉全量列表。
    const own = 20271231
    writeCourse('q-a', own)
    writeCourse('q-b', own)
    writeCourse('q-other', own + 1)
    writeCourse('q-none')
    const got = pairedCourses(own, 'q-a').map((x) => x.course)
    expect(got).toEqual(['q-b'])
    expect(pairedCourses(own, 'q-lonely').map((x) => x.course)).toEqual(['q-a', 'q-b'])
    expect(pairedCourses(999, 'q-a')).toEqual([]) // 无人用这把 V
  })
})

describe('latestRunStartSeed：末条 run_start 才是事实', () => {
  it('续跑会再写一条 ⇒ 取末条；半截行跳过；无账本 ⇒ null', () => {
    writeLedger('r-1', [111, 222])
    expect(latestRunStartSeed('r-1')).toBe(222)
    writeLedger('r-2', [null])
    expect(latestRunStartSeed('r-2')).toBeNull()
    expect(latestRunStartSeed('r-nope')).toBeNull()
  })
})

describe('composePairedLines：★ 两支 + 单腿', () => {
  it('未声明 ⇒ 单腿一行', () => {
    const lines = composePairedLines({ declared: null, siblings: [], siblingSeeds: [] })
    expect(lines).toHaveLength(1)
    expect(lines[0]).toContain('单腿')
  })

  it('声明了却没有对端 ⇒ ★ 配对无对端', () => {
    const lines = composePairedLines({ declared: V, siblings: [], siblingSeeds: [] })
    expect(lines.join('\n')).toContain('配对无对端')
    expect(lines.join('\n')).toContain('不许按配对口径结算')
  })

  it('对端同 V ⇒ ✓；对端异 V ⇒ ★；对端无账本 ⇒ 还没跑过（不喊）', () => {
    const ok = composePairedLines({
      declared: V,
      siblings: [{ course: 'sib', seed: V }],
      siblingSeeds: [{ course: 'sib', seed: V }],
    })
    expect(ok.join('\n')).toContain('✓ 同 V')
    expect(ok.join('\n')).not.toContain('★')

    const bad = composePairedLines({
      declared: V,
      siblings: [{ course: 'sib', seed: V }],
      siblingSeeds: [{ course: 'sib', seed: 1789926915 }],
    })
    expect(bad.join('\n')).toContain('★ 配对各臂：sib')
    expect(bad.join('\n')).toContain('1789926915')

    const fresh = composePairedLines({
      declared: V,
      siblings: [{ course: 'sib', seed: V }],
      siblingSeeds: [{ course: 'sib', seed: null }],
    })
    expect(fresh.join('\n')).toContain('账本无 run_start（还没跑过）')
    expect(fresh.join('\n')).not.toContain('★')
  })
})

describe('pairedSeedReceipt：真课程目录 + 真账本（开课回执用它）', () => {
  it('C 对：声明同 V + 对端账本同 V ⇒ 三行事实、无 ★', () => {
    writeCourse('x20-pair-w', V)
    writeCourse('x20-pair-null', V)
    writeLedger('x20-pair-null', [V])
    const text = pairedSeedReceipt('x20-pair-w').join('\n')
    expect(text).toContain(`V=${V}（课程声明 paired_rotate_seed）`)
    expect(text).toContain('同 V 课程 = ')
    expect(text).toContain('x20-pair-null 账本 run_start.rotateSeed=20260921 ✓ 同 V')
    expect(text).not.toContain('★')
  })

  it('对端账本停在上一次的手传值 ⇒ ★（正是 2026-09-21 那种错配）', () => {
    writeCourse('x20-old-w', V)
    writeCourse('x20-old-null', V)
    writeLedger('x20-old-null', [1789926915])
    const text = pairedSeedReceipt('x20-old-w').join('\n')
    expect(text).toContain('★ 配对各臂：x20-old-null')
    expect(text).toContain('那一臂上一次不在同一把 V 上')
  })

  it('永不抛：课程目录不可用时也返回一行说明', () => {
    const prev = process.env.BCITY_CURRICULA_DIR
    process.env.BCITY_CURRICULA_DIR = path.join(DIR, 'nope')
    try {
      const lines = pairedSeedReceipt('x20-pair-w')
      expect(lines.length).toBeGreaterThan(0)
      // 目录读不到 ⇒ 声明的 V 也读不到 ⇒ 单腿口径一行（不炸）
      expect(lines[0]).toContain('配对 rotateSeed')
    } finally {
      if (prev === undefined) delete process.env.BCITY_CURRICULA_DIR
      else process.env.BCITY_CURRICULA_DIR = prev
    }
  })
})
