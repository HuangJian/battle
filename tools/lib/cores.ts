/**
 * cores.ts — 「本机到底有多少核」的**唯一口径**（TS 侧镜像 `nn-training/common/platform_utils.py`
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
 *
 * 2026-10-03 起本文件还提供**第二个**口径 `physicalCores()`（门禁 / 本地 worker 池用；
 * 组合规则与 `effectiveCores` 同构，但基准是**真物理核**而不是逻辑核）。两者的分工见
 * `physicalCores` 的 docstring 与 python 侧 `common.platform_utils.physical_cores`。
 */
import { spawnSync } from 'node:child_process'
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
 * 与 `nn-training/common/platform_utils.py::effective_cores` 逐条同源——改一边就得改另一边
 * （Kaggle 上 224 vs 96 的读数就是这条口径的考题）。
 */
export function effectiveCores(): number {
  return resolveEffective([cgroupCpuQuota(), affinityCores()], hostLogicalCores())
}

/**
 * `/proc/cpuinfo` 文本 → **物理核数**（`(physical id, core id)` 的**唯一对**数）。
 *
 * 超线程的 sibling 各占一段 `processor : N`，同一物理核的几段共享同一个对；故唯一对数 =
 * 物理核数。ARM 等平台可能缺这两行 ⇒ null（拿不到事实，不是 0、也不假装 1）。
 * 与 python 侧 `common.platform_utils.parse_cpuinfo_physical_cores` 逐字同源。
 */
export function parseProcCpuinfoPhysicalCores(text: string): number | null {
  const pairs = new Set<string>()
  let phys: string | null = null
  let core: string | null = null
  const afterColon = (line: string): string => {
    const i = line.indexOf(':')
    return i < 0 ? '' : line.slice(i + 1).trim()
  }
  const flush = (): void => {
    if (phys !== null && core !== null) pairs.add(`${phys}/${core}`)
    phys = null
    core = null
  }
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (line.startsWith('processor')) flush()
    else if (line.startsWith('physical id')) phys = afterColon(line) || null
    else if (line.startsWith('core id')) core = afterColon(line) || null
  }
  flush()
  return pairs.size > 0 ? pairs.size : null
}

/** 物理核数的进程内缓存（`undefined` = 还没探测过；`null` = 探测失败）。 */
let hostPhysicalCache: number | null | undefined

function detectHostPhysicalCores(): number | null {
  const num = (out: string | undefined): number | null => {
    const n = Number.parseInt((out ?? '').trim().split(/\s+/)[0] ?? '', 10)
    return Number.isFinite(n) && n > 0 ? n : null
  }
  try {
    if (process.platform === 'darwin') {
      return num(
        spawnSync('sysctl', ['-n', 'hw.physicalcpu'], { encoding: 'utf8', timeout: 5_000 }).stdout,
      )
    }
    if (process.platform === 'win32') {
      // 仓库内 PowerShell 调用一律 pwsh（AGENTS §17.7 / DECISIONS §323；裸 powershell = 5.1）。
      // 本机实测（2026-10-03）：`-NoProfile -NonInteractive` + CIM ≈ 1.5s（wmic 已从新 Windows
      // 移除、pwsh 的 WmiObject 更慢 3.8s）—— 故结果必须记忆化（见下）。
      const r = spawnSync(
        'pwsh',
        [
          '-NoProfile',
          '-NonInteractive',
          '-Command',
          '(Get-CimInstance Win32_Processor).NumberOfCores',
        ],
        { encoding: 'utf8', windowsHide: true, timeout: 15_000 },
      )
      return num(r.stdout)
    }
    if (process.platform === 'linux') {
      return parseProcCpuinfoPhysicalCores(readFileSync('/proc/cpuinfo', 'utf8'))
    }
  } catch {
    /* 回落逻辑核 */
  }
  return null
}

/**
 * 本机**物理核数**（排除超线程，只看硬件事实）；探测不到 ⇒ null。
 *
 * 结果**记忆化**：Windows 上要 spawn `pwsh`（~0.3-0.6s 启动），每进程只该付一次
 * （失败也缓存 —— 一台机器上「探测不到」不会因为再问一次而变化）。
 */
export function hostPhysicalCores(): number | null {
  if (hostPhysicalCache === undefined) hostPhysicalCache = detectHostPhysicalCores()
  return hostPhysicalCache
}

/**
 * **门禁 / 本地 worker 池**的并行度口径：本机可用的**物理核数**。
 *
 * 与 `effectiveCores()` 的分工（2026-10-03 决议，plan `gate-parallelism-physical-cores`）：
 * · `effectiveCores()` 给**训练运行时**的 rollout/eval 槽位 —— 那里超线程也能吃进吞吐，
 *   且容器配额是硬约束；
 * · 本函数给**门禁与本地 worker 池** —— 它们开的是「每核一个重型进程」，HT sibling 共享
 *   执行单元与 L1/L2，加 worker 只涨内存与切换（本机 8c/16t 实测：`bun test` 按 16 开
 *   worker 把 Windows 提交上限顶穿 ⇒ worker 崩 + 连环 abort 一片假红，8 全绿）。
 *
 * 组合规则与 `effectiveCores()` 同构：真物理核、cgroup 配额、亲和掩码取**小值**；三者都
 * 读不到才回落宿主机逻辑核数（本文件内的 `hostLogicalCores()`）。
 *
 * **已知灰区**：cpuset 恰好只给超线程 sibling 时会**高估**（精确做法要把 affinity 的 cpu id
 * 与 `/sys/.../topology/{core_id,physical_package_id}` 求交）—— 门禁场景收益极小，本版不做。
 */
export function physicalCores(): number {
  return resolveEffective(
    [hostPhysicalCores(), cgroupCpuQuota(), affinityCores()],
    hostLogicalCores(),
  )
}
