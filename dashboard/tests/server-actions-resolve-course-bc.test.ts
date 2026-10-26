/**
 * server-actions-resolve-course-bc.test.ts — §384 种子路径读课程 bc 字段；未知课程回落 legacy 硬编码
 *
 * 分层：src/server/actions（resolveCourseBc）+ src/core/jsonc（共用 JSONC 解析器）
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 *
 * 2026-09-17 事故回归：`curricula/x1-rebirth.jsonc` 用了**行内**注释
 * （`"lr": 0.0005, // …`），而 dashboard 当时手搓的剥离逻辑只认「整行 `//`」⇒
 * JSON.parse 抛 SyntaxError ⇒ 静默回退 legacy ⇒ 报出「初始权重缺失且 BC 产物不存在:
 * tmp/ep60/…」这种指向完全无关文件的错误。三条用例锁住：① 该课程现在能读到真 bc；
 * ② 解析失败必须**响亮抛错**而不是回退；③ 仓内所有真实 JSONC 文件都必须解析得动。
 */

import { mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { describe, expect, it } from 'bun:test'
import { dropTrailingCommas, normalizeJsonc, parseJsonc, stripComments } from '../src/core/jsonc'
import { CURRICULA_DIR, NN_TRAINING } from '../src/core/paths'
import { actions } from './helpers/console-fixture'

describe('console/actions.resolveCourseBc（§384：种子路径读课程 bc 字段）', () => {
  it('p3-rd1/vk1 → 课程 bc（.ckpt.60）；未知课程 → legacy 硬编码', () => {
    expect(actions.resolveCourseBc('p3-rd1')).toContain('weights.json.ckpt.60')
    expect(actions.resolveCourseBc('p3-vk1')).toContain('weights.json.ckpt.60')
    expect(actions.resolveCourseBc('no-such-course-xyz').endsWith('weights.json')).toBe(true)
    expect(actions.resolveCourseBc('no-such-course-xyz')).not.toContain('ckpt')
  })

  it('行内 `//` 注释的课程照常读到 bc（2026-09-17 x1-rebirth 事故回归）', () => {
    // 该文件的 "lr" / "epochs" / "seed_rotate" 等行都带行内注释。
    // 课程名 = 课程文件名（`nn-training/curricula/<course>.jsonc`）：事故当时的
    // 课程就是 x1-rebirth（提交名 x1-rebirth.jsonc，从未有过 -a2 文件）。
    expect(actions.resolveCourseBc('x1-rebirth')).toBe(
      path.join(NN_TRAINING, 'weights', 'x1-rebirth', 'scratch-init.json'),
    )
  })

  it('课程文件在但解析不了 → 抛错点名文件（不许回退 legacy）', () => {
    const dir = path.join(os.tmpdir(), `bcity-jsonc-${process.pid}`)
    mkdirSync(dir, { recursive: true })
    const prev = process.env.BCITY_CURRICULA_DIR
    try {
      // 未闭合的对象 → 必然解析失败
      writeFileSync(path.join(dir, 'broken.jsonc'), '{ "bc": "x", ', 'utf-8')
      process.env.BCITY_CURRICULA_DIR = dir
      expect(() => actions.resolveCourseBc('broken')).toThrow(/课程文件解析失败/)
      expect(() => actions.resolveCourseBc('broken')).toThrow(/broken\.jsonc/)
      // 对照组：同目录下带行内注释的合法 JSONC 必须读到 bc（不是「一律抛」）
      writeFileSync(
        path.join(dir, 'inline.jsonc'),
        '{\n  "bc": "nn-training/weights/fake/w.json", // 行内注释\n  "lr": 5e-4, // 又一条\n}\n',
        'utf-8',
      )
      expect(actions.resolveCourseBc('inline')).toBe(
        path.join(NN_TRAINING, 'weights', 'fake', 'w.json'),
      )
    } finally {
      if (prev === undefined) delete process.env.BCITY_CURRICULA_DIR
      else process.env.BCITY_CURRICULA_DIR = prev
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('core/jsonc（与 python common/jsonc.py 同语义）', () => {
  it('字符串内的 // 原样保留，注释被剥掉', () => {
    expect(parseJsonc('{\n  "url": "https://a/b", // 注释\n  "n": 1\n}\n')).toEqual({
      url: 'https://a/b',
      n: 1,
    })
  })

  it('剥离注释保留换行（报错行号不漂移）', () => {
    expect(stripComments('a\n// x\nb').split('\n')).toHaveLength(3)
    expect(stripComments('a // x\nb').split('\n')).toHaveLength(2)
  })

  it('尾逗号（,} / ,]）与转义引号', () => {
    expect(parseJsonc('{ "a": [1, 2,], "b": "x\\"//y", }')).toEqual({ a: [1, 2], b: 'x"//y' })
  })

  it('仓内真实 JSONC 全部可解析（这条本该拦住 2026-09-17 事故）', () => {
    const files = readdirSync(CURRICULA_DIR)
      .filter((f) => f.endsWith('.jsonc'))
      .map((f) => path.join(CURRICULA_DIR, f))
    files.push(path.join(NN_TRAINING, 'ladder', 'LEDGER.jsonc'))
    expect(files.length).toBeGreaterThan(10)
    const bad: string[] = []
    for (const f of files) {
      try {
        const d = parseJsonc(readFileSync(f, 'utf-8'))
        if (typeof d !== 'object' || d === null) bad.push(`${f}（非对象）`)
      } catch (e) {
        bad.push(`${f}：${e instanceof Error ? e.message : String(e)}`)
      }
    }
    expect(bad).toEqual([])
  })
})

/** 单趟快路（`normalizeJsonc`）与两步参考实现的**逐字节等价**。
 *
 *  快路是为了性能而重写的（实测 223 个真实文件 28.5ms → 3.6ms），但它的语义必须与
 *  `stripComments` + `dropTrailingCommas` 一字不差——那两个函数是「与 python 同语义」
 *  的**可读参考实现**，也是后续任何改动的对照物。所以拿真仓语料 + 已知边角逐条对拍，
 *  而不是只断言「能 parse」。
 *
 *  另注：**不用** `Bun.JSONC.parse`（实测它是更宽的超集：块注释 / 单引号 / BOM / 前导零 /
 *  十六进制 / 尾随垃圾都接受），所以下面还有一条「块注释仍响亮报错」的语义钉子。 */
describe('jsonc-fastpath：单趟规范化 ≡ 参考实现（逐字节）', () => {
  it('仓内 223 个真实课程文件（1.35MB）逐字节相同，且解析结果不变', () => {
    const files = readdirSync(CURRICULA_DIR)
      .filter((f) => f.endsWith('.jsonc'))
      .map((f) => path.join(CURRICULA_DIR, f))
    expect(files.length).toBeGreaterThan(100) // 语料真的在（别让它静默变成空跑）
    const diffs: string[] = []
    for (const f of files) {
      const src = readFileSync(f, 'utf-8')
      const ref = dropTrailingCommas(stripComments(src))
      if (normalizeJsonc(src) !== ref) diffs.push(f)
      if (JSON.stringify(parseJsonc(src)) !== JSON.stringify(JSON.parse(ref))) diffs.push(f)
    }
    expect(diffs).toEqual([])
  })

  it('边角：注释夹在尾逗号与闭合括号之间（两趟写法的顺序语义）', () => {
    const cases = [
      '{\n  "a": 1, // 注\n}',
      '{\n  "a": 1,\n  // 注\n}',
      '{\n  "a": 1,    \n\n  }',
      '[1, 2 , ]',
      'a\n// x\nb',
      'a // x\nb',
      '{ "a": [1, 2,], "b": "x\\"//y", }',
      "{ 'a': 'http://x' } // 尾部注释",
      '{\n  "u": "src/**/*.ts",\n  "n": 1,\n}',
      '{ "a": "未闭合, }',
      '{\n  // 全注释\n}',
    ]
    for (const src of cases) {
      const ref = dropTrailingCommas(stripComments(src))
      expect(normalizeJsonc(src)).toBe(ref)
    }
  })

  it('语义钉子：块注释 / 单引号 / 前导零 / 尾随垃圾仍然响亮报错（不被 `Bun.JSONC` 的宽松带上）', () => {
    expect(() => parseJsonc('{ "a": 1 /* 块 */ }')).toThrow()
    expect(() => parseJsonc("{ 'a': 1 }")).toThrow()
    expect(() => parseJsonc('[01]')).toThrow()
    expect(() => parseJsonc('{} {}')).toThrow()
  })
})
