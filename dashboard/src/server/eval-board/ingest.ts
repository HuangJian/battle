/** ingest.ts — W1 read-through 入账：引擎身份、课程 ↔ rung 映射、eval_log 行入库。 */
import { existsSync, readdirSync, readFileSync, statSync } from 'fs'
import path from 'path'
import { REPO_ROOT as EVAL_REPO_ROOT } from '../../../../tools/agent/codehash-files'
import { REPO_ROOT, tmpLogsDir } from '../../core/paths'
import { computeEngineEpoch, gitCommit } from '../../evalboard/engine'
import { type IngestCtx, ingestRows, type RawEvalRow } from '../../evalboard/ingest'
import { gamesDir, loadDedupKeys, type EvalGameRow } from '../../evalboard/store'
import { evalDataRoot, trackedLadder } from './ladder-data'
import { readTailDrained, type TailPos } from './tail-read'

// ────────────────────────── W1 read-through 入账 ──────────────────────────

let engineMemo: EvalGameRow['engine'] | null = null
function engineOfConsole(): EvalGameRow['engine'] {
  if (!engineMemo) {
    // 惰性一次：tree walk 秒级，memo 后零成本。engine_epoch = sha256(codeHash)[0:16]，
    // dist_codehash 与节点门同值（2026-09-17 统一事实来源，诊断一眼可比）。
    try {
      const { engine_epoch, codeHash } = computeEngineEpoch()
      engineMemo = { git_commit: gitCommit(EVAL_REPO_ROOT), dist_codehash: codeHash, engine_epoch }
    } catch {
      engineMemo = { git_commit: 'unknown', dist_codehash: '', engine_epoch: 'unknown' }
    }
  }
  return engineMemo
}

let rungIdsMemo: Set<string> | null = null
function ladderRungIds(): Set<string> {
  if (!rungIdsMemo) {
    try {
      rungIdsMemo = new Set(trackedLadder().map((r) => r.id))
    } catch {
      rungIdsMemo = new Set()
    }
  }
  return rungIdsMemo
}

/**
 * 课程 → 阶梯 rung id（`c<count>l<lives>`）。
 *
 * A 层 eval 行的 `stage` 是课程自定义关 id（如 2000），与阶梯 rung id（`c4l1`）不同名；
 * 若按 `stage-<id>` 落账，这些行永远 join 不上阶梯表（console 侧 `x.rung === r.id`），
 * 面板恒空。课程几何（count/lives）与 rung 一一对应，故按 count/lives 反查。
 * 无对应 rung 的课程（如 c5-margin）返回 null → 调用方回退 `stage-<id>`。
 */
export interface CourseRungMeta {
  /** 匹配到的阶梯 rung id；无对应 rung（如 c5-margin）为 null。 */
  rung: string | null
  /** 课程**实际**跑关参数 —— probe_key 的三个可比性分量必须取自这里，不能硬编码。 */
  maxTicks: number
  mapHash: string
}

/**
 * 课程 → 阶梯 rung 元信息（rung id / maxTicks / mapHash）。
 *
 * `probe_key = <rung>-<difficulty>-t<maxTicks>-<mapHash>-<seedSpace>` 是 §3.5 的
 * **可比性键**，三个分量都必须来自课程实跑参数：
 * - `maxTicks` 取自课程 jsonc（历史课程是 2400，阶梯 rung 是 12000 —— 两者不可比，
 *   硬编码会让 A 行冒充 t12000 数据，跨档相减不被断言拦截）；
 * - `mapHash` 取自阶梯 rung（原实现缺省 `mapHashOfStage` → 全部落成 `unknown`，
 *   等于把"可比性键"退化成常量）。
 */
export function courseRungMeta(course: string): CourseRungMeta | null {
  if (!course) return null
  try {
    const p = path.join(REPO_ROOT, 'nn-training', 'curricula', `${course}.jsonc`)
    if (!existsSync(p)) return null
    const text = readFileSync(p, 'utf-8').replace(/\/\/.*$/gm, '')
    const count = Number(text.match(/"count"\s*:\s*(\d+)/)?.[1])
    const lives = Number(text.match(/"lives"\s*:\s*(\d+)/)?.[1])
    const maxTicks = Number(text.match(/"max_ticks"\s*:\s*(\d+)/)?.[1])
    const id = Number.isInteger(count) && Number.isInteger(lives) ? `c${count}l${lives}` : null
    const rung = id && ladderRungIds().has(id) ? id : null
    let mapHash = 'unknown'
    if (rung) {
      mapHash = trackedLadder().find((r) => r.id === rung)?.mapHash ?? 'unknown'
    }
    return { rung, maxTicks: Number.isInteger(maxTicks) ? maxTicks : 12000, mapHash }
  } catch {
    return null
  }
}

/** 便捷包装：只要 rung id（无对应 rung 返回 null）。 */
export function courseRungId(course: string): string | null {
  return courseRungMeta(course)?.rung ?? null
}

// ──────────────────── 增量入账：按字节偏移读 eval_log（2026-09-22） ────────────────────

