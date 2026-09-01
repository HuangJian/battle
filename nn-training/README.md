# 《Battle City Web》神经网络玩家 AI 训练工程（NN-M1）

本目录是 `plan/NN-Player-AI-Training-Plan.md` 所描述的 **NN 玩家 AI** 的 **Python / PyTorch 训练工作区**，位于仓库根目录下（`nn-training/`，与游戏同仓版本管理），但**在 vite 构建之外**——游戏运行时为纯 TS（约 200 行），遵循 plan §2 / AGENTS §2。

## 复现锚点（plan §5）

- 训练脚本与导出器必须对齐的仓库 commit：
  **`e1a144ca7438ca16cb6159a6ee518008624074d3`**（分支 **`new-ai`**）。
- 导出器（`src/nn/obs-encoder.ts` + `tools/replay/export-observations.ts`）与训练侧
  schema（`schema.py`）共享 `OBS_SCHEMA_MAJOR=1`。任一通道 / 标量 / 动作布局改动，
  必须**同步两边并重新导出全部语料**。
- 复现前先：`git -C <battle2> checkout e1a144ca7438ca16cb6159a6ee518008624074d3`

## 目录约定

| 文件 | 职责 |
| --- | --- |
| `schema.py` | TS↔Python **唯一** schema 常量（通道 / 标量 / 动作 / 掩码布局）。 |
| `rl/` | **RL 训练核心包**（2026-08-25 自 run_rl.py 抽取）：`course.py` 课程采样 · `queue.py` 中央队列调度 · `stream.py` 流式迭代 · `eval_dispatch.py` 干净评估分发 · `resume.py` 断点对账 · `reports.py` 报告聚合 · `breaker.py` F4 熔断纯逻辑 · `log.py` 统一日志。入口仍为顶层 `run_rl.py`（启动器只接受裸文件名）。 |
| `test_run_rl.py` | RL 编排回归测试：快速层（纯逻辑，默认）+ 集成层（假 HTTP 节点，`--itest`）。运行：`start-training.sh --script test_run_rl.py [--itest]`。 |
| `npyio.py` | 手写 raw `.npy` 读写（TS 侧无 numpy 也能写出；Python 侧用 `numpy.load` 读取）。 |
| `model.py` | `NNPolicy`：卷积 backbone + 3 个 factored head（move-5 / fire-2 / item-3），≤200K 参数，ReLU-only + 自实现 softmax（与 TS 推理逐字节一致）。 |
| `weights_io.py` | 权重 JSON 导出 / 加载（base64 `<f4`），供 TS 运行时 `infer.ts` 加载。 |
| `dataset.py` | `NNDataset` + `make_loaders` + mirrorX 数据增强。 |
| `train_bc.py` | CPU 行为克隆（BC）：masked cross-entropy、AdamW + CosineAnnealingLR、最佳 val 检查点、导出 JSON 权重。 |
| `eval_bridge.py` | 在留出 npy 上做 per-head 准确率评估，生成 `bun tools/sim/batch-sim.ts --policy nn ...` 命令。 |
| `smoke_test.py` | 合成 4 个 shard 跑通 train，断言 loss 下降 + 权重可回载。 |

## 环境

- Python 3.13（managed venv）。
- **CPU-only**：无 GPU，8 核 32G。
- 依赖：`torch`（CPU build）+ `numpy`，见 `requirements.txt`。
- 安装：
  ```powershell
  python -m venv .venv
  .venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  ```
  > **推荐**：`torch` 只装在 `.venv`，系统裸 `python` 没有 torch。所有训练请
  > 通过 `start-training.sh` 启动（它会自动建/复用 `.venv` 并装依赖）：
  > ```bash
  > bash nn-training/start-training.sh --check                 # 自检 torch 是否就绪并打印解释器
  > bash nn-training/start-training.sh --script smoke_test.py  # 跑任意 nn-training/*.py
  > bash nn-training/start-training.sh --script train_bc.py --data-dir tmp/mix --arch student ...
  > ```


## 复现流程（corpus → npy → BC）

1. **语料（已备）**：`battle2/nn-demo/*.ndjson`（21 局人工演示）。
2. **导出观测**（在 battle2 仓库内运行）：
   ```bash
   bun tools/replay/export-observations.ts nn-demo/*.ndjson --out /path/to/shards
   ```
   产出 `obs/scalars/actions/masks/conditions` 的 raw `.npy` shard + `manifest.json`。
