# NN Weights History

训练产出的权重文件 **不入库（gitignored）**，由你**手动备份**（网盘 / 本仓库
`nn-training/weights/` 的 zip 备份）。本文件是**已提交的权重注册表**，记录每次
备份的版本与关键指标。请与磁盘上的 `weights.*.json` / `*.zip` 保持同步。

---

## 命名规则 (Naming convention)

* **版本化归档文件**：`weights.<YYYYMMDD-HHMMSS>_ep<N>_val<V>.json`
  * `<YYYYMMDD-HHMMSS>` — 训练结束时间戳（本地时间）
  * `<N>` — 训练轮数（epochs）
  * `<V>` — 最佳验证损失，保留 4 位小数（如 `0.5084`）
  * 例：`weights.20260904-100231_ep60_val0.5084.json`
* **Active 指针**：`weights.json` — 最新版本化归档的**精确副本**，供 TS 运行时
  （`src/nn/infer.ts`）加载。同样 gitignored。
* **整包备份**：`battle-p1bc-ep60.zip` 等 zip — 一次训练 run 的完整快照
  （版本化归档 + active 指针 + 每 epoch ckpt + resume 状态 + 语料），
  同样 gitignored（`.gitignore` 的 `*.zip`）。
* 训练脚本 `train_bc.py` / `train/bc.py` 每次运行会**自动**写出版本化归档 +
  复制为 `weights.json` + 在 run 目录的 WEIGHTS.md 末尾追加一行历史。

## 备份策略 (Backup strategy)

1. 每次训练结束后，本地生成版本化归档 `weights.<stamp>.json` + `weights.json`
   （active 指针）。
2. **手动**把权重 zip / json 备份到网盘（外部备份）或本仓库
   `nn-training/weights/`（zip 不入库）。
3. 仅提交 `WEIGHTS.md`（本文件）——它记录当前是哪个版本，权重本体不进 git。
4. 全新 clone 后本地无权重；从网盘 / `nn-training/weights/` 取回需要的文件即可。
   - 自动发现规则（`weights_io.latest_weights_path` /
     `src/nn/weights.ts:resolveLatestWeights`）：优先取文件名时间戳最大的
     `weights.<YYYYMMDD-HHMMSS>_ep<N>_val<V>.json`；若不存在版本化文件，
     则回退到 `weights.json`。
   - 想用某个旧版本时，显式传 `--weights <path>`（Python）或对应路径参数（TS）
     即可覆盖自动选择。

---

## 历史 (History)

| trained_at | file | epochs | samples (train/val) | val_loss | move/fire/value | git | notes |
|---|---|---|---|---|---|---|---|
| 2026-09-04T10:02:31 | `battle-p1bc-ep60.zip`（内含 `weights.20260904-100231_ep60_val0.5084.json` + `weights.json` + `ckpt.1..60` + `run_state.json` + `resume_seed.json` + 语料） | 60 | 149039/16566 | 0.5084 | 0.8616/0.929/0.7022 | 37fcc7b | p1-BC 全量（Colab T4，`train/bc.py --device cuda`，seed 1234）。p1-onset 贪心评估 **94/100** ≈ 教师 92/100（docs/rl.progress.md §1）。**p4-BC 热启动源权重**（p4-onset.ipynb 的 WARM_ZIP）。 |

---

## 已知限制 (Known caveats)

* **`move/fire/value` 列里的 value 是辅助头（0.5 系数），噪声大**：judge 时看
  move/fire acc 与 val_loss 的趋势，勿盯单轮 value。
* **`--resume` 是权重 warm-start（DECISIONS §325）**：AdamW / 余弦 LR 调度不随
  权重延续，续跑时 LR 从头再来。要接近原调度，尽量一次跑长，仅在中断后走 resume。
* **多敌课程（p4-onset）cap 语义**：`max_ticks 2400` 原为单敌标定，4 敌局若
  超时占比过高需按实测重标定（DECISIONS §327 / docs/rl.progress.md §2）。