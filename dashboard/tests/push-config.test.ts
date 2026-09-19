/** push-config.test.ts — push 执行面：URL 归一化 + 「这轮 PPO 会去哪」的部署事实推导。
 *
 *  **2026-09-19 用户口径**：「启动课程训练时，trainloop 不需要指定 pull/push 模式。pull 模式是由
 *  远端 worker 自己请求，本机只需要保证 hub 在线，配以 tailscale/cloudflared tunnel。push 模式
 *  只看系统是否已经配置了 push worker 节点，界面留配置入口，节点数据存 rl-config.json」。
 *
 *  ⇒ 执行面不再是「模式」，而是由**部署事实**推出来的：`rl.hub_push`（缺省开）+ 登记在册的
 *  `nodes[].gpu_push` + hub 地址 ⇒ 三条路（hub 派发 / 直推 / 等人来领）。课程侧一个字都不配。
 *
 *  本文件因此钉两件事：
 *   ① `remoteExecutionFace` 的推导表（纯函数，不碰网络）；
 *   ② **防回流**：任何「把某门课钉到某条传输路 / 某台机器」的写入口与键都不得复活。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { COURSE_COMPONENTS, registryTriples } from '../src/core/registry'
import { ALL_COMPONENTS } from '../src/server/api/component-meta'
import {
  enabledGpuPushNodes,
  hubPushEnabled,
  normalizePushUrl,
  remoteExecutionFace,
} from '../src/stack/push-config'
import type { RlConfig } from '../src/core/types'

/** 去掉注释（`//` 与 `/* … *\/`）后的源码：防回流断言查**代码**，不是查注释。
 *
 *  为什么需要它：这些「已删掉的东西」在文档注释里被反复点名（那正是它们退役的记录），
 *  直接扫原文会把「写明它已退役」当成「它还在」——于是只能扫剥掉注释后的代码。 */
function code(rel: string): string {
  const src = readFileSync(path.join(DASHBOARD_ROOT, 'src', ...rel.split('/')), 'utf-8')
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')
}

function baseCfg(): RlConfig {
  return {
    version: 1,
    nodes: [
      {
        id: 'self',
        url: 'http://127.0.0.1:8443',
        authKey: 'sampler',
        concurrency: 8,
        enabled: true,
      },
    ],
    rl: {
      hub_port: 8787,
      agent_port: 8443,
      remote_token: 'hub-tok',
      remote_hub_url: 'http://100.64.0.1:8787',
    },
    courses: { 'x2-start': { slot: 1 } },
  }
}

/** 加一台云 GPU push 节点。 */
function withGpuNode(cfg: RlConfig, id = 'g1', url = 'https://gpu.example'): RlConfig {
  return {
    ...cfg,
    nodes: [
      ...(cfg.nodes ?? []),
      { id, url, authKey: 'tok-gpu', concurrency: 1, enabled: true, gpu_push: true },
    ],
  }
}

describe('normalizePushUrl', () => {
  it('补 https、去尾斜杠', () => {
    expect(normalizePushUrl('abc.example.com/')).toBe('https://abc.example.com')
    expect(normalizePushUrl('http://127.0.0.1:8790///')).toBe('http://127.0.0.1:8790')
  })
  it('空/非法协议报错', () => {
    expect(() => normalizePushUrl('')).toThrow()
    expect(() => normalizePushUrl('ftp://x')).toThrow()
  })
})

describe('enabledGpuPushNodes（登记即候选）', () => {
  it('只取 enabled 的 gpu_push（enabled 缺省视为 true）', () => {
    const cfg = baseCfg()
    cfg.nodes.push(
      {
        id: 'g1',
        url: 'https://a',
        authKey: 'k',
        concurrency: 1,
        enabled: false,
        gpu_push: true,
      },
      { id: 'g2', url: 'https://b', authKey: 'k', concurrency: 1, enabled: true, gpu_push: true },
    )
    expect(enabledGpuPushNodes(cfg).map((n) => n.id)).toEqual(['g2'])
  })

  it('rollout 节点（无 gpu_push）不是候选', () => {
    expect(enabledGpuPushNodes(baseCfg()).length).toBe(0)
  })
})