3. **训练**（在本目录，权重默认写入 `weights/` 子目录）：
   ```bash
   python train_bc.py --data-dir /path/to/shards --epochs 40 --batch 64
   # 等价于：--out weights/weights.json（版本化归档 + WEIGHTS.md 一并写入 weights/）
   ```
4. **评估桥接**（省略 `--weights` 时自动发现 `weights/` 下最新权重）：
   ```bash
   python eval_bridge.py --data-dir /path/to/shards          # 自动选最新
   python eval_bridge.py --data-dir /path/to/shards --weights weights/weights.2026....json  # 显式指定旧版
   ```
5. **自测**：
   ```bash
   python smoke_test.py
   ```

## 不变式（plan §1.4）

- TS 导出器与 TS 运行时推理共享 `src/nn/obs-encoder.ts` → 字节级一致。
- Python 训练侧**绝不**重新编码观测，只消费导出的 npy。
- 公平物理：导出 / 训练零改动物理；One-Author 规则不变；确定性（导出器不消费 `world.rng`）。
- 调度：CPU-only，BC → DAGGER（NN-M2+）。硬目标：35 关 ≥90% 胜率。

## 退出码约定

- `train_bc.py`：正常退出 0；NaN / 无数据 / 加载失败退出非 0。
- `smoke_test.py`：全部断言通过 0，否则 1。

---

## RL 训练机制（run_rl.py — 唯一 RL 入口：--mode per-tick/intent/goal）

> `run_rl.py` 是三套后端的**唯一入口**（RL 入口整合，plan/RL-Entry-Consolidation.md
> / DECISIONS §307）：`--mode per-tick`（逐 tick move/fire）/ `--mode intent`（意图步
> semi-MDP）/ `--mode goal`（goal 承诺步，原 run_rl_intent --goal）。`--goal` 保留为
> `--mode goal` 别名。任何 RL 入口（含未来新策略）都必须复用 `rl/` 包实现，禁止复制
> 第二份。

### 1. 训练循环（单轮迭代）

```
init：RL 权重不存在 → 从 BC 检查点 warm-start（策略头加载，value 头随机）→ 存 rl_path；
      已存在 → 直接续跑（resume）。
迭代 N 轮：
  ① 断点感知建目录：wver=sha256(weights)；traj_dir 已含 wver 匹配的完整 shard → 保留
     续跑（跳过已完成局 + 续 ppo checkpoint）；否则清空重建。
  ② 课程采样 build_pairs(args, it, rotateSeed)——纯函数，同 (rotateSeed,it) 逐字节可复现
     （断点续跑剔除的前提）。
  ③ rollout（--stream 1 流式 / 0 串行，见 §2）→ 单局 bun 子进程写 shard + manifest。
  ④ PPO 更新：加载 shard → 每局 GAE（adv/ret）→ 全局/每 wave adv 归一 → minibatch
     chunkify → clipped PPO（policy+value+entropy，target_kl 早停）。
  ⑤ 原子写回权重（同卷 tmp+rename）+ 归档（nn-training/weights/<prefix>.it<N>.*.json）。
  ⑥ iteration 事件写 training_log.jsonl（winRate/outcomes/samples/kl/entropy/耗时/评估等）。
  ⑦ 自动巡检 HTML（rl-hourly-inspect.ts --up-to N [--traj-dir]）。
  ⑧ F4 熔断（rl/breaker.py）：KL≥0.15 连续 3 轮 或 entropy≤0.60 连续 8 轮且胜率<50% → 停车。
  ⑨ keep-iters 轮转（默认保留最近 3 轮 traj）。
  ⑩ 失败重试：单轮 SystemExit/Exception → 落 iter_error 事件 + 原地重试（连续 5 次才退出）。
```

### 2. 流式 vs 串行（--stream 1/0）

