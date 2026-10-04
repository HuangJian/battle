/**
 * persist-pool.ts — 长驻 worker 池的**纯策略**（同质入口的 mode 表 + 熔断状态机 + 空闲回收计划
 * + 任务超时控制器）
 *
 * 与 sampler-agent 分离（同 restart-guard.ts / workdir-cleanup.ts 先例）：纯函数、无 IO、无进程，
 * 单测共享。池本身（spawn / stdin / 探活）仍在 sampler-agent.ts。
 *
 * 背景（2026-09-28 a95 真机归因，见 DECISIONS §2026-09-28-goalnn-persist-prewarm-breaker）：
 * a95（Android/Termux/proot，SM7250）稳态吞吐与 mac 同量级（6 局 9–11s ≈ 1.6s/局），
 * 但**每批首批**要冷启 N 个 `bun <exporter> --serve`（bun + 整图 TS transpile + JIT + native/权重装载）：
 * 6 个同起会互踩 ⇒ 每个 ~4s、6 个 spawn 拉开 25s ⇒ **冷池 31.6s/局**（热池 1.6s/局，差 20×）。
 * 而节点每轮（尤其是掉线回线后）都是冷池 ⇒ 轮内份额只有 mac 的 ~1/9。本模块收两件事：
 *
 *   1) **同质入口**（`PERSIST_SERVE_ENTRY` = `tools/sim/serve-any.ts`）：一个池 worker 按每行的
 *      mode token 分派到任一导出器 ⇒ 池里没有「腿」。第一版是「按 kind 分种类预热」，于是盘上
 *      mtime 猜错腿时（这一轮要 eval、盘上最新的是 rollout）池满 ⇒ 那一波每一局都退回一次性
 *      spawn（a95 实测 3.1s/局 vs 对腿 1.55s/局）；换腿还得退役空闲 worker 再补 5 个
 *      （一次 ~39s）。**分工是历史包袱，不是状态问题**：两个导出器都是 `runServe(main)` 的无状态
 *      外壳（每局新建 World，模块级无可变状态），分家的原始理由（别动进 codeHash 红线的
 *      `export-rl-rollout.ts`）在 2026-08-31 之后已不成立——`codehash-files.txt` 现在把四个
 *      导出器 + serve-loop 全收进去了。同质化把「腿部对齐」整套策略（mtime 主腿 / 腾位回收 /
 *      POST 补位）**删掉**，换成恒定的「池里 N 个同质 worker」。
 *   2) `notePersistAttempt` / `persistSpawningAllowed` —— 把熔断语义收窄为**只停补位**：
 *      只有「worker 挂但一次性兜底跑通」才算池的账；`busy`（池满/停补位）是背压，不是失败；
 *      而且停补位**不再停用整池**（暖 worker 继续服务）、冷却后放一次探针（半开重臂）。
 *      这两条与云端池已定的语义一致（DECISIONS §2026-09-25-goalnn-cloud-cpu-ledger、
 *      §2026-09-25-goalnn-rollout-reap-wedge ④；后者把「熔断=停用整池」明确列为被否决项）。
 */

/** 池 worker 的入口：**同质**——一个进程按每行的 mode 分派到任一导出器。 */
export const PERSIST_SERVE_ENTRY = 'tools/sim/serve-any.ts'

/**
 * 任务要的导出器 → 送给池 worker 的 mode token（`serve-any` 每行的第一个元素）。
 *
 * 为什么要这张表：agent 侧按 mode/kind 选导出器（`export-eval-game` / `export-rl-rollout` / …），
 * 而池 worker 只有一个入口，得把「选哪个导出器」变成一条进程内的分派指令。表放这里（而不是
 * 在 serve-any.ts 里导出）是为了 agent**不导入任何导出器**——`tools/sim/**` 会拖进 World/Simulation/
 * `src/nn/**` 的整图，那是每局子进程该付的账，不该加在 agent 启动上。
 * 与 `tools/sim/serve-any.ts` 的 `SERVE_MODES` 同集由单测对拍（`tests/serve-any.test.ts`）。
 * 表的**键集本身就是准入名单**：不在这里的导出器（如 BC 的 `export-godai-bc.ts`）不可池化，
 * 走一次性 spawn（`persistModeFor` 返回 null ⇒ 调用方不送池）。
 */
