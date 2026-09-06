/**
 * pool-page.ts — /pool 节点池监控页渲染（独立文件：2026-08-31 从 sampler-agent.ts 拆出）。
 *
 * 为什么独立：节点池监控的调整不应要求所有远程节点更新重启——sampler-agent.ts 在
 * dist codeHash 集内（dist_common.py + sampler-agent.ts 双语清单：src/nn/** +
 * sampler-agent.ts + 三个 rollout/eval 导出器），改它 = 全节点升级波。本文件**不在
 * codeHash 集**：只动它 → 零升级（节点 agent 升级时 git pull 全仓库会带上它；
 * 特性开关集内文件 import 本文件时旧节点因旧版 sampler-agent 不引用、无裂解）。
 *
 * 调用方：sampler-agent.ts 的 GET /pool handler（仅主控机：rl-config.json 有 nodes）——
 * 经 mtime 键控动态 import 热加载（2026-09-06，DECISIONS §341）：改动本文件即时生效，
 * 无需重启 agent；坏文件（语法错误/编辑中）时 agent 沿用上一版可用模块并打日志。
 * 本文件只读：聚合 tmp 下各训练流目录的 dist-agent-meta.jsonl + 实时 ping，不渲染任何密钥。
 */

import { readFileSync, readdirSync, statSync, existsSync, writeFileSync } from 'fs'
import { join, resolve } from 'path'

/** 仓库根（本文件在 tools/agent/ 下）。 */
const REPO_ROOT = resolve(import.meta.dir ?? process.cwd(), '..', '..')

// ---------------- 历史锚点（一次性，受控清空） ----------------

/** 锚点文件：tmp/dist-agent/pool-epoch.txt（毫秒时间戳）。语义（用户 2026-08-31）：
 *  · 本次修正部署写入一次 → 历史自此刻起重新累计；
 *  · 此后任何部署/重启**只读**同一锚点 → 历史持续累计，不再按部署重置；
 *  · 用户明确要求清空时，重写/删除该文件再重启 agent = 新锚点。 */
const EPOCH_FILE = join(REPO_ROOT, 'tmp', 'dist-agent', 'pool-epoch.txt')

function poolEpochMs(): number {
  try {
    if (!existsSync(EPOCH_FILE)) return 0
    const v = parseInt(readFileSync(EPOCH_FILE, 'utf8').trim(), 10)
    return Number.isFinite(v) && v > 0 ? v : 0
  } catch {
    return 0
  }
}

/** 0 = 锚点未建立（累计全部历史）；>0 = 只统计该时刻之后的行。 */
const POOL_EPOCH_MS = poolEpochMs()

// ---------------- 配置 ----------------

interface PoolNodeCfg {
  id: string
  url: string
  authKey: string
  /** rl-config 的 enabled 字段；disabled 节点不 ping、状态列显示 disabled、历史仍展示。 */
  enabled: boolean
}

function loadPoolConfig(): PoolNodeCfg[] | null {
  try {
    const cfgPath = join(REPO_ROOT, 'nn-training', 'rl-config.json')
    if (!existsSync(cfgPath)) return null
    const cfg = JSON.parse(readFileSync(cfgPath, 'utf8')) as {
      nodes?: Array<{ id?: string; url: string; authKey?: string; enabled?: boolean }>
    }
    const nodes = (cfg.nodes ?? [])
      .filter((n) => n && typeof n.url === 'string')
      .map((n) => ({
        id: n.id || n.url,
        url: n.url,
        authKey: n.authKey ?? '',
        enabled: n.enabled !== false,
      }))
    return nodes.length > 0 ? nodes : null
  } catch {
    return null
  }
}

/** 主控机判定（= handler 的 404 门槛）：rl-config 有 nodes 才有 pool 页。 */
export function poolNodes(): PoolNodeCfg[] | null {
  return loadPoolConfig()
}

/** 训练机直跑槽位数（rl-config 的 rl.local_slots；缺省 null = 未配置/不可读）。 */
function poolLocalSlots(): number | null {
  try {
    const cfgPath = join(REPO_ROOT, 'nn-training', 'rl-config.json')
    if (!existsSync(cfgPath)) return null
    const cfg = JSON.parse(readFileSync(cfgPath, 'utf8')) as { rl?: { local_slots?: number } }
    const v = cfg.rl?.local_slots
    return typeof v === 'number' && Number.isInteger(v) && v > 0 ? v : null
  } catch {
    return null
  }
}

// ---------------- 时间格式化 ----------------

