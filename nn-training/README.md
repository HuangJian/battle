# nn-training — Battle City Web 玩家 AI 训练工坊

> 确定性、可复现、零资产依赖。浏览器里的坦克由 SVG 绘制、声音由 Web Audio 合成，
> 这里的权重也由纯 stdlib + numpy + torch 在 CPU 上训出——没有云服务、没有外部数据集。

---

## 模块地图

```
nn-training/
├── pyproject.toml           # 包元数据 + uv 变体/索引配置 + ruff/mypy/pytest 配置
├── uv.lock                  # universal lockfile（6 个 torch 变体一次锁全）
├── .python-version          # uv 默认解释器档（3.10；tools/bootstrap.py 可用 --python 覆盖）
├── Makefile                 # 跨平台 task runner 的 make 封装（Git Bash 用户）
├── conftest.py              # pytest rootdir 全局守卫 —— **本目录下唯一的 .py**
├── rl-config.json           # RL 训练配置（rl-mode 默认值、workers、eval-seeds 等）
├── ../dashboard/src/       # 训练控制台 server.ts（bun run dashboard；组件启/停/冒烟/模式/节点/
│                             #   变更检测重启）+ train.ts 无头单次启动器（venv 未就绪委派
│                             #   tools/bootstrap.py；DECISIONS §346/§349）
│
│   # 2026-09-30 刀 7：**入口脚本全部归位到各自的包**（此前散在本目录顶层）——
│   #   trainer/{run_rl,run_bc,run_rl_cluster,train_loop,eval_course_once,eval_m1_once}.py
│   #   tools/{bootstrap,task,smoke_test,weights_prune,dist_upgrade_cli}.py
│   #   remote/{remote_worker,remote_worker_serve}.py · worker/rl_config_schema.py
│   #   （它们各自的 sys.path 前置 / 上溯层数 / `python -m` 名字见文件头与
│   #     tests/test_entry_scripts_in_place.py —— 那条守卫把它们真起一次）
│
├── common/                  # 【L0 全层】纯 stdlib，零上层依赖；单一定义防语义漂移
│                            #   2026-09-30 刀 2：L0 不再散在 nn-training/ 根下，全部收进本包
│   ├── hashing.py           #    sha256_file / sha256_bytes / sha256_json
│   ├── proc.py              #    run_capture（显式 utf-8 + errors=replace）/ bun_version / POPEN_NO_WINDOW
│   ├── fs.py                #    原子写 / JSONL 追加 / tar 安全解包
│   ├── text.py              #    exc_tail（异常尾部同口径截取）
│   ├── logutil.py           #    default_log（统一带时间戳 log 工厂）
│   ├── errors.py            #    失败类型族（每条继承线 = 一个处置分支）
│   ├── wire_codec.py        #    v1 gzip+base64 / v2 裸二进制编解码
│   ├── job_identity.py      #    幂等键 / job_id / 撞名守卫
│   ├── payload.py           #    语料归档（tar.xz 双读）
│   ├── manifest.py          #    job manifest 契约 + rollout 规格校验
│   ├── protocol.py          #    分布式采样器协议 + TS 导出器路径唯一来源（原 remote/protocol.py，DECISIONS §2026-09-23）
│   ├── game_watch.py        #    对局转播纯逻辑（原 remote/game_watch.py，同上）
│   ├── platform_utils.py    #    跨平台子进程 / effective_cores（原根下 platform_utils.py，刀 2）
│   ├── pid_probe.py         #    pid_alive（进程存活探测唯一实现；原根下，刀 2）
│   ├── log_bundle.py        #    LogBundle 攒行原语（原根下，刀 2）
│   ├── schema.py            #    张量布局常量（OBS_CHANNELS / MOVE_DIM / …）— TS 侧逐字镜像
│   ├── distribution.py      #    分布式采样器协议（原 dist_common.py，刀 2 去 dist_ 前缀）
│   ├── shard.py             #    shard 清单 + 远端结果容器校验（原 dist_shard.py，同上）
│   ├── weights_ledger.py    #    进程内权重下发账本（原 dist_weights_ledger.py，同上）
│   ├── jsonc.py             #    JSONC 解析（原 rl/jsonc.py，刀 2）
│   ├── net_http.py          #    回环 HTTP 绕代理（原 remote/net_http.py，刀 2）
│   ├── instance_lock.py     #    单实例锁（原 remote/_instance_lock.py，去下划线前缀）
│   └── port_guard.py        #    双监听守卫（原 remote/_port_guard.py，同上）
│                             #   层契约 + 依赖方向由 tests/test_layering.py 断言
│
├── hub/                     # 【hub 服务端 L4】HTTP 面 + 队列/存储状态类 + 引导链 + 入口门面
│   ├── server.py            #    入口与门面（`python -m hub.server`；零实现，只有 17 条自别名 re-export）
│   ├── http_face.py         #    HubHandler + 来源判定（五组路由混入的组装）
│   ├── boot.py              #    as_hub / make_server / main（单实例锁 + 端口守卫 + 发现线程）
│   └── {admin,blob,offline,result,schedule}.py  五组路由混入
│
├── worker/                  # 【本地 torch 训练全栈 L2】「所有支持本地 torch 训练的东西」；坐在 remote/ **下面**
│                            #   2026-09-30 刀 3：节点侧执行体（serve_pool / iter_rollout）从 remote/ 出包
│                            #   2026-09-30 刀 6（用户口径）：算法栈五包（原顶层 models/ ppo/ data/ train/
│                            #     scripts/）+ 52 个训练侧单体（原 biz/）搬进本包 ——
│                            #     云机 worker = **本地 worker + remote/**（remote → worker 是向下边）
│   ├── serve_pool.py        #    节点长驻 worker 池（`--serve` 协议；同质入口 serve-any）
│   ├── iter_rollout.py      #    节点侧「本轮 rollout」执行器（看门狗 / 补局 / 聚合 / 收尾）
│   ├── models/              #    【模型包】core（NNPolicy，BC 基座）· student（PPOStudent，P1.5 蒸馏）
│   │                        #      · rl_model（ResNet-11 教师网；**死文件**）· intent_net · goal_net
│   ├── ppo/                 #    【PPO 包】engine（ppo_update / build_ppo）· common（GAE / masked_logsoftmax）
│   │                        #      · intent / goal 适配（变步长 GAE）· bench（吞吐基准）
│   ├── data/                #    【数据包】dataset（mirrorX 在线增强）· npyio（shard 读写）· weights_io
│   │                        #      · mirror · shard_split · weights_meta
│   ├── train/               #    【训练器脚本】bc.py（masked CE + value MC）· goal_bc.py · intent_probe.py
│   ├── scripts/             #    【辅助脚本】eval_bridge · eval_intent_m5 · gen_self_inj · init_scratch_weights · validate_export
│   ├── loop_guards_*.py     #    四簇护栏混入（trip/leg/gate/sweep；组合根仍住 trainer/loop_guards.py）
│   ├── gate_*.py            #    课程结束门：输入面 / 判决面 / 引擎（读的是训练读数 ⇒ 属训练栈）
│   ├── config.py            #    启动参数校验层（pydantic）；语料身份今住 biz/corpus_fp.py（这里只 re-export）
│   ├── rl_config_schema.py  #    rl-config.json 键白名单校验（只告警不拒；数据 = nn-training/rl_config.schema.json；刀 7 从顶层搬到本包）
│   └── …                    #    其余：cmd / plan / iter_job（节点侧编排）· eval_*（读数/入账/双轨/让位/replay 导出）
│                            #      · volume_*（配额/波次）· *_ledger · node_identity · state_init · breaker · resume · reports
│
├── remote/                  # 【跨端传输与云机执行体 L3】线路 + 云引导 + 云 worker
│                            #   （hub 客户端 hub_client/hub_http 在这里；2026-09-30 刀 1 服务端已出包
│                            #    到 hub/；刀 3 节点侧执行体已出包到 worker/）
│   ├── remote_worker.py     #    【云 worker 入口】`python -m remote.remote_worker`（薄包装 remote.worker.main；刀 7 搬到本包）
│   └── remote_worker_serve.py  #  【push 模式服务端入口】`python -m remote.remote_worker_serve`（刀 7 搬到本包）
│
├── biz/                     # 【游戏业务 L1】只放和**游戏**（关卡/课程/奖励）相关的东西；零传输层依赖
│                            #   2026-09-30 刀 4：从 rl/ 出包（判据 = 编排树快照的补集，64 个模块）
│                            #   2026-09-30 刀 6：训练侧 52 个搬去 worker/ ⇒ 本包只剩 **12** 个游戏业务模块
│                            #   契约与模块表 → biz/__init__.py
│   ├── course*.py           #    课程：配对与课程（course）· 类面（course_spec）· 查找（course_resolve）· 封存（course_archive）
│   ├── reward_*.py          #    奖励公式引擎（reward_library）+ 内置势函数 + 校验 + 加载期 holder
│   ├── ladder_*.py          #    阶梯：账本（ladder_ledger）· 工厂（ladder_factory）
│   ├── corpus_fp.py         #    语料身份指纹（corpus_identity_fp + 规则版本常量；刀 6 从 config 下沉 —— 身份属游戏域）
│   └── hot_reload.py        #    课程热重载判决（same / apply / rejected：身份变了整单拒）
│
├── trainer/                 # 【训练编排 L4】驱动 rollout/eval/远端腿——只剩会碰传输层的那一层
│                            #   2026-09-30 刀 4：纯逻辑整族已搬进 biz/ ⇒ 本包**只有**编排
│                            #   2026-09-30 刀 5：整包改名 rl/ → trainer/（成员一个没变，37 个）
│   ├── queue.py             #    派发队列：race-tier / dup / pick（远端节点 + 本地槽位）
│   ├── stream.py            #    流式迭代：wave_params + 软降档（采集与 PPO 波次重叠）
│   ├── eval_dispatch.py     #    评估轮派发（per-tick 贪心局 + eval_log 对账）
│   ├── eval_m1.py           #    M1 评估协议（intent/goal 干净评估 + Δ 止损）
│   ├── loop_transport.py    #    传输/发布层：rollout 源解析 + transport 选择 + hub 推送 + 节点 failover（2026-09-23 从 loop_steps.py 拆出，S4）
│   ├── loop_remote.py       #    远端 PPO 腿**组合根**（零方法；TrainingSteps 的基类，S4 第二十二刀收口）
│   ├── loop_remote_push.py  #    直推腿 mixin：把 job 送到节点（提交/首发/取回；S4 第二十二刀）
│   ├── loop_remote_job.py   #    远端 PPO job 四步 mixin：发布 → 探活 → 取回 → 三重校验落位（S4 第二十二刀）
│   ├── loop_remote_fail.py  #    远端失败策略 mixin：确定性失败立即停腿 / 可重试失败计连败配额（S4 第二十二刀）
│   ├── loop_remote_drive.py #    驱动入口 mixin：轮内三相 / 整轮上云 / 半离线整段（S4 第二十二刀）
│   ├── loop_eval.py         #    in-loop 评估链 mixin：派发 → 尾巴收拢 → PPO 收官 join/交棒 → 收官 drain（TrainingSteps 的基类，S4 第十七刀）
│   ├── loop_volume.py       #    动态采集编排 mixin：初波/补波/连续配额 → 派发 → 报告合并 → WAL（RoundSteps 的基类，S4 第十八刀）
│   ├── loop_lifecycle.py    #    主循环骨架 mixin：setup / run 编排 / 轮派发 / 收官 / 停车（TrainingLoop 的基类，S4 第十九刀）
│   ├── loop_export.py       #    产物出包 mixin：TS 码 zip / 计划采集块 / 全离线包 / 权重归档（TrainingSteps 的基类，S4 第二十一刀）
│   ├── loop_baseline.py     #    it0 基线评估 mixin：bc 权重指纹 + 落地摘 → 派/不派（RoundSteps 的基类，S4 第二十刀）
│   ├── loop_iter_dir.py     #    本轮目录与产出健康 mixin：续跑保留/重建 + 零 shard 告警（RoundSteps 的基类，S4 第二十刀）
│   ├── loop_dispatch.py     #    本轮派发与让位 mixin：采集三路 / A-eval 稀疏化 / EvalBoard 关窗（RoundSteps 的基类，S4 第二十刀）
│   ├── loop_guards.py       #    训练护栏**组合根**：共享 sink（账本视图增量 + 判决→云机达令，S4 第二十三刀）
│   │                        #      四簇混入（trip/leg/gate/sweep）住 worker/（刀 4 出包到 biz/，刀 6 随训练栈搬进 worker/）
│   ├── run_rl.py            #    【RL 入口】三模式 CLI（--mode per-tick/intent/goal）+ 迭代主循环 + 权重归档/熔断（刀 7 从顶层搬入）
│   ├── run_bc.py            #    【BC 入口】编排器薄壳（一轮实现只在 trainer/bc_loop.py；刀 7 搬入）
│   ├── run_rl_cluster.py    #    【多课调度入口】`--serve` 写的一半 / `--json` 读的一半（刀 7 搬入）
│   ├── train_loop.py        #    【BC 单进程入口】轮次驱动 + 单实例锁（刀 7 搬入）
│   ├── eval_course_once.py  #    【一次性评估入口】课程（节点通信在 Python 侧；刀 7 搬入）
│   ├── eval_m1_once.py      #    【一次性评估入口】m1（给 tools/sim/m1-eval.ts 用；刀 7 搬入）
│   └── __init__.py          #    包入口文档
│
├── tests/                   # 【测试包】纯逻辑回归 + torch 常驻回归
│   ├── conftest.py          #    共享 fixture（bp_args 等）
│   ├── test_rl_course.py    #    确定性配对 + 课程扩展
│   ├── test_rl_breaker.py   #    熔断状态机
│   ├── test_rl_reports.py   #    聚合不变式
│   ├── test_rl_stream.py    #    wave-params + 残局 clamp
│   ├── test_run_rl.py       #    trainer/run_rl.py 编排层回归（含镜像、断点、JSONL 锚点、race-tier）
│   ├── test_run_rl_m1.py    #    三模式整合 + m1-eval 评估管线回归
│   ├── test_ppo_common.py   #    PPO 共享基础设施回归
│   ├── test_ppo_goal.py     #    ppo_goal.py 常驻回归
│   ├── test_ppo_intent.py   #    ppo_intent.py 常驻回归
│   ├── test_rl_model.py     #    rl_model.py（ResNet 教师网）回归
│   ├── test_student_model.py #   student_model.py（CoordConv-ConvMixer-Lite）回归
│   ├── test_train_loop_pure.py  # trainer/train_loop.py 纯函数回归
│   └── test_upgrade.py      #    common.distribution 主动升级机制回归
│
├── tools/                   # 开发/运维脚本（带 __init__.py 的包；刀 7 把顶层五个脚本收进来）
│   ├── bootstrap.py         #    【一条命令搭环境】探测 GPU → 选 torch 变体 → uv sync → 自检（冷机器上唯一能自举的那个）
│   ├── task.py              #    跨平台 task runner（= Makefile 的 python 版：check / test / lint / typecheck / clean / setup）
│   ├── smoke_test.py        #    torch 冒烟（真跑一轮小训练，读 val loss）
│   ├── weights_prune.py     #    权重文件归档轮转（保留最近 K 份，自动更新 WEIGHTS.md）
│   ├── dist_upgrade_cli.py  #    节点升级指令的一次性入口（供 tools/sim/*.ts 复用训练循环的守卫）
│   └── plot_training.py     #    train.log 快速取证（无外部依赖）
│
├── tmp/                     # 训练产物（已 .gitignore）
│   ├── rl-weights/weights.json      # 集成层 fixture
│   ├── itest-*.log                  # 集成层日志（RUN_RL_ITEST=1）
│   └── goal-bc/  goal-bc-smoke/  goal-rl*/  intent-probe-hard/
│
└── weights/                 # 快照归档（按模式分组，已 .gitignore）
    ├── intent-rl-weights.it*.json
    └── rl-weights.it*.json
```

