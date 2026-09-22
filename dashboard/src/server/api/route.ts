/** route.ts — POST 动作路由（唯一动作入口，转到 server/actions 各处理器）。 */
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, writeFileSync } from 'fs'
import path from 'path'
import { loadConfig } from '../../core/config'
import { log, warn } from '../../core/log'
import { NN_TRAINING, REPO_ROOT, loopControlPath } from '../../core/paths'
import type {
  CfEdgeIp,
  CfProtocol,
  Component,
  RolloutSrcMode,
  SlimMode,
  TrainMode,
} from '../../core/types'
import {
  ActionError,
  type ActionResult,
  busy,
  jobIdError,
  markCloudHaltRecovered,
  openCourse,
  registerPushWorker,
  reloadPushWorkers,
  removePushWorker,
  setCourseMode,
  setCoursePaused,
  setMode,
  setNodeConcurrency,
  setNodeEnabled,
  smokeComponent,
  smokeTrain,
  startComponent,
  startPreset,
  stopAll,
  stopComponent,
  stopCourse,
  triggerCloudHalt,
  unfreezeJob,
} from '../actions'
import {
  abortEvalBatch,
  enqueueProbeRun,
  iterFromCkpt,
  startLadder,
  stopLadder,
} from '../eval-board'
import { launchTaskBundleExport, taskBundleInfo } from '../bundles'
import { launchEvalA } from '../eval-a-run'
import { ALL_COMPONENTS } from './component-meta'
import { loadConfigSafe } from './config'
import { actionCtx } from './courses'
import { REPLAY_EXPORT_BUSY_KEY, REPLAY_EXPORT_MAX_GAMES, replayExportPaths } from './eval-games'

// ────────────────────────── 动作路由 ──────────────────────────

export interface PostBody {
  [key: string]: unknown
}

export function bodyStr(b: PostBody, k: string): string {
  const v = b[k]
  return typeof v === 'string' ? v : ''
}

function okResp(r: ActionResult, status = 200): Response {
  return new Response(JSON.stringify(r), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  })
}

export function errResp(message: string, status: number): Response {
  return okResp({ ok: false, message }, status)
}

/** 读接口（不是决策）：高频轮询/回读不能往决策日志里刷行。 */
const READONLY_ACTIONS = new Set(['getGateHaltMode'])

/** **视图态动作**：改的是「操作员在看哪门课」或一次只读回读——不碰任何组件/节点/队列事实。
 *
 *  ★ 为什么单列一张表（2026-09-22，多课程并行切课要等几秒的最后一块）：`setCourse` 是
 *  **切课自己发出的动作**，只写 `console-state.course`；而 server.ts 此前对**所有**动作
 *  一律作废慢快照 + hub 观测面 ⇒ 切一次课就把机群级探测（节点 ping 1.5s + 共享 hub 探测
 *  1.2s）连同课程级快照一起丢掉，下一次 /api/state 必须冷算。也就是说：「切课要等几秒」
 *  有一半是切课这个动作自己造成的——作废缓存的动作和它保护的东西是同一个。
 *
 *  `getGateHaltMode` 同列：它是客户端每次切课时顺带的只读回读（POST 只是传输方式），
 *  返回一个标志文件的文本，快照里根本不含它。 */
export const VIEW_ONLY_ACTIONS: ReadonlySet<string> = new Set(['setCourse', 'getGateHaltMode'])

/** 动作是否要作废快照缓存（server.ts 的调用判据；见 `VIEW_ONLY_ACTIONS`）。
 *  纯函数，便于门禁用例直接钉住（不再靠「server.ts 里那一行还在不在」）。 */
export function invalidatesSnapshot(action: string): boolean {
  return !VIEW_ONLY_ACTIONS.has(action)
}

/** **动作结果落一行盘**（组件级决策的「操作面」；2026-09-20 用户指令）。
 *
 *  为什么这一行是必需的：盘上的证据只有「组件日志 + 账本」，而组件状态变化之前
 *  一定先有「有人点了什么」——但那件事以前只在终端滚屏 / UI 回执里存在。2026-09-20
 *  的教训：hub 被人工停掉后，盘上只剩「日志断在半分钟前 + 账本条目没了」，从证据里
 *  **分不出**「人工停的」与「自己死的」，只能反过来去问操作员。这一行就是那个因果。
 *
 *  失败走 `warn`（带 ⚠️）——「点了没成」与「点了成了」在日志里一眼可分。
 *  任何解析/写盘异常都不影响动作本身（日志不是关键路径）。
 */