function fmtTs(ms: number): string {
  const d = new Date(ms)
  const p = (x: number): string => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** dist-agent-meta 的 ts 分布带 T（ISO）与空格两种写法；统一为空格格式后再比（字典序可比）。 */
function normTs(s: string | undefined): string {
  return (s ?? '').replace('T', ' ')
}

// ---------------- 历史聚合 ----------------

interface NodeHistory {
  ok: number
  fail: number
  lastTs: string
  lastOkTs: string
  lastFailTs: string
  lastError: string
  /** 最近至多 50 局的端到端服务时长样本（滑动窗口——2026-09-06：口径升级/负载变化后
   * 均值不被终身历史拖累，替代原「自 epoch 全量平均」）。 */
  elapsedRecent: number[]
  /** elapsedRecent 的均值（scan 尾部计算）；null = 无样本。 */
  avgElapsedSec: number | null
  /** 最近至多 10 条结算结果（ok=true），完成率 = 在线状态的判定依据（替代单次 ping）。 */
  recent: boolean[]
  /** 该节点自己最近一次成功结算的轮次（-1 = 无成功记录；落后提示用）。 */
  lastIter: number
  /** 全局最新轮（aggregateNodeHistory 的 globalMaxIt）该节点的成功结算局数。 */
  lastIterOk: number
}

/** 活跃训练流信息：mtime 最新的 dist-agent-meta.jsonl 所在目录。 */
interface ActiveFlow {
  /** 目录短名（如 s3-cap2、rl-traj）。 */
  dir: string
  /** 该 meta 文件最后修改时间。 */
  mtimeMs: number
  /** 该文件行数（条目数）。 */
  lines: number
}

function aggregateNodeHistory(): {
  hist: Map<string, NodeHistory>
  activeFlow: ActiveFlow | null
  /** 全局最新轮 = 所有节点成功结算的最大 it（上轮贡献度的对齐基准）。 */
  globalMaxIt: number
} {
  const hist = new Map<string, NodeHistory>()
  // 各节点在「轮次 → 成功局数」的分布：上轮贡献度按全局最大轮对齐（单遍扫描先
  // 收集，扫完后归并——见函数尾的修正注释）。
  const okByNodeIt = new Map<string, Map<number, number>>()
  const bump = (node: string): NodeHistory => {
    let h = hist.get(node)
    if (!h) {
      h = {
        ok: 0,
        fail: 0,
        lastTs: '',
        lastOkTs: '',
        lastFailTs: '',
        lastError: '',
        elapsedRecent: [],
        avgElapsedSec: null,
        recent: [],
        lastIter: -1,
        lastIterOk: 0,
      }
      hist.set(node, h)
    }
    return h
  }
  const epochStr = fmtTs(POOL_EPOCH_MS)
  // 最近一小时永远相对"当下"（不可复用 epoch——epoch 是部署锚点，过去的部署会使
  // hourAgo 退到远古，昨天错误也会被判"一小时以内"）。
  const hourAgoStr = fmtTs(Date.now() - 3_600_000)

  // 收集所有候选 meta 文件（递归扫描 tmp/ 下所有 dist-agent-meta.jsonl）。
  // 训练流的 traj_root 可以是 tmp/X（一层）或 tmp/X/traj（两层），必须递归搜索。
  interface MetaCandidate {
    path: string
    /** 相对 tmp/ 的目录路径（如 s3-cap2、s-dodge/traj）。 */
    dir: string
    mtimeMs: number
  }
  const candidates: MetaCandidate[] = []
  const tmpDir = join(REPO_ROOT, 'tmp')
  const walk = (base: string, rel: string): void => {
    try {
      for (const d of readdirSync(join(base, rel), { withFileTypes: true })) {
        const childRel = rel ? `${rel}/${d.name}` : d.name
        if (d.isDirectory()) {
          // 限制深度：最多 3 层（tmp/X/traj/it1 级别），避免扫描 node_modules 等
          if (childRel.split('/').length <= 3) walk(base, childRel)
        } else if (d.name === 'dist-agent-meta.jsonl') {
          const p = join(base, childRel)
          try {
            candidates.push({
              path: p,
              dir: childRel.replace(/\/dist-agent-meta\.jsonl$/, ''),
              mtimeMs: statSync(p).mtimeMs,
            })
          } catch {
            /* stat failed */
          }
        }
      }
    } catch {
      /* unreadable */
    }
  }
  try {
    walk(tmpDir, '')
  } catch {
    /* tmp missing */
  }

  // 按 mtime 降序：最新修改的 = 当前活跃训练流。
  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs)
  const activeFlow: ActiveFlow | null =
    candidates.length > 0
      ? { dir: candidates[0].dir, mtimeMs: candidates[0].mtimeMs, lines: 0 }
      : null
  // 仅聚合 mtime 最新的那个 meta 文件——避免旧训练流的数千条历史淹没新训练数据。
  // 若无候选（tmp 不存在）则退化为不聚合（返回空 map）。
  const sources = candidates.length > 0 ? [candidates[0]] : []
  for (const src of sources) {
    try {
      if (!existsSync(src.path)) continue
      for (const line of readFileSync(src.path, 'utf8').split(String.fromCharCode(10))) {
        if (!line.trim()) continue
        try {
          const r = JSON.parse(line) as {
            node?: string
            ok?: boolean
            elapsedSec?: number
            ts?: string
            reason?: string
            it?: number
          }
          if (!r.node) continue
          const nts = normTs(r.ts)
          // 只统计"清空锚点之后"的行（POOL_HISTORY_FRESH=1 显式清零时才有锚点；
          // 默认 0 = 保留全部历史）。ts 缺失无法判定新旧 → 忽略，保守。
          if (!r.ts || nts < epochStr) continue
          const h = bump(r.node)
          // 最近 10 次完成率（rollout+eval 混合）：状态判定数据源。
          h.recent.push(!!r.ok)
          if (h.recent.length > 10) h.recent.shift()
          if (r.ok) {
            h.ok++
            if (nts >= h.lastOkTs) h.lastOkTs = nts
            const it = typeof r.it === 'number' && Number.isInteger(r.it) ? r.it : -1
            if (it >= 0) {
              let m = okByNodeIt.get(r.node)
              if (!m) {
                m = new Map()
                okByNodeIt.set(r.node, m)
              }
              m.set(it, (m.get(it) ?? 0) + 1)
            }
            if (typeof r.elapsedSec === 'number' && r.elapsedSec > 0) {
              h.elapsedRecent.push(r.elapsedSec)
              if (h.elapsedRecent.length > 50) h.elapsedRecent.shift()
            }
          } else {
            h.fail++
            // 最新错误只近一小时（用户指令）：窗口外错误不进 lastError。
            h.lastError = nts >= hourAgoStr ? (r.reason ?? '').slice(0, 120) : h.lastError
            if (nts >= hourAgoStr && nts > h.lastFailTs) h.lastFailTs = nts
          }
          if (nts > h.lastTs) h.lastTs = nts
        } catch {
          /* skip bad line */
        }
      }
    } catch {
      /* unreadable */
    }
  }
  if (activeFlow && sources.length > 0) {
    try {
      activeFlow.lines = readFileSync(sources[0].path, 'utf8')
        .split(String.fromCharCode(10))
        .filter((l) => l.trim()).length
    } catch {
      /* ignore */
    }
  }
  // 上轮贡献度修正（2026-09-06，a98 案例）：原实现按「节点自己的最大 it」计数——
  // 慢节点的陈旧贡献会被永远展示成「上轮贡献」（a98 在 it14 的 1 局，全局已到
  // it24 时仍显示 1，与 16 分钟前的 lastOkTs 一起误导）。本注释块之上的旧实现注
  // 释原意即「全局最大 it」，实现与注释不符。现口径：globalMaxIt = 所有节点成功
  // 结算的最大轮次；各节点只统计该轮的成功局数，落后节点计 0（渲染层灰显 +
  // tooltip 给出其最近贡献轮次）。
  let globalMaxIt = -1
  for (const m of okByNodeIt.values()) {
    for (const it of m.keys()) if (it > globalMaxIt) globalMaxIt = it
  }
  for (const [node, h] of hist) {
    const m = okByNodeIt.get(node)
    let maxIt = -1
    if (m) for (const it of m.keys()) if (it > maxIt) maxIt = it
    h.lastIter = maxIt
    h.lastIterOk = (globalMaxIt >= 0 && m?.get(globalMaxIt)) || 0
    // 滑动窗口均值（最近 ≤50 局）：口径升级/负载变化后即时不被终身历史拖累。
    h.avgElapsedSec = h.elapsedRecent.length
      ? +(h.elapsedRecent.reduce((a, b) => a + b, 0) / h.elapsedRecent.length).toFixed(1)
      : null
  }
  return { hist, activeFlow, globalMaxIt }
}

function poolStatusCell(h: NodeHistory): string {
  const n = h.recent.length
  const okN = h.recent.filter(Boolean).length
  if (n === 0) return `<span class="badge b-gray">无数据</span>`
  if (okN / n >= 0.9) return `<span class="badge b-green">健康 ${okN}/${n}</span>`
  if (okN / n >= 0.7) return `<span class="badge b-yellow">波动 ${okN}/${n}</span>`
  return `<span class="badge b-red">异常 ${okN}/${n}</span>`
}

/** 上轮贡献度单元格：>0 = 在全局最新轮的成功结算局数；0 但有历史 = 该节点最近
 * 贡献已落后当前轮（灰显 + tooltip 指出其最近贡献轮次——a98 案例：it14 的 1 局
 * 曾被当作「上轮贡献」展示，实际全局已到 it24）；无任何成功记录 = '-'。
 * data-v=0 让落后节点在默认排序里沉底。 */
