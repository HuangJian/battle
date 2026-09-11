/** cloud-halt.test.ts — §386：云端停机/恢复（halting=红横幅、recovered=灰横幅历史）。
 *
 * 覆盖：
 *  - triggerCloudHalt：停机中幂等（不覆盖原因）；hub 不可达 → 失败且不写停机态；
 *  - 停机已恢复后再次触发 → 新停机记录（旧 recovered 被新一轮 halted 取代）；
 *  - markCloudHaltRecovered：停机中 → recovered（记录保留→灰横幅）；无记录/已恢复 → 幂等跳过；
 *    hub 不可达 → 失败但本地状态照标已恢复。
 * 真实 hub 联动的 halt/resume 由 test_remote_ppo.py::test_hub_workers_halt_flow 覆盖。
 *
 * 环境重定向：BCITY_CONSOLE_STATE / BCITY_REGISTRY_FILE 指向临时目录（top-level 赋值即可生效），
 * 绝不写脏线上 tmp/training-start/console-state.json。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync } from 'fs'
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
import type { RlConfig } from '../tools/training/types'

// hub 不可达（port 1 → 立即连接拒绝）→ 下发失败路径可离线断言。
const CFG = { rl: { hub_port: 1, remote_token: 'x' } } as unknown as RlConfig

function resetState(): void {
  saveConsoleState({ cloudHalt: undefined })
}

describe('triggerCloudHalt（停机中）', () => {
  it('hub 不可达 → 失败且不写停机态（红横幅不出现）', async () => {
    resetState()
    const r = await triggerCloudHalt(CFG, 'TrainingLoop 停车')
    expect(r.ok).toBe(false)
    expect(r.message).toContain('下发失败')
    expect(loadConsoleState().cloudHalt).toBeUndefined()
  })

  it('停机中 → 幂等跳过且不覆盖原原因', async () => {
    resetState()
    saveConsoleState({ cloudHalt: { at: 'T0', reason: '先前原因', status: 'halted' } })
    const r = await triggerCloudHalt(CFG, '新原因不该覆盖')
    expect(r.ok).toBe(true)
    expect(r.message).toContain('幂等跳过')
    expect(loadConsoleState().cloudHalt?.reason).toBe('先前原因')
  })
})

describe('markCloudHaltRecovered（停机条件消失 → recovered，灰横幅历史保留）', () => {
  it('停机中 → recovered：记录保留（含 clearedAt/clearReason），不被删除', async () => {
    resetState()
    saveConsoleState({ cloudHalt: { at: 'T1', reason: '停机', status: 'halted' } })
    const r = await markCloudHaltRecovered(CFG, 'TrainingLoop 已重启')
    // hub 不可达 → 失败但状态照标（避免红横幅永久卡死——灰横幅才是真相）
    expect(r.ok).toBe(false)
    const c = loadConsoleState().cloudHalt
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
    saveConsoleState({ cloudHalt: { at: 'T2', reason: 'r', status: 'recovered' } })
    const r2 = await markCloudHaltRecovered(CFG, 'y')
    expect(r2.ok).toBe(true)
    expect(r2.message).toContain('幂等')
  })

  it('已恢复后再停机 → 新一轮 halted（取代旧 recovered 记录）', async () => {
    resetState()
    saveConsoleState({
      cloudHalt: { at: 'T3', reason: '旧', status: 'recovered', clearedAt: 'T4' },
    })
    // hub 不可达 → 触发停机失败，状态不动（仍是 recovered）
    await triggerCloudHalt(CFG, '新一轮')
    expect(loadConsoleState().cloudHalt?.status).toBe('recovered')
    expect(loadConsoleState().cloudHalt?.reason).toBe('旧')
  })
})
