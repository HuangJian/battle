/**
 * server-actions-upgrade-online-nodes.test.ts — 「全部升级」的目标筛选判据。
 *
 * 只测「谁能被升级」这条纯函数：真正下发会 spawn 升级 CLI，不在这里触发。
 */
import { describe, expect, it } from 'bun:test'
import { selectUpgradeTargets } from '../src/server/actions/nodes'

const nodes = [
  { id: 'a1', enabled: true },
  { id: 'a2', enabled: false },
  { id: 'g1', enabled: true, gpu_push: { url: 'http://x' } },
  { id: 'o1', enabled: true, offline_push: true },
  { id: 'b2', enabled: true },
]

describe('全部升级：目标只留在线采样节点', () => {
  it('停用的 / push 节点 / 不存在的 id 都不下发', () => {
    expect(
      selectUpgradeTargets(nodes, ['a1', 'a2', 'g1', 'o1', 'b2', 'ghost']).map((n) => n.id),
    ).toEqual(['a1', 'b2'])
  })

  it('重复 id 不产生重复目标；空 ids = 空', () => {
    expect(selectUpgradeTargets(nodes, ['a1', 'a1']).map((n) => n.id)).toEqual(['a1'])
    expect(selectUpgradeTargets(nodes, [])).toEqual([])
  })
})
