# Plan: offline-deliver-isolation — 补传腿「忙等修复 + 独立进程」

> **交付物**：本 plan。**本文件不含代码改动**；实现交给后续 agent。
> **路径基准**：除注明外，相对 `nn-training/`（例：`remote/offline_deliver.py` = `nn-training/remote/offline_deliver.py`）。
> **状态**：实施中（2026-10-06 立案）。**2026-10-06 评审稿已处置**（`plan/isolation.review-hy.md`）
> ——逐条结论见 **§9**；**2026-10-07 二次评审（代码级复核）已处置 → §10**
> （正文标 **★二评** 的段落是改动处；**§10 的六条是实现前必须落进的规范**）。
> **来源（诊断已完成，本文不重证）**：`plan/tpu-cpu-silent-downgrade.plan.md` §9（忙等的代码级证明）、
> §10（慢轮 `[ppo] XLA 步耗诊断` 定案：**墙钟 ≈ 40× 于 编译+执行+追踪+拷贝 之和** ⇒ 训练线程被抢走 CPU/GIL）。
> **一句话**：① 修掉 `_drain_loop` 在 `_repost` 上的**忙等**（事故的直接 bug）；② 把整条补传腿搬进
> **独立子进程**，让「网络腿与 PPO 抢 GIL/核」这条通道**永久不存在**。

---

## 0. 目标、证据与诚实账

**已定案的现场**（详见来源 plan §10）：云机 `x21-psh-b`，530 那一刻起 PPO 单步 `0.186 → 5.26 → 7.40 s/step`；
慢轮诊断行给 `最慢窗口=16 步/124.23s`，而 编译 0.00 + 追踪 2.718 + h2d 0.004 + d2h 0.342 ≈ **3.06s**
（**≈40× 差额**）⇒ **设备没换、也没重编译；是训练线程被抢走 CPU/GIL，隧道恢复即自愈**。

**两件事为什么都要做（不是二选一）**：

* **P0 忙等**：`_repost` 是**唯一一个「只唤醒、不消费」的标志** ⇒ 探活失败把它放回去后，外层循环
  **既不 `wait` 也不做事**，退化成纯转圈。触发条件（**探活失败 + 待重投**）与「530 起、隧道好停」逐条吻合。
  **这是确认的 bug。**
* **P1 独立进程**：即使元凶不是它（诊断里还剩 H6a 进程内忙等 / H6b `cloudflared` 自旋 两种可能，
  见来源 plan §10.3），**同进程**这条通道本身就永远是风险面：`_eval_rows_for()` 每投一次要全量解析
  33MB 的 `eval_log.jsonl`、`encode_weights_json`/`encode_opt_tar` 要 gzip+base64 ~1.2MB。搬走 = 永久关闭。

**★评审（A1）诚实账 —— 把两件事分开写**：

* **P0 红转绿 = P0 的完成判据**（它证明那个必然烧核的忙等被拿掉了）；
* **≠ 40× 的结案判据**。纯 Python 忙等的 GIL 抢占粗算只有 ~2×，而实测 40×；**一个线程最多烧掉一个核**
  ⇒ P0 修完，**40× 大概率还在**（若元凶是 H6b）。
* **40× 的闭环靠埋点，而埋点是 P0 的一部分**（§2.4 / §9-A3）：每轮回传「**进程 CPU 秒 + drain 圈数**」
  ⇒ 本进程吃满 = H6a；本进程正常而同机被吃满 = H6b。

**非目标**：不改 hub 协议 / hub 端；不改 PPO 数值路径；不动 `--device`；**不引入新依赖（只用标准库）**；
不做「专门重跑一次云机来验收」（TPU 排队十几小时，用户 2026-10-06 明示不可行）。

---

## 1. 改完必须仍成立的不变量

1. **训练永不因网络停摆**：补传的任何异常都不得外泄、不得阻塞训练线程。
2. **`delivered.json` 是唯一记账**，重启续投靠它（`pending()` = 「磁盘上有、记账里没有」）。
3. **幂等 + 首写锁定**语义不变（重复投递走 hub 的幂等分支）。
4. **单写者**：`delivered.json` 只有一个写者（P1 之后 = 子进程；父侧**不得**再直接写）。
5. **段末有界收线**：`close(timeout)` 超时就放手（产物已在本地）。
6. **不得静默失败**（项目硬规矩）：子进程非零退出、起不来、控制文件坏、**段末摘要没送到**，
   父侧都要**响亮记一行**。
7. **子进程必须可按需重启**（★评审 C1）：任何「子进程已死」都要被发现并补救，不允许
   「往无人读的文件里写」。
8. **控制文件按会话唯一**（★评审 C3）：不得跨会话重放上一段的 `stop`。
9. **不新增常驻子进程**：子进程要有 idle-timeout 兜底，且线程模式可降级（Kaggle 对常驻子进程有取消史）。

---

## 2. P0 — 修忙等（先做，可单独交付）

### 2.1 缺陷坐标

`remote/offline_deliver.py`

* `_drain_loop()`（`:260-294`）：内层 `while not (… or self._repost)` **认** `_repost` 当唤醒源，
  但 `if want_sync or final is not None:` **不认**它 ⇒ `_repost` 非空而另两者为假时**纯转圈**。
* `_sync()`（`:502-524`）：`repost = sorted(self._repost); self._repost.clear()`，探活失败时
  `self._repost.update(repost)` **放回去** ⇒ 正好把 `_drain_loop` 推进上述状态。

> 关键：`_repost` 只在 `want_sync or final` 为真时才会被 `_push_backlog` → `_sync` 消费；
> 一旦它**单独**为真，就没有任何路径能消费它 ⇒ 自锁式忙等。

### 2.2 修法（把 `_repost` 从「唤醒条件」降为「工作项」）

1. **`_repost` 变持久集**（不再「先取走、失败再放回」）：
   - `_sync()` 里 `todo = self.pending() + [it for it in sorted(self._repost) if it not in todo]`，
     **不 clear**；
   - **★评审 B3 discard 条件写死**：**只有真正被 `_post_artifact(it)` 处理过、且返回 `ok`/`skip` 的 `it`
     才能 `discard`**；`todo[:sync_cap]` 截断在外的、以及返回 `stop`（链路问题）的，**一律留着**。
     （`§2.2-1` 原措辞「这一轮处理完时 discard」太松，会被实现成整批丢弃。）
   - **★评审 B4 接受 repost 饥饿**：`todo = pending() + repost` 且 `sync_cap=4` ⇒ backlog ≥4 时 repost
     排不上。这正是 `offline_deliver.py:509-510` 的原意（重投不该挡在真积压前面）；
     **接受饥饿**，并在注释里写明「backlog 总会清空（`_push_backlog` 跑到推空或超预算），届时 repost 自然轮到」。
