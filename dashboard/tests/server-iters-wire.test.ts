/**
 * server-iters-wire.test.ts — M0 传输账入表：iteration 事件的 `wire` 子字典 → IterRow.wire
 *
 * 分层：src/server/iters.ts（`parseIterWire` 纯函数 + 读账本接线）
 *
 * 守什么：① additive —— 旧账本（无 `wire` 键）必须照旧读出、wire 为 null（不是 0，
 * 也不是整行被丢）；② 对账口径严格 —— 字符串数字不收（掩盖上游写端 bug）；
 * ③ worker 子字典键集不固定（M0/M2 先后加过键）→ 逐项透传，不白名单硬编码。
 */

import os from 'os'
import path from 'path'
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import { parseIterWire, readIterMetrics } from '../src/server/iters'

describe('console/iters 传输账 wire 解析', () => {
  it('完整 wire（含 worker 子字典）逐项入表，键名转驼峰', () => {
    const w = parseIterWire({
      up_bytes: 1200000,
      up_sec: 4.8,
      pack_sec: 3.2,
      down_bytes: null,
      down_sec: null,
      blobs_miss: 0,
      protocol: 'http2',
      edge_ip: '4',
      slim: true,
      rollout_src: 'local',
      worker: {
        payload_bytes: 1403724,
        result_bytes: 1041907,
        blob_hits: 1,
        opt_restore_sec: 1.2,
      },
    })
    expect(w).not.toBeNull()
    expect(w!.upBytes).toBe(1200000)
    expect(w!.upSec).toBe(4.8)
    expect(w!.packSec).toBe(3.2)
    // push 模式的 down_bytes 是 null（pull 才有 hub 实测口径）——必须原样保留 null，
    // 不能兜成 0（0 在趋势里是「真的没传字节」）。
    expect(w!.downBytes).toBeNull()
    expect(w!.protocol).toBe('http2')
    expect(w!.edgeIp).toBe('4')
    expect(w!.slim).toBe(true)
    expect(w!.rolloutSrc).toBe('local')
    expect(w!.worker).toEqual({
      payload_bytes: 1403724,
      result_bytes: 1041907,
      blob_hits: 1,
      opt_restore_sec: 1.2,
    })
  })

  it('缺键一律 null（不是 0）；worker 里的非数字项被丢弃', () => {
    const w = parseIterWire({ up_bytes: 5, worker: { a: 1, b: '12', c: null, d: true } })
    expect(w!.upBytes).toBe(5)
    expect(w!.upSec).toBeNull()
    expect(w!.packSec).toBeNull()
    expect(w!.blobsMiss).toBeNull()
    expect(w!.protocol).toBeNull()
    expect(w!.slim).toBeNull()
    // 严格口径：字符串数字 / 布尔 / null 都不收（收 '12' 会掩盖写端 bug）
    expect(w!.worker).toEqual({ a: 1 })
  })

  it('无 wire / 形状非法 → null（additive：旧账本不因缺键改变行为）', () => {
    for (const bad of [undefined, null, 'x', 3, []]) {
      expect(parseIterWire(bad)).toBeNull()
    }
    // 空对象 = 有键但没有任何可读数字 → 仍是对象视图（不是 null）；此处只要求不抛
    expect(parseIterWire({})).not.toBeNull()
  })
})

describe('console/iters 读账本接线', () => {
  it('新行带 wire、旧行 wire 为 null，两行都进表', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-wire-'))
    try {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        [
          JSON.stringify({ event: 'iteration', iter: 1, time: '', winRate: 0.1, score_mean: 0 }),
          JSON.stringify({
            event: 'iteration',
            iter: 2,
            time: '',
            winRate: 0.2,
            score_mean: 1,
            wire: { up_bytes: 999, protocol: 'quic', slim: false },
          }),
        ].join('\n'),
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows.length).toBe(2)
      expect(rows[0]!.iter).toBe(2)
      expect(rows[0]!.wire!.upBytes).toBe(999)
      expect(rows[0]!.wire!.protocol).toBe('quic')
      expect(rows[0]!.wire!.slim).toBe(false)
      // 旧行：字段存在但为 null（UI 显空态），行本身照旧可读
      expect(rows[1]!.iter).toBe(1)
      expect(rows[1]!.wire).toBeNull()
      expect(rows[1]!.winRate).toBeCloseTo(0.1)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
