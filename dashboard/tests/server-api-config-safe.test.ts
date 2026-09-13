/**
 * server-api-config-safe.test.ts — §361③ 配置瞬时损坏回退上次成功配置（不抛 500）+ logTail 超长行不拖垮
 *
 * 分层：src/server/api/config.ts + logs.ts
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import path from 'path'
import { NN_TRAINING } from '../src/core/paths'
import { api } from './helpers/console-fixture'
import { configPath } from '../src/core/paths'
import { describe, expect, it } from 'bun:test'
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'fs'

describe('console/api §361③：配置损坏兜底与日志尾容错', () => {
  it('loadConfigSafe：rl-config.json 瞬时损坏回退上次成功配置，不抛 500', async () => {
    const before = readFileSync(configPath(), 'utf-8')
    const good = api.loadConfigSafe()
    expect(good.nodes).toBeInstanceOf(Array)
    try {
      // 模拟 saveConfig 写盘窗口的半截 JSON（§339 同款竞态家族）
      writeFileSync(configPath(), '{"version":1,"nodes":[', 'utf-8')
      const safe = api.loadConfigSafe()
      expect(safe.nodes).toEqual(good.nodes) // 回退内存缓存
    } finally {
      writeFileSync(configPath(), before, 'utf-8')
    }
    expect(api.loadConfigSafe().nodes).toBeInstanceOf(Array) // 恢复后无崩溃
  })

  it('logTail：缺文件安全 + 尾窗口 + 超长行截断（单行损坏不拖垮）', () => {
    expect(api.logTail('tmp/no-such-log-xyz.log', 5)).toEqual([])
    const p = path.join(NN_TRAINING, 'tmp', 'logtail-test-361.log')
    mkdirSync(path.dirname(p), { recursive: true })
    writeFileSync(p, 'a\n' + 'x'.repeat(300) + '\nb\n')
    try {
      const t = api.logTail('tmp/logtail-test-361.log', 5)
      expect(t[0]).toBe('a')
      expect(t[1]!.length).toBe(200) // 超长行截到 200
      expect(t[2]).toBe('b')
    } finally {
      rmSync(p, { force: true })
    }
  })
})
