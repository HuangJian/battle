# NN 工程与门禁 — 技术档案

> 测试纪律 / 编码契约 / 门禁耗时 / 账本与指标 schema。
>
> **来源**：2026-09-23 由 `docs/nn.progress.md`（单文件 7.9k 行）按主题拆分；本节内编号
> 为本文件局部编号（倒序：新条目置顶、号大，`§1` 最旧），旧编号对照见
> `docs/nn.progress.md` 附录。每节内容拆分时**未改写**（只更新了内部交叉引用）。

---
## §19 本机评估的子进程捕获：gbk 解码把 stdout/stderr 丢成 None（顺带刷屏 65 行/100 局）（2026-09-22）

`rl/eval_local.py::run_local_eval_game` 的 `subprocess.run(capture_output=True, text=True)` 没给
`encoding` —— win32 中文机 locale = gbk（cp936），而 bun 的输出带 UTF-8 字节 ⇒ 解码在 subprocess
的 **reader 线程**里抛 UnicodeDecodeError。两个后果（不只是日志脏）：

1. 父进程日志被 `Exception in thread ... _readerthread` traceback 刷屏（evalA 实测 **65 次/100 局**
   = 每个本机局一次，与 `nodes:local` 局数逐一对应）；
2. **captured stdout/stderr 直接丢成 `None`**（异常死在读线程、`communicate` 不重抛）⇒ 失败路径的
   `RuntimeError(f"rc={rc} ({stderr[-160:]})")` 只剩 rc，诊断信息全没 —— 响亮错误变哑巴。

修复：抽出 `run_eval_runner_capture(cmd, timeout)`，显式 `encoding="utf-8", errors="replace"`
（`rl/queue.bun_version` 早就这么写，本处是漏网的一个）。复现/回归：
`tests/test_eval_local_capture.py`（真子进程写不可解码字节，断言输出还在 + 中文正常 + 替换符命中；
旧实现在同一命令下 `stdout`/`stderr` 都是 `None`）。实测同一 scratch 课程 100 局：traceback
**65 → 0**，本机局输出全部可读。

⚠ 同类隐患仍在别处（全量 grep `text=True` 尚有 13 处）：`dist_common._git_index_blobs`
（`git ls-files`，默认 `core.quotepath=true` 会把非 ASCII 路径转义 ⇒ 现状安全，但哪天加
`-c core.quotepath=false` 就中招）、`rl/archive.py` 的两处 git 调用同理。**新写 subprocess 捕获
一律带上 `encoding="utf-8", errors="replace"`。**

---

---

## §18 修复：本地 resume 的 D14 判据同源化（accident.plan §2/A，2026-09-21）

**问题**：D14（跨课程语料不混训）有两把尺子——`course_fp` = 课程**文件字节** sha256，
`corpus_fp` = 语料**语义**身份（env+reward 解析值哈希）。远端链路 2026-09-13 就改成语义优先
（`remote.protocol.d14_corpus_match`，hub 打包 + worker 装载共用），但**本地对账**一直只比
文件字节 ⇒ 改一下课程里的预算/路径/注释（字节变、语义不变）就把自己历史的 shard 全判成
异血缘 ⇒ **全量重采**，而云端其实照收（同一个 D14 两份实现的代价）。

**改动**：`_scan_shards` / `completed_pairs` / `settled_stage_totals` / `resumed_manifests`
新增 `corpus_fp`，比较换调 `d14_corpus_match`；`loop_core` 加 `self._corpus_fp`
（`rl.cmd.corpus_fp_for_args`，与远端发布同源）并沿 `rollout_phase` → `stream` / `queue` /
`dispatch` 透传；扫描缓存键加上 `corpus_fp`（否则一份身份的缓存会冒充另一份的答案）。
措辞同步：`_course_file_fp` / `cmd.course_fp_for_args` 改称「D14 **文件**血缘」并把
“语料身份 = corpus_fp”写进同一段 docstring——误诊的源头就是那几句命名。

**验收**：`tests/test_local_resume_lineage.py`（6：语义相同/字节不同 ⇒ 认（不再全量重采）；
语义不同 ⇒ 不认；legacy manifest 无 corpus_fp → 回退字节（旧行为逐字节不变）；缓存键含
corpus_fp；另两个消费点同源；`corpus_fp_for_args == corpus_identity_fp`）；门禁 **1961 passed**。

---

---

## §17 e2e 权重下发账本跨用例撞键：门禁随机 flake 的**残留根**（2026-09-20）

**一句话**：§13 的 `weights_push_cache_reset()` 只治了「同进程顺序跑」，**没治在途线程**——
前一个用例的收尾 `weights-push` 线程可能在下一个用例 reset **之后**才
`note_weights_pushed(...)`；而全仓库的哑权重内容都是 `{"stub": true}` ⇒ `wver`（= **文件
指纹**）完全相同、节点名也同为 `fake` ⇒ 下一个用例的权重下发被判 “kept / skip POST” ⇒
FakeServer 收不到 weights 事件 ⇒ `test_it_stream_smoke` 的 I3 断言
（`bool(wts3) and fired[0] > wts3[0]`）假红。xdist 下取决于「同 worker 的前置用例是哪个」，
这就是门禁里那条**随机 flake**（本轮实测：`e2e/` 单跑 3 次 2 次红，日志指纹
`weights[rollout] -> fake (kept, 0.0s)`）。

### 修法（从根上不撞键，而不是再加一次 reset）

哑权重内容**每个用例唯一**：`{"stub": true, "case": <tmp 目录名>}`。

| 位置 | 改动 |
|------|------|
| `e2e/test_run_rl.py::_itest_env` | 每次写盘的哑权重带 `case` 字段（`tmp_path.name`） |
| `e2e/test_volume_e2e.py`（weights 落盘处） | 同上（`tmp.name`） |

**为什么改内容而不是再补一次 reset**：reset 管不了「reset 之后才落地的在途线程」，只有**键
不同**才与线程时序无关；`weights_push_cache_reset()` 保留作双保险。顺带把「断言不该依赖
进程内跨用例缓存」这条写进注释，避免下一个人再把哑权重改成全同内容。

---

---

## §16 Windows 门禁耗时：TCP 空等 + NTFS 大量小文件（2026-09-20）

**一句话**：同一批用例在同机 WSL <5s、Windows 原生 python 5.5–9.1s 触发 >5s 警告。
根因不是训练变慢，是两类 **Windows 税**：① `127.0.0.1:<closed>` 的 connect 空等
（hub_client 默认 timeout=10/15，不可达路径 ×3 次调用 ≈ 6.2s）；② volume 桩按局数
写 manifest，600000 配额 ×3 个 stub ≈ 3k 次 mkdir+write（NTFS 慢一个数量级）。

### 修法（§14 同口径：把测试侧超时/体量拧小，生产缺省不变）

| 用例 | 病因 | 修法 | 修后（xdist -n 8） |
|---|---|---|---|
| `test_clear_halt_on_startup` | 不可达 hub ×3 次默认 timeout | `clear_halt_on_startup(timeout=)` 透传；测试 0.05s | 0.58s |
| `test_volume_topup_partial_ledger_*` | 每局一个 manifest，文件数爆炸 | 配额 600000→60000（g0=16/w2=8/w3=4），断言同比例更新 | 1.14s |
| `test_bc_train_on_epoch_called_per_epoch` | 真 torch 2 epoch + xdist 抢 CPU | 语料 60→16、batch 32→8（钩子语义不测收敛） | 3.48s |
| `test_bc_train_resume_continues_epoch_numbering` | 同上（两段训练） | 同上 | 0.79s |

`clear_halt_on_startup` 新增 `timeout: float = 10.0` 透传给 `hub_halted`/`set_cloud_halt`
（生产行为不变；测试才有办法在不可达地址上秒败）。

---

---

## §15 pathlib 钩子自证随 Py3.12 改写：accessor 已删、rglob 已并发容忍（2026-09-20）

**一句话**：`tests/test_rl_resume.py` 的竞态钩子在 Python 3.12.13 上红——不是生产回归，
是 pathlib 内部结构变了：① `_NormalAccessor`/`_Accessor` 已删；② `_WildcardSelector._select_from`
对 scandir 的 `except OSError: pass`（pathlib.py:206）⇒ **rglob 本身已并发容忍**，
旧自证「Path.rglob 必抛 FileNotFoundError」变成假红。

### 改写口径

- 钩子绑定点：`os.scandir` +（若存在）accessor 类 + `Path._scandir`（3.12 glob 真正拿走的绑定）。
- `rmtree` 与钩子同走 `os.scandir` ⇒ 加 **re-entrancy 旗标**，否则 `RecursionError`（实测）。
- 自证测试改名为 `test_hook_intercepts_scandir_and_deletes_victim`：断言
  ① hook 拦到 `scandir(victim)`；② 拦截瞬间目录已删；③ 后续真实 scandir 抛 ENOENT。
  —— 下方 walk/resume 绿色仍不可能是「钩子没生效」的假绿。
