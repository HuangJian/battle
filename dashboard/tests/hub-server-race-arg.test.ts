/** hub-server-race-arg.test.ts — 竞速广播开关的接线（rl-config → hub-server argv）。
 *
 *  2026-09-17：单课程多卡时 hub 广播最新 job（先回传者胜）。判据在 hub-server 侧自动
 *  （worker 自报服务几个 hub），但运维需要一个说得清的强制闸：`rl.race_mode` →
 *  `hub-server --race auto|on|off`（也可热切 `POST /admin/race?mode=...`）。
 *
 *  本文件只钉两件事：① 归一化（写错的值不得让 hub-server 因 argparse choices 起不来）；
 *  ② argv 真的带上了 `--race`（漏接 = 开关静默失效）。
 */

import { describe, expect, it } from 'bun:test'
import { hubServerSpec } from '../src/stack/specs'
import { normalizeRaceMode } from '../src/core/types'
import type { RlConfig } from '../src/core/types'

function cfg(rl: Record<string, unknown> = {}): RlConfig {
  return {
    version: 5,
    nodes: [],
    rl: { hub_port: 18789, agent_port: 18940, remote_token: 't', ...rl } as RlConfig['rl'],
  }
}

function argvOf(c: RlConfig): string[] {
  // 共享 hub（2026-09-18）：spec 不再按课程构造（一个进程服务所有并行课程）。
  return hubServerSpec(c).cmd.map(String)
}

describe('normalizeRaceMode', () => {
  it('只认 auto/on/off；其余（含 undefined/垃圾）一律回落 auto', () => {
    expect(normalizeRaceMode('on')).toBe('on')
    expect(normalizeRaceMode('off')).toBe('off')
    expect(normalizeRaceMode('auto')).toBe('auto')
    expect(normalizeRaceMode(undefined)).toBe('auto')
    expect(normalizeRaceMode('ON')).toBe('auto') // 大小写不猜：错值回落，不静默改语义
    expect(normalizeRaceMode(1)).toBe('auto')
  })
})

describe('hubServerSpec 传 --race', () => {
  it('缺省（未配置）= auto', () => {
    const argv = argvOf(cfg())
    const i = argv.indexOf('--race')
    expect(i).toBeGreaterThan(-1)
    expect(argv[i + 1]).toBe('auto')
  })

  it('配置 on/off 原样透传（运维强制闸）', () => {
    expect(argvOf(cfg({ race_mode: 'on' })).at(-1)).toBe('on')
    expect(argvOf(cfg({ race_mode: 'off' })).at(-1)).toBe('off')
  })

  it('非法值归一化为 auto —— hub-server 的 argparse choices 会让未知值秒退', () => {
    const argv = argvOf(cfg({ race_mode: 'yes-please' }))
    expect(argv[argv.indexOf('--race') + 1]).toBe('auto')
  })
})
