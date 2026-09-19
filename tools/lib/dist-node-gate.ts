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

/**
 * 「这批局是谁跑的」注脚（本地回落必须显式说出来）。
 * bySrc 形如 `{ 'node:self': 12, local: 4 }`。
 */
export function provenanceNote(prefix: string, bySrc: Record<string, number>): string[] {
  const keys = Object.keys(bySrc).sort()
  if (keys.length === 0) return []
  const local = keys.filter((k) => k === 'local').reduce((n, k) => n + bySrc[k], 0)
  const total = keys.reduce((n, k) => n + bySrc[k], 0)
  const detail = keys.map((k) => `${k}=${bySrc[k]}`).join(', ')
  const lines = [`${prefix} provenance: ${detail}（共 ${total} 局）`]
  if (local > 0)
    lines.push(
      local === total
        ? `${prefix} WARN provenance: ${local}/${total} 局由**本地 worker** 跑（远端未参与）——不要当作分布式读数`
        : `${prefix} WARN provenance: ${local}/${total} 局由本地 worker 跑（节点部分失败或本地兜底）——混跑读数须按来源拆开看`,
    )
  return lines
}
