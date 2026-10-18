# Plan: course-metrics-persistence — tmp/ 课程指标**自动转存备份** + 冷存打包（内存解压读）

> **状态**：**v3**（2026-10-18 吸收评审 `plan/course-metrics-persistence.review-bf.md` F1–F15；v2 同日、v1 同日首稿）。
> 路径基准除注明外相对仓库根。
> **v3 修订（评审发现，全部已并入本文）**：§3.3 冷存读改**异步 `Bun.spawn`**（F1：`spawnSync` 阻塞单进程 +
> Node `maxBuffer` 1MiB ⇒ 37MB 成员必失败）· 成员存在性走**打包时索引**（F2）· 转存扫描面改**白名单**（F3：
> 「全树」会多抄 95MB `deliver/` 副本并把 `pytest-tmp/**` 当课程）· 派生缓存**只写 live**（F4）· 冷存判据
> 改「tmp 无同名目录 + 防抖动 + 世代 K=2 + `history/` 上限」（F5）· 动作闸表（F6）· memo 256MiB 并按实测
> 37.4MB 定量（F7）· 锁**原子创建**（F8）· 重写型**先哈希**（F9）· `BCITY_TAR` 自检（F10）· DoD 补 6 类
> 判据（F11）· §1.3 换本次实测 477.3MiB/70 目录（F12）· M4 两片同级、M3 提前（F13）· 引用与 actuals
> 边界（F14/F15）。
> **v2 修订依据（用户口径，2026-10-18，四条）**：
> ① **清理闸不拒绝删**——`tmp-clean` 先把指标**自动镜像转存**到备份库，**然后照常删**；
> ② **每门课一个备份目录**（隔离、可单课查/单课恢复）；
> ③ **备份目录超过 14 天自动打包压缩**（冷存）；
> ④ **读取时在内存解压缩**（不落盘解压；落盘只发生在显式 `restore`）。
> **触发**：2026-10-17 `tools/tmp-clean.py` 事故——1907 项 / 38GB（含**全部课程账本**：`training_log.jsonl` /
> `eval_log.jsonl` / `dist-agent-meta.jsonl` / `.pool-actuals-cache.json`）一次 `rmtree` 抹掉，**不可恢复**
> （`DECISIONS.md:7123-7129` · `.workbuddy/memory/2026-10-17.md:35-45`）。tmp 全树**没有第二份副本** ⇒
> dashboard 的曲线、轮次、水位、腿剔除、配对裁判全部静默消失。
> **上位**：`DECISIONS.md §377`（tmp-clean 的存在本身）· `§2026-10-17`（deliver 豁免 + 零保留闸）。
> **不冲突**：不动训练语义 · 不动权重保险机制（`§379`）· 不碰 EvalBoard 库 · 不动 `it<N>/` 轮转（设计内回收）。

---

## 0. 一句话目标

> **tmp/ 下那四个「课程读数文件」在 dashboard 侧有一份按课分目录的备份库（`dashboard/data/course-metrics/<课>/`）：
> 服务端与 CLI 增量镜像它；`tmp-clean` 删除前**自动转存**、转存成功即照常删；课程 14 天没活动后整个备份目录
> 自动打包成 `<课>.tar.gz` 冷存；dashboard 读到冷存课时**在内存解压缩**——曲线、轮次、对比、池历史照常显示，
> 只是带一个「备份」徽标。**

事故后的正确表现：`tmp/<课>/` 被清了，dashboard 上**读数仍在**——而且这次不需要任何人记得先跑 CLI。

**v2 的四个机制**：
**(1) 热备份**——ticker（60s + 启动首拍）与 CLI 增量同步四个文件；字节级追加复制 + 锚点校验，零解析；
**(2) 自动转存**——`tmp-clean --apply` 起步先调 `sync`（转存全部将删内容），成功 ⇒ 按原保留策略照删；
失败只跳「含账本的目标」并响亮（I/O 保底，见 §3.5 的边界）；
**(3) 冷存**——课程备份目录最后源活动 >14 天 ⇒ 自动打包 `<课>.tar.gz`（含 meta 与 `history/`）后原目录退役；
**(4) 内存解压读**——读侧解析器三态 `live` / `mirror` / `archive`：archive 走 `tar -xzO` 流式解压进内存
（带 LRU 内存上限），不落盘。

**诚实账（先说不承诺什么）**：
- **不承诺防盘死**：备份库在同一块盘（`dashboard/data/`）。它防**误删**，不防盘坏/格式化——那层归
  「仓库外导出」（§3.6，人工纪律）。
- **不承诺可续训**：给的是**读数与水位**，不是权重/优化器态；`restore` 只把文件放回 tmp，不等于能续跑。
- **不承诺 reopen 合并**：同名课重开时显示**当次 run**；旧 run 的冷存包留在库里（`<课>.<ts>.tar.gz`），
  靠徽标 + 本文档如实说明。
- **不承诺零成本**：备份库稳态 ≈**490.1MiB** 原始（2026-10-18 真库实测，§1.3）+ 冷存包 ≈45–50MB 量级
  （gz ~10× 经验）；内存解压有 LRU 上限（缺省 **256MiB**，env 可调）——单件实测最大 **37.4MB**（eval_log），
  一门课三件 ≈38MB，**有界，但不小**。
- **actuals 只保缓存覆盖的轮**：备份集是 `.pool-actuals-cache.json`（≤500 轮、schema 门闩）；
  `it<N>/per-game.json` 与 manifest **不备份**（§2 非目标）⇒ 缓存未覆盖的轮在 tmp 清理后 actuals 不可恢复。
  这是设计内接受的损失，写在这里而不是藏在实现里（评审 F15）。

---

## 1. 现状实测（为什么现在会全丢）

### 1.1 数据流全景

```
python 训练/hub 写 ──→ tmp/<课程>/{training_log,eval_log,dist-agent-meta}.jsonl   （append-only，多写者）
dashboard 派生写  ──→ tmp/<课程>/.pool-actuals-cache.json                      （重写型，本机自己写）
                          │
                          ▼
dashboard 读面（8 个入口）── 全部只看 tmp/ ──→ tmp 被清理 = 读面全空、课程消失、曲线消失
```

**没有任何一层「读侧」或「清理侧」知道账本还有没有别的副本**——这是事故的完整根因。

### 1.2 读侧：所有入口都以 tmp 的存在为判据（`file:line` 逐条核过）

