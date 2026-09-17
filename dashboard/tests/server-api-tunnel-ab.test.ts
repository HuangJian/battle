/**
 * server-api-tunnel-ab.test.ts — M1 隧道 A/B 探针结果的只读派生
 *
 * 分层：src/server/api/tunnel-ab.ts
 *
 * 守什么：① 腿序稳定（基线 loopback → http2 → quic），方向 up 在 down 前
 * —— 表里顺序跳来跳去比数字错更误事；② 倍率 = quic ÷ http2（>1 = http2 更快，
 * 就是计划 §3.4 的判据本身）；③ 坏文件/半截 JSON 只跳过自己，不拖垮整屏；
 * ④ 新→旧排序（读「连跑是否退化」靠这个）。
 */

import os from 'os'
import path from 'path'
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, utimesSync, writeFileSync } from 'fs'
import { parseTunnelAb, readTunnelAbRuns } from '../src/server/api/tunnel-ab'

const DOC = JSON.stringify({
  bytes: 2097152,
  rounds: 5,
  legs: {
    quic: {
      up: { n: 5, p50_sec: 23.42, p90_sec: 25.4, max_sec: 43.54, p50_mbps: 0.7 },
      down: { n: 5, p50_sec: 8.5, p90_sec: 8.9, max_sec: 9.77, p50_mbps: 2.0 },
      url: 'https://x.trycloudflare.com',
    },
    http2: {
      up: { n: 5, p50_sec: 4.66, p90_sec: 5.9, max_sec: 6.49, p50_mbps: 3.6 },
      down: { n: 5, p50_sec: 5.09, p90_sec: 6.61, max_sec: 8.42, p50_mbps: 3.3 },
    },
    loopback: {
      up: { n: 5, p50_sec: 0.02, p90_sec: 0.02, max_sec: 0.03, p50_mbps: 890 },
    },
  },
})

describe('console/tunnel-ab 解析', () => {
  it('腿序固定（loopback→http2→quic），方向 up 在前；缺失的方向不占行', () => {
    const run = parseTunnelAb(DOC, 'tunnel-ab-2.json', 1000)
    expect(run).not.toBeNull()
    expect(run!.rows.map((r) => `${r.leg}.${r.dir}`)).toEqual([
      'loopback.up',
      'http2.up',
      'http2.down',
      'quic.up',
      'quic.down',
    ])
    expect(run!.bytes).toBe(2097152)
    expect(run!.rounds).toBe(5)
    expect(run!.rows[1]!.stat.p50Sec).toBe(4.66)
  })

  it('倍率 = quic ÷ http2（>1 = http2 更快），缺任一腿 → null', () => {
    const run = parseTunnelAb(DOC, 'f.json', 0)!
    // 23.42 / 4.66 ≈ 5.03（实测那次的「上行快 5 倍」就是这条）
    expect(run.speedup.up).toBeCloseTo(5.03, 1)
    expect(run.speedup.down).toBeCloseTo(1.67, 1)
    // 只有一条协议腿时不许编造倍率
    const onlyHttp2 = JSON.stringify({
      bytes: 1,
      rounds: 1,
      legs: { http2: { up: { n: 1, p50_sec: 1, p90_sec: 1, max_sec: 1, p50_mbps: 1 } } },
    })
    const r2 = parseTunnelAb(onlyHttp2, 'f.json', 0)!
    expect(r2.speedup.up).toBeNull()
    expect(r2.speedup.down).toBeNull()
  })

  it('坏 JSON / 形状不符 / 全空腿 → null（跳过自己，不拖垮整屏）', () => {
    for (const bad of ['{', 'null', '[]', '{"bytes":1}', '{"legs":{}}', '{"legs":{"http2":{}}}']) {
      expect(parseTunnelAb(bad, 'f.json', 0)).toBeNull()
    }
  })

  it('统计缺 p50/p90/max/n 任一 → 该行不出现（半截数据不许画进表）', () => {
    const partial = JSON.stringify({
      bytes: 1,
      rounds: 1,
      legs: {
        http2: { up: { n: 1, p50_sec: 1 }, down: { n: 1, p50_sec: 1, p90_sec: 2, max_sec: 3 } },
      },
    })
    const run = parseTunnelAb(partial, 'f.json', 0)!
    expect(run.rows.map((r) => r.dir)).toEqual(['down'])
  })
})

describe('console/tunnel-ab 磁盘读（tmp/）', () => {
  it('新→旧排序、非探针文件忽略、limit 生效；空目录 = available:false 空态', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-ab-'))
    try {
      writeFileSync(path.join(dir, 'tunnel-ab-1.json'), DOC, 'utf-8')
      writeFileSync(path.join(dir, 'tunnel-ab-2.json'), DOC, 'utf-8')
      writeFileSync(path.join(dir, 'tunnel-ab-broken.json'), '{', 'utf-8')
      writeFileSync(path.join(dir, 'other.json'), DOC, 'utf-8')
      // mtime：让 1 比 2 新（排序按 mtime，不靠文件名）
      const old = new Date(Date.now() - 60_000)
      utimesSync(path.join(dir, 'tunnel-ab-2.json'), old, old)

      const v = readTunnelAbRuns(5, dir)
      expect(v.available).toBe(true)
      // 坏文件被跳过，other.json 不匹配前缀 —— 只剩两条，1 在前（更新）
      expect(v.runs.map((r) => r.file)).toEqual(['tunnel-ab-1.json', 'tunnel-ab-2.json'])
      // limit 生效，且**先解析再截断**：坏文件是 mtime 最新的那个，limit=1 仍必须
      // 给出最新的**有效**运行（先 slice 会让它把好运行一起挡掉 → 假空白）。
      expect(readTunnelAbRuns(1, dir).runs.map((r) => r.file)).toEqual(['tunnel-ab-1.json'])
      // 目录不存在 → 空态 + error 说明，不抛
      const missing = readTunnelAbRuns(5, path.join(dir, 'nope'))
      expect(missing.available).toBe(false)
      expect(missing.error).toBeTruthy()
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
