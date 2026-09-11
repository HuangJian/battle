/** actions.ts — 控制台动作层：组件 启/停/冒烟 · 模式开关 · 节点编辑（回写 rl-config）。
 *
 *  与 CLI 启动器的关系：复用同一套 spawn/账本/冒烟/哨兵原语（proc/registry/hub/
 *  smoke/push），但动作是**面向单组件**的（页面上点某个组件的启动/停止），而
 *  start.ts 的 hub/push 模式是"整条流水线一次拉起"。组件间依赖由 startPreset 的
 *  顺序声明（hub-server 先于 cloudflared/trainer；self-node 是采集底线）。
 *
 *  并发纪律：每个动作是短命异步流程；同一 key 的并发点击被 busy 互斥集合挡住
 *  （第二次调用直接抛 ActionError，由 API 层转 409），不做排队。
 *
 *  模式开关落点：stream / double_buffer / precollect_early 是 run_rl 的真实
 *  rl-config 键 → 直接回写 rl-config.json（run_rl 下次启动即生效）；trainer 的
 *  pull/push/local 是控制台的基建编排选择（pull=remote+隧道，push=remote 无本地
 *  隧道，local=本机 PPO）→ 存 console-state.json，trainer 启动时翻译成
 *  --ppo/--env 与基建组件组合。
 */