function contribCell(h: NodeHistory, globalMaxIt: number): string {
  if (h.lastIterOk > 0) return `<td class="num" data-v="${h.lastIterOk}">${h.lastIterOk}</td>`
  if (h.lastIter >= 0 && globalMaxIt >= 0) {
    return (
      `<td class="num" data-v="0"><span class="muted" title="该节点最近一次成功结算在 ` +
      `it${h.lastIter}，已落后当前 it${globalMaxIt}">${h.lastIterOk}</span></td>`
    )
  }
  return `<td class="num" data-v="0">-</td>`
}

// ---------------- 每轮实际值（it{N}/**/manifest.json 聚合） ----------------

interface IterActuals {
  /** 去重后实际局数（= 唯一 (stage,seed) 数）。 */
  games: number
  /** 本轮总击杀。 */
  totalKills: number
  /** 本轮总道具（powerUpsCollected）。 */
  totalPU: number
  /** 场均存活 tick（totalTicks / games）。 */
  avgTicks: number
}

/** 聚合某一轮的真实终局值：递归扫 it{N} 下全部 shard 的 manifest.json。
 *
 * - 按 (stage,seed) 去重：fan-out 竞速副本会重复写盘（同一局多节点结算），
 *   取 nSamples 最大者（局信息最全的副本）。
 * - 跳过 local-eval：干净评估局不是 rollout 局，混入会放大轮计数。
 * - 无任何 manifest / it 目录不存在（已被 _rotate_cleanup 轮转删除）→ null，
 * 调用方回退为估算值。 */
function readIterActuals(trajDir: string, iter: number): IterActuals | null {
  const itDir = join(trajDir, `it${iter}`)
  try {
    if (!existsSync(itDir)) return null
    const best = new Map<string, { nSamples: number; kills: number; pu: number; ticks: number }>()
    const walk = (base: string, rel: string): void => {
      if (rel.split('/').length > 6) return
      let names: string[]
      try {
        names = readdirSync(join(base, rel), { withFileTypes: true }).map((d) => d.name)
      } catch {
        return
      }
      for (const name of names) {
        if (name === 'local-eval') continue
        const childRel = rel ? `${rel}/${name}` : name
        const p = join(base, childRel)
        let isDir = false
        try {
          isDir = statSync(p).isDirectory()
        } catch {
          continue
        }
        if (isDir) {
          walk(base, childRel)
          continue
        }
        if (name !== 'manifest.json') continue
        try {
          const m = JSON.parse(readFileSync(p, 'utf8')) as {
            stage?: unknown
            seed?: unknown
            kills?: number
            powerUpsCollected?: number
            ticks?: number
            nSamples?: number
          }
          const stage = Number(m.stage)
          const seed = Number(m.seed)
          if (!Number.isFinite(stage) || !Number.isFinite(seed)) continue
          const nSamples = Number(m.nSamples ?? 0)
          const prev = best.get(`${stage}:${seed}`)
          if (!prev || nSamples > prev.nSamples) {
            best.set(`${stage}:${seed}`, {
              nSamples,
              kills: Number(m.kills ?? 0) || 0,
              pu: Number(m.powerUpsCollected ?? 0) || 0,
              ticks: Number(m.ticks ?? 0) || 0,
            })
          }
        } catch {
          /* skip bad manifest */
        }
      }
    }
    walk(itDir, '')
    if (best.size === 0) return null
    let totalKills = 0
    let totalPU = 0
    let totalTicks = 0
    for (const v of best.values()) {
      totalKills += v.kills
      totalPU += v.pu
      totalTicks += v.ticks
    }
    return {
      games: best.size,
      totalKills,
      totalPU,
      avgTicks: Math.round(totalTicks / best.size),
    }
  } catch {
    return null
  }
}

// ---------------- 实际值留底缓存（计算一次永久使用） ----------------
// keep_iters（默认 5）一到，trainer 的 _rotate_cleanup 删旧 it{N} 目录；且 manifest
// 聚合是递归目录扫描——每刷新都重算既慢又不必要。实际值是终局值、聚合一次不再变化，
// 故：①轮次收尾后首次渲染时计算一次，写入 flow 根目录 .pool-actuals-cache.json；
// ②此后每次渲染查缓存直接用（不再扫目录）；③目录被轮转后缓存即唯一来源，真实值
// 照常展示（不再回退 ≈）。键 = iter；值附该轮 iteration 事件的 time 作防串门闩——
// 同 traj 重启复用 iter 号时 time 不同，视为 miss 重算并覆写。文件损坏/缺失 →
// 空缓存重建（自愈）。

interface CachedActuals extends IterActuals {
  /** 该轮 iteration 事件的 time（防串门闩：同 iter 号不同轮 → 不采用缓存）。 */
  time: string
}

function actualsCachePath(trajDir: string): string {
  return join(trajDir, '.pool-actuals-cache.json')
}

function loadActualsCache(trajDir: string): Map<number, CachedActuals> {
  const out = new Map<number, CachedActuals>()
  try {
    const raw = JSON.parse(readFileSync(actualsCachePath(trajDir), 'utf8')) as Record<
      string,
      CachedActuals
    >
    for (const [k, v] of Object.entries(raw)) {
      const it = Number(k)
      if (
        Number.isInteger(it) &&
        it >= 0 &&
        v &&
        typeof v.time === 'string' &&
        typeof v.games === 'number' &&
        typeof v.totalKills === 'number' &&
        typeof v.totalPU === 'number' &&
        typeof v.avgTicks === 'number'
      ) {
        out.set(it, v)
      }
    }
  } catch {
    /* 缺失/损坏 → 空缓存重建 */
  }
  return out
}

function saveActualsCache(trajDir: string, cache: Map<number, CachedActuals>): void {
  try {
    // 只留最近 500 轮，防无限增长。
    const obj: Record<string, CachedActuals> = {}
    for (const k of [...cache.keys()].sort((a, b) => b - a).slice(0, 500)) {
      const v = cache.get(k)
      if (v) obj[String(k)] = v
    }
    writeFileSync(actualsCachePath(trajDir), JSON.stringify(obj))
  } catch {
    /* 写失败不致命——下次渲染带新数据重试 */
  }
}

// ---------------- 干净评估汇总（eval_log.jsonl） ----------------

/** eval_log.jsonl 渲染所需聚合：eval_summary 行 + 该 (iter,wver) 逐局行的
 * 存活/击杀/道具/得分聚合（summary 行本身不带这些——只在 event=eval 逐局行里，
 * 字段 ticks/kills/powerUpsCollected/score）。 */
interface EvalSummary {
  time: string
  games: number
  wins: number
  winRate: number | null
  clears: number
  clearRate: number | null
  dropped: number
  sec: number
  wver: string
  outcomes: Record<string, number>
  /** 逐局聚合（按 wver 对齐该轮 summary）；null = 逐局行已缺（只剩 summary 行）。 */
  avgTicks: number | null
  totalKills: number | null
  totalPU: number | null
  scoreMean: number | null
  scoreStd: number | null
}

