/**
 * dist-node-gate.ts — 节点门 + 可读告警的**共享**实现（rollout/eval 同源口径）。
 *
 * 为什么要共享：`eval-course-ckpt.ts` 与 `m1-eval.ts` 都要「ping → 判能力位/bun/
 * codeHash → 排除不合格节点」，此前一个各写一份（m1-eval 连 codeHash 门都**没有**
 * ——2026-09-19 核实：`tryActivate` 只看 HTTP 200，会把陈旧节点当作可用算力，
 * 让不同 era 的结果混进同一份读数）。门口径本身与 Python 侧
 * `dist_common.check_code_hash` 同源（同一份 `tools/agent/codehash-files.txt`）。
 *
 * 「响亮告警」的目标：判读时**一眼看出**远端算力是否真的参与了，以及为什么没参与
 * ——`--dist-local 0` 也挡不住的全灭回落本地（`hybrid failed … falling back to local`）
 * 必须在这批产物里说明白。
 */

import { readFileSync } from 'node:fs'

export interface DistNodeCfg {
  id: string
  url: string
  authKey?: string
  concurrency?: number
  enabled?: boolean
}

export interface DistPing {
  codeHash?: string
  bunVersion?: string
  evalSupport?: boolean
  stageJsonSupport?: boolean
  cpus?: number
}

export type GateReason =
  | 'ping failed'
  | 'lacks evalSupport'
  | 'lacks stageJsonSupport'
  | 'bun version mismatch'
  | `codeHash mismatch`
  | null

export interface NodeGateEntry {
  id: string
  /** 门判定通过（可参与分派）。 */
  ok: boolean
  /** 未通过的原因；ok=true 时为 null。 */
  reason: string | null
  /** ping 报的 codeHash（不可达为空串）——升级指令与告警都要用。 */
  pingHash: string
}

export interface PingOpts {
  /** 单次 ping 超时（默认 10s）。节点被别的作业占满时 /v1/ping 会很慢（用户 2026-09-19：
   *  「所有 enabled 节点都在线，只是可能 ping 得慢」）——超时太短会把健康节点判成不可达。 */
  timeoutMs?: number
  /** 尝试次数（默认 2）：一次慢响应 ≠ 节点不在。 */
  attempts?: number
  /** 重试间隔（默认 500ms）。 */
  gapMs?: number
}

/**
 * ping 一个节点（带重试）。返回 null = **多次**尝试后仍拿不到 200。
 *
 * 为什么要重试 + 可调超时：单次探测的结论会被用来决定「本轮是否用这台算力」，
 * 而慢响应（占满、隧道抖动）与真的下线在单次超时上不可区分；判错一次就白白
 * 浪费整轮批的节点容量（配合 local_slots=0 时更是直接决定成败）。
 */
export async function pingNode(
  url: string,
  authKey = '',
  opts: PingOpts = {},
): Promise<DistPing | null> {
  const timeoutMs = opts.timeoutMs ?? 10_000
  const attempts = Math.max(1, opts.attempts ?? 2)
  const gapMs = opts.gapMs ?? 500
  for (let a = 0; a < attempts; a++) {
    try {
      const r = await fetch(`${url.replace(/\/$/, '')}/v1/ping`, {
        headers: { Authorization: `Bearer ${authKey}` },
        signal: AbortSignal.timeout(timeoutMs),
      })
      if (r.status === 200) return (await r.json()) as DistPing
    } catch {
      /* 超时/网络：退避后重试 */
    }
    if (a + 1 < attempts) await new Promise((r) => setTimeout(r, gapMs))
  }
  return null
}

/** 失败触发的再探测闸门（纯函数）：冷却窗口内不重复探（一批任务成批失败时只探一次）。 */
export function reprobeDue(lastMs: number | undefined, nowMs: number, cooldownMs: number): boolean {
  return lastMs === undefined || nowMs - lastMs >= cooldownMs
}

/** bun 版本 major.minor（节点门，与 Python `mm()` 同式）。 */
export function bunMajorMinor(v: string): string {
  return String(v).split('.').slice(0, 2).join('.')
}

/** 节点门：返回拒绝原因或 null（与 `dist_common.check_code_hash` + 能力位同源）。 */
export function nodeGateReason(
  ping: DistPing | null,
  localBunMM: string,
  localCodeHash: string,
): string | null {
  if (!ping) return 'ping failed'
  if (!ping.evalSupport) return 'lacks evalSupport'
  if (!ping.stageJsonSupport) return 'lacks stageJsonSupport'
  if (bunMajorMinor(ping.bunVersion ?? '?') !== localBunMM) return 'bun version mismatch'
  if (localCodeHash && ping.codeHash && ping.codeHash !== localCodeHash)
    return `codeHash mismatch (node ${String(ping.codeHash).slice(0, 12)}… local ${localCodeHash.slice(0, 12)}…)`
  return null
}

