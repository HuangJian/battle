/**
 * disk-level.test.ts — 磁盘水位：分级（回差）/ 准入守门 / 上报接线（plan/self-node-disk-alert）。
 *
 * 分层：`tools/agent/sampler-agent.ts` 的**纯函数**（`classifyDiskFree` / `decideTaskAdmission`）
 * + 源码守卫（ping/status 两处必须从同一个 `diskReport()` 取字段——handler 在 `start()` 闭包里，
 * 行为面不开服务器测不了；本仓同款做法见 `tests/dist-agent.test.ts` 的源码守卫）。
 *
 * 三条钉子：
 *   ① 回差：入档严格 `<`、出档要 `>= 阈值 + 回差` ⇒ 无抖振（4095→warn、4607→仍 warn、4608→ok）；
 *   ② **N1 守门**（§4.1 那条否决的用例）：`<4096 且 >=2048` 时**仍接受作业**（拒收地板冻结在 2048）；
 *   ③ 上报：ping 与 status 都带五个磁盘字段，且**同源同拍**。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import {
  DISK_FLOOR_MB,
  DISK_HYST_MB,
  DISK_WARN_MB,
  classifyDiskFree,
  decideTaskAdmission,
  type DiskLevel,
} from '../../tools/agent/sampler-agent'

describe('阈值常量（冻结；改这里 = 改节点行为与告警口径）', () => {
  it('地板 2048 / 预警 4096 / 回差 512（plan §4.1：抬地板被否决，值不得漂移）', () => {
    expect(DISK_FLOOR_MB).toBe(2048)
    expect(DISK_WARN_MB).toBe(4096)
    expect(DISK_HYST_MB).toBe(512)
  })
})

describe('classifyDiskFree：入档边界 + 回差', () => {
  it('ok 起：4095 → warn、4096 → ok、2047 → critical、2048 → warn', () => {
    expect(classifyDiskFree(4095, 'ok')).toBe('warn')
    expect(classifyDiskFree(4096, 'ok')).toBe('ok')
    expect(classifyDiskFree(2047, 'ok')).toBe('critical')
    expect(classifyDiskFree(2048, 'ok')).toBe('warn')
  })

  it('warn 起：4095 仍 warn、4607 仍 warn、4608 → ok（回差 512 的出档线）', () => {
    expect(classifyDiskFree(4095, 'warn')).toBe('warn')
    expect(classifyDiskFree(4607, 'warn')).toBe('warn')
    expect(classifyDiskFree(4608, 'warn')).toBe('ok')
  })

  it('critical 起：2047 仍 critical、2559 仍 critical、2560 → warn（floor + 512）', () => {
    expect(classifyDiskFree(2047, 'critical')).toBe('critical')
    expect(classifyDiskFree(2559, 'critical')).toBe('critical')
    expect(classifyDiskFree(2560, 'critical')).toBe('warn')
    // 升档不因回差被压住：warn 过程里跌破地板照样 critical
    expect(classifyDiskFree(1900, 'warn')).toBe('critical')
  })

  it('critical 起的 warn 带也用回差（2560..4607 都停在 warn，4608 才回 ok）', () => {
    expect(classifyDiskFree(3000, 'critical')).toBe('warn')
    expect(classifyDiskFree(4607, 'critical')).toBe('warn')
    expect(classifyDiskFree(4608, 'critical')).toBe('ok')
  })

  it('★ 不抖振：在 4096 上下反复擦边，档位只变一次（回差的意义）', () => {
    let lv: DiskLevel = 'ok'
    let flips = 0
    const samples = [4090, 4095, 4090, 4096, 4097, 4096, 4095, 4096, 4097, 4098]
    for (const mb of samples) {
      const next = classifyDiskFree(mb, lv)
      if (next !== lv) flips += 1
      lv = next
    }
    expect(flips).toBe(1) // ok → warn，之后一路 warn（4608 才回 ok，样本里没到）
  })

  it('阈值可覆盖（--diskWarnMB 旋钮的接口面；本案不接 CLI，仅保纯函数可参数化）', () => {
    expect(classifyDiskFree(5000, 'ok', { warn: 6144 })).toBe('warn')
    expect(classifyDiskFree(5000, 'ok', { warn: 4096 })).toBe('ok')
  })
})

describe('decideTaskAdmission：拒收地板的唯一判据（N1 守门）', () => {
  it('★ 3000MB（>=2048 且 <4096）⇒ 仍接受作业——抬地板被否决的那条守门', () => {
    expect(decideTaskAdmission(3000)).toBe('accept')
  })

  it('2047 ⇒ 拒收；2048 ⇒ 接受（地板是严格 <）', () => {
    expect(decideTaskAdmission(2047)).toBe('low-disk')
    expect(decideTaskAdmission(2048)).toBe('accept')
  })

  it('磁盘不可知（statfs 失败）⇒ 照常接受（与旧行为一致：不可知不拒单）', () => {
    expect(decideTaskAdmission(null)).toBe('accept')
  })

  it('地板可覆盖（参数面）：传 4096 才是「抬线」语义——默认值不会变成它', () => {
    expect(decideTaskAdmission(3000, 4096)).toBe('low-disk')
    expect(decideTaskAdmission(3000)).toBe('accept')
  })
})

describe('上报接线（源码守卫：handler 在闭包里，两处必须同源）', () => {
  const src = readFileSync(new URL('../../tools/agent/sampler-agent.ts', import.meta.url), 'utf8')

  it('/v1/status 与 /v1/ping 各有一处 `...diskReport()`（且恰好两处）', () => {
    expect(src.split('...diskReport(),').length - 1).toBe(2)
  })

  it('准入调用的是 decideTaskAdmission(free)，不是内联的裸 2048', () => {
    expect(src).toContain("decideTaskAdmission(free) === 'low-disk'")
    expect(src).not.toContain('free !== null && free < 2048')
  })

  it('diskReport 单点产出五个 wire 字段（ping/status 不得各拼一遍）', () => {
    const block = src.slice(
      src.indexOf('function diskReport():'),
      src.indexOf('/**\n * 工作目录磁盘收敛'),
    )
    for (const k of ['diskFreeMB', 'diskLevel', 'diskWarnMB', 'diskFloorMB', 'diskLevelSince']) {
      expect(block).toContain(k)
    }
  })
})