| # | 入口 | 位置 | 判据 / 清理后的表现 |
|---|---|---|---|
| R1 | 课程发现 | `dashboard/src/server/api/courses.ts:44-86`（`discoverCourses`） | `tmp/<课>/training_log.jsonl` 能 `statSync` 才算课 ⇒ 清理后课程从 select 与总览**消失** |
| R2 | 主视图指标闸 | `dashboard/src/server/api/state-view.ts:59-72` | `existsSync(tmp/<课>/training_log.jsonl)` ⇒ `available:false`，iters 表整片空 |
| R3 | 对比趋势 | `dashboard/src/server/api/compare-trends.ts:178-190`（+`ledgerFingerprint:121-132`） | 无账本 ⇒ `unavailable:'无账本'`；账本空 ⇒ `'账本不可读'` |
| R4 | 总览「轮次」列 | `dashboard/src/server/api/overview.ts:349-357`（`courseIter`） | 读不到尾部 ⇒ `null` ⇒ 全部显示 `—` |
| R5 | BC epoch/eval 面板 | `dashboard/src/server/api/ledger.ts:151-177` | 空态（epochs/evals 空数组） |
| R6 | 池历史/水位/腿 | `dashboard/src/server/pool-history.ts:948+`（`scanPoolStreams` 扫 `dist-agent-meta.jsonl` 972 行 / `training_log.jsonl` 984 行）· `lastCompletedIterInfo:682-696` · `readOfflineLegs:857-899` | 流消失 ⇒ 节点贡献/池曲线/离线腿剔除/水位全停摆（y20 三条腿水位归零正是此条） |
| R7 | 逐轮 actuals 抄录 | `dashboard/src/server/api/snapshot-refresher.ts:140-164` | 无账本 ⇒ `continue` ⇒ 不再抄录（存量缓存随 tmp 一起死） |
| R8 | 课程热加载判决 | `dashboard/src/server/api/snapshot-cache.ts:213-221` | `courseEdit` ⇒ null |

连带（同族，P2）：`state-view.ts:43-47` 收官横幅 · `eval-board/ingest.ts:148-149`（EvalBoard 开启时
「只在前台看时才入账」的冻结问题会因 tmp 消失进一步恶化）· `courses.ts:18-29` `sanitizeViewCourse`
切课校验 · `paired-seed-receipt.ts:83`。

### 1.3 写侧：四个文件的格式与体量（2026-10-18 实测）

| 文件 | 数量/体量 | 形态 | 写者 |
|---|---|---|---|
| `training_log.jsonl` | **16 文件 / 7.0MiB** | append-only 多写者（`iteration` / `run_start` / `run_complete` / `course_edit` / `bc_*` / `offline_artifact` / hub `job_*`） | 训练循环 + hub |
| `eval_log.jsonl` | **16 文件 / 417.1MiB** | append-only 逐局行；重导按 `(iter,wver,stage,seed)` 去重为 no-op | 训练/eval 腿 + 回灌 |
| `dist-agent-meta.jsonl` | **16 文件 / 52.5MiB** | append-only 逐局 | 采样节点 |
| `.pool-actuals-cache.json` | **16 文件 / 0.74MiB**（单件最大 65KB） | **重写型**（JSON 整份替换，schema≥v4 门闩，只留最近 500 轮） | dashboard 自己（`iters.ts:516` 路径 / `:563` 写盘 / `:1371` 调用） |

- 四者**都没有轮转/截断**，读方容忍 EOF 半行 ⇒ **字节级追加复制天然安全**；全量合计 ≈ **477.3MiB**
  （16 门课的账本；`find tmp -maxdepth 2`，2026-10-18 快照——**文件在长，别把数当常量**）。
- **M1 落地后真库重测（2026-10-18 21:5x，同一口径）**：**76 文件 / 490.1MiB（19 课有账本）**，
  单件最大 **35.67MiB**（`y20-far/eval_log.jsonl`）。全量 `sync` 实测见 §10 末行（均从 0 建库）。
  顶层目录 **70** 个（含 `pytest-tmp` / probe / `deliver/` 子树，它们**不进**备份集，见 §3.5）。
- 先例可复用：`pool-history.ts` 已有**游标 + 64B 锚点 + 分块读**（`readChunkLines:1179-1232` ·
  `readAnchor:833-847` · `legsCursors:825`，2026-10-17 修 OOM/全量重扫时落地）——镜像引擎用同一套路。
- **14 天口径与 tmp-clean 对齐**：`tmp-clean` 缺省 `--keep-days 14`；备份库的「打包压缩」阈也用 14 天
  （口径 = **最后源活动**，见 §3.2-5），两个 14 天是同一个语义（「两周没动过 = 冷」），不引入第三个时间概念。

### 1.4 已有机制为何都不够

| 机制 | 现状 | 为什么救不了本次 |
|---|---|---|
| `tmp-clean` `refuses_zero_retention`（174-183） | 只闸零保留+`--apply` | 常规 keep-runs 3/14 天的定期收敛同样会删老课账本；且它只是**拒绝**，不提供出路 |
| `deliver/` 长期资产豁免（`DECISIONS §2026-10-17`） | 保住导入产物 | 与四个账本文件无关 |
| `course_archive.py` 封存 | gz 逐件 + manifest | 只服务退役课程；dashboard 明令不解压 ⇒ 封存后读数不可读；且要人工发起 |
| EvalBoard（`dashboard/data/evalboard/`） | 有 README + backup.ts 先例 | 缺省停用；只存 eval 行；视图触发入账 |
| `§379` weights in-use 双份 | 权重有保险 | **权重≠读数**；账本类没有对应机制 |

---

## 2. 术语与边界

**术语**（避开已占名词）：
- 「**课程指标备份库**」= `dashboard/data/course-metrics/` 整座库；「**备份目录**」= 每课一个目录
  `<课>/`；「**热备份**」= 目录里的原始追加文件；「**冷存包**」= `<课>.tar.gz`（14 天后自动打包）；
  同步动作叫「**转存/镜像**」。
- **不**用「账本归档」（被 `plan/dashboard-memory.review.md` W7′–W9′ 占用）、**不**用「课程封存」
  （被 `plan/course-archive.plan.md` 占用）。

**备份集 = 恰好这四个文件**（按课程目录）：

```
training_log.jsonl · eval_log.jsonl · dist-agent-meta.jsonl · .pool-actuals-cache.json
```

**非目标**：`it<N>/`（体积主体，设计内轮转；actuals 由第 4 个文件承接，**只到缓存覆盖的 500 轮**——§0 诚实账）·
`deliver/`（已有豁免；转存**显式排除**，它的 `eval_log.jsonl` 副本实测 95MB）·
`pytest-tmp/**` 与 `*.run/**`（测试/探针一次性目录，不是课程）·
`weights.json`/优化器态（§379）· `*.log`/`remote-jobs/`/`replay-export*`（过程物）·
训练侧双写（§7 否决）· 跨 run 合并（reopen 边界）· 仓库外备份自动化（人工纪律，§3.6）。

---

## 3. 设计

### 3.1 备份库布局（每课一个目录 → 冷存包）

