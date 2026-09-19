/** dist-node-gate.test.ts ↔ tools/lib/dist-node-gate.ts（节点门 + 响亮告警的共享实现）。 */
import { describe, expect, it } from 'bun:test'
import { rmSync, writeFileSync } from 'node:fs'
import {
  claimNote,
  configLocalSlots,
  pickDistLocal,
  pingNode,
  reprobeDue,
} from '../tools/lib/dist-node-gate'
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
  it('纯节点 ⇒ 说明远端全包，且无 WARN', () => {
    const lines = provenanceNote('[t]', { 'node:self': 8 })
    expect(lines[0]).toContain('node:self=8')
    expect(lines[0]).toContain('远端 8 / 本地 0')
    expect(lines[1]).toContain('全部 8 局由远端节点完成')
    expect(lines.join('\n')).not.toContain('WARN')
  })

  it('混跑 ⇒ 只报份额 + 点明 --dist-local，绝不写成「节点失败/兜底」', () => {
    // 2026-09-19 实测误读源头：1600 局里 728 本地是 --dist-local 15 的**份额**，
    // 旧文案却写成「节点部分失败或本地兜底」，被读成「没分派到集群」。
    const lines = provenanceNote(
      '[t]',
      { 'node:mac': 358, local: 730 },
      {
        localCap: 15,
        usableNodes: 6,
      },
    )
    expect(lines[0]).toContain('共 1088 局：远端 358 / 本地 730')
    expect(lines[1]).toContain('--dist-local 15 的份额')
    expect(lines[1]).toContain('不是失败兜底')
    const all = lines.join('\n')
    expect(all).not.toContain('WARN')
    expect(all).not.toContain('部分失败')
  })

  it('全本地 ⇒ WARN；过门节点数 >0 时给出「分到但没干完」的排查口径', () => {
    const noNodes = provenanceNote('[t]', { local: 5 }, { localCap: 5 })
    expect(noNodes[1]).toContain('5/5 局全部由**本地 worker** 跑')
    expect(noNodes[1]).toContain('无可用节点')
    expect(noNodes[1]).toContain('不要当作分布式读数')
    const withNodes = provenanceNote('[t]', { local: 5 }, { localCap: 5, usableNodes: 6 })
    expect(withNodes[1]).toContain('6 个节点过门')
    expect(withNodes[1]).toContain('claims')
  })

  it('空 ⇒ 空', () => {
    expect(provenanceNote('[t]', {})).toEqual([])
  })
})

describe('pingNode（探测不能是「一次定生死」）', () => {
  const serve = (
    handler: (req: Request, state: { hits: number }) => Response | Promise<Response>,
  ): { server: ReturnType<typeof Bun.serve>; state: { hits: number }; url: string } => {
    const state = { hits: 0 }
    const server = Bun.serve({ port: 0, fetch: (req) => handler(req, state) })
    return { server, state, url: `http://127.0.0.1:${server.port}` }
  }

  it('单次慢响应不判死：第一次超时、第二次成功 ⇒ 仍拿到 ping', async () => {
    const { server, state, url } = serve(async (_req, st) => {
      st.hits++
      if (st.hits === 1) await new Promise((r) => setTimeout(r, 300))
      return Response.json({ ok: true, evalSupport: true, stageJsonSupport: true, codeHash: 'x' })
    })
    const ping = await pingNode(url, '', { timeoutMs: 100, attempts: 2, gapMs: 10 })
    expect(ping?.codeHash).toBe('x')
    expect(state.hits).toBe(2)
    server.stop(true)
  })

  it('每次都慢/非 200 ⇒ null（重试用尽才算不可达）', async () => {
    const slow = serve(async (_req, st) => {
      st.hits++
      await new Promise((r) => setTimeout(r, 300))
      return Response.json({ ok: true })
    })
    expect(await pingNode(slow.url, '', { timeoutMs: 50, attempts: 2, gapMs: 10 })).toBeNull()
    expect(slow.state.hits).toBe(2)
    slow.server.stop(true)
    const busy = serve((_req, st) => {
      st.hits++
      return new Response('busy', { status: 503 })
    })
    expect(await pingNode(busy.url, '', { timeoutMs: 200, attempts: 2, gapMs: 10 })).toBeNull()
    expect(busy.state.hits).toBe(2)
    busy.server.stop(true)
  })

  it('连不上的主机 ⇒ null（不死循环）', async () => {
    expect(await pingNode('http://127.0.0.1:1', '', { timeoutMs: 200, attempts: 2 })).toBeNull()
  })

  it('reprobeDue：冷却窗口内不重复探，窗口外可探；首次可探', () => {
    expect(reprobeDue(undefined, 1_000, 15_000)).toBe(true)
    expect(reprobeDue(1_000, 5_000, 15_000)).toBe(false)
    expect(reprobeDue(1_000, 16_000, 15_000)).toBe(true)
  })
})

