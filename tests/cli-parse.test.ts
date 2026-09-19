import { describe, it, expect } from 'bun:test'
import { flag, parseDifficulties } from '../tools/lib/cli'

/**
 * M0 §3.1 protocol applied to difficulty list specs: a bare word ("hard")
 * must parse as ONE key, never iterate into characters (h/a/r/d) — that
 * char-iteration footgun silently ran 4× classic sweeps labeled as four
 * difficulties (2026-08-26, sweep-winrate --difficulties hard).
 */
describe('parseDifficulties', () => {
  const EVAL_KEYS = ['classic', 'hard', 'chaos']
  const ALL_KEYS = ['relax', 'classic', 'hard', 'chaos']

  it('absent spec → fallback defaults', () => {
    expect(parseDifficulties(undefined, EVAL_KEYS)).toEqual(['classic', 'hard', 'chaos'])
  })

  it('"hard" → ["hard"] — one token, not per-character h,a,r,d', () => {
    expect(parseDifficulties('hard', EVAL_KEYS, ALL_KEYS)).toEqual(['hard'])
  })

  it('"classic,hard,chaos" → three keys in order', () => {
    expect(parseDifficulties('classic,hard,chaos', EVAL_KEYS, ALL_KEYS)).toEqual([
      'classic',
      'hard',
      'chaos',
    ])
  })

  it('trims whitespace around tokens', () => {
    expect(parseDifficulties(' hard , chaos ', EVAL_KEYS, ALL_KEYS)).toEqual(['hard', 'chaos'])
  })

  it('unknown key throws with valid keys in the message', () => {
    expect(() => parseDifficulties('hardd', EVAL_KEYS, ALL_KEYS)).toThrow(/hardd/)
  })

  it('empty spec / empty token throws', () => {
    expect(() => parseDifficulties('', EVAL_KEYS, ALL_KEYS)).toThrow()
    expect(() => parseDifficulties('hard,,chaos', EVAL_KEYS, ALL_KEYS)).toThrow()
    expect(() => parseDifficulties(',', EVAL_KEYS, ALL_KEYS)).toThrow()
  })
})

/**
 * 布尔存在位必须用 `flag()`（`process.argv.includes`），**不能**用 `arg() !== undefined`
 * ——`arg()` 取「下一个 token」，把开关写在命令行末尾时下一个 token 不存在，开关被静默
 * 忽略（2026-09-19 实测：`eval-course-ckpt … --no-dist` 末尾 ⇒ 仍然跑了混合分派）。
 * 本用例把两种写法的差别钉住：末尾 / 后跟另一个 flag / 缺席 三种位置。
 */
describe('flag（布尔存在位，位置无关）', () => {
  const withArgv = (extra: string[], body: () => void): void => {
    const saved = process.argv.slice()
    try {
      process.argv = [...saved.slice(0, 2), ...extra]
      body()
    } finally {
      process.argv = saved
    }
  }

  it('写在命令行末尾仍为 true（arg() 式实现会误判为缺席）', () => {
    withArgv(['--games', '2', '--no-dist'], () => expect(flag('no-dist')).toBe(true))
  })

  it('后跟另一个 flag / 缺席 → 真值只由出现与否决定', () => {
    withArgv(['--no-dist', '--out', 'x.jsonl'], () => expect(flag('no-dist')).toBe(true))
    withArgv(['--out', 'x.jsonl'], () => expect(flag('no-dist')).toBe(false))
  })

  it('子串旗标不误命中', () => {
    withArgv(['--no-dist-x'], () => expect(flag('no-dist')).toBe(false))
  })
})