```
dashboard/data/course-metrics/                 # 备份库根（env BCITY_COURSE_METRICS 可覆盖）
  README.md                                    # 唯一入库文件：布局 + 备份纪律 + 恢复演练
  .lock                                        # 单写者锁 {pid, host, startedAt}（ticker ∥ CLI ∥ tmp-clean 转存）
  <课程>/                                      # 热备份目录（每课一个）
    training_log.jsonl                         # 字节级副本（append 追加，末行为完整行）
    eval_log.jsonl
    dist-agent-meta.jsonl
    .pool-actuals-cache.json                   # 整份重写（sha256 对账）
    mirror-meta.json                           # 逐文件 {bytes, lines, anchor, srcMtime, mode, sha256?} + lastSourceActivity
    history/<ts>/<file>                        # 复位归档（源被重写/截断时旧副本挪这儿，永不静默覆盖；**上限 K=8 条**）
  <课程>.tar.gz                                # 冷存包（14 天无源活动后自动打包；含四文件 + mirror-meta + history）
  <课程>.tar.gz.index.json                     # 成员索引 {size, mtimeMs, members[]}（读侧判存在性，**不解包**；评审 F2）
  <课程>.<ts>.tar.gz                           # 旧世代冷存包（重打包时产生；**只留最近 2 个**）
  backups/course-metrics-<ts>.tar.gz           # 仓库外导出件（§3.6；不进扫描面）
```

- **相对路径保真**：源允许 `tmp/<课>/<f>` 或 `tmp/<课>/traj/<f>`（两种布局均在用：
  `eval-board/ingest.ts:148-149` · `pool-history.ts:783`）——备份目录**按源相对路径原样存**
  （`<课>/traj/<f>`），读侧候选路径与今天同形，零映射表。
- **为什么住 `dashboard/data/`**：与消费方同仓同项目（evalboard 先例，`.gitignore:64-68`）；env 给换盘出口。
- `.gitignore` 照抄 evalboard 写法（不能整树排除父目录，否则否定无效）：
  ```
  dashboard/data/course-metrics/*
  !dashboard/data/course-metrics/README.md
  ```
- `mirror-meta.json` 是**唯一状态源**（游标/锚点/复位史/最后源活动）；打包时随文件进冷存包。

### 3.2 引擎（字节级增量，零解析）

**A. 同步（ticker / CLI / tmp-clean 转存共用）**，对每个（课程，备份文件）：

0. **分型**：append 三件走「stat 快路 + 锚点 + 增量追加」；`.pool-actuals-cache.json`（重写型）走
   「**先 sha256** 再比 meta」——快路对重写型**不生效**（同尺寸同 mtime 的改写会漏检，评审 F9）。
1. **stat 快路**（仅 append 型）：`源 size == meta.bytes ∧ 源 mtimeMs == meta.srcMtime` ⇒ 跳过（**不读一个字节**）。
2. **锚点校验**：读源 `[bytes-64, bytes)` 的 hex 与 `meta.anchor` 比对（`pool-history.ts::readAnchor` 同款）。
   失配，或源 size < `meta.bytes` ⇒ **复位**：现有备份文件 `rename` 进 `history/<ts>/`，游标归零，响亮一行，
   从 0 重抄。**绝不静默覆盖**。
3. **增量抄写**：从 `bytes` 起分块（1MiB）读到源当前末**的最后一个 `\n` 为止**（半行留给下一拍）；
   追加写、`fsync`、更新 meta（`bytes/lines/anchor/srcMtime`，tmp+rename 原子写）。
4. **排序不说谎**：抄完 `utimes(备份文件, 源 mtime)` ⇒ 备份文件 mtime = 源最后活跃时刻（也是 §3.2-5 的判据）。
5. **重写型**（`.pool-actuals-cache.json`）：整份读（>8MB 拒绝 + WARN）、`JSON.parse` 校验、sha256 对账、
   原子替换；sha 相同只更新 meta 的 `srcMtime/size`（**不做 stat 快路**）。