2. **唤醒条件去掉 `_repost`**：`_drain_loop` 的内层 `wait` 只看 `want_sync` / `final` / `stopping`；
   `_repost` 由同一次唤醒里的 `_push_backlog` 处理（`submit_eval_round()` 本来就同时置了
   `_want_sync = True`，所以重投立刻会被试一次）。
   * **★评审 B1 重试节奏必须写进注释**：去掉 `_repost` 唤醒后，真实行为是「**repost 的重试完全依赖
     下一次唤醒**（每轮 `submit_round` 必唤醒一次）」——这**不是**「自动重试」。
     这是**有意取舍**（与原始日志的「本轮放弃，下轮再试」同语义），不是漏配。
   * 来源 plan §9.3-2 提过备选「探活失败后退化为 `wait(min(DRAIN_TICK, 剩余 PROBE_TTL))`」；
     本 plan **显式不采用**：它会让探活负结果 TTL 内反复醒来重试，收益（几分钟内多试几次）
     不值这份复杂度——**重试节奏交给轮次**。
3. **★评审 B2 `close()` 唤醒条件补 `_repost`**：`offline_deliver.py:256`（★二评勘误：原写 :255）目前是
   `self._want_sync = self._want_sync or bool(self.pending())`，而 `pending()` **跳过已投递项**
   （`:416`）⇒ 段末若只有 repost、backlog 为空 ⇒ 线程直接 `elif stopping: return`，**repost 被丢**。
   改成 `… or bool(self.pending()) or bool(self._repost)`（旧代码同病；既然本 plan 宣称「保住重投语义」，就得补）。
4. **不变量写进注释**：*「每一次唤醒，要么干活，要么 `wait`；禁止出现既不 wait 也不干活的回圈。」*
   并在 `_drain_loop` 里落一个**探针** `self._idle_spins += 1`（只在违反该不变量时触发；§2.3 / §10.4）。
5. **★二评 G3 跨会话兜底**：被丢的重投**必须**能跨会话补——`delivered.json` 加 `owed_reposts`（§10.3）；
   `close()` 收尾后仍有未销项 ⇒ 响亮一行。
6. **★二评 G7-1 毒丸项**：B3 的 discard 只认「`_post_artifact` 处理过且返回 ok/skip」——**抛异常那一支**
   在持久集下永远销不掉（每次唤醒重抛 + 整趟 `_push_backlog` 中止）。补「同一 `it` 连续 3 次异常 ⇒
   记进 `_rejected`（带原因）并销账」。

### 2.3 判据 / 测试（本阶段主闸门）

* **★评审 B5 主判据换成正计数器，而不是 CPU 时间**（CPU 时间阈值本质是「赌机器速度」，
  正是 `tests/test_no_sleep_as_sync.py` 要拦的那类）：
  - 在 `_drain_loop` 外层加 `self._drain_ticks += 1`（一个 int，零成本；**同时就是 §2.4 的埋点**）；
  - **★二评 G4 判据 = 不变量探针 `_idle_spins`，不是 `_drain_ticks`**：修好之后 tick 仍每
    `DRAIN_TICK_SEC=0.5s`（超时唤醒）涨 1 ⇒「采样窗口内不得增长」在**绿**的实现上就是假的。
    `_idle_spins` 只在「这一圈既没干活、也没 `wait`、也没收线」时 +1 ⇒ 修好后**该分支不可达**，
    断言 `== 0` 是**确定性**的（无窗口、无容差、无 sleep）。
  - **红用例**（进 `tests/remote/test_offline_deliver_async.py`）：假 opener 一律回 **530**（隧道断线形态）
    + `submit_eval_round(1)` ⇒ `_wait_until(lambda: d._idle_spins > 0, timeout=0.6)`（红：毫秒级命中）
    → 断言 `_idle_spins == 0`（绿：等满窗口后成立）。当前实现**必红**。`_drain_ticks` 只作埋点。
  - **★二评 G5 顺序（§7 纪律）**：先落计数器/探针（**不改行为**）⇒ 在未修代码上确认红 ⇒
    再改 `_drain_loop`/`_sync`/`close`。
  - CPU 时间**降为副判据**（可选，若用则按 `# timing-ok: 上界兜底` 标注）。
* **绿回归**：探活先失败 N 次、之后成功 ⇒ **重投仍然送达**（证明修忙等没有把 `_repost` 弄丢）。
* 既有 `tests/remote/test_offline_eval_wiring.py::test_deliverer_reposts_a_delivered_round_for_late_eval_rows`
  必须保持绿。

### 2.4 ★评审 A3 — 40× 的闭环埋点（**放进 P0**，约 10 行）

诊断（来源 plan §10.3）已经给出判据，但**这条没有被任何一份实施 plan 接走**。它极便宜：

* **进程 CPU 秒**：用 `os.times()`（**跨平台**；`resource` 是 Unix-only）取 `user+sys` **差值**，随轮次走；
* **`_drain_ticks`**：就是 §2.3 那个计数器（一个 int，零成本）。

**落点（实现时二选一，先说清成本）**：

1. **最便宜**：每轮/段末在 `[deliver]` 日志行里打一次 `cpu=…s ticks=… pending=…`
   —— 这行**本来就会出现在 notebook 输出里**（操作者看得到，本事故的 530 行就是这么拿到的），**不动任何 schema**；
2. **可归档**：把它并进**已有回传行**（`metrics_row` 的 `extra` / `wire` 块）。
   ⚠ 按项目规矩「加 metrics 列 = 全链 lockstep」（元数据见 `MEMORY.md`「奖励机制」段），
   **实现时必须先确认这条链的改动面**，别为了一个诊断字段牵动全链。

**建议**：先做 1（零风险、当天可见），2 作为后续按需。判读：
**本进程 CPU 吃满 ⇒ H6a（进程内）**；**本进程 CPU 正常而同机被吃满 ⇒ H6b（cloudflared 等外部）**。

**★二评 G5 角色澄清**：忙等期间这条腿**什么日志都不打**（既不 `wait` 也不干活），而在飞的包里
**连埋点都不存在**（导出快照，同 C9）⇒ **P0 与 P1 都要重新导出才生效**；云上判 H6a/H6b 的唯一判据是
`os.times()` 的**进程 CPU 秒增量**（与已有 `ppo_sec` 同屏看），`_drain_ticks`/`_idle_spins` 的正确定位是
**本地测试钩子 + 不变量探针**。落点是每轮一条
`[deliver] … cpu=+x.xxs ticks=… idle=… pending=… repost=…`（最便宜那档）。