- **串行（0）**：rollout 全部完成 → PPO 一次跑完。简单，但 rollout 期间训练机算力闲置。
- **流式（1，推荐）**：rollout 采集与 PPO **波次重叠**（rl/stream.py `run_rollout_stream`）：
  - collector 线程跑 `run_rollout_queue`，**本机槽位压到 `--local-slots`（缺省 max(2,workers//4)）
    给 torch 让核**；
  - 每积压 ≥ `streamWaveGames`（默认 12）局 → 装载该波 shard + GAE + 每 wave adv 归一
    → PPO 更新一遍（与串行总更新遍数一致）；
  - **首个 PPO 波次启动 → local_suspend 置位 → 本机 dist 槽位暂停领 rollout 任务**
    （集群停摆豁免：远端失联超阈值自动恢复本机采样）；
  - **轮内累计 KL 超 `streamKlCap`（默认 0.12）的 70% 软降档收缩 wave；触顶 → 停止训练 +
    停派发采集（halt_event），在途局自然收尾，未训练语料按 dropped 记账**；
  - **干净评估时机**：中央派发队列清空（全部采集任务已派到节点、结果仍在途）即派发评估
    （on_queue_drained → dispatch_eval_bg），**PPO 收尾后 eval_gate 放行本机 eval 参与**
    （R6：评估线程在 jsonl 写回前 join，下轮分发前必须收官）。

### 3. 断点续跑

- `training_log.jsonl` 的 `run_start`/`iteration` 事件是唯一锚点：`--start-it` 缺省 =
  最后完成迭代 + 1；`rotateSeed` 继承自上一 run_start（课程连续性）。
- 单轮中途崩：`completed_pairs(traj_dir, wver)` 识别已完整落盘的 (stage,seed) → 跳过重跑；
  PPO epoch checkpoint（traj_dir/ppo_ckpt：model/opt/numpy RNG）→ 从最近 epoch 续梯度。
- 流式期间 PPO checkpoint 不落盘（崩溃该轮重训，语料靠 completed_pairs 秒回）。

### 4. 干净评估（eval_dispatch.py）

- 固定语料贪心局（动作 argmax 无探索噪声、(stage,seed) 恒定）→ 跨 checkpoint 配对可比。
- 派发到各节点（evalSupport 门 + bun 版本门）+ 本机直跑（读派发时刻冻结权重快照）；
- 每局先验后落盘（wver 对账 + mode 回显 + 关键字段），结果追加 eval_log.jsonl；
- 单轮失败回队，节点连续失败熔断；窗口超时未结算放弃（不阻塞 PPO 与下一轮）。

### 5. 分布式采样（plan/distributed-rollout.md，rl/queue.py + tools/agent/sampler-agent.ts）

- `rl-config.json`（gitignored）：节点 url/authKey/concurrency/enabled；每轮分派点动态读取。
- 中央队列逐局 RPC：`POST /v1/weights`（x-kind 分桶：rollout / intent）→ `GET /v1/task`
  （kind/replan 透传）→ 异步 202 + `/v1/result` 轮询（结果缓存幂等）。
- 权重切换（异 sha）→ agent 原子清场（旧权重+结果缓存）；同 sha 幂等不动。
- 确定性门：`codeHash`（TS/Python 双语配方，含 export-rl-rollout.ts / export-intent-rollout.ts）
  + bun major.minor 一致，否则拒绝派发。
- 本机槽位与远端同队消费（head_tasks 保留段 = local 优先领任务）；HTTP 503(busy) 不计
  熔断连击；尾部 fan-out 竞速 + EWMA 分档 + 动态节点发现（rescan）。

### 6. 意图/goal 模式（--mode intent/goal）差异点（机制全同，仅以下不同）

- 网络：意图 `IntentNet`（StudentNet 主干 + 意图/enemy/anchor/value 头，137 隐藏
  含注入）/ goal `GoalRLNet`。
- 动作：意图 8 类（replan 周期决策）/ goal 676·169 路目标格（心跳承诺期），窗口冻结；
  rollout 走 `export-intent-rollout.ts` / `export-goal-rollout.ts`（意图/目标步 shard：
  inject/dt/mask）；PPO 走 `ppo_intent.py` / `ppo_goal.py`（变步长 GAE γ^Δt）。
- 冷启动：B′/goal-BC 权重 + value 预热（冻结主干只训 value 头）+ kickstarting KL
  惩罚（系数 decay/iter 衰减）；权重初始化及续跑 = `build_model --mode` 幂等分叉。
- 评估：m1-eval --policy intent-exec/goal 固定语料（35 关 × N seeds）贪心局；Δ vs
  baseline，`--stop-loss-at/--stop-loss-delta` 判门（原 iter15 Δ≤0 转 M9 语义）。
- 权重归档前缀 `intent-rl-weights` / `goal-rl-weights`（与 per-tick `rl-weights` 分桶）。

启动（原 run_rl_intent.py 命令等价）：
  bash nn-training/start-training.sh --script run_rl.py --mode intent --bc tmp/intent-weights-Bp.json ...
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 \
      -Script run_rl.py --mode goal --bc tmp/goal-bc/weights.json ...
