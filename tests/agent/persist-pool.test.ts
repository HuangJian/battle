import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { REPO_ROOT } from '../../tools/agent/codehash-files'
import {
  PERSIST_DISABLE_STREAK,
  PERSIST_MODE_BY_ENTRY,
  PERSIST_REARM_MS,
  PERSIST_SERVE_ENTRY,
  newPersistBreaker,
  notePersistAttempt,
  persistModeFor,
  persistSpawningAllowed,
  type PersistBreaker,
} from '../../tools/agent/persist-pool'

/**
 * 长驻池的**纯策略 + 接线钉子**。
 *
 * 这里有两条互相独立的保障：
 *   1. **熔断语义**（`notePersistAttempt` / `persistSpawningAllowed`）：只停补位、不通停整池。
 *   2. **同质入口**（`PERSIST_SERVE_ENTRY` + `PERSIST_MODE_BY_ENTRY` / `persistModeFor`）：池里
 *      没有「腿」，所以「预热猜错腿」（a95 实测 3.0s/局 vs 对腿 1.39s/局）与「换腿退役重补」
 *      （一次 ~39s）这一整类问题在结构上不存在。**被删掉的那套策略不许悄悄回来**——源级钉子
 *      就钉这个。
 * 分派本身的等价性（同一进程交替跑两种 mode、产物逐字节一致）在 `tests/serve-any.test.ts`。
 */

describe('persist 熔断状态机（只停补位，不通停整池）', () => {
  const t0 = 1_000_000

  it('新状态：未停、无连败、允许新建', () => {
    const b = newPersistBreaker()
    expect(b).toEqual({ streak: 0, stopped: false, stoppedAtMs: 0 })
    expect(persistSpawningAllowed(b, t0)).toBe(true)
  })

  it('busy / skipped / taskErr 不改账（背压、未启用、任务级错误都不是池的健康信号）', () => {
    const b: PersistBreaker = { streak: 2, stopped: false, stoppedAtMs: 0 }
    expect(notePersistAttempt(b, 'busy', t0)).toBe(b)
    expect(notePersistAttempt(b, 'skipped', t0)).toBe(b)
    // taskErr = worker 报「这一局错了」而 worker 自己健康（serve-loop 仍在等下一行）。
    // 该不该算池的账由调用方看一次性兜底结果定（两路都挂 = 任务/数据问题）。
    expect(notePersistAttempt(b, 'taskErr', t0)).toBe(b)
    // 回归钉子：旧实现把「池满」也算失败 ⇒ 3 连就永久关池
    const busyFive = Array.from({ length: 5 }).reduce<PersistBreaker>(
      (s) => notePersistAttempt(s, 'busy', t0),
      newPersistBreaker(),
    )
    expect(busyFive.stopped).toBe(false)
    expect(busyFive.streak).toBe(0)
  })

  it(`连败 ${PERSIST_DISABLE_STREAK} 次 ⇒ 停补位；冷却前不放行、冷却后放行（半开）`, () => {
    let b = newPersistBreaker()
    for (let i = 1; i < PERSIST_DISABLE_STREAK; i++) {
      b = notePersistAttempt(b, 'failed', t0 + i)
      expect(b.stopped).toBe(false)
      expect(b.streak).toBe(i)
    }
    b = notePersistAttempt(b, 'failed', t0 + PERSIST_DISABLE_STREAK)
    expect(b.stopped).toBe(true)
    expect(b.stoppedAtMs).toBe(t0 + PERSIST_DISABLE_STREAK)
    // 冷却窗内：不允许新建（暖 worker 仍能服务 —— 本函数只管 spawn）
    expect(persistSpawningAllowed(b, t0 + PERSIST_REARM_MS)).toBe(false)
    expect(persistSpawningAllowed(b, t0 + PERSIST_DISABLE_STREAK + PERSIST_REARM_MS - 1)).toBe(
      false,
    )
    expect(persistSpawningAllowed(b, t0 + PERSIST_DISABLE_STREAK + PERSIST_REARM_MS)).toBe(true)
  })

  it('半开探针再失败 ⇒ 只重计时（不叠惩罚、不额外升格）', () => {
    const stopped: PersistBreaker = { streak: 3, stopped: true, stoppedAtMs: t0 }
    const after = notePersistAttempt(stopped, 'failed', t0 + PERSIST_REARM_MS + 5)
    expect(after.stopped).toBe(true)
    expect(after.streak).toBe(3)
    expect(after.stoppedAtMs).toBe(t0 + PERSIST_REARM_MS + 5)
    expect(persistSpawningAllowed(after, t0 + PERSIST_REARM_MS + 6)).toBe(false)
  })

  it('一次成功 ⇒ 连败与停补位一起复位（半开探针成功即恢复）', () => {
    const b: PersistBreaker = { streak: 7, stopped: true, stoppedAtMs: t0 }
    expect(notePersistAttempt(b, 'ok', t0 + 1)).toEqual(newPersistBreaker())
  })

  it('纯函数：不改入参', () => {
    const b = Object.freeze({ streak: 2, stopped: false, stoppedAtMs: 0 })
    notePersistAttempt(b, 'failed', t0)
    notePersistAttempt(b, 'ok', t0)
    expect(b).toEqual({ streak: 2, stopped: false, stoppedAtMs: 0 })
  })
})

