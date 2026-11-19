/** tunnel-reuse-origin.test.ts — 「复用的前提是它拨的是我们要的地址」（2026-11-19 事故回归）。

 *  现场：cloudflared 的 `--url` 曾写 `http://localhost:8787`。Go 把 `localhost` 解析成 `::1`，
 *  而 hub 只绑 `127.0.0.1` ⇒ 每条经隧道的请求吃 `dial tcp [::1]:8787: refused` ⇒ CF 回 502。
 *  把 spawn 参数改成 `127.0.0.1` 之后**故障依旧**——因为控制台的复用判据是
 *  `edgeReady || 穿隧道 ping`：edge 在线就原样复用了那条回源已坏的旧隧道。代码修好、进程没换，
 *  现场表现为「改了代码却没生效」（DSW 节点 502/530 风暴的真正来源）。
 *
 *  这里只钉**判据表**（`tunnelRestartReason`）——不 spawn cloudflared、不联网。
 *  真进程行为在 single-hub-tunnel.test.ts / views-cloudflared-health.test.ts 那一侧。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { sharedHubPort, sharedTunnelMetricsPort } from '../src/core/slots'
import { tunnelRestartReason } from '../src/stack/hub'
import { cloudflaredSpec } from '../src/stack/specs'
import type { RlConfig } from '../src/core/types'

/** 当前期望：hub 在 8787，http2 + 边缘 IPv4。 */
const WANT = { origin: 'http://127.0.0.1:8787', protocol: 'http2', edgeIp: '4' }

describe('隧道复用判据：origin 优先于 edge/选项', () => {
  it('回源地址一致 + 选项一致 ⇒ 可复用（null）', () => {
    expect(
      tunnelRestartReason(
        {
          originUrl: 'http://127.0.0.1:8787',
          cfProtocol: 'http2',
          cfEdgeIp: '4',
        },
        WANT,
      ),
    ).toBeNull()
  })

  it('回源仍是 localhost ⇒ 必须重启（正是 2026-11-19 那条旧隧道）', () => {
    // 注意：`localhost` 与 `127.0.0.1` 在字符串层面就不等——判据不试图「等价对待」它们，
    // 因为二者的**实际行为**不同（Go 解析出 `::1`），等价的假设本身就是那次事故。
    expect(
      tunnelRestartReason(
        { originUrl: 'http://localhost:8787', cfProtocol: 'http2', cfEdgeIp: '4' },
        WANT,
      ),
    ).toBe('origin')
  })

  it('旧条目没有 originUrl ⇒ 重启一次把地址钉进账本（缺席 = 未知，不猜）', () => {
    expect(tunnelRestartReason({ cfProtocol: 'http2', cfEdgeIp: '4' }, WANT)).toBe('origin')
  })

  it('hub 端口改了（回源地址随之改）⇒ 重启，而不是沿用旧端口', () => {
    expect(
      tunnelRestartReason(
        { originUrl: 'http://127.0.0.1:8787', cfProtocol: 'http2', cfEdgeIp: '4' },
        { ...WANT, origin: 'http://127.0.0.1:9787' },
      ),
    ).toBe('origin')
  })

  it('origin 正确但隧道选项变了 ⇒ 返回 options（M1 行为不回归）', () => {
    // 显式标注类型：`cfProtocol` 在账本里是 `CfProtocol` 联合，裸字面量会退化成 `string`
    const ok: Parameters<typeof tunnelRestartReason>[0] = {
      originUrl: 'http://127.0.0.1:8787',
      cfProtocol: 'http2',
      cfEdgeIp: '4',
    }
    expect(tunnelRestartReason(ok, { ...WANT, protocol: 'quic' })).toBe('options')
    expect(tunnelRestartReason(ok, { ...WANT, edgeIp: '6' })).toBe('options')
    // 旧条目不带选项字段 ⇒ 按 'auto' 比对，缺省 http2/4 触发一次升级重启
    expect(tunnelRestartReason({ originUrl: 'http://127.0.0.1:8787' }, WANT)).toBe('options')
  })
})

describe('回源字面量：两条 spawn 路径都必须是 127.0.0.1', () => {
  it('spec 的 `--url` 是 127.0.0.1:<sharedHubPort>（监督器重建走这条）', () => {
    const cfg = {
      version: 1,
      nodes: [],
      rl: { hub_port: 8787, agent_port: 1, remote_token: 't', local_slots: 0, workers: 1 },
      courses: {},
    } as unknown as RlConfig
    const argv = cloudflaredSpec(cfg).cmd.map(String)
    const i = argv.indexOf('--url')
    expect(i).toBeGreaterThan(-1)
    expect(argv[i + 1]).toBe(`http://127.0.0.1:${sharedHubPort(cfg)}`)
    // 顺带钉死 metrics 也是 v4 字面量（`resolveCfTunnel` 之外的第二个回环地址）
    const m = argv.indexOf('--metrics')
    expect(argv[m + 1]).toBe(`127.0.0.1:${sharedTunnelMetricsPort(cfg)}`)
  })

  it('stepCloudflared 的内联 spawn 用同一个字面量，且不再有 localhost 回源', () => {
    // 这是「两半同步」的人工约定（spec 与 stepCloudflared 各写一份 argv）——用源码断言兜住，
    // 免得下次只改了一处。`localhost` 一旦回来就是 502，没什么可商量的余地。
    const src = readFileSync(path.join(DASHBOARD_ROOT, 'src/stack/hub.ts'), 'utf-8')
    expect(src.includes('`http://127.0.0.1:${hubPort}`')).toBe(true)
    expect(src.includes('http://localhost')).toBe(false)
  })
})
