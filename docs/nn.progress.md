# NN Player AI — Training Progress Log

> All architecture changes, eval results, and lessons learned are recorded here.
> New entries are appended at the top (reverse chronological).

---

## §2.2 启动器 Windows 本机验证：ps1 双 bug 修复（BOM + 参数风格兼容，2026-08-21）

用 AGENTS.md 规定的幂等自检验证本机 torch 可用性：

- bash 版 `bash nn-training/start-training.sh --check` ✅ 直接通过，
  torch **2.7.1+cpu** @ `.venv/Scripts/python.exe`（OMP threads=12）。
- PowerShell 版 `powershell ... start-training.ps1 -Check` ❌ 解析失败，两个独立 bug：

1. **UTF-8 无 BOM → PS 5.1 按 GBK 解码**。本机无 pwsh 7，Windows PowerShell 5.1
   对无 BOM 的 .ps1 一律按系统 ANSI 代码页（中文系统=cp936）解码，UTF-8 中文注释
   字节被误读后破坏字符串引号配对，整个文件 ParserError。
   **修复**：文件头加 UTF-8 BOM（`EF BB BF`，微软官方推荐做法，对 pwsh 无副作用）。
2. **switch 只匹配 `'--check'`，`-Check` 落入透传**。`powershell -File` 调用时参数
   按字面量进入 `$args`（不做原生参数绑定），`-Check` 无法命中 POSIX 风格分支，
   被当未知参数转发给 train_loop.py → `unrecognized arguments: -Check`。
   **修复**：CLI 解析改 `switch -Regex` + `'^--?name$'`，同时兼容 `-Check` 与
   `--check`（PS switch 默认不区分大小写）；训练脚本参数均为 `--xxx` 长选项，
   不会被误吞。

**验证**：`-Check` / `--check` / `-Echo -Script train_bc.py --arch student` 全部
exit=0 且正确消费/透传参数；`bun run check` 全绿（1429 pass / 0 fail）。

**教训**：(a) 含非 ASCII 的 .ps1 必须带 BOM 才能跨 PS 5.1/pwsh 正确解析；
(b) `-File` 调用无参数绑定，脚本内 CLI 解析必须自行兼容两种前缀风格；
(c) Git Bash 终端显示 powershell.exe 的 GBK stdout 会乱码——那是终端显示问题，
以退出码和 ASCII 行（venv/torch version）为准。

**后续（同日）：VBS 启动器移除。** 删除 `launch-training.vbs` / `launch_rl.vbs`，
`--detach` 改为原生 `Start-Process -WindowStyle Hidden`（ShellExecute 派生完全
脱离控制台的进程，等价旧 VBS 行为，且规避 VBScript 被微软弃用的趋势）；bash 版
detach 分支委托给 ps1（detach 行为单一定义，避免两处维护）。顺手修复
`smoke_test.py` 的 arg-proxy 缺属性 bug（缺 `arch`/`notes`/`resume`，
train_bc.train 2026-08-20 前后新增字段未同步），修复后端到端 PASS。
验证：Start-Process 隐藏派生真实执行 smoke_test 并回传退出码；`-Check`/`--echo`
干跑 exit=0；`bun run check` 全绿。

---

## §2.1 移动死锁修复 + 0% 根因收敛（续 §2，2026-08-20）

### 关键修正：policy-input.ts 的 move-freeze 死锁（§2 评估 0% 的真正主因）

§2 报告「学生 BC 12ep → avgKills=0 / avgTicks=3933」与「+DAgger 6ep 仍 0%」。
重新诊断发现一个被忽视的**部署语义死锁**（非权重/精度/方向问题）：

- `policy-input.ts` 原把 move 头 argmax=0（`none`）映射为 `moveDir = null`；
- `SimulationPlayer` 在 `getMoveDirection()` 返回 null 时设 `moving=false`，
  坦克在出生点原地静止。但 `none` 在教师(God-AI)语义里 = **「保持当前航向」**，不是「停」；
- 世界状态冻结 → 模型每 tick 仍见静止状态 → 持续预测 `none` → 永久锁死，
  0 击杀、基地最终被毁（avgTicks≈3933 即 base_destroyed 时间线）。

**修复**：`none` 改为「持有上次指令方向」(`lastDir`)，坦克保持移动、世界状态
活跃。修复后单局 trace 从 `distinctCells=1 / kills=0` 变为 `distinctCells=200 / kills=1`。

