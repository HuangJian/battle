/**
 * cores.ts — 「本机到底有多少核」的**唯一口径**（TS 侧镜像 `nn-training/platform_utils.py`
 * 的 `effective_cores`，两边必须同口径：谁在容器里都按**配额**算，不按宿主机报的大数字算）。
 *
 * 为什么需要它（2026-09-25 云机 rollout 卡死取证，见 docs/nn/runtime-opt.md §23）：
 * 容器里的 `os.cpus().length` / `os.cpu_count()` 报的是**宿主机**的逻辑核数 —— Kaggle 的 TPU
 * 会话实测报 224，而 cgroup 只给 **96** 核 ⇒ 按 224 派并发就是 2.3× 超订，单局墙钟被推过
 * 5s 看门狗硬顶，成批「超时 → 回退 → 补位」把整轮 rollout 拖停。
 *
 * 三个来源的优先级**不是风格问题**：cgroup 配额（限流）与亲和掩码（cpuset 绑核）是这台机器
 * 给的事实，取两者的**小值**；宿主机核数是最不可信的一个，只配当最后的兜底
 * （Windows / macOS / 裸机 / 无 cgroup 的容器）。
 *
 * 消费方：`tools/lib/worker-pool.ts`（physicalCores）、`tools/agent/sampler-agent.ts`
 * （CPUS）、`tools/sim/perf-cmp-rollout.ts`、`dashboard/src/core/venv.ts`（torch 线程数）。
 */
import { readFileSync } from 'node:fs'
import * as os from 'node:os'

const CGROUP_V2_CPU_MAX = '/sys/fs/cgroup/cpu.max'
const CGROUP_V1_QUOTA = '/sys/fs/cgroup/cpu/cpu.cfs_quota_us'
const CGROUP_V1_PERIOD = '/sys/fs/cgroup/cpu/cpu.cfs_period_us'
const CGROUP_V1_PERIOD_DEFAULT = 100_000
const PROC_SELF_STATUS = '/proc/self/status'

/** 读一个内核伪文件；读不到/无权限/空文件 ⇒ null（诊断用途，绝不抛）。 */
function readText(path: string): string | null {
  try {
    const t = readFileSync(path, 'utf8').trim()
    return t.length > 0 ? t : null
  } catch {
    return null
  }
}

/**
 * v2：`/sys/fs/cgroup/cpu.max` 的解析（`"9600000 100000"`；`"max 100000"` = 不限）。
 * quota÷period 向上取整；不限/垃圾/缺字段 ⇒ null。
 */
export function parseCgroupV2CpuMax(raw: string | null): number | null {
  if (raw === null) return null
  const [quotaStr, periodStr] = raw.trim().split(/\s+/)
  if (quotaStr === undefined || quotaStr === 'max') return null
  const quota = Number.parseInt(quotaStr, 10)
  const period = Number.parseInt(periodStr ?? '', 10)
  if (!Number.isFinite(quota) || !Number.isFinite(period) || quota <= 0 || period <= 0) return null
  return Math.max(1, Math.ceil(quota / period))
}

/**
 * v1：`cpu.cfs_quota_us` ÷ `cpu.cfs_period_us`（`-1`/`0` = 不限）；period 缺省 100000。
 */
export function parseCgroupV1Quota(
  quotaRaw: string | null,
  periodRaw: string | null,
): number | null {
  const quota = Number.parseInt((quotaRaw ?? '').trim(), 10)
  if (!Number.isFinite(quota) || quota <= 0) return null
  const period = Number.parseInt((periodRaw ?? '').trim(), 10)
  const periodEff = Number.isFinite(period) && period > 0 ? period : CGROUP_V1_PERIOD_DEFAULT
  return Math.max(1, Math.ceil(quota / periodEff))
}

/**
 * `/proc/self/status` 里 `Cpus_allowed_list` 的解析（`"0-95"`、`"0-3,8-11"`）。
 *
 * Node/Bun 没有暴露 `sched_getaffinity`，所以读这个伪文件 —— 得到的是**同一个事实**
 * （python 侧走 `len(os.sched_getaffinity(0))`）。取不到/格式坏 ⇒ null。
 */
export function parseCpusAllowedList(statusText: string | null): number | null {
  if (statusText === null) return null
  const line = statusText.split('\n').find((l) => l.startsWith('Cpus_allowed_list:'))
  if (line === undefined) return null
  const spec = line.slice('Cpus_allowed_list:'.length).trim()
  if (spec.length === 0) return null
  let n = 0
  for (const part of spec.split(',')) {
    const [loStr, hiStr] = part.split('-')
    const lo = Number.parseInt(loStr ?? '', 10)
    if (!Number.isFinite(lo)) return null
    const hi = hiStr === undefined ? lo : Number.parseInt(hiStr, 10)
    if (!Number.isFinite(hi) || hi < lo) return null
    n += hi - lo + 1
  }
  return n > 0 ? n : null
}

/** 容器 cgroup 允许的 CPU 核数；不限/读不到 ⇒ null。 */
export function cgroupCpuQuota(): number | null {
  const v2 = parseCgroupV2CpuMax(readText(CGROUP_V2_CPU_MAX))
  if (v2 !== null) return v2
  return parseCgroupV1Quota(readText(CGROUP_V1_QUOTA), readText(CGROUP_V1_PERIOD))
}

/** 进程亲和掩码允许的核数（Linux）；无此文件/取不到 ⇒ null。 */
export function affinityCores(): number | null {
  return parseCpusAllowedList(readText(PROC_SELF_STATUS))
}

/**
 * 宿主机报出来的逻辑核数（**仅兜底**）：优先 `os.availableParallelism()`，它在新运行时里
 * 自己会读 cgroup/亲和掩码；取不到才是 `os.cpus().length` 这个「宿主机裸数」。
 */
export function hostLogicalCores(): number {
  const avail = (os as unknown as { availableParallelism?: () => number }).availableParallelism?.()
  if (typeof avail === 'number' && Number.isFinite(avail) && avail >= 1) return Math.floor(avail)
  const n = os.cpus().length
  return Number.isFinite(n) && n >= 1 ? n : 1
}

/**
 * 优先级决策（纯函数，便于用例钉住）：配额与亲和掩码取**小值**，都没有才回宿主机核数。
 */
export function resolveEffective(signals: Array<number | null>, host: number): number {
  const usable = signals.filter((n): n is number => typeof n === 'number' && n > 0)
  if (usable.length > 0) return Math.max(1, Math.min(...usable))
  return Math.max(1, host)
}

/**
 * 本进程**真正能用**的核数（单一口径）。
 *
 * 与 `nn-training/platform_utils.py::effective_cores` 逐条同源——改一边就得改另一边
 * （Kaggle 上 224 vs 96 的读数就是这条口径的考题）。
 */
export function effectiveCores(): number {
  return resolveEffective([cgroupCpuQuota(), affinityCores()], hostLogicalCores())
}