- `_dir_signature` 断言侧 `\`→`/` 归一（Windows `relative_to` 反斜杠；缓存 key 本机自洽）。

生产修法（`walk_shard_dirs` = `os.walk`）**不变**；本条只是 sentinel 随解释器演进。

---

---

## §14 门禁墙钟 157s → 24s：五个「测试替生产超时白等」的坑 + per-test 耗时预算护栏（2026-09-20）

**一句话**：单测跑成 157~185s（文档基线 ~25s，用户口径「昨天还 <30s」）不是因为工作量变大，
而是因为**多处测试在空等生产超时**——CPU 占用低、墙钟长。逐个定位（`--durations` +
`pytest-timeout --timeout-method=thread` 线程栈）后改回去，并加护栏让这类退化不能再静默回来。

### 定位手法（可复用）

1. `--durations=30` 排序：四个参数化用例各 26.5s、三个相邻用例 15/20s——**整数秒**是 sleep 指纹；
2. 单个用例 `--timeout=8 --timeout-method=thread`：栈里若全是
   `all_settled.wait(...)` / `time.sleep(backoff)`，就是**纯空闲等**（不占 CPU 的那类）；
3. 按「谁配的小节奏没生效」顺藤摸瓜，而不是按「哪个用例慢」逐个改。

### 五个坑（均已修）

| # | 位置 | 病因 | 修法 |
|---|---|---|---|
| 1 | `rl/dispatch.py` 瞬断背压退避 | 上限**硬编码 5s**：每个 502/503/504/超时 白等 5s ⇒ 3~5 次即 15~26.5s/用例 | 提为 policy `transientBackoffSec`（生产缺省 5s 不变），测试 0.02s |
| 2 | `rl/queue_local.py` rescan 节奏 | `next_probe_at` 地板**写死 1.0s**，`recoverPingSec=0.05` 形同虚设 ⇒ 4 轮回场 ≥3s | 地板改为与 wait 同源 0.05s（生产缺省 20s/5s 远大于地板，不受影响） |
| 3 | `remote/push_client.py` 409 重试梯子 | `backoff=min(2**attempt,8)`（2s+4s）被 `test_worker_refusing_job_requeues_to_another` **顺带**跑满 6s | 梯子提为 `PushDispatcher(push_attempts=)`，该用例 `=1`；梯子另用零墙钟用例逐个钉住（sleep 换记录，试满抛 `RetryableError`） |
| 4 | `tests/test_rollout_dispatch_resilience.py` | harness `queueWindowSec=30`：回场上界用尽后无人能结算，三处线程全停在 `all_settled.wait` 直到 deadline（实测 26.5s） | 该用例窗 1.5s（配速旋钮，同 `nodeRecoverFirstSec` 用法）+ 断言 4 局全进 `missing`（喂 volume 补波/resume） |
| 5 | 同文件 `test_halt_stops_round_without_waiting_window` | 用 0.3s 定时器置 halt，隐含「0.3s 时轮还在跑」——坑 2 修好后轮在 <0.3s 跑完，halt 落空 | 改为**首局取活即置位**（确定性，不依赖墙钟） |

### 第 6 个坑：一个「先当真工作放过、实为死测试」的对拍（`test_serve_wiring`）

`test_course_args_match_run_rl_echo_config` 实测 5.02s。我第一反应判它是**真工作**（起真 Python
子进程 + 导入含 torch 的 `run_rl`），打算用 `@pytest.mark.time_budget(10)` 显式放宽——**判错了**，
用户一句「为什么要依赖 torch，CI 又不真跑训练」点醒：

- oracle 的 argv 是 `["run_rl.py", "--course", stem]`，**没传 `--echo-config`**；
- 而 `run_rl.main()` 的 echo 调用点在 `if getattr(args, "echo_config", False):`
  （run_rl.py:212）之后 ⇒ 那次 dump **从未被调用**；
- 于是 main() 一路往下：`validate_args` → loop 启动 → `build_model` → **导入 torch** + 读
  `weights/<course>/*.json` ⇒ 5s 全耗在这条与判据无关的链上；
- 后果更重：本机缺权重 ⇒ 永远 skip（实测如此）；真机有权重 ⇒ 没 PARITY ⇒ `pytest.fail`。
  即**这条对拍一直是死测试**（既没对拍上，又在有权重的环境必红）。

修法：oracle 传 `--echo-config`，走**文档化短路**（echo → log → return，在 validate_args
与任何权重/torch 之前）⇒ 0.24s、不依赖权重与 torch、任何环境都真跑。顺手去掉
`echo_config` 键（调用开关，不算解析快照，否则就是唯一的假分叉——实测 98 项全同、仅此一项差）。
`time_budget` 标记与 env-blocked 跳过一并删除（拿不到 PARITY 现在一律算真回归）。
跳过数因此 4 → 3。

### 新护栏：`nn-training/conftest.py`（根 conftest，用户口径）

**单测 >5s 警告、>10s 报错**。放在**根** conftest 是因为门禁跑 `pytest tests/ e2e/`，两层要同规。
实现用 `pytest_runtest_makereport` 改写 outcome（超预算即判 **failed**，不是 teardown 报错）⇒
`-x`/xdist/summary 全是标准语义；只计 call 阶段（fixture 建拆不算）。阈值可用
`NN_TEST_WARN_S` / `NN_TEST_FAIL_S` / `--test-warn-s` / `--test-fail-s` 覆盖；个别确需更长的用例用
`@pytest.mark.time_budget(N)`（必须写明理由）。它抓的正是本节的退化形态：**不占 CPU 的等待、
真实 sleep、被当成配速用的生产超时**，都会在耗时上现形。

### 效果（同一 16 核容器）

| 指标 | 修前 | 修后 |
|---|---|---|
| nn python gate 全量 | 157 / 182 / 185s | **24s** |
| `pytest tests/ e2e/ -n 12` | 142.7s | **20.1s** |
| 最慢单测 | 30.04s（4 个用例 26.5s） | **0.24s**（原 5.02s 的对拍；见上栏第 6 个坑） |
| >5s 警告 | — | **0** |
| 跳过 | 4 | **3**（serve_wiring 那条由「永远 skip」变为真跑） |

结果：1822 passed / 3 skipped / 0 failed，ruff + mypy 全绿。

### 遗留（未擅自改生产终止语义）

「全员停派（软停/熔断）且 `nodeRearmLimit` 用尽」时，若只剩 pending 而无人能服务，整轮会
**空等到 `queueWindowSec`**（生产缺省 1800s）才收官。测试里配小窗即可，但生产侧这条路径
值得独立评估（早退 vs 等窗，与 volume 补波/resume 的交互）——属终止语义变更，不在本次范围。

---

---

## §13 e2e `check()` 静默失败：autouse fixture `_fail_loudly` + 三条被它揭出的既存红（2026-09-20）

**一句话**：`e2e/test_run_rl.py` 的 `check()` 只把失败追加进模块级 `FAILS`、**从不抛错**，
只有手写了 `f0 = len(FAILS) … raise` 的用例才变红 —— `test_it_stream_smoke` 漏了那一段，
它那条「I3 eval after weight distribution」断言于是**静默 FAIL 了很久**，直到 xdist `-n 12`
下落到不同 worker 顺序才偶发红（门禁里被当作“随机 flake”）。

### 根因（I3 那条为何是顺序相关的）

`dist_common._WEIGHTS_PUSHED` 是**进程内账本**，键 `(kind, wver)`：
`_itest_env` 给每个 e2e 用例写的哑权重内容都是 `{"stub": true}` ⇒
`weights_fingerprint` 相同 ⇒ 同一 worker 里第二个用例的权重下发走 “kept / reuse
… skip POST”，**根本不经 HTTP** ⇒ FakeServer 没有 `weights` 事件 ⇒ `bool(wts3)` 假。
实测（`-p no:randomly`，`queue_normal → stream_smoke`）**必现**；单跑 smoke 则必过。

### 修法

| 位置 | 改动 |
|---|---|
| `e2e/test_run_rl.py` 模块级 | 新增 `@pytest.fixture(autouse=True) _fail_loudly`：测前快照 `len(FAILS)`，测后新增即 `raise AssertionError`——义务交给框架，新用例不可能再忘；三处手写 `f0/raise` 随之删除 |
| `_itest_env()` | 每个用例开头 `dist_common.weights_push_cache_reset()`（进程内账本清零）⇒「本轮真的下发过权重」重新成为**结构性**前提而非对用例顺序的断言。排序那一半本就有结构保证：stream 模式 `local_slots=2 < 4` 对局数 ⇒ 队列只能靠 POST 成功后孵化的节点线程清空 |

### 揭出的三条既存静默红（全在 `-n 12` 下）

1. **`test_mirror_scalar_lockstep`**：`SCALAR_X_INDICES == [15,18]` 已过期——`schema.py:94`
   追加了 `29=iceVx`（`SCALAR_DIM=30`）。改为 `[15,18,29]` 并补 29 的翻转断言（真语境：
   iceVx 是 x 分量，必须镜像）。
2. **`test_eval_local_gate` (phase B)**：原断言「gate 关闭 ⇒ 本地 runner 零调用」被
   2026-09-15 的 `release_local_gate_if_starved`（无节点时轮末主动开闸，防 600s 空等）
   **废除**；本地 worker 与那次开闸是竞态，单跑时 worker 已退（开闸是空操作）、`-n 12`
   下它仍在等门而被放行。断言换成当下真值：无节点轮必被主动开闸 + 不挂死 + 0 结算
   （“关门则让位”由 `hold_for_local` 单函数断言 + I7 慢 eval 用例覆盖）。
3. **`test_it_longtail_race` (I6)**：**单节点**配置下 `pick_race_target` 的「本节点已持有
   则不派回」（`queue_local.py:411`）恒假 ⇒ race lane **永不触发**（dispatches 恒=1），
   与 v3.14 竞速用例同源——那例当时靠加第二个节点修好，本例漏了。补同款第二节点后
   `dispatches=2`、单轮 0.2s（`race lane` 行重现）。曾误判为「负载把另一条任务也拖过慢窗」，
   把 3.0s 慢窗调到 15s 后仍 `dispatches=1` ⇒ 反证竞速条件本身不成立，窗口长度不是变量。

### 教训

- **「静默聚合」型断言 = 事实上的注释**：绿只在有人记得写 raise 时成立。用 autouse fixture
  把义务交给框架，而不是靠每个用例自觉（本例正是自觉漏掉的那一个）。
- **单节点不会竞速**：任何基于 race lane 的用例，配置必须是 ≥2 个节点（`nd_id not in
  inflight_nodes[task]` 是其唯一入口）。
- **进程内账本/缓存必须在用例入口清零**，否则同一 worker 的用例顺序会改变被测行为
  （`weights_push_cache_reset` 已是 `tests/test_dist_common_poll.py` 的既有惯例）。
- **A/B 定位法**：把「只加 fixture、不加隔离」的副本跑一遍（`git show`+注入，不 stash），
  就能把「fixture 揭出的既存红」与「本改动引入的红」分开——本例三条都在两版里同现。

### 验证

nn python gate 全绿 182s（含 `-n 12` e2e 12 用例）· `e2e/test_run_rl.py -n 12` **3/3 绿**
（改前 3/3 红）· 单进程整文件绿 · standalone 入口 `RESULT: ALL PASS` · 根 `bun run check`
1934 pass / 0 fail。

### 2026-09-20 后续更正：I3 那条断言本身立不住（§13 的一句话被实测推翻）

上文表格里「排序那一半本就有结构保证：stream 模式 `local_slots=2 < 4` 对局数 ⇒ 队列只能靠
POST 成功后孵化的节点线程清空」 **是错的**——本地槽是**复用**的：2 条本地腿 0.01s/局，
4 局全在本地跑完（`byNode={"local": 4}`），队列在后台 `weights-push` 仍**在途**时就清空。
门禁复现（`-n 12`，3 次里红 1 次）拿到的原始读数：

```
[stream] clean-eval dispatched (dispatch queue drained)   ← eval 已派发
eval_fired=1789903983.124095  weights POST 落地=1789903983.1436107   （push 落后 19.5ms）
[dist] tail-join grace 0s 到期：7 个 worker 仍在收尾: ['weights-push', ...]
```

**结论**：I3 原断言的「eval 晚于节点权重 POST」**不是本代码的性质**——契约（`rl/stream.py`
文档串）= 清空即「采集任务已全部交出（节点/本地）」，而干净评估另走自己的 `kind='eval'`
权重握手（`rl/eval_dispatch.py`），从不依赖 rollout 那条 POST 的完成时刻。

**改法（断言换成真性质 + 诊断带读数）**：

| 位置 | 改动 |
|---|---|
| `e2e/test_run_rl.py` I3 | 改为 **`not node_disp or wts3[0] < node_disp[0]`**：有结构保证的是「**节点采样派发**晚于其权重落地」（节点 worker 在 POST 成功后才孵化，`dispatch.py::_push_need_and_spawn`）；本地腿跑完全部对局时该断言不适用（and 不该乱红）。消息里带 `weights_events/first/first_node_dispatch/eval_fired` 四个读数 |
| `rl/stream.py` | 那行日志原写 “frozen weights on nodes” —— 同一误读，改成「采集任务已全部交出（节点/本地在途），评估与其并行」 |

**教训**：断言「A 晚于 B」时必须确认 A 与 B 的因果关系**在每一条执行路径上**都成立；
「上限（`local_slots<对局数`）」不等于「排他」（本地腿复用 ⇒ 上限不封吞吐）。

### 2026-09-21 后续：I1 是同一个坑的另一半（已修）

`test_it_queue_normal`（I1）留着同族的旧断言 `drained_ts[0] > wts[0]`
（「队列清空晚于权重 POST」）——同一机制下必发：`local_slots_max=0` 仍会孵化
`cap_full = max(1, min(workers=4, n_tasks=2)) = 2` 条**本地腿**，2 局 × 0.01s 能在后台
`weights-push` 落地前清空队列。

**实测（本机，standalone 单跑 6 次，同一份未改动代码）**：**红 2 / 绿 4**（≈1/3），
与任何改动无关——`nn-python-gate.sh` 因此约 1/3 概率红。原始读数（反向探针抓到的）：

```
weights_events=1  first=1789982346.3306324
first_node_dispatch=1789982346.4449425      （权重确实先到节点，真契约成立）
drained=1789982346.3306324                   （drained 与权重 POST 同一毫秒 ⇒ 旧断言恒输）
```

**改法**：与 I3（2026-09-20）同一条 —— 断言换成有结构保证的真性质
**「节点采样派发晚于其权重落地」**（节点 worker 在 POST 成功后才孵化）；本地腿跑完对局时
该断言不适用（and 不该乱红）。修后 standalone **10/10 绿**；反向探针（把 `<` 改成 `>`）
**立刻红**且打印出上面四个读数 ⇒ 断言是活的、非空。

**教训**：同一个错误断言会在**多个用例里各写一遍**；改一条时要 `grep` 同族表达式
（`drained.*>.*wts` / `wts.*<.*node`），别只修红的那一个。

---

---

## §12 并发退场 × 磁盘遍历：`walk_shard_dirs`（os.walk）取代 shard 树上的 `Path.rglob`（2026-09-20）

**一句话**：dup-settle 输家退场（结算线程 `rmtree` 它自己的 shard 目录）与主线程的
`resumed_manifests` 磁盘对账**同轮并发**，`Path.rglob` 的 `scandir` 撞上删除即抛
ENOENT——而该异常在 **for 语句的迭代**里（不在循环体内），调用方的 `try/except OSError`
只裹了 `read_text`，挡不住 ⇒ `stream collector failed` / 整轮红。

### 现场（门禁 e2e `-n 12`，栈完整）

```
rl/dispatch.py:1121 in run → resumed_manifests(...)
rl/resume.py:248 in resumed_manifests → traj_dir.rglob("rl_s*_seed*/manifest.json")
pathlib.py:440 in _select_from → with scandir(parent_path) as scandir_it:
FileNotFoundError: [Errno 2] No such file or directory: '…/i9/dist/fake/rl_s0_seed111'
⇒ RuntimeError: stream collector failed: …
```

**症状特征**（判据）：`No such file or directory` 后面跟的是**目录**路径（抛点在 scandir
而非文件读）——这与「文件缺失」类 ENOENT 一眼可分。

### 修法

`rl/resume.py::walk_shard_dirs()`（新，单一口径）：`os.walk(root, followlinks=True)` +
`fnmatch` 目录名，契约是「scandir 失败按 onerror=None 静默跳过该层」⇒ 遍历期被删的
目录自然消失，其余 shard 照常对账（少一份已退役副本正是期望语义）。四处调用点换用它：
`_dir_signature` / `_scan_shards`（`completed_pairs`）/ `resumed_manifests` /
`trailing_stage_samples_per_game`（连带 `it_dirs` 的 `stat` 竞态）；`remote/hub_client.
iter_shard_dirs`（发布端同一条竞态，同一 helper + `_shard_mtime` 兜底）。

### 证据链

- **先复现**：门禁实测栈（上表）＋ `tests/test_rl_resume.py` 的确定性钩子自证
  （原名 `test_hook_makes_old_rglob_raise`——「旧 rglob 必抛」；Py3.12 后 pathlib 已
  并发容忍，自证改为 `test_hook_intercepts_scandir_and_deletes_victim`，见 §15）。
- **确定性竞态钩子**：在 `scandir` 的实参 = 受害目录那一刻 rmtree（= 事故时序），
  绑定点随 Python 版本：≤3.11 pathlib 锁存 `os.scandir` 成 accessor 类属性（cpython 3.10
  `pathlib.py:290`）；≥3.12 改 `Path._scandir` + `os.scandir`（见 §15）。
- **反面证据**：「retire 竞态」曾被怀疑是 collector 读 shard（`_shard_dir`）出错——实测
  不成立：把本地竞速副本拖慢以**确定性**制造 `dup settle … node=local (+retired …)`
  （`e2e/test_run_rl.py::test_it_stream_local_loser_retire`），collector 照常收官 4/4。

### 教训

1. 对**活的** shard 树做遍历的每一处都必须并发删除安全（退场是设计内的并发写者）；
   新加 walker 时先问「谁会在我遍历时删这个目录」。
2. `pathlib.rglob/glob` 的 ENOENT 抛在**迭代**里 —— 在循环体内 try 是无效防护，
   必须换成遍历期本身安全的原语（os.walk）或自己 scandir + try。
3. 竞态复现钩子要**自证有效性**（同一钩子下旧实现必须红），否则「钩子没生效」会被
   当成「测试通过」。

---

---

---

## §11 测试侧端口竞态：`spawn_bound_port()` 把「探端口 → 子进程 bind」收成一个出口（2026-09-19）

**现象**：R4 提交被 pre-commit 的 nn python gate 拦下，红的是一条与本轮改动**无关**的 e2e ——
`hub-server 未就绪或课程表不对（rc=1）`；真因（`端口 127.0.0.1:53637 已被占用——拒绝启动`）
只埋在子进程输出里（gate 的报错摘要里看得见，本地单跑该文件却 2 passed）。

**根因**：`_free_port()` 是「`bind(0)` → `close()` → **交给子进程** bind」的 TOCTOU。串行跑窗口只
微秒级，但 gate 用 pytest **xdist**：另一个 worker 的探测会拿到刚被释放的同一端口并先绑上
（窗口 = 另一端 Python 冷启动 ~1s）⇒ 先绑者赢、后绑者被 `remote/_port_guard.py` 拒启。

**修法**（`tests/subproc_util.py::spawn_bound_port()`）：

- 成功判据 = **这个子进程自报** `listening on <host>:<port>`（不是「端口上有人监听」——那可能是
  别人的服务，最坏会让用例对着陌生 hub 跑完并且通过）；
- 撞端口的子进程带 `PORT_TAKEN_MARKER` 退出 ⇒ **换端口重试**（默认 5 次）＋ print 一行（重试要可见）；
- 非端口原因的死法**立刻**抛（重试只该救端口，不该把真 bug 藏成「偶尔红一次」）；
- 超时既没自报也没退出 ⇒ 当成功（日志文案变了不该变硬失败），就绪判定交回调用方。

**证据**：`tests/test_subproc_util.py` 6 例——真 `remote._port_guard` 子进程驱动「撞端口 → 重试」、
非端口死法不重试、上限到顶、超时兜底 + `tail()` 可读、守卫文案对齐、**源码守卫**（两个调用点
不得再出现裸取端口）。全量 gate `1579 passed / 4 skipped`（+6）；`-n 6` 并发复跑相关 5 文件 83 passed × 3
（**未见重试**——竞态本身稀有，所以靠确定性用例而不是压力测试来证明）。

**顺带**：两个调用点各自那份 `_startup_output()`（「进程活着时 read() 会阻塞到 EOF」的绕行）删除，
改由 helper 的 reader 线程实时收行 + `BoundServer.tail()` 取尾部——诊断面从「只能在进程死后读」
变成「随时能读」。

### 续（同日）：全部取端口点收编——四份私有 `_free_port` 清零，守卫改扫全测试面

仓库里还剩四份私有副本，语义分两档，故出口也定成三个（全在 `tests/subproc_util.py`）：

| 出口 | 适用 | 机制 |
|---|---|---|
| `free_port()` | **进程内**探端口（`test_port_guard` / `test_push_bootstrap_teardown`） | bind 紧随探测，窗口微秒级 |
| `spawn_bound_port()` | **单个**服务子进程（`test_multi_course_hub` / `test_instance_lock` 的顺序双启 / `test_worker_server_lock` / e2e 的 `_Hub`） | 等**这个子进程**自报 listening；撞端口换端口重试 |
| `retry_on_port_stolen(scenario)` | **多个**进程抢同一端口（两处「三启 ⇒ 恰好一个」） | `scenario(port)` 抛 `PortStolenError` ⇒ 换端口重跑；其余异常原样上抛 |

第三个出口的理由：「三启同时启动」必须让 N 个进程抢**同一个**端口，单进程版用不了；端口若被外人
抢走则 N 个全灭且输出带占用文案——那是**场景作废**（换端口重跑），不是被测行为不对，既不该报红
也不该把断言放宽。

**另一个真坑（这次顺手拆掉）**：helper 的子进程 stdout 一开始写 `text=True`——那按**控制台代码页**
解码（zh-CN Windows = gbk），而服务侧按 `force_utf8_stdio()` 写 UTF-8 ⇒ 读线程撞 UnicodeDecodeError
静默死掉，`lines` 永远为空 ⇒ 自报监听看不见、端口竞争也识别不出。改成显式
`encoding="utf-8", errors="replace"`，并加一条源码守卫钉住（`test_child_output_is_decoded_as_utf8_not_console_codepage`）。
同一个根因 2026-09-17 在本仓吃过一次（`test_instance_lock` / `test_worker_server_lock` 当时被迫用
`capture_output=True` + bytes + 手动 utf-8 解码）。

**守卫扩面 + 剥注释**：从「2 个文件的点名清单」改成扫全部 `tests/**` + `e2e/**`（剥掉注释后扫，R4 那条
教训——「已退役」的记录恰恰写在注释里）：① 不许再有私有 `_free_port`；② 凡 argv 里出现
`"-m"` + `remote.hub_server|remote_worker_serve|remote.worker_server` 的文件必须借端口（用**带引号的
argv 元素**判定，避免把 `from remote.worker_server import` 这种进程内用法误扫进来）。

**证据**：`tests/test_subproc_util.py` **9 例**；全量 gate `1582 passed / 4 skipped`（+3）；根 `bun run check`
1868 绿。记录：`DECISIONS.md` §2026-09-19-goalnn-test-port-convergence。

---

## §10 节点门统一：rollout 与 eval 同用 codehash-files.txt（eval 门改比 codeHash，ping 不再报 engineEpoch）（2026-09-17）

**为什么记这一笔**：eval 节点门比的是 `engine_epoch = sha256(git_full_commit + GAMEPLAY_SPECS
指纹)[0:16]`——**掺了 git commit**，而 gameplay 集还是 TS（codehash-files.ts）/Python
（dist_common.py）两侧手工镜像的第二份清单。于是任何与 rollout/eval 无关的提交（dashboard /
nn-training / docs）都把全节点判 stale，运维只能同步 + 重启 sampler-agent（2026-09-15
x3-power it30：epoch 全员 mismatch → nodes_ok=[] → 600s 零局；2026-09-03 x3-chip-k10
it1–it4 四节点同因全 skip）。用户指令：两者统一用 `tools/agent/codehash-files.txt` 作为
「节点是否可用」的事实来源。

```
唯一事实来源（改这里 = 同时决定 rollout 门与 eval 门）
  tools/agent/codehash-files.txt
    ├ src/nn/**（策略）· tools/sim/export-*（导出器）· tools/agent/*.ts（agent 协议）
    └ 2026-09-17 并入：src/game/** · src/config/** · src/utils/**（RNG）· src/ai/**（God）
                        · tools/det-golden.v1.sha256   ← 原 GAMEPLAY_SPECS 双份清单已删
  排除原则（同处写明）：dashboard/** · nn-training/** · docs/** · plan/** · tests/**
                        —— 它们的提交不得让任何节点变 stale

  /v1/ping 只报 codeHash = hash(清单展开) —— 唯一的节点门字段
  rollout 门（dispatch / rescan）与 eval 门（eval_dispatch §6.6 / batch_eval 严格）同用
    dist_common.check_code_hash(ping, 本机 codeHash)
  engine_epoch = sha256(codeHash)[0:16]（训练机侧算）= **账本记录值**：进
    EvalGameRow.engine / 心跳 / S10 哨兵；**不再是门**，故不进 ping（原 check_engine_epoch 删）
```

**语义变化（有意为之）**：S10「引擎漂移」的范围从 git commit 收窄为清单代码（含 src/nn 策略），
**比旧式更不容易误报**（以前连 docs 提交都算漂移）；而引擎文件入清单后，「改引擎不改名 codeHash」
的漏网口同时关闭。节点门也只剩**一个字段（codeHash）与一个判据**：旧 agent「无 engineEpoch →
过渡期放行」的宽松分支随之取消——codeHash 是 rollout 门一直都在用的字段，比它只严不宽。**代价**：本次自身即清单变更 ⇒ 全节点一次性
判 stale ⇒ 一轮预期内的升级波（同步代码 + agent 重启后自动纳管）。

**门禁实测（2026-09-17 收尾全绿）**：根 `bun run check` **1851 pass / 0 fail**；dashboard
typecheck + **485 pass / 0 fail**（顺带修掉进入本次任务时那条**存量红**
`server-actions-resolve-course-bc`：用例引用的是从未存在过的课程名 `x1-rebirth-a2`，仓内课程是
`x1-rebirth.jsonc`，已改成真课程名）；nn python：ruff + mypy（225 文件）干净、
**1183 passed / 3 skipped**。当时剩下一条未跑
`test_remote_iter::test_spec_argv_weights_must_be_relative` 是**平台性存量红**：
`_iter_rel_path` 只挡 `..` / `~` / POSIX 绝对路径，`C:/weights.json` 在 Linux 上既非
`os.path.isabs` 也非 `Path.isabs`（Windows 上才拦得住）——**已于同日修掉，见 `docs/nn/runtime-opt.md` §1.1**。
决策见 `DECISIONS.md §2026-09-17-goalnn-unified-node-gate`。

---

## §9 python 全量门禁提速：worker 数 × CPU 内线程数必须成对调（2026-09-17）

**背景**：用户问「检查 python 全量门禁，提高可维护性，减少耗时」。门禁墙钟实测 39~50s，而
ruff(~1s) / mypy(~4s) 完全藏在 pytest 后面 ⇒ 只有一个瓶颈：pytest。

**根因（推翻 `docs/goal-nn.progress.md §25` 的旧结论）**：旧默认 `-n 4` + torch 默认内线程
（= 物理核 16）⇒ 4 worker × 16 线程 = 64 线程抢 16 核，**严重超订**。§25 那条「n=4 最优、
auto=16 反更慢」正是这个假象的读数（worker 越多越慢本身就是超订证据），不是「torch import 开销」。

**实测**（16 核，`python -m pytest tests/ e2e/` 单独计时，同机交错 3 次/项）：

| 配置 | 均值墙钟 | 备注 |
|---|---|---|
| `-n 4` 默认线程 | 36.3s | 旧默认 |
| `-n 12` 默认线程 | 44.7s | worker 越多越慢（超订证据） |
| `-n 4` 线程=1 | 39.7s | 只封线程不救小并发 |
| **`-n 12` 线程=1** | **24.7s** | ← 新默认 |
| `-n 8` / `-n 16` / `auto` 线程=1 | 25.5 / 23.5 / 24.0s | 8~16 平坦 |

**改动**：① 门禁 export `OMP/MKL/OPENBLAS_NUM_THREADS`（`NN_GATE_THREADS`，默认 1，0 = 不设）
+ worker = `min(核数, 12)`（`NN_GATE_NPROC`；取 12 同时给内存封顶：峰值 pytest 进程树
RSS ≈ 3.9GB ≈ `-n 4` 的 3 倍）；② 三路工具启动去重成 `run_tool`（原先 LIVE/detach 二选一
复制了三遍，加一个工具就要再抄一遍，漏掉 detach 分支会让 Windows commit 卡死）；③ 去掉 pytest 的
重复 `-q`（addopts 已有 `-q` ⇒ 原本是 `-qq`，把结尾的「N passed in Xs」吞了，hook 日志里看不到
用例数与耗时）——现在日志里是 `1010 passed in 21.2s`；④ `t0` 提到启动工具之前，报告的秒数 = 门禁
真实墙钟（旧版只算「等最慢那个」）。

**同策推广**（防「门禁快、日常入口慢」的漂移）：`task.py` 四个 target 共用 `clean_env()`，线程封顶
只改一处即全生效，worker 一律 `-n auto`（**2026-09-15 把 `-n auto` 判为「沙箱 ~34% 停滞」头号嫌疑
是误判，本次回退**）；`nn-training/Makefile` 加 `NPROC ?= auto` / `THREADS ?= 1` + export；
CI `nn-training.yml` 加 job 级线程封顶，单测层从**无 `-n`**（job 里最长的 pytest 步）改为 `-n 2`
（与 e2e 同口径）。

**验证**：门禁连跑两次 20s / 23s rc=0（改前 39 / 50s）；日志含 `1010 passed in 21.2s`；ruff +
mypy 绿；`test_githook_scripts.py` 新增两条静态护栏（封线程 export + 核数派生 worker 数），并用
**变异测试证明非空转**（删 export / 退回 `-n 4` / 去掉上界 → 3/3 被抓住）；CI 侧封顶用
`taskset -c 0,1` 压成本机 2 vCPU 模拟（`-n 2`：默认 57/73s → 封顶 55/53s）。

**教训（通用形态）**：把「并行度不够」当结论之前，先看**每个进程内部**开了多少线程——
`-n`（进程数）× 库默认线程数（= 核数）是一对乘积，只调一半得到的结论会**反号**（本来该加 worker，
却得出「workers 越少越好」）。

---

## §8 metrics v6 落地：分敌种命中/击杀列（idx31–38，TS+Python 全链，2026-09-16）

**为什么记这一笔**：T5（分敌杀信用）的工程前提（x3-credit-p6 课程文件头「工程前提」
节明示）。`docs/nn/experiments.md` §8 判 T6 全面阴性后，T5 主路径依赖本条落地；观测/模型/encoder 未动
（x3-power-followup §52 禁令维持）——本条是**采集通道加宽**，非奖励语义变更。

**落地内容**（plan/t5-metrics-v6.plan.md，全部尾部追加、0–30 列号永久不动）：
- `METRICS_DIM` 31→39，`METRICS_VERSION` 5→6（TS/Python 双侧同源，manifest 与
  summary 均改由常量导出，消除硬编码副本）；idx31–34=kills{Basic,Fast,Power,Armor}
  （只记 `tank_destroyed by=player` 打敌车），idx35–38=hits{同序}（`enemy_hit.targetKind`，
  含致死命中）；列序 = `ENEMY_KIND_ORDER = [basic, fast, power, armor]`。
- 验证三层：跨语言 SSOT 断言（两侧列名/idx 互锁）＋新 `tests/sim/metrics-v6-census.test.ts`
  （独立重实现对账＋守恒 sum(killsByKind)==玩家击杀敌车＋同 seed 双跑确定性）＋
  golden 复用零填充（v6 尾列对 v7 公式零贡献，不重铸 oracle）。
- 新课程 `x3-credit-p6` / `x3-credit-p6-r2`（power 杀信用 2× 主臂 ×2 独立 run，
  `docs/nn/experiments.md` §8 新规下限；单变量 vs x3-start；verdict 规格已冻结在课程文件头，
  **待用户手工开训**）。首次开训触发采样节点 codeHash 重编译，属预期。
- ★ 残差桶（评审 P0，方案 A）：bomb 清屏（`SimulationPowerUps.ts:391`）不推
  `tank_destroyed` ⇒ 四桶之和恒 ≤ 标量 `kills` 列（God AI 长局缺 11.6%）。
  公式末尾带 `wKillBasic*(kills − ΣkillsByKind)`——全 3.0 剂量下与 x3-start
  标量公式逐字等价（golden 6 局对账 |Δ|=0），单变量纯度保住；已知残留
  （桶按 basic 计价，bomb 杀 power 无溢价）记在课程文件与结算模板。
- 教训（本条勘误位）：kills 包络上界 40 是单关 2× 余量——多关合一 episode 的
  课程形态须重估（caveat 注释已落在 `reward_validation.py` DEFAULT_RANGES）。

---

---

## §7 ipynb 清场 + tpu-probe 单源化（2026-09-13，用户指令「重构 ipynb：删无用 notebook，重新整理 tpu-probe 使其更模块化并保持独立性」）

- **删三个被取代的旧 notebook**（均无活引用，文档中的历史提及保留为史实）：`p4-onset.ipynb`（课程专用 BC → 通用 `battle-bc.ipynb`）、`m2_colab_worker.ipynb` + `p4-onset-rl.ipynb`（旧式内联 worker → 单 cell `battle-rl.ipynb`，运行时已迁 code.zip）。现存三个：battle-rl（云端 worker）、battle-bc（通用 BC 蒸馏）、tpu-probe（吞吐探针）。
- **tpu-probe.ipynb 重构为 §0-§6 分区 + Cell 地图**：§1 vfio 诊断/释放两个 cell 原各自内嵌一份 `find_vfio_holders` 拷贝 → 合并为单一「工具函数」cell（无副作用）+ 两个 thin driver（NameError 时提示先跑工具 cell）；§3 脚本 cell 从"手工保持逐字节一致"改为**单源生成**；测量 cells（TPU 三遍/GPU 单卡/多卡/CPU）原样保留为 §4 thin driver。
- **单源机制**：正本 `nn-training/tools/tpu-probe.py` → 新工具 `tools/sync_tpu_probe_nb.py`（写回 / `--check`）→ notebook 的 `%%writefile tpu_probe.py` cell；新测试 `tests/test_tpu_probe_notebook.py` 双守卫（内嵌==磁盘逐字节 + 工具 `--check` 通过），drift 在 python-gate 常驻拦截。**运行时独立性不变**：notebook 在 Colab/Kaggle 仍自包含（脚本随 cell 写盘，无需克隆仓库）。
- **顺手修三处 HEAD 上已红的旧账**（7aeafb2 / 26f9171 / 99c9367 落地时未跑全门禁）：
  ① `rl/forensics.py::_rss_mb_posix` 同源化——`getrusage ru_maxrss` 在 WSL2 内核**持续滞后**于实际常驻集（实测滞后 ~16kB），与 `/proc/self/statm` 混用导致 `test_rss_mb_sane` **确定性红**（3/3）；改读 `/proc/self/status` 的 VmRSS/VmHWM 同源快照（20 万次采样 0 违例，peak ≥ cur 由内核保证），getrusage 降级路径保留给非 Linux POSIX。
  ② `rl/eval_replays_once.py` 两处 RUF100 unused noqa（`# noqa: E402/BLE001` 指向未启用规则；意图注释保留）。
  ③ `tools/training/console/api.ts:782` `CURRICULA_DIR` → `curriculaDir()`（TS2552，7aeafb2 引入的未定义标识符）。
- **环境记录**：满编 `-n 4` 下 `test_bc_epoch_e2e` 两例曾红（fake_worker POST 400 manifest 校验拒绝），单跑与 `-n 2` 均绿——负载型 flake 非回归，e2e 并发时序对 CPU 争用敏感（§0.1#4 单文件绿 + 全组红 = 环境的又一实例）。

---

---

## §6 自主审查轮：battle-rl.ipynb 单 cell 化 + 真跑暴露四缺陷修复 + bc-c4 it1 真实产物（2026-09-13）

**BC 云端 notebook 重构（用户指令：网页只执行一个 cell 建连，其余完全自主）**——
`nn-training/ipynb/battle-rl.ipynb` 从 8 代码 cell 顺序流程改为 **markdown + 单代码 cell**：

- 参数置顶（MODE/HUB_URL/HUB_TOKEN + 行为参数）；自动 = 平台/设备探测（CUDA/TPU/CPU，
  TPU 独占设备约束全保留：内核绝不 import torch_xla，占用者诊断在）、保活、
  `/code` 代码引导（**404 = 每 30s 等待重试**——旧版硬失败，顺序敏感；现在先起 worker
  后起 trainer 完全合法）、worker 监督（热替换 86 自动重启 + **崩溃指数退避重启**
  ≤MAX_WORKER_RESTARTS；rc=0 干净退出/-2 配置致命不重启）、push 模式 cloudflared
  自动安装 + bootstrap 优先 hub /code（GitHub tarball 兜底）。中断 = 干净停机。
- **E2E 实证**：本地 hub + 抽取 cell 源码 headless 执行——worker 先起等 /code 重试
  → trainer 后起发布 → cell 引导代码、领 kind=bc 任务、真训练 5.5s、回传 accepted。
- **二次精简（同日）**：运行时逻辑再搬进 code.zip——新模块 `remote/notebook_runtime.py`
  （设备探测/pull/push/崩溃退避重启，入库受 ruff+mypy 门禁），cell 降到 **134 行** =
  参数 + 保活 + /code 引导 + `run_notebook(CFG)` 委托。修运行时不再重发 notebook
  （重跑 cell 即拉新版）；push 的 GitHub tarball 兜底随之删除（/code 等待重试已覆盖）。
  headless E2E 复验 PASS（code.zip 100 个 .py 含本模块）。
- 新夹具课程 `curricula/bc-e2e.bc.jsonc`（BC 管线流校验，对齐 tiny-a 定位；wins_only=false
  适配 300-tick 冒烟）。

**真跑暴露四缺陷（全部修复）**：

1. **smoke 语料污染真轮**：smoke shard 落 `bc-data/it1` 被真跑断点续跑复用（40 局里混进
   1 局 300-tick timeout 局）→ smoke 落 `bc-data/smoke` + 全量重采不复用；`round_name`
   贯通采集/发布/落位三处（iter_bc_shard_dirs/verify_and_land_bc/publish_bc_job）。
2. **本地训练路径根错位**：run_bc chdir 仓库根但子进程 cwd=nn-training，相对路径
   FileNotFoundError → 全 resolve 绝对路径；子进程输出 capture 缓冲到结束（§16.3 违例）
   → Popen 逐行流式中继。
3. **本地 ckpt_every 死键**：train_local_bc 漏传 --ckpt-every（云端 manifest 有）→ 接上；
   真跑 ckpt.10..60 全落盘。
4. **本地 WEIGHTS.md 行全 0**：extra_meta 是 weights JSON **顶层合并**（sizes/best_val_loss/
   history），误读 `meta` 子键 → 修读法 + it1 注册行已人工订正。

**真实产物（bc-c4 it1，全链本地实证）**：arena4 语料 40 局（29 胜保留/11 败过滤，
6442 帧，18.6s 采集）→ CPU 6 线程 60 epoch 37 分钟（val_loss 2.24→1.6260，move acc
0.236→0.566，fire acc 0.761→0.764）→ 归档 `nn-training/weights/bc-c4/bc-c4.it1.20260913-161311.json`
+ 中央 WEIGHTS.md 行 + 活跃指针 `tmp/bc-c4/weights.json`。该权重可直接作 RL 课程
`bc` 种子 / `kickstart_ref` 快照。

---

## §5 python 门禁 flake 全面审计：静态扫雷 + 负载轰炸（2026-09-13，§4 后续）

**方法**：① 静态扫雷——全测试目录 grep 紧墙钟（sleep/wait/timeout 断言）、被采样日志行
依赖（RACE_LOG_SAMPLE 类）、固定端口、mtime 排序、xdist 共享 tmp 撞路径；② 历史证据——
git log 的历次 flake 修复（`82cc6d6` I9 假红、`b0317ad` 沙箱删除配额、§4）+ tmp 红跑
日志；③ 实证——全量 541 项 × 5 轮禁用 `-x`（首败不停、收全部失败）轰炸，其中 2 轮带
4 spinner、叠加真机 c4-chip03 训练负载；I7 定向 10 连跑（6 spinner）。

**发现与处置**：
- **I7（`test_it_eval_deferred`）set-then-clear 竞态 + 3s 紧等待 → 已修**：
  `eval_th.start()` 之后才 `eval_dispatched.clear()`——负载下主线程若在 start→clear
  之间被调度延迟数秒，eval 线程先置位再被清掉 → 必假红（与 I9 §4 同族）。修法：
  clear 提前到 start 之前（置位必属真实派发）+ 等待 3s→30s（正常 ~10-100ms，覆盖
  ping→POST 权重→worker 孵化→首局 fetch 全链路的负载放大）。
- **I10（tail join grace）`took10 < 15s`**：对设计的 2s grace 有 ~4× 余量，观察保留。
- **其余全部干净**：端口全 bind 0（ephemeral）；mtime 仅等值断言；tmp_path 已由
  conftest 唯一化（pid 参与命名）；`test_dist_common_poll` 的 `dt < 5s` 有 4-5× 余量；
  test_upgrade 的短超时是故意触发降级路径的合法输入；I1/I3 的排序断言走真实回调与
  服务器事件、不经过可采样日志；P0 新增 test_loop_gate_soft_remediate 纯逻辑零时序。

**实证结果**：5 轮全量 2705 次执行零失败（19.4s / 21.3s / 19.8s / 24.8s / 54.4s——
末两轮带 spinner + 真机训练负载）；I7 定向 10/10 绿。结合 §4 修复前的历史红跑，
目前门禁内已知 flake 清零；`-x` 首败即停是门禁的有意设计（快速反馈），审计口径
须用 `-o addopts=` 覆盖。

---

---

## §4 I9 长尾竞速测试 flake：两条失败路径 + 双通道断言修（2026-09-13 复核 `python-flaky.issue.md`）

**问题（复核确认真实并复现）**：`test_run_rl.py::test_it_early_race_v314` 在 xdist -n 4
门禁下偶发红（他机 2/3；本机 8 CPU spinner 负载下复现，`tmp/pygate-flaky-repro2.log`；
4 spinner 与单跑 ×8 均绿）。与 P0 提交 `d17e9f0` 无关（stat 确认未触及调度路径）。

**根因（比原 issue 的分析多一条路径）**：断言的证据链有两层隐性依赖，负载下各自翻车——

- **路径 1（原 issue 发现）**：竞速检查的墙钟时刻。空闲槽无任务可派时按
  `all_settled.wait(0.5)` 空转——**轮询粒度 0.5s 本身 > 0.4s 慢窗**；xdist/沙箱负载下
  首个竞速检查实测晚至 +1s，seed111 已结算离场，race lane 只能命中剩余任务（红跑日志
  命中 `(3,9006)`）。
- **路径 2（本次复核新发现）**：证据通道被采样。`741c395` 起 race drops 每类只打前
  `RACE_LOG_SAMPLE=2` 条；而 v3.7 突发 fanout **不打派发日志**，会在派发突发期抢占
  慢任务的 dup 槽——快副本 0.01s 获胜即把任务弹出 inflight，race lane 从此**结构性
  选不中它**，慢主副本的证据行（`main_by_fanout` 类）落在第 3 条后被采样挤掉。
  复现红跑（repro2）正是此形态：race 命中 `(2,9005)`，`main_by_fanout=3` 只打 2 条。

**修（test_run_rl.py，三处）**：
1. **慢窗 0.4→3.0s 上限（FakeAgent）+ 竞速副本到达即提前放行**：远大于轮询粒度 +
   观测抖动（+1s）；慢 handler 每 50ms 轮询同键并发 fetch 计数，≥2（= 竞速副本已
   派出、被测性质已成立）即提前返回——窗口只在回归（无副本）时才睡满，正常路径单轮
   回到**亚秒级**（实测 I9 0.38s / I6 0.4s，比 0.4s 窗时代还快，门禁墙钟无净增）。
   修掉「v3.15 判据不依赖窗长」的错误注释。round 仍远低于 30s 死锁兜底。
2. **I9 测试 cfg 设 `tailFanoutN: 0`**：突发 fanout 是唯一不打日志的复制通道，关掉它
   让 race lane 成为唯一复制路径（fanout 语义由 I6/longtail 测试覆盖）——证据不再
   依赖「seed111 未被突发复制」的时序运气。
3. **断言改双通道 OR**：日志行（tail-race 等，不采样）**或** FakeAgent 派发计数
   `(0,111) ≥2`（对采样免疫，与 I6/longtail 既有断言同款式）。2026-09-06 曾硬断言
   计数而"HTTP 事件偶发缺席"——OR 化吸收该教训；回归（`pick_race_target` 断）时两
   通道同时缺席必红（已反验：patch `return None` → 红 → 还原；提前放行下仍成立，
   因无副本时慢窗照旧睡满）。

**验证**：单跑绿（`log evidence=True, dispatches=2`；提前放行后 I9 0.38s / I6 0.4s，
较 0.4s 窗时代还快）；反验红（两种窗口形态下各验一次）；门禁 5 连（第 2/4 次带
6 spinner 负载）+ 优化后 3 连全绿。教训：**「结构确定性」判据仍可能依赖墙钟
时序**——写竞速/超时类测试时，慢窗必须按「轮询粒度 × 安全系数」取值；改日志采样
（RACE_LOG_SAMPLE）前必须 grep 测试对被采样行的依赖。

---

---

## §3 CLI 子进程编码契约：环境无关的三层修（2026-09-13 复核 `python-cli.issue.md`）

**问题（复核确认真实）**：`test_gate_check.py::test_cli_dry_run_exit_code` 用裸
`subprocess.run(..., text=True)` 捕获 `rl.gate_check --json`——父侧解码编码 =
`locale.getpreferredencoding(False)`（**解释器启动期决定，运行时改不了**；zh-CN
Windows = cp936），子侧却由启动环境任意决定（`ensure_ascii=False` 把中文直排进
stdout）。子进程 UTF-8（agent 沙箱常设 `PYTHONIOENCODING=utf-8` 且无 `PYTHONUTF8`）
× 父进程 cp936 → 读线程在 `subprocess._readerthread` 死亡 → `stdout=None` →
`json.loads(None)` TypeError。本机复现矩阵证实：仅 `PYTHONIOENCODING=utf-8` 必红；
`PYTHONUTF8=1` 两侧都 UTF-8 则绿（**它掩蔽而非修复**）——不同 agent 沙箱 env/代码页/
Python 版本（3.15 起 PEP 686 默认 UTF-8）各异，环境解不可能通用。

**修（契约从环境移进代码，三层）**：
1. **`--json` 机器通道改 `ensure_ascii=True`**（`rl/gate_check.py`）——纯 ASCII 字节对
   任何解码器免疫（含我们控制的裸 text=True 父进程与不控制的第三方 agent）；中文经
   `\uXXXX` 传输，`json.loads` 还原无损。人类可读走非 `--json` 分支。
2. **子侧入口钉死**：`platform_utils.force_utf8_stdio()`（运行时 `reconfigure`
   stdout/stderr 为 UTF-8，实测压过强设的 `PYTHONIOENCODING=gbk`），`gate_check.main`
   与 `run_rl.main` 接入——被测 CLI 的字节流恒 UTF-8，与环境解耦。
3. **父侧测试统一出口**：`tests/subproc_util.run_utf8()`（强制 `encoding="utf-8"` +
   `stdout is None` 就地断言），扫掉全部 7 处裸 `text=True`（test_gate_check ×2、
   test_run_rl、test_upgrade ×2——后两处 spawn 的 **bun 管道恒 UTF-8**，本就是同型
   雷点、test_no_torch_on_import ×2）。

**验证**：四场景矩阵（无强制/仅 PYTHONIOENCODING/PYTHONUTF8/沙箱原样）全绿；对抗
探针（子进程强设 GBK env）下 `--json` 输出纯 ASCII、verdict/report 无损；nn-python-gate
全绿。生产侧同模式捕获点（`dist_common` ×2 / `run_rl` 的 git 调用 = ASCII 输出、
`bootstrap` 已 `errors="replace"`）不急；新 subprocess 测试一律用 `run_utf8`。

---

---

## §2 metrics v5（`clearTick`）+ outcome 虚拟符号：修复加列只改了一半（2026-09-12）

**背景**：c6-bonus 修「清场后 BONUS TIME 窗口被 max_ticks 截断 ⇒ 歼灭局吃 `terminal.timeout=−2`」需要两个新能力：指标列 `clearTick`（idx30，哨兵 −1）与公式可访问 outcome（虚拟符号 `is_timeout` 等，不占列）。设计侧验证通过：`wClear=4.0` + `wBonusTicks=600` 让「清场+超时」与 `stage_clear` 总回报相等（实测两边均 −14.0）。

- **P0（已修）**：`tools/sim/export-rl-rollout.ts` 只改 `metricsRow()` 的行、`METRICS_DIM` 仍为 30 ⇒ `writeRlShard` 的 `metrics.set(row, i*30)` 在**每局终局行**越界 `RangeError`，整条 RL 采集腿零产出；`tsc`/TS 测试全看不见（`number[]` 无长度类型、无行宽断言）。修：常量改 31 + 行构造提为 `export function buildMetricsRow` + 新增 `tests/export-rl-rollout-metrics.test.ts`（行宽 / 两种哨兵 / 与 Python `METRICS` 条目数跨语言对账）。e2e 复核：1 局 → `metrics.npy (41,31)`、`metrics_version=5`、未清场列 −1。
- **Python 门（已修）**：① `reward_validation.DEFAULT_RANGES` 缺 `clearTick` ⇒ `validate_reward(course)` 抛未捕获 `KeyError`；现改为带列名的 `FormulaError`（由 `validate_reward` 归入 errors），并新增 `test_all_metrics_have_envelope_range` 锁「加列必须登记域」。② `symbolic_envelope` 对引用虚拟符号的加性项不带 outcome 调 `phi` ⇒ FormulaError 被当成数值爆炸记 `inf/超限`（假临界）；现按**每个真实 outcome** 各求一遍取峰值（比「全 0 虚拟」忠实），角点回映加取模。③ `tests/golden/v7_phi_ts_oracle.json` 仍是 30 列 ⇒ 重生成；**`phi` 逐位不变**（v7 不读 clearTick），纯宽度同步，非重新标定。④ `test_item_metrics_layout_locked` 尾部清单补 `clearTick`(idx30)。
- **门禁**：nn-python-gate（ruff + mypy 144 文件 + pytest 全量）**绿**；`bun run check` **2022 pass / 3 skip / 0 fail**；`validate_reward(c6-bonus)` = ok、errors/warnings 均空，`wClear` 项 max_abs=4.0。
- **reward golden 补真实覆盖（已做）**：原先 60 个 case 的 `clearTick` 全是 `0.0` ⇒ `wClear` 是常数项、diff 恒 0，补偿/豁免**零覆盖**；且 `0.0` 是合法值（「第 0 tick 已清场」）而非「未清场」。改：① `_v7_corpus` 显式写哨兵 `-1.0`（对不读该列的 5 门课 reward **逐位无影响**，已验证 60/60 不变）；② 新增 `_clear_metrics()` + c6-bonus 专属单调行序列（tick 0→3000、清场于 tick=600 ⇒ 封顶上界 1200）共 4 case（cleared × {timeout, stage_clear}、uncleared × {timeout, lives_exhausted}）；③ 差分验证：`timeout/cleared` 去掉 `wClear` 项 Δ=**+4.0**、去掉 tick 豁免 Δ=**+18.0**，其余三种 Δ=0 ⇒ 两个新机制都真正参与；④ `cleared-timeout` 总额 == `stage_clear` 总额（均 −10.0），设计意图入 golden。golden 60→64 case，原有 60 个 reward 逐位不变。
- **c6-bonus 起点/教师在新口径下重测（已做）**：新 `win = stage_clear ∪ cleared` 抬高同一权重的读数，而 `gate_check.py` 直接吃 `teacher.wins/games` + eval 行 win_rate ⇒ 两个基准必须同口径重测，否则"学生被抬高、基准仍旧"会白过门。实测（`eval-course-ckpt.ts --course tmp/c6-pickup-strict.jsonc --seed0 860001 --games 100`；旧记录的 kills 3.54 / phits 0.57 / zero_kill_frac 0.14 **逐位复现** ⇒ 只口径变）：bc `c6-pickup.it35` 31/100→**32/100**（`baseline_win_rate 0.31→0.32`）、教师 42/100→**43/100**（`teacher.wins 42→43`）、教师 `timeout_frac 0.03→0.02`（剔除"已清场被截断"，同 `evalboard/stats.ts`）。G1 有效门槛随之 0.36→**0.37**。逐局证据 `tmp/c6bonus-{teacher,bc}.jsonl`。同时订正 c6-bonus 头注释 4 处与磁盘不符的说法（"只改 2 处 / gates 全锁死"、"gates 原样继承"、"报数并列 win/cleared 两口径"、params 名 `wTickFree`→`wBonusTicks`）。
- **仍欠**：① metrics v5 + `win` 口径变更的 `DECISIONS.md` 条目（含 golden 覆盖扩充与 `_v7_corpus` 哨兵改写的理由）。② **其余课程的基准仍是旧口径**：`c6-pickup2`/`c6-pickup3`（0.31 / 42）、`c4-dodge`（0.72 / 68）、`c6b-margin`（0.22 / 42）——凡还有在跑的腿，需按各自 stage 重测；`gate_check.py:293-298` 的 timeout_frac 回退分支仍按**原始 outcome** 算（与 TS 侧新口径不一致，旧行才走到该分支）。

---

---

## §1 外围组件巡检：goal 热图静默常量（生产档目标策略失效）+ eval 墙损失测 + 我引入的 payload 回归（2026-09-10）

用户指令："检查一下其它组件（sampler-agent, cloudflared, src/nn, export-rl-rollout,
export-eval-game, ...）有没有 bug 或者可以优化的空间"。方法：3 个只读子代理并行 + **逐条回验**
（子代理给的路径与行号一律自核——本轮 5 条外部结论里 3 条是假报，见 §1.5）。改动前先做决定性复现。

### 1.1 ★`goalForward` 目标热图在生产架构下恒为常量（HIGH）

**病根**：wasm 卷积段跑完只回拷 `pooled`（256 B），`offBufA` 写进 wasm 线性内存后**从不搬回 JS**。

    conv-wasm.ts   feats(..., offBufA, offBufB, offBufC, offPooled)
                   pooled.set(f32At(offPooled).subarray(0, pooled.length))  ← 只有这一行回拷
    infer.ts       goalHeatmap[p] += w * this.bufA[...]                    ← bufA 只在非 wasm 分支被填

**决定性复现**（合成 h16/d2 与 h64/d8 合法权重，同一份代码）：

| 架构 | 主干路径 | 换输入后热图 max&#124;Δ&#124; | 热图不同取值 |
|---|---|---|---|
| h16/d2 | TS 手写循环 | 3.889e+2 | 676/676 |
| h64/d8 | **conv-wasm** | **0.000e+0** | **1/676（常量）** |

用真实生产 golden（`goal_net.py --golden --h 64 --d 8`，本次新增 fixture `goal-golden-wasm.json`）：
热图对 py 期望 max&#124;Δ&#124; = **1.245e+1**，而 engage 头 7.6e-6 正常。

**影响面**（生产必踩——`goal_net.py` 默认 `--h 64 --d 8`）：`export-eval-game.ts`（policy=goal 的
评估）、`export-goal-rollout.ts`、`export-counterfactual-goals.ts`、`src/nn/goal-executor.ts`。

**为何一直没被发现（结构性教训）**：TS 侧有**两套等价卷积实现**（TS 手写 / wasm），而 golden 是
**按档位分工**覆盖的——`goal-golden.json` 是 `h=16/d=2` 瘦身档（注释自陈"主干不触发 wasm"），
`student-golden-wasm.json` 是 `h=64/d=8` 生产档但**只覆盖 student 三头**（消费 pooled，回拷正常）。
⇒ 测试跑 TS 路径、生产跑 wasm 路径，两条永不相交，wasm 专属缺陷零覆盖。**新增头/新架构时，
必须同时补一条生产档 golden，别只补瘦身档。**

**修法**：`run()` 补 `bufA.set(f32At(offBufA).subarray(0, bufA.length))`；`runStudentConvWasm` 以
`bufA.length !== H*SP` 作为"架构不符"守卫（不符即退 TS 原路径，正确性优先）。回拷成本实测
**2.9 µs/次 ≈ features(6.9 ms) 的 0.04%** ⇒ 无条件拷贝，换掉"某个头悄悄读陈旧空间特征"这类静默 bug。

**回归**：新增 `tests/fixtures/goal-golden-wasm.json`（h=64/d=8，422 KB，同 student-wasm 量级）+
`tests/nn/goal-infer.test.ts` 一组 5 例：三头对 py golden（热图 ≤1e-3，修复后实测 1.1e-5，修复前
1.245e+1）+ **"热图随 obs 变化"**（通道 0/1 对调；修复前恒 0、676 格只剩 1 个取值）。

### 1.2 eval `baseWallIntact` 恒等于 `baseWallTotal`，baseIntegrity 被钉死在 1.0（MED）

`export-eval-game.ts` 只在 telemetry 初始化时赋值一次，循环内从不更新（对照：`export-rl-rollout`
每决策步重算、`export-observations` 每 tick 重算）。下游 `godai-score.ts`：

    baseIntegrity = 0.55 + 0.45·clamp01(baseWallIntact / baseWallTotal)   // 分子恒等分母 ⇒ 恒 1.0

**实锤（两份独立证据）**：

- **在野扫描**：`tmp/**/_eval_report.json` **173 份，100% `intact == total`**（含 100 个 gameover、
  61 个 stage_clear）——真实对局里墙被打掉却从不记录。
- **A/B（修复后重跑同一 stage/seed）**：s5/seed1 随机策略 → 终局 `5/8`（修复前必为 `8/8`）；
  s5/seed5 基地存活、墙被打到 `1/8` ⇒ `baseIntegrity = 0.606`（**修复前 1.0**）——即注释里
  "墙被打秃但基地还活着"这个领先指标此前完全丢失。

**修法**：终局（循环退出后、构造 `scorable` 前）取一次 `tel.baseWallIntact = countBaseWall(world)`。

### 1.3 我上一提交（`d183997`）引入的回归：陈旧 pending job 永不下架（MED，我的责任面）

`tools/training/hub.ts` 的 `drainStaleJobs()` 仍硬编码 `unlinkSync('payload.zip')`，而同一提交把
payload 容器改名 `payload.tar.xz`（`protocol.PAYLOAD_NAME`）⇒ `unlinkSync` 恒抛 → 被
`catch { /* already gone */ }` 吞 → `n` 恒 0 → **账本里所有陈旧 pending job 保持可领**
（`hub_server.claimable_job_ids` 以 `find_payload` 存在性判定），真 worker 会白烧 GPU 租约去跑
已死运行的局——恰是该函数存在的唯一理由。

**修法**：改为按前缀扫描 job 目录删 `payload.*`（而非硬编码全名），从根上免疫再次改名；`hub.ts`
该处注释同步更正。**回归**：新增 `tests/training-drain-stale.test.ts` 5 例（新名/旧名/两名并存
都被下架；非 payload 文件与已 completed job 不动；坏账本、缺目录不抛）。

### 1.4 `cacheHits` 少计（LOW）

`tools/agent/sampler-agent.ts` 的 `/v1/result` 轮询端命中 `resultCache` 时未累加 `cacheHits`
（提交端有）。trainer 轮询是主要取包路径 ⇒ 缓存命中率被系统性低估。已补。注：该字段目前**只产出、
仓内无消费方**（经 `/v1/status` 暴露），影响限于对外状态接口的口径正确性。

### 1.5 被证伪的假报（记录以免重走）

| 假报 | 证伪 |
|---|---|
| 子代理给的路径 `tools/dist/sampler-agent.ts` | **文件不存在**（只有 `tools/agent/`）——其行号与结论整体不可信 |
| "异步提交路径缺 resultCache 短路" | 两条路径都有（提交端 / 轮询端各一处） |
| "export-rl-rollout 每局重建模型，是可优化项" | `runOne` 确实逐局 `buildModelFromText`，但 `--pack`（生产路径）强制**一进程一局** ⇒ 只跑一次；实测 1.228 ms/次（90% 为 base64→f32），不值得改 |
| "`npy.ts` 解析校验不严" | `npy.ts` 只有 `writeNpy/writeShard`，**无 reader** |
| "cloudflared 日志泄漏" | `tmp/cloudflared-*.log` 归 `tools/tmp-clean.py`（`.log` 在后缀表内，保留 N 天） |

### 1.6 巡检确认为健康（勿动）

- `stepCloudflared`：隧道复用判定走**本地 cloudflared `/ready`**（而非穿隧道 ping）——避免 hub
  出网劣化造成假阴性，是对的；3 次重试 + edge 20 s 确认 + "失败保留基础设施"均为刻意设计。
- `src/nn/obs-encoder.ts`：`obs`/`scalars` 预分配 + `fill(0)`，无每 tick 分配。
- `conv-wasm.ts`：权重上传用 `uploaded !== stemW` 实例指纹守卫，越界有显式 `check()` 抛错；
  wasm 加载/运行异常**有** `console.error`——§1.1 之所以静默，是因为它发生在 wasm **成功**时。

### 1.7 门禁

`bun run check` 绿（tsc + check-decisions + `bun test --parallel`）；oxlint **0 error**（20 条既有
warning，非本次引入）；oxfmt 对本次改动的 6 个源码/测试文件 clean。

### 1.8 连带影响：`docs/goal-nn.progress.md` §3 的 goal 结论需重新解读

§1.1 的缺陷**必须在历史结论里对账**。`docs/goal-nn.progress.md` §1「性能实测」自证 goal 前向
探针跑的是 **h=64/d=8** ⇒ T9a 金丝雀那批 goal 策略实验**全部在 wasm 路径上**，即热图恒为常量、
argmax 恒选同一格。

- **`goal 0.05%`（canary ②）**：除已记录的 executor 短板，还叠加本缺陷 —— "目标选择轴从未生效"。
  该节"执行层短板与**目标选择**、学习无关"的归因**需修正一半**。
- **`goal-god 0.0%`**：零网络，**不受本缺陷影响** ⇒ "执行层无生存能力"这一结论**仍成立**。

已在 `docs/goal-nn.progress.md` §3 追加该注记。**卡 A0 的 goal 重测必须在修复后的代码上做**，
否则重测仍在测常量热图（原计划的重测因此不作数）。

---

---

<!-- OLD-NUMBER-MAP: 自动生成，勿手改 -->

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §22→§1 · 旧 §27→§2 · 旧 §30→§3 · 旧 §31→§4 · 旧 §33→§5 · 旧 §40→§6 · 旧 §42→§7 · 旧 §53→§8 · 旧 §57→§9 · 旧 §65→§10 · 旧 §93→§11 · 旧 §96→§12 · 旧 §97→§13 · 旧 §98→§14 · 旧 §102→§15 · 旧 §103→§16 · 旧 §106→§17 · 旧 §121→§18 · 旧 §128→§19
