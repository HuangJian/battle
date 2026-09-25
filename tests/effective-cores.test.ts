/**
 * effective-cores.test.ts — 「本机到底有多少核」的 TS 单一口径（`tools/lib/cores.ts`）。
 *
 * 与 `nn-training/tests/test_platform_utils_cores.py` **同题**：python 侧是权威，TS 侧是镜像，
 * 两边都按「容器 cgroup 配额 / 亲和掩码 取小 > 宿主机裸数兜底」。
 * 事故本体（2026-09-25 云机 rollout 卡死）：容器里 `os.cpus().length` 报宿主机 224，
 * cgroup 只给 96 ⇒ 按 224 派 worker = 2.3× 超订 ⇒ 成批踩 5s 硬顶。
 */
import { describe, expect, it } from 'bun:test'

import {
  effectiveCores,
  hostLogicalCores,
  parseCgroupV1Quota,
  parseCgroupV2CpuMax,
  parseCpusAllowedList,
  resolveEffective,
} from '../tools/lib/cores'

describe('cgroup 配额解析（v2 / v1）', () => {
  it('v2 cpu.max: quota÷period 向上取整', () => {
    expect(parseCgroupV2CpuMax('9600000 100000')).toBe(96)
    expect(parseCgroupV2CpuMax('150000 100000')).toBe(2) // 1.5 核 → 2（向上取整）
    expect(parseCgroupV2CpuMax('300000 100000')).toBe(3)
  })

  it('v2: "max"（不限）/ 读不到 / 垃圾 ⇒ null', () => {
    expect(parseCgroupV2CpuMax('max 100000')).toBeNull()
    expect(parseCgroupV2CpuMax(null)).toBeNull()
    expect(parseCgroupV2CpuMax('')).toBeNull()
    expect(parseCgroupV2CpuMax('nonsense')).toBeNull()
    expect(parseCgroupV2CpuMax('9600000 0')).toBeNull() // period 0 不能当除数
  })

  it('v1: quota÷period，period 缺省 100000；-1/0/垃圾 ⇒ null', () => {
    expect(parseCgroupV1Quota('9600000', '100000')).toBe(96)
    expect(parseCgroupV1Quota('200000', null)).toBe(2) // period 缺省 100000
    expect(parseCgroupV1Quota('-1', '100000')).toBeNull()
    expect(parseCgroupV1Quota('0', '100000')).toBeNull()
    expect(parseCgroupV1Quota(null, '100000')).toBeNull()
    expect(parseCgroupV1Quota('lots', '100000')).toBeNull()
  })
})

describe('亲和掩码解析（/proc/self/status 的 Cpus_allowed_list）', () => {
  const status = (spec: string): string =>
    `Name:\tbun\nCpus_allowed_list:\t${spec}\nMems_allowed: 0\n`

  it('区间与逗号列表都算对', () => {
    expect(parseCpusAllowedList(status('0-95'))).toBe(96)
    expect(parseCpusAllowedList(status('0-3,8-11'))).toBe(8)
    expect(parseCpusAllowedList(status('7'))).toBe(1)
  })

  it('格式坏 / 没有这一行 / 读不到 ⇒ null（不许静默当 0 或 1 核）', () => {
    expect(parseCpusAllowedList('Name:\tbun\n')).toBeNull()
    expect(parseCpusAllowedList(null)).toBeNull()
    expect(parseCpusAllowedList(status(''))).toBeNull()
    expect(parseCpusAllowedList(status('a-b'))).toBeNull()
    expect(parseCpusAllowedList(status('8-3'))).toBeNull() // 倒序区间宁可判定为读不到
  })
})

describe('口径优先级：配额/亲和掩码取小，都没有才回落宿主机', () => {
  it('两者都有 ⇒ 取小值（cpuset 绑核与配额限流是两个不同的事实）', () => {
    expect(resolveEffective([96, 8], 224)).toBe(8)
    expect(resolveEffective([8, 96], 224)).toBe(8)
    expect(resolveEffective([96, null], 224)).toBe(96)
    expect(resolveEffective([null, 96], 224)).toBe(96)
  })

  it('两者都读不到 ⇒ 才信宿主机裸数（Windows / macOS / 裸机）', () => {
    expect(resolveEffective([null, null], 16)).toBe(16)
    expect(resolveEffective([0, -3], 16)).toBe(16) // 0/负数不是信号
  })

  it('永不为 0（最坏也是 1）', () => {
    expect(resolveEffective([], 0)).toBe(1)
    expect(resolveEffective([null], -5)).toBe(1)
  })
})

describe('本机不变量（在开发机/CI 上也必须成立）', () => {
  it('1 ≤ effectiveCores() ≤ hostLogicalCores()', () => {
    const n = effectiveCores()
    expect(Number.isInteger(n)).toBe(true)
    expect(n).toBeGreaterThanOrEqual(1)
    expect(n).toBeLessThanOrEqual(hostLogicalCores())
  })

  it('纯函数解析器是唯一决策点：effectiveCores 与手工算的一致', () => {
    // 真机读数（不是 mock）：容器里两个信号中可能只有亲和掩码可读，取小值即口径。
    const signals = [parseCgroupV2CpuMax(null), parseCpusAllowedList(null)]
    expect(resolveEffective(signals, hostLogicalCores())).toBe(hostLogicalCores())
  })
})
