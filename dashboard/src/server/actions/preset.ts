/** preset.ts — **启动服务进程**（一套编排，不再是 pull/push/local 三选一）。
 *
 *  ★ 2026-09-20 用户口径：「服务进程启动不应与课程绑定。进程启动时不要自动开启课程训练，
 *  需要增加独立的入口开启/停止课程训练」。本动作因此**不再要课程**：
 *   · 它做的是「起进程」：selfNode → hubServer → trainingLoop；
 *   · 「开哪门课」是另一个入口的事：`course-lifecycle.ts::{openCourse,stopCourse}`
 *     （顶部课程选择旁的开课/停课）。
 *  旧形状把课程准备（播权重 / 建账本 / 写课程旋钮 / 置 hub 模式）挂在第三步上，代价是
 *  ①「起个进程」被迫先选一门课；②置 hub 模式发生在课程还不存在时 → hub 回
 *  `需要合法 course（[]）`（2026-09-20 实测）。
 *
 *  ★ 2026-09-19 用户口径：「启动课程训练时，trainloop 不需要指定 pull/push 模式。pull 模式是由
 *  远端 worker 自己请求，本机只需要保证 hub 在线，配以 tailscale/cloudflared tunnel。push 模式
 *  只看系统是否已经配置了 push worker 节点，界面留配置入口，节点数据存 rl-config.json」。
 *
 *  ⇒ 编排恒为 **selfNode → hubServer → trainer**：
 *   · hub 在线 + （可选）隧道 = 让**任何** worker（云机 / 本机）都能来领活；
 *   · 这轮 PPO 到底推给谁由**部署事实**决定（`rl.hub_push` 缺省开 + 登记在册的 `nodes[].gpu_push`
 *     + hub 队列），见 `stack/push-config.ts::remoteExecutionFace` —— 课程侧一个字都不配；
 *   · 本机 worker 不再是「local 模式」的一部分：它是一张独立的共享卡片，起它就参与领活
 *     （与云机逐字同权，「所有 worker 都可能接到在训的课程任务，不管它是哪个课程的」）。
 *
 *  **不再自动拉 cloudflared**（2026-09-16）：云机与本机组网后用 tailnet 直连 hub 即可；隧道
 *  回源会把所有云端流量归成 127.0.0.1，hub 的 D9 闭锁一旦触发会让训练主循环被别的云机连坐。
 *  需要公网隧道时单独点「cloudflared」组件（不会自动跑）。
 *
 *  任一步失败即中断（已完成的组件保留，页面可单独停止）。 */
import { loadConfig, saveConfig, writeRemoteHubUrl } from '../../core/config'
import type { CfEdgeIp, CfProtocol, Component, SlimMode } from '../../core/types'
import { tailscaleIp } from '../../core/net'
import { sharedHubUrl } from '../../core/slots'
import { remoteExecutionFace } from '../../stack/push-config'
import { rlConfigSmoke } from '../../stack/smoke'
import { slimToCfg } from '../../stack/specs'
import { saveConsoleState } from './console-state'
import { ActionError, ActionResult, done, guard, release } from './result'
import { startComponent, StartCtx } from './start'

// ────────────────────────── 启动服务进程 ──────────────────────────

/** 启动顺序（恒一条路）：本机 agent → 共享 hub → 共享 trainer。 */
export const TRAIN_START_ORDER: readonly Component[] = [
  'selfNode',
  'hubServer',
  'trainingLoop',
] as const

export interface PresetOpts {
  /** M1：隧道协议/边缘 IP（随启动回写 rl-config.rl.* + console-state 生效值）。 */
  cfProtocol?: CfProtocol
  cfEdgeIp?: CfEdgeIp
  /** M2：协议瘦身回退开关（随启动回写 rl-config.rl.slim=1|0 + console-state `'on'|'off'`）。
   *  ⚠ 与 cf_* 不同：**不能**把字符串写进 rl-config（python `--remote-slim` 是
   *  `type=int, choices=(0,1)`），必须过 `slimToCfg()` 换算。 */
  slim?: SlimMode
}

/** **启动服务进程**（selfNode → 共享 hub → 共享 trainer）。与课程无关：不传课程、
 *  不建账本、不写课程旋钮、不置 hub 模式——那些是「开课」（`openCourse`）的事。 */
