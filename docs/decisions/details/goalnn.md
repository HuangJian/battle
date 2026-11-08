# 决策正文归档 — goalnn

> 2026-11-08 从 `DECISIONS.md` 搬出的决策正文（索引行与编号仍在 `DECISIONS.md`）。

## §2026-10-11-goalnn-rollout-topup-fastpath（2026-10-11，rollout 热路径四刀：探针上界**另立常量** + 补差批按机器宽度铺满 + 轮级 `RoundContext` + **取消搬回**）

- **背景**：Kaggle 自主 worker（96 核 / 94 槽）上**同一轮内相邻两批天差地别**（it34 补差1（18 局）3.35s vs
  补差2（**1 局**）48.90s；it49 同形 3.00s → 52.49s；54 轮 p90 88.9s / max 111.2s）。现场四条否定式同时成立
  （≥5s 有 0 局 / 重试 0 / 池 killed=0 unready=0 fallback=0 / 搬回 0 失败）⇒ 既不在跑局、也不在重试、池回退、搬回超界。
  两条根因（均已在代码上闭合）：① `scratch.open_scratch` 是这条热路径上**唯一没有 `call_bounded` 的一步**
  （`probe_write_speed_mbps` 只有 `except OSError`；8MB+fsync 每批付一次）；② 补差批大小由**缺口方程**
  （`ceil(缺口/est)`）决定 ⇒ 批越补越小，而**批次级固定开销与批内局数无关**（§34 的「逐局 spawn 重付启动成本」
  在批次层面的重演）。
- **决定**：
  - ① 探针上界**另立** `game_watch.PROBE_CEILING_SEC = 5.0`（本机实测 8MB 探针 0.009–0.015s / 690–1250 MB/s
    ⇒ ×300 余量）；**不复用** `SCAN_CEILING_SEC`（`call_bounded` 超界要等满 timeout，60s 与「补差批 < 8s /
    rollout max < 30s」不可兼得；而扫盘那 60s 管的是几百个小 JSON + 内容哈希，不能被探针压低）。
    超界 ⇒ **逐字回退档** + 响亮一行 + 轮报 `probe_probe` 读数（盘抖的直接读数，不是失败）。
  - ② 新增 `worker/volume_alloc.spread_games_by_stage`：补差批 = `width`（首批**实际用过的** workers）局，
    轮转均分给「未达标且未触顶」的关（顺序 = `stages` 给定序 ⇒ 确定性）；不吃 est / 不吃 `last_lo_factor`；
    超采（相对 quota）丢弃（`wasted_samples` 上升，**软指标**）；`game_cap` 仍是**硬顶**。
  - ③ 新增 `iter_rollout.RoundContext`（**轮级**资源的唯一住处）：只装 `layout`（轮级 scratch 布局，
    `root` 是它的属性）/ `ts_home` / `job_dir` / `pool` / `workers` + `resolved`/`probe_sec`；
    **批级**的 `jd`/`s_layout`/`exec_dir`/`gate` **不进** ctx（每批一个 `topup{k}/`，批级布局由
    `batch_layout(layout, batch_rel(轮 job 目录, 批 job 目录))` 镜像推出来）。**池的生涯 = 轮**：
    首批建、补差批复用，调用方（`remote/worker.py`）在**补差跑完、PPO 之前** `close()`
    （不让 94 个常驻 bun 陪 PPO 吃内存）；`ctx=None`（老调用）⇒ 本轮自建自收，逐字今天。
    池**建不起来**时（`make_pool` 返回 `None` / `start()` 0 就绪）**不**缓存进 ctx ⇒ 下一批再试（冷启动失败多为瞬态；
    而确定性拒绝下的重试也只是常数级判断）——这是有意的，不是漏缓存。
  - ④ **取消搬回**（用户第 1 条，§3.5）：产出就住在节点本地盘的轮 scratch 目录里，`shard_dirs` 报真实路径；
    节点自己的 PPO 装载根改由**产出方显式给出**（`iter_info["shards_root"]` → `train_core.ppo_shards_root`，
    评审 P0-1——漏了就是「读 0 局语料静默训空」）；回收从「每批 `finally`」移到**全 job 收尾、PPO 之后**
    （`RoundContext.cleanup()`），并新增**轮初清场**（否则上一轮/上次尝试的残留会被轮末对账当成实产）；
    容量判据改成**整轮**需求（`_round_need_bytes` = 单批局数 × (1 + `volume.topup.max_batches`) × per_game，
    评审 P0-3）；批的产出根**镜像**进轮 scratch 目录（`scratch.batch_layout`/`batch_rel`，越界响亮）——
    否则补差批的产出没有共同祖先可被 PPO 的**单一**读取根扫到。
    ⚠ **判据前移**（代价写在这里）：F7「搬不回去的局不算结算」随搬回一起消失，结算判据简化为
    「跑局 future 正常返回」；「结了算却没产出」的发现从**搬回那一刻**退到**轮末逐位对账**
    （`verify_shards`：实产集 ≠ 声明集 ⇒ 响亮 `ProtocolError`）。也就是说失败的**发现变晚**，但它们
    仍然是响亮的（不静默少一局）。
  - ⑤ **§3.4 降级为小形状**（2026-10-11 第四刀，plan §3.4 末段）：原计划的
    `worker/iter_verify.verify_round`（「扫全轮再按声明集分组」）在 §3.5 把批产出根**镜像进轮目录**
    之后已无必要——**批根本身就是分组单位**（每批各验各的 = 按批分组，且失败当场）。真正的重复读
    只剩「同一批 manifest 为 `perGame` 读一遍、又为补差记账扫一遍盘」⇒ 改成 `_batch_totals` 优先吃
    批次报告里的逐局画像（字段逐字同名，等价性用例钉住），缺画像回落磁盘扫。轮级报告装配契约
    （`shards`/`perGame`/`perGameSecs`/`elapsedSec` 由调用方一次装配、同键同型）写进 `remote/worker.py`。
    **没做** `NN_ROUND_SCAN=0`：没有「轮级一次扫盘」可关。
- **备选与否决**：① 「探针整段不跑，直接原地跑」——否（本地盘换根是 §34 的既有收益，回退只在**真超界**时发生）；
  ② 「复用 `SCAN_CEILING_SEC`」——否（两个语义共用一个数 ⇒ 必有一头错）；
  ③ 「缺口解算但设下限（如 ≥ width/2）」——否（下限是另一个要标定的魔数；「批 = 机器宽度」不需要）；
  ④ 「cap 不截断」（plan v3 原文）——否（课程字段是操作员的**硬省**，且改造前就有活判据钉着
  `games_by_stage == {2000: 10}`：保留活判据比改写它划算）；
  ⑤ 「宽度猜一个（缺省 = 首批 argv 条数就已足够）」——否（批大小直接决定墙钟；拿不到就去**不补差**）；
  ⑥ 「ctx 里连批级布局一起缓存（plan v4 的字段草图）」——否（那会让第二节批把产出写进**第一批的** exec 根，
  与「批目录隔离 / `--out` 续号 / 批级清理」相冲）；  ⑥b「把池生命周期交给 `remote/worker.py` 单独管理
  （不经 ctx）」——否（池的建与用都在 `run_iter_rollout` 内，多一条旁路就要同步一份 ts 树/宽度，
  ctx 是同一个信息的单一来源）；
  ⑦「取消搬回但保留『每批一个独立 scratch 作业目录』」——否（PPO 只收**一个**读取根，而各批目录
  彼此不是祖孙关系，`<根>` 又是整个本地盘 ⇒ 必须镜像进一个轮目录）；
  ⑦b「产出不回 job 目录但让 PPO 收一串根（`shards_roots: list`）」——否（`discover_shards` 的调用面、
  D14 与合并账都得跟着改成复数；镜像一个目录只需三行，且与回退档形状同构）。
  ⚠ 搬回原语（`scratch.drain_tree` / `InstallGate` / `drop_drain_stage`）**保留**（有全套用例）但
  **已无生产用点**（模块头已标注）——删它要另开一刀（连带清用例），本轮只降为“备用腿”。
- **后果**：§3.1/§3.2/§3.3/§3.4/§3.5 已落地（探针有界 + 轮级 ctx + 批=机器宽度 + 扫盘去重/装配契约
  + 取消搬回/PPO 读取根/整轮容量）；§3.6–§3.10（回传失败备份 / 池宽 / 探针缓存 / 读数五分项 / LAN 队列）
  仍在队列。§3.5 那个**已定案的前置已实现**：`remote/train_core.py` 的装载根不再是写死的 `job_dir`，
  而是产出方给的 `iter_info["shards_root"]`（纯函数 `ppo_shards_root`，空串/缺键/`None` 逐字回落）——
  「取消搬回但忘记改读取根 ⇒ job 内 PPO 读 0 局语料」这条已闭。
  ⚠ 满铺器改的是**每轮样本量/更新构成**（非语料身份/课程/奖励）⇒ 属训练侧；按 §15.5 口径当**新实验**处理：
  新 `--out`/`--traj`，**不跨改动续跑**。
—— 全文（现场读数 / 根因链 / 判据迁移表 / DoD）→ `plan/rollout-topup-fastpath.plan.md` §1–§4 · §10 ·
`docs/nn/runtime-opt.md` §35 · 评审 → `plan/rollout-topup-fastpath.review-bf.md`

## §2026-10-13-goalnn-body-transfer-guard-v2（2026-10-13，传输护栏 v2：`read1` 收字节 + 时间窗采样与 2 窗迟滞 + 失败尝试入账；plan/body-transfer-guard-v2.plan.md + 首轮评审 R1–R8）

- **背景**：真机一个 job 的传输账 `合计 1.31MB/343.5s`、`other=343.7s` 独吞关键路径，而链路不慢
  （同日其它 job 1.2–7.9MB/1.4–16.1s）。真因（本机滴流 socket 实测）在**护栏自身的两处形状**：
  ① `resp.read(amt)` 是**循环 recv 凑满 amt**，而 socket 超时是**每次 recv** 的超时 ⇒ 隧道只要每
  < `idle_timeout`（45s）吐 1 字节，`body 停滞` 判据**结构上永不触发**，只剩墙钟闸兜底；
  ② 低速判据只在**首块**判一次（`probed` 一次性）：慢链路要等满一整块（≈300s）才有样本，而只修 ①
  之后首块又太小 ⇒ 判一次 `should=False` 就**永久关掉自己**（探针复现现场那条行）。第三笔：失败的
  尝试**不进任何账** ⇒ 那 300s 只以「`other` 巨大」出现，归不了因。
- **决定**：**M1** 新唯一收字节口 `_recv_some(resp, limit)`（= `read1` 语义），`remote/http.py` 与
  `remote/tailscale_boot.py` 的 `_read_body` **两处同改**（后者单文件自包含，孪生是结构性的；
  `tests/test_reuse_ledger.py` 的 `BASELINE_EXACT` 12→13 为此记账）；**M2** 采样改**净时间窗**
  （`win_elapsed ≥ WIRE_PROBE_SEC = 3s`，净值 = 墙钟 − Σ让路、不用一次性标记）+ **连续
  `WIRE_REROLL_PROBES = 2` 个慢窗**才断（抗单窗抖动与重连后的 TCP 慢启动），`_reroll_decision` 只加
  关键字 `remaining: int | None = None`（缺省 = 旧累计口径，**逐字不变**），每次重抽的浪费上界从
  「≤1 块 256KB」改为「≤2 窗」；**M3** `BodyTimeoutError(TimeoutError)` 带 `partial/got/total`
  （**只做可读性**）；**M4** `_WIRE` bucket 加 `failed{sec,bytes,n}`，每次**未成功**的尝试
  （超时/瞬时网络错/重抽/被 P1 挤走）结束时登记，同行进 phases 与 `wall` 的 `max()`，
  `_wire_totals`（预取摘要）**不含**它，**4xx 不入账**；**M7** prefetch 轮有候选要下时先 `_wire_start`。
- **备选与否决**：① 只把 `BODY_CHUNK` 调小（16KB）—— 否：结构问题没解（873B/s 下仍要 19s）；
  ② 自建 socket 读循环绕开 `read(amt)` —— 否：要重写 urllib 的代理装配（CONNECT/绝对 URI）；
  ③ 把 socket `timeout` 当「每块总预算」—— 否：即 §21 记录的「整读」（护栏设立前的病根）；
  ④ 迟滞取 1（单窗即断）—— 否：链路 44× 双峰 + 重连后第一窗天然慢 ⇒ 每次重连自我判死刑；
  ⑤ 把超时挪进重抽路径 —— 否：重抽是「幂等 GET + 换连接」，超时是「退避重试」；
  ⑥ 承诺「续传起点」—— 否：HEAD 无 Range（§51 立项门槛没动）⇒ 半截仍作废，文案不许写「续传」；
  ⑦ 引入「重抽无收益即停」判据 —— 否（**反向约束**）：无 Range 时重抽的浪费是实打实的。
- **后果**：滴水链接不再等满 `total_timeout`（≈6s 内动手）；`failed=` 让「300s 花在哪」第一次可归因；
  `read1` 把采样点从「每 256KB」变「每 recv」（`pace()` 调用变密，但其本身只是几个计数 + 条件判断）。
  **未做（有意）**：R4 最后一次尝试必然硬传 · R5 单通道 P1→P2 连带（调度面）· R7 失败样本不进
  `_note_rate`。**收益仍是推演**（343.5s ⇒ ≈20s = 单次现场反推 + 缩放探针）：真机 ≥10 job **配对**
  读数取回来之前只算「机制成立」（未决 → `docs/nn.progress.md` §3.1 #28），回退档
  `WIRE_REROLL_PROBES = 1`（保留 `read1`）或整段回滚。