---

## 3. P1 — 补传腿独立进程

### 3.1 为什么可行（一句话）

补传的状态**全在磁盘上**：`ArtifactsStore` 目录（`weights.json` / `opt` / `metrics.jsonl` / `eval_log.jsonl`）
+ 一个 `delivered.json` 记账；`pending()` 的定义就是「磁盘上有、记账里没有」（`offline_deliver.py:402-420`）
⇒ 子进程**零共享内存**即可接手。训练侧只有四个 fire-and-forget 调用。

### 3.2 形态已定：`subprocess.Popen`，**不用 `multiprocessing`**

| 方式 | 判定 |
|---|---|
| **`subprocess.Popen([sys.executable, "-m", "remote.deliver_worker", …])`** | ✅ **选它**。exec 一个干净解释器：不碰 `__main__`、不碰已初始化的 XLA/TPU 运行时、跨平台一致（Windows dev 也能跑测试） |
| `multiprocessing` + `spawn` | ❌ notebook 里会**重新 import `__main__`**，而 kernel 的 `__main__` 是交互模块，`if __name__ == "__main__":` 守卫也拦不住 ⇒ 这是「notebook 里 multiprocessing 不工作」的常见成因 |
| `multiprocessing` + `fork` | ❌ 会**复制已初始化的 XLA/TPU 运行时**（设备状态损坏风险），并复制整个训练进程的内存 |

**本项目的实证**（不需要论证多进程在云机上可行）：云机同时跑着 94 个 bun 子进程（rollout/eval 长驻池
`tools/sim/serve-any.ts`），notebook 本身也把训练 worker 作为子进程拉起
（`remote/notebook_runtime.py:581` 那条「`PJRT_DEVICE` 必须在 spawn **之前**打上」的注释即为此）。

### 3.3 子进程：新模块 `remote/deliver_worker.py`

* `main(argv) -> int`（`if __name__ == "__main__": sys.exit(main())`，与 `remote/run_loop.py:507` 同形）。
* 参数：`--artifacts`、`--hub-url`、`--run-id`、`--course`、`--ctl`（控制文件路径）、
  `--ctl-offset`（★评审 C15：父侧重启时把「已消费字节数」传下来）、`--idle-timeout`、`--tick-sec`。
* **token 只从环境变量 `BATTLE_HUB_TOKEN` 读**（★评审 C14：仓库既有名字，见 `remote/run_loop.py:346-361`），
  **绝不进 argv**。
* **★评审 C8 boot 握手**：import 成功、循环就绪后往 stdout 打一行 `[deliver] boot ok <pid>`；
  父侧靠它判「真的活着」（`Popen` 成功 ≠ 子进程活着 ≠ 能 import）。
* 主循环（每 tick）：
  1. **增量消费控制文件**（见 §3.5 的偏移规则）→ 应用 `round`(仅唤醒) / `repost` / `final` / `stop`；
  2. `OfflineDeliverer(background=False, …)` 的 `sync()`（同步版；内部即 §2.2 修好的语义）；
  3. **★评审 C12 `final` 必须排在 `stop` 之前尝试一次**（对齐现线程语义 `offline_deliver.py:283-294`：
     `if final is not None: deliver_result(...)` 在 `elif stopping: return` **之前**）——写成代码注释 + 断言；
  4. **★评审 C10 每 tick 给预算**：一次 tick 最多推 N 轮或 T 秒（如「8 轮 / 20s，先到为止」），
     把追赶摊到几个 tick。**理由**：隧道一恢复就连发几十个 ~1.9MB POST、gzip+base64 全压在云机那
     2–4 核上（`platform_utils.cpu_worker_slots`：「≤4 核全给」的正是这种机器）⇒ 会反过来拖训练。
     ⚠ 该预算**不得**让段末 `close()` 的 flush 推不完：`close` 期间放宽到「直到 stop 预算耗尽」。
  5. `stop` ⇒ 有界 flush 到预算耗尽后**退出 0**；
  6. **idle-timeout**（★评审 C1，见下）到期 ⇒ 退出 0。
* **★评审 C16 父进程消失的检测**：子进程用**阻塞读线程**读 `stdin`（`sys.stdin.read()` 直到 EOF 后置位），
  **不用 `selectors`**（Windows 的 `selectors` 只认 socket）。父侧 `close()` 收尾时关闭该管道。
* 退出码：0 = 干净；非 0 = 异常（父侧负责响亮记录）。

**★评审 C1 idle-timeout 的三条收紧（缺一不可）**

1. **触发条件是合取**：`无新控制行 ≥ idle-timeout` **且** `pending()` 为空 **且** 无未送达 repost。
   （健康段里 `pending()` 在轮间通常是空的，**只有第一个条件才能保证它不在段中触发**——
   实测轮间隔 30–700s。三条合起来，正常段永不触发。）
2. **默认值 ≥ 1800s**（= 最长轮 700s 的 2.5 倍以上）。
3. **定位降级**：idle-timeout 只该解决「父进程没了」，而 stdin-EOF 已经覆盖了那件事 ⇒
   它应当**退化成纯兜底**，**不承担生命周期管理**。

### 3.4 父侧：`DelivererProcess`（与 `OfflineDeliverer` **同接口**）

**★二评裁定：放 `remote/offline_deliver.py`**（不新增依赖账本条目、`DRAIN_FLUSH_SEC`/日志约定就近；
文件已 823 物理行，改完先量 `tests/test_python_loc_budget.py` 的「<1000 代码行」预算）。
子进程那个**新模块**才是 `remote/deliver_worker.py`，它必须按 §10.1 入 `remote_dag.LAYERS`（L2）。

**★评审 C7 类型面必须落地（否则 gate 必红）**：`plan_handoff.py:171` 与 `:311` 现在写死
`OfflineDeliverer | None`；`nn-python-gate.sh` 跑 mypy。⇒ 加一个 `Protocol`：

```
DelivererLike = Protocol(start, submit_round, submit_eval_round, submit_final, close, status, pending)
```

`RunContext.deliverer` 与 `open_run_context(deliverer=…)` 都改成它 —— §3.4 的「同接口」必须**在类型上**成立。

