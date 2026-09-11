# EvalBoard 数据目录（gitignore + 人工备份）

本目录是 RL EvalBench（`plan/rl-eval-system.md`）的唯一逐局账本落盘处。
**无 git 兜底** —— `tools/training/data/` 整树被 `.gitignore` 忽略（本文件除外），
误删/换机即丢。备份纪律（强制）：

- **何时备份**：每个里程碑一次（阶梯过门 / 课程 verdict / 网页 Pin）+ 每周一次
  （有活跃训练时）。`backup.ts` 一键导出 `backups/evalboard-<ts>.zip`。
- **备份到哪**：仓库**外**（人工拷贝 zip 到 NAS/云盘；路径记在当日
  `.workbuddy/memory/YYYY-MM-DD.md`）。
- **存档纪元自 P0 起**：P0 落地前 `tmp/` 里的 `eval_log.jsonl` 会被
  `tools/tmp-clean.py`（keep-runs=3 + 14 天）回收，不可追溯；
  自 P0 起的逐局行以本目录 `evalboard/games/YYYY-MM.jsonl` 为准。

布局：

```
evalboard/
  README.md                  # 本文件（唯一入库文件）
  games/YYYY-MM.jsonl        # 逐局 append-only（唯一事实来源）
  batches.jsonl              # 批次台账（同时是 console → runner 的触发队列）
  runner_state.json          # 训练侧心跳（R4-G1：窗口开关/当前批/单元；console 只读）
  ladder.json                # 阶梯定义工作副本（入库正本：tools/training/evalboard/ladder.json）
  baselines/god-<ladder-ver>.jsonl  # God 全阶梯基线（永久保留）
  space_calibration.json     # Δ_space 标定（永久保留）
  milestones/<course>_<ts>.zip      # 里程碑冷备
  backups/evalboard-<ts>.zip        # 一键导出
```

留存（`store.ts` 执行，规格 §3.6）：`games/` 按月分区，近 12 个月全量；
更旧聚合为 per-`(ckpt, rung)` 汇总行 + 逐局行转冷备 zip；
`milestone=true` 行归档到 `milestones/`（主目录只留聚合行）；
`batches.jsonl` / `baselines/` / `space_calibration.json` 永久保留。
体量估算：逐局 ~350 B，500 批/年 ≈ 50 MB/年。
