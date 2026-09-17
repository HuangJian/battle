/** preset.ts — 模式预设与开关（stream / double_buffer / trainer pull|push|local）。
 *
 *  2026-09-15：`local` 不再是「进程内本机 PPO」——本机 PPO 拆成独立受管进程
 *  `localWorker`（云端 remote_worker 同一份代码，pull 本课 hub）。因此 local 预设
 *  变成 hubServer → localWorker → trainingLoop(--ppo remote + 本机 hub)。 */
import { loadConfig, saveConfig, validateCourseArg, writeRemoteHubUrl } from '../../core/config'
import { configurePushEndpoint } from '../../stack/push-config'
import type { CfEdgeIp, CfProtocol, Component, RolloutSrcMode, SlimMode } from '../../core/types'
import { tailscaleIp } from '../../core/net'
import { slotPort } from '../../core/slots'
import { rlConfigSmoke } from '../../stack/smoke'
import { slimToCfg } from '../../stack/specs'
import { ConsoleState, saveConsoleState } from './console-state'
import { ActionError, ActionResult, done, guard, release } from './result'
import { startComponent, StartCtx } from './start'
import { stopComponent } from './stop'

// ────────────────────────── 模式预设与开关 ──────────────────────────

export interface PresetOpts {
  /** Push：worker_server / cloudflared endpoint。留空 = 复用 rl-config 中 enabled 且 ping 通的
   *  gpu_push（含本机回落节点）；都没有 → 回落本机 worker_server（一键本机 push，2026-09-15）。 */
  pushEndpoint?: string
  /** Push：worker_server Bearer token（显式填写时必填；复用/回落时用节点 authKey·rl.remote_token）。 */
  pushAuthKey?: string
  /** T7：远端连败是否 opt-in 降级本机 PPO（默认 false）。 */
  remoteDegrade?: boolean
  /** M1：隧道协议/边缘 IP（随启动回写 rl-config.rl.* + console-state 生效值）。 */
  cfProtocol?: CfProtocol
  cfEdgeIp?: CfEdgeIp
  /** M2：协议瘦身回退开关（随启动回写 rl-config.rl.slim=1|0 + console-state `'on'|'off'`）。
   *  ⚠ 与 cf_* 不同：**不能**把字符串写进 rl-config（python `--remote-slim` 是
   *  `type=int, choices=(0,1)`），必须过 `slimToCfg()` 换算。 */
  slim?: SlimMode
  /** M3：rollout 执行位置（随启动回写 rl-config.rl.rollout_src + console-state）。
   *  ⚠ 与 slim **不同**：python `--rollout-src` 的 choices 就是这三个字符串——
   *  直接写，**不要**过任何换算函数。 */
  rolloutSrc?: RolloutSrcMode
}

/** 按 trainer 模式顺序拉起组件组合：
 *  pull = selfNode→hubServer→trainer（云机 poll 领取；hub 地址写成本机 tailnet IP，
 *         **不自动拉 cloudflared**——2026-09-16 起，隧道只在你单独点它时才起）；
 *  push = （执行面解析 + 回写 rl-config）→ selfNode→trainer
 *         （云机自起 cloudflared；hub 直推 code.zip/job，**不启本地 hubServer/cloudflared**）；
 *         执行面回落本机时（config 无可用 gpu_push）多一步 `workerServe` —— 本机
 *         worker_server 作为 push 接收端（与 localWorker 共用同一份 worker 代码）。
 *  local = hubServer→localWorker→trainer（本机 worker poll 本机 hub——worker 与
 *          trainer 两个进程，可各自随时启停；语义与云机 pull 完全一致）。
 *  任一步失败即中断（已完成的组件保留，页面可单独停止）。 */
