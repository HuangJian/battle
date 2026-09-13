/**
 * server-api-local-chips.test.ts — rl.local_slots = 0 也显示本机芯片
 *
 * 分层：src/server/api/state-view.ts + pool.ts（本机芯片）
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { api, loadConfig } from './helpers/console-fixture'
import { configPath } from '../src/core/paths'
import { describe, expect, it } from 'bun:test'
import { writeFileSync } from 'fs'
import type { TestConfig } from './helpers/console-fixture'

describe('console local 芯片（rl.local_slots = 0 也显示）', () => {
  it('buildStateView：local_slots=0 时 localNode 仍出现（slots=0）；配置缺失才缺省', async () => {
    const cfg = loadConfig()
    try {
      const patched = JSON.parse(JSON.stringify(cfg)) as TestConfig
      patched.rl.local_slots = 0
      writeFileSync(configPath(), JSON.stringify(patched, null, 2))
      api.invalidateSlowSnapshot()
      const s = await api.buildStateView()
      expect(s.localNode).not.toBeNull()
      expect(s.localNode!.slots).toBe(0)
    } finally {
      writeFileSync(configPath(), JSON.stringify(cfg, null, 2))
      api.invalidateSlowSnapshot()
    }
  })

  it('buildPoolView：local_slots=0 时 local 行 spec 显示「0 槽」（非缺失的 -）', async () => {
    const cfg = loadConfig()
    try {
      const patched = JSON.parse(JSON.stringify(cfg)) as TestConfig
      patched.rl.local_slots = 0
      writeFileSync(configPath(), JSON.stringify(patched, null, 2))
      const p = await api.buildPoolView(true)
      expect(p.local).not.toBeNull()
      expect(p.local!.spec).toBe('0 槽')
    } finally {
      writeFileSync(configPath(), JSON.stringify(cfg, null, 2))
    }
  })
})