### 修复后正式评估（stages 1-5 × seeds 1-10 = 50 局，hard，--policy nn，权重不变）

```
WIN RATE 0.0% (gate 60%) -> FAIL
totalKills=13  avgKills=0.26  avgTicks=3331
SCORE V7 suite=0.0691 lcb=0.0656 meanWinRate=0
```
avgKills 从 **0（冻结）→ 0.26（移动）**，avgTicks 3331 表明坦克现在会移动并
零星开火，但仍在 ~55s 内阵亡。**结论**：冻结死锁已修复（移动恢复），但残余
0% 来自**分布漂移**——学生在「自己的部署状态」上几乎不开火（部署 trace：
ready=true 时 `fireLogits[0] >> [1]`，fire 命中仅 3/477）。这与 §2 的
「BC 分布漂移」根因一致，但本次是更纯粹的**学生自部署漂移**（教师只在教师
状态上标过 fire）。

### 对 §2 DAgger 回合的影响（重要）

§2 的 DAgger 冒烟（9 局 3725 样本）是在 **freeze bug 存在时**采集的——学生
冻结在出生点，所有采集状态都聚集在 spawn 邻域，是一份**退化分布**，故续训
毫无帮助（move acc 0.35→0.386 也只是拟合 spawn 邻域）。修复后学生真正移动、
访问真实状态空间，DAgger 采集才首次有效。

### 本次交付

| 交付 | 文件 | 状态 |
|------|------|------|
| 移动死锁修复 | `src/nn/policy-input.ts`（`lastDir` 持有语义） | ✅ 已落地，评估验证 |
| DAgger 采集器（清理版） | `tools/sim/export-dagger-labels.ts` | ✅ smoke 2 局/330 样本通过 |
| 正式 DAgger 采集 | `tmp/dagger/`（stages 0-4 × seeds 0-9，50 局） | ✅ 完成：19783 样本 / 163124 ticks，50 shards，obs `(N,14,26,26)` 与 godai 同构（godai 368M / dagger 182M） |
| 混合重训 | `tmp/godai/` + `tmp/dagger/` → `train_bc.py --arch student` | ⏳ 需 torch 机（本机无 torch） |

### 下一步

1. ✅ `tmp/dagger/` 已采集完成（19783 样本，50 shards）。下一步：混合
   godai(368M) + dagger(182M) → `train_bc.py --arch student` 续训（--resume 当前
   学生权重），目标把 fire 头在学生自部署状态上拉起。`--data-dir` 当前为单目录，
   需先合并两目录或把该参数改为 `nargs='+'`。
2. 重训后跑 `m1-eval --policy nn` 量化保留率；若仍不足，追加 DAgger 回合
   （学生新权重 + 更多 seeds/stages）。
3. RL 教师落地后，同一管线直接复用（仅换 label 源）。

---

## §2 P1.5: God-AI 教师端到端蒸馏管线（学生架构）验证 (2026-08-20)

> 计划：`plan/RL-Net-Selection.md` §4.3–4.4（v4/v5）。目标：在 RL 教师落地前，用现成
> God-AI 当教师，端到端验证「CoordConv-ConvMixer-Lite 学生（68,554 参数）+ 离线蒸馏 +
> DAgger 在线蒸馏 + TS 推理 + 保留率测量」整条管线。

### What was built

| 组件 | 文件 | 说明 |
|------|------|------|
| 学生模型 | `nn-training/student_model.py` | ConvMixer-Lite h=64/d=8，BN-free，68,554 参数 / ~37M MAdds；forward 内追加 2 个 coord 通道（uint8 0..255，不除 255）；语料保持 14ch 不 bump schema |
| 学生训练 | `nn-training/train_bc.py` | 新增 `--arch student`（默认 `bc` 路径不动）；复用 masked CE / AdamW / Cosine / best-val 导出 |
| TS 学生推理 | `src/nn/infer.ts` | `StudentModel`（conv3x3/conv5x5dw groups=h/conv1x1/GAP/linear，零分配缓冲）+ `ModelLike` 接口 + `buildModelFromJson/Text` 按 `arch.kind` 分发 |
| 输入适配 | `src/nn/policy-input.ts` | 改用 `ModelLike`（cachedModel/loadModel/NNInput.model）；`think()` 决策谓词 `t==0 || t%K==0 || itemAppeared` |
| God-AI 采样器 | `tools/sim/export-godai-labels.ts` | 离线蒸馏语料导出器：`--stages/--seeds/--difficulty/--out/--max-ticks/--verify-determinism`；writeShard 与 BC 格式一致；确定性双跑字节比较 |
| DAgger 采样器 | `tools/sim/export-dagger-labels.ts` | 学生（NNInput）驱动真实引擎 + 独立 RNG 的 God-AI labeler 每 tick 跟读世界；在 `t==0 || t%K==0 || itemAppeared` 采 (state, God-AI label)；labeler 每 tick think 保持内部状态一致 |
| 评估 | `tools/sim/m1-eval.ts`（既有） | `--policy nn --weights-dir <dir>`；God-AI 基线 `--policy god` 同种子对比 |

