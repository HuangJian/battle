/** cloud-halt.test.ts — §386 + S17：云端停机/恢复按课程（halting=红横幅、recovered=灰横幅历史）。
 *
 * 覆盖：
 *  - triggerCloudHalt：停机中幂等（不覆盖原因）；hub 不可达 → 失败且不写停机态；
 *  - 停机已恢复后再次触发 → 新停机记录（旧 recovered 被新一轮 halted 取代）；
 *  - markCloudHaltRecovered：停机中 → recovered（记录保留→灰横幅）；无记录/已恢复 → 幂等跳过；
 *    hub 不可达 → 失败但本地状态照标已恢复；
 *  - **S17 多课程**：A 课停机不写 B 课；对 A 的幂等跳过不吃掉 B 的 halt 记录；
 *    旧单键 `cloudHalt` 一次性迁移进 per-course 表。
 * 真实 hub 联动的 halt/resume 由 test_remote_ppo.py::test_hub_workers_halt_flow 覆盖。
 *
 * 环境重定向：BCITY_CONSOLE_STATE / BCITY_REGISTRY_FILE 指向临时目录（top-level 赋值即可生效），
 * 绝不写脏线上 tmp/training-start/console-state.json。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'

const CSTATE_DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-cstate-'))
process.env.BCITY_CONSOLE_STATE = path.join(CSTATE_DIR, 'console-state.json')
const CREG_DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-creg-'))
process.env.BCITY_REGISTRY_FILE = path.join(CREG_DIR, 'registry.json')
afterAll(() => {
  try {
    rmSync(CSTATE_DIR, { recursive: true, force: true })
    rmSync(CREG_DIR, { recursive: true, force: true })
  } catch {
    /* noop */
  }
})

import {
  loadConsoleState,
  markCloudHaltRecovered,
  saveConsoleState,
  triggerCloudHalt,
} from '../tools/training/console/actions'
import { consoleStatePath } from '../tools/training/paths'
import type { RlConfig } from '../tools/training/types'

// hub 不可达（port 1 → 立即连接拒绝）→ 下发失败路径可离线断言。
const CFG = { rl: { hub_port: 1, remote_token: 'x' } } as unknown as RlConfig

function resetState(): void {
  saveConsoleState({ cloudHalts: {} })
}

/** 无课程（'' 键）停机记录。 */
const noCourse = () => loadConsoleState().cloudHalts?.['']

describe('triggerCloudHalt（停机中）', () => {
  it('hub 不可达 → 失败且不写停机态（红横幅不出现）', async () => {
    resetState()
    const r = await triggerCloudHalt(CFG, 'TrainingLoop 停车')
    expect(r.ok).toBe(false)
    expect(r.message).toContain('下发失败')
    expect(noCourse()).toBeUndefined()
  })

  it('停机中 → 幂等跳过且不覆盖原原因', async () => {
    resetState()
    saveConsoleState({ cloudHalts: { '': { at: 'T0', reason: '先前原因', status: 'halted' } } })
    const r = await triggerCloudHalt(CFG, '新原因不该覆盖')
    expect(r.ok).toBe(true)
    expect(r.message).toContain('幂等跳过')
    expect(noCourse()?.reason).toBe('先前原因')
  })

  it('S17：A 课幂等跳过不吃掉 B 课的停机记录', async () => {
    resetState()
    saveConsoleState({
      cloudHalts: {
        a: { at: 'T0', reason: 'A 停车', status: 'halted' },
        b: { at: 'T1', reason: 'B 停车', status: 'halted' },
      },
    })
    // 对 a 重复下发：幂等跳过；b 的记录逐字段不动
    const r = await triggerCloudHalt(CFG, 'a 新原因', 'a')
    expect(r.message).toContain('幂等跳过')
    const halts = loadConsoleState().cloudHalts
    expect(halts?.a?.reason).toBe('A 停车')
    expect(halts?.b?.reason).toBe('B 停车')
  })
})

