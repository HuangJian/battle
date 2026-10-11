# 评审：rollout-topup-fastpath.plan.md（v3）

> 评审人 **bf** · 2026-10-11 · 基准 `dc340d16`（tracked 树干净）· 对象 `plan/rollout-topup-fastpath.plan.md`（734 行，未跟踪）
> 方法：逐条把 plan 对代码的断言拿到源码上核（行号 + 语义）；把「不搬回」的整条下游链逐个读方查证；
> 跑既有守卫证明「将反水的用例」当前是绿的（`tmp/bf-review-guards.log`，38/38 exit=0）。
> 未起训练、未改任何生产码、未跑真机。

## 0. 结论

**需修订后再落地（v4）。** 主根因 G（探针裸调无上界）与 §3.1 的落点**成立且可独立先落地**；
但 **§3.5（取消搬回）按现文落地会打断训练链**——plan §1.9 判「下游根本不读产出」是**错判**，
链上还有第二个、且是承重的读方（见 P0-1）。另外 §3.1 的上界选值与 §2.3/§9 自相矛盾（P0-2），
S3/S6 的空间口径没重算（P0-3），既有用例面被系统性低估（P1-1）。

行号核实结果：**绝大多数命中**（`iter_rollout.py:852/1091-1095/1125-1136/1157`、`scratch.py:158/224/237`、
`iter_topup.py:52/74/88/194/209/247`、`worker.py:658/682`、`volume_alloc.py:87/221`、
`hub_client.py:1247`、`job_fs.py:59/72`）——plan 是读过码写的，这点先说清楚。
偏差：个别行号 ±1–6（`install_tree` 实际在 857 起、`open_scratch` 调 `resolve_scratch_root` 在 256 起），
**不影响判读**；一处引用的是调用点行号而非函数定义行（`np_core.discover_shards` 定义在 60、被调在 517）。

---

## P0（阻塞：按现文实现会出错或达不成 DoD）

### P0-1 ★ 「不搬回」漏了承重读方：**节点自己的 PPO 读 `job_dir`**

plan §1.8/§1.9 的立论原话是「下游根本**不读**它」「链上没有任何一环要求产出落网络盘」，
§1.9 表把 `remote/train_core.py:553` 那行判成 ✅（理由：「`shards_root`，与哪块盘无关」）。**这一行是错的。**

```
remote/train_core.py:526      shards_root = str(job_dir)          ← 判据在**这一个字面量**上
remote/train_core.py:553      ppo_engine.load_episodes(shards_root, …)
worker/ppo/np_core.py:517     shards = discover_shards(data_root, need_files)   # 递归扫
worker/iter_rollout.py:945    partial(scratch.drain_tree, src, dst, …)  src=exec_dir/out_rel → dst=jd/out_rel
```

即：换根档下 `--out` 被算到 `exec_dir`（`_exec_argv`，`iter_rollout.py:192`），
产出**先落到 scratch 的 `r/`**，靠 `_drain_one`（`iter_rollout.py:934`，调用点 `:1037`）
逐局搬回 `job_dir/w{i}/`；**节点自己的 PPO 随后递归扫 `job_dir` 取语料**。
搬回不是「给一个不读它的下游做搬运」——**它就是那个下游的供料段**。
（`iter_info` 在 `train_core` 里只用于 `report`/`rollout_sec`/`bun_version`（:665-681），**从不**用于取 shard。）

⇒ 后果：删掉搬回而不同时改这一处，`job_dir` 下将**没有 shard**，PPO 以 0 局语料开练
（静默训空 / 或 loader 响亮报「无 shard」，两种都不可接受）。

**修法（并入 §3.5 的动作表，并改正 §1.9 该行为 ⚠）**：把读取根显式传下去，例如
`iter_info["shards_root"] = str(s_layout.exec_dir if s_layout is not None else jd)`，
`train_core` 用 `iter_info.get("shards_root") or str(job_dir)`（`iter_info is None` = 老行为逐字不变），
并补一条用例：**「产出只存在于 scratch 时，PPO 装载源 == scratch」**（这是新判据，plan 的 4.1 表里没有）。