import {
  appendFileSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from 'fs'
import path from 'path'
import { CURRICULA_DIR, LOG_DIR, NN_TRAINING, REPO_ROOT, START_LOG_DIR } from '../paths'
import { httpOk, killPid, pidAlive, waitUntil } from '../net'
import { clearComponent, loadRegistry, saveComponent } from '../registry'
import { loadConfig, saveConfig, validateCourseArg } from '../config'
import { launchSpec, portOwnerPids, stopAllManaged } from '../proc'
import { resolveVenvPython } from '../venv'
import { monitorTouch } from '../reload-touch'
import {
  TRAINING_LOOP_ENTRY,
  cloudflaredSpec,
  hubServerSpec,
  selfNodeSpec,
  trainingLoopSpec,
  workerServeSpec,
} from '../specs'
import {
  rlConfigSmoke,
  rolloutSmoke,
  selfNodeSmoke,
  summarizeSmoke,
  type SmokeItem,
} from '../smoke'
import {
  hubServerHealthy,
  selfNodeHealthy,
  stepCloudflared,
  stepHubServer,
  stepKaggleRehearsal,
  stepSelfNode,
} from '../hub'
import { startLocalWorkerServer } from '../push'
import type { Component, ProcSpec, RlConfig } from '../types'

/** 动作失败（API 层转 409/400，页面 toast 显示 message）。 */
export class ActionError extends Error {}

/** 进行中的动作 key（`start:trainingLoop` / `node:mac` / `mode:rl.stream`）。 */
export const busy = new Set<string>()

function guard(key: string): void {
  if (busy.has(key)) throw new ActionError('动作进行中，请稍候')
  busy.add(key)
}
function release(key: string): void {
  busy.delete(key)
}

export interface ActionResult {
  ok: boolean
  message: string
  /** 逐项明细（冒烟子项 / 预设启动各步）。 */
  detail?: string[]
}

function done(ok: boolean, message: string, detail?: string[]): ActionResult {
  return { ok, message, ...(detail && detail.length > 0 ? { detail } : {}) }
}

// ────────────────────────── 控制台状态（trainer 模式 / 当前课程） ──────────────────────────

// 测试注入位（同 registry.ts 的 BCITY_REGISTRY_FILE）：默认线上路径，
// 单测置 env 重定向到临时目录，绝不写脏线上 console-state.json。
const CONSOLE_STATE =
  process.env.BCITY_CONSOLE_STATE || path.join(START_LOG_DIR, 'console-state.json')

export interface CloudHaltInfo {
  /** 停机时刻（ISO）。 */
  at: string
  /** 触发原因（训练停车/异常退出的判词，或手动）。 */
  reason: string
}

export interface ConsoleState {
  /** trainer 基建编排模式：pull=remote+隧道 · push=remote 无本地隧道 · local=本机 PPO。 */
  trainerPpo: 'pull' | 'push' | 'local'
  /** 当前课程（组件启动的 jobRoot/日志目录来源）。 */
  course: string
  /** 云端停机记录（有值 = 处云端停机态，UI 出横幅；「恢复云端」后清除）。 */
  cloudHalt?: CloudHaltInfo
}

const DEFAULT_STATE: ConsoleState = { trainerPpo: 'pull', course: '' }

export function loadConsoleState(): ConsoleState {
  try {
    return {
      ...DEFAULT_STATE,
      ...(JSON.parse(readFileSync(CONSOLE_STATE, 'utf-8')) as ConsoleState),
    }
  } catch {
    return { ...DEFAULT_STATE }
  }
}

export function saveConsoleState(patch: Partial<ConsoleState>): ConsoleState {
  const next = { ...loadConsoleState(), ...patch }
  try {
    mkdirSync(path.dirname(CONSOLE_STATE), { recursive: true })
    writeFileSync(CONSOLE_STATE, JSON.stringify(next, null, 2), 'utf-8')
  } catch {
    /* 非致命——内存态仍生效到本进程 */
  }
  return next
}

// ────────────────────────── 云端停机 / 恢复（§385 复审：停云端省 GPU 配额，本地进程不动） ──────────────────────────

/** hub 管理端点（Bearer 同 worker）。hub 不可达/鉴权失败 → false（不抛）。 */
export function hubAdminOk(cfg: RlConfig, pathSuffix: string): Promise<boolean> {
  if (!cfg.rl.hub_port) return Promise.resolve(false)
  return httpOk(`http://127.0.0.1:${cfg.rl.hub_port}${pathSuffix}`, cfg.rl.remote_token, 5000)
}

/** 云端停机（幂等）：hub 置 halt（worker 下轮轮询即退出）+ console-state 记原因。
 *  只停云端——hubServer/trainingLoop/console 一律不动（§385 复审语义）。 */
export async function triggerCloudHalt(
  cfg: RlConfig,
  reason: string,
): Promise<{ ok: boolean; message: string }> {
  if (loadConsoleState().cloudHalt) {
    return { ok: true, message: '已是云端停机态（幂等跳过）' }
  }
  const ok = await hubAdminOk(cfg, '/admin/workers/halt')
  if (!ok) {
    return { ok: false, message: '云端停机指令下发失败（hub 不可达或拒绝）' }
  }
  saveConsoleState({ cloudHalt: { at: new Date().toISOString(), reason } })
  return { ok: true, message: `云端已停机：${reason}` }
}

/** 恢复云端：hub 复位 halt + 清 console-state 停机记录。worker 会话需另行重启才重新入队。 */
export async function resumeCloud(cfg: RlConfig): Promise<{ ok: boolean; message: string }> {
  const ok = await hubAdminOk(cfg, '/admin/workers/resume')
  saveConsoleState({ cloudHalt: undefined })
  if (!ok) {
    return { ok: false, message: '恢复指令失败（hub 不可达或拒绝）；已清除本地停机标记' }
  }
  return { ok: true, message: '云端已恢复（停机标记清除）；worker 会话需重启后重新入队' }
}

// ────────────────────────── 组件标签与就绪谓词 ──────────────────────────

export const COMPONENT_LABELS: Record<Component, string> = {
  selfNode: 'self-node (采集节点)',
  hubServer: 'hub-server (作业中枢)',
  cloudflared: 'cloudflared (入站隧道)',
  trainingLoop: 'TrainingLoop (trainer)',
  workerServe: 'worker_server (本机伪 GPU 节点)',
}

function tailLines(p: string, n = 8): string[] {
  try {
    return readFileSync(p, 'utf-8')
      .split('\n')
      .filter(Boolean)
      .slice(-n)
      .map((l) => l.slice(0, 240))
  } catch {
    return []
  }
}

/** run_rl 单实例锁持有人（与 start.ts preflight 同语义；null = 无存活持有人）。 */
function runRlLockHolder(): number | null {
  const lockPath = path.join(NN_TRAINING, '.run_rl.lock')
  if (!existsSync(lockPath)) return null
  try {
    const holder = Number.parseInt((readFileSync(lockPath, 'utf-8').split('|')[0] ?? '').trim(), 10)
    return Number.isInteger(holder) && holder > 0 && pidAlive(holder) ? holder : null
  } catch {
    return null
  }
}

/** 课程 BC 种子路径（§384）：读课程 jsonc 的 `bc` 字段（相对仓库根解析）；
 *  文件缺失/解析失败/无 bc 键时回退 legacy 硬编码（旧课程兼容）。 */
export function resolveCourseBc(course: string): string {
  const legacy = path.join(REPO_ROOT, 'tmp/ep60/battle2-p1bc/run/weights.json')
  try {
    const raw = readFileSync(path.join(CURRICULA_DIR, `${course}.jsonc`), 'utf-8')
    // JSONC 容尾逗号：oxfmt 给 curricula/*.jsonc 加的尾逗号是合法 JSONC、非法 JSON。
    // 不剥掉 → JSON.parse 抛错 → 静默回退 legacy 种子路径（§384 的事故正是这个
    // 静默回退：读不到课程 bc 就拿旧权重开腿）。剥完再解析，解析失败仍回退。
    const stripped = raw
      .split('\n')
      .filter((l) => !l.trimStart().startsWith('//'))
      .join('\n')
      .replace(/,(\s*[}\]])/g, '$1')
    const bc: unknown = (JSON.parse(stripped) as { bc?: unknown }).bc
    if (typeof bc === 'string' && bc.length > 0) return path.join(REPO_ROOT, bc)
  } catch {
    /* 回退 legacy */
  }
  return legacy
}