### Verification results

- **确定性导出**：`--verify-determinism` 双跑 3 局 5 npy 文件字节一致（`[DET OK]` ×3）。
- **TS↔Python 前向一致**：同权重 + 同 obs/scalars（corpus shard 第 0 样本），TS `StudentModel`
  对 Python `StudentNet` 三头 logits maxAbsDiff ≈ 4e-5（float32 累加顺序噪声），argmax 全 MATCH。
- **权重格式**：42 键（stem/8×blocks{dw,pw}/fc/三头 ×{weight,bias}），与 `StudentModel` 完全匹配。
- **端到端冒烟**：9 局 God-AI 语料（8,252 样本）→ 12 epochs → 5×5 hard 评估。
- **DAgger 冒烟**：学生 9 局（3,725 样本）→ 合并续训 6 epochs → 5×5 hard 评估。

### Eval 对比（hard，5 stages × 5 seeds，同种子）

| 策略 | 胜率 | 说明 |
|------|------|------|
| God-AI（教师，基线） | **72%** (18/25) | suite=0.5985 lcb=0.5291 |
| 学生（BC 12ep，8.2K 样本） | 0% (0/25) | avgKills=0，avgTicks=3933 |
| 学生（+DAgger 6ep，11.9K 样本） | 0% (0/25) | move acc 0.35→0.386 |

0% 属**语料量/轮次不足**（不是管线 bug）：学生在打游戏（平均 96 发子弹/局）但 move 头太弱不会瞄准；
God-AI 教师 72% 门内。val_loss 1.73–1.87，move acc ~0.39（5 类 hard-label，teacher 自身随机）。

### 性能实测（本机，torch CPU 8 线程）

- 训练吞吐：学生 ~3.4ms/sample/step（depthwise 5×5 + pw 1×1 在 torch CPU NCHW 上极慢，1.7s/step@b256；
  channels_last 后 0.88s/step@b256；batch512 无增益；16 线程反降）。实测 ~2.5min/epoch @ 8.2K 样本。
- **全量 35×10 语料（~158K 样本）× 25 epochs ≈ 3.5–4h CPU** —— 这是唯一能抬出非零保留率的下一步。
- DAgger 导出：labeler 每 tick think 使导出 ~18× 慢于纯 God-AI 导出（9 局 162s）。

### 关键教训

1. **学生架构的 depthwise 卷积是 CPU 训练瓶颈**：torch 对 groups=h 的 5×5 dw 无高效实现；
   `channels_last` 仅 2× 加速。69K 参数换来 10× 的每样本 FLOPs（vs BC 52K）——训练成本必须
   计入保留率实验预算（web 推理端 TS 零分配 ~ms 级，部署不受影响）。
2. **BC 语料 label 的 teacher 自身随机性**：God-AI 在相同状态有随机性，hard-label CE 天花板低
   （move ~0.4）。DAgger 标签同样受此影响。
3. **确定性契约成立**：TS 端逐字节复现 Python 前向（float32 顺序噪声内），coord 通道公式、
   uint8 0..255 尺度、GAP、scalar concat 全部对齐。
4. **保留率基线（尚未达成）**：需全量语料训练后重测；届时报告 `学生胜率 / God-AI 胜率`。

### 下一步

1. 全量导出 `--stages 1-35 --seeds 1-10` God-AI 语料 → 学生训练（可后台跑，~4h CPU）。
2. 评估 + 算保留率；若不足，追加 DAgger 回合（学生当前权重 + 更多 seeds）。
3. RL 教师落地后，同一管线直接复用（`student_model.py` 不变，仅换 label 源）。