### P0-2 §3.1 的上界选值自相矛盾，且现文达不到 §9 的 DoD

- `call_bounded` 语义：超界要**等满** `timeout` 才返回 `(False, None)`
  （`common/platform_utils.py:511-545`，`done.wait(timeout)`；docstring 明写「最多等 `timeout` 秒」）。
  它**不是**「超界即返回」的熔断。
- 于是 §3.1「复用 `SCAN_CEILING_SEC`（60s）」= 一次探针挂住就付 **60s**；而 §9/§4.2 要求
  `rollout p90 < 15s`、`max < 30s`、**补差批 < 8s**。**单个探针超界一项就把 max 打穿**，DoD 现文不可能达成。
  §3.1 里那句「实际会远小于，因为盘抖是阵发的」是希望不是工程。
- 同一节的 §2.3 又明写「**不动** `SCAN_CEILING_SEC`」，而 §5 B2 说要去「标定 `SCAN_CEILING_SEC` 对探针是否合适
  （60s 可能过长）」——**§2.3 与 §3.1/B2 直接冲突**。

**修法**：新增 `PROBE_CEILING_SEC`（B2 标定，建议 5–10s，**不改** `SCAN_CEILING_SEC` 的扫描语义），
把它写进 §2.2 新语义、并把 §9 的阈值按它重算；常量增生按本仓惯例在 DECISIONS 里给理由
（`docs/nn/remote-transport.md` 有「常量别增生」的先例评审，别默默加）。

### P0-3 S3+S6 让 scratch 空间口径失真（`/dev/shm` 可能当场 ENOSPC）

- 今天：`open_scratch` 每批一次，容量判据 `free < need×1.5` 里的 `need = need_bytes_for(本批局数)`
  （`iter_rollout.py:851` → `scratch.py:145`，`SCRATCH_EST_DEFAULT_BYTES = 8MB/局`、
  `SCRATCH_MIN_FREE_RATIO = 1.5`）；每批 `finally` 清自己的 `exec_dir` ⇒ **峰值 = 最大一批**。
- S3（私有目录 = 轮）+ S6（轮末才清）之后：同一 `r/` **累积全轮各批**，
  峰值 ≈ Σ批 = `width × 批数`（S5 之后每批 = width，上界 `max_batches`），
  而探针/容量判据仍只按**一批**算 ⇒ 判据与实际需求差着 2–3 倍（`max_batches≈3` 时）。
  Kaggle 那种 `/dev/shm` 容量本来就紧，这是**新的失败模式**（ENOSPC → 轮末对账红）。

**修法**：S3 必须同时改需求口径（`need = width × max_batches × 单局估算`，或保留 per-batch 目录、
轮末统一回收并对 Σ 做账），并把这条写进 DoD（新增 `scratch 可用/需` 读数行，不止 §3.9 那五分项）。

---

## P1（落地前必须写清，否则必踩）

### P1-1 既有守卫面被低估：至少有 **3 个文件**会反水，DoD 表里只提了 1 个

现状（本次实测，`tmp/bf-review-guards.log`，**38/38 绿**）：

| 文件 | 现在钉住什么 | 现文在哪儿反水 |
|---|---|---|
| `tests/remote/test_rollout_scratch.py`（28 例，21 处 `搬回`/`drain_tree`） | `test_rollout_drain...`(:479) 断言 `搬回=2 局/失败 0 局`(:500)、轮末清 `exec_dir`(:504)、`drain_stage_root` 回收(:506)、回退档「没有搬回这一说」(:522)、**F7「搬不回去的局不算结算」**(:527) | §3.5 全删 ⇒ 这些是 F1/F3/F4/F7 判据的**唯一载体** |
| `tests/worker/test_iter_topup.py`(:191-194) | `batches == 1`、`wasted_samples == 0`、`collected_by_stage == {quota}`（**3 局缺口 ⇒ 批就是 3 局**） | §3.3 满铺 ⇒ 批 = width ⇒ wasted > 0、collected > quota |
| `tests/remote/test_remote_iter.py:997` | `assert (job_dir / "w0" / "rollout.log").exists()` | §3.5 之后 job_dir 里没有产出（除非该用例走的是回退档——**必须显式钉住档位**，别靠机器运气） |

