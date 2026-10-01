/**
 * single-instance.ts — 「一个 node id 只准有一个 agent 在听」的应用层互斥（纯函数）。
 *
 * 背景（plan/sampler-single-instance.plan.md，2026-09-29 h3-geo/x20-geo it1 云端 grad 5 连炸）：
 * Linux 上 `serveWithRetry` 为重启链开了 `SO_REUSEPORT`（避免 TIME_WAIT 期间 EADDRINUSE），
 * 副作用是**第二个实例绑同一端口也静默成功**——内核按 4 元组哈希把连接分给两个进程，于是一台
 * 节点上两个代码版本同时服务：`/v1/restart` 可能重启错那个实例、`codeHash` 门变成抽签（旧版本
 * shard 进 payload ⇒ 云 worker 一算就炸）。EADDRINUSE 这条「第二实例被拒」的护栏在 Linux 上
 * 被静默拆掉了，本模块把它在**应用层**补回来，而**不**关掉 `reusePort`。
 *
 * 与 restart-guard.ts / workdir-cleanup.ts 同规：**纯判定、无 IO**（不碰 fs / 网络 / 进程表），
 * IO（读锁文件、`process.kill(pid,0)`、HTTP 探活、`exit`）全留在 sampler-agent，判定与 IO 分离
 * 才可表驱动单测。
 *
 * 判据优先级（§3.1）：**HTTP 探活（probe）优先于 pid 锁文件**。理由：锁文件只在交接路径写、
 * pid 会被系统复用，而「真的有人在服务这个端口」的自我证明（`/v1/status` 的 pid + bootId）最可靠。
 */

/** 锁文件名（住 `<WORK_DIR>` = `tmp/dist-agent/`）。 */
export const LOCK_NAME = 'agent.lock'
/** 起听前的本机探活超时（ms）——只在**起服**路径付这一次，不是每请求。 */
export const PROBE_TIMEOUT_MS = 1000
/** 探活路径：`/v1/status` 已报 pid/bootId/codeHash，且与 ping 同鉴权。 */
export const PROBE_PATH = '/v1/status'
/** 交接标记环境变量：**唯一**由 `/v1/restart` 的 spawn 处注入（禁命令行 flag——会被复制粘贴继承）。 */
export const HANDOFF_ENV = 'SAMPLER_HANDOFF'
/** 交接父子核对通道：父进程把自己的 bootId 经此传给 child（§3.2，ppid 会因父进程先退而失效）。 */
export const HANDOFF_BOOTID_ENV = 'SAMPLER_HANDOFF_BOOTID'
/** 拒起的退出码（**非 0**，让 supervisor / 运维看得见）。 */
export const REFUSE_EXIT_CODE = 2

/** 本次起听的判定结论（同时也是 `/v1/status` 的 `instanceGuard` 取值）。 */
export type GuardAction = 'serve' | 'serve-handoff' | 'serve-took-over-stale' | 'refuse'
export type InstanceGuard = 'fresh' | 'handoff' | 'took-over-stale'

/** `<WORK_DIR>/agent.lock` 的内容（pid + pidAlive + isSelf 由调用方从文件/进程表推出）。 */
export interface LockInfo {
  pid: number
  /** `process.kill(pid, 0)`：false = 进程已死（陈旧残留 ⇒ 可自愈接管）。 */
  pidAlive: boolean
  /** 锁里写的 pid 就是本进程（同 pid 上一代；PID 复用）。 */
  isSelf: boolean
  /** 锁文件里的 bootId（交接父子核对用；旧锁 / 手写锁可能没有）。 */
  bootId?: string
}

/** 本机 `127.0.0.1:<port>/v1/status` 的探活结果；`pid === null` = 探不到（超时 / 无监听 / 鉴权失败）。 */
export interface ProbeInfo {
  pid: number | null
  codeHash: string
  bootId: string
  uptimeSec?: number
}

export interface GuardInput {
  /** 本进程 pid（`process.pid`）。 */
  selfPid: number
  lock: LockInfo | null
  probe: ProbeInfo
  /** 本次是 `/v1/restart` 拉起的 child（`process.env[HANDOFF_ENV] === '1'`）。 */
  handoff: boolean
  /** 显式 `--takeover`（运维兜底；唯一允许无交接标记就接管的入口）。 */
  takeover: boolean
  /** 交接时的父进程 pid（`process.ppid`）；非交接为 null。 */
  handoffParentPid?: number | null
  /** 交接时父写的 bootId（`process.env[HANDOFF_BOOTID_ENV]`）；非交接为 null/空。 */
  handoffBootId?: string | null
}

export interface GuardVerdict {
  action: GuardAction
  /** 人读一行；refuse 时含既有实例的 pid/codeHash/uptime 与「怎么停它」。 */
  reason: string
}

/** 探不到任何实例时的 `ProbeInfo`（IO 侧 fetch 失败/非 200/解析失败都映射到它）。 */
export function emptyProbe(): ProbeInfo {
  return { pid: null, codeHash: '', bootId: '' }
}

/**
 * `/v1/status` JSON → `ProbeInfo`（纯映射；IO 侧只管 fetch + JSON.parse）。
 *
 * 兼容旧 agent（本次改动尚未升级的节点）：老 `/v1/status` **没有** `pid` 字段，只有
 * `nodeId: "bun-<pid>"` ⇒ 从 nodeId 回推 pid。没有这一步，跨版本交接时会「探到了却当成探不到」。
 */