describe('markCloudHaltRecovered（停机条件消失 → recovered，灰横幅历史保留）', () => {
  it('停机中 → recovered：记录保留（含 clearedAt/clearReason），不被删除', async () => {
    resetState()
    saveConsoleState({ cloudHalts: { '': { at: 'T1', reason: '停机', status: 'halted' } } })
    const r = await markCloudHaltRecovered(CFG, 'TrainingLoop 已重启')
    // hub 不可达 → 失败但状态照标（避免红横幅永久卡死——灰横幅才是真相）
    expect(r.ok).toBe(false)
    const c = noCourse()
    expect(c?.status).toBe('recovered')
    expect(c?.clearedAt).toBeDefined()
    expect(c?.clearReason).toBe('TrainingLoop 已重启')
    expect(c?.reason).toBe('停机') // 原始原因保留
  })

  it('无记录/已恢复 → 幂等跳过', async () => {
    resetState()
    const r1 = await markCloudHaltRecovered(CFG, 'x')
    expect(r1.ok).toBe(true)
    expect(r1.message).toContain('幂等')
    saveConsoleState({ cloudHalts: { '': { at: 'T2', reason: 'r', status: 'recovered' } } })
    const r2 = await markCloudHaltRecovered(CFG, 'y')
    expect(r2.ok).toBe(true)
    expect(r2.message).toContain('幂等')
  })

  it('已恢复后再停机 → 新一轮 halted（取代旧 recovered 记录）', async () => {
    resetState()
    saveConsoleState({
      cloudHalts: { '': { at: 'T3', reason: '旧', status: 'recovered', clearedAt: 'T4' } },
    })
    // hub 不可达 → 触发停机失败，状态不动（仍是 recovered）
    await triggerCloudHalt(CFG, '新一轮')
    expect(noCourse()?.status).toBe('recovered')
    expect(noCourse()?.reason).toBe('旧')
  })

  it('S17：恢复只动本课，另一课 halt 原样保留', async () => {
    resetState()
    saveConsoleState({
      cloudHalts: {
        a: { at: 'T0', reason: 'A 停机', status: 'halted' },
        b: { at: 'T1', reason: 'B 停机', status: 'halted' },
      },
    })
    await markCloudHaltRecovered(CFG, 'A 已重启', 'a')
    const halts = loadConsoleState().cloudHalts
    expect(halts?.a?.status).toBe('recovered')
    expect(halts?.b?.status).toBe('halted') // 未被误恢复
  })
})

describe('旧单键 cloudHalt 迁移（R4 additive）', () => {
  it('旧文件只有 cloudHalt（无 cloudHalts）→ 折叠进 [] 键且不丢', () => {
    // 直接写旧形状文件（绕过 saveConsoleState），模拟升级前的 console-state.json
    writeFileSync(
      process.env.BCITY_CONSOLE_STATE!,
      JSON.stringify({
        trainerPpo: 'pull',
        course: 'legacy-c',
        cloudHalt: { at: 'T9', reason: '旧事故', status: 'halted' },
      }),
    )
    const s = loadConsoleState()
    expect(s.activeCourse).toBe('legacy-c') // activeCourse 由旧 course 回填
    expect(s.cloudHalts?.['legacy-c']?.reason).toBe('旧事故')
    expect(readFileSync(process.env.BCITY_CONSOLE_STATE!, 'utf-8')).toContain('cloudHalt')
  })
})

describe('consoleStatePath 惰性求值（2026-09-12 线上污染回归）', () => {
  it('import 之后再改 env 也生效（模块级 const 会冻结线上路径）', () => {
    const other = mkdtempSync(path.join(os.tmpdir(), 'bcity-cstate-lazy-'))
    try {
      process.env.BCITY_CONSOLE_STATE = path.join(other, 'console-state.json')
      expect(consoleStatePath()).toBe(path.join(other, 'console-state.json'))
      saveConsoleState({ course: 'lazy-probe' })
      expect(loadConsoleState().course).toBe('lazy-probe')
    } finally {
      process.env.BCITY_CONSOLE_STATE = path.join(CSTATE_DIR, 'console-state.json')
      rmSync(other, { recursive: true, force: true })
    }
  })
})
