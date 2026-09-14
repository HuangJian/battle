import { describe, expect, it } from 'bun:test'
import { existsSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../src/core/paths'
import { SELF_NODE_ENTRY, selfNodeSpec } from '../src/stack/specs'
import type { RlConfig } from '../src/core/types'

/**
 * self-node spec cwd 护栏（2026-09-14 sampler-agent Module not found 回归）。
 *
 * 背景：`selfNodeSpec.cmd = [bun, run, tools/agent/sampler-agent.ts]` 是仓库
 * 相对路径，但 spec 没写 `cwd` —— 控制台以 `dashboard/` 为 cwd 启动时，
 * `bun run` 在 dashboard/ 下解析入口，直接 Module not found 退出，
 * 健康检查 30s 超时后报 exit-error。其余 spec（hub/cloudflared/worker）
 * 用的全是绝对路径或 `-m` 模块，可豁免；唯 self-node 必须钉 cwd。
 */
const cfg = { rl: { agent_port: 18901 }, nodes: [] } as unknown as RlConfig

describe('self-node spec cwd', () => {
  it('cwd == REPO_ROOT 且入口文件在其下存在', () => {
    const spec = selfNodeSpec(cfg)
    expect(spec.cwd).toBe(REPO_ROOT)
    expect(existsSync(path.join(spec.cwd!, SELF_NODE_ENTRY))).toBe(true)
  })
})