---

## 命令拓扑

```bash
# 全部在 nn-training/ 目录下执行

# ── 一条命令搭环境（新机器 / 换机器 / 换 GPU）──
python tools/bootstrap.py              # 推荐：探测 GPU → 选 torch 变体 → uv sync → 装后自检
python tools/bootstrap.py --check      # 只读模式：打印本机探测结果与环境健康度，不改任何东西
make setup                             # 等价于 python tools/bootstrap.py
python tools/task.py setup --variant cu128   # 显式指定变体（跳过自动探测）

# ── ruff + mypy + fast-test ──
make check               # 或:  python tools/task.py check
make lint                # 或:  python tools/task.py lint
make typecheck           # 或:  python tools/task.py typecheck

# ── 测试分层（层 = 路径：tests/ 单测层 · e2e/ 集成层）──
make test-fast           # 单测层（tests/）
make test-e2e            # 集成层（e2e/，hermetic：不需 bun/真节点/weights）
make test                # 全量（tests/ + e2e/）
make check               # lint + typecheck + 全量 —— 与 pre-commit 同一套判定集

# ── 清理 ──
make clean               # 删除 __pycache__ / *.log / 临时产物，保留 weights/

# ── 权重管理 ──
make weights-prune       # 显示将被裁剪的权重（dry-run，保留最新 K 份）
make weights-prune-apply # 实际裁剪权重文件
make weights-update-md   # 重新生成 weights/WEIGHTS.md 目录清单

# ── 训练启动器（venv 由它 bootstrap；取代旧 start-training.sh/.ps1）──
bun dashboard/src/launch/cli.ts --script trainer/run_rl.py --mode intent-rl --stream 1
```

