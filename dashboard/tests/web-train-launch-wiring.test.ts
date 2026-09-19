/**
 * web-train-launch-wiring —— app.tsx → TrainLaunchModal 的启动选项透传。
 *
 * 历史（2026-09-15 实测 bug，用户报「启动时明明填好正确的 cloud endpoint，却自动开本机节点」）：
 *   ① `TrainLaunchModal.tsx` onLaunch(mode, { endpoint, authKey })  —— 弹窗**有**传凭据
 *   ② `app.tsx` 的接线写成 `(m) => void handleLaunch(m)`            —— **丢掉了第二个参数**
 *   ③ `if (mode === 'push' && push)` 因 push=undefined 为假 ⇒ body 里根本没有 pushEndpoint 键
 *      ⇒ `route.ts bodyStr` → '' ⇒ 预设走健康扫描/回落路径，控制台打印「启动本机伪 GPU 节点」。
 *
 * 现行口径（2026-09-19 用户指令「启动课程训练时，trainloop 不需要指定 pull/push 模式」）：
 *   pull 由远端 worker 自己来领（本机只需 hub 在线 + 可选隧道），push 只看系统里**是否登记了**
 *   push worker 节点（配置入口 = worker 登记面板，数据住 rl-config.json）。⇒ 启动链路里
 *   **不再有 mode / endpoint / authKey**，只剩隧道/瘦身/rollout 三个随启动回写的 rl-config 选项
 *   + **训练模式**（在线/离线，2026-09-19；那是另一个域——`trainMode` 决定本机跑不跑，
 *   与已退役的 pull/push「传输模式」无关）。
 *
 * 本文件守两条：**选项不得在路上丢掉**（③ 那类假成功）与 **模式不得复活**。
 * 为什么 TS/tsc 抓不到丢参：`(opts) => void` 对 `(opts?) => void` 合法，少形参也合法，
 * 所以只能靠源码断言。仓库既有同款手法：`web-ssr-readonly.test.ts` 直接读 app.tsx。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const DASHBOARD_ROOT = join(import.meta.dir, '..')
const APP = join(DASHBOARD_ROOT, 'src', 'web', 'app', 'app.tsx')
const MODAL = join(DASHBOARD_ROOT, 'src', 'web', 'app', 'panels', 'TrainLaunchModal.tsx')

const squash = (p: string): string => readFileSync(p, 'utf8').replace(/\s+/g, ' ')

describe('app.tsx → TrainLaunchModal 启动选项透传', () => {
  it('onLaunch 接线把弹窗给的选项整体转给 handleLaunch（不丢参）', () => {
    const src = squash(APP)
    expect(src).toContain('TrainLaunchModal')
    // 弹窗只给一个 opts 参数；接线必须原样转交
    expect(src).toMatch(/onLaunch=\{\s*\(?\s*opts\s*\)?\s*=>\s*void handleLaunch\(opts\)\s*\}/)
    // 反向：不得出现「把 opts 丢掉」的形态（历史 bug 的精确形状）
    expect(src).not.toMatch(/onLaunch=\{\s*\(\s*\)\s*=>\s*void handleLaunch\(\s*\)/)
    expect(src).not.toMatch(/onLaunch=\{\s*\(\s*\w+\s*\)\s*=>\s*void handleLaunch\(\)/)
  })

  it('handleLaunch 把隧道/瘦身/rollout 选项写进 POST body（漏了 = 点了不生效的假成功）', () => {
    const src = squash(APP)
    expect(src).toMatch(/handleLaunch\s*=\s*async\s*\(\s*opts\?/)
    for (const key of ['cfProtocol', 'cfEdgeIp', 'slim', 'rolloutSrc', 'trainMode']) {
      expect(src).toContain(`body.${key} = opts.${key}`)
    }
    // T7：降级开关是严格布尔（缺省 false），不走「未选 = 不传」那条路
    expect(src).toContain('remoteDegrade: opts?.remoteDegrade === true')
  })

  it('launch 链路里没有 mode / push 凭据（模式已退役，防回流）', () => {
    const src = squash(APP)
    expect(src).not.toContain('pushEndpoint')
    expect(src).not.toContain('pushAuthKey')
    expect(src).not.toMatch(/handleLaunch\s*=\s*async\s*\(\s*mode/)
    expect(src).not.toContain('body.mode')
  })

  it('弹窗侧：产出单参数 opts（隧道 + 降级 + 训练模式），不再有 pull/push 凭据', () => {
    const modal = squash(MODAL)
    // 唯一的上抛形态：opts 里带 remoteDegrade + 隧道选项 + 训练模式
    expect(modal).toMatch(
      /onLaunch\(\{\s*remoteDegrade,\s*cfProtocol,\s*cfEdgeIp,\s*slim,\s*rolloutSrc,\s*trainMode\s*\}\)/,
    )
    expect(modal).not.toContain('PushCredentials')
    expect(modal).not.toContain('pushEndpoint')
    expect(modal).not.toContain('pushAuthKey')
    // 已退役的 pull/push 模式键（`tc.train.mode`）不得复活；`TC_TRAIN_MODE` 是
    // 在线/离线那个**新**域，不是同一回事。
    expect(modal).not.toContain('tc.train.mode')
    // M1/M2/M3：三个选项控件确实在弹窗上，且各自显示「当前生效值」
    expect(modal).toContain('cfProtocol')
    expect(modal).toContain('cfEdgeIp')
    expect(modal).toContain('slim')
    expect(modal).toContain('rolloutSrc')
    expect(modal).toContain('modes.cfProtocol')
    expect(modal).toContain('modes.slim')
    expect(modal).toContain('modes.rolloutSrc')
  })
})

describe('route.ts preset 分支：只认隧道/瘦身/rollout 三个可选键', () => {
  it('preset 分支里没有 mode / pushEndpoint / pushAuthKey 的读取', () => {
    const route = squash(join(DASHBOARD_ROOT, 'src', 'server', 'api', 'route.ts'))
    const at = route.indexOf("case 'preset':")
    expect(at).toBeGreaterThan(-1)
    const presetCase = route.slice(at, route.indexOf("case 'setMode':"))
    expect(presetCase).not.toMatch(/bodyStr\(body, 'mode'\)/)
    expect(presetCase).not.toContain('pushEndpoint')
    expect(presetCase).not.toContain('pushAuthKey')
    // 白名单校验仍在（非法值 400，不静默落库）
    expect(presetCase).toContain("['http2', 'quic', 'auto']")
    expect(presetCase).toContain("['auto', 'local', 'node', 'run']")
  })
})
