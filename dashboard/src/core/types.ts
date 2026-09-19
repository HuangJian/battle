/** types.ts — training 工具链共享类型（配置 / 登记 / 监督进程）。 */

/** rl-config.json 的 nodes 条目（rollout 节点）。 */
export interface NodeConf {
  id: string
  url: string
  authKey: string
  concurrency: number
  enabled: boolean
  /** GPU push 节点（DECISIONS §340 补充 4：URL 指向其 worker_server 隧道）。
   *
   *  ★ **课程任务与 worker 节点正交**（2026-09-19 用户口径：「所有 worker 都可能接到在训的
   *  课程任务，不管它是哪个课程的」）：节点只登记一次，谁接到活由部署（`rl.hub_push` + hub
   *  队列）决定。`local_push` 标记已删除——本机伪节点与那套「一键本机 push」在 R3-7 全部退场
   *  （启动时由 `pruneLegacyCourseKnobs` 连同按课程的指针一并清理）。 */
  gpu_push?: boolean
}

/** 课程配置块（plan multi-course-parallel-training §1.3）。
 *  slot = 槽位（只决定本机 push 端口）；workers/local_slots = 本机并发配额。
 *
 *  ★ **课程不携带任何传输/节点指针**（2026-09-19）：删掉了 `push_node_url`、
 *  `remote_transport`、`remote_hub_url`、`hub_push`。它们是「把**这门课**钉到某条路 /
 *  某台机器」的耦合——课程定义任务，worker 节点提供算力，二者正交。旧值由
 *  `stack/course-knobs.ts::pruneLegacyCourseKnobs` 在启动训练时清理。 */
export interface CourseConf {
  slot?: number
  workers?: number
  local_slots?: number
  /** 本课隧道协议覆盖（M1；缺省 = 用 rl.cf_protocol）。 */
  cf_protocol?: CfProtocol
  /** 本课隧道边缘 IP 版本覆盖（M1；缺省 = 用 rl.cf_edge_ip）。 */
  cf_edge_ip?: CfEdgeIp
  /** 本课协议瘦身覆盖（M2；缺省 = 用 rl.slim）。数字域：python 侧 `_d("slim",1)`
   *  只认 1/0（`--remote-slim` 是 `type=int, choices=(0,1)`）。 */
  slim?: 0 | 1
  /** 本课 rollout 执行位置覆盖（M3；缺省 = 用 rl.rollout_src，再缺省 local）。字符串域，
   *  与 python `--rollout-src` 的 choices 同字面量（`auto` = 按配置解析）。 */
  rollout_src?: RolloutSrcMode
  /** 半离线段长覆盖（本课跑几轮一次上交；`-1` = 直到课程末尾，`0` = 关）。
   *  与 `rollout_src:'run'` 是**一对**：离线训练模式（2026-09-19）同时写这两个键，
   *  只给 `run` 不给段长在训练侧是配置错误（`_run_segment_iters` 回 0 ⇒ 响亮拒跑）。 */
  run_iters?: number
  // ── 共享 trainer 的**机器侧旋钮**（2026-09-19 / R3-5）──────────────────────────
  //  一个进程服务所有课程 ⇒ 「这门课怎么跑」不能是那个进程的命令行参数（只有一份）。
  //  住这里而**不能**住 `curricula/*.jsonc`：课程文件字节 = course_fp（语料血缘 / 熔断口径
  //  D14）——往里加一个旋钮，熔断会把同一份语料读成新语料。
  //  读面：python `rl/loop_serve.py::apply_course_machine_overrides`（开课时施加）。
  //  传输/节点指针**不在**这里（课程与 worker 节点正交）。
  /** T7：远端连败降级本机的阈值（0 = 关）。 */
  remote_degrade_after?: number
  /** 门禁失败语义（halt = 打进停机态）。 */
  gate_halt_mode?: string
}