/** 读取 eval_log.jsonl：eval_summary 按 iter 归并（同 iter 多条 = 断点续跑补评估后
 * 的重复落账，取最后一条 = 最新对账结果，与 rl/eval_m1.read_eval_summary 的回读
 * 口径一致），并聚合该 (iter,wver) 的 event=eval 逐局行。
 *
 * it 序数语义（eval 行对齐本表的关键）：eval_summary.iter = N 评估的是**第 N 轮
 * PPO 更新前**的权重——即第 N 轮 rollout 采样所用的同一权重（eval 派发发生在第
 * N 轮 rollout 收尾、_serial_ppo 之前，rl/rollout_phase.py 把该轮 report.winRate
 * 随派发传入，落成 summary 的 rolloutWinRate 字段，可与本表第 N 行「胜率」逐字
 * 对账：tmp/p4-onset 实测 it5 = 0.0533 两处一致）。summary 可能晚到——_join_eval
 * 软等待最多 180s，溢出的在途局收官后 eval 线程才落账（写盘时刻已在第 N+1 轮内）
 * ——因此按 iter 字段匹配本表行，绝不按时间邻近匹配。 */
function readEvalSummaries(trajDir: string): Map<number, EvalSummary> {
  const out = new Map<number, EvalSummary>()
  const logPath = join(trajDir, 'eval_log.jsonl')
  // 逐局行按 (iter → wver → 聚合桶) 收集：summary 落账晚于逐局行（单遍文件读，
  // wver 未知时先收着）；断点续跑同 iter 可能出现双 wver，逐局桶按 wver 分键互不串。
  interface GameAgg {
    n: number
    ticks: number
    kills: number
    pu: number
    scoreSum: number
    scoreSqSum: number
  }
  const games = new Map<number, Map<string, GameAgg>>()
  try {
    if (!existsSync(logPath)) return out
    for (const line of readFileSync(logPath, 'utf8').split(String.fromCharCode(10))) {
      if (!line.trim()) continue
      try {
        const r = JSON.parse(line) as Record<string, unknown>
        const iter = Number(r.iter ?? -1)
        if (!Number.isInteger(iter) || iter < 0) continue
        if (r.event === 'eval') {
          const wver = String(r.wver ?? '')
          if (!wver) continue
          let byWver = games.get(iter)
          if (!byWver) {
            byWver = new Map()
            games.set(iter, byWver)
          }
          let agg = byWver.get(wver)
          if (!agg) {
            agg = { n: 0, ticks: 0, kills: 0, pu: 0, scoreSum: 0, scoreSqSum: 0 }
            byWver.set(wver, agg)
          }
          const score = Number(r.score ?? 0)
          agg.n++
          agg.ticks += Number(r.ticks ?? 0) || 0
          agg.kills += Number(r.kills ?? 0) || 0
          agg.pu += Number(r.powerUpsCollected ?? 0) || 0
          agg.scoreSum += score
          agg.scoreSqSum += score * score
          continue
        }
        if (r.event !== 'eval_summary') continue
        out.set(iter, {
          time: String(r.time ?? ''),
          games: Number(r.games ?? 0),
          wins: Number(r.wins ?? 0),
          winRate: typeof r.winRate === 'number' ? r.winRate : null,
          clears: Number(r.clears ?? 0),
          clearRate: typeof r.clearRate === 'number' ? r.clearRate : null,
          dropped: Number(r.dropped ?? 0),
          sec: Number(r.sec ?? 0),
          wver: String(r.wver ?? ''),
          outcomes: (r.outcomes ?? {}) as Record<string, number>,
          avgTicks: null,
          totalKills: null,
          totalPU: null,
          scoreMean: null,
          scoreStd: null,
        })
      } catch {
        /* skip bad line */
      }
    }
    // 合并：每个 summary 只配它自己 wver 的逐局桶。总体 std（÷n）——与逐局展示
    // 同量纲即可，非统计推断场景。
    for (const [iter, s] of out) {
      const agg = games.get(iter)?.get(s.wver)
      if (!agg || agg.n === 0) continue
      const mean = agg.scoreSum / agg.n
      s.avgTicks = Math.round(agg.ticks / agg.n)
      s.totalKills = agg.kills
      s.totalPU = agg.pu
      s.scoreMean = +mean.toFixed(4)
      s.scoreStd = +Math.sqrt(Math.max(0, agg.scoreSqSum / agg.n - mean * mean)).toFixed(4)
    }
  } catch {
    /* unreadable */
  }
  return out
}

// ---------------- 每轮迭代指标（training_log.jsonl） ----------------

interface IterRow {
  iter: number
  time: string
  winRate: number
  scoreMean: number
  scoreStd: number
  samples: number
  rolloutSec: number
  ppoSec: number
  kl: number
  entropy: number
  policyLoss: number
  valueLoss: number
  meanRet: number
  lr: number
  expectedGames: number
  halted: boolean
  /** dist.nodes 值（node→count），用于渲染节点贡献。 */
  distNodes: Record<string, number>
  /** dim_means 前三名 key（简化展示）。 */
  topDims: string
  avgTicks: number
  accuracy: number
  loot: number
  kills: number
  /** 该轮磁盘真实聚合（it{N} 下各局 manifest.json）；null = 无数据（已轮转/未收尾）。 */
  actuals: IterActuals | null
  /** 该轮干净评估汇总（eval_log.jsonl 的 eval_summary，按 iter 对齐）；null = 该轮未派发 eval。 */
  evalData: EvalSummary | null
}

/** 从 training_log.jsonl 读取最近 MAX_ITER_ROWS 轮迭代指标。

 * 实际值缓存优先（2026-09-06 用户指令「计算过一次就缓存起来一直用」）：每轮的
 * manifest 聚合（递归扫 it{N} 目录）只做一次，结果落 .pool-actuals-cache.json，
 * 之后每次渲染查缓存直接用——time 门闩匹配才命中（同 traj 重启复用 iter 号时
 * time 不同 → 视为 miss 重算并覆写）。轮转删除目录后缓存即唯一来源，真实值
 * 照常展示（不再回退 ≈）。 */
