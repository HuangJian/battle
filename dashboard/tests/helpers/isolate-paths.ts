/** isolate-paths.ts — 测试预载：把全部 `BCITY_*` **运行时路径** env 重定向进本进程专属的
 *  临时目录。**零测试读/写真实工作目录**。
 *
 * ## 为什么要有这一层（用户 2026-10-19 定调）
 *
 * 在此之前，`core/paths.ts` 里每一个「惰性取值」的 env 重定向都是**自愿参加**的：测试自己
 * 记得设才隔离，忘了就落到真实工作目录。实测代价：`dashboard/data/course-metrics/`（热备份库，
 * 20 门课）被 `pool-history` 的 `addHotMirrorCandidates()` 当成**课程候选**读进聚合 ⇒
 * 池历史/贡献数用例在真实库存在时随机红（`y23-acc-off` 这种真课名直接出现在断言里）。
 * 同类隐患还有 `repo/tmp/`（活训练流）、`nn-training/weights/`、`nn-training/rl-config.json`。
 *
 * 自愿参加 = 必然漏。所以提升为**进程级预载**：在任何测试模块 import 之前就把这些 env 全部
 * 指进一个空目录，测试即使完全忘了设也碰不到真实数据。
 *
 * ## 边界
 *
 *  · **只动 env，不碰被测代码、断言与产物**；测试自己后设的同名 env 仍然赢（预载跑在最前）。
 *  · **不含 `BCITY_CURRICULA_DIR`**：`nn-training/curricula/` 是**入库源数据**（与 `src/` 同级的
 *    只读事实源），重定向它会让「读课程配置」的用例整体失去对象；它与 `tmp/` 这类**运行时**
 *    工作目录不是一类。要动它得单独一刀（连同所有依赖它的用例一起搬进夹具）。
 *  · 每个 worker 进程一份临时根；`--parallel` 下不同 worker 互不共享，同 worker 内顺序复用的
 *    文件由各测试自带的 `mkdtempSync` 隔离（本层只保证「不落在真实目录里」）。
 *
 * 挂载点：`dashboard/scripts/run-tests.ts` 的 `--preload`（与 `no-proxy.ts` 并列）。
 * 守门：`tests/test-path-isolation.test.ts` —— 隔离失效会当场红，而不是等某条用例随机红。
 */
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { deadPort } from './dead-endpoint'

/** 本进程的隔离根（每 worker 一份）。导出仅供守卫测试断言，测试代码不拿它写真实路径。 */
export const ISOLATED_ROOT: string = mkdtempSync(join(tmpdir(), 'bcity-isolated-'))

/** env 名 → 隔离根内的相对路径。**只收「运行时工作目录/文件」，不收入库源数据。 */
const REDIRECTS: Record<string, string> = {
  BCITY_RL_CONFIG: 'rl-config.json',
  BCITY_TMP_LOGS_DIR: 'tmp-logs',
  BCITY_POOL_DIR: 'pool',
  BCITY_ERROR_LEDGER: 'component-errors.jsonl',
  BCITY_TRAINER_HEARTBEAT: 'trainer-heartbeat.json',
  BCITY_CONSOLE_LOG: 'console.log',
  BCITY_CONSOLE_STATE: 'console-state.json',
  BCITY_LOOP_CONTROL: 'loop-control.json',
  BCITY_LOOP_APPLIED: 'loop-control.applied.json',
  BCITY_GATE_HALT: 'gate-halt.json',
  BCITY_GATE_HALT_APPLIED: 'gate-halt.applied.json',
  BCITY_REGISTRY_FILE: 'registry.json',
  BCITY_LOCKS_DIR: 'locks',
  BCITY_ARCHIVE_DIR: 'archive',
  BCITY_WEIGHTS_ARCHIVE_DIR: 'weights',
  BCITY_COURSE_METRICS: 'course-metrics',
  BCITY_CODE_SNAPSHOT_DIR: 'code-snapshots',
  // EvalBoard 工作数据根（`ladder-data.ts` / `verdict-cli.ts` 读同一个 env）。
  EVALBOARD_DATA: 'evalboard',
}

for (const [name, rel] of Object.entries(REDIRECTS)) {
  process.env[name] = join(ISOLATED_ROOT, rel)
}

/** 隔离根自带的**最小种子 rl-config**。
 *
 *  为什么预载就播种：旧行为是「用例直接读到线上 `nn-training/rl-config.json`」——那正是要禁的
 *  读真实工作目录；但若只把 env 指到一个**空**目录，一批「读配置」的用例会当场 ENOENT
 *   （`loadConfig` 不兜底），与本次要修的污染无关，只会把红从 7 条变成 20 条。
 *  折中：沙箱里放一份**空夹具**（无节点、无课程），任何忘了设 env 的用例拿到的是
 *  「一份什么都不是的配置」，而不是线上真配置。要特定配置的用例照旧自己写（`console-fixture`
 *  的四节点种子就是那么用的，它后设 env ⇒ 赢过本预载）。
 */
// `hub_port` / `agent_port` 指向**死端点**（accept 后立刻 reset，见 `./dead-endpoint.ts`）：
// 默认种子下每个冷探测（`buildStateView` / `componentViews` / push 探活）都会真去连它们，
// 写死没人监听的端口在丢回环 SYN 的内核上要白等满 1.5s 超时（实测 `bun run test` 里
// 十来个没用 console-fixture 的文件各白等 1.5s）。测试要特定配置时照旧自己写（后设 env 赢）。
const DEAD = deadPort()
const SEED_RL_CONFIG = {
  version: 1,
  nodes: [],
  rl: {
    hub_port: DEAD,
    agent_port: DEAD,
    local_slots: 0,
    stream: 0,
    double_buffer: 0,
    workers: 8,
    torch_threads: 8,
    remote_token: 'fixture-token',
  },
}
writeFileSync(process.env.BCITY_RL_CONFIG!, JSON.stringify(SEED_RL_CONFIG, null, 2), 'utf-8')
