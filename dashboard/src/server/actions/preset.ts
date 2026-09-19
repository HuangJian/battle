/** preset.ts — **启动训练**（一套编排，不再是 pull/push/local 三选一）。
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
import { loadConfig, saveConfig, validateCourseArg, writeRemoteHubUrl } from '../../core/config'
import type {
  CfEdgeIp,
  CfProtocol,
  Component,
  RolloutSrcMode,
  SlimMode,
  TrainMode,
} from '../../core/types'
import { tailscaleIp } from '../../core/net'
import { sharedHubUrl } from '../../core/slots'
import { remoteExecutionFace } from '../../stack/push-config'
import { rlConfigSmoke } from '../../stack/smoke'
import { slimToCfg, trainModeKnobs } from '../../stack/specs'
import { saveConsoleState } from './console-state'
import { setCourseMode } from './course-mode'
import { ActionError, ActionResult, done, guard, release } from './result'
import { startComponent, StartCtx } from './start'

// ────────────────────────── 启动训练 ──────────────────────────

/** 启动顺序（恒一条路）：本机 agent → 共享 hub → 共享 trainer。 */
export const TRAIN_START_ORDER: readonly Component[] = [
  'selfNode',
  'hubServer',
  'trainingLoop',
] as const

export interface PresetOpts {
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
   *  ⚠ 与 slim **不同**：python `--rollout-src` 的 choices 就是这几个字符串——
   *  直接写，**不要**过任何换算函数。 */
  rolloutSrc?: RolloutSrcMode
  /** 训练模式（2026-09-19）：`offline` ⇒ 写该课的 `{rollout_src:'run', run_iters:-1}`
   *  并把该课 hub 模式置 offline（只有带标 worker 能领整段）；`online` ⇒ **删掉**该课的
   *  这两个覆盖键（不删就「切回在线了但还在整段上云」）。域换算见 `trainModeKnobs`。 */
  trainMode?: TrainMode
}

export async function startPreset(course: string, opts: PresetOpts = {}): Promise<ActionResult> {
  guard('preset:train')
  try {
    if (!course) throw new ActionError('需要 course（先在顶部设置课程）')
    validateCourseArg(course)
    saveConsoleState({ course })
    // M1：隧道选项随启动回写（rl-config 的 rl.* 键 + console-state 生效值）——
    // 留空 = 不动（沿用 rl-config 现值/缺省 http2/4）。
    // 模式 → 课程级键的换算只走 `trainModeKnobs` 一处；全局面取它的 rolloutSrc，
    // 而离线档算出来的就是 `run`——`run` **绝不写进全局 rl.rollout_src**（那个键是所有课
    // 共用的默认面，落进去 = 把全部课程一起拖进离线，而用户在弹窗里只选了这一门课）。
    const knobs = opts.trainMode ? trainModeKnobs(opts.trainMode, opts.rolloutSrc ?? 'local') : null
    const globalRolloutSrc: RolloutSrcMode | undefined =
      knobs === null ? opts.rolloutSrc : knobs.rolloutSrc === 'run' ? undefined : knobs.rolloutSrc
    if (opts.cfProtocol || opts.cfEdgeIp || opts.slim || globalRolloutSrc || opts.trainMode) {
      const cfgT = loadConfig()
      cfgT.rl = cfgT.rl || ({} as (typeof cfgT)['rl'])
      if (opts.cfProtocol) cfgT.rl.cf_protocol = opts.cfProtocol
      if (opts.cfEdgeIp) cfgT.rl.cf_edge_ip = opts.cfEdgeIp
      // M2：写数值域（`1|0`）——字符串会让训练侧 `choices=(0,1)` 直接报错退出。
      if (opts.slim) cfgT.rl.slim = slimToCfg(opts.slim)
      // M3：rollout 位置是字符串域，原样落 rl-config（与 --rollout-src choices 同字面量）。
      if (globalRolloutSrc) cfgT.rl.rollout_src = globalRolloutSrc
      // 训练模式（2026-09-19）：离线是**课程级**决定（`courses.<课>.{rollout_src,run_iters}`），
      // 不落 rl.*（那是所有课共用的默认面）。
      if (knobs && opts.trainMode) {
        cfgT.courses = cfgT.courses ?? {}
        const row = (cfgT.courses[course] = cfgT.courses[course] ?? {})
        if (opts.trainMode === 'offline') {
          // 两个键缺一不可：`run` 是声明，`run_iters` 是段长（`-1` = 到课程末）。
          row.rollout_src = knobs.rolloutSrc
          row.run_iters = knobs.runIters ?? -1
        } else {
          // 切回在线 = **撤掉离线标记**：段长必删（留着它 = 下一轮又被当成段长 + 本机
          // 采样 = 半状态），课程级的 `run` 也删。但**不能**顺手删掉别的课程级覆盖
          // （有人显式写过 `rollout_src:'node'`，那是本课在线的另一个理由，不归这里管）。
          delete row.run_iters
          if (row.rollout_src === 'run') delete row.rollout_src
        }
      }
      saveConfig(cfgT)
      saveConsoleState({
        cfProtocol: opts.cfProtocol,
        cfEdgeIp: opts.cfEdgeIp,
        slim: opts.slim,
        rolloutSrc: globalRolloutSrc,
      })
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
    // 训练模式 = 离线时把该课的 hub 派发闸也切到 offline：整段 job 只能被**带标** worker
    // 领走（hub 对离线课一律不放行普通 worker）。不切的话，整段 job 会被任意一台普通
    // worker 领走并对着一份「跑不完的任务」开工。在线启动同样下发 online：训练模式字的
    // 就是「本机跑 + 实时派发」，留着旧的 offline 会让在线启动静默停摆。
    let modeNote = ''
    if (opts.trainMode) {
      const r = await setCourseMode(course, opts.trainMode === 'offline' ? 'offline' : 'online')
      modeNote = `；训练模式 ${opts.trainMode === 'offline' ? '离线（整段上云）' : '在线'}：${r.message}`
    }
    const ctx: StartCtx = { course, remoteDegrade: !!opts.remoteDegrade }
    const detail: string[] = []
    for (const k of TRAIN_START_ORDER) {
      const r = await startComponent(k, ctx)
      detail.push(`${k}: ${r.message}${r.detail && !r.ok ? ` — ${r.detail[0] ?? ''}` : ''}`)
      // trainer 是**共享**进程：它的首条详情是「本课执行面 = …」——启动之后最需要确认的就是这条
      // （本轮 PPO 会去哪、缺什么会退到下一条路）。幂等早退（已在运行）时同样有这两行，故不按 ok 分支。
      if (k === 'trainingLoop' && r.detail?.[0]) detail.push(`  ${r.detail[0]}`)
      if (!r.ok) return done(false, `启动训练中断于 ${k}`, detail)
    }
    const face = remoteExecutionFace(loadConfig())
    return done(
      true,
      `已启动训练栈 (course=${course})${hubNote}${modeNote}` +
        `；本轮执行面：${face.text}` +
        '（trainer 是共享进程：一个进程服务所有课程，停它 = 停全部）',
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