| 方法 | 语义 |
|---|---|
| `start()` | 建会话级控制文件 + `Popen`；stdout/stderr 用后台线程转发并按行加 `[deliver]` 前缀；等 `boot ok`（≤5s） |
| `submit_round(it)` | 追加 `{"round": it}`（非阻塞、永不抛） |
| `submit_eval_round(it)` | 追加 `{"repost": it}` |
| `submit_final(**)` | 追加 `{"final": {…}}` |
| `close(timeout)` | 追加 `{"stop": true, "budget": <秒>}` → `wait(timeout + grace)` → 超时 `terminate()` → 再超 `kill()`；**★评审 D3 并 `join` 两个转发线程**；**★评审 C12/C1 收尾核对**：`poll()` 非 0、或 `final` 已提交而 `result_done` 仍 False ⇒ **响亮记一行** |
| `status()` | 从磁盘读 delivered/pending + 子进程**状态文件**（★二评 G2：`disabled_reason`/`result_done`/`reposts_unsent`，规格见 §10.2——进程内 `status()` 早有这几个键，`:758-770`）+ **★评审 C1 `restarts`**；`drain_alive = proc.poll() is None`（**不是** `Thread.is_alive()`）；`background = True`；`mode ∈ {process, thread}` |
| `pending()` | 从 `delivered.json` + artifacts 目录算（保持 `plan_handoff` 的日志字段可用） |

**★评审 C1 按需重启（respawn）**：`submit_*` 与 `close()` 先判 `self._proc.poll() is not None` ⇒
**重启并响亮记一行** `[deliver] 子进程已退出（code=N），按需重启（第 k 次）`；重启计数进 `status()`。
**没有这一条，P1 会把「补传偶尔不工作」变成「补传整段不工作且没人知道」。**

**★评审 C1 的替代方案（已评估并否决）**：干脆**去掉 idle-timeout**、只靠 stdin EOF。
否决理由：EOF 只在「父进程死亡/正常关闭管道」时触发，**挡不住子进程自身异常退出**（OOM/崩溃），
而 C1 的致命点恰恰是「子进程死了没人知道」⇒ respawn 是安全网，idle-timeout 是第二层兜底，两者互补。

### 3.5 控制通道（格式先定死再写码）

* **★评审 C3 文件名带会话身份**：`work_dir` **跨会话复用**——它缺省就是**artifacts 目录下的 `work/`**
  （`run_loop.py:186`：`wd = Path(work_dir) if work_dir is not None else root / "work"`，`root` = `--artifacts`），
  而这个 run 目录在 Colab 上是**同一门课反复续跑的那一个**（离线训练会「发现已有 run 目录就从最后
  checkpoint 继续」）⇒ 固定文件名会让新会话的子进程一启动就吃到**上一段末尾那条 `{"stop": true}`**、
  立刻退出 0（整段不投递，且比 C1 更难查）。
  ⇒ 用 `deliver-ctl-<run_id>-<start_ts>.jsonl`，`start()` 时**新建**。
  **不要**用「启动时 truncate」——可能与还活着的上一个子进程竞争读。
  * ⚠ 落点既然在 artifacts 目录**之内**（`<artifacts>/work/`），实现时要**确认产物 zip 的打包范围**
    不含 `work/`（否则 ctl 文件会混进交付物）；必要时把 ctl 挪到 artifacts 目录**之外**
    （如 `run_loop` 的 `--work-dir` 显式指向别处），或给它点号前缀。
* 文件格式 = **append-only JSONL，一行一条**：
  `{"round": 215}`（★评审 C2，仅唤醒；子进程本来就会重扫磁盘） ·
  `{"repost": 215}` ·
  `{"final": {"it_end": …, "state": …, "summary": {…}, "end_it_reached": …}}` ·
  `{"stop": true, "budget": 90.0}`（★评审 C13）
* **★评审 C13 `stop` 传「预算时长」而不是 epoch 秒**：子进程收到后用 `time.monotonic()` 自算
  deadline ⇒ 不受 NTP 跳变影响（同机 monotonic 同源）。
* **★评审 C4 只推进到最后一个 `\n`**：绝不能「坏行/半行跳过并继续推进偏移」——只要偏移越过了那半行，
  下 tick 读到的是**剩下的半个 JSON**，**永远拼不回去** ⇒ 段末摘要永久丢失。
  且 `{"final": {...}}` **完全可能 >4KB**（POSIX 单次 `write` 的原子边界约 4KB；
  `OFFLINE_RESULT_BODY_MAX` 管的是**线上体**，管不住 ctl 这一行）。⇒
  **残余字节留缓冲区等下 tick 拼；只有解析成功的整行才推进 offset。**
  （父进程若在写一半时死掉，EOF 处那半行直接丢弃。）
* **★评审 C15 重启不重放**：父侧记住自己已写入的字节数，重启子进程时传 `--ctl-offset=N`
  —— 否则重启后 offset 归零，会重放本会话所有 `repost`（**每一个都是一次真 POST**，200 轮 = 200 次重投）。

### 3.6 接通（`remote/plan_handoff.py::open_run_context`）

① `make_deliverer`（`offline_deliver.py:790`）加 `mode` 参数：`"process" | "thread" | "sync"`；
   离线路径**缺省 `"process"`**，测试/单步调试可显式给另外两个。
   **★二评 G7-4 解析顺序 = `mode 参数 > $NN_DELIVER_MODE > "process"`**；未知值 ⇒ 响亮一行 + 退线程模式；
   `nn-training/tests/conftest.py::pin_production_env` 钉 `NN_DELIVER_MODE=thread`
   （**单测默认不起真子进程**；真子进程只在 `tests/remote/test_offline_deliver_proc.py` 里显式给 `"process"`）。
② 需要把 **`work_dir`** 传进去（控制文件落点）——`open_run_context` 两者都有。
③ **★评审 C14 token 传法**：复用既有环境变量名 **`BATTLE_HUB_TOKEN`**（`remote/run_loop.py:346-361`），
   **别造新名字**；取值顺序是 `inline > token_file > env` ⇒ 父侧若拿到的是 inline/文件 token，
   **必须显式塞进子进程 env** 才传得下去。argv 里**不得**出现 token（`/proc/<pid>/cmdline` 全机可读）。
④ `deliver=False`（`--no-deliver`，`remote/run_loop.py:409/494`）时仍返回 `None`。
⑤ **★评审 C8/C9 起不来是常态，不是异常**：
   - `Popen` 必须**显式给 `cwd`**（nn-training 根）与 `env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)`
     —— `sys.path` 可能被离线包解压流程改过，不继承；
   - **判据**：等 `boot ok` ≤5s；超时或非 0 退出 ⇒ **降级线程模式 + 响亮一行**；
   - **降级必须 sticky**（不要每轮重试 `Popen`）；
   - 环境变量（如 `NN_DELIVER_MODE=thread`）也作为显式降级开关。
⑥ `start()` 之后那行 `产物补传已启用：…` 日志（`plan_handoff.py:375-382`）字段保持不变
   （`hub_url` / `delivered` / `pending` / `background`），另加 `mode=`。