function readIterMetrics(
  trajDir: string,
  actualsCache: Map<number, CachedActuals>,
): { rows: IterRow[]; cacheDirty: boolean } {
  const MAX = 20
  const logPath = join(trajDir, 'training_log.jsonl')
  // eval 汇总整册读一次（按 iter 键控），逐行查表——不在循环里反复开文件。
  const evalSummaries = readEvalSummaries(trajDir)
  let cacheDirty = false
  try {
    if (!existsSync(logPath)) return { rows: [], cacheDirty }
    const lines = readFileSync(logPath, 'utf8').split(String.fromCharCode(10))
    const rows: IterRow[] = []
    for (const line of lines) {
      if (!line.trim()) continue
      try {
        const r = JSON.parse(line) as Record<string, unknown>
        if (r.event !== 'iteration') continue
        const dm = (r.dim_means ?? {}) as Record<string, number>
        const topDims = Object.entries(dm)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 3)
          .map(([k, v]) => `${k}:${(v * 100).toFixed(0)}%`)
          .join(' ')
        const iter = Number(r.iter ?? 0)
        const rowTime = String(r.time ?? '')
        // 实际值：缓存命中（time 门闩匹配）→ 直接用，不再递归扫目录；miss →
        // 读盘聚合一次并写入缓存。iteration 事件只在轮次收尾时落账，故缓存值
        // 恒为该轮终局聚合（无中途部分值污染）。
        let actuals: IterActuals | null = null
        const cached = actualsCache.get(iter)
        if (cached && cached.time === rowTime) {
          actuals = {
            games: cached.games,
            totalKills: cached.totalKills,
            totalPU: cached.totalPU,
            avgTicks: cached.avgTicks,
          }
        } else {
          actuals = readIterActuals(trajDir, iter)
          if (actuals) {
            actualsCache.set(iter, { ...actuals, time: rowTime })
            cacheDirty = true
          }
        }
        rows.push({
          iter,
          time: rowTime,
          winRate: Number(r.winRate ?? 0),
          scoreMean: Number(r.score_mean ?? 0),
          scoreStd: Number(r.score_std ?? 0),
          samples: Number(r.samples ?? 0),
          rolloutSec: Number(r.rollout_sec ?? 0),
          ppoSec: Number(r.ppo_sec ?? 0),
          kl: Number(r.kl ?? 0),
          entropy: Number(r.entropy ?? 0),
          policyLoss: Number(r.policy ?? 0),
          valueLoss: Number(r.value ?? 0),
          meanRet: Number(r.mean_ret ?? 0),
          lr: Number(r.lr ?? 0),
          expectedGames: Number(r.expectedGames ?? 0),
          halted: Boolean(r.halted),
          distNodes: (r.dist as any)?.nodes ?? {},
          topDims,
          avgTicks:
            Number(r.expectedGames) > 0
              ? Math.round(Number(r.ticks ?? 0) / Number(r.expectedGames))
              : 0,
          accuracy: Number(dm.accuracy ?? 0),
          loot: Number(dm.loot ?? 0),
          kills: +(Number(dm.progress ?? 0) * 20).toFixed(2),
          actuals,
          evalData: evalSummaries.get(iter) ?? null,
        })
      } catch {
        /* skip bad line */
      }
    }
    // 同 iter 去重：取最后一条 = 最新对账结果（2026-09-06 双 trainer 并行事故：
    // it57-59 各被两遍训练/落账，页面出现双行；与 rl.eval_m1.read_eval_summary
    // 对 eval_summary「同 iter 取最后一条」的回读口径一致）。
    const byIter = new Map<number, IterRow>()
    for (const r of rows) byIter.set(r.iter, r)
    const merged = [...byIter.values()]
    merged.sort((a, b) => b.iter - a.iter)
    return { rows: merged.slice(0, MAX), cacheDirty }
  } catch {
    return { rows: [], cacheDirty }
  }
}

/** eval 子行：插在对应 iter 主行下，13 列与主行一一对齐。数据 = eval_summary（胜率/
 * 全歼/局数/dropped/用时）+ 该 (iter,wver) 逐局行聚合（存活/击杀/道具/得分，见
 * readEvalSummaries）。PPO 侧指标干净评估不产生，占 '-'——胜率/得分/存活/击杀/道具
 * 上下两行同列同语义，采样（主行）vs 贪心（子行）逐列对照。 */
function renderEvalRow(iter: number, e: EvalSummary): string {
  const dash = `<td class="num"><span class="muted">-</span></td>`
  // 标签：默认只显示「eval」（用户指令）；eval only 过滤模式下经 .evit 追加轮号
  // （主行被隐藏后位置相邻失效，子行需自描述），全部/rollout 模式 CSS 隐藏轮号。
  // 缺N pill 仅 dropped>0 时出现，恒显。
  const label =
    `eval<span class="evit"> it${iter}</span>` +
    (e.dropped > 0
      ? `<span class="pill" title="评估窗口内未收官、被下轮权重分发清场的评估局数">缺${e.dropped}</span>`
      : '')
  let winCell = dash
  if (e.winRate !== null) {
    const cls = e.winRate >= 0.3 ? 'b-green' : e.winRate >= 0.1 ? 'b-yellow' : 'b-red'
    const outcomeTxt =
      Object.entries(e.outcomes)
        .map(([k, v]) => `${k}×${v}`)
        .join(' ') || '-'
    const title =
      `干净评估（greedy 固定语料）· 评估权重 = 第 ${iter} 轮 PPO 更新前（与该轮 rollout 同权重，对照上行采样胜率）· ` +
      `${e.games} 局 ${e.wins} 胜 · 全歼 ${e.clears} · outcomes: ${outcomeTxt} · 用时 ${e.sec}s · wver ${e.wver.slice(0, 12)}…`
    winCell =
      `<td><span class="badge ${cls}" title="${title}">${(e.winRate * 100).toFixed(1)}%</span>` +
      `<span class="muted"> ${e.wins}/${e.games}</span></td>`
  }
  const scoreCell =
    e.scoreMean !== null
      ? `<td class="num">${e.scoreMean.toFixed(4)}<span class="muted">±${(e.scoreStd ?? 0).toFixed(4)}</span></td>`
      : dash
  const ticksCell = e.avgTicks !== null ? `<td class="num">${e.avgTicks}</td>` : dash
  const killsCell =
    e.totalKills !== null
      ? `<td class="num">${e.totalKills}<span class="muted"> /${e.games}局</span></td>`
      : dash
  const puCell =
    e.totalPU !== null
      ? `<td class="num">${e.totalPU}<span class="muted"> /${e.games}局</span></td>`
      : dash
  return `<tr class="evalrow">
<td class="muted" style="white-space:nowrap" title="第 ${iter} 轮的干净评估（对齐上方同 iter 主行；评估的是该轮 PPO 更新前的权重，全歼 ${e.clears}/${e.games}）">${label}</td>
<td class="muted">${e.time}</td>
${winCell}
${ticksCell}
${killsCell}
${puCell}
${scoreCell}
<td class="num" title="eval 窗口用时">${e.sec.toFixed(0)}s</td>
${dash}${dash}${dash}${dash}${dash}
</tr>`
}