/** cloudflared 隧道协议（M1，plan/remote-wire-remediation §3）：
 *  `http2` = TCP/443（**缺省，推荐**）——国内 ISP 对 QUIC(UDP/443) 做 QoS 降质是
 *  实测病灶（重启复位、数轮内再劣化的状态化限速签名）；
 *  `quic` = UDP/443（cloudflared 自身缺省）；
 *  `auto` = 不传 `--protocol`，逐字节回到旧行为（A/B 的对照组）。 */
export type CfProtocol = 'http2' | 'quic' | 'auto'
/** cloudflared 边缘 IP 版本（M1）：`4`（缺省）/ `6` / `auto`（不传旗标）。 */
export type CfEdgeIp = '4' | '6' | 'auto'
/** 协议瘦身开关（M2，`plan/remote-wire-remediation.plan.md` §1.4 的回退开关）。
 *
 *  **双域**（有意为之，别"统一"掉）：UI / console-state / HTTP body 用 `'on'|'off'`
 *  （跟 CfProtocol 一样的字符串域，UI 直接用）；rl-config 里必须落成 `1|0` —— python
 *  侧 `--remote-slim` 是 `type=int, choices=(0,1)`，写字符串会让训练启动直接报错。
 *  换算只允许走 `slimToCfg()`（dashboard 侧）一个入口。 */
export type SlimMode = 'on' | 'off'

/** rollout 执行位置（M3，`plan/remote-wire-remediation.plan.md` §5）。
 *
 *  `local` = 本机采样（历史行为，整轮口径逐字节不变）；
 *  `node` = 本轮**整轮上云**（节点 bun 跑 exporter 产 shard + 跑 PPO，kind=iter job）；
 *  `auto` = 不表态，交给训练侧按 `courses.<课>.rollout_src` > `rl.rollout_src` 解析
 *  （缺省仍是 local）。与 python `choices=("auto","local","node")` 同域——
 *  与 `SlimMode` 不同，这里**不需要**域换算（两侧都是字符串）。 */
export type RolloutSrcMode = 'local' | 'node' | 'run' | 'auto'

/** 启动训练时的**训练模式**（2026-09-19 用户口径：启动时需指定，缺省在线）。
 *
 *  · `online`  = 现状：本机跑 rollout，每个 it 向云端 worker 传语料；hub 实时派发。
 *  · `offline` = 本机不跑训练：整段上云（`courses.<课>.{rollout_src:'run', run_iters:-1}`）
 *    + hub 该课置 offline（只有带标 worker 能领），或在控制台导出任务包人工搬上云。
 *
 *  它不是「一个旋钮的显示名」：域换算（模式 → 课程级键）住在 `stack/specs.ts::trainModeKnobs`，
 *  是**唯一**推导点（在线要显式清掉 run 的两把键，否则切回在线仍是整段上云）。 */
export type TrainMode = 'online' | 'offline'

/** 竞速广播模式（hub-server `--race`，2026-09-17）。
 *
 *  `auto` = 本 hub 的 worker 全都只服务这一个 hub（机群只为单一课程干活）时，最新 job
 *           不下租约广播给所有 worker——先回传者胜；多课程机群自动退回独占（P3b）。
 *  `on` / `off` = 运维强制开关（也可热切：`POST /admin/race?mode=...`）。
 */
export type RaceMode = 'auto' | 'on' | 'off'

/** 归一化 race_mode：认不出的值回落 `auto`（写错配置不得让 hub-server 启动即退）。 */
export function normalizeRaceMode(raw: unknown): RaceMode {
  return raw === 'on' || raw === 'off' || raw === 'auto' ? raw : 'auto'
}