### 3.7 云机上的真实约束（写进实现注释）

1. **核很少**：`cpu_worker_slots` 的判据是「`cores ≤ 4` ⇒ 全给」⇒ 云机常是 2–4 vCPU。
   子进程必须**空闲等待**、绝不烧核。（这正是本次事故的教训：一个忙等核在 2 核机上就是灾难。）
2. **Kaggle 对常驻子进程敏感**（本项目已踩过 `Canceled by backend, Exit code: 137`）⇒ §3.3 的 idle-timeout。
3. **子进程日志要带前缀与时间戳**，否则与训练日志交错后不可读（`common/logutil.py` 的行格式）。
4. **★评审 C9 在飞的包根本没有新模块 ⇒ 降级是上线后的常态**：`code.zip` 是**导出那一刻的代码快照**
   ⇒ 任何**改前导出、还在跑**的任务包里都没有 `remote/deliver_worker.py`，
   子进程**必然起不来**。⇒ 对那些腿，本 plan 的实际效果 = **保持今天的行为**（线程模式），
   **这本身是安全的**；process 模式只对**重新导出**的包生效。
   **不要**把「起不来」当异常告警刷屏——它是预期形态（C8 的 sticky 降级正是为此）。

---

## 4. 测试 / DoD

**★评审 D4 分两层**

* **快层（假对象，秒级）**：`tests/remote/test_offline_deliver_async.py`
  —— §2.3 的忙等红用例（判据 = `_idle_spins`，★二评 G4）+ 「探活失败后恢复仍重投」+ 「段末只有 repost 也不得丢」。
* **慢层（真子进程，单独命名，便于只跑它）**：`tests/remote/test_offline_deliver_proc.py`（新）
  —— 子进程里没法注入 opener ⇒ 用真 HTTP：`http.server.ThreadingHTTPServer` + **端口 0**
    （★评审 D2 拿到真实端口再传给子进程；`test_remote_ppo.py::_boot_server` 是带 hub 语义的重型夹具，
    能复用则复用，不能就上面这个）。断言的八条：
  1. `submit_*` **非阻塞**（入队耗时与网络无关）；
  2. 子进程把积压**全部推完**（`delivered.json` 记满、pending 清零）；
  3. `close(timeout)` **有界**（hub 卡住时按预算收线）；
  4. `stop` 后子进程**退出码 0**；
  5. **token 不在 argv**里（断言 argv 不含 token，而请求确实带鉴权头 ⇒ 证明它从 env 读的）；
  6. 子进程**非零退出**时父侧留下日志；
  7. **控制文件增量消费**：分两次写半个 `final` ⇒ 最终仍被投递（★评审 C4 的用例）；
  8. **重启不重放**：杀掉子进程后 `submit_round` 触发 respawn ⇒ 只处理**新增**的控制行（★评审 C15）。
* **★评审 D1**：真子进程用例必须显式给 `cwd=<nn-training 根>` + `env["PYTHONPATH"]`，否则 CI import 失败。
* **★评审 D3**：`close()` 收尾要 `join` stdout/stderr 转发线程，否则残留线程污染后续用例。

**既有用例全绿**：`test_offline_deliver.py` · `test_offline_deliver_async.py` ·
`test_offline_deliverable_split.py` · `test_offline_eval_wiring.py`。

**DoD**

- [ ] `bash tools/githook/nn-python-gate.sh` 绿（ruff + mypy 全量 + pytest 全量）。
- [ ] `tests/test_no_sleep_as_sync.py` 绿（新用例的 `sleep` 必带 `# sleep-ok:`；上界墙钟断言必带 `# timing-ok:`）。
- [ ] **★评审 D5 顺序契约**：新增断言钉住 `plan_run.py` 的 **`deliver_final` 在 `close_delivery` 之前**
      （`:544-550`），且**三条 `close_delivery` 路径（`:498 / :534 / :550`）换进程后都不得乱序**。
- [ ] **★二评 G1 三条机械门禁**：`remote.deliver_worker` 入 `tests/helpers/remote_dag.py::LAYERS` = **L2**
      （只依赖 `offline_deliver`(L1) + stdlib ⇒ 秩 2，2 已被占用 ⇒ 不产生空洞；**不得** import 上层、
      **不得**进 `STANDALONE_BOOT_MODULES`）· 新老文件都过 `tests/test_python_loc_budget.py` ·
      慢层用例显式 `@pytest.mark.time_budget(...)`（缺省 warn 5s / **fail 30s**）。
- [ ] **★二评 G7-5 降级用例**（本功能在真机上的主路径）：`Popen` 起不来 ⇒ **sticky 降级线程模式 +
      响亮一行 + 补传仍然送达**（且不再重试 `Popen`）。
- [ ] **★二评 G3 跨会话续投**：段末未销 repost 落 `owed_reposts` ⇒ 下一会话自动续投；未销时响亮一行。
- [ ] **无新依赖**（标准库 only）。
- [ ] 文档：`docs/nn/remote-transport.md` 顶部 `## §<该文件当前最大号+1>` + `docs/nn.progress.md` 索引一行。
- [ ] `DECISIONS.md` 一条（三问：被否决备选 ✅ `multiprocessing` spawn/fork、去掉 idle-timeout ·
      会重犯 ✅ 「后台线程就够了」这个直觉 · 不能就近表达 ✅ 跨进程边界 + 生命周期/降级策略）。

---

## 5. 验收（**不依赖 TPU 重跑**）

* **本地（主闸门）**：§4 全部用例绿；忙等红用例转绿 = **P0 的完成判据**（**不是** 40× 的结案判据，见 §0）。
* **★评审 A3 埋点在位**：`[deliver] … cpu=…s ticks=…` 每轮可见（最便宜那档）。
* **★评审 A2 云机侧改成「埋点判据」而不是「不崩」**：这次很可能**仍然崩**（H6b `cloudflared` 自旋还在候选里）
  ⇒ 验收写成两条：① 埋点到位；② **若仍崩，用埋点判 H6a/H6b**
  （本进程 CPU 吃满 ⇒ H6a；本进程 CPU 正常而同机被吃满 ⇒ H6b），而不是把「不崩」当成通过条件。
* **★评审 A2' 可选对照段（若云机上还有腿在跑）**：`--no-deliver`（`run_loop.py:409/494`）跑一段，
  530 照旧而 PPO 不变慢 ⇒ 补传腿是元凶。这是**唯一直接**分离「补传 vs cloudflared」的实验，
  成本远低于信息量 ⇒ 至少列为可选取证。
* **负向**：无 530 的正常段，步耗仍 ~0.186 s/step；`pending()` 清零、`delivered.json` 记满、段末摘要在。