---

## §1 v2: Scalar Fusion Architecture (2026-08-19)

### What changed

The v1 backbone ignored all 24 scalar inputs. v2 concatenates the 24-dim scalar vector with the GAP output before the FC layer:

```
v1:  obs(14×26×26) → Conv(32→48→64) → GAP → FC(64→64) → heads
v2:  obs(14×26×26) → Conv(32→48→64) → GAP → cat(scalars) → FC(88→64) → heads
```

**FC layer input**: 64 (GAP) + 24 (scalars) = 88. Weight shape [64, 88].

### Files modified

| File | Change |
|------|--------|
| `nn-training/model.py` | `nn.Linear(c + scalar_dim, head_hidden)` + `torch.cat([x, scalars], dim=1)` |
| `nn-training/weights_io.py` | `load_state_into` tolerates FC shape mismatch (loads 13/14 params, skips FC) |
| `src/nn/infer.ts` | `fusedBuf` = pooled + scalars → FC; TS forward matches Python exactly |

### Parameter count

| | v1 | v2 |
|--|-----|-----|
| Total | ~50K | ~52K |
| FC input dim | 64 | 88 |
| FC params | 4,160 | 5,728 |

### Training warm-start strategy

Old conv weights (13/14 params) loaded into v2 model. FC layer randomly initialized.
This preserves learned spatial features while the FC layer learns to use scalar inputs from scratch.

### First epoch results (warm-started from v1 R10)

| Epoch | train_loss | val_loss | move_acc | fire_acc |
|-------|-----------|----------|----------|----------|
| 1 | 1.7835 | 1.3834 | 0.590 | 0.852 |
| 2 | 1.2787 | 1.3521 | 0.583 | 0.851 |

val_loss 1.35 at epoch 2 is already lower than v1's from-scratch start (1.91),
confirming the warm-start works — conv features transfer.

### Training timeline (v2, 68K samples)

| Round | val_loss | Δ vs R2 | Interpretation |
|-------|----------|---------|----------------|
| R1 | 1.1974 | +21.9% | Starting point |
| **R2** | **0.9984** | — | 🏆 Best — breaks v1 ceiling (1.0919) |
| R3 | 1.0066 | +0.8% | Plateau |
| R4 | 1.0172 | +1.9% | Overfitting begins |
| R5 | 1.0256 | +2.7% | — |
| R6 | 1.0342 | +3.6% | — |
| R7 | 1.0481 | +5.0% | — |

**Pattern**: same as v1 — val_loss bottoms at R2, then monotonically increases.
Scalar fusion lowered the ceiling (0.998 vs 1.092) but didn't change the shape.

### M1 Sim Eval (v2, best weights R2 val_loss=0.9984)

```
policy=nn  difficulty=hard  35 stages × 10 seeds = 350 games
WIN RATE 0.0% (gate 60%) → FAIL
SCORE V7 suite=0.1087  lcb=0.1071  meanWinRate=0
avgKills=3.04  avgTicks=4207
```

**All 350 games ended in gameover.** 0% win rate — same as v1 despite val_loss
improving 8.4% (1.0919 → 0.9984).

### v1 vs v2 comparison

| Metric | v1 (no scalars) | v2 (scalar fusion) | Δ |
|--------|-----------------|--------------------|----|
| val_loss | 1.0919 | **0.9984** | -8.4% ↓ |
| Win rate | 0.0% | 0.0% | — |
| Avg kills | 2.6 | **3.04** | +17% ↑ |
| Avg ticks | 4755 | 4207 | -11% |
| Score V7 | 0.1085 | 0.1087 | +0.2% |

**Key finding**: Scalar fusion improved learning (val_loss ↓, kills ↑) but didn't
improve winning. The model kills 17% more enemies but still can't survive to
clear a stage.

### Per-stage highlights

| Stage | avgKills | Notes |
|-------|----------|-------|
| Ramparts | 8.0 | Highest kills — still 0% win |
| Waterways | 6.5 | — |
| Eagle Nest | 6.4 | — |
| Checkers | **0.0** | Complete paralysis — 0 kills in all 10 games |
| Iron Curtain | 1.1 | — |
| Gauntlet | 1.3 | Worst score V7 (0.089) |

### Why scalar fusion didn't help winning

