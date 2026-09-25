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
  it('快照包含五个受管组件（含 2026-09-15 独立出来的 localWorker）、节点表与模式区块', async () => {
    const s = await api.buildStateView()
    const keys = s.components.map((c) => c.key) as string[]
    // 本机伪节点（workerServe）2026-09-19 已退出受管组件：它只服务 trainingLoop 冒烟预演，
    // 由预演自起自停（没有卡片、账本、日志页入口）。
    expect(keys.sort()).toEqual(
      ['cloudflared', 'hubServer', 'localWorker', 'selfNode', 'trainingLoop'].sort(),
    )
    for (const c of s.components) {
      expect(['running', 'stopped', 'exited']).toContain(c.status)
      expect(c.label.length).toBeGreaterThan(0)
    }
    expect(s.nodes.length).toBeGreaterThan(0)
    // 启动训练不选模式（2026-09-19）：modes 里不再有 trainerPpo；执行面看 `pushFleet`
    expect(s.modes).not.toHaveProperty('trainerPpo')
    expect(['hub-dispatch', 'direct-push', 'pull']).toContain(s.pushFleet?.mode ?? 'pull')
    expect([0, 1]).toContain(s.modes.stream)
    expect([0, 1]).toContain(s.modes.doubleBuffer)
    expect(s.courses).toBeInstanceOf(Array)
  })

  it('★2026-09-24 courseRolloutSrc：**逐课**生效 rollout 源（课程矩阵要按行判「配置没跟上」）', async () => {
    // 为什么必须逐课：`modes.rolloutSrc` 只有**查看课程**一个（开课弹窗用），而那张表是逐行
    // 全课表——非当前课程的行没有这一格，就判不出「hub 与意图都回到在线、配置还是 run」。
    const before = readConfigText()
    try {
      const cfg = JSON.parse(before) as Record<string, unknown>
      writeFileSync(
        scratchConfig,
        JSON.stringify({ ...cfg, courses: { 'p4-fast': { rollout_src: 'run' } } }, null, 2),
        'utf-8',
      )
      const s = await api.buildStateView()
      expect(s.courseRolloutSrc?.['p4-fast']).toBe('run')
      // 没有课程级覆盖的课回落到全局/缺省（local）—— 不编一个假的「离线」
      for (const c of s.courses.filter((x) => x !== 'p4-fast')) {
        expect(s.courseRolloutSrc?.[c]).toBe('local')
      }
    } finally {
      writeFileSync(scratchConfig, before, 'utf-8')
    }
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

  it('push 执行面（2026-09-19）：快照按**机群级**事实推执行面，不再按课程认领节点', async () => {
    // 在 scratch 配置上临时种一个云 push 节点（课程侧一个字不配）+ 两个课程名。
    const prev = readConfigText()
    const cloud = 'push-probe-cloud'
    const none = 'push-probe-none'
    try {
      const cfg = JSON.parse(prev) as Record<string, unknown>
      const courses = (cfg.courses as Record<string, unknown>) ?? {}
      cfg.courses = { ...courses, [cloud]: { slot: 1 }, [none]: { slot: 1 } }
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
      // ★ 配置被改 → **机群级**缓存（节点表 / push 机群）必须显式作废（2026-09-22）：
      //   它现在跨课程共用（切课程不再重探），不再靠「换一门没看过的课」顺带重算。
      //   与 `server-api-local-chips.test.ts` 的写法同规：改了 rl-config 就作废。
      api.invalidateSlowSnapshot()
      // 课程侧**一个字都没配**（两个课程块只有 slot）——执行面仍然被登记节点抬起来，
      // 这正是「课程 ↔ worker 节点正交」：同一个机群服务所有课程，不按课认领。
      expect(JSON.stringify(cfg.courses)).not.toContain('push_node_url')
      const s = await api.buildStateView(cloud)
      // 执行面 = 登记事实：有 gpu_push 节点 ⇒ hub 派发/直推（不再有「这门课指向谁」）。
      expect(['hub-dispatch', 'direct-push']).toContain(s.pushFleet?.mode ?? '')
      expect(s.pushFleet?.nodes).toBeGreaterThan(0)
      // 逐节点探活结果在队列里（127.0.0.1:1 无人监听 → false）
      const probe = s.pushFleet?.probes.find((p) => p.id === 'probe-cloud')
      expect(probe?.healthy).toBe(false)
    } finally {
      writeFileSync(scratchConfig, prev)
      api.invalidateSlowSnapshot() // 还原配置同样要作废（否则机群级缓存带着种下的节点泄漏给后序用例）
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