export interface GateSummary {
  total: number
  usable: string[]
  /** codeHash 不符（可升级对象）→ 节点 id 列表。 */
  stale: string[]
  unreachable: string[]
  /** 其他能力位/bun 原因 → [id, reason]。 */
  other: Array<[string, string]>
}

export function classifyGate(entries: NodeGateEntry[]): GateSummary {
  const out: GateSummary = {
    total: entries.length,
    usable: [],
    stale: [],
    unreachable: [],
    other: [],
  }
  for (const e of entries) {
    if (e.ok) out.usable.push(e.id)
    else if (e.reason === 'ping failed') out.unreachable.push(e.id)
    else if (e.reason?.startsWith('codeHash mismatch')) out.stale.push(e.id)
    else out.other.push([e.id, String(e.reason)])
  }
  return out
}

/**
 * dist 配置里的「本机槽位」约定。`slots=null` = 配置未约定（调用方用自己的兜底）。
 *
 * 为什么必须读它：`rl.local_slots: 0` / `policy.evalLocalSlots: 0` 是**机器口径**
 * （本机不参与，全交集群）。一次性评估工具此前缺省 `--dist-local` = 物理核数，
 * 把「本机不参与」静默变成「本机跑近一半」（2026-09-19 实测：配置 local_slots=0，
 * 1600 局的批本地跑了 730 局，5 台节点只分到 870）。取值优先级与 Python 评测栈
 * 同序：`policy.evalLocalSlots`（评测专用旋钮，`rl/eval_local.py`）→ `rl.local_slots`
 * （机器级，`dashboard/src/core/slots.ts` 同源）。
 */
export interface LocalSlotsFromConfig {
  slots: number | null
  /** 生效键名（日志用）：`policy.evalLocalSlots` / `rl.local_slots` / 空。 */
  source: string
}

export function configLocalSlots(cfgPath: string): LocalSlotsFromConfig {
  const pick = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.floor(v) : null
  try {
    const raw = JSON.parse(readFileSync(cfgPath, 'utf8')) as {
      policy?: { evalLocalSlots?: unknown }
      rl?: { local_slots?: unknown }
    }
    const evalSlots = pick(raw.policy?.evalLocalSlots)
    if (evalSlots !== null) return { slots: evalSlots, source: 'policy.evalLocalSlots' }
    const rlSlots = pick(raw.rl?.local_slots)
    if (rlSlots !== null) return { slots: rlSlots, source: 'rl.local_slots' }
    return { slots: null, source: '' }
  } catch {
    return { slots: null, source: '' }
  }
}

export interface GateWarnOpts {
  /** 本机可用于兜底的本地 worker 槽数（0 = 无兜底）。 */
  localCap: number
  /** 远端升级指令是否已下发（`--upgrade-nodes`）——影响处置建议文案。 */
  upgradeRequested?: boolean
  /** 提示里可加的工具名（如 `tools/agent/codehash-report.ts`）。 */
  reportHint?: boolean
}

/**
 * 节点门告警块（响亮；无异常时返回空数组——常态不打噪声）。
 * 触发条件：有节点被排除（stale / 不可达 / 能力位不符）。
 */
export function gateWarning(
  prefix: string,
  entries: NodeGateEntry[],
  localCodeHash: string,
  opts: GateWarnOpts,
): string[] {
  const s = classifyGate(entries)
  if (s.total === 0 || (s.stale.length === 0 && s.unreachable.length === 0 && s.other.length === 0))
    return []
  const lines: string[] = []
  const parts = [`${s.usable.length}/${s.total} usable (${s.usable.join(',') || 'none'})`]
  if (s.stale.length) parts.push(`${s.stale.length} stale (${s.stale.join(',')})`)
  if (s.unreachable.length)
    parts.push(`${s.unreachable.length} unreachable (${s.unreachable.join(',')})`)
  if (s.other.length)
    parts.push(`${s.other.length} unusable (${s.other.map(([id]) => id).join(',')})`)
  lines.push(`${prefix} WARN dist nodes: ${parts.join(' · ')}`)
  for (const [id, reason] of s.other) lines.push(`${prefix}      ${id}: ${reason}`)
  if (s.stale.length) {
    const remote = entries.find((e) => s.stale.includes(e.id))?.pingHash ?? ''
    lines.push(
      `${prefix}      stale cause: codeHash node ${remote.slice(0, 12) || '?'}… ≠ local ${localCodeHash.slice(0, 12)}…` +
        ` (节点代码未同步/未重启——${opts.reportHint === false ? '用 codehash-report 双侧 diff 定位' : 'bun tools/agent/codehash-report.ts 与本机 diff'}）`,
    )
  }
  const remoteZero = s.usable.length === 0
  if (remoteZero) {
    lines.push(
      opts.localCap > 0
        ? `${prefix}      ⇒ 远端可用算力 0 —— 本次全部局将由**本地 worker** 完成（产物看不出这点，注意判读口径）`
        : `${prefix}      ⇒ 远端可用算力 0 且本地槽 0 —— 本次无算力可用（将直接失败）`,
    )
    lines.push(
      opts.upgradeRequested
        ? `${prefix}      ⇒ 已按训练循环同规守卫下发升级（见下方逐节点结果）；节点重启后重跑即可用远端`
        : `${prefix}      ⇒ 处置：显式加 --upgrade-nodes 下发 pull+restart（训练循环同规守卫），或先把节点同步到本地 codeHash`,
    )
  }
  return lines
}

