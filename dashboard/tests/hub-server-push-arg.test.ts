/** hub-server-push-arg.test.ts — hub 派发腿的接线（rl-config → hub-server argv）。
 *
 *  2026-09-18：push 模式下 hub 按队列顺序把 job 推给**登记在册**的空闲 GPU worker
 *  （`remote/push_dispatch.py`）。判据全在 hub 侧，但「这个 hub 要不要做派发」是部署
 *  事实，住 `rl.hub_push`：打开时 argv 必须带 `--push`，且 `--push-config` 指向仓库
 *  那份 rl-config.json —— `gpu_push` 节点（控制台的 worker 登记入口回写的目标）住在那里，
 *  指错文件 = 登记表恒空 = job 永远躺在队首（最难查的一种）。
 *
 *  2026-10-24 真机事故补钉：离线 push 派发 `--offline-push` 原来**只存在于手工命令行**，
 *  控制台起的 hub 从不带它 ⇒ notebook 自动注册成功、`nodes[]` 也写进去了，却零派单。
 *  现在它是恒带项，`--push-config` 也跟着恒带（两条腿共用同一份登记表）。
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
  it('缺省（未配置）= 不带 --push，但登记表与离线派发恒在', () => {
    const argv = argvOf(cfg())
    expect(argv).not.toContain('--push')
    // 登记表来源恒带（离线腿也读它）；2026-10-24 前只有 hub_push 打开时才带。
    const i = argv.indexOf('--push-config')
    expect(i).toBeGreaterThan(-1)
    expect(argv[i + 1]).toBe(CONFIG_PATH)
    // 离线 push 派发恒挂：无 offline_push 节点时它纯空转，有节点就必须能派单。
    expect(argv).toContain('--offline-push')
  })

  it('rl.hub_push=true ⇒ 追加 --push（登记表参数不重复）', () => {
    const argv = argvOf(cfg({ hub_push: true }))
    expect(argv).toContain('--push')
    expect(argv.filter((a) => a === '--push-config')).toHaveLength(1)
    const i = argv.indexOf('--push-config')
    expect(argv[i + 1]).toBe(CONFIG_PATH)
    expect(argv).toContain('--offline-push')
  })

  it('显式 false 与缺省同形（把派发关掉的写法不该变成「打开」）', () => {
    const argv = argvOf(cfg({ hub_push: false }))
    expect(argv).not.toContain('--push')
    expect(argv).toContain('--offline-push')
  })
})
