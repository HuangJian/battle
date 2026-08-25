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
