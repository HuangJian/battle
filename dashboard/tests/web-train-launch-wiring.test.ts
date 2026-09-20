/**
 * web-train-launch-wiring —— app.tsx → TrainLaunchModal 的启动选项透传。
 *
 * 历史（2026-09-15 实测 bug，用户报「启动时明明填好正确的 cloud endpoint，却自动开本机节点」）：
 *   ① `TrainLaunchModal.tsx` onLaunch(mode, { endpoint, authKey })  —— 弹窗**有**传凭据
 *   ② `app.tsx` 的接线写成 `(m) => void handleLaunch(m)`            —— **丢掉了第二个参数**
 *   ③ `if (mode === 'push' && push)` 因 push=undefined 为假 ⇒ body 里根本没有 pushEndpoint 键
 *      ⇒ `route.ts bodyStr` → '' ⇒ 预设走健康扫描/回落路径，控制台打印「启动本机伪 GPU 节点」。
 *
 * 现行口径（2026-09-20 用户指令「服务进程启动不应与课程绑定。进程启动时不要自动开启课程训练」）：
 *   ① pull 由远端 worker 自己来领（本机只需 hub 在线 + 可选隧道），push 只看系统里**是否登记了**
 *      push worker 节点（配置入口 = worker 登记面板，数据住 rl-config.json）⇒ 启动链路里
 *      **不再有 mode / endpoint / authKey**；
 *   ② 启动**只带进程级选项**（隧道/瘦身），课程级选项（训练模式 在线/离线 · rollout 位置 ·
 *      降级本机）整体迁到「开课」（`OpenCourseModal.tsx` → `openCourse` 动作，落
 *      `courses.<课>.*`）。
 *
 * 本文件守三条：**选项不得在路上丢掉**（③ 那类假成功）· **模式不得复活** ·
 * **课程级选项不得回流进启动链路**（回流 = 又变成「起进程先选一门课」）。
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

/** 剥掉注释后 squash（防回流断言查**代码**：迁移记录写在注释里，扫原文会假阳性）。 */
const squashCode = (p: string): string =>
  readFileSync(p, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/[^\n]*/g, '')
    .replace(/\s+/g, ' ')

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

  it('handleLaunch 把进程级选项（隧道/瘦身）写进 POST body（漏了 = 点了不生效的假成功）', () => {
    const src = squash(APP)
    expect(src).toMatch(/handleLaunch\s*=\s*async\s*\(\s*opts\?/)
    for (const key of ['cfProtocol', 'cfEdgeIp', 'slim']) {
      expect(src).toContain(`body.${key} = opts.${key}`)
    }
    // ★ 课程级选项**不得**回流启动 body（2026-09-20：那是「开课」的事；服务端也会 400 拒掉）
    for (const key of ['rolloutSrc', 'trainMode', 'remoteDegrade']) {
      expect(src).not.toContain(`body.${key} = opts.${key}`)
    }
  })

  it('launch 链路里没有 mode / push 凭据（模式已退役，防回流）', () => {
    const src = squash(APP)
    expect(src).not.toContain('pushEndpoint')
    expect(src).not.toContain('pushAuthKey')
    expect(src).not.toMatch(/handleLaunch\s*=\s*async\s*\(\s*mode/)
    expect(src).not.toContain('body.mode')
  })

  it('弹窗侧：产出单参数 opts（只剩隧道 + 瘦身），不再有 pull/push 凭据', () => {
    const modal = squashCode(MODAL)
    // 唯一的上抛形态：opts 里只剩进程级选项
    expect(modal).toMatch(/onLaunch\(\{\s*cfProtocol,\s*cfEdgeIp,\s*slim\s*\}\)/)
    expect(modal).not.toContain('PushCredentials')
    expect(modal).not.toContain('pushEndpoint')
    expect(modal).not.toContain('pushAuthKey')
    // 已退役的 pull/push 模式键（`tc.train.mode`）不得复活
    expect(modal).not.toContain('tc.train.mode')
    // M1/M2：两个选项控件确实在弹窗上，且各自显示「当前生效值」
    expect(modal).toContain('cfProtocol')
    expect(modal).toContain('cfEdgeIp')
    expect(modal).toContain('slim')
    expect(modal).toContain('modes.cfProtocol')
    expect(modal).toContain('modes.slim')
    // ★ 课程级选项**不得**留在启动弹窗（2026-09-20 用户口径：进程启动与课程无关）
    expect(modal).not.toContain('训练模式')
    expect(modal).not.toContain('remoteDegrade')
    expect(modal).not.toContain('rolloutSrc')
  })
})

describe('route.ts preset 分支：只认隧道/瘦身两个可选键（课程级选项已迁开课）', () => {
  it('preset 分支里没有 mode / pushEndpoint / pushAuthKey 的读取', () => {
    const route = squash(join(DASHBOARD_ROOT, 'src', 'server', 'api', 'route.ts'))
    const at = route.indexOf("case 'preset':")
    expect(at).toBeGreaterThan(-1)
    const presetCase = route.slice(at, route.indexOf("case 'openCourse':"))
    expect(presetCase).not.toMatch(/bodyStr\(body, 'mode'\)/)
    expect(presetCase).not.toContain('pushEndpoint')
    expect(presetCase).not.toContain('pushAuthKey')
    // 白名单校验仍在（非法值 400，不静默落库）
    expect(presetCase).toContain("['http2', 'quic', 'auto']")
    expect(presetCase).toContain("['on', 'off']")
    // ★ 课程级选项在 preset 里**响亮拒绝**（静默丢掉 = 一条假承诺）
    expect(presetCase).toContain('已迁到「开课」')
  })

  it('openCourse 分支：课程级选项在这里（白名单 + 落到 openCourse）', () => {
    const route = squash(join(DASHBOARD_ROOT, 'src', 'server', 'api', 'route.ts'))
    const at = route.indexOf("case 'openCourse':")
    const stopAt = route.indexOf("case 'stopCourse':")
    expect(at).toBeGreaterThan(-1)
    expect(stopAt).toBeGreaterThan(at)
    const openCase = route.slice(at, stopAt)
    expect(openCase).toContain("const trainMode = bodyStr(body, 'trainMode')")
    expect(openCase).toContain("['online', 'offline']")
    expect(openCase).toContain("['auto', 'local', 'node', 'run']")
    expect(openCase).toContain('await openCourse(ctx.course')
    // 停课：非破坏（服务端只认课程名，没有别的旋钮）
    expect(route).toContain('await stopCourse(ctx.course)')
  })
})
