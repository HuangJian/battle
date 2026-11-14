import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { serveResult, stampServiceSec } from '../../tools/agent/sampler-agent'
import { buildPack, serviceElapsedSec, unpackContainer } from '../../tools/sim/pack-container'

/**
 * 服务耗时戳（elapsedSec）的两个不变量。
 *
 * 背景（2026-09-21 真机归因，docs/nn/runtime-opt.md §5）：a95 上同一局「客户端看到 3.4s、
 * manifest 只记 0.7s」，第二次请求（命中缓存）却记 3.2s —— 根因是同步路径把 `stampServiceSec`
 * 的**盖章副本**放进缓存、把**原 buf**发给客户端（缓存里那份才带真值）。
 *
 * 这里钉两件事：
 *   ① 盖章函数本身：改写 elapsedSec、其余字段与 entries 逐字节不动、没有接单时刻时原样透传；
 *   ② 同步出口只发盖章值（结构钉子）——真 agent over-HTTP 的判例在 `bun test` 里起不来/极慢
 *      （实测 30s 超时），而这条不变量的唯一另一种破坏形式就是「谁又把 buf 发出去」，
 *      源码级断言能以 ~0 成本挡住这次事故的复发。
 */
const REPO_ROOT = join(import.meta.dir, '..', '..')

const MANIFEST = { stage: 0, seed: 7, outcome: 'gameover', ticks: 120, mode: 'eval', wver: 'x' }

function pack(): Buffer {
  return buildPack(MANIFEST as unknown as Record<string, unknown>, [
    { name: 'obs.npy', data: Buffer.from([1, 2, 3, 4]) },
  ])
}

describe('stampServiceSec（服务耗时戳）', () => {
  it('有接单时刻：只改 elapsedSec，其余字段与 entries 原样', () => {
    const before = pack()
    const after = stampServiceSec('k1', before, 1000, 4200)

    const a = unpackContainer(before)
    const b = unpackContainer(after)
    expect(b.manifest.elapsedSec).toBe(3.2)
    expect({ ...b.manifest, elapsedSec: undefined }).toEqual({
      ...a.manifest,
      elapsedSec: undefined,
    })
    expect([...b.entries.keys()]).toEqual([...a.entries.keys()])
    for (const [name, data] of a.entries) {
      expect(b.entries.get(name)!.equals(data)).toBe(true)
    }
  })

  it('没有接单时刻（未知 key / 解包失败）：原样透传同一个 buffer', () => {
    const before = pack()
    expect(stampServiceSec('no-such-key', before, undefined)).toBe(before)
    const junk = Buffer.from('not a pack')
    expect(stampServiceSec('no-such-key', junk, 1000, 9000)).toBe(junk)
  })

  it('同步出口只发 serveResult 的返回值（结构钉子：缓存 == 对外 + 零拷贝）', () => {
    const src = readFileSync(join(REPO_ROOT, 'tools', 'agent', 'sampler-agent.ts'), 'utf8')
    // 事故形态：缓存盖章、对外发原件
    expect(src.includes('controller.enqueue(new Uint8Array(buf))')).toBe(false)
    expect(src.includes('const served = serveResult(key, buf)')).toBe(true)
    expect(src.includes('controller.enqueue(served)')).toBe(true)
    // 容器已在 resultCache 里，出口与轮询命中都不该再整体复制一份
    expect(src.includes('new Uint8Array(cached.buf)')).toBe(false)
    expect(src.includes('new Uint8Array(served)')).toBe(false)
  })
})

describe('服务计时前移（2026-11-14，真机容器实测）', () => {
  it('serviceElapsedSec：有服务起点用起点，缺席/非法值回退导出器 t0', () => {
    const t0 = 1_700_000_000_000
    const now = t0 + 9_400
    expect(serviceElapsedSec(t0, [], now)).toBe(9.4)
    expect(serviceElapsedSec(t0, ['--service-started-at-ms', String(t0 + 5_100)], now)).toBe(4.3)
    expect(serviceElapsedSec(t0, ['--service-started-at-ms', 'garbage'], now)).toBe(9.4)
    expect(serviceElapsedSec(t0, ['--service-started-at-ms'], now)).toBe(9.4)
  })

  it('serveResult 原样返回同一个 buffer（不再解包重压）', () => {
    const before = pack()
    expect(serveResult('service-clock-probe', before)).toBe(before)
  })

  it('结果出口不得再调 stampServiceSec（13–44ms/局同步阻塞的回归钉子）', () => {
    const src = readFileSync(join(REPO_ROOT, 'tools', 'agent', 'sampler-agent.ts'), 'utf8')
    expect(src.includes('const served = stampServiceSec(key, buf)')).toBe(false)
    expect(src).toContain('--service-started-at-ms')
  })
})