function renderIterTable(rows: IterRow[]): string {
  if (rows.length === 0) return ''
  const hdr = `<thead><tr>
<th>iter</th><th>时间</th><th>胜率</th><th>存活</th><th>击杀</th><th>道具</th><th>得分</th>
<th>rollout</th><th>PPO</th>
<th>KL</th><th>entropy</th><th>mean_ret</th>
<th>lr</th>
</tr></thead>`
  const cells: string[] = []
  for (const r of rows) {
    const winPct = (r.winRate * 100).toFixed(1)
    const winCls = r.winRate >= 0.3 ? 'b-green' : r.winRate >= 0.1 ? 'b-yellow' : 'b-red'
    const klCls = r.kl > 0.05 ? 'b-red' : r.kl > 0.02 ? 'b-yellow' : 'b-green'
    const retCls = r.meanRet > -0.5 ? 'b-green' : r.meanRet > -1.0 ? 'b-yellow' : 'b-red'
    const halted = r.halted ? ' <span class="pill">halted</span>' : ''
    // 实际值优先（it{N} 磁盘 shard 聚合）；轮次被轮转清理/未收尾 → 回退估算并标注。
    const a = r.actuals
    const ticksCell = a
      ? `<td class="num">${a.avgTicks}</td>`
      : `<td class="num"><span class="muted" title="该轮磁盘数据已清理，估算值">${r.avgTicks}≈</span></td>`
    const killsCell = a
      ? `<td class="num">${a.totalKills}<span class="muted"> /${a.games}局</span></td>`
      : `<td class="num"><span class="muted" title="该轮磁盘数据已清理，估算值">${r.kills.toFixed(1)}≈</span></td>`
    const lootCell = a
      ? `<td class="num">${a.totalPU}<span class="muted"> /${a.games}局</span></td>`
      : `<td class="num"><span class="muted" title="该轮磁盘数据已清理，估算值">${(r.loot * 100).toFixed(0)}%≈</span></td>`
    cells.push(`<tr>
<td class="num">${r.iter}${halted}</td>
<td class="muted">${r.time}</td>
<td><span class="badge ${winCls}">${winPct}%</span></td>
${ticksCell}
${killsCell}
${lootCell}
<td class="num">${r.scoreMean.toFixed(4)}<span class="muted">±${r.scoreStd.toFixed(4)}</span></td>
<td class="num">${r.rolloutSec.toFixed(0)}s</td>
<td class="num">${r.ppoSec.toFixed(0)}s</td>
<td><span class="badge ${klCls}">${r.kl.toFixed(4)}</span></td>
<td class="num">${r.entropy.toFixed(3)}</td>
<td><span class="badge ${retCls}">${r.meanRet.toFixed(3)}</span></td>
<td class="num">${r.lr}</td>
</tr>`)
    // eval 子行：有已落账干净评估的轮才插（eval_every 稀疏派发，多数轮没有）。
    if (r.evalData) cells.push(renderEvalRow(r.iter, r.evalData))
  }
  return `<div class="card" style="margin-top:16px">
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:14px 14px 0">
<h3 style="margin:0;font-size:15px;font-weight:600;color:var(--text)">📈 每轮训练指标</h3>
<div style="display:flex;gap:12px;align-items:center;font-size:12.5px;color:var(--muted)">
<span>行过滤</span>
<label style="display:flex;align-items:center;gap:4px;cursor:pointer"><input type="radio" name="iterFilter" value="all">全部</label>
<label style="display:flex;align-items:center;gap:4px;cursor:pointer"><input type="radio" name="iterFilter" value="rollout">rollout only</label>
<label style="display:flex;align-items:center;gap:4px;cursor:pointer"><input type="radio" name="iterFilter" value="eval">eval only</label>
</div>
</div>
<div style="max-height:800px;overflow:auto;margin-top:8px">
<table id="iters">${hdr}
<tbody>${cells.join(String.fromCharCode(10))}</tbody>
</table>
</div>
<p class="foot" style="margin:8px 2px 0">存活/击杀/道具 = <b>实际值</b>（该轮 <code>it{N}/**/manifest.json</code> 逐局聚合，按 stage+seed 去重；<b>计算一次即留底</b> <code>.pool-actuals-cache.json</code>——目录被 keep_iters 轮转删除后照常按真实值展示，刷新不再重算）。带 <code>≈</code> 的为估算（该轮收尾时页面未在运行、缓存未及建立，仅剩 <code>training_log.jsonl</code> 的 dim 均值换算）。胜率/得分/PPO 指标来自 PPO 收敛日志。eval 子行 = <b>干净评估</b>（greedy 固定语料，<code>eval_log.jsonl</code>：eval_summary + 逐局行聚合），插在对应 iter 行下、列位对齐——<b>iter=N 的 eval 评估的是第 N 轮 PPO 更新前的权重</b>，即该轮 rollout 采样所用的同一权重：胜率/存活/击杀/道具/得分上下两行直接对照（采样 vs 贪心）；summary 按 <code>iter</code> 字段对齐（可能晚到下一轮才落账，不按时间匹配）；<code>缺N</code> = 窗口内未收官被清场的评估局。行过滤选择记录在 localStorage；eval only 模式下子行标签带轮号。</p>
<script>
(function () {
  const KEY = 'pool.iterFilter';
  const apply = (mode) => {
    const tbl = document.getElementById('iters');
    if (!tbl) return;
    tbl.classList.toggle('f-eval', mode === 'eval');
    for (const tr of tbl.tBodies[0].rows) {
      const isEval = tr.classList.contains('evalrow');
      tr.style.display = mode === 'all' || (mode === 'eval' ? isEval : !isEval) ? '' : 'none';
    }
  };
  let mode = 'all';
  try { mode = localStorage.getItem(KEY) || 'all'; } catch {}
  for (const el of document.querySelectorAll('input[name="iterFilter"]')) {
    el.checked = el.value === mode;
    el.addEventListener('change', function () {
      if (!this.checked) return;
      try { localStorage.setItem(KEY, this.value); } catch {}
      apply(this.value);
    });
  }
  apply(mode);
})();
</script>
</div>`
}

// ---------------- 渲染上下文（sampler-agent 侧状态注入） ----------------

export interface PoolPageCtx {
  workers: number
  inflight: Map<string, { stage: number; seed: number; startedAt: number }>
  gamesDoneTotal: number
  /** kind → 桶（结构性类型：渲染只需 size）。 */
  weightsByKindSha: Map<string, { size: number }>
  lastError: string
  localHash: () => string
}