- **★ 合并说明（2026-10-13，rebase 至 `origin/goal-nn`）**：本条原写于 `3c603ba9`（当时最大 §84、
  无 Range），而目标基线已先落地 §2026-10-12-transfer-jitter-tolerance（迟滞/衰减/`WIRE_REROLL_GAIN`）
  与 §2026-10-12 plan A/B/C（**payload Range 断点续传**、上传让路点、控制面死线）⇒ 本条「无 Range」
  与备选⑦「不引入停抽」两条前提在合并基线**已不成立**。合并口径：**保留上游的 Range / `WIRE_REROLL_GAIN`
  / 衰减 / 让路基建**，在其上补 M1（`_recv_some`/`read1`）、M2（时间窗 + 2 窗迟滞，**取代**上游 §86 的
  「每块一点」采样）、M3（`BodyTimeoutError`）、M4（`failed=` 入账，重抽尝试按**本段**字节计以免带 Range
  时跨尝试重复）、M7。故备选⑦**作废**；`WireSlowError` 仍带 `partial` 且 `bytes_read` 为累计口径；
  「半截作废」改为「Range 续传，仅服务端忽略 Range 才真作废（`_wire_note_resend`）」。全文节号 §85 → **§89**。
—— 全文（触发 / 探针实测表 / 五处关键设计 / 口径红线 / 未做清单）→ `docs/nn/remote-transport.md` §89
—— 落点 → `remote/http.py`（`BodyTimeoutError` / `_recv_some` / `_read_body` 窗采样 / `_get_with_retry`
失败账）· `remote/tailscale_boot.py`（孪生三处）· `remote/wire.py`（`WIRE_REROLL_PROBES` /
`_reroll_decision(remaining=)` / `failed` / `_wire_note_failed` / `_wire_flush`）· `remote/job_round.py`（M7）·
`remote/worker.py`（重导出）· `tools/wire_report.py`（phases 解析 + `T_fail`）
—— 回归 → `nn-training/tests/remote/{test_body_transfer_guard,test_wire_reroll,test_boot_wire_guard,
test_prefetch_adaptive,test_wire_report}.py`（新增/改写 15 例，先红后绿）；门禁
`bash tools/githook/nn-python-gate.sh` 绿（4275 passed / 1 skipped）

## §2026-10-14-goalnn-wire-diet-m3-weights-delta（2026-10-14，R1：权重回传走 int16 单 scale delta；hub **不**引入 torch）

- **背景**：`plan/wire-diet.plan.md` §3 R1 —— result 上行的大头是 `weights_json`（gzip 后实测 288,205 B），
  回传 `Δ = W_new − W_base` 的 int16 量化可降到 **87,546 B**；base 恒为**本轮下发的 init 权重**
  （双方按 `init_weights_fp` 内容寻址同一份）⇒ 残差 = 本轮量化误差、**不累积**。
- **定案 1（单 scale）**：全模型共享一个 `scale = max|Δ| / 32767`。**否决**逐张量/逐参数 scale：
  ① plan 的 1.44e-4 相对 RMS 是在单 scale 下量的，换口径要重测；② 42 个 scale 加进头里（~700 B）会
  吃掉小模型上的收益；③ 误差（RMS 3.0e-7）已比每轮真实更新幅度（RMS 2.1e-3）小两个数量级 —— 再降
  一个数量级换不来任何东西。**后人若想「顺手加 scale」，必须先重测并改这一条。**
- **定案 2（R1 不吃 torch，与 §10-2 的「hub 引入 torch」无关）**：权重的线上字节是 `nn-weights-json`
  （`save_weights_json` 写、TS 运行时也读），每个参数是 `{shape, data:<f32 LE base64>}` ⇒ 解析/量化/
  重建全在 `json` + `base64` + `numpy` 里完成（numpy 在 hub 侧本来就有：`remote/payload_slim.py`）。
  §10-2 那次「hub 装 torch」的显式决策**只管 R3/M4**（opt 是 tar + 真张量）。
- **定案 3（重建产物 = 权威字节，两档与 §12 同构）**：hub 落盘的是**重建**出来的 JSON（只替换
  `params[*].data`，其余键与**键序**原样带回、`indent=2` 与写端同形）⇒ `init_weights_fp = sha256(落盘
  字节)` 的算法一字不改；它与 worker 本机那份 f32 全量**必然不同 sha**，命中判据是 M0 的身份映射。
  `base_sha` 与 hub 那份 init 字节相等 = **exact 档**（逐字节对账）；不等 = **identity 档**（worker 用
  自己那条链当基准 ⇒ 只能靠身份闸 + 结构闸，偏差 ≈ 一轮量化误差的随机游走）。
- **被否决备选**：① 逐张量 scale（见定案 1）；② 在**绝对**权重上量化（同 §3 否决表里 opt 状态 f16
  那条灾难）；③ hub 侧只存 delta 不解码（下一轮 base 无从重建 ⇒ 链断）；④ 让 hub 为了 R1 引入 torch
  （定案 2：结构性不需要）。
- **补记（同日，落地时两处收紧；plan §13 定案 6/7）**：① **拒收面再加一格** —— 「魔数被改坏、头却
  自述 delta」的字节一律拒收（`_container_shaped`）：宽容档只留给「根本不是容器」的旧形态字节
  （旧 worker / 冒烟回显），改坏的容器当权重落盘是最坏的那一格（身子是 int16 载荷 ⇒ 静默污染链）。
  ② **读法去重**：`fmt_bit` / `container_base_sha` 下沉 `common.wire_codec`，两族容器只留 `partial` 别名
  —— 新开一个共用模块会让两个 L0 叶子一起升到 L1、连坐 `hub_client` 与 9 个上位模块（同 §13 定案 5）；
  复用账本结构克隆 18 → 16。
- **补记 2（同日，实机事故：hub-server 启动即退）**：M3 落地后 hub 进程挂 —— `remote/weights_delta` 的
  **顶层 `import numpy`**，而它是被 `remote/hub_client` 在**模块顶层**拉进 hub import 图的
  （`hub.queue → hub.queue_observe → remote.code_snapshot → remote.hub_client`）。两个独立缺陷叠在一起：
  ① **本片的错**：违反本仓「remote 子模块顶层零三方依赖（hub 侧免重依赖）」的规矩（§13 定案 1 里那句
  「numpy 在 hub 侧本来就有」是错的 —— `remote/payload_slim` 只在**打包端**被延迟 import，hub 从不读它）；
  ⇒ numpy 改**惰性**（`_np()`，同 `opt_delta._torch()`：顶层零三方依赖、缺了只在使用点响亮拒收）；
  ② **潜藏的错（先于本片）**：uv 造的 venv 没有 `executable=` 行 ⇒ `resolveVenvPython()` 解析出的是**基础
  解释器**（uv 跳板真身），而 `dashboard/src/stack/specs.ts` 的 hub-server spec 只挂 `PYTHONPATH: NN_TRAINING`
  ⇒ hub 进程**一个三方包都看不到**（numpy/torch 皆无），M4 决策 (a)「hub 引入 torch」在这台机上不成立；
  ⇒ spec 改为 site-packages + `nn-training/`（与 `launch/cli.ts` / `server/run-python.ts` 同规）。
  复现用例：`tests/remote/test_weights_delta.py::test_hub_import_graph_needs_no_numpy` /
  `::test_numeric_paths_reject_loudly_without_numpy`；spec 侧回归钉在
  `dashboard/tests/hub-server-push-arg.test.ts`；全文 → `docs/nn/remote-transport.md` §95。
  （2026-10-15 合并勘误：这个「全文」指针**无对应节** —— `git grep` 全树（含 `origin/goal-nn`）只在
  `remote/opt_delta.py` / `dashboard/src/**` 找到本补记 2 的**代码**，**没有任何一节写过这段事故正文**；
  且本篇 §95 现为「离线段的两条命」、§96 为「staged 预取搬入抬 mtime」，都不是这段内容。
  要补正文得另起一节，别照这个号去跳。）
- **后果**：R1 与 R3 共用同一套「形态位 + 容器自述 + 两档判别 + 缺 base 拒发」的纪律，形态位键
  分别为 `manifest.w_fmt` / `manifest.opt_fmt`；开关仍 `--wire-diet 0`，等 §6 的真机 A/B 通过才翻 1。
  全文 → `plan/wire-diet.plan.md` §3 R1 / §13 · `docs/nn/remote-transport.md` §94 · 代码
  `remote/weights_delta.py`。

## §2026-10-14-goalnn-offline-local-disk-first-d（同日收尾：**持久面容量判据** B2 + 备份**对账** C3 + 离线版档案）

- **上位**：前三条（A 腿 = 删探针 · B1/C2 = 两个根 + 恢复腿 · C1/B4 = 备份写方 + 段末交付面）。
  本条把上一条「诚实账」里那两笔（B2 与离线版文档）落下，并补上 §3.4 自己写明的「对账」那一半。
- **定案**：
  ① **B2 持久面容量判据**（`remote/offline_deliverable.py`）：开段前判
     `free(本地根所在设备) ≥ ARTIFACT_BYTES_PER_ROUND(4MB/轮) × 轮数 × 1.5`，`rounds` 由
     `offline_boot.run_one_course` 从 `CFG.max_iters` 递进来（取不到 ⇒ `SEGMENT_ROUNDS_DEFAULT`=100，
     现场观测的段长）。不过 ⇒ 与「写不进」同一条出口：**回落网络侧产物根 + 一行带 `free=…MB < need=…MB`**。
     判据住交付面（不是 plan §4 表里写的 `common/scratch.py` / `remote/plan_handoff.py`）——因为 B1 之后
     **产物根的生效点在 `offline_deliverable.resolve_local_root`**，`plan_handoff` 只收显式 `--artifacts`；
     判据写在「谁决定落点」那里，才不会被绕过。
  ② **轮内峰值**（4MB/轮之外的第二项）**没有并进这条判据**：它要 spec/槽位/批数，只有 rollout 腿求得到
     （`worker/iter_rollout._round_need_bytes`），由 scratch 自己的容量闸按**候选盘**算。两条腿共用 `/tmp`
     时两项相加而「合起来超了」没人在开段前判 ⇒ 由 ③ 兜（这也是如实记账，不是漏项）。
  ③ **段中盘满响亮**（DoD-14，`remote/plan_run._checkpoint`）：产物写的 `OSError` 当场记一行
     `WARN: 产物落盘失败（…）—产物根=<path>｜…换根续跑`，再转成 `ProtocolError`（`run_loop` 的失败支路：
     非零退出 + `[run] 失败`，**不会**走到 `finalize` 的成功口径 = 段末不谎报成功）；`state.json` 仍是上一轮
     （`checkpoint` 写序「先产物后状态」）⇒ 续跑锚点不受影响。`ValueError`（产物字节坏了）**不**并进来：
     那类由 `_weight_bytes` 在进 try 之前抛，归因是「结果不可用」而不是「盘写不下去」。
  ④ **C3 对账**：`backup_pending` 的幂等从「抽屉里有这个目录 ⇒ 跳过」收紧成「**字节一致**才跳过」
     （对账 `weights.json` + `opt.tar`；不一致 ⇒ 撤掉重装并记一行）。理由：本机同一轮可能被**重跑**过，
     旧字节留在抽屉里会被恢复腿当成「这一轮」铺回去（看起来完全正常）。新增孪生常量
     `offline_deliverable.OPT_TAR_NAME`（与 `ArtifactStore.opt_path` 末段对账）。
  ⑤ **备份/恢复原语不共享 `common/scratch.install_tree`（C3 的形态变更）**：抽屉要被 `it-*` 视图扫
     （`install_backup` 的 `glob("it-*")`），而 `install_tree` 的临时目录名是 `<name>.tmp-<pid>` ⇒ 崩后残留的
     `it-005.tmp-1234` 会被当成一个**轮次**恢复；恢复腿又住在 standalone 兄弟文件里（拿不到 `common/`）。
     ⇒ 抽屉侧保留自己的 `.` 前缀临时名（原语各一份是**有理由的**，不是重复）。
  ⑥ **离线版档案**：`docs/nn/remote-transport.md` **§95**（= §81 的离线版）——四个落点的常态表 ·
     日志该/不该出现什么 · 现场四步判读 · 诚实账（`/tmp` 本地介质仍未取证 · 4MB/轮是地板估算 ·
     备份不抗网络盘挂掉）。
- **被否决备选**：① 把 B2 判据写进 `common/scratch.py`（它不知道产物根，等于让每个 scratch 调用方各传一次；
  而真正决定落点的地方就在交付面）；② 把轮内峰值猜一个 GB 级常数并进判据（猜大了把好机器推去网络盘，
  猜小了等于没判——真机取证之前不猜）；③ 段中盘满让它裸抛 `OSError`（traceback 里有 `.tmp` 路径，但归因难、
  看起来像崩溃，且没有「已收轮次不受影响」这句话）；④ 幂等只判「存在」（会把重跑过的旧字节永久留在抽屉里）；
  ⑤ 为「原语唯一」把 `install_tree` 的临时名改成点前缀（会动 scratch 侧既有契约，收益只是少一份实现）。
- **后果**：开段前的三条判据（能写 · 装得下 · 段长算得准）齐了；段中盘满不再是「看起来像崩溃」；抽屉里的
  每一轮都与本机字节相符；离线链路第一次有一份**运维读法**档案。文本断言基线 1466 → 1469（三条响亮行的账
  见 `tests/test_source_text_assert_budget.py` 模块头）。
- **诚实账**：① `/tmp` 是节点本地介质、4MB/轮的取值**都要真机读数**（不由 agent 跑训练腿）；② 备份不抗
  网络盘挂掉；③ 轮内峰值与产物预算**共用 `/tmp` 时的合判**没做（见 ②③）。
- **全文** → `plan/offline-local-disk-first.plan.md` §3.1/§3.2/§3.4/§8 · `docs/nn/remote-transport.md` §95 ·
  `docs/nn/runtime-opt.md` §36。

## §2026-10-15-goalnn-offline-export-status（2026-10-15，导出**成败**有权威落点：python 退出层写 `export-status.json`，控制台与 hub 都读它）

- **来源**：用户 2026-10-14 报障第二条 **「导出离线包失败应响亮报错」**。plan →
  `plan/offline-open-ended-iters-and-loud-export.plan.md` §3.3（J3）· 评审
  `plan/offline-open-ended-iters-and-loud-export.review-bf.md`（P0-4 + P1-5/P1-6；本条**含一处评审勘误**，
  见最后一节）。
