/** trainer-lock-release.test.ts — 「停止 trainer」必须把 run_rl/run_bc 单实例锁一起释放。
 *
 *  2026-09-17 事故（重启障碍清单第 3 条）：账本 pid 与**锁持有者**可以是两个不同进程
 *  （崩溃残留 / PID 复用 / 控制台重启竞态）。旧停止路径只杀账本 pid ⇒ 锁里的存活持有者
 *  把「停止 → 启动」永久卡死：python 侧只回「先停止在跑训练（或删除该锁文件）」，而控制台
 *  没有任何入口能删它 —— 唯一出路是人工 rm（`run_rl._acquire_run_rl_lock` /
 *  `run_bc` 的同一把锁）。
 *
 *  修复 = 停止 trainer 时释放本课两把锁，且**先核验进程身份**（命令行必须命中本课
 *  `run_rl.py` / `run_bc.py`）：锁里的 PID 可能已被系统复用给无辜进程，只看「活着」就杀
 *  就是误伤。身份不符 → 不杀不删，fail-closed 交人工确认。
 *
 *  纪律（同 training-port-reclaim.test.ts）：单测走可注入依赖，绝不写真实锁文件 / 不碰
 *  真实进程；接线门禁直接读源码，防 helper 写了却忘接线。
 */

import { describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { DASHBOARD_ROOT, NN_TRAINING } from '../src/core/paths'
import {
  TRAINER_SCRIPT,
  readLockHolder,
  releaseTrainerLock,
  releaseTrainerLocks,
} from '../src/launch/cli'

const COURSE = 'x1-rebirth'
const LOCK = path.join(NN_TRAINING, `.run_rl.${COURSE}.lock`)

/** 注入夹具：默认「锁文件存在、持有者已死」——各用例只改自己关心的那一条。
 *
 *  `state.alive` 是**存活持有者 pid 集合**（多把锁/两个进程的用例需要分别跟踪）；默认
 *  `killTrainer` 会清空它（= 真被停掉），于是「核验通过 → 释放」不必空烧探测窗口。
 *  `killWaitMs` 缩到 100ms，免测试被 5s 存活窗口拖慢（真跑保持 5s）。 */
function io(over: Partial<Parameters<typeof releaseTrainerLock>[2]> = {}) {
  const calls = { killed: [] as string[][], removed: 0 }
  const state = { alive: new Set<number>() }
  const deps = {
    lockPath: LOCK,
    exists: () => true,
    holderOf: () => ({ pid: 4242, exe: '/venv/python' }),
    alive: (pid: number) => state.alive.has(pid),
    cmdlineOf: () => null,
    killTrainer: async (script: string, course: string) => {
      calls.killed.push([script, course])
      state.alive.clear() // 夹具默认单持有者；多持有者用例自行覆写
    },
    removeLock: () => {
      calls.removed++
    },
    killWaitMs: 100,
    ...over,
  }
  return { deps, calls, state }
}

// ────────────────────────── 单测：锁文件解析 ──────────────────────────

describe('readLockHolder', () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'trainer-lock-'))

  it('解析 PID|EXE|TS 与裸 PID；残缺/不存在 → null', () => {
    const full = path.join(dir, 'full.lock')
    writeFileSync(full, '12345|/venv/python|1726000000')
    expect(readLockHolder(full)).toEqual({ pid: 12345, exe: '/venv/python' })

    const bare = path.join(dir, 'bare.lock')
    writeFileSync(bare, '777')
    expect(readLockHolder(bare)).toEqual({ pid: 777, exe: '' })

    const junk = path.join(dir, 'junk.lock')
    writeFileSync(junk, 'not-a-pid')
    expect(readLockHolder(junk)).toBeNull()

    expect(readLockHolder(path.join(dir, 'missing.lock'))).toBeNull()
    rmSync(dir, { recursive: true, force: true })
  })
})

// ────────────────────────── 单测：释放四分支（注入依赖） ──────────────────────────

