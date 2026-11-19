/** tunnel-reuse-origin.test.ts — 「edge 在线的隧道绝不重建」（2026-11-19 用户强烈指令后的硬不变式）。

 *  两次走偏都在这同一张判据表上：
 *  ① 旧码 `edgeReady || 穿隧道 ping` 短路或 ⇒ 从不看 origin，回源写成 `localhost`（Go 解析成
 *     `::1`）的旧隧道在代码修好后仍被复用，节点继续吃 502，现场像「改了什么都没用」；
 *  ② 于是改成「origin/选项不一致就杀旧起新」——**更糟**：quick tunnel 的域名是一次性凭据，
 *     杀进程重新申请就换名，所有把 hub url 写死在 notebook cell / 配置 / 节点里的服务同时失联，
 *     且 CF 不允许重新领取同一 hostname ⇒ 不可逆。
 *
 *  所以本文件钉的是**安全那侧**：`edgeReady` 为真时一律 `reuse`，漂移只作为 `drift` 报警；
 *  只有 edge 掉线（该隧道已经没路可走，没有可保的域名）才 `respawn`。
 *  纯函数 —— 不 spawn cloudflared、不联网；真进程行为在 single-hub-tunnel 那一侧。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { sharedHubPort, sharedTunnelMetricsPort } from '../src/core/slots'
import { tunnelReuse } from '../src/stack/hub'
import { cloudflaredSpec } from '../src/stack/specs'
import type { RlConfig } from '../src/core/types'

/** 当前期望：hub 在 8787，http2 + 边缘 IPv4。 */
const WANT = { origin: 'http://127.0.0.1:8787', protocol: 'http2', edgeIp: '4' }

/** 账本里「已知且与期望一致」的一条登记。 */
const SAME: Parameters<typeof tunnelReuse>[0] = {
  originUrl: 'http://127.0.0.1:8787',
  cfProtocol: 'http2',
  cfEdgeIp: '4',
}

describe('★★ edge 在线 ⇒ 永远复用（重启 = 换域名 = 全网断连）', () => {
  it('一切一致：复用、无漂移', () => {
    expect(tunnelReuse(SAME, WANT, true)).toEqual({ act: 'reuse', drift: null })
  })

  it('回源仍是 localhost：只报 drift=origin，**仍然复用**（不许杀）', () => {
    // 这就是上一刀的教训：它确实该报警，但「该报警」不等于「该杀」——杀了域名就没了。
    expect(tunnelReuse({ ...SAME, originUrl: 'http://localhost:8787' }, WANT, true)).toEqual({
      act: 'reuse',
      drift: 'origin',
    })
  })

  it('旧条目没有 originUrl：drift=origin（未知不猜），但仍复用', () => {
    expect(tunnelReuse({ cfProtocol: 'http2', cfEdgeIp: '4' }, WANT, true)).toEqual({
      act: 'reuse',
      drift: 'origin',
    })
  })

  it('hub 端口改了：drift=origin，仍复用（换端口要人显式停→启）', () => {
    expect(tunnelReuse(SAME, { ...WANT, origin: 'http://127.0.0.1:9787' }, true)).toEqual({
      act: 'reuse',
      drift: 'origin',
    })
  })

  it('隧道选项变了：drift=options，仍复用（新选项下次自然启动才生效）', () => {
    expect(tunnelReuse(SAME, { ...WANT, protocol: 'quic' }, true)).toEqual({
      act: 'reuse',
      drift: 'options',
    })
    expect(tunnelReuse(SAME, { ...WANT, edgeIp: '6' }, true)).toEqual({
      act: 'reuse',
      drift: 'options',
    })
    // 旧条目不带选项字段 ⇒ 按 'auto' 比对（报警即可，别拿它当重启理由）
    expect(
      tunnelReuse({ ...SAME, cfProtocol: undefined, cfEdgeIp: undefined }, WANT, true),
    ).toEqual({ act: 'reuse', drift: 'options' })
  })
})

describe('edge 掉线才重建（此时没有可保的域名）', () => {
  it('edge 未连接 ⇒ respawn；即便还有漂移也一并带出（给日志用）', () => {
    expect(tunnelReuse(SAME, WANT, false)).toEqual({ act: 'respawn', drift: null })
    expect(tunnelReuse({ ...SAME, originUrl: 'http://localhost:8787' }, WANT, false)).toEqual({
      act: 'respawn',
      drift: 'origin',
    })
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
    // 「两半同步」的人工约定（spec 与 stepCloudflared 各写一份 argv）——用源码断言兜住，
    // 免得下次只改了一处。`localhost` 一旦回来就是 502，没什么可商量的余地。
    const src = readFileSync(path.join(DASHBOARD_ROOT, 'src/stack/hub.ts'), 'utf-8')
    expect(src.includes('`http://127.0.0.1:${hubPort}`')).toBe(true)
    expect(src.includes('http://localhost')).toBe(false)
  })

  it('复用分支里 killPid 只出现一次（= edge 掉线那条），漂移绝不动手', () => {
    // 结构性护栏：若有人再把「漂移就重启」加回来，这条会先红 —— 那正是 2026-11-19 全网断连的形状。
    const src = readFileSync(path.join(DASHBOARD_ROOT, 'src/stack/hub.ts'), 'utf-8')
    const head = src.slice(src.indexOf('if (prev && pidAlive(prev.pid)'), src.indexOf('let url'))
    expect(head.split('killPid').length - 1).toBe(1)
  })
})
