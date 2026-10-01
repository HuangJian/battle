/**
 * single-instance.test.ts — 「一个 node id 只准一个 agent 在听」的判定表（plan/sampler-single-instance.plan.md §3.5）。
 *
 * 背景：`SO_REUSEPORT`（Linux 上为重启链而开）让第二个实例绑同一端口**静默成功** ⇒ 一台节点两个
 * 代码版本同时服务、`codeHash` 门变成抽签、旧版本 shard 进 payload 把云 worker 炸掉。护栏改为
 * 应用层判定（锁文件 + HTTP 探活），本文件逐行钉住判定表。
 */
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  HANDOFF_ENV,
  LOCK_NAME,
  PROBE_TIMEOUT_MS,
  REFUSE_EXIT_CODE,
  decideSingleInstance,
  emptyProbe,
  instanceGuardOf,
  probeFromStatusJson,
  type GuardInput,
} from '../../tools/agent/single-instance'

const SELF = 1000
const OTHER = 2000
const PARENT = 3000

const hit = (
  pid: number,
  over: Partial<{ codeHash: string; bootId: string; uptimeSec: number }> = {},
) => ({
  pid,
  codeHash: over.codeHash ?? 'c3cc5d88'.padEnd(64, '0'),
  bootId: over.bootId ?? 'deadbeef',
  ...(over.uptimeSec !== undefined ? { uptimeSec: over.uptimeSec } : {}),
})

const base = (over: Partial<GuardInput> = {}): GuardInput => ({
  selfPid: SELF,
  lock: null,
  probe: emptyProbe(),
  handoff: false,
  takeover: false,
  handoffParentPid: null,
  handoffBootId: null,
  ...over,
})

describe('decideSingleInstance — 判定表（§3.1，按序第一命中）', () => {
  it('行 1：--takeover ⇒ serve（唯一的无标记接管入口）', () => {
    const v = decideSingleInstance(base({ takeover: true, probe: hit(OTHER), lock: null }))
    expect(v.action).toBe('serve')
    expect(v.reason).toContain('takeover')
  })

  it('行 2：探活命中别人 + 非交接 ⇒ refuse', () => {
    const v = decideSingleInstance(base({ probe: hit(OTHER, { uptimeSec: 50600 }) }))
    expect(v.action).toBe('refuse')
  })

  it('行 2：探活命中自己（同 pid 探到自己）不算冲突 ⇒ 落到后续行', () => {
    const v = decideSingleInstance(base({ probe: hit(SELF) }))
    expect(v.action).toBe('serve')
  })

  it('行 3：探活落空但锁在活进程手上 ⇒ refuse（保守，不冒两个实例的险）', () => {
    const v = decideSingleInstance(base({ lock: { pid: OTHER, pidAlive: true, isSelf: false } }))
    expect(v.action).toBe('refuse')
    expect(v.reason).toContain(`pid=${OTHER}`)
  })

  it('行 4：锁陈旧（owner 已死）⇒ serve-took-over-stale（自愈）', () => {
    const v = decideSingleInstance(base({ lock: { pid: OTHER, pidAlive: false, isSelf: false } }))
    expect(v.action).toBe('serve-took-over-stale')
    expect(v.reason).toContain(`pid=${OTHER}`)
  })

  it('行 4：锁属于本 pid 的上一代（PID 复用 / 自己残留）⇒ serve-took-over-stale', () => {
    const v = decideSingleInstance(base({ lock: { pid: SELF, pidAlive: true, isSelf: true } }))
    expect(v.action).toBe('serve-took-over-stale')
  })

  it('行 3：锁在别人手上且**自己就是交接 child** 但父子核对不通过 ⇒ 仍 refuse', () => {
    const v = decideSingleInstance(
      base({
        handoff: true,
        handoffParentPid: PARENT,
        lock: { pid: OTHER, pidAlive: true, isSelf: false },
      }),
    )
    expect(v.action).toBe('refuse')
  })

  it('行 3：交接 child + 锁的 owner 就是父进程 ⇒ serve-handoff', () => {
    const v = decideSingleInstance(
      base({
        handoff: true,
        handoffParentPid: PARENT,
        lock: { pid: PARENT, pidAlive: true, isSelf: false },
      }),
    )
    expect(v.action).toBe('serve-handoff')
  })

  it('行 5：无锁、探不到 ⇒ serve（fresh）', () => {
    const v = decideSingleInstance(base())
    expect(v.action).toBe('serve')
    expect(v.reason).toBe('fresh')
  })
})

describe('refuse 的 reason——「含既有实例身份 + 怎么停它」（§3.5-2）', () => {
  it('含 pid / codeHash / uptime，并给出 kill 指路', () => {
    const v = decideSingleInstance(
      base({
        probe: hit(OTHER, { codeHash: 'c3cc5d88aabbccdd'.padEnd(64, '0'), uptimeSec: 50600 }),
      }),
    )
    expect(v.action).toBe('refuse')
    expect(v.reason).toContain(`pid=${OTHER}`)
    expect(v.reason).toContain('codeHash=c3cc5d88…')
    expect(v.reason).toContain('uptime≈50600s')
    expect(v.reason).toContain(`kill ${OTHER}`)
    expect(v.reason).toContain('--takeover')
  })
})