---

## 环境搭建与 GPU 变体（tools/bootstrap.py）

**目标：任何机器上一条命令搭出可用环境，torch 自动装对机器的那一版。**

```sh
python tools/bootstrap.py    # 探测 → 装 → 自检，全自动
```

内部流水线（详见 `plan/python-env-bootstrap-and-device.md`）：

1. 找 uv（PATH 上没有就装到 `~/.local/bin`，可 `--no-install-uv` 拒绝）；
2. 探 GPU：NVIDIA 驱动版本 → CUDA runtime 梯子（≥12.8→cu128，≥12.6→cu126，
   ≥11.8→cu118）；否则看 AMD ROCm（`/dev/kfd`，Linux）→ rocm6.3、Intel XPU →
   xpu；macOS 一律 cpu（Apple Silicon 的 MPS 由默认 wheel 提供）；以上全无 → cpu；
3. `uv sync` 对应 `dependency-groups`（互斥变体，见 `pyproject.toml`）：
   - `cpu` / `cu118` / `cu126` / `cu128` → Win + Linux
   - `rocm6.3` / `xpu` → 仅 Linux
   - torch 2.7.1 无 `cu124`/`cu130` 构建，故不提供（实测 download.pytorch.org）；
4. **装后自检**：真跑一次 matmul+backward 再读回 CPU —— 光 `is_available()`
   会骗人（驱动与 wheel CUDA 版本不匹配时它仍返回 True，跑起来才炸）；自检失败
   自动二段自救（重装 torch → 重建 venv）。

