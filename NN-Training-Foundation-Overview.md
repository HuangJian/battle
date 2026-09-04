# NN Player-AI 训练工程基础 — 交付状态

> 依据 `plan/NN-Player-AI-Training-Plan.md`（NN-M0 / NN-M1 里程碑）。
> 目标：搭好「TS 推理侧 + 外部 Python 训练侧」双语工程基础，并锁死跨语言 npy 契约。

## 已完成（核心交付）

### TS 侧（在 vite 构建内，纯 TS 推理，~200 行）
| 文件 | 职责 | 状态 |
|------|------|------|
| `src/nn/npy.ts` | 手写 raw `.npy` 写出器（magic/版本/uint16 LE hlen/64B 对齐头 + C 序数据） | **修过关键 bug** |
| `src/nn/obs-encoder.ts` | 14 通道空间 obs + 24 维标量；事件型决策 tick；3 头动作 + 10 维 mask | ✅ 通过 |
| `tools/replay/export-observations.ts` | 从 replay 确定性重建 World，逐决策 tick 抓取特征并分片导出 npy；**新增计时 + `--verify-determinism` 模式** | ✅ 通过 |
| `tools/replay/verify-item-events.ts` | **新增**（gate ⑤ 交叉核对）：导出 item 数 ≤ 回放 guard/frenzy 位变化数 | ✅ 通过 |
| `tools/replay/verify-demos.ts` | replay 保真校验（verify-replay），提升自 tmp/，支持多文件/--json/--out | ✅ 通过 |
| `tests/nn/obs-encoder.test.ts` | 29 测试：维度/动作解析/fire 边界/决策 tick/mask/通道/确定性 | ✅ 29 pass |
| `tests/nn/npy.test.ts` | 2 测试：锁死 hlen 含 padding 契约 `(10+hlen)%64==0` | ✅ 2 pass |

**关键 bug（已修，合约破坏性）**：`npy.ts` 旧实现 `hlen = dict.length`（未含尾部 48B 空格 padding），
但数据前补了最多 48B 空格 → numpy 读取方落在数据前 48B，形状解析失败。修正为
`hlen = dict.length + pad`，并用回归测试锁死。此 bug 会在 Python 端让所有训练崩溃。

### Python 训练侧（`battle2/nn-training/`，随仓库同仓跟踪，不在 vite 构建，纯 PyTorch/CPU）
| 文件 | 职责 |
|------|------|
| `schema.py` | 唯一定义源：14ch/26²/24/scalar、5-2-3 头、10 维 mask、`OBS_SCHEMA_MAJOR=1`、mirrorX 索引 |
| `npyio.py` | 读写 npy（与 TS 写入器契约一致） |
| `model.py` | conv backbone（无 BN/dropout→可复现）+ GAP + FC + 3 factored heads；~77K 参数（≤200K） |
| `weights_io.py` | 权重 JSON+base64 导出，供 TS 运行时加载 |
| `dataset.py` | 分片加载 + mirrorX 在线增广（训练集专用），mask 切分 `[:5]/[5:7]/[7:10]` |
| `train_bc.py` | 行为克隆：逐头 masked CE；AdamW + cosine；输出权重 JSON + .pt checkpoint |
| `eval_bridge.py` | TS↔Python 评估桥 |
| `smoke_test.py` | 无语料前向/反向冒烟 |
| `validate_export.py` | **纯 stdlib npy 校验器**（不依赖 numpy/torch），锁死 TS→Python 契约 |
| `requirements.txt` / `.gitignore` / `README.md` | torch==2.7.1 + numpy==2.1.3（CPU，清华镜像 cp313 轮）；锁 commit `e1a144ca…`（branch `new-ai`） |

## 契约验证结果（真实语料，2026-08-18 全量重导）

- 对 **14 个 NDJSON 文件、69 局回放**跑 `export-observations`：
  - **66 局保留**（3 局被 desync 门剔除 = 录制时基地被毁）。
  - 输出 `tmp/nn-export/`：**66 分片，43,566 样本**（69 局 → 66 保留 / 3 剔除）。
  - 每局样本 421–1,094 ∈ [0.4K, 1.2K] 门内（与计划 M0b 门④一致）。
