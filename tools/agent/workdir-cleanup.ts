/**
 * workdir-cleanup.ts — sampler-agent 工作目录（tmp/dist-agent）的磁盘收敛纯函数
 *
 * 与 sampler-agent 分离（同 restart-guard.ts 先例）：纯函数、无 IO，单测共享。
 *
 * 背景（2026-09-08 用户指令）：agent 长期运行后 tmp/dist-agent 累积 ~150 个权重
 * 文件（~54MB）。根因：v3.7 kind 分桶后权重文件名改为 weights-<kind>-<sha16>.json，
 * 而旧清扫正则是 /^weights-[0-9a-f]{16}\.json$/（无 kind 段）——永远匹配不到，
 * sweepWeightFiles 静默空操作，磁盘只留最新 4 份的上限从未生效（内存桶逐出只在
 * 单进程代内生效，每次 /v1/restart 后桶清空重新累积）。另：进程被强杀会留下
 * game-<pid>-<seq> 半成品目录与陈旧 agent.pid/agent-child.pid（finally 清理无法
 * 在 SIGKILL 时执行）。
 *
 * 本模块提供两个纯计划函数（只算"删哪些"，执行交给调用方）：
 *   1) sweepWeightFilePlan —— 每 kind 按 mtime 降序保留最新 keep 份；被在飞权重桶
 *      引用的文件永不删（删掉正在被 /v1/task 消费的权重 = 该局 409/失败）。
 *   2) staleOrphanPlan —— 删除 mtime 早于 staleMs 的 game-* 孤儿目录与
 *      agent.pid/agent-child.pid。年龄门防误删重启交接窗口内父进程的在飞局目录
 *      （/v1/restart：父进程 spawn 子进程后 ~500ms 才退出，在飞局最长 ~480s）。
 */
export const WEIGHT_FILES_KEEP = 4
/** 孤儿判定年龄门：覆盖「父进程在飞局 + 子进程启动」交接窗口（见头注释）。 */
export const STALE_ORPHAN_MS = 5 * 60_000
/** 权重文件名：weights-<kind>-<16hex>.json（v3.7 kind 分桶命名）。 */
export const WEIGHT_RE = /^weights-[a-z]+-[0-9a-f]{16}\.json$/
/** /v1/restart 链的运维 kill 参考文件（见 sampler-agent）。 */
const PID_FILES = ['agent.pid', 'agent-child.pid']
/** 孤儿游戏目录：game-<pid>-<seq>（runGame 的 finally 清理被杀进程残留）。 */
const GAME_RE = /^game-\d+-\d+$/

export interface DirEntry {
  name: string
  mtimeMs: number
}

/** 每 kind 保留最新 keep 份；liveFiles（在飞权重桶引用的文件名）永不删。 */
export function sweepWeightFilePlan(
  entries: DirEntry[],
  liveFiles: ReadonlySet<string>,
  keep = WEIGHT_FILES_KEEP,
): string[] {
  const counts = new Map<string, number>()
  const out: string[] = []
  const sorted = entries
    .filter((e) => WEIGHT_RE.test(e.name) && !liveFiles.has(e.name))
    .sort((a, b) => b.mtimeMs - a.mtimeMs)
  for (const e of sorted) {
    const kind = e.name.split('-')[1] ?? ''
    const seen = counts.get(kind) ?? 0
    if (seen >= keep) out.push(e.name)
    else counts.set(kind, seen + 1)
  }
  return out
}

/** mtime 早于 staleMs 的 game-* 孤儿目录与陈旧 pid 文件 → 删除名单。 */
export function staleOrphanPlan(
  entries: DirEntry[],
  nowMs: number,
  staleMs = STALE_ORPHAN_MS,
): { games: string[]; pids: string[] } {
  const games: string[] = []
  const pids: string[] = []
  for (const e of entries) {
    if (nowMs - e.mtimeMs < staleMs) continue
    if (GAME_RE.test(e.name)) games.push(e.name)
    else if (PID_FILES.includes(e.name)) pids.push(e.name)
  }
  return { games, pids }
}
