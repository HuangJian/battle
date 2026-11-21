/**
 * node-upgrade-async.test.ts — 服务端专用的异步下发版：必须不堵事件循环。
 *
 * 2026-11-21 事故：dashboard「全部升级」误用同步版，CLI 逐节点发请求（每节点最多 20s）
 * 把整个后端事件循环卡住几分钟。这里钉住「spawn 未结束 ⇒ Promise 仍 pending」。
 */
import { describe, expect, it } from 'bun:test'
import { requestNodeUpgradesAsync, type AsyncSpawn } from '../tools/lib/node-upgrade'

const opts = {
  repoRoot: process.cwd(),
  branch: 'goal-nn',
  expectedHash: 'a'.repeat(64),
  dirty: [] as string[],
  nodes: [{ id: 'a1', url: 'http://x', authKey: 'k', pingHash: '' }],
}

describe('requestNodeUpgradesAsync', () => {
  it('spawn 未 resolve 前保持 pending —— 不阻塞事件循环', async () => {
    let release!: (v: { exitCode: number | null; stdout: string; stderr: string }) => void
    const fake: AsyncSpawn = () =>
      new Promise((res) => {
        release = res
      })
    let settled = false
    const p = requestNodeUpgradesAsync(opts, fake).then((r) => {
      settled = true
      return r
    })
    await Promise.resolve()
    await Promise.resolve()
    expect(settled).toBe(false)

    release({ exitCode: 0, stdout: JSON.stringify({ dirty: [], results: [] }), stderr: '' })
    expect((await p).ok).toBe(true)
    expect(settled).toBe(true)
  })

  it('spawn 抛错 → ok:false + error，不把异常甩给调用方', async () => {
    const fake: AsyncSpawn = async () => {
      throw new Error('boom')
    }
    const out = await requestNodeUpgradesAsync(opts, fake)
    expect(out.ok).toBe(false)
    expect(out.error).toContain('boom')
  })

  it('显式节点模式不写 cfg_path（写了 CLI 就扫全量并忽略 nodes）', async () => {
    let specText = ''
    const fake: AsyncSpawn = async (_argv, input) => {
      specText = input
      return { exitCode: 0, stdout: JSON.stringify({ dirty: [], results: [] }), stderr: '' }
    }
    await requestNodeUpgradesAsync(opts, fake)
    const spec = JSON.parse(specText)
    expect(spec.cfg_path).toBeUndefined()
    expect(spec.nodes).toHaveLength(1)
    expect(spec.nodes[0].id).toBe('a1')
  })
})