/** rl-config.json（本工具链只消费 nodes + rl + courses 块，其余键原样保留）。 */
export interface RlConfig {
  version: number
  nodes: NodeConf[]
  rl: {
    hub_port: number
    agent_port: number
    remote_token: string
    remote_hub_url?: string
    /** 每课 hub 隧道 URL（P3：hub 与隧道每课独立；单键 remote_hub_url 保留兼容读）。 */
    remote_hubs?: Record<string, string>
    torch_threads?: number
    local_slots?: number
    /** 隧道协议（M1；缺省 http2）与边缘 IP 版本（缺省 4）——`auto` = 旧行为。 */
    cf_protocol?: CfProtocol
    cf_edge_ip?: CfEdgeIp
    /** 协议瘦身（M2；缺省 1 = 开）。数字域，见 `SlimMode` 注释。 */
    slim?: 0 | 1
    /** rollout 执行位置（M3；缺省 = local，即本机采样）。字符串域，见 `RolloutSrcMode`。 */
    rollout_src?: RolloutSrcMode
    /** 竞速广播（2026-09-17；缺省 = auto）。字符串域，见 `RaceMode`。 */
    race_mode?: RaceMode
    /**
     * hub 中介 push 派发（2026-09-18；缺省 = 关）。
     *
     * 打开时 hub-server 多带 `--push --push-config <rl-config>`：它按队列顺序把 job 推给
     * 登记在册的 `gpu_push` 节点（周期 `/ping` 探活、超时回落队首换 worker）。训练侧仍住在
     * `rl.hub_push` 的课程级覆盖下（`courses.<课>.hub_push`）——两侧同一个键名是故意的：
     * 「push 要不要经 hub」是部署事实，不该在面板与训练循环各写一遍。
     */
    hub_push?: boolean
    [key: string]: unknown
  }
  /** per-course 槽位/配额（唯一事实来源；console-state 不存这些）。 */
  courses?: Record<string, CourseConf>
  [key: string]: unknown
}

/** 受管组件（registry 分文件账本的键）。 */
export type Component = 'selfNode' | 'hubServer' | 'cloudflared' | 'localWorker' | 'trainingLoop'

/** 单组件登记条目（PID 账本 + 可选元数据）。 */
export interface RegistryEntry {
  pid: number
  /** 进程入口（监督重启/变更检测用）。 */
  entry?: string
  course?: string
  /** 槽位（§1.4 重建契约：重启时必须知道自己占哪槽）。
   *  **一切受管组件都恒 0/缺省**（hubServer / cloudflared / trainingLoop / localWorker 的实例
   *  不属任何单门课——判据 `registry.componentScope`）；槽位算术如今只服务**课程配置**
   *  （`courses.<课>.slot` → push 端口），不再用于受管进程。 */
  slot?: number
  url?: string
  log?: string
  metrics?: number
  /** hub 的 jobRoot（§1.4 重建契约：重启不得用全局状态猜课程目录）。 */
  jobRoot?: string
  /** trainingLoop 的 push 节点 URL（REMOTE_PUSH_NODE 重启注入）。 */
  pushNodeUrl?: string
  /** T7：启动时是否 opt-in 远端连败降级本机 PPO（默认 false；监督重启复现）。 */
  remoteDegrade?: boolean
  /** 启动模式：控制台 trainer 编排（pull/push/local）或 'remote'（冒烟预演）。 */
  mode?: 'pull' | 'push' | 'local' | 'remote'
  /** 本进程启动时实际生效的隧道选项（M1；复用判定用它检测「改了选项没生效」）。 */
  cfProtocol?: CfProtocol
  cfEdgeIp?: CfEdgeIp
  /** 非正常退出记录（§380 exit-watchdog 写入；UI/API 展示退出原因）。 */
  error?: string
  exitAt?: string
}

/** registry.json：全部组件条目（缺省组件 = 未启动）。
 *
 *  多课程形状（plan §1.4，P1b）：五个组件键各有一份 `Record<course, Entry>`（**表**按课程，
 *  但 hubServer / cloudflared / trainingLoop / localWorker 四条是**共享**实例，槽恒 `''`；
 *  谁按课程看 `registry.componentScope`，不要看表名）。
 *  旧扁平单键（`hubServer`/…）**已在 P5 移除**（R2）：类型里不再声明，唯一读点是
 *  `registry.ts::migrateFlatCourseEntries` 的一次性搬迁（把旧条目搬进 per-course 表再删键），
 *  写入路径不再产生扁平键（`saveComponent` 只服务 selfNode）。
 *  枚举一律走 `registry.ts::registryComponents()`（三元组），不要直接按 key 枚举账本对象。 */
