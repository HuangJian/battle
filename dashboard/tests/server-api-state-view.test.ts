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
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { invalidatePpoAttributionMemo } from '../src/server/contribution'
import * as pool from '../src/server/pool-history'
import * as poolView from '../src/web/view'

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

  it('★M4：状态面不再带「意图 / 逐课 rollout 源」；存量 rollout_src=run 读成 local', async () => {
    // 旧 `courseRolloutSrc` / `courseModeIntents` 两个字段随模式语义退役（plan §3-M4）：
    // 「这门课归谁」不再是控制台的一个声明，而是 hub 的 hold 事实（逐课行随 /admin/queue）。
    // 这里钉的是「两个字段真的不见了」——留着就会有旧客户端读它做漂移判断（退役值 `run`
    // 的容忍读在 `rollout-src-launch-option.test.ts` 里逐条钉）。
    const before = readConfigText()
    try {
      const cfg = JSON.parse(before) as Record<string, unknown>
      writeFileSync(
        scratchConfig,
        JSON.stringify({ ...cfg, courses: { 'p4-fast': { rollout_src: 'run' } } }, null, 2),
        'utf-8',
      )
      const s = await api.buildStateView()
      expect(s).not.toHaveProperty('courseRolloutSrc')
      expect(s).not.toHaveProperty('courseModeIntents')
      // 弹窗那格还在（查看课程的生效值）——但它只回答「rollout 位置」，不再被拿去判接管。
      expect(['local', 'node', 'auto'] as string[]).toContain(String(s.modes.rolloutSrc))
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

// ────────────────────────── R1：请求路径零聚合（plan/dashboard-reload-perf W1） ──────────────────────────
// 病根：`state-view.ts` 曾在每次 `/api/state` 里**裸调** `aggregateNodeHistory()` +
// `buildContributionView()`（每请求 walk 37ms + PPO 8.8–100ms，每 30s 一次 1s 级同步冷算）。
// 修法：贡献度缩略挂 `getFleetProbes` 的 SWR 值（`computeFleetProbes` 在后台顺手产出），
// 请求路径只读缓存。下面用**逐调用计数器**做结构性断言（不靠墙钟）。

describe('console/api.buildStateView · 请求路径零聚合（reload-perf W1）', () => {
  it('G1：暖缓存后 `buildStateView` 一次聚合调用都没有（调用计数 = 0）', async () => {
    // 先暖：冷启动首调**允许**经 SWR 触发一次聚合（那是设计行为，不是回归）。
    //
    // ★ 2026-10-06（修波动），**硬作废再暖**：`getFleetProbes` 的 TTL 只有 5s
    //   （`SNAPSHOT_REFRESH_MS`），而 `swr-cache.get` 在「陈旧」时会**丢一次后台重算**
    //   ——那次重算同样调 `aggregateNodeHistory`，于是「暖」这一拍若没落到新鲜值，
    //   下面测量窗口里的计数就被后台重算染成 1。满载时单次 `buildStateView` 要 1–2.8s，
    //   5s 线随时被越过（实测：单跑本文件 4/4 绿，满载全量 ~50% 红）。
    //   `clear()` 后第一次 `get` **必须等**重算落地（`swr-cache` 语义③）⇒ 暖完缓存 `at`
    //   是新鲜的，窗口内不再触发后台重算。判据本身一字未改（仍是 calls/computes/bytesRead = 0），
    //   只是把「暖」这个前提真正兑现。
    api.invalidateSlowSnapshot()
    await api.buildStateView()
    pool.resetPoolHistoryCounters()
    await api.buildStateView()
    const c = pool.poolHistoryCounters()
    expect(c.calls).toBe(0)
    expect(c.computes).toBe(0)
    expect(c.bytesRead).toBe(0)
  })

  it('G1 反向守卫：`state-view.ts` 源码不得再出现裸调（结构断言，防回退）', () => {
    const raw = readFileSync(
      path.join(DASHBOARD_ROOT, 'src', 'server', 'api', 'state-view.ts'),
      'utf-8',
    )
    // 只看**代码行**（注释里会提到旧实现的名字来解释为什么删——那不是回退）。
    const code = raw
      .split('\n')
      .filter(
        (l) =>
          !l.trim().startsWith('//') && !l.trim().startsWith('*') && !l.trim().startsWith('/*'),
      )
      .join('\n')
    expect(code).not.toContain('aggregateNodeHistory')
    expect(code).not.toContain('buildContributionView')
    expect(code).not.toContain('inflightByWorkerFromQueue')
    expect(code).toContain('getFleetProbes(cfg)')
  })

  /** 池根夹具（`BCITY_POOL_DIR` 重定向 + 两侧聚合 memo 的清理）。
   *
   *  ★ 2026-10-10（flake 修复）：本用例比的是**两次独立聚合的逐字相等**——state 的 brief 产自
   *  `computeFleetProbes`、pool 视图的 contribution 产自 `getPoolProbes`，而两者的输入根是
   *  **同一个** `tmpPoolDir()`。真实池根在训练期间一直在长：两次聚合之间落盘的新局会把采样
   *  总数推高（实测 `total` 11617 ≠ 11576）⇒ 断言退化成时序竞态（同一份代码：16:00 全绿、
   *  19:30 连红两次）。冻结输入之后它才是确定性用例，还顺带能断言「不是空对空」。
   *
   *  与同文件「课程发现目录可重定向」用同一套纪律：夹具起手清 memo、`finally` 里恢复 env
   *  并**再清一次** memo（否则下一个用例会读到夹具根的那份聚合）。 */
  async function withPoolFixture(fn: () => Promise<void>): Promise<void> {
    const root = mkdtempSync(path.join(os.tmpdir(), 'bcity-stateview-pool-'))
    const prev = process.env.BCITY_POOL_DIR
    const nowMs = Date.now()
    /** 毫秒 → 'YYYY-MM-DD HH:MM:SS'（与 python 侧 `strftime` 写入同形）。 */
    const ts = (ms: number): string => {
      const d = new Date(ms)
      const p = (n: number): string => String(n).padStart(2, '0')
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(
        d.getMinutes(),
      )}:${p(d.getSeconds())}`
    }
    // 锚点：24h 滚动窗要吃「子日事件环」，环的下界由它给出（与 pool-history 的预筛用例同规）。
    mkdirSync(path.join(root, 'dist-agent'), { recursive: true })
    writeFileSync(path.join(root, 'dist-agent', 'pool-epoch.txt'), String(nowMs - 3_600_000))
    // 采样侧：一条流 4 局（fx-a 2 rollout + 1 eval；fx-b 1 rollout）⇒ 合计 4、top[0] = fx-a。
    const flow = path.join(root, 'fx-flow')
    mkdirSync(flow, { recursive: true })
    const meta = (node: string, mode: 'rollout' | 'eval'): string =>
      JSON.stringify({
        node,
        mode,
        it: 7,
        stage: 0,
        seed: 1,
        ok: true,
        elapsedSec: 1,
        ts: ts(nowMs - 60_000),
      })
    writeFileSync(
      path.join(flow, 'dist-agent-meta.jsonl'),
      `${[meta('fx-a', 'rollout'), meta('fx-a', 'rollout'), meta('fx-a', 'eval'), meta('fx-b', 'rollout')].join('\n')}\n`,
    )
    // PPO 侧：一份课程账本，`job_result_accepted`（带 worker）join `job_completed` ⇒ done=1。
    const course = path.join(root, 'fx-course')
    mkdirSync(course, { recursive: true })
    const sec = Math.floor((nowMs - 60_000) / 1000)
    writeFileSync(
      path.join(course, 'training_log.jsonl'),
      `${[
        JSON.stringify({
          event: 'job_result_accepted',
          job_id: 'fxj1',
          worker: 'fx-cloud',
          ts: sec,
        }),
        JSON.stringify({ event: 'job_completed', job_id: 'fxj1', ts: sec + 1 }),
      ].join('\n')}\n`,
    )
    process.env.BCITY_POOL_DIR = root
    pool.invalidateNodeHistoryMemo()
    invalidatePpoAttributionMemo()
    try {
      await fn()
    } finally {
      if (prev === undefined) delete process.env.BCITY_POOL_DIR
      else process.env.BCITY_POOL_DIR = prev
      pool.invalidateNodeHistoryMemo()
      invalidatePpoAttributionMemo()
      rmSync(root, { recursive: true, force: true })
    }
  }

  it('缩略与面板同源：brief 是 `/api/pool` 同一份聚合的裁剪（逐字相等）', async () => {
    await withPoolFixture(async () => {
      // 同一个窗口（首页 = 24h 滚动档，2026-10-03 用户口径取代 today）+ 同一套 N（采样 3 / PPO 全列）
      // 下，两个端点必须给出同一份数字——防「两份真相」。
      api.invalidateSlowSnapshot()
      // 先 pool 后 state：`fresh` 会把探测层与聚合 memo 一起硬清 ⇒ 这一份 fixture 聚合
      // 就是两边共用的那一份（state 侧走 memo 命中，不再重扫）。
      const view = await api.buildPoolView(true, '24h')
      expect(view.contribution).toBeTruthy()
      const s = await api.buildStateView()
      // 不是空对空：夹具那 4 局采样 + 1 个 PPO 完成必须真的**在两边**读出来
      expect(s.contributionBrief?.sampling.total).toBe(4)
      expect(s.contributionBrief?.ppo.totalDone).toBe(1)
      expect(s.contributionBrief).toEqual(
        poolView.compactSummary(view.contribution!, 3, poolView.BRIEF_ALL),
      )
    })
  })

  it('W4：冷启动保证在 `await reconcileWatch()` 链（先于 Bun.serve），不是刷新器首拍', () => {
    const src = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'server', 'server.ts'), 'utf-8')
    const lines = src.split('\n')
    const reconcileAt = lines.findIndex((l) => l.includes('await reconcileWatch()'))
    const serveAt = lines.findIndex((l) => l.includes('Bun.serve('))
    expect(reconcileAt).toBeGreaterThan(0)
    expect(serveAt).toBeGreaterThan(0)
    // 冷启动首帧不撞冷算的**唯一保证**：await 链先于监听端口。
    expect(reconcileAt).toBeLessThan(serveAt)
    // 刷新器首拍是 fire-and-forget（`void run()`），不得被当保证——注释里点明这条分工。
    expect(src).toContain('唯一保证')
  })

  it('A6 护栏：brief 产出路径不得 await hub 探测（用 peekHubAdmin，源码断言）', () => {
    const src = readFileSync(
      path.join(DASHBOARD_ROOT, 'src', 'server', 'api', 'snapshot-cache.ts'),
      'utf-8',
    )
    expect(src).toContain('peekHubAdmin(')
    expect(src).not.toContain('await getHubAdmin(')
    const ov = readFileSync(
      path.join(DASHBOARD_ROOT, 'src', 'server', 'api', 'overview.ts'),
      'utf-8',
    )
    expect(ov).toContain('export function peekHubAdmin()')
  })
})
