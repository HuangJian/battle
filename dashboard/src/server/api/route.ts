/** route.ts — POST 动作路由（唯一动作入口，转到 server/actions 各处理器）。 */
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, writeFileSync } from 'fs'
import path from 'path'
import { loadConfig } from '../../core/config'
import { NN_TRAINING, REPO_ROOT } from '../../core/paths'
import type { CfEdgeIp, CfProtocol, Component, RolloutSrcMode, SlimMode } from '../../core/types'
import {
  ActionError,
  type ActionResult,
  busy,
  markCloudHaltRecovered,
  setMode,
  setNodeConcurrency,
  setNodeEnabled,
  smokeComponent,
  smokeTrain,
  startComponent,
  startPreset,
  stopAll,
  stopComponent,
  triggerCloudHalt,
} from '../actions'
import {
  abortEvalBatch,
  enqueueProbeRun,
  iterFromCkpt,
  startLadder,
  stopLadder,
} from '../eval-board'
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

/** 解析/分发 POST /api/*。返回 null = 未匹配（调用方 404）。 */
export async function routeAction(action: string, body: PostBody): Promise<Response | null> {
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
      case 'smoke': {
        const key = bodyStr(body, 'component') as Component
        if (!ALL_COMPONENTS.includes(key)) return errResp(`未知组件: ${key}`, 400)
        return okResp(await smokeComponent(key, ctx))
      }
      case 'smokeTrain':
        return okResp(await smokeTrain(ctx.course))
      case 'preset': {
        const mode = bodyStr(body, 'mode')
        if (!['pull', 'push', 'local'].includes(mode)) return errResp(`未知预设: ${mode}`, 400)
        // M1：隧道选项白名单（与 mode 同写法）——非法值 400，不静默落库。
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
        // M3：rollout 执行位置（同白名单写法）——与 python `choices=("auto","local","node")`
        // 同字面量域，直接落库（**无**域换算，别在这里发明 on/off 那种中间态）。
        const rolloutSrc = bodyStr(body, 'rolloutSrc')
        if (rolloutSrc && !['auto', 'local', 'node'].includes(rolloutSrc)) {
          return errResp(`未知 rollout 位置: ${rolloutSrc}（只接受 auto|local|node）`, 400)
        }
        return okResp(
          await startPreset(mode as 'pull' | 'push' | 'local', ctx.course, {
            pushEndpoint: bodyStr(body, 'pushEndpoint'),
            pushAuthKey: bodyStr(body, 'pushAuthKey'),
            // T7：布尔用严格 true（缺省/其它 = 关，不自动降级）。
            remoteDegrade: body.remoteDegrade === true,
            cfProtocol: (cfProtocol || undefined) as CfProtocol | undefined,
            cfEdgeIp: (cfEdgeIp || undefined) as CfEdgeIp | undefined,
            slim: (slim || undefined) as SlimMode | undefined,
            rolloutSrc: (rolloutSrc || undefined) as RolloutSrcMode | undefined,
          }),
        )
      }
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
        const ckpt = bodyStr(body, 'ckpt')
        if (!ckpt) return errResp('缺少 ckpt（权重文件路径）', 400)
        const iterRaw = Number(body.iter)
        const iter = Number.isFinite(iterRaw) && iterRaw > 0 ? iterRaw : (iterFromCkpt(ckpt) ?? 0)
        if (busy.has('eval:A')) return errResp('evalA 已在运行', 409)
        busy.add('eval:A')
        const resolved = (await import('../../core/venv')).resolveVenvPython()
        // 优先 venv 自带入口（.venv\Scripts\python.exe 已含依赖）；uv 跳板解析出的
        // 基础解释器须靠 PYTHONPATH 挂 site-packages，否则 pydantic 缺失。
        const venvEntry =
          process.platform === 'win32'
            ? path.join(NN_TRAINING, '.venv', 'Scripts', 'python.exe')
            : path.join(NN_TRAINING, '.venv', 'bin', 'python3')
        const pyBin = existsSync(venvEntry) ? venvEntry : resolved.python
        const sitePackages = resolved.sitePackages
        const script = path.join(NN_TRAINING, 'rl', 'eval_a_once.py')
        const logFile = path.join(REPO_ROOT, 'tmp', ctx.course, 'evalA.log')
        try {
          const { spawn } = await import('child_process')
          mkdirSync(path.dirname(logFile), { recursive: true })
          const out = openSync(logFile, 'a')
          // uv venv 跳板解析出的是基础解释器——必须 PYTHONPATH 挂 site-packages，
          // 否则 `import pydantic` 直接 ModuleNotFoundError（2026-09-12 实测）。
          const env = { ...process.env } as Record<string, string>
          if (sitePackages) {
            const prev = env.PYTHONPATH || env.PYTHONHOME || ''
            env.PYTHONPATH = prev ? `${sitePackages}${path.delimiter}${prev}` : sitePackages
          }
          const child = spawn(
            pyBin,
            [
              script,
              '--course',
              ctx.course,
              '--ckpt',
              ckpt,
              '--iter',
              String(iter),
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
          child.on('exit', () => {
            busy.delete('eval:A')
            try {
              closeSync(out)
            } catch {
              /* ignore */
            }
          })
          child.on('error', () => {
            busy.delete('eval:A')
            try {
              closeSync(out)
            } catch {
              /* ignore */
            }
          })
          child.unref()
          return okResp({
            ok: true,
            message: `evalA 已启动 it${iter}（课程干净评估 → eval_log；日志 tmp/${ctx.course}/evalA.log）`,
          })
        } catch (e) {
          busy.delete('eval:A')
          return errResp(e instanceof Error ? e.message : String(e), 500)
        }
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
