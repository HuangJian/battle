/** training-port-reclaim.test.ts — 「崩溃后手动重启失败」的护栏：启动前回收端口占用者。
 *
 *  2026-09-17 事故：hub-server 因 D9 内存闭锁（127.0.0.1 连续 5 次鉴权失败 → 封 3600s，
 *  封禁只住进程内存）变成「活着但不可健康」，进程仍占着课程 hub 端口；控制台「启动」
 *  spawn 的新实例被 python 侧的双监听守卫（`remote/_port_guard.ensure_port_free`）
 *  拒绝启动、秒退（控制台只报「启动即退出」）——**重启是唯一的解药，却被幸存者自己
 *  占着的端口挡死**，只能手动杀进程。修复 = spawn 前按端口回收幸存占用者
 *  （`dashboard/src/stack/hub.ts::reclaimPort`，接在 stepHubServer / stepSelfNode）。
 *
 *  纪律（同 training-multi-course.test.ts）：端口/容量一律从测试自造夹具推导，不写死
 *  线上配置；OS 进程表列举（lsof / netstat）不可得的平台，集成用例 skip（单测与接线
 *  门禁仍全跑）。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { portOwnerPids } from '../src/core/proc'
import { killPid, pidAlive, portListen, waitUntil } from '../src/core/net'
import { reclaimPort } from '../src/stack/hub'

// ────────────────────────── 单测：可注入依赖（跨平台确定性） ──────────────────────────

describe('reclaimPort（注入依赖）', () => {
  it('杀掉端口上的幸存占用者、跳过 console 自身、等端口释放', async () => {
    const killed: number[] = []
    const probed: number[] = []
    const struck = await reclaimPort(18787, {
      ownerPids: () => [4242, process.pid, 5353],
      kill: async (pid) => {
        killed.push(pid)
        return true
      },
      listening: async (p) => {
        probed.push(p)
        return false // 回收后端口已释放
      },
    })
    expect(killed).toEqual([4242, 5353]) // console 自身 PID 永不回收
    expect(struck).toEqual([4242, 5353])
    expect(probed).toEqual([18787]) // 有回收才探测释放
  })

  it('端口无占用者 → 不杀、不探测、返回空', async () => {
    let killCalls = 0
    let probeCalls = 0
    const struck = await reclaimPort(18788, {
      ownerPids: () => [],
      kill: async () => {
        killCalls++
        return true
      },
      listening: async () => {
        probeCalls++
        return true
      },
    })
    expect(struck).toEqual([])
    expect(killCalls).toBe(0)
    expect(probeCalls).toBe(0)
  })

  it('kill 抛错不炸（已死/权限不足）——仍返回清单、仍探测释放', async () => {
    let probed = 0
    const struck = await reclaimPort(18789, {
      ownerPids: () => [7777],
      kill: async () => {
        throw new Error('ESRCH')
      },
      listening: async () => {
        probed++
        return false
      },
    })
    expect(struck).toEqual([7777])
    expect(probed).toBe(1)
  })
})

// ────────────────────────── 集成：真实监听进程被回收（默认 OS 进程表） ──────────────────────────

/** 本平台能否列举端口占用者（POSIX 靠 lsof，Windows 靠 netstat -ano）。 */
const CAN_ENUMERATE = process.platform === 'win32' || !!Bun.which('lsof')
const integrationIt = CAN_ENUMERATE ? it : it.skip

/** 取一个本机空闲高端口（占用者探测触发，无固定等待）。 */
async function freePort(): Promise<number> {
  for (let i = 0; i < 20; i++) {
    const p = 21000 + Math.floor(Math.random() * 8000)
    if (!(await portListen(p))) return p
  }
  throw new Error('找不到空闲测试端口')
}

/** 起一个真实监听 127.0.0.1:port 的子进程（= 幸存 hub-server 的最小替身）。 */
function spawnListener(port: number) {
  return Bun.spawn(
    [
      process.execPath,
      '-e',
      `Bun.serve({ port: ${port}, hostname: '127.0.0.1', fetch() { return new Response('ok') } })`,
    ],
    { stdout: 'ignore', stderr: 'ignore' },
  )
}

describe('reclaimPort（真实监听进程）', () => {
  const started: number[] = []
  afterAll(async () => {
    for (const pid of started) {
      try {
        if (pidAlive(pid)) await killPid(pid)
      } catch {
        /* already dead */
      }
    }
  })

  integrationIt('回收占着端口的幸存进程：端口释放 + 进程死亡', async () => {
    const port = await freePort()
    const child = spawnListener(port)
    started.push(child.pid)
    const up = await waitUntil(() => portListen(port), 8000, 100)
    expect(up).toBe(true) // 幸存者确实在监听（否则本用例无意义）
    // 端口确实可归属（OS 进程表能列出占用者）——列不出说明平台探测不可信，跳过判定。
    if (portOwnerPids(port).length === 0) return

    const struck = await reclaimPort(port)
    expect(struck).toContain(child.pid)
    expect(await portListen(port)).toBe(false)
    expect(pidAlive(child.pid)).toBe(false)
  })
})

// ────────────────────────── 接线门禁（防 helper 写了却忘接线） ──────────────────────────

/** 取源码里某个导出函数的**函数体文本**（到下一条 `export async function` 为止）。 */
function bodyOf(src: string, fn: string): string {
  const start = src.indexOf(`export async function ${fn}`)
  expect(start).toBeGreaterThan(-1)
  const rest = src.indexOf('export async function ', start + 1)
  return src.slice(start, rest === -1 ? undefined : rest)
}

describe('接线：启动步骤在 spawn 前回收端口', () => {
  const src = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'stack', 'hub.ts'), 'utf-8')

  for (const fn of ['stepHubServer', 'stepSelfNode'] as const) {
    it(`${fn} 在 launchSpec 之前调用 reclaimPort`, () => {
      const body = bodyOf(src, fn)
      const call = body.indexOf('reclaimPort')
      const spawn = body.indexOf('launchSpec')
      expect(call).toBeGreaterThan(-1) // helper 必须被接线，否则形同虚设
      expect(spawn).toBeGreaterThan(-1)
      expect(call).toBeLessThan(spawn) // 且必须早于 spawn（先清口再起）
    })
  }
})