**为什么训练时不能只写 `torch.device('cpu')` 了**：机器若装了 cu128 变体，训练段
（PPO update / BC step）是实打实的算力瓶颈（前向+反传 ~220 MFLOPs/样本，165K
帧/epoch ≈ 36 TFLOPs）。rollout 已由 bun 分布式集群承担（节点零 Python，GPU 无
用武之地），GPU 只属于 orchestrator —— 设备层（P2，`worker/train/device.py`）会让 PPO/BC
自动 `cuda` + TF32 + AMP，无 GPU 机器自动回落 CPU + 线程数自适配。在 P2 落地前，
统一启动器（`dashboard/src/launch/cli.ts`）仍以 CPU + OMP 线程档运行（OMP≤8 时 `PROC_BIND=close`）。

**机器画像**：bootstrap 把探测结果写入 `.venv/machine-profile.json`
（platform / python / gpu / variant / probe），`tools/bootstrap.py --check` 只读展示。

---

## 改动红线（本目录内有效，不得突破）

1. **不碰生产算法**：PPO / BC / 课程 / 熔断 / 镜像增强的逻辑行不在此工程化范围内。
2. **不新增运行时行为**：commit 不改变集成层回归（`e2e/test_run_rl.py`）的输出
   （ALL PASS 集合）。
