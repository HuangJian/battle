/** dist-node-gate.test.ts ↔ tools/lib/dist-node-gate.ts（节点门 + 响亮告警的共享实现）。 */
import { describe, expect, it } from 'bun:test'
import { claimNote } from '../tools/lib/dist-node-gate'
import {
  bunMajorMinor,
  classifyGate,
  gateWarning,
  nodeGateReason,
  provenanceNote,
  type NodeGateEntry,
} from '../tools/lib/dist-node-gate'

const LOCAL = 'ba6f7b4eda13348ad5a561c9db2754c97d9f6351800764dcc687b68d5176bc29'
const entry = (over: Partial<NodeGateEntry> = {}): NodeGateEntry => ({
  id: 'n1',
  ok: true,
  reason: null,
  pingHash: LOCAL,
  ...over,
})

describe('nodeGateReason（与 dist_common.check_code_hash 同源口径）', () => {
  it('不可达 / 缺能力位 / bun 不匹配 / codeHash 不符 逐条拒绝', () => {
    expect(nodeGateReason(null, '1.4', LOCAL)).toBe('ping failed')
    expect(nodeGateReason({ evalSupport: false }, '1.4', LOCAL)).toBe('lacks evalSupport')
    expect(nodeGateReason({ evalSupport: true, stageJsonSupport: false }, '1.4', LOCAL)).toBe(
      'lacks stageJsonSupport',
    )
    expect(
      nodeGateReason(
        { evalSupport: true, stageJsonSupport: true, bunVersion: '1.3.9' },
        '1.4',
        LOCAL,
      ),
    ).toBe('bun version mismatch')
    const why = nodeGateReason(
      {
        evalSupport: true,
        stageJsonSupport: true,
        bunVersion: '1.4.2',
        codeHash: 'aeccc9838dd2' + '0'.repeat(52),
      },
      bunMajorMinor('1.4.2'),
      LOCAL,
    )
    // 拒绝原因必须带**两侧**前缀（F2 口径）：运维一眼看出差异在哪一侧。
    expect(why).toContain('aeccc9838dd2')
    expect(why).toContain('ba6f7b4eda13')
    expect(why?.startsWith('codeHash mismatch')).toBe(true)
  })

  it('通过时返回 null（bun 只比 major.minor）', () => {
    expect(
      nodeGateReason(
        { evalSupport: true, stageJsonSupport: true, bunVersion: '1.4.0', codeHash: LOCAL },
        '1.4',
        LOCAL,
      ),
    ).toBeNull()
  })
})

describe('classifyGate', () => {
  it('按原因分桶：usable / stale / unreachable / other', () => {
    const s = classifyGate([
      entry({ id: 'self' }),
      entry({ id: 'mac', ok: false, reason: 'codeHash mismatch (node a… local b…)' }),
      entry({ id: 'gcs', ok: false, reason: 'ping failed', pingHash: '' }),
      entry({ id: 'a96', ok: false, reason: 'lacks evalSupport' }),
    ])
    expect(s.total).toBe(4)
    expect(s.usable).toEqual(['self'])
    expect(s.stale).toEqual(['mac'])
    expect(s.unreachable).toEqual(['gcs'])
    expect(s.other).toEqual([['a96', 'lacks evalSupport']])
  })
})

describe('gateWarning（响亮告警）', () => {
  const stale = [
    entry({ id: 'self' }),
    entry({
      id: 'mac',
      ok: false,
      reason: 'codeHash mismatch (node aeccc9838dd2… local ba6f7b4eda13…)',
      pingHash: 'aeccc9838dd2e38cac39dd778f7aa7bcb8ac6955beb7dc9faffc2ba9570a4e84',
    }),
  ]

  it('全过 ⇒ 不产生噪声', () => {
    expect(gateWarning('[t]', [entry()], LOCAL, { localCap: 4 })).toEqual([])
    expect(gateWarning('[t]', [], LOCAL, { localCap: 4 })).toEqual([])
  })

  it('有排除项 ⇒ 计数 + 双方 hash（self 仍可用时不谈本地回落）', () => {
    const text = gateWarning('[t]', stale, LOCAL, { localCap: 15 }).join('\n')
    expect(text).toContain('1/2 usable (self)')
    expect(text).toContain('1 stale (mac)')
    expect(text).toContain('codeHash node aeccc9838dd2… ≠ local ba6f7b4eda13…')
    expect(text).toContain('codehash-report')
    // self 节点可用 ⇒ 不是「远端全灭」，不该出现回落真相文案。
    expect(text).not.toContain('本地 worker')
  })

  it('远端全灭 + 有本地槽 ⇒ 明说全部本地 + 处置建议；无本地槽 ⇒ 明说无算力', () => {
    const allStale = [
      entry({ id: 'mac', ok: false, reason: 'codeHash mismatch (node a… local b…)' }),
    ]
    const withSlots = gateWarning('[t]', allStale, LOCAL, { localCap: 4 }).join('\n')
    expect(withSlots).toContain('远端可用算力 0')
    expect(withSlots).toContain('本地 worker')
    expect(withSlots).toContain('--upgrade-nodes')
    expect(gateWarning('[t]', allStale, LOCAL, { localCap: 0 }).join('\n')).toContain('无算力可用')
  })

  it('已下发升级 ⇒ 文案改为「见逐节点结果」，不再劝加 flag', () => {
    const allStale = [
      entry({ id: 'mac', ok: false, reason: 'codeHash mismatch (node a… local b…)' }),
    ]
    const lines = gateWarning('[t]', allStale, LOCAL, { localCap: 15, upgradeRequested: true })
    expect(lines.join('\n')).toContain('已按训练循环同规守卫下发升级')
    expect(lines.join('\n')).not.toContain('--upgrade-nodes')
  })
})

describe('provenanceNote（谁跑的必须说出来）', () => {
  it('纯节点 ⇒ 无 WARN；混跑/全本地 ⇒ WARN 且按来源拆开', () => {
    expect(provenanceNote('[t]', { 'node:self': 8 })).toEqual([
      '[t] provenance: node:self=8（共 8 局）',
    ])
    const mixed = provenanceNote('[t]', { 'node:self': 6, local: 2 })
    expect(mixed[0]).toContain('node:self=6')
    expect(mixed[0]).toContain('local=2')
    expect(mixed[1]).toContain('2/8 局由本地 worker 跑')
    const allLocal = provenanceNote('[t]', { local: 5 })
    expect(allLocal[1]).toContain('5/5 局由**本地 worker** 跑')
    expect(allLocal[1]).toContain('不要当作分布式读数')
  })

  it('空 ⇒ 空', () => {
    expect(provenanceNote('[t]', {})).toEqual([])
  })
})

describe('claimNote — 分派口径（与 provenance 配对读）', () => {
  it('列出逐消费者分派数 + 合计', () => {
    const lines = claimNote('[t]', { 'node:self': 4, 'node:mac': 2, 'node:a95': 1, local: 1 })
    expect(lines).toHaveLength(1)
    expect(lines[0]).toContain('node:a95=1')
    expect(lines[0]).toContain('local=1')
    expect(lines[0]).toContain('共 8 次分派')
  })

  it('空 ⇒ 空（不打噪声）', () => {
    expect(claimNote('[t]', {})).toEqual([])
  })
})