// ────────────────────────── 组件启动 ──────────────────────────

export interface StartCtx {
  course: string
  /** trainer 模式（pull/push → --ppo remote；local → 不带 --ppo）。 */
  trainerPpo: ConsoleState['trainerPpo']
}

/** 启动单个组件（已在运行 = 幂等成功；依赖缺失 = ActionError/失败结果）。 */
export async function startComponent(key: Component, ctx: StartCtx): Promise<ActionResult> {
  guard(`start:${key}`)
  try {
    const cfg = loadConfig()
    const venv = resolveVenvPython()
    const reg = loadRegistry()

    switch (key) {
      case 'selfNode': {
        if (await selfNodeHealthy(cfg))
          return done(true, `self-node 已在运行 (port ${cfg.rl.agent_port})`)
        await stepSelfNode(cfg)
        return done(true, 'self-node 已启动')
      }
      case 'hubServer': {
        if (!ctx.course) throw new ActionError('hub-server 需要 course（先在顶部设置课程）')
        validateCourseArg(ctx.course)
        if (await hubServerHealthy(cfg))
          return done(true, `hub-server 已在运行 (port ${cfg.rl.hub_port})`)
        const trajDir = path.join(REPO_ROOT, 'tmp', ctx.course)
        await stepHubServer(cfg, path.join(trajDir, 'remote-jobs'))
        return done(true, 'hub-server 已启动')
      }
      case 'cloudflared': {
        const url = await stepCloudflared(cfg, false)
        return done(true, `隧道已就绪: ${url}`)
      }
      case 'workerServe': {
        if (reg.workerServe?.pid && pidAlive(reg.workerServe.pid))
          await killPid(reg.workerServe.pid)
        const { pushUrl, servePid } = await startLocalWorkerServer({
          course: ctx.course || 'smoke',
          cfgToken: cfg.rl.remote_token,
          hubPort: cfg.rl.hub_port,
          venv,
        })
        saveComponent('workerServe', {
          pid: servePid,
          url: pushUrl,
          entry: 'nn-training/remote_worker_serve.py',
        })
        monitorTouch()
        return done(true, `本机伪 GPU 节点就绪: ${pushUrl}`)
      }
      case 'trainingLoop': {
        if (!ctx.course) throw new ActionError('trainer 需要 course（先在顶部设置课程）')
        validateCourseArg(ctx.course)
        if (pidAlive(reg.trainingLoop?.pid))
          return done(false, `TrainingLoop 已在运行 (PID ${reg.trainingLoop!.pid})——先停止再启动`)
        const holder = runRlLockHolder()
        if (holder)
          return done(
            false,
            `run_rl 锁被 PID ${holder} 持有——先停止在跑训练（或删除 nn-training/.run_rl.lock）`,
          )

        const trajDir = path.join(REPO_ROOT, 'tmp', ctx.course)
        const weightsPath = path.join(trajDir, 'weights.json')
        if (!existsSync(weightsPath)) {
          const bcPath = resolveCourseBc(ctx.course)
          mkdirSync(trajDir, { recursive: true })
          if (existsSync(bcPath)) {
            copyFileSync(bcPath, weightsPath)
          } else {
            return done(false, `初始权重缺失且 BC 产物不存在: ${bcPath}`)
          }
        }
        const trainLog = path.join(LOG_DIR, ctx.course, 'training-loop.log')
        const baseline = (() => {
          try {
            return statSync(trainLog).size
          } catch {
            return 0
          }
        })()
        const spec = trainingLoopSpec(cfg, {
          course: ctx.course,
          ppo: ctx.trainerPpo,
          venv,
        })
        const r = launchSpec(spec)
        saveComponent('trainingLoop', {
          pid: r.pid,
          course: ctx.course,
          entry: TRAINING_LOOP_ENTRY,
          mode: ctx.trainerPpo,
        })
        monitorTouch()
        await waitUntil(
          async () => {
            if (!pidAlive(r.pid)) return true
            try {
              return statSync(trainLog).size > baseline
            } catch {
              return false
            }
          },
          20000,
          500,
        )
        if (!pidAlive(r.pid)) {
          // §380：启动即退出不许静默——往日志文件追加失败标记（含尾日志）后再清账，
          // 否则只剩 startComponent 响应里的临时 tail，刷新即丢（2026-09-08 vk1 事故）。
          try {
            appendFileSync(
              trainLog,
              `\n[console] ${new Date().toISOString()} ${COMPONENT_LABELS[key]} 启动即退出` +
                ` (PID ${r.pid})——启动失败，原因见上方日志尾段：\n` +
                tailLines(trainLog)
                  .map((l) => `  | ${l}`)
                  .join('\n') +
                '\n',
              'utf-8',
            )
          } catch {
            /* best-effort */
          }
          clearComponent('trainingLoop')
          return done(false, `TrainingLoop 启动即退出 (PID ${r.pid})`, tailLines(trainLog))
        }
        return done(true, `TrainingLoop 已启动 (PID ${r.pid}, ppo=${ctx.trainerPpo})`)
      }
    }
  } catch (e) {
    if (e instanceof ActionError) throw e
    return done(false, `${COMPONENT_LABELS[key]} 启动失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    release(`start:${key}`)
  }
}

// ────────────────────────── 组件停止 ──────────────────────────

/** 停止单个组件（按账本；无登记时按端口兜底清场——与 --kill 同语义）。 */
export async function stopComponent(key: Component): Promise<ActionResult> {
  guard(`stop:${key}`)
  try {
    const entry = loadRegistry()[key]
    if (entry?.pid) {
      if (pidAlive(entry.pid)) {
        const dead = await killPid(entry.pid)
        if (!dead) return done(false, `${COMPONENT_LABELS[key]} (PID ${entry.pid}) 未能停止`)
      }
      clearComponent(key)
      return done(true, `${COMPONENT_LABELS[key]} 已停止`)
    }
    // 无登记：端口兜底（selfNode=agent_port；hubServer=hub_port；workerServe=hub_port+2）
    const ports: Record<string, (cfg: RlConfig) => number> = {
      selfNode: (c) => c.rl.agent_port,
      hubServer: (c) => c.rl.hub_port,
      workerServe: (c) => c.rl.hub_port + 2,
    }
    if (ports[key]) {
      const cfg = loadConfig()
      const pids = portOwnerPids(ports[key]!(cfg))
      for (const pid of pids) await killPid(pid)
      return done(true, pids.length > 0 ? `已按端口兜底停止 ${pids.length} 个进程` : '未在运行')
    }
    return done(true, `${COMPONENT_LABELS[key]} 未在运行`)
  } catch (e) {
    return done(false, `停止失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    release(`stop:${key}`)
  }
}

/** 停止全部受管进程（账本 + 端口兜底，复用 CLI --kill 语义）。 */
export async function stopAll(): Promise<ActionResult> {
  guard('stop:all')
  try {
    await stopAllManaged()
    return done(true, '已停止全部受管进程')
  } catch (e) {
    return done(false, `停止失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    release('stop:all')
  }
}

// ────────────────────────── 组件冒烟 ──────────────────────────

/** 单组件冒烟（各组件子集不同：轻量 ping / 真 rollout 一局）。 */
export async function smokeComponent(key: Component, ctx: StartCtx): Promise<ActionResult> {
  guard(`smoke:${key}`)
  try {
    const cfg = loadConfig()
    const items: SmokeItem[] = []
    const extraDetail: string[] = []

    switch (key) {
      case 'selfNode': {
        items.push(await selfNodeSmoke(cfg))
        if (ctx.course) {
          items.push(
            await rolloutSmoke(cfg, path.join(REPO_ROOT, 'tmp', ctx.course, 'weights.json')),
          )
        } else {
          items.push({ name: 'rollout 冒烟', passed: true, fatal: false, detail: '未设课程，跳过' })
        }
        break
      }
      case 'hubServer': {
        const hubOk = await hubServerHealthy(cfg)
        items.push({
          name: 'hub-server /ping',
          passed: hubOk,
          fatal: true,
          detail: hubOk ? `port ${cfg.rl.hub_port}` : '未运行或 token 不匹配',
        })
        break
      }
      case 'cloudflared': {
        const url = loadRegistry().cloudflared?.url ?? ''
        if (!url) {
          items.push({ name: 'cloudflared', passed: false, fatal: false, detail: '未建立隧道' })
          break
        }
        const ping = await httpOk(`${url}/ping`, cfg.rl.remote_token, 10000)
        items.push({
          name: `隧道 ping (${url})`,
          passed: ping,
          fatal: false,
          detail: ping ? undefined : 'edge 在线但出网劣化，或隧道不可达',
        })
        break
      }
      case 'workerServe': {
        const ping = await httpOk(
          `http://127.0.0.1:${cfg.rl.hub_port + 2}/ping`,
          cfg.rl.remote_token,
          3000,
        )
        items.push({ name: '本机伪 GPU 节点 /ping', passed: ping, fatal: false })
        break
      }
      case 'trainingLoop': {
        const reg = loadRegistry()
        const alive = pidAlive(reg.trainingLoop?.pid)
        items.push({ name: 'TrainingLoop 进程存活', passed: alive, fatal: false })
        const logPath = path.join(
          LOG_DIR,
          ctx.course || loadConsoleState().course,
          'training-loop.log',
        )
        extraDetail.push(...tailLines(logPath, 6).map((l) => `日志│ ${l}`))
        break
      }
    }

    const passed = summarizeSmoke(items)
    const detail = [
      ...items.map((i) => `${i.passed ? '✅' : '❌'} ${i.name}${i.detail ? ` — ${i.detail}` : ''}`),
      ...extraDetail,
    ]
    return done(
      passed,
      passed ? `${COMPONENT_LABELS[key]} 冒烟通过` : `${COMPONENT_LABELS[key]} 冒烟未通过`,
      detail,
    )
  } catch (e) {
    return done(false, `冒烟失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    release(`smoke:${key}`)
  }
}

// ────────────────────────── 模式预设与开关 ──────────────────────────

/** 按 trainer 模式顺序拉起组件组合：pull = selfNode→hubServer→cloudflared→trainer；
 *  push = selfNode→hubServer→trainer；local = trainer。任一步失败即中断（已完成
 *  的组件保留，页面可单独停止）。 */
export async function startPreset(
  mode: ConsoleState['trainerPpo'],
  course: string,
): Promise<ActionResult> {
  guard(`preset:${mode}`)
  try {
    if (!course) throw new ActionError('需要 course（先在顶部设置课程）')
    validateCourseArg(course)
    saveConsoleState({ trainerPpo: mode, course })
    const order: Component[] =
      mode === 'pull'
        ? ['selfNode', 'hubServer', 'cloudflared', 'trainingLoop']
        : mode === 'push'
          ? ['selfNode', 'hubServer', 'trainingLoop']
          : ['trainingLoop']
    const ctx: StartCtx = { course, trainerPpo: mode }
    const detail: string[] = []
    for (const k of order) {
      const r = await startComponent(k, ctx)
      detail.push(`${k}: ${r.message}${r.detail && !r.ok ? ` — ${r.detail[0] ?? ''}` : ''}`)
      if (!r.ok) return done(false, `${mode} 预设启动中断于 ${k}`, detail)
    }
    return done(true, `已按 ${mode} 模式启动 ${order.length} 个组件 (course=${course})`, detail)
  } catch (e) {
    if (e instanceof ActionError) throw e
    return done(false, `预设启动失败: ${e instanceof Error ? e.message : e}`)
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

// ────────────────────────── 节点编辑（回写 rl-config.json） ──────────────────────────

/** 启用/停用 rollout 节点。 */
export async function setNodeEnabled(id: string, enabled: boolean): Promise<ActionResult> {
  guard(`node:${id}`)
  try {
    const cfg = loadConfig()
    const n = cfg.nodes.find((x) => x.id === id)
    if (!n) throw new ActionError(`节点不存在: ${id}`)
    n.enabled = enabled
    saveConfig(cfg)
    return done(true, `节点 ${id} 已${enabled ? '启用' : '停用'}（rl-config.json 已回写）`)
  } finally {
    release(`node:${id}`)
  }
}

/** 修改节点并行采集数（1-64）。 */
export async function setNodeConcurrency(id: string, concurrency: number): Promise<ActionResult> {
  guard(`node:${id}`)
  try {
    if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 64)
      throw new ActionError(`并发数需为 1-64 的整数，收到: ${concurrency}`)
    const cfg = loadConfig()
    const n = cfg.nodes.find((x) => x.id === id)
    if (!n) throw new ActionError(`节点不存在: ${id}`)
    n.concurrency = concurrency
    saveConfig(cfg)
    const smoke = rlConfigSmoke(cfg)
    return done(
      smoke.passed,
      `节点 ${id} 并行数 = ${concurrency}（rl-config.json 已回写）`,
      smoke.passed ? undefined : [smoke.detail ?? ''],
    )
  } finally {
    release(`node:${id}`)
  }
}

// ────────────────────────── 推送链路预演（原 start.ts push --smoke-only） ──────────────────────────

/** 推送链路端到端预演：本机伪 GPU 节点（remote_worker_serve echo）+ 真课程
 *  TrainingLoop（--smoke）发布真 job 并推送 → 伪节点 echo 回显 → 三重校验落位 →
 *  作废本轮干净退出（it 不前进、账本零污染，DECISIONS §340）。不跑真 PPO。
 *  完成/失败后都停掉预演用 TrainingLoop（--smoke 进程没有 echo 结果会一直等待）。 */
export async function smokeTrain(course: string): Promise<ActionResult> {
  guard('smoke:train')
  let servePid = 0
  try {
    if (!course) throw new ActionError('需要 course（先在顶部设置课程）')
    validateCourseArg(course)
    const cfg = loadConfig()
    const reg = loadRegistry()
    if (pidAlive(reg.trainingLoop?.pid))
      return done(false, 'TrainingLoop 已在运行（可能是真训练）——预演会干扰在途 job，先停止')
    const venv = resolveVenvPython()
    const trajDir = path.join(REPO_ROOT, 'tmp', course)
    const weightsPath = path.join(trajDir, 'weights.json')
    if (!existsSync(weightsPath)) {
      const bcPath = resolveCourseBc(course)
      mkdirSync(trajDir, { recursive: true })
      if (!existsSync(bcPath)) return done(false, `初始权重缺失且 BC 产物不存在: ${bcPath}`)
      copyFileSync(bcPath, weightsPath)
    }

    // 1) 本机伪 GPU 节点
    const { pushUrl, servePid: pid } = await startLocalWorkerServer({
      course,
      cfgToken: cfg.rl.remote_token,
      hubPort: cfg.rl.hub_port,
      venv,
    })
    servePid = pid

    // 2) 真课程 TrainingLoop --smoke（REMOTE_PUSH_NODE 注入伪节点）
    const trainLog = path.join(LOG_DIR, course, 'training-loop.log')
    const spec = trainingLoopSpec(cfg, {
      course,
      ppo: 'remote',
      smoke: true,
      pushNodeUrl: pushUrl,
      venv,
    })
    const r = launchSpec(spec)
    saveComponent('trainingLoop', {
      pid: r.pid,
      course,
      entry: TRAINING_LOOP_ENTRY,
      mode: 'remote',
    })
    monitorTouch()

    // 3) 预演等三段日志触发（发布 → 落位 → 作废退出），任何一段失败都停预演进程
    try {
      await stepKaggleRehearsal(course, r.pid)
    } catch (e) {
      if (pidAlive(r.pid)) {
        await killPid(r.pid)
        clearComponent('trainingLoop')
      }
      return done(
        false,
        `推送链路预演未通过: ${e instanceof Error ? e.message : e}`,
        tailLines(trainLog, 6),
      )
    }
    return done(
      true,
      '推送链路预演全通过（发布→推送→echo→落位→作废；真训练零污染）',
      tailLines(trainLog, 4),
    )
  } catch (e) {
    return done(false, `预演失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    if (servePid) {
      const { killPid } = await import('../net')
      await killPid(servePid)
    }
    release('smoke:train')
  }
}

// ────────────────────────── 变更检测重启（监督器回调） ──────────────────────────

/** 按账本元数据 + 当前 rl-config 重建组件 spec（监督器 restartProc 的数据源）。
 *  返回 null = 该组件没有可重建的 spec（未登记或缺元数据）。 */
export function restartSpecFor(key: Component): ProcSpec | null {
  const entry = loadRegistry()[key]
  if (!entry) return null
  const cfg = loadConfig()
  const venv = resolveVenvPython()
  const state = loadConsoleState()
  const course = entry.course || state.course
  switch (key) {
    case 'selfNode':
      return selfNodeSpec(cfg)
    case 'hubServer':
      return hubServerSpec(cfg, course)
    case 'cloudflared':
      return cloudflaredSpec(cfg, entry)
    case 'workerServe':
      return workerServeSpec(cfg, venv)
    case 'trainingLoop':
      return trainingLoopSpec(cfg, {
        course,
        ppo: entry.mode,
        venv,
      })
  }
}