- **静默的机理（为什么要落到盘上）**：控制台起导出是 **detached 子进程**
  （`core/proc.ts` 的 `detached + unref`，`:43`/`:48`）⇒ **拿不到退出码**；控制台重启后子进程仍在跑 ⇒
  控制台的内存状态不可靠。于是每一方都在演「成功」：面板说「产物将落在 …」、hub 的自动交接拿 `ok:true`、
  hub 的触发账本只判 HTTP 码、束末告警只会说 `pending-export`。**失败证据只留在
  `tmp/<课>/export-bundle.log` 的末行，没有任何一方读它**（代价：每次触发白烧一次 code-snapshot +
  一次 `git push`）。
- **定案（落点与写点）**：
  ① 落点 = **与包同目录**（`tmp/<课>/export-status.json`）。python 侧
     `remote/bundle.py::export_status_path(Path(out).parent)`（`out` = `--export-bundle` 的取值），
     TS 侧 `path.dirname(taskBundlePath(course))` ⇒ **包搬到别处也不会分家**；名字与魔数
     （`EXPORT_STATUS_NAME`/`EXPORT_STATUS_MAGIC` ↔ `taskBundleStatusFileName`/`taskBundleStatusMagic`）
     读 python 源逐字对账（同 `BUNDLE_META_SUFFIX` 的手法）。
  ② 写点 = **进程退出层**（`trainer/run_rl.py` 的 `with export_status_guard(args):`）——**不是**包住那两处
     `raise SystemExit`：轮内的 `ProtocolError`（撞硬上界 / 空计划）走 `round_failure` ⇒ 落 `iter_error`
     + **同轮重试 5 次**（每次退避 30s）才退，包住 `raise` 会漏掉那条**真实**失败路径
     （「每次进程都秒退」只对启动期拒成立）。**每次尝试覆盖写**（末次即真值）+ 临时文件 + `replace`。
  ③ **成功也写**（`ok:true` + `exit_code:0`）：只写失败的话，「文件不在」就同时意味着「失败了」与
     「还没跑完」。**`null`（没写过）= 不可知，不是成功**——这个三态是这条腿的全部要害。
  ④ `reason` 是**第一手**的（写它的人就是那个进程手上的异常原文）；控制台那条读**日志尾**的
     `tailFailureReason`（`exit-watchdog.ts:211-236`）降为**找不到状态文件时**的兜底（被 kill / OOM）。
     两处各司其职，不叠第二份口径。
  ⑤ 旁挂件**不配把主腿拖红**：`write_export_status` 写不下返回 `None`、不抛；异常一律继续上抛
     （`SystemExit` 的 code 按解释器口径记：`0`/`int`/`None→0`/其它→1）。
- **读面（谁读它）**：控制台 `GET /api/taskBundleInfo` 的 5s 轮询多带 `statusLine` + `status`（不另起通道）；
  面板在课程行上亮红徽标「上次导出失败 · <原因>」；`POST /api/taskBundleStatus` 是**只读**动作
  （`READONLY_ACTIONS` + `VIEW_ONLY_ACTIONS`，零副作用）供 hub 问；回执语义从「产物将落在 …」
  改成「**成败待定**」（那句在失败时是假承诺）。
- **hub 侧：不再盲重试（P1-6 的收口）**：`hub/task_pack.console_export_status()` 只读回读；
  `poll_console_export_status()` 在**导包窗**里（云机每 ~60s 重试 claim 那条路）**限频 60s** 问一次，
  且**只认「新」失败**（那份状态的 `at` 不早于我们上一次触发——否则它属于上一轮，拿它归罪这一轮是栽赃）。
  已知失败 ⇒ `auto_handoff_decision` 直接回 **`failed`**（409 带 `give_up=true` + `export_failed=true` +
  真因），窗口 `TASK_BUNDLE_FAILURE_WINDOW_SEC=900`；**窗口锚是那份状态的写入时刻（持久）**，不是进程内
  计数——触发账本会被 `reset_auto_handoff_triggers` 在「换主 ∨ 超窗」时清零，所以「3 次用完就 give_up」
  在时间轴上不成立，现场正是这么演成「一直在失败」的。**不可知不是失败**：控制台不可达 / 旧版本 ⇒
  结论一字不变（只负责「知道失败时少烧资源」）。
- **被否决备选**：① 只让控制台读**日志尾**（`tailFailureReason`）——控制台重启即失去活进程日志，而 hub
  根本读不到；② 控制台在 `watchExportExit` 里「失败就自动重试」——静态拒因重试一万次也一样，
  动态失败应**响亮上报**；③ 把状态塞进 `/api/state`（那是高频大 payload，而这是按需一问）；
  ④ 让 hub 在 claim 的**临界区**里同步等控制台（`pending_export` 不占闸正是为了不冻住所有盘——
  本次把回读放在已经做网络调用的那一支，并限频 + 有界超时）；⑤ 把「已知失败」写进 `stale_reason`
  （那是**另一个东西**：包起点 sha vs 活动权重；本次新增字段 `export_failed`，不挤别人的语义）。
- **未覆盖的边界（诚实账）**：① 比 `run_training` 更早的启动期拒因（课程未开课 / `--course` 与 CLI 冲突）
  **不进**这个文件——那条路靠控制台的 `tailFailureReason` 读日志尾兜底；② 被 SIGKILL/OOM 的导出不会写；
  ③ 状态文件是**最后一次尝试**的事实（不是历史）：反复失败只留最后一条原因；④ 面板红徽标与
  `statusLine` 只在 `taskBundleInfo` 轮询里上屏（5s），不推送。
- **门禁**：`bash tools/githook/nn-python-gate.sh` 绿（ruff + mypy 673 文件 + pytest **4465 passed / 1 skipped**）·
  `cd dashboard && bun run typecheck && bun run test` 绿（**1588 passed / 0 fail**）·
  `bun dashboard/src/server/build.ts` 三份 bundle 过。文本断言基线 1469 → **1470**（+1 = hub 递给云机的那行
  `trigger_note`；理由见 `tests/test_source_text_assert_budget.py` 模块头，同批能改的都改成了行为断言）。
  用例：`tests/remote/test_export_status.py`（8 例：非导出腿一字不写 · 成功也写 · 第一手原因 + 异常原样上抛 ·
  `SystemExit` 四种 code · 轮内 `ProtocolError` 也落盘 · 覆盖写 + 无 `.tmp` 残留 · 写不下只降级）·
  `tests/hub/test_auto_handoff.py`（+3：控制台报失败 ⇒ 不再盲重试 + 真因进读面 + 限频 ·
  旧失败与不可知**不归罪** · `failed` 窗口过期与清零）·
  `dashboard/tests/auto-offline-handoff.test.ts`（+两段：三态读面 · 名字/魔数/落点跨语言对账）。
- **顺手修的一处**（**与本次改动无关的既有红**，见勘误）：同文件里「排除表与 `pack_code_zip` 对账」
  用的是写死的 `py.slice(i, i + 3000)` 窗口，而 `eb26884b`（2026-10-14）把该函数 docstring 加长后
  排除名单被推到 3000 字之外 ⇒ **HEAD 上这条用例本来就是红的**。改成「整个函数体」（到下一个顶层
  `def`）——覆盖面只增不减，也不会再因注释变长而假红。
- **勘误（评审 P0-4 的第一条是错的）**：评审说「落点在 `courseLogDir()` 而不在 `tmp/<课>/`」——
  实测 `core/paths.ts:33` 的 `LOG_DIR = REPO_ROOT/tmp`，`stack/specs.ts:30-32` 的 `courseLogDir`
  = `path.join(LOG_DIR, course)` ⇒ **同一个目录**。v1 原稿的 `tmp/<课>/` 本来就对；
  已把勘误追加进评审文件（`plan/offline-open-ended-iters-and-loud-export.review-bf.md` 末节），
  实施改用「与包同目录」这条更根上的口径。评审的另两条（全员演成功 · 写点在进程退出层）成立，已按原评审实施。
- **全文** → `plan/offline-open-ended-iters-and-loud-export.plan.md` §3.3 · 评审
  `plan/offline-open-ended-iters-and-loud-export.review-bf.md`（含勘误节）。

## §2026-10-16-goalnn-offline-segment-roll（离线「无限轮」的下一半：J2 段末自动滚（含回灌腿）+ J4 静态拒因前置）

- **来源**：`plan/offline-open-ended-iters-and-loud-export.plan.md` v2 §3.2/§3.4（B/D 腿）·
  评审 `plan/offline-open-ended-iters-and-loud-export.review-bf.md`（P0-1/P0-2/P0-3 + P1-2/P1-3/P1-4/P1-8）。
  §97 落了 J1（`iters<=0` ⇒ 开区间段）与 J3（导出成败权威落点）；本条补的是**下一半** ——
  段末之后「不用人」以及「点下去之前就说清楚为什么导不了」。
- **定案一（J2 三态）**：段末判据从两态 `end_it_reached` 扩成三态 `seg_end_kind`：
  `open`（无限课的段末）/ `course`（课末，封课）/ `segment`（有限课程被 `--run-iters` 截短的一段）。
  判据由**云机从计划只读取出**（`remote/plan_run._seg_end_kind`），hub 只消费
  （`hub/task_pack.pack_seg_end_kind` 从包里 `plan.json` **反推**当兜底），控制台按它决定
  要不要转交 `run_complete`。v1 的「`end_it_reached` ∧ 非开区间 ⇒ 课末」会把第三格误判成课末
  （课当场被封死），那是这条判据存在的全部理由。
- **定案二（盖章不动 + 事件驱动）**：`completed_pack_sha` 是「这份包别再发了」的**唯一**机制
  ⇒ 段末**照旧盖章**（`_end_seal_ok` 的 run_id/plan_sha 对账一字未动），改的是**触发时机**：
  段末事件在 `hub/offline._post_offline_result` 里主动请控制台出下一段（与 claim 无关 ⇒ 解掉
  「completed 挡死 claim、而重导要有需求才有包」的死循环）。已知导出失败（J3 的
  `export-status.json` 窗口锚）在窗口内**不重复触发**（否则这条新腿会绕过「失败就不烧快照」的纪律）；
  三态**未知**也不触发（宁可少滚一次，不在课末白烧一次导出）。
- **定案三（回灌腿，评审 P0-1 选型①）**：「上报 → 回灌 → 出包」三步先后固定，
  `autoOfflineHandoff` **先回灌、再判包**：`python -m remote.deliver_zip --from-run <hub 已收目录>`
  把段末产物并进本机账本/指标（幂等：轮次只补缺、账本行按 `it` 去重、已到段末 ⇒ `imported:false`）。
  **不由 hub 构造下一段计划**（否决：要新造授权/合法性口径，且新节点会拿到与 `start_it` 不匹配的权重
  ⇒ 静默错血缘），也**不接受「保留全手动」**（那 §0 的目标就得照实改成「半自动」）。
- **定案四（T6 收紧）**：控制台转交 `run_complete` 的判据收紧为「跑满 ∧ run_id/末轮号对得上 ∧
  `seg_end_kind` 不是 `open`/`segment`」；`""`（旧 hub/旧云机）按旧行为转交。段末另立一个读数
  （导入回执 `segmentComplete` + 「本段完成（开区间段）——下一段由段末事件自动滚」）。
- **定案五（J4 静态拒因前置 + `--run-max-iters`）**：`exportGuard` 重建 ★M4 删掉的
  `declaredCourseIters`（住 `stack/courses.ts`：课程域共享判据的家、不断环；D14 口径 = 只读
  `curricula/<课>.jsonc`），并读账本末轮：`剩余 = iters - last_completed`（与 python 同一锚：
  启动期 `self._start_it` = `last_completed+1`、轮内 `it-1` = `last_completed`）⇒
  `<=0`「没有剩余轮次」· `>500`「> 硬上界」**在点击那一刻**拒（不 spawn、不烧 code-snapshot、不 push）；
  `iters<=0` 与不可知**不判**。另补 `--run-max-iters`（`common/manifest.py` 的注释一直说它存在，
  而全仓没有这个 argparse）：**只能降不能抬**，控制台同名常量读 python 源对账。
- **备选与否决**：① 云机自延长段（跑到 `end_it` 自己 `+= 500`）—— 否（等于「计划不设终点」，
  且 `end_it_reached` 永不触发 ⇒ hub 永远不盖章 ⇒ 另一个节点能把同一份包再跑一遍）；
  ② 「不盖章」换「同一 sha 不再服务」—— 否（要多造一条持久判据，而盖章本来就是那条判据）；
  ③ 抬 `RUN_MAX_ITERS_HARD_CAP` 到 2000+ —— 否（拆掉「防手写/损坏计划把节点按一整天」的闸）；
  ④ 段末种类**未知**时按段末处理 —— 否（课末会白烧一次导出 = 一次 code-snapshot + 一次 git push）。
- **后果**：hub 侧 6 格新用例 + 云机三态判定表 9 格 + 回灌腿 4 格（`tests/{hub,remote}`）+
  控制台 J2/T6/回灌/静态预判/常量对账 8 格（`dashboard/tests/*`）全绿；
  `docs/nn/remote-transport.md` §102 给出**契约表**（谁定终点 / 谁推进账本 / 谁触发出包 / 失败怎么停）。
  **真机未验**：DoD-2/3/6 要一段真云机跑满才成立，闭合判据留在 `docs/nn.progress.md` §3.1 条目 32
  —— 在此之前「无限轮」的代码面闭环，端到端按「未验」对待。

## §2026-10-16-goalnn-component-error-registry（组件错误注册中心：五处散落的错误 → 一张登记表 → 首页入口 + `/errors` 详情页）

- **来源**：`plan/component-error-registry.plan.md` v3（用户 2026-10-15 诉求：「让远端 ppo 云机、自主 rollout/eval/ppo
  云机，本地进程 trainer/hub/cloudflared 的错误日志能够在 dashboard 注册并查看…避免训练各个组件静默失败无人处理」）·
  评审 `plan/component-error-registry.review-bf.md`（3 P0 + 4 P1 + 5 P2，处置见 plan §12）。**D 批次**（`dashboard/**`）
  与 **P 批次**（`nn-training/**`：E3 改走既有 410 通道补 `detail` + E4/E5 上报腿）**均已落地** —— P 批次按 §7-Rk1
  单开窗口：改 `nn-training/**` 会翻训练 code zip，云机 worker 下次校验 `CodeChangedError` ⇒ 退出码 86 自愈重拉一次。