plan §4.1 只写了「`test_remote_iter.py` 一个字不改」+「五处反探针」，**没有「被删/被改判据清单」**。
本仓的规矩是「删活守卫要论证」（`plan/nn-training-test-debt-cleanup.review-bf.md` F1-F8 就是干这个的）：
请补一张表：**每一处被删/翻转的断言 → 判据挪去哪 → 凭什么等价**。特别是 F7：
「结算」的判据从「搬回成功」前移到「轮末 `verify_round` 对账」是**判据前移**还是**判据变弱**？
（我倾向：`data_fp` 逐位仍在，等价，但要**由 plan 说出这句**并让新用例钉住它。）

### P1-2 满铺器的「兼容」理由与事实不符：`topup_games_by_stage` 的唯一生产调用点就是被替换的那个

§3.3 写「**保留不删**（旧的 `kind=run` 单 est 重放路径仍在用）」。实测：

```
worker/volume_alloc.py:87   def topup_games_by_stage   ← 定义
worker/iter_topup.py:209    plan_g = topup_games_by_stage(...)   ← 唯一生产调用点（即将被换掉）
（其余命中全在 tests/worker/test_volume_alloc.py）
```

`kind=run` 的补波走的是**另一个**模块：`worker/volume_waves.py:158 topup_games` / `:236 plan_topup`
（消费方 `trainer/loop_volume.py:349`）。⇒ 换调用点之后，`topup_games_by_stage` 会成为
**只有自己的单测在用的僵尸函数**。二选一，写进 plan：**（a）删**（连它的单测一起，按本仓「不靠测试养死码」的惯例）；
**（b）留**并明确写「test-only，保留是为了与 `volume_waves` 的对账口径」+ 一条守它对账的用例。
别留一个模糊的「兼容」理由——那正是半年后没人敢删的那类注释。

### P1-3 决定性证据**不在仓库里**，且表内数字自相矛盾 ⇒ 主根因 G 在评审侧不可复算

- §1.1/§1.11 的账本 `tmp/x24-stack5/remote-jobs/offline/0805a8f36a305708/it-*/row.json`
  与「523 轮跨课程表」在**本工作区不存在**（`find -name row.json` = 0；无 `x24-stack5` 目录）。
  `tmp/` 不入库 ⇒ **A 档「已用现有材料闭合」不可审计**，评审者只能采信叙述。
- 数字对不上（都在 plan 自己文内）：
  - 表头「**53 轮**里 p50=11.8 / p90=88.9 / max=111.2」 vs §1.1「（**54** 轮，逐轮实证）」vs §1.11 表 n=**54**（31+23）；
  - §1.11 证据 3 表：84+142+148+126+54 = **554**，标题写「**523** 轮」，正文又说「**500** 轮里坏轮 <5%」；
  - 该表 `x21-psh-b` **出现两行**（n=84 与 n=142，p50 3.92 vs 3.05）——同名不同腿，没标 jid/leg。
- 验收课程也不在库：`nn-training/curricula/` 只有 `x24-stack{,2,3,4}.jsonc`，
  **没有 `x24-stack5.jsonc`**（`x24-stack5` 仅出现在 `DECISIONS.md:4453` / `docs/nn/engineering.md:10926` 的决策叙述里）。
  ⇒ §4.2 的「x24-stack5 续跑」不可复现。