export async function startPreset(
  mode: ConsoleState['trainerPpo'],
  course: string,
  opts: PresetOpts = {},
): Promise<ActionResult> {
  guard(`preset:${mode}`)
  try {
    if (!course) throw new ActionError('需要 course（先在顶部设置课程）')
    validateCourseArg(course)
    saveConsoleState({ trainerPpo: mode, course })
    // M1：隧道选项随启动回写（rl-config 的 rl.* 键 + console-state 生效值）——
    // 留空 = 不动（沿用 rl-config 现值/缺省 http2/4）。
    if (opts.cfProtocol || opts.cfEdgeIp || opts.slim || opts.rolloutSrc) {
      const cfgT = loadConfig()
      cfgT.rl = cfgT.rl || ({} as (typeof cfgT)['rl'])
      if (opts.cfProtocol) cfgT.rl.cf_protocol = opts.cfProtocol
      if (opts.cfEdgeIp) cfgT.rl.cf_edge_ip = opts.cfEdgeIp
      // M2：写数值域（`1|0`）——字符串会让训练侧 `choices=(0,1)` 直接报错退出。
      if (opts.slim) cfgT.rl.slim = slimToCfg(opts.slim)
      // M3：rollout 位置是字符串域，原样落 rl-config（与 --rollout-src choices 同字面量）。
      if (opts.rolloutSrc) cfgT.rl.rollout_src = opts.rolloutSrc
      saveConfig(cfgT)
      saveConsoleState({
        cfProtocol: opts.cfProtocol,
        cfEdgeIp: opts.cfEdgeIp,
        slim: opts.slim,
        rolloutSrc: opts.rolloutSrc,
      })
    }
    let pushNote = ''
    let viaLocalWorker = false
    if (mode === 'push') {
      // 执行面解析（ping 门 / 复用 config / 本机回落）+ 必要时回写 rl-config ——
      // 失败抛 ActionError，**绝不启动** trainingLoop。
      const t = await configurePushEndpoint(course, opts.pushEndpoint ?? '', opts.pushAuthKey ?? '')
      viaLocalWorker = t.viaLocalWorker
      pushNote =
        t.source === 'local'
          ? `; 无可用 gpu_push → 回落本机 worker_server (${t.url})，已把本课 push 目标指到本机；无本地 hub-server/cloudflared`
          : t.source === 'config'
            ? `; 复用 rl-config gpu_push (${t.url}) 已 ping 通；无本地 hub-server/cloudflared`
            : `; push endpoint 已验证并回写 rl-config (${t.url})；无本地 hub-server/cloudflared`
    }
    // pull **不再自动拉 cloudflared**（2026-09-16）：云机与本机组网后用 tailnet 直连
    // 本课 hub 即可。理由不是"少一个组件"——隧道回源会把**所有**云端流量归成
    // 127.0.0.1，hub 的 D9 闭锁（5 次鉴权失败封 IP 3600s）一旦触发，训练主循环会
    // 被其它云机的失败连坐（x3-step 事故：训练循环连续 403 自杀退出、云机空转一整晚）。
    // 需要公网隧道时，单独点「cloudflared」组件启动即可（不会自动跑）。
    let hubNote = ''
    if (mode === 'pull') {
      const cfgNow = loadConfig()
      const ip = tailscaleIp()
      if (ip) {
        const hubUrl = `http://${ip}:${slotPort(cfgNow, course, 'hub')}`
        writeRemoteHubUrl(hubUrl, course)
        hubNote = `; 云机 pull 地址 = tailnet 直连 ${hubUrl}（未启动 cloudflared）`
      } else {
        hubNote = '; 未检测到 Tailscale 网卡 IP——remote_hub_url 未改（云机需自行可达本课 hub）'
      }
    }
    const order: Component[] =
      mode === 'pull'
        ? ['selfNode', 'hubServer', 'trainingLoop']
        : mode === 'push'
          ? viaLocalWorker
            ? ['selfNode', 'workerServe', 'trainingLoop']
            : ['selfNode', 'trainingLoop']
          : ['hubServer', 'localWorker', 'trainingLoop']
    // 离开 local：清掉上一轮 local 预设留下的 localWorker（进程+登记）。否则切到
    // pull/push 后卡片仍亮绿点——操作员以为「未启动却在跑」（2026-09-16 用户反馈）。
    // pull/push 不消费本机独立 worker；停失败不阻断预设（训练主路径更重要）。
    if (mode !== 'local') {
      try {
        await stopComponent('localWorker', course)
      } catch {
        /* leftover stop is best-effort */
      }
    }
    const ctx: StartCtx = {
      course,
      trainerPpo: mode,
      remoteDegrade: !!opts.remoteDegrade,
    }
    const detail: string[] = []
    for (const k of order) {
      const r = await startComponent(k, ctx)
      detail.push(`${k}: ${r.message}${r.detail && !r.ok ? ` — ${r.detail[0] ?? ''}` : ''}`)
      if (!r.ok) return done(false, `${mode} 预设启动中断于 ${k}`, detail)
    }
    return done(
      true,
      `已按 ${mode} 模式启动 ${order.length} 个组件 (course=${course})${pushNote}${hubNote}`,
      detail,
    )
  } catch (e) {
    if (e instanceof ActionError) throw e
    // configurePushEndpoint 的 ping/校验失败 → ActionError（响亮，不启动）
    throw new ActionError(e instanceof Error ? e.message : String(e))
  } finally {
    release(`preset:${mode}`)
  }
}

/** 模式开关：rl.* 键回写 rl-config.json（run_rl 真实键，下次 trainer 启动生效）；
 *  trainer.ppo 持久化到 console-state.json（pull/push/local 基建编排选择）。 */
export async function setMode(key: string, value: string): Promise<ActionResult> {
  guard(`mode:${key}`)
  try {
    if (key === 'trainer.ppo') {
      if (!['pull', 'push', 'local'].includes(value))
        throw new ActionError(`未知 trainer 模式: ${value}`)
      saveConsoleState({ trainerPpo: value as ConsoleState['trainerPpo'] })
      return done(true, `trainer 模式 = ${value}（下次启动 trainer 生效）`)
    }
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
      default:
        throw new ActionError(`未知模式开关: ${key}`)
    }
    saveConfig(cfg)
    const smoke = rlConfigSmoke(cfg)
    return done(
      smoke.passed,
      `${key} = ${value}（已回写 rl-config.json，下次 trainer 启动生效）`,
      smoke.passed ? undefined : [smoke.detail ?? ''],
    )
  } finally {
    release(`mode:${key}`)
  }
}