describe('交接父子核对（§3.5-3）：child 只准接管自己的父进程', () => {
  it('探活 pid = ppid ⇒ serve-handoff', () => {
    const v = decideSingleInstance(
      base({ handoff: true, handoffParentPid: PARENT, probe: hit(PARENT) }),
    )
    expect(v.action).toBe('serve-handoff')
  })

  it('探活 pid ≠ ppid 且 bootId 对不上（误传到别的 agent）⇒ refuse', () => {
    const v = decideSingleInstance(
      base({
        handoff: true,
        handoffParentPid: PARENT,
        handoffBootId: 'aaaaaaaa',
        probe: hit(OTHER, { bootId: 'bbbbbbbb' }),
      }),
    )
    expect(v.action).toBe('refuse')
  })

  it('ppid 已被 reinit 收走（父进程先退），但 bootId 对上 ⇒ serve-handoff', () => {
    const v = decideSingleInstance(
      base({
        handoff: true,
        handoffParentPid: 1, // 父进程已 exit ⇒ child 被 reinit 收养
        handoffBootId: 'aaaaaaaa',
        probe: hit(OTHER, { bootId: 'aaaaaaaa' }),
      }),
    )
    expect(v.action).toBe('serve-handoff')
  })

  it('有交接标记但既无 ppid 命中也无 bootId ⇒ refuse', () => {
    const v = decideSingleInstance(base({ handoff: true, probe: hit(OTHER) }))
    expect(v.action).toBe('refuse')
  })

  it('交接标记不能顶掉 --takeover 之外的语义：无标记时 bootId 对上也 refuse', () => {
    const v = decideSingleInstance(
      base({
        handoff: false,
        handoffParentPid: PARENT,
        handoffBootId: 'aaaaaaaa',
        probe: hit(PARENT),
      }),
    )
    expect(v.action).toBe('refuse')
  })
})

describe('probeFromStatusJson——兼容未升级的旧 agent（§1.2-④ 的跨版本窗口）', () => {
  it('读新字段 pid/bootId/codeHash/uptimeSec', () => {
    expect(
      probeFromStatusJson({
        pid: 1234,
        bootId: 'abcd1234',
        codeHash: 'f'.repeat(64),
        uptimeSec: 12,
        nodeId: 'bun-1234',
      }),
    ).toEqual({ pid: 1234, bootId: 'abcd1234', codeHash: 'f'.repeat(64), uptimeSec: 12 })
  })

  it('旧 agent 没有 pid 字段：从 nodeId "bun-<pid>" 回推', () => {
    expect(probeFromStatusJson({ nodeId: 'bun-26105', codeHash: 'a'.repeat(64) }).pid).toBe(26105)
  })

  it('拿不到可判 pid 的响应 ⇒ 一律当「探不到」（拿不到应答不误判为有实例）', () => {
    expect(probeFromStatusJson({ codeHash: 'a'.repeat(64) }).pid).toBeNull()
    expect(probeFromStatusJson(null)).toEqual(emptyProbe())
    expect(probeFromStatusJson('nope').pid).toBeNull()
    expect(probeFromStatusJson({ nodeId: 'bun-abc' }).pid).toBeNull()
  })
})

describe('观测映射与常量（§3.4）', () => {
  it('action → instanceGuard（refuse 不会起听，故不在状态里）', () => {
    expect(instanceGuardOf('serve')).toBe('fresh')
    expect(instanceGuardOf('serve-handoff')).toBe('handoff')
    expect(instanceGuardOf('serve-took-over-stale')).toBe('took-over-stale')
  })

  it('拒起退出码非 0；探活超时有上界；交接标记是环境变量名', () => {
    expect(REFUSE_EXIT_CODE).not.toBe(0)
    expect(PROBE_TIMEOUT_MS).toBeGreaterThan(0)
    expect(PROBE_TIMEOUT_MS).toBeLessThanOrEqual(2000)
    expect(HANDOFF_ENV).toBe('SAMPLER_HANDOFF')
    expect(LOCK_NAME).toBe('agent.lock')
  })
})

describe("'wx' 独占创建是唯一互斥原语（§3.1 原子性；§3.5-5）", () => {
  it('同目录第二次创建失败（EEXIST）——冷启动竞态只有一个赢家', () => {
    const dir = mkdtempSync(join(tmpdir(), 'single-instance-'))
    const lock = join(dir, LOCK_NAME)
    try {
      writeFileSync(lock, JSON.stringify({ pid: SELF }), { flag: 'wx' })
      expect(JSON.parse(readFileSync(lock, 'utf8')).pid).toBe(SELF)
      let code = ''
      try {
        writeFileSync(lock, JSON.stringify({ pid: OTHER }), { flag: 'wx' })
      } catch (e) {
        code = (e as { code?: string }).code ?? ''
      }
      expect(code).toBe('EEXIST')
      // 失败者不得覆盖赢家的锁
      expect(JSON.parse(readFileSync(lock, 'utf8')).pid).toBe(SELF)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