6. **扫描面 = 白名单函数**（评审 F3；唯一实现，单测直接打）：
   `tmp/<名>/{training_log,eval_log,dist-agent-meta}.jsonl`、`tmp/<名>/.pool-actuals-cache.json`，
   外加 `tmp/<名>/traj/<同名四件>`；`<名>` 必须匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`，
   且**排除** `pytest-tmp`、`_*`、`backups`。**不递归**：`deliver/**`、`*.run/**`、`*.out|*.txt|*.log` 一律不进库。
   已封存课跳过（archive 已是长期资产）。
7. **纪律**：单课失败只记数不中断 · 绝不抛 · 绝不写源（tmp 只读）· 绝不删源 · 内存有界（1MiB 块，不 `split`）。

**B. 冷存打包（compaction）**：

- **触发判据**（评审 F5，v3 改定）：`tmp/<课>/` **不存在** ∧ `meta.lastSourceActivity` 距 now **> 14 天**
  （`BCITY_COURSE_METRICS_PACK_DAYS` 可调）∧ **防抖动**：若 `<课>.tar.gz` 或其最近世代包的 mtime
  ≥ `lastSourceActivity` ⇒ 已经有等价冷存，跳过。**不允许**「tmp 仍在但停更」进入打包（否则
  sync 重建目录 → 新目录按旧 srcMtime 判「冷」→ 下一拍再打包，每拍一次 40MB gzip，磁盘无界）。
- **保留上界**：世代包只留最近 **K=2** 个（多余删除并在日志计数）；`history/` 只留最近 **8** 条复位归档
  （每次复位可能拷一份 37MB eval_log）。
- **打包**：先跑一次该课同步（确保没有未转存字节）⇒ `tar -czf <课>.tar.gz.tmp -C <库根> <课>`（含
  `mirror-meta.json` 与 `history/`）⇒ `tar -tzf` 成员数/名单 sanity ⇒ 写出
  `<课>.tar.gz.index.json`（`{size,mtimeMs,members}`，读侧存在性判据）⇒ 若已有同名 `<课>.tar.gz`，先把旧的
  改名 `<课>.<ts>.tar.gz`（旧世代）⇒ `rename` 新包为 `<课>.tar.gz` ⇒ **删原热目录** ⇒ 响亮一行。
  顺序硬约束：**index 必须在删热目录之前落盘**（否则冷存不可读）。
- **节拍限制**：ticker 每拍至多打包 1 门（IO 有界）；CLI `compact` 不限（有预算参数）。
- **幂等/安全**：打包失败 ⇒ 原目录不动（下次重试）；打包中崩 ⇒ 只有 `.tmp` 残件，清理时识别并删除；
  读侧在包未就绪期间仍读热目录（`rename` 是分界点）。
- **工具链实测**（2026-10-18）：`tar` 在位（PATH 上 Git GNU tar 1.35 + `C:\Windows\System32\tar.exe`）；
  `zip` 不存在 ⇒ 冷存与仓库外导出都用 tar.gz（README 记明与 evalboard `backup.ts` 的 zip 口径分化原因）。

**C. 锁**（评审 F8）：`.lock` 单写者（ticker ∥ CLI ∥ tmp-clean 转存）。**创建必须原子**：
`openSync(lock, 'wx')` 成功才写 `{pid, host, startedAt}`；`EEXIST` ⇒ 读旧锁，仅当「pid 死（`core/net.ts`
同款判活）∧ age>10min」才 `rename(lock, lock.stale.<ts>)` 抢占（rename 失败 = 别人抢先，放弃）。
ticker 拿不到锁跳过本拍；CLI 重试 ~30s；T4 用**两个真子进程**并发，不得单进程串行模拟。

**引擎落点**：`dashboard/src/server/course-metrics/{engine.ts,meta.ts,mirror.ts,compact.ts,archive.ts,index.ts}`
（纯函数 + IO 薄壳分层，照 `readChunkLines` 的可测形状）。

### 3.3 读侧回退（用户可见的修复；冷存走内存解压）

**唯一规则**：**读历史 ⇒ 可回退备份（热或冷）；动作/活体 ⇒ 只认 tmp**（起课/杀进程/导出/导入/权重仍 tmp-only；
备份课不提供动作入口，管理页如实显示「无活体」）。

**解析器**（`dashboard/src/server/api/course-source.ts` + `paths.ts` 提供库根常量）：

```ts
type CourseSource = 'live' | 'mirror' | 'archive' | 'none'
resolveCourseFile(course, name): { path: string | null; source: CourseSource }
  live    : <tmp>/<课>/<name> 或 <tmp>/<课>/traj/<name>（存在即算）
  mirror  : <库>/<课>/<name> 或 <库>/<课>/traj/<name>（热备份目录）
  archive : <库>/<课>.tar.gz 的 **index.json 成员表**里有该成员（不 tar -tzf；评审 F2）
  规则    : live ⇒ live；否则 mirror ⇒ mirror；否则 archive ⇒ archive；否则 none（维持今天的 fail-soft）
resolveCourseDir(course)  // 以 training_log 为锚（课程存在性判据就是它）：dir + source
```

**冷存读取 = 内存解压（用户口径-④）**：

- `readCourseMember(course, name): Promise<Buffer | null>`（评审 F1）：**`Bun.spawn(['tar', ['-xzO', '-f',
  archive, '<课>/<rel>']])` + `await new Response(stdout).arrayBuffer()`**——异步、不阻塞事件循环；
  **禁用** `spawnSync`（Node 语义 `maxBuffer` 缺省 1MiB，现网 eval_log 最大 37.4MB 必失败；Bun 同步版
  也会把整页请求停 2–4s）。成员名按索引/`mirror-meta.rel` 解析（`<课>/<f>` 或 `<课>/traj/<f>`）。
  `BCITY_TAR` 可覆盖二进制；启动自检一次，失败即关闭 archive 源并 ERROR 一行（不逐请求重试）。
- **存在性判定**（评审 F2）：`resolveCourseFile` 的 archive 态只信 `<课>.tar.gz.index.json`
  （成员表 + `size/mtimeMs` 对账），**不** `tar -tzf`；索引缺失/尺寸不符 ⇒ 不宣称 archive（读时 fail-soft，
  后台重建一次索引）。gzip 不可随机寻址，任何「先列表再读」的实现都是每请求整包解压。
- **memo（有界）**：`Map<archivePath, {mtimeMs, size, bytes, files: Map<rel, Buffer>}>`，LRU 上限缺省
  **256MiB**（`BCITY_COURSE_METRICS_MEMO_MB`；实测单课三件 ≈38MB ⇒ 128MiB 只装 3 门，对比 4–5 门就抖动）；
  存 **Promise**（不是 Buffer）以去重同一成员的并发解压；按整包逐出，逐出后重读仍必须正确。
- **读侧解析拆分**：把路径型读函数拆成「取内容」+「解析内容」两半，两半各自可测、双源共用：
  - `readIterMetrics(dir)` → `parseIterRows({ trainingLog, evalLog, actualsCacheJson })` + 薄 IO 包装
    （`iters.ts:1217-1291`；`readEvalSummaries`/`readEvalGameWins`/`readPairedReferee` 同级拆分）；
  - 尾读：`readLogTail/readLedgerTail(path)` 增加 buffer 变体（冷存 training_log 通常 ≤ 几 MB，整件读入取尾）；
  - `pool-history` 的 `readChunkLines(path, …)` 增加 `walkLines(text, onLine)` 公用内核（同一套行语义）。

**改点清单（每条给到今天行号）**：

| 优先 | 入口 | 改法 |
|---|---|---|
| **P0** | R2 `state-view.ts:59-72` | 闸与 trajDir 走解析器；`MetricsView` 增 additive `source: 'live'\|'mirror'\|'archive'`（`web/view/console-types.ts:183-189`）；`MetricsTable.tsx` 徽标：mirror=「备份数据」，archive=「备份数据（冷存包）」 |
| **P0** | R1 `courses.ts:44-86` | 第三/四源 = 备份库热目录 + 冷存包（课程名去 `.tar.gz`/`.<ts>.tar.gz`）；tmp 命中优先、按名去重；已封存集合照旧排除；`sanitizeViewCourse:22` 同步放行备份课（切课 `?course=` 可用） |
| **P1** | R3 `compare-trends.ts:178-190` | trajDir/内容走解析器；指纹带 `source`（源切换 ⇒ 缓存自动失效）；不可用原因如实（如「tmp 与备份均无账本」） |
| **P1** | R4 `overview.ts:349-357` | `courseIter` 走解析器（冷存 = 内存取件取尾） |
| **P1** | R5 `ledger.ts:151-177` | 同上（BC 面板） |
| **P1** | R6 `pool-history.ts:948+` | 扫描根加备份库（热目录 stat；冷存包用 meta 的 size/mtime 合成候选，数据读内存解压件）；**同课双份取 tmp（活体优先，防双计）**；`lastCompletedIterInfo`/`readOfflineLegs` 吃 provider |
| **P1** | 派生缓存读写分离 | **只在 `source==='live'` 写 cache**（mirror/archive 不写 tmp、不写库；memo 已缓存内容）——评审 F4：`mkdir -p tmp/<课>` 会让冷存判据永远不成立（`iters.ts:1371`） |
| **P1** | actuals 读取链 | `readRoundActuals`（`iters.ts:489`）→ `readPerGameFileEntries`/`readIterActuals` 也走「内容源」，archive 下回 cache-only（不静默打空）——评审 F15 |
| **P1** | 动作闸 | 每个动作 endpoint 在 `source!=='live'` 时返回 **409 `无活体`**（kickstart/resume/stop/export/import/eval-a/replay）；备份课不提供动作入口不是文案，是拒绝——评审 F6 |
| P2 | R7 `snapshot-refresher.ts:140-164` | 抄录仍只扫 tmp（活体语义），不改 |
| P2 | R8 `snapshot-cache.ts:213-221` · `state-view.ts:43-47` 横幅 · `eval-board/ingest.ts:148-149` | courseEdit/收官/入账的三处尾读接 provider；缺省停用面最后做 |
| 不改 | `kickstart-receipt.ts:274` / `paired-seed-receipt.ts:83` / 全部动作面 | 收据/动作为活体语义，tmp-only 是正确口径 |

**内存诚实**：冷存读取会把该课成员解压进内存（memo 保存）；`status`/日志暴露 memo 占用与逐出次数，
超过上限按 LRU 逐出（不做无界缓存）。这条与 §2「有界但不小」呼应。

### 3.4 定时节拍（ticker）

- `dashboard/src/server/course-metrics-mirror.ts::startCourseMetricsMirror()`：**启动首拍 + 60s 独立节拍**
  （照 `snapshot-refresher.ts:177-215` 的三条独立节拍形状：写盘面不能被 ping/hub 探测预算拖住）。
- 每拍 = ① 同步（预算 `BCITY_COURSE_METRICS_BUDGET`，缺省 64MiB）→ ② 冷存检查（至多打包 1 门）。
- 接线：`server.ts:303` `startSnapshotRefresher()` 旁一行；unref、绝不抛、**绝不在请求路径上跑**。
- 开关：`BCITY_COURSE_METRICS_SYNC=0` ⇒ 停 ticker 写侧（读侧回退不关）；`BCITY_COURSE_METRICS` 是**库根路径**（两者不同名，避免路径覆写被误读成开关）；**读侧回退不随开关关闭**（读已存在的
  备份无害）。

### 3.5 自动转存（`tools/tmp-clean.py`：转存后删，不拒绝）

**新契约：`--apply` 分两段**——

1. **转存段**：先跑一次全量转存 `bun dashboard/scripts/course-metrics.ts sync --course '*' --budget 0 --quiet`
   （`--budget 0` = 不限预算，明写；扫描面 = §3.2-A6 的**白名单**——顶层四件 + `traj/` 四件，
   **不递归** `deliver/`、`*.run/`、`pytest-tmp`；已同步内容 = stat 快路零成本）。
   这是对本计划 §0 承诺「自动镜像转移过去后再删」的直接兑现。
2. **删除段**：按现有保留策略生成目标；对每个**含备份集文件**的目标做一次**廉价核对**
   （append 型 `meta.bytes == 源 size`；重写型 `size+sha256` 一致；缺 meta/不等 ⇒ 未覆盖），
   覆盖 ⇒ 照常删；未覆盖 ⇒ **跳过该目标 + 响亮一行**（只可能发生在转存失败/文件在转存后被追加的窄窗口）。

- **不拒绝删**：工具退出码常态 0；「未覆盖」只在转存段失败（bun 缺 / 锁冲突 30s / 磁盘满）或窄窗口竞态时出现，
  是**I/O 错误保底**（数据不可再生的方向上不允许硬删），不是策略性拒绝——这两者写进 docstring 与输出文案。
- **`--no-mirror`**：显式跳过转存与核对、按旧行为直删（无 bun 机器 / 极端回收场景的逃生门；明写意图才放行）。
- dry-run 打**转存预览**（哪些课有未转存字节，不真转存、不真删）；`.run_rl.lock` 存活照旧跳过运行目录。
- 读数以 ASCII + 路径/数字为主（§377 防 GBK 乱码）：`指标转存: 同步 X 课 / 新增 K KB ｜ 删除 N 目标 ｜ 跳过 M（未覆盖）`。
- 实现纪律：转存段就是**调用 CLI**（不重写引擎——备份的游标/锚点/meta 只有一份实现），CLI 路径从
  `Path(__file__).resolve().parents[1]` 推仓根拼出，禁止猜 cwd。
- 用例（照 `nn-training/tests/common/test_tmp_clean_keeps_deliver.py` 模板加载工具文件）：转存后删 /
  转存失败（注入桩命令返回非零）⇒ 跳过 + 响亮 + 其余照删 / `--no-mirror` 直删 / dry-run 预览 /
  窄窗口竞态（转存后源又被追加）⇒ 跳过。**python 测试一律走 `bash tools/githook/nn-py-safe.sh`**。

### 3.6 CLI（`dashboard/scripts/course-metrics.ts`，`package.json` 加 `"course-metrics"`）

| 命令 | 行为 |
|---|---|
| `sync [--course C] [--budget N]` | 立刻转存（§3.2-A）；打印逐课新增字节/行/复位 |
| `status` | 逐课：热/冷、镜像 bytes vs 源 bytes、落后量、最后源活动、打包阈值倒计时；memo 占用走服务端 `/api/course-metrics/status`（CLI 跨进程读不到 memo——评审 F14） |
| `verify` | 全量对账：热目录逐文件 size+sha256+行数；冷存包用 `tar -tzf` + 成员解压哈希对 meta；非零退出 |
| `compact [--days 14] [--course C]` | 冷存打包（§3.2-B）；打印逐课包体积与耗时 |
| `backup` | 先 `sync`，再 `tar -czf backups/course-metrics-<ts>.tar.gz`（内容 = 库内全部热目录与冷存包 + README；排除 `backups/` 与 `.lock`） |
| `restore --course C [--to tmp] [--force]` | **落盘**恢复（热目录直接拷；冷存包流式解成员写文件）；目标非空默认拒、`--force` 才覆盖；响亮一行 |
| `cat --course C --file F` | 调试：把成员内容打到 stdout（冷存走内存解压；不进任何生产读圈） |

- **备份纪律（写进 README，照 evalboard README:5-13 形状）**：时机 = 每周 + 里程碑 + 课程退役前；
  去向 = 仓库**外**（NAS/云盘），路径记当日 `.workbuddy/memory/`；演练 = `tar -tzf` 列举 + 抽一门
  `restore --to <临时目录>` 抽查；`*.tar.gz` 不入库（注：`.gitignore` 已排库根，仅 README 回捞）。
- **CLI 不是训练操作入口**（AGENTS §5 的「训练操作唯一入口 = 控制台」不适用）：它只读 tmp、写备份库。
- **不回写源的自动恢复**：读侧回退就是「恢复服务」；`restore` 是显式人工动作（自动往 tmp 写 = 隐藏状态）。

### 3.7 读数

- ticker 每拍一行：`[mirror] 拍#N 课 A/B 新增 K KB（预算 B）｜未变 S｜复位 R｜冷存 C（<课>→P MB）｜失败 F`。
- `sync/status/verify/compact` 逐课表；`verify` 对账表；`status` 带 memo 占用。
- tmp-clean：`指标转存: 同步 X 课 / 新增 K KB ｜ 删除 N 目标 ｜ 跳过 M（未覆盖）`。
- 判据：**稳态拍零新字节时不读正文**（只 stat；mtime/size 任一变才付 1 次 64B 锚点读）；一眼可见
  「哪些课还没转存、哪些课快冷了」。

---

## 4. 动作表

| # | 动作 | 文件 | 优先级 |
|---|---|---|---|
| M0-1 | 库根常量 + env 覆盖 + `.gitignore` 两条 + README（布局/纪律/演练） | `dashboard/src/core/paths.ts` · `.gitignore` · `dashboard/data/course-metrics/README.md` | P0 |
| M0-2 | `mirror-meta.json` schema（类型 + 校验器 + 用例）；§1.3 基线体量入档 | `dashboard/src/server/course-metrics/meta.ts` · `dashboard/tests/` | P0 |
| M1-1 | 引擎纯函数：游标/完整行剪切/锚点/预算/复位判定（零 IO） | `dashboard/src/server/course-metrics/engine.ts` | P0 |
| M1-2 | IO 薄壳 `syncCourse/syncAll`：stat 快路、增量追加、fsync、utimes、锁、复位归档 | `dashboard/src/server/course-metrics/mirror.ts` | P0 |
| M1-3 | CLI `sync/status/verify` + `package.json` 脚本 | `dashboard/scripts/course-metrics.ts` · `dashboard/package.json` | P0 |
| **M2-1** | 解析器 `resolveCourseFile/Dir`（live/mirror/none 三态先行，archive 留接口）+ 用例 | `dashboard/src/server/api/course-source.ts` · `dashboard/src/core/paths.ts` | **P0** |
| **M2-2** | 读侧改点 P0/P1（热部分）+ `MetricsView.source` + 徽标 + 解析拆分（`parseIterRows` 等） | `state-view.ts` · `courses.ts` · `compare-trends.ts` · `overview.ts` · `ledger.ts` · `pool-history.ts` · `iters.ts` · `web/view/console-types.ts` · `web/app/panels/MetricsTable.tsx` | **P0** |
| M2-3 | ticker 同步半 + `server.ts` 接线 | `dashboard/src/server/course-metrics-mirror.ts` · `server.ts` | P1 |
| **M3-1** | tmp-clean **转存段**（调 CLI）+ 核对跳过 + `--no-mirror` + dry-run 预览 + 用例 | `tools/tmp-clean.py` · `nn-training/tests/common/test_tmp_clean_needs_mirror.py` | **P0** |
| M4-1 | `compact`：触发判据/打包/索引/旧世代改名/世代 K=2/节拍限制/幂等（ticker 冷存半 + CLI） | `dashboard/src/server/course-metrics/compact.ts` · `course-metrics-mirror.ts` · `dashboard/scripts/course-metrics.ts` | **P0**（与 M4-2 同批） |
| M4-2 | 冷存读取：`readCourseMember` 内存解压 + memo LRU 有界 + 解析器 archive 态 + 读面接上（含 pool-history） | `course-source.ts` · `iters.ts` · `pool-history.ts` · 其余读面 | **P0**（M4-1 的配套：两者必须同批，否则打包即失读） |
| M5-1 | `backup/restore/cat` + README 备份纪律 + 演练一次（tar 列举 + restore 抽查记 memory） | `dashboard/scripts/course-metrics.ts` · `dashboard/data/course-metrics/README.md` | P1 |
| M5-2 | 决策记录：`DECISIONS.md` 新条目（四条用户口径 + 否决 + 术语纪律）+ memory | `DECISIONS.md` · `.workbuddy/memory/` | P1 |

**M0 契约 → M1 引擎+CLI → M3 自动转存（先关：否则这个版本里清理仍直接删）→ M2 热回退+ticker
（用户可见修复）→ M4 冷存（打包 + 内存解压读，同批）→ M5 仓库外备份/恢复 + 记录。**
M2/M3 若按里程碑发版必须同 release（评审 F13）。

---

## 5. 验收判据（DoD）

**门禁**：
- `bun run check` 绿；动过 `dashboard/**` ⇒ `cd dashboard && bun run typecheck && bun run test` 绿；
  动过 `dashboard/src/web/**` ⇒ `bun dashboard/src/server/build.ts` 三份 bundle 过；`bun run build` 过。
- python 用例（tmp-clean 转存）：`bash tools/githook/nn-py-safe.sh -m pytest nn-training/tests/common/test_tmp_clean_needs_mirror.py`。

**行为判据（每条都配反探针——故意做错必须转红）**：

| # | 判据 | 反探针 |
|---|---|---|
| T1 | 增量转存：夹具源 > 备份 ⇒ sync 后 bytes/行数相等、锚点落 meta；再 sync = 零写入（stat 快路，**正文读出 0 字节**） | 去掉 stat 快路 ⇒ 「二拍零读」红 |
| T2 | 半行剪切：源尾半行 ⇒ 备份只落完整行；源补齐后下一拍补全 | 去掉「只落完整行」⇒ 红 |
| T3 | 复位：源被截短/重写 ⇒ 旧副本进 `history/<ts>/` + 响亮 + 从 0 重抄 | 静默覆盖 ⇒ 红 |
| T4 | 锁：并发第二写者不追加（无重复/交错字节）；陈旧锁可抢占 | 去掉锁 ⇒ 并发用例红 |
| T5 | **事故重放（用户场景）**：备份有数据 + tmp 清空 ⇒ metrics `available:true`、iters 来自备份、`source:'mirror'` | 关掉回退 ⇒ `available:false` 红 |
| T6 | 发现与切课：备份独有课程（热+冷两态）出现在 `discoverCourses` 且 `sanitizeViewCourse` 放行；已封存课照旧排除 | 去掉第三/四源 ⇒ 课程消失红 |
| T7 | 对比趋势：备份独有课程出序列（热与冷各一派生）；指纹随源切换自动失效 | 指纹不带 source ⇒ 切源后旧缓存红 |
| T8 | 池历史：备份课的 training_log 参与水位/腿；**双份不双计**（同课 tmp 优先，计数与 tmp-only 全等）；冷存件流式参与 | 双源都吃 ⇒ 计数翻倍红 |
| T9 | 读写分离：备份 source 下派生缓存写回 tmp，备份库字节零变化 | 让 cache 落备份目录 ⇒ 红 |
| T10 | **转存后删**：夹具未转存账本 ⇒ `--apply` 先转存（注入桩记录调用）再删，删后备份里有完整字节；转存桩非零退出 ⇒ 该目标跳过 + 响亮 + 其余照删 + 退出码 0；`--no-mirror` ⇒ 直删 | 去掉转存段 ⇒ 「删后备份无字节」红 |
| T11 | **冷存打包**：最后源活动 20 天的课 ⇒ `compact` 出 `<课>.tar.gz`（`tar -tzf` 含四文件+meta+history）、原热目录退役；5 天的课不动；同课再打包 ⇒ 旧包改名 `<课>.<ts>.tar.gz` | 阈值判断写反/覆盖旧包 ⇒ 红 |
| T12 | **内存解压读**：冷存课（tmp 与热目录均空）⇒ `/api/state` metrics 来自包内、`source:'archive'`、内容与打包前逐字节一致；重复读 memo 命中（不重复 spawn tar） | 每次读都 spawn ⇒ 命中用例红 |
| T13 | **memo 有界**：注入小上限 ⇒ 大课解压后按 LRU 逐出；逐出后重读仍正确 | 无上限 ⇒ 内存用例红 |
| T14 | `restore`：冷存/热备份恢复到空 tmp 后 `source` 翻回 `live`；目标非空默认拒、`--force` 才覆盖 | 默认覆盖 ⇒ 红 |
| T15 | 仓库外导出：`backup` 的 tar 含全部热目录与冷存包、不含 `backups/` 自身；`verify` 对「翻一个字节」（热与冷各一）转红 | 冷存不做成员哈希 ⇒ 红 |
| T16 | 画像诚实：`source` 三态徽标与来源一致（live 无徽标；mirror/archive 必现且文案区分） | 不做徽标 ⇒ 组件用例红 |
| T3b | 扫描面白名单：夹具含 `deliver/<hash>/eval_log.jsonl`、`pytest-tmp/a/b/training_log.jsonl`、`x.run/eval_log.jsonl` ⇒ 库内**零**这类路径 | 全树扫描 ⇒ 红 |
| T5b | actuals 边界：mirror-only 课的 `actuals` 与清理前逐轮相等（cache 覆盖的轮）；archive 下 `readRoundActuals` 不扫空目录（计数 = 0） | `trajDir` 仍指 tmp ⇒ 红 |
| T7b | 冷存合法性：`tmp/<课>` 仍在但停更 20 天 ⇒ 连打 3 拍只产生 **1** 个包、0 个新世代；世代只留 2 | 阈值写反/每拍重打包 ⇒ 红 |
| T12b | 冷存存在性判定不 spawn tar（夹具断言 spawn 计数 = 0）；索引缺失 ⇒ 不宣称 archive | 每请求 `tar -tzf` ⇒ 红 |
| T16b | 动作闸：mirror/archive-only 课对 kickstart/resume/stop/export/import/eval-a/replay 返回 **409 无活体**（不是 500/200） | 任一动作放行 ⇒ 红 |

**性能/内存判据**：ticker 稳态单拍 = 每课 ≤ 4 次 stat、零正文读（夹具断言读字节上限）；
冷存 memo 缺省 **256MiB**、逐出计数可读；**同时查看 4 门冷课** `/api/state` p95 < 1.5s、RSS 增量 < 300MB；
`sync` 全量（§1.3 实测 490.1MiB）与单课打包/解压的墙钟/峰值内存：全量已实测（§10），单课打包/解压仍待实测。

**人工验收（agent 不做）**：真机「`sync` → 手删一门课 tmp（模拟清理）→ 刷新 dashboard」⇒ 曲线/轮次/对比/
池历史仍在 + 「备份数据」徽标；再把该课 mtime 拨旧 + `compact` ⇒ 热目录变冷存包、刷新仍可读（徽标变
「冷存包」）；`restore` 回 tmp ⇒ 徽标消失。读数记当日 memory。

---

## 6. 顺序

1. **M0**（paths/gitignore/README/schema）——零行为变化，先钉存储契约。
2. **M1**（引擎 + CLI）——`sync` 可用；对真 tmp/ 跑一次全量（§1.3 快照 477.3MiB），实测墙钟/内存/落盘。
3. **M2**（热回退 + ticker）——**用户可见修复的第一半**：热备份态下 tmp 被清不再空白（T5/T6/T7/T8/T9）。
4. **M3**（tmp-clean 转存段）——清理侧闭环：**从此清理自动转存后删**（T10）。
5. **M4**（冷存：`compact` + 内存解压读，**必须同批**）——把备份库从「只涨」变成「两周自动降温」，
   且降温后读面不掉（T11/T12/T13）；ticker 冷存半在最后接上。
6. **M5**（backup/restore/cat + README 纪律 + DECISIONS/memory）。

---

## 7. 与既有 plan / DECISIONS 的关系

| 事项 | 关系 |
|---|---|
| `DECISIONS §377`（tmp-clean 存在） | **互补**：保留策略不动，只在其前加「转存段」 |
| `DECISIONS §2026-10-17`（deliver 豁免 + 零保留闸） | **同族续作**：那次「拒绝删」保资产，这次**转向自动转存**（用户口径-①）——不拒绝，但保底方向不变 |
| `§379` weights in-use 双份 | **正交**：权重 ≠ 读数 |
| EvalBoard（`plan/rl-eval-system.md`） | **平行**：独立库；本计划不塞进 EvalBoard；其 ingest 认备份路径为 P2 |
| `plan/course-archive.plan.md`（封存） | **边界**：封存 = 退役正规转移（gz + manifest，dashboard 不解压）；备份库跳过已封存课；两条路互不依赖 |
| `plan/offline-backfeed-resilience.plan.md §7-3`（deliver 豁免） | **同理念**：长期资产不进清理面；账本走「转存 + 冷存」而不是「豁免」 |
| `plan/dashboard-memory.review.md` W7′–W9′ | **术语纪律**：避开「账本归档」；「备份库/冷存包/转存」是本案用词 |
| 训练语义（§15.5 类改动） | **零接触**：语料/课程/奖励/算法一行不改 ⇒ 无需新纪元三件套 |

**用户口径（2026-10-18，四条，本文档的验收基线）**：① 清理闸自动转存后删（不拒绝）· ② 每课一个备份目录 ·
③ >14 天自动打包压缩 · ④ 读取时内存解压。

**已否决备选（记此防重犯）**：① 训练侧双写（耦合控制台布局 + 热路径 fsync）· ② 复用封存机制（人工发起、
dashboard 不可读）· ③ 复用 EvalBoard（缺省停用、只有 eval 行）· ④ rsync 类外部工具（无增量子集语义）·
⑤ 压缩镜像替换原始行（读方要原始行、gz 不可追加）· ⑥ 只做 CLI 无 ticker（覆盖率取决于人记住）·
⑦ 只转存不做读侧回退（文件抄了没人读）· ⑧ 每拍整份重抄（IO 荒谬；游标+锚点已被 pool-history 验证）·
⑨ 新依赖（SQLite 等，须单独论证）· ⑩ 冷存读取落盘解压（违反用户口径-④；临时目录还会把污染带回 tmp 面）。

---

## 8. 风险与回退

| 风险 | 缓解 | 回退 |
|---|---|---|
| 磁盘：热 ≈477MiB + 冷包 ≈45–50MB | `status` 逐课可见；env 换盘；14 天自动降温 | `BCITY_COURSE_METRICS_SYNC=0` 停写（读侧照旧）；急删热目录内容不可逆 ⇒ 用 `--no-mirror` 前先 `backup` |
| tmp-clean 转存段失败（bun 缺/锁/磁盘满） | 只跳含账本目标 + 响亮 + 退出码 0；其余照删 | `--no-mirror`（明写放弃转存） |
| 转存后有窄窗口追加（核对不过） | 该目标跳过（不硬删），下一轮转存后自然可删 | 重跑 `sync` 再 `--apply` |
| 两个写者并发（ticker∥CLI 转存） | `.lock` 单写者 + 陈旧抢占 + T4 | CLI 重试 ~30s；极端 ⇒ 手工 sync |
| 打包与读取竞态 | `rename` 是分界点：rename 前读热目录、后读包；`.tmp` 残件清理 | 读侧两态都试（mirror→archive 顺序） |
| 内存解压把 RSS 拉高（大课 eval_log） | memo LRU 256MiB 上限 + 单包去重 + 服务端 status 暴露占用（T13） | 调小 `BCITY_COURSE_METRICS_MEMO_MB`；该课退回 `cat`/`restore` 人工看 |
| Windows `rename` 替换语义 / tar 在别的机器缺失 | 用例在 Windows 钉住；README 写手动拷贝纪律 | 手动拷贝冷存包；`restore --to` 抽取 |
| reopen（同名课重开，旧包在库） | 热目录优先；旧包留 `<课>.<ts>.tar.gz`；徽标 + 文档如实 | P2 再谈 merge（不承诺） |
| 双源不一致（同课 tmp+备份） | 规则单一：live 优先；pool-history 防双计用例（T8） | 关掉第三/四源（热回退保留） |

---

## 9. 一句话交付判据

> **`sync` 之后（或直接 `tmp-clean --apply`——它自己会先转存），把 `tmp/<课>/` 整个删掉，刷新 dashboard：
> 曲线、轮次、对比、池历史仍在，带「备份数据」徽标；课程两周不动 ⇒ 备份目录自动变成 `<课>.tar.gz`（原目录
> 退役），刷新仍可读（内存解压，徽标变「冷存包」）；`restore` 能把热备份或冷存包放回 tmp；`backup` 的
> tar.gz 在仓库外有一份。**

---

## 10. 落地进度

| 节 | 状态 | 证据 |
|---|---|---|
| M0-1 库根/`.gitignore`/README | ✅ | `core/paths.ts::courseMetricsDir` · `.gitignore` · `dashboard/data/course-metrics/README.md` |
| M0-2 meta schema + 基线体量 | ✅ | `course-metrics/meta.ts`（白名单/meta/index/世代）· §1.3 实测 477.3→490.1MiB |
| M1-1 引擎纯函数 | ✅ | `course-metrics/engine.ts` |
| M1-2 IO 薄壳（sync/锁/复位） | ✅ | `course-metrics/mirror.ts` · `tests/course-metrics-mirror.test.ts`（11 pass；**稳态零正文读**由 `SyncStats.sourceBytesRead` 断言，含备份侧尺寸进快路 + `mirror-missing`/`mode-changed` 复位） |
| M1-3 CLI sync/status/verify | ✅ | `dashboard/scripts/course-metrics.ts` · `package.json` |
| M2-1 解析器（三态 + archive） | ✅ | `api/course-source.ts` · `api/course-metrics-read.ts` |
| M2-2 热回退改点 + 徽标 + 解析拆分 | ✅ | `state-view` / `courses` / `overview` / `ledger` / `compare-trends` · `iters.ts` 内容/解析拆分 · `MetricsTable` 徽标 · `tests/course-metrics-read.test.ts` |
| M2-3 ticker + 接线 | ✅ | `server/course-metrics-mirror.ts` · `server.ts`（`/api/courseMetrics`） |
| M3-1 tmp-clean 转存段 + 核对 + `--no-mirror` | ✅ | `tools/tmp-clean.py` · `tests/common/test_tmp_clean_needs_mirror.py`（7 pass） |
| M4-1 `compact`（打包/世代/索引） | ✅ | `course-metrics/compact.ts` · `tests/course-metrics-compact.test.ts` |
| M4-2 冷存内存解压读 + memo 有界 | ✅ | `course-metrics/archive.ts`（`Bun.spawn` 异步 + 256MiB LRU + index + **`tar --version` 自检**：失败即 ERROR 一行并关 archive 源，`verify` 相关包转红；重打包后旧 memo 条目连字节账一起作废） |
| M5-1 backup/restore/cat + 纪律 | ✅ | CLI 子命令 + README；`restore --force` **不覆盖活体课**（目标有 `.run_rl.lock` 即拒，评审 F11）；**演练待人工**（§5 人工验收） |
| M5-2 DECISIONS + memory | ✅ | `DECISIONS.md §2026-10-18-goalnn-course-metrics-backup` · `.workbuddy/memory/2026-10-18.md` |

**落地后自审（2026-10-18，签入前）**：把 §5 的判据当反探针重跑一遍，抓到五处「测试绿但判据不成立」，均已修 + 补用例：
① **稳态零读**：原 `syncAppend` 先算 `completeEndOfFile` 再判 skip ⇒ 每拍每文件仍读 1MiB 尾窗。改为「源 size/mtime ∧ **备份侧尺寸** 三者都对上游标」才走 stat 快路（备份侧那一项同时钉住「镜像被删/改短」），并以 `SyncStats.sourceBytesRead` 断言第二拍为 0（T1 的反探针第一次真的会红）。
② **镜像丢失**：`mirror-missing` / `mode-changed` 两种复位补上（旧副本进 history，绝不续写出缺前缀的镜像）。
③ **F10 tar 自检**：`tar --version` 首次使用前探一次，失败 ⇒ ERROR 一行 + **关 archive 源**（`archiveHasMember` 返 false ⇒ 课回 `none`，不假装空读数），`verify` 对每个冷存包转红。
④ **F11 restore 活体闸**：目标含 `.run_rl.lock` 时 `--force` 也拒（账本被旧快照覆盖 = 第二起事故）。
⑤ **动作闸漏项**：`evalLadderStop` 补进 `COURSE_MUTATING_ACTIONS`（原表只到 `evalLadderStart`）。另修 memo 重打包后旧条目的字节账只增不减（会让 LRU 提前逐空）与 `discoverCourses` 在 `tmp/` 整目录缺失时提前 `return []`（备份课跟着消失）。

**残留（已记录，不在本批）**：
- **池历史的冷存件不参与**：`pool-history` 聚合是同步路径，冷存读是异步内存解压（评审 F1）。
  现已接入**热备份**（同课 tmp 优先、不双计）；冷存要等聚合异步化，或单独做「冷存→池历史」的流式读。
- **ticker 的 sync 是同步 IO**（64MiB 预算/拍）：真库（490.1MiB）稳态整拍 **0.69s**、首跑 2.86s（§10 末表）；
  预算内一拍可达 64MiB，跨网盘部署时应改异步或调小预算。
- **`history/` 上限 8 条**已落（`HISTORY_KEEP`）；冷存包里带 history，所以 old 包的保留策略只到 8 条/课。
- **真库全量读数的尾巴**：`compact`/`restore` 未在真库上跑过（冷存要等某课两周不动）；单课解压的墙钟只在小夹具里
  量过（450–500ms / 三门课），真库 35.7MB 包装载未实测。

**真库实测（2026-10-18 21:5x，本机 SSD，`tmp` 四件 76 文件 / 490.1MiB / 19 课）**：

| 动作 | 墙钟 | 峰值工作集 | 产出 |
|---|---|---|---|
| 首跑全量 `sync`（空库，另一份探针库，`--budget 0`） | **2.86s** | **62.9MiB** | 19 课 / 新增 489.5MiB / 570,020 行 / 0 失败 |
| 首跑全量 `sync`（正式库，`tasklist` 100ms 采样） | 4.07s | ≥62.9MiB（仅 8 次采样，粗） | 同上；耗时含探针每次起 `tasklist` 的开销 |
| 稳态第二拍（两门 y23 在训，真增量） | **0.69s** | — | 新增 91.8KB（90 行）/ 跳过 72 / 0 复位 0 失败 |
| `status` | 0.65s | — | 19 门热课，落后 0（在训两门瞬时 11,266B / 338B），倒计时 12.6–14d |

=> 稳态单拍 < 1s；内存与账本总量无关（1MiB 块 + 64B 锚点 + 只抄完整行）——62.9MiB 峰值是
全量首跑的进程工作集（含 Bun 运行时与逐课字符串开销），不是镜像内容常驻；
入库后库体积 **96 文件 / 490.4MiB**（19 热目录，0 冷存包）——与 §0 诚实账的量级一致。
