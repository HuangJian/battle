/**
 * child-failure-summary.test.ts — sampler-agent 子进程失败摘要（2026-09-14 mac 节点事故）。
 *
 * 事故样本：mac 节点跑 BC 语料（export-godai-bc）胜局一律退出 1，训练机侧收到的 reason 是
 * stderr 的**前** 300 字符 —— 恰好整段都是 bun 的源码帧：
 *   `111 |   const masks = new Uint8Array(N * MASK_DIM)\n112 |   const conditions = ...`
 * 真正的 `TypeError: ...` 与栈顶帧都在其后，被切掉 ⇒ 17 局失败只表现为一行无错误类型的乱码，
 * 无法定位根因。summarizeChildFailure 把「错误类型行 + 栈顶帧 + 源码帧」提到前面。
 */

import { describe, expect, it } from 'bun:test'
import { summarizeChildFailure } from '../../tools/agent/sampler-agent'

/** 还原 bun 崩溃输出的真实结构（源码帧在前，error 行在后）。 */
const BUN_CRASH = [
  '66 |       for (let c = 0; c < GRID; c++) {',
  '67 |         this.grid[r][c] = "empty"',
  '68 |       }',
  '69 |     }',
  '111 |   const masks = new Uint8Array(N * MASK_DIM)',
  '112 |   const conditions = new Uint8Array(N)',
  '113 |   const returns = new Float32Array(N)',
  '114 |   for (let i = 0; i < N; i++) {',
  '                  ^',
  "TypeError: undefined is not an object (evaluating 's.obs')",
  '      at capture (D:\\github\\battle2\\tools\\sim\\export-godai-bc.ts:121:20)',
  '      at main (D:\\github\\battle2\\tools\\sim\\export-godai-bc.ts:69:15)',
  '',
  'Bun v1.4.2 (Windows x64)',
].join('\n')

describe('summarizeChildFailure', () => {
  it('错误类型行排在最前（300 字符截断下也看得见根因）', () => {
    const s = summarizeChildFailure(BUN_CRASH)
    expect(s.startsWith('TypeError: undefined is not an object')).toBe(true)
    expect(s.slice(0, 300)).toContain('TypeError')
  })

  it('带上栈顶帧（可定位到源码位置）', () => {
    const s = summarizeChildFailure(BUN_CRASH)
    expect(s).toContain('export-godai-bc.ts:121:20')
  })

  it('带上错误行上方的源码帧（行号 → 语句）', () => {
    const s = summarizeChildFailure(BUN_CRASH)
    expect(s).toContain('114 |   for (let i = 0; i < N; i++) {')
  })

  it('多行 Errors 段：优先第一个 Error 行，不越过它挑栈帧', () => {
    const s = summarizeChildFailure(
      ['RangeError: Invalid typed array length: -1', '    at f (a.ts:1:1)'].join('\n'),
    )
    expect(s).toContain('RangeError: Invalid typed array length: -1')
    expect(s).toContain('at f (a.ts:1:1)')
  })

  it('无错误行 → 退回输出尾部（保留最有信息的近因）', () => {
    const out = 'noise\n'.repeat(200) + 'tail-marker'
    const s = summarizeChildFailure(out)
    expect(s).toContain('tail-marker')
    expect(s.length).toBeLessThanOrEqual(500)
  })

  it('空输出不抛（节点异常退出无 stderr 的兜底）', () => {
    expect(summarizeChildFailure('')).toBe('')
  })
})
