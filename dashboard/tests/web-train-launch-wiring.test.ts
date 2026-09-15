/**
 * web-train-launch-wiring —— app.tsx → TrainLaunchModal 的 push 凭据透传（2026-09-15）。
 *
 * 实测 bug（2026-09-15 13:36，用户报「启动时明明填好正确的 cloud endpoint，却自动开本机节点」）：
 *   ① `TrainLaunchModal.tsx:156`  onLaunch(mode, { endpoint, authKey })  —— 弹窗**有**传凭据
 *   ② `app.tsx` 的接线写成 `(m) => void handleLaunch(m)`                —— **丢掉了第二个参数**
 *   ③ `app.tsx:304` `if (mode === 'push' && push)` 因 push=undefined 为假 ⇒ body 里根本没有
 *      pushEndpoint 键 ⇒ `route.ts:96 bodyStr` → '' ⇒ `preset.ts:48` 传空串
 *   ④ `configurePushEndpoint` 走健康扫描：唯一 enabled 的 gpu_push 是那条死 tunnel（实测 530）
 *      ⇒ 第 ③ 条「回落本机 worker_server」（`push-config.ts:240`）⇒ viaLocalWorker=true
 *      ⇒ `preset.ts:56` 启动顺序插入 workerServe ⇒ 控制台打印「启动本机伪 GPU 节点」。
 *
 * 为什么 TS/tsc 抓不到：`(m) => void` 对 `(mode, push?) => void` 是**合法**赋值（少形参合法），
 * 所以只能靠源码断言守住这条接线。仓库既有同款手法：`web-ssr-readonly.test.ts` 直接读 app.tsx。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const DASHBOARD_ROOT = join(import.meta.dir, '..')
const APP = join(DASHBOARD_ROOT, 'src', 'web', 'app', 'app.tsx')

describe('app.tsx → TrainLaunchModal push 凭据透传', () => {
  it('onLaunch 接线必须把 mode 与 push 凭据一起转给 handleLaunch', () => {
    const src = readFileSync(APP, 'utf8').replace(/\s+/g, ' ')

    // 守卫：正则必须真匹配到（防静默空跑）
    expect(src).toContain('TrainLaunchModal')

    // ① 正向：必须有两个形参
    const m = src.match(/onLaunch=\{\((\w+),\s*(\w+)\)\s*=>/)
    expect(m).not.toBeNull()
    expect(m![1]).toBe('m')
    expect(m![2]).toBe('push')

    // ② 反向：不得再出现「只转 mode」的形态（本次 bug 的精确形状）
    expect(src).not.toMatch(/onLaunch=\{\(\w+\)\s*=>\s*void handleLaunch\(\w+\)\}/)
  })

  it('handleLaunch 必须把凭据写进 POST body（下游 route.ts/preset.ts 才读得到）', () => {
    const src = readFileSync(APP, 'utf8').replace(/\s+/g, ' ')

    // 签名吃 push 可选参
    expect(src).toMatch(/handleLaunch\s*=\s*async\s*\(\s*mode[^)]*push\?/)
    // 只在 push 模式下写入（其它模式不带凭据）
    expect(src).toContain("if (mode === 'push' && push)")
    expect(src).toContain('body.pushEndpoint = push.endpoint')
    expect(src).toContain('body.pushAuthKey = push.authKey')
  })

  it('弹窗侧确实产出凭据对象（接线两端的契约一致）', () => {
    const modal = readFileSync(
      join(DASHBOARD_ROOT, 'src', 'web', 'app', 'panels', 'TrainLaunchModal.tsx'),
      'utf8',
    ).replace(/\s+/g, ' ')
    // 弹窗在 push 模式下必须带第二参数；只带 mode 就是另一端漏传。
    // T7 起还带 remoteDegrade（opt-in 降级本机）。
    expect(modal).toMatch(/onLaunch\(mode,\s*\{\s*endpoint,\s*authKey,\s*remoteDegrade\s*\}\)/)
    expect(modal).toContain('PushCredentials')
    // 非 push 路径也要透传 remoteDegrade（否则本地/pull 预设丢开关）。
    expect(modal).toMatch(/onLaunch\(mode,\s*\{\s*remoteDegrade\s*\}\)/)
  })
})