/** 已消费到的**字节偏移**（进程内 memo）。键 = `<数据根>|<日志绝对路径>`。
 *
 * 空 memo（控制台刚启动）时第一遍仍是全量读——这是**幂等去重的兜底**，不是浪费：
 * 上一会话之后的全部新增就在这里补上；此后每次只剩新增尾巴。
 *
 * store 是 append-only 账本（store.ts 头注）且控制台没有清库路径，故不需要为「库被清空」
 * 重置偏移；真要重放，重启控制台（或换 `EVALBOARD_DATA` 到新目录 ⇒ 键跟着变）即可。 */
const ingestPositions = new Map<string, TailPos>()

// ──────────────────── 去重键集 memo（避免每次未命中重扫整个 store） ────────────────────

let dedupMemo: { root: string; fingerprint: string; keys: Set<string> } | null = null

/** store 分片指纹（`name|size|mtimeMs`；只 stat，不读内容）。 */
function storeFingerprint(root: string): string {
  const dir = gamesDir(root)
  try {
    return readdirSync(dir)
      .filter((f) => f.endsWith('.jsonl'))
      .sort()
      .map((f) => {
        const st = statSync(path.join(dir, f))
        return `${f}:${st.size}:${st.mtimeMs}`
      })
      .join('|')
  } catch {
    return ''
  }
}

/**
 * 去重键集（跨调用复用）。
 *
 * `loadDedupKeys` 要读**整个 store**（全部课程、全部月份），而它只在「真有新行要入账」时
 * 才有意义——每次缓存未命中都重扫一遍是纯浪费。指纹不含内容，外部进程改过 store
 * （回填 CLI / 另一个控制台）指纹即变 → 自动重载。
 */
export function knownKeysFor(root: string): Set<string> {
  const fp = storeFingerprint(root)
  if (dedupMemo && dedupMemo.root === root && dedupMemo.fingerprint === fp) return dedupMemo.keys
  dedupMemo = { root, fingerprint: fp, keys: loadDedupKeys(root) }
  return dedupMemo.keys
}

/**
 * 课程 eval_log 的候选路径（真实布局优先；legacy `traj/` 保留兼容）。
 *
 * 导出是给**视图输入指纹**用的：`eval_log` 的新行是「入账」的触发源，它必须出现在输入清单里，
 * 否则视图会命中缓存、根本不跑入账 ⇒ 新评估永远不上屏（两个候选都要列：日志后来才出现时
 * 指纹也得变）。
 */
export function courseEvalLogCandidates(course: string): [string, string] {
  return [
    path.join(tmpLogsDir(), course, 'eval_log.jsonl'),
    path.join(tmpLogsDir(), course, 'traj', 'eval_log.jsonl'),
  ]
}

/** 课程 eval_log 实际路径（两个候选都不存在 ⇒ null）。 */
export function courseEvalLogPath(course: string): string | null {
  if (!course) return null
  return courseEvalLogCandidates(course).find((p) => existsSync(p)) ?? null
}

/** 课程 eval_log.jsonl（训练落盘处，tmp 缓冲）→ EvalStore。返回新入账行数。 */
export function ingestCourseEvalLog(course: string): number {
  if (!course) return 0
  // 与课程发现同源（tmpLogsDir）：BCITY_TMP_LOGS_DIR 重定向时两侧一起走，不再只重定向一半。
  // 真实布局优先；legacy `traj/` 候选保留兼容（两者都不在则 0）。
  const evalLog = courseEvalLogPath(course)
  if (!evalLog) return 0
  const root = evalDataRoot()
  const posKey = `${root}|${evalLog}`
  const tail = readTailDrained(evalLog, ingestPositions.get(posKey))
  // 消费即记账（与入账成败无关）：坏行/非 eval 行本来就只跳过，不该每拍重读。
  ingestPositions.set(posKey, tail.next)
  if (tail.lines.length === 0) return 0
  const raws: RawEvalRow[] = []
  for (const line of tail.lines) {
    try {
      const r = JSON.parse(line) as RawEvalRow
      if (r.event === 'eval') raws.push(r)
    } catch {
      /* 坏行跳过 */
    }
  }
  if (raws.length === 0) return 0
  // 课程 → 阶梯 rung 元信息（A 行 stage 是自定义关 id，必须映射；maxTicks/mapHash
  // 取自课程实跑参数，不得硬编码，否则 probe_key 这个可比性键失真）。
  const meta = courseRungMeta(course)
  const rung = meta?.rung ?? null
  const rungOfStage = (s: string | number): string => rung ?? `stage-${s}`
  const ctx: IngestCtx = {
    run_id: course,
    course,
    batch_id: `A-${course}`,
    batch_of: 1,
    rungOfStage,
    engine: engineOfConsole(),
    source: 'A',
    ckpt_path: '',
    ckpt_sha16: '',
    init_sha16: '',
    difficulty: 'hard',
    maxTicks: meta?.maxTicks ?? 12000,
    mapHashOfStage: () => meta?.mapHash ?? 'unknown',
  }
  // wver/iter 逐行透传：ingestEvalRow 从 raw 取 iter/wver（A 行自带）。
  const { appended } = ingestRows(root, raws, ctx, knownKeysFor(root))
  // 我们自己刚 append 过 → 重取一次指纹（stat 级），否则下一次调用会被自己的写入打成失效。
  if (dedupMemo && dedupMemo.root === root) dedupMemo.fingerprint = storeFingerprint(root)
  return appended
}