export async function renderPoolPage(ctx: PoolPageCtx): Promise<string> {
  const { hist, activeFlow, globalMaxIt } = aggregateNodeHistory()
  const localHash = ctx.localHash()
  const nodes = loadPoolConfig()
  const nowStr = fmtTs(Date.now())
  // 每轮迭代指标：从 activeFlow 目录下的 training_log.jsonl 读取。
  const trajDir = activeFlow ? join(REPO_ROOT, 'tmp', activeFlow.dir) : null
  const actualsCache = trajDir ? loadActualsCache(trajDir) : new Map<number, CachedActuals>()
  const { rows: iterRows, cacheDirty } = trajDir
    ? readIterMetrics(trajDir, actualsCache)
    : { rows: [] as IterRow[], cacheDirty: false }
  if (trajDir && cacheDirty) saveActualsCache(trajDir, actualsCache)
  const iterTableHtml = renderIterTable(iterRows)
  const rows: string[] = []
  const disabledRows: string[] = []
  if (nodes) {
    const probes = await Promise.all(
      nodes.map(async (n) => {
        if (!n.enabled) return { n, ping: null, ms: -1, skip: true }
        const started = Date.now()
        try {
          const resp = await fetch(`${n.url.replace(/\/$/, '')}/v1/ping`, {
            headers: { Authorization: `Bearer ${n.authKey}` },
            signal: AbortSignal.timeout(2500),
          })
          if (!resp.ok) return { n, ping: null, ms: Date.now() - started }
          return {
            n,
            ping: (await resp.json()) as Record<string, unknown>,
            ms: Date.now() - started,
          }
        } catch {
          return { n, ping: null, ms: Date.now() - started }
        }
      }),
    )
    for (const { n, ping, ms } of probes) {
      const h = hist.get(n.id) ?? {
        ok: 0,
        fail: 0,
        lastTs: '-',
        lastOkTs: '-',
        lastFailTs: '-',
        lastError: '',
        elapsedRecent: [],
        avgElapsedSec: null,
        recent: [],
        lastIter: -1,
        lastIterOk: 0,
      }
      // disabled：状态固定显示 disabled，规格/版本/ping 为 '-'，历史数据照常展示。
      const disabled = n.enabled === false
      let statusCell: string
      let specCell = '-'
      let verCell = '-'
      if (disabled) {
        statusCell = `<span class="badge b-gray">disabled</span>`
      } else if (ping) {
        const hashOk = String((ping as any)?.codeHash) === localHash
        verCell = `v${String((ping as any)?.codeHash ?? '?').slice(0, 7)}`
        if (!hashOk) verCell += `<span class="pill">旧</span>`
        statusCell = poolStatusCell(h)
        specCell = `${(ping as any)?.cpus ?? '?'} 核`
      } else {
        // ping 失败：状态仍以最近 10 次完成率表征（用户指令 8），ping 列如实显示失败。
        statusCell = poolStatusCell(h)
        if (h.recent.length === 0) statusCell = `<span class="badge b-red">无 ping · 无历史</span>`
      }
      const errCell = h.lastError
        ? `<span class="err">${h.lastError}</span><br><span class="muted">${h.lastFailTs || ''}</span>`
        : '<span class="muted">-</span>'
      const rowHtml =
        `<tr><td class="name">${n.id}</td><td>${statusCell}</td>` +
        `<td>${specCell}</td><td class="ver">${verCell}</td>` +
        `<td class="num" data-v="${disabled || !ping ? 9999 : ms}">${!disabled && ping ? `${ms}ms` : '-'}</td>` +
        `<td class="num" data-v="${h.ok}">${h.ok}</td><td class="num" data-v="${h.fail}">${h.fail}</td>` +
        `${contribCell(h, globalMaxIt)}` +
        `<td class="num" data-v="${h.avgElapsedSec ?? 9999}">${h.avgElapsedSec !== null ? `${h.avgElapsedSec}s` : '-'}</td>` +
        `<td data-v="${h.lastOkTs}">${h.lastOkTs || '-'}</td>` +
        `<td data-v="${h.lastFailTs}">${errCell}</td></tr>`
      // disabled 节点行进折叠组（渲染时默认折叠、可展开），其余行正常入表。
      if (disabled) disabledRows.push(rowHtml)
      else rows.push(rowHtml)
    }
    // 本机直跑（训练器 local 槽位）固定入表一行：无 ping/版本，状态按最近完成率、
    // 上轮贡献度照常聚合。无历史（部署清零后）也占位显示，槽位数来自 rl-config。
    const localSlots = poolLocalSlots()
    const localH = hist.get('local') ?? {
      ok: 0,
      fail: 0,
      lastTs: '-',
      lastOkTs: '-',
      lastFailTs: '-',
      lastError: '',
      elapsedRecent: [],
      avgElapsedSec: null,
      recent: [],
      lastIter: -1,
      lastIterOk: 0,
    }
    {
      const errCellL = localH.lastError
        ? `<span class="err">${localH.lastError}</span><br><span class="muted">${localH.lastFailTs || ''}</span>`
        : '<span class="muted">-</span>'
      rows.push(
        `<tr><td class="name">local<span class="dim">（本机直跑）</span></td><td>${poolStatusCell(localH)}</td>` +
          `<td>${localSlots !== null ? `${localSlots} 槽` : '-'}</td><td class="ver">-</td>` +
          `<td class="num" data-v="9999">-</td>` +
          `<td class="num" data-v="${localH.ok}">${localH.ok}</td><td class="num" data-v="${localH.fail}">${localH.fail}</td>` +
          `${contribCell(localH, globalMaxIt)}` +
          `<td class="num" data-v="${localH.avgElapsedSec ?? 9999}">${localH.avgElapsedSec !== null ? `${localH.avgElapsedSec}s` : '-'}</td>` +
          `<td data-v="${localH.lastOkTs}">${localH.lastOkTs || '-'}</td>` +
          `<td data-v="${localH.lastFailTs}">${errCellL}</td></tr>`,
      )
    }
  }
  // 默认按「已结算局」倒序（点击表头仍可手动排）。num cell 列表：ping、已结算局、失败局、
  // 上轮贡献度、平均耗时——第 2 个（index 1）= 已结算局。
  const bySettledDesc = (a: string, b: string): number => {
    const okA = Number((a.match(/<td class="num" data-v="(\d+)">/g) ?? [])[1]?.match(/\d+/) ?? 0)
    const okB = Number((b.match(/<td class="num" data-v="(\d+)">/g) ?? [])[1]?.match(/\d+/) ?? 0)
    return okB - okA
  }
  rows.sort(bySettledDesc)
  disabledRows.sort(bySettledDesc)
  return `<!doctype html><html><head><meta charset="utf-8">
<title>sampler-agent 节点池</title>
<style>
:root{--bg:#f4f6f9;--card:#ffffff;--border:#e5e8ee;--text:#1c2333;--muted:#7a8395;
--green:#16a34a;--green-bg:#e9f9ef;--yellow:#b45309;--yellow-bg:#fdf3e7;--red:#dc2626;--red-bg:#fdecec;
--gray:#7a8395;--gray-bg:#f1f3f7;--accent:#2f5fe0;--accent-bg:#eef2fe;--row-hover:#f7f9fc}
*{box-sizing:border-box}
body{font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
margin:0;background:var(--bg);color:var(--text);padding:24px 28px}
.wrap{max-width:1180px;margin:0 auto}
.pool-header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;
margin-bottom:16px}
.pool-header h2{margin:0;font-size:20px;font-weight:700;letter-spacing:.2px}
.pool-header h2 .dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--green);
margin-right:8px;vertical-align:1px}
.pool-header .ts{font-size:12.5px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;
box-shadow:0 1px 3px rgba(16,24,40,.05)}
table{width:100%;border-collapse:collapse}
thead th{background:#fafbfd;border-bottom:2px solid var(--border);text-align:left;padding:11px 14px;
font-size:12px;font-weight:600;color:var(--muted);cursor:pointer;white-space:nowrap;user-select:none;
letter-spacing:.3px}
thead th:hover{background:#f0f3f8;color:var(--accent)}
tbody td{padding:10px 14px;border-bottom:1px solid #f0f2f6;font-size:13px;vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:var(--row-hover)}
tr.grp td{background:#eef2fb;color:var(--accent);font-weight:600;cursor:pointer;font-size:12.5px;
letter-spacing:.3px;user-select:none;border-bottom:1px solid #e2e8f4}
tr.grp:hover td{background:#e4ecfa}
tr.grp .caret{display:inline-block;width:14px}
td.name{font-weight:600}
td.name .dim{color:var(--muted);font-weight:400;font-size:12px}
td.ver{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;color:#4b5563}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr.evalrow td{background:#fafbfd;font-size:12px;padding:5px 14px;border-bottom:1px solid #f0f2f6}
#iters .evit{display:none}
#iters.f-eval .evit{display:inline}
#iters thead th{position:sticky;top:0;z-index:1}
.badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:12px;font-weight:600;line-height:18px;white-space:nowrap}
.b-green{background:var(--green-bg);color:var(--green)}
.b-yellow{background:var(--yellow-bg);color:var(--yellow)}
.b-red{background:var(--red-bg);color:var(--red)}
.b-gray{background:var(--gray-bg);color:var(--gray)}
.b-accent{background:var(--accent-bg);color:var(--accent)}
.pill{display:inline-block;margin-left:6px;padding:1px 8px;border-radius:6px;font-size:11px;font-weight:600;
background:var(--yellow-bg);color:var(--yellow);vertical-align:1px}
.err{color:var(--red);font-size:12px;word-break:break-all}
.muted{color:var(--muted);font-size:12px}
.foot{margin:16px 2px 0;font-size:12.5px;color:var(--muted);line-height:1.8}
.foot b{color:#59606f}
@media (max-width:900px){body{padding:14px}thead th,tbody td{padding:8px 10px}}
</style></head>
<body><div class="wrap">
<div class="pool-header"><h2><span class="dot"></span>节点池监控</h2>
<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
${activeFlow ? `<span class="badge b-accent">当前训练流: ${activeFlow.dir}（${activeFlow.lines} 条 · 更新于 ${fmtTs(activeFlow.mtimeMs)}）</span>` : `<span class="badge b-gray">无活跃训练流</span>`}
<span class="ts" id="status">本次刷新 ${nowStr}</span>
<select id="interval" style="font-size:12px;padding:2px 6px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--text);cursor:pointer">
<option value="60">1 分钟</option>
<option value="300" selected>5 分钟</option>
<option value="600">10 分钟</option>
<option value="1800">30 分钟</option>
</select>
<button onclick="location.reload()" style="font-size:12px;padding:2px 10px;border:1px solid var(--border);border-radius:6px;background:var(--accent-bg);color:var(--accent);cursor:pointer;font-weight:600">⟳ 立即刷新</button>
</div></div>
<div class="card"><table id="pool">
<thead><tr>
<th onclick="sortTbl(0,this)">节点</th><th onclick="sortTbl(1,this)">状态</th><th onclick="sortTbl(2,this)">规格</th>
<th onclick="sortTbl(3,this)">版本</th><th onclick="sortTbl(4,this)">ping</th><th onclick="sortTbl(5,this)">已结算局</th>
<th onclick="sortTbl(6,this)">失败局</th><th onclick="sortTbl(7,this)">上轮贡献度</th><th onclick="sortTbl(8,this)">平均耗时</th>
<th onclick="sortTbl(9,this)">最近成功</th><th onclick="sortTbl(10,this)">最近错误</th>
</tr></thead>
<tbody>${rows.join(String.fromCharCode(10))}</tbody>
${
  disabledRows.length > 0
    ? `<tbody id="disabledBody">
<tr id="disabledToggle" class="grp" onclick="toggleDisabled()" title="点击展开/折叠"><td colspan="11"><span id="disabledCaret" class="caret">▸</span>&nbsp;已禁用节点（${disabledRows.length}）</td></tr>
${disabledRows.join(String.fromCharCode(10)).replaceAll('<tr>', '<tr class="drow">')}
</tbody>`
    : ''
}
</table></div>
${iterTableHtml}
<script>
function sortTbl(col, th) {
  const tb = document.querySelector('#pool tbody');
  const rows = Array.from(tb.rows);
  const dir = th.dataset.dir === 'asc' ? -1 : 1;
  th.dataset.dir = dir === 1 ? 'asc' : 'desc';
  rows.sort((a, b) => {
    const av = a.cells[col].dataset.v ?? a.cells[col].innerText;
    const bv = b.cells[col].dataset.v ?? b.cells[col].innerText;
    const an = parseFloat(av), bn = parseFloat(bv);
    const cmp = !isNaN(an) && !isNaN(bn) ? an - bn : String(av).localeCompare(String(bv));
    return dir * cmp;
  });
  for (const r of rows) tb.appendChild(r);
}// 自动刷新定时器（间隔记录到 localStorage，刷新后恢复）
let _timer = null;
const INTERVAL_KEY = 'pool.refreshSec';
function readInterval() {
  try {
    const v = parseInt(localStorage.getItem(INTERVAL_KEY), 10);
    const opts = document.querySelectorAll('#interval option');
    for (let i = 0; i < opts.length; i++) if (Number(opts[i].value) === v) return v;
  } catch {}
  return parseInt(document.getElementById('interval').value, 10);
}
function _startTimer() {
  if (_timer) clearInterval(_timer);
  const sec = readInterval();
  document.getElementById('interval').value = String(sec);
  var label = sec >= 60 ? (sec/60)+' 分钟' : sec+'s';
  document.getElementById('status').textContent = '\u6bcf ' + label + ' \u81ea\u52a8\u5237\u65b0 \u00b7 \u672c\u6b21\u5237\u65b0 ' + '${nowStr}';
  _timer = setInterval(function() { location.reload(); }, sec * 1000);
}
function onIntervalChange() {
  const sel = document.getElementById('interval');
  try { localStorage.setItem(INTERVAL_KEY, sel.value) } catch {}
  _startTimer();
}
document.getElementById('interval').addEventListener('change', onIntervalChange);
_startTimer();
// 折叠 disabled 节点组：用户展开/折叠状态记录到 localStorage（pool.disableCollapsed），
// 刷新后按记录恢复。默认折叠（disabled 节点很少看）。
const DISABLED_KEY = 'pool.disableCollapsed';
function isDisabledCollapsed() {
  try { return localStorage.getItem(DISABLED_KEY) !== '0' } catch { return true }
}
function setDisabledCollapsed(v) {
  try { localStorage.setItem(DISABLED_KEY, v ? '1' : '0') } catch {}
}
function applyDisabledView() {
  const toggle = document.getElementById('disabledToggle');
  if (!toggle) return;
  const collapsed = isDisabledCollapsed();
  const rows = document.querySelectorAll('#disabledBody tr.drow');
  for (let i = 0; i < rows.length; i++) rows[i].style.display = collapsed ? 'none' : '';
  const caret = document.getElementById('disabledCaret');
  if (caret) caret.textContent = collapsed ? '▸' : '▾';
  const toggleRow = document.getElementById('disabledToggle');
  if (toggleRow) toggleRow.style.display = '';
}
function toggleDisabled() {
  setDisabledCollapsed(!isDisabledCollapsed());
  applyDisabledView();
}
applyDisabledView();
</script>
<p class="foot">状态 = 最近 10 次 rollout/eval 结算完成率（<b>健康</b>≥90% · <b>波动</b>≥70% · <b>异常</b>&lt;70%），替代单次 ping 判断；ping 列仅作实时参考。
上轮贡献度 = <b>全局最新轮</b>（所有节点成功结算的最大 it）该节点的成功局数；灰色 0 = 该节点最近贡献已落后当前轮（悬停显示其最近贡献轮次）。
平均耗时 = 每局<b>端到端服务时长</b>（agent 节点：接单→结果就绪，含 bun 冷启动 ~2s；local：子进程墙钟）——同口径可横向比；取<b>最近 50 局滑动平均</b>（口径升级/负载变化不被历史拖累；agent 侧节点升级前的历史行不参与）。
最近错误仅显示最近 1 小时内。数据源：按 mtime 自动选取最新训练流的 dist-agent-meta.jsonl（仅聚合最近活跃目录）·
${POOL_EPOCH_MS > 0 ? `<b>历史自 ${fmtTs(POOL_EPOCH_MS)} 起重新累计</b>（此后部署不再重置）` : `<b>累计全部历史</b>`}。
只读页面，不含密钥。默认按「已结算局」倒序，点击表头排序。</p>
</div></body></html>`
}
