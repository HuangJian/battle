# 课程指标备份库（course-metrics）

> 事故驱动（2026-10-17 `tools/tmp-clean.py` 一次 `rmtree` 抹掉 1907 项 / 38GB，含全部课程账本）。
> 计划：`plan/course-metrics-persistence.plan.md`；评审：`plan/course-metrics-persistence.review-bf.md`。

## 这里是什么

`tmp/<课>/` 下**恰好四个**课程读数文件的镜像（+ 14 天无源活动后的冷存包）：

```
training_log.jsonl · eval_log.jsonl · dist-agent-meta.jsonl · .pool-actuals-cache.json
```

布局：

```
<课>/<文件>                          # 热备份（append 增量 + 锚点校验；重写型按 sha256）
<课>/traj/<文件>                     # 源用 traj/ 布局时原样保留相对路径
<课>/mirror-meta.json                # 唯一状态源：逐文件 {bytes,lines,anchor,srcMtime,mode,sha256?}
<课>/history/<ts>/<文件>             # 源被截短/重写时的旧副本（永不静默覆盖；最多 8 条）
<课>.tar.gz                          # 冷存包（含四文件 + meta + history）
<课>.tar.gz.index.json               # 成员索引（读侧判存在性，不拆包）
<课>.<ts>.tar.gz                     # 旧世代（最多保留 2 个）
backups/course-metrics-<ts>.tar.gz   # 仓库外导出件（人工搬到 NAS/云盘；本目录自身被排除）
```

**不在这里**（计划 §2 非目标）：`it<N>/`（含逐局 `per-game.json`）、`deliver/`（已有清理豁免）、
权重/优化器态、`*.log`、`remote-jobs/`、测试/探针目录（`pytest-tmp/**`、`*.run/**`）。

## 谁在写

- dashboard ticker（启动首拍 + 每 60s；`BCITY_COURSE_METRICS_SYNC=0` 停写——注意 `BCITY_COURSE_METRICS`
  是**库根路径**，不是开关）；
- `dashboard/scripts/course-metrics.ts sync`（人工/脚本）；
- `tools/tmp-clean.py --apply` 在删除前自动调 `sync`（`--no-mirror` 才直删）。

读侧**永远不写**这个库：`live` 用 tmp、`mirror` 用热目录、`archive` 在内存解压（不落盘）。
冷存读走 `tar -xzO` + 进程内 LRU（缺省 256MiB，`BCITY_COURSE_METRICS_MEMO_MB` 可调；`BCITY_TAR` 可换二进制）；
启动后首次用到冷存时先自检 `tar --version`：失败 ⇒ 服务端 ERROR 一行并**关闭冷存源**（课回「无数据」而不是空曲线；
换 `BCITY_TAR` 后重启即可，或改用 `restore`/`cat` 手工读）。服务端占用见 `GET /api/courseMetrics`
（CLI 是另一个进程，读不到服务端 memo）。

## 命令

```bash
bun dashboard/scripts/course-metrics.ts sync [--course C] [--budget N]  # 增量转存（budget=0 不限）
bun dashboard/scripts/course-metrics.ts status        # 逐课落后量/最后源活动/打包倒计时
bun dashboard/scripts/course-metrics.ts verify        # size+sha256+行数 + 冷存成员哈希对账（非零退出）
bun dashboard/scripts/course-metrics.ts compact [--days 14] [--course C]
bun dashboard/scripts/course-metrics.ts backup        # 先 sync，再打 backups/course-metrics-<ts>.tar.gz
bun dashboard/scripts/course-metrics.ts restore --course C [--to tmp] [--force]
bun dashboard/scripts/course-metrics.ts cat --course C --file training_log.jsonl
```

## 备份纪律（人工，与 EvalBoard 同形）

- **时机**：每周一次 + 里程碑 + 课程退役前 + 换机前。
- **去向**：仓库**外**（NAS / 云盘）。路径记当日 `.workbuddy/memory/YYYY-MM-DD.md`。
- **演练**（每次 backup 后）：`tar -tzf` 列举 + 抽一门 `restore --to <临时目录>` 抽查首尾行。
- **为什么是 tar.gz 不是 zip**：本机没有 `zip`（EvalBoard 的 `backup.ts` 用 zip，那是它的先例，
  这里不复制一个不存在的依赖）；`tar` 在 Git GNU tar 与 Windows bsdtar 上都在位。
- **它不是防盘死的**：库与 tmp 同盘。盘坏/格式化要靠上面这条仓库外导出。

## 诚实边界

- `restore` 只把读数文件放回 tmp，**不等于能续训**（权重/优化器态不在这里）；`--force` 也不覆盖
  正在训练的课（目标有 `.run_rl.lock` 就拒）。
- actuals 只保 `.pool-actuals-cache.json` 覆盖的轮（≤500，schema 门闩）；`it<N>/per-game.json`
  不备份，缓存未覆盖的轮在清理后 `actuals` 不可恢复。
- 同名课重开时 dashboard 显示**当次 run**（live 优先）；旧 run 在 `<课>.<ts>.tar.gz` 里，不合并。