3. **不删测试**：搬迁后的原函数体必须同步从 test_run_rl.py 中删除以避免重复定义。
4. **不引入新依赖**：Python 侧仅用 torch / numpy + stdlib；工具链（ruff/mypy/pytest）是 dev-only。
5. **不扩大 scope**：Mermaid 图入口（`trainer/run_rl.py` 的 CLI 解析块）暂不拆——改动影响三模式调度。

---

## 分层测试策略

| 文件 | 层（路径） | 触发 | 依赖 |
|------|------|------|------|
| `tests/**` | 单测层 | `make test-fast` / 门禁 / CI | stdlib + torch |
| `e2e/test_run_rl.py` | 集成层 | 门禁 / CI（`pytest e2e/`） | numpy + run_rl 编排（不需 bun） |
| `e2e/test_run_rl.py --itest` | 集成层 | `RUN_RL_ITEST=1`（standalone 入口） | 同上 |
| `e2e/test_volume_e2e.py` 等 | 集成层 | 门禁 / CI（`pytest e2e/`） | numpy + 本机假 HTTP 节点 |

两层是同一次 xdist 调用（门禁）或 CI 的两步；单跑某层用 `make test-fast` / `make test-e2e`。

---

## 三模式架构（DECISIONS §307）

`trainer/run_rl.py` 是唯一的 RL 入口，通过 `--mode` 选择后端：