export async function startPreset(opts: PresetOpts = {}): Promise<ActionResult> {
  guard('preset:train')
  try {
    // M1：隧道选项随启动回写（rl-config 的 rl.* 键 + console-state 生效值）——
    // 留空 = 不动（沿用 rl-config 现值/缺省 http2/4）。
    // ★ 这里只写 `rl.*`（全进程共用的默认面）：课程级选项（训练模式 / rollout 位置 /
    //  降级本机）随「开课」走，写 `courses.<课>.*`。
    if (opts.cfProtocol || opts.cfEdgeIp || opts.slim) {
      const cfgT = loadConfig()
      cfgT.rl = cfgT.rl || ({} as (typeof cfgT)['rl'])
      if (opts.cfProtocol) cfgT.rl.cf_protocol = opts.cfProtocol
      if (opts.cfEdgeIp) cfgT.rl.cf_edge_ip = opts.cfEdgeIp
      // M2：写数值域（`1|0`）——字符串会让训练侧 `choices=(0,1)` 直接报错退出。
      if (opts.slim) cfgT.rl.slim = slimToCfg(opts.slim)
      saveConfig(cfgT)
      saveConsoleState({ cfProtocol: opts.cfProtocol, cfEdgeIp: opts.cfEdgeIp, slim: opts.slim })
    }
    // hub 地址：把本机 tailnet IP 写进单键 `rl.remote_hub_url`（**pull 与 hub 派发都要它**）。
    // 没有它就只能直推节点——而「worker 自己来领」是默认路径，所以这一步不能省。
    let hubNote = ''
    const cfgNow = loadConfig()
    const ip = tailscaleIp()
    if (ip) {
      const hubUrl = sharedHubUrl(cfgNow, ip)
      writeRemoteHubUrl(hubUrl)
      hubNote = `；hub 地址 = tailnet 直连 ${hubUrl}（一个 hub 服务所有课程，未启动 cloudflared）`
    } else {
      hubNote = '；未检测到 Tailscale 网卡 IP——remote_hub_url 未改（worker 需自行可达共享 hub）'
    }
    // 进程编排：只起进程。`StartCtx.course` 恒空 —— 共享 hub / 共享 trainer 都不按课键控
    // （本机 worker 同）。课程由「开课」入队。
    const ctx: StartCtx = { course: '' }
    const detail: string[] = []
    for (const k of TRAIN_START_ORDER) {
      const r = await startComponent(k, ctx)
      detail.push(`${k}: ${r.message}${r.detail && !r.ok ? ` — ${r.detail[0] ?? ''}` : ''}`)
      if (!r.ok) return done(false, `启动服务进程中断于 ${k}`, detail)
    }
    const face = remoteExecutionFace(loadConfig())
    return done(
      true,
      `已启动服务进程（本机 agent → 共享 hub → 共享 trainer）${hubNote}` +
        `；执行面：${face.text}` +
        '（进程与课程是两件事：**没有自动开课**——用顶部课程选择旁的「开课」把课程入队；' +
        'trainer 是共享进程：一个进程服务所有课程，停它 = 停全部）',
      detail,
    )
  } catch (e) {
    if (e instanceof ActionError) throw e
    throw new ActionError(e instanceof Error ? e.message : String(e))
  } finally {
    release('preset:train')
  }
}

/** 模式开关：rl.* 键回写 rl-config.json（训练侧真实键，下次 trainer 启动生效）。
 *
 *  2026-09-19 删掉了 `trainer.ppo`（pull/push/local）：执行面不再是一个「模式」，而是由
 *  `rl.hub_push` + 登记节点推出来的事实——它的配置入口在 worker 登记面板。 */
export async function setMode(key: string, value: string): Promise<ActionResult> {
  guard(`mode:${key}`)
  try {
    const cfg = loadConfig()
    cfg.rl = cfg.rl || {}
    const num = Number(value)
    if (value !== '0' && value !== '1') throw new ActionError(`${key} 只接受 0/1，收到: ${value}`)
    switch (key) {
      case 'rl.stream':
        cfg.rl.stream = num
        break
      case 'rl.double_buffer':
        cfg.rl.double_buffer = num
        break
      case 'rl.precollect_early':
        cfg.rl.precollect_early = num
        break
      case 'rl.hub_push':
        // hub 中介派发开关（缺省开）：配了节点就走 hub 派发，关掉则训练侧直推节点。
        cfg.rl.hub_push = num === 1
        break
      default:
        throw new ActionError(`未知模式开关: ${key}`)
    }
    saveConfig(cfg)
    const smoke = rlConfigSmoke(cfg)
    return done(
      smoke.passed,
      `${key} = ${value}（已回写 rl-config.json；训练栈重启后生效）`,
      smoke.passed ? undefined : [smoke.detail ?? ''],
    )
  } finally {
    release(`mode:${key}`)
  }
}
