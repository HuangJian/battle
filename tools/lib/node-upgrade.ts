/**
 * node-upgrade.ts — 节点升级指令的 TS 客户端（**复用训练循环的守卫与探测**）。
 *
 * 训练循环（`nn-training/rl/dispatch.py` ping 门）发现节点 codeHash stale 时会
 * `dist_common.upgrade_stale_nodes(...)`：逐节点 ping → codeHash ≠ 期望 →
 * `request_upgrade_guarded(...)`（POST `/v1/restart {pullBranch}`），三重护栏 =
 * 脏工作区拒发（字节级判据，**不是** `git status`——autocrlf 会藏 CRLF 污染，
 * 2026-09-09 mac 事故）/ 跨代去重 (agent codeHash, 期望 hash) / self 节点纯重启禁 pull。
 *
 * 本模块**不重写任何一条上述逻辑**：把 cfg 路径交给
 * `nn-training/dist_upgrade_cli.py`（经仓库规定的 `bash tools/githook/nn-py-safe.sh`
 * 启动），由它自己 ping、自己判 stale、自己走守卫。TS 侧只负责：拼 spec、读 JSON、
 * 打日志/告警、持久化跨调用 memo。
 *
 * 已知边界：子进程是一次性的 ⇒ 守卫内部的跨代去重只在单次调用内生效；跨调用由本模块的
 * memo 文件兜底（key = nid + agent ping hash + 期望 hash，**语义与 dist_common._RESTART_SEEN
 * 一致**，落 `tmp/node-upgrade-memo.json`，可用 NN_UPGRADE_MEMO 覆盖）：调用前按节点取
 * memo 里最新的一条经 spec.`seen` 预置回子进程，判据（含**去重冷却窗**）仍只有一处实现。
 *
 * F3（2026-09-19）：去重不是永久的——memo 值就是该次下发的 ISO 时刻，本模块把它换算成
 * `atSec`（epoch 秒）随 `seen` 传给 Python，由 `dist_common.request_upgrade_guarded` 按
 * `RESTART_DEDUP_COOLDOWN_SEC`（缺省 600s）判定「还在窗内 ⇒ dedup」/「窗已过 ⇒ 再发一次」。
 * 旧行为下，pull 失败（或节点环境不支持远端升级）的节点会带着同一个 codeHash 回来、
 * memo 键永久命中 ⇒ 该节点再也收不到升级指令且跨调用持续被压制。
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import path from 'path'

export interface UpgradeSpecInput {
  /** 期望 codeHash；缺省由 Python 侧 `dist_common.compute_code_hash()` 现算。 */
  expectedHash?: string
  branch: string
  /** 节点配置（`rl-config.json` 或 `--dist-nodes` 指定的文件）。 */
  cfgPath: string
  /** 跨调用去重 memo（预置回 `dist_common._RESTART_SEEN`；`atSec` = 该次下发时刻）。 */
  seen?: Array<{ id: string; pingHash: string; expectedHash: string; atSec?: number }>
  /** null/缺省 ⇒ 子进程用 `dist_common.dirty_hash_files()` 字节级探测。 */
  dirty?: string[] | null
  dryRun?: boolean
  /** 重启请求超时（秒）。 */
  timeout?: number
  /** 节点 ping 超时（秒）。 */
  statusTimeout?: number
}

export interface UpgradeNodeResult {
  id: string
  ok: boolean
  reason: string
  /** 本次 ping 到的 agent codeHash（写 memo 用；unreachable 时为空）。 */
  pingHash: string
}

export interface UpgradeOutcome {
  ok: boolean
  dirty: string[]
  results: UpgradeNodeResult[]
  /** 子进程/解析失败时的原因（此时 results 为空，调用方须响亮告警而非静默）。 */
  error?: string
}