describe('configLocalSlots（本机槽位必读配置）', () => {
  const writeCfg = (obj: unknown): string => {
    const p = `tmp/dist-node-gate-cfg-${Math.random().toString(36).slice(2)}.json`
    writeFileSync(p, JSON.stringify(obj))
    return p
  }

  it('rl.local_slots=0（本机不参与）⇒ 取 0，不被「物理核数」覆盖', () => {
    // 2026-09-19 实测：这份配置下 1600 局的批本地跑了 730 局。
    const p = writeCfg({ nodes: [], rl: { local_slots: 0, workers: 8 } })
    expect(configLocalSlots(p)).toEqual({ slots: 0, source: 'rl.local_slots' })
    rmSync(p, { force: true })
  })

  it('policy.evalLocalSlots 优先于 rl.local_slots（与 Python 评测栈同序）', () => {
    const p = writeCfg({ policy: { evalLocalSlots: 4 }, rl: { local_slots: 8 } })
    expect(configLocalSlots(p)).toEqual({ slots: 4, source: 'policy.evalLocalSlots' })
    rmSync(p, { force: true })
  })

  it('两键都没有 / 非数值 / 文件不可读 ⇒ null（调用方用兜底）', () => {
    const none = writeCfg({ nodes: [], rl: { workers: 8 } })
    expect(configLocalSlots(none)).toEqual({ slots: null, source: '' })
    const bad = writeCfg({ policy: { evalLocalSlots: '0' }, rl: { local_slots: -1 } })
    expect(configLocalSlots(bad)).toEqual({ slots: null, source: '' })
    expect(configLocalSlots('tmp/definitely-not-here-42.json')).toEqual({ slots: null, source: '' })
    rmSync(none, { force: true })
    rmSync(bad, { force: true })
  })
})

/**
 * 回归守卫（用户 2026-09-19 报障：`rl.local_slots: 0` 被忽略，评测仍跑本机 4 槽）。
 * 当时两个工具各写一遍求解，`eval-course-ckpt` 还把缺省值留给 Python 侧
 * `policy.evalLocalSlots`（缺省 4）⇒ 整条配置链被静默跳过。现在链只有这一处。
 */
describe('pickDistLocal（本机槽位链：显式 > 配置 > 物理核数）', () => {
  const cfg = (slots: number | null, source: string) => ({ slots, source })

  it('显式 --dist-local 最高优先（含 0 = 本机不参与）', () => {
    expect(pickDistLocal(0, cfg(3, 'policy.evalLocalSlots'), 15)).toEqual({
      slots: 0,
      source: '--dist-local',
    })
    expect(pickDistLocal(7, cfg(0, 'rl.local_slots'), 15)).toEqual({
      slots: 7,
      source: '--dist-local',
    })
  })

  it('未显式给定时取配置值（含 0），并写明来源键', () => {
    expect(pickDistLocal(NaN, cfg(0, 'rl.local_slots'), 15)).toEqual({
      slots: 0,
      source: '配置 rl.local_slots',
    })
    expect(pickDistLocal(NaN, cfg(4, 'policy.evalLocalSlots'), 15)).toEqual({
      slots: 4,
      source: '配置 policy.evalLocalSlots',
    })
  })

  it('配置未约定 ⇒ 物理核数兜底（来源可读）', () => {
    expect(pickDistLocal(NaN, cfg(null, ''), 15)).toEqual({
      slots: 15,
      source: '物理核数（配置未约定）',
    })
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