/**
 * 「每个消费者实际分到多少任务」注脚（与 provenance 配对读）。
 *
 * provenance 记的是**结算**来源，claims 记的是**分派**来源：两者不一致（如 a95 有 claims
 * 但 provenance 里没有）说明该节点的请求全数失败，局被别处补跑或整批失败——只看 provenance
 * 会把「没分到活」与「干完活」混为一谈（2026-09-19 核实时遇到的判读歧义）。
 */
export function claimNote(prefix: string, byClaim: Record<string, number>): string[] {
  const keys = Object.keys(byClaim).sort()
  if (keys.length === 0) return []
  const total = keys.reduce((n, k) => n + byClaim[k], 0)
  return [
    `${prefix} claims: ${keys.map((k) => `${k}=${byClaim[k]}`).join(', ')}（共 ${total} 次分派）`,
  ]
}

export interface ProvenanceOpts {
  /** 本机 worker 槽数（`--dist-local`）——用于区分「本地份额」与「失败兜底」。 */
  localCap?: number
  /** 过门的节点数（>0 说明远端本来可用 ⇒ 「全本地」才是异常）。 */
  usableNodes?: number
}

/**
 * 「这批局是谁跑的」注脚。bySrc 形如 `{ 'node:self': 12, local: 4 }`。
 *
 * 口径（2026-09-19 修正）：本地 >0 **不等于**出事——`--dist-local N` 本就是显式的本地份额
 * （缺省 = 物理核数），15 个快本地槽与 41 个高延迟远端槽同台竞速时本地本来就会多吃；
 * 上一版把所有 local>0 都写成「节点部分失败或本地兜底」，把健康跑批误报成故障
 * （实测：1600 局的批 728 本地 / 872 远端，被读成「没分派到集群」）。
 * 现在只对**远端零参与**喊 WARN，混跑只报份额，并点明它由 `--dist-local` 决定。
 */
export function provenanceNote(
  prefix: string,
  bySrc: Record<string, number>,
  opts: ProvenanceOpts = {},
): string[] {
  const keys = Object.keys(bySrc).sort()
  if (keys.length === 0) return []
  const local = keys.filter((k) => k === 'local').reduce((n, k) => n + bySrc[k], 0)
  const total = keys.reduce((n, k) => n + bySrc[k], 0)
  const remote = total - local
  const detail = keys.map((k) => `${k}=${bySrc[k]}`).join(', ')
  const lines = [
    `${prefix} provenance: ${detail}（共 ${total} 局：远端 ${remote} / 本地 ${local}）`,
  ]
  if (remote === 0) {
    const why =
      (opts.usableNodes ?? 0) > 0
        ? `，但本轮有 ${opts.usableNodes} 个节点过门——节点可能被别的任务占满 / 请求全超时，看 claims 区分「没分到活」与「分到但没干完」`
        : '（无可用节点）'
    lines.push(
      `${prefix} WARN provenance: ${local}/${total} 局全部由**本地 worker** 跑（远端零参与${why}）——不要当作分布式读数`,
    )
  } else if (local > 0) {
    lines.push(
      `${prefix} provenance: 本地 ${local}/${total} 局是 **--dist-local ${opts.localCap ?? '?'} 的份额**（不是失败兜底；想全走远端用 --dist-local 0），远端 ${remote}/${total} 局`,
    )
  } else {
    lines.push(`${prefix} provenance: 全部 ${total} 局由远端节点完成（本地 0）`)
  }
  return lines
}