describe('hubPushEnabled（缺省开 = 配了节点就走 hub 派发）', () => {
  it('未配置 → true', () => {
    expect(hubPushEnabled(baseCfg())).toBe(true)
  })
  it('显式 false → false（回到直推节点）', () => {
    const cfg = baseCfg()
    cfg.rl = { ...cfg.rl, hub_push: false }
    expect(hubPushEnabled(cfg)).toBe(false)
  })
})

describe('remoteExecutionFace —— 由部署事实推「这轮 PPO 会去哪」', () => {
  it('没登记节点 → pull（等 worker 来领；任何 worker 同权）', () => {
    const f = remoteExecutionFace(baseCfg())
    expect(f.mode).toBe('pull')
    expect(f.nodes).toBe(0)
    expect(f.text).toContain('等 worker')
  })

  it('登记节点 + hub_push 缺省 + hub 地址齐备 → hub 中介派发', () => {
    const f = remoteExecutionFace(withGpuNode(baseCfg()))
    expect(f.mode).toBe('hub-dispatch')
    expect(f.nodes).toBe(1)
    expect(f.hubPush).toBe(true)
  })

  it('登记节点但显式 hub_push=false → 直推节点（训练侧按序 failover）', () => {
    const cfg = withGpuNode(baseCfg())
    cfg.rl = { ...cfg.rl, hub_push: false }
    const f = remoteExecutionFace(cfg)
    expect(f.mode).toBe('direct-push')
    expect(f.detail).toContain('hub_push=false')
  })

  it('登记节点但 hub 地址缺失 → 直推（并说清缺什么）', () => {
    const cfg = withGpuNode(baseCfg())
    cfg.rl = { ...cfg.rl, remote_hub_url: '' }
    const f = remoteExecutionFace(cfg)
    expect(f.mode).toBe('direct-push')
    expect(f.detail).toContain('hub 地址未配置')
  })

  it('登记节点但 hub token 缺失 → 直推（python 同口径：不炸训练，退化直推）', () => {
    const cfg = withGpuNode(baseCfg())
    cfg.rl = { ...cfg.rl, remote_token: '' }
    const f = remoteExecutionFace(cfg)
    expect(f.mode).toBe('direct-push')
    expect(f.detail).toContain('token')
  })

  it('停用的节点不算数（停用 → 回到 pull，而不是「派给一台不会应答的机器」）', () => {
    const cfg = withGpuNode(baseCfg())
    cfg.nodes = cfg.nodes!.map((n) => (n.gpu_push ? { ...n, enabled: false } : n))
    expect(remoteExecutionFace(cfg).mode).toBe('pull')
  })

  it('多节点 = 多候选（同一批候选服务所有课程；不做按课绑定）', () => {
    const cfg = withGpuNode(withGpuNode(baseCfg(), 'g2', 'https://gpu2.example'))
    const f = remoteExecutionFace(cfg)
    expect(f.mode).toBe('hub-dispatch')
    expect(f.nodes).toBe(2)
  })
})

// ────────────────── 防回流：课程与 worker 节点正交，传输不再是课程属性 ──────────────────