**修法**：把提取配方落成**仓内可跑的东西**（`tools/` 小脚本或 `docs/nn/*.md` 的读数表 + 列定义），
对齐 53/54/523/554/500；§4.2 写明课程来源（入库，或「操作员本地课程 + 记 course sha」）。
**判据本身可以留**（「探针没有上界」是自证缺陷，不依赖那 54 轮），但**归因表述**必须降级为
「现场读数支持的假说」直到有仓内可复算的证据。

### P1-4 记录缺失：plan 通篇没有「Record」一节

本仓硬规矩：AGENTS §4「Record 决策进 `DECISIONS.md` + memory 一行」· §15.5「改语料/课程/奖励语义 = 新实验：
新 `--out`/`--traj` + 一条 DECISIONS，**不得跨改动续跑**」· 以及 `docs/nn/*.md` 顶部 §N 的沉淀惯例。
本 plan 的 grep：**除引用 `docs/nn/runtime-opt.md` §20 一处，全篇无 DECISIONS / docs / memory 字样**。
本项目里它至少要落：① 一条 DECISIONS（锁/快照那两笔的邻居）；
② `docs/nn/runtime-opt.md` 新 §N（scratch 探针上界 + 批粒度口径 + 取消搬回）；
③ 被退役的 F1/F3/F4/F7 判据的去向；
④ `.workbuddy/memory/2026-10-11.md` 一行。
另：§4.2 的「x24-stack5 **续跑**」与 §15.5 的「不得跨改动续跑」需要一句明确判定——
满铺器改的是**每轮样本量/更新构成**（不是语料身份、不是课程、不是奖励），我倾向判「训练侧可动，
但属于新实验：新 `--out`/`--traj`」；请把它写成一句话，别留给读者。

---

## P2（不必阻塞，但别带病落地）

1. **§1.3 的「内容寻址」不成立**：`common/scratch.py:136 ts_home(root, name) → f"{SCRATCH_TS_PREFIX}{_slug(name)}"`
   ——按**目录名**寻址（`iter_rollout.py:854 picked.ts_for(tsd.name)`），**不是** `<sha>`。
   docstring/模块头（`scratch.py:19/56`）都写着「内容寻址」⇒ **文档与代码不符**。
   「跨批命中」这个**行为**结论仍然对（同一轮同名），但「仅首批真付」「永不清所以安全」这两句推论
   要按「按名寻址」重写（两个不同 commit 若共用同名树，靠 `install_tree` 覆盖与否来保证正确性——
   这不是本 plan 的活，但 plan 别把它当成既有保证）。
2. **§1.2 的「理论下界」不是下界**：`ceil(局数/并发) × max单局` 是**波次模型**的耗时，不是下界；
   表里 it14 首批 **−1.51s**、it48 首批 **−1.08s** 的负「空等」就是它自证的破绽。
   建议换成 `max(max单局, Σ单局/并发)`（工作/槽位口径），或只对**单局批**用这个表（it34/it49 补差2 的结论不受影响）。
3. **§3.9 删键要过一遍读方**：`remote/artifacts.py:536` 读 `report["perGame"]`（控制台逐局列的数据源），
   `merge_iter_reports`（`iter_topup.py:136`）显式相加 `shards/elapsedSec/perGame/perGameSecs`。
   §3.4 把扫盘三连搬到轮级之后，**轮级报告的装配者是谁、这些键还在不在**必须写清（否则控制台那几列静默变空）。
   `clean_sec`/`搬回` 两个键我在 `dashboard/src/**` 里没找到读方（TS 侧无命中），删它们安全——但请把这条查证写进 plan。
