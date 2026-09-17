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
import { parseJsonc, stripComments } from '../src/core/jsonc'
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

describe('core/jsonc（与 python rl/jsonc.py 同语义）', () => {
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