export interface UpgradeDeps {
  /** 注入点（单测用）：argv + stdin → 进程结果。 */
  spawn?: (
    argv: string[],
    input: string,
    cwd: string,
  ) => { exitCode: number | null; stdout: string; stderr: string }
  env?: Record<string, string | undefined>
  /** memo 文件路径覆盖（缺省 = <repoRoot>/tmp/node-upgrade-memo.json）。 */
  memoPath?: string
  /** 跳过 memo 读写（单测/强制重发）。 */
  noMemo?: boolean
}

/** 仓库规定的沙箱免疫 python 启动器（**不要**裸起 python）。 */
export const NN_PY_SAFE = 'tools/githook/nn-py-safe.sh'
export const UPGRADE_CLI = 'nn-training/dist_upgrade_cli.py'

export function buildUpgradeSpec(input: UpgradeSpecInput): Record<string, unknown> {
  const spec: Record<string, unknown> = {
    cfg_path: input.cfgPath,
    branch: input.branch,
    dry_run: input.dryRun === true,
    timeout: input.timeout ?? 20,
  }
  if (input.expectedHash) spec.expected_hash = input.expectedHash
  if (input.seen && input.seen.length > 0) spec.seen = input.seen
  if (input.statusTimeout !== undefined) spec.status_timeout = input.statusTimeout
  if (input.dirty !== undefined) spec.dirty = input.dirty
  return spec
}

/** 解析子进程 stdout（单行 JSON）；结构不对 → 抛（调用方转成 error 并响亮告警）。 */
export function parseUpgradeOutput(stdout: string): {
  dirty: string[]
  results: UpgradeNodeResult[]
} {
  const lines = stdout
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
  const last = lines[lines.length - 1]
  if (!last) throw new Error('empty stdout')
  const doc = JSON.parse(last) as { error?: string; dirty?: unknown; results?: unknown }
  if (doc.error) throw new Error(String(doc.error))
  if (!Array.isArray(doc.results)) throw new Error('results 缺失')
  return {
    dirty: Array.isArray(doc.dirty)
      ? doc.dirty.filter((x): x is string => typeof x === 'string')
      : [],
    results: doc.results.map((r) => {
      const o = r as { id?: unknown; ok?: unknown; reason?: unknown; pingHash?: unknown }
      return {
        id: String(o.id ?? '?'),
        ok: o.ok === true,
        reason: String(o.reason ?? ''),
        pingHash: typeof o.pingHash === 'string' ? o.pingHash : '',
      }
    }),
  }
}

/** memo 键（与 dist_common._RESTART_SEEN 的 (agent ping hash, 期望 hash) 同语义，全量 hex）。 */
export function memoKey(nid: string, pingHash: string, expectedHash: string): string {
  return `${nid}|${pingHash}|${expectedHash}`
}

/** memo 键 → {id, pingHash, expectedHash}（键是拼出来的，解析回来喂 `seen`）。 */
export function parseMemoKey(key: string): { id: string; pingHash: string; expectedHash: string } {
  const [id = '', pingHash = '', expectedHash = ''] = key.split('|')
  return { id, pingHash, expectedHash }
}

export interface UpgradeMemo {
  [key: string]: string
}

export function loadMemo(memoPath: string): UpgradeMemo {
  try {
    const doc = JSON.parse(readFileSync(memoPath, 'utf-8')) as UpgradeMemo
    return doc && typeof doc === 'object' ? doc : {}
  } catch {
    return {}
  }
}

export function saveMemo(memoPath: string, memo: UpgradeMemo): void {
  try {
    mkdirSync(path.dirname(memoPath), { recursive: true })
    writeFileSync(memoPath, JSON.stringify(memo, null, 1), 'utf-8')
  } catch {
    /* best effort：memo 只是防重复打扰，写不进去不影响本次升级 */
  }
}

/**
 * memo → 每节点**最新**一条 `seen` 条目。与常驻循环同构：`_RESTART_SEEN[nid]` 只留
 * 最近一次（重启后 agent hash 变化 ⇒ 键不匹配 ⇒ 允许再发，正是 F1 语义）。
 */