export const PERSIST_MODE_BY_ENTRY: Readonly<Record<string, string>> = {
  'tools/sim/export-rl-rollout.ts': 'rollout',
  'tools/sim/export-eval-game.ts': 'eval',
  'tools/sim/export-goal-rollout.ts': 'goal',
  'tools/sim/export-intent-rollout.ts': 'intent',
}

/**
 * 这一局该送给池 worker 的 mode token；`null` = 这个导出器不进池（BC / 未知条目）。
 * 唯一准入判据：同质池的所有 worker 都只认 `SERVE_MODES` 里的 token。
 */
export function persistModeFor(entryTs: string): string | null {
  return PERSIST_MODE_BY_ENTRY[entryTs] ?? null
}

/** worker 连败到此数 → 停补位（旧实现=停用整池；见头注释）。 */
export const PERSIST_DISABLE_STREAK = 3
/** 停补位后的冷却：过了它放**一次**探针（半开重臂）；探针再失败 → 重新计时。 */
export const PERSIST_REARM_MS = 60_000

/**
 * 一次 persist 尝试的结果。`busy`/`skipped`/`taskErr` 都**不是**失败：
 *   · `busy`     = 背压（池满 / 停补位冷却）——没有 worker 可用，与池的健康无关；
 *   · `skipped`  = 没走池（`--no-persist` / 该任务不在池里）；
 *   · `taskErr`  = worker 报了「这一局错了」（`__SERVE_ERR__`），但 **worker 本身健康**
 *                  （serve-loop 捕获了 main 的异常，仍在等下一行）⇒ 不记池的账。
 *                  调用方在知道「一次性兜底的结果」之后再定：两路都挂 = 任务/数据问题（池无责，
 *                  worker 留用）；worker 挂而一次性跑通 = 两路结论不一致，池这一路可疑，按 `failed` 记。
 */
export type PersistAttempt = 'ok' | 'busy' | 'failed' | 'skipped' | 'taskErr'

export interface PersistBreaker {
  /** 连败计数：只由「worker 失败但一次性兜底跑通」累加。 */
  streak: number
  /** 停补位中：暖 worker 继续服务，只是不再新建。 */
  stopped: boolean
  /** 停补位起点（半开冷却的时间锚）。 */
  stoppedAtMs: number
}

export function newPersistBreaker(): PersistBreaker {
  return { streak: 0, stopped: false, stoppedAtMs: 0 }
}

/**
 * 记账（纯）：返回新状态，不修改入参。
 *
 * - `ok`：清账（连败与停补位一起复位）——半开探针成功也走这条。
 * - `failed`：连败 +1；到阈值 ⇒ `stopped`（停补位）。已 stopped 时**只重新计时**（退避，
 *   不叠惩罚——探针失败本就是预期内的一条分支）。
 * - `busy` / `skipped` / `taskErr`：状态不变（池满、停补位冷却、`--no-persist`、以及
 *   「这一局的任务/数据错了」都不是池的健康信号；任务级错误该不该记池的账由调用方按
 *   一次性兜底的结果决定，见 PersistAttempt 的注释）。
 */
export function notePersistAttempt(
  b: PersistBreaker,
  attempt: PersistAttempt,
  nowMs: number,
): PersistBreaker {
  if (attempt === 'ok') return newPersistBreaker()
  if (attempt !== 'failed') return b
  if (b.stopped) return { streak: b.streak, stopped: true, stoppedAtMs: nowMs }
  const streak = b.streak + 1
  return streak >= PERSIST_DISABLE_STREAK
    ? { streak, stopped: true, stoppedAtMs: nowMs }
    : { streak, stopped: false, stoppedAtMs: 0 }
}