| 模式 | 后端 | 模型 | GAE | 说明 |
|------|------|------|-----|------|
| `per-tick` | `worker/ppo/engine.py` | PPOStudent (ConvMixer) | 标准 γ | 逐 tick move/fire PPO |
| `intent` | `worker/ppo/intent.py` | IntentNet (三头) | 变步长 γ^Δt | 意图步 semi-MDP，8 类意图 |
| `goal` | `worker/ppo/goal.py` | GoalNet (目标格) | 变步长 γ^Δt | goal 承诺步，676/169 路目标格 |

`--goal` 是 `--mode goal` 的兼容别名（原 `run_rl_intent.py` 已退役并入本文件）。

---

## 依赖关系图

```
trainer/run_rl.py
  ├─→ trainer/{queue,stream,eval_dispatch,eval_m1}（编排）+ worker/{cli,modes,archive,resume,reports,breaker}
  │     + biz/course（游戏业务）+ common.log（统一日志）
  │     2026-09-30 刀 4：纯逻辑那一半住 biz/（编排树只剩编排）
  │     2026-09-30 刀 5：编排树整包改名 rl/ → trainer/
  │     2026-09-30 刀 6：训练栈整族住 worker/（算法栈五包 + 52 个训练侧单体；biz/ 只剩游戏业务）
  ├─→ worker/ppo/{engine,goal,intent}
  ├─→ worker/data/weights_io
  ├─→ common.distribution
  └─→ common.platform_utils

trainer/train_loop.py
  ├─→ worker/train/bc
  ├─→ worker/models/{core,student}
  ├─→ worker/data/{dataset,weights_io}
  └─→ common.schema

worker/train/goal_bc.py → worker/models/goal_net → worker/data/weights_io
worker/train/intent_probe.py → worker/models/{intent_net,student}
worker/scripts/* → worker/models/*, worker/ppo/*
```

---

