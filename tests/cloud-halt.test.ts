/** cloud-halt.test.ts — §385 复审：云端停机/恢复（停云端省 GPU 配额、本地进程不动、横幅数据源）。
 *
 * 覆盖：
 *  - triggerCloudHalt：hub 不可达 → 失败且不写停机态；已是停机态 → 幂等跳过（不覆盖原因）；
 *  - resumeCloud：hub 不可达 → 失败但本地停机标记照清（横幅先消失，不永久卡死）。
 * 真实 hub 联动的 halt/resume 行为由 test_remote_ppo.py::test_hub_workers_halt_flow 覆盖。
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
  resumeCloud,
  saveConsoleState,
  triggerCloudHalt,
} from '../tools/training/console/actions'
import type { RlConfig } from '../tools/training/types'

// hub 不可达（port 1 → 立即连接拒绝）→ 下发失败路径可离线断言。
const CFG = { rl: { hub_port: 1, remote_token: 'x' } } as unknown as RlConfig

describe('triggerCloudHalt（云端停机，本地不动）', () => {
  it('hub 不可达 → 失败且不写停机态（横幅不出现）', async () => {
    const r = await triggerCloudHalt(CFG, 'TrainingLoop 设计内停车：STOP')
    expect(r.ok).toBe(false)
    expect(r.message).toContain('下发失败')
    expect(loadConsoleState().cloudHalt).toBeUndefined()
  })

  it('已是停机态 → 幂等跳过且不覆盖原原因', async () => {
    saveConsoleState({ cloudHalt: { at: 'T0', reason: '先前原因' } })
    const r = await triggerCloudHalt(CFG, '新原因不该覆盖')
    expect(r.ok).toBe(true)
    expect(r.message).toContain('幂等跳过')
    expect(loadConsoleState().cloudHalt?.reason).toBe('先前原因')
  })
})

describe('resumeCloud（恢复云端）', () => {
  it('hub 不可达 → 失败但本地停机标记照清（横幅先消失，不永久卡死）', async () => {
    saveConsoleState({ cloudHalt: { at: 'T1', reason: '停机' } })
    const r = await resumeCloud(CFG)
    expect(r.ok).toBe(false)
    expect(loadConsoleState().cloudHalt).toBeUndefined()
  })
})
