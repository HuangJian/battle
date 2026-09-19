/** hub-server-push-arg.test.ts — hub 中介 push 派发的接线（rl-config → hub-server argv）。
 *
 *  2026-09-18：push 模式下 hub 按队列顺序把 job 推给**登记在册**的空闲 GPU worker
 *  （`remote/push_dispatch.py`）。判据全在 hub 侧，但「这个 hub 要不要做派发」是部署
 *  事实，住 `rl.hub_push`：打开时 argv 必须带 `--push` **且** `--push-config` 指向仓库
 *  那份 rl-config.json —— `gpu_push` 节点（控制台的 worker 登记入口回写的目标）住在那里，
 *  指错文件 = 登记表恒空 = job 永远躺在队首（最难查的一种）。
 *
 *  本文件只钉两件事：① 缺省不带 `--push`（不打开连探活线程都不起）；② 打开时两个旗标
 *  都在、且路径就是 CONFIG_PATH。
 */

import { describe, expect, it } from 'bun:test'
import { hubServerSpec } from '../src/stack/specs'
import { CONFIG_PATH } from '../src/core/paths'
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

describe('hubServerSpec 传 --push', () => {
  it('缺省（未配置）= 不带 --push，也不带 --push-config', () => {
    const argv = argvOf(cfg())
    expect(argv).not.toContain('--push')
    expect(argv).not.toContain('--push-config')
  })

  it('rl.hub_push=true ⇒ --push + --push-config <仓库 rl-config.json>', () => {
    const argv = argvOf(cfg({ hub_push: true }))
    expect(argv).toContain('--push')
    const i = argv.indexOf('--push-config')
    expect(i).toBeGreaterThan(-1)
    expect(argv[i + 1]).toBe(CONFIG_PATH)
  })

  it('显式 false 与缺省同形（把派发关掉的写法不该变成「打开」）', () => {
    const argv = argvOf(cfg({ hub_push: false }))
    expect(argv).not.toContain('--push')
  })
})
