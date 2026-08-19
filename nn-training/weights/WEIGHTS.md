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
* **M1 sim 评估（"能打游戏"）已跑通，但首版基线不达标（预期内）**：评估管线（`tools/sim --policy nn` + `src/nn/infer.ts` + `src/nn/policy-input.ts`）已完整实现并跑通。首版 BC 基线（ep40, val1.2431）在 **BC hard 35×10 = 350 局全败，胜率 0.0%（门 60% → FAIL）**；但 NN 确实在"打游戏"——avgKills 2.6 / avgTicks 4755 / 350 局全部正常 gameover、无崩溃/无异常。0% 属预期：骨干当前**忽略全部标量输入**（reserved，零成本）、训练轮次与语料远未充分。已启动**连续多轮训练**（`train_loop.py`：全量语料 43566 样本、每轮 40 epoch、自动 `--resume` 续跑、10 分钟心跳），目标是把胜率抬过 60% 门；新权重每轮写出版本化归档 + 更新 `weights.json`。
| 2026-08-18T21:08:12 | `weights.20260818-210812_ep2_val1.1649.json` | 2 | 39210/4356 | 1.1649 | 0.6892/0.8444/1.0 | 757b7e5 |  |
| 2026-08-18T21:41:22 | `weights.20260818-214122_ep40_val1.1320.json` | 40 | 39210/4356 | 1.1320 | 0.7075/0.8517/1.0 | 757b7e5 | round 1 resume weights.20260818-194915_ep40_val1.1350.json |
| 2026-08-18T23:30:19 | `weights.20260818-233019_ep40_val1.1500.json` | 40 | 39210/4356 | 1.1500 | 0.7117/0.8574/1.0 | 757b7e5 | round 1 resume weights.20260818-214122_ep40_val1.1320.json |
| 2026-08-19T00:03:53 | `weights.20260819-000353_ep40_val1.1651.json` | 40 | 39210/4356 | 1.1651 | 0.7101/0.8584/1.0 | 757b7e5 | round 1 resume weights.20260818-233019_ep40_val1.1500.json |
| 2026-08-19T06:43:50 | `weights.20260819-064350_ep1_val1.9140.json` | 1 | 39210/4356 | 1.9140 | 0.4596/0.6641/1.0 | 757b7e5 | venv smoke test |
| 2026-08-19T06:45:38 | `weights.20260819-064538_ep1_val1.7468.json` | 1 | 39210/4356 | 1.7468 | 0.4805/0.7121/1.0 | 757b7e5 | round 1 resume weights.20260819-064350_ep1_val1.9140.json |
| 2026-08-19T06:57:05 | `weights.20260819-065705_ep1_val1.6759.json` | 1 | 39210/4356 | 1.6759 | 0.5117/0.7369/1.0 | 757b7e5 | round 1 resume weights.20260819-064538_ep1_val1.7468.json |
| 2026-08-19T06:58:06 | `weights.20260819-065806_ep1_val1.6230.json` | 1 | 39210/4356 | 1.6230 | 0.5326/0.7544/1.0 | 757b7e5 | round 1 resume weights.20260819-065705_ep1_val1.6759.json |
| 2026-08-19T06:59:05 | `weights.20260819-065905_ep1_val1.5731.json` | 1 | 39210/4356 | 1.5731 | 0.5351/0.7663/1.0 | n/a | round 1 resume weights.20260819-065806_ep1_val1.6230.json |
| 2026-08-19T06:59:47 | `weights.20260819-065947_ep1_val1.5731.json` | 1 | 39210/4356 | 1.5731 | 0.5351/0.7663/1.0 | 757b7e5 | round 1 resume weights.20260819-065806_ep1_val1.6230.json |
| 2026-08-19T07:00:43 | `weights.20260819-070043_ep1_val1.5453.json` | 1 | 39210/4356 | 1.5453 | 0.5494/0.7748/1.0 | 757b7e5 | round 1 resume weights.20260819-065947_ep1_val1.5731.json |
| 2026-08-19T07:01:26 | `weights.20260819-070126_ep1_val1.5215.json` | 1 | 39210/4356 | 1.5215 | 0.5523/0.7805/1.0 | 757b7e5 | round 2 resume weights.20260819-070043_ep1_val1.5453.json |
| 2026-08-19T07:02:10 | `weights.20260819-070210_ep1_val1.5080.json` | 1 | 39210/4356 | 1.5080 | 0.5574/0.7833/1.0 | n/a | round 3 resume weights.20260819-070126_ep1_val1.5215.json |
| 2026-08-19T07:02:56 | `weights.20260819-070256_ep1_val1.4922.json` | 1 | 39210/4356 | 1.4922 | 0.5647/0.786/1.0 | n/a | round 4 resume weights.20260819-070210_ep1_val1.5080.json |
| 2026-08-19T07:03:41 | `weights.20260819-070341_ep1_val1.4798.json` | 1 | 39210/4356 | 1.4798 | 0.5654/0.792/1.0 | n/a | round 5 resume weights.20260819-070256_ep1_val1.4922.json |
| 2026-08-19T07:04:26 | `weights.20260819-070426_ep1_val1.4661.json` | 1 | 39210/4356 | 1.4661 | 0.5744/0.7945/1.0 | n/a | round 6 resume weights.20260819-070341_ep1_val1.4798.json |
| 2026-08-19T07:05:12 | `weights.20260819-070512_ep1_val1.4551.json` | 1 | 39210/4356 | 1.4551 | 0.5728/0.798/1.0 | n/a | round 7 resume weights.20260819-070426_ep1_val1.4661.json |
| 2026-08-19T07:05:57 | `weights.20260819-070557_ep1_val1.4455.json` | 1 | 39210/4356 | 1.4455 | 0.5758/0.7971/1.0 | n/a | round 8 resume weights.20260819-070512_ep1_val1.4551.json |
| 2026-08-19T07:06:42 | `weights.20260819-070642_ep1_val1.4325.json` | 1 | 39210/4356 | 1.4325 | 0.5776/0.8028/1.0 | n/a | round 9 resume weights.20260819-070557_ep1_val1.4455.json |
| 2026-08-19T07:07:26 | `weights.20260819-070726_ep1_val1.4240.json` | 1 | 39210/4356 | 1.4240 | 0.5852/0.8014/1.0 | n/a | round 10 resume weights.20260819-070642_ep1_val1.4325.json |
| 2026-08-19T08:31:00 | `weights.20260819-083100_ep1_val1.4083.json` | 1 | 39210/4356 | 1.4083 | 0.5856/0.8058/1.0 | 6d5241b | round 1 resume weights.20260819-070726_ep1_val1.4240.json |
| 2026-08-19T09:54:29 | `weights.20260819-095429_ep40_val1.1974.json` | 40 | 59850/6649 | 1.1974 | 0.6733/0.8383/0.9998 | 460ecaf | round 1 resume weights.20260819-083100_ep1_val1.4083.json |
| 2026-08-19T10:31:34 | `weights.20260819-103134_ep40_val1.0919.json` | 40 | 61714/6857 | 1.0919 | 0.6974/0.8594/0.9997 | 460ecaf | round 2 resume weights.20260819-095429_ep40_val1.1974.json |
| 2026-08-19T11:08:35 | `weights.20260819-110835_ep40_val1.0974.json` | 40 | 61714/6857 | 1.0974 | 0.7075/0.8609/0.9997 | 460ecaf | round 3 resume weights.20260819-103134_ep40_val1.0919.json |
| 2026-08-19T11:46:11 | `weights.20260819-114611_ep40_val1.1192.json` | 40 | 61714/6857 | 1.1192 | 0.7098/0.8577/0.9997 | 460ecaf | round 4 resume weights.20260819-110835_ep40_val1.0974.json |
| 2026-08-19T12:23:12 | `weights.20260819-122312_ep40_val1.1422.json` | 40 | 61714/6857 | 1.1422 | 0.7082/0.8582/0.9997 | 460ecaf | round 5 resume weights.20260819-114611_ep40_val1.1192.json |
| 2026-08-19T13:00:59 | `weights.20260819-130059_ep40_val1.1499.json` | 40 | 61714/6857 | 1.1499 | 0.7083/0.8588/0.9997 | 460ecaf | round 6 resume weights.20260819-122312_ep40_val1.1422.json |
| 2026-08-19T13:38:11 | `weights.20260819-133811_ep40_val1.1639.json` | 40 | 61714/6857 | 1.1639 | 0.7069/0.8591/0.9997 | 460ecaf | round 7 resume weights.20260819-130059_ep40_val1.1499.json |
| 2026-08-19T14:16:22 | `weights.20260819-141622_ep40_val1.1601.json` | 40 | 61714/6857 | 1.1601 | 0.7102/0.8596/0.9997 | 460ecaf | round 8 resume weights.20260819-133811_ep40_val1.1639.json |
| 2026-08-19T15:07:57 | `weights.20260819-150757_ep40_val1.1625.json` | 40 | 61714/6857 | 1.1625 | 0.7118/0.8587/0.9997 | 460ecaf | round 9 resume weights.20260819-141622_ep40_val1.1601.json |
| 2026-08-19T15:49:00 | `weights.20260819-154900_ep40_val1.1760.json` | 40 | 61714/6857 | 1.1760 | 0.7123/0.8584/0.9997 | 460ecaf | round 10 resume weights.20260819-150757_ep40_val1.1625.json |
| 2026-08-19T16:39:11 | `weights.20260819-163911_ep40_val0.9984.json` | 40 | 61714/6857 | 0.9984 | 0.7248/0.894/0.9997 | 460ecaf | round 1 resume weights.20260819-154900_ep40_val1.1760.json |
| 2026-08-19T17:18:42 | `weights.20260819-171842_ep40_val1.0066.json` | 40 | 61714/6857 | 1.0066 | 0.7308/0.8949/0.9997 | 460ecaf | round 2 resume weights.20260819-163911_ep40_val0.9984.json |
| 2026-08-19T17:54:19 | `weights.20260819-175419_ep40_val1.0172.json` | 40 | 61714/6857 | 1.0172 | 0.7325/0.8959/0.9997 | 460ecaf | round 3 resume weights.20260819-171842_ep40_val1.0066.json |
| 2026-08-19T18:30:16 | `weights.20260819-183016_ep40_val1.0256.json` | 40 | 61714/6857 | 1.0256 | 0.7325/0.8959/0.9997 | 460ecaf | round 4 resume weights.20260819-175419_ep40_val1.0172.json |
| 2026-08-19T19:05:44 | `weights.20260819-190544_ep40_val1.0342.json` | 40 | 61714/6857 | 1.0342 | 0.7368/0.8973/0.9997 | 460ecaf | round 5 resume weights.20260819-183016_ep40_val1.0256.json |
| 2026-08-19T19:41:14 | `weights.20260819-194114_ep40_val1.0481.json` | 40 | 61714/6857 | 1.0481 | 0.7371/0.8978/0.9997 | 460ecaf | round 6 resume weights.20260819-190544_ep40_val1.0342.json |