async function logActionDecision(
  action: string,
  body: PostBody,
  resp: Response | null,
): Promise<void> {
  if (!resp || READONLY_ACTIONS.has(action)) return
  const who = [bodyStr(body, 'course'), bodyStr(body, 'component')].filter(Boolean).join('/')
  let ok: unknown = null
  let message = ''
  try {
    const parsed = (await resp.clone().json()) as { ok?: unknown; message?: unknown }
    ok = parsed?.ok ?? null
    message = typeof parsed?.message === 'string' ? parsed.message : ''
  } catch {
    /* 非 JSON 响应：只记 HTTP 状态码 */
  }
  const line =
    `[action] ${action}${who ? ` ${who}` : ''} → ${ok === true ? 'ok' : 'fail'}` +
    ` (HTTP ${resp.status})${message ? `: ${message}` : ''}`
  if (ok === true) log(line)
  else warn(line)
}

/** 解析/分发 POST /api/*。返回 null = 未匹配（调用方 404）。
 *
 *  ★ 出口只有一个，且**结果必落盘**（`logActionDecision`）：这是「谁在何时对什么
 *   做了什么」的唯一可靠来源（见上面那段理由）。新加动作自动继承，不需各自记得打日志。
 */
export async function routeAction(action: string, body: PostBody): Promise<Response | null> {
  const resp = await dispatchAction(action, body)
  await logActionDecision(action, body, resp)
  return resp
}