describe('同质入口（mode token 取代「腿」）', () => {
  it('入口只有一个：所有导出器类任务都送给 PERSIST_SERVE_ENTRY', () => {
    expect(PERSIST_SERVE_ENTRY).toBe('tools/sim/serve-any.ts')
  })

  it('mode 表的值域 = 四个导出器，且 persistModeFor 对表外条目给 null（BC 走一次性）', () => {
    expect(Object.keys(PERSIST_MODE_BY_ENTRY).sort()).toEqual([
      'tools/sim/export-eval-game.ts',
      'tools/sim/export-goal-rollout.ts',
      'tools/sim/export-intent-rollout.ts',
      'tools/sim/export-rl-rollout.ts',
    ])
    expect(new Set(Object.values(PERSIST_MODE_BY_ENTRY)).size).toBe(4)
    expect(persistModeFor('tools/sim/export-eval-game.ts')).toBe('eval')
    expect(persistModeFor('tools/sim/export-godai-bc.ts')).toBeNull()
  })
})

describe('sampler-agent 接线（源级钉子）', () => {
  const src = readFileSync(join(REPO_ROOT, 'tools', 'agent', 'sampler-agent.ts'), 'utf8')

  it('池子只 spawn 同质入口，且每行带 mode token', () => {
    // 池 worker 的 spawn 只在两处：启动预热 + 缺位时的按需新建。两处都必须经 launch(入口, [入口])
    // ——launch 对 bun 臂**直接透传 argv**，传 [] 会 spawn 出 `bun undefined --serve`
    //（2026-09-28 首次上机：4 个 worker 立刻 code=1 退出）。
    const spawns = src.split('persistSpawn(rolloutRunner().launch(').length - 1
    expect(spawns).toBe(2)
    expect(src.split('launch(PERSIST_SERVE_ENTRY, [PERSIST_SERVE_ENTRY])').length - 1).toBe(2)
    // 送进 stdin 的是 `[mode, ...args.slice(1)]`：与 serve-any.dispatch 同规
    expect(src.includes("JSON.stringify([mode, ...args.slice(1)]) + '\\n'")).toBe(true)
    // 准入 = persistModeFor（表外 ⇒ null ⇒ 一次性 spawn）
    expect(src.includes('persistModeFor(entryTs)')).toBe(true)
  })

  it('池是按「空闲」挑 worker，不再按 key 找「对腿的那个」', () => {
    expect(src.includes('persistPool.find((x) => !x.busy)')).toBe(true)
    // 旧形态：persistPool.find((x) => !x.busy && x.key === key)
    expect(src.includes('!x.busy && x.key === key')).toBe(false)
  })

  it('腿部对齐那一套策略已被删掉（不许悄悄回来）', () => {
    for (const gone of [
      'prewarmEntryPlan', // 按盘上 mtime 猜主腿
      'retireSurplusPlan', // 换腿腾位
      'KindRecency',
      'recentWeightKindsOnDisk',
      'queuePersistTopUp', // 权重 POST 后补位换腿
      'topUpChain',
      'PERSIST_ENTRY_BY_KIND',
      'PERSIST_SERVE_ENTRIES',
      'poolKeyFor',
    ]) {
      expect(src.includes(gone)).toBe(false)
    }
  })

  it('熔断：连败只在「worker 挂 + 一次性跑通」时记，且不再停用整池', () => {
    expect(src.includes("notePersistAttempt(persistBreaker, 'failed'")).toBe(true)
    // 旧实现：非 ok 一律 ++persistFailStreak 且 persistEnabled = false
    expect(src.includes('++persistFailStreak')).toBe(false)
    expect(src.includes('→ 熔断，改回一次性 spawn')).toBe(false)
  })

  it('池满 / 停补位冷却走 busy（不计失败），新建前查 persistSpawningAllowed', () => {
    expect(src.includes("if (persistPool.length >= workers) return { outcome: 'busy'")).toBe(true)
    expect(src.includes('persistSpawningAllowed(persistBreaker')).toBe(true)
  })

  it('__SERVE_ERR__ 只算「这一局错」，不当场杀 worker', () => {
    // 2026-09-28：以前 `__SERVE_ERR__` 就地 recycle（杀 + 摘），于是一条任务级错误就白付
    // 一次冷启（a95 实测 7–8s/条 vs 热池 1.4s/条）；serve-loop 捕了 main 的异常继续收行，
    // 这个 worker 的下一个任务可以马上发。
    expect(src.includes("settle('taskErr', line.slice('__SERVE_ERR__'.length)")).toBe(true)
    expect(src.includes("return { outcome: 'taskErr', worker: w }")).toBe(true)
  })

  it('回收 worker 只在「两路结论不一致」时（一次性跑通而 worker 报错）', () => {
    const fail = src.indexOf('if (rc !== 0) {')
    expect(fail).toBeGreaterThan(0)
    const afterFail = src.indexOf('if (attempt ===', fail)
    // 一次性也挂（rc≠0）那段里**不得**回收：那才是任务/数据问题，池无责、worker 留用。
    expect(src.slice(fail, afterFail).includes('recyclePersistWorker')).toBe(false)
    // 只有「一次性跑通」之后的分歧分支才回收
    expect(src.slice(afterFail).includes('recyclePersistWorker(errWorker)')).toBe(true)
  })

  it('池的连败只经 notePoolFailure（唯一入口，且只在兜底跑通之后可达）', () => {
    const callSites = src.split('\n').filter((l) => l.trim() === 'notePoolFailure()').length
    expect(callSites).toBe(2) // failed 分支 + taskErr 分歧分支
    expect(src.split("notePersistAttempt(persistBreaker, 'failed'").length - 1).toBe(1)
  })

  it('启动预热已接线且有开关', () => {
    expect(src.includes('prewarmPersistPool()')).toBe(true)
    expect(src.includes("a === '--no-prewarm'")).toBe(true)
  })

  it('预热在 bind 之前 await（先上线再预热 = 对着协调器探针失踪 ~50s）', () => {
    // 回归钉子（2026-09-28 第三轮上机）：a95/proot 上 fork 本身阻塞事件循环 ~7s/次
    // （spawn('/bin/true') 亦然），7 槽连发 = 50s 全程失联，而协调器 node_ping 超时 3s
    // ⇒ 先 bind 再预热 = 上线即被判 ping failed — excluded（账本 2029 次的同一机制）。
    const prewarmAt = src.indexOf('await prewarmPersistPool()')
    expect(prewarmAt).toBeGreaterThan(0)
    expect(prewarmAt).toBeLessThan(src.indexOf('listening on 0.0.0.0:${port}'))
    expect(prewarmAt).toBeLessThan(src.indexOf('serveWithRetry(port,'))
  })

  it('预热逐个 spawn 并等 __SERVE_READY__（连发会把慢机的事件循环饿死）', () => {
    expect(src.includes('__SERVE_READY__')).toBe(true)
    expect(src.includes('await waitPersistReady(w)')).toBe(true)
    // 坏 worker / 慢机不把上线无限期拖住
    expect(src.includes('PREWARM_BUDGET_MS')).toBe(true)
    expect(src.includes('PREWARM_READY_TIMEOUT_MS')).toBe(true)
  })

  it('入口坏（worker 生出来就死）时预热自行中止，不把启动预算烧在重试上', () => {
    // 2026-09-28 上机实测：推上去的 export-rl-rollout.ts 引了设备上不存在的模块 ⇒ 每个 worker
    // 活 ~7.5s 就 code=1，重试把 180s 预算耗光才 bind（那段时间既没上线也没预热）。
    expect(src.includes('PREWARM_DRY_LIMIT')).toBe(true)
    expect(src.includes('persist prewarm 中止')).toBe(true)
    expect(src.includes('} else dry++')).toBe(true)
  })
})