The model can now "see" lives, base distance, enemy distribution, etc. But it
still can't *act on* this information effectively. Root causes:

1. **BC distribution shift still dominates**
   - Scalar fusion reduces the information gap but doesn't fix the fundamental
     problem: once the NN's trajectory diverges from the human's, it can't recover
   - The model needs to be *robust* to its own mistakes, not just accurate on the
     first few decisions

2. **7×7 receptive field can't capture global strategy**
   - 3 layers of 3×3 conv → 7×7 receptive field on a 26×26 board
   - Model can't reason about "enemies are coming from the north, base is south"
   - Scalars give relative positions but the spatial backbone can't plan paths

3. **Checkers stage = complete failure mode**
   - 0 kills in 10 games — the model literally cannot move or shoot
   - Suggests the model has learned a brittle policy that collapses on certain
     terrain layouts

### Lessons learned (v2 additions)

7. **Scalar fusion is necessary but not sufficient** — the model needs scalars to
   make context-aware decisions, but scalars alone don't solve distribution shift
8. **val_loss continues to be a poor game-performance proxy** — 8.4% improvement
   with zero win-rate improvement
9. **Receptive field is the next bottleneck** — model can see the data but can't
   reason about spatial relationships beyond 7×7
10. **BC has a fundamental ceiling on hard difficulty** — the model needs to be
    robust to its own mistakes, which BC doesn't train for

### Status (2026-08-19)

**v2 scalar fusion: 0% win rate on hard. BC approach has reached its ceiling.**

Next options:
- Train on classic difficulty (easier → model can learn complete strategies)
- Switch to RL (reinforcement learning) — train with win/loss signals
- Increase model capacity (deeper conv, attention mechanism)

---

## §0 v1: Conv-Only Baseline (2026-08-18 → 2026-08-19)

### Architecture

```python
# nn-training/model.py v1
class NNPolicy(nn.Module):
    # Conv backbone: 14ch → 32 → 48 → 64, 3×3 kernels
    # GAP → FC(64→64) → ReLU → 3 heads (move/fire/item)
    # scalars parameter: ACCEPTED but IGNORED in forward()
    def forward(self, obs, scalars):
        x = obs.float()
        x = self.conv(x)           # (B, 64, 26, 26)
        x = self.gap(x)            # (B, 64, 1, 1)
        x = x.flatten(1)           # (B, 64)
        h = self.fc_relu(self.fc(x))  # (B, 64)  ← scalars NOT used
        return self.move_head(h), self.fire_head(h), self.item_head(h)
```

**Fatal flaw**: `scalars` parameter accepted but never concatenated into the FC input.
The model had no access to: lives, base distance, enemy distance, fire cooldown, ring integrity, inventory, etc.

### Training timeline

| Phase | Dates | Samples | Rounds | Best val_loss | Notes |
|-------|-------|---------|--------|---------------|-------|
| Initial baseline | 8/18 17:00 | 43,566 | 1×40ep | 1.2431 | First training run |
| Continuous 40ep | 8/18 21:08–23:30 | 43,566 | 3×40ep | **1.1320** | val_loss rebounded after R2 |
| Corpus expansion | 8/19 07:46 | 68,571 | 21×1ep | 1.4083 | From scratch after venv rebuild |
| Continuous 40ep | 8/19 09:54–15:07 | 68,571 | 9×40ep | **1.0919** (R2) | val_loss rebounded from R3 onward |

### val_loss trend (68K samples, v1)

| Round | val_loss | Δ vs R2 | Interpretation |
|-------|----------|---------|----------------|
| R1 | 1.1974 | +9.7% | Starting point |
| **R2** | **1.0919** | — | 🏆 Best |
| R3 | 1.0974 | +0.5% | Plateau |
| R4 | 1.1192 | +2.5% | Overfitting begins |
| R5 | 1.1422 | +4.6% | — |
| R6 | 1.1499 | +5.3% | — |
| R7 | 1.1639 | +6.6% | — |
| R8 | 1.1601 | +6.2% | — |
| R9 | 1.1625 | +6.5% | — |

**Pattern**: val_loss bottoms at R2, then monotonically increases — textbook overfitting.

### M1 Sim Eval (v1, best weights R2 val_loss=1.0919)