/** 是否允许**新建** worker：正常时恒真；停补位后只在冷却到期时放行（半开探针）。 */
export function persistSpawningAllowed(b: PersistBreaker, nowMs: number): boolean {
  if (!b.stopped) return true
  return nowMs - b.stoppedAtMs >= PERSIST_REARM_MS
}

/**
 * 单任务超时控制器（纯接线，单测共享）：返回的 `cancel()` 必须在任务**结算后立刻**调用。
 *
 * 回归背景（2026-10-04 采样内存/吞吐盘点）：旧实现在 `runViaPersistWorker` 里裸 `setTimeout`，
 * 任务完成后**不 clearTimeout**。600s 后旧定时器触发时只看「worker 此刻有没有 `pending`」
 * ——于是把**新任务**误判成超时 ⇒ 健康 worker 被回收（live 日志每 ~10min 一批
 * `closed SIGTERM (was busy=true)` + 秒速重生；在飞局弃去走冷启动兜底，还会误触「连败 3 次
 * → 停补位」）。修法 = 定时器句柄可撤销 + 结算路径显式 cancel。
 */
export function armTaskTimeout(ms: number, onTimeout: () => void): { cancel: () => void } {
  const t = setTimeout(onTimeout, ms) as ReturnType<typeof setTimeout> & { unref?: () => void }
  t.unref?.()
  return { cancel: () => clearTimeout(t) }
}

/**
 * 空闲回收的默认阈值（30min，用户 2026-10-04 指令「长阈值」）：池是按需长起来的
 * （只在「无空闲 worker 且未到 workers 上限」时新建），但旧行为**只涨不回缩**；
 * 空闲超过它之后把超出 floor 的 worker 回收，换回 ~75–100MB/个的常驻内存。
 * `--pool-idle-ms 0` = 关回收。
 */
export const PERSIST_IDLE_MS = 30 * 60_000
/**
 * 空闲回收的保底 worker 数（`--pool-floor`；实际会再被 `--workers` 夹取）。
 * 为什么保底：热路径的首批局不该为「刚才闲过」付冷启——典型节点并发 4–6，
 * floor 取 4 让常见波次零冷启，只有超出 floor 的**多余**容量会被收。
 */
export const PERSIST_POOL_FLOOR = 4

/** 空闲回收计划的一个候选：`idleSinceMs = null` = 在飞（绝不回收）。 */
export interface ReapCandidate {
  idleSinceMs: number | null
}

/**
 * 空闲回收计划（纯）：返回应回收的候选**下标**。
 *
 * 纪律（2026-10-04 用户指令「floor + 长阈值」）：
 *   · 只收**空闲**且空闲时长 ≥ `idleMs` 的（在飞 `null` 永不收）；
 *   · 收完池不小于 `floor`（至少留 1：floor ≤ 0 也按 1 处理——把池收空 = 下一局无条件冷启）；
 *   · 候选多于预算时先收**最久空闲**的（最暖的留在池里）；
 *   · `idleMs <= 0` = 关回收（返回空）。
 */
export function reapPlan(
  candidates: readonly ReapCandidate[],
  opts: { floor: number; idleMs: number; nowMs: number },
): number[] {
  if (opts.idleMs <= 0) return []
  const keep = Math.max(1, Math.floor(opts.floor))
  const budget = candidates.length - keep
  if (budget <= 0) return []
  const due: { i: number; since: number }[] = []
  for (let i = 0; i < candidates.length; i++) {
    const since = candidates[i].idleSinceMs
    if (since === null) continue
    if (opts.nowMs - since >= opts.idleMs) due.push({ i, since })
  }
  if (due.length <= budget) return due.map((d) => d.i)
  due.sort((a, b) => a.since - b.since)
  return due.slice(0, budget).map((d) => d.i)
}