export function probeFromStatusJson(raw: unknown): ProbeInfo {
  if (raw === null || typeof raw !== 'object') return emptyProbe()
  const j = raw as Record<string, unknown>
  let pid: number | null = typeof j.pid === 'number' && Number.isInteger(j.pid) ? j.pid : null
  if (pid === null && typeof j.nodeId === 'string') {
    const m = /^bun-(\d+)$/.exec(j.nodeId)
    if (m) pid = parseInt(m[1], 10)
  }
  return {
    pid,
    codeHash: typeof j.codeHash === 'string' ? j.codeHash : '',
    bootId: typeof j.bootId === 'string' ? j.bootId : '',
    ...(typeof j.uptimeSec === 'number' ? { uptimeSec: j.uptimeSec } : {}),
  }
}

/** 判定结论 → 观测字段（§3.4：refuse 不会起听，故状态里只会出现这三个）。 */
export function instanceGuardOf(action: GuardAction): InstanceGuard {
  if (action === 'serve-handoff') return 'handoff'
  if (action === 'serve-took-over-stale') return 'took-over-stale'
  return 'fresh'
}

function describeHolder(pid: number, codeHash: string, bootId: string, uptimeSec?: number): string {
  const parts = [`pid=${pid}`]
  if (codeHash) parts.push(`codeHash=${codeHash.slice(0, 8)}…`)
  if (bootId) parts.push(`bootId=${bootId}`)
  if (typeof uptimeSec === 'number') parts.push(`uptime≈${uptimeSec}s`)
  return parts.join(' ')
}

/** 停掉既有实例的指路（拒起日志必须让人知道下一步做什么）。 */
function stopHint(pid: number): string {
  return `停止它：kill ${pid}（或控制台停 self-node）后重启本实例；确认要强接管请用 --takeover`
}

/**
 * 交接父子核对（§3.2）：child 只准接管**自己的父进程**。`ppid` 在父进程退出后会被 reinit
 * 收走（child 起听前还要预热，父进程早已 exit）⇒ 认 pid **或** bootId 任一即算确认。
 */
function confirmsHandoff(inp: GuardInput, holderPid: number | null, holderBootId: string): boolean {
  if (!inp.handoff) return false
  if (inp.handoffParentPid != null && holderPid !== null && holderPid === inp.handoffParentPid)
    return true
  if (inp.handoffBootId && holderBootId && holderBootId === inp.handoffBootId) return true
  return false
}

/**
 * 起听前的互斥判定（判定表按序、第一命中即返回；§3.1）：
 *
 *  1. `--takeover`                          ⇒ serve（显式接管，随后覆盖锁）
 *  2. 探活命中且不是自己                     ⇒ handoff 且父子核对通过 ? serve-handoff : refuse
 *  3. 探活落空但锁在活进程手上（非自己）      ⇒ 同上二分（保守；正常交接不会走到这里，父进程先删锁）
 *  4. 锁陈旧（owner 已死 / 是自己上一代）     ⇒ serve-took-over-stale（自愈，不要求人工清盘）
 *  5. 其余（无锁、也探不到）                 ⇒ serve（fresh）
 */
export function decideSingleInstance(inp: GuardInput): GuardVerdict {
  const { lock, probe } = inp

  // 1. 显式接管：唯一不需要交接标记的接管入口（§1.4-2）。
  if (inp.takeover) {
    return { action: 'serve', reason: 'explicit takeover (--takeover)' }
  }

  // 2. 有实例真的在服务这个端口（最可靠判据）。
  if (probe.pid !== null && probe.pid !== inp.selfPid) {
    if (confirmsHandoff(inp, probe.pid, probe.bootId)) {
      return { action: 'serve-handoff', reason: `handoff from parent pid=${probe.pid}` }
    }
    return {
      action: 'refuse',
      reason:
        `another instance alive: ${describeHolder(probe.pid, probe.codeHash, probe.bootId, probe.uptimeSec)}` +
        ` — ${stopHint(probe.pid)}`,
    }
  }

  // 3. 探活落空但锁在活进程手上 ⇒ 保守（宁可要人来看，也不要两个实例）。
  if (lock !== null && lock.pidAlive && !lock.isSelf) {
    if (confirmsHandoff(inp, lock.pid, lock.bootId ?? '')) {
      return {
        action: 'serve-handoff',
        reason: `handoff from parent pid=${lock.pid} (port probe miss)`,
      }
    }
    return {
      action: 'refuse',
      reason:
        `lock held by live pid=${lock.pid} but not serving? (port probe miss)` +
        ` — ${stopHint(lock.pid)}`,
    }
  }

  // 4. 陈旧残留（进程已死 / 同 pid 上一代）⇒ 自愈接管，不阻塞起服。
  if (lock !== null && (!lock.pidAlive || lock.isSelf)) {
    return {
      action: 'serve-took-over-stale',
      reason: lock.isSelf
        ? `stale lock (pid=${lock.pid} = self, previous generation)`
        : `stale lock (pid=${lock.pid} not alive)`,
    }
  }

  // 5. 干净冷启动。
  return { action: 'serve', reason: 'fresh' }
}