/** 动作分发的内部实现（只被 `routeAction` 调用；落盘在那边统一做）。 */
async function dispatchAction(action: string, body: PostBody): Promise<Response | null> {
  const ctx = actionCtx(body)
  try {
    switch (action) {
      case 'start': {
        const key = bodyStr(body, 'component') as Component
        if (!ALL_COMPONENTS.includes(key)) return errResp(`未知组件: ${key}`, 400)
        return okResp(await startComponent(key, ctx))
      }
      case 'stop': {
        const key = bodyStr(body, 'component') as Component
        if (!ALL_COMPONENTS.includes(key)) return errResp(`未知组件: ${key}`, 400)
        return okResp(await stopComponent(key, ctx.course))
      }
      case 'stopAll':
        return okResp(await stopAll())
      case 'cloud-halt': {
        // 手动下发停机命令（§386 + S17）：按当前课程下发，任务照常分发。
        const reason = bodyStr(body, 'reason') || '手动下发停机命令'
        return okResp(await triggerCloudHalt(loadConfigSafe(), reason, ctx.course))
      }
      case 'cloud-resume': {
        // 停机条件消失（手动恢复）：本课 hub resume + recovered（灰横幅保留历史）。
        const clearReason = bodyStr(body, 'reason') || '手动恢复'
        return okResp(await markCloudHaltRecovered(loadConfigSafe(), clearReason, ctx.course))
      }
      case 'unfreeze-job': {
        // §4.1 毒包熔断的**人工解冻**（唯一的可逆口；重发刻意不解除冻结）。
        // job_id 形状先在路由层挡一次：它是**参数错误**（400），不是 busy（409）——
        // 判据与动作层同源（同一个 `jobIdError`）。
        const jobId = bodyStr(body, 'jobId')
        const badJob = jobIdError(jobId)
        if (badJob) return errResp(badJob, 400)
        return okResp(await unfreezeJob(ctx.course, jobId))
      }
      case 'smoke': {
        const key = bodyStr(body, 'component') as Component
        if (!ALL_COMPONENTS.includes(key)) return errResp(`未知组件: ${key}`, 400)
        return okResp(await smokeComponent(key, ctx))
      }
      case 'smokeTrain':
        return okResp(await smokeTrain(ctx.course))
      case 'preset': {
        // 启动训练（2026-09-19 起只有一条编排：selfNode → hubServer → trainer）。
        // 执行面不再是「模式」：由 rl.hub_push + 登记节点推出来（`remoteExecutionFace`）。
        // M1：隧道选项白名单——非法值 400，不静默落库。
        const cfProtocol = bodyStr(body, 'cfProtocol')
        const cfEdgeIp = bodyStr(body, 'cfEdgeIp')
        if (cfProtocol && !['http2', 'quic', 'auto'].includes(cfProtocol)) {
          return errResp(`未知隧道协议: ${cfProtocol}（只接受 http2|quic|auto）`, 400)
        }
        if (cfEdgeIp && !['4', '6', 'auto'].includes(cfEdgeIp)) {
          return errResp(`未知边缘 IP 版本: ${cfEdgeIp}（只接受 4|6|auto）`, 400)
        }
        // M2：协议瘦身回退开关（同白名单写法）——`'on'|'off'` 是 UI 域，落库时换算成 1|0。
        const slim = bodyStr(body, 'slim')
        if (slim && !['on', 'off'].includes(slim)) {
          return errResp(`未知瘦身开关: ${slim}（只接受 on|off）`, 400)
        }
        // ★ 课程级选项（trainMode / rolloutSrc / remoteDegrade）**已迁到「开课」**
        // （2026-09-20：进程启动不再为某门课写配置）——还往这里发就是一条静默无效的承诺，
        // 所以响亮拒绝并指路，而不是默默丢掉。
        for (const k of ['trainMode', 'rolloutSrc'] as const) {
          if (body[k] !== undefined) {
            return errResp(`${k} 是课程级选项，已迁到「开课」（openCourse）`, 400)
          }
        }
        // ★ §3（2026-09-21）：`remoteDegrade` **已删除**（不是搬走）——单一 PPO 路径下没有
        //   「降级本机」档位。旧客户端还可能发它：响亮拒绝（静默忽略 = 一条不会发生的承诺）。
        if (body.remoteDegrade !== undefined) {
          return errResp(
            'remoteDegrade 已删除：PPO 恒为「发布到 hub 队列 + 等 worker 认领」，' +
              '没有就地降级本机这一档（plan/accident.plan.md §3）',
            400,
          )
        }
        return okResp(
          await startPreset({
            cfProtocol: (cfProtocol || undefined) as CfProtocol | undefined,
            cfEdgeIp: (cfEdgeIp || undefined) as CfEdgeIp | undefined,
            slim: (slim || undefined) as SlimMode | undefined,
          }),
        )
      }
      // ---- 开课 / 停课（2026-09-20 用户指令：进程启动与课程解耦，课程生命周期独立入口）----
      case 'openCourse': {
        // 训练模式（`在线|离线`）：离线要同时写两个课程级键（run + run_iters=-1）并把该课
        // hub 置 offline，换算在 `stack/specs.ts::trainModeKnobs`（这里只做白名单）。
        const trainMode = bodyStr(body, 'trainMode')
        if (trainMode && !['online', 'offline'].includes(trainMode)) {
          return errResp(`未知训练模式: ${trainMode}（只接受 online|offline）`, 400)
        }
        // rollout 位置：与 python `rl/loop_steps.py::ROLLOUT_SRCS` 同字面量域。
        const rolloutSrc = bodyStr(body, 'rolloutSrc')
        if (rolloutSrc && !['auto', 'local', 'node', 'run'].includes(rolloutSrc)) {
          return errResp(`未知 rollout 位置: ${rolloutSrc}（只接受 auto|local|node|run）`, 400)
        }
        return okResp(
          await openCourse(ctx.course, {
            trainMode: (trainMode || undefined) as TrainMode | undefined,
            rolloutSrc: (rolloutSrc || undefined) as RolloutSrcMode | undefined,
          }),
        )
      }
      case 'stopCourse':
        return okResp(await stopCourse(ctx.course))
      case 'setMode': {
        const key = bodyStr(body, 'key')
        const value = bodyStr(body, 'value')
        return okResp(await setMode(key, value))
      }
      case 'getGateHaltMode': {
        // 门禁动作模式（2026-09-13）：halt = 触发门禁时下发云端停机达令（默认）；
        // notify = 只记录 verdict + 横幅提示，绝不停云机。
        const { readGateHaltMode } = await import('../../stack/specs')
        // okResp 的载荷是 ActionResult（ok/message/detail）——模式值走 message 回传，
        // 客户端据此校准开关（不为此扩 ActionResult 类型，避免污染所有动作返回值）。
        return okResp({ ok: true, message: readGateHaltMode(ctx.course) })
      }
      case 'setGateHaltMode': {
        // 写 `<traj>/gate-halt-mode.txt`；Python 侧每轮门判定读它（优先于启动参数）
        // ⇒ 训练途中切换**立即生效**，无需重启。
        const mode = bodyStr(body, 'mode')
        if (mode !== 'halt' && mode !== 'notify') {
          return errResp(`未知门禁模式: ${mode}（只接受 halt|notify）`, 400)
        }
        if (!ctx.course) return errResp('未指定课程（无法定位 traj 目录）', 400)
        const { writeGateHaltMode } = await import('../../stack/specs')
        const written = writeGateHaltMode(ctx.course, mode)
        return okResp({
          ok: true,
          message: written === 'notify' ? 'notify（只提示，不下发停机令）' : 'halt（下发停机令）',
        })
      }
      case 'setCourse': {
        const { saveConsoleState, ActionError } = await import('../actions')
        const course = bodyStr(body, 'course')
        if (course) {
          // validateCourseArg 会 process.exit（CLI 语义）——控制台改抛 ActionError。
          const { existsSync } = await import('fs')
          const { curriculaDir } = await import('../../core/paths')
          const pathMod = await import('path')
          if (
            !existsSync(course) &&
            !existsSync(pathMod.default.join(curriculaDir(), `${course}.jsonc`)) &&
            !existsSync(pathMod.default.join(curriculaDir(), `${course}.bc.jsonc`))
          ) {
            throw new ActionError(
              `课程不存在: ${course}（curricula/ 下无同名 .jsonc/.bc.jsonc，或传已存在的课程文件路径）`,
            )
          }
        }
        saveConsoleState({ course })
        return okResp({ ok: true, message: `当前课程 = ${course || '(空)'}` })
      }
      case 'setNodeEnabled': {
        const id = bodyStr(body, 'id')
        const enabled = body.enabled === true || body.enabled === 'true'
        return okResp(await setNodeEnabled(id, enabled))
      }
      case 'setNodeConcurrency': {
        const id = bodyStr(body, 'id')
        const n = Number(body.concurrency)
        if (!Number.isFinite(n)) return errResp(`并发数非法: ${body.concurrency}`, 400)
        return okResp(await setNodeConcurrency(id, Math.round(n)))
      }
      // ---- GPU push worker 登记（2026-09-18）：回写 rl-config nodes[] + 叫醒 hub ----
      // （hub 观测面缓存的置空不在本层：动作后统一在 server.ts 与慢快照同时失效）
      case 'registerPushWorker': {
        return okResp(
          await registerPushWorker({
            id: bodyStr(body, 'id'),
            url: bodyStr(body, 'url'),
            authKey: bodyStr(body, 'authKey'),
            // 并发数缺省由 actions 侧填 1（不在这里编默认值，避免两处口径）。
            concurrency: body.concurrency === undefined ? undefined : Number(body.concurrency),
            enabled: body.enabled === undefined ? undefined : body.enabled !== false,
          }),
        )
      }
      // ---- 每课 hub 派发模式（R3-2）：热切 + 落意图（起 hub 时回灌）----
      case 'setCourseMode':
        return okResp(await setCourseMode(bodyStr(body, 'course'), bodyStr(body, 'mode')))
      // ---- 每课「暂停/恢复」意图（R2d 操作面）：写 tmp/loop-control.json，训练进程每拍读 ----
      // 缺 `paused` 字段 = 暂停（前端只传方向时不必再编一个布尔约定）。
      case 'setCoursePaused':
        return okResp(
          setCoursePaused(bodyStr(body, 'course'), body.paused !== false, loopControlPath()),
        )
      case 'removePushWorker':
        return okResp(await removePushWorker(bodyStr(body, 'id')))
      case 'reloadPushWorkers':
        return okResp(await reloadPushWorkers())
      case 'nodeSmoke': {
        const id = bodyStr(body, 'id')
        if (busy.has(`node:${id}`)) return errResp('该节点冒烟进行中', 409)
        const cfg = loadConfig()
        const node = cfg.nodes.find((x) => x.id === id)
        if (!node) return errResp(`节点不存在: ${id}`, 400)
        if (!node.enabled) return okResp({ ok: false, message: '节点已停用——先启用再冒烟' })
        const cCourse = ctx.course
        const cPath = path.join(REPO_ROOT, 'tmp', cCourse, 'weights.json')
        const weightsPath = existsSync(cPath)
          ? cPath
          : path.join(REPO_ROOT, 'tmp/ep60/battle2-p1bc/run/weights.json')
        if (!existsSync(weightsPath))
          return okResp({ ok: false, message: '无可用权重文件——无法 rollout 冒烟' })
        busy.add(`node:${id}`)
        try {
          const raw = readFileSync(weightsPath)
          const bytes = new Uint8Array(raw.byteLength)
          bytes.set(raw)
          const { rolloutSmokeNode, weightsFingerprint } = await import('../../stack/smoke')
          const wver = await weightsFingerprint(weightsPath)
          const passed = await rolloutSmokeNode(node, bytes, wver)
          return okResp({
            ok: passed,
            message: passed ? `${id} rollout 冒烟通过` : `${id} 冒烟失败（明细见控制台服务日志）`,
          })
        } finally {
          busy.delete(`node:${id}`)
        }
      }
      case 'evalProbeRun': {
        // EvalBench §5.3：只写请求文件（runner 下窗物化为批）；派发期节点配置
        // 冻结——任一 node:* 动作进行中则 409（与 setNodeEnabled 共 busy 语义）。
        for (const k of busy) {
          if (k.startsWith('node:')) return errResp('节点配置调整中，稍后再触发评估批', 409)
        }
        if (busy.has('eval:probe')) return errResp('评估入队进行中', 409)
        const rungFrom = bodyStr(body, 'rung_from') || bodyStr(body, 'rung') || 'c4l1'
        const ckpt = bodyStr(body, 'ckpt')
        if (!ckpt) return errResp('缺少 ckpt（权重文件路径）', 400)
        const policy = bodyStr(body, 'policy') === 'god' ? 'god' : 'nn'
        busy.add('eval:probe')
        try {
          // D-b：iter 优先取显式入参，否则从 `weights.it<N>.json` 解析（不靠前端）。
          const iterRaw = Number(body.iter)
          const iter = Number.isFinite(iterRaw) && iterRaw > 0 ? iterRaw : (iterFromCkpt(ckpt) ?? 0)
          const r = enqueueProbeRun({
            course: ctx.course,
            rung_from: rungFrom,
            ckpt,
            requester: bodyStr(body, 'requester') || 'web',
            iter,
            policy: policy as 'nn' | 'god',
            ladder_pos: body.ladder_pos === undefined ? undefined : Number(body.ladder_pos),
            k_seq: body.k_seq === undefined ? undefined : Number(body.k_seq),
          })
          if (r.deduped) {
            return okResp({ ok: true, message: '评估请求已在队列（去重，不重复入队）' })
          }
          return okResp({
            ok: true,
            message: `评估请求已入队 ${r.req_id}（runner 下窗认领；训练忙碌时可本机运行 kick-once.py 手动执行）`,
          })
        } finally {
          busy.delete('eval:probe')
        }
      }
      case 'evalA': {
        // 课程设计评估（A 层）：用该 iter 权重跑与训练每 eval_every 轮相同的干净评估，
        // 写 tmp/<course>/eval_log.jsonl（与 EvalBoard B 层 evalProbeRun 无关）。
        // 唯一启动点在 server/eval-a-run.ts——「导入产物后自动评估」走的是同一个函数
        // （共享互斥键，否则两处会各起一个 evalA 写同一份 eval_log）。
        const ckpt = bodyStr(body, 'ckpt')
        if (!ckpt) return errResp('缺少 ckpt（权重文件路径）', 400)
        const iterRaw = Number(body.iter)
        const iter = Number.isFinite(iterRaw) && iterRaw > 0 ? iterRaw : (iterFromCkpt(ckpt) ?? 0)
        const r = launchEvalA(ctx.course, ckpt, iter)
        if (!r.ok) return errResp(r.message, r.message.includes('已在运行') ? 409 : 500)
        return okResp({ ok: true, message: r.message })
      }
      case 'exportTaskBundle': {
        // 任务包导出（`task-<课程>.zip`）：把整段剩余交给云机。真正的导出在 python 一侧
        // （run_rl --export-bundle）；这里只起进程 + 把产出文件信息回给面板。
        if (!ctx.course) return errResp('缺少 course', 400)
        // 互斥在 launchTaskBundleExport 里，靠**子进程退出**释放（导出是长任务）。
        const r = launchTaskBundleExport(ctx.course)
        if (!r.ok) return errResp(r.message, 409)
        return okResp({
          ok: true,
          message: r.message,
          detail: [`产物将落在 ${taskBundleInfo(ctx.course).path}`],
        })
      }
      case 'evalReplays': {
        // 导出 replay：确定性重放所选 eval 局 → .replay（rl/eval_replays_once.py；
        // 同 evalA 的 spawn-detached + 日志 + busy 互斥模式，弹窗轮询 /api/evalReplayJob）。
        if (!ctx.course) return errResp('缺少 course', 400)
        const iterRaw = Number(body.iter)
        if (!Number.isInteger(iterRaw) || iterRaw < 0) return errResp('iter 非法', 400)
        const wver = bodyStr(body, 'wver')
        if (!/^[0-9a-f]{16}$/.test(wver)) return errResp('wver 非法（需 16 位 hex）', 400)
        const gamesRaw = body.games
        if (!Array.isArray(gamesRaw) || gamesRaw.length === 0)
          return errResp('缺少 games（勾选至少一局）', 400)
        if (gamesRaw.length > REPLAY_EXPORT_MAX_GAMES)
          return errResp(`单次导出上限 ${REPLAY_EXPORT_MAX_GAMES} 局——请缩小勾选范围`, 400)
        const games: Array<{ stage: number; seed: number }> = []
        for (const g of gamesRaw) {
          const o = g as Record<string, unknown>
          const stage = Number(o.stage)
          const seed = Number(o.seed)
          if (!Number.isInteger(stage) || !Number.isInteger(seed) || stage < 0 || seed < 0) {
            return errResp('games 行非法（stage/seed 须为非负整数）', 400)
          }
          games.push({ stage, seed })
        }
        if (busy.has(REPLAY_EXPORT_BUSY_KEY)) return errResp('已有 replay 导出在进行', 409)
        busy.add(REPLAY_EXPORT_BUSY_KEY)
        const resolved = (await import('../../core/venv')).resolveVenvPython()
        const venvEntry =
          process.platform === 'win32'
            ? path.join(NN_TRAINING, '.venv', 'Scripts', 'python.exe')
            : path.join(NN_TRAINING, '.venv', 'bin', 'python3')
        const pyBin = existsSync(venvEntry) ? venvEntry : resolved.python
        const sitePackages = resolved.sitePackages
        const script = path.join(NN_TRAINING, 'rl', 'eval_replays_once.py')
        const p = replayExportPaths(ctx.course)
        try {
          mkdirSync(path.dirname(p.gamesFile), { recursive: true })
          writeFileSync(p.gamesFile, JSON.stringify(games))
          const { spawn } = await import('child_process')
          mkdirSync(path.dirname(p.log), { recursive: true })
          const out = openSync(p.log, 'w')
          // 同 evalA：uv 跳板解析的基础解释器须 PYTHONPATH 挂 site-packages（pydantic）。
          const env = { ...process.env } as Record<string, string>
          if (sitePackages) {
            const prev = env.PYTHONPATH || env.PYTHONHOME || ''
            env.PYTHONPATH = prev ? `${sitePackages}${path.delimiter}${prev}` : sitePackages
          }
          const child = spawn(
            pyBin,
            [
              '-u',
              script,
              '--course',
              ctx.course,
              '--iter',
              String(iterRaw),
              '--wver',
              wver,
              '--games',
              p.gamesFile,
              '--out-dir',
              p.outDir,
              '--manifest',
              p.manifest,
              '--bun',
              'bun',
            ],
            {
              cwd: path.join(REPO_ROOT, 'nn-training'),
              detached: true,
              stdio: ['ignore', out, out],
              windowsHide: true,
              env,
            },
          )
          const release = (): void => {
            busy.delete(REPLAY_EXPORT_BUSY_KEY)
            try {
              closeSync(out)
            } catch {
              /* ignore */
            }
          }
          child.on('exit', release)
          child.on('error', release)
          child.unref()
          return okResp({
            ok: true,
            message: `replay 导出已启动（${games.length} 局，确定性重放 it${iterRaw} 的 eval）——弹窗自动跟踪进度；日志 tmp/${ctx.course}/replay-export.log`,
          })
        } catch (e) {
          busy.delete(REPLAY_EXPORT_BUSY_KEY)
          return errResp(e instanceof Error ? e.message : String(e), 500)
        }
      }
      case 'evalBatchAbort': {
        // P4 温和中止：只写 abort 请求；在途单元跑完即停（runner 消费后标 aborted）。
        const batchId = bodyStr(body, 'batch_id')
        if (!batchId) return errResp('缺少 batch_id', 400)
        try {
          const r = abortEvalBatch(batchId, bodyStr(body, 'requester') || 'web')
          return okResp({
            ok: true,
            message: r.deduped
              ? `中止请求已在队列 ${r.req_id}（去重）`
              : `已请求中止 ${batchId}（在途单元跑完即停，runner 下窗生效）`,
          })
        } catch (e) {
          return errResp(e instanceof Error ? e.message : String(e), 400)
        }
      }
      case 'evalLadderStart': {
        // R4 自动爬梯启动：只写 ladder_start 请求；推进由 console ticker 执行。
        for (const k of busy) {
          if (k.startsWith('node:')) return errResp('节点配置调整中，稍后再启动爬梯', 409)
        }
        const ckpt = bodyStr(body, 'ckpt')
        if (!ckpt) return errResp('缺少 ckpt（权重文件路径）', 400)
        const iterRaw = Number(body.iter)
        const iter = Number.isFinite(iterRaw) && iterRaw > 0 ? iterRaw : (iterFromCkpt(ckpt) ?? 0)
        const thRaw = Number(body.threshold)
        const threshold = Number.isFinite(thRaw) && thRaw > 0 && thRaw < 1 ? thRaw : 0.2
        const r = startLadder({
          course: ctx.course,
          iter,
          threshold,
          start_rung: bodyStr(body, 'start_rung') || bodyStr(body, 'rung_from') || 'c4l1',
          ckpt,
          requester: bodyStr(body, 'requester') || 'web',
        })
        if (r.deduped) {
          return okResp({ ok: true, message: '爬梯任务已在进行（去重，不重复启动）' })
        }
        return okResp({
          ok: true,
          message: `自动爬梯已启动 ${r.req_id}（it${iter}，阈值 ${threshold}）`,
        })
      }
      case 'evalLadderStop': {
        // R4 爬梯停止：只写 ladder_stop 请求；ticker 见 stop 即停（无状态推导）。
        const iterRaw = Number(body.iter)
        const iter = Number.isFinite(iterRaw) && iterRaw > 0 ? iterRaw : 0
        const r = stopLadder({
          course: ctx.course,
          iter,
          reason: bodyStr(body, 'reason') || undefined,
          requester: bodyStr(body, 'requester') || 'web',
        })
        return okResp({ ok: true, message: `爬梯已停止 ${r.req_id}` })
      }
      default:
        return null
    }
  } catch (e) {
    if (e instanceof ActionError) return errResp(e.message, 409)
    return errResp(e instanceof Error ? e.message : String(e), 500)
  }
}

// ================================================================
// curriculumLadderView —— I5（roadmap v2.0 §4-I5）：阶梯统一 identity 台账的
// 控制台 LAN 只读渲染。读 nn-training/ladder/LEDGER.jsonc（I4 gate runner 与
// rl/ladder_ledger.py 双写方，字段级 merge），返回 20 级 + 经典的 status /
// lastGate / hypothesis / teacherWR 摘要。与 God-AI evalboard 的 ladder.json
// （LadderRung）完全无关——命名特意区分。
// ================================================================
