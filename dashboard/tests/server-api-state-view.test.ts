/**
 * server-api-state-view.test.ts — 快照结构（组件 / 节点 / 模式 / 指标）与课程发现
 *
 * 分层：src/server/api/state-view.ts（buildStateView）
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { api, readConfigText, scratchConfig } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'

describe('console/api.buildStateView', () => {
  it('快照包含六个组件（含 2026-09-15 独立出来的 localWorker）、节点表与模式区块', async () => {
    const s = await api.buildStateView()
    const keys = s.components.map((c) => c.key) as string[]
    expect(keys.sort()).toEqual(
      ['cloudflared', 'hubServer', 'localWorker', 'selfNode', 'trainingLoop', 'workerServe'].sort(),
    )
    for (const c of s.components) {
      expect(['running', 'stopped', 'exited']).toContain(c.status)
      expect(c.label.length).toBeGreaterThan(0)
    }
    expect(s.nodes.length).toBeGreaterThan(0)
    expect(['pull', 'push', 'local']).toContain(s.modes.trainerPpo)
    expect([0, 1]).toContain(s.modes.stream)
    expect([0, 1]).toContain(s.modes.doubleBuffer)
    expect(s.courses).toBeInstanceOf(Array)
  })

  it('课程发现含 curricula/*.jsonc（即使 tmp 无日志）', () => {
    // max 用足量窗口（500）：课程目录随阶梯（+20）与经典（+35）持续增长，
    // 固定小窗口会把任何固定课程名挤出断言范围（p4-fast 曾在 12/50 窗口两次被挤出）。
    const courses = api.discoverCourses(500)
    // curricula/ 至少有 p4-fast.jsonc 等；不强制非空，但类型必须对
    for (const c of courses) expect(typeof c).toBe('string')
    // p4-fast 在 curricula/ 有定义但 tmp/ 可能无日志——应被补充进列表
    expect(courses).toContain('p4-fast')
  })

  it('默认窗口必须包含新建 BC 课程 bc-c4-v3（不被更晚写入的课程挤出）', () => {
    // 回归（2026-09-14）：课程目录随阶梯（+20）/经典（+35）/BC（*.bc.jsonc）持续增长，
    // discoverCourses 默认 12 的小窗口被 ladder-*（mtime 23:32 的 20 个课程）占满，
    // bc-c4-v3（23:14）连 bc-c4/c6-chip 一并从课程 select 消失——BC 训练无法在控制台开启。
    const courses = api.discoverCourses()
    expect(courses).toContain('bc-c4-v3')
  })

  it('课程发现目录可重定向：新 BC 课程在大量更新课程中不被默认窗口挤出', () => {
    const prevTmp = process.env.BCITY_TMP_LOGS_DIR
    const prevCur = process.env.BCITY_CURRICULA_DIR
    const tmpRoot = mkdtempSync(path.join(os.tmpdir(), 'bcity-discover-tmp-'))
    const curRoot = mkdtempSync(path.join(os.tmpdir(), 'bcity-discover-cur-'))
    try {
      // tmp/：25 个带 training_log.jsonl 的课程目录（超过旧默认窗口 12 就能把新课程挤出）
      for (let i = 0; i < 25; i++) {
        const d = path.join(tmpRoot, `run-c${String(i).padStart(2, '0')}`)
        mkdirSync(d, { recursive: true })
        writeFileSync(path.join(d, 'training_log.jsonl'), 'x\n')
      }
      // curricula/：再多 30 个更新课程 + 一个新创建的 BC 课程（.bc.jsonc 后缀即课程键）
      for (let i = 0; i < 30; i++) {
        writeFileSync(path.join(curRoot, `z-new-${String(i).padStart(2, '0')}.jsonc`), '{}\n')
      }
      const bcCourse = 'bc-c4-v3'
      writeFileSync(path.join(curRoot, `${bcCourse}.bc.jsonc`), '{}\n')
      process.env.BCITY_TMP_LOGS_DIR = tmpRoot
      process.env.BCITY_CURRICULA_DIR = curRoot
      const courses = api.discoverCourses()
      expect(courses).toContain(bcCourse)
      expect(courses.length).toBeGreaterThan(30) // 不被固定小窗口截断
      expect(courses.map((c) => c.replace(/\.bc\.jsonc$/, ''))).toContain(bcCourse) // 键已去后缀
    } finally {
      rmSync(tmpRoot, { recursive: true, force: true })
      rmSync(curRoot, { recursive: true, force: true })
      if (prevTmp === undefined) delete process.env.BCITY_TMP_LOGS_DIR
      else process.env.BCITY_TMP_LOGS_DIR = prevTmp
      if (prevCur === undefined) delete process.env.BCITY_CURRICULA_DIR
      else process.env.BCITY_CURRICULA_DIR = prevCur
    }
  })

  it('push 执行面（2026-09-15）：快照给出「job 推给本机还是云机」的数据源', async () => {
    // 在 scratch 配置上临时种一个云 push 节点 + 两个课程键（course 名唯一，不与他人争缓存）。
    const prev = readConfigText()
    const cloud = 'push-probe-cloud'
    const none = 'push-probe-none'
    try {
      const cfg = JSON.parse(prev) as Record<string, unknown>
      cfg.courses = {
        ...((cfg.courses as Record<string, unknown>) ?? {}),
        [cloud]: { push_node_url: 'https://127.0.0.1:1' },
        [none]: { slot: 1 },
      }
      cfg.nodes = [
        ...((cfg.nodes as unknown[]) ?? []),
        {
          id: 'probe-cloud',
          url: 'https://127.0.0.1:1',
          authKey: 'k',
          concurrency: 1,
          enabled: true,
          gpu_push: true,
        },
      ]
      writeFileSync(scratchConfig, JSON.stringify(cfg, null, 2))
      const s = await api.buildStateView(cloud)
      // kind 由 config 认领（`push_node_url` → gpu_push 节点）；healthy 是 /ping 直探
      // （127.0.0.1:1 无人监听 → false）；active=false（trainer 未以 push 模式在跑）。
      expect(s.pushTarget?.kind).toBe('cloud')
      expect(s.pushTarget?.nodeId).toBe('probe-cloud')
      expect(s.pushTarget?.healthy).toBe(false)
      expect(s.pushTarget?.active).toBe(false)
      // 未配置 push 目标 → null（非 push 场景卡片不出徽章）
      const s2 = await api.buildStateView(none)
      expect(s2.pushTarget ?? null).toBeNull()
    } finally {
      writeFileSync(scratchConfig, prev)
    }
  })

  it('isBc stamp（2026-09-14 首页 BC/RL 分流）：BC 课 true / RL 课 false', async () => {
    // bc-c4-v3 = *.bc.jsonc 课程（真实仓库 curricula/）；p4-fast = 经典 RL 课程。
    const bc = await api.buildStateView('bc-c4-v3')
    expect(bc.isBc).toBe(true)
    const rl = await api.buildStateView('p4-fast')
    expect(rl.isBc).toBe(false)
  })
})