describe('课程不带传输/节点指针（防回流）', () => {
  it('push-config 不再导出「按课钉传输/钉节点」的写入口', () => {
    const src = code('stack/push-config.ts')
    // 为什么必须没有：这些函数写 `courses.<课>.push_node_url`，等于把「哪门课跑在哪台机器」
    // 做成课程属性——任何 worker 都该能接任何课程的活，二者正交。
    expect(src).not.toMatch(
      /export (async )?function (applyPushNodeConfig|configurePushEndpoint|findHealthyGpuPushNode|pushTargetFromConfig|applyLocalPushNodeConfig|localPushUrl)\b/,
    )
    expect(src).not.toContain('push_node_url')
  })

  it('类型表 / spec 面 / 启动面都没有传输耦合键（只有注释记录它们已退役）', () => {
    for (const rel of [
      'core/types.ts',
      'stack/specs.ts',
      'server/actions/preset.ts',
      'server/actions/workers.ts',
    ]) {
      const src = code(rel)
      expect(src).not.toContain('push_node_url')
      expect(src).not.toContain('remote_transport')
      // 旧的本机伪节点标记也不得复活（python 的 auto 会把它当真节点）
      expect(src).not.toContain('local_push')
    }
  })

  it('course-knobs 只把旧键当**清理名单**（不是读面：没有读者）', () => {
    const knobs = code('stack/course-knobs.ts')
    // 旧键在这里出现是有意的——pruneLegacyCourseKnobs 靠它把残留值从 rl-config 里剃掉。
    // 真正要防的是「有人又去读它」：本模块除删除外不得出现对这些键的取值。
    expect(knobs).toContain('LEGACY_COURSE_KEYS')
    expect(knobs).not.toMatch(/\[('|")remote_transport\1\]/)
    for (const key of ['push_node_url', 'remote_transport', 'remote_hub_url']) {
      const reads = knobs.match(new RegExp(`\\.${key}\\b`, 'g')) ?? []
      expect(reads.length).toBe(0)
    }
  })

  it('specs 不再注入 --remote-transport（传输裁决交回训练侧 auto）', () => {
    const specs = code('stack/specs.ts')
    expect(specs).not.toContain('--remote-transport')
    expect(specs).not.toContain('s.ppo')
  })
})

describe('启动训练不选模式（防回流）', () => {
  it('preset 只有一条编排：selfNode → hubServer → trainer', () => {
    const preset = code('server/actions/preset.ts')
    expect(preset).toContain("'selfNode',")
    expect(preset).toContain("'hubServer',")
    expect(preset).toContain("'trainingLoop',")
    expect(preset).not.toContain('workerServe')
  })

  it('控制台状态与视图里没有 trainerPpo（模式选择已退役）', () => {
    expect(code('server/actions/console-state.ts')).not.toMatch(/trainerPpo|trainer\.ppo/)
    expect(code('web/view/console-types.ts')).not.toContain('trainerPpo')
  })

  it('启动弹窗没有模式开关与 push 凭据输入（配置入口在 worker 登记面板）', () => {
    const modal = code('web/app/panels/TrainLaunchModal.tsx')
    // 保留的 SegmentedControl 只服务隧道/瘦身/rollout——不得再出现 pull/push/local 三选一
    expect(modal).not.toContain("label: 'Push'")
    expect(modal).not.toContain('readSavedMode')
    expect(modal).not.toContain('pushEndpoint')
    expect(modal).not.toContain('pushAuthKey')
    // 已退役的 pull/push 模式 localStorage 键（`tc.train.mode`）不得回流。
    // 注意：`TC_TRAIN_MODE` 是**另一个域**（在线/离线，2026-09-19），别把两者混为一谈。
    expect(modal).not.toContain('tc.train.mode')
    expect(modal).toContain("const TC_TRAIN_MODE = 'tc.trainMode'")
  })

  it('launch 动作面不再传 mode / endpoint / authKey', () => {
    const app = code('web/app/app.tsx')
    expect(app).not.toContain('pushEndpoint')
    expect(app).not.toMatch(/handleLaunch = async \(\s*mode/)
  })
})

// ────────────────── 接线回归：本机伪节点不在启动面/受管面 ──────────────────

describe('本机伪节点不在启动面/受管面（防回流）', () => {
  it('受管组件全集里没有 workerServe', () => {
    expect(ALL_COMPONENTS).not.toContain('workerServe')
    expect(COURSE_COMPONENTS).not.toContain('workerServe')
    // 账本枚举路径也管不到它（旧账本里的 workerServes 表已无读者）
    expect(registryTriples({ workerServes: { x: { pid: 1 } } } as never).length).toBe(0)
  })

  it('启动面：start/smoke 里没有它的分派分支', () => {
    expect(code('server/actions/start.ts')).not.toContain("case 'workerServe'")
    expect(code('server/actions/smoke.ts')).not.toContain('workerServe')
  })

  it('spec 面：没有它的 ProcSpec（伪节点由冒烟预演自起自停）', () => {
    const specs = code('stack/specs.ts')
    expect(specs).not.toContain('workerServeSpec')
    expect(specs).not.toContain('WORKER_SERVE_ENTRY')
    // 预演侧确实自起自停（同一份远端入口）
    const push = code('stack/push.ts')
    expect(push).toContain("'remote_worker_serve'")
    expect(push).toContain('killPid')
  })
})
