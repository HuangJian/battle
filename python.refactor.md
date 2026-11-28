# python.refactor.md — `nn-training` Python 代码审计与重构方案

> 审计对象：`nn-training/`（378 个生产 `.py` / 147,991 行；499 个测试 `.py` / 155,612 行）
> 审计日期：2026-11-12 · 基线：`goal-nn` @ `7426d156`
> 方法：全量 AST 度量 + 逐模块精读 + 少量只读静态脚本（`tmp/py-audit.py`、`tmp/py-clones.py`）
> 定位：本文与 `plan/nn-training-refactor.md`（S 系列 23 刀）**互补**。那份记录「已经拆了什么」，
> 本文回答「**拆完之后还剩下什么、为什么还在、下一刀该往哪切**」。

## 处理状态（2026-11-14）

> 本节记录**已经落地**的部分；§3 / §4 正文保留为「修复前基线」，不要当成当前状态。

| 条目 | 状态 | 证据 |
|---|---|---|
| P1-3 lint 豁免在保护 bug 类 | **已修** | `B023` / `SIM115` 从全局 `ignore` 移除；40 处违规清零（25 处循环变量显式绑定，8 处句柄改 `with`，7 处确实跨调用持有 → 行级精确抑制 + 理由） |
| P0-6 时间戳两份实现 + 假声明 | **部分修** | `common/log.py::format_line` 改走 `common.logutil.stamp()`，删掉 `log(course=…)` 里的第二份格式化；`logutil.py` 指向不存在的 `biz/log.py` 的假声明已改正。`print` 仍剩 134 处。**`hub/` 包已收口**（54 → 1，唯一保留的是 `tunnel_ab_probe` 的 `print(json.dumps(results))` = 一次性 CLI 的机器读交付物）；**`remote/` 的日志格式也已单源**（13 文件 43 处：`log=lambda msg: print(f"[…][worker] {msg}")` 的默认回调换成模块级 `_log = tag_logger(…)`，`def _log(msg)` 包装的 body 换成 `log_line(tag, msg)`），手拼 `time.strftime` 的日志时间戳随之消失。两处**刻意保留**的手拼格式：`remote/worker_proc.py`（L0 纯 stdlib 秩，不许 import `common.*`）与`remote/deliver_zip.py` / `run_loop.py` 的协议输出面；`worker/iter_rollout.py` 的 `[iter]` 回调（2026-11-21 已收口：缺省 `log` 改走 `common.logutil.tag_logger("iter")`，与全仓同一条 `[HH:MM:SS] [tag]` 口径，不再自己拼、不再没有时间戳；该文件 G3 1 → 0；同族最后两处 `worker/bc_dispatch` / `worker/bc_eval` 的缺省 `log` 也已收进 `tag_logger("bc")` / `tag_logger("bc-eval")`（连前缀都没有的那种）⇒ G3 123 / 38 文件）。2026-11-14 另修了日志层自己的盲区：`LogRouter` 的镜像写失败 / 句柄关闭失败原来都是裸 `pass`，「该课日志在丢行」与「这课本来没日志」长得一模一样 —— 现在按 `mirror:<课>` / `close:<课>` 各喊一次 WARN |
| P0-5 `common/` 模块级可变状态 | **已修（`common/**` 清零）** | `log` / `fs` / `weights_ledger` 已对象化：`LogRouter`（ContextVar 前缀 + 显式 sink 表）/ `JsonlLedger` / `WeightsLedger`，均可 `router=` / `ledger=` 注入，默认实例只是薄包装；新增隔离性用例。`net_http` 的 `_NO_PROXY_OPENER` / `_RACING_OPENERS` 两个懒单例改为 `functools.cache`，G2 名册对应项已删。`distribution` 的三份状态（在飞登记 / 收工标记 / 重启去重 memo）搬进 `RequestRegistry` / `RestartMemo` 显式对象；`platform_utils` 的 `_UNREAPED` 搬进 `UnreapedProcs`（`keep` / `sweep` / `__len__`，带 `store=` 可注入），**G2 名册清零**（`L0_MUTABLE_ALLOWED = {}`，名册本体保留给「过期豁免」用例）。2026-11-20 收尾：派生线程的上下文传播（§234）、`UPGRADE_BRANCH` → `BranchLatch`（§235）、L2 两个单例 → `common.cell.Cell`（§236）、`hub/http_face` 四个类属性 → `HubState`（2026-11-19）；`common/**` 之外的同类存量另有清单（见 §P0-5 末小节） |
| P0-3 参数爆炸（56 参） | **部分修** | `publish_job` 的 55 个字段收进 `PublishSpec`（frozen + kw_only，住 `remote/publish_spec.py`，L0），签名只剩 `spec` / `log`（`**fields` 过渡适配器已删，26 个调用点全部改传对象）；manifest 打包只读对象。`run_training_core` / `_run_training_core_paddle` 的 27 个 kwarg 收进 `TrainSpec`（frozen + kw_only，签名只剩 `(base_url, token, spec)`）；`build_argparser` 的 92 个 `add_argument` 收进模块级 `_flag_rows(mode, rl_args)` 的 `(flags, kwargs)` 数据表（650 → 7 行；表 164 行）。`ppo_update` 的 17 参入口保留为 trainer/stream 契约壳，内部运行时状态收进 `PPOContext`。**2026-11-20（§229）**：`worker/eval_arbiter.EvalRound.__init__` 的 40 个关键字参数 → `RoundSpec`（frozen + kw_only，装配点 `trainer/eval_dispatch._round_spec`；回调按形状声明，该文件 `Any` 21 → 16），`EvalRound.settle_task` 的 9 个结果位（含从没人读的 `wall_sec`）→ `SettleOutcome` ⇒ 该文件宽签名归零、全仓 132 → 130；守 `tests/worker/test_eval_round_spec.py`。**2026-11-20（§230）**：`trainer/batch_runner._settle_unit` 的 15 个位置参数 → 一个 `UnitLedger`（frozen + kw_only，装配点 `_UnitLanes.ledger(threads=…)`）—— 前置条件是把通道机器整块拆成 `trainer/batch_lanes.py`（LOC 999 → 336 + 694），全仓宽签名 130 → **129**；守 `tests/trainer/test_batch_runner_phases.py` 的 `LEDGER_FIELDS` 闭集。**2026-11-20（§231）**：`remote/plan_handoff.open_run_context`(35) / `remote/run_loop.run_standalone`(29) / `remote/plan_run.run_plan_job`(25) 收成一个 `RunSetup`（frozen + kw_only 旋钮袋，37 字段；`RunContext.__init__` 29 → 4 参），规范化上移到 `RunSetup.__post_init__`、身份位只剩 `_require_run_identity` 一个校验点，顺手删死参 `job_id` ⇒ 全仓宽签名 129 → **125**；守 `tests/remote/test_run_setup_spec.py`。**2026-11-20（§232）**：全仓最宽的 `trainer/queue_local.rescan_nodes`（27 参，其中 19 个位置参数 —— docstring 里两次事故的同一成因面）→ 一个 `RescanSpec`；唯一调用点改成 `kwargs=dict(spec=…)` ⇒ 全仓宽签名 125 → **124**；守 `tests/trainer/test_rescan_spec.py`。**2026-11-20（§233）**：`remote/worker_server._execute_job`（14 参；`Runtime.thread(...)` 只收位置参数，调用点逐位传 11 个实参 —— 与上一刀同一条失效面）→ 一个 `JobExecSpec`，位置实参只剩 1 个 ⇒ 全仓宽签名 124 → **123**；守 `tests/remote/test_job_exec_spec.py`。**2026-11-21（§256）**：全仓最宽的 `worker/eval_local.run_local_eval_game`（**25 参**，前 9 个是位置语义：抄错一格就是「评估了别的关/别的权重」）→ 一个 `EvalGameSpec`（frozen + kw_only，25 个字段）⇒ 该文件宽签名归零、全仓 123 → **122**（顺带清掉 `remote/hub_client` 3→2、`remote/train_core` 5→3 两处从未收紧过的余量）；5 个调用点（`remote/offline_eval` / `worker/eval_exec` / `trainer/batch_lanes` / `worker/eval_replays_once` + e2e 替身）全部改传对象。**2026-11-21（§261）**：认领族 `claim` / `claim_outcome` / `_claim_locked` 与两个转发层（`_JobStore` / 队列门面）收成一个 frozen `LeaseSpec`（`job_id` + 7 个带缺省字段，顺序沿用旧 `claim`）⇒ `hub/queue_claims` · `hub/queue_peer` · `hub/store` · `hub/store_leases` 四文件的宽签名归零、全仓 122 → **117**；`claim_job`（6 参）签名不动、内部构造 spec；123 处测试调用一次性包进 spec；归属闸守卫扩成「role= / LeaseSpec( / spec」三种载法 |
| P0-4 动作空间嗅探 | **部分修（动作空间）** | 新增 stdlib-only `common/contracts.py`：`ActionSpace` + `HeadLayout`；PPO torch/Paddle 引擎只在数据边界 `from_batch` 一次，ref 用 `from_forward_arity` 校验，手写掩码切片改为契约切片；`tests/common/test_action_space_contract.py` 覆盖，`paddle_parity` 8 case 全 PASS。**2026-11-16 删掉最后两处 `a_hold` 嗅探**（`worker/ppo/np_core.py` 的 `load_shard` 掩码宽校验与 `load_episode_from_shard` 的字段集），并加门禁 `test_only_one_place_decides_v6`：生产面除 `common/contracts.py` 外不许再出现 `"a_hold" in …` / `.get("a_hold")`（字节初筛，0.16s）。**2026-11-18 数据面契约迈出第一步**：`common/contracts.py` 新增 `Shard` / `ShardV6` / `Episode` / `EpisodeV6`（零运行时成本 TypedDict，numpy 只在 `TYPE_CHECKING`），`np_core.load_shard` 返回 `Shard | ShardV6`、12/14 个键在 `_as_shard` 里逐个写出 ⇒ 拼错键名是 mypy 错误而不是静默走 legacy（实测 `a_holdd` 两条错）；字段表↔契约的同源判据做成 import 哨兵（读私有表会撞 G5）；三 backend 共用的骨架面收敛成一个有理由的 `ShardRow = Mapping[str, Any]`（G14 −1）。`tests/common/test_shard_contract.py` 覆盖键集 / episode 键序 / 零成本 / L0 不拉 numpy（见 `docs/nn/engineering.md` §126）。**2026-11-18 第三块：episode 契约跨 `RolloutBackend`/`trainer/stream`** —— 三个后端返回 `Episode|EpisodeV6` / `GoalEpisode` / `IntentEpisode`，协议面是 `EpisodeLike` union（stream 只碰 `adv`）；四串字段元组搬进 `common/contracts.py` 由声明序派生。顺手挤出 P0-1 同型破洞：stream 恒传 `(shard, gamma, lam)` 而 intent/goal 装载器只收 `dirpath`（`load_episodes` 别名只收 `(data_root, normalize_adv)`）⇒ 一接上就逐 shard TypeError 被吞成 `skip bad shard`；两个后端现已收下 gamma/lam 并传给 GAE（缺省 = 模块常量，数值不变），守卫 `test_episode_loaders_accept_stream_call_shape` 修前 4 红（`docs/nn/engineering.md` §127）。~~**manifest/result 仍是裸 dict —— B5 剩余那一半**~~ **2026-11-18 复核：该句已过期** —— `Manifest` / `CloudResult` 是字段级校验的 pydantic 模型（`common/manifest.py` / `common/protocol.py`，§128），`normalize_manifest` / `validate_result` 只是薄包装；B5 的剩余项只剩 `cfg`/`course` schema（可选）。
| P0-2 `trainer/eval_dispatch.py::EvalDispatcher.run`（589 行） | **已拆** | `run` ~77 行只做编排；派生状态收进 `_EvalRun`，阶段拆成 `_eval_prepare`（127）/ `_eval_register`（120）/ `_eval_admit`（193）/ `_eval_start`（35）/ `_eval_close`（163）。节点门、权重 POST、收工对账、summary 与清理顺序逐字搬移 |
| P0-2 `worker/iter_rollout.py::run_iter_rollout`（604 行 / 9 参） | **已拆** | 入口只剩编排（~50 行）；批级状态收进 `_IterRun`，阶段拆成 `_setup_run`（94）/ `_setup_scratch`（140）/ `_setup_pool`（48）/ `_drive_games`（172）/ `_run_games`（15）/ `_finish_run`（107），`_samples_of` 与 `_clean_stuck` 独立成模块函数。流式调度、带超时 wait、整轮重投、scratch/池/对账顺序逐字搬移 |
| P0-2 `remote/train_core.py::run_training_core`（原 torch 核）与 `_run_training_core_paddle`（387 行） | **已拆** | 两条核入口各 24/30 行；torch 阶段 `_torch_model`（126）/ `_torch_opt`（122）/ `_torch_ref`（58）/ `_torch_ppo`（96）/ `_torch_artifacts`（112），Paddle 阶段 `_paddle_model`（34）/ `_paddle_opt`（79）/ `_paddle_ref`（37）/ `_paddle_ppo`（70）/ `_paddle_artifacts`（74）。框架无关状态与结果面下沉 `remote/train_common.py`（L2）：`_CoreRun` / `_load_ppo_data` / `_core_result` / `_agg_block`；`train_core.py` 源 LOC 1098 → 929。Paddle 复用 `_load_demo_bank`，`paddle_env.apply` 时序与 `steps_out` 单源记账逐字保留 |
| P0-2 `worker/ppo/engine.py::ppo_update`（598 行 / 17 参） | **已拆** | `ppo_update` 只留 17 参薄适配器（~85 行）；共享状态收进 `PPOContext`，阶段拆成 `_prepare` / `_train_step`（177）/ `_record_diag` / `_maybe_heartbeat` / `_finish_epoch` / `_finish` / `_aggregate`。XLA mark/诊断、ref cache、demo、profiler 与 RNG 顺序逐字搬移 |
| P0-2 `dispatch._run`（1184 行 / CC 190） | **已拆（两层）** | `_run` 只做编排（~30 行）；相位拆成 `_ping_gate` / `_prepare_weights` / `_run_queue` / `_settle_round`，共享状态收进 `DispatchState`。第二层把 `_run_queue`（871 行）拆成 `_queue_plan`（79 行）/ `_queue_setup`（144 行）+ `trainer/queue_worker.py`（`QueueState`、`_claim` 134 / `_execute` 133 / `_settle_success` 119 / `_settle_failure` 144 / `run_queue` 168）；499 行 `worker` 闭包消失，`dispatch.py` 长函数 2 → 0、LOC 958 → 473 |
| P1-5 配置散读（130 处 + 裸 `dict` CFG） | **部分修（门禁 + 第一批）** | 量出生产面 **94 处 / 44 个变量**，其中多读者只有 5 个、真分叉 2 个 ⇒ 不做「一个 `Settings` 对象穿透全部签名」，只建 `common/settings.py` 收**多读者**旋钮（`EVALBOARD_DATA` · `BATTLE_HUB_TOKEN`），形态 = 常量 + 调用时读 env 的纯函数（`env=` 可注入）。新**门禁 G13** `tests/test_env_reads_budget.py`：按文件冻结直读次数（**60 / 30**，只许调小）+ 旋钮单家判据；豁免 4 个 standalone 引导文件。**第二批（2026-11-18）**：重新按 AST 量出「≥2 个模块各解一次」的旋钮只剩 `REMOTE_PUSH_NODE`（`trainer/loop_transport` 两处 + `trainer/bc_loop` 一处，其中一处还包了 `str(... or "")`）⇒ 收进 `common/settings.py::push_node_from_env`（三个调用点改 import，G13 存量 63/32 → **60/30**，两个文件条目归零删除）；`PATH` 是两个引导文件读的系统变量，不是旋钮，不动。裸 `dict` CFG（notebook 侧 26 处）未动 —— 它的读者在 notebook cell 里，门禁看不到，需要时单独开一刀 |
| P1-2 版本矩阵四者不一致 | **已修** | CI 矩阵加 3.10（下界）+ 3.12（钉住版本），`uv sync --python` 真用矩阵值；`tests/test_python_version_matrix.py` 钉死自洽 |
| `-x`（CI 只报第一个失败） | **已修** | `addopts` 去掉 `-x` |
| P0-2 函数形状无人管 | **已加门禁** | `tests/test_python_shape_budget.py`：>200 行 / >8 参数按文件冻结存量，新增即红 |
| G3 生产包 `print` 增长 | **已加门禁 + 首批收敛** | `tests/test_no_print_in_prod.py`：AST 扫 `common/biz/hub/remote/train/trainer/worker`，按文件冻结存量（`tests/golden/print_baseline.json`，**123 处 / 38 文件**（2026-11-18 第三批：`biz/course.py` 3 → 0、`biz/ladder_ledger.py` 7 → 2，全走 `log_line`；`graduated` 那条顺手补上时间戳））；`tools/` 与 `worker/scripts/` 豁免。2026-11-14 两批：`hub/` 54 → **1 处**（`tunnel_ab_probe` 的机器读结果面）；`remote/` 88 → 47，全部是 `log=` 默认回调与 `def _log` 包装里手拼的 `[{time.strftime(…)}] [tag]`（13 文件 43 处）改走 `common.logutil`。基线同步删条目，并补了「基线只许调小」一条 |
| P0-4 dict 即契约（manifest / result） | **已修** | `Manifest`（`common/manifest.py`）与 `CloudResult`（`common/protocol.py`）是字段级校验的**唯一实现**，`normalize_manifest` / `validate_result` 降为薄包装：严格标量（`bool` 不冒充 int、`"5"` 不冒充 number，`int` 仍是 `float` 的合法替身且**类型不变**）· `extra="allow"` 保留未知键 · 协议错误文案关键词逐字保留。代价：L0 契约①第一次有例外，**只放行 `pydantic`**（已声明依赖、纯 Python、随 code.zip 装），闭集三处（`common/__init__` / `test_common_layer::THIRD_PARTY_ALLOWED` / `test_manifest_split::ALLOWED_IMPORTS`）。见 §128 |
| P1-9 `assert` 做运行时校验 | **已修（模块级全生产面归零 + 函数级冻结）** | 2026-11-14：`common/schema.py` 的 5 处模块级 assert 改成显式 `RuntimeError`（布局校验收进 `_check_layout`），新增 G7 扫 `common/**`；2026-11-18：G7 **扩到全部生产包**（`tests/test_module_asserts.py`，原 `test_common_module_asserts.py`），扩面时抓到仅剩的两处 —— `trainer/loop_round_steps.py` 的步骤表同步与 `worker/eval_track.py` 的种子池区间 —— 都改成 import 期显式 `raise`（`python -O` 不再可能静默跳过它们）。函数体里的 62 处按 P1-9 原裁定「热路径保留但注明」冻结进 `tests/golden/assert_baseline.json`（只许调小，豁免 `worker/scripts/` 与 `hub/smoke_loopback.py`）；同文件新增**第三条**门禁：可执行命令面（`.sh` / `Makefile` / CI / `package.json` scripts）不得出现 `python -O`。详见 `docs/nn/engineering.md` §129。 |
| P1-10 `torch.load` 依赖版本默认值 | **已修** | 生产 8 处 `torch.load` 全部显式 `weights_only=True`（`remote/train_core.py` 3、`remote/opt_delta.py` / `opt_standard.py` / `round_forms.py` 各 1、`worker/ppo/common.py` 2）；`tools/paddle_parity.py` 的 2 处同样显式；新增 `tests/test_torch_load_safe.py` AST 门禁，直接调用与 `from torch import load` 都拦；`tools/` 豁免 |
| G4 宽泛异常处理增长 | **已加门禁** | `tests/test_broad_except_budget.py`：AST 扫 `common/biz/hub/remote/train/trainer/worker` 的 `except Exception` / `except BaseException` / 裸 `except:`，按文件冻结存量（`tests/golden/except_baseline.json`，**339 处 / 137 文件**；2026-11-14 抬格两笔记在测试文件头的记账表：`pump_lines` 的回调边界、`_release_cloud_machine` 的 `unassign` 调用边界；同日 `deliver_proc._forward` 收窄 −1 回到底数）；`tools/` 与 `worker/scripts/` 与 G3 同款豁免。只拦增长 + 基线只许调小；具体异常（`except (OSError, ValueError)`）不计数。（G4 只管「捕得宽」，不管「捕了不做」—— 后半句是 G9） |
| G6 抑制符增长 | **已加门禁** | `tests/test_suppression_budget.py`：`tokenize` 只读 `COMMENT` token，按文件冻结 `# noqa: F401`（**已全仓归零（2026-11-19 第四批：最后三处 —— `hub/smoke_loopback.py` 的死 import 直接删、`remote/offline_boot.py` 的探测改 `importlib.import_module`、`worker/ppo/engine.py` 的契约名改 `x as x` 再导出；自检锚点从「活文件」换成「扫描器本身」）**；2026-11-14 两批随 `trainer/dispatch_queue.py` 的 re-export 面退役 7 → 0；2026-11-16 两批再退役 11 条：`worker/ppo/engine.py` 11 名 → 1 名、`trainer/` 四处（`run_rl` 6 · `eval_dispatch` 3 · `loop` 2 · `dispatch` 1 全归零）⇒ 18 → 6；2026-11-16 第三批退役 `worker/ppo/common.py` 对 `np_core` 的 29 名再导出 ⇒ 6 → 5；第四批 2026-11-16 再退役 `worker/data/{dataset,weights_io}.py` 两处 ⇒ 5 → 3）与 `# type: ignore`（**163 处 / 68 文件**；271/98 起 —— 2026-11-14 开 `warn_unused_ignores` 后清掉 111 条无用 ignore）存量；字符串里引用的字样不算。只拦增长 + 基线只许调小 |
| G9 「宽泛捕获 + 只 `pass`」增长 | **已加门禁 + 首批清理** | `tests/test_silent_except_budget.py`：AST 扫同一生产面，冻结 `except Exception/BaseException/裸 except:` 且**体内只有一个 `pass`** 的存量（`tests/golden/silent_except_baseline.json`，**35 处 / 28 文件**）；具体异常、体内还有别的语句、`except*` 都不计。只拦增长 + 基线只许调小。为什么与 G4 分开：「捕了并处理」和「捕了假装没发生」是两种事故—— 2026-11-14 的活例就是日志层自己（镜像写失败 / 句柄关闭失败都是裸 `pass` ⇒ 「课日志在丢行」与「本来没开日志」长得一样）；同批删掉 `worker/train/loop_util.py` 退役循环工具，带走 5 处这类静默。第二批（同日晚）= `remote/job_lifecycle.py` 的 6 处控制面通知（`start` / `ready` / `abandon` / `release` / `heartbeat` / 丢副本回调）从裸 `pass` 改成「不抛但留一行（job id + 异常类型 + 兜底是什么）」，并配行为用例 `tests/remote/test_job_control_visibility.py`（5 个通知逐条：失败不致命 + 恰好一行、带得上异常类型）。心跳周期 60s ⇒ 一条/分钟，不构成刷屏。第三批（同日晚）= **开算打点的另外三处**：`remote/train_core.py` 的 torch / paddle 两条 PPO 腿（`on_ppo_start`）与 `remote/push_dispatch.py::_mark_started`（推模式下 GPU 机从不连 hub，这个大点只有它会打）—— 失败 ⇒ `computing_at` 为空 ⇒ hub 按掉队再派一份，现场只看到「备份莫名变多」；配行为用例 `test_hub_push_dispatch.py::test_start_beacon_failure_is_logged`。第四批 = **子进程行泵**（`remote/child_proc.py::pump_lines`）：原先把「读流被截断」（EOF 的回声，静默是对的）与「回调自己抛」（我们的行解析/记账 bug）揉进同一个宽捕，后者会让泵线程当场停摆 ⇒ 调用方再也看不到子进程的任何一行（含协议行），只能等到超时。现在两种分开：读流照旧静默，回调逐行兜住、继续读、收尾报「丢了几行 + 第一次的异常」，两个代理（`upload_proc` / `payload_proc`）把各自的 `_log` 传进来。用例 `tests/remote/test_child_proc_pump.py`。第五批 = **`remote/worker_proc.py::_release_cloud_machine` 的 `unassign`**：探测与调用原先共罩一条宽捕 `pass`，于是日志先答应「→ 直接调用 runtime.unassign()」、失败后没有下文 ⇒ 读到的是「停机已安排」，实例却还在计费（§386 的意义就是「worker 退出 ≠ 停机省钱」）。拆开后探测仍静默（非 Colab / 子进程里 `get_ipython()` 为 None 是常态），调用失败留一行「异常 repr + 机器未释放 + 该怎么做」；用例 `test_remote_hotswap.py::test_release_cloud_machine_reports_failed_unassign`（另加成功路径不刷告警一条）。该文件 G9 2 → 1, G4 同笔 +1（新边界，记账在 `tests/test_broad_except_budget.py` 表头）。第六批 = **EvalBoard 心跳的两个训练侧调用点**（`trainer/loop_lifecycle._evalboard_idle` 开窗 / `trainer/loop_dispatch._evalboard_yield` 关窗）：原来是 `except Exception: pass`，静默的后果不是「少写一个 JSON」，而是 console 的「训练忙碌中 / 已等 N 分钟」**永久停在旧值**（训练侧一切正常、屏幕上像卡住，而这条链的读者正是靠那个文件判断要不要干预）；改成 `default_router().warn_once(key, …)`，并顺手补上 `worker/eval_heartbeat.write_state` 内部落盘失败的同一句（那是真正会失败的那层，「不抛」不等于「不留痕」）。用例 `tests/trainer/test_loop_heartbeat_visibility.py`（走延迟 import 的 DI seam 记入参，不读私有字段）+ `tests/worker/test_eval_heartbeat.py::test_write_failure_is_reported_once_and_never_raises`。第七批 = **补传腿的转发线程**（`remote/deliver_proc.DelivererProcess._forward`）：它自己手写的行循环与 `pump_lines` 重复，而且同样是「读流截断」与「回调自己抛」共罩一条宽捕 `pass` —— `self.log` 一抛（最终往 stdout 写，父进程 stdout 被关时 `print` 抛 `ValueError`）转发线程就停摆，而 `_boot` **只有这条线程会置位** ⇒ 子进程明明打了 `boot ok`，父侧却只看到「等 boot ok 超时」并 sticky 降级。改成直接复用 `pump_lines`（口径单源），并顺手补上它自己的一处漏：报数那行也是往 stdout 写，失败会把整条行泵带走（现在 `except (OSError, ValueError)` 到此为止）。该文件 G9 3 → 2、G4 8 → 7。第八批 = **窄捕（第三种处置）**：`remote/lifecycle.py` 4 处（`/proc` 读 RSS、NVML 读显存、`statvfs` 读盘、SIGTERM 自投）与 `remote/colab_bc.py` 4 处（venv / 系统 python 探测 ×2、state 读不了、monitor 尾读日志）—— 捕获面本来就只该是具体的（OSError / RuntimeError / ValueError / SubprocessError），宽捕只是把本模块自己的 bug 一起吞掉。其中两处**真信息丢失**改成留一行而不是窄捕了事：`load_state` 读不了（现场只看到「进度莫名归零」，与 disk 满长得一样）与 **SIGTERM 自投失败**（进程活着而 hub 当这台已下线 = 重复计费，这是本模块唯一会改变进程结局的失败）。G9 51 → **44**（colab_bc 4→0，lifecycle 3→0），扫描地板 50 → 40；G4 441 → **433**（lifecycle 4→0，colab_bc 5→1，剩下那处是 `mount_drive` 的第三方 API 边界）。第九批 = **`remote/job_lifecycle.py` 的十条控制面通知**收敛成 `remote.http.NET_ERRORS`（OSError / HTTPException / WireSlowError / RetryableError / 防御性 RuntimeError），11 → **1**（留 `on_drop` 回调边界）。这不是放松：原来 `AttributeError` / `TypeError` / `KeyError` 会被吞成「hub 不可达（租约兜底）」，现场永远像网络问题；`ProtocolError` 刻意留在外面（确定性拒绝，调用方要据此走放弃/首写胜出）。判据 `test_job_control_visibility.py::test_programmer_error_is_not_disguised_as_a_network_failure`（五条通知逐条；实测把 `job_started` 改回宽捕 ⇒ 该参数化用例红）。G4 → **423**；用例 `tests/remote/test_lifecycle.py` +3（读数缺项不崩 ×2、自投失败留痕 ×1），文本形态断言 1500 → 1501（记账在该文件 BASELINE_FORM 上方）。第十批 = **同一个 `NET_ERRORS` 推到 `remote/hub_http.py`**（传输薄壳，10 处 → **0**）：薄壳的 `except Exception` 把「hub 没答」与「我们自己的 bug」抹成同一分支 —— 探针把 `AttributeError`（回执形状变了）读成 `PROBE_TRANSIENT`，轮询环于是永远「还没好」＝静默挂死，真因从不出现。基底 `NET_ERRORS` 落 `common/errors.py`（叶子**零 import** 这条不破：`http.client.HTTPException` 由各传输壳自己追加，`remote/http.py` 与 `hub_http.py` 各自组装），并经 `common.protocol` 门面转发；`hub_http.py` 的依赖闭集只多了一个 stdlib `http.client`（记账在 `test_hub_http_split.py` 的 `ALLOWED_IMPORTS` 旁）。判据 `test_hub_http_split.py::test_probe_does_not_disguise_a_programmer_error_as_transient`（实测把那一处改回 `except Exception` ⇒ DID NOT RAISE 红）。G4 423 → **413**、G9 44 → **42**（该文件 2 处静默同批收窄）。第十一批 = **`common/distribution.py` 的探活 / 探测 / 取消面**（11 → **2**，G9 2 → 1）：这些函数的失败返回值（`node_ping → None`、`weights_cached_on_node → False`）不是「没答」而是**业务结论**——`None` 意味着该节点被排除出本轮分发、份额落回本地。宽捕把「连不上」与本模块自己的 bug 抹平后，现场就是「全部节点突然不可用」且一行真因都没有。改为 `NET_ERRORS`（基底 + `http.client.HTTPException`），探活类再并上 `ValueError`（200 但 body 不是 JSON，`PING_ERRORS`）；`weights_cached_on_node` 顺手把形状判断显式化成 `isinstance`。留下的 2 处是第三条边界：`_closer`（对任意响应对象尽力 `close()`）与 `fetch_task` 的「非 `DistError` 确定性失败」兜底。判据 `tests/common/test_dist_ping_errors.py`（3 条，新增文件）+ `test_dist_common_poll.py::test_poll_result_lets_a_programmer_error_through`；两处各自改回宽捕实测红。第十二批 = **`remote/notebook_runtime.py`**（7 → **0**，G9 1 → 0，扫描地板 400 → 370）：notebook cell 里的 hub / worker_server 出站点原先把一切错都打成「FATAL: hub 不可达——检查隧道 URL」，连本模块自己的 `AttributeError` 也被指去查隧道（最贵的一种误诊）。hub ping 与 `/ping` 探针改 `NET_ERRORS` / `PROBE_ERRORS`；`md.version` 收窄成 `PackageNotFoundError`；token 文件 `chmod` 收窄成 `OSError`（Colab FUSE 挂载点不一定支持）；顺带修 `_run_probe` 的 `except BaseException` 吞 Ctrl-C（子进程探测最长 60s × 2 核，按了 Ctrl-C 却在「按未知处理」后继续跑）。判据 `tests/remote/test_notebook_runtime.py` +4（不可达仍 -2 / 程序员错穿出 / 探不起来回落 CPU / Ctrl-C 不被吞；各自改回宽捕实测红）。G4 → **397**、G9 → **40**。第十三批 = **`worker/forensics.py` 的「降级即静默」**（G9 5 → **0**，G4 同笔 +1 = 边界兜底）：取证模块的契约是「任何失败都降级、绝不反杀训练」，但降级不等于静默 —— `rss_mb: 0` 与「探针自己失败了」在 jsonl 里长得一模一样，而这份文件唯一的读者是事故后想回答「上一轮到底有没有 OOM」的人。5 处 `except Exception: pass` 改成 `_note(errors, …)`：值与缺省语义逐字不变，失败成因进快照的 `probe_errors`；顺手把 `rss_mb()` 的「绝不出错」变成真的（探针自己抛也降级，G4 +1 记账）。判据 `tests/worker/test_commit_journal.py` +4（探针失败留成因 / 探针自炸不抛 / 量不到的盘留名 / 全好时不出现该键；把 `_note` 改回 `pass` 实测 2 条红）|第十四批 = **三处「先吞后说」的收尾**（G9 35 → **32**）：① `trainer/batch_runner._heartbeat` 的 `except Exception: pass` —— 它包的是「写心跳」整块（含 `from worker.eval_heartbeat import …`），包导不进来与「心跳根本没调」在 console 上完全同形（第六批修的是 `write_state` **内部**落盘失败，这里是它**之前**那段）；改 `default_router().warn_once("eval_heartbeat:batch", …)`，`tests/trainer/test_loop_heartbeat_visibility.py` +2。② `worker/loop_guards_sweep._rotate_cleanup` 的 `except BaseException: pass` —— `swept 0`（确实没有孤儿波次）与「清理器自己炸了」（磁盘在涨的前兆）在日志里同形，而且 `BaseException` 连 Ctrl-C 一起吞（批量 rmtree 是本轮最慢的动作）；收窄成 `Exception` + 一行 `workdir-sweep itN skipped: <repr>`，`tests/worker/test_guards_sweep_visibility.py`（新，2 条：失败留痕不反杀 / Ctrl-C 穿出去）。③ `remote/hub_client.git_head` 的「先 `pass` 再抛一句『无法解析 git HEAD』」—— git 没装 / cwd 没了 / 超时 / 不是 git 仓库四种成因压成同一行字；改成 `raise … from e` 带成因，退出码非 0 与空输出各自一句话，`tests/remote/test_hub_client_git_head.py`（新，4 条）。三处还原成旧行为各实测红（共 6 条）；文本形态断言 1501 → **1508**（记账在该文件 BASELINE_FORM 上方，逐笔 7 条）第十五批 = **两处降级留痕 + 一处死双生删除**（G9 32 → **29**，G4 398 → **397**，140 文件不变）：① `worker/terminal_stats.write_terminal_stats` 的 `except Exception: pass` —— 它写的 `iteration-terminal.jsonl` 只被「事后回看训练进程」的人读，而它停更与「这轮没有终局数据」在盘上完全同形（docstring 还写着「所有异常静默捕获」）；改 `warn_once(WARN_KEY, …)` 带成因。② `worker/serve_pool.begin_round` 的 `w.kill()` 循环 —— 该段存在的理由就是「只从列表剔掉会留尸」（docstring 原话），连 kill 自己失败也被 `pass` 吞：那一份是仍在吃内存的孤儿，而 `killed` 已经把它算进去了；改为每个失败留一行（轮边界最多几个，不必去重）。③ `remote/job_fs._git_head` 查下来是**死双生** —— `remote/code_snapshot.py` 有自己的 `_git_head`（且已走 `run_capture` 防闪窗），job_fs 这份全仓零调用，只靠 `remote/worker.py` 的转发名活着 ⇒ 直接删（连同 `__all__` 条目、worker 的转发块、`common.proc.run_capture` import），没给它加日志。判据：①② 还原成旧行为各实测红（2 条）；③ 由既有 `test_moved_names_are_defined_in_job_fs_and_not_redefined_in_worker` / `test_worker_forwards_every_moved_name` 覆盖。文本形态断言 1508 → **1511**（+4 新、-1 随 job_fs 测试删除，记账在该文件 BASELINE_FORM 上方）。同时把 G9 的绝对文件数下限（`>= 25`）删掉：清到 22 个文件后它只是又一处人工回填，扫描面活性由派生根数地板兜住｜**新门禁**（2026-11-15，接 P1-11「转发名」）：`tests/test_forward_names_are_live.py` —— 全仓 15 个含 ≥3 个 `name as name` 转发的文件（≈500 名）逐名判活，只活在转发声明里的名字判红。推广后立刻抓到真例：`common/protocol.GOAL_SCRIPT` / `INTENT_SCRIPT`（定义模块 `common/manifest.py` 自己在用、门面上零读者）已删。口径坑：第一版判活用「全仓文本含该名字」，而门禁文件自己的说明里点名了这两个名字作例子 ⇒ 它自己成了「还有人引用」的证据（**判据文件的示例文字不得成为判据输入**），修法是把门禁自身排除出扫描面。性能：一次分词建 `{标识符: 文件集合}` 倒排索引，3 个用例 ~4s（不做索引 17s）。第十六批（2026-11-18）= **`trainer/loop_transport.py` 的五处 rl-config 课级覆盖读取**：原写法 `try: … except Exception: <用缺省>` 罩住的**不是**配置读失败（`load_dist_config` 的契约是永不抛：文件缺失 / 坏 JSON / BOM 都返回 None）⇒ 真正被吞的是本模块自己的 bug（配置块从对象变成列表 ⇒ `AttributeError`、键改名 ⇒ `TypeError`），而处置是「静默用缺省」= 配置改了却没生效、日志里一个字都没有。改成三个**形状总函数**（`_config_block` / `_course_stem` / `_course_value`，形状不对喊一次 `warn_once`）后整段没有可捕的东西；`run_iters` 的非数字 / 非整数值也是窄捕（`TypeError, ValueError`）+ 一行。G4 397 → **385**（`loop_transport` 9 → 1，剩那处是 `_with_node_failover` 的单节点边界）、G9 26 → **25**（该文件 1 → 0）。三条用例随之从「宽捕静默」改成**真实失败形态**（`load_dist_config → None`）+ 两条「程序员错穿出去」的反向判据（把 `return_value` 改回 `side_effect` 即红）。详见 `docs/nn/engineering.md` §132 | 第十七批（同日）= **`hub/task_pack.py` 的观测面**（6 → **0**，138 文件）：三个读包助手（`pack_index_part_sha` / `pack_index_meta` / `pack_seg_end_kind`）与三个打控制台的调用点都是 `except Exception`。前者的后果最阴：包其实是好的，但索引 JSON 形状一变（对象 → 列表）就 `AttributeError` ⇒ 读成「这包没有计划」，云机拿到空段末判据、静默回退成旧行为；后者的后果是「我们自己的 bug」被记成「控制台不可达 —— 降级为手动导出」。改成两个具名异常集：`_PACK_UNREADABLE`（OSError / BadZipFile / EOFError / KeyError / NotImplementedError / ValueError = 真读不出来）与 `_CONSOLE_NET_ERRORS`（OSError / HTTPException / ValueError）；`AttributeError` / `TypeError` 现在穿出去。判据 `tests/hub/test_offline_task_pack.py` +3（真失败形态=坏包 ⇒ 不可知；包读里的 `AttributeError` 不许读成包坏了；控制台不可达 ⇒ unknown、本模块 bug ⇒ 穿出）。G4 385 → **379** | 第十八批（同日）= **`hub/offline_push.py` 派发器**（10 → **2**，138 文件）：八个「读不到 ⇒ 不编」的宽捕里，`_default_root` 最危险（`traj_root()` 契约是 `Path | None`，宽捕只是把本模块的 bug 换成缺省值 `Path.cwd()` —— 一个扫错目录的派发器）；`_pack_meta` 包的 `pack_index_meta` 已是总函数 ⇒ 整段删除；`_still_mine` 是 fail-open（读不到 ⇒ 「还是我的」），宽捕会把 `hold_of` 的形状 bug 读成「永远是我的」⇒ 永不放手，现已显式化 `or {}` 并收窄。剩下 2 处是**真边界**且都留痕：pump 线程主循环（线程不许死）与 `_claim`（拒因带异常类型）。判据 `tests/hub/test_offline_push_dispatch.py` +3（None 契约 / 无 hold = 仍是我的 + 本模块 bug 穿出 / 读面失败仍 fail-open）。G4 379 → **371** | 第十九批（同日）= **四条 fail-open 读面的具名集**（9 → 0/1/2，`hub/http_face.py` 4 → 2、`hub/store_offline.py` 3 → 0、`worker/sibling_reuse.py` 3 → 1、`remote/bc_job.py` 4 → 2）：① 请求体读（`OSError` + 非整数 `Content-Length` 的 `ValueError`）——宽捕把 `rfile` 形状变了的 `AttributeError` 也答成 400「read body failed」；② 回传 wire 解码（`_WIRE_DECODE_ERRORS`：`binascii.Error`(ValueError) / `EOFError` 截断 gzip / `zlib.error` 坏 deflate / `BadGzipFile`(OSError)）与两条「观测面不拖垮回传」的落位（`_LAND_ERRORS`）；③ 兄弟课程加载（`load_course` 只捕文件面；`episode_identity_fp(other)` 对已校验课程是全函数 ⇒ try 整段删，旧的 `continue` 让复用默默死掉）；④ BC 两条回传腿（`_BC_HTTP_ERRORS` = `_request` 的三类路面，`AttributeError` 不再被记成「网络不通」白等两轮退避）。判据 4 处、还原成宽捕各实测红。G4 → **362**（137 文件） | 第二十批（同日）= **`remote/offline_deliver.py`**（12 → **6**）：补传腿的「hub 连不上」与「这一发没成」被写成一件事。`probe()` / `_post()` 改捕 `_DELIVER_NET_ERRORS`（OSError / `http.client.HTTPException` / `RetryableError` = `_opener` 的路面）——旧宽捕把 `_headers()` 的 `KeyError` 读成「hub 不可达」；`sanitize_run_id` 只捕 `ProtocolError`（它的唯一契约失败）；`backup_pending` / `_prune_drawer` 的判据面收窄成盘 + 账行形状；`_err_text` 删掉不可能触发的 try；`_guard` 的 `BaseException` → `Exception`（后台线程不再吞 Ctrl-C）。留下的 6 处各自就地写了理由。判据 `tests/remote/test_offline_deliver.py` +3。G4 → **356**（137 文件） | 第二十一批（同日）= **`remote/rollout_node.py` 探活面**（8 → **4**）：`_ping` 的结论是「节点死了 ⇒ 重启 agent」，而旧宽捕把回执形状的 `AttributeError` 也读成不健康 ⇒ 重启环（`_status` / `_probe_https` 同一个集：`NET_ERRORS` + `http.client.HTTPException` + `ValueError`）；`_install_bun_mirror` 只吞「网/盘/坏 zip」（`OSError` / `RuntimeError` / `BadZipFile` / `EOFError`），zip 布局假设变了要穿出去——它的返回值就是「镜像装没装上」。留下的 4 处：`_terminate` 的两级 kill（G9 冻结的收尾边界）、`_slot_count` 的 platform_utils 兜底、`_open_tailscale`（`tailscale_boot.ensure` 的异常族由那边定）。判据 +2，反向改回宽捕实测红。G4 → **352**（137 文件） | 第二十二批（同日）= **`remote/push_dispatch.py`**（6 → **3**）：`_job_priority_of` 是**进程内纯内存**判据，旧宽捕把 hub 方法的 bug（判据表换形状）读成 `PRIORITY_HIGHEST` =「没人在做这一份」⇒ **备份永不派发**且无日志 ⇒ 现在整段删 try（边界在 `run_tick_loop`）。`_http` 的两处收窄成 `_PUSH_HTTP_ERRORS`（`NET_ERRORS` + `HTTPException` + `ValueError`；4xx/5xx 仍算「有答」）与「状态码有了、体读不出来」的 `(OSError, HTTPException)`。判据 `tests/remote/test_hub_push_dispatch.py` +1。G4 → **349**（137 文件） | 第二十三批（同日）= **`remote/offline_boot.py`**（19 → **16**，生成物：改 `_offline_boot/07_queue.pyfrag` 后跑 `tools/build_offline_boot.py` 重建）：`_progress_label` / `_should_log_progress` 的引导兜底收窄成 `ImportError`（「没这个包」）；第二段 try（已 import 成功的判据）留 `TypeError` / `ValueError` —— 那是**跨版本**真边界（老 `code.zip` 的签名不同），`AttributeError` / `KeyError` 现在穿出去。判据 `tests/remote/test_offline_boot.py` +1，改回 `Exception` 实测红。G4 → **346**（137 文件） | 第二十四批（同日）= **`remote/offline_boot.py` 的控制面读面**（16 → **13**）：新增 standalone 版 `_BOOT_NET_ERRORS`（OSError / `http.client.HTTPException` / ValueError，本文件不得 import 本仓代码）供 `_post_json`（连不上仍 `(0, {})`）与 `_http_error_parts`（HTTPError 正文读不出来仍「（无正文）」）；`hub_candidates` 收窄成 `(ImportError, ValueError, TypeError)`（兄弟文件没拉下来 / 配置脏值），`resolve_hub_url` 的 bug 穿出去。判据 `tests/remote/test_offline_boot.py` +2。G4 → **343**（137 文件） | 第二十五批（同日）= **拉子进程的边**（`remote/deliver_proc.py` 7 → **4**、`remote/child_proc.py` 7 → **6**）：`_spawn` / `spawn_module` 只捕 Popen 的真失败面（`OSError` / `ValueError` / `SubprocessError`）——把「我们拼 `env` 的姿势坏了」报成「Popen 失败」会把人指去查系统；`_new_state_path` 只捕 `sanitize_run_id` 的 `ProtocolError`；`backup_pending` 收窄成盘 + 账行形状。`trainer/stream._load_wave` 同型但**未改**：它是闭包、无公开接缝，按 §7「先写判据再改」的口径暂不动。判据 +2。G4 → **339**（137 文件） | 仓根锚点散成 13 份 | **已收口 + 门禁** | `REPO_ROOT` 曾同时住在 `common/distribution.py` 与 13 份「自己数层数」的副本里（`biz/ hub/ remote/ trainer/ worker/`）；`hub/store_offline.py` 那次 `parents[3]` → `parents[2]` 就是活例 —— 层数算错**不报错**，只是路径全指到仓库父目录。2026-11-16：`common/paths.py::REPO_ROOT` 改成 `Path` 并成为唯一一份（13 文件改 import，`trainer.dispatch_queue` / `remote.artifacts` / `hub.store_offline` 等处的本地定义与 `trainer/batch_eval` 的 `X as X` 自别名一并删除）；新增 G12 `tests/test_repo_root_anchor.py` （三种写法文本扫描，按文件冻结 17 个**入口脚本自举**位置，只许调小）。三向证伪：新文件出现 / 基线条目加一处 / 基线条目清零未删表 —— 全红。 |
| §9 模块 docstring 超 50 行 | **已加门禁** | `tests/test_module_doc_budget.py`：模块 docstring 行数 > 50 按文件冻结（`tests/golden/module_doc_baseline.json`，**13 文件**：`biz/__init__.py` 113 · `common/__init__.py` 99 · `trainer/__init__.py` 90 · `remote/code_snapshot.py` 64 · `hub/http_face.py` 62 · `remote/opt_delta.py` 60 · `worker/stream_collect.py` 55 · 其余 6 个 51–53）；只拦增长 + 基线只许调小。不一次清零的理由（三个 `__init__.py` 是分层契约）写在测试模块头。**债在减**：2026-11-16 `common/game_watch.py` 51 → 50（把「为什么重试而不是竞速副本」那段压掉一行），基线条目随之删除，现 **12 文件** |
| §9 注释指针腐烂（`path.py::name` 指向不存在的文件） | **已加门禁 + 清零** | `tests/test_docstring_refs.py`：`tokenize` 只读 COMMENT + STRING，查「带目录的 `file.py::name`」是否落在真实文件上（解析：仓库根 → `nn-training/` → 引用者目录 → 包内后缀）；不查行号形态与裸文件名。落地时 13 处旧家指针全部改成现在的家，新用例 1.6s。 |
| G5 测试私有断言增长 | **已加门禁** | `tests/test_private_assert_budget.py`：AST 扫 `tests/**` 的 `assert`，判据含私有属性（`x._x`，不含 dunder）或私有键下标即计数，按文件冻结（`tests/golden/private_assert_baseline.json`，**172 条 / 50 文件**）；只拦增长 + 基线只许调小。**2026-11-18 G5 第一批（先用已有公开面，不新建模块）**：`tests/hub/test_hub_queue_split.py` 16 → **11** —— `_stores` 表 → `queue_state()["courses"]`、`_workers` 登记 → `active_worker_count()`；`_locate_cache` / `_cursor` / `_leases` 三处白盒读由公开面覆盖（`course_of` / `qs["cursor"]` / `result_token_ok` + `epoch_of`）并删掉重复断言；留下的 11 处是**结构对账**（`__mro__` / `__bases__` / `STATE_WRITERS` / `_adopt_solo` 搬状态）与 `_job_dir` 哨兵契约 —— 那是被测对象本体，不是待迁的耦合。**第六批（2026-11-18）**：`tests/worker/test_rollout_volume.py` 46 → **2** —— 新增 `VolumeView` + `TrainingVolume.volume_view()` 只读观测面（`target` / `collected` / `waves` / `g0` / `est` / `capped` / `halted` / `stage_stats` / `rotation_seed` / `report` / `greedy_*`），字段名取 iteration 行语义；留下的 2 处是 `CommitJournal.pending()`（独立 WAL 对象自己的公开查询）。**第七批（2026-11-18）**：`tests/remote/test_rollout_node.py` 43 → **8**、`test_rollout_node_notebook.py` 5 → **4** —— bun / cloudflared 的查找、下载、持久缓存从 `rollout_node` 拆成 `remote/node_toolchain.py`（L1，只碰 `cfg` / `log` / 文件系统），45 个工具链用例随之搬进 `tests/remote/test_node_toolchain.py` 直接断言公开函数；隧道日志/健康用例同样改问 `remote/rollout_tunnel` 的公开面（`test_rollout_tunnel.py`）。节点监护循环不再替「工具从哪来」背测试。**第八批（2026-11-18）**：`tests/worker/test_eval_timing.py` 32 → **0** —— 新增 `EvalTimingView` + `TrainingEval.eval_timing()`（`round_thread` / `gate` / `tail` / `join_sec` / `backfill` / `pending_m` / `open_threads`），补账队列快照成元组、尾巴保持活引用；顺手给 `_eval_thread` / `_eval_gate` 补类级 `None` 默认值。**第九批（2026-11-18）**：`tests/remote/test_hub_push_dispatch.py` 17 → **0** —— 全部改问队列已有的公开门面（`get_result` / `store_result` / `wire_stats` / `job_status(...)["computing_at"]`），派发器在飞账改读 `tick()["inflight"]`（本来就是 `/admin/push-workers` 的快照）。**第十批（2026-11-18）**：`tests/worker/test_kickstart_burn_guard.py` 20 → **0** —— 新增 `BurnGuardView` + `TrainingGuardsLeg.burn_view()`（`streak` / `stop_cause` / `stop_detail` / `notified` / `fallback_logged`）；测试替身的 `_halt_calls` 改公开 `halt_calls`，驱动被测入口的 `assert leg._kickstart_burn(...)` 移出 assert 表达式。**第十一批（2026-11-18）**：`tests/trainer/test_loop_gate_soft_remediate.py` 19 → **0** —— 新增 `GuardStateView` + `TrainingGuards.guard_state()`（`cloud_halted` / `leg_abort` / `soft_remediate_count`），`_apply_verdict` / `_gate_halt_mode` 的调用移出 assert 表达式。**第十二~十五批（2026-11-18）**：四处执行面读数 —— `profile_steps` 转正（`test_ppo_profile_gate.py` 12 → 0）、`RemoteFailState` + `remote_fail_state()`（remote failure 15 → 1，留轮级 `_consec_fail`）、`BaselineEvalView` + `baseline_eval_state()`（baseline eval 16 → 0，份额门复用 `eval_timing().gate`）、`ResumeState` + `TrainingLifecycle.resume_state()`（train ledger wiring 16 → 0，启动继承十二项 + 账本摘要）。**第十六批（2026-11-18）**：`test_kickstart_plan.py` 15 → 0、`test_ppo_watchdog.py` 13 → 0 —— 命中的私有名都只是**驱动入口**（`_kickstart_burn` / `_remote_ppo_step` / `_bump_remote_fail`），调用赋给局部变量再断言；`_leg_abort` 改读 `remote_fail_state().leg_aborted`。**第十七批（2026-11-18）**：`test_wire_reroll.py` 12 → 2（`remote/wire.py` 新增 `best_rate()`，读写分离；注入点仍必须是 `wire._BEST_RATE`；留 2 处 `_WIRE` / `_BULK` 拆分契约的结构断言）、`test_deliver_mode.py` 11 → 0（`DeliverState` + `deliver_state()`：`delivered` / `metrics_sent` / `metrics_stat` / `metrics_unsupported` / `mode`；`status()` 的 operator 字典面不变）。**第十八批（2026-11-18）**：`test_remote_serve_pool.py` 10 → 0 —— 5 条建池前置断言把 `iter_rollout._make_pool(...)` 的调用移出 assert；5 条读 `_workers` 的断言改读新增的 `ServePool.workers()`（**活引用元组**，锁内快照：「跨轮复用不重建」「未就绪不杀」要看同一个 worker 的身份和它的 `proc.poll()`，计数读数不够）。**第十九批（2026-11-18）**：`test_payload_selfcheck.py` 7 → 0（`_old_heuristic_misjudges` / `_payload_reader_selfcheck` 是重打循环内部判据，不为用例造公开 API，只把调用移出 assert）、`test_warmup_criterion.py` 7 → 0（`tests/helpers/warmup._needed` 是助手自己的判据 ⇒ 公开 `needed`）。**第二十批（2026-11-18）**：`test_offline_push_dispatch.py` 7 → 0 —— 替身字段 `_late_body` 改公开 `late_body`；`_adopt_existing` / `_settle_assignment` / `_settle_retry` 的调用移出 assert；新增 `OfflinePushDispatcher.pump_count()` 回答「拉取线程退干净没有」（账本先空、线程后退出，与 `tick()["assignments"]` 不是同一时刻的同一件事）。**第二十一批（2026-11-18）**：`test_notebook_runtime.py` 8 → 0 —— `_resolve_backend` / `_run_probe` / `_tpu_holders` / `_hub_base_of` 都是被测入口本身（前两个还是夹具注入点 / 被 monkeypatch），改名带不来契约收益 ⇒ 一律把调用移出 assert。**第二十二批（2026-11-18）**：`test_paired_kill.py` 6 → 0、`test_greedy_rollout_chain.py` 6 → 0 —— 前者全是驱动入口；后者「node 腿每轮清零上轮 greedy 槽位」的跨轮契约改读新增的 `GreedyView` + `TrainingVolume.greedy_view()`（`plan` / `stats` / `report` / `waves`），用例脚手架换成继承 `TrainingVolume` 的最小 `_NodeLoop`；volume 拆分守卫成员闭集 12 → 13。**第二十三批（2026-11-18）**：`test_offline_deliver.py` 6 → 0 —— `DeliverState` 补上拒收面（`rejected` 升序轮次）与 `ledger_readable`（抽屉删除的硬门槛，DoD-3c），D6 的 TTL / 人工重置用例改读它；`_reject_round(...)` 的调用移出 assert。**第二十四批（2026-11-18）**：`test_hub_auth_d9_order.py` 8 → 0 —— 新增 `AuthState` + `_AuthGuard.auth_state()`（`failures` / `blocked_until`），两份 dict 锁内复制：请求线程持续改写，测试要的是某一刻的一致快照；`_JobStore` 继承 `_AuthGuard`，既有入口可直接读。**第二十五批（2026-11-18）**：`test_auto_handoff.py` 8 → 0 —— 新增 `QueueScopeMixin.course_store(course)`（课程 → store 只读查询，未知课程 None）与 `LedgerMixin.is_cancelled(job_id)`（进程内即时闸，不按账本对账）；可领池改读既有门面 `hub.claimable_job_ids(course)`，租约断言改用 `lease_worker(jid) == ""`。守卫：`DOMAIN_METHODS` 136 → 137、实现名 138 → 139、store `MIXIN_METHODS` 71 → 72。**第二十六批（2026-11-18）**：`test_loop_park.py` 6 → 1 —— 新增 `TrainingLifecycle.stop_reason()`（只读、不清账）与 `collect_child()`（活引用），`finish_course` 内部同步改用；`_budget_hard_cut` 调用移出 assert；留 1 处 `_node_rollout` 初值断言（**构造契约**，AttributeError 事故，不是执行面读数）。lifecycle 拆分守卫成员闭集 7 → 9。**第二十七批（2026-11-18）**：`test_loop_gate_nopark.py` 5 → 0、`test_remote_ppo_phases.py` 5 → 0 —— 零生产改动，复用既有接缝：停机旗标读 `guard_state().cloud_halted`、连败/停腿读 `remote_fail_state()`，驱动调用移出 assert。**第二十八批（2026-11-18）**：`test_boot_wire_guard.py` 4 → 0、`test_offline_eval_wiring.py` 5 → 0 —— 零生产改动：`offline_boot` 是生成物（改名要重建发行物，收益为零），`_sync()` / `_post_artifact()` 的返回值就是判据 ⇒ 调用赋给局部变量后再断言。**第二十九批（2026-11-18）**：`test_dead_lease_sweep.py` 5 → 0、`test_remote_iter.py` 4 → 1 —— 新增 `has_lease()`（有记录、不回收）与 `lease_state()`（四态判据公开读数）；调度承诺三表改读既有 `scheduling_facts()`；`_leg_abort` 读 `remote_fail_state()`；留 1 处 `_zero_shard_streak`（测试替身承载、生产直接读写的槽位）。store 拆分守卫 `MIXIN_METHODS` 72 → 74。**第三十批（2026-11-18）**：`test_rollout_node.py` 8 → 1 —— `_status` / `_slot_count` / `_start_cloudflared` / `_open_tailscale` 的调用全部移出 assert；刻意留 1 处 `_slot_count` 作为 `test_scan_sees_the_known_surface` 的扫描面活性锚（该自检要求本文件基线 > 0）。**第三十一批（2026-11-18）**：`test_multi_course_hub.py` 5 → 1，`test_eval_a_once.py` 4 → 0、`test_eval_course_once.py` 3 → 0、`test_eval_dispatch_resilience.py` 4 → 0、`test_stage_rotation_wiring.py` 3 → 0 —— 鉴权计数读 `auth_state()`、旧 store 旗标经 `course_store()` 读 `halt_workers`；其余全是纯判据/驱动入口 ⇒ 先赋值再断言。留 1 处 `hub._job_dir`（队列门面里它就是 `_job_dir`，无另一份公开名）。**第三十二批（2026-11-18）**：`test_ppo_redo.py` 3 → 0、`test_seg_end_kind.py` 4 → 0、`test_rollout_node_notebook.py` 4 → 0、`test_lifecycle.py` 3 → 0 —— 驱动调用移出 assert；notebook 命名空间里的 `_fetch_archive` 先取结果；bound method 先赋 `handler`；替身的 `"_block"` 键改公开 `"block"`。**第三十三批（2026-11-18）**：`test_bc_dp.py` 4 → 0、`test_reward_golden.py` 3 → 0、`test_stream_alpha.py` 3 → 0、`test_admin_cancel_job.py` 3 → 0、`test_offline_deliver_async.py` 3 → 0、`test_offline_task_queue.py` 3 → 1、`test_bulk_sched.py` 3 → 1 —— 新增 `RewardFn.backend`（`formula`/`builtin`）、`DelivererProcess.child()`、`StreamCollector.alpha_started`；其余为公开门面替换与纯判据调用移出 assert。留 `_TASK_PACK_TRIGGERS`（清单即契约）与 `_p1_waiting`（计数器对称性）各 1。**第三十四批（2026-11-18）**：`test_loop_round.py` 5 → 0、`test_serve_aborted_revival.py` 5 → 3 —— 轮级 `_consec_fail` 零生产改动复用 `resume_state().consec_fail`；假引擎加 `leg_aborted` 公开读数（私有槽保留给 `_revive_hygiene` 清账）；留 3 处 `SimpleNamespace` 槽位契约（被测对象本体）。**第三十五批（2026-11-18）**：`test_priority_schedule.py` 3 → 0、`test_batch_runner_split.py` 3 → 0、`test_tools/test_forkdist.py` 3 → 0、`test_biz/test_course_spec_split.py` 3 → 2 —— 新增 `LeaseMixin.lease_expiry()`（纯读：判「这次操作有没有动过租约表」），持有人/承诺痕迹改读 `lease_worker()` / `scheduling_facts()`；纯判据调用移出 assert；store 拆分守卫 `MIXIN_METHODS` 74 → 75；`_block_dict` 两处身份契约保留。**第三十六批（2026-11-18）**：12 个文件各 2 → 0（`test_hub_leases` / `test_hold` / `test_offline_metrics` / `test_worker_pulse` / `test_bc_epoch_backfill` / `test_body_transfer_guard` / `test_control_pool` / `test_rollout_scratch` / `test_run_loop` / `test_reuse_ledger` / `test_batch_plan_split` / `test_to_cpu_batch`）—— 新增 `LeaseMixin.heartbeat_at()`（错 token 心跳不许动心跳锚）与 `HubHandler.pulse_readout()`（哨兵只读快照）；其余为纯判据调用移出 assert。store 守卫 `MIXIN_METHODS` 75 → 76。转发名 / 身份 / 兼容参数 / 归属缓存本体 6 个文件刻意保留。**第三十七批（2026-11-18）**：16 个文件各 1 → 0、`test_rollout_volume.py` 2 → 1 —— 新增 `common.runtime.ThreadOwner.running`（三条后台线程腿共用一份实现，避免逐字节重复审计新增债）、`UploaderProcess.degraded`、`PrefetchStore.handoff_path`、`StreamCollector.plan_cap`、`_JobStore.ledger_offset`；其余为纯判据调用移出 assert 与既有读数替换；store 守卫 `MIXIN_METHODS` 76 → 77；`test_rollout_volume` 保留 1 处 `_commit_journal()`（生产同名钩子 + 扫描面活性锚）。**第三十八批（2026-11-18）**：`test_rollout_end_event.py` / `test_offline_deliver_proc.py` / `test_push_priority_dispatch.py` 各 1 → 0 —— 新增 `PushDispatcher.inflight_records()`（同步状态副本，防「快慢当先后」）；`ctl_offset` 改读 `status()`（它本就并入子进程状态面）；断言里的 `_traj_dir` 改读脚手架路径。**第三十九批（2026-11-18）**：`test_rollout_dispatch_resilience.py` / `test_prefetch_proc.py` / `test_push_register.py` 各 1 → 0 —— 新增 `common.distribution.aborted_tags()`（全表收工态副本）、`PayloadProcess.local_store`（降级后的进程内实现）、`PushRegister.retry_step`（退避档位）。私有属性**赋值**与 `from x import _name` 未纳入（待接缝就绪）。**2026-11-19 第四十批（第十二批收尾）**：`tests/trainer/test_loop_eval_split.py` 12 → **6** —— `eval_timing()` 本来就有 `round_thread` / `tail`，只是用例还伸手进 `_eval_*` 槽位；顺手给它补上与本该成对的 `tail_start`（「尾巴在手但没起点」不是合法态）。剩下 6 条是**拆簇契约**（`__mro__` / `__bases__` / 类级默认 / `__module__`），不是待迁的耦合。同批认定的两个「不可迁」钉子：`test_loop_guards_split.py` 的 `obj._burn_streak` 与 `test_loop_core_tail_split.py` 的 `_zero_shard_streak` 都跑在 `object` 型探针上（`_obj()` 返回 `object`、`SimpleNamespace`），改成 `burn_view()` / 读数对象必须**新增** `# type: ignore[attr-defined]` —— 那是拿 G6 的债换 G5 的债，不做；`_revive_hygiene` 清 `_leg_abort`/`_remote_fail`/`_consec_fail` 的用例同理：那三个字段**就是**被测函数的契约。**第十三批（2026-11-19）**：hub 侧已经有的内存读数面 `mem_readout()` 补上 `peeked_n`（peek 时刻是提速缓存，撛课时得跟着清），于是 `test_hub_discover_eviction.py` 3 → **1**（`_peeked` / `_locate_cache` 改问 `peeked_n` / `locate_n`）；`test_job_identity_collision.py` 2 → **1**（`_locate_cache.get(jid) is None` 与 `course_of(jid) is None` 是同一件事的两面，并进一条）；`test_hub_job_store_split.py` 9 → **8**（`_ledger_cache[0]` 改读早就为用例准备的 `ledger_offset()`）。合计 **172 → 162**。**第十四批（2026-11-19）**：`test_loop_export_split.py` 6 → **1**（新增 `TrainingExport.ts_code_view()`：`sha256 == ""` 就是「还没建」，不另造 `built` 布尔；两条用例改用 `TrainingExport.__new__` 的真实例读这个面）、`test_remote/test_bulk_sched.py` 1 → **0**（`stats()["p1_waiting"]` 本来就是控制台与长跑告警的读数面）。合计 **162 → 156**，文件数 50 → **49**。**第十五批（2026-11-19）：远端失败面**。`test_loop_remote_split.py` 9 → **4** —— `remote_fail_state()`（`consecutive_failures` / `leg_aborted`）**本来就有**，只是用例没迁；两条「驱动私有入口」的调用也从 `assert` 表达式里提出来。留下的 4 条是跨簇方法同一性（`TrainingLoop._abort_node_failure is TrainingRemoteFail._abort_node_failure` 等）。**两处认定不可迁**：① `_node_rollout` 必须裸读 —— 读数用 `getattr(…, False)` 兑底，而 `test_node_rollout_is_initialized_at_construction` 钉的正是「**没这个属性就 AttributeError**」（h4-hurt 每小时重启一次那个 P0），换成读数就把被钉的 bug 护住了；② `_revive_hygiene` 的三格同理 —— 它是纯函数，`hasattr`/`setattr` 的**键名本身就是契约**，而且该文件有 autouse fixture 把 `loop_core.TrainingLoop` 换成 `_FlakyLoop`，真实例在那里拿不到。合计 **156 → 151**。**第十六批（2026-11-19）：全仓平扫（一次过）**。把「这个私有名是否已被某个公开方法读过」做成 AST 差分（含 `getattr(self, "_x", …)` 与私有字面键），扫出 48 条候选、逐条定性后迁了 5 条：新增 `_HubQueue.worker_registry()`（登记表**副本**；与 `_registry()` 的分工 = 快照 vs 活引用），`test_hub_queue_split` 11 → **8**（`_registry()` 读 → `worker_registry()`，`_auth_fail` 读 → `auth_state().failures`）、`test_worker_name_guard` 1 → **0**、`test_poison_freeze` 1 → **0**（`_job_dir` 拼路径 → 公开 `job_root`）。剩下 43 条候选**全部**是：拆簇同一性（`X._m is Mixin._m` / `__module__` / 类级默认）、`STATE_WRITERS`/AST **表格自检**、模块级单例状态契约、句子串/未源探测面、以及 `args._kl_cap` 这种**线协议字段名**（无类可挂读数）。合计 **151 → 146** |
| G8 `common/` 出站副作用 | **已加门禁** | `tests/test_distribution_io_budget.py`：AST 按点分调用名数 `net_http.racing_urlopen` / `urllib.request.Request` / `run_capture` / `subprocess.run` 共 4 处，按「模块 + 原语」冻结（`common.distribution|…` 2 条、`common.codehash|…` 2 条；归零即删键）；`_request` 这类内部漏斗不计。2026-11-16 随 codeHash 拆分把扫描面扩成两个模块 —— 否则「搬家」会被读成「副作用消失」，而它们仍在 L0。只剩降的方向（B8 搬去 `remote/`）。**2026-11-19（第十刀）**：升级/重启簇搬到 `remote/node_upgrade.py` 时基线**一个数没变**（簇里从来没有 IO 原语），所以新增的是「新家零 IO」断言 `test_node_upgrade_has_no_io_primitives`（不许自备出口 + 必须仍走漏斗，漏斗改名公开的 `node_request`）。**第十一刀（同日）**：任务协议 / 权重传输拆成 `common.task_client` / `common.weights_client`，两者基线**零额度**、直接加进 `L0_IO_MODULES` —— 搬家后自备一个 `urlopen` 会当场红 |
| P1-8 判活窗两份 → 一份 L0 | **已修** | 「接管后多久没进度算掉线」（900s + env 旋钮）原先只有 `hub/task_pack.py` 一份、训练侧靠 `from hub.task_pack import …` 的**函数内** import 拿它 —— 一个阈值把纯逻辑模块 `loop_control` 钉成「碰网络」。2026-11-16 下沉 `common/hold_window.py`（`HOLD_PROGRESS_STALE_SEC` / `hold_progress_stale_sec()` / env 名），hub 与训练侧同读一份。`hub/store_leases.py` 的 **BC 进度租约**窗刻意不复用（语义不同，plan §1.5.2-P0-1 禁合并）。 |
| `trainer/loop_control.py` 归属 | **已搬 `worker/`** | 它只 import `common.*`（纯逻辑），却靠 `from trainer.dispatch_queue import REPO_ROOT` 那条「为取一个路径常量」的边才算「编排」（与 `biz/batch_plan.py` 同一型）。2026-11-16 随仓根收口一起暴露 ⇒ 搬进 `worker/loop_control.py`（与 `worker/gate_halt.py` 同族：控制文件契约），顺带正过来 `worker/loop_hold → trainer.loop_control` 这条反向边；`TRAINER_ORCHESTRATION` / `remote_dag.LAYERS` / 各处指针同步。 |
| P0-1 / P0-2 / P0-3 / P0-4 / P0-7 / P0-8，P1-1 / 4 / 5 / 6 / 7 / 8 / 11 / 12 | **部分修** | 结构与契约类改造，按 §10 的 B3–B8 分批；§8 的验收条件即各批 DoD。已落地的三笔：**P0-7 的「宽泛捕获 + 只 pass」面**已由 G9 锁住增长，并有 3 批落地（日志层 `warn_once` · 退役模块 5 处 · `job_lifecycle` 6 处控制面通知 · 开算打点 3 处，现已 35 处 —— 日志层 / 退役模块 / `job_lifecycle` / 开算打点 / 行泵 / `unassign` / 心跳两处 / 补传转发 十三批，55 → 35，其余未动；**P1-11 的死代码**清掉了 `worker/train/loop_util.py` 的全部退役循环工具（该入口 2026-10-17 退役，`_emit` / `_TrainTee` / `heartbeat` / `auto_export_corpus` / `parse_val_loss_from_output` 全仓零调用，文件 321 → 109 行）。P1-11 的转发名清了两批（2026-11-16：`worker/ppo/engine.py` 11 → 1、`trainer/` 四处 12 → 0，见 §P1-11 第四/五批；`noqa: F401` 18 → 6 → **0**（2026-11-19 全仓归零，见 G6 行）），其余（兼容壳 / 其他转发名）与 P0-1 / P0-8 等仍在；**P1-4 的第一步**（`warn_unused_ignores=true` + `platform="linux"`，清掉 111 条无用 ignore，`type: ignore` 271/98 → 163/68）已落地；`Any` 那半边**已加门禁 G14**（1178/181 冻结，只减不增）+ 实测否决「批量换 `object`」。**2026-11-25**：本地新建容器「只改不读」做成常驻门禁 G20（`tests/test_write_only_containers.py`，生产面 4805 函数 / 0 处，§341）；三刀回归扫（`offline_tasks` / `run_slot` / `evaluate`）对拆分前 `24b1b799` 全字段差分全同（161 场景·640 组合 / 12 场景 / 10370 用例，§342）；**P0-1（2026-11-28）**：四刀已落（`IterWorkspace` / `BaselineEval` / `BundleExporter` / `Dispatcher`，MRO 21 → 17），剩余簇的迁移顺序 / 状态面 / 跨簇调用见下一节「P0-1 剩余簇」 |
| `remote/offline_boot.py` 3554 行单文件（§0.4-12 / §7.6 / §10-B8） | **已落地（生成式拆分）** | 发行物改**生成物**：源码拆在 `remote/_offline_boot/*.pyfrag` **12 段**（按顶层块边界切，最大 441 行），`tools/build_offline_boot.py` 逐字节拼回 `remote/offline_boot.py`（6 行「不要手改」文件头 + 12 行分段头 + 段间 11 个空行 ⇒ 与拆分前 **+29 行，其余逐字节相同**）。为什么分片是 `.pyfrag` 而不是 `.py`：生产树的静态门禁（print / 宽捕获 / `Any` / 函数形状 / LOC / 层级账本 / 抑制符）都按 `rglob("*.py")` 扫**发行物**，分片若叫 `.py` 同一行字会被数两遍 ⇒ 要么抬基线（本仓禁）、要么给每个门禁加排除名单（= 让门禁看不见代码）；`.pyfrag` 让「被检查的东西」与「被运输的东西」是同一个文件。守卫 `tests/remote/test_offline_boot_is_generated.py`（提交物 == `build()` · 分片目录无表外分片 / 无 `.py` · 分段头编号与位置一致 · 每段以换行收尾），四向证伪实测红（手改生成物 / 加表外分片 / 加 `.py` / 分段头漂号）。LOC 豁免**改成有条件的**：`test_python_loc_budget.py` 新增逐分片 LOC 判据（同一个 `loc_of`；实测往 `12_entry.pyfrag` 塞 1000 行 ⇒ 红）—— 「拆不开」已不再是理由，只剩「运输契约要单文件」。`test_module_doc_budget`（53 行）与 G3/G4/G14 读数**全部不动**（扫描面仍是发行物那一个文件） |
| P1-6 并发（没有生命周期管理） | **已修（`Runtime` + G16 · 时钟 + G17 · 裸线程 + G18：就地建池 0 处，裸 `Thread(` 剩 9 处 / 4 文件（冻结））** | 全仓 10 处就地建池（生产包 9 + `tools/` 1）→ **0 处**：新增 `common/runtime.py`（L0，stdlib-only）的 `Runtime`，做成 `ThreadPoolExecutor` 的**同形替身**（`with Runtime(scope=…, workers=N) as ex:` 后 `ex.submit/map` 照旧，转发默认池 `"main"`）—— 单一所有者（`close()` 一次收完，`__exit__` 调它）· 线程名 `<scope>-<pool>`（scope 必填；G15 只管 `Thread(`，池这一半在这里）· 取消点 `request_stop()` / `check()` / `sleep()` / `submit|map` 拒新活（**已开跑的不打断** —— Python 线程杀不掉）· `describe()` 第一次让「开了几个线程、超没超」可读。**三条有意不做**：不静默节流（`workers=N` 是调用方的决定，上限只约束隐式请求并只报数）· 不读 env（上限走参数，G13 不抬）· 不替调用方接 `halt_event`（6 模块的 halt 语义要真跑；`stop=` 已吃 `Event`）。新增**零容忍门禁 G16** `tests/test_executor_ownership.py`（生产包只许 `common/runtime.py` 构造池 + `Runtime(` 必须带 `scope=` + 迁移处数下限），迁移前实测红（9 违规 / 0 使用）、迁移后绿。基线两笔逐笔记账：SOURCE 1159→1160 · 同名散落 110→113（协议名 + `describe`；exact 14 / clone 17 未动）。`iter_rollout` 的 LOC 仍 999。**第二刀（时钟）**：`Runtime(clock=, monotonic=)` 两个零参可调用（墙钟只做时间戳 / 单调钟只做时长，缺省就是 `time` 那两个），`describe()["clock"]` 报可调用名；首个迁移点 `post_weights_parallel` 的 4 处计时走 `ex.monotonic()`，并因形状门禁（>7 参数）把 `log`/`on_alive`/`monotonic` 收进 `PushHooks`（签名 9→7）；存量 482 处 / 92 文件冻结进 `tests/golden/wall_clock_baseline.json`（只许调小，`common/runtime.py` 与引导期单文件豁免），新门禁 **G17** `tests/test_wall_clock_budget.py`（冻结 + 只降 + 扫描自检 + seam 点分调用 ≥4 的两向护栏） |
| P0-8 测试耦合（第 3/4/5 项） | **已修（2026-11-19）** | `check()`/`FAILS` 脚本壳 → 真 `assert`（9 文件 / 345 处；conftest 的 `_no_silent_check_failures` 与 5 条自证用例退役，`tests/test_conftest_hooks.py` 只留两条 hook 契约）；根 conftest 不再改写 stdlib `BaseServer.serve_forever`（66 处改用 `tests/helpers/http_server.py::serve_fast`，G6 的 conftest `type: ignore` 随之清零）；新增 **G19** 冻结「monkeypatch 打私有名」（392 处 / 95 文件，只减不增；2026-11-21 端口收编 −14）。端口样板已扩到训练机侧：`common.proc.Toolchain`（`TOOLCHAIN` 端口 + `stub_toolchain`）收编三家 `bun_version` 共 6 处**公开名** patch（G19 不动；裸名契约随改，文本断言 1535→1536 / 1224→1226；§260）。**仍开放**：全量 monkeypatch（4 160 处）与私有属性赋值（429 处）迁移，要先铺端口对象（2026-11-23 实测：含 `monkeypatch` 的行 4,630、
`monkeypatch.setattr/delattr(` 2,082 —— 两处口径都比原文宽，趋势仍在涨） |
| B7 壳收敛（S1/S3/S2，2026-11-19） | **已修** | `queue_discover` 并入 `queue_scope`；5 条 `_job_or_404` 私有断言提出 `assert`；`QueueScope` 切成协作对象 + 组合类 26 个同名转发壳 ⇒ `_HubQueue` bases **9 → 7**、MRO **14 → 12**，hub 私有断言 **25 → 19**。锁对象/顺序与 `_solo` 借表语义逐字不变；`queue*.py` 4 035 → 4 207（+172 全是壳，七个域文件本身 −38）。见 `docs/nn/engineering.md` §225 |
| B7 前半：`hub/http_face.py` 的 55 支 `elif` 路由 + 五份进程级类属性状态 | **已修（路由表 + `HubState`）** | ① 两个端点面各 30 支顺序 `elif` → **数据表** `GET_ROUTES`(30) / `POST_ROUTES`(28) + 纯函数 `match_route`；`do_GET` / `do_POST` **68/72 → 18/18 行**。表存**方法名**（在实例之前就存在，import 即可断言），9 个带参端点各一个零参包装方法 ⇒ 表里没有闭包、没有 `(*args)` 间接面。新守卫 `tests/hub/test_hub_route_table.py`（方法存在 / `(形态,参数)` 不重复 / 逐行代表路径只命中自己 / AST 零路径比较 / 三族参数绑定从**公开入口**验 / 404 与 `ProtocolError⇒400` 两条边界）；`test_admin_mem` 的接线判据从「AST 找 `Compare`」改成「问表」（旧写法钉的是形状）。**刻意不冻结路由条数**（加端点不是欠债）。② 五份可变类属性（`_blocked_logged` / `_seen_worker_sources` / `_worker_source_warned` / `_worker_pulse` / **`_REJECT_WARN_AT`**）收进 `HubState`（`state: HubState = HubState()`），写点变成 `self.state.…`，用例隔离改成整份换新；第五份是新守卫抓到的（附录行只点了四份）。守卫 `tests/hub/test_hub_state.py`（类体不许再有可变容器类属性 / 两个 `HubState` 互不共享 / 跨请求实例看到同一份）。文件 LOC 572 → 564 → **567**。见 `docs/nn/engineering.md` §217/§218 |
| P1-7 RNG 建在全局 `np.random` 上 | **已修** | 新 `worker/ppo/rng.py::PPORng` 持三条显式流（`chunk`/`epoch`/`demo`，派生自 `common/runtime.py` 的 `substream(seed, name)` = blake2b·128bit·stdlib-only）；ckpt 改存 `state.json["rng_streams"]`（每条流的 `bit_generator.state`），旧 ckpt ⇒ `restore()` False + 响亮 WARN；`chunk_episodes(shuffle=True)` 缺 rng ⇒ `ValueError`；四条训练线 + `remote/train_core`（种子改完整 hex）+ `trainer/stream` 显式传；AST 零容忍门禁 `tests/worker/test_ppo_rng_streams.py`。有意代价：PCG64 ≠ MT19937 ⇒ 与历史轨迹数值断代 |

---

### 门禁读数复核（2026-11-23）

> 上表的「证据」列里散落着各条门禁的存量数；它们会随每一刀调小，抄在正文里必然过期。
> 下面是一次全量对账（数值 = `tests/golden/*.json` 的当前和，测试就是判据）：

| 门禁 | 正文里写的 | 2026-11-23 实测 | 备注 |
|------|-----------|-----------------|------|
| G3 生产包 `print` | 123 / 38 文件 | **123 / 38** | 与基线一致（门禁只许调小） |
| G4 宽泛捕获 | 339 / 137 | **352 / 141** | 期间新增的边界逐笔记在测试表头 |
| G9 宽捕 + 只 `pass` | 35 / 28 | **26 / 19** | 十三批清理的净额 |
| G13 env 直读 | 60 / 30 | **68 / 38** | 新读者来自后续合入，逐笔抬过格 |
| G14 `Any` 注解 | 1,178 / 181 | **1,211 / 185** | 同上（`README` 说的「只减」指新增代码不得再加） |
| G7 函数级 `assert` | 62 | **60 / 32** | 模块级全生产面仍为 0 |
| G17 墙钟直读 | 482 / 92 | **521 / 101** | 同上，随合入上涨后重新冻结 |
| G18 裸 `Thread(` | 「50 → 0」 | **9 / 4** | 首次冻结是 48 / 35（非 50、非 0），见 §P1-6 |
| G19 私有名 monkeypatch | 392 / 95 | **392 / 95** | 与基线一致 |
| G5 测试私有断言 | 172 / 50 | **166 / 54** | 批次清理的净额 |
| G6 `# type: ignore` | 163 / 68 | **166 / 68** | 同 G13/G14，随合入小幅上涨 |
| G6 `# noqa: F401` | 0 | **0** | 全仓归零（2026-11-19） |
| §9 模块 docstring > 50 行 | 13 → 现 12 文件 | **11 文件** | `common/game_watch.py` 之后又降一个 |
| §9 文本形态断言 | 1,536 | **1,536** | 与基线一致 |
| P0-2 / P0-3 形状 | 45 个 > 200 行、124 个 > 8 参 | **0 / 0** | 两张基线已清空（§P0-2 复核） |

**读法**：`= 基线` 的行说明「正文数与门禁一致」；`> 基线` 的行不是回归 —— 是门禁在**新合入的代码**上
重新冻结过（每次抬格都在对应测试文件头有日期与理由）。真正**过期**的是「50 → 0」那条：G18 的基线
从第一天起就是 48 处 / 35 文件，三批迁移后是 9 / 4 —— 它从来没有清零过。`§3`/`§4` 正文里的 2026-09 读数按本节开头声明保留为「修复前基线」。

### 2026-11-27 复核（审查后的一手读数）

> 口径同上一节；数字取 `tests/golden/*.json` 与当前树实测。这里只记「相对 11-23 变了什么 / 还剩什么」，不改旧表。

- G14 `Any` **1,207 / 184**（11-23 为 1,211 / 185）；G17 墙钟 **521 / 102**；G6 `type: ignore` **166 / 68**。
- P1-1 `sys.path` **444 文件**（11-23 为 439）；函数内 import 596 未动。
- P0-8 含 `monkeypatch` 的行 **4,763**（11-23 为 4,630，仍在涨）；G19 审查时 **392 / 95**（本轮后 384 / 91）；私有属性赋值 **429**、`from x import _name` **71**。
- 形状：>200 行 / >8 参 **0 / 0**；>150 行只剩 6 个，全是 `tools/` 一次性脚本与 `worker/scripts/eval_intent_m5.py`。§P0-2 的「`bc_paddle.run_epochs` 200 行、推荐下一刀」**已过期**：当前 AST 57 行，`train` 141 行。

**仍开放的真欠账（按重要性 × 复杂度）**：

1. **P0-8 全量 monkeypatch / 私有属性赋值 / 私有 import**：G19 只冻结「打私有名」（2026-11-28 已到 **352 / 83**，见下方增量节）；全量 4.7k 行含 `monkeypatch` 的面仍无闸，端口对象先行。
2. **P0-1 `TrainingLoop`**：4 个直接基类、约 12 个间接类；再拆要像 B7 一样把编排状态搬成协作者，风险高于 `_HubQueue`。
3. **P1-1 导入体系 / src layout**：444 个 `sys.path` 文件、596 处函数内 import；editable 安装已可用，但不能一次性删。
4. **P1-4 `Any` / mypy 非 strict**：1,207 / 184 处；批量换 `object` 已实测否决，只能按数据边界做 `TypedDict` / dataclass。
5. **P0-2 / P0-3 门禁收紧**：现在 200 行 / 8 参，目标是 120 行 / CC>40 / 7 参；CC 至今没有测量口径。
6. **B5 可选项 + P1-5**：`cfg` / `course` schema、`load_dist_config` 裸 dict、notebook 侧 26 处裸 CFG。
7. **P0-6 标准 logging / 日志级别**：`print` 123 / 38 已裁定冻结；除非出现级别/结构化日志需求，否则不动。

**不算欠账**：P0-5 清单 15/15 清零；P0-4 manifest/result/action 已收；P1-2/3/6/7/8/9/10 已修；B1/B3/B4/B6/B7/B8 已落；P1-11 活转发名是契约、P1-12 没有未完成改造；G3/G4/G5/G6/G9/G13/G14/G17/G18/G19 是冻结存量（G18 是 9/4，不是 0）。

**本轮已落（P0-8 首刀）**：hub 单包端口样板 —— `hub/seams.py::NET` + `task_pack` 的 `net=` 注入 + `OfflinePushSpec.client_mod` 构造注入；hub 自有私有 patch 6 处清零，顺带把共享夹具里 2 处（`tests/common/test_offline_task_queue.py`、`tests/test_production_isolation.py`）也迁到端口。G19 **392 / 95 → 384 / 91**；跨包的 `_request` / `_wire_add` 5 处留给 remote 包。

**2026-11-28 增量（P0-8 第二代，`common/ports.py::WIRE`）**：上一行的「5 处」只是 hub 用例里看得见的一角 ——
按 G19 实测是 **80 处 / 28 个测试文件**，分两批：批一 `job_lifecycle` / `bc_job` 32 处
（`_request` 23 · `_wire_add` 4 · `bc_job._request` 4，余 1 处同族跨包 patch），批二
`hub_http` / `push_client` 48 处（28 + 10 + e2e 10）。`WirePort`（`request` / `add` 两项能力）+
`PortPair` 组装壳（宿主自拼缺省实现，不抬秩；落 **`common/`** —— `hub_http` 自己是 L0，
端口若住 `remote/` 会撞「内部边严格向下」）落地后：G19 **384 / 91 → 304 / 71**，
两条证伪（宿主直调 / `WIRE` 提前绑定）各红一次，批一 250 + 批二 158 条用例全绿。
**批三（同日）**：`remote/http._request` 21 处 + `download._get_with_retry` 3 处 ——
`_attempt_once` 是唯一发请求点（一处 `WIRE.request` 收住整条重试/重抽），`download` 另立
`FetchPair`（「拿到整份字节」与「发一次」不是同一个协议）。G19 **304 / 71 → 280 / 69**
（累计 384 / 91 → 280 / 69）。**批四（同日）**：`notebook_runtime` 26 处收进 `RuntimeProbes`（`backend` / `cuda` / `paddle` /
`hub_open` 四字段 + `PROBES`），G19 **280 / 69 → 254 / 68**（累计 384 / 91 → 254 / 68）。
**批五（同日）**：`remote/rollout_node` 52 处（`test_rollout_node` 49 + `test_tailscale_boot_bun` 3）
收进模块自带的 `NodePorts`（17 字段 frozen dataclass，`NODE` 是唯一装配行；调用点一律
`NODE.x(...)`），另 3 条打的是公开包装 `ensure_bun`，不计 G19 但随同一端口一起迁。
G19 **254 / 68 → 202 / 66**（累计 384 / 91 → 202 / 66，−182 次 / −25 文件，两个文件归零）。
**批六（同日）**：`remote/offline_boot`（生成物；改 `remote/_offline_boot/*.pyfrag` 后重建）24 处
收进 `BootPorts`（NamedTuple，5 字段：两个延迟 loader / `post_json` / `run_batch` / `explicit_leases`；
为什么不是 dataclass 见 §392），`BOOT` 装配住 `12_entry`。G19 **178 / 64**（累计 384 / 91 → 178 / 64，
−206 次 / −27 文件）；G14 +2（两个 loader 必须是 `Any`，已在基线记明）。
**批七（同日）**：`remote/rollout_relay` 9 处（预检的五个量网络步骤）收进 `RelayPorts`；
`_route_open` 与 `RelayLink._post_reply` / `_worker` 有意不收（实例/线程入口，见 §393）。
G19 **169 / 63**（累计 384 / 91 → 169 / 63，−215 次 / −28 文件）。
**批八（同日）**：`worker/ppo/common` 8 处（`_xla_fetch_cat` / `_xla_fetch_cpu`）收进 `D2HPorts`
（`is_xla` 一并迁，否则判据仍看真实设备）；G19 **161 / 62**（累计 384 / 91 → 161 / 62，
−223 次 / −29 文件）。
**下一个已知口径**：`TrainingLoop._*` 15 处（与 P0-1 同批 —— 它们是**类方法**，需要的是协作者/
公开读数，而不是模块级端口），然后按 G19 存量降序（`test_finish_course_drains_eval` 11 ·
`test_ppo_backend_switch` 9 · `test_offline_bc_course` / `test_loop_park` 7 · …）。
全文 → `docs/nn/engineering.md` §389 / §391 / §392 / §393 / §394。

**P0-1 第一刀（同日，混入 → 协作对象）**：`trainer/loop_iter_dir.py::TrainingIterDir`（`RoundSteps`
第三个基类，2 方法）→ `IterWorkspace` 对象 + `IterWorkspaceHost` Protocol。状态归对象
（`_zero_shard_streak` 搬出组合根 `__init__`）、宿主读面全走 property（`traj_dir` / `course_fp` /
`corpus_fp` / `state_init` / `node_rollout` / `extra_wver`）、组合根只留两个**一行**转发壳
（`_prepare_iter_dir` / `_check_quota_incident`），轮内调用点与 patch 点不动。`TrainingLoop.__mro__`
**21 → 20**，`RoundSteps.__bases__` 四件套 → 三件套；`loop_iter_dir` 的 `Any` **7 → 1**；
G5 基线该文件 5 → 3、`test_remote_iter` 1 → 0。守卫从 `TrainingLoop.X is <簇>.X` 换成 AST 形状判据
（壳恰好一行、`__init__` 恰好一次装配、宿主读面 == 协议声明、对象不调宿主方法），**没放宽**。
nn 门禁 **7110 passed / 4 skipped**。全文 → `docs/nn/engineering.md` §395。
**P0-1 第二刀（同日）**：同一形状再走一遍 —— `trainer/loop_baseline.py::TrainingBaseline`
→ `BaselineEval` + `BaselineHost`（`args` / `bun` / `traj_dir` / `eval_every`），
`_thread` / `_landed_wver` 归对象，组合根两个壳（`_maybe_dispatch_baseline_eval` /
`baseline_eval_state`）；`weights` 不留壳，用例改问对象。MRO **20 → 19**，
`RoundSteps.__bases__` = `(TrainingVolume, TrainingDispatch)`；`loop_baseline` 的 `Any` 6 → 2；
G5 `test_loop_core_tail_split` 3 → 2；同名散落 147 → 148（`traj_dir` 两个 Protocol + 宿主 getter）。
守卫的协作对象表扩成 `COLLABORATORS` 字典，两刀一视同仁，并新增「归对象的状态不得在组合根
再出现一份」。
**P0-1 第三刀（同日，产物出包）**：`trainer/loop_export.py::TrainingExport` → `BundleExporter` +
`BundleHost`（`args` / `bun` / `traj_root` / `jsonl_path` / `rotate_seed` + 公开入口 `remote_ppo`）；
四个 TS/replay 缓存槽归对象，远端两簇改问 `ts_code_view()`。`remote_ppo` 是**宿主公开入口**
（一行转发 `_remote_ppo`，调用时解析 ⇒ 既有 patch 不失效），协作对象不伸手进 `_remote_ppo`。
MRO **19 → 18**，`TrainingSteps.__bases__` = `(TrainingRemote, TrainingEval)`；`loop_export` 的
`Any` 11 → 2（两个 `log` 改 `Callable`），`loop_remote_push` 3 → 2；同名散落 148 → 149
（`traj_root` 的协议声明）；口径 B 1276 → 1278（结构判据）。
**P0-1 第四刀（同日，派发与让位）**：`trainer/loop_dispatch.py::TrainingDispatch` → `Dispatcher` +
`DispatchHost` + `RolloutOutcome`。这一簇**无自有状态**（六个轮级槽被别的簇读写，`_eval_at_set` /
`_eb_window` / `_eb_thread` 由 Lifecycle 建）⇒ 对象只算，宿主经 `land_rollout(outcome)` 一次落位；
宿主新增 9 个活读数（全 `cast` 直透）。MRO **18 → 17**，`RoundSteps.__bases__` = `(TrainingVolume,)`。
**顺序契约变强了**：真实现胜出从「MRO 下标靠前」变成「组合根类体里的一行壳」——
`TrainingLoop` 永远是 MRO 第一项，`TrainingEval` 的 `raise` 占位不可能反过来胜出；守卫改钉
`"_eval_on_round" in TrainingLoop.__dict__` + 真转发。`Any` `loop_dispatch` 25 → 13；
同名散落 149 → 153（`course_fp` / `corpus_fp` / `eval_every` / `jsonl_path`：同一组宿主接口）；
口径 A 1537 → 1539。
**P0-1 剩余簇（2026-11-28，四刀之后；`docs/nn/engineering.md` §395 为每刀细节）**。
排序判据 = 自有状态占比高、入边少的先做；每一刀仍是「对象拥有自己的状态 / 宿主读面走
Protocol property / 组合根留一行壳 / 写面收成具名落位方法」。入边与跨簇调用是 2026-11-28 的 AST 实测。

| # | 簇（文件 / 方法数） | 外部入边 | 自有状态面 | 跨簇调用与共享槽 | 前置条件 |
|---|---|---|---|---|---|
| 1 | ~~`TrainingVolume`（`loop_volume.py` / 20）~~ ✅ **第五刀已落（2026-11-28）** | 4 处（全在 `loop_round_steps`） | `_volume_*` 10 格 + `_greedy_*` 4 格 —— 已归 `VolumePlanner` | 外簇只经 `volume_view()` / `greedy_view()` 读、经 `reset_greedy` / `reset_ordinary_seeds` / `merge_greedy` 写；纯波次算术切到 `worker/volume_wave_plan.py` | 已完成：`VolumeHost` 16 个读面 + `land_collect` / `commit_journal` 写面；MRO 17 → 16，`RoundSteps.__bases__` = `("object",)` |
| 2 | `TrainingSteps`（`loop_steps.py` / 9） | 15 处（Job 8 · RoundSteps 6 · Volume 1） | 迭代账本 `_agg` / `_chunks_n` / `_total_steps` / `_ppo_sec` / `_kl_cum` / `_hr_*` | 被 Volume/Remote/Steps 读；自身写 `_eval_thread`（与 Eval 共享） | ✅ 前置步已落：`IterLedgerView` + `land_iter_ledger` 唯一写面（`_eval_thread` 留在 Eval） |
| 3 | `TrainingEval`（`loop_eval.py` / 17） | 7 处（lifecycle 3 · round_steps 3 · gate 1） | `_eval_*` 10 格 | 读 `_eval_at_set` / `_eval_every` / `_traj_dir` / `_jsonl_path`；`_eval_thread` 被 Steps/Job 读写 | 占位 `_eval_on_round` 已由组合根壳压住，可随簇平移 |
| 4 | `TrainingRemoteFail`（4） | 7 处（全在 Drive） | `_leg_abort` | 只写 `_leg_abort`（Drive/gate/core/round_steps 读） | 无（叶子，远程腿热身刀） |
| 5 | `TrainingRemotePush`（3） | 2 处（Job） | 无 | 读 `sess` / `exporter.ts_code_view()` | 先定 `sess` 所有权 |
| 6 | `TrainingRemoteJob`（20） | 6 处（Drive 5 · core 1） | `_wire` / `_code_sha256` / `_code_zip_path` / `_published_mem` / `_demo_raw` / `_bundle_index` | 借 `_commit_journal` / `_evalboard_idle` / `_forensics` / `_per_stage_quota`；写 `_agg` / `_collect_child` / `_greedy_stats` / `_ppo_sec` | 统一 `sess`；`_collect_child` 需宿主 getter/setter |
| 7 | `TrainingRemoteDrive`（6） | 2 处（round_steps） | `_remote_fail` / `_rollout_sec` / `_node_rollout_sec` / `_redo_err_note` | 调 Fail 7 · Job 5；写 `_report` / `_leg_abort` | 排在 4–6 之后（远程腿组合点） |
| 8 | `TrainingGuards*`（Trip 2 / Leg 8 / Gate 4 / Sweep 1） | 各 1–2 处（round_steps） | `_tripped` / `_kl_streak` / `_ent_*` / `_burn_*` / `_pair_kill_streak` / `_stop_cause` / `_stop_detail` / `_soft_remediate_count` / `_gate_notes_logged` / `_min_train_warned` / `_cloud_halted` / `_leg_abort` | 借 `_ledger_apply` / `_sync_cloud_halt` / `_eval_on_round` / `_is_soft_verdict` | 先四叶，最后 `TrainingGuards`（入边 22 处 / 6 文件） |
| 9 | `TrainingLifecycle`（10） | 6 处（bc_loop 3 · remote_job 1 · round_steps 2） | 声明 **40 槽、37 个跨簇共享**（`_traj_dir` 8 模块读、`_jsonl_path` 12 模块读） | 借 `_drain_pending_eval` / `_sync_cloud_halt` | **最后**：它建 run、写全局槽，1–8 先把读面变成 property |

## 0. 执行摘要

### 0.1 一句话结论

这套代码的**工程纪律是同类项目里少见的强**（原子写、失败分类、确定性检查点、奖励公式白名单、门禁文化），
但它得了一种典型的「**拆分后遗症**」：为了让拆分"零行为变化"，所有拆分都停在**物理层**
（把 1400 行搬到 3 个文件），没有一次真正改变**耦合形状**——于是混入越拆越多、参数越传越长、
dict 越穿越深、全局状态越躲越多，而**测试白盒耦合**把每一处旧形状都钉成了事实上的公开 API。

一句话：**债务不在"文件太大"，在"边界不存在"。**

### 0.2 五条最要命的判断

| # | 判断 | 最硬的证据 |
|---|------|-----------|
| 1 | **混入式上帝对象**：拆分把 1 个大类变成 8 个混入拼回的同一个对象，耦合没降、可读性更差 | **审计时读数**（2026-11-12；B7 后已修，见附录 B）：`hub/queue.py:7` `_HubQueue` 继承 8 个基类（2026-11-19：**7**）；`trainer/loop_core.py:120` `TrainingLoop` 经 4 层混入组合出 ~12 个类的成员；全仓 25 个 `*Mixin` |
| 2 | **函数粒度失控**：45 个函数 > 200 行，最长的 1184 行、圈复杂度 190 | `trainer/dispatch.py:151 _run`（1184 行 / CC 190）；`remote/hub_client.py:680 publish_job`（**56 个参数**） |
| 3 | **隐式 dict 契约**：模块之间靠裸 `dict` + 魔法字符串 + `None` 传状态，类型系统完全失效 | 生产代码 4,117 处 `.get(`（2026-11-23 实测 4,057）；`e.get("a_hold")` 决定动作空间（`worker/ppo/engine.py`）；726 处 `Any` |
| 4 | **全局可变状态成灾**：14 处 `global`（2026-11-23 实测 8）、7 个 L0 模块级可变容器，**直接违反 `common/__init__.py` 自己写的契约 #4** | `common/log.py:23,26`；`common/fs.py:130`；`common/distribution.py:647`；`biz/reward_context.py:54` |
| 5 | **测试在反向设计生产代码**：混入被选中的第 3 条理由写的是"tests 直接读私有状态" | `hub/queue.py` 模块头；测试侧 3,962 处 `monkeypatch`、656 处私有属性断言、385 处私有属性赋值 |

### 0.3 分维度评级

| 维度 | 评级 | 依据 |
|------|------|------|
| 正确性/数据安全 | **A−** | 原子写、`os.replace` 重试、安装票、校验和对账都很扎实 |
| 可测试性 | **C+** | 用例极多，但 80% 是白盒；改一处内部实现要改几十个用例 |
| 可读性 | **C** | 单文件可读性高，跨文件调用链几乎不可读（混入 + 惰性 import） |
| 类型安全 | **D+** | mypy 非 strict；`Any` 泛滥；协议全靠约定 |
| 可演化性 | **D** | 56 参数函数、8 基类组合、全局单例 ⇒ 任何语义变更都要全仓改 |
| 可观测性 | **C−** | 生产包 238 处 `print`、无日志级别、无结构化字段、两套时间戳实现 |
| 性能 | **B−** | 热路径有实测优化（同步合并、chunk 对齐），但 GAE 仍是 Python 循环 |
| 文档 | **B** | 信息量巨大，但**注释承担了类型与契约的职责**——这是问题不是优点（见 §9） |

### 0.4 十二条必做项（按收益/成本排序）

1. 把 `DispatchContext` / `PublishSpec` / `TrainSpec` 三个参数对象建起来（一次消掉 100+ 参数）。
2. 把 `trainer/dispatch._run` 按已有 ①②③ 相位切成 5 个方法 + 1 个状态 dataclass。
3. 给 `common/` 立规矩：**任何模块级可变状态都不许进 L0**，`_COURSE_SINKS`/`_JSONL_WRITERS`/`_WEIGHTS_PUSHED` 全部改为显式对象并注入。
4. 用标准库 `logging` 替换 `print` + 自建 `log()` + `tag_logger` 三套；日志级别进配置。
5. 把 `Optional`/`Any` 的 dict 契约换成 `TypedDict`/`dataclass`（先做 shard、manifest、result 三类）。
6. 给测试加"**协作对象接缝**"：`monkeypatch` 只允许打**端口对象**的方法，禁止打模块私有变量。
7. 把 `publish_job` 的 56 个参数收进 `PublishSpec`（frozen dataclass），`wire` 层只认这个对象。
8. 统一 Python 版本：`.python-version`、CI、mypy、venv 四者对不齐（3.12 / 3.12 / 3.12 / 3.10）。
9. 关掉 `-x`（CI 一次只报第一个失败）；把 mypy 提到 `strict = true`，按模块白名单推进。
10. 让 `common.distribution` 只留"纯函数"，把 HTTP/git/重启副作用搬到 `remote/`。
11. 给 `engine.py`/`goal.py`/`intent.py` 的 PPO 循环抽一个 `PPOLoop` 内核，三份实现变三份配置。
12. 把 `remote/offline_boot.py`（3552 行）改成**生成物**：源码拆多文件，构建时拼成单文件发行。

---

## 1. 审计口径（可复现）

```bash
# 生产代码形状（排除 .venv / tmp / __pycache__）
cd nn-training
find common hub remote trainer worker biz tools train -name '*.py' | wc -l      # 378
cat $(find common hub remote trainer worker biz tools train -name '*.py') | wc -l # 147991

# AST 度量（函数长度 / 复杂度 / 嵌套 / 参数数 / except 分布 / 模块级容器）
# 注意：脚本住仓库根 tmp/，用 nn-training 的 venv 跑，cwd = 仓库根
cd "$(git rev-parse --show-toplevel)"
nn-training/.venv/bin/python tmp/py-audit.py     # 输出 tmp/py-audit.json

# 近似重复（12 行归一化窗口，剔除 docstring）
nn-training/.venv/bin/python tmp/py-clones.py
```

**口径说明**

* **LOC** = 物理行（与 `tests/test_python_loc_budget.py` 的"代码行"口径不同，本文标注清楚）。
* 注释 + docstring 占比 **27%**（21,110 注释行 + 44,198 docstring 行 / 241,694 代码行，全仓口径）；
  只看生产包是 **36.9%**（13,233 注释行 + 21,981 docstring 行 / 95,511 代码行）。
  这是本仓最独特的特征，也是本文 §9 批评的对象之一。
* 审计为**静态只读**：未运行 `bun run check`、未跑 pytest、未跑训练。凡涉及"行为"的结论都有
  代码或测试文件为证；凡涉及"性能"的结论只做结构性判断，**不给未实测的加速比**。

---

## 2. 规模与形状

| 目录 | 文件 | 行数 | 职责 | 主要问题 |
|------|-----:|-----:|------|---------|
| `common/` | 53 | 12,686 | L0 原语 | 契约已由 pydantic 模型兜住；`distribution.py` 582 行（1555 → 582） |
| `biz/` | 13 | 5,597 | 课程/奖励纯逻辑 | 较好；`course_spec._check_rules` 的 CC 未复核（无 CC 工具，见 §P0-2 复核） |
| `worker/` | 136 | 41,162 | 节点执行体 | `iter_rollout` 入口已拆（编排 ~20 行）；`eval_arbiter` 1,182 行 / 85 方法 |
| `trainer/` | 49 | 23,271 | 本机编排 | `dispatch._run` 已拆（编排 ~30 行）；`loop_core` 312 行 |
| `hub/` | 42 | 17,512 | hub 服务端 | 7 基类（B7）；路由已是数据表；类属性已进 `HubState` |
| `remote/` | 86 | 50,702 | 线路/云引导 | `offline_boot` 是生成物（12 分片）；`publish_job` 已收 `PublishSpec` |
| `tools/` | 45 | 16,022 | 一次性探针 | 连字符文件名、无入口约定、与生产代码混放 |
| `tests/`+`e2e/` | 673 | 187,891 | 测试 | 白盒耦合（G5/G19 冻结）、最长文件 2,343 行（`tests/remote/test_remote_iter.py`） |
| `tmp/` | — | 34,188 | 本地临时脚本（`.gitignore` 已覆盖，0 个文件入库） | 不构成仓库债，但会污染本地 lint/AST 口径 |

**生产函数粒度分布**（2026-09 读数 4,398 个函数；2026-11-23 实测 **5,301** 个）

| 指标 | 数量 |
|------|-----:|
| > 200 行 | **45** → **0** |
| > 100 行 | **161** → **182** |
| > 150 行 | 未计 → **67**（2026-11-23 起以 71 冻结为存量闸，逐刀下调；见 engineering §329 / §333） |
| 参数 > 8 个 | **124** → **0** |
| 带 `**kwargs` | 37 → 36 |
| 签名里出现 `Any` | **316**（口径未复核；G14 的另一个口径是 1,211 处 / 185 文件） |
| 完全无注解 | 65（口径未复核） |

> 2026-11-23 复核：「→」右侧是本次实测（`ast.walk` 全量、7 个生产包）。前四行是硬读数
> （>200 / >100 / >8 参 / `**kwargs`），后两行的 2026-09 口径无法复现，只标「未复核」，不编新数。
> 目录表的「文件 / 行数」两列同样是物理行全量重数（`rglob("*.py")`，含 `__pycache__` 之外的树）。

---

## 3. P0 —— 必须修（按危害排序）

### P0-1　混入组合 = 分布式上帝对象（最严重）

**现象**

```python
# hub/queue.py:7
class _HubQueue(QueueScopeMixin, QueueDiscoverMixin, QueueAuthMixin, QueueClaimsMixin,
                QueueResumeMixin, QueueObserveMixin, QueueOfflineMixin, QueueStoreFaceMixin,
                _AuthGuard): ...

# trainer/loop_core.py:120
class TrainingLoop(RoundSteps, TrainingSteps, TrainingGuards, TrainingLifecycle): ...
# 而 RoundSteps 本身 = (TrainingVolume, TrainingBaseline, TrainingIterDir, TrainingDispatch)
```

`_HubQueue` 的 8 个基类合计 **200+ 个方法**；`TrainingLoop` 的间接基类合计约 **12 个类**。
混入内部对 `self._stores` / `self._lock` / `self._halts` 的依赖**没有任何静态声明**，全靠
组合类 `__init__` 里那 44 行赋值；控件流一旦跨混入就断（IDE 跳不到定义、mypy 看不见来源）。

更糟的是，混入之间还**互相继承**：`QueueOfflineMixin(QueuePeer)`、`QueueAuthMixin(_AuthGuard, QueuePeer)`
——这已经不是 mixin，而是"用多继承手工拼一个类，再把它拆成 8 个文件"。

**为什么这是问题（对照最佳实践）**

* PEP 8 / Google Python Style：优先组合（composition）而非继承；多继承只用于**真正的接口**（ABC/Protocol）。
* 混入的合法用法是"行为正交、无共享状态"（如 `DictMixin`）。这里的状态是**强耦合**的
  （模块头自己列了 5 个 `_lock` 读者、多处跨域互调）——共享状态的混入 = 上帝对象的伪装。
* 后果是**不可局部推理**：改 `queue_claims` 必须同时读 7 个文件才能知道 `self._order` 谁在写。
  模块头承认"守卫 `test_hub_queue_split.py` 逐字段对账谁写它"——用测试代替类型系统，
  这是把编译器该干的活外包给了 2000 行测试。

**根因（必须直说）**

`hub/queue.py` 模块头把"tests 直接读私有状态（`hub._stores` / `hub._order` / …）"写成选择混入的
第三条理由。这是**测试倒逼设计**：因为用例断言了私有字段，所以不敢引入协作对象。
审计实测：测试侧 **656 处** `assert ... ._private`、**385 处**私有属性赋值、**146 处** `from ... import _x`。

**解决方案（三步，可独立合并）**

1. **先立契约，不动实现**：把 `_HubQueue` 需要的能力抽成 3 个 `Protocol`
   （`JobStorePort`、`Clock`、`WorkerRegistry`），混入改为对 `Protocol` 编程。
   这一步零行为变化，但让"谁依赖什么"第一次可被 mypy 检查。
2. **按域切出 3 个真协作对象**（不是 8 个）：`Scheduler`（选谁/轮转/优先级）、
   `LeaseBook`（租约/心跳/回收）、`OfflineBook`（离线清单/领取）。共享的 `_lock` 提升为
   `HubState` 的**单一所有者**，三个对象各拿一个 `HubState` 引用。
   混入文件保留为**转发壳**（`def claim_next(...): return self._scheduler.claim_next(...)`），
   这样 656 处私有断言先不炸。
3. **测试去白盒**：新增 `hub.seams`（已存在 `tests/helpers/hub_seams.py`）作为**唯一**测试接缝，
   把 `assert hub._stores[...]` 逐步替换为 `assert hub.seams.queue_state(...)`。
   禁止新增对 `_` 前缀属性的断言（写成门禁，见 §8）。

**验收**：`_HubQueue.__mro__` 长度 ≤ 4；`hub/queue_*.py` 的 `self._` 未声明引用为 0（用 AST 检查）；
`tests/` 新增私有断言 = 0。

---

### P0-2　巨型函数：拆分没拆到"函数"这一层

**现象**

| 函数 | 行数 | 圈复杂度 | 参数 | 备注 |
|------|-----:|---------:|-----:|------|
| `trainer/dispatch.py:151 _run` | **1184** | **190** | 0（读 `self` 15 个别名） | 开头 15 行是"参数局部别名（OO 化）" |
| `tools/paired_power.py:332 main` | 625 | 159 | 0 | 一次性工具，可接受度略高 |
| `remote/train_core.py:399 run_training_core` | 564 | 63 | 3（收进 `TrainSpec`） | |
| `worker/iter_rollout.py:942 run_iter_rollout` | 604 | 102 | 9 | |
| `worker/ppo/engine.py:274 ppo_update` | 598 | 80 | **17** | |
| `trainer/eval_dispatch.py:129 run` | 589 | 96 | 0 | |
| `trainer/dispatch.py:587 worker` | 499 | 112 | 2 | |
| `remote/hub_client.py:680 publish_job` | 468 | 84 | **56** | 全仓参数冠军 |
| `worker/cli.py:197 build_argparser` | 7 | 1 | 2 | 92 个 flag 收进模块级 `_flag_rows()` 数据表（164 行） |

生产包 **45 个函数 > 200 行**、**161 个 > 100 行**（共 4,398 个函数）。`tests/test_python_loc_budget.py` 只管**文件**
< 1000 行（且豁免了 `offline_boot.py`），**函数级完全无人管**——这就是"文件达标、函数失控"的
由来：拆文件最容易，拆函数要动参数与状态。

**2026-11-23 复核**（上面「现象」表是 2026-09 的快照，代码已经把这张表走完了）：

* **现状**：生产包 **0 个函数 > 200 行**、**0 个函数 > 8 参**（`tests/test_python_shape_budget.py` 的
  `LONG_FUNCS_BASELINE` / `WIDE_FUNCS_BASELINE` 两张表都已清空，空表保留只为「再进一个存量要写理由」）；
  > 150 行 **72** 个、> 100 行 **182** 个（共 5,301 个函数）。表里 9 行全部落地：
  `dispatch._run` 1184 → 编排 ~30、`tools/paired_power.py main` 625 → **35**，其余落点见上表右侧备注。
* **第 4 条只落了一半**：门禁是 **200 行 / 8 参**，本节要的是「>120 行 / CC>40 / 参数>7 直接红」；
  **圈复杂度至今没有测量口径**，所以「CC 190」这类读数无法被门禁复现。要收紧阈值，先补 CC 的度量
  （`radon` 之类的新依赖按 §C 记账）与按文件冻结的基线，再动线 —— 否则只是把阈值写进文档。
* **离天花板最近的三个**（按「独立所有者 + 独立触发条件」判可行性；行数为 2026-11-23 实测，含 def 与末行）：

  | 函数 | 行数 | 文件 LOC | 为什么可切 / 不可切 |
  |------|-----:|---------:|---------------------|
  | `worker/train/bc_paddle.py::_Train.run_epochs` | **200**（恰好压线） | 592/1000 | **推荐下一刀**。与 `worker/train/bc.py` 的同名函数是双生，torch 侧已于 `docs/nn/engineering.md` §252 拆成 `run_epochs`(63) + `_train_epoch`(79) + `_validate`(65)；本侧接缝逐字相同（训练内循环 / 验证 / 历史·早停·断点·回调），守卫 `tests/worker/test_bc_paddle.py`（+ `test_bc_early_stop` / `test_bc_log_stamp`）现成，LOC 有 400 行余量 |
  | `hub/queue_offline.py::offline_tasks` | 197 | 793/1000 | 可切：hub 侧离线队列的「领任务 / 发 blob / 收工」三相，但文件只剩 207 行预算，切出来要**搬模块**（同 §295 手法），不是纯方法拆分 |
  | `remote/http.py::_get_with_retry` | 190 | 512/1000 | 收益递减：重试循环与错误映射能分开，但 §265 刚把它收成 `WireGet`，再拆只是换形状、不减路径 |

* **不动的**：`tools/*.main`（`tpu-probe` 183 行 / `wave2_judge` 189 行）按本节早先的口径「一次性工具、
  可接受度略高」保留 —— 它们没有第二个消费者，拆开只会多一层只有自己用的私有函数。

**为什么这是问题**

* `_run` 的 CC=190 意味着**至少 190 条独立路径**；该函数里含 3 个相位（ping 门 / 权重下发 /
  中央队列），每个相位有自己的失败模式、超时、日志与早退。任何一条分支的回归都只能靠
  "读 1200 行"发现。
* 全仓 **1,528 个 `except` 处理器**（2026-11-23 实测 1,532 —— 规模基本没变，这一项不是靠重构能降的）里相当一部分就在这类函数内，异常路径与正常路径交织 ⇒ 覆盖率再高也测不全。
* `ppo_update` 598 行把"模型前向 / 损失组装 / 诊断 / 断点 / profiler / 日志"六件事塞进一个循环体，
  新增一个 loss 项（历史上加过 4 次：kl、kickstart、demo、value_anchor）都要改这个函数的中段。

**解决方案**

1. **`dispatch._run`**：按已有注释的 ①②③ 相位抽方法，引入 `DispatchState`（frozen dataclass，
   字段 = 现在那 15 个局部别名 + 3 个相位产物）。每个相位返回 `PhaseResult`，失败即提前 `return`。
   目标：`_run` ≤ 80 行，只做编排；`_ping_gate` / `_distribute_weights` / `_run_queue` /
   `_settle_round` 各自 ≤ 150 行、CC ≤ 25。
2. **`publish_job`**：56 个参数可大致归为三类——"本轮课程/公式/奖励调度"、"线协议固定字段"、
   "传输开关"，三者本该各自成对象。建成 `PublishSpec`（见 §7.1）后，函数签名变成 `publish_job(spec, paths)`。
   这不是美化——**56 参数无法被任何调用方正确使用**，事实上已经有 3 个打包点各自复制了一份拼装逻辑。
3. **`ppo_update`**：已拆为 `PPOContext` + `_prepare` / `_train_step` / `_record_diag` /
   `_maybe_heartbeat` / `_finish_epoch` / `_finish` / `_aggregate`，17 参入口保留为薄壳。
   `LossTerms` 注册表**未做**：各损失项共享同一批前向张量与 RNG 顺序，硬拆成对象只会多传上下文；
   新增损失项仍改 `_train_step` 中段（该段已是单一职责：一次 minibatch 的 loss 组装）。
4. **加函数级门禁**（§8）：新函数 > 120 行 / CC > 40 / 参数 > 7 直接红；存量按文件白名单冻结。

**风险**：`_run` 里有大量 `return` 与共享可变局部（`nodes`、`plan`）。切分必须"先搬后改"：
先原样搬到方法里（只传 `state`），跑一遍全量测试，再清理。这一步的**唯一**正确性标准是
`tests/trainer/test_dispatch*.py` 全绿 + 一轮真跑。

---

### P0-3　参数爆炸：上下文无处安放

**现象**

* `remote/hub_client.publish_job`：**56 个关键字参数**（468 行）。
* `remote/train_core.run_training_core`：29 个。
* `remote/train_core.py:976 _run_training_core_paddle`：28 个。
* `worker/cli.build_argparser`：650 行，把 **92 个 `add_argument`** 手写在同一个函数里。
* 生产代码 **124 个函数参数 > 8**（2026-09 读数）。**2026-11-23 实测 0** —— 本节的批表已全部落地，
  `tests/test_python_shape_budget.py` 的 `WIDE_FUNCS_BASELINE` 已是空表。
  > 2026-11-23 又收一处**贴着阈值**的：`worker/train/bc_paddle.py::run_epochs` 恰好 200 行
  > （门禁判据是 `> 200`），照 torch 双生的三道接缝拆成 57 / 56 / 73 / 22，并新增
  > `tests/worker/test_bc_epoch_seams.py` 钉「两个后端必须同形」（engineering §328）。
  > 同日起门禁再收紧一档：**> 150 行**进入存量闸（71 个 / 60 文件冻结、新增即红），
  > **> 200 行**仍是零容忍硬顶（engineering §329）—— 150~200 那档正是上面这种
  > 「贴着阈值」的函数聚集的地方。
  > 第一刀就落在这档最长的一格：`hub/queue_offline.offline_tasks` 197 → 34（+ 三段：
  > 读一门课 / 判它 / 成型一行），存量 71 → 70（engineering §330）。
  > 余下 70 个按「接缝烂度」分诊过一遍，下一批的五处已点名（engineering §331）：
  > 行数排序不是判据 —— 同样 180 行，纯函数里的四段规则远比「整个生命周期在一个
  > `while` 体内」好拆。
  > 第一批的第一格已落地：`worker/bc_dispatch.run_slot` 159 → 26（+ 六段，`_Attempt`
  > 收三个跨循环布尔），存量 70 → 69（engineering §332）。
  > 第五格也落地了：`worker/gate_check.evaluate` 171 → 64（+ 五个助手），判决改表驱动
  > （`_SPLIT_KINDS` / `_RULE_PASSES` / `_ADVANCE_CONDITIONS` / `_ADVANCE_VETOES` + frozen
  > `_AdvancePreps`）；HEAD-vs-新 9450 组矩阵差分全一致，存量 68 → 67（engineering §333）。
  > 第二格（§331 点名的「最烂一格」）也落地了：`remote/offline_deliver._sync` 162 → 41
  > （+ 八段，frozen `_PassPlan` 收两批），整面搬进 `remote/deliver_sync`(L1) —— 腿本体 LOC
  > 991/1000 撞顶，按 §5.7「先拆再收」；三个节奏常量随之下沉 `deliver_mode`(L0)。
  > HEAD-vs-新 2000 组行为差分全一致（敏感性探针 540 组红），存量 67 → 66（engineering §334）。
  > 第三格也落地了：`trainer/queue_local.rescan_nodes` 186 → 139（+ 四个判断助手
  > `_rescan_candidates` / `_rearm_room` / `_rescan_ready` / `_rescan_admit`）；语句对账 92 → 90，
  > A4 回场/上界用例全绿，存量 66 → 65（engineering §335）。
  > 第五格也落地了：`remote/hub_publish._publish_stage` 188 → 24（+ `_stage_manifest` /
  > `_stage_scaffold` / `_stage_payload_extras` / `_stage_keys`）；发布字节级 A/B 全同、`Any` 1 → 0，
  > 存量 65 → 64（engineering §336）。§334 留的死 `sent` 顺手删掉（engineering §337）。
  > 分诊五格拆完后按同一张表往下走：`remote/push_client.submit_job` 166 → 88（+ `_UploadPlan` /
  > `_plan_uploads` / `_body_for` / `_handle_428`），HEAD-vs-新 13 组决策表差分全同，存量 64 → 63
  > （engineering §338）；`rescan_nodes` 另补了 13 组假钟/假 ping 决策表差分（§335 末段）。
  > 2026-11-25：整树 AST 守卫耗时盘点（engineering §343）—— 最慢的 `python_shape`
  > 五读数接内容哈希缓存，1.44s → 0.05s；其余守卫都已是「一次 walk + 缓存」，无平方项可套。
  > 分诊第八格 ⇒ §344（`worker/eval_track.settle_eval_summary` 175 → 46 + 读数/缺口/成行/
  > 日志四段；HEAD-vs-新 300 组决策表全同，存量 61 → 60）。
  > 2026-11-25 复查：剩余 60 个里 44 格仍带 `try`/嵌套循环；排除工具 / 探针 / 生成物 /
  > 构造器后，下一批五格已点名（engineering §345）：`batch_lanes.worker` /
  > `push_dispatch._await_result` / `job_round.run` / `worker_server.make_worker_server` /
  > `greedy_round.run_greedy_round`。
  > 第九格也落地了：`trainer/batch_lanes.worker` 178 → 29 + 四段（`_lane_claim` / `_lane_fetch` /
  > `_lane_accept` / `_lane_fail` + `_LanePick`）；HEAD-vs-新 289 组决策表全同，存量 60 → 59
  > （engineering §346）。
  > 第十格也落地了：`remote/push_dispatch._await_result` 165 → 82 + 五段（`_await_stale` /
  > `_await_queue_expired` / `_await_landed` / `_await_running` / `_await_gone` + `_AwaitState`）；
  > HEAD-vs-新 96 组响应序列差分全同，存量 59 → 58（engineering §347）。
  > 第十一格也落地了：`remote/job_round._Round.run` 176 → 23 + 八段（四档失败 / 两个
  > SystemExit 出口 / 兜底上报 / `_wrap_up`）；HEAD-vs-新 27 组差分全同，存量 58 → 57
  > （engineering §348）。
  > 第十二格也落地了：`remote/worker_server.make_worker_server` 191 → 63 + `_auth_ok` /
  > `_worker_get` / `_worker_post`；19 个真 HTTP 请求差分全同，存量 57 → 56（engineering §349）。
  > 第十三格也落地了：`worker/greedy_round.run_greedy_round` 151 → 102 + `_progress_of` /
  > `_greedy_finish`；6 组差分全同，存量 56 → 55（engineering §350）。
  > 第十四格：`trainer/loop_volume._volume_continuous_loop` 170 → 90 +
  > `_plan_continuous_wave` / `_remember_first_est`；9 组差分全同，存量 55 → 54（engineering §351）。
  > 第十五格：`remote/deliver_worker.main` 162 → 103 + `_build_deliverer` /
  > `_drain_tick`；6 组假 ctl/deliverer 差分全同，存量 54 → 53（engineering §352）。

**为什么这是问题**

Python 没有"命名参数即文档"的强制力。56 个参数的调用点只能靠**位置/关键字混用**维持，一旦
新增参数，所有调用点都要想"我该不该传"。这直接导致：**同一个语义在 3 个打包点各拼一次**
（`hub-server` 重发、`smoke_loopback`、`plan_run`），拼错一个默认值就静默产出错 manifest——
`publish_job` 的注释里已经记录过两次这类事故（`wire_diet`、`reward_spec`）。

**解决方案**

* 用 **frozen dataclass** 承载"一次发布的全部输入"：`PublishSpec`。所有字段有默认值、有类型、
  有 `__post_init__` 校验；线协议序列化只写一个 `PublishSpec.to_manifest()`。
* 用 **`argparse` 分组 + `from_namespace()`** 替代 650 行手写：`parser.add_argument_group("rollout" / "ppo" / "eval")`，
  再加一个 `WorkerOptions.from_args(ns)` 集中做类型转换与互斥校验。
* 原则：**参数 > 7 个就说明缺一个对象**（这是可机器检查的规则，见 §8）。

---

### P0-4　dict 即契约：类型系统被绕过

**现象**

* 生产代码 **4,117 处 `.get(`**（2026-11-23 实测 **4,057**：这一项几乎没动，P0-4 的进度全在
  `manifest`/`result` 契约化那一侧，不在 `.get(` 的数量上）。
* `manifest`、`shard`、`result`、`metrics`、`course`、`cfg` 六类对象全是裸 `dict`。
* 魔法字符串决定**动作空间**：

```python
# worker/ppo/engine.py
out = model(obs, sc, e.get("extra"))
if e.get("a_hold") is not None:      # ← 数据事实决定头数
    mv, fr, hd, val = out
else:
    mv, fr, val = out
```

同一份代码里还有第 2、第 3 处判据（`_ref_v6 = len(_ro) >= 4`、`_data_v6 = _e.get("a_hold") is not None`），
注释明确写着"判据用 len(out) 而不是模型属性：DataParallel 包装下属性代理不可靠"。
这是**用运行时嗅探替代类型**：v6/legacy 两种动作空间的差异应该是一个 `ActionSpace` 枚举，
而不是三个隐式约定。

* `worker/ppo/engine.py` 里 `demo_bank: dict | None`、`demo_t: dict[str, Any]`、`_e: dict` 层层套娃。
* `common/manifest.py`（672 行）里定义了大量"默认值 dict"（`MANIFEST_OPTIONAL_DEFAULTS` 等），
  校验逻辑手写在多个函数里，**schema 与代码不同步没有任何检查**。

**为什么这是问题**

* 拼写错误 `e.get("a_holdd")` 不会报错，只会**静默走 legacy 分支**——这正是注释里记录的
  "y32-nohp / y32-tempo 事故"的形态。
* 任何一个字段改类型（`int` → `float`）、改可选性，都无法被 mypy 发现；只能靠 1,300 行
  `tests/worker/test_np_core.py` 之类的用例兜。
* `pydantic` 已经是依赖（`worker/config.py` 等 11 个文件在用），**却只在配置层用**，
  数据面（shard/manifest/result）一个没用。

**解决方案**

| 对象 | 建议 | 理由 |
|------|------|------|
| `manifest` / `result` | `pydantic.BaseModel`（已有依赖） | 来自网络、必须校验、要报字段级错误 |
| `shard` / `metrics` | `TypedDict`（`total=False` 分区） | 热路径，零运行时开销，只要静态检查 |
| 动作空间 | `Enum ActionSpace.LEGACY / V6` + `HeadLayout` dataclass | 消掉三处嗅探 |
| `cfg` / `course` | `pydantic`（`biz/course_spec.py` 已经在用） | 已有先例，继续推进 |

**落地顺序**：先给 `shard` 加 `TypedDict`（无运行时成本，`load_shard_fields` 的返回类型立刻有用），
再给 `manifest` 加 `pydantic` 模型 + `model_validate`，最后删掉手写校验（保留旧函数做一层薄包装）。

---

### P0-5　全局可变状态：`common/` 违反自己的契约

**现象**

`common/__init__.py` 的层契约第 4 条写得清清楚楚：

> **无模块级可变状态**（`pid_probe` 那种只读常量表除外）——只放纯函数与不可变常量。

实测违反：

| 文件 | 状态 | 后果 |
|------|------|------|
| ~~`common/log.py:23,26`~~ **已修 2026-11-13 / 收尾 2026-11-20（§234）** | ~~`_PREFIX: str`、`_COURSE_SINKS`~~ → `LogRouter`（`ContextVar` 前缀 + 实例级 sink，`router=` 可注入）；派生线程的上下文传播在 `Runtime.thread`/`submit`/`map` 补齐 | 原病：日志路由是进程级隐式状态，并发课程互相污染（现场：4 条 `it5 DONE` 里 3 条前缀是错的） |
| ~~`common/fs.py:130`~~ **已修 2026-11-13** | ~~`_JSONL_WRITERS`~~ → `JsonlLedger`（实例级写者表 + `close()`） | 原病：每条路径一个永不回收的线程；进程退出无 flush/join |
| ~~`common/weights_ledger.py:21`~~ **已修 2026-11-13** | ~~`_WEIGHTS_PUSHED`~~ → `WeightsLedger`（实例级账本，可注入） | 原病：跨轮复用判据住进程内存 ⇒ 重启即失效（不可复现） |
| ~~`common/net_http.py:93`~~ **已修（B8）** | ~~`_RACING_OPENERS`~~ → `racing_opener(no_proxy)` = `@functools.cache`（键就是那个 bool，`cache_clear()` 给用例）；`no_proxy_opener()` 同款，两个 opener 都**无状态**（`build_opener` 的结果不随环境变），故不串味、无需对象化 | 原病：手搓 `dict[bool, OpenerDirector]` |
| ~~`common/distribution.py:647`~~ **已修（2026-11-14 对象化 / 2026-11-19 B8 搬家 / 2026-11-20 §235）** | ~~`_RESTART_SEEN`~~ → `RestartMemo`（默认实例 `_RESTARTS`）；~~`UPGRADE_BRANCH`~~ 已不在 `common/`：整簇搬去 `remote/node_upgrade.py`，锁存现在是 `BranchLatch` 对象（`_BRANCH`，无 `global`） | 原病：升级分支是进程级锁存且「显式值能盖锁存」——2026-08-30 事故的载体（配置残留旧分支名 ⇒ 全节点 reset 回旧代码）；`explicit` 通道已删（签名层面不可复发） |
| `common/platform_utils.py:417` | `_UNREAPED: list[Any]`（**已修** 2026-11-16：`UnreapedProcs`） | 无界增长（收尸失败的子进程） |
| `biz/reward_context.py:54` | `_current: RewardContext` | 加载期奖励的**隐式全局**；多课程/多线程下是竞态源 |
| ~~`worker/eval_exec.py:354`~~ **已修 2026-11-20（§236）** | ~~`_EXECUTORS` 单例~~ → `Cell(EvalExecutors(), retire=…close_pools)` | 原病：`global` ×2 + 锁外惰性判断（两线程同时首次取会造出孤儿池子） |
| ~~`worker/eval_arbiter.py:1127`~~ **已修 2026-11-20（§236）** | ~~`_ARBITER` 单例~~ → `Cell(EvalArbiter(), retire=…reset_for_tests)` | 原病：换实例的 `global` 没有归属 |
| ~~`hub/http_face.py:192` 起~~ **已修 2026-11-19** | ~~`_blocked_logged` / `_seen_worker_sources` / `_worker_pulse`~~ → `HubState`（**五份**类属性：闭锁节流 / 同名来源观测 / 告警节流 / **`_REJECT_WARN_AT`（守卫抓到的第五份，原表只点了四份）** / 静默哨兵；另有对端断开计数，共六个字段），写点长成 `self.state.blocked_logged[…]`；守卫 `tests/hub/test_hub_state.py`（类体不许有可变容器类属性 + 两个 `HubState` 互不相干） | 原病：`ThreadingHTTPServer` 每请求新建 handler ⇒ 共享面只能挂类上，散成几个类属性后在调用点与实例字段长得一模一样，用例逐表 `.clear()` 漏清一张就串味 |

全仓 **11 处 `global` 语句**（2026-11-20 实测）：`biz/reward_context` ×2（裁定保留）、`hub/admin` 1、
`remote/{wire,http,payload_slim,worker,tailscale_boot}` 各 1、`tools/{task,forkdist}` 3。`common/**` 0 处。
**2026-11-23 实测 8 处**：`biz/reward_context` ×2、`common/cell` 1、`remote/{payload_slim,tailscale_boot}` 各 1、
`tools/{task,forkdist}` 3 —— `hub/admin` 与 `remote/{wire,http,worker}` 那 4 处随各自改造消失了，`common/**` 也不再是 0。
（原文记的 14 处含 `worker/eval_exec` ×2 与 `worker/eval_arbiter` ×1，§236 之后归零。）

> **2026-11-20 复核**：上表**整张已清零**——log / fs / weights_ledger（2026-11-13 对象化 +
> 注入契约；2026-11-20 §234 补齐派生线程的上下文传播）、net_http 的两个 opener（B8 换成
> `functools.cache`）、distribution 的升级/重启簇（2026-11-19 搬去 `remote/node_upgrade.py`，
> 锁存 2026-11-20 §235 对象化成 `BranchLatch`）。守卫是
> `tests/test_python_shape_budget.py::test_common_has_no_new_mutable_module_state`
> （`L0_MUTABLE_ALLOWED = {}`，即 `common/**` 现已零模块级可变容器）+ `GLOBAL_BASELINE` 收掉
> `remote/node_upgrade.py`（`UPGRADE_BRANCH` 的 `global`），L2 的两个单例（`_EXECUTORS` /
> `_ARBITER`）也收进 `common.cell.Cell`（§236）。`hub/http_face` 的几个类属性
> 2026-11-19 已收进 `HubState`（五份，含原表漏掉的 `_REJECT_WARN_AT`；守卫 `tests/hub/test_hub_state.py`。
> 本轮只是把这一行的状态补正，没动代码），
> 按裁定保留的只剩 `biz/reward_context._current`（每 iter 整体替换的加载期上下文 + 2 条
> `global`，见该文件头）。**`common/**` 之外仍有同类存量**，见下一小节——它们不在本表的
> P0-5 口径内（G2 只管辖 `common/**`），清单是 2026-11-20 盘点的结果，动手前先读。

#### P0-5 之外的同类存量（2026-11-20 盘点，**未动手**）

P0-5 的判据只覆盖 `common/**`（G2：`L0_MUTABLE_ALLOWED = {}`），而「模块级可变状态」在 L2/L3
一样存在。下面这份清单是 AST 盘点的结果（模块级 / 非 dataclass 类体的可变容器字面量与
`dict()`/`list()`/`set()` 构造，排除 `__all__` 与全大写查表常量，再逐个确认有写入点）：

| 文件 | 名字 | 形态 |
|------|------|------|
| ~~`remote/control_pool.py:44`~~ **已修 2026-11-21（§238）** | ~~`_CONTROL_CONNS`~~ → `ControlPool`（默认实例 `_POOL`；三档关闭时机：失败即作废 `close()` / 整池 `close()` / 不挂 `atexit`） | 隧道连接池 —— 本轮唯一动手的一处（连接是有 fd 的资源，旧形状答不出「谁负责关」） |
| ~~`remote/rollout_relay.py:1087`~~ **已修 2026-11-21（§239）** | ~~`_LIVE_LINKS`~~ → `LiveLinks`（`add`/`discard`/`snapshot`/`count`；`discard` 幂等，`stop_stale_links` 锁外调 `stop()`） | 活着的 relay link 列表 —— 旧写法双重注销会 `ValueError`，且用例只能替换模块变量 |
| ~~`remote/push_client.py:44`~~ **已修 2026-11-21（§244）** | ~~`_CACHE_HITS`~~ → `ShaCacheHits`（`record`/`known`/`forget`；两条翻面理由写进类头，上限仍是整片清空） | 推送命中缓存 |
| ~~`remote/http.py:341`~~ **已修 2026-11-21（§242）** | ~~`_POLL_WARN_AT`~~ → `WarnThrottle`（`allow(key, now)` / `forget_all`；窗口是构造参数，`WARN_THROTTLE_SEC=60` 是缺省）；同文件的懒建单例 `_opener`（`global`）一并收成 `ProxyOpeners`（`get()` 懒建、`reset()` 重建） | 轮询告警节流表 + 代理 opener |
| ~~`remote/worker.py:473`~~ **已修 2026-11-21（§248）** | ~~`_ACTIVE_CODE_SHA`（`global`）~~ → `CodeSession`（`pin` 只钉一次、重复钉抛 `RuntimeError`；`active` 唯一读面） | 当前代码 sha 的锁存 |
| ~~`hub/admin.py:138`~~ **已修 2026-11-21（§246）** | ~~`_PROBE_BLOCK`（`global`）~~ → `ProbeBlock`（`bytes()` 惰性建一次；不用 `Cell` 是因为它不惰性） | net-probe 填充块 |
| ~~`trainer/loop_transport.py:159,229`~~ **已修 2026-11-21（§240）** | ~~两个 `*_NOTED` 集合~~ → `WarnOnce`（`mark()` 查与加同一个动作） | 一次性告警集合 |
| ~~`trainer/queue_local.py:322,323`~~ **已修 2026-11-21（§249）** | ~~`_REUSE_INDEX` / `_REUSE_SIG`~~ → `ReuseIndex`（`rows()` 里签名与内容一起换，`clear_rows()` 清场） | rollout 源复用索引（签名变了才重建） |
| ~~`worker/cmd.py:11`~~ **已修 2026-11-21（§245）** | ~~`_CODE_SHA_MEMO`~~ → `@functools.cache`（记忆变成函数属性，清场 `cache_clear()`；不用 `Cell` 是因为它刻意不惰性构造） | code-hash 记忆 |
| ~~`worker/resume.py:92`~~ **已修 2026-11-21（§241）** | ~~`_SCAN_CACHE`~~ → `ScanCache`（`lookup`/`remember`/`forget_all`；两条失效时机写进类头：签名变 / 满则整表清空） | resume 扫描缓存 |
| ~~`worker/node_identity.py:40`~~ **已修 2026-11-21（§247）** | ~~`_LEDGER`~~ → `BootLedger`（`pin`/`pinned_boot`/`clear_rounds`；钉一次=一条 `if`，容量成构造参数） | 节点身份账本 |
| ~~`worker/ppo/np_core.py:448`~~ **已修 2026-11-21（§250）** | ~~`_XLA_CACHE_STATE`~~ → `CompileCacheState`（`dir` / `mark_enabled` / `clear_dir`；失败不记账） | XLA 编译缓存状态探针 |
| ~~`worker/ppo/np_core.py:287`~~ **已修 2026-11-21（§250）** | ~~`_SPEED_PROBE`~~ → `SpeedProbeCache`（`cached`/`store`/`clear_secs`；`0.0` 是合法读数） | 吞吐探针 |
| ~~`worker/gate_halt.py:270`~~ **已修 2026-11-21（§240）** | ~~`_WARNED`~~ → `WarnOnce`（`seen()` 先查、文件存在才 `mark()`，不存在的路径不烧 key） | warn-once 集合 |
| ~~`remote/wire.py:218`~~ **已修 2026-11-21（§243）** | ~~`_BEST_RATE` / `_stale_samples`~~ → `RateReference`（`sample`/`level`/`floor`/`clear_samples`；迟滞与衰减是构造参数，清场一次清两个）；`remote/tailscale_boot.py` 那份**不可动**（独立引导模块，零依赖约束） | 测速参照速率 |

**进度**：`remote/control_pool`（§238）· `remote/rollout_relay`（§239）· 三处 warn-once 集合（§240）·
resume 扫描缓存（§241）· `remote/http` 的两份（§242）· `remote/wire` 的参照速率（§243）· `remote/push_client` 的命中记忆（§244）· `worker/cmd` 的 code-hash 记忆（§245）· `hub/admin` 的填充块（§246）· `worker/node_identity` 的账本（§247）· `remote/worker` 的会话指纹（§248）· `trainer/queue_local` 的复用索引（§249）· `worker/ppo/np_core` 的两份探针状态（§250）已收（2026-11-21）。

**P0-5「模块级可变状态 → 显式对象」清单已清空（15/15）**。复跑审计若仍有新条目，按同一配方做：
先答「谁拥有 / 何时清 / 跨进程怎么复现」三问，再选形状（对象 / `@functools.cache` / 保持独立引导模块豁免），
并把棘轮（`GLOBAL_BASELINE` / `OWNER_*` 清单 / `L0_MUTABLE_ALLOWED`）同向收紧——不收就等于留在账上。

#### 2026-11-21 复核（清单清零后的一次全树重扫）

**口径**（AST，生产包 `common/biz/worker/remote/hub/trainer/tools`）：模块级 `Assign`/`AnnAssign`
的值是可变的**容器**（`{}`/`[]`/`set()` 字面量与推导式，以及 `dict()`/`list()`/`set()`/
`defaultdict()`/`deque()`/`Counter()`/`OrderedDict()` 构造），去掉 `__all__` 等 dunder、全大写查表
常量、以及**没有任何写入点**的只读表（下标赋值 / 变更方法 / rebind 都不命中）；另查
① 类体里的可变容器类属性（`ThreadingHTTPServer` handler 那族）· ② 全部 `global` 语句 ·
③ 全大写常量的**反向检查**（大写 ≠ 永久只读：凡大写容器**有**写入点的都要单列）。
复跑方式：`bash tools/githook/nn-py-safe.sh tools/audit/py_audit.py` 的 `mod_globals` 段 + 上述三条过滤。

**结果**

* 上面 15 项**零残留**（15/15 全部已成对象 / `functools.cache`）；
* `global` 语句 **7 处 / 5 文件**，与 `GLOBAL_BASELINE` 一条不差：`biz/reward_context` ×2（裁定保留）、
  `remote/payload_slim`、`remote/tailscale_boot`（独立引导）各 1、`tools/{forkdist:2,task:1}`；
* `common/**` 的可写容器 **0**（`L0_MUTABLE_ALLOWED` 仍空）；全大写查表常量里**有写入点**的 **0 条**；
* 类体可变容器 16 条：15 条是 **pydantic `BaseModel` 字段默认值**（`enemy_spawns: list[Spawn] = []` 那族，
  pydantic 逐实例深拷贝，**不是**共享状态）+ 1 条 `tools/console_window._ProcessEntry32._fields_`（ctypes 约定名）。

**清单之外仍有 2 簇（2026-11-20 盘点漏掉，2026-11-21 复核补上；同日同批收掉，见 §251）**

| 文件 | 名字 | 形态 | 为什么算（写入点 / 归属问题） |
|------|------|------|------|
| `hub/task_pack.py:221,224,232` | `_TASK_PACK_TRIGGERS` / `_TASK_PACK_MISS_TRIGGERS` / `_TASK_AUTO_HANDOFF_TRIGGERS` | 三本 `dict[str, dict[...]]` 触发账（节流窗 + 失败原因 + 计数上界） | **两个模块都在写**：`hub/task_pack.py` 与 `hub/offline.py`（`setdefault`）；清场靠 `reset_auto_handoff_triggers()` 单点；`hub/server.py` 还把三个名字当门面再导出，用例直接读 `hub_server._TASK_PACK_TRIGGERS`——正是 P0-5 要消的「同一份状态散在模块作用域、谁都能写」。~~收法：`PackTriggers`~~ **已修（§251）**：`PackTriggers`（`stale_gate` / `miss_gate` / `note_failure` / `failure` / `poll_window` / `decision` / `note_trigger` / `clear_*` / `snapshot`，锁在对象里；两条重导腿共用一个私有 `_leg_gate`），门面不再 re-export 可变状态 |
| `remote/deliver_outbox.py:93` | `_GATES` | `dict[str, dict[str, Any]]`：每个 outbox 一把进程内 `RLock` + 持有深度 + **文件 fd**（跨进程 flock） | 写在同文件但**持有 fd 与线程锁**：旧形状答不出「谁负责关」；另有独立的 `_GATES_LOCK` 保护建表。~~收法：`OutboxGates`（`gate(path)` 懒建、`close_all()` 关 fd）~~ **已修（§251）**：`OutboxGates` + `@dataclass _Gate`，fd 只在临界区里存在（`leave()` 即放）⇒ 不需要 `close_all()` |

**不算的（附理由，免得下一轮再翻一遍）**：`remote/wire._WIRE`（每 job 传输账，已在
`tests/remote/test_worker_state_contract.py` 的 `OWNER_CONTAINERS` 钉住且有封顶用例）·
`remote/tailscale_boot._ORIG_PROXY_ENV`（独立引导模块，`STANDALONE_BOOT_EXEMPT`，零依赖约束下不可动）·
`biz/reward_builtin._REGISTRY`（**import 期**装饰器注册表，运行期零写入；若将来要运行期注册再对象化）·
`tools/{audit/py_audit,audit/py_clones,backtest-burn-rule}`（CLI 脚本，模块顶层即 `main`）·
`tools/forkdist._ITEMS`/`_REPORTS`（pytest 插件状态，已在 `GLOBAL_BASELINE` 的同一文件里）。


**为什么这是问题**

* 违反本仓自己写下的契约（比违反通用最佳实践更严重）。
* 单例 + 全局使**并发正确性不可证明**：`_PREFIX` 是线程共享的字符串，`prefix_scope` 在
  eval 的后台线程里读到的就是"别的课程最后写的值"（注释已经踩过）。
* 可测试性靠 `reset_for_tests()` 这种后门维持，测试顺序开始影响结果。
* `_JSONL_WRITERS` 的写者线程是 `daemon=True` 且**没有任何 shutdown/atexit 钩子**：
  进程被 kill 时，队列里尚未写出的行直接丢失（调用方也没拿到返回），而没有任何地方
  能对这批"在飞"的写入做收尾/告警。

**解决方案**

| 状态 | 替换 |
|------|------|
| 日志前缀/镜像 | `contextvars.ContextVar`（异步/线程天然隔离）+ 一个显式 `LogRouter` 对象，由组合根构造 |
| JSONL 写者 | 显式 `JsonlLedger(path)` 上下文管理器，配 `atexit`/`close()`；或直接用 `logging` + `QueueHandler` |
| 权重账本 / 重启去重 | 落盘（已有 jsonl 基础设施）或挂到 `HubState` 对象上，不驻模块 |
| 奖励上下文 | 作为**显式参数**传入 `load_episodes(...)`；保留模块级函数做一层薄包装并标 `DeprecationWarning` |
| eval 单例 | 由 worker 的 `Runtime` 对象持有，`run_iter_rollout(runtime, ...)` 收参 |

**验收（可写成测试）**：AST 扫描 `common/**`，模块级非 `Final` 的 `dict/list/set` 赋值 = 0；
`global` 语句只允许出现在 `common/distribution.py` 的过渡白名单里（该文件本身在 §5.1 要拆走）。

---

### P0-6　日志体系分裂：`print` + 两套 `log()` + 一句假声明

**现象**

* 生产包 **238 处 `print(`**（`hub/boot.py` 21、`hub/store_offline.py` 12、`hub/http_face.py` 8…）。
* 两套"唯一实现"：

```
common/log.py       log() / say() / format_line()   ← 自带 time.strftime('%H:%M:%S')
common/logutil.py   stamp() / log_line() / tag_logger()  ← 也自带 STAMP_FMT
```

`common/logutil.py:13` 写着：

> `biz/log.py::format_line` 也改用这里的 `stamp()`，两边的时间戳因此不可能再漂移。

**但 `biz/log.py` 不存在**（`biz/` 下只有 12 个模块，没有 `log.py`），真正存在的是
`common/log.py::format_line`，它第 32 行**直接调 `time.strftime`**，没有用 `stamp()`。
所以那句"不可能再漂移"是假的——两套时间戳**现在就各写各的**。

* `common/log.py::log` 里同一段格式化写了两遍（第 56 行与第 62 行），只差一个 `course` 参数。
* 没有日志级别、没有 logger 名、没有结构化字段；所有"抓日志"的脚本都在对中文句子做正则。

**为什么这是问题（不只是风格）**

* 无法按模块降噪（`hub-server` 的高频访问行是**手写 if 规则**在做"静默"，
  `hub/http_face.py::log_message` 里那串 `'"GET /jobs/peek' in line` 就是没有 level 的代价）。
* 无法把日志接到文件/聚合器（stdout 是唯一出口，`Tee` 是自制的）。
* 时间戳漂移会让跨进程对时失败——而这个仓库**大量依赖日志对时**做事故复盘。
* `print` 无 flush 参数的地方在管道下会攒块，注释里已经为此加过 `flush=True` 的补丁。

**解决方案**

1. 建 `common/logging.py`：一个 `configure(level, sink, json=False)`，内部用标准 `logging`
   （`RotatingFileHandler` / `StreamHandler`），统一 `%(asctime)s %(levelname)s [%(course)s] %(name)s %(message)s`。
2. `log()` 保留为薄包装（`logging.getLogger("nn.trainer").info(...)`），**删掉 `format_line` 的第二份实现**。
3. `print` 按优先级替换：`hub/` 与 `trainer/` 先做（它们是运行时长最长的进程），`tools/` 一次性脚本可留。
4. `course` 用 `LoggerAdapter` 或 `contextvars` 注入，不用全局 `_PREFIX`。
5. 高频访问行用 `logger.debug`，不再手写 if 白名单。

---

### P0-7　异常吞噬：错误分类做得很好，捕获面却太宽

**现象**

* 生产包 **444 处 `except Exception`**、**36 处 `except BaseException`**、
  **70 处 `except ...: pass`**（完全静默；以上为 2026-09 粗扫，含 `tests/`/`tools/`）。
  > 2026-11-23 复核（门禁口径：7 个生产包，`except Exception` / `BaseException` / 裸 `except`）：
  > G4 **352 处 / 141 文件**、G9（宽捕且体内只有 `pass`）**26 处 / 19 文件**，两张基线都只许调小。
* 分类本身是**优点**：`common/errors.py` 的 6 个异常各自对应一个处置分支，注释把
  "为什么必须独立"讲得比多数开源项目清楚。问题是分类只在**少数关键路径**上被使用，
  其余地方一律 `except Exception` 兜底。

典型：

```python
# conftest.py 末尾
    except Exception:
        pass
```

```python
# common/log.py
def _mirror(sink, line):
    try:
        sink.write(...)
    except Exception:
        pass          # 「日志不得反向杀死训练」——合理，但应 logger.exception 留痕
```

```python
# common/fs.py::append_jsonl 的写者
            except Exception as e:   # 写失败回给调用方（`_record_with_retry` 靠它重试）
                fut.set_exception(e)
```

**为什么这是问题**

* `except Exception` 不捕 `KeyboardInterrupt`/`SystemExit`（这是对的），但它**会捕 `MemoryError`**，
  而且把 `ProtocolError`/`RetryableError` 与真正的编码 bug 混成同一个分支 ⇒ 重试策略被抹平
  （本仓花了大量注释在"确定性失败 vs 瞬时失败"上，这里又把它合回去了）。
* 70 处 `pass` 让"失败"变成"没发生"：`job_lifecycle` 6 处、`train/loop_util` 5 处、
  `forensics` 5 处——全是诊断路径。
* `conftest.py` 的 3 层 `try/except` 意味着**清理失败永不可见**。

**解决方案**

1. 立规矩（门禁）：`except Exception` 只允许在**进程边界**（顶层 `main`、线程入口、HTTP handler）
   出现，且必须 `logger.exception` + 计数 + 返回明确的失败码。
2. 内部路径只捕**具体异常**（本仓已有 `ProtocolError` / `RetryableError` / `OSError` / `ValueError` 足够用）。
3. `pass` 一律改为 `logger.debug("...", exc_info=True)`（保留"不影响主流程"语义，但留痕）。
4. 用 ruff 的 `BLE001`（blind-except）做机器检查，存量按文件白名单冻结。

---

### P0-8　测试反噬设计：白盒耦合已经成了架构约束

**现象**

| 指标 | 数量 |
|------|-----:|
| `monkeypatch` 出现 | **3,962** |
| `assert ... ._private` | **656** |
| 测试里给私有属性赋值 | **385** |
| `from ... import _name` | **146** |
| 测试文件 | 499（155,612 行） |
| 单个测试文件最长 | 2,254 行（`tests/remote/test_remote_iter.py`） |

`conftest.py` 还在**全局改写标准库**：

```python
_socketserver.BaseServer.serve_forever = _serve_forever_fast_poll  # type: ignore[method-assign]
```

以及用 `dict` 模块级状态（`_TEST_TMP_DIRS`、`_TEST_BAD`）管理临时目录。

**为什么这是问题**

* 3,962 次 `monkeypatch` 意味着**几乎没有依赖注入**：所有协作方都是模块级函数/常量，
  测试只能"改模块里的名字"。这正是 §P0-1/P0-5 的成因：为了不改测试，实现只能保持模块级形状。
  Python 的最佳实践是**构造时注入**（`class Service(repo, clock, client)`），
  `monkeypatch` 只用于第三方边界（`time`、`os.environ`）。
* 656 处私有断言把实现细节变成事实 API；重构一次要改几百个用例，于是"重构"变成
  "搬文件但保持形状"——S 系列 23 刀就是被这条约束逼成"混入"的。
* 全局 patch 标准库会影响**同进程所有测试**（xdist 下每个 worker 一份，问题被掩盖），
  且让"关服慢"这种生产问题在测试里永远不可复现。
* `check()`/`FAILS` legacy harness 还有 **11 个文件**在用，靠 conftest 的 autouse fixture 兜底
  才不至于静默绿——这是"测试框架被发明了两次"。

**解决方案（按顺序）**

1. **先建接缝，再改实现**：每个包给一个 `seams.py`（`hub`/`remote`/`trainer` 已有雏形），
   暴露 `clock()` / `now()` / `client()` / `executor()` 这类工厂；测试只 patch 接缝。
2. **私有断言迁移表**：用 AST 扫出全部 `\._[a-z]\w*` 断言，按"等价公开查询"分类，
   能换的换（`hub._stores[x].claimable_job_ids()` → `hub.seams.claimable(x)`），
   换不了的标记 `# noqa: whitebox` 冻结存量。
3. **删 `check()` harness**：11 个文件改成普通 `assert`，conftest 的 `_no_silent_check_failures`
   随之退役（少一个全局 hook）。
4. **禁止新增**：门禁规则——`tests/**` 新增 `monkeypatch.setattr(module, "私有名")` 直接红。
5. `conftest.py` 的 `serve_forever` patch：改为在 `tests/helpers/http_server.py` 里提供
   `serve_fast()` 包装，用例显式使用；不再改标准库。

**注意**：这一条不产生任何用户可见收益，但它是**其他所有重构的前置条件**。
如果不做，第 4 轮的"再拆一次"还会以混入告终。

---

## 4. P1 —— 应该修

### P1-1　导入体系：439 个文件在做 `sys.path` 手术（2026-11-23 实测；2026-09 为 409）

**现象**

* **409 个文件**含 `sys.path.insert/append`（生产 36、`tests/` 317、`e2e/` 18、`tools/` 36，余下为根入口）。
  典型形态：

```python
# worker/models/core.py:30-38
import importlib.util as _ilu
if _ilu.find_spec("common") is None:
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))
```

* `pyproject.toml` 自己写着（2026-09-02）："此前 `py-modules=[]` 使 `pip install -e .` 装不进任何东西，
  各模块只能靠 sys.path hack 互相可见。现在声明包与顶层模块，安装后可任意目录 import。"
  **但 hack 一个没删**。这就是"配置已修、代码未清"的典型。
* **596 处函数内 import**（`trainer/loop_volume.py` 22、`loop_serve.py` 20…），
  全仓只有 **8 个文件**用 `TYPE_CHECKING`。
* 顶层包名是 `common` / `worker` / `tools` / `train` / `hub` —— 全是**极易与 PyPI 包或标准库
  撞名**的通用词（`worker` 是 Celery 的关键概念；`tools` 到处都有；
  `worker/cmd.py` 会遮蔽标准库 `cmd`，注释已记录过一次"torch 的 `import pdb` 当场炸"）。

**解决方案**

1. 一次性删除 409 处 hack：editable 安装已经保证可导入；测试入口统一由
   `tests/conftest.py` 在**一处**插入根路径（现在每个测试文件插一次）。
2. 项目采用 **src layout**：`nn-training/src/battle_nn/{common,worker,hub,remote,trainer,biz}`，
   顶层只留 `pyproject.toml`/`tests`/`tools`。这一步能同时消灭撞名与"cwd 依赖"。
3. 循环依赖用 `TYPE_CHECKING` + `Protocol` 解决，而不是函数内 import（后者把 ImportError
   推迟到运行时，且 IDE/mypy 都变弱）。
4. `tools/` 里带连字符的一次性脚本（`spatial-probe.py` 等 6 个）移到 `tools/oneoff/`，
   用 `python -m` 入口或 `[project.scripts]` 注册，别再豁免 N999。

### P1-2　版本矩阵四者不一致

| 位置 | 值 |
|------|-----|
| `.python-version` | `3.12` |
| `pyproject.toml` `requires-python` | `>=3.10,<3.14` |
| `pyproject.toml` `[tool.mypy] python_version` | `3.12` |
| 实际 venv | **Python 3.10.21** |
| CI matrix | `ubuntu-latest` + `windows-latest`，**只在 3.12 上跑** |

**后果**：本机 3.10 上跑的训练，被 3.12 的 mypy 检查；`>=3.10` 的承诺只被一个 minor 验证过。
`fs.extract_tar_bytes` 专门写了 `<3.12` 分支（说明真的要在 3.10 上跑），但 CI 永远走不到那支。

**方案**：要么把 `requires-python` 收紧到 `>=3.12`（推荐，云端 Colab/Kaggle 都 ≥3.10 但可控），
要么 CI 加 3.10 一档。**不能两边都留着**。

### P1-3　lint / type 的豁免清单在保护旧债

```toml
ignore = ["E402",   # ← 保护 sys.path hack
          "RUF012", # ← 可变类变量（模型类，确有理由）
          "SIM115", # ← 文件不用上下文管理器（真 bug 类）
          "B023",   # ← 闭包捕获循环变量（真 bug 类）
          ...]
```

* `B023` 被整类关掉：注释说"safe in immediately-called callbacks"——但**没有任何机制**
  保证下一个回调仍是 immediately-called。这是把一类潜在 bug 变成"靠 code review 记性"。
* `SIM115` 关掉 = 允许 `open()` 不关；`common/log.py` 的 `open_course_sink` 正是靠它。
* `mypy`：`disallow_untyped_defs = false`、`disallow_incomplete_defs = false`、
  `warn_unused_ignores = false`、无 `strict`。

**方案**：`B023`/`SIM115` 改回 `select`，存量按 `per-file-ignores` 精确冻结（不是全局关）；
mypy 先开 `disallow_untyped_defs`（存量 65 个）与 `warn_unused_ignores`（43 个 ignore 要复查），
再按模块白名单推进 `strict`。

### P1-4　类型化不足

* `Any` 注解 **726 处**（2026-11-16 复测 **1176 处 / 182 文件**，含 `dict[str, Any]` 236 处）；
  **316 个函数签名**含 `Any`；65 个函数完全无注解。
* 典型滥用：到处可见的 `dict[str, Any]`（219 处）、`def __getattr__(name: str) -> Any`
  （`remote/offline_boot.py:203`，用模块级 `__getattr__` 转发到另一个模块——**PEP 562 的反面教材**：
  静态分析完全失效，IDE 跳转不可能）。
* `type: ignore` 43 处，且 `warn_unused_ignores=false` ⇒ 这些 ignore 里有多少已经无用，没人知道。

**方案**：先开 `warn_unused_ignores`，删掉一批；再把 `Any` 换成 `object`（惰性）或具体协议。
`offline_boot.__getattr__` 的转发改成显式 `from remote.offline_deliverable import ...` 的薄包装函数
（或构建期拼接，见 P1-8）。

**2026-11-16：「`Any` → `object` 一把梭」实测**（这条否决有数字）：生产面 78 个文件的
**236 处** `dict[str, Any]` 批量改成 `dict[str, object]`，mypy 当场 **406 个红点 / 93 文件**；
退到「单文件 ≤2 处」的 50 个小文件再试，仍有 **102 个红点 / 29 文件**，逐步回滚到还剩 42 个时，
剩下的红点全在**调用方**（`object` 不可下标、`dict[str, T]` 的值型不变式挡住 `dict[str, 具体]`）。
结论：这些 dict 缺的不是「更宽的注」，而是**字段表** —— 该写 `TypedDict` / frozen dataclass，
只读面用 `Mapping[str, object]`。所以「换 object」不是可批量执行的机械改动，收益只能按
数据边界一个个拿。

**2026-11-16：先落门禁 G14**（存量冻结 `1176 处 / 182 文件`，只减不增；`Any as A` 改名绕过
单独拦）。理由：别的类型债都有闸（`type: ignore` 走 G6、函数形状走 G1），唯独最能长的那一种
没有 —— 清得慢可以接受，**不许再长**是当下最便宜的一步。

**2026-11-16：第一个数据边界落地 —— `demo_bank` 有了字段表**（`worker/ppo/demo.py::DemoBank`）。
审计 §P1-4 / §0.2-3 点名的 `demo_bank: dict | None` 链现在有五个具名键（`obs` / `scalars` /
`extra` / `actions` / `masks`），四处消费面（`remote/train_common.decode_demo_bank` /
`check_demo_bank`、`remote/train_core._CoreRun`、`worker/ppo/engine.py` 的 `PPOContext` 与
`ppo_update`、`engine_paddle.ppo_update`）统一到它。**安全阀的名单从字段表派生**
（`DEMO_BANK_FIELDS = frozenset(DemoBank.__required_keys__)`），名单不再有两份。
它防的不是 `KeyError`，是 `engine_paddle` 里 `if "extra" in demo_bank` 那种形状 —— 缺字段被
静默跳过、BC 项少一半而日志一切正常；现在缺 `extra` 的老 bank 由 `check_demo_bank` 响亮拒收
（`tests/remote/test_demo_bank_contract.py` 三条：字段表 == 导出端名单 / 安全阀走同一份 /
缺 `extra` 拒收）。G14 的计数**没动**（这里的债是裸 `dict`，不是 `Any`）—— 下降要一处处搬，
这一处先证明「怎么搬」。
**副产品（类型化的第一天就还本）**：把字段表写到 `engine.py` 时抓到一处真实分叉 ——
bank 的 `extra` 只有 Paddle 侧进模型，torch 侧 `_setup_demo` 从来没物化它；对
`policy_extra=True` 的模型这意味着 demo 段前向拿 `extra=None`，而模型拒绝静默用 0
（`ValueError`），legacy 模型则**看不出差别**（「两侧输入不同但都绿」）。先红后修：
`tests/worker/test_ppo_demo_mix.py::test_demo_bc_feeds_the_bank_extra_to_a_policy_extra_model`
（旧实现 ValueError，修复后 `demo_bc > 0`）。记录见 `docs/nn/training-stack.md` §77。

**2026-11-14 第一步（已落地）**：`warn_unused_ignores` 由 false 翻成 **true**，清掉它当场指出的
**111 条无用 `type: ignore`（54 个文件）**，G6 的 `type: ignore` 存量 271/98 → **163/68**。三条
实操教训：

1. **「无用」是按平台说的**。`os.sysconf` / `os.fork` / `ctypes.windll` 一类符号在 Linux stubs 里
   根本不存在 ⇒ `# type: ignore[attr-defined]` 是**必需**；在 Windows stubs 里它们存在 ⇒ 同一条
   注释被判**多余**。只翻 `warn_unused_ignores` 会让仓库在两台机器上只能有一台绿，所以同一次改动里
   钉了 `platform = "linux"`（= 门禁所跑的那套 stubs），Windows 开发机得到同一份判定。
2. **多码 ignore 会逼着二选一**。`# type: ignore[attr-defined, assignment]`（`test_pid_probe_windows_safe`）
   在 Linux 只有前者触发 —— 开着 false 时多余的那半是「无害噪音」，开着 true 时它当场红，迫使注释
   只留真实成立的那一个码。
3. **mypy 删掉的注释里有「删了就红」的**。`from google.colab import files # type: ignore[import-not-found,
   import-untyped]`：本机装了包 ⇒ 前者多余，但整条注释被当成 unused 报出来时人容易整条删掉，而
   `import-untyped` 仍在。删完必须**再跑一遍 mypy**（不看 unused 报告就收工会退化成 3 条新红）。

剩下 163 条是真在压错误的；`Any`（复测 1176 处 / 182 文件）→ 字段表/协议那半边：门禁 G14
已锁住增长，逐边界的下降才刚开始（「批量换 `object`」已实测否决，见上方 P1-4）。

### P1-5　配置：68 处环境变量直读 + 裸 `dict` CFG（2026-11-23 实测；2026-09 为 130）

* `os.getenv/os.environ.get` 生产代码 **130 处**（2026-09 宽口径）。门禁 G13 的口径（生产包内
  `os.getenv` / `os.environ.get` / `os.environ[...]` 直读）2026-11-23 实测 **68 处 / 38 文件**，散落在各模块顶层与函数内。
* `CFG[...]` 这种 notebook 侧的裸 dict 26 处；`rl-config.json` 有 JSON Schema，但读侧仍是 `cfg.get("policy", {})`。
* 没有 `Settings` 层：同一个变量（`NN_GATE_NPROC`、`PPO_XLA_DIAG`、`PPO_PROFILE_STEPS`…）
  在多个文件里各读一次、各给默认值。

**方案**：`common/settings.py` 用一个 `pydantic-settings` 风格（或手写 frozen dataclass + `from_env()`）
集中声明；模块只收 `Settings` 对象，不直接读 `os.environ`。测试注入 `Settings(...)` 而不是 `monkeypatch.setenv`。

**2026-11-16 第一批（已落地）：`common/settings.py` + G13。**
先量了一遍（AST 数 `os.getenv` / `os.environ.get` / `os.environ[...]` 三种形态，动态键也计）：生产面
**94 处 / 44 个变量**，其中**被 >1 个模块读的只有 5 个**，真正「同一根旋钮两个默认值」的活例只有两个 ——
所以「130 处 → 一个 `Settings` 对象」不是这批该做的事：那是给 44 个旋钮加一层转发面。

- `common/settings.py`（L0）挑走**多读者**的两个：`EVALBOARD_DATA`（`trainer/batch_store` 的台账与
  `worker/eval_heartbeat` 的心跳各解一次；`worker/` 不能 import `trainer/` ⇒ 真家只能落 L0）与
  `BATTLE_HUB_TOKEN`（`remote/run_loop` 的 CLI 续跑与 `remote/deliver_worker` 的补传腿各抄一遍
  `(os.environ.get(...) or "").strip()`）。形态按 `common/hold_window` 的先例：**常量 + 调用时读 env 的
  纯函数**，函数收 `env=` 映射（测试传字典即可，不必 `monkeypatch.setenv`，并行用例不互踩）。
- 新增 **G13** `tests/test_env_reads_budget.py`：按文件冻结直读次数（**63 处 / 32 文件**，只许调小）+ 一条
  「单家」判据（`settings.MIGRATED_ENV` 里的旋钮在别处出现**字面读法**即红）。豁免
  `STANDALONE_BOOT_MODULES` 那四个从 raw 单独拉取的引导文件（它们不许 import `common.*`）。
- 两向证伪：把 `EVALBOARD_DATA` 的旧读法放回 `worker/eval_heartbeat` ⇒ 增长 + 单家两条同时红；
  基线抬高一格 ⇒「只许调小」红。

### P1-6　并发：裸 `Thread(` 9 处、无生命周期管理（2026-11-23 实测；2026-09 为 54）

* `threading.Thread(` **54 处**（2026-09），绝大多数 `daemon=True`；线程池出现在 trainer/worker 多处。
  > 2026-11-23 复核：`Runtime`（G16）落地后**就地建池 0 处**；裸 `Thread(` 仍有 **9 处 / 4 文件**
  > （`remote/rollout_relay.py` 6 · `common/http_pool.py` 1 · `hub/http_face.py` 1 · `trainer/queue_local.py` 1），
  > 按 G18 冻结、只许调小（基线最初就是 48 处 / 35 文件，不是 50，也从未清零）。
* 没有统一的 shutdown 语义：`_JsonlWriter` 靠 30s 空闲自退；`executors` 靠 `reset_for_tests()`；
  `hub` 的后台线程靠 `halt_event`。
* 多个线程池各自 `ThreadPoolExecutor(...)` 就地创建（`dispatch.py` 里 `min(8, len(cfg_nodes))`），
  没有全局上限 ⇒ 超订只能靠环境变量封 BLAS 线程（`conftest.py` 那一大段就是在补这个）。

**方案**：一个 `Runtime` 对象持有全部 executor / threads / stop event，`with Runtime() as rt:` 保证收尾；
热路径共享一个有界池；后台任务用 `concurrent.futures` 而不是裸 `Thread`；需要取消时用
`Event` + 显式检查点（已有 `halt_event` 雏形）。

**2026-11-16 第一刀（名字先有，`Runtime` 后说）**：55 处 `Thread(` 里已有 45 处带 `name=`，
剩下 10 处正好在最需要现场读数的几条腿上（撤单 / 派发 / 节点监管 / 在飞采样 / 流式收集）——
没有名字时 `faulthandler` / `py-spy` / `top -H` 只给 `Thread-7`，「某个线程卡在 `queue.get`」
就等于没有信息。10 处补齐（`batch-worker:<node>-<i>` / `batch-supervise:<node>` / `batch-watch` /
`queue-local:<node>` / `queue-worker:<node>` / `stream-collector` / `push-bootstrap` / `smoke-hub-server` /
`tunnel-ab-hub`），并加 **G15**（零容忍、无基线）钉住「新建线程必须带名字」。
`trainer/batch_runner.py` 本来顶在 1000 行 LOC 预算上，所以那三处顺手收进一个
`_spawn_thread(target, name, *args)` 小助手 —— 净行数比改之前还少（1000 → 997）。
**没动**的是 `Runtime` 本身（executor 生命周期 / 有界共享池 / `Event` 取消点），
那是 B6 的体量，见 §10。

**2026-11-17 第二刀（已落地）：`Runtime` + G16（线程池的唯一所有者）。** 量下来全仓 10 处就地建池（生产包 9 + `tools/` 1）
（`common/distribution` ×2 · `remote/{offline_eval,payload_pack,rollout_node}` ·
`trainer/{dispatch,queue_local}` · `worker/{eval_replays_once,iter_rollout}` · `tools/e2a_readout`）
同时缺四件事：**所有者**（池的生死绑在词法作用域上，提前 `return` / 异常 / `break` 就让「在飞的活」
与「池」分家）· **上限**（`min(8, …)` 两处、`len(items)` 一处、`n_slots` 一处叠加，无人报数）·
**取消点**（`halt_event` 是 6 个模块的事实约定，却到不了池边）· **线程名**（10 处只有 1 处写
`thread_name_prefix`，`py-spy` 里全是 `ThreadPoolExecutor-3_0`）。

落法：`common/runtime.py` 的 `Runtime` **做成 `ThreadPoolExecutor` 的同形替身** ——
`with Runtime(scope=…, workers=N) as ex:` 之后 `ex.submit/map` 照旧（转发默认池 `"main"`），
所以 10 处替换是**等行数、等语义**的（`iter_rollout` 的 LOC 仍是 999；写成 `as rt` +
`rt.pool(…)` 会顶到 1000 被 LOC 门禁拦下 —— 这也是选「同形替身」而不是「包一层”的理由之一）。
正常退出 `shutdown(wait=True)` 且**不**取消排队活（提前 `return` 不该丢掉已投出去的局），
**只有** `stop` 已置位时才 `cancel_futures=True`。

三条**有意不做**（写进模块 docstring，免得下一个当成隐藏的债）：① **不节流** —— `workers=N` 是
调用方（`--workers` / 课程配置）的决定，静默砍并发是拿吞吐换好看的数字，而上限只约束隐式请求
（`workers=None`）与 `describe()["over"]` 的报数；② **不读 env** —— 上限走 `Runtime(ceiling=)`
参数，G13 的直读计数不抬，「同一根旋钮两个默认值」不再种一遍；③ **不替调用方接 `halt_event`**
—— 6 个模块的 halt 语义一起改需要一轮真跑，`stop=` 已吃 `threading.Event`，接线是调用方一行。
B6 的 **rng 半边**（§P1-7：`PPORng` 三条流 + `rng_streams` 进 ckpt）与 **clock 半边**
（§121：`Runtime(clock=, monotonic=)` 两个可注入读数 + `PushHooks` + G17 墙钟预算）都已落（2026-11-17）。

**2026-11-17 第三刀（已落地）：裸线程收进 `Runtime.thread()` + G18（存量闸）。** 池那一半结清之后
裸 `threading.Thread(...)` 还剩 **50 处 / 35 文件**（G18 口径；`grep` 的 51 含所有者自己那处）——
G15 只判「有没有 `name=`」，不判「归属于谁」，「没人 join」这条从来没被管过。落法：
`rt.thread(target, *args, name=, kwargs=, daemon=)` 起线程即登记 —— 名字统一 `<scope>-<name>`、
`describe()["threads"]` 报名字→还活不活、`join_threads()` 返回仍活着的名字、`close(wait=True)` 一并 join；
**`start()` 已代调**（本仓 50 处没有一处是「先造后等」），**不替 target 包 `check()`**（往别人线程体里
塞可能抛异常的取消点会改写各家自己的退出语义），取消点仍由当事人调 `rt.check()` / `rt.sleep()`。
首批迁移选接缝已经在那儿的两个：`remote/offline_eval.CloudEvalRunner`（持 `Runtime("cloud-eval-runner")`，
线程名 `cloud-eval-runner-loop`，`drain` 的 join 语义不动）与 `trainer/queue_local.LocalLane`
（`worker-<slot>` / `watch`；顺手把三条同名 `rollout-local` 改成带槽位号 —— 三条约同名在转储里等于没名字）。
**第二批（同日，五个类各自持 Runtime）**：`DelivererProcess`（`deliver-proc-out/-err`）·
`UploaderProcess`（`upload-proc-out` 先起、`-in` 后起 —— 顺序是原注释里的契约）· `PushDispatcher`
（`hub-push-tick` + `hub-push-<jid>`）· `HeartbeatPublisher`（`trainer-heartbeat-write`）·
`EvalExecutors`（`eval-exec-<key>-<i>`）。`Runtime.thread()` 起新线程时顺手丢已退出的登记，
长驻对象换线程不会让 `describe()["threads"]` 只涨不落。存量 48 → **40 处 / 30 文件**（`rt.thread(...)` 11 处）。
⚠ `EvalExecutors` 的顺序是契约（先挂池、后开跑）⇒ 为此加了 `thread(..., start=False)`：默认形态把它写成
「起线程即登记」，满载下 `test_two_eval_rounds_share_dispatch_round_robin` 的 A,B,A,B 退化成 A,A,B,B
（实测 3/10 红）；改成 `start=False` 后 6/6 绿。**迁移一个类之前先看它的启动顺序是不是被别处依赖。**

**第三批（同日，十一个类）**：`hub/offline_push`（`hub-offline-push-tick` / `-pump-<course>`）·
`remote/push_dispatch`（`hub-push-tick`）· `deliver_outbox`（`deliver-outbox-loop`）· `job_round._StartBeacon`
（`ppo-start-run`）· `offline_deliver`（`offline-deliver-drain-loop`）· `offline_push._Child`（`push-child-tee-*` /
`-wait-*`）· `payload_proc`（`payload-proc-out`）· `payload_worker._Engine`（`payload-worker-fill`）·
`result_upload`（`result-upload-run`）· `trainer/loop_eval`（`eval-drain-it<起始轮>`；mixin 无 `__init__` ⇒
`_eval_rt` 实例属性懒建，写成类属性就是隐藏单例）· `worker/eval_gate`（`eval-gate-<name|round>-<序号>`：
调用方给的是语义名、不唯一）· `worker/serve_pool._Worker`（`serve-worker-<pid>-read`）。
两个派发器的 `start()` 逐字节相同 ⇒ 按重用账本抽 `remote.push_dispatch.start_tick_thread()` + `TickLoopHost`，
`hub/offline_push.py` 从 1046 行压回 997（LOC 预算 1000）、`exact_groups` 回到 14。
存量 40 → **27 处 / 21 文件**（`rt.thread(...)` 24 处）；G15 的扫描地板随之 40 → 3（只防扫描入口写歪）。

**第四批（同日，模块级扇出清零）**：剩下的 27 处全在模块级函数里，做法是「域就在函数作用域里」——
在拥有一轮/一个单元/一个 job 的那个函数里建**局部** `Runtime`（随函数收尾而散，不造模块级单例），
有 `self` 的挂实例（`BatchEvalRunner._rt`），有 `q` 的挂本轮状态（`queue` / `queue-rescan` /
`alloc-cancel` / `batcheval-u<idx>` / `job-<jid>` / `bc` / `bc-eval` / `stream` / `eval-m1-it<n>` /
`abort` / `race` / `bounded` / hub 与 remote 的常驻几条）。三处「迁移改变语义」：`rt.thread` 只收位置
参数（`args=c` 要写 `*c`，否则 target 里 TypeError 且 `done` 永不置位）· `start()` 已代调（原来的
「先造列表再统一 start」必须删，否则 `threads can only be started once`）· `remote/push_bootstrap.py`
整份进 notebook cell、顶层零仓内 import ⇒ 进 G18 的 `EXEMPT`。**G18 由此转零容忍**：基线清空、地板换成
「>200 文件 + 所有者本人那处数得出」、两向护栏 1 → 40。存量 **0 处**（`rt.thread(` 49 处）。

**门禁 G18** `tests/test_thread_ownership_budget.py` + `tests/golden/thread_construction_baseline.json`：
四批期间是「按文件冻结 + 只许调小 + 9 成扫描地板 + `rt.thread(...)` ≥1」，**存量归零后转零容忍** ——
基线清空（就地 `Thread(...)` 一律红）、地板换成「扫到 >200 个生产面文件 + 所有者本人那处还数得出」
（归零后「数到多少处」证明不了扫描器活着）、两向护栏下限抬到 40（实测 49 处）。豁免三处：
`common/runtime.py`（所有者本人）· `remote/*_boot.py` 与 `remote/push_bootstrap.py`（引导期整份进
notebook cell，顶层连 `common.*` 都不许 import）。

新增 **G16** `tests/test_executor_ownership.py`（零容忍、无基线）：AST 扇生产包里
`ThreadPoolExecutor(...)` / `ProcessPoolExecutor(...)` 只许出现在 `common/runtime.py`，
`Runtime(...)` 必须带 `scope=`，另有一条两向护栏（生产面 `Runtime(` 处数 ≥ 8 —— 「迁移被无声回退」
与「扫描入口坏了」都要红）。红-绿两向都是实测（迁移前：9 违规 / 0 处使用）。全文 →
`docs/nn/engineering.md` §119。

### P1-7　RNG：可复现性建立在全局 `np.random` 上（**已修** 2026-11-17）

```python
# worker/ppo/engine.py
np.random.seed(args.seed)                     # 入口播种全局
...
perm = np.random.permutation(len(tensored))   # 每 epoch 读全局
_didx = np.random.randint(0, demo_n, ...)     # 每步读全局
```

注释明确写"RNG 用全局 `np.random`（由 main 播种，可复现）"。但：

* 全局 RNG 是**进程级共享**，任何第三方库/后台线程抽一次随机数就会改变训练序列
  ⇒ ckpt 续跑"精确复现"的保证依赖"没有别人用过 numpy 随机数"这个不可执行的前提。
* `remote/train_core.py:470` 的种子只有 **32 bit**（sha256 前 8 hex），碰撞空间 2^32。
* 同一仓库里 `worker/train/bc_paddle.py` 用的是 `np.random.default_rng(seed)`（正确做法），
  两种风格并存。

**方案**：所有消耗随机的地方显式拿 `rng: np.random.Generator`（`default_rng(SeedSequence([...]))`），
存进 ckpt 的 `bit_generator.state`；入口处只构造一次并注入。`ppo_update(model, opt, ..., rng=...)`。

**落地（2026-11-17）**：`common/runtime.py` 出 `substream(seed, name)`（blake2b·128bit，stdlib-only），
`worker/ppo/rng.py::PPORng` 持 `chunk`/`epoch`/`demo` 三条流（**三条而不是一条** —— 加/删一处抽样不
移动另外两条，正是本节诉求）；ckpt 存 `state.json["rng_streams"]`，旧 ckpt ⇒ `restore()` False + WARN；
`chunk_episodes(shuffle=True)` 缺 rng 直接 `ValueError`（不保留「没给就读全局」的后门）；四条训练线 +
`remote/train_core`（种子改用完整 hex，不再截 2^32）+ `trainer/stream` 显式传。代价与门禁全文见
`docs/nn/engineering.md` §120（含 AST 零容忍：`worker/ppo/**` 不许再消费全局 numpy 随机）。

### P1-8　重复实现：双生与单文件发行物

克隆检测（12 行窗口，剔除 docstring）结果：真实重复不算多（最大 10 个窗口），集中在：

| 对 | 重复窗口 | 说明 |
|----|---------:|------|
| `worker/models/student.py` ↔ `student_paddle.py` | 10 | 双生实现 |
| `worker/ppo/goal.py` ↔ `intent.py` | ~~8~~ **0** | 两份 PPO 循环 → **2026-11-19 已合**（`worker/ppo/loop.py`；见 `docs/nn/engineering.md` §221） |
| `worker/ppo/engine.py` ↔ `engine_paddle.py` | ~~13~~ **0** | torch / Paddle 训练步 → **2026-11-19 已合**（`worker/ppo/backend.py` + `Backend` 算子表；见 §222） |
| `common/code_sentinel.py` ↔ `remote/offline_boot.py` | 7 | 复制常量与扫描逻辑 |
| `trainer/eval_course_once.py` ↔ `eval_m1_once.py` | 4 | 两个一次性入口几乎一样 |

更严重的是**结构性重复**：`engine.py` / `engine_paddle.py` / `goal.py` / `intent.py` 是**四份**
"PPO 训练循环"，差异只在 head 数与 loss 项；合计约 2,000 行。

`remote/offline_boot.py`（3552 行 / 91 个函数）是另一个极端：它**必须**是单文件（notebook 从
raw URL 拉），于是被 `tests/test_python_loc_budget.py` 永久豁免。这是"**约束真实、但解错了**"。

**方案**

* PPO：抽 `PPOLoop`（数据装载 → 前向 → loss 组装 → step → 诊断），四种线变成四份
  `HeadSpec + LossTerms` 配置。`engine_paddle` 只保留"后端适配器"。
  **2026-11-19 两刀均已落**：第一刀（numpy 两份）→ `worker/ppo/loop.py`，goal / intent 各自
  带 `HeadSpec` + `LossTerms`（§221）；第二刀（torch / paddle）→ `worker/ppo/backend.py`，
  20 字段 `Backend` 算子表 + `run_step`（§222，paddle parity 三段读数不变）。
* paddle 双生：定义 `Backend` 协议（`forward/backward/optimizer_step/tensor_from_numpy`），
  torch 与 paddle 各实现一次，训练逻辑只写一遍。
* `offline_boot`：源码拆成 `remote/_offline_boot/{__init__,packs,hub,lease,code,run}.py`，
  加一个 `tools/build_offline_boot.py` 把模块**拼接**成单文件（带 `# GENERATED` 头）。
  仓库里保留生成物，门禁校验"生成物 == 重新生成"。这样既守住单文件契约，又能拆开维护。

### P1-9　用 `assert` 做运行时校验

* 生产包 **87 处 `assert`**。最危险的是 `common/schema.py:122,216,294`——**模块级**断言：
  在 `python -O` 下全部消失，而它们校验的是"张量布局与 TS 侧逐字一致"这种**正确性根基**。
* `hub/smoke_loopback.py` 用 assert 做冒烟结论（可接受，它是脚本）。
* `remote/payload_slim.py:102` 用 assert 做类型收窄（可接受）。

**方案**：模块级不变量改成显式 `if ...: raise RuntimeError(...)`；热路径的 assert 保留但加注释
说明"仅调试期校验"；`python -O` 不在任何生产/训练命令里出现（加一条门禁 grep）。

### P1-10　安全与健壮性

| 问题 | 位置 | 说明 |
|------|------|------|
| `curl … \| sh` | `remote/tailscale_boot.py:213` | URL 是常量，风险可控；但没有 checksum/签名校验，且 `shell=True` |
| `torch.load` 未显式 `weights_only` | `remote/train_core.py:89,604,909`、`remote/opt_delta.py:624`、`worker/ppo/common.py:422` | torch 2.7 默认已是 `weights_only=True`，**当前安全**；但语义靠版本默认值，降级即 RCE。`common/torch_pickle.py` 已有白名单实现，却只被部分路径使用 |
| 手写 HTTP 服务 | `hub/http_face.py`（935 行）+ `remote/worker_server.py` | 鉴权、Range、分块发送、超时全靠自研；这是有意取舍（零依赖），但**55 个 `elif` 路由**与类属性状态让它难审计 |
| 鉴权失败封禁住进程内存 | `hub/http_face.py` | 重启即清空；注释已承认曾靠重启解封却撞端口死锁 |
| `except BaseException` 36 处 | `common/scratch.py` 等 | 注释说"沙箱删除守卫会打死线程"——环境特例，但应集中到一个 helper |

**方案**：`torch.load` 全部显式传 `weights_only=True`（一行事）；`curl|sh` 改为下载到临时文件 +
校验 sha256 再执行；HTTP 面若要长期维护，考虑把路由表抽成 `(method, pattern, handler)` 的
`Route` 数据（不引第三方也能做），`do_GET/do_POST` 从 55 个分支降到 15 行。

### P1-11　死代码、兼容壳与"转发名"

* `worker/ppo/engine.py` 顶部用 `# noqa: F401` 再导出 11 个名字（`compute_gae` / `load_shard` …），
  注释解释"没人 `import` 这个名字 ≠ 没人在属性上取"——这是**事实**，但解药是测试改用
  `from worker.ppo.np_core import compute_gae`，而不是永久保留转发面。
* `remote/offline_boot.py:203` 的模块级 `__getattr__` 转发。**2026-11-17 复查后保留**（生成式拆分落地时
  顺手把这条的代价重算了一遍）：交付面 36 个名字，逐个手写薄包装 = 36 个函数（比一行门面更难读、
  且每个都要重写签名），而成品拼接也不该把 `offline_deliverable.py` 并进来 —— notebook 的引导格
  是**按文件名**把两个文件一起从 raw 拉的，合并会改运输契约。真正的解是「门面闭集 + 打错不吞」
  已有守卫（`_DELIVERABLE_NAMES` 闭集 + `AttributeError`），本条不再是 P1-11 的欠账，转记在
  `docs/nn/engineering.md` §118。
* `hub/server.py` re-export `_HubQueue`（注释："名字是契约、位置不是"）。
* `trainer/dispatch_queue.py` 整个模块只做 re-export（6 个 `# noqa: F401`）+ 一个 `RUN_ID`，存在的理由就是"旧名字是契约"。
* `common/protocol.py` 保留 `X as X` 门面（`ProtocolError` 等）。

这些单看都有理由（迁移期兼容），合起来构成**"任何东西都不能删"的文化**。建议每个转发面
写一个 `MIGRATION.md` 表格 + 截止条件（例如"tests 全量改完后删除"），并把 `# noqa: F401`
的数量做成**递减**门禁（只许少、不许多）。

**2026-11-14 第二批（已落地）**：`# noqa: F401` 递减门禁 = G6（已有）；本轮把
`trainer/dispatch_queue.py` 的 re-export 面从 7 条削到 **1 条**：6 个消费方全部改 direct import
真家（`common.proc` / `worker.agent_meta` / `trainer.queue_local`），`bun_version` 那条附带的
薄包装一并删掉（`common.proc.bun_version` 的缺省 fallback 本来就是 `"?"`），e2e 的四处竞速
符号 import 也回到 `trainer.queue_local`。**发现（这一刀最有价值的部分）**：`trainer/batch_plan.py`
之所以算「编排」、过得了 `test_layering.py` 的「trainer/ 只有编排」那条，**全靠**
`from trainer.dispatch_queue import mm` 这条 re-export 边——它本身只依赖 `common.*`。改到
`common.proc.version_mm` 的版本会让那条门禁立刻红（`batch_plan` 变成住错包的纯逻辑），
所以那一轮**刻意保留**了 `mm` 并写明理由：删它的前提是先定 `batch_plan` 的归属，
不是顺手改一行 import —— 后者会把门禁静默塌成「纯逻辑可以住 trainer/」。

**2026-11-14 第三批（已落地，同一晚）**：按冲突代价最小的方向定了归属 —— `trainer/batch_plan.py`
**搬进 `biz/batch_plan.py`**（口径 = 它读的是课程关卡文件与 `dashboard/src/evalboard/ladder.json`，
是游戏领域的评估规划；且 `biz/` 早已住着 `course*` / `ladder_ledger` / `reward*` 同族）。
`mm` 随之直取 `common.proc.version_mm`，`trainer/dispatch_queue.py` 的 re-export 面**归零**，
`TRAINER_ORCHESTRATION` 快照少一员（`batch_plan` 真回纯逻辑），`biz/` 的「不许 import 编排」
那条从此也盖住它。

**2026-11-16 第四批（已落地）：`worker/ppo/engine.py` 的转发面从 11 名削到 1 名**。原先那排
`# noqa: F401` 里，真正有消费方的只有两类：① engine 自己调的（`GAMMA` / `LAM` / `load_episodes`）；
② `RolloutBackend`（`worker/backend.py`）要求后端模块上有的（`load_episode_from_shard`，由
`trainer/stream.py` 运行期取）。其余 7 个（`compute_gae` / `discover_shards` / `discover_rl_shards` /
`load_episodes_common` / `load_shard` / `load_shard_fields` / `_RL_SHARD_SPEC`）的唯一"消费方"是两个
worker 测试的属性取用（`import worker.ppo.engine as ppo; ppo.compute_gae(...)`）—— 正是当年逼出
那条"再导出必须活着"守卫的形状。这批把那两个测试改成 `from worker.ppo.np_core import …`，影子随之
删掉，并把 `tests/worker/test_ppo_common.py` 的守卫**翻转**成 `test_np_core_is_the_only_home_for_gae_and_shard_loading`
（断言这 7 名**不在** engine 上、仍在 np_core 上；`load_episode_from_shard` 则断言仍在）。
一个反面发现（值得写下来）：曾试图用 `load_episode_from_shard = np_core.load_episode_from_shard`
赋值来省掉那条 `noqa`，`tests/worker/test_backend_contract_runtime.py` 当场红 —— 静态契约扫描器只认
`from X import y`（记为 `import:X`），赋值别名一律 `<unresolved>`。契约成员必须用 import 形态发布。

**2026-11-16 第五批（已落地）：`trainer/` 的四处"re-exported for tests"全归零**（12 条 `noqa: F401` → 0）。
这四条都没有生产消费方，唯一的读者是测试/e2e 走别名访问，于是停机条件（"tests 全量改完后删除"）一直没兑现：

* `trainer/run_rl.py`：`worker.resume` 的 5 个（`completed_pairs` / `last_completed_iter` /
  `last_rotate_seed` / `peak_entropy` / `resumed_manifests`）与 `trainer.dispatch_queue.run_rollout_queue`
  只有 `e2e/test_run_rl.py` 在用（20 处属性取用）；e2e 改为顶部直取真家，`run_rl.py` 里那两条 import 删掉。
* `trainer/eval_dispatch.py`：`find_archive_weights`（真家 `worker.archive`）、`report_winrate_safe`
  （真家 `worker.eval_local`）、`select_delayed_eval_it`（真家 `worker.eval_yield`）。消费方：
  `find_archive_weights` 与 `select_delayed_eval_it` 各在 `trainer/loop_eval.py` 有一处生产调用（改直取），
  `report_winrate_safe` 零消费方；`tests/worker/test_eval_timing.py` 的两条 import 随之改直取。
* `trainer/dispatch.py`：`resolve_tail_join_sec`（真家 `trainer.queue_local`，`tests/trainer/test_tail_grace.py`）。
* `trainer/loop.py`：`run_inspect` / `log_iter_error` **全仓零消费方**（`run_inspect` 的可替换点是
  `trainer.loop_core`，测试替换的也是那个）—— 直接删，模块只留 `run_training` 与它真用到的 `TrainingLoop`。

G6 基线随之 18 处 / 10 文件 → **6 处 / 6 文件**（余下的是 `hub/smoke_loopback.py` / `remote/offline_boot.py` /
`worker/data/{dataset,weights_io}.py` / `worker/ppo/common.py` —— 各自有独立理由，见 §P1-11 开头三条）。

**2026-11-16 第三批（已落地）：拆掉 `ppo/common` 给 `np_core` 挂的门牌 —— 6 → 5 文件。**
`worker/ppo/common.py` 曾一口气再导出 29 个 `np_core` 名字。量下来：**10 个全仓零读者**
（`_DUR_RE` / `_UNIT_SEC` / `XLA_COUNTERS` / `XLA_TIME_METRICS` / `_RNGState` / `trim_shard_arrays` …），
另外 19 个的消费者只有 5 个文件，且包根 `worker/ppo/__init__.py` 的便捷名表**本来就写着名字住
`np_core`** —— 也就是说这层再导出是第三份门牌：谁写 `from ppo.common import chunk_episodes` 都能过，
撤 `np_core` 的某个名字时没人看得见第二个入口。
- 迁移：`worker/ppo/{engine,goal,intent}.py` · `worker/train/goal_bc.py` · `remote/train_core.py` 共 5 处
  `from worker.ppo.common import …` 改成从 `worker.ppo.np_core` 取（`remote/train_core` 那条是延迟 import，
  而且 `np_core` 免 torch，顺带少拖一次）；`common.py` 只留自己真用的四个名字，`# noqa: F401` 整体消失。
- 判据换代（`tests/worker/test_ppo_common.py`）：原来的「四个面仍保留、但必须是同一对象」是一条
  **白名单**，会替再导出背书；改成结构判据 —— `common` 里每条 `from … np_core import X` 的 X 都必须在
  本模块体内真被引用，否则就是再导出。这类壳整类被禁，不用每加一个名字就改测试。

**2026-11-16 第四批（已落地）：同法清 `worker/data/{dataset,weights_io}.py` —— 5 → 3 文件。**
两处都是 2026-09-26「把免 torch 的那一半搬出去、顺手留层再导出」的残壳：
- `dataset.py` 从 `data/mirror.py` 再导出 5 名，其中 `_MOVE_FLIP` / `BULLET_MIRROR_LUT` /
  `ENEMY_MIRROR_LUT` / `_flip_direction` **全仓零读者**（连一条 `from data.dataset import …` 都没有），
  只有 `mirror_x` 本模块自己要用 ⇒ 直取一行。
- `weights_io.py` 从 `data/weights_meta.py` 再导出 6 名，唯一有外部读者的是 `latest_weights_path`
  （`worker/scripts/eval_bridge.py`，已改直取 `weights_meta`）；其余四个名字本模块自己校验要用，
  `validate_weights_meta` 只是文档里提到 ⇒ 收成一条真 import。

### P1-12　热路径与分配

* `worker/ppo/np_core.compute_gae`：**纯 Python 逐 transition 反向循环**，每次
  `float(values[t+1])` 做标量装箱。一轮 30 万 transitions ⇒ 30 万次 Python 迭代。
  **2026-11-16 已落地**（实测 2.2~4.3×，逐字节不变）：能向量化的那半（γ_step / δ / carry）
  挪进 numpy，tick 循环里只剩 `last = δ[t] + carry[t]·last` 一行。三条否决记录：
  - **「按 done 分段后 `np.cumsum` 前缀积」不可用**：那不是同一个数。`adv` 存 float32 但按
    float64 **逐项**递推，前缀积形式要乘一遍再除一遍（`Σ P_k δ_k / P_t`），舍入顺序变了，
    且 `P_t` 会在长局上下溢到 0 再爆炸；`freeze:check` 的 golden 签名当场就变。
  - **`γ^Δt` 不能用 `np.power`**：numpy 对整数指数有自己的快路径，末位不保证与 Python `pow`
    相同 ⇒ 按 distinct Δt 建 Python pow 查表（变步长语料的 Δt 取值个数远小于 N）。
  - **先 `.tolist()` 再递推**：列表下标比 numpy 标量快，且 `tolist()` / `asarray(float64)` 都是
    精确转换，不影响逐字节。
  实测（`timeit` 取 7 次最小，合成数组，N=决策步数）：N=128 2.7×/2.2×、512 3.5×/3.5×、
  1024 3.5×/4.3×、4096 4.0×/4.4×（前定长 / 后 dt 变步长）。**未测端到端占比** —— 本机没有
  shard 语料，rollout update 里 GAE 占总时长的比例仍需在有 `runs/` 的机器上量。
  回归：`tests/worker/test_np_core.py::test_gae_bit_exact_against_reference_loop`（把旧循环
  逐字留作独立实现，6 长度 × 3 终止密度 × 3 γ × 3 dt 路径逐位对账）。
* `ppo_update` 每步 `_step_walls.append()`、`diag_by_b` dict 操作——可接受，但 `list` 无界增长
  （受 job 时长约束）。
* `common/fs.py::append_jsonl`：**2026-11-16 已落地 —— 去掉写者线程，161 → 40 µs/行（4.0×）**。
  实测拆开看：写盘（open/append/close）只占 28 µs，**99 µs 是跨线程 ack 的等锁延迟**
  （`Future.result()` → `Condition.wait`；cProfile 里 `_thread.lock.acquire` 就是最大头）。
  所以审计档原建议的「批量刷」治不了它 —— 每个调用方仍要等自己那一行被 ack。改成
  「每路径一把锁 + 就地追加」：`append()` 返回时行已经写完并关掉句柄（契约不变），
  异常天然就是调用方的异常（不再经 Future 转一次）。两条**否决**：
  - 不保留「常开句柄」把 28 µs 也省掉（能到 7 µs/行）：那会破坏既有不变式「写者不持有
    句柄」，且 Windows 上同路径的 `os.replace`（`atomic_write_bytes`）会撞上我们自己
    开着的句柄 —— 这条只能在 Linux 门禁上验证，没法在本机证伪。
  - 不做批量/异步：`append` 的语义是「返回即另一个进程能读到整行」，延后写等于把账本
    丢行的窗口从「进程被 kill」扩大到「任何时刻」。
  附带收益：`close()` 不再需要「排空队列」与 `timeout`，`_JSONL_WRITER_IDLE_EXIT_SEC`
  的空闲退场、`daemon` 线程泄漏也一起消失（G4 `common/fs.py` 1 → 0）。
  判据 `tests/common/test_jsonl_writer.py`：单句柄不变式照旧（打桩 `open` 放大窗口），
  新增「返回即可读」与「不再有 `jsonl-writer` 线程」两条。
* `common/distribution.py` 在 L0 里做 HTTP + git 子进程 —— 任何 import `common.distribution` 的
  模块都会被拖上 `urllib`/`subprocess` 的 import 成本（可测，但未测）。

**方案**：GAE 向量化 + 单测对拍（固定 seed 逐位相同）；`append_jsonl` 改批量刷（已有队列，
加 `flush()` 与批量 `writelines`）。

---

## 5. 逐层点评

### 5.1 `common/` —— 好的原语，坏的杂物间

**好**：`fs.atomic_write_bytes` 带 Windows 句柄重试；`proc.run_capture` 显式 UTF-8；
`errors` 的失败分类有处置语义；`hashing`/`text`/`logutil` 都是纯函数。这是全仓质量最高的层。

**坏**：

* **契约被违反**（§P0-5）。
* **`distribution.py` 1555 行 / 54 个函数**塞了：配置加载、code hash（含 git）、HTTP 客户端、
  节点探活、权重下发、任务领取、结果轮询、重启去重、容器解包。L0 里本来就有 `proc`/`fs`/`net_http`
  这些带副作用的原语，但它把**网络栈 + git + 调度策略**全拉了进来，于是等于把网络栈焊在底层。
  建议拆：`common/codehash.py`（纯）、`common/config.py`（纯）、`remote/node_client.py`（副作用）。
* `protocol.py` 1064 行把**线协议常量**与**调度策略**（`job_priority`、`pick_push_worker`）混在一起。
  策略会变、协议不该变；将来改调度要动"协议模块"，评审时很难判断是否破坏兼容。
  **2026-11-18 已拆（B8 第二刀）**：32 名策略面（优先级表 + `CLAIM_MODE_*` · push 节奏/冷却/告警 +
  `push_worker_id_of` · 节点能力 `CAP_*`/`NODE_*` + 三个形状函数 + `pick_push_worker` /
  `push_job_wants_hub_push`）逐字节搬进 `common/scheduling.py`（L0、stdlib-only），`protocol.py`
  1120 → 969 行、只留 `X as X` 门面；守卫 `tests/common/test_scheduling_split.py` 钉住「定义唯一 /
  依赖闭集无环 / 转发同一对象 / 不抄线协议常量」。详见 `docs/nn/engineering.md` §130。
* `log.py` 与 `logutil.py` 两套时间戳（§P0-6）。

### 5.2 `biz/` —— 最健康的一层

`reward_library.py` 用 AST 白名单做公式引擎，禁 `eval/exec/Attribute/Subscript`，限长限深，
求值后 `isfinite` 检查——这是**教科书级的做法**，应作为其他"用户可配置逻辑"的模板。
`course_spec.py` 用 pydantic 校验课程。唯一问题：`_check_rules` CC=70（1357 行文件里的单函数），
可以按规则类型拆成 `RULE_CHECKS: list[Callable]` 注册表。

### 5.3 `worker/` —— 算法正确，结构失控

* `iter_rollout.run_iter_rollout` 604 行 / CC 102：把"准备 scratch / 装 TS / 分批 / 补波 /
  收尸 / 日志"全塞进一个函数。
* `eval_arbiter.py` 773 行 / 58 方法的 `EvalRound` + 1,130 行模块 = 评估调度的事实标准，
  但没有任何接口文档（只有代码注释）。
* `ppo/engine.py` 的 `ppo_update` 是**四份 PPO 实现之一**（§P1-8）。
* `models/student.py` 与 `student_paddle.py` 双生，靠"注释保证逐字一致"。

### 5.4 `trainer/` —— 编排层的混入地狱

`TrainingLoop(RoundSteps, TrainingSteps, TrainingGuards, TrainingLifecycle)`，
四层混入合计约 12 个类。`dispatch.py` 的 `RolloutDispatcher` 1258 行 / 3 个方法——
其中 `_run` 1184 行。这是**全仓最该重构的单个文件**。

### 5.5 `hub/` —— 服务端逻辑清楚，HTTP 面手工

调度语义（租约、优先级、轮转、熔断、离线租约）写得很细，失败模式都有注释。
但 `http_face.py` 用 28 + 27 个 `elif` 做路由、用 `Any` 声明混入接口、用类属性存全局状态。
`_HubQueue` 的 8 基类是 §P0-1 的核心。

### 5.6 `remote/` —— 协议层与"云机现实主义"

这一层承载了大量**真实世界约束**（Colab/Kaggle 无 NET_ADMIN、raw URL 拉取、无 torch 环境），
所以它的"重复"很多是**必要的**。批评应限于：`offline_boot` 的单文件形态没有工程化（§P1-8）、
`hub_client.publish_job` 的 56 参数（§P0-3）、以及 `common.distribution` 与 `remote.*` 的职责重叠。

### 5.7 `tools/` —— 41 个脚本住进了生产包

`p0_auc_probe.py`、`lever_scan.py`、`wave2_judge.py`、`paired_power.py`…
它们有 `main()`、有 argparse、有 `sys.path` hack，却**没有入口约定**（一半 `if __name__`，
一半 `run_main`）。建议移到 `tools/oneoff/`，并在 `pyproject.toml` 用 `[project.scripts]` 注册
少数长期使用的（如 `nn-bootstrap`、`nn-smoke`）。

### 5.8 `tests/` —— 数量惊人，形状堪忧

* 单文件动辄 2000 行（`test_remote_iter.py` 2254），命名按"事故"而不是"行为"。
* `tests/helpers/` 已有 `hub_seams`、`net_seams`、`source_scan`、`scan_cache` —— 方向是对的，
  但只服务少数用例。
* 11 个文件仍用 `check()/FAILS` 遗产 harness。
* 门禁 `-x`：第一个失败就停，CI 上等于**每次只修一个 bug**。

---

## 6. 目标架构

分层不变（L0–L4 已经被 `tests/test_layering.py` 与 `tests/helpers/remote_dag.py` 守住，这是资产），
但要补上**三个缺失的横向层**：

```
L0  common/        纯原语 + settings + logging + 契约类型（无状态、无 I/O）
L1  biz/           领域纯逻辑（奖励/课程/阶梯）
L2  worker/        节点执行体（+ 显式 Runtime：rng/clock/executors）
L3  remote/        跨端线路 + 云引导（+ 生成的 offline_boot 单文件）
L4  hub/ trainer/  服务端与编排（+ 协作对象，不再是混入）
```

**新增（横向）**

| 新模块 | 内容 | 替代 |
|--------|------|------|
| `common/contracts/` | `Shard`(TypedDict)、`Manifest`(pydantic)、`Result`(pydantic)、`ActionSpace`(Enum) | 裸 dict + 魔法字符串 |
| `common/settings.py` | `Settings`（frozen dataclass / pydantic） | 130 处 `os.getenv` |
| `common/logging.py` | `configure()` + `get_logger(name, course=...)` | `print` + `log()` + `logutil` |
| `common/runtime.py` | `Runtime(rng, clock, executors, stop)` | 全局 RNG + 54 个裸线程 |

**关键形状变化**

```
# 之前
dispatch = RolloutDispatcher(...)          # 1258 行 / _run 1184 行
dispatch.run()

# 之后
state = DispatchState.from_args(args, cfg)      # frozen
with Runtime(seed=...) as rt:
    plan = PingGate(rt).run(state)               # 失败 → PhaseResult.fail
    dist = WeightDistributor(rt, hub_client).run(state, plan)
    out = QueueRunner(rt, store).run(state, plan, dist)
    return settle_round(state, out)
```

---

## 7. 模式配方（可直接照抄）

### 7.1 参数对象（消灭 56 参数）

```python
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class PublishSpec:
    # —— 身份 ——
    run_id: str
    it: int
    course: str
    course_name: str = ""
    course_fp: str = ""
    corpus_fp: str = ""
    # —— 代码 ——
    commit: str = ""
    code_sha256: str = ""
    # —— 训练超参 ——
    mode: str = "ppo"
    epochs: int = 4
    mb: int = 512
    lr: float = 3e-4
    kl_coef: float = 0.0
    ent_coef: float | None = None
    # —— 线协议开关 ——
    wire_diet: bool = False
    reward_spec: RewardSpec | None = None

    def __post_init__(self) -> None:
        if self.epochs < 1 or self.mb < 1:
            raise ProtocolError(f"非法超参 epochs={self.epochs} mb={self.mb}")
        if self.wire_diet and self.reward_spec is None:
            raise ProtocolError("wire_diet 需要 reward_spec（否则读侧按 it=1 算错整轮）")


def publish_job(spec: PublishSpec, *, job_root: Path, traj_dir: Path) -> dict: ...
```

收益：3 个打包点共用同一份构造与校验；mypy 能查字段；测试只构造 `PublishSpec`，不拼 56 个 kwarg。

### 7.2 dict → TypedDict（零运行时成本）

```python
from typing import NotRequired, TypedDict

class ShardV6(TypedDict):
    obs: np.ndarray        # (T, C, H, W) u8
    scalars: np.ndarray    # (T, SCALAR_DIM) f4
    a_move: np.ndarray
    a_fire: np.ndarray
    a_hold: np.ndarray
    lp_move: np.ndarray
    lp_fire: np.ndarray
    lp_hold: np.ndarray
    mask: np.ndarray       # (T, 9)
    value: np.ndarray
    reward: NotRequired[np.ndarray]
    extra: NotRequired[np.ndarray]
```

`if "a_hold" in shard` 就变成**类型收窄**（mypy 会提示 `NotRequired` 的存在），
`e.get("a_hold")` 的三处嗅探可以合并到一个 `ActionSpace.of(shard)` 函数。

### 7.3 失败分类（保留现有优点，扩大到边界）

```python
def run_phase(fn: Callable[[], T]) -> T:
    """相位边界：只有这里允许宽捕获，且必须留痕 + 分类。"""
    try:
        return fn()
    except RetryableError as e:
        log.warning("phase retryable: %s", e, exc_info=True)
        raise
    except ProtocolError as e:
        log.error("phase protocol: %s", e)
        raise
    except Exception:
        log.exception("phase crashed (unexpected)")
        raise
```

### 7.4 显式 Runtime（RNG / 时钟 / 线程池）

```python
@dataclass
class Runtime:
    rng: np.random.Generator
    clock: Callable[[], float] = time.time
    stop: threading.Event = field(default_factory=threading.Event)
    _executors: list[ThreadPoolExecutor] = field(default_factory=list)

    @classmethod
    def create(cls, seed: int | Sequence[int]) -> "Runtime":
        return cls(rng=np.random.default_rng(np.random.SeedSequence(seed)))

    def pool(self, name: str, workers: int) -> ThreadPoolExecutor:
        ex = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self._executors.append(ex)
        return ex

    def __enter__(self) -> "Runtime":
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        for ex in self._executors:
            ex.shutdown(wait=False, cancel_futures=True)
```

ckpt 里存 `rt.rng.bit_generator.state`，续跑时 `rng.bit_generator.state = saved` ⇒ 不再依赖全局随机流。

**落地形态（2026-11-17，与上面的草图两处不同）**：① 随机流不是一个 `rng`，而是 `PPORng` 的三条
（`chunk`/`epoch`/`demo`，由 `common.runtime.substream` 派生）—— 上面写「加/删一处抽样不动别处」，
一条 `rng` 做不到；② 时钟不是一个 `clock`，而是 `clock`（墙钟，只做时间戳）+ `monotonic`（时长/超时），
否则迟早有人拿单调值去比时间戳。全文 → `docs/nn/engineering.md` §120 / §121。

### 7.5 协作对象替代混入（保留测试接缝的过渡法）

```python
# hub/scheduler.py
class Scheduler:
    def __init__(self, state: HubState, clock: Clock) -> None:
        self._s = state
        self._clock = clock

    def claim_next(self, worker: str) -> str | None:
        with self._s.lock:
            ...

# hub/queue.py —— 过渡期保留转发壳，656 处私有断言先不炸
class _HubQueue:
    def __init__(self, ...):
        self._state = HubState(...)
        self._scheduler = Scheduler(self._state, clock)
    def claim_next(self, worker: str) -> str | None:
        return self._scheduler.claim_next(worker)
```

然后逐条把测试从 `hub._stores[x]` 迁到 `hub.seams.queue_state(x)`，迁完删转发壳。

### 7.6 单文件发行物的生成式拆分

```python
# tools/build_offline_boot.py
MODULES = ["packs", "hub", "lease", "code", "run"]
HEADER = '"""GENERATED from remote/_offline_boot/*.py — do not edit."""\n'
def build() -> str:
    parts = [HEADER]
    for m in MODULES:
        src = (ROOT / "remote" / "_offline_boot" / f"{m}.py").read_text()
        parts.append(strip_relative_imports(src, prefix="from ._offline_boot."))
    return "\n".join(parts)
```

门禁：`python tools/build_offline_boot.py --check`（生成物与源码不一致 ⇒ 红）。
收益：开发期 3552 行可拆成 6 个 500 行模块；运行期仍是单文件契约。

**2026-11-17 已落地**（口径与上面的草稿有两处不同，都以实测为准）：

* **片数 12 而不是 6**（`01_front` 门面 · `02_consts` · `03_pack` 任务包识别 · `04_fetch` 取包 ·
  `05_code` 代码引导 · `06_run` 跑一局 · `07_queue` 队列面 · `08_progress` 打点 · `09_session` 会话面 ·
  `10_batch` 批/自动 · `11_bc` BC 腿 · `12_entry` 入口）：切点取**顶层块边界**（函数/常量/注释分隔行），
  所以每段都能独立 `ast.parse`，最大的一段 441 行；6 段会让每段 600 行且要在一段里混两个域。
* **分片后缀 `.pyfrag` 而不是 `.py`**：草稿里的 `.py` 会让生产树每个 `rglob("*.py")` 门禁把同一行字
  数两遍（print / 宽捕获 / `Any` / 函数形状 / LOC / 层级账本 / 抑制符全中），那等于逼着抬基线或给门禁
  加排除名单。代价写在明处：分片自身不被 mypy/ruff 解析 ⇒ 一致性与语法由「重建后跑 mypy + 用例」兜住。
* 发行物头部 6 行**只有注释**（不进 AST），所以后面那三引号仍是模块 docstring，`test_module_doc_budget`
  的 53 行读数不动。

### 7.7 CLI 收敛

```python
def build_argparser() -> argparse.ArgumentParser:
    p = house_parser("worker")
    for group, args in ARG_GROUPS.items():          # 数据驱动，而不是 650 行手写
        g = p.add_argument_group(group)
        for a in args:
            g.add_argument(*a.flags, **a.kwargs)
    return p
```

---

## 8. 新门禁（每条都能写成 `tests/`）

| # | 规则 | 实现 |
|---|------|------|
| G1 | 函数 ≤ 200 行、参数 ≤ 8（原提议 120/7，落地时取了本仓实测可守的口径，CC 未进门禁） | AST 扫描 + 白名单冻结存量（`tests/test_python_shape_budget.py`；**>200 行存量 2026-11-19 已清零**） |
| G2 | `common/**` 无模块级可变容器、无 `global` | AST 扫描（`tests/test_python_shape_budget.py::test_common_has_no_new_mutable_module_state`，**名册已于 2026-11-16 清零**） |
| G3 | 生产包新增 `print(` = 0（工具脚本豁免） | `tests/test_no_print_in_prod.py` |
| G4 | 新增 `except Exception` 只允许在 `*_boundary` 白名单 | ruff `BLE001` + per-file-ignores |
| G5 | `tests/**` 新增私有断言 = 0 | AST 扫描差集 |
| G6 | `# noqa: F401` / `type: ignore` 数量只减不增 | 计数快照文件 |
| G7 | `common/**` 模块级 `assert` = 0 | AST 扫描 |
| G8 | `common.distribution` 的 HTTP/git 调用数只减不增 | 计数快照 |
| G9 | 生成物（`offline_boot.py`）与源码一致 | `build_offline_boot.py --check` |
| G10 | 依赖方向不变（已有） | `tests/test_layering.py` 扩展：把新 contracts/settings 纳入 |
| G13 | 生产面 env 直读只减不增 + 已收官旋钮单家 | `tests/test_env_reads_budget.py`（AST；基线 `tests/golden/env_reads_baseline.json`，豁免 `STANDALONE_BOOT_MODULES`） |
| G14 | 生产面 `Any` 注解只减不增 | `tests/test_any_budget.py`（AST；基线 `tests/golden/any_baseline.json`，**1178 处 / 181 文件**；另挡 `Any as A` 改名绕过） |
| G15 | 每个 `Thread(...)` 必须带 `name=` | `tests/test_thread_names.py`（AST；**零容忍无基线** —— 2026-11-16 一次性补齐 55/55） |
| G18 | 就地 `Thread(...)` 的存量只减不增（所有者 = `Runtime.thread()`） | `tests/test_thread_ownership_budget.py`（**零容忍**：生产面就地 `Thread(...)` 一处都不许 + 扫描自检「>200 文件 + 所有者本人那处数得出」 + `rt.thread(` ≥40 的两向护栏；豁免 `common/runtime.py` / `remote/*_boot.py` / `remote/push_bootstrap.py`） |
| G16 | 线程池只能由 `common.runtime.Runtime` 建（带 `scope=`） | `tests/test_executor_ownership.py`（AST；**零容忍无基线** —— 2026-11-17 把 10 处就地建池全收进 `Runtime`） |
| G19 | 测试面「`monkeypatch` 打私有名」只减不增 | `tests/test_monkeypatch_targets_budget.py`（AST；基线 `tests/golden/monkeypatch_private_baseline.json`，**392 处 / 95 文件**冻结；dunder 与非字面量目标不算；2026-11-19 新增，对应 §P0-8 第四项；2026-11-21 端口收编 −10） |

**注意**：这些门禁全部是"冻结存量 + 禁止新增"，**不要求一次性清零**——这是本仓门禁文化
（`EXEMPT_FILES` + 防漂移检查）的自然延伸，也不会让提交被卡死。

---

## 9. 关于"注释承担契约"这件事（必须单独说）

生产包有 **36.9% 的注释 + docstring 行**（全仓 27%）。大多数单条注释质量很高
（事故现场、实测数字、被否决的备选），这是资产。但整体上已经出现
三个病理特征：

1. **注释替代类型**：`dict[str, Any]` 的字段含义写在 docstring 里（`demo_bank = {"obs": ..., "scalars": ...}`）
   ⇒ 编译器看不见，改字段不会红。**docstring 不是类型系统。**
2. **注释替代结构**：`ppo_update` 用 31 行 docstring + 大量行内注释描述 6 个子系统
   （前向/损失/诊断/断点/profiler/日志），而不是拆成 6 个函数。
   读者必须先读完注释才知道代码在干什么——这是"文档债"掩盖"结构债"。
3. **注释腐烂且无人检查**：`common/logutil.py:13` 引用不存在的 `biz/log.py` 并做出**可证伪的
   错误断言**（§P0-6）；`common/scratch.py` 模块 docstring 里有一处被截断的句子
   （"…**闸内改名**。暂存目录放在" 紧接下一段"⚠ **已无生产调用点**…"），且同一段
   `⚠ 已无生产调用点` 重复了两次。这类漂移只能靠人读出来。

**建议**：
* 把"为什么"留在注释，"是什么"交给类型与签名；
* 每条**跨模块**断言（"与 X 逐字一致"）必须配一个自动化测试，否则删掉——注释不能做契约；
* 模块 docstring 上限 ~50 行，超出的设计说明移到 `docs/nn/*.md` 并在 docstring 留链接；
* 新增一条门禁：docstring 里出现 `path/to/file.py::name` 时，该文件必须存在（AST + 路径检查）。

**已落地（2026-11-16）：最后一条。** `tests/test_docstring_refs.py::test_docstring_pointers_resolve`
—— 只查**带目录的 `::` 形态**（`worker/events.py::write_iteration`），不查 `path.py:123` 行号
（行号天然漂移，而且它出现在被引用的事故 traceback 里，那是历史证据），不查裸文件名
（散文里提个同名模块不算指针）；解析顺序 = 仓库根 → `nn-training/` → 引用者目录 → 包内后缀。
落地时抓到 **13 处**指向搬家前旧家的指针并全部改成现在的家：`biz/loop_guards_{trip,leg,gate,sweep}.py`
⇒ `worker/…`、`biz/events.py::write_iteration` ⇒ `worker/events.py`、`biz/archive.py` ⇒ `worker/archive.py`、
`biz/config.py::course_from_args` ⇒ `biz/course_resolve.py`、`biz/config.py::CourseConfig` ⇒ `biz/course_spec.py`、
`biz/bc_ledger.py::inflight_jobs` ⇒ `worker/bc_ledger.py`、`rl/batch_eval.py`/`rl/batch_runner` ⇒
`trainer/batch_runner.py::_UnitLanes.record`（原「三处行构造点」现在是两处）。

**三条建议的现状（2026-11-19 复核）**：① 模块 docstring ≤50 行 —— **已落门禁 G11**
（`tests/test_module_doc_budget.py`，2026-11-16）；② 注释不承担类型 —— **部分**：数据边界
已换成 `Shard`/`Episode` TypedDict、`Manifest`/`CloudResult` pydantic 模型与 `ActionSpace`
Enum，剩下的 `dict[str, Any]` 由 G14 冻结（1178 处 / 181 文件），逐个边界继续收；③ 跨模块
断言配测试 —— **部分**：注释指针有 `test_docstring_refs`，结构契约有拆簇守卫，
但没有「一条注释断言 = 一条用例」的机器判据（也不建议做成门禁：判据是语义的）。

---

## 10. 分阶段路线图

> 原则：每批**独立可合并、可回滚**；每批先写"防漂移测试"再动代码；不做大爆炸式重写。

| 批次 | 内容 | 验收 | 风险 |
|------|------|------|------|
| **B1**（1–2 天） | 版本矩阵对齐（3.12 或 CI 加 3.10）；关 `-x`；开 `warn_unused_ignores`；`B023`/`SIM115` 收回 | CI 绿；ignore 数量下降 | 低 |
| **B2**（2–3 天） | `common/logging.py`；替换 `hub/`+`trainer/` 的 `print`；删 `logutil` 的假声明；修 `log.py` 双写 | 日志级别生效；跨进程时间戳一致 | 低 |
| **B3**（3–5 天） | 三个参数对象：`PublishSpec` / `DispatchState` / `TrainSpec`；改调用点 | 参数 > 8 的函数减少 40% | 中（网络路径） |
| **B4**（3–5 天） | 拆 `dispatch._run` 为 5 个相位方法 | `_run` ≤ 80 行；dispatch 测试全绿 | 中高（需一轮真跑） |
| **B5**（5–7 天） | `common/contracts`：Shard/Manifest/Result/ActionSpace；删三处 `a_hold` 嗅探 | 类型检查覆盖数据面；v6/legacy 由 Enum 决定 | 中（**已完成**：`ActionSpace`+`HeadLayout`（2026-11-16）、三处嗅探清零、`Shard`/`ShardV6`/`Episode`/`EpisodeV6` 契约 + import 哨兵（2026-11-18，§126）、episode 跨三层收口（§127）、`Manifest`/`CloudResult` 的 pydantic 模型 + 两个薄包装（§128，L0 放行 pydantic 一个包，闭集三处）；**剩余**：`cfg`/`course` 的 schema 继续收（可选）） |
| **B6**（5–7 天） | `Runtime`（rng/clock/executors）注入；PPO 改 `default_rng`；ckpt 存 bit generator（**三半边全落** 2026-11-17：executor = `Runtime` 收走 10 处就地建池 + G16 · rng = `PPORng` 三条流 + `rng_streams` 进 ckpt + AST 零容忍 · clock = `Runtime(clock=, monotonic=)` + `PushHooks` + G17 墙钟预算（482 处/92 文件冻结）· 裸线程 = `Runtime.thread()` 名字/登记/join + G18 零容忍闸（50 → **0 处**；四批：`CloudEvalRunner` · `LocalLane` · `DelivererProcess` · `UploaderProcess` · `PushDispatcher` · `HeartbeatPublisher` · `EvalExecutors` + 第三批 11 个类 + 第四批模块级扇出，见 §P1-6）） | 同 seed 两遍逐位一致；并发下可复现 | 中 |
| **B7**（7–10 天） | 协作对象替混入（`Scheduler` / `LeaseBook` / `Ledger` / `CourseTable` / `WorkerBoard` / `Dispatch` / `QueueScope`）——**P1–P4b + S1/S3/S2 已落**（2026-11-19，8 笔提交，见 `docs/nn/engineering.md` §225）：`_JobStore` 的 bases 7 → **4**、MRO 9 → **6**；`_HubQueue` 的 21 个状态字段搬进协作者（138 个域方法与 5 把旁路锁不动），课程表 / 路由 / 停机 / 发现面 26 个方法搬进 `QueueScope` 协作者，bases 9 → **7**、MRO 14 → **12**。锁集合/顺序不变、`queue_state()`/`mem_readout()` 键集逐字不变。**设计原稿**见 §224；**壳收敛已完成**（原稿 ≤4/≤9 与 §3 冲突，按 §9 重估为 ≤7/≤12，见 §225「下一刀」）。测试侧另落两刀：`hub/http_face.py` 的 55 支 `elif` 路由 → 两张数据表 + 纯函数 `match_route`（§217），五份可变类属性 → `HubState`（§218） | `_JobStore.__bases__` ≤ 4 ✅；`_HubQueue.__bases__` ≤ 7 / `__mro__` ≤ 12 ✅；私有断言 25 → **19** ✅ | 高（面广） |
| **B8**（5–7 天） | `common.distribution` 拆分（**第一刀已落** 2026-11-16：codeHash 面 → `common/codehash.py`，仓库锚点 → `common/paths.py`，调用点与打桩面同步迁走；**第十刀已落** 2026-11-19：升级/重启护栏 12 名 → `remote/node_upgrade.py`，HTTP 漏斗改名公开的 `node_request`；**第十一刀已落** 2026-11-19：任务协议 / 权重传输拆成 `common/task_client` + `common/weights_client`，`distribution` 1 181 → 490 行）；`offline_boot` 生成式拆分（**已落** 2026-11-17：12 个 `.pyfrag` + `tools/build_offline_boot.py` + `--check` 守卫）；`PPOLoop` 统一四份实现（**第十二刀已落** 2026-11-19：numpy 两份（goal / intent）合成 `worker/ppo/loop.py`，克隆窗口 11 → 0；**第十三刀同日已落**：`engine ↔ engine_paddle` 的训练步合成 `worker/ppo/backend.py` + 20 字段 `Backend` 算子表，13 → 0，paddle parity 三段读数不变；**第十四刀同日已落**：两个头的 `main()` 合成 `worker/ppo/cli.py`（`CliSpec` + 顺序），`--help` 逐字节不变） | 单文件 < 1000 行（取消豁免）；克隆窗口下降 | 中 |

**不建议的路线**：一次性 `strict = true`、一次性删全部 `sys.path`、一次性把 hub 换 FastAPI。
前两个会制造 1000 个红点并诱发"为了绿而加 ignore"；第三个引入运行时依赖，违背零依赖口径。

---

## 11. 明确**不要**改的地方（避免把好设计改坏）

1. **`common/errors.py` 的失败分类**——它是全仓最有价值的抽象之一，不要合并成一个异常。
2. **原子写 / 安装票 / 对账**（`fs.atomic_write_bytes`、`scratch.InstallGate`、`drain_tree`）——
   这些是踩过事故后的正确解，不要为了"简化"去掉重试与票据。
3. **奖励公式的 AST 白名单**（`biz/reward_library.py`）——零依赖、可审计、有边界，别换 `eval`。
4. **确定性检查点与 `xla_mark_step` 的处理**——性能与正确性的关键，注释里的实测数字是资产。
5. **`common/proc.run_capture` 的显式 UTF-8**——别再出现裸 `text=True`。
6. **三份 bootstrap 模块的"刻意重复"**（`tailscale_boot` / `notebook_boot` / `offline_boot`）——
   约束真实；要改的是**工程化方式**（生成），不是"合并成一份"。
7. **分层守卫测试**（`test_layering.py` / `remote_dag.py`）——它们是这份代码最值钱的基础设施。

---

## 12. 结论

这套代码**不缺勤奋，缺的是边界**。

S 系列 23 刀证明了团队有能力做大重构，但它选择了一条"零行为变化 + 测试不改"的路径，
于是每一刀都只是把代码搬了个家：混入变多、参数变长、dict 变深、全局状态藏得更隐蔽。
真正的下一步不是"再拆一个文件"，而是：

> **先在测试里建立接缝（B7 的前半），再在代码里建立对象（B3/B5/B6），最后才动结构（B4/B8）。**

顺序反了，就会得到第 24 个混入。

如果只做三件事：**`PublishSpec` + `DispatchState`（B3）**、**`Runtime` + `default_rng`（B6：`Runtime` 已落地；2026-11-18 `goal_bc` 的 epoch 打乱改走 `substream` 派生 + 局部 Generator，全局 `np.random.seed` 还剩 6 处各带理由，见 `docs/nn/engineering.md` §144）**、
**`common` 去状态化（G2）**。这三件做完，后面的每一件都会变便宜。

---

## 附录 A：度量复现

> **2026-11-18 已落地**：两个脚本从 gitignored 的 `tmp/` 移进 `nn-training/tools/audit/`
> （`py_audit.py` / `py_clones.py`），路径从 `__file__` 推、不依赖 cwd，读数可原样复跑；
> 本附录的每条命令现在都指向仓库内文件。是否再把它们接成门禁另开一刀（现在是「可复跑」，
> 不是「会变红」）。

```bash
cd nn-training
# 1) 文件/行数
find common hub remote trainer worker biz tools train -name '*.py' -print0 | xargs -0 wc -l | tail -1
#    → 147991 total

# 2) 函数形状 / 复杂度 / except 分布 / 模块级容器
.venv/bin/python tools/audit/py_audit.py      # 见 tmp/py-audit.json（cwd = nn-training）

# 3) 近似克隆（已剔除 docstring 与空行，12 行窗口）
.venv/bin/python tools/audit/py_clones.py

# 4) 关键计数（本文引用的原始命令）
grep -rn "sys.path.insert\|sys.path.append" --include='*.py' common hub remote trainer worker biz tools tests e2e | wc -l   # 409
grep -rn "monkeypatch" --include='*.py' tests e2e | wc -l                                                                   # 3962
grep -rn "assert .*\._[a-z]" --include='*.py' tests e2e | wc -l                                                             # 656
grep -rn "except Exception" --include='*.py' common hub remote trainer worker biz tools | wc -l                              # 444
grep -rn "^\s*print(" --include='*.py' common hub remote trainer worker biz | wc -l                                         # 238
grep -rn "^class .*Mixin" --include='*.py' common hub remote trainer worker | wc -l                                          # 25
grep -rn "^\s*global " --include='*.py' common hub remote trainer worker biz | wc -l                                         # 14
```

## 附录 B：问题速查表（2026-11-18 逐行复核）

> 复核方式：每行都在**当前代码**上重新量了一遍（不是凭记忆）。状态三态：**✅ 已修** /
> **部分**（有明确残留）/ **⏳ 未动**（有理由或没排期）。这张表是「下一个接手的人先看什么」，
> 过期的一行会把已完成的工作重做一遍。

| 文件 | 原问题 | 修法 | 批次 | 现状 |
|------|--------|------|------|------|
| `trainer/dispatch.py` | 1184 行 / CC 190 | 相位切分 + `DispatchState` | B3/B4 | **✅ 已修**：现在 **648 行**，`_run` 只做编排 |
| `remote/hub_client.py` | `publish_job` 56 参 | `PublishSpec` | B3 | **✅ 已修**：签名只剩 `(spec, *, log=_log)` |
| `worker/ppo/engine.py` | 598 行 / 17 参 / 全局 RNG | `PPOLoop` + `Runtime` | B6/B8 | **✅ 已修**：`ppo_update` 是 85 行薄适配器 + `PPOContext` |
| `worker/ppo/engine.py` 的 `a_hold` 嗅探 | 运行时类型判断 | `ActionSpace` Enum | B5 | **✅ 已修**：`common/contracts.py` 唯一来源 + 字节初筛门禁 |
| `hub/queue.py` | 8 基类混入 | `CourseTable` / `WorkerBoard` / `Dispatch` + `QueueScope` + 同名转发壳 | B7 | **✅ 已修（2026-11-19）**：bases **9 → 7**（6 混入 + Auth）、MRO **14 → 12**；21 个状态字段搬进 `CourseTable` / `WorkerBoard` / `Dispatch`（组合类只留转发属性），课程表 / 路由 / 停机 / 发现面 26 个方法搬进 `QueueScope` 协作者（组合类留同名壳）。锁对象与加锁顺序逐字不变，`_solo` 借表 / `_adopt_solo` 整表搬运 / 138 个域方法与测试调用点未改。`queue*.py` 4 035 → **4 207 行**（+172 全是转发壳；七个域文件本身 −38）。实现记录见 `docs/nn/engineering.md` §225 |
| `hub/store.py`（`_JobStore`） | 六个域混入 + Auth | `Scheduler` / `LeaseBook` / `Ledger` + 转发壳 | B7 | **✅ 已修（2026-11-19）**：bases **7 → 4**（Wire / Results / Offline / Auth）、MRO **9 → 6**；调度 / 租约 / 账本三簇共 20 个状态字段与 62 个方法搬进协作者，组合类只留 64 个同名转发；唯一两相引用是 `Ledger.leases` 回填（构造顺序有断言） |
| `trainer/loop_core.py` | 12 类混入组合 | 同上（编排层） | B7 | **部分**：12 → **4**（`RoundSteps`/`TrainingSteps`/`TrainingGuards`/`TrainingLifecycle`） |
| `common/log.py` / `common/logutil.py` | 双时间戳 + 假声明 + 全局状态 | 统一格式 + `contextvars` | B2 | **✅ 已修**：`logutil.stamp()` 是唯一行格式（§143 把最后两份手拼也收了）、假声明已改正 |
| `common/fs.py` | 线程泄漏 / 无 flush | `JsonlLedger` 对象 | B2/B6 | **✅ 已修**：文件里已无 `Thread(`，写入走 `JsonlLedger` |
| `common/distribution.py` | 1555 行杂物间 | 拆 codehash/config/node_client | B8 | **✅ 已修（2026-11-19）**：**490 行**，只剩传输核心（配置 / 错误分类 / 收工断连 / `node_request` 漏斗 / 探活 / 权重指纹）；codehash · 锚点 · 调度策略 · 升级重启簇 · 任务协议（`common/task_client` 452 行）· 权重传输（`common/weights_client` 315 行）全部外移 |
| `biz/reward_context.py` | 全局 `_current` | 显式参数 | B6 | **⏳ 未动**（有理由）：`set_current` 是**每 iter 整体替换**的加载期上下文，与「隐藏可变状态」不同型；改成显式参数要把 ctx 穿过整条 rollout 链 |
| `remote/offline_boot.py` | 3552 行单文件 | 生成式拆分 | B8 | **✅ 已修**：`_offline_boot/*.pyfrag` 分片 + `tools/build_offline_boot.py`，字节一致由 `test_offline_boot_is_generated` 守 |
| `hub/http_face.py` | 55 个 `elif` 路由 + 类属性状态 | `Route` 表 + `HubState` | B7 | **✅ 已修**（2026-11-19）：55 支 `elif` → `GET_ROUTES` / `POST_ROUTES` 两张数据表 + `match_route`（`do_GET`/`do_POST` 各 18 行，§217），五份可变类属性 → `HubState`（§218，第五份 `_REJECT_WARN_AT` 是新守卫抓到的）；守卫 `tests/hub/test_hub_route_table.py` + `test_hub_state.py`。宽捕已从 4 收到 2（§139）；`hub` / `push` / `offline_push` 三个注入的服务面不算状态，不动 |
| `tests/**` | 3962 monkeypatch / 656 私有断言 | seams + 冻结门禁 | B7 前半 | **部分（2026-11-19 复核）**：G5 门禁在位、**140 条 / 46 文件**（原 656 是另一种口径；2026-11-18 连做三十九批 702 → 172；2026-11-19 B7 P1–P4b 再 −1、S3 −5）。**§P0-8 的第 3/4/5 项已落**：`check()`/`FAILS` 脚本壳 → 真 `assert`（9 文件 / 345 处，conftest 守卫与 5 条自证用例退役）、根 conftest 不再改写 stdlib `serve_forever`（66 处改显式 `serve_fast`）、新增 G19 冻结「monkeypatch 打私有名」（现 392 处 / 95 文件）。**仍开放**：monkeypatch 总量 **4 160 处**的迁移（要先铺端口对象，§P0-8 第 1 项）、私有属性赋值 **429 处**、`from x import _name` **71 处** |
| `.github/workflows/nn-training.yml` | 只测 3.12 / `-x` | 版本矩阵 + 去 `-x` | B1 | **✅ 已修**：矩阵含 3.10 下界 + 3.12，`addopts` 去掉 `-x`，`test_python_version_matrix` 钉自洽 |
| `pyproject.toml` | ruff 全局豁免 B023/SIM115；mypy 非 strict | 收回 + 白名单 | B1 | **✅ 已修**：两条全局 ignore 已移除，40 处违规清零（行级精确抑制） |
| `tests/test_python_loc_budget.py` | 只管文件、豁免单文件 | 加函数级 + 生成式拆 | B1/B8 | **✅ 已修**：另有 `test_python_shape_budget.py`（>200 行 / >8 参按文件冻结；**>200 行函数已清零**，此后一律按新增判） |

**复核顺带看到的真·下一批**（都不在上表，按价值排）：

1. **0 个 >200 行函数**（2026-11-19 清零）——形状门禁从此没有存量，任何新长函数直接红。
   **2026-11-18 首刀**：`worker/eval_replays_once._export` 202 → 157（拆出 `_load_games` /
   `_read_replay_ledger`），基线删该文件条目 ⇒ 37 → 36；**第二刀**：
   `trainer/rollout_phase.dispatch_rollout_phase` 207 → 191（拆出 `_stream_extra_kwargs`，
   纯取值段），基线删该文件条目 ⇒ 36 → 35；**第三刀**：
   `hub/offline._post_offline_lease` 211 → 168（拆出 `_offline_claim_refusal`，claim 拒因
   回执段），基线删该文件条目 ⇒ 35 → 34；**第四刀**：
   `remote/worker_server.make_worker_server` 213 → 182（拆出 `_decode_job_body`，POST 体
   解析段），基线删该文件条目 ⇒ 34 → 33；**第五刀**：
   `remote/offline_deliver.OfflineDeliverer.__init__` 218 → 191（拆出 `_init_background_state` /
   `_init_probe_state` 两组状态位），基线删该文件长函数条目 ⇒ 33 → 32；**第六刀**：
   `worker/ppo/engine_paddle.ppo_update` 230 → 181（拆出 `_prepare_demo` / `_build_ref_cache` /
   `_zero_stats`），基线删该文件长函数条目 ⇒ 32 → 31；**第七刀**：
   `remote/run_loop.main` 233 → 86（拆出 `_build_parser`，整张 argv 表），基线删该文件
   条目 ⇒ 31 → 30；**第八刀**：`worker/eval_track.settle_eval_summary` 238 → 180
   （拆出 `_scan_ledger` + `_LedgerTally`），基线删该文件条目 ⇒ 30 → 29；**第九刀**：
   `trainer/queue_local.rescan_nodes` 242 → 184（拆出 `_rescan_stale_code` /
   `_rescan_push_weights`），基线删该文件长函数条目 ⇒ 29 → 28；**第十刀**：
   `hub/store_offline.store_offline_artifact` 243 → 181（拆出 `_decode_offline_artifact` /
   `_write_offline_round` / `_artifact_land_note`），基线删该文件条目 ⇒ 28 → 27；**第十一刀**：
   `worker/bc_eval.dispatch_bc_eval` 244 → 163（拆出 `_build_eval_sources` /
   `_run_bc_eval_one`），基线删该文件长函数条目 ⇒ 27 → 26；**第十二刀**：
   `hub/smoke_loopback.main` 249 → 177（拆出 `_smoke_parser` / `_check_round_wire` /
   `_assert_smoke_final`），基线删该文件条目 ⇒ 26 → 25；**第十三刀**：
   `worker/train/intent_probe.main` 261 → 171（拆出 `_probe_parser` / `_build_report`，
   helper 名模块专属——`_build_parser` 跨第 3 个模块会红同名散落账本），
   基线删该文件长函数条目 ⇒ 25 → 24；**第十四刀**：
   `remote/bc_job._run_bc_job` 263 → 184（拆出 `_check_corpus_lineage` / `_run_bc_echo` /
   `_load_bc_trainer` / `_bc_train_namespace`），基线删该文件长函数条目 ⇒ 24 → 23；
   **第十五刀**：`trainer/loop_serve.serve` 275 → 186（拆出 `_discover_tick` /
   `_publish_heartbeat` / `_finish_serve`），基线删该文件长函数条目 ⇒ 23 → 22；
   **第十六刀**：`remote/plan_run._run_iteration` 276 → 162（拆出 `_round_volume` /
   `_apply_greedy_budget` / `_iter_manifest`），基线删该文件长函数条目 ⇒ 22 → 21；
   **第十七刀**：`trainer/loop_volume._volume_collect_greedy` 286 → 187（拆出
   `_dispatch_greedy_wave` / `_greedy_collected` / `_greedy_plan_games` / `_greedy_scan`），
   基线 2 → 1 ⇒ 21 → 20；**第十八刀**：`hub/boot.main` 327 → 148（拆出 `_hub_parser` /
   `_build_hub_queue`），基线删该文件条目 ⇒ 20 → 19。

   **结构跳过（同日，理由见 `docs/nn/engineering.md` §196）**：`remote/rollout_node.run`
   312、`worker/bc_dispatch.dispatch_bc_corpus` 316、`trainer/loop_volume._volume_collect_continuous`
   327 —— 均为闭包式顺序编排（需先造上下文对象），不做纯搬移；欠账仍在门禁读数里。
   后续追加跳过：`remote/offline_boot.run_one_course` 323（生成式分片 + 顺序编排）、
   `remote/worker.worker_loop` 334（轮询 setup 与 try/finally 主体互引）、
   `remote/notebook_runtime.run_push_worker` 336（两条守候循环各持 6+ 闭包）。

   **第十九刀（tools）**：`tools/lever_scan.main` 204 → 164（拆出 `_print_panel`），
   基线删该文件条目 ⇒ 19 → 18；**第二十刀（tools）**：`tools/tpu-probe.main` 207 → 151
   （拆出 `_tpu_parser`，notebook cell 随之重生成），基线删该文件条目 ⇒ 18 → 17；
   **第二十一刀（tools）**：`tools/spatial-probe.main` 232 → 152（拆出
   `_probe_linear_bufa` / `_probe_region16`），基线删该文件长函数条目 ⇒ 17 → 16；
   **第二十二刀（tools）**：`tools/wave2_judge.main` 270 → 189（拆出 `_load_judge_rows` /
   `_print_health` / `_print_point_deltas` / `_print_tail`），基线删该文件条目 ⇒ 16 → 15；
   **第二十三刀（tools）**：`tools/paired_power.main` 625 → 35（按 §1–§11 报表段拆出
   11 个 `_print_*` helper），基线删该文件条目 ⇒ 15 → 14；**第二十四刀（remote）**：
   `remote/rollout_node.run` 312 → 175（新增 `_Tunnel` / `_Session` 上下文对象，
   原 `_get_postures` / `_open_cloudflared` 闭包与注册 / 重启 / 隧道自愈块搬进方法，
   `run` 保留监护循环骨架与 try/finally 收线），基线删该文件条目 ⇒ 14 → 13；
   **第二十五刀（worker）**：`worker/bc_dispatch.dispatch_bc_corpus` 316 → 185
   （152 行的 `slot_worker` 闭包搬成 `_Corpus.run_slot`，计数改 `_Stats` 字段表，
   `_is_busy_hint` 提为模块级），基线删该文件长函数条目（宽参数条目保留）⇒ 13 → 12；**第二十六刀（trainer）**：
   `trainer/loop_volume._volume_collect_continuous` 327 → 48（新增 `_Continuous` 常量快照，
   循环段与收官段搬进 `_volume_continuous_loop` / `_volume_continuous_settle`），
   基线删该文件条目 ⇒ 12 → 11；**第二十七刀（remote）**：
   `remote/notebook_runtime.run_push_worker` 336 → 99（两条守候分支搬成
   `_wait_existing_serving` / `_wait_started_serving`），基线删该文件条目 ⇒ 11 → 10；
   **第二十八刀（remote）**：`remote/offline_eval.run_cloud_eval` 401 → 182（新增
   `_eval_preflight` / `_EvalRound`，逐局闭包与轮内重投/结算搬成方法），
   基线删该文件条目 ⇒ 10 → 9；**第二十九刀（remote）**：`remote/job_round.run_one_round`
   396 → 75（`_Round`：`start` / `_execute` / `run`），基线删该文件条目 ⇒ 9 → 8；
   **第三十刀（worker）**：`worker/train/bc_paddle.train` 372 → 138（`_Train`：
   `run_epochs` / `finalize`），基线删该文件条目 ⇒ 8 → 7；**第三十一刀（worker）**：
   `worker/train/bc.train` 456 → 145（`_resolve_class_weights` + `_Train`：
   `run_epochs` / `_emit_epoch` / `finalize_bc`），基线删该文件条目 ⇒ 7 → 6；
   **第三十二刀（remote）**：`remote/offline_boot.run_one_course` 323 → 183（生成源
   pyfrag 拆出 `_prepare_roots` / `_choose_pack` / `_probe_hub` / `_deliver`），
   基线删该文件条目 ⇒ 6 → 5；**第三十三刀（remote）**：`remote/hub_client.publish_job`
   418 → 23（按 `# 4)`/`# 5)` 段切三相 `_publish_facts` / `_publish_stage` /
   `_publish_commit`，中间形状 `PublishFacts` / `PublishStaged` 下沉 `publish_spec.py`），
   基线删该文件条目 ⇒ 5 → 4；**第三十四刀（remote）**：`remote/worker.worker_loop`
   334 → 196（while 体未动；壳外四段搬进新模块 `remote/worker_session.py`：
   `hub_plan` / `acquire_retry` / `yield_self_check` / `close_worker`），
   基线该文件计数 2 → 1 ⇒ 4 → 3；**第三十五刀（remote）**：`remote/worker.run_job`
   439 → 134（七段相位搬进新模块 `remote/job_shell.py`：`precheck_job` /
   `land_materials` / `bc_or_none` / `resolve_job_weights` / `run_iter_phase` /
   `check_mode_and_d14` / `echo_smoke_result`），基线删该文件条目 ⇒ 3 → 2；
   **第三十六刀（trainer）**：`trainer/stream.run_rollout_stream` 442 → 112
   （嵌套闭包搬进 `_StreamRun`，状态收进 `_StreamCtx`；PPO RNG 派生留在宿主），
   基线删该文件条目 ⇒ 2 → 1；**第三十七刀（trainer）**：`trainer/loop_remote_job.
   _remote_ppo_publish` 444 → 192（七段相位成本类 `_remote_*` 助手），
   基线清空 ⇒ 1 → **0**。

   **本轮收口（2026-11-19）**：共 37 刀，37 → **0**。逐条结构理由与接法见
   `docs/nn/engineering.md` §201；本批十五刀见 §202–§216。
   **形状门禁已无存量读数**：>200 行函数从此一律按新增判（**2026-11-21 续**：合入上游后又量出
   三处存量 —— `remote/rollout_node.run` 249 / `trainer/eval_dispatch._eval_admit` 248 /
   `worker/train/bc._Train.run_epochs` 201。第三处已拆成 `run_epochs` 63 + `_train_epoch` 79 +
   `_validate` 65（基线条目删除，见 `docs/nn/engineering.md` §252）；`trainer/eval_dispatch._eval_admit`
   248 也拆成 21 + 176 + 47 + 33 四个相位方法（§253）。最后一处 `remote/rollout_node.run`（249）
   也已按所有者拆开：`run` 162（装配 + 三段编排）+ `_open_channel` 50（通道决策，返回生效的
   `hub_url`）+ `_supervise` 80（监护 while + `finally` 收线，所有者 = `_Session`）—— **基线清零**，
   全仓 >200 行函数 0 个（§254）。
2. **G5 剩余 140 条 / 46 文件**（2026-11-19 第十二~十六批拧了 26 条，B7 P1–P4b 再 −1、S3 −5）已全部是结构守卫、状态/构造契约与两处扫描活性锚（详见
   `docs/nn/engineering.md` §172–§178 与本文 G5 行）；**真正没动的另一半是 4 160 处
   `monkeypatch`**（判据没进门禁，G19 只冻结「打私有名」那 392 处）与 **429 处私有属性赋值**
   —— 它们和 G5 是同一类耦合，但要先有「端口对象」才谈得上迁移（B7 后半）；
3. **G3 剩余 123 处 `print`** 里有 `remote/deliver_zip.py`(15) 这种**故意**的 CLI 契约
   （`DELIVER_IMPORT_JSON=` 末行机器可读，加时间戳前缀就坏），别再拿它当欠账。

**2026-11-19 续作（B7 收口 + P0-8 的第 3/4/5 项）**：

4. ~~`hub/http_face.py` 的 55 支 `elif` 路由 + 类属性状态~~ **✅ 已修**（两张数据表 +
   `HubState`，§217/§218）。**发现**：审计表只点了四份可变类属性，新守卫抓到了第五份
   `_REJECT_WARN_AT` —— 「隐藏状态」必须用门禁数，不能靠表的记忆。
5. **下一刀的真实位置（按性价比）**：
   ① ~~`_JobStore` 的调度/租约/账本三簇~~ **P1/P2/P4b 已落（2026-11-19）**：`Scheduler` /
   `LeaseBook` / `Ledger` 三个协作者 + 64 个转发壳，bases 7 → **4**、MRO 9 → **6**；
   `CourseTable` / `WorkerBoard` / `Dispatch`（P3/P4a）也已落地，`_HubQueue` 的 21 个状态
   字段不再住组合类。**`_HubQueue` 壳收敛也已落（S1/S3/S2，2026-11-19）**：`queue_discover`
   并入 `queue_scope`（S1）、5 条私有断言提出 `assert`（S3）、`QueueScope` 切成协作者 +
   26 个同名转发壳（S2）⇒ bases **9 → 7**、MRO **14 → 12**、hub 私有断言 **25 → 19**；
   原稿的 ≤4 / ≤9 / ≤3 400 三个数字按 §9 重估为 ≤7 / ≤12 / ≤4 250（理由与最终读数见
   `docs/nn/engineering.md` §225）；
   ② ~~`common/distribution.py`（1 434 物理行 / 线协议 + HTTP 客户端 + 权重下发 + 升级重启护栏
   四簇）~~ **第十刀已落（2026-11-19）**：升级/重启护栏整簇 12 名 → `remote/node_upgrade.py`
   （1 181 + 282 行），HTTP 漏斗改名公开的 `node_request`，G8 基线不变 + 新增「新家零 IO」断言；
   **第十一刀已落（同日，§220）**：任务协议 → `common/task_client.py`（452 行）、权重传输 →
   `common/weights_client.py`（315 行）⇒ `distribution` **490 行**，只剩传输核心。剩下的两簇有
   `worker/`(L2) 的读者 ⇒ 搬去 `remote/` 会造反向边，所以「§0.4-10 只留纯函数 + 副作用去
   `remote/`」在任务/权重面上到此为止（第三刀的落点是「按职责分文件」，不是再抬一层）；
   ③ 3,962 处 `monkeypatch`（要先有端口对象，与 ① 同一前提；建议先挑一个
   「被 patch 最多」的模块做主例——实测前三是 `offline_boot` 139 / `common.distribution` 106 /
   `iter_rollout` 57）；
   ④ `PPOLoop` 四份实现合一（§0.4-11；**动数值就动训练**，要有真语料与 60-seed 基线，
   不适合与纯搬移混做）。

6. **B8 审计（2026-11-19，同日第三轮）**：B8 的三个交付物现在只剩 **④ `PPOLoop`**。
   两项已落：`common.distribution` 490 行（第十/十一刀）、`offline_boot` 12 段生成物（§118）。
   验收的两半都量过了：① 单文件 < 1000 行——`worker/ppo/` 四个循环的 **LOC**（口径 =
   `tests/test_python_loc_budget.py::loc_of`，不是物理行）为 `engine` **802** · `goal` **374** ·
   `intent` **337** · `engine_paddle` **323** ⇒ 达标；② 克隆窗口（12 行归一窗口，剔空行/注释）
   实测 `goal ↔ intent` **26** · `engine ↔ engine_paddle` **13** · `engine ↔ goal/intent` **0**
   ⇒ **降窗口的下一刀只能落在前两对**。`goal ↔ intent` 的 26 里有 **12 个在模块序言**
   （import/常量/docstring，不是债），**函数内的真重复是 14 个**：循环体 9（`ppo_update_goal`
   ↔ `ppo_update_intent`）+ `main` 5；`engine ↔ engine_paddle` 的 13 个**全部**落在训练步里
   （`_train_step` ↔ paddle 的 `ppo_update`）—— 密度更高，但每一行都在数值路径上。

   **决定：下一刀 = `goal ↔ intent` 合一（numpy 侧 PPOLoop v0）**，理由三条：
   · 同一后端（纯 numpy）且两文件各有独立冒烟用例（`test_ppo_goal` / `test_ppo_intent` /
     `test_ppo_numerics` / `test_ppo_rng_streams`）⇒ 可以用「搬移 + 逐位对账」守住数值；
   · 它的重复不在「拷了一整段」，而在**同一顺序写了两次**：`main`（83/94）· `load_episode_from_shard`
     （20/22）· `load_episodes_*`（17/17）· `build_rl_net`（13/13）与两个循环体（188/175）——
     正是 `PPOLoop` 要抽的「装载 → 前向 → loss 组装 → step → 诊断」五段；把顺序抽成一份
     两块**配置**（`HeadSpec` + `LossTerms`）才能得到计划里说的「四份变四份配置」；
   · `engine ↔ engine_paddle` 那一对是**后端协议**（forward/backward/optimizer_step +
     paddle parity 六条命令），风险高一个量级，放在 `PPOLoop` 接缝被证明之后再做；
     `apply_value_anchor_pull` / `policy_new_rms_of` 的双生也留给那一刀。

   落地形状（防「第 24 个混入」）：新模块只放**顺序**（装载 → 前向 → loss → step → 诊断），
   两端差异全部表达成 `HeadSpec`（几头 / 动作空间 / 掩码语义）+ `LossTerms`（哪几项 / 系数来源）
   数据，循环体里**零 `if` 分叉**；两个旧入口保留为薄适配器（`goal.ppo_update_goal` /
   `intent.ppo_update_intent` 的签名不动，它们有独立 CLI 与测试在打）。
   DoD：① 克隆窗口 `goal ↔ intent` → 0（同一份检测脚本）；② 两侧冒烟用例全绿且
   `test_ppo_numerics` 的逐位/容差判据不变；③ 两个入口签名与 argv 逐字节不变；
   ④ 两个文件 LOC 各降 ≥ 100（14 个窗口 + 重复的 `main`/loader 夹在两者之间）；
   ⑤ 新增一条守卫：循环体里不得出现 `if args.kind == …` 式分叉
   （AST 判据，与 `test_single_ppo_path` 同风格）。

   **第十二刀已落（同日，§221）**：`worker/ppo/loop.py`（276 物理行 / LOC 201）+ 两份 frozen
   dataclass 配置；两个入口只剩 `return run_ppo(...)`。读数：`goal` LOC **374 → 252**、
   `intent` **337 → 226**；克隆窗口按 `tools/audit/py_clones.py` 口径 **11 → 0**、按函数内口径
   **14 → 0**（`main` 以外 9 个清零；剩下的只在 `main` 的 CLI 参数/help 表 —— 当时判为**有意不并**，
   并成配置表不会更短；⚠ 这个取舍已由**第十四刀**推翻，理由见下：行数不是判据，“修一处要改两遍”才是）。DoD ①~⑤ 全中（④ 实测 −122 / −111）；数值面 A/B 逐位（goal 5 例 +
   intent 3 例 + 两边空 chunks = 10 例，含 engage / ref-KL / warmup）。守卫落成
   `tests/worker/test_ppo_loop_core.py`（6 条：核心不知头名 / 入口只转发 / 零克隆窗口 /
   形参表冻结）。门禁 6265 passed。

   **第十三刀已落（同日，§222）**：`engine ↔ engine_paddle` 的 13 个窗口（全部在训练步里）
   → **0**；顺序进 `worker/ppo/backend.py::run_step`，差异进 20 字段 `Backend` 函数表，
   `StepConfig` / `StepState` 装系数与已物化状态。LOC：`engine` 802 → 757、`engine_paddle`
   323 → 282、新模块 138。证明不是“容差变宽松”：`paddle_parity.py` 的 8 个 case 在改动前后
   的 torch 产物**逐字节相同**，Paddle `check` / `verify` 的读数也与改动前**逐位相同**
   （worst fwd 1.73e-06 / stats 1.37e-05 / weights 5.96e-04 / after 9.86e-04）。守
   `tests/worker/test_ppo_backend_parity.py`（6 条）。至此 B8 三个交付物全部到位。

   **第十四刀已落（同日，§223）**：推翻第十二刀「`main` 有意不并」的取舍 —— 理由不是行数，
   而是**同一处修正要改两遍**（`--threads 0` / `--out` 必填 / ref 权重缺省这类都住在那 90 行里）。
   两个 `main()` 的差别实测只有 9 句文案 + 5 个函数 + 4 个数据位 ⇒ 进 frozen dataclass
   `CliSpec`，顺序进 `worker/ppo/cli.py::run`（145 物理行 / LOC 101）；两个头只剩 `_spec()` +
   `main()`（单句 `run_cli(_spec())`）。LOC：`goal` 252 → **202**、`intent` 226 → **165**；
   `goal ↔ intent` 函数内窗口 **1 → 0**（最后那个就在 `main`）。证明：`-m worker.ppo.<head> --help`
   切分前后 **逐字节相同**；`tmp/ab_cli.py` 把 I/O 打桩后跑 5 个场景 × 2 个头，
   **10/10 逐事件相同**（init / update / +kl / +kl+ref / 全参数改）。守
   `tests/worker/test_ppo_cli_core.py`（6 条：参数顺序与 help 文案冻结 / 默认值冻结 / `main` 只能
   是单句 `run_cli(_spec())` 且两个文件不得再有 `add_argument`）。账本：`any_baseline` +3、
   `remote_dag` 登记 `worker.ppo.cli` L3、`assert_baseline` 删 goal/intent 两条（改成显式 raise）、
   源文本 1173 → 1174、复用账本 exact 14 → 15 / clone 15 → 16（两格都是目标状态本身，理由
   写在 `tests/test_reuse_ledger.py`）。门禁 6278 passed。

7. **2026-11-19 收口后的剩余清单（逐项量过；都是多刀程序，不在单批内完成）**：
   * **P1-1 导入体系**：`sys.path` hack 仍是 409 处（生产 36 / tests 317 / e2e 18 / tools 36），
     顶层包名仍是 `common` / `worker` / `tools` / `train` / `hub`。方案里的 src layout 是
     一次全仓重命名，必须单独排期；**没有做**，也没有为了数字删测试里的自举（删了会让
     `python tests/xxx.py` 单跑失效，而 §P0-8 的脚本壳刚清完，不能再造第二种入口语义）。
   * **P0-8 第 1/2 项（端口对象 / 全量 monkeypatch 迁移）**：4 160 处 `monkeypatch`、
     429 处私有属性赋值、71 处 `from x import _name`。G19 已冻结「打私有名」的 392 处
     （只减不增）；**主例已落（2026-11-21，§258/§259）**：`worker/iter_rollout` 的六处接缝收进
     `NodeExec` 端口，五个测试文件的 42 处 `setattr(iter_rollout, …)` → 26 条 `stub_exec`；
     流式采集 e2e 的 4 处 `_run_one_game_bounded` 假局也搬进 `run_game` 端口字段。G19 406 → 392。
     剩下的仍是逐模块工程，照该形状先铺端口再收测试。
   * **P1-4 类型化**：`Any` 1178 处 / 181 文件（G14 冻结）；mypy 仍是非 strict。
     批量换 `object` 已实测否决（406 个红点），只能逐个数据边界换成 TypedDict / 协议。
   * **P0-3 参数爆炸**：仍有 **40 个函数 >8 参数 / 32 文件**（2026-11-22 实测；形状门禁按文件
     冻结）。`dispatch_bc_corpus`(25) → `BcDispatchSpec`（§286，62 → 61）；
     `dispatch_rollout_phase`(25) → `RolloutPhaseSpec`（§289，61 → 60）；
     `run_rollout_stream`(22) → `StreamRunSpec`（§290，60 → 59）；
     `settle_eval_summary`(21) → `EvalSettleSpec`（§291，59 → 58）；
     `worker_loop`(22) → `WorkerLoopSpec`（§292，58 → 57）；
     `run_one_round`(21) → `JobRoundSpec`（§293，57 → 56）；
     `run_rollout_queue` / `RolloutDispatcher` → `DispatchSpec`（§294，56 → 54）；
     `OfflineDeliverer.__init__`(20) → `OfflineDelivererSpec`（§295，54 → 53）；
     `trainer/loop_serve.serve`(18) → `ServeSpec`（§296，53 → 52）；
     `worker/ppo/engine_paddle.ppo_update`(21) + `worker/ppo/engine.ppo_update`(20) →
     `PaddleUpdateSpec` / `TorchUpdateSpec`（共用 `BaseUpdateSpec`，**同刀**：两侧是同一调用面，
     `train_core` 用同一组 kwargs 分派 —— 只收一侧就是给分派点造一个分支；端口 `update(...)`
     因 goal / intent 的 `update_kwargs` 不同仍保留 5 位置 + `**kwargs`）（§297，52 → 50，
     43 → 41）；
     `BatchEvalRunner.__init__`(16) + `dispatch_batch_bg`(13) → 共收 `RunnerSpec`（**同刀**：
     派发口原按**位置**把 13 个实参转手给 `__init__`）（§298，50 → 48，41 → 40）；
     `remote/worker.run_job`(17) → `RunJobSpec`（spec 下沉 `remote/job_spec.py`：`job_round` 不许
     import `remote.worker`）（§299，48 → 47，40 → 39）；
     `common/task_key.task_key`(16) → `TaskKeySpec`（同模块；9 个同型开关由位置位改按名）（§300，
     47 → 46，39 → 38）；
     `worker/ppo/loop.run_ppo`(15) → `PPOSpec`（推翻 B8 ④「签名即契约」：契约在入口 +
     新守卫的字段表上；`model`/`opt` 改 Protocol 消除 `object` 就地 cast）（§301，46 → 45，38 → 37）；
     `worker/plan.build_plan`(14) → `PlanSpec`（同模块；导出期手抄的 13 个旋钮改按名，
     自检掩护不了「计划本身按错旋钮组装」）（§302，45 → 44，37 → 36）；
     `remote/offline_eval.run_cloud_eval`(14) → `CloudEvalSpec`（同模块；顺带杀掉生产那条
     「装配点造关键字 `dict` → `run_cloud_eval(**params)`」的路，`Any` 8 → 6）（§303，44 → 43，36 → 35）；
     **A 批（三处同刀）**：`worker/ppo/goal.ppo_update_goal`(13) · `intent.ppo_update_intent`(13) →
     `HeadUpdateSpec`（两个入口的 13 个形参逐字相同，差别只在 `head`/`terms` ⇒ 一份 spec；
     `to_ppo` 成为「15 关键字逐个搬」的唯一实现）、`worker/ppo/np_core.load_episodes_common`(13) →
     `LoadEpisodesSpec`。两头入口的**端口**仍是 5 位置 + `**update_kwargs`（stream / `REQUIRED_UPDATE_KWARGS`
     建在它上面），但     `update` 改成一层薄适配器：未知旋钮**构造 spec 时**当场 TypeError（§304，43 → 40，35 → 32）。
     **B 批（六处同刀，hub 推送/派发）**：`hub/offline_push.OfflinePushDispatcher.__init__`(12) →
     `OfflinePushSpec`、同文件 `_settle_assignment`(8) 的四个同名开关 → `SettlePlan`（写读同源：
     `ledger_fields()` / `from_ledger()` 是账本四列的唯一实现）、`remote/push_dispatch` 的
     `PushDispatcher.__init__`(11) → `PushDispatchSpec` 与 `_await_result`(8 位置) → `JobLeg`
     （顺手删死参 `rec`）、`remote/push_client.submit_job`(8) → `(base_url, token, JobUpload)`
     （端点**不收**：同一条腿的 `cancel_job`/`wait_result`/`poll_result` 都是 `(url, token)` 开头）、
     `remote/push_register.Registry.__init__`(9) → `RegistrySpec` ⇒ 全仓 40 → **34**、32 → **28**
     文件（§305；守 `tests/hub/test_push_spec.py`）。
     **C 批（同一把刀的第三批，BC 链四入口）**：`remote/bc_job._run_bc_job`(12) → `BcJobSpec`
     （住 L1 的 `remote/job_spec`：装配点 `job_shell`(L4) 不许 import `bc_job`(L4)，否则成环）·
     `trainer/bc_loop.publish_bc_job`(12) → `PublishBcSpec` · `worker/bc_eval.dispatch_bc_eval`(12) →
     `BcEvalSpec` · `remote/colab_bc.run_training`(**10 位置参数**) → `TrainRun` ⇒ 全仓 34 → **30**、
     28 → **24** 文件（§306；守 `tests/remote/test_bc_job_spec.py` / `tests/trainer/test_publish_bc_spec.py` /
     `tests/worker/test_bc_eval_spec.py`）。
     **下一批见下面「P0-3 剩余宽签名：分文件分批待办」（D2c、E、F）** —— C2 已落（§319）。
     `make_loaders` / `archive_course` / `collect_corpus` 与 `bc_ingest` 三兄弟
     已收成 spec（§273）；`load_ppo_corpus` / `ingest_offline_artifact` /
     `resumed_manifests` 紧随其后（§274）。审计的「124」是 2026-11-19 上游合入前的读数，合入带进了新的宽签名）。
     认领族已收成 `LeaseSpec`（§261，122 → 117）；下载三兄弟 + 补传三装配已收成
     `BlobAsk` / `WeightsAsk` / `CodeAsk` / `RoundBody` / `ResultBody` / `ArtifactBody`
     （§262，117 → 109）；outbox 构造器 → `OutboxSpec`（§263，109 → 108）；
     `deliver_proc` 的构造器与工厂 → `DelivererSpec` / `FactorySpec`（§264，108 → 106）；
     `http._request` / `_get_with_retry` → `WireRequest` / `WireGet`（§265，106 → 104，
     传输族收口）；`worker/events` 的两个落账入口 → `KickstartBurn` / `GateVerdict`
     （§266，104 → 102）；`plan_topup` → `TopUpSpec`（§267，102 → 101）；`quota_sample_priority` → `QuotaSampleSpec`（§268，101 → 100）；`opt_standard` 的递归对 `_base_tar_bytes` / `_landed_bytes_for` → `BaseTarSpec` / `LandedBytesSpec`（§269，100 → 98）；`pack_payload_zip` → `PackSpec`（§270，98 → 97）；`build_task_zip` → `TaskZipSpec`（§271，97 → 96）；`reap_alloc_timeouts` → `ReapSpec`（§272，96 → 95）。下一批按形状门禁基线挑下一个模块。
     ~~最大的 `worker/eval_arbiter.EvalRound.__init__` 41 参~~ **已收（2026-11-20，§229）**：
     40 个关键字参数 → `RoundSpec`（装配点 `trainer/eval_dispatch._round_spec`），
     顺手把同文件的 `EvalRound.settle_task`（11 参，含死参 `wall_sec`）→ `SettleOutcome`
     ⇒ 该文件宽签名 **2 → 0**、全仓 132 → **130**。下一批按同一手法（先有装配点，再收签名）：
     ~~`remote/plan_handoff.open_run_context` 35、`remote/run_loop.run_standalone` 29~~
     **已收（2026-11-20，§231）**：一个 `RunSetup` 旋钮袋 + `_require_run_identity` ⇒ 全仓 129 → 125；剩下的下一代：`hub/queue_claims` 的 lease 族（`LeaseSpec`）。
     **2026-11-20 复核（下一个接手的人先看这三条）**：
     ① ~~`open_run_context` / `run_standalone` 是**带缺省值的旋钮袋**（云机 A 层 eval / 放大 /
     轮换 / 补传 / outbox 各一簇，调用方只传子集）⇒ 不能照抄 `RoundSpec`：先定「怎么表达
     只覆盖几个旋钮」~~
     **已收（2026-11-20，§231）**，答案是**旋钮袋**：除身份位外全部带缺省，入口用
     `dataclasses.replace(setup, …)` 覆盖自己那几簇（`replace` 就是「只覆盖几个旋钮」的语义，
     再包一层 builder 只是换个 API 名）⇒ 调用点不必为了传三个参数造一整个对象。
     ~~②     `trainer/batch_runner._settle_unit`（15 参）卡在 LOC 999/1000~~
     **已收（2026-11-20，§230）**：先把通道机器整块拆成 `trainer/batch_lanes.py`
     （`_UnitLanes` / `_UnitPlan` / 两个背压常量；`batch_runner.py` LOC 999 → 336），再让
     `_settle_unit(self, ledger)` 只收一个 `UnitLedger`（frozen + kw_only，装配点
     `_UnitLanes.ledger(threads=…)`）⇒ 宽签名 130 → **129**。原猜的 ③ 成立且已兑现：守卫按
     「`UnitLedger` 的字段集 + 只收一个对象」重锚（`LEDGER_FIELDS` + `_settle()`
     构造 `UnitLedger`），没有删任何一条判据；顺带发现**同族陷阱第 4 种形态 = 换模块**（patch 锚点、
     入边归属者表、行构造守卫、`GATE_SITES` 都要跟着换家，否则静默空转）。
   * **P0-3 剩余宽签名：分文件分批待办（2026-11-23 实测 **0 个 / 0 文件（清空）**；A 批 ⇒ §304、B 批 ⇒ §305、C 批 ⇒ §306、D1 ⇒ §307、G 批 ⇒ §308、H1 ⇒ §309、H2 ⇒ §310、G2 ⇒ §311、D2 ⇒ §312–§314、F1 ⇒ §315、F2 ⇒ §316、F3 ⇒ §317、F4 ⇒ §318、C2 ⇒ §319、F 的 `lifecycle` ⇒ §320、E 批三处 ⇒ §321、F 的 `start_fill` ⇒ §322、E 的 `_run_one_game_bounded` ⇒ §323；**发布腿搬运（前置，不消签名）⇒ §324** ** —— `remote/hub_client` 的发布段（`_publish_*` / `publish_job` / `PublishSpec` 族）整块搬去新模块 `remote/hub_publish`（L2，不留门面），`hub_client` LOC **999 → 651** ⇒ 两条 `verify_and_land`(9) 与 `merge_memory_shards`(9) 的「先拆再收」解堵，下一刀从这里开工；**批 I ⇒ §325**（`gate_check.evaluate`(9) → `GateAsk`，60 处调用点）；**批 J ⇒ §326**（`run_iter_rollout`(9) → `IterRunSpec`，57 处调用点）；**批 K ⇒ §327**（`verify_and_land` / `verify_and_land_bc` / `merge_memory_shards` → `LandSpec` / `BcLandSpec` / `ShardMergeSpec`；三条都是 §307 记的「先拆再收」，§324 拆完才收得动）；**分诊第六格 ⇒ §339**（`trainer/loop_volume._volume_collect_greedy` 187 → 85 + 预算 / 取局 / 收官三段，14 组决策表差分全同）；**分诊第七格 ⇒ §340**（`worker/bc_eval.dispatch_bc_eval` 186 → 84 + 单图源助手三段，12 组决策表差分全同，存量 62 → 61）

     按**同一装配面 + 同一层**分批，每批 = 一把刀（spec 住同模块或就近轻模块 + 删
     `WIDE_FUNCS_BASELINE` 条目 + 一份字段表守卫 + `engineering.md` 一节 + `DECISIONS` 一条）。
     批内先挑「位置参数多」或「生产调用点多」的那个开头。

     | 批次 | 文件 : 函数（参） | 闸前先看 |
     |---|---|---|
     | ~~**A · PPO 两入口**~~ **已完成（§304，43 → 40）** | ~~`ppo_update_goal` / `ppo_update_intent` / `load_episodes_common`~~ | 实测：入口形参表被 `test_ppo_loop_core` 钉着 ⇒ 契约改钉 `HeadUpdateSpec` 字段表；端口保留 `**update_kwargs`，错键在 `from_port` 构造时爆 |
     | ~~**B · hub 推送/派发**~~ **已完成（§305，40 → 34）** | ~~`hub/offline_push.__init__`(12) · `_settle_assignment`(8→`SettlePlan`) · `remote/push_dispatch.__init__`(11) · `_await_result`(8→`JobLeg`，顺手删死参 `rec`) · `remote/push_client.submit_job`(8→`(url, token, JobUpload)`) · `remote/push_register.Registry.__init__`(9→`RegistrySpec`)~~ | 实测：`_settle_assignment` 收的是那**四个同名开关**（`SettlePlan`，写读同源），不是前四项位置；`submit_job` **端点不收**（同腿四处端点一个形状）；守 `tests/hub/test_push_spec.py` |
     | ~~**C1 · BC 链四入口**~~ **已完成（§306，34 → 30）** | ~~`remote/bc_job._run_bc_job`(12) · `trainer/bc_loop.publish_bc_job`(12) · `worker/bc_eval.dispatch_bc_eval`(12) · `remote/colab_bc.run_training`(10 位置参数)~~ | 实测：`BcJobSpec` 必须住 L1 的 `remote/job_spec` —— 装配点 `job_shell`(L4) 不许 import `bc_job`(L4)；`run_training` 十个位置参数全是 str/Path/int ⇒ 最危险的反而是它；守 `tests/remote/test_bc_job_spec.py` · `tests/trainer/test_publish_bc_spec.py` · `tests/worker/test_bc_eval_spec.py` |
     | ~~**C2 · 学生网**~~ **已完成（§319，13 → 11）** | ~~`worker/models/student.__init__`(10) · `worker/models/student_paddle.__init__`(10)~~ | 实测：**同形双生**（torch / paddle）⇒ 同刀共收一份 `NetSpec`（住 `worker/models/arch_meta.py`），**六个子类**（`PPOStudent` / `GoalNet` / `IntentNet` / `GoalRLNet` / `IntentRLNet` / `IntentProbeNet`）一并只收 spec，`read_arch_kwargs` 改成返回 spec 的 `read_arch_spec`；120 处调用点（含工具/用例）按 AST 改写；守 `tests/worker/test_net_spec.py` | 原估：22 处调用多是 `StudentNet()` / `StudentNet(h=16, d=2)`，先定「第 9 个参数怎么并」（9 个 kw 缺省），再决定 spec 是「架构 + 旋钮袋」还是两段 |
     | ~~**D1 · 一轮的两份输入**~~ **已完成（§307，30 → 28）** | ~~`worker/greedy_round.run_greedy_round`(12) · `trainer/loop_transport._push_job_round`(10)~~ | 实测：坏点是**相邻同类位**（`it`/`rotate_seed`/`round_target` 三个 int、`manifest`/`jid`、两个 bytes）；`merge_memory_shards` 同批改通但**撞 `remote/hub_client.py` LOC 999/1000** ⇒ 已回滚，按 §230 先拆 `hub_client` 的发布侧合并段；守 `tests/worker/test_greedy_round_spec.py` · `tests/trainer/test_push_round_spec.py` |
     | ~~**D2a · `LaneSpec` + `BreakerSpec`**~~ **已完成（§312 · §313，21 → 19）** | ~~`trainer/queue_local.__init__`(12) · `worker/breaker.breaker_update`(12)~~ | 实测：`LaneSpec` 只是一份共享账（`local_cap`/`last_remote_ok`）+ 两个 Event；`breaker_update` 是**五条相邻阈值分属 KL/ENT 两族**（19 处调用，生产 2 + 用例 17）；守 `tests/trainer/test_lane_spec.py` · `tests/worker/test_breaker_spec.py` |
     | ~~**D2b · `RolloutCmdSpec`**~~ **已完成（§314，19 → 18）** | ~~`worker/cmd.build_rollout_cmd`(10，15 调用)~~ | 实测：`stage`/`seed`、`weights`/`out_dir`、`wver`/`node_label`/`code_sha` 三对相邻；改写要防「行内注释吞掉下一个关键字」；`args: Any` 进 `any_baseline`（同族先例）；守 `tests/worker/test_rollout_cmd_spec.py` |
     | **D2c · 训练循环剩两处** | `worker/gate_check.evaluate`(9，**61 处调用**，5 个位置参数) · `worker/resume.merge_memory_shards`(9，**被 LOC 卡住**：先拆 `hub_client` 再收) | `evaluate` 的调用面最大 ⇒ 先按「传了几个位置参数」分桶（只传 `course`/`trend_rows` 的占多数），再决定是整份 spec 还是保位置面 `evaluate(course, rows, *, GateEvalSpec)` |
     | **E · rollout / job 腿** | ~~`worker/iter_rollout._run_one_game_bounded`(11)~~ **已完成（§323，6 → 5）** · `worker/iter_rollout.run_iter_rollout`(9) · ~~`remote/job_round._prefetch_fill`(10)~~ **已完成（§321，10 → 7）** · ~~`remote/rollout_node._banner_lines`(10)~~ **已完成（§315，F1）** · ~~`remote/rollout_node._try_register_once`(9)~~ **已完成（§321）** · ~~`remote/node_upgrade.request_upgrade_guarded`(9)~~ **已完成（§321）** | `run_iter_rollout` 的 9 参里前几个是 `args` 派生值（与 `PlanSpec` 同类：先看能不能直接复用 `RunSetup`）；`_run_one_game_bounded` 已有 `OneGameSpec`/`RetryGameSpec` 先例（§279），先查是否只是没接上；§321 实测：三处收 spec 后**装配点全在调用方**（`payload_worker._fill_once` / `job_round.run_one_round` 的预取线程 / `RolloutNode._try_register` / `upgrade_stale_nodes` 的扫描分支），用例里的 `**kw` 打桩与 `threading.Thread(args=…)` 是主要残留面 |
     | **F · 传输 / lifecycle** | ~~`remote/lifecycle.__init__`(11)~~ **已完成（§320，11 → 10）** · ~~`remote/error_report.report_error`(11)~~ **已完成（§317，16 → 15）** · `remote/hub_client.verify_and_land`(9) · `remote/hub_client.verify_and_land_bc`(9) · ~~`remote/hub_http.report_job_failure`(9) · `remote/hub_http.wait_job`(9)~~ **已完成（§318，15 → 13）** · ~~`remote/payload_proc.start_fill`(9)~~ **已完成（§322，7 → 6）** · ~~`remote/tailscale_boot.fetch_guarded`(9)~~ **已完成（§316，17 → 16）** · ~~`remote/rollout_node._try_register_once`(9)~~ **已完成（§321）** | `error_report` 6 个生产调用点（已收，§317）；`hub_http` 两处已收（§318：4 个相邻 float 全以秒计、`now`/`log` 两个回调互换，都不抛只错位；重入一并改 `return wait_job(spec)`；`report_job_failure` 无生产调用者 = 与 `job_lifecycle` 重复实现，删除候选） |
     | ~~**G1 · 回传腿两个入口**~~ **已完成（§308，28 → 26）** | ~~`remote/upload_proc.__init__`(11) · `remote/upload_proc.make_uploader`(10)~~ | 实测：两个入口参数表**逐字相同** ⇒ 共用一份 `UploadSpec`（不像 §264 那样给两份）；`mode=""` 的两种解释保留（进程入口 = `process`、工厂 = 缺省 async）；守 `tests/remote/test_upload_spec.py` |
     | ~~**G2 · 导出面**~~ **已完成（§311，23 → 21）** | ~~`remote/bundle.export_bundle`(10) · `remote/bundle.write_export_status`(10)~~ | 实测：两份表**不同**（各给一份 spec，`BundleSpec` / `ExportStatusSpec`）；坏点是三条相邻路径（代码包/TS 树/动量）与「进度对 + ok/running」；11 处调用点改写要防 `_remote_export_bundle(` 同名后缀、内联 dict 切分、`bundle_mod.` 前缀；守 `tests/remote/test_bundle_spec.py` |
     | ~~**H1 · 工具面报告双生**~~ **已完成（§309，26 → 24）** | ~~`tools/p0_auc_probe.build_report`(11) · `tools/c9_critic_calibration.build_report`(10)~~ | 实测：**同名不代表同形** —— 字段名故意不同（`seed`/`boot` vs `boot_seed`/`n_boot`）、`games` 类型也不同 ⇒ **各给一份** spec，共用一份 `ReportSpec` 已否决；守 `tests/tools/test_report_specs.py` |
     | ~~**H2 · 工具面剩一处**~~ **已完成（§310，24 → 23）** | ~~`tools/tpu-probe.bench_case`(9)~~ | 实测：坏点是 `ref_model`/`ref_cache` **互斥对**（互换 ⇒「ref 收益」测出负号）+ 三个 int 相邻；改完要跑 `tools/sync_tpu_probe_nb.py` 重生成 notebook 内嵌副本；守卫装载搬进 `tests/tools/probe_loader.py`（口径 B 会把「读 `.py` 的函数」里所有 assert 记成源码断言）；守 `tests/tools/test_tpu_probe_bench_case.py` |

     记账/收尾同一套：`docs/nn/engineering.md` 新节 + `DECISIONS.md` 新条目（含被否决项）+ 
     `docs/nn.progress.md` 计数 +1 + 本节读数降格 + `.workbuddy/memory` 一行。

   * **P0-5 剩余全局状态**：`common/**` 已清零（`L0_MUTABLE_ALLOWED = {}`）：`log` / `fs` /
     `weights_ledger` 对象化（§234），`net_http` 的两个 opener 换成 `functools.cache`（B8），
     `distribution` 的升级/重启簇搬去 `remote/node_upgrade.py` 且锁存改成 `BranchLatch`（§235）。
     L2 的两个单例也收进 `Cell`（§236），`hub/http_face` 的四个类属性 2026-11-19 已进 `HubState`。
     **P0-5 表内已无剩余**：按裁定保留的只有 `biz/reward_context._current`（每 iter 整体替换的
     加载期上下文 + 2 条 `global`）。`common/**` 之外的 15 处同类缓存/节流表里，连接池（§238）、
     活 link 登记（§239）、warn-once 集合（§240）、resume 扫描缓存（§241）已收，其余 10 处清单
     见 §P0-5 末小节（G2 不覆盖 L2/L3，属独立一轮）。
   * **B5 可选项**：`cfg`/`course` 的 schema（`biz/course_spec.py` 已是 pydantic；
     `load_dist_config` 仍返回裸 dict）。`P1-5` 的 notebook 侧 26 处裸 dict CFG 同理
     （读者在 cell 里，门禁扫不到）。
   * **`trainer/loop_core.py`**：12 类混入 → 4（已落），再往下要像 B7 一样把编排层状态
     搬成协作者 —— 与 B7 同量级，且 `TrainingLoop` 是整条训练链的宿主，风险更高。
   * **P1-12 热路径**：两处已落（`compute_gae` 向量化 2.2–4.3×、`append_jsonl` 去写者线程
     4.0×），剩余两条是观察项（`ppo_update` 的诊断 list 受 job 时长约束；
     `common.distribution` 的 import 成本未测），**没有未完成的改造**。
   * **P1-11 兼容壳**：`trainer/dispatch_queue.py` 只剩 `RUN_ID` 等**契约名**（消费方仍在用），
     转发名由 `test_forward_names_are_live` 逐名判活 —— 活着的名是契约，不是欠账。
   * **G3 `print` 123 处 / 38 文件**、**G9 26 处 A 类静默**、**G4/G6/G13/G14 的冻结存量**：
     均已逐条定性（附录 C / 各门禁行），**有意不再动**，不是欠账。

## 附录 C：G9 剩余 29 处的归类（2026-11-15，逐条）

> G9 门禁（`tests/test_silent_except_budget.py`）冻结的口径是「`except Exception/BaseException/裸 except`
> 且**体内只有一个 `pass`**」。经过十三批清理后剩 **29 处 / 22 文件**。
> 本节把每一处**定性**，好让下一个人不用从头再审一遍 —— 也好看清「还剩什么值得动」。
>
> 三种归类：
> **A 契约静默** = 静默就是契约，改它会引入更大的风险；
> **B 漏网** = 宽捕把「我们的 bug」和「世界不合作」混在一起，值得收窄；
> **C 已留痕** = 同一失败路径上别处已经会说（此处再喊就是重复）。
>
> 结论（2026-11-15 收盘）：**A 26 处（不再动）· B 3 处已全部收窄 · C 0 处**。
> ⚠ 收窄后 G9 读数 **29 → 26 处 / 19 文件**，G4 **397 → 394**（三处 `except Exception` 变成具名 tuple）。

### A 类（26 处）：静默就是契约

| 位置 | 吞掉的是什么 | 为什么静默是对的 |
|------|--------------|------------------|
| `common/log.py:233` / `:238` / `:245` | 某个 sink 的 `write` / `on_line` 回调 / `flush` | **日志层不能因为日志失败而失败**，而且在这里「报告失败」又没有别的通道（只能再走日志 ⇒ 递归）。这类失败的可见性由**产物侧**承担：文件缺行 / 磁盘满在别处会炸。三处都在 `common/log.py::Tee`（同时写控制台 + 文件），一路死掉不该拖死其余路 |
| `remote/cloud_pool.py:77` | `self._log(...)` | 同上（池的日志钩子可能是坏掉的 stdout）。它包的是「说一句话」，不是说这句话的内容 |
| `remote/error_report.py:163` | 往云机日志写「错误上报未送达」 | 观测腿的最后一站：**这条日志本身就是尽力而为**，再包一层错误处理只能静默 |
| `remote/offline_deliver.py:631` | 补传后台线程的日志写不出去 | `_guard` 的契约定死：「后台腿不得因任何异常死掉」（含 `BaseException`）。日志失败 ≠ 线程该死 |
| `common/distribution.py:502` | `r.close()`（abort 后在守护线程里尽力关） | 关不掉就是关不掉，没有调用方在等它；目标对象类型不保证有 `close` |
| `remote/offline_push_client.py:215` | `resp.close()`（`finally`） | 已在 `finally`，成功/失败路径都要走；再抛只会把已有结论替换成一个「关连接失败」 |
| `remote/child_proc.py:95` / `:106` | `proc.stdin.close()` / 级联最后一层的 `proc.kill()` | **进程收尾的兜底级联**：`close→wait→terminate→wait→kill` 每一步都只在前一步失败时执行，最后一层失败意味着进程已经不受控 —— 没有可做的事，也不该把收尾异常抛回调用方 |
| `remote/deliver_proc.py:395` / `:407` | 同上（`_stop_child`） | 同上；且 stdin EOF 是**第二条「父进程收线了」通道**，关不掉不影响主通道 |
| `remote/rollout_node.py:255` | `p.kill()`（`terminate` 之后） | 同上 |
| `remote/worker_proc.py:131` | `proc.kill()`（收到 Ctrl-C 后） | 紧跟 `raise`：异常本来就要往上走，kill 失败不该改写成另一个异常 |
| `remote/child_proc.py:142` | 读流被截断（`for s in iter(readline, …)` 外层） | 「管道关了」是**正常收线**（EOF 的回声），不是错误。回调自己抛的异常在第一/七批已经单独兜住并报数（`dropped` / `first`） |
| `remote/deliver_worker.py:131` | `_watch_stdin` 里 `stream.read()` 的外层 | 父进程消失检测：读到 EOF/坏管道 ⇒ `break` ⇒ `parent_gone.set()`，**结论本身就是「父没了」** |
| `remote/bulk_sched.py:212` | `_notify_p1` 的观察者回调 | docstring 已定死：「观察者自己的异常一律吞掉，它是观测/协同面」；P1 的真实状态由 `PayloadProcess` 响亮地说 |
| `worker/eval_gate.py:123` | `_serve` 里 `job()` 抛出的异常 | 派发器本身已自带兜底；这里只保证「不变成未捕获线程异常」。**收窄它没有收益**：任务异常的语义由派发器那层定义 |
| `remote/push_bootstrap.py:245` | 每 0.5s 一次的 `/ping` 探测 | 探测循环里「这次不通」是**下一次迭代的输入**，不是错误；`wait_ping` 的返回值已经表达结论 |
| `remote/push_register.py:194` | 读 HTTPError 的响应体（拼诊断信息） | 判据是 `e.code`，响应体只是让日志更具体；读不到就少一段细节，**不改判定** |
| `remote/payload_worker.py:218` / `remote/upload_worker.py:106` | `stream.reconfigure(encoding="utf-8")` | Windows 控制台不接受时退化成运行环境缺省；协议行是纯 ASCII，**不影响正确性**（`getattr` 那步已经处理没有该方法的情形） |
| `trainer/loop_transport.py:86` | 读分发配置（`cf_protocol` / `cf_edge_ip`） | 既有契约测试钉死：「配置读不到 ⇒ 用缺省隧道参数」（`test_config_read_failure_is_silent` 等三条）。**改它会破契约** |
| `worker/ppo/np_core.py:301` / `:307` / `:312` | `torch_xla` 的四个版本相关属性 / 复制设备数 / 设备属性串 | 这些都是**能力探测**：不同 torch_xla 版本返回不同集合，缺项进 `out` 就是「这台机器没有这个信息」。探测函数本身返回 dict，缺 key 已是显式结论 |

### B 类（3 处，**已于 2026-11-15 全部收窄**）：宽捕把「我们的 bug」和「世界不合作」混在一起

| 位置 | 改成 | 用例（`程序员错误必须穿出`） |
|------|------|-----------------------------|
| `remote/push_client._probe_sha` | 模块级 `PROBE_ERRORS = (*NET_ERRORS, ValueError)`；200 但 body 非 JSON **照抛** | `tests/remote/test_push_probe_errors.py`（5 条：三态 / 非 200 / 网络错 / 非 JSON 抛 / TypeError 抛） |
| `remote/worker.run_job` 结果复用 | `except (OSError, ValueError, ProtocolError)` | `tests/remote/test_worker_bun_precheck.py` +3（坏 JSON 与校验失败照旧重算 / 校验器 `TypeError` 穿出且**不下 payload**） |
| `trainer/bc_loop.append_weights_md_row` | `except (OSError, subprocess.SubprocessError)` | `tests/trainer/test_weights_md_sha.py`（4 条：正常记 sha / git 缺失与超时 → `n/a` / 参数拼错 `TypeError` 抛出） |

撤回去（`except Exception: pass`）实测 4 条新用例红：非 JSON 被读成「问不到」、`TypeError` 被吞、
`TypeError` 记成 `n/a`、校验器崩了被当成缓存坏。

> **附带的测试纪律**：`LogRouter.warn_once` 的去重是**进程级**的（同一 key 只喊一次）。
> 两个用例共用一个 key 时，「喊了几次」取决于谁先跑 —— forkdist 分片一变就随机红。
> 断言「只喊一次」的用例必须注入**同一个** `LogRouter()` 实例（`monkeypatch.setattr(模块, "default_router", lambda: fresh)`），
> 不要每次 new 一个（那等于把去重关掉，断言会变成「喊了几次就是几次」）：
> `tests/trainer/test_loop_heartbeat_visibility.py::test_batch_heartbeat_reports_failure_once`。

（下表保留原始诊断，作为「为什么当时算漏网」的记录。）

| 位置 | 现状 | 为什么算漏网 | 收窄方向 |
|------|------|--------------|----------|
| `remote/push_client.py:58` (`_probe_sha`) | `except Exception: pass` ⇒ 返回 `None`（「问不到」） | 与第十一批收窄的 `common/distribution` 探针**同形**：200 但 body 不是 JSON（`json.loads` 抛 `ValueError`）是**服务端契约变了**，现在被读成「问不到」⇒ 调用方重复上传，真因从不出现 | `NET_ERRORS` + `ValueError`（同 `PING_ERRORS` 的做法） |
| `remote/worker.py:569` (`run_job` 结果复用) | `json.loads` + `validate_result` 的任何异常 ⇒ 当作缓存坏了，清场重算 | 校验器自己的 bug 会被读成「缓存损坏」⇒ 静默重算一次 PPO（几十分钟 + 权重不同）。缓存坏是 `(OSError, ValueError)`，跨 manifest 是校验器的**具名**异常 | 窄捕到 `(OSError, ValueError, ProtocolError)`；其余照抛 |
| `trainer/bc_loop.py:193` (`append_weights_md_row`) | `git rev-parse --short HEAD` 的任何异常 ⇒ sha 记为 `"n/a"` | 写进表格的 `"n/a"` 是**显式**的（不算静默丢信息），但宽捕让「git 没装 / 超时 / 子进程起不来」与「我们拼错了命令行」同形；而这一行是模型档案的**溯源列** | 窄捕到 `(OSError, subprocess.SubprocessError)`；真的取不到仍写 `"n/a"` |

### 处置建议（按性价比排序）

1. ~~**B 三处**~~：已完成（见上表）。
2. **A 类不再动**：它们要么是递归风险（日志层），要么是级联的最后一级（进程收尾），要么返回值已经表达结论
   （探针 / 探测 / 配置缺省）。给它们加日志 = 在不可能再失败的地方加一条可能失败的操作。
3. **不要给 A 类加 `# noqa` 或例外清单**：G9 的基线本来就只许调小，A 类保持冻结即可；
   真要表达「这里是契约」，把理由写进**代码注释**（就近表达），不要新增清单文件。

### 已落地：26 处的「就地理由」（2026-11-15）

上面那张 A 类表在仓库根 —— 改 `common/log.py` 的人看不到它，于是同一批 `except Exception: pass`
每隔几个月就被重新审计一遍（本节存在的理由）。现在 26 处**各自的 `pass` 上方**都有一行
`# 契约静默（G9 冻结，非旧债）：<为什么静默是对的>`（第二行可以续理由），
`grep -rn "契约静默（G9 冻结" nn-training/` 一次数全。

门禁：`tests/test_silent_except_budget.py::test_frozen_silent_sites_say_why_in_place`
（`_marker_audit()`）双向钉住 —— ① 基线里的静默宽捕**没写理由** ⇒ 红（列出文件:行）；
② 理由注释**对不上任何静默宽捕**（那处窄捕 / 删了）⇒ 红（注释跟着代码走）。
两个方向都实测过（删一行理由红在 `common/log.py:233`；把标记挂进 `remote/hub_http.py` 的窄捕红在 `:293`）。

⚠ 标记**不是**豁免：它不减轻 G9 计数，新增静默宽捕照样要让 G9 红一次再决定处置。
口径账：本次未动 `BASELINE_FORM`/`BASELINE_SRC`（新用例的两条断言都不落在文本口径里 ——
语句级 `for node in ast.walk(tree)` 会把 `node` 记成「读 .py 的名字」从而让别处那条匿名断言
凭空 +1，所以那个循环变量叫 `h`；这是个值得记的口径陷阱）。

> 2026-11-25 第十六格：`trainer/loop_remote_job._remote_ppo_publish` 191 → 97 +
> `_remote_publish_setup`（51，只读 `_PublishSetup`）/ `_remote_publish_spec`（90）；
> HEAD-vs-新 6 组差分全同（含两条 SystemExit），存量 53 → 52（engineering §353）。

> 2026-11-25 第十七格：`trainer/loop_remote_job._remote_ppo_land` 151 → 17 + 五段
> （`_land_verified` / `_land_settled` / `_land_precollect` / `_land_settle_fields` /
> `_land_accept_log`）；HEAD-vs-新 20 组差分全同（敏感性探针 3 组红），存量 52 → 51
> （engineering §354）。

> 2026-11-25 第十八格：`remote/deliver_zip.import_deliver_zip` 178 → 42 + 四段
> （`_import_precheck` / `_unpack_import` / `_merge_import` / `_import_result`，载体
> `_ImportPackage` / `_ImportMerged`）；HEAD-vs-新 16 组差分全同（探针 10 组红），
> 存量 51 → 50（engineering §355）。

> 2026-11-25 第十九格：`hub/boot.main` 152 → 14 + 七段（`_resolve_token` /
> `_acquire_startup_guards` / `_ensure_code_snapshot` / `_build_push_dispatcher` /
> `_build_offline_push_dispatcher` / `_mount_worker_pulse` / `_serve`）；HEAD-vs-新 13 组
> 差分全同（探针 4/4 红），存量 50 → 49（engineering §356）。

> 2026-11-25 第二十格：`remote/http._get_with_retry` 190 → 69（函数体 ~35）+ `_attempt_once` `/
> `_plan_after` / `_plan_preempt` / `_plan_reroll` / `_plan_settle`（决策对象 `_RetryPlan` +
> `_RetryState` / `_Attempt` / `_RetryAction`）；HEAD-vs-新 18 组差分全同（探针 4/4 红），
> 存量 49 → 48（engineering §357）。

> 2026-11-25 第二十一格：`trainer/loop_serve.serve` 184 → 111 + `_start_serve` /
> `_build_service`（主循环逐字未动）；HEAD-vs-新 11 组差分全同（探针 4/4 红），
> 存量 48 → 47（engineering §358）。

> 2026-11-25 第二十二格：`hub/offline._post_offline_lease` 169 → 32 + 五段
> （`_offline_lease_claim` / `_claim_admissible` / `_settle_claimed_lease` /
> `_offline_lease_heartbeat` / `_offline_lease_release`）；HEAD-vs-新 21 组差分全同
> （探针 3/3 红），存量 47 → 46（engineering §359）。

> 2026-11-26 第二十三格（批）：`remote/offline_boot` 五个 >150 行函数全部落到线下
> （`run_one_course` 181→78 · `install_progress_pinger` 183→140 · `resolve_courses`
> 158→108 · `_run_batch` 169→105 · `_run_auto` 190→139）；改 `.pyfrag` 分片后重建，
> 存量 46 → 41（engineering §360）。

> 2026-11-26 第二十四格（T0）：`train_local_bc` 177→29 · `stream.run` 176→19；
> 存量 41 → 39（engineering §361）。

> 2026-11-26 第二十五格：`remote/offline_push.run_push` 191→113；存量 39 → 38
> （engineering §362）。

> 2026-11-26 第二十六格：`worker/model_build.build_model` 167→45；配假 tensor 差分；
> 存量 38 → 37（engineering §363）。

> 2026-11-26 第二十七格：`trainer/run_rl_cluster.main` 194→9（`_parse_args` 54 /
> `_serve_cluster` 79 / `_plan_view` 59；逐字搬移，惰性 import 与 monkeypatch 接缝不变）；
> 存量 37 → 36（engineering §364）。

> 2026-11-26 第二十八格（批）：`hub/store_offline.store_offline_artifact` 181→86
> （path `_offline_round_path` 52 / verify `_offline_round_verdict` 41 / write
> `_offline_round_commit` 65；锁仍包住三相，`_OfflineRound` 只做载体）·
> `remote/offline_eval.run_cloud_eval` 186→142（pool-setup `_acquire_eval_pool` 53 /
> round-close `_close_eval_round` 18；池所有权仍在外层 `finally`，不新增 `Any`）；
> 两个文件形状存量清零，36 → 34（engineering §365）。

> 2026-11-26 第二十九格：`worker/train/intent_probe.main` 181→58（`_index_shards` 29 /
> `_load_split` 31 / `_inject_features` 4 / `_sample_train` 48 / `_fit` 30 /
> `_predict_val` 13；载体 `_Split` / `_TrainSet`，注入先于采样的 P2-6c 约束改由调用顺序
> 保证）；同一刀抓到两条配额臂自 P2-6c 起的 `ValueError` 解包缺陷 —— 补
> `tests/worker/test_intent_probe_sampling.py` 3 条（先红后绿）；存量 34 → 33
> （engineering §366）。

> 2026-11-26 第三十格（批）：`hub/smoke_loopback.main` 181→21（boot `_boot` 91 / tick
> `_tick` 90 / report `_report_smoke` 22；载体 frozen `_Boot`（16 个就绪件）/ `_Round`；worker
> 非 0 退出改 `_WorkerExitError`，出口码不变）· `remote/bc_job._run_bc_job` 179→30
> （dispatch `_bc_dispatch` 46 / epoch 回传 `_bc_epoch_reporter` 36 / run `_bc_run` 75 /
> settle `_bc_settle` 17；载体 `_BcPlan`；`_load_bc_trainer` 仍先于设备检查，paddle 线程
> 旋钮早于 paddle import）；两文件形状存量清零，31 条目 / 合计 33 → 29 / 31
> （engineering §367）。

> 2026-11-26 训练路径审计：`worker/train/intent_probe.py` 的 self-feed 注入装载此前零用例
> ——缺 `self_inj.npy` 的 shard 哨兵是 1-D `(n,)`，与 `gen_self_inj.py` 的 N×9 契约不符
> ⇒ 混合数据根 `concatenate` ValueError、全缺时 `--ss-eps` 的 `xinj[:, 0]` IndexError。
> 三条缺陷 + 单 shard 拒绝全部按 §7 先写红（`tests/worker/test_intent_probe_training_path.py`
> 8 条，4 红）再修；另补 `build_injection` / `max_train` 对齐 / `_fit`+`_predict_val`
> 端到端覆盖（engineering §368）。

> 2026-11-26 第三十一格：`remote/rollout_relay.probe_hub` 154 → 32 + 五段
> （`_probe_routes` / `_route_findings` / `_unreachable_tail` / `_tls_readings` /
> `_alt_port_advice`；载体 frozen `_ProbeCtx` + `_TlsReadings` / `_PortAdvice`，
> 「每层都比上一层贵」的顺序逐字保留）；HEAD-vs-新 256 组决策表差分全同（含三个
> sweep 的调用序列），存量 28 文件 / 31 → 27 / 30（engineering §369）。

> 2026-11-26 第三十二格：`trainer/dispatch_state.admit_rollout_nodes` 152 → 36 + 四道门
> （`_stale_code` / `_bun_gate` / `_boot_gate` / `_admitted_node`；`_stale_code` 不返回布尔——
> 这条分支没有准入路径）；16 组假边界决策表差分全同（含 `dedup_streak` / agent meta /
> 升级请求字段），存量 27 文件 / 30 → 26 / 29（engineering §370）。

> 2026-11-26 第三十三格：`trainer/batch_runner._open_unit` 160 → 139 + 三份前置条件
> （`_unit_weights` / `_unit_pairs` / `_local_weights`；`_UnitPlan` 仍在入口唯一一处构造，
> `_settle_unit(self, ledger)` 不动）；13 组假边界决策表差分全同（含快照失败 / 空 seeds /
> god 两种），存量 26 文件 / 29 → 25 / 28（engineering §371）。

> 2026-11-26 第三十四格：`worker/bc_collect.collect_corpus` 159 → 33 + 四条出口
> （`_fixed_corpus` / `_complete_corpus` / `_dispatch_corpus` / `_apply_policy`，种子面进
> `_CollectPlan`；两处逐字重复的 ratio 落账归一）；13 组假边界决策表差分全同（含异常文案），
> 存量 25 文件 / 28 → 24 / 27（engineering §372）。

> 2026-11-26 第三十五格：`worker/iter_rollout._drive_games` 173 → 128 + 填槽器出栈
> （`_SlotFill` + `_fill_slots`，`supply_open` 改返回值、不再 `nonlocal`）；15 组假 Runtime /
> 假时钟场景差分全同，`Any` 13 → 12，存量 24 文件 / 27 → 23 / 26（engineering §373）。

> 2026-11-26 第三十六格：`trainer/eval_dispatch._eval_eligible` 173 → 100 + 九道能力位门
> 归一成一张表（`_capability_skip`，保留 `openingConnectSupport能力位` 的历史空格）；
> 25 组假节点场景差分为 0，存量 23 文件 / 26 → 22 / 25（engineering §374）。

> 2026-11-26 第三十七格：`worker/eval_replays_once._export` 178 → 97 + 课程/口径/重放三段
> （`_course_context` 24 / `_replay_knobs` 45 → frozen `_ReplayKnobs` / `_run_games` 70 +
> `_ReplayPlan`；`ns` 随 plan 走，因为 `stage_json_for_args` 要 `course_obj`）；23 组假边界
> 场景逐字段差分 0（manifest / 日志 / `EvalGameSpec` 25 字段 / workers / 产物面），
> `Any` 不变，存量 22 文件 / 25 → 21 / 24（engineering §375）。

> 2026-11-26 第三十八格：`trainer/eval_dispatch._eval_close` 162 → 95 + 三本账载体与三段收工
> （`_CloseBook` 15 字段 / `_wait_landing` 12 / `_announce_close` 50 / `_drain_writers` 10 /
> `_log_close_books` 15；`tail` 只拼一次，四分支共用）；14 组假收工账场景逐字段差分 0
> （日志 / `EvalSettleSpec` / rnd 调用序列），`Any` 保持 4，存量 21 文件 / 24 → 20 / 23
> （engineering §376）。

> 2026-11-26 第三十九格：`trainer/rollout_phase.dispatch_rollout_phase` 176 → 43 + 三条腿各一函数
> （`_stream_phase` 111 / `_queue_phase` 54；两个 new 入口 `assert dist_cfg is not None` 让 mypy
> 收窄，`nonlocal` 写面缩进同一函数体）；22 组假边界场景逐字段差分 0（返回元组 / 三个 spec
> 全字段 / 预采五档 / course 归属 / 日志），存量 20 文件 / 23 → 19 / 22（engineering §377）。
> 2026-11-26 第四十格：`remote/worker.worker_loop` 176 → 144 + 会话外件 `_LoopServices` /
> `_open_loop` 与一轮入参 `_round_spec`（`JobRoundSpec` 的 20 字段逐名装配搬到新接缝，
> 守卫同口径对账）；10 组假边界场景逐字段差分 0（返回码 / 日志 / 调用序列 / spec 全字段 /
> 收线实参），存量 20 文件 / 22 → 19 / 21（engineering §378）。

> 2026-11-26 第四十一格：`trainer/eval_a_once.main` 181 → 114 + CLI/课程/权重/配置/基线五段
> （`_eval_a_cli` / `_apply_course_args` / `_locate_ckpt` / `_dispatch_cfg` /
> `_fill_rl_defaults` / `_baseline_landed`；重依赖仍是函数内延迟 import）；10 组账本/CLI
> 场景逐字段差分 0（返回码 / 归一化日志 / `EvalDispatchSpec` 全字段 / 账本行），
> 存量 19 文件 / 21 → 19 / 20（engineering §379）。
> 2026-11-27 第四十二格：`trainer/eval_course_once.main` 174 → 80 + spec/课程/配置/执行/
> 落盘/汇总六段（同名 `_course_once_*`；两个载体用 NamedTuple —— 测试不注册 `sys.modules`，
> `@dataclass(kw_only)` 会在导入期炸）；10 组 spec 场景逐字段差分 0（返回码 / 日志 /
> `RunnerSpec` / 产物 JSONL），存量 19 文件 / 20 → 18 / 19（engineering §380）。

> 2026-11-27 第四十三格：`remote/plan_run._run_iteration` 172 → 67 + init/执行/留痕三段
> （`_iter_init` / `_execute_iter` / `_annotate_iter_result`；`iter_spec` 留原位，run_job 赋值与
> `_reseed_anchors` 搬家后守卫改钉 `_execute_iter`）；9 组假 RunContext 场景差分 0，
> 存量 18 文件 / 19 → 17 / 18（engineering §381）。

> 2026-11-27 第四十四格：`worker/loop_guards_leg._kickstart_burn` 171 → 50 + 读数/落账/提示/停腿
> 四段（`_burn_input` + `_BurnInput` / `_burn_note` / `_burn_notify` / `_burn_halt`；
> 三条早退副作用原样保留，notify 与 halt 分家）；9 组场景差分 0，存量 17 文件 / 18 → 16 / 17
> （engineering §382）。

> 2026-11-27 第四十五格：`remote/plan_handoff._eval_job_builder` 173 → 61 + 落点/落地/搬迁三段
> （`_eval_scratch_layout` 62 / `_eval_land_weights` 21 / `_eval_relocate_weights` 44；
> 落点缓存收进 `_EvalLayout`（`Any` 9 → 8），`CloudEvalSpec(` 唯一装配点未动）；10 组场景差分 0，
> 存量 16 文件 / 17 → 16 / 16（engineering §383）。

> 2026-11-27 第四十六格：`trainer/queue_worker.run_queue` 175 → 38 + 车道/孵化/推送/
> rescan/收尾五段（`_queue_lane` 17 / `_spawn_node_workers` 22 / `_push_need` 19 /
> `_rescan_thread` 50 / `_drain_and_join` 45；`worker` 用具体 Callable 类型，Any 预算不回涨）；
> 5 组场景差分 0，存量 16 文件 / 16 → 15 / 15（engineering §384）。

> 2026-11-27 第四十七格（批）：`remote/run_loop._build_parser` 151→7（输入/预算 · hub/outbox ·
> 评估/放大/备份三段 adder）· `worker/cli._flag_rows` 165→14（入口/intent · 训练 · 采集 · 并发/PPO ·
> 评估 · 课程/远端六段；`_Rows` / `_Default` 别名让 Any 保持 1）· `trainer/loop_core.TrainingLoop.__init__`
> 157→7（core/volume/remote/eval/ledger 五个 `_init_*`，赋值顺序不变）；27/94×6 个 action 与 80 个
> 实例属性差分 0，存量 15 文件 / 15 → 12 / 12（engineering §385）。

> 2026-11-27 第四十八格（批）：`remote/worker.main` 168→68 + `_worker_parser` 104（argv 表整段
> 搬出）· `remote/rollout_node.run` 164→100 + `_RunPlan`(16) / `_run_plan` 67 / `_AgentBoot`(7) /
> `_open_agent` 27 / `_build_session` 36（凭据留在 `run`：notebook AST 契约钉 `secret("TS_AUTHKEY")`
> 先于 `_ensure_bun`）· `remote/plan_handoff.open_run_context` 159→112 + `_resolve_deliverer` 62
> （删 8 个失效局部解包）；4+2+3 组差分 0，存量 12 文件 / 12 → 9 / 9（engineering §386）。

> 2026-11-27 第四十九格（批）：`remote/offline_deliver.OfflineDeliverer.__init__` 155→18
> （wire 45 / backup 15 / transport 18 / observability 72；每段只从 `spec` 取自己那几格）·
> `worker/train/bc.train` 160→57（`_bc_prepare` 21 / `_bc_loaders` 45 / `_bc_model` 89；10 项返回含
> `arch_meta`）· `worker/ppo/backend.run_step` 153→55（forward 31 / policy 42 / kickstart 17 /
> demo 26 / apply 10；无注解返回元组保 Any=21）；3+3+3 组差分 0（含 step 后参数 `torch.equal`），
> 存量 9 文件 / 9 → 6 / 6（engineering §387）。