describe('releaseTrainerLock（注入依赖）', () => {
  it('无锁文件 → 空串（不制造噪音）', async () => {
    const { deps, calls } = io({ exists: () => false })
    expect(await releaseTrainerLock('run_rl', COURSE, deps)).toBe('')
    expect(calls.killed).toEqual([])
    expect(calls.removed).toBe(0)
  })

  it('课程名非法（停止动作不校验课程名）→ 无害退场，绝不抛错', async () => {
    // 回归：停止路径不校验课程名（历史行为），锁释放不能因此把「停止」变成失败
    const { deps, calls } = io({ lockPath: undefined })
    expect(await releaseTrainerLock('run_rl', 'no such course!', deps)).toBe('')
    expect(await releaseTrainerLocks('no such course!', deps)).toEqual([])
    expect(calls.killed).toEqual([])
    expect(calls.removed).toBe(0)
  })

  it('持有者已死 → 清理陈旧锁（不杀任何进程）', async () => {
    const { deps, calls } = io()
    const note = await releaseTrainerLock('run_rl', COURSE, deps)
    expect(note).toContain('陈旧锁已清理')
    expect(note).toContain(`.run_rl.${COURSE}.lock`)
    expect(calls.killed).toEqual([]) // 死人不杀
    expect(calls.removed).toBe(1)
  })

  it('持有者活着但身份不符（PID 复用）→ 不杀、不删、点名告警', async () => {
    const { deps, calls, state } = io({ cmdlineOf: () => 'python some-other-tool.py --watch' })
    state.alive.add(4242)
    const note = await releaseTrainerLock('run_rl', COURSE, deps)
    expect(note).toContain('身份未通过核验')
    expect(calls.killed).toEqual([])
    expect(calls.removed).toBe(0) // fail-closed：锁留在原处等人工确认
  })

  it('身份读不到（进程表不可用）→ 同样 fail-closed，不放行、不错杀', async () => {
    const { deps, calls, state } = io()
    state.alive.add(4242)
    expect(await releaseTrainerLock('run_bc', COURSE, deps)).toContain('身份未通过核验')
    expect(calls.killed).toEqual([])
    expect(calls.removed).toBe(0)
  })

  it('身份核验通过 → 停掉本课 trainer 并删锁（脚本按 kind 取）', async () => {
    const { deps, calls, state } = io({
      cmdlineOf: () => `python -u run_rl.py --course ${COURSE}`,
    })
    state.alive.add(4242)
    const note = await releaseTrainerLock('run_rl', COURSE, deps)
    expect(calls.killed).toEqual([['run_rl.py', COURSE]]) // 只有核验过的那个脚本被停
    expect(calls.removed).toBe(1)
    expect(note).toContain('已释放')
  })

  it('核验通过但没停掉（kill 无效）→ 不删锁、如实报告', async () => {
    const { deps, calls, state } = io({
      cmdlineOf: () => `python -u run_rl.py --course ${COURSE}`,
      killTrainer: async () => {
        /* no-op：模拟 kill 之后进程仍活着 */
      },
    })
    state.alive.add(4242)
    const note = await releaseTrainerLock('run_rl', COURSE, deps)
    expect(note).toContain('未能停止')
    expect(calls.removed).toBe(0) // 进程还在，锁留着（免双开）
  })

  it('别课 trainer 的同名锁绝不误杀（身份核验按 course 词边界）', async () => {
    const { deps, calls, state } = io({
      cmdlineOf: () => 'python -u run_rl.py --course other-course',
    })
    state.alive.add(4242)
    expect(await releaseTrainerLock('run_rl', COURSE, deps)).toContain('身份未通过核验')
    expect(calls.killed).toEqual([])
  })

  it('两把锁分别按 kind 取脚本：run_bc → run_bc.py', async () => {
    const seen: string[][] = []
    const { deps, state } = io({
      cmdlineOf: () => `python -u run_bc.py --course ${COURSE}`,
      killTrainer: async (script, course) => {
        seen.push([script, course])
        state.alive.clear()
      },
    })
    state.alive.add(4242)
    await releaseTrainerLock('run_bc', COURSE, deps)
    expect(seen).toEqual([['run_bc.py', COURSE]])
    expect(TRAINER_SCRIPT.run_bc).toBe('run_bc.py')
  })

  it('releaseTrainerLocks = run_rl + run_bc 两把（各自的持有者/命令行各自核验）', async () => {
    const seen: string[] = []
    const { deps, state } = io({
      // 锁路径走真实派生（lockPathFor(course, kind)）——两把锁的持有者是两个进程，
      // 命令行按锁名区分，验证 kind → script 的映射
      lockPath: undefined,
      holderOf: (p) => (p.includes('run_bc') ? { pid: 5353, exe: '' } : { pid: 4242, exe: '' }),
      cmdlineOf: (pid) =>
        pid === 5353
          ? `python -u run_bc.py --course ${COURSE}`
          : `python -u run_rl.py --course ${COURSE}`,
      killTrainer: async (script) => {
        seen.push(script)
        // 只让本 kind 的持有者退出（另一把锁的持有者是另一个活进程）
        state.alive.delete(script === 'run_bc.py' ? 5353 : 4242)
      },
    })
    state.alive.add(4242)
    state.alive.add(5353)
    const notes = await releaseTrainerLocks(COURSE, deps)
    expect(seen).toEqual(['run_rl.py', 'run_bc.py'])
    expect(notes).toHaveLength(2)
    expect(notes.every((n) => n.includes('已释放'))).toBe(true)
  })
})

// ────────────────────────── 接线门禁：停止路径必须真的调用它 ──────────────────────────

describe('接线：停止 trainer 走锁释放', () => {
  const stopSrc = readFileSync(
    path.join(DASHBOARD_ROOT, 'src', 'server', 'actions', 'stop.ts'),
    'utf-8',
  )

  it('stopComponent 引入并调用 releaseTrainerLocks，且限定在 trainingLoop', () => {
    const start = stopSrc.indexOf('export async function stopComponent')
    const end = stopSrc.indexOf('export async function stopAll')
    expect(start).toBeGreaterThan(-1)
    const body = stopSrc.slice(start, end === -1 ? undefined : end)
    expect(body).toContain('releaseTrainerLocks(') // helper 被接线，否则形同虚设
    expect(body).toContain("key !== 'trainingLoop'") // 只对 trainer 生效
  })

  it('调用点在两条返回路径上（有账本 / 无账本都要释放）', () => {
    const calls = stopSrc.match(/await releaseLocks\(/g) ?? []
    expect(calls.length).toBeGreaterThanOrEqual(2)
  })
})
