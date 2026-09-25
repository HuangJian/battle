/**
 * weight-buckets.ts — 权重桶的纯逻辑（与 sampler-agent 同源的唯一实现，单测共享）。
 *
 * 为什么单独成文件：`sampler-agent.ts` 是 1500 行、启动即起服务器的单体，
 * 桶键/查找这类**判据**住在里面就没法单测（只有 boot 整只 agent 才能验）。仓库里
 * 已有同款先例（`workdir-cleanup.ts`：纯函数出列，单测直接吃）。
 *
 * ─────────────────────────────────────────────────────────────────────────
 * 多课程（2026-09-18 用户指令：「rollout 集群需缓存并行课程的最近权重，避免同一份
 * 权重多次传递」）
 *
 * 改造前：桶按 `kind` 分（'rollout' / 'intent' / 'goal'），每 kind 64 桶 LRU。
 * 单课程/单实验流下没问题（§goal-nn A2/A5 就是为「同一 kind 多训练流」加的按 sha
 * 多桶）。**多课程并行时它是错的**：5 门课每轮各产出一个新 sha，全部挤进同一个
 * `rollout` 桶 ⇒ 64 桶被 5 门课分着用，历史深度从 64 掉到 ~13 轮；再叠加「慢节点
 * 落后 2–3 轮就回来取旧权重」，撞上驱逐就是 `409 wver not cached here`（那一局作废）。
 *
 * 改造后：桶键 = **(course, kind)**。课程之间不再互相驱逐（各自 64 桶的完整历史），
 * 而「同一 kind 多 sha」的多桶语义原样保留（它解决的是另一个问题：同课内的历史 sha）。
 *
 * ─────────────────────────────────────────────────────────────────────────
 * 兼容（本轮改动**必须**能与旧调用方混跑，节点是不停机升级的）
 *
 * `course` 空串 = **旧单课程桶**。旧训练侧（不带 `X-Course`）的权重照旧落在这里，
 * 查找时按「本课桶 → 旧桶 → 任意同 kind 桶」的顺序退让 —— 最后一步是安全的，因为
 * **sha 是内容寻址的**：命中任何桶里的同一个 sha 都拿到同一份字节。于是：
 *   · 新训练侧 + 新 agent：按课程精确隔离；
 *   · 新训练侧 + 旧 agent：头被忽略，退化成改造前的单 kind 桶（可用，只是共享历史）；
 *   · 旧训练侧 + 新 agent：落旧桶，被本课查找的第二步命中（行为与改造前一致）。
 */

/** 桶键分隔符：NUL（课程名里不可能出现——`common.protocol.parse_course_arg` 拒绝空白）。 */
const SEP = '\u0000'

/** 桶键 = `course\0kind`（course 空串 = 旧单课程桶）。 */
export function bucketKey(course: string, kind: string): string {
  return `${course ?? ''}${SEP}${kind ?? ''}`
}

/** 桶键还原（观测/日志用；键非法时尽力而为，不抛）。 */
export function splitBucketKey(key: string): { course: string; kind: string } {
  const i = key.indexOf(SEP)
  if (i < 0) return { course: '', kind: key }
  return { course: key.slice(0, i), kind: key.slice(i + 1) }
}

/** 该桶键的 kind（与课程无关的那一半）。 */
export function kindOfBucketKey(key: string): string {
  return splitBucketKey(key).kind
}

/**
 * 按 sha 找权重：**本课桶 → 旧单课程桶 → 任意同 kind 桶**。
 *
 * 第三步为什么允许：sha 内容寻址 ⇒ 同一个 sha 在哪都是一份字节。它换来的是「升级
 * 顺序自由」——训练侧先带 `X-Course` 而后端还是旧桶（或反过来）都不会 409。
 */
export function findSha<T>(
  buckets: ReadonlyMap<string, ReadonlyMap<string, T>>,
  course: string,
  kind: string,
  sha: string,
): T | null {
  if (!sha) return null
  const exact = buckets.get(bucketKey(course, kind))?.get(sha)
  if (exact !== undefined) return exact
  if (course !== '') {
    const legacy = buckets.get(bucketKey('', kind))?.get(sha)
    if (legacy !== undefined) return legacy
  }
  for (const [key, bucket] of buckets) {
    if (kindOfBucketKey(key) !== kind) continue
    const hit = bucket.get(sha)
    if (hit !== undefined) return hit
  }
  return null
}

/**
 * 某 kind 的「最新」权重（跨课程取最后插入的那个）。
 *
 * 语义与改造前一致，只服务 intent/goal（单训练流的评估类调用方）——它们的桶本来就是
 * 一条流，跨课程取最新不会串。rollout 的取用一律走 `findSha(wver)`（按 sha 精确命中），
 * 不用「最新」这种相对语义。
 */
export function latestOfKind<T>(
  buckets: ReadonlyMap<string, ReadonlyMap<string, T>>,
  kind: string,
): T | null {
  let latest: T | null = null
  for (const [key, bucket] of buckets) {
    if (kindOfBucketKey(key) !== kind) continue
    for (const ws of bucket.values()) latest = ws // Map 保持插入序，最后一个 = 最新
  }
  return latest
}