---

## 6. 分阶段与顺序

1. **P0 忙等 + 埋点**：先落计数器/探针（**不改行为**）→ 写红用例（判据 = `_idle_spins`，§2.3）
   → 在未修代码上确认红 → 改 `_drain_loop`/`_sync`/`close` → 红转绿 + 既有用例不回归
   → 接 A3 埋点（最便宜那档）与 G3 的 `owed_reposts`/C5 的 `rejected` → **可单独交付**。
2. **P1 独立进程**：`remote/deliver_worker.py`（子进程）+ `DelivererProcess` + `Protocol`（C7）
   → 快/慢两层用例 → 再接 `plan_handoff.open_run_context`（默认 `mode="process"`，sticky 降级）。
3. **P2（可选，★评审 C11）**：`_eval_rows_for()` / `_row_for()` 的**增量读**
   （`eval_log.jsonl` 是 append-only 且单调增长 —— 缓存 `(size, offset)` 即可；`metrics.jsonl` 同理）。
   **搬出训练进程只解决了 GIL，没解决「每轮全量重解析一个越来越大的文件」**——
   而它正是 P1 的立项理由之一（来源 plan §8.5），**不许无声消失**：要么排进 P2，要么显式写「不在范围内」。
   **★二评：本行即「显式写不在范围内」的落点——P2 不在本次交付范围（本次只做 P0 + P1）。**
4. 每阶段各自跑一次全量 nn gate；两阶段都完成后跑 `bun run check`（根项目，附加）。

---

## 7. 被否决的备选（三问存档，免重犯）

| 备选 | 否决理由 |
|---|---|
| `multiprocessing`（`spawn`/`fork`） | `spawn` 在 notebook 里 re-import kernel 的 `__main__` 必坏；`fork` 复制已初始化的 XLA/TPU 运行时 + 整个训练进程内存 |
| **只**修忙等、不做独立进程 | 同进程通道仍在：`_eval_rows_for()` 全量解析 33MB `eval_log.jsonl`、gzip/base64 ~1.2MB、`json.dumps` ~2MB 体 —— 每轮都在训练进程的 GIL 上跑 |
| **只**做独立进程、不修忙等 | 忙等烧的是**一个核**，搬进子进程照样烧机器；2–4 vCPU 的云机上照样拖慢训练。「都要做」不是「二选一」 |
| 用 pipe/socket 传**控制请求** | 需要**定时读**（Windows 的 `selectors` 只认 socket，管道上没法 select）。★评审 C16 澄清：这不与 §3.3 的 stdin-EOF 冲突——那里是**无定时的阻塞读 + EOF 判父死**，不需要 select。此处措辞原有过歧义，已改写 |
| 去掉 idle-timeout、只靠 stdin EOF | 见 §3.4：EOF 挡不住子进程自身异常退出（OOM/崩溃），而 C1 的致命点正是「死了没人知道」 |
| 子进程无 idle-timeout 的常驻 daemon | 本项目已有 Kaggle `Canceled by backend`（常驻子进程）的事故；必须留退路 |
| 段末用 `kill()` 直接收子进程 | 会丢掉「积压已推完」这个事实；必须先给有界 flush（`stop.budget`），超时才升级到 `terminate()`/`kill()` |
| 探活失败后 `wait(min(DRAIN_TICK, 剩余 PROBE_TTL))` 自旋重试 | 见 §2.2-2：收益（几分钟内多试几次）不值复杂度；重试节奏交给「下一轮唤醒」，与原始「本轮放弃，下轮再试」同语义 |
| 把「欠一次重投」留在**通道/内存**里（不落盘） | **★二评 G3**：那一轮已在 `delivered.json` 记账 ⇒ 下次会话 `pending()`（磁盘有−账本无）**不再含它** ⇒ hub 侧该轮 `eval_rows` **永久缺失**；而 §3.5-C15 的「不重放」还会让「子进程死掉时那条重投」同样不补。状态必须在磁盘上（`owed_reposts`） |

---

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 子进程起不来（`sys.executable` / cwd / `PYTHONPATH` 不对，**或包太旧没有该模块**） | **★评审 C9：这是上线后的常态** ⇒ 判据 `boot ok` ≤5s + **sticky 降级到线程模式** + 响亮一行；训练绝不因此停 |
| 子进程自己退出（idle/崩溃）后没人重启 | **★评审 C1**：idle 条件三合一 + ≥1800s + `submit_*`/`close()` 按需 respawn + 重启计数进 `status()` |
| 控制文件跨会话重放旧 `stop` | **★评审 C3**：文件名带 `run_id + start_ts`，`start()` 新建（不 truncate） |
| 半行 `final` 永久丢失 | **★评审 C4**：只推进到最后一个 `\n`，残余留缓冲区 |
| 重启重放整段 repost | **★评审 C15**：父侧记已写字节数，重启传 `--ctl-offset` |
| 子进程恢复后一瞬间灌满积压，把 2–4 核打满 | **★评审 C10**：每 tick 预算（如 8 轮 / 20s），段末 flush 除外 |
| `_rejected` 随子进程重启重置 ⇒ 被拒轮次反复重投 ~1.9MB | **★评审 C5**：把 `_rejected` 按 `weights_fp` 落进 `delivered.json`（该文件按 `run_id` 归属，`_load_ledger` 本来就会校验）⇒ 语义从「本会话」改为「本 run」（新 run 仍会再试一次） |
| 补传因 401/403 自停后父侧不知道 | **★评审 C6**：`disabled_reason` 写进状态文件，`status()` 带上 |
| 双写者（父侧又去写 `delivered.json`） | §1-4 写进实现注释；`DelivererProcess` 只读该文件 |
| 子进程变孤儿（kernel 被杀） | `stdin` EOF（阻塞读线程）即收线 + idle-timeout 双保险 |
| 换进程后段末摘要丢失 | 新用例专门钉 `{"final"}` 穿到 `deliver_result`（含 `end_it_reached`，历史上漏过一格）+ `close()` 后核对 `result_done` |
| 毒丸项（一轮永远投不出去）拖住整趟 drain | **★二评 G7-1**：同一 `it` 连续 3 次异常 ⇒ 记进 `_rejected`（带原因）并销账 |
| 单测里意外起真子进程（变慢/污染真面） | **★二评 G7-4**：`pin_production_env` 钉 `NN_DELIVER_MODE=thread`；慢层用例显式 `"process"` 且 `pop("BATTLE_HUB_TOKEN")` |
| 段末仍有未销 repost 而无人知道 | **★二评 G3**：`close()`/子进程退出前核对 `owed_reposts` ⇒ 响亮一行 |