- `validate_export.py` → **PASS**：
  - 66 分片 / 43,566 样本 / mask 违例 **0** / scalar 越界 **0** / obs 非空 43566/43566
  - condition 分布：`turn=3437 fire=1025 item=8 subsample=21151`
    （item 事件极稀疏，仅 8 例 —— 与计划 N3 风险一致，item-head 需兜底）
- `bun run check`：1424 pass / 23 skip / 0 fail。

## M0b 门禁闭环状态（审核后补完）

依据实测审核（非仅读文档）的 4 项缺口，已全部闭环：

| 审核缺口 | 门禁 | 措施 | 结果 |
|----------|------|------|------|
| ① 语料过时（37→43 局，缺 s13-s14） | — | 全量重导 14 文件 / 69 局 | ✅ 43,566 样本 |
| ② 无确定性字节比对模式 | gate ② | 新增 `--verify-determinism`：同回放导出两次逐字节比对 | ✅ 5/5 可导出局字节一致 |
| ⑤ item 静默丢失未交叉核对 | gate ⑤ | 新增 `verify-item-events.ts`：导出 item 数 ≤ 回放 guard/frenzy 位变化数 | ✅ 8/8 映射，phantom=0 |
| ⑥ 性能基准缺失未记录 | gate ⑥ | 编码计时写入 `_export_report.json` + env | ✅ 22.343 µs/tick（<0.3ms 预算 13×） |

- **确定性（gate ②）实测**：`--verify-determinism` 对 s1-s2 六局双导，5 局字节一致、1 局被 desync 门正确跳过（`BASE DESTROYED`）。`tmp/nn-det/_determinism_report.json` 记录。
- **item 交叉核对（gate ⑤）实测**：43 局 `nItem`(8) == `nItemEvents`(8)，`phantom=0`；8 例分布在 s1-s2#2(2) / s7-s8#0(4) / s9-10#3(2)。`priorityOverride=0`，即无因优先级改标而"丢失"。
- **性能（gate ⑥）实测**：`bun 1.3.14 / win32 / x64 / 16 核`，215,907 ticks 编码 4,824ms = **22.343 µs/tick**，远低于 <0.3ms/tick。环境写入报告 `env` 字段。
- 审核 minor 已补：`--skip-verify` 用法写入报告（`skipVerify:false`）；"1422 pass" 漂移实为 **1424**（新增 2 个 npy 测试），已校正。

## 当前阻塞 & 进行中

- **torch 安装进行中**（task `6u9j6y`）：受管 Python 3.13.12，`torch<2.6.0` 无 cp313 轮 —— 已将 `requirements.txt` 钉到 `2.7.1`（镜像上 cp313 最低可用版；2.6–2.13 皆兼容）。
  pip 走清华镜像 `https://pypi.tuna.tsinghua.edu.cn/simple` 装 `numpy==2.1.3 + torch==2.7.1`（216MB CPU 轮），下载中。
- 装好后立即执行：
  ```
  python smoke_test.py
  python train_bc.py --data-dir D:/github/battle2/tmp/nn-export --epochs 40   # 权重默认写入 nn-training/weights/
  ```
  确认损失在真实语料（43,566 样本）上下降。

## 环境坑（已沉淀）
- **venv 必须走 pwsh（PowerShell 7）+ 原生 `C:\` 路径** 创建：Bash(msys) 下 `python -m venv` 静默 no-op
  （sandbox 丢弃 `~/.workbuddy` 写入），且 `/c/` 在 `dangerouslyDisableSandbox` 下被错拼成 `d:\c\`。
- 改用 pwsh：`$PyExe -m venv --copies --without-pip`。
- **torch 版本 × Python**：3.13 解释器只能用 `torch>=2.6.0`；`torch==2.5.1` 永远装不上，勿回退。