4. **S2 的池生命周期要落一个具体点 + 一次资源账**：§2.2 写「上移到**整个 job 的收尾**（`worker.py:658` 之后）」，
   658 是 `topup_rollout` 调用点，而 job 里 658 之后还有 D14 和 `run_training_core`（PPO）。
   「整个 job 的收尾」若被读成「PPO 之后」，就是 **94 个常驻 bun 陪 PPO 吃内存**。
   请写明：**最后一批之后、D14/PPO 之前** `close()`，并给一条读数（`spawned/killed` 累计已在 §3.9）。
   另：池的生命周期变长会碰到它的空闲语义——我本次只做了关键字扫描（`worker/serve_pool.py` 见
   `ready_timeout_sec`/`_fallback`/熔断，**没扫到空闲回收判据**），请显式确认「批间空闲期不会被池自己的
   看门狗判死/补位」，这是「只改生命周期」这句话的实际工作量。

---

## 值得肯定的部分（别改掉）

- §2.1 的不变式表把「实产集 == 本批声明集」「批目录隔离」「`data_fp` 逐位」「回退档逐字」列成红线，**方向正确**；
  §1.9 里「D14 是唯一需要改的一处」虽然漏了 PPO（P0-1），但它**找对了 D14**（`worker.py:682` 逐 shard 读本地
  `manifest.json`）——这一处改 `shard_dirs` 报真实产出根即可，plan 的修法与代码一致。
- §0 的「诚实账」（好轮只省 1–2s，治的是坏轮）比多数性能 plan 都克制；§3.1 单独排第一、
  且论证「独立于其它五项」是对的（一行调用点 + 一个 import，不改协议）。
- §5 把「TPU 排不到」转成 A/B/C 档，其中 B1 用的 `probe=` 注入点**确实存在**
  （`scratch.py:203` 的 `probe` 参数、`iter_rollout` 侧无注入面）——这条**是本 plan 可离线验证的关键**，
  建议 B1 直接落成仓内用例（比 P2 的读数更值钱）。
- §3.3 的「不做 cap 截断、超采故意」与 `np_core` 既有的**逐关样本配额截断**（`train_core.py:555-560`
  `per_stage_quota`）是自洽的：多跑的局只烧墙钟，不改训练语义。**这一点 plan 没写，建议补一句**
  （它正是「超采无害」的硬依据，比 `wasted_samples` 是软指标更有说服力）。

---

## 建议的落地切片（每片独立可验）

| 序 | 切片 | 前置 |
|---|---|---|
| 1 | §3.9 读数（五分项 + `probe_probe` + 批形状；先只加不改） | 无 |
| 2 | §3.1 探针包上界 —— **但先用新增 `PROBE_CEILING_SEC`**（P0-2），并补「超界 ⇒ 回退档且不挂住」用例 | 1 |
| 3 | §5 B1 落成仓内用例（假探针线性累加 → 包上界后不再上升） | 2 |
| 4 | §3.2 `RoundContext`（`ctx=None` 后向兼容；既有用例一个字不改） | 2 |
| 5 | **P1-1 的「被删/改判据清单」先写成表**（人评审过再动 §3.3/§3.5） | 4 |
| 6 | §3.5 取消搬回 —— **含 P0-1 的 `shards_root` 改造** + P0-3 的空间口径 | 5 |
| 7 | §3.3 满铺器（含 P1-2 的 `topup_games_by_stage` 归置） | 4 |
| 8 | §3.4 扫盘分组（唯一语义扩张，等价性用例 + 轮级报告装配者写死） | 6,7 |
| 9 | §3.7/§3.8 + §3.6（回传失败备份，C2 定位不了就在 DoD 标「未落地」）+ §3.10（取证不足则降级待立项） | — |

## 本次评审的复算/实测（可核）

```
# 既有守卫当前是绿的（⇒ 会被 §3.3/§3.5 反水的那批判据确实活着）
cd nn-training && bash ../tools/githook/nn-py-safe.sh -m pytest \
  tests/remote/test_rollout_scratch.py tests/worker/test_iter_topup.py -q   # exit=0，38/38
# 语料不可复算
find . -name row.json -not -path '*/.git/*' | wc -l     # 0
ls nn-training/curricula | grep -i x24                    # face/fore/move/stack{,2,3,4}，无 stack5
# 关键判据的代码出处
remote/train_core.py:526        shards_root = str(job_dir)
worker/iter_rollout.py:934/1037 _drain_one / 调用点
worker/volume_alloc.py:87 + worker/iter_topup.py:209   # topup_games_by_stage 的唯一生产调用点
common/scratch.py:136           ts_home 按 name 寻址（非内容寻址）
common/platform_utils.py:511    call_bounded 等满 timeout 才返回 False
```

