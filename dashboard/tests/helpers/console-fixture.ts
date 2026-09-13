/**
 * console-fixture.ts — 控制台单测的共享 scratch 夹具（环境重定向 + 被测模块）。
 *
 * 为什么需要它：
 *  ① 被测模块会写 `nn-training/rl-config.json` 与 `tmp/training-start/console-state.json`
 *     这些**真实**路径。历史做法是「跑前备份真实文件 → 测试直接写真路径 → 跑完字节级
 *     还原」：进程被杀或套件崩溃，工作配置就留下被改坏的内容。
 *  ② 那套备份/还原是 `beforeAll`/`afterAll` 级别的**全局**夹具，一个文件只能有一份。
 *     1355 行的巨型测试按 src 分层拆成多个文件后，各文件在 `--parallel` 下是独立进程，
 *     多份「写真实文件再还原」会互相踩（A 还原时 B 正读自己刚写的内容）。
 *
 * 因此改用 `core/paths.ts` 已有的**惰性** env 重定向（`BCITY_RL_CONFIG` /
 * `BCITY_CONSOLE_STATE`，与 `cloud-halt.test.ts` 同一惯例）：每个测试文件一个独立
 * 临时目录，配置以真实 `rl-config.json` 为蓝本播种 —— 写盘分支照常真实执行，但只落在
 * 临时目录，跑完随进程消失，真实配置**整轮零写入**（连崩溃都不可能污染）。
 *
 * **顺序不变量**（本模块存在的第二个意义）：env 必须早于被测模块的 import。这里把
 * 「先重定向 → 再 await import」封装成一次，调用方只要 import 本模块就不可能搞错顺序
 * （逐个文件重写 4 行 `await import` 迟早会有人把顺序写反）。
 *
 * 用法：
 * ```ts
 * import { api, postJson, loadConfig } from './helpers/console-fixture'
 * ```
 */

import { afterAll } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { CONFIG_PATH } from '../../src/core/paths'

/** rl-config.json 的节点行（测试补丁用；线上 schema 以 src/core/config.ts 为准）。 */
export interface TestNode {
  id: string
  url: string
  authKey: string
  concurrency: number
  enabled: boolean
  gpu_push?: boolean
}

/** rl-config.json 的最小测试视图。 */
export interface TestConfig {
  version: number
  nodes: TestNode[]
  rl: Record<string, unknown>
}

/** 本测试文件专属的临时目录（每个文件一个，互不共享）。 */
export const scratchDir = mkdtempSync(path.join(os.tmpdir(), 'bcity-console-'))
/** 重定向后的 rl-config.json（等价于 `configPath()`）。 */
export const scratchConfig = path.join(scratchDir, 'rl-config.json')
/** 重定向后的 console-state.json。 */
export const scratchState = path.join(scratchDir, 'console-state.json')

// 以真实配置为蓝本播种：节点表 / rl 段 / local_slots 等与线上同形，
// 因此「放行真实课程」「节点并发回写」这类断言读到的仍是真实形态。
writeFileSync(scratchConfig, readFileSync(CONFIG_PATH, 'utf-8'))
process.env.BCITY_RL_CONFIG = scratchConfig
process.env.BCITY_CONSOLE_STATE = scratchState

afterAll(() => {
  rmSync(scratchDir, { recursive: true, force: true })
})

/** 读当前（scratch）配置——与旧的 `loadRealConfig()` 语义一致：拿一份可改的配置蓝本。 */
export function loadConfig(): TestConfig {
  return JSON.parse(readFileSync(scratchConfig, 'utf-8')) as TestConfig
}

/** 读当前（scratch）配置原文（「动作是否写盘」类断言用）。 */
export function readConfigText(): string {
  return readFileSync(scratchConfig, 'utf-8')
}

/** 读当前（scratch）控制台状态原文；不存在返回 null。 */
export function readStateText(): string | null {
  try {
    return readFileSync(scratchState, 'utf-8')
  } catch {
    return null
  }
}

// ── 被测模块（env 重定向之后 import；模块初始化无副作用）──

export const api = await import('../../src/server/api')
export const actions = await import('../../src/server/actions')
export const render = await import('../../src/web/render')
export const view = await import('../../src/web/view')

/** 发一个控制台动作（等价于 POST /api/action/:act）。 */
export function post(act: string, body: Record<string, unknown> = {}): Promise<Response> {
  return api.routeAction(act, body) as Promise<Response>
}

/** 发动作并取 JSON + HTTP 状态码（`__status`）。 */
export async function postJson(
  act: string,
  body: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const r = (await post(act, body)) as Response
  return { ...((await r.json()) as Record<string, unknown>), __status: r.status }
}
