/**
 * worker-pool-defaults.test.ts — 门禁 / 本地 worker 池的**并发默认值**口径（2026-10-03）。
 *
 * 2026-10-03 决议（plan `gate-parallelism-physical-cores`）：门禁与本地 worker 池一律按
 * **物理核数**（超线程不计入）。本文件钉两件事：
 *   · `gateCoreCount()` 默认 = `physicalCores()`（此前写死 4）；`GATE_CORES` 是逃生口；
 *   · `defaultWorkerCount()` 保留「物理核 − 1」的老约定（给主线程留一核）；`SIM_POOL_WORKERS` 覆盖优先。
 *
 * 两个逃生口都是读 `process.env` 的纯函数 —— 用例里改 env 必须还原（否则污染同进程后续用例）。
 */
import { describe, expect, it } from 'bun:test'

import { physicalCores } from '../tools/lib/cores'
import { defaultWorkerCount, gateCoreCount } from '../tools/lib/worker-pool'

function withEnv<T>(key: string, value: string | undefined, fn: () => T): T {
  const prev = process.env[key]
  if (value === undefined) delete process.env[key]
  else process.env[key] = value
  try {
    return fn()
  } finally {
    if (prev === undefined) delete process.env[key]
    else process.env[key] = prev
  }
}

describe('门禁 / 本地 worker 池的并发默认值', () => {
  it('gateCoreCount 默认 = physicalCores()（不再写死 4）', () => {
    expect(withEnv('GATE_CORES', undefined, () => gateCoreCount())).toBe(physicalCores())
  })

  it('GATE_CORES 是逃生口：正数覆盖优先，0/垃圾不算覆盖', () => {
    expect(withEnv('GATE_CORES', '4', () => gateCoreCount())).toBe(4)
    expect(withEnv('GATE_CORES', '12', () => gateCoreCount())).toBe(12)
    expect(withEnv('GATE_CORES', '0', () => gateCoreCount())).toBe(physicalCores())
    expect(withEnv('GATE_CORES', 'nonsense', () => gateCoreCount())).toBe(physicalCores())
  })

  it('defaultWorkerCount 保留「物理核 − 1」（给主线程留一核）', () => {
    expect(withEnv('SIM_POOL_WORKERS', undefined, () => defaultWorkerCount())).toBe(
      Math.max(1, physicalCores() - 1),
    )
  })

  it('SIM_POOL_WORKERS 覆盖优先（≥1 才算）', () => {
    expect(withEnv('SIM_POOL_WORKERS', '3', () => defaultWorkerCount())).toBe(3)
    expect(withEnv('SIM_POOL_WORKERS', '0', () => defaultWorkerCount())).toBe(
      Math.max(1, physicalCores() - 1),
    )
  })

  it('两者都不为 0（零 worker 会当场卡死）', () => {
    expect(withEnv('GATE_CORES', undefined, () => gateCoreCount())).toBeGreaterThanOrEqual(1)
    expect(
      withEnv('SIM_POOL_WORKERS', undefined, () => defaultWorkerCount()),
    ).toBeGreaterThanOrEqual(1)
  })
})
