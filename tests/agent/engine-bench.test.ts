/**
 * engine-bench 端到端守护：rollout 引擎选择用的微基准必须**真的跑进生产特征内核**。
 *
 * 事故现场（2026-11-12）：v7 把 IN_CH 18→19（conv_native.h::CF_IN_CH），
 * tools/agent/engine-bench.ts 的 dummy 模型仍按 18 构造 ⇒ native/wasm 两臂都在形状
 * 守卫处 return false ⇒ 计时的是空转函数（0.00x ms）、BENCH-ARM 恒 unknown，
 * 假数字还被写进 engine-choice.json 永久缓存（choiceValid 只看版本/资产 sha）。
 */
import { describe, expect, it } from 'bun:test'
import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { IN_CH, STEM_W } from '../../src/nn/conv/conv-shape'

const ROOT = path.resolve(import.meta.dir, '../..')
const ENTRY = path.join(ROOT, 'tools/agent/engine-bench.ts')

describe('engine-bench 微基准（rollout 引擎选择的数据来源）', () => {
  it('bun 臂真跑特征内核：BENCH 非 0 且 BENCH-ARM 为 native/wasm，不是 unknown', () => {
    const r = spawnSync(process.execPath, [ENTRY, '1'], {
      cwd: ROOT,
      encoding: 'utf8',
      timeout: 60_000,
      windowsHide: true,
    })
    expect(r.status).toBe(0)
    const out = String(r.stdout ?? '')
    const ms = Number(/^BENCH\s+([\d.]+)$/m.exec(out)?.[1] ?? NaN)
    const arm = /^BENCH-ARM\s+(\S+)$/m.exec(out)?.[1] ?? 'unknown'
    expect(arm === 'native' || arm === 'wasm').toBe(true)
    expect(Number.isFinite(ms) && ms > 0.01).toBe(true)
  })

  it('TS 形状常量的唯一来源与 C 头 CF_IN_CH 对齐（跨语言契约）', () => {
    const h = readFileSync(path.join(ROOT, 'src/nn/conv/conv_native.h'), 'utf8')
    const cInCh = Number(/#define\s+CF_IN_CH\s+(\d+)/.exec(h)?.[1] ?? NaN)
    expect(cInCh).toBe(IN_CH)
    expect(STEM_W).toBe(IN_CH * 64 * 9)
  })
})