export function latestSeenEntries(
  memo: UpgradeMemo,
): Array<{ id: string; pingHash: string; expectedHash: string; atSec: number }> {
  const latest = new Map<
    string,
    {
      at: string
      entry: { id: string; pingHash: string; expectedHash: string; atSec: number }
    }
  >()
  for (const [key, at] of Object.entries(memo)) {
    const e = parseMemoKey(key)
    if (!e.id || !e.pingHash || !e.expectedHash) continue
    const prev = latest.get(e.id)
    if (!prev || String(at) > prev.at)
      latest.set(e.id, { at: String(at), entry: { ...e, atSec: memoAtSec(at) } })
  }
  return [...latest.values()].map((v) => v.entry)
}

/**
 * memo 值（该次下发的时刻）→ epoch 秒。认 ISO 8601（本模块写入的形态）与纯数字两种；
 * 解不出来时回 **0**（= 最旧）——由 Python 侧的冷却窗判它「已过期 ⇒ 允许再发一次」。
 * 这里**不做去重判断**（判据只在 dist_common，单一实现），只负责把时刻送达。
 */
export function memoAtSec(at: unknown): number {
  const s = String(at ?? '').trim()
  if (!s) return 0
  // 纯数字（可带符号）= epoch 秒。必须**先**判数字：`Date.parse('1758300000')` 在
  // 宽松解析下会给出一个与输入毫无关系的日期（实测 2001-05-01），静默把新鲜 memo
  // 变成「一小时前」。
  if (/^[+-]?\d+$/.test(s)) {
    const n = Number(s)
    return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
  }
  const iso = Date.parse(s)
  return Number.isFinite(iso) && iso > 0 ? Math.floor(iso / 1000) : 0
}

/** 期望分支：`UPGRADE_BRANCH` 优先（与训练循环锁存语义一致），否则 git 当前分支。 */
export function resolveUpgradeBranch(repoRoot: string, deps: UpgradeDeps = {}): string {
  const env = deps.env ?? (process.env as Record<string, string | undefined>)
  if (env.UPGRADE_BRANCH) return env.UPGRADE_BRANCH
  const run =
    deps.spawn ??
    ((argv: string[], input: string, cwd: string) => {
      const p = Bun.spawnSync({ cmd: argv, cwd, stdin: Buffer.from(input) })
      return {
        exitCode: p.exitCode,
        stdout: p.stdout?.toString() ?? '',
        stderr: p.stderr?.toString() ?? '',
      }
    })
  try {
    const r = run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], '', repoRoot)
    const b = r.stdout.trim()
    return r.exitCode === 0 && b && b !== 'HEAD' ? b : ''
  } catch {
    return ''
  }
}

