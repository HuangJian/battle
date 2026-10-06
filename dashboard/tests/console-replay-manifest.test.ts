/** console-replay-manifest.test.ts — 导出 replay 的产物清单解析
 *  （server/api/eval-games.ts::readReplayManifest）：成功/失败两种 manifest 的透传与降级。
 *
 *  `failReason` 是 2026-10-06 补的字段（任意轮导出：失败必须归因到「哪一轮、为什么」）——
 *  readReplayManifest 是**白名单式重建**（不 spread raw），不显式透传的话视图层永远看不到它；
 *  本文件把「写入方（python）写的失败 manifest 能被读面完整读出来」钉成用例。
 *  python 侧的写入面用例在 nn-training/tests/worker/test_eval_replays_once.py。 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { readReplayManifest } from '../src/server/api/eval-games'

const dir = mkdtempSync(path.join(os.tmpdir(), 'replay-manifest-'))
afterAll(() => rmSync(dir, { recursive: true, force: true }))

function writeManifest(name: string, body: unknown): string {
  const p = path.join(dir, name)
  writeFileSync(p, typeof body === 'string' ? body : JSON.stringify(body))
  return p
}

describe('readReplayManifest', () => {
  it('成功 manifest：files/errors/mismatches 与请求轮透传', () => {
    const p = writeManifest('ok.json', {
      ok: true,
      course: 'c1',
      iter: 12,
      wver: 'a'.repeat(16),
      weightsPath: 'nn-training/weights/c1/c1.it12.x.json',
      difficulty: 'hard',
      maxTicks: 12000,
      generatedAt: '2026-10-06 10:00:00',
      sec: 42.5,
      requested: 2,
      files: [{ stage: 0, seed: 1, file: 'hard-s01-clear-l1-t40-seed1.replay' }],
      errors: [{ stage: 1, seed: 2, error: '未产出 .replay' }],
      mismatches: [{ stage: 0, seed: 1, field: 'ticks', ledger: 100, resim: 101 }],
    })
    const m = readReplayManifest(p)!
    expect(m.ok).toBe(true)
    expect(m.iter).toBe(12)
    expect(m.files).toHaveLength(1)
    expect(m.errors).toHaveLength(1)
    expect(m.mismatches).toHaveLength(1)
    expect(m.failReason).toBeUndefined()
  })

  it('失败 manifest：ok:false + failReason 透传（弹窗据此显示原因，而不是「导出完成」）', () => {
    const p = writeManifest('fail.json', {
      ok: false,
      course: 'c1',
      iter: 7,
      wver: 'b'.repeat(16),
      weightsPath: '',
      difficulty: '',
      maxTicks: 0,
      generatedAt: '2026-10-06 10:01:00',
      sec: 0,
      requested: 3,
      files: [],
      errors: [],
      mismatches: [],
      failReason: '未找到 wver=bbb… 的权重文件（临时快照/活动权重/weights 归档全不匹配）',
    })
    const m = readReplayManifest(p)!
    expect(m.ok).toBe(false)
    expect(m.iter).toBe(7)
    expect(m.requested).toBe(3)
    expect(m.failReason).toContain('权重文件')
  })

  it('failReason 缺失/非字符串/空串 ⇒ undefined，其余字段照常解析（降级「看日志尾」）', () => {
    for (const bad of [undefined, 7, '', {}]) {
      const p = writeManifest(`bad-${String(bad)}.json`, {
        ok: false,
        iter: 1,
        errors: [{ stage: 0, seed: 0, error: 'x' }],
        failReason: bad,
      })
      const m = readReplayManifest(p)!
      expect(m.failReason).toBeUndefined()
      expect(m.iter).toBe(1)
      expect(m.errors).toHaveLength(1)
    }
  })

  it('缺 ok / 坏 JSON / 文件不存在 ⇒ null（诚实显示「无产物」）', () => {
    expect(readReplayManifest(writeManifest('nook.json', { iter: 1 }))).toBeNull()
    expect(readReplayManifest(writeManifest('badjson.json', '{not json'))).toBeNull()
    expect(readReplayManifest(path.join(dir, 'missing.json'))).toBeNull()
  })
})