```
policy=nn  difficulty=hard  35 stages × 10 seeds = 350 games
WIN RATE 0.0% (gate 60%) → FAIL
SCORE V7 suite=0.1085  lcb=0.1069  meanWinRate=0
avgKills=2.5  avgTicks=4234
```

**All 350 games ended in gameover.** 0% win rate — same as the initial baseline
despite val_loss improving 12% (1.2431 → 1.0919).

### Per-stage highlights

| Stage | avgKills | progress | baseIntegrity | mobility | accuracy |
|-------|----------|----------|---------------|----------|----------|
| Waterways (best) | 5.1 | 0.255 | 0.244 | 0.169 | 0.300 |
| Lattice (worst) | 0.6 | 0.030 | 0.072 | 0.085 | — |
| Ramparts | 4.0 | 0.200 | 0.182 | 0.484 | — |
| Steel Fortress | 2.5 | 0.125 | 0.000 | 0.314 | 0.552 |

**Key observations**:
- `baseIntegrity` ≈ 0 on most stages → base always destroyed
- `progress` ≤ 0.255 → kills at most 25% of enemies
- `mobility` ≤ 0.48 → limited map exploration
- No correlation between avgKills and score — killing more doesn't help if you can't protect the base

### Corpus analysis

**94.2% of training replays are wins** (98/104 cleared all enemies).
Only 6 losses in the corpus (partial clears on Bunker Hill, Labyrinth, Brick Maze, Spider).

This means the NN was trained primarily on winning trajectories but couldn't reproduce them in sim.

### Root cause analysis

#### Why val_loss ↓12% but win rate = 0%

1. **BC loss measures imitation accuracy, not winning ability**
   - val_loss = cross-entropy between NN predictions and human actions
   - A model that perfectly mimics a winning trajectory should win — unless it can't
     maintain the trajectory under distribution shift

2. **Distribution shift (the real killer)**
   - Training: given obs_t, predict action_t (ground truth from human replay)
   - Inference: NN's action_0 may match human, but action_1 diverges slightly →
     obs_1 diverges → action_2 diverges more → ... → cascade failure
   - Even 94% winning training data can't prevent this if the NN lacks the information
     needed to make the same decisions as the human

3. **Missing scalar inputs = missing decision context**
   - Human player decides "retreat to base" based on knowing: "I have 1 life left,
     base ring is damaged, enemy is approaching from the north"
   - NN only sees the 14-channel spatial snapshot — it can't distinguish "aggressive
     push" from "desperate retreat" without scalar context
   - The 24 scalar features (lives, base distance, fire cooldown, enemy count, etc.)
     were available in the encoding but never fed to the model

4. **Model capacity bottleneck**
   - 50K params for 68K samples — near the capacity boundary
   - GAP compresses 26×26 spatial info to 64 dims — heavy information loss
   - 3×3 convs have 7×7 receptive field — can't capture long-range spatial relationships

#### Why move_acc improved but didn't help

- move_acc 0.586 → 0.709 over 10 rounds
- But accuracy is measured against **human actions**, not **optimal actions**
- The human's movement in winning replays is context-dependent — "go left" is only
  correct when you know the base is to the right and enemies are above
- Without scalar context, the NN learns a statistical average of directions, not
  a context-aware policy

### Lessons learned

1. **Never ignore available inputs** — if scalars are encoded, they must be consumed
2. **val_loss is a poor proxy for game performance** — always validate with sim eval
3. **BC requires the model to see everything the human sees** — otherwise distribution
   shift makes inference unreliable
4. **Warm-starting conv weights is effective** — v2 epoch 1 val_loss (1.38) already
   below v1 from-scratch start (1.91)
5. **94% winning corpus ≠ easy BC** — distribution shift dominates even with clean data
6. **Architecture changes require `load_state_into` tolerance** — shape mismatches
   should be caught and handled gracefully, not crash the training loop

---

## §-1 Pre-history (before 2026-08-18)

Training infrastructure established:
- `nn-training/train_loop.py` — continuous training loop with auto-resume
- `nn-training/train_bc.py` — behavior cloning trainer
- `nn-training/start-training.sh` — launch script with VBS detach on Windows
- `tools/replay/export-observations.ts` — NDJSON → npy shard exporter
- `src/nn/infer.ts` — TS runtime inference
- `src/nn/policy-input.ts` — NNInput InputLike implementation
- `src/nn/obs-encoder.ts` — 14-channel spatial + 24-dim scalar encoder