/** venv python 候选（仅用于错误提示：真正的解析在 nn-py-safe.sh 里）。 */
export function pythonCandidates(repoRoot: string): string[] {
  return [
    path.join(repoRoot, 'nn-training', '.venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, 'nn-training', '.venv', 'bin', 'python'),
  ]
}

export interface RequestUpgradesOpts extends UpgradeSpecInput {
  repoRoot: string
}

/**
 * 扫描节点并下发升级指令（探测/判 stale/护栏全在 Python 侧）。**永不抛**：任何失败都以
 * `{ok:false, error}` 返回，由调用方响亮告警（静默吞掉会退化成「以为发出去了、其实没有」）。
 */
export function requestNodeUpgrades(
  opts: RequestUpgradesOpts,
  deps: UpgradeDeps = {},
): UpgradeOutcome {
  const env = deps.env ?? (process.env as Record<string, string | undefined>)
  const spawn =
    deps.spawn ??
    ((argv: string[], input: string, cwd: string) => {
      const p = Bun.spawnSync({ cmd: argv, cwd, stdin: Buffer.from(input) })
      return {
        exitCode: p.exitCode,
        stdout: p.stdout?.toString() ?? '',
        stderr: p.stderr?.toString() ?? '',
      }
    })
  const memoPath =
    deps.memoPath ??
    env.NN_UPGRADE_MEMO ??
    path.join(opts.repoRoot, 'tmp', 'node-upgrade-memo.json')
  const memo = deps.noMemo ? {} : loadMemo(memoPath)
  const seen = opts.seen ?? (deps.noMemo ? [] : latestSeenEntries(memo))
  const spec = buildUpgradeSpec({ ...opts, seen })
  const venvMissing = !pythonCandidates(opts.repoRoot).some((p) => existsSync(p))
  let parsed: { dirty: string[]; results: UpgradeNodeResult[] }
  try {
    const r = spawn(['bash', NN_PY_SAFE, UPGRADE_CLI], JSON.stringify(spec), opts.repoRoot)
    if (r.exitCode !== 0 && !r.stdout.trim()) {
      return {
        ok: false,
        dirty: [],
        results: [],
        error:
          `升级子进程退出 ${r.exitCode}: ${(r.stderr || r.stdout).trim().slice(0, 400) || '(无输出)'}` +
          (venvMissing ? ' [nn-training/.venv 不存在——先建 venv 或用 NN_PY 指定]' : ''),
      }
    }
    parsed = parseUpgradeOutput(r.stdout)
  } catch (e) {
    return {
      ok: false,
      dirty: [],
      results: [],
      error:
        `升级指令下发失败（${(e as Error).message}）—— 检查 bash 与 nn-training/.venv 可用` +
        `（解释器可用 NN_PY 覆盖）`,
    }
  }
  // dry_run 不可能产生 restart-requested（python 侧只回 stale/current），但仍显式挡一道：
  // 没真下发的绝不许写成「已发过」，否则下次会被自己静默吞掉。
  const requested =
    opts.dryRun === true ? [] : parsed.results.filter((x) => x.reason === 'restart-requested')
  if (!deps.noMemo && requested.length > 0) {
    for (const x of requested) {
      if (!x.pingHash) continue
      const exp = opts.expectedHash ?? ''
      memo[memoKey(x.id, x.pingHash, exp)] = new Date().toISOString()
    }
    saveMemo(memoPath, memo)
  }
  return { ok: true, dirty: parsed.dirty, results: parsed.results }
}

/** 升级结果 → 日志行（响亮；调用方逐行打到 stderr）。 */
export function upgradeLogLines(prefix: string, out: UpgradeOutcome, dirty?: string[]): string[] {
  const lines: string[] = []
  if (out.error) {
    lines.push(`${prefix} WARN node upgrade FAILED: ${out.error}`)
    return lines
  }
  const d = dirty ?? out.dirty
  if (d.length > 0)
    lines.push(
      `${prefix} node upgrade: 工作区有 ${d.length} 个集内文件未提交（${d.slice(0, 3).join(', ')}${d.length > 3 ? ', …' : ''}）` +
        ` ⇒ 远端 pull 永不收敛，守卫已拒发远端升级（self 不受限）`,
    )
  for (const r of out.results) {
    const label =
      r.reason === 'restart-requested'
        ? '已下发 pull+restart（重启后重跑即可用远端）'
        : r.reason === 'current'
          ? '已是期望 codeHash'
          : r.reason === 'dedup'
            ? '同一 (agent hash, 期望 hash) 且仍在去重冷却窗内，跳过（防连环重启；窗过后自动重发一次）'
            : r.reason.startsWith('dirty-tree')
              ? `拒发（${r.reason}）`
              : r.reason === 'restart-failed'
                ? 'agent 拒绝/不可达（grace 窗口或网络）'
                : r.reason === 'unreachable'
                  ? 'ping 不通（本轮跳过；节点恢复后重跑即可）'
                  : r.reason === 'stale'
                    ? 'stale（dry-run 未下发）'
                    : r.reason === 'planned'
                      ? '计划下发（dry-run 未发）'
                      : r.reason
    lines.push(`${prefix} node ${r.id}: ${r.ok ? 'OK ' : ''}${label}`)
  }
  return lines
}