## 状态

- [x] P0 可复现基座（pyproject.toml / Makefile / task.py）
- [x] P1 type hints（课程侧 `biz/course.py` 与 `worker/resume.py`）+ ruff/mypy 配置
- [x] P2 测试架构（新增 tests/ + 瘦身 test_run_rl.py + 20 项 pytest 通过）
- [x] P2.5 包化重组（models/ + data/ + train/ + ppo/ + rl/ + scripts/）· 2026-09-30 六包重组（common/biz/worker/remote/hub/trainer：`worker/` = 本地训练全栈、`biz/` = 游戏业务）
- [ ] P3 配置治理（config schema 校验）
- [ ] P4 CI（GitHub Actions + 权重归档自动化）

> 注：本 README 与 `plan/nn-training-refactor.md` 路线图对齐。

---

## Pre-commit 门禁（2026-09-02）

仓库 `tools/githook/pre-commit`（`core.hooksPath = tools/githook`）在提交时自动
执行质量门禁。**当 staged 内容包含 `nn-training/` 下文件时**，除仓库既有的
TS 门禁（typecheck / bun test / oxfmt / oxlint / freeze gate）外，额外跑 Python
门禁（在 `nn-training/` 内、用 `.venv` 解释器）：

```sh
python -m ruff check .              # lint
python -m mypy . --config-file pyproject.toml   # typecheck
python -m pytest tests/ e2e/        # 全量：单测层 + 集成层
```

任一失败即阻止提交。跳过方式（二选一）：

```sh
SKIP_NN_TRAINING_GATE=1 git commit ...   # 仅跳过 Python 门禁
git commit --no-verify ...               # 跳过全部门禁
```

> 门禁产物（pytest basetemp / ruff / mypy 缓存）全部落在仓库根 `tmp/`
> （已 gitignore；2026-09-08 双 tmp 统一，不再用 `nn-training/tmp`），提交时不产生额外噪音。

### 日常开发入口

与提交时**同一套**并行门禁，日常随时可跑（脚本 `tools/githook/nn-python-gate.sh`
自定位 nn-training 与 venv，从仓库根或任意目录执行）：

```sh
bash tools/githook/nn-python-gate.sh        # 默认 xdist -n min(核数,12) + CPU 内线程 1
make -C nn-training python-gate             # Makefile 入口（= make check）
```

并行架构：ruff + mypy（热缓存 ~4s）+ pytest xdist（目标 `tests/ e2e/`）三路并行；
pytest 步的墙钟由最慢的单个用例与 xdist 分发决定（实测本机 16 核 ~23s）。
**两个旋钮必须一起调**（2026-09-17 实测）：只加 worker 会更慢、只封线程没收益——
旧默认 `-n 4` × torch 默认内线程（= 物理核）是 4×16 超订，全量 36.3s；封顶 + 按核数
发 worker 后 ~23s。完整数据、以及 `NN_GATE_NPROC` / `NN_GATE_THREADS` 两个逃生口
见 `tools/githook/nn-python-gate.sh` 头注。
跳过单项：

```sh
NN_GATE_SKIP=ruff,mypy bash tools/githook/nn-python-gate.sh
NN_GATE_SKIP_E2E=1 bash tools/githook/nn-python-gate.sh   # 只退集成层（保单测层）
```

### 测试临时目录自动清理

`tmp/pytest-tmp/` 每次运行累积测试临时目录（零删除策略），由门禁**前置自动清理**
（`nn-python-gate.sh` 每次运行前执行 `python -S tools/githook/nn-clean-tmp.py`）：

- 只删除 `tmp/pytest-tmp/` 下 **1 天前** 的测试子目录（`NN_TMP_KEEP_DAYS` 可调）
- 用 `python -S` 启动（跳过 site 初始化 → 沙箱删除保护不注入 → 无删除确认弹窗）；
  这是用户知情的沙箱保护绕过方案，严格限界于该临时目录
- 手动触发：`NN_TMP_KEEP_DAYS=2 python -S tools/githook/nn-clean-tmp.py`