---

## 9. 评审处置（`plan/isolation.review-hy.md`，2026-10-06）

**总评**：评审的**诊断复核、三个「静默失败」洞（C1/C3/C4）、类型面（C7）、
「在飞的包必然起不来」（C9）** 都站得住，已全部采纳并改进正文。
**唯一一处「术语纠正」**：C16 说的「§3.3 与 §7 自相矛盾」——不是矛盾（**定时读**才需要 select，
**EOF 判父死**不需要），但它指出的**措辞歧义**确实存在，已改写 §7 对应行并在 §3.3 写明机制。

| # | 结论 | 处置 |
|---|---|---|
| **C1** respawn 缺失 / idle-timeout 会杀腿 | **采纳** | §3.3 idle 条件三合一 + ≥1800s + 定位为「纯兜底」；§3.4 按需 respawn + 重启计数进 `status()`；§1-7 列为不变量；§3.4 补「去掉 idle-timeout」的备选与否决理由 |
| **C2** `{"round": it}` 没进格式 | **采纳** | §3.5 补 `{"round": it}`（仅唤醒） |
| **C3** ctl 跨会话重放旧 `stop` | **采纳** | §3.5 会话级文件名；§1-8 列为不变量；§8 风险表 |
| **C4** 半行 `final` 永久丢失 | **采纳** | §3.5「只推进到最后一个 `\n`」+ 残余缓冲；§4 用例 7 |
| **C5** `_rejected` 随重启重置 | **采纳** | §8 风险表：落进 `delivered.json`（按 `weights_fp`），语义改为「本 run」 |
| **C6** `status()` 看不到 `disabled_reason` | **采纳** | §3.4 表 `status()` 一行 |
| **C7** 类型面 `OfflineDeliverer \| None` 必红 mypy | **采纳** | §3.4 加 `Protocol`；正文写明「§3.4 的同接口必须在类型上成立」 |
| **C8** 降级没有判据 / 要 sticky | **采纳** | §3.3 `boot ok` 握手；§3.6-⑤ 判据 + sticky |
| **C9** `sys.path` 不继承 + 在飞包里没有该模块 | **采纳（本条最有价值）** | §3.6-⑤ 显式 `cwd`+`PYTHONPATH`；**§3.7-4 写明「降级是上线后的常态」**；§8 风险表首行 |
| **C10** 子进程一瞬间灌满积压 | **采纳** | §3.3-4 每 tick 预算；并加「段末 flush 不受此限」的例外 |
| **C11** `_eval_rows_for` 33MB 全量解析没进范围 | **采纳** | 列为 **P2**（§6-3），并写明「不许无声消失」 |
| **C12** `final` 必须排在 `stop` 之前 + 收尾核对 | **采纳** | §3.3-3 写成注释 + 断言；§3.4 `close()` 一行加「`result_done` 仍 False ⇒ 响亮记一行」 |
| **C13** `stop.until` 用 epoch 秒 | **采纳** | §3.5 改 `{"stop": true, "budget": 90.0}` + 子进程 `monotonic()` 自算 |
| **C14** token env 名要复用 | **采纳** | §3.6-③ 点名 `BATTLE_HUB_TOKEN`（已核实 `run_loop.py:346-361`）+ 显式注入 |
| **C15** 重启重放整段 ctl | **采纳** | §3.5 `--ctl-offset`；§4 用例 8 |
| **C16** stdin EOF 需要 select / 与 §7 矛盾 | **采纳（澄清）** | §3.3 写「阻塞读线程，不用 selectors」；§7 该行改写（见上「术语纠正」） |
| **B1** repost 重试节奏要写清 | **采纳** | §2.2-2 写明「依赖下一次唤醒、不是自动重试」+ 为何不采用 `wait(剩余 PROBE_TTL)` |
| **B2** `close()` 漏 `_repost` | **采纳** | §2.2-3（核实 `offline_deliver.py:255` vs `:416`，判断成立） |
| **B3** discard 条件要写死 | **采纳** | §2.2-1 |
| **B4** repost 饥饿 | **采纳（写清接受）** | §2.2-1 末段 |
| **B5** 主判据换 `_drain_ticks` | **采纳** | §2.3 主判据；CPU 时间降为副判据 |
| **D1–D5** 测试面 | **采纳** | §4（D1/D3 写明；D4 分快慢两层；D5 进 DoD） |
| **A1** 「核心证据」与诚实账打架 | **采纳** | §0 拆成「P0 完成判据」vs「40× 结案判据」；§5 同步 |
| **A2** 云机验收是待验不是 DoD | **采纳** | §5 改成「埋点到位 + 若仍崩用埋点判 H6a/H6b」 |
| **A3** 40× 闭环埋点没被接走 | **采纳，并上调为 P0 的一部分** | §2.4（`os.times()` 差值 + `_drain_ticks`）；落点给了「最便宜（日志行）/ 可归档（回传行）」两档，并标注 lockstep 代价 |
| **A2'** `--no-deliver` 对照段 | **采纳（可选）** | §5 末「可选取证」 |
| 认可不动（§3.2 用 Popen / `_repost` 降为工作项 / P0 可单独交付 / token 不进 argv / 否决 pipe 控制通道） | 一致 | 未改结论 |

---

## 10. 二次评审处置（2026-10-07，代码级复核）

**总评**：§0 的诊断、§2 的修法、§3.2 的形态判定都经代码复核**成立**——忙等自锁链是
`_drain_loop`:260-294 认 `_repost` 当唤醒源 → `_sync`:515-523 探活失败又把它放回
⇒ 内层 `while` 立刻退出、无 work 分支、不进 `wait`（100% 核自旋）；而 530 ⇒ `probe()`:356-383
走失败分支且**不** `disable`（非 401/403）⇒ §2.3 的红用例构造有效。
本节追加的是**上一轮评审（`isolation.review-hy.md`）与立案稿都漏掉的**六条，实现前必须落进本文件。

### 10.1 ★二评 G1 — 三条机械门禁（不写就必红）

| 门禁 | 判据（源码） | 本 plan 的动作 |
|---|---|---|
| `tests/helpers/remote_dag.py::LAYERS` | 「`remote/`·`hub/`·`worker/` 下**全部生产模块恰好**在册；层号 = 拓扑秩 = `1 + max(deps)`；层号集合**稠密**」 | `remote.deliver_worker` **入册 = L2**（只依赖 `offline_deliver`(L1) + stdlib；2 已被 `offline_eval`/`http`/`push_dispatch` 占用 ⇒ 不产生空洞）。**不得** import `plan_handoff`(5)/`plan_run`(6) 等上层；**不得**进 `STANDALONE_BOOT_MODULES`（它在 `code.zip` 里，不是 raw 拉的引导文件） |
| `tests/test_python_loc_budget.py` | 单文件**代码行 < 1000** | `DelivererProcess` 放 `offline_deliver.py`（§3.4 裁定）；改完量一遍 |
| `nn-training/conftest.py` | 每个用例 **warn 5s / fail 30s**（`NN_TEST_*` 可覆盖，`@pytest.mark.time_budget(N)` 才放宽） | 慢层真子进程用例**必须**显式 `time_budget`；「`close` 有界」用例用 1–3s 预算，不用 30s |

