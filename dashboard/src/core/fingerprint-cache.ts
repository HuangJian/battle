/** fingerprint-cache.ts — 「输入指纹」缓存原语：判据是**数据变了没**，不是时间过了多久。
 *
 *  为什么需要它（2026-09-22，评估板视图缓存那次教训）：TTL 缓存把**时间**当数据的代理，
 *  于是两个方向都错 —— 没变也重算（白付），变了却可能拿旧值。实测评估板：2 万行单课一次
 *  合成 22–85ms，而 `/eval` 页与首页摘要是 300s 轮询 ⇒ 旧口径（30s TTL）等于每轮白付一次；
 *  反过来新评估行/新批次进来又可能被 30s 窗口挡住不上屏。换成输入指纹后：命中 0.044ms、
 *  变了**立即**上屏。
 *
 *  语义（三条）：
 *    ① 指纹相同 **且** 距上次重算未超 `backstopMs` → 复用上次结果（**同一个对象实例**，
 *       调用方可以拿实例身份当「真的没重算」的证据）；
 *    ② 指纹变了 → **立即**重算，不等任何窗口（这是本原语存在的全部意义）；
 *    ③ `backstopMs` 到点 → 重算一次（见下「兵底」）。
 *
 *  `backstopMs` **不是 TTL**：它唯一的职责是兜住「指纹清单将来漏了某个输入」时的**陈旧上限**
 *  —— 所以取值要比业务里任何轮询间隔都长（评估板取 600s，而页轮询是 300s/15s）。它是安全网，
 *  不是节流阀；把调小当「让数据更新鲜」用，就退回了 TTL。
 *
 *  ## 选型：什么时候**不该**用它（判据写在这里，省得下一个缓存再重新推一遍）
 *    · **采样**（输入是活体观测，无法指纹化）：节点 ping / 共享 hub HTTP / python 扫全部
 *      tmp 账本 / 机群探活 → 用 `createSwrCache`：那里的 `freshMs` 表达的是「多久探一次」，
 *      不是「数据多久算新」（`POOL_TTL_MS` / `OVERVIEW_TTL_MS` / `LOOP_QUEUE_TTL_MS` /
 *      `SNAPSHOT_REFRESH_MS` 都是这一类，别去给它们编指纹）；
 *    · **失败兜底**（时间就是它要表达的东西）：busy 键 5min 自解锁、坏配置 1s 重试窗口；
 *    · **内容摘要**（`codeHash` / `engine_epoch`：输入就是全部内容）→ 指纹不可能比计算本身
 *      便宜，没有收益（现状的 memo + 时间窗是**故意**的）；
 *    · **重算体本身很便宜**（毫秒级、且不需要预热）：加缓存只是加复杂度。
 *
 *  ## 与 `swr-cache` 的分工
 *  本原语是**同步**的：指纹化输入 = 本地可枚举的文件/内存事实，重算 = 纯计算（评估板视图就是
 *  这样）。要等子进程/网络的场合请用 `createSwrCache`（它管单飞、后台重算、代际不回落）。
 */

import { statSync } from 'fs'

/** 输入指纹缓存（按 `key` 分条）。 */
export interface FingerprintCache<T> {
  /** 取数（只用值、不关心这次是命中还是重算）。 */
  get: (key: string, compute: () => T) => T
  /** 取数 + 告诉调用方**这次是不是命中**（命中时 `compute` 没跑、值是同一实例）。
   *  想对「命中」这一事实做点事时用（例：评估板命中时把 `ingested` 归 0）。 */
  getWithStatus: (key: string, compute: () => T) => { val: T; hit: boolean }
  /** 只读诊断：该键此刻会命中吗（不重算、不记账 —— 不计入 stats）。 */
  peek: (key: string) => boolean
  /** 作废：不给 key = 整表；给 key = 单条。下一次读必重算。 */
  invalidate: (key?: string) => void
  /** 清空整表 + 诊断计数。 */
  clear: () => void
  /** 诊断计数（命中 / 重算 / 兵底重算）——「缓存到底有没有在干活」的可观测面。 */
  stats: () => { hits: number; misses: number; backstops: number }
}

export interface FingerprintCacheOpts {
  /** 该键当下的输入指纹：同键下它变了就必须重算（由调用方提供 —— 它知道自己的输入）。
   *
   *  ★ `key` 会被喂给 `signature`，所以 **key 必须是业务身份本身**（如课程名），
   *  不要用装饰过的字符串（如 `evalboard:<course>`）：那样指纹会算到一把不存在的身份上
   *  ——如果它恰好恒定（例：不存在的课程永远没有行），就变成「永不重算」的静默错误。 */
  signature: (key: string) => string
  /** 兵底：距上次重算超过它，即便指纹相同也重算一次（防指纹清单漏项）。不是 TTL。 */
  backstopMs: number
  /** 时钟（默认 `Date.now`；测试注入可控时钟，避免 sleep）。 */
  now?: () => number
}

export function createFingerprintCache<T>(opts: FingerprintCacheOpts): FingerprintCache<T> {
  const now = opts.now ?? Date.now
  const entries = new Map<string, { at: number; sig: string; val: T }>()
  let hits = 0
  let misses = 0
  let backstops = 0
  const getWithStatus = (key: string, compute: () => T): { val: T; hit: boolean } => {
    const ent = entries.get(key)
    if (ent) {
      const same = opts.signature(key) === ent.sig
      if (same) {
        if (now() - ent.at < opts.backstopMs) {
          hits++
          return { val: ent.val, hit: true }
        }
        backstops++
      } else {
        misses++
      }
    } else {
      misses++
    }
    const val = compute()
    // 指纹在 compute **之后**再取一次：重算体自己可能刚写了输入（入账就是这种）
    entries.set(key, { at: now(), sig: opts.signature(key), val })
    return { val, hit: false }
  }
  return {
    get: (key, compute) => getWithStatus(key, compute).val,
    getWithStatus,
    peek(key) {
      const ent = entries.get(key)
      return !!ent && opts.signature(key) === ent.sig && now() - ent.at < opts.backstopMs
    },
    invalidate(key) {
      if (key === undefined) entries.clear()
      else entries.delete(key)
    },
    clear() {
      entries.clear()
      hits = 0
      misses = 0
      backstops = 0
    },
    stats: () => ({ hits, misses, backstops }),
  }
}

// ────────────────────────── 输入清单指纹的公共写法 ──────────────────────────

/** 文件指纹 `size:mtimeMs`（缺失 = `-`）；**只 stat，不读内容**。 */
export function fileSignature(p: string): string {
  try {
    const st = statSync(p)
    return `${st.size}:${st.mtimeMs}`
  } catch {
    return '-'
  }
}

/**
 * 一组 `(标签, 路径)` → 拼接指纹。
 *
 * 清单式输入的标准写法（评估板视图就是这么用的）；标签是用来**读日志/调试**的，路径变化本身
 * 也会体现在 `size:mtimeMs` 里。**清单必须含「触发副作用的那份输入」**（评估板踩过：`eval_log`
 * 是入账的触发源，漏了它就会命中缓存、根本不跑入账 ⇒ 新数据永不上屏）。
 */
export function filesSignature(files: Array<[string, string]>): string {
  return files.map(([label, p]) => `${label}=${fileSignature(p)}`).join('\u0001')
}