- **定案一（五处 → 一张表）**：`ErrorEvent` 是唯一形状（类型住 `web/view/error-types.ts` —— 零 import；计算住
  `server/api/error-{fingerprint,scan,registry}.ts`）。四条腿**各带闸**：腿 A 本机组件日志尾 `size:mtime` 签名、
  腿 B 已见 jid 记忆化（`fail.json` 首写胜出）、腿 C 云机上报只在自己的 **10s 独立节拍**上探 hub、腿 D 读既有池视图
  SWR ⇒ **`/api/state` 请求路径零新增探测零网络**（N9 / R1 结构闸）。技术正文：`docs/nn/console.md` §39。
- **定案二（`fp` 不含 `scope`）**：指纹 = `shortHash(source | component | normalize(message))`（归一去掉 hex / 数字
  序列 / 时刻 / `tmp/` 之前的绝对路径前缀）。把课名算进指纹会让同一条错误在 10 门课上折成 10 条 ⇒ 首页计数虚高、
  详情页十行同一条，正是要治的刷屏在详情页上的复刻。
- **定案三（两个时刻分开 · 两个 ack kind）**：账本 `firstSeenAt`（**不可变**，只做「这个 fp 我见过」的种子）vs
  内存 episode `firstAt`（**可重置**，ack 身份）；ack = `component-error`（逐条：`fp` × 内存 `firstAt`）与
  `component-errors`（首页汇总条：subject `'all'`，eventId = 最新未 ack 错误的时刻）。二者混用 ⇒ 一次「知道了」
  永久静音；分开 ⇒ 持续态不重复打断、**消失后复发必重弹**、新错误进来必重弹。
- **定案四（账本可重定向）**：`tmp/component-errors.jsonl` 住 `core/paths.ts::errorLedgerPath()`（`BCITY_ERROR_LEDGER`），
  与 `tmpLogsDir()` / `consoleLogPath()` / `loopControlPath()` 同规 —— 否则用例会把指纹写进**操作员真账本**。
  只在「新 `fp` 首次出现」时 append 一行；重启重放只当种子、**不继承** `firstAt`（代价：未消失的错误重弹一次，显式接受）。
- **定案五（入口与详情页）**：首页 = 告警坞**第 9 类汇总条目**（0 或 1 条，逐条清单是详情页的事），为此把
  `AlertAction.kind` 闭集放宽出唯一一个**无副作用**的 `'link'`（渲染成真 `<a href>`、**不挂 `onClick`**）；详情页 =
  **第 3 个独立页 / 第 4 份 bundle**（`/errors`，照 `/log` 范式，只读面照渲）；`detail` **不进** 3s/4s 轮询投影，按 `fp` 懒取。
- **定案六（P 批次：云机的两条腿怎么回本机）**：① **E3 复用既有 410 通道**（`set_error(detail=…)` → 410 体
  `fail_detail` → `remote/push_dispatch.push_failure_record()` 改读，缺字段回退原常量）⇒ **无新端点 / 无新凭据 /
  无阻塞**；② **E4/E5 走 `POST /admin/worker-error`**（形状照抄 `worker-prefetch`：同一枚 token、缺 `worker`/`reason`
  ⇒ 400、64KB ⇒ 413；hub 侧只是 TTL 120s + 200 条的**内存环** —— 权威账本只在控制台）；③ 上报走 `remote/error_report.py`
  的 **daemon 线程 + 2s 超时**：`report_error` **立即返回**、失败即丢、不重试（G9）；④ 读面游标 = **hub 的到达时钟
  `at`**，不是上报方的事件钟 `ts`（跨机时钟不可比）。上报点只有三个 pull 失败分支 + 离线段失败；**取消 / 热替换 /
  确定性拒绝一律不报**（设计内生命周期，F15）。技术正文：`docs/nn/remote-transport.md` §103。
- **备选与否决**：① 复用 `/jobs/{id}/fail` 收瞬时错误 —— 否（那条腿「首写胜出 + 只收确定性失败」，会污染训练侧读到
  的那条失败原因）；② 在 `/jobs/*` 加 `detail` 字段 —— 否（要给取活协议动租约/游标面，410 通道是纯加法）；
  ③ 让 `/errors` 进主 bundle —— 否（独立页与 `/log` 同规）；④ 固定 / 纯账本 ack 键 —— 否（只增不减的 `TC_ALERT_ACKS`
  会把第二次同因事件永久吃掉）；⑤ 指纹住 `web/view/**` —— 否（`node:crypto` 撞分层禁词闸与 bundle 禁词闸，评审 P0-1）；
  ⑥ 把取消 / 热替换也报成错误 —— 否（设计内生命周期，报成错误就是灌噪音，F15）。
- **后果**：控制台 `tests/server-api-error-registry.test.ts`（新）· `web-alert-dock.test.ts` 第 9 类 · `web-ssr-errors-page.test.ts`
  （新）· `web-view-routes.test.ts` 的 link href 白名单改**数据驱动** · `web-ssr-readonly.test.ts` 夹具补 `errors: null`；
  云机 `nn-training/tests/{hub/test_admin_worker_error,remote/test_error_report,remote/test_worker_server_error_detail}.py`
  （25 格：含 F15 守门与「1s 慢 hub 仍立即返回」）+ `remote/error_report.py`（新，L0，已入 `tests/helpers/remote_dag.py` 层账）。
  **未验**：真机端到端（要一段真云机跑满才成立，闭合判据在 `docs/nn.progress.md`）。
  与 `tmpLogsDir()` / `consoleLogPath()` / `loopControlPath()` 同规（P0-3）—— 否则用例会把指纹写进**操作员真账本**。
  账本只在「新 `fp` 首次出现」时 append 一行；重启重放它当种子，**不继承** `firstAt`（代价：未消失的错误重弹一次，显式接受）。
- **定案五（入口与详情页）**：首页 = 告警坞**第 9 类汇总条目**（0 或 1 条，逐条清单是详情页的事），为此把 `AlertAction.kind`
  闭集放宽出唯一一个**无副作用**的 `'link'`（渲染成真 `<a href>`、**不挂 `onClick`**）；详情页 = **第 3 个独立页 /
  第 4 份 bundle**（`/errors`，照 `/log` 范式，只读面照渲）；`detail` **不进** 3s/4s 轮询投影，按 `fp` 懒取。
- **备选与否决**：① 复用 `/jobs/{id}/fail` 收瞬时错误 —— 否（那条腿有「首写胜出 + 只收确定性失败」语义，
  塞进去会污染训练侧读到的那条失败原因）；② 在 `/jobs/*` 加 `detail` 字段 —— 否（要给取活协议动租约/游标语义面，
  410 通道是纯加法）；③ 让 `/errors` 进主 bundle —— 否（独立页与 `/log` 同规）；④ 固定 / 纯账本 ack 键 —— 否
  （只增不减的 `TC_ALERT_ACKS` 会把第二次同因事件永久吃掉）；⑤ 指纹住 `web/view/**` —— 否（`node:crypto` 撞分层禁词闸
  与三份 bundle 禁词闸，评审 P0-1）；⑥ 把取消 / 热替换也报成错误 —— 否（设计内生命周期，报成错误就是灌噪音，F15）。
- **后果**：`dashboard/tests/server-api-error-registry.test.ts`（新：P0-1/P0-2/P0-3 三条守门 + G8 跨 episode 试金石）·
  `web-alert-dock.test.ts` 补第 9 类（含 `link` 渲染分支、汇总条 ack 分工、复现重弹）· `web-ssr-errors-page.test.ts`（新）·
  既有闸同批改：`web-view-routes.test.ts` 的 link href 白名单改为**数据驱动**（原先硬编码 `/eval|/log/` ⇒ 加第 3 个独立页必红）、
  `web-ssr-readonly.test.ts` 夹具补 `errors: null`（沿用 selfDisk F11 那条「夹具必须把无告警造成事实」的口径）。
  技术正文 `docs/nn/console.md` §39；**未落地**：P1/P2 两条云机腿 + 真机端到端（见 plan §5/§6 的批次划分）。

## §2026-10-17-goalnn-backfeed-e-f-g-h-close（E/F/G/H 四片收口：判据化重试与删除 · 一键包 · 报错表 · 停课导入）

- **来源**：`plan/offline-backfeed-resilience.plan.md` §4（E）· §5（F）· §6.2（G）· §7（H）
  + `plan/offline-backfeed-resilience.review-bf.md`（F1/F2/F4/F9/F10/F11）。
- **E（§4 自愈重试 + 租约重领 + 判据化删除）**：不新增机制，把 tick 内已有的「试」收紧并记住 ——
  ① 探活负缓存从 60s 收到 **15s**（`PROBE_FAIL_TTL_SEC`，封顶仍 ≤60s，别把平坦重试换成退避）；
  ② `next_try_sec`/`last_error`/`retry_n` 进 `status()`（**给人看，不做退避判决**）；
  ③ `_disable`（401/403）加**冷却复活**（`DISABLE_COOLDOWN_SEC`，到点清 reason + 重探活）；
  ④ 409 `expired`/`taken` ⇒ 补传腿照落（hub 侧本无租约门）+ 打点腿**重新 claim**（`_reclaim_lease`）
  —— `revoked` 不动（那是人的意志）；⑤ 抽屉删除 = **回传成功（`POST 200 → _save_ledger()`）才删**，
  `delivered.json` 不可信 ⇒ 一拍不删；`BACKUP_KEEP_ROUNDS`/`BACKUP_KEEP_HOURS` 接口留而默认 0。
- **E 的孤儿口径（F4 落地选择）**：账本是 run 级、抽屉是课级 ⇒ 换 run_id 后老段轮次在新账本里**无证据**
  ⇒ 走**内容对账**（本地有同一份字节 = 可重建的冗余 ⇒ 可删），剩下真孤儿**保留并计数**
  （`backup_orphans`）——「不许默默不删」两条口径各一条用例（DoD-3e）。备选「孤儿一律不删」被否：
  抽屉只涨不落，而它能被内容对账证明可重建。
- **F（§5 一键包）**：新增 `package_pending(cfg, log, *, courses=None)`，判据表四行（本地根齐 → 全量+三件+
  两账本 / 只有抽屉齐 → 抽屉全量 / 已收官 → `artifacts.zip` 直接复制 / 两边都不齐 → 响亮报缺哪一件、返回空表、
  绝不抛）；包内加 `IMPORT_META.json`（`magic/课程/run/it_from/it_to/generated_at/source`），
  历史名 `deliver-<课>@<run>-it<N>.zip` —— 与既有 `package_deliverable` 共用判据函数（不留第二份带账本的口径）。
- **G（§6.2 报错表 / 自动识课 / 留证保留）**：`import.ts` 的失败收口加一张**现象 → 人话 + 出路**表
  （句柄占住 / 目标已存在 / 非 zip / 拿错包 / 形状 / 课名冲突 / 盘满 / 超时 / 兜底），`detail` 一律带
  「挂在的门 + 出路 + 末尾八行 + **日志绝对路径**」，铁律 = 结论句里不出现裸 `WinError`/`Traceback`；
  留证目录保最近 3 份（删时响亮一行）。**§6.2-2 自动识课的定案改判**：`IMPORT_META.json` 已落地 ⇒
  优先级 `IMPORT_META.course` > `state.run_id` > `manifest.course_fp` > 文件名，冲突**不猜**（python 侧
  拒收并给两个课名 + 动作），控制台**不静默切课**、只把「包内自述：课程/run/来源」上屏
  （上一轮取「只报错 + 指引」的那个前置条件已消失，扩展上一条 `§2026-10-16-goalnn-import-checklist-and-name`）。
- **H（§7 停课导入）**：导入**全程不看**开课标记/在不在训（写进 docstring + 用例）；唯一与课状态有关的一步
  只是 T6 段末横幅转交，拿不到 hub 摘要就**不转交且说出来**（`bannerNote = 段末横幅未落账：hub 不可达`）。
  为此 `fetchHubOfflineResults` 的返回体从「摘要或 null」改成 **`{results, hubUp}`**（「hub 不可达」与
  「hub 活着但没这一段」是两件事，挤进一个 null 就只能编一个原因）。
- **H 配套（F9 落地选择①）：`tmp/<课>/deliver/` 是课程的长期资产**。`tools/tmp-clean.py` 只收它的**兄弟**
  （`LONG_TERM_KEEP`，递归到资产所在那一层）；备选②（把长期资产移出 `tmp/`）被否 —— 那要改导入/回灌/
  控制台三处路径口径，而豁免在收敛侧一处即可，且危险窗（课已停、`training_running()` 闸放开、人正在导入）
  正是 `tmp/<课程>/` 被判成 run root 时。导入/回灌的 docstring 同步写明这条语义。