## 响应（2026-10-11 第二轮：plan v4 + 首刀落地）

| 评审项 | 处置 |
|---|---|
| **P0-1**（PPO 读 `job_dir`，搬回是承重段） | plan §1.9 行改 ⚠ + §1.8 结论改正 + §2.2-S6 连通项 + §3.5 动作表两行 + §4.1 专用用例（待 §3.5 同笔）：`shards_root` 由产出方显式给出 |
| **P0-2**（探针复用 60s 与 DoD 不相容） | ✅ **已落**：新增 `game_watch.PROBE_CEILING_SEC = 5.0`（实测依据 + 为什么不复用 `SCAN_CEILING_SEC`），§2.3/§3.1/§5-B2/§9 同步改正 |
| **P0-3**（S3+S6 空间口径） | plan §2.2-S3 加连带项（峰值 = Σ批 vs 判据 = 单批）+ §8 风险行（待 §3.5 一并落） |
| **P1-1**（既有守卫面） | plan 新增 **§3.5b 判据迁移表**（5 行，逐条交代判据去哪；含三处实测现场与日志路径） |
| **P1-2**（`topup_games_by_stage` 归置） | ✅ **已落**：模块里标注「无生产调用点 + 对账规格身份」，plan §3.3 兼容段改正 |
| **P1-3**（证据/课程不可复现） | plan §1.11 加注（归因降级为**假说**）+ §5-A 要求仓内复算脚本 + §4.2 写清课程来源 + 数字对齐（53→54、554/523/500 标为待复算） |
| **P1-4**（无记录节） | plan 新增 §10.2 记录表；`DECISIONS.md §2026-10-11-goalnn-rollout-topup-fastpath` + `docs/nn/runtime-opt.md` §35 + `docs/nn.progress.md` 已落 |
| **P2-1**（`ts_home` 按名非内容寻址） | ✅ **已落**：`common/scratch.py` 三处注释/docstring 改正（代码行为不变） |
| **P2-2**（「理论下界」不是下界） | plan §1.2 改成 `max(单局 max, Σ单局/并发)` + 负「空等」自证破绽的加注 |
| **P2-3**（轮级报告装配契约） | plan §3.4 末注写死（含 `artifacts.py:536` 读 `perGame`、`clean_sec`/`搬回` 无 TS 读方的查证） |
| **P2-4**（S2 池生命周期点 + 资源账） | plan §8 加风险行（“最后一批之后、D14/PPO 之前”写法待 §3.2 落地时定） |

**本轮另有两条实现期发现（已落）**：① 满铺器的「无顶」若写成余量 0 ⇒ 整个满铺静默成空（新用例抓到，已修）；
② cap 语义：plan v3 的「不做 cap 截断」会让课程旋钮失效、并推翻一条活判据 ⇒ 落地版改成 **cap 硬顶**，
偏差已记入 plan §3.3 与 DECISIONS。

## 局限（我做到哪、没做到哪）

- **没跑真机**：§1.11 归因（坏轮 = 探针撞盘抖）**我无法证真也无法证伪**——语料不在库里（P1-3）；
  我核的是「探针无上界 + 每批一次 + 唯一无界步骤」这半句，它**成立**。
- **没改任何生产码/用例**，review 全程静态核 + 跑既有用例（38 例）。
- 空间口径（P0-3）与池内存（P2-4）是**量级推算**（8MB/局 × width × max_batches），
  真机 `/dev/shm` 的实际容量与 `max_batches` 取值我取不到（课程文件不在库）——请按 B 档在本地量一遍。