### 10.2 ★二评 G2 — 子进程 → 父侧的**状态面**（§3.4 的 `status()` 现在是一句空话）

进程内 `status()` **早有** `disabled_reason`/`rejected`/`drain_alive`（`offline_deliver.py:758-770`），
所以 C6 的真问题不是「加一个 key」，而是**子进程的状态必须落盘**。规格（写死）：

* 文件名 `deliver-status-<run_id>-<start_ts>.json`，与 ctl **同目录、同会话身份**；
* 写者 = 子进程，每个 tick 用 `atomic_write_json` 全量覆盖（小文件，1Hz）；
* 字段：`pid` / `updated_at`（epoch 与 monotonic 都给）/ `enabled` / `disabled_reason` / `delivered` /
  `pending` / `reposts_unsent` / `rejected` / `result_done` / `last_ctl_at` / `ticks` / `idle_spins`；
* 读法：父侧 `status()` = 磁盘（`delivered.json` + artifacts 目录）+ 本文件 + 父侧自持的 `restarts`；
  `drain_alive = proc.poll() is None`；`mode ∈ {process, thread}`；`result_done` 对
  §3.4-C12 的收尾核对（「final 已提交而 `result_done` 仍 False ⇒ 响亮一行」）。

### 10.3 ★二评 G3 — 「欠一次重投」必须是**磁盘状态**（§1-6 只覆盖了段末摘要）

`_repost` 是纯内存集（`:176`），而**该轮已在 `delivered.json` 记账** ⇒ 段末丢掉的那条 repost
**下次会话也补不回来**（`pending()` = 磁盘有−账本无，不再含它）⇒ hub 侧那一轮的 `eval_rows`
**永久缺失**——而那正是 `submit_eval_round` 存在的唯一理由。C15 的「重启不重放」还会让
「子进程死掉时那条 repost」同样不补（它当时住在**通道**里）。修法（写死）：

* `delivered.json` 加 **`owed_reposts`**（= 未销的重投集合，落盘即事实）：送达（`ok`/`skip`）即销账并落盘；
  构造/启动时读回 `_repost` 继续投（**跨会话续投**）；`close()` / 子进程退出前仍有未销项 ⇒
  **响亮记一行**（`[deliver] 段末仍有 N 轮重投未送达`）。
* C5 的 `rejected` 一并落进同一个文件，形状 `[{"it":3,"weights_fp":"…","why":"HTTP 400: …"}]`：
  **加载时**才校验 fp（只对「被拒过的」那几轮读一次权重，不是每轮候选）；fp 不符 = 内容换了 ⇒ 照常重试。
  `pending()` 的语义与开销**不变**（仍只查 `weights.json` 存在 + 内存 `_rejected`）。

### 10.4 ★二评 G4 — §2.3 的判据换成**确定性探针**

修好之后 `_drain_ticks` 仍每 `DRAIN_TICK_SEC=0.5s`（超时唤醒）涨 1 ⇒「采样窗口内不得增长」在**绿**的
实现上就是假的。改用 `_idle_spins`（只在「这一圈既没干活、也没 `wait`、也没收线」时 +1 ⇒ 修好后
**该分支不可达**）：断言 `== 0`，无窗口/容差/sleep；红用例用 `_wait_until(_idle_spins > 0, timeout=0.6)`。

### 10.5 ★二评 G5/G6 — P0 的顺序与埋点的角色

* 顺序：**先落计数器与探针（不改行为）⇒ 在未修代码上确认红 ⇒ 再改 `_drain_loop`/`_sync`/`close`**（§7 纪律）。
* 忙等期间那条腿**什么都没打**（既不 `wait` 也不干活），而在飞的老包里**连埋点都不存在**
  ⇒ **P0 与 P1 都要重新导出 `code.zip` 才生效**（§3.7-4 只写了 P1）。云上判 H6a/H6b 的唯一判据是
  **`os.times()` 的进程 CPU 秒增量**（与已有 `ppo_sec` 同屏）；`_drain_ticks`/`_idle_spins` 是
  **本地测试钩子 + 不变量探针**。落点 = 每轮一条 `[deliver] … cpu=+x.xxs ticks=… idle=… pending=… repost=…`。

### 10.6 ★二评 G7 — 其余采纳项（小但会咬）

1. **持久集 + 异常**（B3 的洞）：同一 `it` 连续 3 次异常 ⇒ 记进 `_rejected`（带原因）并销账。
2. **行号勘误**：`close()` 的 `_want_sync` 在 **`:256`**（原写 :255）；`BATTLE_HUB_TOKEN` 解析在
   **`run_loop.py:347-362`**（原写 346-361）。其余点位（`_drain_loop` 260-294 · `_sync` 502-524 ·
   `pending` 416 · `plan_handoff` 171/311 · `plan_run` 498/534/550 · `_boot_server` 694）复核**准**。
3. **打包范围核查结论**：`ArtifactStore.finalize`（`artifacts.py:306-344`）按**白名单**入包
   （PLAN/MANIFEST/README/STATE/METRICS/EVAL_LOG + `it-*/*`）⇒ ctl/status 落在 `work_dir`
   （甚至 artifacts 目录内的 `work/`）**都不会混进交付物**（§3.5 的 ⚠ 可关闭）。
4. **测试环境**：`tests/conftest.py::pin_production_env` 钉 `NN_DELIVER_MODE=thread`（单测默认不起
   真子进程）；慢层用例 spawn 时 `pop("BATTLE_HUB_TOKEN")`（子进程继承环境，别把请求打到真面上）。
5. **最该有却缺的用例**：**「子进程起不来 ⇒ sticky 降级线程模式 ⇒ 补传仍然送达」**——C9 说这是
   上线后的**常态**（在飞的包没有新模块），它是本功能在真机上的主路径，必须有断言。
6. §2.2-2 的「重试交给下一次唤醒」写清最坏时延：轮间隔 30–700s ⇒ 一次失败的 repost 最坏等 ~700s
   （迟到 ≠ 丢失；§10.3 的 `owed_reposts` 兜住跨会话）。