- **同批加的闸（事故驱动的，非 plan 项）**：`refuses_zero_retention()` —— `--keep-runs 0 --keep-days 0 --apply`
  拒绝执行（要 `--force`）。**事故**：2026-10-17 一次探针用 `mod.TMP = <临Dir>` 注入，而 `plan` 的
  `tmp_dir` 默认参数在**定义时**绑定 ⇒ 探针实际在**真 tmp/** 上跑了一趟 `--apply`，1907 项 / 38GB
  （各课本地 `it<N>/`、账本、日志）一次抹掉且**不可恢复**（`rmtree` 不进回收站；唯一幸存的是三个
  `PROTECTED` 顶层，以及**本次豁免的 `deliver/`**—— §7-3 的豁免在真实数据上当场自证）。两处据此修死：
  ① `plan(tmp_dir=None)` 改调用时解析（默认参数陷阱）；② 零保留 + `--apply` 需 `--force`；
  ③ 用例改按**目标集合全等**判（不再是否定式子串），并新增「资产在更深一层也要保住」的用例。
- **备选与否决**：指数退避（15s→300s）—— 否（对「抖动 60s 后恢复」这个主场景是降级；若将来要，只能
  限定为**日志节流**）· 抽屉删除加保留窗口 8 轮/24h —— 用户 2026-10-16 已否（回传成功即删）·
  新增 `courseFromPackage=1` 请求字段让控制台按包内课程导 —— 否（`IMPORT_META` 落地后 python 侧已有
  权威对账口，多一个会被误用的开关不如把冲突说清楚）· 把长期资产移出 `tmp/` —— 见上。
- **后果**：`tmp-clean` 的删除目标从「整个 run root」变成「刨掉资产后的各块」（无资产时仍是**一次
  `rmtree`**，与旧行为逐字节一致）；文本断言基线 FORM 1470 → **1480**（+10 操作员读数行 −6 已改成
  非文本判据，逐笔分摊写在 `tests/test_source_text_assert_budget.py` 模块头）。

## §2026-10-18-goalnn-eval-gap-honesty（评估缺口不许伪装成读数：保真 / scratch 隔离 / 处置 / 第四态 / 判决准入）

- **背景**：y22-lr it5 的 `eval_log.jsonl` 是四条兄弟腿里**唯一** `dropped>0`、也**唯一**高于起点
  的读数（0.7098 vs 副门 0.685，兄弟腿 0.605–0.6425）——83 局缺口把胜率配比抽歪，差点被当成
  「lr 减半立刻见效」。逐层锚点：① 失败被压成字符串（`rc=… ({(stderr or stdout)[-160:]})`，
  bun 把 `error: ENOENT … open '<path>'` 打在栈**前面** ⇒ 83 条 meta 只剩栈尾，errno 被切掉、
  `is_transient_error` 恒 False）；② 本机腿 2 次预算、无退避、无改派、无熔断（8 秒烧完 83 局）；
  ③ eval scratch 住 `it<N>/`，轮转（`keep_iters=3`）连在飞的评估一起删；④ 判决层
  （`gate_inputs._row_from_summary`）从不读 `dropped`；⑤ `trainer-cluster.log` 的课程前缀不可信
  （`_PREFIX` 是模块全局，eval 的 daemon 线程活过 `prefix_scope`，镜像还会写进别的课）。
  全文 plan/eval-gap-honesty.plan.md（含评审 F1–F11 处置与诚实边界）。
- **决定**：① **D1 保真**——新 `worker/local_eval_error.LocalEvalProcessError`（errno 归一化的
  单一实现）保留 stderr 头尾两段 + `errno_code`/`msg_head`；`common.distribution.is_transient_error`
  按**鸭子协议**认 `transient`（`common/` 是下层，不得反向 import `worker/`）；meta 的 `reason`
  改取头部并**纯新增** `errno`/`msg_head` 两键。
  ② **D1b scratch 隔离**——eval scratch 迁到 `<traj>/_eval-<it<N>>/`（冻结快照 + `local-eval/`）；
  `_rotate_cleanup` 对它**只删**「已有 `dropped==0` summary 且超 `keep_iters`」的轮（没有 summary
  的轮永不删）；`eval_replays_once` 同时认新旧两种 glob（旧课程目录仍要能解析）。
  ③ **D2 处置**——per-leg 尝试计数（`attempts[task]==0` 的 never_dispatched 判据一字不动）；
  瞬断预算 `EVAL_TASK_ATTEMPTS_TRANSIENT=4`；瞬断回队退避 `min(2**(lt-1), 8)`；共享熔断
  `local_stopped`（N 条本机线程；**结算即清零**，与节点腿同款）；非瞬断失败**一手改派**给确实
  还活着的节点线程（判线程存活，不是 `nodes_ok` 非空）。
  ④ **D3 第四态**——新增 `close_reason="machine-fault"`，只在 `workers-gone` 同一前提下细分；
  判据 `lost >= MACHINE_FAULT_MIN_LOST(8)` ∧ 最大节点占比 ≥ `MACHINE_FAULT_SHARE(0.8)` ∧
  该节点是 `local` 或已停派；`dropped/lost/never_dispatched` 三键与分解公式一字不动。
  ⑤ **D4 判决准入**——`EvalRow` 纯新增 `dropped`/`close_reason`；`_row_from_summary` 单漏斗拒收
  （machine-fault 硬拒 + 缺口占 `games+dropped` 超 `GATE_DROP_FRAC_MAX=2%` 拒，与
  `BASELINE_DROP_FRAC_MAX` 同值同分母）；CLI 与训练循环各打一行「拒收 N 条缺口读数」，拒收不静默。
  ⑥ **D5 账**——`log(course=)` 显式课程归属（行标签与**镜像文件**都归对课；缺省 = 旧行为逐字节
  不变）；dashboard 只透传 `closeReason` 一个字段（无新交互）。
- **备选与否决**：① 把 `local` 降级成纯远端 —— 否（本机是四条腿里最快的腿，问题在处置不在参与）；
  ② 按 `close_reason != settled` 一刀切拒收 —— 否（云腿 summary 从不传 `close_reason` ⇒ 缺 1 局
  就整行拒、趋势窗口凭空断档；**量的诚实**才是判据）；③ `_PREFIX` 改 `threading.local()` —— 否
  （牵动 `open_course_sink` 的课程归属契约；eval 是唯一跑在 prefix 作用域外的日志源）；
  ④ 写 `eval-topup.json` —— 否（`_drain_pending_eval` 已在补同一批局，多一个文件只多一条读侧契约）；
  ⑤ summary 补写/覆盖 —— 否（账本 append-only；补评 = 既有 drain + `(iter,wver)` 聚合）；
  ⑥ 改 `eval_games_per_stage` 把 400 局「修」成 200 —— 否（双轨是设计，改它就是改语料 §15.5）；
  ⑦ 动 `tools/sim/export-eval-game.ts` —— 否（在 codehash 集里，改它要动远端 bundle）；
  ⑧ 按 `keep_iters` 一刀切删 `_eval-it<N>/` —— 否（会删在飞的评估，正是本次事故嫌疑）。
- **后果**：下一次同类故障**带 errno 可钉死**，且 scratch 在结构上不再与轮转同生共死；瞬断不再
  烧穿预算、真失败自动换机器、持续故障熔断一行说明；`dropped>0` 的读数要么带量要么不进判决。
  y22-lr it5 的 83 局按 plan §7 人工补评（`eval_a_once --iter 5 --ckpt <归档>`，wver 逐位相同 ⇒
  确定性重放），补完前该读数不得进任何判决。门禁：nn 全量 python gate ×2 绿、dashboard typecheck
  + 1667 用例 + 三份 bundle、`bun run check` 2537、`bun run build` 全绿。
  ⚠ 课程头注「×50/关 ⇒ 锚点 50 + 轮转 50 = 400 局/轮（it0 才是 200）」的补记 pending
  —— y22 四门 `.jsonc` 不在本 checkout。

## §2026-10-19-goalnn-test-path-isolation — 测试零读写真实工作目录：路径 env 全量预载隔离 + `REPO_ROOT/tmp` 硬编码清零

- **背景（用户定调）**：「测试应该用自己的 fixtures，禁止读写真实工作目录」。实测违规面：
  `dashboard/data/course-metrics/`（热备份库，20 门课）被 `pool-history.addHotMirrorCandidates()`
  当成**课程候选**读进聚合 ⇒ 池历史/贡献数用例在真实库存在时随机红（断言里直接冒出
  `y23-acc-off` 这种真课名）；`auto-offline-handoff.test.ts` 更把 `__ahand-*__` 假课**写**进
  仓根 `tmp/`；`local-worker.test.ts::loadRealConfig()` 读常量 `CONFIG_PATH`（= 线上真
  `rl-config.json`），把断言挂在本机配置上。同命令同树对照确认：**与任何功能改动无关**，
  纯粹是隔离缺口。
- **根因**：`core/paths.ts` 的惰性 env 重定向是**自愿参加**的——测试记得设才隔离；而
  `src/` 里有 **31 处** `path.join(REPO_ROOT, 'tmp', …)` 硬编码（bundles / eval-games /
  state-view / specs…），**绕开**重定向 ⇒ 即使设了 env 也照旧读写真实目录。
- **决策**：
  1. **新增 `core/paths.ts::tmpCourseDir(course)`**（`= join(tmpLogsDir(), course)`），
     把 31 处硬编码全部收敛到它（外加 `--traj-root` / heapsnapshot / `--control-file`
     三处非课程路径改用 `tmpLogsDir()` / `loopControlPath()`）。默认行为零变化。
  2. **新增 `tests/helpers/isolate-paths.ts` 预载**：进程级（早于任何测试模块 import）把
     全部 `BCITY_*` **运行时**路径 env 指进一个 `mkdtempSync` 隔离根，并在根里播种一份
     **最小种子 rl-config**（无节点、无课程）——忘了设 env 的用例拿到的是空夹具，
     而不是线上真配置或 ENOENT。挂在 `scripts/run-tests.ts` 的 `--preload`（与 no-proxy 并列）。
  3. **守卫**：`tests/test-path-isolation.test.ts` —— ① 全部运行时路径必在隔离根内且不在
     仓根 tmp / `dashboard/data` / `nn-training` 之下；② 备份库为空；③ **源码级围栏**：
     `src/**/*.ts` 里除 `core/paths.ts` 的定义外零处 `REPO_ROOT, 'tmp'`。
     `tests/gate-composition.test.ts` 加钉「启动器带 isolate-paths 预载」。
- **备选与否决**：① **继续自愿参加、逐个测试补 env** —— 否（本次 7 条红就是这么来的，
  漏一个红一个，且新增测试天然继续漏）；② 预载只重定向、**不播种 rl-config** —— 否
  （`loadConfig` 不兜底，一批「读配置」用例当场 ENOENT，红从 7 条变 20 条，与要修的污染无关）；
  ③ 把 `BCITY_CURRICULA_DIR` 也隔离 —— **否（本轮明确不做）**：`nn-training/curricula/` 是
  **入库源数据**（与 `src/` 同级的只读事实源），重定向它会让「读课程配置」的用例整体失去对象；
  它与 `tmp/` 这类**运行时**工作目录不是一类。要动得单独一刀，连同所有依赖它的用例一起搬进夹具
  （守卫里有反面断言钉住这个边界）；④ 用 fs 层 hook 拦截真实目录访问 —— 否（Bun 下
  ESM 命名导入绑定早于 hook，且会把「读源数据」一起打死）。
- **迁移的测试**：`auto-offline-handoff` / `server-api-task-bundle` / `console-eval-rounds-api` /
  `course-lifecycle` / `training-multi-course` / `training-shared-trainer` 的路径断言改用
  `tmpCourseDir()` / `tmpLogsDir()`；`server-api-error-registry` 的 P0-3 不再读仓根真账本
  （那本身就是一次真实目录读），改为断言隔离生效；`loop-control` 的「默认落在 tmp/」改为
  **先删 env 再断言**（预载之下原断言恒红）；`local-worker` 的 `loadRealConfig` 改名
  `loadTestConfig` 并改读 `configPath()`。
- **后果**：dashboard 全量 **1712 pass / 0 fail**（此前同树同命令 7 条红，其中 4 条直接
  由真实库/真实 tmp 数据决定）；fs 层追踪确认整个套件对 `repo/tmp`、`dashboard/data`、
  `repo/archive` **零读零写**。**不承诺**：`curricula/`、`nn-training/ladder/`、
  `nn-training/.venv/`（工具链）仍未隔离——它们是只读源数据/工具链，不是运行时工作目录。
- **门禁**：`bun run typecheck` 绿 · `bunx oxlint` 0 警告 · `oxfmt --check` 净 ·
  `bun run test` 1712 绿 · `bun src/server/build.ts` 四份 bundle 过。

## §2026-10-20-hub-crash-commit-limit-and-infra-auto-restart — hub「崩溃」实为提交限额击穿下的**被外部终止**；基础设施组件改为自动重启

- **报障**（用户 2026-10-20）：hub 进程在 ~14:40 崩溃了。
- **定位（证据链，五步坐实「谁杀的」）**：
  ① **死亡时刻**：看护器标记 `[console] 2026-10-20T06:44:32.071Z hub-server (作业中枢) 意外退出
     (PID 23232)`（UTC ⇒ 本地 **14:44:32** 发现）；hub 自己最后一条访问日志 **14:44:22**
     ⇒ 死于 14:44:22–32 之间。
  ② **不是自身异常退出**：日志尾**只有正常访问行**，无 traceback、无 SystemExit；hub 的
     stdout/stderr 合并进同一个日志文件（`run-python.ts`）⇒ 「没有输出」是真的没有。
  ③ **不是原生崩溃**：Windows Application 日志 14:30–15:00 **无** Application Error 事件。
  ④ **真凶**：Windows **System** 日志 Event 2004「low virtual memory condition」@ **14:44:24**
     （explorer.exe 14.5GB / Code.exe 7.1GB / vmmemWSL 1.7GB）⇒ **提交限额 37.9GB 被击穿**，
     hub 是**受害者**（它自己 RSS 只有 37MB，不在事件榜前三）。
  ⑤ **08:04 那次是另一回事**：当日 Event 2004 只有 14:44/14:49 两条；08:04 那次标记对应的
     hub 最后一条日志是**前一晚 23:31** ⇒ 整个控制台+hub 栈夜里就停了，hub 是跟栈一起没的。
     ⇒ 两起不同因，别混成一条故事。
- **影响与放大器**：训练主链**未断**（远端 PPO 走云端 hub `100.124.208.62:8787`，trainer
  全程在跑）；断的是本机 hub 的腿（控制台 admin 轮询、云机 worker 领活、在飞 job
  `95cab515f03877b8`(y26-null) 随**内存 job 表**一起消失 ⇒ 重启后查该 job = 404，trainer 补发
  unfreeze/cancel-job 也 404）。**真正的损失是放大器**：看护器**只记不重启**（文案就是
  「用控制台「启动」恢复」）⇒ hub 实停 **1 小时 29 分**（14:44:22 → 16:13:36 才恢复应答）。
- **决定**：① **共享基础设施组件意外退出 ⇒ 自动重启**（新 `dashboard/src/server/auto-restart.ts`）：
  只管 **hubServer / selfNode**；**trainingLoop 是课程状态**（门判决/ALL DONE 后悄悄续跑 =
  违背 §385 的判死判决）不自动续跑。**四道闸**（任一不满足 ⇒ 不重启，理由写进日志）：
  非设计内停车 / **宽限 12s 后账本条目仍在**（操作员手动 stop 会 `clearAnyComponent` ⇒
  条目消失 = 最可靠的「操作员要它停」判据，胜过用时间窗口猜意图）/ 组件仍不健康 /
  频次闸（10 分钟 ≤3 次，超闸只报警不动手）。启动复用动作路径（`stepHubServer`/`stepSelfNode`）
  ⇒ 账本/换代接管/端口回收语义一致。
  ② **死因文案点破形态**：`buildExitMarker` 在「有日志、但尾段无任何异常输出」时明说
  「更像**被外部终止**（系统内存耗尽 / 被杀 / 断电），而非自身异常退出；请对照系统事件日志
  确认」——原文案「非正常退出，请检查下列日志」会把操作员引到进程自己的日志（那里什么都没有）。
  ③ cloudflared / localWorker **暂不**纳入自动接管（启动路径各带隧道选项 / venv 解析）。
- **备选与否决**：① 只加内存/调页文件就收工 —— 否（那是缓解不是修复：hub 仍是「一死等人」，
  且页文件要重启才生效）；② 自动重启**所有**组件 —— 否（同上：课程状态不自动续跑）；
  ③ 宽限期内靠时间窗口猜操作员意图 —— 否（意图是动作，账本是动作的果）。
- **落点**：`dashboard/src/server/auto-restart.ts`（新）· `dashboard/src/server/exit-watchdog.ts`
  （接线 + 文案）· `dashboard/tests/auto-restart.test.ts`（新，14 例）。
- **门禁**：dashboard typecheck + **1771 用例**（+14）· `bun run check` 绿。
- **未做（机器层面，留给运维/用户）**：提交限额本身要抬——当前 commit limit **37.9GB**
  （RAM 29.6GB + 页文件约 8GB；页文件在 E: 且**当前 0 字节占用**、系统级自动管理关闭）。
  建议把 E: 的页文件设为定值 24–32GB（E: 尚余 103GB）⇒ 提交限额升到约 55–60GB；
  **改完需重启才生效**（本机在训练中，未擅自动）。另：explorer.exe 当时独占 14.5→17.6GB
  （经典泄漏，重启 Explorer 即可清）、VS Code 7.8GB。

## §2026-10-22-goalnn-offline-full-weights-backup — 打包出口用**注入的 `full_for`** 接上整份（复用 `<store>/.weights-standard/`），不改 `hub/deltas.py`、不搬模块

- **背景**：`plan/offline-full-weights-backup.plan.md` v3（评审 `plan/offline-full-weights-backup.review-bf.md`
  P0×5 / P1×7）。现场：y28-steady 的离线包里 232 份 `it-*/weights.json` **全是** 149KB 的 `BWD1`
  容器（`delta-i16`），本机一份都解不开 ⇒ 控制台只评估到 it63。
- **根因**：delta 只该活在「回传进程 ↔ hub 落地器」之间（`hub/deltas.py` 模块头），而云机产物目录
  存的就是**回传件**（`plan_run._weight_bytes` → `checkpoint`，`artifacts.py:405` 的行 fp 也是它）；
  三个打包出口（`artifacts.zip` / `LATEST.zip` / `write_deliver_zip`）直接拷字节 ⇒ 落地器没机会跑。
  `base_sha` 指云机本轮实际载入的 init ⇒ **只能在云机上落**。
- **决定**：
  1. **注入，不搬家**：`finalize(full_for=…)` / `write_deliver_zip(full_for=…)` / `package_pending(full_for=…)`，
     实现住 L6 的 `plan_run._pack_full_for`。**否决**「搬 `hub/deltas.py` → `remote/land_stage.py`」：
     `remote/artifacts` 是 L0 且被 `hub.store_offline`(L1) import，接落地器(L1) 就是向上边，
     连带 `remote.offline_deliver`/`deliver_worker`/`plan_run` 全要重编号（`tests/test_remote_dag.py`）。
  2. **复用已在盘上的 sidecar**：云机每轮开跑前就 `standard_weights_copy(prev_weights, store.root)`
     写 `<store>/.weights-standard/<sha16>.json`，而 `prune_caches` 只扫 `cache_root` 三棵内容寻址缓存
     （`download.py:452`）⇒ 历史轮打包 = 一次 `is_file()`；只有末轮现场落（基准走 `blob_root` 的
     `_cas_hit`，O(1)）。**否决** v2 的「`full-last` 当默认档」：既然命中 sidecar 几乎免费，
     分档的成本只剩包体，而段末只打一次包。
  3. **只准 `standard_weights_copy`，严禁 `ensure_standard_weights`**：后者对「叫 `.json` 却装着容器」
     的输入是**就地改写**（旁存 `.delta` + 覆盖 `.json`）⇒ 会同时破「产物根不可变」、改变 deliver 腿
     发出的字节、并让同轮重传撞 hub 的 409。
  4. **两态判据由布局所有者提供一份**：`weights_fps(root, it, prefer)` 生成器（`remote/artifacts`，L0），
     三个读者共用 —— `rows-fp`、`state-fp`（`deliver_zip._weight_fp_matches`）、`_row_for`
     （`offline_deliver`）。行里的 fp 语义**不变**（仍是回传件 sha）。
  5. **两版都进包**（整份顶名 + 原件旁存），与 hub 树逐字同形；包体每轮 +394,685B（裸字节，
     238 轮 ≈ +93.9MB，压缩率未实测）。**否决**「只替换原件」（省 1.6 倍但破坏同形，且体检拿不到原件对账）。
  6. **`LATEST.zip` 不落**（v2 的 W3 删掉）：每轮重写的便利件，加落地等于往训练热路径叠 I/O。
- **代价（诚实账）**：① 包体上界 +93.9MB（opt 也一起落，P1-3）；② 盘上已被清掉基准的轮次仍是原件
  （包照出 + 一行 WARN，不阻断）；③ 「任意历史轮」只覆盖当过 init 的轮（sidecar 没有清理路径，
  但盘可回收）；④ **notebook 那个「中途取回」cell 暂不注入**（`package_pending` 只做透传）：
  blob 根解析函数现成（`plan_handoff._blob_roots_of(work_dir)`），只是那条腿没接线（P1-5）；
  真正的顾虑是抽屉-only 那支不共享搜索预算（每次 `standard_weights_copy` 都是新 `_SearchContext`）
  ⇒ 失败路径会变成 O(n²) 扫读，而那是人点一下坐着等的。段末自动腿与「跑完再点」已覆盖
  （`package_deliverable` 复制的是 `finalize` 刚打的包）；要做先量墙钟（plan §7-Q5）。
- **落点**：`remote/artifacts.py`（`_round_entries`/`weights_fps`/`finalize(full_for=)`）·
  `remote/offline_deliverable.py`（`write_deliver_zip`/`_full_map`/`package_pending`）·
  `remote/deliver_zip.py`（`_weight_fp_matches`）· `remote/offline_deliver.py`（`_row_for`）·
  `remote/plan_run.py`（`_pack_full_for` + `blob_root`）· `tests/remote/test_pack_full.py`（新，5 例）·
  `docs/nn/remote-transport.md §113`。**`hub/*` 一个字节没动。**
- **验收**：5 条新用例绿（含「不给 `full_for` 逐字旧行为」「打包前后产物根逐字节相同」
  「落地包导入后 `rows-fp`+`state-fp` 都绿」「落地后 `_row_for` 仍认得出账本行」）；
  `nn-training/tests/{remote,hub,worker,common}` 全绿；`tests/test_remote_dag.py` 与
  `tests/test_python_loc_budget.py` 绿（`offline_deliver.py` 999/1000）。换机端到端、包体实测与
  W0 三项读数 **未做**（需现场），见 plan §5/§7。

## §2026-10-22-sibling-episode-reuse — 并行课共享「同一局」：到同族课程的工作目录里直取产物，**不做缓存**

- **背景（用户 2026-10-22，两段口径 + 一条否决）**：① 现在经常同时开多条并行课（y30 四腿），用
  相同起始权重；起点权重与 level 相同的课，**it0 评估**局局相同 ⇒ 可复用。② 若这些课的
  `paired_rotate_seed` 也相同，那么同一次迭代抽到的 **rollout** (stage,seed) 也相同 ⇒ 语料可复用。
  ③ 并行课延迟开课时前面课程的语料可能已被 `keep_iters` 清空 —— 找不到就自行重采。
  ④ **不做缓存**：缓存会让内存/硬盘爆炸。
  （曾把这件事误读成「it0 eval ≡ it1 rollout」——两侧种子池/执行模式不同，那种互相当账是伪造读数；
  复用轴是**跨课程**，不是跨迭代。）
- **决定**：
  1. **复用面 = 盘上已有的产物**（`nn-training/worker/sibling_reuse.py`），零新增存储：无缓存根、
     无索引、无 LRU。
  2. **兄弟课判据** = `episode_identity_fp` 相同（`biz/corpus_fp.episode_identity_fp` = 语料身份
     减去 `reward`；与 `corpus_identity_fp` 共用唯一构造点 `_identity_payload`，现有指纹逐字节不变），
     排除自己，上限 `MAX_SOURCES=100`（用户 2026-10-22：复用面就是全库同族，扫不全等于白算）。合法性：per-tick 奖励由训练侧读 `metrics.npy` 现算
     ⇒ 同一局 obs/action/metrics 与奖励公式无关，换课重算奖励是**精确**的。
  3. **局判据**：rollout 只认目录名 `rl_s{stage}_seed{seed}` + manifest `wver` **逐字节同**
     （`paired_rotate_seed` 不必单独比：种子流不同就撞不上同一对），且同波有 `_rl_report.json`、
     有 `metrics.npy` 且**无** `reward.npy`（预置奖励是别的课算的，不借）；eval 只认同
     `(wver16, stage, seed)` 的 `event:"eval"` 行（带 `source` 的 B/C 批行不取）。
  4. **取用方式**：rollout 命中把那一局的 shard 拷进本课 `w{idx}/rl_s*_seed*` 并 **D14 改戳成本课**、
     写 `reused.from`（派发前过滤那条腿用 `rw{i}/`，避开本机槽正在写的 `w{i}`）；eval 命中把那一行
     **移植**进本课账本（只换 iter/节点/时间/来源，读数逐字段保留，`reusedFrom` 记出处）。
     找不到 ⇒ 本课自采（不降级、不伪造）。
  5. **只替代计算、不替代账**（R6）：shard 照落本课 traj、照被 `completed_pairs` 数到；评估行照落
     本课 `eval_log.jsonl` 并进 `landed` 收工口径；`node=reuse` 不入采样机健康账（它不是机器）。
     命中局 `elapsedSec` 只算拷贝墙钟，源局耗时另列 `reusedSourceElapsedSec`。
  6. **接缝**：① `trainer/dispatch` 在**派发前**调 `reuse_rollouts`——命中局出队（不派节点、不重算），
     shard 拷进本课 traj 后由 `resumed_manifests` 从盘上收进聚合（与「重启撞上已落盘局」同一条路），
     所以没本机槽（`local_slots=0`）的集群轮也不重算；② `trainer/queue_local.run_one`/`run_local_rollout`
     用本轮索引（`reuse_index`，同 `wver` 只建一次）做同样的直取；③ `trainer/eval_dispatch`
     （`record(row=…)` 支持整行移植）。
  7. **开关**：`--reuse-siblings 0` / env `NN_REUSE_SIBLINGS=0` = 关（逐字节旧行为）；测试的全局
     隔离夹具把它钉成 0（否则单测会读操作员在跑的课）。
- **否决的备选**：① **内容寻址缓存**（先写一份、以后取）—— 用户否决：内存/磁盘占用不可控；
  ② 引用源课 traj 路径而不拷贝 —— `keep_iters` 会清、跨目录不稳；③ 用完整 `corpus_identity_fp`
  做判据（含 reward）⇒ y30 四腿永不命中，等于没做；④ 只按 `(wver, stage, seed)` 手写判据 ——
  漏关卡/难度/决策粒度就会拿别人的读数当自己的。
- **代价（诚实账）**：每轮扫一遍候选工作目录（按目录名匹配，命中才读 manifest；有 `MAX_SOURCES`
  上限）；命中一局 = 一次 shard 目录拷贝（本来就落盘）。奖励不同的腿在 PPO 首步后就分岔，it1 起
  rollout 自然 hit=0；跨机器取产物（节点集群）**未做**。
- **落点**：`nn-training/worker/sibling_reuse.py`（新）· `biz/corpus_fp.py`（拆 `_identity_payload` +
  `episode_identity_fp`）· `trainer/queue_local.py` · `trainer/dispatch.py` · `trainer/eval_dispatch.py` ·
  `worker/cli.py`（`--reuse-siblings`）· `tests/conftest.py`（测试隔离）· `tests/helpers/remote_dag.py`
  （新模块 **L4**：D14 穿模要拿 L3 的 `worker.cmd`）· `tests/worker/test_sibling_reuse.py`（13 例）·
  `tests/trainer/test_rollout_dispatch_resilience.py`
  （派发前过滤用例）· `tests/trainer/test_eval_dispatch_resilience.py`（借行不派发用例）·
  `docs/nn/training-stack.md §38` · `plan/episode-reuse.plan.md`。
- **验收**：`tests/worker/test_sibling_reuse.py` 13 passed；`tests/trainer`（eval/rollout 子集）47 passed；
  `bash tools/githook/nn-python-gate.sh` 全绿；`bun run check` 绿。

## §2026-10-23-push-mode-notebooks（2026-10-23，用户口径「worker 注册一次，hub 主动来拉」）

- **背景**：两条链方向相反。`battle.cloudflared`（GPU）已是「hub 推 job、hub 拉结果」，缺的只是
  自动注册（登记靠人手把隧道 URL 抄进 `rl-config`）；`battle.offline` 是 pull（worker 领任务、
  worker 回传产物），大流量正好穿在用户说的「hub 入站隧道不稳定」那一侧。直接的修法是把离线链
  翻成 hub 拉取式 push：worker 只做一次很小的注册/注销，之后 hub 用自己的出站连接经 worker 稳定
  的隧道推任务包、拉进度/每轮产物/段末结果。设计全文 `plan/push-mode-notebooks.plan.md`，
  评审（5 个 P0 + 10 个 P1）已逐条写回该 plan。
- **决定**（本次落地 W0–W2；W3–W6 的语义也一并定下）：
  1. **能力键是唯一持久事实，`kind` 只是注册 wire 值**。`gpu_push` / `offline_push` 写进
     `rl-config.nodes[]`；`kind` 白名单 `sampler|gpu_push|offline_push`，不落盘。**`kind` 缺席
     ≠ `sampler`** —— 缺席时**一个能力键都不动**（老采样客户端每会话注册一次，把它当成声明会
     静默删掉控制台手工挂的 `gpu_push`）；只有显式 `kind` 才「先清全部能力键、再写本次那一个」。
  2. **push 节点的探活面是 `GET /ping`（Bearer），采样节点是 `GET /v1/ping`**，按 `kind` 分流
     （`distribution.push_node_ping` 新增）；注册应答的**三条 200 出口**（含最常命中的 `unchanged`）
     都回显 `kind` + `capabilities`，worker 用它区分「新 hub 听懂了」与「老 hub 静默忽略」。
  3. **注册客户端只有一份**（`remote/push_register.py`，从 `rollout_node` 下沉，同名 re-export）：
     采样 / GPU / 离线三条腿共用退避梯子（2→120s，`auth` 立即放弃）与 `manual_snippet`；
     `Registry.ensure()` 只在 url/authKey 变化时发请求，退出 `unregister()`。
  4. **node_id 按角色稳定**：`gpu-<env>` / `offline-<env>`（与 `rollout-<env>` 同口径，CFG 可覆盖）。
     不落 `/tmp` 的随机 hex —— hub 的 unregister **不删条目**，随机 id 会让节点列表随 VM 重建线性增长。
  5. **push 启动 = 代码优先**：`registry_url or hub_url` 的 `/code`（有界 `code_wait_sec`，缺省 600s）
     → GitHub clone → 最后才是 push-first（`allow_push_first` 可关）。push-first 打头会死锁：
     没代码 ⇒ 只有 bootstrap server；没登记 ⇒ hub 不知道 URL ⇒ 不推 job ⇒ code.zip 永远不来。
     **push 与 pull 分支拆开**：pull 仍是 1h 等 `/code`、401/403 直接 FATAL（非目标：pull 不变）。
  6. **离线 push 的 hub 侧是「拉」**：pump 线程按 `it` 拉 progress/round/final，ingest 与 HTTP 腿
     共用同一份函数（不 loopback POST）；**body 的权威是 assignment**（course/run_id 由 hub 校验，
     不一致 409 + `conflict`）；缺包 ⇒ 派发器调共享的「请导包」而不是跳过；outbox 每轮写**完整
     body**（不做服务端按需重建）+ 迟到 eval 行（rev+1）+ metrics 批，ack **带 rev**。
- **否决的备选**：① 继续 worker→hub 回传、只优化 hub 隧道 —— 正是用户说的不稳定方向；
  ② `kind` 缺省当 `sampler` —— 一次普通注册抹掉 GPU 能力键（P0-2）；
  ③ 新开 `remote/deliver_build.py` 抽 body 构造 —— 会把它顶到 L2、`offline_deliver` 升 L3 并级联
  到 `plan_handoff`/`run_loop`（`test_every_layer_number_equals_its_topological_rank` 当场红），
  而缺的只有 result body ⇒ 放进已有的 `deliver_parts`；
  ④ 第三份注册客户端 —— 三套退避梯子必然漂开；
  ⑤ push-first 当默认 —— 注册死锁；
  ⑥ hub 拉到 body 后 loopback POST 给自己的 `/offline/artifact` —— 大 body 二次复制、耦合端口/token；
  ⑦ ack 无条件删 `it<=upto_it` —— 迟到 eval 行没有落点；
  ⑧ 离线 push 默认 `mode="push"` —— 老 hub/未开 `--push` 会静默空转（v1 只做 opt-in）。
- **违反后果**：把 `kind` 缺省当声明 ⇒ 控制台手工能力键被静默清掉、GPU 派发器一台都看不见；
  push kind 打 `/v1/ping` ⇒ 每台 422（或都通），注册永远不成；push 走共享 `/code` 循环 ⇒ pull 从
  「等 1 小时」变成「10 分钟 FATAL」；`--outbox` 与 `--no-deliver` 互相抵消 ⇒ **没有报错**地一轮
  产物都不出去（训练照跑、`/state` 永远空、最后被 hold stale 判掉线）。
- **代价（诚实账）**：稳定 node_id 让「两台同角色并发」互相顶 URL（需要并发时显式给 `node_id`）；
  push 模式多一份 outbox 权重副本（稳态 1–2 轮，WARN 2GiB / 上限 4GiB）；冷启动 hub 还没导
  `code.zip` 时 `source=github` 是预期（两个源的 commit 可能不同，日志打 sha12 备查）；
  真机收益（换方向后大流量成功率）没有数值证据 —— 按用户口径采信云机房 cloudflared 稳定，未复测。
- **落点**：`nn-training/common/{protocol,distribution}.py` · `hub/admin.py` · `remote/push_dispatch.py` ·
  `remote/push_register.py`（新）· `remote/rollout_node.py`（改为 re-export）· `remote/notebook_runtime.py` ·
  `ipynb/battle.cloudflared.ipynb` · `tests/{hub,remote}/` 五份 + `tests/helpers/remote_dag.py`（`push_register`=L0）·
  全文 → `docs/nn/remote-transport.md §117`；设计 → `plan/push-mode-notebooks.plan.md`。
- **验收**：`bash tools/githook/nn-python-gate.sh`（ruff+mypy+pytest）· `bun run check` · `bun run build`；
  注册面/注册客户端/notebook 契约共 40+ 用例（含真 hub loopback）。W4–W6（worker 服务端、
  hub 派发器、韧性、观测 API）未落地；**W3 outbox 地基已落地** → `§2026-10-23-push-mode-outbox`。

## §2026-10-23-push-mode-w4（2026-10-23，W4 落地：离线 push 的 worker 侧）

- **背景**：W3 给了「产物怎么落盘」，W4 回答「谁在服务、谁在训练」。新增
  `remote/offline_push_face.py`（L2 纯 HTTP 面）· `remote/offline_push.py`（L3 supervisor）·
  `remote/offline_push_task.py`（训练子进程入口）· `remote/push_tunnel.py`（L0 quick tunnel 三件）；
  `offline_boot.run_one_course` 加 `push_mode`；`run_loop/plan_run` 加 `--stop-file`。设计全文
  `plan/push-mode-notebooks.plan.md` §6（评审 P1-3/P1-9）；本条只记落地时**新定/收紧**的语义。
- **决定**：
  1. **`push_mode` 住进 `run_one_course`，不另写一条训练链**（P1-9 的原始动机就是「别让 push 分支
     成为半成品」）。它只换输入来源：包 = 任务包给的、锚点 = 任务包给的、outbox = 任务包给的 ⇒ 不探
     hub、不取包、不取锚点、不装 HTTP 打点、不打交付 zip。最容易出错的几段（两个产物根、半截目录闸、
     code.zip 引导、argv 装配）两条腿逐字共用。用例把「不探 hub」钉成**抛异常的替身**（漏接一条就是
     一次静默出站请求，而 P0-1 的病根正是「静默」）。
  2. **一单一个 outbox**（`<work>/tasks/<task_id>/outbox`）。所有任务共用一份会让 A 课的轮留在 B 课
     的 `state.json` 里，hub 按 `it` 拉就拿到别人的权重 —— 串课不可能发生由**目录结构**保证。
  3. **受理前五道闸**：`Content-Length` 有界（2GiB，超限 413 且不落半截）· 防 zip-slip（绝对路径 /
     `..` / 符号链接一律拒）· `proto == offline_push_v1` · `pack_sha256` 逐字节对账 · 包名课程 ==
     `task.course`。整包校验通过前 `tasks/<id>` 根本不存在（先落 `incoming/` 再 rename）。重投同一
     `task_id` = 202 duplicate（hub 超时重试不重复 spawn）。
  4. **ack 只有一个实现**：从类方法抽出 `deliver_outbox.ack_outbox()`（模块函数）。服务进程不跑补传
     腿（腿在训练子进程里），只能读同一份 `state.json` —— 两处各写「删哪些」就是两套 rev 语义。跨进程
     **刻意不锁**：唯一竞态是「子进程刚写的新轮还没进服务进程账本」，那轮会在 `pending()` 重现并重新
     物化（幂等，代价一份重复 body）；反向顺序（旧快照覆盖新账本）才会丢数据。
  5. **supervisor 只装配、训练在子进程**。状态面（`/state` 是 hub 的版本门与 busy 判据）必须一直答得上
     话，而训练会吃满 CPU、注册进度钩子、解 `code.zip`；一次 OOM/`SystemExit` 不该把状态面一起带走。
     `busy` = 在跑 **或** 跑完但 final 未 ack；`result=true` 的 ack 才收回 `idle`。
  6. **取消 = stop 文件 + 轮边界**（`plan_run._drive` 在本轮开工之前查，先于预算闸）：已落盘的轮一条
     不丢、当前轮不成半截，`state="cancelled"` 随 final 回传（hub 据此 release/换段，不当失败）。
     supervisor 写 stop 后有界等 120s 再 terminate。
  7. **notebook 的 push 代码来源必须早于 `_load_boot`**：push 要完整仓库代码（4 个引导文件不够 import
     `remote.offline_push`）—— hub `/code`（有界 `code_wait_sec`，401/403 转下一源）→ GitHub clone →
     本机任务包 `code.zip`，全失败 FATAL；`mode` 缺省仍是 `pull`（旧路径逐字节不动）。
  8. **quick tunnel 三件下沉 `remote/push_tunnel.py`(L0)**：GPU push 的 `run_push_worker` 改为调用同一份
     （安装/起隧道/取 URL 只此一处）。代价 = 那条腿的假钟要绑 `push_tunnel.time`（用例已按新家改）。
- **否决的备选**：① 另写 `run_push_course`（与 pull 腿逐项漂开，P1-9 的原病）；② 共用一份 outbox
  （串课）；③ 训练跑在 supervisor 进程的线程里（状态面随训练一起死）；④ 受理时先建 `tasks/<id>` 再校
  验（坏包会变成一单「已受理」）；⑤ ack 留在类方法里、服务端另写一份文件删除判据（两套 rev 语义）；
  ⑥ 把 face 内联进 notebook cell（不可测，且跨端契约没有类型/测试面）。
- **违反后果**：共用 outbox ⇒ 串课（权重接到别的课程账本）；受理前不校验 sha256 ⇒ 坏包跑成一段
  「跑完了但没有产物」；ack 不看 rev ⇒ 迟到评估行永久丢；训练在 supervisor 进程 ⇒ 一次崩溃把
  `/state` 一起带走（hub 只能判掉线）；push 代码来源晚于 `_load_boot` ⇒ ImportError 或半成品引导。
- **落点**：`remote/{offline_push_face,offline_push,offline_push_task,push_tunnel}.py`（新）·
  `remote/{offline_boot,run_loop,plan_run,plan_handoff,notebook_runtime,deliver_outbox}.py` ·
  `common/protocol.py`（`OFFLINE_PUSH_PROTO` + 七个端点 + 2GiB 上限 + 三件名）·
  `ipynb/battle.offline.ipynb`（CFG/代码来源/push 分支/markdown）·
  `tests/{helpers/remote_dag,remote/test_offline_boot_push,remote/test_offline_push_face,remote/test_offline_push_supervisor,remote/test_offline_notebook,remote/test_notebook_runtime}.py` ·
  全文 → `docs/nn/remote-transport.md §119`；设计 → `plan/push-mode-notebooks.plan.md` §6。
- **验收**：`bash tools/githook/nn-python-gate.sh`（ruff + mypy + pytest 全量）· `bun run check`；
  新增 37 个用例（11 push_mode 接线 + 17 真 HTTP 面 + 7 supervisor/子进程 + 2 notebook 契约）。
  W5（hub 派发器）与 W6（韧性/观测）未落地；worker 服务端与训练入口的语义已在 plan 就位。

## §2026-10-23-kickstart-burn-scope（2026-10-23，两条改行为腿 18:06/18:23 双双被自己的熔断收官）

- **触发事实**：`y32-tempo` it31（18:06:28）与 `y32-nohp` it41（18:23:12）先后打
  `CRITICAL KICKSTART-BURN`（基线 90.2%，最新 79.5% / 84.0%），各写 `training PAUSED` + 收官
  ⇒ trainer 不再发布 PPO job ⇒ **云端 worker 连续 2 小时领不到任务**（`tmp/hub-server.out`
  18:23:13 → 20:26:05 无任何 `POST /jobs/priority`；只重启 trainer 即恢复，hub 全程无辜）。
  两条腿当时带的是 `kickstart_ref: true`——**该开关会同时启用结果面干烧熔断**
  （`worker/loop_guards_leg.py::_kickstart_burn` 首行 `if not kickstart_ref: return False`）。
- **三条执行面缺陷**（判据本体 `worker/kickstart_burn.py` 一个字未改，改的是「何时允许判」与
  「判中了停不停」）：
  1. **`kk` 已退火到期仍在判**。判据的定义域是「锚主导更新把起点洗回去」；`kk=1.0×0.5^(it−1)`
     到 it30 已 < 1e-9。现场：`kickstart 系数已衰减到期（kk=0.0000）` 之后 1 秒仍打 CRITICAL。
  2. **平台开关 `gate_mode=notify` 时仍本地停车**。`trainer/loop_guards.py::_sync_cloud_halt`
     的契约原话是「门判决只作用于远端云机，TrainingLoop 永不停车」，notify 分支也只守了
     「不下发 cloud halt」；而腿簇自己 `training PAUSED` ⇒ 日志出现
     「notify → 只提示」紧接「training PAUSED」的自相矛盾。
  3. **`auto`/`paired` 解析不出唯一对端时回退 `baseline`**。`worker/kickstart_burn.py` 头注
     自己的回测：baseline 参照在「训练本身在飘」时零假设假阳性 **49.3%**，paired 0.56%。
     改行为类腿（抬血价 / 砍正项 / 落后税）**必然先掉胜率** ⇒ 缺 `kickstart_burn` 块 = 定时炸弹。
- **决定**：
  ① 缰绳不活跃（`kk ≤ NEGLIGIBLE_COEF`）⇒ **不判**，清空 streak 并说一次（`_rein_active`，
     系数唯一来源仍是 `trainer/loop_transport.kickstart_coef`，**不重写公式**）；
  ② `gate_mode == "notify"` ⇒ **只提示不停腿**（CRITICAL 只喊一次，`_burn_notified`；仍写
     `gate_verdict`（带 `notify, 不停腿` 字样）与仍下发云端达令；streak 保留 ⇒ 开关拨到 halt
     后下一次判定立即停）；
  ③ `mode != baseline` 且解析不出唯一对端 ⇒ **不判**（不写 `kickstart_burn` 台账，WARN 一次）。
     **要用起点基线必须显式 `mode: baseline`**。
- **否决的备选**：① 「缺 `kickstart_burn` 块就拒开课」（`load_course` 抛错）——把配置不完备
  升级成开不了课，且历史上 y4 之后无人开缰绳，代价大于收益；② 「把 `MODE_AUTO` 缺省改成
  `baseline`」——正是缺陷 3 本身；③ 「只改 notify 不改 ①②」——两条腿的实测跌幅已经证明
  baseline 判据对改行为腿无效（跌幅是设计的一部分），光改开关只会换成「halt 模式下照样炸」。
- **违反后果**：把 `kickstart_ref: true` 用在改行为类腿上，且没有显式 `mode: baseline` 或
  `peer` ⇒ 该腿在首次「连续 3 点低于起点 5pp」时被停（这次是 it31 / it41），
  **表现为 worker 无活可领**，极易被读成 hub / 网络故障。
- **落点**：`nn-training/worker/loop_guards_leg.py`（`_rein_active` + 三处闸）·
  `nn-training/tests/worker/test_kickstart_burn_guard.py`（新，7 例）·
  `test_kickstart_plan.py`（4 例随契约变更改写）· `tests/trainer/test_loop_guards_split.py`
  （借用方法/入边/出边三张闭集表 +1 项，功能用例改显式 baseline）。
- **验收**：新 7 例绿，且**回滚实测**（`git show HEAD:` 换回旧文件）`_kickstart_burn(15)` 返回
  `True` ⇒ 用例是真红不是假红；`tests/worker/` **1307 passed**；`tests/trainer/test_loop_guards_split.py`
  **29 passed**；ruff / mypy 单文件绿。
- **⚠ 生效条件**：`worker/loop_guards_leg.py` 跑在 **trainer 进程**里 ⇒ **必须重启 trainer**
  （`nn-training/**` 不在 `tools/agent/codehash-files.txt` 内，不触发节点 stale 重启波；
  但 trainer 内存里是启动那刻的代码）。改课程文件同样只对**下一次**启动生效
  （`kickstart_ref` 在 `biz/hot_reload.py::RESTART_ONLY_FIELDS`）——本次事故里 17:10 改文件、
  18:06 第一条腿照旧被停，就是这条。

## §2026-10-24-push-mode-real-machine（2026-10-24，离线 push 真机首测：派发器没接开机路径）

- **背景**：`battle.offline.ipynb` 的 `mode=push` 真机首测"失败"（Colab，UTC 13:40 = 本机 21:40）。
  现场日志停在「注册被拒 HTTP 422 → 自动注册未生效 → 手工片段」，之后什么都没有。
- **根因（三处，按严重度）**：
  1. **派发器根本没被启动**：`dashboard/src/stack/specs.ts::hubServerSpec()` 从不追加
     `--offline-push`，而 `hub/boot.py:391` 只在它出现时才构造 `OfflinePushDispatcher`（现场 hub
     argv 只有 `--discover`）⇒ 注册成功只是写进 `nodes[]`，没有任何东西会派单。控制台是唯一许可
     启动入口 ⇒ 这条腿在真机上**结构上不可能**工作。W5 只交付了命令行旗标。
  2. **成功是静默的、失败的话术是错的**：`Registry.ensure` 在「失败后转成功」那一拍不打日志，
     而 422 的横幅写成「hub 不可达/未配置」。真机实录：13:40:23 被拒 → **13:40:38 已注册成功**
     → 13:40:40 操作员把 cell 掐了。
  3. **push 节点进了采样/评估候选表**：`offline_colab` 每轮被 `/v1/ping` 探一次（它只有 `/ping`），
     `[dist] node offline-colab: ping failed — excluded this round`。
- **决定**：① `--offline-push` **恒带**（`--push-config` 一并恒带，两条腿共用一份登记表）——
  无 `offline_push` 节点时调度拍是纯空转，不设开关；② 首拍注册收敛成
  `remote/push_register.register_or_warn()` 一份实现（GPU / 离线共用），带 45s 就绪窗口就地快重试，
  按 `last_status` 分档说话（422 = 探活门，不是 token）；`Registry.ensure` 失败后转成功自己打一行；
  ③ 新增 `common/protocol.sampler_nodes()`（按能力键剔），rollout 与 eval 的节点门改用它。
- **否决的备选**：① 给离线派发加 `rl.offline_push` 开关（要动 rl-config schema + 控制台状态 +
  迁移，而"节点存在"本身就是意图）；② worker 侧先自 ping 隧道做就绪门（多一条 HTTP 面，且
  hub 的 422 本来就是同一件事的回声）；③ 靠主循环 15s 节拍重试（现场就是在这 15s 里被放弃的）；
  ④ 手工登记 `skipPing`（把失败挪到派单时刻，还是打不通）。
- **落点**：`dashboard/src/stack/specs.ts` · `dashboard/tests/hub-server-push-arg.test.ts` ·
  `nn-training/remote/push_register.py` · `nn-training/remote/offline_push.py` ·
  `nn-training/remote/notebook_runtime.py` · `nn-training/common/protocol.py` ·
  `nn-training/trainer/dispatch.py` · `nn-training/trainer/eval_dispatch.py` ·
  `tests/remote/test_push_register.py`（5 条新用例）· `tests/remote/test_hub_push_dispatch.py` ·
  `docs/nn/remote-transport.md §125`。
- **未验证（不写成已做）**：hub 重启后是否真派单还取决于业务侧（目标课程 `claimable` + 任务包
  新鲜）；真机旁证要等 `/admin/offline-push-workers` 与 `[hub-server offline-push]` 日志。

### 补记（同日 22:13，`colab-o` 二次现场：注册成功仍然零派单）

- **事实**：worker 侧修复已生效（`★ 注册成功 id=colab-o` + `★ 已自动注册` 两行都在），hub 仍零派发。
  查现场：hub `--offline-push` 依旧缺席，但这次 hub 是 22:11 **经控制台**拉起的 —— 控制台 bun 进程
  启动于 12:18，而 `dashboard/src/stack/specs.ts` 改于 21:51 ⇒ **它按旧代码拼 argv**。
- **规则（补进本条）**：`dashboard/src/**` 的改动**不会热生效**；它启动的 hub/trainer 用的是旧参数。
  ⇒ 改控制台代码后，**先重启控制台，再由它重启 hub**。只重启 hub 只会复现旧 argv。
- **两侧都加读数（避免第三次）**：hub 启动时**两态都打印**（`离线 push 派发已启用` /
  `离线 push 派发**未挂载**（启动参数无 --offline-push）`）；worker 注册成功后探一次
  `/admin/offline-push-workers`（404/409 ⇒ 一行「注册成功也不会收到派单 + 该怎么重启」，
  5xx/鉴权失败 ⇒ 不说话，不可知 ≠ 没有）。
- **另一条前提（实测活 hub）**：三门课 `state=no_pack` ⇒ 派发器首拍只会「请控制台导包」
  （`auto_handoff_decision` 三门都是 `trigger`），包好了才真正派单；且这三门都 `claimable`，
  包一就绪就会被 `claim_offline` **接管**（hold 压住本地派发）——不想被接管的课要先停掉。

## §2026-10-24-push-mode-takeover-semantics（2026-10-24，接管四条口径：包就绪≠接管 / 一次一门 / 断线照跑 / 提议后过期重导）

- **背景**：用户对离线 push 的接管语义下了四条口径 —— ① 包就绪不代表接管，**云机应答受理之后**
  才接管；② 一台自主云机**一次只接管一门**，训练完一门再接下一门；③ 接管后全程全速训练，
  传输独立、hub 断线训练照跑，hub 回来后逐轮回传；④ 导包结束到接管之间若又跑完一轮，
  接管时必须**基于最新内容重新导包**。
- **现状核查**：②③ 本来就成立（`of_worker` + `break`；训练在子进程走 `push_mode`，
  一个 hub 调用都没有，`LocalOutboxDeliverer` 只往 outbox 写、删只发生在 hub 的 ack 之后）；
  ①④ 不成立：旧顺序是 `claim → 上传`，hold 早于云机受理（413/409 时控制台显示「已被接管」而
  云机连包都没收到），且接管前不复核包是否已经被本机新一轮推进甩下。
- **决定**：`_dispatch` 只写 `phase=dispatching` 的 assignment（**不 claim**），`_pump` 在收到
  202（`duplicate` 亦然）之后调 `_claim`；claim 不到 ⇒ `cancel` 撤单但 `free_worker=False`
  （云机子进程可能已在跑，`ack(result=True)` 会在它脚下删 outbox）。接管前再跑一次
  `_still_fresh`（活清单的 `stale_reason` / 包 sha ≠ 提议时的 sha）⇒ 命中就请控制台重导、
  撤单并冷却，下一拍用新包重新提议。
- **否决的备选**：① 保留先 claim 但「失败即 release」——release 窗口里别人也抢不到，且控制台
  已经在显示「已接管」，读面仍然是假的；② 给 worker 加「受理预占」再两阶段确认——多一个状态机，
  而 `claim_offline` 的 `mine/expired/stale` 已经覆盖「同一个 worker 回来续领」；
  ③ 接管时用本地账本推 freshness——本机进度与活动权重是 hub 的事实，本地推会造第二事实源；
  ④ 把四条口径塞进 worker 侧——worker 只有 409 busy 与 stop 文件两件武器，语义的归属在 hub。
- **落点**：`nn-training/hub/offline_push.py` · `hub/offline_pack_fresh.py`（新，纯函数）·
  `hub/offline_push_workers.py`（新，登记表拆出；LOC 预算触发，账本 L3）·
  `tests/helpers/remote_dag.py`（层号 0/3、派发器 3→4）·
  `tests/hub/test_offline_push_dispatch.py`（四条新用例，窗口用 `_GatedClient` 卡住不靠 sleep）·
  `dashboard/src/web/app/panels/OfflinePushPanel.tsx`（`dispatching` 文案改成「提议中…受理后才接管」）·
  `docs/nn/remote-transport.md §126`。
- **验收**：四条用例绿（含「卡住 202 的窗口里换掉课程 weights.json ⇒ 无 hold、有导包、worker 收到
  stop」）；nn python gate 全绿；dashboard `bun run test` 1805 绿。

### 补记（`§2026-10-24-push-mode-takeover-semantics` 第 5 条，22:56 用户指令：停用自主云机 = 停止请求 + 释放课程）

- **口径**：控制台首页停用「自主云机」节点后，hub **立即停止**向它请求训练结果，并**自动释放**被它
  抢占的课程。
- **现场缺口**：`setNodeEnabled` 只回写 `rl-config.json` 的 `enabled:false`；hub 的登记表热重载后
  该机器消失，但**已在跑的那条 pump 线程仍按捕获的 url 每 5s 拉一次** `/state` 与轮 body ——
  「停用」只挡住了新提议，没挡住旧拉取；hold 也要等 TTL 才过期。
- **决定**：`_pump_loop` 每拍先判 `_worker_registered(rec)`（**只看在不在登记表，不看 `online`**，
  否则隧道抖动会被误判成停用、与第 3 条冲突）；不在 ⇒ `_settle_assignment(cancel=False,
  free_worker=False)`：ack 已入账的 rev → `release_offline`（清 hold ⇒ 课程回到 `claimable`）→
  丢账本，pump 线程退出。`_pump` 的提议阶段同样先判一次（节点在推包前被停用 ⇒ 连包都不推）。
- **不 cancel 的理由**：停用 ≠ 撤单。云机子进程可能还在跑，它的 outbox 是证据；替它删未入账的轮
  等于把还在跑的训练变成孤儿。遗留：那台机器上未 ack 的 outbox 留在它自己的盘（按 `task_id` 分
  目录），等它自己收摊或被回收。
- **落点**：`nn-training/hub/offline_push.py`（`_worker_registered` + 两处判定）·
  `tests/hub/test_offline_push_dispatch.py::test_disabling_the_node_stops_pulling_and_releases_the_course`
  （写 `enabled:false` + `reload(force=True)` 走真链路，不调 `remove()`）· `docs/nn/remote-transport.md §126`。

### 补记（`§2026-10-24-push-mode-takeover-semantics` 之后的两处真机读数问题，23:20）

- **① 首派慢 2 分 30 秒**：`hub-server.out` 实证 —— 23:09:25 「缺包 ⇒ 请控制台导包（triggered）」
  → 23:11:32 才「提议」。导包 ~2 分钟完成，剩下的是白等：触发之后调的是 `self._cool(course)`
  = **失败**用的 120s 窗。
  * 决定：导包在途（`pending_export` 在窗内）⇒ 不重触发、**不冷却**；包一落地下一拍就派。
    短窗 `PACK_EXPORT_WAIT_SEC=20s` 只兜「触发没递出去」。`_cool` 因此支持自定义窗。
  * 否决：把全局 `cooldown_sec` 改成 20s —— 那把「413 容量 / 派发失败」的重撞间隔一起缩短了，
    失败类冷却本来就该更长；两件事不该共用一把尺子。
- **② `iter it0 2/17` 不是在处理 it0**：`worker/iter_rollout.py` 的轮内打点没传 `it`（缺省 0），
  而 `seg-start`/`round-start`/`round-done` 都带。加 `it` 参数（**只用于打点标签，不参与判据**），
  `remote/worker.py` 与 `worker/iter_topup.py` 补差批一起传下去。
- **③ `seq=N` = 进度事件游标**（`progress.json` 的单调序号，跨重启续上，hub 用 `?since=N` 拉增量），
  不是轮号；`seq=0 it0 0/0` 是文件还没写时的默认读数。
- 落点：`hub/offline_push.py`（`_pack_for` / `_cool` / `PACK_EXPORT_WAIT_SEC`）·
  `worker/iter_rollout.py` · `worker/iter_topup.py` · `remote/worker.py` ·
  `tests/worker/test_iter_topup.py`（替身收 `it` + 断言每批都带）· `docs/nn/remote-transport.md §127`。