export interface Registry {
  /** 单例（agent 全局一份）。 */
  selfNode?: RegistryEntry
  /** per-course 键（P1b 起唯一写入路径）。 */
  hubServers?: Record<string, RegistryEntry>
  cloudflareds?: Record<string, RegistryEntry>
  localWorkers?: Record<string, RegistryEntry>
  trainingLoops?: Record<string, RegistryEntry>
}

/** 旧扁平账本键（P1–P4 的历史形状）——**仅**供 `registry.ts` 的一次性搬迁读取（R2）。
 *  任何其它代码不得读它：编译期把它们挡在 `Registry` 之外，正是为了不留读兼容后门。 */
export type LegacyFlatRegistry = Partial<
  Record<'hubServer' | 'cloudflared' | 'trainingLoop', RegistryEntry>
>

/** 需要**整树停止**的组件（stop / 全部停止 / 监督重启三处共用，实现见 net.ts::killPidTree）。
 *
 *  只有「自身还带一个子进程监督器」的组件在内：localWorker 跑的就是云端那条
 *  `remote.worker` 入口——父 `supervise_worker` + 子 `worker_loop`（子进程 60s 心跳续租、
 *  长期轮询 hub 抢 job）。只杀父进程 = 留一个继续抢 job 的孤儿，「随时启停」形同虚设。
 *  其余组件都是单进程，不进此集合（默认 False 路径行为不变）。 */
export const COMPONENT_KILL_TREE: ReadonlySet<string> = new Set(['localWorker', 'trainingLoop'])

/** 受管进程的描述（spawn + 监督 + 变更检测的统一载体）。 */
export interface ProcSpec {
  /** registry 键（selfNode/hubServer/...）。 */
  key: Component
  /** 展示名。 */
  name: string
  /** 命令行（argv[0] = 可执行文件）。 */
  cmd: string[]
  cwd?: string
  /** 追加到子进程 env（在 process.env 之上）。 */
  env?: Record<string, string>
  /** stdout/stderr 直写的日志文件。 */
  log: string
  /** 健康检查（就绪判定）；监督循环周期性调用。 */
  healthy: () => Promise<boolean>
  /** 可选：就绪前先确认**这个新 pid 真的持有它要独占的资源**（目前都是端口）。
   *
   *  为什么健康检查不够（2026-09-17 事故）：端口上的**旧僵尸**也能答健康检查——新进程
   *  bind 失败（EADDRINUSE / python 的双监听守卫）时它早已死，但僵尸的 200 会被记成
   *  「新进程已就绪」：控制台报成功、账本记新 pid，而实际服务的是旧进程；监督器同理会把
   *  僵尸的 200 当成「重启成功」。二者都只对「独占端口的组件」有意义，故用可选字段。 */
  ownsResource?: (pid: number) => Promise<boolean>
  /** 变更检测哨兵文件（mtime 变了 = 该进程运行的代码已更新）。 */
  sentinels: string[]
  /** 归属课程（多课监督以 (key, course) 为单位；空串 = 无课程）。 */
  course?: string
  /** 停止/重启时是否连**整棵进程树**一起杀（默认 false = 只杀登记的那个 PID）。
   *  true 只给「自身还带着一个子进程监督器」的组件（localWorker 跑云端同款
   *  `remote.worker`：父进程 supervise_worker + 子进程 worker_loop）——只杀父进程会
   *  留下继续轮询 hub 抢 job 的孤儿，停止/重启就形同虚设。 */
  killTree?: boolean
}
