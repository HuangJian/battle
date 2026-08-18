# NN Weights History

训练产出的权重文件 **不入库（gitignored）**，由你**手动备份到网盘**。本文件是**已提交的权重注册表**，记录每次训练的版本与关键指标。请与磁盘上的 `weights.*.json` 保持同步。

---

## 命名规则 (Naming convention)

* **版本化归档文件**：`weights.<YYYYMMDD-HHMMSS>_ep<N>_val<V>.json`
  * `<YYYYMMDD-HHMMSS>` — 训练结束时间戳（本地时间）
  * `<N>` — 训练轮数（epochs）
  * `<V>` — 最佳验证损失，保留 4 位小数（如 `1.2431`）
  * 例：`weights.20260818-170055_ep40_val1.2431.json`
* **Active 指针**：`weights.json` — 最新版本化归档的**精确副本**，供 TS 运行时（`src/nn/infer.ts`，尚未实现）加载。同样 gitignored。
* 两个文件均位于 `nn-training/weights/` 目录下（本注册表 `WEIGHTS.md` 也在同一目录）。
* 训练脚本 `train_bc.py` 每次运行会**自动**写出版本化归档 + 复制为 `weights.json` + 在本文末尾追加一行历史。

## 备份策略 (Backup strategy)

1. 每次训练结束后，本地生成版本化归档 `weights.<stamp>.json` + `weights.json`（active 指针）。
2. **手动**把新的 `weights.*.json` 复制到网盘（外部备份）。
3. 仅提交 `WEIGHTS.md`（本文件）——它记录当前是哪个版本，权重本体不进 git。
4. 全新 clone 后本地无权重；从网盘取回需要的 `weights.*.json` 放进 `nn-training/weights/` 即可——**加载程序会自动识别最新的一份，无需手工改名**。
   - 自动发现规则（`weights_io.latest_weights_path` / `src/nn/weights.ts:resolveLatestWeights`）：
     优先取文件名时间戳最大的 `weights.<YYYYMMDD-HHMMSS>_ep<N>_val<V>.json`；
     若不存在版本化文件，则回退到 `weights.json`。
   - 想用某个旧版本时，显式传 `--weights <path>`（Python）或对应路径参数（TS）即可覆盖自动选择。

---

## 历史 (History)

| trained_at | file | epochs | samples (train/val) | val_loss | move/fire/item acc | git | notes |
|---|---|---|---|---|---|---|---|
| 2026-08-18T17:00:55 | `weights.20260818-170055_ep40_val1.2431.json` | 40 | 39210/4356 | 1.2431 | 0.662/0.830/1.000 | fcb0cda | initial BC baseline（训练基于 post-fcb0cda 代码，含未提交的 dataset.py / npy.ts 修复） |

---

## 已知限制 (Known caveats)

* **`item_acc = 1.000` 是平凡结果，勿当能力指标**：item 事件仅 24 例（8 局各 guard/frenzy 位变化），远少于 none 样本；item-head 在所有有效样本上恒预测同一类即达 1.000。符合计划 N3 风险预期——解读 val_loss / 三头 acc 时，不应把 `item_acc` 视作模型学到了东西。
* **M1 sim 评估（"能打游戏"）尚未验证**：目前只证明损失在真实语料上下降，未证明权重能在游戏里取胜。计划 M1 门 = BC hard 35×10 smoke ≥60% 胜率，需要 `tools/sim --policy nn` + `src/nn/infer.ts`（推理前向）+ `src/nn/policy-input.ts`（把 obs-encoder 的 obs 喂给模型），三者当前均未实现。这是下一步。
