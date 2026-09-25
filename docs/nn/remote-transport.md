# NN 远程链路与传输 — 技术档案

> hub / worker / 云机 / 离线任务包 / 回传 / 优先级调度。
>
> **来源**：2026-09-23 由 `docs/nn.progress.md`（单文件 7.9k 行）按主题拆分；本节内编号
> 为本文件局部编号（倒序：新条目置顶、号大，`§1` 最旧），旧编号对照见
> `docs/nn.progress.md` 附录。每节内容拆分时**未改写**（只更新了内部交叉引用）。

---

## §48 「离线 = 发一份 kind=run 队列项」那条腿退役：离线课由云机取包接手（2026-09-25）

> 现场来源：`reports/online-offline-hot-switch-audit-2026-09-25.md`（§4-L3 / R3）+ 用户裁决；
> 裁决与残留清单 → `plan/online-offline-role-routing.plan.md` §7（口径 §7.0、不许碰的两条 §7.0.1）。

**裁决（用户口径）**：离线场景里**没有「一整段」这个中间概念**——云机接手一门课就一直跑，直到
① 跑完整个课程；② 云机配额/预算用尽；③ 人工停机/停课；**能传回来多少是多少**（已跑完的轮尽力
补传，不为凑完整卡住在飞的那一轮）。所以队列里的「整段 job」不是离线场景的载体，它只是
`kind=run` 这条**残留路径**的形状。

**为什么退役**：那条腿（本机发一份带段长的 `kind=run` 队列项、随后等 8h）与取包链
（`--export-bundle` 任务包 → 云机 `/offline/tasks` → 取包 → 跑完回传）**干的是同一件事**，
是两个执行者——2026-09-25 云机接错盘事故的结构。裁决：**取向 1（源头砍掉，砍在发布点）+
取向 3 的机制作尾巴；取向 2（只给离线盘）否决**（`role=offline` 的消费者当时并不存在，
要成立就得新造并部署一个）。

**实施形状**（P5）：

| 位置 | 现在 |
|---|---|
| `rl/loop_steps.py` | 发布端（`_remote_run_segment`）删除；`RUN_WAIT_DEFAULT_SEC` / `_run_wait_sec` / `--run-wait-sec` / `rl.run_wait_sec` 一并退役（**没有 8h 白等这回事了**） |
| `rl/loop_round_steps.py::step_course_iter` | 离线课（来源 `run` 或写了终点值）⇒ 一行指路 + `ROUND_OFFLINE_EXIT`：**不采样、不发队列项、不等、不进账本** |
| `rl/loop_round.py::resolve_collect_mode` | `COLLECT_SEGMENT` → `COLLECT_OFFLINE`：**绝不**回落 `COLLECT_LOCAL`（回落 = 本机偷偷自己采样、与云机双跑） |
| `rl/loop_steps.py::_remote_ppo_publish` | **咽喉点守卫**：带 `plan_bytes` 而不带 `export_path` ⇒ `SystemExit`，消息指路 `battle.offline.ipynb` + `--export-bundle` |
| `remote/worker.py::run_job` | `kind=run` **响亮拒收**（最前，零指令零下载；不是「当成 iter 跑一轮」——半跑会产出权重、让控制面看着像在推进） |
| `remote/run_loop.py::run_plan_job` | 保留但**无生产调用者**：它是「从首轮结果续下去」的唯一入口，`tests/test_run_loop.py` 的 4 组段语义回归挂在它上面 |
| `kind=run` 的 **manifest 形状** | **保留**（`--export-bundle` 仍造它，只是 `register=False` ⇒ 只建 job 目录当打包源、不进待领池） |
| 配置（`rollout_src=run` + `run_iters`） | **不动**：它是「这门课由云机接手」的既有声明，也是导出腿的终点口径（删字段会让本机悄悄退回自己采样） |

**离线盘报名（新）**：跑 `battle.offline.ipynb` 的云机**不碰队列**（它走清单/取包/租约/补传），
所以角色头此前只在 worker 的 peek/claim 面上被读 ⇒ 离线盘在 hub 眼里是匿名的、「本环境有没有
离线盘」这个读数恒为空。现在：`remote/offline_boot.py` 的**每一个** hub 调用都带
`X-Battle-Offline: 1`（`_headers()`，本模块不 import `remote.*`，故头名/值两份拷贝由测试守逐字
相同），hub 在 `/offline/*` 前缀上记一次（`?worker=` 取身份，清单/取包记成 `<offline>`），
`/admin/queue.offline_disk` 报出：`recent`/`recent_n`/`last_seen_ago` + **`stale_jobs`**
（还挂着的 `role=offline` 待领项 = 盘上遗留 / 手写参数 / 混部期旧 hub）+ `hint`。

**没动的两条（用户点名保留）**：① 离线云机**串行跑多门课**（`/offline/tasks` 清单 + `drain`
驻守 + claim/heartbeat/release 租约 + 预算/空闲上限，空队列正常收工）；② **notebook 里不写课程名**
（`requested_courses` 空 ⇒ 清单发现）。`/offline/*` 与任务包格式**一个字节都没改**（既有取包链
e2e 全绿）。

**门禁**：`nn-python-gate` 2484 passed（ruff + mypy + tests/+e2e）· 新增
`tests/test_offline_leg_retired.py`（13 条：发布点枚举 / 咽喉点响亮拒 / worker 拒收 / 本机循环
收官 / 读数 / 报名）· dashboard `bun run typecheck` + 1158 测试绿（文案改词同步）· 一条
`DECISIONS`。

---

## §47 归属（role）：job 自己说「该由哪块盘执行」；认领咽喉点两道闸（2026-09-25）

> 现场来源：`reports/online-offline-hot-switch-audit-2026-09-25.md`（L1–L6 / I1–I6）+ 用户四项裁决；
> 设计与实施 → `plan/online-offline-role-routing.plan.md`（§8 是评审 A–K 的处置表）。

**症状**：课程热切模式时，**历史 job 的归属跟着跳**。最锋利的一条：盘 A 两小时前因为节点上
没有 `bun` 被拒的那个 `kind=run` job（整段自主），在课程切成**在线**之后被**另一块盘**领走——
它有能力跑，但它不该跑；日志上一切正常（队列在降、心跳在跳），只有一行「离线课整段交领」
事后能对上账，而那一行的判据本身就是错的（见下）。

**根因（三条，互相放大）**：

1. **归属不是 job 的属性**：`claim_next` / `peek_jobs` 用 `mode_of(course)` 判「谁能领」——
   mode 住在 hub 内存表、控制台可热切、重启即回启动参数，用它当判据等于把归属交给一个易变的开关。
2. **认领面有四条腿，闸只在其中三条上**：`claim_next`、`peek`+`claim`、按 id 直领（`claim_job`，
   原本**零校验**）、以及 **push 派发**（`push_dispatch._dispatch` → `Hub.claim`，**不经过** `claim_job`）。
   各写各的判据必然漂（第 4 条腿就这么漏了）。
3. **能力 ≠ 归属**：`--offline` / `X-Battle-Offline: 1` 原本是**能力声明**（「我能自主跑完整段」），
   而 `kind=run` 整段**确实**由在线盘跑得动（审计 §3）⇒ 能力闸拦不住「有能力的盘接走不属于它的活」。
   这是把 *capability* 换成 *policy* 的正当理由（supersede `DECISIONS.md:1634` ③）。

**修法（落成不变式）**：

- **`manifest.role ∈ {offline, online}`，发布时定死**（`publish_job`，`kind` 的同一快照：
  `run ⇒ offline`，`iter/ppo/bc ⇒ online`；`MANIFEST_KINDS`/`KIND_ROLES` 共用一份全集，穷举用例钉住）。
  字段**可选**（旧 job 无它 ⇒ `role_of` 按 kind 兜底，不拒单），但一旦存在必须合法。
  显式 `role=` 可覆盖（逃生口）；不落 `MANIFEST_OPTIONAL_DEFAULTS`（那会造第二个事实源）。
- **闸下沉到 `_JobStore._claim_locked`**（租约写入的唯一临界区）⇒ 四条腿天然同源；
  判据函数只有一份 `role_blocked(job_id, role) -> "" | "parked" | "role"`：
  派发面用它**过滤候选**、临界区用它**拒绝**（同一份尺子两个方向）。
- **两道正交的闸**：归属闸（job 级，`manifest.role`）+ **停摆闸**（课程级，`mode=offline` 且请求方
  不是离线盘）——后者是 2026-09-20 的既有语义，**保留**（离线课的活留给切回在线，`pending_n` 不降）。
- **worker 侧复用既有载体、不新增第 4 个模式载体**：`CFG["offline_worker"] → --offline →
  X-Battle-Offline: 1`，头名与取值**逐字节不变**（改名只会让混合部署里的带标 worker 静默掉线），
  只有语义从「能力」升为「归属」。**claim 也必须带头**（旧代码里这条头从未出现在 claim 上；
  闸下沉之后，只在 peek 上带头 = 带标 worker **自锁**：peek 绿、claim 红）。
- **取包端点补 mode 闸**（`_get_task_pack`）：包在盘上 ≠ 该发给你（切离线时自动导出且「已有包不动」
  ⇒ 切回在线后包还在）。只在「表里有它且明确 online」时 409；冷课/未扫到的课照旧放行
  （与 `_task_pack_miss_candidate` ① 同规）。
- **归属可见**：`/admin/queue` 每行加 `roles: {jid: role}`。**不**报 `claimable` 布尔——
  「可不可领」是**相对请求方角色**的属性，观察者不带角色，任何布尔都会误导。

**行为变更（必须点名）**：带 `X-Battle-Offline` 的盘**不再兼领在线盘的活**（旧注释里明写过
「带标 worker 仍可领在线课」）——一个盘一种任务，用户 2026-09-25 裁决。
`DECISIONS.md §2026-09-25-goalnn-role-routing`。

**被否决**：新增独立 `X-Battle-Role` 头 / `--role` 参数（= 第 4 个模式载体，与「模式只有一个来源」
自相矛盾，且旧 worker 不带新头 ⇒ 静默掉线）· `role` 进 `MANIFEST_OPTIONAL_DEFAULTS`（静态默认值 = 第二个事实源）·
`NN_ROLE_ROUTING=0` 回退开关（它把「一个盘一种任务」变成**可选**，正好在最需要它的混部期复现事故；
回滚面已在笔记本/CLI + 本提交同批）· 只在各调用点加闸（push 腿必然漏）· 归属缓存永不失效
（重发覆盖 manifest 就谎报；改为 `publish` 里 pop）。

**违反后果**：归属写在调用点 ⇒ 新加一条腿就绕过闸，而绕过的表现是静默的（活被错的盘领走、
日志正常）· 归属读 mode ⇒ 每切一次模式历史 job 跳一次（本轮事故）· claim 不带头 ⇒ 带标 worker
自锁（peek 说能领、claim 当场拒）· 取包端点不查 mode ⇒ 离线盘能取走在线课的包跑整段（L6）·
`/admin/queue` 报 `claimable` 布尔 ⇒ 排障的人拿着一个与请求方角色无关的答案去定位「为什么没人领」。

**真机验证点**：`整段交领：` 那一行的判据是 `job_role(jid)==offline`（不再读 mode）·
`/admin/queue` 的 `roles` 与 `pending_n` 一起看（「队列不降」是在等另一块盘）·
worker 侧两条 HTTP 面（`/jobs/peek`、`/jobs/{id}/claim`）都应带 `X-Battle-Offline: 1`（离线盘观察），
在线盘**一条都不带**。

---

## §46 在线腿（tailscale 盘）从来不装 bun：切到在线课程后 worker 每单零下载拒单（2026-09-25）

**症状**（用户真机日志，`battle.tailscale.ipynb`，`mode=rl/pull`，Tesla T4）：

```
[01:39:43] [worker] job abfef8f7398938b6 REJECTED: 节点上找不到 'bun'（kind=iter 需要 bun 跑 rollout）
           —— bun 必须随节点引导装好，且装 bun 要发生在装 tailnet/代理之前 — skip (not retried)
```

之后连续 47 次轮询空转（hub 没有第二个能跑的 job）—— 一单都没开始。

**根因不是「装不上」，是「从来没装」**：`battle.tailscale.ipynb` 的 cell、`remote/notebook_boot.py`、
`remote/tailscale_boot.py` 三处 `git log -S bun` 全空；而 `battle.offline.ipynb` 的 cell 里一直有那段
（`curl -fsSL https://bun.sh/install | bash` + 把 `~/.bun/bin` 前置进 PATH，且刻意放在装 tailnet **之前**）。
所以只有「课程切到在线 / 换盘」才暴露。worker 侧的能力自检（`plan/train-mode-hot-switch` L3.2，
`f274ac1b`）只是把这个失败从「payload+code.zip+ts_code.zip 下完 3.42MB / 12.1s 之后才炸」**前移到零下载**，
不是新引入的缺陷。

**修法（模块侧，不必重发 notebook）**：`remote/tailscale_boot.py` 新增 `bun_path()` / `ensure_bun(log)`，
由 `ensure()` 在**第一条语句**调用 —— `ensure()` 是在线腿（`notebook_boot.run` → `tailscale_boot.ensure`）
与 Colab 离线腿（`offline_boot.ensure_tailscale` → `tailscale_boot.ensure`）共用的「让节点具备 rollout
环境」入口，顺序因此天然正确，调用方不必记得这条约束：

- 已装（`PATH` 命中，或 installer 落点 `~/.bun/bin/bun`）⇒ 跳过；
- 未装 ⇒ **在 `platform_net_env()` 里**跑 installer（userspace tailscaled 的本地代理只转发
  Tailscale IP，代理一改就再也装不上；`platform_net_env` 负责还原引导前的平台代理）；
- 装成 ⇒ 把 `~/.bun/bin` **前置进 `os.environ["PATH"]`** —— installer 只改 shell rc，
  当前进程的 PATH 不会自动更新，而 `resolve_bun` 只认 `shutil.which`；
- 装不上 ⇒ **只记日志不抛**：能力缺失的唯一判定点仍是 `resolve_bun` 抛 `ProtocolError`。
  两处都判会分叉成两条事实源，还会把「装 bun 失败」升级成「引导失败」，连能跑的单一起拒掉。

**覆盖范围要说清**（别以为一改全绿）：Kaggle 上的**离线盘不走 tailscale**（`offline_boot.py:33` 明确
跳过全部 tailscale 步骤），因此不经过 `ensure()` —— 它的 bun 仍来自 cell 里那一段（一直有）。本次修复
命中的是**在线腿**，以及在 Colab 上会走 `ensure()` 的离线腿。

**不变式**：任何「让节点具备 rollout 能力」的入口，都不得在 bun 就绪之前改写代理环境 ——
`ensure()` 的第一条语句就是 `ensure_bun(log)`（`tests/test_tailscale_boot_bun.py` 用源码顺序钉住）。

**交付链提醒**：`tailscale_boot.py` 由 notebook 从 GitHub raw 拉 ⇒ 修好要 **push + 重开会话**
（`battle.tailscale.ipynb` 旧的「有缓存先用缓存」当天才改成「每次刷新」，在该修复生效之前，
`/tmp/battle-boot` 里那份旧文件会继续被用）。

## §45 首次跑死在补 TS 运行时那一步：产物目录还不存在就往里写 zip（2026-09-25 云机实测）

**症状**（用户真机日志，`battle.offline.ipynb` 全新一跑）：取包、铺代码都顺利，紧接着

```
[00:11:39] [offline] 代码就位: /tmp/worker-code（1710636 bytes, sha12=51e0678bb5c5，来源 任务包 …）
[00:11:39] [offline] 未捕获异常 FileNotFoundError: [Errno 2] No such file or directory:
                     '/kaggle/working/battle-offline/x20-dodge-l1/run/ts_code.zip'
```

整个 cell 死在 rollout 之前 —— 现场看不到任何「下一步该做什么」，只能看着一句 errno 报错。

**根因**：`ensure_ts_tree`（plan/offline-rerun-local-first §4.1 第 5 条）在「本机没有 `ts_code/`」
时从包里补 TS 运行时，直接 `(dest / "ts_code.zip").write_bytes(raw)`。而**首次跑**时
`dest`（`<work>/run`）还不存在 —— 它是 `run_loop`/`import_bundle` 建的那个目录。产物目录**已有**
（本机优先那条腿）时目录当然在，所以这一路只在「全新一跑」时炸：`ensure_code` 的写盘是**有条件的**
（只有 `manifest.json` 读得到才修 `code.zip`，全新一跑没有 manifest ⇒ 不写 ⇒ 不暴露同一个坑），
`ensure_ts_tree` 的写盘是无条件的 ⇒ 只有它炸。

**修法**（最小改动）：写之前 `root.mkdir(parents=True, exist_ok=True)`。
为什么不「等 bundle 导入来铺」：本机优先那条腿**不导入包**（argv 不带 `--bundle`），TS 树只能由
这个函数铺；两条腿共用一个函数，就在函数里把目录准备好。

**为什么此前没有测试拦住（教训，比 bug 本身重要）**：`e2e/test_offline_training_e2e.py` 只覆盖
**hub 侧**（能力闸 / 归位 / 读面 / 取包），云机侧只有单测，而单测的产物目录**全是预建好的**
（`_artifacts()` 夹具）⇒「全新一跑」这条最常见的路径**从来没人走过**。现在两层都补上：
单测 `test_ensure_ts_tree_fills_a_dest_that_does_not_exist_yet`（红过）+ e2e
`test_a_fresh_run_lays_down_code_and_ts_tree_before_the_loop`（真导出器写的包 → 真 `run_one_course`
→ `run_loop` 注入点，断言「run_loop 起来时产物目录已存在、`ts_code/` 与 `ts_code.zip` 就位、
argv 带 `--bundle`」）。两条都在未修版本上复现过同一条 errno。

**不变式**：`ensure_ts_tree` 对「产物目录不存在」「目录在但树缺」「树在」三种输入都要能走完
（前两种要能把目录/树建出来，第三种不重解）。

**第二层（同一条报障的第二次真机，08:37）**：同一个 errno 又出现一次，而这次 `offline_boot.py`
里**已经有那行 mkdir**。日志自证刷新到位（`引导模块 offline_boot.py 已刷新（sha12=608920196dc0）`
= 磁盘那份正是含修复的版本），可报错照旧 ⇒ **跑的不是磁盘那份**。

真因在 notebook 侧：`_load_boot` 每次会话只**刷新磁盘文件**，而同 kernel 里上一次 Run 已经
`import` 过 `offline_boot` ⇒ `sys.modules` 里那份**旧模块继续被 import 命中**（同 kernel 不重载）。
于是「内存跑旧代码、日志 sha 打磁盘新版」—— 那行 sha 取的是 `(dst_dir/"offline_boot.py").read_bytes()`，
读的**是磁盘**，所以 09-22 那次「把实际加载的 sha 打进日志」的加固被整个绕过。

**识别指纹（最硬的一条：用行号抓）**：traceback 帧行号与该行**磁盘文本对不上**。现场
`ensure_ts_tree` frame @ **870**，配的源码文本是磁盘新版 870 行的 `if (root / TS_TREE_NAME).is_dir():`，
而实际动作是 `write_bytes`。按版本对表：048d25bb（07:42 那版）`write_bytes@870` 且**没有 mkdir**；
b697a2cd `write_bytes@890`；bf05ac21 `mkdir@895 / write_bytes@896` ⇒ **内存里跑的是 048d25bb**
（`run_one_course@1139`、`run@1290` 也衬得上帧行号 1232 / 1330）。「帧行号来自执行版、源码文本来自
磁盘版」这个错配本身，就是「执行对象 ≠ 磁盘文件」的判据。

**修法**：① notebook 导入前 `for _m in ("offline_boot", "tailscale_boot", "remote.offline_boot"):
sys.modules.pop(_m, None)`；② `offline_boot.py` 新增 `BOOT_SELF`（内存指纹常量），notebook 日志打
`getattr(offline_boot, "BOOT_SELF", "<missing>")` —— **磁盘 sha 骗得过，模块对象骗不过**。
测试：`test_offline_boot.py::test_boot_self_fingerprint_is_present` + `test_offline_notebook.py`
三条断言（含「pop 必须在 import 之前」）。

**两个盘都改了（同日）**：`battle.tailscale.ipynb` 的 `_load_boot` 原来停在更早的形态
（「有缓存先用缓存」+ `_branch.txt` + 无 sha 日志 + 无 `sys.modules.pop`）—— 一并升到与
offline 盘同级。两个盘的 loader 是**各自内联的两份**（没有共享实现），所以漂移风险靠
`tests/test_notebook_boot_refresh.py` 守：关键行为（`已刷新` / `@ sha12=` / `用上一份缓存继续` /
`.replace(_dst)` / `sys.modules.pop` / `BOOT_SELF`，加「先摘再导」顺序与「无 `_branch.txt`」）
做成一组 needle，两个 cell 各跑一遍。

**用户侧即时解**：**Restart kernel** 再跑（没重启的话，修好的 notebook 也挡不住已在内存里的那份）。

—— 全文（判定表 / §8 判据与上界）→ `plan/offline-rerun-local-first.plan.md` §6.1

## §44 云机不再写死课程名：hub 清单 + 领取租约（plan/offline-task-discovery.plan.md，2026-09-25）

**用户口径**：「云机不应该要在 `battle.offline.ipynb` 里配置离线课程名，它应该直接向 hub 问询，
逐个下载离线任务包并完成训练任务。」

**新读面 `GET /offline/tasks`**（只读）：默认列 `mode=offline` 的课 + 包在哪（名字/字节/sha/mtime）+
段元信息（`run_id`/`it`/`end_it`，从包的 `task.json`+`plan.json` 读）+ 新鲜度（`stale_reason` 复用
过期判据）+ 持有者 + 段内进度；排序 = `ready` 优先、同级按包 mtime 升序（最老的先跑）、`claimed`/`no_pack`
殿后。`?include=all` 才附带 `not_offline`（控制台排障；云机永远用默认）。**零副作用**：不触发重导、
不写账本、不动游标（触发重导仍是 `GET /offline/task-pack` 的专属特权）。
**候选面 = 课程表 ∪ 盘上的开课标记**（评审 S-1）：离线课本机不训练 ⇒ 冷掉/hub 重启后它从
1 小时新鲜窗里消失而包还在盘上；只认表会让云机得到「没有任务」而干等。

**租约 `POST /offline/{claim,heartbeat,release}`**（用户拍板 = 硬租约）：TTL **900s**（离线段是小时级，
与逐轮 job 的 300s 不同档）+ 60s 心跳；**惰性过期**（读时判，不养清理线程）；hub 重启即清
（与触发账本同口径，最坏情形由回传侧 `(run_id, it)` 首写幂等兜底）。**409 表达业务拒绝**
（被别人持有 / 已过期 / 不是持有人），**不用 403**——job 腿上「403 lease mismatch 被 worker 读成
ProtocolError ⇒ 报 job 失败 ⇒ 训练停腿」已经踩过一次。**租约不参与回传**：`/offline/artifact` 一行不改。
`worker_id` 由云机**持久化**在 `<work>/.worker-id`（评审 G1）：cell 中断后重跑是同一台机器，
hub 判 `mine` 直接续领（换新 token）；否则会白等 900s（Kaggle 上等于废掉整个会话）。要顶掉别人用
显式 `?takeover=1`。

**云机侧**：`CFG.course` 退化成**可选覆盖**——填了 = 老行为（顺序/校验一字不改，且**一次都不问清单**）；
留空 = 问清单 → 领租约 → 取包 → 跑完 → 交还 → 记 `served[course]=包 sha`（同 sha 跳课 = 防自激：
包没换就重跑同一段 ⇒ `run_id` 相同 ⇒ 回传全判 `duplicate`）。`queue_mode` 缺省 `"drain"`（跑完一批
继续驻守轮询），受 `session_budget_sec`（**新键**，与逐段的 `budget_sec` 泾渭分明）/
`idle_wait_sec` / 停机信号限制；`"once"` = 跑一批就收工。**老 hub（404）只探测一次**就降级回
「必须填 course」。租约的任何失败都只是日志（训练永不因网络停摆）。

**边界（与 plan/offline-switch-auto-bundle 同一句话的两半，评审 X1）**：**点名**（CFG 或清单）要跑的课
取不到包 = **异常**（跳过继续、全跳过响亮 `SystemExit`）；**队列本来就空** = **正常收工**（rc=0）。
**被否决**：软占位（两台同跑一门课会白烧一张卡，第二份回传被静默丢弃）· 只靠幂等不要租约 ·
hub 自己打包 · 用 403 表达租约不匹配。**真机残留**：两门课自动跑完 + 第二台 409 + 控制台看 holder
（`docs/nn.progress.md` 3.3 row 14，由用户跑）。

—— 全文（规则表 / 兼容矩阵 / 评审处置 G1–G7+S-1+X1–X3）→ `plan/offline-task-discovery.plan.md` §11

## §43 切离线自动出包 + 缺包自愈：不再让云机干等 30 分钟（plan/offline-switch-auto-bundle.plan.md，2026-09-25）

**现象**（用户报障，2026-09-25 18:46–19:14）：在线课切成离线之后，云机 `GET /offline/task-pack` 一贯 404——
每 15s 一条**固定文案**刷满 `hub_tries=10` 轮，转「等上传」模式，等满 `wait_pack_sec=1800` 后 `SystemExit`，
第二门课 `x20-dodge-l3` 一行没跑。整条链上有四处各自成立、合起来才致命：

1. **切离线不导出**：`launchTaskBundleExport` 此前只挂两处（开课 `course-lifecycle.openCourse` / 手动按钮
   `api/route.ts`）⇒ 热切离线后盘上根本没包；
2. **缺包零自愈**：hub 的 `_task_pack_gate` 只管「过期」，包不存在时连门都不进（`_get_task_pack` 直接 404）；
3. **云机 404 日志没有信息**：固定文案 ⇒「没导出 / hub 的 traj-root 不对 / 课程名不一致」三条表现逐字相同；
4. **一次取包失败吃掉整个会话**：`obtain_pack` 等到 deadline 抛 `SystemExit`，串行循环当场停 ⇒ 后面的课全不跑。

**修法（四层同改，逐层各自可判）**

- **控制台**：`setCourseMode` 在 hub 推送**之后**接一个派生动作（纯函数 `autoBundleDecision`，八条规则表）：
  非离线 / hub 没收下意图 / 逃生阀 / 导出忙 / 缺起点权重 / **盘上已有包** ⇒ 不导（有包**也不作废**——
  热切不是重开课，作废会让云机在导出窗口里探到 404）；缺包且可导 ⇒ 起一次导出。**任何结局都不改
  `setCourseMode.ok`**（模式切换本身已经成功）。
- **云机 404 日志**：`_http_error_parts(e)`（HTTPError 的 fp **只能读一次**，正文与字段必须同一份 bytes 出）
  把 hub 的 `path` / `known_courses`（含本门 ↔ 没有本门）/ 触发回执打进日志；409 也打正文。
- **hub 404 自愈门**：缺包时替这门课推一次控制台重导（另开一本 `_TASK_PACK_MISS_TRIGGERS` 账 +
  `TASK_PACK_MISS_TRIGGER_LIMIT=3` + 沿用 600s 节流）；到顶只指路手动，**不制造新的等待理由**；
  包重新出现 ⇒ 账本清零（否则一次上界用一辈子）。
  **候选面 = 盘上的事实**（评审 S-1）：表里明确 online 才排除，否则只要盘上有开课标记就替它导——
  课程表是「1 小时新鲜度扫描」的产物，而**离线课本机不训练**（`remote-jobs`/`training_log` 一小时后全变旧），
  只认表会在最需要自愈的场景里静默无作为。`_course_dir_live` 顺带认「**新鲜的包**」作活证据
  （包与 `task_pack_path` 同源推导，是文件系统事实）。
- **云机队列语义**：`PackUnavailableError(SystemExit)`（message 逐字不变）⇒ 记一行、**跳过继续下一门**，
  末尾汇总「完成 N / 跳过 M」且 M>0 ⇒ 非零 rc；全跳过 ⇒ `SystemExit`（响亮失败）。配置错误
  （课程名不一致 / 产物目录不完整）与训练 `rc≠0` **照旧立即停**。

**不变式**：切离线+无包 ⇒ 恰好一次导出且 `ok===true`；+有包 ⇒ 零导出且包**没被**挪进 `stale-packs/`；
hub 的 `triggered` 在缺包 404 与过期 409 里**同名不同义**（后者兼表「已有触发在飞」，靠 `trigger_note` 自解释）；
两本账 ⇒ 一个重导窗口内同一门课最多 2 次触发（**刻意**，不是 bug）。
**判据来源不许合并**（与本轮的 plan/offline-task-discovery 交叉）：本节的「全被跳过」= **CFG 点名要跑的课**
一份包都没拿到 ⇒ 响亮失败；那份 plan 的「清单为空」= 排队里本来就没活干 ⇒ 正常收工（rc=0）。

**被否决**：把「切离线要导出」写进 hub（hub 不是打包者，控制台才是唯一打包者）· 缺包时让 hub 自己
`export_bundle()`（造第二份打包逻辑）· 「有包也重导」（作废旧包 ⇒ 云机在窗口期探到 404）·
按包与 HEAD 的 `commit` 不符判过期（包是**代码快照**，但新鲜度只看 `init_weights` sha；比对另开工单）。

**真机残留**：E8（切离线 → 云机取包）由用户跑；`docs/nn.progress.md` 3.3 表 row 13 盯这条。

—— 全文与评审处置（F1–F9 / S-1 / X1–X3）→ `plan/offline-switch-auto-bundle.plan.md` §11

---
## §42 离线腿的 it0 基线：控制台在「离线开课」那一刻补评段起点（2026-09-24）

**缺口（不是猜测）**：离线档 = `courses.<课>.rollout_src:'run'`（本机不跑训练）⇒ it0 读数两条产路
都不通——云机侧 `remote/offline_eval.CloudEvalPlan.due()` 对 `it < 1` 恒 `False`（it0 是「本机主循环
的事」），而本机主循环的基线派发（`rl/loop_core._maybe_dispatch_baseline_eval`）在这条腿上压根不在
场上。代价不是少一行：控制台的配对基线是 `evalIters.includes(0) ? 0 : evalIters[0]`
（`dashboard/src/server/iters.ts:1034`）⇒ 缺 it0 时退化成「拿第一条 eval 轮当基线」，随 run 起点漂移，
跨腿（如 x20-demo-mix vs x20-firstkill）失去共同锚。

**修法**：控制台「以离线模式开课」那一刻补派一次（`course-lifecycle.openCourse` →
`launchEvalA(course, '', 0, { baseline: true })` → `rl/eval_a_once.py --baseline`）。评的是
**课程活动权重 `out` = 本段起点 W(0)**，也就是任务包 manifest 里 `init_weights` 的**同一份字节**
（导出即从 `args.out` 取；`prepareCourseForOpen` 缺文件时从课程 `bc` 字节复制播种 ⇒ 新课的 out 与
bc 同 wver，与历史腿的 in-loop 锚天然对齐）；`--iter` 恒 0，派发照走两腿共用那条路
（`dispatch_eval_round(baseline=True)`，账本按 `iter` 隔离去重、快照文件名分流都是现成的）。
同 wver 的 it0 summary 已落账 ⇒ 早退不重派（离线课「停课→重开」不重评）。

**两个坑（写下来免得下次踩）**：
- `baseline_summary_landed(traj_dir, wver)` 收的是**轮目录**（内部取 `traj_dir.parent / "eval_log.jsonl"`，
  `rl/eval_local.py:803-829`）——传课程目录会去读上一级的 `tmp/eval_log.jsonl`，**恒 False**（早退失效、
  每次开课白评一次）。本实现用同口径的 `eval_a_once._read_summary(eval_jsonl, w16, 0)`（读同一册、
  判据逐字相同）。
- `Path("")` 是 `.`（存在）⇒ `--ckpt` 空串会被当成目录去算指纹。TS 侧 `ckpt` 为空时**整条
  `--ckpt` 都不传**，python 侧显式判空（非 baseline 缺 ckpt 仍响亮退 2）。

**成本**：baseline 语料 = `EVAL_SEEDS[:n]`（`should_dual_track(n, baseline=True) = False` ⇒ 无轮转轨）
= **关数 × `eval_games_per_stage`**。x20 课（4 关 × 50）= 200 局，是 A-eval（4×100 双轨 400）的一半；
`eval_games_per_stage` 为 100/200 的课与 A-eval 等量。

**门禁证据**：`tests/test_eval_a_once.py`（+4 例：缺省取 out / 已落账早退 / 缺 ckpt 拒 / iter≠0 拒）·
`dashboard/tests/eval-a-baseline.test.ts`（argv 形状 + `shouldAutoBaseline` 三态）·
`course-lifecycle.test.ts`（逃生阀置位不起子进程）· ruff/mypy 全绿、`pytest tests/ e2e/` 2283 passed、
`dashboard` 1143 passed、根 `bun run check` 2139 passed。

**真机判据未取**（见 `docs/nn.progress.md` §3.3 第 12 条）：点一次离线开课 ⇒ `tmp/<课>/evalA.log`
出现 `[evalA] DONE it0 …`；**等第一轮回传落账后**（指标表的 it0 合成行需要 ≥1 条 `iter>0` 的
`iteration` 行）控制台出现 it0 行、配对基线回到 0。字节同一性有个秒级前提：回传轮会原子推进
`tmp/<课>/weights.json`，核对入口是 `[evalA] … wver=…` 那行 vs 包 manifest 的 `init_weights_fp`
（不自动对账——错了能看出来就够）。

---
## §41 离线 cell 重跑以本机产物为准 + hub 递包前判新鲜度（plan/offline-rerun-local-first.plan.md，2026-09-24）

用户 2026-09-24 口径两条：①「停止 cell 后再 run，应该要能接着机器上已经跑过的 it 继续跑，而不是从 hub 取
（可能过时的）任务包」；②「即使云机重启、之前的工作目录全丢，重新请求离线任务包时，hub 端也要基于课程的
**最新状态**重新打包（可能开课后已有离线 worker 回传了很多轮权重），避免重复训练浪费算力」。

### 现状（为什么用户会有①的体感）

起点计算（`run_standalone` 的 `start_from = state.last_it`）本来就是本机优先，坏的是另外三环：
`run_one_course` 无条件先取包（`wait_pack_sec` 缺省 **1800s**，等不到就 `SystemExit`）· `ensure_code`
无条件用**包里**的代码覆盖运行时 · argv 恒带 `--bundle` ⇒ `import_bundle` 无条件解压 ⇒ **包覆盖本机的
`plan.json`/`manifest.json`**（段参数、`end_it`、`code_sha256` 全换成包里那份）。所以真因是
「计划/代码被包改写 + 白等取包」，不是起点算错。

### 决定

1. **判据 `offline_boot.local_artifacts(dest)`**：`state.json` + `plan.json` + `manifest.json` 三件齐全
   ⇒ 本机优先。三件都要（`run_loop.load_planned_manifest` 就是这么判的）。
2. **本机优先 = argv 不带 `--bundle`**（`build_run_argv(local_first=True)` ⇒ `--artifacts` 单用；
   `run_loop.main` 一直允许它，只是此前没人走）。包降级为**代码/TS 的备源**，不再参与计划/清单。
3. **取包变可选**：`obtain_pack(optional=True)` —— `wait_s` 归零、不弹上传框、取不到返回 `None`（**不抛**；
   `0s` 的既有语义是"不等待，立刻报错"，与本模式无关）。本机有进度时不再为取包白等。
4. **半截产物目录响亮拒**：有 `state.json` 而缺计划/清单 ⇒ 拒（两条出路：补回缺件 / 清空 `dest`）。
   理由：让包补齐会走 `ArtifactStore.start` 的"plan_sha/run_id 不符 ⇒ 重开一段"分支 ⇒ 本机 `it-NNN`
   被**重跑覆盖**。绝不静默重跑。
5. **代码/TS 跟着产物走**（`ensure_code` / `ensure_ts_tree`）：候选 = `<dest>/code.zip` → 包 → hub `/code`，
   逐候选用 **`<dest>/manifest.json` 的 `code_sha256`** 选中；全部对不上 ⇒ 拒且**不写 `CODE_DIR`**；
   选中字节 ≠ 现盘 `<dest>/code.zip` ⇒ **用同 sha 副本修复**它（`run_loop` 读的是那份：
   `_read_opt_file(root, "code.zip")`；不修会在 worker 侧报"传输损坏"，一条指向错误原因的报错）。
   TS 同规（`ts_code_sha256`），已有 `ts_code/` 就直接用、不重解。
6. **`CFG.force_pack` / `CFG.task_zip` = 显式老行为**（包覆盖计划/清单，日志写明本机 it 与计划区间）。
   `CFG.force_pack` 是本 plan 新加的 notebook 键（`tests/test_offline_notebook.py` 的 `CFG_KEYS` 守着）。
7. **G3 文案**（`run_loop._drive`）：`todo` 空时区分「本机段落已完成（it{N} ≥ end_it{M}）——要用新段请清空
   产物目录 / 等新包（或置 `CFG.force_pack`）」与「计划内的轮次都已在产物里——无事可做」。
8. **为什么决策住在 `offline_boot` 而不是 `run_loop`/`bundle`**（评审 F1，P0）：notebook 每次会话从
   **GitHub raw 刷新** `offline_boot.py`（+`tailscale_boot.py`），而 `remote.run_loop`/`remote.bundle` 来自
   **代码快照**（`ensure_code` 解出的 `code.zip`）——正是本机优先要保护的那份，可能很旧。把新开关写进
   `run_loop` 会在**本 plan 要救的那台机器上恰好不生效**（旧快照 = 老实现），而新 flag 传给旧
   `run_loop.main` 直接 `unrecognized arguments` 崩。
   **不变式（三份代码）**：同一进程里住着 ① raw 刷新的 `offline_boot.py` 与 ② 产物 `code.zip` 的 `remote.*`；
   跨这条边界只允许走两条腿都认的东西（argv / `--artifacts`），**不得**新增只有新代码认识的参数。

### 被否决（评审 F3/F4 的现场证据）

`bundle.preserve_existing` + `safe_extract_zip(skip_existing)` + `run_loop --force-pack` 一并砍掉：
(i) **跨层 version skew**（见上）；(ii) `import_bundle` 的逐件对账读的是**落点文件**（`bundle.py` 的
`f = root/name; raw = f.read_bytes()`），保留件按定义与包索引不符 ⇒ 第一次真实重跑就
`ProtocolError("… 与索引不符（搬运截断/损坏？）——拒收")`；(iii) `skip_existing` 只跳**已存在**件，
缺失件仍会被包写入（`code.zip`/`it-N/weights.json`）⇒「本机 manifest + 包里代码」混血，而失败现场是
worker 侧 `RetryableError("code_sha256 不匹配——传输损坏")`（白烧重试后死于误导性文案）。
另外：进度停滞时**自动清空 `dest` 重开**也被否决（那就是静默重跑）。

### §8 附则：`GET /offline/task-pack` 的新鲜度门

判据（`hub_server.task_pack_stale_reason`）：`sha256(tmp/<课>/weights.json)` ≠ 包内 `init_weights.json`
的 sha ⇒ 过期。**可满足性有据**：导出就是 `init_weights_path=args.out` 的**原字节**，而课程 `out` 恒为
`tmp/<课>/weights.json`（`_land_offline_round_extras` 推进的同一个文件）。
决策（`decide_task_pack`，纯函数）：`trigger`（触发控制台 action `exportTaskBundle` —— 控制台是唯一打包者，
hub 只触发；`remote/net_http.urlopen` 保证回环不走代理）⇒ 409「已触发重导，请稍后重试」；`throttled`
（同课 600s 窗内不重复触发）⇒ 409；`give_up`（连续触发上界 **2** 次仍过期）⇒ **照发旧包 + 一行告警**；
**判不了（索引不可读 / 课程还没权重）⇒ 照发**（本端点首先是文件递送）。
* **为什么必须有上界**：只要还有别的 worker 在回传，`weights.json` 就一直在动 ⇒ 判据是**移动靶**；
  没有上界时云机会被 409 卡到 `wait_pack_sec`（30 分钟）然后 `SystemExit` —— 那就从"白烧算力"变成
  "白烧整台机器"。到顶照发时起点仍由 resume 锚点兜（包与锚点互补，起点 = max(包 it, 锚点 it)）。
* **为什么不 hub 自己重打包**：那会造出与 `run_rl --export-bundle` 分叉的第二份打包逻辑（原注释"不造第二份
  真相"）。打包只有一个产地 = 控制台/python 侧；hub 只触发。
* **404 窗口是刻意的**：重导先作废旧包（挪进 `stale-packs/`），窗口内取包会 404 —— 这正是"不拿旧包"的代价；
  重导失败有 `restoreTaskBundle` 把旧包放回。
* **409 的正文要到云机日志**：`offline_boot.fetch_task_pack` 为 409 单开一支，把正文里的 `error` 打出来
  （原来只打一个 code，排障等于没有信息）。

### 证据（真代码 / 测试）

* `nn-training/remote/offline_boot.py`：`local_artifacts` / `describe_local_vs_pack` / `obtain_pack(optional=)` /
  `build_run_argv(local_first=)` / `ensure_code`（三源 + sha 门 + 修复）/ `ensure_ts_tree` / `_http_error_body`，
  以及 `run_one_course` 的判定表；顺带修掉一个既有崩溃：`live_backfeed=False`（或 hub 不可达）时 `tok_file`
  从未赋值却传进 `build_run_argv` ⇒ `NameError`（纯离线盘一直没跑到）。
* `nn-training/remote/run_loop.py`：`_drive` 的两种"空 todo"文案（G3）。`--bundle` 分支与 `bundle.py` **未改**。
* `nn-training/remote/hub_server.py`：`TASK_PACK_INDEX_NAME` / `CONSOLE_URL_ENV` / `TASK_PACK_STALE_THROTTLE_SEC` /
  `TASK_PACK_STALE_TRIGGER_LIMIT` / `task_pack_stale_reason` / `decide_task_pack` / `pack_index_part_sha` /
  `trigger_task_bundle_export` / `reset_task_pack_triggers` + `_get_task_pack` 的新鲜度门。
* 测试：`tests/test_offline_local_first.py`（20 条：判定表 / sha 门 / 修复 / 半截目录 / optional 取包 /
  argv 形状）· `tests/test_offline_task_pack.py` 新增 11 条（新鲜⇒逐字节一致 / 过期⇒409 且恰好触发一次 /
  节流 / 上界⇒照发 / 控制台不可达⇒降级 / 无权重⇒照发 / busy 视为成功）· `tests/test_run_loop.py` 新增 2 条
  （`--artifacts` 单用合法且不动本机 plan/manifest · G3 文案）。

## §40 opt blob 只装优化器状态、权重走内容寻址：每轮上行 −35.3%（plan/opt-blob-diet.plan.md，2026-09-24）

用户 2026-09-23 指令：把「同一份权重传两遍」这件事的精减写成 plan。实测（`tmp/x20-clutch`
it174–176 真产物 + `tmp/opt_wire_probe2.py` / `tmp/opt_blob_shape.py`）：上行 result v2 =
**760,658 B/轮**（slim 已开），其中 `weights_json`（gz）281,782（37.0%）· opt tar（gz 477,988）里的
`model.pt` **268,996（35.4%，gzip 压不动，1:1）** · 同 tar 的 `opt.pt` 208,783（27.4%）· JSON 头 ~1,100。
`model.pt` 与同一个 POST 里的 `weights_json` 是**同一份权重**。

### 决定

1. **`pack_opt_tar` 只打 `opt.pt`**（`model.pt` 从此不进 tar）——`opt.pt` 是每轮必变、跨轮续跑真正
   需要的唯一状态。红线（minimize-payload §1.3-1）：**不得量化 / 降质 / 裁剪**。
2. **权重走内容寻址的 `init` blob**：`BLOB_NAMES` 加 `init`，`sha = manifest.init_weights_fp`
   （**复用既有字段**——它的定义就是 `sha256_file(args.out)`，与内容寻址的要求逐字一致；加一个同义的
   `init_sha` 只会造第二份真相），raw = 该轮 `init_weights.json` 原样字节。**manifest 字段集不变**。
3. **worker 侧五源优先级**（`worker._resolve_weights`；`job_dir/init_weights.json` **只解析一次**）：
   W0 payload → W1 `blob_cache/<init_weights_fp>` → W2 `preloaded["blobs"]["init"]` →
   W3 `GET /jobs/{id}/blob?name=init` → W4 tar 里的 `model.pt`（旧形状兜底）。
   四源皆尽**且**无 `model.pt` ⇒ `ProtocolError`（**绝不**静默 warm-start）；瞬时失败（5xx/网络/字节
   sha 不符）⇒ `RetryableError`（释放租约重领重下；报成确定性失败 = `report_job_failure` 停腿）。
4. **「稳态零下行」靠一行新代码**：`run_job` 产物段把 `weights.json` 原始字节也写进
   `blob_cache/<sha256(raw)>`（`_cache_produced_weights`，与 opt tar 缓存同一手法）。下一轮 hub 的
   `init_weights_fp` = `sha256(args.out)` = 同一份字节 ⇒ 同会话 100% 命中。
   **缺它 = 每轮净亏 110 KB**（上行省 268,996 而下行多付 379,114）。opt 今天稳态命中的机制就是这个。
5. **顺序（最容易做错的一处）**：解析必须落在 **BC 的 early-return 之后**（BC 的 `init_weights_fp`
   是哨兵 `"bc"`）、`run_iter_rollout`（它的 `--weights init_weights.json` 相对 job 目录）与
   `ppo_engine.build_ppo`（arch 从权重读，缺文件会静默退回默认 64/8/128）**之前**。
6. **哨兵 sha**：`init_weights_fp ∈ {"", "bc"}`（BC job / 全新 run 首轮）⇒ **零 blob 请求**。
   节点侧索取 `init` 还要过 `manifest.slim` 闸（非 slim 臂的权重就在 payload 里 = W0）。
7. **升级顺序**：先停所有 worker → 升 hub → 升 worker；回退粒度 = **PR revert**（**禁半套**）。

### 账（三条腿）

| 腿 | 现状 | 改后 | 差 |
|---|---|---|---|
| 上行（每轮） | 760,658 | ~491,662 | **−268,996 B（−35.3%）** |
| 下行 opt blob（稳态命中） | 0 | 0 | 0 |
| 下行换机首次（miss） | 890,880 | 593,920 + 379,114 = 973,034 | +82,154 B（+9.2%，罕见事件） |
| blob_cache 每轮占用 | 890,880 | 973,034 | +9.2%（无 quota 是既有待拍板项） |

口径注（评审 F8）：`268,996` 是**单文件** `gzip(model.pt)`；真实 tar 级减量是
`gzip(整 tar)` 之差 **477,988 − 208,783 = 269,205**（差 209 B）。两者都在噪声内。

> **绝对值不是常数（2026-09-24 实测）**：`result_bytes` 随 `opt.pt` 的可压缩性漂——同为
> 590,451 B 的 `opt.pt`，it176 压到 208,697 而落地冒烟的 128 步新生 Adam 态只压到 489,055。
> 可判据的不变量 = 去掉 `model.pt` 成员的**差值 −262,790 B（本机反事实实测，−25.4%）**。
> 详见 plan/opt-blob-diet.plan.md §5.2。

### 被否决

① 在 worker 里为 `init` 另写一条下载路径（会漏掉 `bulk_sched` 的优先级/让路/重抽账）；
② hub 侧重打 tar（sha 漂移的老坑）；③ 用「是不是同一个 worker」当命中判据（要 hub 侧 inventory +
TTL + 谎报处理，且换机省不掉）；④ 新增 `init_sha` manifest 字段（第二份真相）；
⑤ 保留 `model.pt` 让旧 worker 不炸（= 没做，改由升级顺序替代）；⑥ 给 `_resolve_blob` 加 `cache`
开关（现有路径已在命中/下载后无条件 `_cache_blob`，init 复用即得）。

### 落地时测出来的四处与设计文档不同（已同步文档正文）

① `_resolve_weights` 四源皆尽时**返回 `none` 而不抛**（只有 restore 段知道 W4 在不在），
契约 = `(path | None, src, wire_bytes)`；拒绝（`ProtocolError`）与瞬时（`RetryableError`）都用**抛异常**
表达；`kind=iter/run` 的 rollout 用不上 W4，所以那两类 job 在解析后**立即**由 `run_job` 响亮拒绝。
② 节点侧索取 `init` 要 **`slim` + 64-hex 两道闸**：只靠 64-hex 时，`slim=False` 臂（发布端**不落**
init blob）会被要求补一个永远不存在的 blob ⇒ **428 死循环、job 永不完成**（`e2e/test_multi_course_single_hub_e2e.py`
抓到）。判据抽成纯函数 `worker_server.missing_blobs(manifest, sent, cached)`。
③ payload 携带权的判定门是 **`use_opt_blob`**，不是 `use_init_blob`：`use_opt_blob = slim AND 有 opt 字节`
⇒ 有 opt blob 就必然也有 init blob，两者等价；写成后者会把首轮/冷启动改成「不带」，多一处可漂的地方。
（改造前这条由 `rollout_spec ⇒ keep_init_weights=True` 顺手蔽着——**那份强制正是本项要取消的**。）
④ 产物缓存抽成 `worker._cache_produced_weights(blob_root, raw, log)`，让「产出的权重进 `blob_cache`」
这条不变式可被单测直接钉住（`run_job` 那一层要真 torch）。

### 判据

`nn-training/tests/test_opt_blob_shape.py`（15 用例：tar 形状 / 五源优先级 / 失败分类 / 哨兵 /
产物缓存闭环 / `missing_blobs` 两闸）· `test_remote_ppo.py`（blob 端点 `?name=init` 200 · 未知名 400 · 缺失 404（同一白名单路径） +
payload diet + slim=false 臂不落 init blob）· `test_remote_iter.py`（it≥2 不带 payload 权重 / 首轮带 /
kind=iter 发布端守卫 / opt-only tar 落位）· `remote/smoke_loopback.py`（第 2 轮起 `blob_hits ≥ 2`、
`weights_src == "cache"`、`weights_bytes == 0`、`blob_miss_bytes == 0`、回传 tar 里无 `model.pt`）·
`bash tools/githook/nn-python-gate.sh`（2298 passed）· `bun run check`（2144 pass / 0 fail）·
`bun run freeze:check`（God-AI 确定性签名不变）。

**E5 实测（2026-09-24，本机闭环 `smoke_loopback --rounds 2`）**：稳态轮 `blob_hits=2`（opt+init）·
`weights_src=cache` · `weights_bytes=0` · `blob_miss_bytes=0`（首轮 `weights_src=payload`）；
回传 tar 成员 = `{opt.pt}`，`model.pt` 不再出现。同产物反事实（`tmp/optdiet-e5-shape.py`，
用 `_ppo_save` 重造同一份权重）：旧形状 result **1,033,096** → 新形状 **770,306** =
**−262,790 B（−25.4%）**，new 侧与实测 `result_bytes=771,084` 逐字节吻合。

### 未决（需用户另裁，不是本项的缺口）

① `opt.pt` 的 Adam m/v 降精度（bf16 再 −115 KB、fp16 再 −180 KB；实测 `cos(m/√v) = 0.999998`）——
与本项互斥且是新数值臂（需 DECISIONS + 60-seed `hard` 配对基线）；② `blob_cache` 上限/清理
（全仓无 quota，本项使它每轮 +973 KB）；③ 状态回传降频（每 N 轮才落 hub，代价是换机耐久窗口变宽）；
④ `course_cache`（本项结论：**不需要它**——权重与 opt 的缓存命中同源同寿命，`blob_cache/<sha>` 已吃下）。

---
## §39 bulk 让路账「一次传输一行」：逐次打点 = 刷屏（2026-09-24）

现场（用户贴的 worker 日志）：一次 payload 下载里
`bulk 让路 X.Xs（控制面在途；单次预算 ≤ 5s）` 连打 **6 行**（4.0/1.5/1.0/1.5/1.0/0.5s），
把真正要看的两行（`blob opt 下载中 …` / `准备完成 …`）埋在中间。

**为什么逐次打点是纯噪声**：让路本来就是**分片间隙反复发生**的（`_read_body` 每分片调一次
`pace`），而每 job 的 wire 行里已经有 `yield=<n>` 的合计 ⇒ 单次让路那几行零信息增量。

**改法**：让路账记在**所属 slot**（= 一次传输）上（`BulkScheduler._yield_cur`），
`pause_if_needed` 只累加、不打印；`slot()` 退出时（含异常 / P2 被抢占退出）合并成一行：

```
bulk payload: 让路合计 9.5s / 6 次（控制面在途；单次预算 ≤ 5s）
```

`stats()` 的 `yield_count` / `yield_sec`（**步数**口径，wire 行在用）**不动** —— 只动日志。
判据：`tests/test_bulk_sched.py::test_yield_log_is_one_line_per_transfer`（同场景旧实现打 3 行 ⇒ 改前红）。

---
## §38 job 身份跨课程碰撞：幂等键纳入课程 + 归属唯一化 + 发布端守卫（2026-09-24）

用户 2026-09-24 报障「双课程并行，调度请求 409」。根因**不在传输层**，而在 **job 身份**：
`x20-dodge-l1` 与 `x20-dodge-l3` 发布出了**同一个 `job_id`**，而 hub 的 per-job 路由取
「第一个匹配」⇒ 两个训练轮读到**同一份结果**（静默污染，且自持循环）。
plan：`plan/job-identity-collision.plan.md`（含一次独立评审的 M1–M5 修订记录）。

### 现场五条实测

① **两门课发布了同一个 job_id**：`tmp/x20-dodge-{l1,l3}/remote-jobs/` 下三轮成对出现
`2bf293c29f33c561`(it1)、`d275349b46463d3f`(it2)、`8effb3be5adc8448`(it3)。

② **两份 manifest 只差课程身份**：`course_fp`/`corpus_fp`/`payload_sha256`/`course`/`reward_formula`
全不同，而 **`runId`/`it`/`init_weights_fp`/`data_fp` 逐字相同**。四个分量为什么全同：

- `runId` 是**进程级**（`rl/queue.py::RUN_ID`）——单 hub 多课程（`--serve` 共享 trainer）后两门课同源；
- `init_weights_fp`：两门课同一份 warm-start；
- `data_fp`：只哈希 `(shard 目录名, wver, stage, seed)`，而 per-stage seed 与课程无关；
- `it`：同一轮。

③ **结果/租约在两份副本间交替落位**（哪一门课的目录先存在，`course_of` 就先记住谁）。

④ **409 = `held`**：peek 列出 l3 的那份 → claim 被首匹配路由到 **l1** 的那份 → 它此刻
`_claimed` 尚在、租约活着、`expected_epoch` 相同 ⇒ `_claim_locked` 命中 `held`（`:655-661`）
⇒ `_post_claim` 回 409。优先级视图不拦是因为 `_facts_locked(exclude_worker=自己)` 把**自己的**
痕迹排除了。**不是** frozen、**不是** stale_holder/避让、**不是** hub 崩/隧道 —— worker 那句
「hub 异常，请检查 hub 进程与隧道」只是 `_warn_non_200` 对**所有非 200** 的兜底文案。

⑤ **比 409 严重**：`verify_and_land` 的三重校验比的正是幂等键的那三个分量（两边天然相等 ⇒
静默通过），而**训练侧的读路径也走同一个首匹配**（`wait_job` → `GET /jobs/{id}/result` →
`_job_dir` → `course_of`）⇒ **两个 trainer 拿到同一份 `result.json`**，各自落进自己的
`args.out`（两课 `ppo_ckpt_remote.tar` / `weights.json` 逐字节相同）。
且这是**自持循环**：污染让两课 `init_weights_fp` 持续相同 ⇒ 下一轮 key 又撞，不会自愈。

### 根因（一句话）

`idempotency_key = (runId, it, init_weights_fp, data_fp)` **不含课程**，而归属解析
（`course_of`）在**读写两条路径**上都是「取第一个匹配」—— id 撞了以后，路由**没有能力**分辨，
也不**吭声**。

### 决定（三层，各自可独立回退）

**L1 幂等键纳入课程身份**（`protocol.idempotency_key`）：

```python
idempotency_key = (runId, course_fp, it, init_weights_fp, data_fp)
```

用 `course_fp`（课程 jsonc 的 sha256）而不是课程名/hub 课程键：它是 manifest **必填**字段，
且在同一进程内**恒定**（课程字节装载时冻结 —— `rl/config.py` 落 `args.course_frozen_bytes`，
发布腿用的正是冻结字节）⇒ mid-run 热加载编辑不换 job id、不产生孤儿。
⚠ 它是**文件血缘**哈希，不是语料身份（那是 `corpus_fp`），也不等于 hub 的课程键
（`<discover-root>/<目录名>`，= 课程文件 stem）；**只做键分量，不做路由键**。

**L1' 归属唯一化**（`hub_server.course_of`）：≥2 门课都认识同一个 jid ⇒ 返回 `None`
（404 `unknown`）+ 打一行「身份歧义」+ 进 `/admin/queue` 的 `ambiguous_jids`；
歧义**绝不**进 `_locate_cache`（一次歧义会变成永久归属）。
原「取第一个匹配」正是事故的静默通道 —— 改成「唯一才认」后，代价是**响亮失败**
（job 作用域入口 404、训练轮等到超时），收益是**绝不污染**。方向是刻意选的。

**L3 发布端守卫**（`protocol.collision_rows` + `hub_client.publish_job`）：
判据 = 「**完整幂等键**相同 且 落在**别的 store**」⇒ `HubClientError` 拒发，
位置在 `job_id` 算完之后、**任何写盘之前**（连 `.extra_tmp` 都不建）。
`course_fp` 进键之后正常发布撞不出来 ⇒ 这是**哨兵**：手写 manifest / 回灌历史 job /
跨 hub 搬目录等旁路一旦造出同身份，必须在发布那一刻响亮。
★ 判据**不是**「四分量相同且 `course_fp` 不同」——那正是本事故的配置，而它在 L1 之后是
**合法**的（两门课各有各的 id）；拿它当判据会把已经修好的场景全部拒掉。

**L4 原因可读**（`worker._warn_non_200` + hub 侧 `_log_reject`）：409 文案从「hub 异常」
改成「调度面拒绝（被持有/冻结/归属歧义）——**不是** hub 故障」，并把 hub 响应体里的
`error`（`claim 被拒: held (…)`）与 jid 打进同一行；hub 侧对每次 claim/result 拒绝留一行
`job=… course=… worker=… status=… reason=…`（按 `(jid,status)` 60s 节流）。

### 判据（测试）

| 位置 | 用例 | 压什么 |
|---|---|---|
| `tests/test_job_identity_collision.py` | `test_job_id_differs_across_courses_same_key_components` | L1：异课程 ⇒ 异 id |
| 同上 | `test_old_key_formula_still_collides`（测试内**独立重实现**旧 4 分量公式） | 把 bug 焊成回归锚 |
| 同上 | `test_collision_rows_flags_only_other_store` | 守卫判据（异 store 命中；同 store/异键不命中） |
| 同上 | `test_publish_refuses_cross_store_identity` | 拒发且**不落任何文件、不记账本** |
| 同上 | `test_publish_allows_same_components_different_course_fp` | **事故配置必须放行**（守卫不误伤） |
| 同上 | `test_publish_twice_in_same_store_is_still_idempotent` | 幂等语义不变 |
| 同上 | `test_ambiguous_job_id_is_refused_not_guessed` / `test_queue_state_reports_ambiguous_jids` / `test_single_course_legacy_semantics_unchanged` | L1'（拒答 / 可见 / 单课程旧语义逐字不变） |
| 同上 | `test_claim_reject_reason_is_logged` / `test_warn_non_200_still_calls_5xx_a_hub_fault` | L4（409 ≠ hub 异常；5xx 仍是） |
| `e2e/test_multi_course_single_hub_e2e.py` | 夹具改成「两课刻意同 `runId`/`it`/`init_weights_fp`/`data_fp`，只差 `course_fp`」+ 假 PPO 把 `course_fp` 烙进权重 | 端到端：id 不同 · 目录集合不相交 · **两课权重两两不同**（串课回归） |

### 成本实测（E0，真语料）

真 `tmp/`：131 个目录 / 82 份 job `manifest.json`。`collision_rows` 全量 `json.loads`
= **399ms/次**（manifest 含内联 `opt_init`/`course` 全文，很大）⇒ 加一道**快速闸**：
「job 目录名 **就是** `job_id`」（`course_of` 的归属证据用的同一条不变量）⇒ 只有同名目录
才可能是冲突，**不必读任何文件**。加闸后 **14ms/次**（一次 glob + 至多 1 次读取）。
发布是每轮一次，故可接受；超阈值再谈索引。

### 被否决 / 不做

- **带 `course` 参数的逐端点迁移（一稿的 L2）**：要给 17 个 job 作用域端点加形参（GET
  `payload|ts_code|code|blob|status|result|resume|bc-metrics` + POST
  `heartbeat|claim|start|ready|abandon|release|fail|result|epoch`）外加 worker 全链路与 push 腿。
  L1 落地后 id 已按 store 唯一、L1' 又堵死了唯一的静默通道 ⇒ 边际收益只剩「显式意图」，
  不值得那 17 处半套迁移的第二事实源风险。重开条件：出现新的 id 碰撞源，或需要「同 id 跨课程共存」。
- **把 `course_fp` 纳入 `data_fp`**：能堵「同局不同课程」，但会改所有 `data_fp` ⇒ 波及 D12 验收 /
  `iter_expected_data_fp` / 离线包与锚点，需要单独 plan。
- **用 hub 课程键（目录名/stem）当键分量**：更贴路由命名空间，但要新增 manifest 字段 +
  第二个派生点（`Path(job_root).parent.name`），违反「判据唯一」；且 `course_fp` 已能分开本事故。
- **`job_seed` 纳入课程**：现在两门课同 seed ⇒ 同一 minibatch 顺序（数据不同，不构成 bug）；
  改它 = 换数值，与修复分开裁。
- **`verify_and_land` 加比 `payload_sha256`**：三重校验是 D12 契约，动它要过 D12 评审。

### 运维（迁移）

现场**换 runId 重跑**（`--serve` 重启即换）即可 —— 旧 `remote-jobs/*` 由轮转/清理自然清掉，
本次污染的 it1–it3 本来就不可用。顺手清 `tmp/<课>/{it*,remote-jobs,training_log.jsonl}`
可让 `ambiguous_jids` 立刻归零（不清也能跑，只是观测面会亮着旧残影）。
**注意**：旧代码的 trainer + 新 hub 这个窗口里，旧的碰撞 job 会因归属歧义一律 404
（`wait_job` 把它当 pending 等到超时才响亮报错，25min）——不污染，但别把它当新故障。

---
## §37 缺 bun 在**零下载**时就拒单：能力自检前移到 payload/code/ts_code 之前（2026-09-24）

用户 2026-09-24 报障链的云机一端：节点上没 bun 时，worker 仍然把
**payload 296KB/2.5s + code.zip 1.58MB/6.1s + ts_code.zip 1.55MB/4.5s** 三件全下完（合计
**3.42MB / 12.1s**，worker 日志自己的 `合计=` 行），才在 `run_iter_rollout` 里由
`resolve_bun` 发现没 bun ⇒ `REJECTED`。全是白传。

### 为什么能提前（不变量）

`run_job` 拿到的 `job["manifest"]` 在**认领响应里就完整可用**（`normalize_manifest` 之后
`rollout.bun` 必有值，缺省 `"bun"`，见 `protocol.py::ROLLOUT_SPEC_DEFAULTS`）；payload / code /
ts_code **三件都是之后才下载的**。所以判定不需要任何字节。

### 决定（plan/train-mode-hot-switch.plan.md L3.2）

```python
# 结果复用块（_result.json 命中 ⇒ 纯重传）之后、payload 下载之前
if str(manifest["kind"]) in ("iter", "run") and not echo:
    resolve_bun(str((manifest.get("rollout") or {}).get("bun") or ""))
```

四条判据都必须写进测试（`nn-training/tests/test_worker_bun_precheck.py`）：

| # | 判据 | 为什么 |
|---|---|---|
| ① | `kind in (iter, run)` ∧ 无 bun ⇒ `ProtocolError` **且零下载** | 这就是本项的全部价值；实测口径 **0 字节 / <0.1s** |
| ② | `echo=True` 豁免 | echo 只验传输链，kind 分叉里整个跳过 rollout |
| ③ | `kind=ppo` 豁免 | 只有「节点自己跑 rollout」才需要 bun（本机采样 + 云机只算 PPO 不需要） |
| ④ | **位置**：`_result.json` 命中时即使没 bun 也照旧重传 | 纯重传不需要 bun；放在复用块之前会误拒「已算完、只差回传」的 job |

### 不动分类（只是前移）

失败类型与原来**一模一样**（`resolve_bun` 抛 `ProtocolError`）：`worker_loop` 的
`except ProtocolError` 分支本来就写着「确定性拒绝（commit 不符/模式不符/**节点能力缺失如 bun 装不上**）
不重试」⇒ hub 落终局 failed、训练侧 `JobFailedError` 停腿。本项只把同一判定从 3.42MB 之后
挪到 0 字节处，**不新建**异常类、不改重试策略。

### 另一条腿（同一报障的配置端）

「切回在线后仍然派 `kind=run`」的根因在**控制台那颗开关只翻了半场**（本机配置没改）⇒
`docs/nn/console.md §15`。两件合起来才是完整的修法：配置端不再派 `run`；真派了 `run` 而无 bun 时
也不再白传 3.42MB。

---
## §36 hub 的课程发现：写动作也该触发一次真扫（回灌跑在发现之前 = 静默失配）（2026-09-23）

用户 2026-09-23 报障：新开的三个离线课里，`x20-demo-mix` 在 hub 里**不是** offline（而另两个
是），于是控制台把那门课当在线课渲染（课程区「在训」、顶栏没有离线态、操作列给「切离线」），
而操作员以为自己开的是离线课。

### 根因（hub 日志实锤）：发现是**顺带**跑在读路径上的，而回灌跑在它之前

```
[hub-server] courses=0 [] … listening on 0.0.0.0:8787 courses=[]      ← hub 起来时课程表是空的
[20:28:46] POST /admin/courses?course=x20-…&mode=offline × 9 → 400    ← 控制台回灌九条意图，全被拒
[20:28:47] GET /admin/queue → 200                                     ← 这次**读**才触发 discover()
[hub-server] discovered courses: x20-demo-mix, x20-firstkill, x20-terminal
```

两处缺陷叠在一起：

1. `set_mode` 只查 `self._stores`（已登记课程），而 `discover()` 只在 `claim_next` / `queue_state`
   这些**读**路径里被顺带调用（且有 `DISCOVER_SCAN_MIN_SEC` 间隔闸）⇒ 谁先读谁决定课程表；
   回灌跑在第一次读之前 ⇒ **刚起 hub 的那一轮回灌必然全 400**。
2. 控制台那侧的回灌是**单发**（带重试的 `pushHubMode` 只有「开课」那条路在用）⇒ 三个课各自靠
   开课时的 3×2s 重试去赌发现时机，**恰有一门输掉**（最后一次重试 20:29:46、发现也 20:29:46）。

### 修法

* **hub（本次）**：`POST /admin/courses` 在「课不在表里」时按需 `discover(force=True)`
  （`force` 跳过 `DISCOVER_SCAN_MIN_SEC` 间隔闸）再试一次。指名一门课的**写**动作有资格要求一次真扫；
  模式非法不扫盘（直接 400），真不存在的课仍 400 且不改变课程表。
* **控制台（本次）**：`restoreCourseModes` 每课有界重试（3×2s），**只对「需要合法 course」这一类**
  错误重试（hub 连不上不重试——白等只让「起 hub」变慢）。全文 → `docs/nn/console.md §14`。

### 门禁

`nn-training`：`tests/test_multi_course_hub.py::test_mode_post_discovers_the_course_on_demand`
（刚建目录、间隔闸未过期时，一条 mode POST 必须接住；`ghost` 课仍 400 且不改变课程表；非法模式不扫盘）；
`dashboard`：`tests/course-mode.test.ts` 两例（重试成功 / 连不上不重试）。

### 未决 / 口径

* hub 那份课程表**仍是唯一权威**（它是事实源，控制台那份是**意图**）；本次只让写动作能触发发现，
  不顺带改「谁决定在训」的判据。
* 已失配的那一门课**不会自愈**：意图只在「起 hub」那一步回灌 ⇒ 手工口 = 在该行点一次
  「切离线/恢复在线」（幂等），或点「hubServer」让它重灌全部意图；矩阵/pill 的「意图未生效」
  徽标就是为了让这种失配**看得见**（`docs/nn/console.md §14`）。

---

## §35 云腿评估读数的 summary 也要并（`eval_summary` 是控制台/门判唯一认的键）（2026-09-23）

用户 2026-09-23 实测：`x20-demo-mix` 云腿 **it50–110、每 5 轮 400 局、`node=cloud`** 的读数
全在课程账本里（`wver` 逐轮不同、轨迹连贯），**控制台却一栏都不显示**。

### 根因：两条腿都只并逐局行，而读方只认 summary

`rl/eval_local.merge_eval_rows`（人工导入腿）与 `remote/hub_server.merge_eval_rows`（实时补传腿）
都只并 `event:"eval"` 逐局行，把 `event:"eval_summary"` **丢掉**；而读方一律只建 summary：

| 读方 | 建条目/行的条件 |
|---|---|
| `dashboard/src/server/iters.ts::readEvalSummaries`（指标表 eval 列、配对基准、`davgTicks` 等衍生列） | `r.event === 'eval_summary'` |
| `iters.ts::readLatestEvalGames`（eval 弹窗） | 先找 summary 的最大 iter，再取该 `(iter,wver)` 的逐局行 |
| `stack/kickstart-receipt.ts`（开课回执基线对照） | 同 |
| `rl/gate_check.py::read_trend_rows`（门判据趋势） | 同 |

丢 summary 的原始依据是「summary 由课程侧按合并后的台账重算」。**对纯云腿这条退路不存在**
——全程没有本地循环（下一轮 PPO 在云上跑），没人替它算 ⇒ 数据在账本里、读数在屏幕上为零。

### 修法：云机自己那份 summary 一并并进去（单调）

* `rl/eval_local`：新增 `eval_summary_key` / `read_eval_summary_rows` / `append_eval_summaries`；
  `merge_eval_rows` 改为返回 `(逐局行数, summary 行数)`。
* **单调规则**：只在该 `(iter, wver)` 尚无 summary、或新来的 `games` **更多**时追加
  （断点续跑「先部分 200 局、后补齐 400 局」要能升级）；后到的旧 summary 一律不覆盖新的。
  读方「同 iter 多条取最后一条」⇒ 有效读数就是补齐后的那一份；重投/重导幂等。
* **补传体补上 summary**：`OfflineDeliverer._eval_rows_for` 原只发逐局行（上界 400 条）⇒ 现在
  连本轮的 summary 行一起发（每个 `(iter,wver)` 至多一行，不占上界）。这正是既有「评估落账后
  重投一次」那条路（`on_round_done` → `submit_eval_round`）该带的东西。
* **报告/日志分两类计数**：导入回执新增 `eval_summaries` 键，hub 端点打
  `eval rows +N / summary +M`——少一类时能一眼看出（否则又是「看着导入了、实际表里没数」）。

### 为什么不改成「读方从逐局行合成 summary」

读方合成 = 在两处（TS 视图、Python 门）各写一套聚合口径，与 `settle_eval_summary` 三足鼎立
——口径一分为二正是 §10/§12 治的那个病。把云机**同一份实现**（它自己就调
`settle_eval_summary`）算出的 summary 搬回账本，才是「同一份读数」。

**验证**：`tests/test_eval_ledger_merge.py`（单调/幂等/坏行不抛/两类计数）、
`tests/test_offline_resume_anchor.py`（端到端补传：summary 落账 + 重投幂等）、
`tests/test_offline_eval_wiring.py`（体里带 summary + 导入腿并两类）、
`tests/test_offline_eval_cloud.py`（`_eval_rows_for` 只带本轮、不带 B/C 与别轮）、
`tests/test_deliver_zip.py`（包里的 summary 落到课程账本）。

**存量数据**：已并过但无 summary 的旧轮（如那份 it50–110）**只能重导一遍产物 zip** 才能补上
（重导对逐局行幂等、对 summary 是新增）；数据目录已删则不可补（本轮修复只对未来生效）。

---
## §34 回传轮的课程侧三处落位：交付镜像 / 活动权重 / 权重归档（2026-09-23）

用户 2026-09-23 实测（`x20-demo-mix` seg-2，it49–124）：产物**都在盘上**
（`<traj>/remote-jobs/offline/<run>/it-NNN/{weights,opt,row}` —— 77 轮，含 opt），但

| # | 现象 | 机制 | 影响 |
|---|---|---|---|
| ① | seg-2 不在 `deliver/` | `deliver/` 只有人工导入写 | 两段分居两棵树，找东西要翻两处 |
| ② | `<traj>/weights.json` 停在 `11ac6161…` | 推进它的只有本机循环的「发布→等结果→落位」；回传腿只写自己的 `offline/` 树 | 该指纹 = 这个 run 的 **it0**（init）⇒ 段期间「活动权重」是假的（`eval_replays_once` 兜底、本机续跑、控制台显示都读它） |
| ③ | `nn-training/weights/<课>/` 零轮 | 归档只在**本机循环**每轮 `_export_weights → backup_weights` | 控制台 evalA 的 iter 选择器只扫那个目录 ⇒ **回传段的任何一轮都选不到**（唯一真正的「没落盘」） |

### 落位（`hub_server._land_offline_round_extras`，新回传轮自动做）

全部 **best-effort**：回传的主价值是权重到岸（调用方已三校验地落定），这三处失败只记一行，
不把一轮合法回传判负。

* **①交付镜像** `<traj>/deliver/<run>/it-NNN/{weights,opt,row}` —— 与导入腿同路径同文件名
  ⇒ 两腿同构（幂等：已存在不重写）。
* **②活动权重** `<traj>/weights.json` —— 判据是**课程账本**：`_ledger_has_newer_iter(it)` 看有没有
  `iteration.iter` / `offline_artifact.it` **大于 `it`**。为什么是账本：它**两条腿都写**（本机循环
  每轮写 `iteration`，回传腿经 `_land_round_metrics` 也写）⇒ 「课程已知的最新轮」是两腿合用的
  单一判据，不需要额外 sidecar，也不会出现「另一条腿推进了我不知道」。
  * 只认那两种事件：`job_pending`/`job_cancelled` 也带 `it`，但它们说的是**某台机器上的一个 job**
    （发了还没落权重）⇒ 拿它们当判据会让「发了又取消的更大 it」永久挡住推进。
  * 账本**不存在**（新课程）⇒ 判「没有更新轮」（否则新课第一轮永远不推进）；存在却读不到 ⇒
    保守不写。
* **③归档** `<归档根>/<课>/<prefix>.it<N>.<时间戳>.json`（复用 `rl.archive.backup_weights`）。
  `prefix`/`dir` 从 `curricula/<课>.jsonc` 读（与 `rl/config.py`、dashboard 同一份单一事实来源），
  缺配置才用「课名 + 归档根/<课>」兜底 —— 两种缺省（那边按 mode 前缀）混用会把不同课程的
  归档倒进同一目录。同一轮**已有归档即跳过**（否则补做/重跑堆积同轮副本）。

### 测试隔离（必须有，否则污染控制台）

归档根的开关做成 env：`BCITY_WEIGHTS_ARCHIVE_ROOT`（`_weights_archive_root()` **调用时读**）。
为什么不是只留可 patch 的模块常量：`e2e/test_offline_training_e2e.py` 拉的是**真 hub 子进程**，
patch 传不进去。三个碰补传的测试文件（`test_multi_course_hub` / `test_offline_deliver` /
那个 e2e）都加了 autouse fixture 指到 `tmp_path` —— 否则工装用例会往真
`nn-training/weights/` 撒 `<课>.it<N>.<时间戳>.json`，而控制台的 evalA 选择器会把它们当成
**真训练轮次**列出来。

### 历史轮补做：`remote/backfill_offline.py`（**尚未跑**）

新代码只对**未来**的轮生效，而实测那 77 轮已经落过了 ⇒ 一次性工具：每个三件齐全的已落地轮调
**同一个** `_land_offline_round_extras`（不复制第二份落位逻辑），按 it 升序（老段账本还没行时
靠「最后处理的是最大 it」）。幂等：第二遍 `archived=0`、盘上归档名集合不变。

```
bun dashboard/src/launch/cli.ts --script remote/backfill_offline.py -- \\
    --traj-root tmp --course x20-demo-mix
```

⚠ **跑它会推进 `weights.json` 到段尾（it124）** ⇒ 下次导出任务包的段起点从它来。若打算用新规则
（`T=49152` + mb 对齐，见 tpu-perf §8/§10）从 it0 重跑，就别跑（或只补镜像/归档）。**用户 2026-09-23
裁决：先不跑，只提交代码。**

**磁盘代价**：镜像会再存一份（每轮 ≈ weights 380KB + opt 890KB ≈ 1.3MB；一课 300 轮 ≈ 390MB）。
提交 `99a945d0`。

---
## §33 离线课运维四件套：hub 取包重试上限 / Kaggle 跳过 tailscale / 进度行每分钟一句 / 底部「中途取回」格（2026-09-23）

> 编号说明：`§32` 是本文件的「决策正文归档」节，进度节从 **§33** 起（新条目置顶、号大）。
> 四条都是用户 2026-09-23 的口径，全部落在 `remote/offline_boot.py` + `ipynb/battle.offline.ipynb`。

**① hub 取包重试上限 10 轮 → 转「等上传」模式**（`CFG.hub_tries`，0=不限）

- 旧行为：hub 候选每 `poll_sec`（缺省 15s）探活 + 取包，直到 `wait_pack_sec`（缺省 **30 分钟**）到点。
  hub 还没导出包时，这就是 30 分钟 × 上百轮的无意义请求与同样多的日志行，而云机会话是按时间计费的。
- 现行为：连试 `hub_tries`（缺省 `DEFAULT_HUB_TRIES` = 10）轮都没拿到 ⇒ **不再碰 hub**，
  转入「等上传」模式并响亮说明（去控制台「导出任务包」→ 上传；或写进 `CFG['task_zip']`；
  想让它自己再试就重跑本 cell）。上传那条路**一直在轮**（同一个循环），切模式不牺牲「人随手传上来」这条。
- **否决**：① 无上限重试（旧行为）——把预算花在不会成功的请求上；② 到点后靠人重跑整个 cell
  （人不在云机前时没人重跑）；③ 把上限合进 `wait_pack_sec`（语义混淆：一个是「等多久」，一个是「试几次」）。
  ④ 上限用 `cfg.get("hub_tries") or DEFAULT` 兜底 —— **本批实测踩到**：`0`（不限轮数）会被
  falsy-zero 吞成 10，判据必须 `is None`（`test_hub_tries_zero_means_no_cap` 盯着）。
- 落地：`_try_hubs()`（一轮只真取一次）+ `obtain_pack()` 的 `hub_parked`；
  测试 `test_hub_fetch_gives_up_after_the_cap_and_switches_to_upload`。
- 「起隧道」与「取包」**分开判**：上限只管取包；tailscale 仍只在「所有候选都够不着」时试一次。

**② Kaggle 上一律不起 tailscale（跳过全部 tailscale 步骤）**

- 背景：2026-09-16/17 两次事故（`DECISIONS §2026-09-16-kaggle-kernel-no-torch` /
  `§2026-09-17-kaggle-cred-before-proxy`）都出在「Kaggle 容器里做网络改造」这条路上：平台既不给
  NET_ADMIN 也换不得网络命名空间，而 userspace 引导会把进程代理改写成只转发 Tailscale IP
  ⇒ 平台 Secrets（公网 HTTPS）够不着、凭据读成空串 ⇒ `/code` 401 ⇒ 会话终结。
- 现行为：`is_kaggle()` 为真时 —— `ensure_tailscale()` **短路在函数第一行**（不是调用点：将来新增的
  调用点也绕不过），`hub_candidates()` **不给 tailnet 候选**（没隧道必然不通，留着只是每轮白等一个 8s 探活超时）。
- **否决**：① 只在调用点判（多一个调用点就漏一次）；② 让 `tailscale_boot.ensure()` 自己判
  （它必须在**动网络之前**就知道，而那时它已经改了代理环境）；③ 保留 tailnet 候选「试试看」。
- 落地：`is_kaggle()`（判据与 `remote/artifacts.py::is_kaggle` 同源 —— 本模块不能 import remote，
  所以抄一份并由 `test_is_kaggle_is_a_superset_of_the_artifacts_module` 对账「只许多认、不许漏认」）/
  `ensure_tailscale()` / `hub_candidates()`；测试 `test_kaggle_skips_tailscale_entirely`（连
  `_load_tailscale_boot` 都不许被调到）、`test_kaggle_drops_the_tailnet_candidate`、`test_colab_is_not_kaggle`。
  说明书（notebook markdown）同步写明「Kaggle 上 `HUB_IP` / `TS_AUTHKEY` 会被忽略」。

**③ 进度行按时间节流：每分钟一句**

- 用户看到的那行是 `[run] kind=iter rollout: 30/224 games settled (18s)`。旧口径**按局数**（每 10 局一句）：
  8 并发一轮 328 局 3 分钟 = 33 行；在线节点 220 并发时是每秒数行。这条线的成本只与**墙钟**有关 ⇒ 阀也拿墙钟量。
- 现行为：`game_watch.progress_due(done, total, now, last_at)`（`PROGRESS_LOG_SEC = 60.0`），
  **最后一句恒打**（那是「这一轮结束」的唯一落点）。两条 rollout 腿共用一份：`remote/iter_rollout.py`
  （节点/云机）与 `rl/queue_local.py`（本机）；`ROLLOUT_LOG_EVERY`（按局数）随之退役 ——
  `test_rollout_progress_paths_use_the_shared_cadence` 扫源码防它回来。
- **否决**：① 保留按局数（快机器上等于没节流）；② 干脆删掉进度行（长轮次就完全没有「还活着」的信号）。

**④ 底部新增「中途取回」格：跑到一半也能把产物交回控制台**

- 场景：云机会话到点/被回收，而产物已经在盘上（`LATEST.zip` 每次 checkpoint 刷新；干净停机还写全量
  `artifacts.zip`）。缺的只是一个**能交回控制台的名字**：导入侧 `remote/deliver_zip.py` 用文件名
  `deliver-<课>.zip` **对账课程**（拿 A 课的权重去评 B 课，读数看起来完全正常，只有这道对账能拦）。
- 现行为：cell 调 `offline_boot.package_partial(cfg, log)` —— 逐门课找 `<work>/run` 下**最能代表
  当前进度**的包（`artifacts.zip` 优先，其次 `LATEST.zip`），复制成 `deliver-<课>.zip` 放进
  `download_dir`（Kaggle=`/kaggle/working`、Colab=`/content`），Colab 上直接触发 `files.download()`；
  只读 + 复制，不动产物、不训练、不碰网络；没有产物时**响亮说明是哪个目录空**（返回空列表，不抛）。
- **否决**：① 让用户自己解压 `LATEST.zip` 再改名（改名 = 把课程对账让给运气）；② 训练中途后台线程自动打包
  （单内核被训练 cell 占着跑不了第二格，且「什么时候想拿走」是人的判断）；③ 在 cell 里自己推导工作目录/包名
  （工作目录有两层规则——多课程再套一层课程名——抄一份必然漂；所以那部分留在
  `course_work_dir` / `package_partial`，cell 只做引导与本机下载）；④ 复用 `package_deliverable`
  （它只认 `artifacts.zip`，而跑到一半时那个文件还不存在）。
- 落地：`package_partial()` / `_partial_last_it()` / `PARTIAL_CANDIDATES`；notebook 第三格
  （`test_notebook_has_a_mid_run_package_cell` 钉「恰一格 + 不含引导调用 + 走 runtime」）；
  测试 `test_package_partial_*`（4 例，含用**真** `ArtifactStore` 造盘上那份 `LATEST.zip`）。

**验证与边界**

- 门禁：`bash tools/githook/nn-python-gate.sh` 绿（ruff + mypy + pytest）· 根 `bun run check` 绿。
- 未验证（诚实边界）：**没有真 Kaggle 会话**跑过 —— 判据与环境变量由单测对账，而「真机上能不能起隧道」
  本来就不再需要验（我们不再去起）；进度行的实际行数也没在真云机轮次里数过。
- 回退粒度：`CFG.hub_tries`（0 = 旧行为）· 进度行改回按局数（改一处，两条腿同时变）·
  尾部那格删掉不影响训练与交付（它是只读便利件）。

---
## §31 异步结果回传：把 `out` 从关键路径上摘下来（plan/transfer-scheduling P2.5，2026-09-22）

**为什么做**：双课程单 worker（最典型场景）下 A 的 rollout 与 B 的 PPO 交错填空 ⇒ **算力已满**，
再快只能把传输从关键路径上摘掉。现场账 `rollout/in/ppo/out = 45/15/50/25 s`（用户口径）：
云侧只感知 `in/ppo/out`，而 **`out` 25s 比 `in` 15s 还大**、改造前又全程压在关键路径上——
P2 预取只治 `in`，`out` 无人管。两者正交：命中让 `in`→0，`out` 不动。

**做了什么**

| 面 | 落地 |
|---|---|
| 新模块 | `remote/result_upload.py`：`ResultUploader`（有界队列 `depth=2` + 专用线程 + `drain`/`close` + 落定回调）；`sync` 模式逐字回旧行为 |
| worker 接线 | `submit` 入队即返回 → 主循环立刻领下一份；`_result_settled` 在**落定那一刻**才 `_wire_flush(wall_end=…)`；job 级 `uploaded` 标志决定 finally 收不收；整段主循环包 `try/finally` 收尾 drain；`--result-upload {async,sync}`（缺省 async） |
| 记账 | `_wire_flush(wall_end=)`：`wall` = **关键路径**（claim → 结果就绪），新增 `overlap=` 字段；`out` 秒数照报不抹 | 
| 读方 | `tools/wire_report.py`：`overlap=` 可选组（旧日志当 0）+ `out_overlap_sec` + 渲染「不占关键路径 / 仍压在关键路径上」 |
| 安全网 | 队列满 / 入队超时 / 上传器已收尾 ⇒ **退回同步**（绝不丢）；失败落带 jid 的 `★` 行 + 收尾汇总；`--once` 先 drain 再判成败（H8 不退让） |

**验证**：`tests/test_async_result_upload.py`（18 例）——核心是 **A/B 次序**（async 下第二份 job
开算早于第一份回传结束；sync 基线必须晚于，否则用例是空转），另有「退出必 drain」（用事件闸住上传，
确定性可判，不赌调度）「队列满退同步不丢」「失败响亮」「每 job 恰好一行阶段账」「取消路径照旧收账」。
**8 刀改坏必红全红**（submit 恒同步 / close 不等落定 / 队列满丢结果 / 失败不响亮 / 忘传 wall_end /
finally 无条件收账 / --once 不等落定 / 读方不认 overlap）。nn 门禁 ruff+mypy+pytest
**2051 passed / 3 skipped**；根 `bun run check` 2122 例 0 fail。

**未做（明确入口）**：`out` 的**减重**（minimize-payload：让那 25s 本身变小）——与 P2.5 正交，
两者都做才是全量收益。**P0.5 的实机数字**仍待现场（见 §29/§30）。

---

---

## §30 竞速退役 + 控制台同批 + push 腿：P3 落地（plan/transfer-scheduling，2026-09-22）

**起因**：§28/§29 把取活换到 `peek → priority → claim`、把传输拆成 bulk 单通道之后，**旧**的
竞速广播判定（§2026-09-17）仍与新的优先级表并存——两套调度语义同时在跑。本批一次性删干净
（plan §2.8「不留兼容短语」），并把 push 腿拉到同一张表上。

**改了什么（判据看函数名）**
- `remote/protocol.py`：删 `RACE_MODE_*` / `race_decision` / `parse_hub_scope` / `HUB_SCOPE_HEADER`；
  `RACE_WORKER_WINDOW_SEC` → **`WORKER_SEEN_WINDOW_SEC`**（窗口本身还有用：登记表）。
- `remote/hub_server.py`：删 `race_mode/race_active/set_race_mode/race_state/clear_workers`、
  `GET|POST /admin/race`、`--race`、`/jobs/next`；`claim_next`/`claim(race=)` 的 race 分支删除。
  **保留** `claim(mode="backup")` 机制与 `_backup_authorized`（删判定不删机制，R1-1）。
- `remote/worker.py`：`poll_job` 整函数删除（取活只剩 `acquire_job`）；`--poll` 多值退化；
  `/jobs/next` 的发送点删除。
- `remote/push_dispatch.py`（R1-7）：同一张优先级表（`_priority_of_course`）、**1 主 + N 备份**
  （`backups_per_course` 缺省 1；备份无租约、槽位键 `f"{jid}#b{n}"`）、派发后 `hub.start_job`
  打 `computing_at`（否则掉队救援在 push 腿永久沉默）、landed 时 `_cancel_others` 推取消帧
  （推不到 ⇒ 让它跑完 + 409 丢弃，**不算失败**）。
- `dashboard/src/**`（R2-1，**同批**否则 hub 起不来）：去 `--race` 透传 / `RaceMode` / `raceActive`
  渲染，删 `tests/hub-server-race-arg.test.ts`；连注释里也不再出现退役面名。
- 测试侧：`tests/helpers/hub_poll.py`（新面拼回**与旧面逐字段同形**的返回值，10 个登录点机械替换，
  不各写十份会各自漂的实现）；`tests/helpers/push_worker.py` + `tests/conftest.py::worker_factory`
  （假 push worker 搬进 helper/conftest——跨测试文件 `from tests.test_a import fixture` 会撞
  ruff `F811`，因为夹具名遮蔽模块级 import）。

**门禁（常驻）**：`test_priority_schedule.py::test_race_judgment_has_no_production_path`（判定零命中）·
`test_jobs_next_retired.py`（真 HTTP 404 + 生产零命中）· `test_scope_unrelated_race.py`（R1-10 负向：
eval 侧长尾竞速**没有**被误删）· `test_push_priority_dispatch.py`（6）· `test_pause_budget.py`（4）。

**改坏必红自查（五刀）**：① 生产里加回 `/jobs/next` 兼容别名；② 竞速判定改名加回；③ 备份副本改走
租约；④ push 备份上限改 2；⑤ `peek` 不再登记 worker（避让链端到端用例）——五刀全红。

**踩到的坑**
- **跨测试文件 import 夹具 = ruff F811**：pytest 允许 `from tests.test_a import worker_factory`，
  但参数名遮蔽模块级 import，ruff 判「redefinition of unused」。正解是把夹具放进 `conftest.py`
  （无需 import 即全局可见）+ 实现搬 `tests/helpers/`，而不是加 `noqa`。
- **`_POLL_WARN_AT` 随 `poll_job` 一起被删**：它是 `_warn_non_200` 的节流表，与取活面无关——
  「整函数删除」时要清点的是**函数体引用的模块级名字**，不是函数名本身。
- **注释也算「删除清单命中」**：控制台 7 处 prose 注释还在写 `/jobs/next`（讲「取活面不看课程」的
  历史理由）。删除清单的 grep 若把控制台算进范围，注释必须一起改词——否则「零命中」是自欺。

**一处改名（R2-10d 对齐）**：让路预算 `BULK_YIELD_BUDGET_SEC` → **`PAUSE_BUDGET_SEC`**（与 plan
`docs/nn/legacy.md` §6 #5 同名；§29 里写的是旧名），安全裕度断言从 `test_bulk_sched.py` 拆到 plan 点名的
`tests/test_pause_budget.py`，并改为引用 hub 的 `SEND_TIMEOUT_SEC` 常量（原先写死 60.0）。

**未完成（不是已交付）**：P0.5 的**实机数字**（阶段账 `in/out/ppo/wall` 占比、`p90` 取消延迟）
——需一次云-hub-LAN 会话跑 `tools/wire_report.py`；在那之前 P2 预取的收益结论不成立，可用
`--prefetch-depth 0` 关闭。

---

---

## §29 传输 QoS + 阶段账 + 软持有预取：P0 / P0.5（仪器）/ P2 落地（plan/transfer-scheduling，2026-09-22）

**起因**：§28 只交付了 pull 线的取活换面；本批补上「传输那半场」——bulk 单通道、控制面旁路、
预取把下载叠到上一个 job 的 PPO 上，以及判 P2 盈亏所必需的阶段账。

**改了什么（判据看函数名）**
- `remote/bulk_sched.py`（新）：`BulkScheduler.slot`（**唯一** bulk 入口，任意并发下 `inflight≤1`）、
  `pace`（分片间隙让路 + P2 抢占检查）、`pause_if_needed`（单次预算 `BULK_YIELD_BUDGET_SEC=5s`，
  上界 = min(worker `BODY_IDLE_TIMEOUT_SEC`=45s, hub `SEND_TIMEOUT_SEC`=60s)——**两个都写进注释**）、
  `control_path`（按**路径**分流：`/payload` `/result` `/code` `/ts_code` `/blob` 才是 bulk）、
  `BulkPreemptError`（P2 被挤走 = 丢半截重下，**不**算失败）。
- `remote/worker.py`：`_request` 按 `control_path` 标记控制面（控制面**不进**队列；`urllib` 每请求
  新连接 = 独立 socket）；`_read_body(pace=…)` 每分片查一次让路；`post_result` 占 **P1** 槽
  （不被抢断、退避睡眠在槽外）；`download_*(bulk_prio=…)` 缺省 P1、预取传 P2。
- **阶段账（P0.5 仪器）**：`_wire_start`（claim 起算 wall）+ `_wire_time(jid,"ppo",sec)` +
  `_wire_flush` 尾部 `phases in=…s out=…s ppo=…s other=…s wall=…s`；每 job 行另加
  `wait=<queue_wait_sec>/yield=<yield_count>/p0_p95=<p0_rt_ms>`。
- `remote/prefetch.py`（新）：`PrefetchStore`（无租约软持有；`work_dir/prefetch/<jid>/` 只存引用与
  摘要，字节同时落内容寻址 `blob_cache/<sha>`；预算 64MB 超限按 `updated_at` 丢最旧；存取双向 sha
  校验）+ `pick_candidates`（纯函数）。`worker.py::_prefetch_fill` 后台填充（候选 = `peek_jobs`，
  下载 = **P2**，与 `run_job` 重叠），命中经**现成的** `run_job(preloaded=…)` 零下载开算；
  `--prefetch-depth`（0 = 关）；`prefetch/` 进 `prune_job_dirs` 豁免名单。
- `tools/wire_report.py`：阶段占比表（`parse_phases` / `summarize_phases` / `render_phases`：
  总量占比 + 每 job p50/p90；`--json` 同步）。

**关键取舍（DECISIONS §2026-09-22-goalnn-bulk-single-channel · 全文 → 本文件 §32）**：让路**预算有界**（超 5s 会被 worker
自己的 45s 空闲判停/hub 60s 分片写超时判成停滞，而两个上界必须同时看）；P1 **不可抢断**（POST 大 body
没有安全 Range，抢断 = 整份白传）而 P2 **必须可弃**；预取**只走** `download_payload` 一条路（omit 协商
落在那里，另写整包 GET 会把 minimize-payload 的瘦身吹回去）；预取失败**永不**转
`ProtocolError`/`report_job_failure`。

**门禁**：`test_bulk_sched.py`（11）· `test_control_plane_bypass.py`（2，真 HTTP：bulk 在途时控制面
往返 <1s **且**量到并行）· `test_soft_hold_prefetch.py`（12）· `test_wire_report.py` 阶段账段（+5，
含**写方/读方格式一致性**用例——防「阶段表静默变空」）。七刀改坏必红已自查（门禁有效性）。
全量门禁：ruff + mypy + pytest `2050 passed / 3 skipped`（27s）。

**未做（下一步入口）**
1. **P0.5 的实机数字**：仪器与聚合口就绪，但本机没有真机 `wire` 日志可算 ⇒ 下一次云-hub-LAN 会话跑
   `python nn-training/tools/wire_report.py <worker 日志>`，把阶段表贴到 plan §1.1 下方。
   **在那之前 P2 的收益结论不成立**（机制可关：`--prefetch-depth 0`）。
2. **P3 竞速退役**（`/jobs/next` / race 判定 / `poll_job` / `hub_scope` / `--race` / `/admin/race`
   + 控制台同批改造 + push 腿 R1-7）：**本批未动**，两套仍并存且全绿。爆炸半径已清点（生产
   `hub_server.py` 83 处、`protocol.py` 15、`worker.py` 9；测试 `test_race_broadcast.py` 57 整文件删、
   `test_multi_course_hub.py` 50、`test_remote_ppo.py` 10、`test_poison_freeze.py` 9 + e2e 4 文件；
   控制台 `--race` argv 必须同批，否则 hub argparse 未识别即退出）⇒ 建议单开一批。

---

## §28 传输∥PPO 优先级调度面（pull 线取活换面）：P1/P1.5/P2 落地（plan/transfer-scheduling，2026-09-22）

**起因（用户指令）**：处理 `transfer-scheduling.review-R2.md` 的评审意见 → 自主开发/测试/审查/提交。
plan 的完整语义是「软持有预取 + 优先级调度 + landed 取消 + 传输 QoS + 取代 race broadcast」，
本次落地的是**其中依赖顺序最先可自洽交付的一段**：pull 线的**取活换面**。

**改了什么（判据看函数名）**
- `remote/worker.py`：`acquire_job`（取活三件套 = `peek_jobs` → `request_priority` → `claim_job`）、
  `job_started` / `job_ready` / `abandon_job` / `job_status` / `start_cancel_watcher`；`worker_loop`
  改走 `acquire_job`；`post_result(mode=…)` 把 backup 副本的 403 单列为**丢弃**；`run_job` 接
  `should_cancel` / `on_ppo_start`，取消点 = `ppo_update(on_epoch_done=…)` 的 epoch 边界 + 开算前预检。
- `remote/hub_server.py`：`GET /jobs/peek` 与 `POST /jobs/{priority,claim,start,ready,abandon}`；
  `_JobStore.claim_outcome/_claim_locked`（唯一临界区）；`_claimed` / `_computing` / `_ready` /
  `_epoch` / `_backup_authorized`；`scheduling_facts` / `priority_for` / `start_job` / `set_ready` /
  `abandon_job`；`note_worker` 换源到 peek/priority 且 `hub_scope` 照旧上报（避让链输入不丢）。
- `remote/protocol.py`：`job_priority`（§1.4 表纯函数：landed→none、ready→low、computing 超阈值→high、
  未超→medium、claim→medium、无人→highest）、`JobCancelledError`、`CLAIM_MODE_*` / `PRIORITY_*` /
  `STRAGGLER_SEC=180` / `JOB_CANCEL_POLL_SEC=1.5`。

**关键取舍（详见 DECISIONS §2026-09-22-goalnn-transfer-scheduling-pull · 全文 → 本文件 §32）**：备份副本**不 pop 原租约**
（只加 `_backup_authorized` 放行回传）——pop 会让原 worker 硬死后无租约可过期 ⇒ 毒包熔断失明；
`highest` 的唯一性闸与 claim 同临界区（epoch 只在 claim 一处校验）；掉队阈值只认 `computing_at`；
取消只认 `landed` 且必须是独立异常（不可落进 `ProtocolError`/`RetryableError`）。

**门禁**：`nn-training/tests/test_priority_schedule.py` 17 例（纯函数五分支 / 两把时钟 / 备份租约 /
abandon 零 reclaim / highest 唯一性闸 / peek 无副作用 / 真实 HTTP 端到端 / 403 丢弃 / 取活三件套 /
取消环 / 源码级接线断言）；迁移 3 个既有文件到 `acquire_job`（`test_remote_hotswap.py` /
`test_worker_offline_cap.py` / `e2e/test_worker_queue.py`）。四刀改坏必红已自查（门禁有效性）。

**未做（下一步入口）**：P0 传输 QoS（控制面旁路 + bulk 单通道）、P0.5 基线（`T_in/T_out/T_ppo` 与
GPU 空转占比）、P2 的**预取半场**（`PrefetchQueue` + omit 协商）、P3 竞速退役（`/jobs/next` /
race 判定 / `poll_job` + 控制台同批改造 + push 腿 R1-7）。**本批不动** `/jobs/next`、`poll_job`
与 race 判定（两套并存，`test_race_broadcast.py` 仍绿），故 2026-09-17 的 race 条目尚未 supersede。

**门禁口径**：nn 侧 `bash tools/githook/nn-py-safe.sh -m pytest -q tests/ e2e/` —— 全绿，唯一红是
`tests/test_serve_wiring.py::test_course_args_match_run_rl_echo_config`（**既有环境失败**：本机
`nn-training/rl-config.json` 是未入库的机器本地配置，`course_args` 吃到机器侧覆盖而 oracle 不会；
已用「把三个文件还原成 HEAD 版本」在同一工作树上复现同一红，证明与本批无关）。

---

---

## §27 补传 400 的真因 / 导入后指标表看得见 / 引导模块每次刷新（2026-09-22）

用户 2026-09-22 问的四件事里剩下三件的收口（第一件＝单局看门狗，`docs/nn/runtime-opt.md` §8）。

### ① 「it1 产物回传失败，且失败后再也不回传」——真因在**取错了账本行**

现场：`补传 it1 体被拒（HTTP 400: 账本行与权重不符：行记 bff93df1… 实得 5f099e55…）——
本会话停用补传`。两个独立缺陷叠在一起：

* **取错行**：`OfflineDeliverer._row_for(it)` 从 `metrics.jsonl` 里返回**第一条**同 `it` 的行。
  而 `ArtifactStore.checkpoint` 是**追写**的：续跑/重开同一目录时同一个 `it` 会再落一行 ⇒
  拿上一会话的行去配这一会话的权重 ⇒ 指纹必然不符。现在：**新→旧扫描 + 只认 `weights_fp`
  与本轮字节相符的行**（与控制台「同 iter 取最后一条 = 最新对账结果」同一口径）；
  配不上就**宁可不发 row**（一个指错轮次的行比缺行危险得多），并记一行 WARN 把两个前缀并排。
* **一次内容拒收停用整条腿**：旧口径 `400/413 → 本会话停用补传` 是照抄 401/403 的反应。
  但两者代价不同：鉴权错每轮重试会把本 IP 封掉（hub D9 闭锁），内容错只毁**那一轮**。
  现在：400/413/422 → 记进会话内 `_rejected`（每个 it 只跳一次）→ **其余轮次照推**；
  401/403 仍然停用整条腿。`_post_artifact` 的返回值从 bool 改成 `"ok"/"skip"/"stop"`，
  三态各自对应「记已投递 / 跳过这轮继续 / 这一拍到此为止」——把「一轮的代价」与
  「整条腿的代价」在类型上分开。

### ② 导入产物后控制台「各轮指标表」一行不显——账本没并

那张表只读 `tmp/<课程>/training_log.jsonl` 的 `iteration` 事件（
`dashboard/src/server/api/state-view.ts` → `iters.readIterMetrics`）。导入只落了产物目录
（权重/优化器/`metrics.jsonl`）⇒ 权重可评估、末轮能评，但**表是空的**（用户实测）。
现在导入器把包内 `metrics.jsonl` 的逐轮行搬进课程账本（`deliver_zip._merge_carried_metric_rows`）：
只搬控制台真读的字段（`winRate/outcomes/samples/ticks/expectedGames/rollout_sec/ppo_sec/
steps/chunks/kl/policy/value/entropy/mean_ret`），带 `import_run_id` 来源标记，按 `it` 幂等
（账本已有一行就不写第二遍），失败只记一笔（导入的主价值是权重可评估）。
`expectedGames ← report.games` 是必要的：控制台用 `ticks / expectedGames` 算平均每局时长，
缺了它那一列恒为 0（看起来像「跑了零 tick」）。`dim_means`/`score_mean` 产物行里没有 ⇒
那些列**留空，不编数字**。控制台回执也加了一句「逐轮指标 +N 行」——最贵的失败是
「导进去了但表是空的」，它必须在上传后的第一眼就被看见。

### ③ 多课程写法没生效——是**引导模块缓存**在跑旧版

现场：`CFG.course` 给了三个课名，日志里却拼出 `['x20-demo-mix', ...]` 这种目录名，
且去找一个不存在的任务包。多课程解析本身早已随 `05691e72` 入库（`offline_boot.courses_of`
认字符串/列表/逗号分隔，串行跑完，`course_work_dir(multi=True)` 给每门课再套一层目录）；
真因是 notebook 的 `_load_boot` 旧策略「**有缓存先用缓存**」：同一个 kernel 里跑过一次旧
代码之后，之后每次 Run 都在跑那份旧的，而日志只打 branch（同分支看不出新旧）。
现在：每次会话先拉最新、`Path.replace` **原子替换**，拉不到才回落到缓存（并响亮说明用的是
上一份），并把**实际加载那份**的 `sha12` 打进日志。`tests/test_offline_notebook.py` 钉住这四点
（旧策略的 `_branch.txt` 不得回潮）。

#### 复审（2026-09-22 晚）：为什么用户导入后还是空的 + 补上两列

用户报「导进去了但表还是空的」。实测现场（`tmp/x20-demo-mix/`）：

* 那次导入跑在 **19:28**（`deliver-import.log` 的时间戳），而本节的合并代码写在 **20:54**
  ——那次 `DELIVER_IMPORT_JSON` 里**根本没有 `metric_rows` 键**（旧代码的指纹）；
* 包内**有** `metrics.jsonl`（49 行）：不是打包漏了；
* 课程账本里当时 `iteration` 行 = **0** ⇒ 表当然一行不显。

用当前代码重跑同一条导入命令：`metric_rows: 49`，账本 49 行，并用**控制台自己的读方**
（`dashboard/src/server/iters.ts::readIterMetrics`）验证：49 行、winRate/kl/samples/
rolloutSec/ppoSec 均真实。⇒ 「重复导入」是支持的（同 run_id ⇒ `rmtree` + 原子改名重解，
新包胜；账本按 it 幂等合并），且不需要清目录重来。

同时把两列补齐：

* `expectedGames ← report.games`（否则控制台 `ticks/expectedGames` 恒 0）；
* **产物行现在带 `dimMeans`/`scoreStats`**（`remote/artifacts.metrics_row`，additive，~17 个
  float/轮）：本机账本行一直是带的（`rl/events` 读 `report.dimMeans/scoreStats`），而产物行
  只留摘要 ⇒ 导入的那条腿在 kills/accuracy/loot 与 score 列上恒空，同一张表两条腿列不可比。
  现在产物行带上、导入器按分层路径搬进账本。**旧包没有这两块** ⇒ 那几列仍留空（不写 0：
  「缺数据」与「真的零击杀」不是一回事）。

顺带坐实了 §27① 的根因：那个包的 `metrics.jsonl` 里**有两条 `it:1`**（`bff93df1…` ts=…2424
与 `5f099e55…` ts=…3323）—— 与 hub 400 里那句「行记 `bff93df1`… 实得 `5f099e55`…」逐字对应：
旧 `_row_for` 取的就是**第一条**（上一会话的）行。

### 验证

`nn-python-gate` 2128 passed（连跑绿）、根 `bun run check` 绿、`dashboard` typecheck+test
1103 passed 绿。测试新增：`tests/test_deliver_zip.py` 三条（导入 ⇒ 账本 / 字段搬运（含
`expectedGames`）/ it0 与坏行不进账本）；`tests/test_offline_deliver.py` 三条
（`test_rejected_round_is_skipped_but_the_rest_keep_flowing` /
`test_row_is_picked_by_matching_bytes_not_by_first_same_iter` /
`test_row_with_mismatching_fingerprint_is_dropped_not_sent`）；`tests/test_offline_notebook.py`
一条（引导模块每次刷新 + 实际加载那份的 sha12）。

---

---

## §26 离线课「点训练 = 重打包」：旧包先作废，导出失败再恢复（2026-09-22）

用户口径：**「重启离线课程时，需要把最新代码重新打进任务包，因为代码可能已经发生了变化」**，
并且明确**时机 = 用户点击「训练」那一刻**（`openCourse`），**不是** worker 取任务时实时打包
——hub 的 `/offline/task-pack` 永远只递盘上那一刻的文件，不现场构造（无隐藏的第二份真相）。

* **触发点**：`course-lifecycle.openCourse`（离线模式）→ `launchTaskBundleExport(c)`。停课 →
  重新开课即重打一份；课已开着再点「训练」也会重打（开课无「已开课即拒」门）。
* **为什么必须先把旧包作废**：包是**代码快照**（`code.zip`/`ts_code.zip`/`commit` + `code_sha256`），
  而导出要跑几分钟。若只在后台重导、旧包仍躺在 `tmp/<课>/task-<课>.zip`，云机会在导出窗口里
  探到**旧代码的包**并拿它跑完整段——且看起来完全正常。
* **作废 = 挪走不是删除**：`tmp/<课>/stale-packs/task-<课>.zip.stale-<时间戳>`；此后
  `/offline/task-pack` 404，而云机 `obtain_pack` 的等包循环本就按「404 → 稍后重试」处理
  （默认 30 分钟）⇒ 它等新包，不退回旧代码。作废排在 `exportGuard` **之后**（拒启 = 零副作用，
  与课程生命周期的既有约定一致）。
* **作废的反面必须有人管**：导出启动失败 / 子进程中途死掉 → 盘上就没包了，而旧包是此刻唯一
  还能跑的东西。两道恢复：启动 throw 时立刻 `restoreTaskBundle`；进程退出后 `watchExportExit`
  兜底 `restorePackIfMissing`（线上路径没新包 ⇒ 把**最新**归档搬回来，并往 `export-bundle.log`
  追一行 `[作废兜底]`）。
* **回执必须说出来**：开课/导出的 detail 里明写「旧包已作废 → 导出完成前云机取不到包（404），
  它会等新包——这是预期行为，不是故障」，否则「云机拉不到包」会被当成事故排查。
* 测试：`dashboard/tests/server-api-task-bundle.test.ts`（作废挪走不丢件 / 幂等 / 无包不报错 /
  失败恢复不覆盖新包）。

---

---

## §25 离线整段五件套：云机按计划采够样本 / 回传与 PPO 并行 / 云上 A 层 eval（与 PPO 并行）/ 多课程串行 / 断点续跑锚点（2026-09-22）

用户指令五条（原文见下），一次做完并把「同口径」做成**构造性质**（共用同一份函数）
而不是靠人比对。

| # | 指令 | 落地 |
|---|---|---|
| 1 | 云端按 `target_transitions` 采够样本，与本地集群 rollout 一致 | `rl/volume_waves.volume_block/initial_wave_pairs` 抽成共享纯函数；计划带 `volume` 块，`pairs_for` 走它重放；训练侧 `per_stage_quota` 随逐轮 manifest 传递 |
| 2 | 回传权重/opt/指标与 PPO **并行**，失败不打断 PPO | `offline_deliver` 后台单写者线程（`submit_round/submit_final/close`）+ `run_loop` 三出口有界 flush；同步模式保留（`background=False`） |
| 3 | notebook 增加「是否在云机跑 eval」 | `CFG.eval_on_cloud`（+`eval_slots`/`eval_game_timeout_sec`）→ `--eval-on-cloud`；语料/行/结算全部取自 in-loop 同一份实现 |
| 3b | 云机 eval 与下一轮 PPO **并行**（eval 吃 CPU，不该阻塞 PPO） | `remote/offline_eval.CloudEvalRunner`（后台线程 + 单飞 + 有界交接 + 段末有界 `drain`） |
| 4 | `CFG.course` 支持多个离线课程名，云机串行逐个完成 | `offline_boot.courses_of`（字符串/列表/逗号通吃，按序串行，中途失败停在那里并列出剩余）；`run_one_course` 逐课一层工作目录 |
| 5 | 断点续跑：回传或人工导入的权重/opt/指标，再领任务时交给云机续跑 | hub `GET /offline/resume`（+`/offline/resume/blob`）选**最新同轮齐全**的轮次；云机 `fetch_resume` → `run_loop --resume-dir` → `apply_resume_overlay` 采纳 |

### 「同口径」在哪几处是**同一份代码**

* 语料：`rl/eval_local.a_eval_seed_list`（双轨锚点 50 + 当轮轮转 50 也在内）——`remote/offline_eval.eval_pairs` 只是 `[ (s, sd) for s in stages for sd in a_eval_seed_list(it, n) ]`；
* 行 schema：`rl/eval_local.eval_row` 是**唯一**的行构造点（in-loop 派发器 `record()` 里的 60 行字典已删、改调它）；
* summary：`settle_eval_summary`（含双轨 `anchor_wr/rotor_wr`、技能子指标、掉落三列、`nodes` 分布）；
* `wver`：权重**文件字节的 sha256**（`ArtifactStore.sha256_file` ≡ `dist_common.weights_fingerprint`）⇒ 云机评的 W(it) 与本地/节点评的同一 W(it) 在账本里同 wver，可直接配对。

云机局的 `node` 字段写 `"cloud"`（与 `local`/节点 id 分开，summary 的 `nodes` 里一眼可辨）。

### 并行语义（本次最容易被后人改回去的一处）

`CloudEvalRunner`：`submit(it)` **立刻返回**（后台线程跑局）、同一时刻只评一轮（上一轮还在飞时
有界等 120s 交接，仍不空闲就**跳过本轮**而不是排队——排队的 eval 只会越落越远）；段末 `drain`
给在飞的局 600s 有界时间落账（超时只记 WARN：已落的逐局行有效）。三个出口（complete/noop/failed）
都调 `_close_eval`，且**先收评估再 finalize**——否则 artifacts.zip 里少掉刚评的那一段。
回归钉在 `tests/test_offline_eval_cloud.py::test_submit_does_not_block_ppo`（量的是提交耗时 < 0.5s，
谁把它改回同步这个用例立刻红）。

### 读数回程（三条，都必要）

1. **artifacts zip**：`ArtifactStore.finalize` 把 `eval_log.jsonl` 打进包（原来只打 plan/manifest/
   readme/state/metrics/it-*——云上白评一轮的那种漏）；
2. **人工导入**：`remote.deliver_zip` 导入时把包里的 `eval_log.jsonl` 并进课程账本
   （`<traj>/<课>/eval_log.jsonl`，按 `(iter,wver,stage,seed)` 去重）——**含 summary**：
   2026-09-23 之前只并逐局行、把 summary 丢掉，而控制台的表/弹窗/开课回执与门判据
   只认 summary（「课程侧重算」对纯云腿不成立）⇒ 整段读数上不了屏，见 **§35**；
3. **实时补传**：`_post_artifact` 的体里带 `eval_rows`（只本轮的、无 `source` 的逐局行，上界 400 条；
   **另带本轮 summary 一行**，见 §35），
   hub 侧 `_HubQueue.merge_eval_rows` 并进课程账本 ⇒ 段内就能在板子上看到读数。
   ⚠ **时序陷阱**（并行带来的顺序后果）：产物 POST 发生在落盘之后而评估还在飞 ⇒ 第一次投递
   时这一轮的 `eval_rows` 还不存在（若只做这一条，实时路径会**永不**带上读数——看着实现了、
   实际零命中）。处置：`CloudEvalRunner` 的 `on_round_done` 钩子在评估落账后调
   `OfflineDeliverer.submit_eval_round(it)` 把这**已投递的轮次重投一次**（hub 对重复投递幂等
   但仍并账 eval 行）——重投排在真积压之后，探活失败则退回队里下次再试。

### 断点续跑的锚点选法（用户口径：必须同轮齐全，否则退到更早轮）

* hub 的两个来源：`<job_root>/offline/<run>/it-NNN/`（自回传）与 `<traj>/<课>/deliver/<run>/it-NNN/`
  （控制台「导入产物」）；三件（`weights.json`/`opt.tar`/`row.json`）缺一就**不认**这一轮，从更大的
  it 往下退；同一 it 两个来源取目录 mtime 更新的那份。缺 opt 只是 Adam 归零、缺 row 只是曲线少一点
  ——两者都「看起来能跑」，所以判据卡在选轮这一步（`tests/test_offline_resume_anchor.py`）。
* 云机侧：`fetch_resume` 三件拿不齐就**整个锚点作废**（半套锚点比没有更危险）；`apply_resume_overlay`
  只采纳比产物当前 `last_it` 更新的锚点，指纹不符即拒（传输损坏不得进产物目录），同轮同名幂等。
* 端点：`GET /offline/resume?course=X` 的 `resume: null` 是**正常应答**（云机要能区分「hub 说没有」
  与「端点不可用」）；blob 端点只服务当前锚点 + 文件名白名单（不是通用文件服务）。

### 别的

* 一个真坑（ruff 抓的）：多课程重构后 `build_run_argv` 里的 `course` 变量已删但引用还在
  （F821）——`--hub-course` 会带空串/直接炸；同处还修了 `RUF034` 的恒假三元。
* `remote/offline_eval` 参与 `sys.path` 时的 stdlib `queue` 遮蔽风险：本模块**不用** `queue`，
  用 `deque`+`Event` 手写单飞（`rl/queue.py` 会遮蔽 stdlib，历史上已踩过一次）。
* 测试：`tests/test_offline_eval_cloud.py`（口径/执行/并行/采纳）、`tests/test_offline_resume_anchor.py`
  （hub 选轮 + 端点 + 补传并账）、`tests/test_offline_eval_wiring.py`（装配/收线/argv/拉取/导入合并/多课程）。
  门禁：`nn-python-gate`（ruff+mypy+pytest 2065 用例）39s 绿、`bun run check` 38s 绿。
* **未做**（下一手可接）：云机 eval 的读数不进 `dist-agent-meta.jsonl`（采样机健康表不含云机腿）；
  云机 eval 用的是云机自己的 CPU（不是节点池）——「云上的 eval 与 in-loop 的节点集群版差多少」
  还没有同权重的对照实测（口径相同，算力不同）。

---

---

## §24 教训：毒包熔断 + worker 崩溃响亮回传 + claim 可观测 + 发布端自检（accident.plan §4.1/4.2/4.3/4.5/4.6，2026-09-21）

**背景**：C-0 it58（job `43a4eb01cf9fe35c`）的 payload 被 hub served **约 40 次**（每 5 分钟
一次，对齐认领 TTL 300s），每次 worker 都在同一处 `BadZipFile` 炸掉、零回传，训练侧只有
3×1800s 超时——**三方合谋烧 3.5 小时，零告警**。§4.0/4.4 已修「炸的那一处」（解包判别先
tar 后 zip）；本轮补的是「**炸了要有人知道**」那一层。

| 项 | 落地 |
|---|---|
| §4.1 熔断 | hub `_JobStore` 新增 `_reclaims`（认领后**零回传**计数）与 `_frozen`（冻结记录），阈值 `FREEZE_AFTER_RECLAIMS = 3`；冻结即移出可领取池 + 一行响亮告警（job/课程/最后认领者/次数）；`/status` 报 `frozen`、`/result` 报 410 + `fail_kind=PoisonFrozen` ⇒ 训练侧**立刻**带原因停腿；人工解冻 `POST /admin/unfreeze?job_id=` |
| §4.2 崩溃响亮 | `remote/worker.py::job_body_error(phase, e)`：restore（model/opt、kickstart ref）与 grad（load_episodes/chunk/ppo_update）段的崩溃按**内容决定性**归 `ProtocolError` ⇒ 走**既有** `report_job_failure → JobFailedError` 链（带 traceback 摘要）；OOM / `OSError` / `MemoryError` 仍按瞬态 `raise` 回去重领 |
| §4.3 claim 日志 | 每次 claim 一行（job/课程/worker/lease/`reclaims=` 非零才带），本次事故现场靠 `payload served ×40` 的 cadence 反推，太贵 |
| 观测面 | `/admin/queue` 每课加 `frozen` 块（谁被冻、冻在几次）；冻结告警一次性（`consume_freeze_announcement`，不会每次轮询重喊） |

**为什么熔断是「独立第二状态」而不是复用失败标记**：`hub_client.py` 规定「重发同 job_id
清失败标记」（重发即重试）。冻结若住失败标记字段，**重发当场解冻**，本次事故照烧 3.5 小时。
两者正交：重发不清冻结（`publish` 不碰 `_frozen`），解冻只走人工入口。

**为什么计数点必须收在一个入口（本轮第二个坑）**：「租约过期」有三个观测入口——`claim()`、
`lease_worker()`、`claimable_job_ids()` 的资格判定。只在 `claim()` 里贴计数会被控制台每秒轮询
的 `/admin/queue`（走 `lease_worker`）抢在前面回收，计数**恒为 0、熔断永不触发**。处置：三者统一
走 `_collect_expired_locked()`（持锁调用），并把这条写成用例（`test_admin_queue_observation_path_also_counts`）。
这是本仓「判据要有唯一入口」的第三次同一教训。

**为什么阈值是 3 而不是 1**：合法重试真实存在（worker 挂一次、换台机器接着跑），3 给了一轮
自然愈合窗口（≈15 分钟），又不放过 3.5 小时的静默空转；拿不准的崩溃归瞬态——**熔断覆盖
「未知死法」，判错方向有兜底，而错钉终局没有**。

**§4.5/4.6 同轮落地（发布端把毒包拦在上传前）**：自检与重打循环长在 `hub_client.pack_payload_zip`
（唯一发布侧打包入口 ⇒ 不存在“某个调用者忘了自检”；重发走同一函数，“重发复用 payload 同样先过此链”
自动成立）。① **reader 全链自检**：调 `protocol.unpack_payload` **本体**（内部调 `_extract_archive`）
再断言解包出的 shard 目录与打包输入**逐名相等**——“能打开”会被本次毒包穿透（它 tar 打开完全正常）；
② **旧判别反向探测**：`zipfile.is_zipfile`（故意用旧启发式，旧 worker 发版前仍在跑），抽成
`_old_heuristic_misjudges` 并写死注释“它存在的意义就是模拟旧 worker”；③ 不通过 ⇒ 重打，但重打**必须
显式扰动**（写 `payload.perturb` = `repack-{n}`）：打包确定性 ⇒ 不扰动就逐字节相同、旧判在同一份
字节上永远为真、闭环永不收敛（这是评审判死原“换 job_id 重打”方案的根据）；④ 上限
`PAYLOAD_SELFCHECK_ATTEMPTS=3`，触顶 `ProtocolError` **响亮放弃、不发布**。

**新发现（夹具是真字节）**：把一整个真 zip 接在真 tar.xz 之后 ⇒ `is_zipfile` 为 **True**
（`_EndRecData` 只验 EOCD 的注释长度自洽，**不**验中央目录），而 `tarfile` 照旧完整打开——
这就是本次事故字节形状的最小重现，也解释了“完好的 tar.xz 为什么会被判成 zip”。
**成本实测（不是推算）**：16 核、240 shard / 480 文件 / 1.77MB ⇒ pack 0.67s、自检 **1.18s**
（+1.2s/轮发布，几乎全在“建 480 个文件”）、探测 ≈0.000s；事故代价 3.5h。
**坑**：试解目录**不能**用 `tempfile.TemporaryDirectory`（回收走 `shutil.rmtree`，本沙箱删除 shim
会 `raise SystemExit` ⇒ `ignore_errors` 拦不住、当场打死线程）——改 job 目录下 `.selfcheck/` +
`rmtree_best_effort`。

**验收（盘上）**：`tests/test_poison_freeze.py`（9：达阈冻结 / 主动 release 不算 / 已结算不算 /
`/admin/queue` 路径也计数 / 告警只喊一次 / **重发不清冻结 + 解冻回池** / 训练侧立刻 `JobFailedError` /
解冻端点 400·404·409 / claim 与冻结各一行日志）；`tests/test_payload_selfcheck.py`（7：真字节分叉 /
探测确实用旧启发式 / 好包一次通过且无扰动标记 / 产物不符·垃圾·缺额外文件三种不通过 / 误判 ⇒ 扰动重打 /
自检失败也重打 / 触顶响亮放弃 + 三次字节各不相同）；`tests/test_job_body_crash.py`（5：内容决定性 →
`ProtocolError`、瞬态原样放回、restore/grad 两段确实被包、已判定 `ProtocolError` 原样上抛）；
顺带修掉一个**既有 flake**：`test_hub_push_dispatch.py` 用例依赖 mtime 判定，Windows 系统时钟
节拍（~15.6ms）内两次写会拿到相同 mtime ⇒ 全量套件下偶发假红；改为显式 `os.utime` 递增。
门禁：`bash tools/githook/nn-python-gate.sh` 全绿（ruff+mypy+**1940 passed**）。

---

---

## §23 引导期大 body 护栏 + wire 账聚合：M1（低速重抽）的收尾（2026-09-20）

**一句话**：M1 的坏签重抽只盖了 worker 侧四段 GET（payload/code/ts_code/blob），而**最贵的两段
跑在 code.zip 之前**（引导 `GET /code` 的 code.zip、离线盘 `task-pack`）——那里连一行进度行都没有；
同时账只有「逐 job 原文」，没有 §7.2 要求的分位数。收尾 = 把同一套判据接到那两个段，并让账可复算。

### 三个口子

| 口子 | 为什么漏 | 落法 |
|------|---------|------|
| 引导期 code.zip / task-pack | 跑在 **code.zip 之前**时 `remote.worker` 还不存在（鸡生蛋） | `remote/tailscale_boot.py::fetch_guarded`；`notebook_boot._pull` · `offline_boot.fetch_task_pack` 改走它 |
| `wire` 只有原文、没有聚合 | 验收口径是 p50/p90 + 每组 ≥10 job，人眼扫不出来 | `nn-training/tools/wire_report.py`（三类行 → 逐段 p50/p90 秒 · p50/worst 速率 · bad% · reroll · 命中） |
| ts_code 缓存命中不进账 | 只漏这一处（code/blob 都记了） | `_ensure_ts_code` 命中即 `_wire_hit(jid, "ts_code")` |

### 护栏（与 worker 侧孪生）

分块读 256 KB + 进度行 + 停滞 45 s（`BOOT_IDLE_TIMEOUT_SEC`）+ 调用方的墙钟预算
（code.zip 300 s、task-pack = 既有 `PACK_TIMEOUT`）+ **首块判一次**的低速重抽
（`max(80KB/s, 本会话最好速率/4)`、预计剩余 >20 s、≤3 次、**末次必硬传**）；`HTTPError` 原样上抛
（401/404 是确定性的答案，不是抖动，由调用方按状态码处置）。
**住 `tailscale_boot` 的理由**：三个 notebook（训练 cell / 连接体检 / 离线盘）都拉它——与
`resolve_hub_url` 同一条「单源一份」；新开文件要改三份 notebook 的 raw 清单（notebook 是要重发的
交付物），而 `net_http.py` 在 code.zip 里、引导期够不到。
**孪生而非共享**：引导期只能 import 它自己与 `tailscale_boot`；两边各自单测钉同一组数字
（`test_wire_reroll.py` · `test_boot_wire_guard.py`），**改一边要同步另一边**。

### 账聚合的一处口径纠偏（重要）

工具有意**不报「速率 p90」**：坏签是**低尾**（13 KB/s 排在排序最前）⇒ 报速率 p90 量到的是好签
那一端，会把 321.8 s 的坏签读成「一切正常」。所以速率只报 **p50 + worst**，尾部看 **bad%** 与
**p90 秒数**（秒数那边坏签就是高尾）。这是 §7.2「报 p50/p90，不报均值」的落地口径。

### 备选与否决

- **往 cell 的内联回退里复刻护栏** —— 不做：那是「GitHub raw 拉不到时的最小 pull 路径」，80 行进
  cell 与「cell 只留 CFG / 凭据 / 保活」正面冲突；代价写在 plan §4.0.1。
- **只加进度行、不做重抽** —— 否：进度行治「看不见」，106 s 的坏签仍要付；重抽成本 ≈ 建连。
- **判据单源化（worker 反过来 import tailscale_boot）** —— 否：方向反了（worker 是产品），且把两个
  已发布、各有测试的实现绑成一个发布单元。
- **`tools/remote_wire_scan.py` 当作已有的复核手段** —— 事实纠正：**该文件不在仓里**
  （`git ls-files` 只有 `test_wire_cf_tunnel.py` / `test_wire_reroll.py`）⇒ plan §1.2 的体积表
  无法就地复现；plan §6/§7.3/§9 已标注，替代口径 = worker 的 `result=` 字节数 + 协议层用例。

### 验证

- 新增 `tests/test_boot_wire_guard.py` **17 例**（判据/停滞正文/超预算/有界重抽/末次硬传/两处接线/
  护栏不可用的兜底）+ `tests/test_wire_report.py` **11 例**（现场原文解析/最近秩/命中不采样/
  重抽不重复计数/CLI）+ `test_wire_reroll.py` 补 1 例（ts_code 命中进账）。
- `tools/wire_report.py` 对现场原文**实跑**：`job:payload` p50 3.9 s / p90 **321.8 s**、worst 11 KB/s、
  bad% 50%、reroll 1（正是 §22 那两次坏签的形状）。
- 门禁：nn python 全量绿 —— ruff · mypy **303 files** · pytest `tests/` + `e2e/`
  （单跑一次记数：**1896 passed in 30.98s**）；完整门禁（含 ruff/mypy 并行）**37s**。

### 遗留（未做，已写进 plan）

- plan §M0（引导↔worker 的 code 交接：同一 sha 一次会话不传两次，现场白传 1.43 MB / 106 s）——
  仍是最优先的下一件（零协议）。
- 实机采一组 ≥10 job 的 `wire:` 样本，用 `wire_report` 的 bad% / p90s 校准 §8-4 的阀值。
- `tools/remote_wire_scan.py` 要重写回仓，否则 §1.2 的体积表不可复现。

---

---

## §22 大 body 低速重抽 + 每 job 传输账：痛在连接抽签的尾部，不在字节均值（2026-09-20）

**一句话**：同机同 hub 的传输速率是 **44× 双峰**（13.5 KB/s ↔ 990 KB/s），所以「瘦身」不是
第一杠杆——**换一条连接**才是。

### 现场（同一台 V100 / 同一 hub / 同一隧道）

| 时刻 | 端点 | 字节 | 耗时 | 速率 |
|------|------|------|------|------|
| 10:41:35→10:43:04 | payload | 2,012,020 | 88.6 s | **22.7 KB/s** |
| 11:25:01→11:25:06 | payload | 4,848,536 | 4.9 s | 990 KB/s |
| 12:49:28→12:49:32 | payload | 3,454,884 | 3.9 s | 885 KB/s |
| 12:49:58→12:51:30 | code | 1,433,893 | 106 s | **13.5 KB/s** |
| 12:51:30→12:51:42 | opt_init blob | —（无进度行 ⇒ <5 s） | — | ≥120 KB/s |
| **12:59:29→13:04:51** | **payload** | **3,486,200** | **321.8 s** | 首连接 **6–9 KB/s** 烧完 300 s 预算；**重试换连接后 354 KB/s**（余下 0.6 MB 只用 5 s） |

12:49 那两行是**相邻 3 秒的同机请求**；12:59 那次「重试」其实就是一次**意外重抽**——只是由
`BODY_TOTAL_TIMEOUT_SEC` 在 **300 s 之后**触发（而不是首块的 ~39 s），321.8 s 里约 280 s 白等。
`urllib` 每个请求新建连接（`net_http.urlopen` → `OpenerDirector.open`，无 keep-alive 池）
⇒ 云机侧 ha-connection / 边缘节点决定这条连接的质量。

### 修法（`nn-training/remote/worker.py`）

- **判据**（纯函数 `_reroll_decision`，阈值只有一处实现）：首块速率 < `max(WIRE_MIN_RATE=80KB/s,
  本会话最好速率/4)` **且** 按此速率预计剩余 > `WIRE_REROLL_BUDGET_SEC=20s` ⇒ 抛
  `WireSlowError`（带已收字节数/速率/预计剩余，**有正文**：同裸 `TimeoutError()` 的教训）。
- **只在首块判一次**（`probed` 一次性）⇒ 每次重抽浪费 ≤ 一块（256 KB），**不可能退化成
  「再整份重传一遍」**。
- **`_get_with_retry` 不退避重抽**（重抽的价值就在快）；上限 `WIRE_REROLL_MAX=3`，且
  **最后一次尝试永不可重抽**——慢链路不会变成「永远下不完」。只对幂等 GET 开放。
- **POST result 永不重抽**（产物上行，走既有 5 次退避）+ 只记账。
- **每 job 一行传输账**：`job X: wire payload=3.32MB/321.8s(10KB/s) code=cache-hit
  result=0.89MB/3.7s(245KB/s) reroll=1(wasted 0.25MB) 合计=…`；零字节命中（code/blob 缓存、
  preloaded）也进账（否则读数失真）；push 模式（`worker_server`）同样 flush；未 flush 的 job
  封顶 4 个（防无界增长）。

### 验收基准与教训

- 验收：12:59 那次若带本改动 ⇒ 首块（39 s 处）即判坏签 ⇒ 预期 ~50 s（而非 321.8 s）。
- 教训：**可观测性先于优化**——「13.5 KB/s 与 3 秒后 ≥120 KB/s」这种对比，只有在有了进度行
  之后才看得见；上一节（§21）加的停滞/进度判据是本节判据能出台的前提。
- 门禁：nn python 全量 **1859 passed**（ruff + mypy 绿），新增 `tests/test_wire_reroll.py`
  17 例（判据/相对阈值/上限/最后一次不重抽/浪费有界/账行形状/产物不重抽）。

---

---

## §21 大 body 传输停滞：两侧同时沉默（2026-09-20，用户报障：云机 claim 第二个 job 后几分钟无动静、无日志）

**一句话**：云机领到 `it2` 的 PPO job 后卡在 payload 下载里 **5 分钟一行日志都没有**——
不是没做事，是**两侧都没有停滞判据**：worker 端 `download_payload` 只有一把 `timeout=300`
的整读（socket 超时抛出的是**没有正文**的 `TimeoutError()`，`_get_with_retry` 把它 `repr`
进日志 = `TimeoutError()`，等于没写；过程零进度输出），hub 端 `wfile.write()` 压根没有发送
超时（对端半开 = 永久阻塞）。而 `/payload` 的访问行属于高频静默规则 ⇒ **两端日志同时一个字
没有**，现场只剩「卡住」。

诱因（同机取证）：`tmp/cloudflared-*.log` 同期每 5 分钟一条
`lookup region1.v2.argotunnel.com: i/o timeout`（DNS 劣化窗口；隧道只有 1 条 ha-connection）——
**小 POST（心跳/轮询）照常、大 body 卡死**是这类劣化的典型指纹。

### 修法（卡住必须有名字、有进度、有界）

| 位置 | 判据 | 行为 |
|---|---|---|
| `remote/worker.py::_read_body` | 空闲 45s（`BODY_IDLE_TIMEOUT_SEC`）无新字节 | 抛**有正文**的 `TimeoutError`：`body 停滞：45s 内没有新字节（已收 N bytes / 共 M）` |
| 同上 | 总预算 300s（`BODY_TOTAL_TIMEOUT_SEC`） | 治「永远在滴水」 |
| `download_payload / code / ts_code / blob` | 进度回调（≥5s 一行） | `job X: payload 下载中 3.20 MB / 4.85 MB (66%) 用时 12s（270 KB/s）` |
| `remote/hub_server.py::_bytes` | 发送超时 60s（`SEND_TIMEOUT_SEC`）+ 256KB 分片 | 停滞即断并打印 `已发 N/M bytes`；≥256KB 的 body 完成时打一行（可对账速率） |

缺省路径未变：`_request` 只在给了 `idle_timeout`/`progress` 的下载路径上改走分块读，
`poll_job`/`post_result` 等小请求行为逐字节不变。

回归：`tests/test_body_transfer_guard.py`（8 例；含真 TCP socket 的「读端不读 ⇒ hub
≤发送超时断开并打印已发字节数」，以及「停滞 ⇒ 一条带原因的日志 + 退避重试」）。

**同窗口的 hub 停服 = 人工操作（用户确认手动关的）**：`stopComponent` 杀进程后正是
`clearAnyComponent`（条目没了 ⇒ exit-watchdog 没有可标记的对象），不是崩溃。
教训：组件级决策只打控制台 stdout、不落文件 ⇒ 从盘上证据无法区分「人工停的」与
「自己死的」——同类报障**先问是不是手动停的**；已修：控制台现在把启/停/重启/判死/开课/停课
全部写到 `tmp/training-start/console.log`（稳定文件名 + 会话头，DECISIONS
§2026-09-20-console-decision-log）。

---

---

## §20 离线训练模式：启动选在线/离线 + 云端整段执行 + 补传归位（2026-09-19）

**用户口径**：启动课程训练时指定在线/离线（缺省在线）；离线 = 下载任务包上云自跑，或由
带特别标识的云端 worker 在线领取；`battle.offline.ipynb` 带「是否实时回传 hub」开关（不实时
回传则跑完统一打包手动导入）；notebook 先试连 hub 取包，不通则等人手动上传。

**四个已拍板**（讨论后）：① 离线启动后本机**发一个整段 job + 保留导包按钮**；② 「特别标识」=
worker **自报能力**（`X-Battle-Offline: 1` / `--offline`），不是课程绑定；③ notebook 实时回传**默认开**；
④ 段内逐轮进度**要**回传控制台。

**模式 → 键的唯一换算**（`stack/specs.ts::trainModeKnobs`）：离线 ⇒ `courses.<课>.{rollout_src:'run',
run_iters:-1}`（声明 + 段长，缺一不可）；在线 ⇒ **撤标记**（删段长；课程级 `run` 也删；别的覆盖
不动）。离线档的 `run` **绝不**写全局 `rl.rollout_src`（那会把所有课一起拖进离线）。

**本轮的另一个收获 —— 一条存量真 bug**（e2e 把它逼了出来）：补传体里原来没有**可用的课程身份**
（`manifest.course_name` 是课程文件的 `name` 字段，与 hub 的课程键 `<traj>/<课>/` 不是一回事）
⇒ **多课程 hub 下每条 `/offline/artifact` 都被 400「无法归属课程」拒掉**，节点侧补传**整体停用**
（体是自己造的，重试不会变对）。训练照常，唯一症状是控制台上段内进度永远空着。

**修法**：归位键由**生产者**带（不是 hub 猜）：领活路径 = hub 在 `/jobs/next` 里下发的 `course`
（本来就发了，只是没人往下透传）→ `run_plan_job(hub_course=)` → `make_deliverer(course=)` → 逐轮体
与段末摘要都带；全离线包路径 = `--hub-course <CFG.course>`（offline_boot 写进 argv）。空 = 单课程
hub（键就是空串）⇒ **不带**这个键，逐字回到旧形状。

**e2e**（`e2e/test_offline_training_e2e.py`，真 hub 进程 / 不跑真 rollout·PPO·eval）三条：
① 离线整段 job —— 普通 poller 先领到**在线课**那份、再轮仍是 None，带标 poller 领到 `kind="run"`
（并自报 `plan_sha256`）；hub 日志有「离线课整段交领」；② 真 `OfflineDeliverer` 逐轮补传 → 落
`<traj>/<课>/remote-jobs/offline/<run>/it-NNN/` → `GET /admin/offline` 报出 `{its,count,last_mtime}`
（控制台 `parseOfflineProgress` 吃的就是这个形状）+ 重传幂等 + 不串到另一门课；③ `GET /offline/task-pack`
200（字节相等）/ 404（人读下一步）/ 401 / 400（路径越界）。

**控制台**：启动弹窗新增「训练模式 在线/离线」（缺省在线；服务端生效值 `run` ⇒ 打开就已选中离线，
否则重新开弹窗再点启动 = 静默拉回在线）；离线启动同时把该课 hub 模式置 offline（整段只给带标 worker）。
总览行新增离线段内进度（`段内 N 轮 · 最近 …`，超 1h 无新产物变醒目 —— 离线课唯一的「死」信号）。

**门禁**：nn python gate ✔ 31s · dashboard typecheck/test（675 pass）/lint ✔ · 三份 bundle ✔ · 根 `bun run check` ✔ 33s。

---

---

## §19 启动训练不选 pull/push 模式：传输成为部署事实，课程与 worker 节点正交（2026-09-19）

**一句话**：控制台启动一门课不再问 pull/push/local——**pull 零配置**（hub 在线 + 可选隧道，
谁在轮询谁领活），**push 只看 `nodes[].gpu_push` 是否登记**（配了就默认走 hub 中介派发）：
`courses.<课>.{remote_transport,push_node_url,remote_hub_url,hub_push}` 四个键从类型表、
python 读面与写面上全部删除，残留值由 `pruneLegacyCourseKnobs` 在启动时清掉。

用户口径：「启动课程训练时，trainloop 不需要指定 pull/push 模式。pull 模式是由远端 worker
自己请求，本机只需要保证 hub 在线，配以 tailscale/cloudflared tunnel。push 模式只看系统是否
已经配置了 push worker 节点，界面留配置入口，节点数据存 rl-config.json」；并再次强调
「课程任务与 worker 节点互相正交！所有 worker 都可能接到在训的课程任务！本地 worker 与
云端 worker 完全一致」。

**传达推（`stack/push-config.ts::remoteExecutionFace`，纯函数）**：登记节点数 = 0 ⇒ `pull`；
有节点 + `rl.hub_push`（**缺省 true**）+ hub 地址/token ⇒ `hub-dispatch`；否则 `direct-push`
（detail 里说清缺哪一项）。卡面徽章、启动详情、总览三处**共用这一份**，不再有「本课指向谁」
的按课认领。

**为什么必须删键而不能只改默认值**：课程定义**任务**，worker 节点提供**算力**。按课程的
`push_node_url` 意味着「同一门课换个机器跑就得改课程配置」，而 `local_push` 那种残留条目
**仍有读者**（python 的 auto 全取登记节点）⇒ 会把训练指向一条没人服务的本机地址，表面
「训练正常」。两个方向都是静默失败，故从写面上彻底移除、从读面上清掉。

**python 侧**：`_course_push_url` / `_course_hub_push` 删除；`_gpu_push_nodes(token)` 不再
按课过滤（登记即全部候选）；`_hub_push_opt_in()` 只读全局 `rl.hub_push` 且缺省 True；
`--remote-transport` 保留为「运维钉死一条路」的最后手段，但控制台不再代写。**冒烟预演的
`REMOTE_PUSH_NODE` 改为独占**——设了它就只它一个，否则伪节点失败时 failover 会把预演的 job
送上真 GPU（「冒烟不该碰真训练」）。

**控制台侧**：启动弹窗删掉模式开关与 push 凭据输入（只剩隧道/瘦身/rollout + 降级 + 预演）；
`console-state.trainerPpo` 与 localStorage `tc.train.mode` 退役，`setMode('trainer.ppo')` 响亮
拒绝；唯一配置入口 = 「push worker 登记」面板（`nodes[]` 增删改 + `rl.hub_push` 开关 + hub/面板
两列探活）；移除 worker 时不再改写「指向它的课程指针」（那个键不存在了）。

**门禁**：nn python gate 绿（ruff + mypy + pytest xdist，含重写的 `test_course_push.py`）·
dashboard `typecheck`/`test` **658 pass**/`lint` 0 · 三份 bundle 绿 · 根 `bun run check` 绿。
决策记录：`DECISIONS.md §2026-09-19-goalnn-course-worker-orthogonal · 全文 → 本文件 §32`。

---

---

## §18 多课程单 hub 端到端 + 回环 HTTP 绕开代理（P1 余下 ②）（2026-09-18）

P1 收口：`e2e/test_multi_course_single_hub_e2e.py` 把「一个 hub 进程服务所有并行课程」钉在
**真跨进程**链路上——真 hub 子进程（`--discover --push`，命令行不点课程名）+ 真
`worker_server`（starter 换成写结果的假执行器 ⇒ 不跑 rollout/PPO/torch）+ 训练侧真发布
（`publish_job(dispatch="push")`）与真等待（`wait_job` HTTP 轮询）。

```
训练侧 publish_job(dispatch="push")  →  <traj>/<课>/remote-jobs/<jid>/
hub（真进程，发现课程表）            →  push_client.submit_job（code.zip 随体）
worker_server（真 HTTP 契约）        →  GET /job/<jid>/result
hub accept_result（对账→租约→首写）  →  本课 result/result.json
训练侧 wait_job（控制台读同一份账本）
```

两个用例：① 两门课各发一份 job → 都完成、结果**各回本科目录**、账本不串课、worker
各收一次、派发器 `pushed=2/requeued=0`；② 离线课（`POST /admin/courses?mode=offline` 真热切）
不实时派发（job 停在队首、worker 没碰），同 hub 的在线课照常走完，切回在线后**同一份 job**
（不重发）立刻被推走。

### 实测踩到的四个口径（写进用例注释）

1. **就绪 ≠ 端口能答**：课程表是后台扫描登记的（`DISCOVER_SCAN_MIN_SEC=2s`），端口刚答
   200 时课程表还是空的——只等端口就会假红（首版就这么红的）；等的是「`/admin/queue`
   能答 **且**课程表就位」。
2. **结果落的是 `result/result.json`**（不是 job 根下的 `result.json`），且可领池看的是
   **盘上有没有结果**，不是租约——成功后租约要到 TTL 才消失，所以断言是「无可领的活 +
   派发器自己手上没有在飞」，而不是 `inflight == []`。
3. **`job_completed` 是训练侧写的**（验收落位后 `mark_job_completed`），hub 只写
   `job_pending` + 落结果；用例显式走一次 `mark_job_completed` 并断言「只进本科账本」。
4. `--race off`：竞速广播会把同一份活推给两台，本用例要的是 1:1 派发。

### 顺带修掉的门禁真缺陷：**回环 HTTP 被环境代理截走**

`test_offline_deliver.py::test_offline_endpoints_require_auth` 在门禁里红过一次：hub 日志
明明两次 401，测试侧读到的却是 **502**。根因不在测试——本机用户级环境带
`HTTP_PROXY`/`HTTPS_PROXY`，而 `no_proxy` 写的是 `127.*` 这种通配，Python 的
`proxy_bypass()` **不认**（只认 `host == entry` / `*.suffix` / `.suffix`），实测
`proxy_bypass("127.0.0.1") is False` ⇒ **每一发去 127.0.0.1 的请求都被送到外部代理再转
回来**（代理抖动/回错误页 = 502），本机训练也凭空多一跳。

修法：新增 `remote/net_http.py`（`is_loopback` / `no_proxy_opener` / `urlopen` 替身），
把四条本机 HTTP 出口接上——`hub_client._request`（训练侧↔hub）、`push_dispatch._http`
（hub↔GPU worker）、`worker._request`（worker↔hub，非回环仍用它的显式 ProxyHandler，
Colab 需求不受影响）、`offline_deliver._urllib_opener`；非回环分支刻意仍调
`urllib.request.urlopen`（保住测试里那条 monkeypatch 缝）。

复现→修复（§7）：`tests/test_loopback_http_no_proxy.py`（5 例）——把环境代理指到**死端口**
再打本机真服务，修复前三条出口全部 `ConnectionRefused`（我用临时脚本实测过：raw urllib
URLError、`net_http.urlopen` 200），修复后全绿；另有一例断言非回环仍走 urllib 默认。

只改生产侧还不够：**测试侧另加一层兜底**——`tests/conftest.py` 把精确回环主名
（`127.0.0.1` / `localhost` / `::1`）补进 `no_proxy`（`proxy_bypass()` 认精确匹配），8 个仍用
**裸 `urlopen`** 打本机临时端口的既有用例因此一并脱离代理（实测证据：只改生产侧时
`test_multi_course_hub.py::test_main_discover_picks_up_course_from_disk` 在满载下仍会
在 `/jobs/next` 那一步吃到代理的 `Errno 111`，而 hub 自己毫发无损）。生产靠代码、测试靠环境。

### 门禁

| 门 | 结果 |
|---|---|
| nn python gate | ✔ **1320 passed / 3 skipped**（+2 e2e +6 回环用例；ruff + mypy 干净）——连跑两次均绿 |
| 根 `bun run check` | ✔ 19s 绿 |
| dashboard typecheck / test | ✔ 15s 绿（本轮未动 dashboard） |

**P1 到此收口**；余下的「组件卡片按『单例角色 / 按课程』分组」仍按 §17 的说明推迟（属形状
整理，不阻塞任何链路）。

---

## §17 单 hub + 单隧道（P1 余下 ①）：hub/cloudflared 收敛为单实例 + 课程表从盘上发现（2026-09-18）

用户口径（原话）：`hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程，就能同时支持所有并行训练课程`。
本轮做 **hub + 隧道**（trainingLoop 是有状态会话，收敛要等 P2 的任务队列改造，见 DECISIONS 条目的「备选与否决」）。

### 形状：一个进程，一份地址，课程表来自磁盘

```
训练侧（每课一个会话）        共享实例（一个进程）
tmp/<课A>/remote-jobs  ─┐
tmp/<课B>/remote-jobs  ─┼─→ hub-server :<rl.hub_port>  --discover --traj-root <repo>/tmp
tmp/<课C>/remote-jobs  ─┘        ├─ 扫 <traj>/<课>/{remote-jobs,offline}（新鲜 1h 窗口）自动登记
                                 ├─ 每课一条 FIFO + 跨课程轮转（既有）
                                 └─ cloudflared（单隧道 → 同一个 hub 端口）
```

- **课程表不做注册**：训练侧把 job 发布到盘上就是「这门课在跑」的事实。扫描两处触发——`claim_next()` 前置（2s 最小间隔闸 ⇒ 新课程**下一次轮询**就能被领到）+ 后台 5s 节拍（push 模式/无 worker 时兜底）。登记后不撤销（课程暂停时在飞 job 的结果回传不能 404）。
- **新鲜窗口 1h**：陈旧实验目录的磁盘形状与残留 pending job 完全一样，误登记 = 把死课程的活派给真 GPU worker。
- **统计面唯一**：`scopeOf(key, course)` 是共享槽（`''`）的唯一归一入口；`sharedHubPort/sharedHubUrl/sharedTunnelMetricsPort` 是唯一地址来源（grep 门禁守 `slotPort(…, 'hub')` 零调用面）。
- **换代接管**：共享实例启动前把旧形状（per-course）条目活则杀、死则清账；per-course 条目**拒重建**（fail-closed），否则等于凭空再造一个 hub 读同一棵树。
- **单实例切换要搬状态**：`_adopt_solo()` 把 halt / race / worker 登记 / 鉴权计数从那份 `_JobStore` 搬到队列自己身上。
- **停机达令按课程下发**（连带必修）：单 hub 之前「一个 hub 一份 halt 布尔」≈ 按课程；单进程后那个布尔升格为**进程级**，A 课门禁 ABORT 会连坐停掉 B 课云机（表现为「云机莫名停机」，属最贵的静默故障）。故 halt/resume/status 一律支持 `?course=`（空 = 全课程），训练侧（门禁 / 启动清停机态）与控制台各自带课名。

### 验收

| 面 | 结果 |
|---|---|
| nn python gate | ✔ 26s 绿（ruff / mypy / 全量 pytest **1312 passed / 3 skipped**，含 `test_multi_course_hub.py` 24 例，其中 1 例是**真进程** `--discover` 主流程） |
| dashboard typecheck / test | ✔ 542 pass / 0 fail（`single-hub-tunnel.test.ts` 新 7 例；`cloud-halt.test.ts` +2 例按课程达令） |
| dashboard build:ui | ✔ 三份 bundle（app 61.8KB gzip / log / eval） |
| 根 `bun run check` | ✔ 32s 绿 |

### 踩到的坑（都留了回归）

1. **`tmp/*/remote-jobs` 写进了 TS 块注释** ⇒ `*/` 提前闭合注释，tsc 在下一行开始报 `Module declaration names…` 一类怪错。TS 注释里写通配路径要避开 `*/`。
2. **`withScratch(async () => …)` 不 await 回调** ⇒ `finally` 在第一个 await 处就恢复环境变量，异步体后半段跑去读**线上** `registry.json`，而那份里正好躺着历史遗留的 `hubServers[''] = {pid: 1}`（exit-watchdog 事故污染）——测试看起来「读到 pid 1」其实是读错文件。修法：测试夹具必须 `await fn(...)`。
3. **`_pump` 的 8s 上限在满载下会偶发红**：门禁拿 xdist -n 12 跑，假 worker 的 0.5s 探测超时在满载时会把**健康** worker 误判为「没答」⇒ 派发器把它当忙 ⇒ 没有机器能接活（`test_worker_refusing_job_requeues_to_another` 实测红过一次）。修法：假 HTTP 探测超时提到 2s + `_pump` 上限提到 20s（纯测试夹具余量）。
4. **子进程还活着时读它的 stdout 会阻塞**：`assert st == 200, f"{proc.stdout.read()}"` 一旦被改成**先读后断言**（不在失败分支里惰性求值），就变成等 EOF → 整个用例挂到 60s 超时（mypy 还顺手报 `IO[Any] | None`）。修法：`_startup_output(proc)` 只在失败时调用、且进程未退出时直接返回空串。
5. **`# noqa: BLE001` 在这个 ruff 配置下是 RUF100（未启用该码）**——写了反而红，异常兜底的注释直接当普通注释写。
6. **`toEqual` 比 ProcSpec 会因闭包函数不等而假红**（`healthy`/`ownsResource` 每次构造都是新引用）⇒ 逐可比字段断言（沿用 W4 的写法）。

---

## §16 hub 中介 push 派发（P1 余下）：队列顺序推空闲 worker + 周期探活 + 超时回落换人（2026-09-18）

用户指令：hub 中介的 push 派发 + worker 登记入口 + 周期 ping 探活，训练侧 push 改走 hub。
抉择与代价全文见 `DECISIONS.md §2026-09-18-goalnn-hub-push-dispatch · 全文 → 本文件 §32`，这里只记落地与实测。

**形状：派发权搬到 hub，训练侧只留一句话。** 训练侧发布时写 `manifest.dispatch="push"`
（= `--remote-transport hubpush`，或 auto + `courses.<课>.hub_push` / `rl.hub_push`），
之后回 `wait_job` —— 与 pull 完全同一条收尾链（三重校验 → 落位 → 记账），**不新增第二条
客户端**。hub 侧新模块 `remote/push_dispatch.py` 认领这份活自己推：

- `PushWorkers`（登记表）：读 rl-config `nodes[]` 里 `gpu_push` 的条目（控制台的 worker
  登记入口回写它们），**mtime 热重载**（写完配置不必重启 hub）；周期 `GET /ping` 更新
  在线/忙闲/排队；运行时增删走 `POST /admin/push-workers`（volatile，**且重载后仍保留**）。
- `PushDispatcher`（派发拍）：跨课程轮转 + **每课程至多一份在途**（课程内轮次有硬序）+
  只推**队首**且队首必须是 push job（不越过它推后面的）+ 四道闸（在线/不忙/在飞<并发/
  不在本次避让名单）；上传与等待在**每份 job 一条线程**里做（几十 MB 的 POST 不阻塞拍）。
- 回落：超时（缺省 45min 兜底）/ 连续探活失败（3 次）/ 拒收（409/428）/ 结果入账被拒
  ⇒ 放租约 + 避开那台 + 立刻换人重推（job 停在该课队首）。
- 结果入账与云机 POST **共用** `accept_result`（对账 → 租约 → 首写锁定）；`_post_result`
  改为调用它 —— 两条腿不可能一条校验一条不校验。

**探活的两条硬判定**（都会在回归里咬）：

1. **从没答过 = 不在线**：宁可这一拍不推，也不往一台可能是死的机器上推几十 MB。
2. **失败但未达阈值 ⇒ 当局忙**：状态未知必须当忙，否则一次隧道抖动就够调度器认为它空闲
   并二次推送同一门课（两份 PPO 抢同一轮）。

**缺省关**：`--push` 才启用（不打开连探活线程都不起），既有单课程用例与线上行为逐字节不变；
控制台经 `rl.hub_push` 透传，`--push-config` 显式指向仓库那份 rl-config（指到 per-course
目录 = 登记表恒空，是最难查的一种配错）。

**回归**（14 例，全用假 GPU worker server 的三件套 `/ping` `/job` `/job/{id}/result`，
不跑任何真实运算、不 spawn bun）：

- 纯函数：登记判据与训练侧 `_gpu_push_nodes` 同尺子（`gpu_push` + enabled 缺省 true + url）；
  四道闸 + 注册序稳定挑选。
- 登记表：mtime 热重载、非 push 节点不进表、探活写回在线/忙闲/计数、运行时增删。
- 全链路：happy path（推 → 结果落盘 → **真 hub 端点** `/jobs/{id}/status|result` 读得到 →
  wire 账 `payload_bytes/body_bytes` 实测）；忙的 worker 被跳过；**离线课一份都不推**；
  失联 → 回落队首换另一台（并断言跑死它的那台**不得**再拿同一份）；超时兜底；worker 拒收
  （409）→ 换台；**被篡改的结果**（data_fp 漂）→ 拒收 + 不落盘 + 换台重跑；队首是 pull 活
  → 一份都不推；没有空闲 worker → job 留队首、无租约残留；**训练侧真发布**
  （`publish_job(dispatch="push")`）走完同一条链；`/admin/push-workers` 读/增/删 + 未启用 409。
- 传输裁决（`test_remote_transport.py` +5 例）：`hubpush` 无条件走（缺 hub/token 响亮拒），
  `push`/`pull` 压过配置，auto 只在 opt-in + hub 齐备时切（旧部署一行不改）。

**门禁**：nn python gate **1301 passed / 3 skipped**（ruff + mypy 干净）、根 `bun run check`
（1851+ pass）、dashboard typecheck / test（309 例）/ `build:ui` 三份 bundle 全绿。

**本轮未做（P1 余下）**：控制台「worker 登记入口」UI 与面板重组（课程 select 自由可切 /
在训课程高亮 / 队列与 push 总览）、单隧道（hub/cloudflared 收敛为单例）、多课程单 hub 的
端到端 e2e（训练侧 hubpush → hub → 真 worker_server + 假 PPO）。

---

## §15 多课程单 hub（P1）：一个进程托管 N 份账本 + 跨课程轮转 + 离线课 + 分课程权重桶（2026-09-18）

用户指令：多课程并行训练流程与操作重组（上限先设 5；hubserver/trainingloop/selfNode/
cloudflared 各只需一个进程；hub 设 PPO 任务队列；离线课不实时派发但收回传；rollout 集群
按课程缓存最近权重；课程数 < worker 数时用单课程竞速；面板重组；e2e）。抉择与代价全文
见 `DECISIONS.md §2026-09-18-goalnn-multi-course-single-hub · 全文 → 本文件 §32`，这里只记落地与实测。

**形状：一个进程托管 N 份 `_JobStore`，磁盘契约逐字节不变。** 多课程不是换一套目录约定，
而是「同一进程里多挂几份账本」（仍是 `tmp/<course>/remote-jobs` + `training_log.jsonl`）。
这条取舍换来的是：既有 100 个单课程 hub 用例、`tmp/<course>` 约定、诊断工具全部照旧，
回滚只需改启动参数。

- `_HubQueue`（调度面）：路由（job_id → 课程，扫 job 目录一次并缓存）+ 每课程 FIFO +
  **跨课程轮转**（`rotation_order` 从上次派发的下一门开始）+ 离线课不参与 + 观测面。
- `claim_next(worker_id, race)`：挑活的唯一入口。超时回收时记下「谁跑死的」，在**还有
  别的活跃 worker** 时避开那位前持有人（用户口径「回落队首并改为推送其它 worker」）；
  独苗时允许自领（否则那台 worker 永远空转）。
- 竞速口径：`race_decision(..., active_courses=N)` = 「窗口内不同 worker 数 > 在派发课程数」。
  **缺省 `active_courses=1` 时与旧口径逐字节等价**（`< 2` ⟺ `<= 1`）⇒ 旧用例一行未改。
- 鉴权面提取 `_AuthGuard`（进程级一份，不按课程各算 ⇒ 封禁阈值不会变成 5×N）；单课程队列
  **借**那一份 store 的鉴权/竞速/停机状态。
- 权重桶按 `(course, kind)`（`tools/agent/weight-buckets.ts`，纯逻辑出列）+ 上传前预检
  `GET /v1/weights?sha=&kind=&course=`（命中连体都不传）。课程身份走进程级 `RL_COURSE_NAME`
  （在 `apply_course` 挂——训练进程唯一知道课程名的地方）。

**实测踩到的两个真缺陷（都有回归）**：

1. **「找不到归属」不能写成空串**：单课程队列（与旧单课程 hub）的课程名**就是空串**
   （`tmp/nocourse` 那套约定）。`course_of` 用空串兼作缺失值 ⇒ 单课程下 `/jobs/next` 刚派
   出的 job 立刻解析不到归属、handler 打到哨兵路径上 **500**（每一次拉活都失败）；
   补传路径同款 ⇒ 单课程每一次补传 **400**。两处都改成 `None` 表缺失。
2. **避让的判定时序**：在队列层「先读 stale 记录再比身份」恒为空——那一刻过期租约还没
   被回收，stale 记录还没写。修正为「闸在队列层（`may_avoid_stale_holder`：有身份 +
   还有替班），身份比对在 store 内」（那里才是回收之后的最新状态）。第一版就是这么写的，
   回归测试当场抓出来。

**另一条实测口径**：避让的闸看的是「窗口内活跃 worker 数」（180s），而租约 TTL 是 300s
——一个 worker 停 poll 超窗口即判离场。测试里必须按真实节奏刷 `note_worker`（活着的
worker 每几秒就打一次 `/jobs/next`），否则两台都被当离场 ⇒ 活跃数 0 ⇒ 避让不开（这是
**对**的行为：连一台活的都没有时，避让只会让这活没人干）。

**门禁**：nn python gate **1270 passed / 3 skipped**（+29 例：`test_multi_course_hub.py` 17 +
`test_weight_course_buckets.py` 12）；根套件（含 `tests/agent/weight-buckets.test.ts` 13 例）
全绿；ruff + mypy 干净。

**本轮未做（P1 余下）**：hub 中介的 push 派发（hub 主动推给空闲 worker + worker 登记入口 +
周期 ping 探活）与训练侧 push 改走 hub；单隧道（随单 hub 自然成立）；dashboard 重组；多课程
单 hub 的 e2e。**P2（用户已拍板）**：训练循环**迭代任务化**（一轮 = 一个任务、执行器不跨轮
持状态、状态全在磁盘），验收 = 「断开续跑 == 连续跑，逐字节等价」；允许牺牲 T4 预采、允许
本机降级改每轮载入、eval 尾巴改盘上轮询。

---

## §14 本机 hub 地址走凭据（`HUB_IP`）：两个 notebook 同键同口径 + 一条对账守卫（2026-09-17）

**用户指令**：`battle.tailscale.ipynb`，把本机 ip 也设置为 secret，避免每次都要手动输入。

**动机**：`CFG["hub_url"]` 是「**每个会话都要手填、值却长期不变**」的值——hub 跑在操作者本机，
Tailscale IP 在设备重注册前是稳定的。这正是 secret 的用途，而它此前是 CFG 里唯一还需要人肉
改的条目；模板值 `http://<本地TS_IP>:8787` 忘了改就会拿一个带尖括号的主机名去连
（错在 DNS 层，比当场点名难查得多）。

**一个键、两种取值**（不必记两套约定）：

```
HUB_IP = 100.64.0.5                     → http://100.64.0.5:8787（配 CFG hub_port，缺省 8787）
HUB_IP = 100.64.0.5:9999                → http://100.64.0.5:9999
HUB_IP = http://hub.tailnet.ts.net:8787 → 原样（换域名/协议/端口都行，同机 127.0.0.1 同）
CFG hub_url 含 "<" → 视为未填（返空串，由调用方响亮失败并点名该填哪个键）
```

`HUB_IP` 有值时**压过** CFG `hub_url`；未设时按 CFG 手填——老会话（没建这个 Secret）行为不变。

**单源一份，两个 notebook 共用**：解析逻辑住在 `remote/tailscale_boot.py::resolve_hub_url`，
消费点是 `notebook_boot.run()`（真跑）与 `diagnose()`（体检）——两个 notebook 都从 GitHub raw
拉这**同一个文件**（体检那边只拉它一个），所以口径不会两边漂。

**与 §2026-09-17-kaggle-cred-before-proxy 同一条时序约束**：`HUB_IP` 与另三个凭据**同批在
引导之前**读。它同样只能公网取（平台 Secrets），引导后 userspace 代理只转发 Tailscale IP ⇒
读出来会是空串；而若它被读成空，症状是「连不上 hub」——与真正的原因（地址没读到）看起来
完全不像，是最费排障时间的一类假象。

**notebook 侧改动**：

| 位置 | 改动 |
|---|---|
| 训练 cell CFG | 新增 `hub_ip` / `hub_port` 两条；`hub_url` 降为「手填兜底」 |
| 训练 cell 内联回退（远端模块拉不到时唯一的活路） | 复刻同一段解析 + **打日志记来源**（`hub = … （来源：HUB_IP / CFG hub_url）`），失败信息同时点名 `HUB_IP` 与 `hub_url` |
| 体检 cell | 自己读 `HUB_IP` 并把 `hub_ip`/`hub_port` 喂进 `diagnose()` |
| 两 cell 的说明（markdown） | 凭据清单补 `HUB_IP`；pull 行不再指向一个要手改的 CFG 值 |

体检 cell 必须同口径，否则会出现「**体检说连不上、真跑却连得上**」这种最难信的一种诊断结论；
`diagnose` 里那句「未配 hub_url，跳过」也改成「未配地址（HUB_IP 凭据 / CFG hub_url）」——
体检报告里的跳过一次不该让人再去猜该填哪个键。

**回归守卫**：新增 `nn-training/tests/test_notebook_hub_ip.py`（**14 例**）。notebook 不 import
仓库代码，所以按 `test_tpu_probe_notebook.py` 的做法把 cell 文本抠出来**独立执行**——
但不止「有这几行」，而是把内联回退算出的 `_hub` 与 `resolve_hub_url` 的返回值**逐例对账**
（8 例取值 + 2 例未填→`SystemExit` 且点名两个键 + 缺省端口跟随 `HUB_DEFAULT_PORT`），
再钉两个 cell 的读取位置早于代理引导、体检 cell 把 `hub_ip`/`hub_port` 喂进 `diagnose`。
先红后绿验过：把 cell 里的缺省端口字面量 `8787` 改成 `9999`，对账断言当场红。

**存量测试立即咬到新凭据**（这是好信号）：`test_bootstrap_proxy.py` 的「凭据前置」用例
（假 secret 只认三个旧键）在新凭据加入后立刻 `KeyError: 'HUB_IP'` —— 它断言的正是
「任何凭据在 tailnet 代理生效后被读即红」，说明这道闸门对**新增凭据**也是自动生效的。
已扩为四键并顺手加固两处：`calls` 断言四个键的读取顺序；`captured["hub"]` 断言
HUB_IP（100.64.0.5）**压过** CFG hub_url（故意留成不同的 100.64.0.9，好让「压过」可观测）；
另加一例「HUB_IP 未设 ⇒ 回落 CFG hub_url」。

**门禁**：nn python gate **1238 passed / 3 skipped**（+15 例；ruff + mypy 干净）；
根 `bun run check` **1851 pass / 0 fail**。

---

---

## §13 「云端同机 rollout + PPO」全链路集成测试：一条真链路，两个面板读法（2026-09-17）

**用户指令**：写一个集成测试，确保「云端同机 rollout + PPO」全流程畅通，本地 dashboard
能正常读出云端回传的**每 it 权重与指标**；rollout/PPO 用假节点，不跑实际运算。

新增 `nn-training/e2e/test_cloud_iter_e2e.py`（单用例，**~2.0s**，进门禁 e2e 层；见文末"墙钟"）：

```
训练侧 TrainingLoop._remote_iter(it)     真 build_iter_spec + 真 publish_job（磁盘 IPC）
  → 真 hub-server（127.0.0.1 临时端口，真租约/真账本；单 worker ⇒ race=false 独占）
  → 假云机（本测试线程）：真 /jobs/next 领取 → 真下载 payload/code/ts_code（逐 sha 对账）
      假 rollout（按 argv 逐局写 w{i}/rl_sX_seedY + _rl_report.json，**不 spawn bun**）
      → 真 verify_shards（实产集 == 声明集）+ 真 combine_reports（聚合口径不假）
      假 PPO（换一份权重 + opt tar + agg，**不碰 torch**）
      → 真 validate_result + 真 pack_result_v2（v2 线格式）→ POST result
  → 训练侧 真 wait_job → 真 verify_and_land（三重校验 + 权重原子落位 + opt 解包）
  → 真 _export_weights（归档）→ 真 _record_iteration（iteration 账本）
```

**断言面 = 面板的三个真读者**（python 侧独立重实现其读法，并把口径钉在面板源码的字面量
上——双端锚，同 `tests/nn/schema-fingerprint.test.ts` 的思路）：

| 面板面 | 源码 | 本测试断言 |
|---|---|---|
| 每 it 权重 | `eval-board/ckpts.ts::iterFromCkpt`（`/\.it(\d+)\./`）+ `buildEvalCkptsView` | 归档名解出 iter==7、归档/活动指针字节 == **云端回传**的那份（≠ 本地 init） |
| 每 it 指标 | `server/iters.ts::readIterMetrics` | iteration 行字段**存在且非空** + 逐值对账（winRate 0.5 / score_mean 0.1 / dim_means.kills 0.5 / rollout_sec=节点自报 / ppo_cloud_sec=云真训练秒 / wire.rollout_src==node；M0 计量：hub 实测 `up_bytes`==节点 payload 字节、`down_bytes`==result 体字节、`wire.worker.payload_bytes` 同值） |
| job 账本 | `api/ppo-queue.ts::detectPpoQueueStall` | `job_pending` → `job_completed` 有序、`result/result.json` 与 `claimed` 标记齐、无 `fail.json` |

**为什么单独断言「字段存在」**：`readIterMetrics` 用 `Number(r.x ?? 0)`——写入侧漏一个字段
会被**静默读成 0**（曲线掉零、表格 0% 而不报错）。这是读者**不会**替我们发现的回归，
只有存在性断言能抓。

**失败卫生**：假云机任何异常都 (a) 记进 `node.errors`（主线程先断言它，根因不被超时掩埋）、
(b) `POST /jobs/{id}/fail` —— 真链路上这条让训练侧 `wait_job` 立即带原因收兵，测试红了也
是秒级；训练侧另跑在独立线程 + 45s 限时监督（不会陪 `wait_job` 等满 30min）。

**边界（其余面由既有测试承担）**：push 直推云机 → `e2e/test_push_mode_integration.py`；
真 bun rollout → `tests/test_remote_iter_real_bun.py`（本机无 bun/权重时 skip）；
竞速输家叫停仍是后续项（旧的竞速广播条，§67）。
（**2026-09-23 注**：竞速随下面 P3 整体退役（见 §30），此后续项已消失；§67 本体亦随 2026-09-23 拆分删除。）

**墙钟（首版 6.8s → 现 ~2.0s）**：掐表后最大的那块不是算，是**等**——`wait_job` 的
`poll_sec` 生产默认 **5s**（为隧道抖动设计的节奏），而假云机在 publish 后几百 ms 就把结果
POST 上来了 ⇒ 训练侧仍要等满下一个轮询窗。实测分解：**1.0s** 准备/打包/发布（code.zip 238
文件 + ts_code.zip 340 文件 ~1.4MB + tar.xz payload）+ **5.0s** 轮询窗 + 收尾。
⇒ 加 `fast_wait_poll` fixture：**只**把 `poll_sec` 调成 0.05（`wait_job` 本体全真跑：404 续等、
410 带原因收兵、5xx 退避、超时前 `/status` 二次确认）；真 CLI 解析器 import 提到模块级。
现测 1.99 / 2.00 / 2.08s，占空降到 ~2s（剩下的都是真工作：两包打包 + 真落位）。

**门禁**：nn python gate **1223 passed / 3 skipped**（+1 例）；根 `bun run check` 1851 pass / 0 fail。

---

## §12 产物补传：中途能连上 hub 就自动恢复在线回传（2026-09-17）

**为什么记这一笔**：这是全离线（§11）需求的后半句，也是**第一条「在线能力可选」的链路**
——它之前的每条腿都以「hub 可达」为前提，而这条腿必须**既不影响训练、又能在断网后自愈**。
决策与备选（含六条否决）见 `DECISIONS.md §2026-09-17-goalnn-offline-reconnect-delivery · 全文 → 本文件 §32`。

```
节点每轮：checkpoint 落盘（自洽）→ sync()：探 GET /ping（带 token）
  ├ 不通 / 5xx     → 静默跳过（节流记一行），下轮再试           ← 训练继续
  ├ 401/403        → **本会话停用**补传（D9 会把每轮重试的自己封掉；配置错重试无用）
  └ 通 → POST /offline/artifact（权重+opt+账本行，(run_id, it) 幂等、首写锁定）
            └ 第一次连上时把之前攒的**积压一次补齐**（pending = 磁盘上有、delivered.json 里没有）
hub：<job_root>/offline/<run_id>/it-NNN/{weights.json,opt.tar,row.json} + metrics.jsonl
     + result.json（段末摘要，覆盖写）+ 账本审计事件 offline_artifact / offline_result
```

### 三个必须先说清的判定

1. **探活走带 token 的 `/ping`，不新增无鉴权 `/health`**：探针要回答「我能不能用这条链」，
   只证可达会让「token 配错」在第一次上传 ~1.9MB 之后才暴露，还多一个向公网泄露「hub 在线」
   的端点。
2. **401/403/400/413 = 本会话停用**，不是重试：D9 的闭锁是「同 IP 五次无效鉴权封 3600s」
   （每轮重试 = 自己把自己封掉），且体被拒说明形状不对（版本不匹配），重试一百次也不会对。
   5xx / 网络异常才走「下轮再试」。
3. **补传不是 job**：没有 pending、没有租约、不进待领池（`claimable_job_ids() == []`，测试钉住）
   ——记一条 `job_pending` 会让控制台显示一条永远等不到工人的任务，还可能被别的节点领走。

### 写测试时按以往教训卡的两个真缺陷（都有回归）

| 缺陷 | 不修的后果 |
|---|---|
| 传输异常从 `_post` 冒到 `sync()` 外层 | 外层兜住了异常但**跳过了 `_save_ledger`** ⇒ 本轮已投递的几轮只在内存里、下次会话整批重传（幂等但白焚流量）。修法：异常收在 `_post`，返回状态 0 = 没送达 |
| 账本行自称的权重指纹没与实收字节对账 | 产物目录**内部不一致**（人改过 / 半截写入）的权重会以「hub 上的产物」身份进入 eval/续跑，而真因在几千行日志之外。修法：hub 侧 400 拒收 |

另外两处防护按「不可信输入」对待（hub 端）：`run_id` 是目录名 ⇒ `sanitize_run_id`
（`[A-Za-z0-9][A-Za-z0-9._-]*`、≤64、拒 `..`、**非字符串即非法**，不做 str() 兑底——宽容只会
把「上游传错类型」变成看似正常的目录名）；声明长度超限直接 413（不读进内存）。

### 验证面（本机可跑的）

`tests/test_offline_deliver.py`(16)：接通后落位/幂等/重启续投/**hub 关机期间零影响 + 重连一次补齐**
（核心需求）/坏 token 只试一次不多出网/体被拒即停用/`SYNC_CAP` 分次/传输异常不破记账/
越界 `run_id` 拒收且不落盘/指纹不符拒收/重复投递首写锁定/摘要覆盖写 + 审计/未鉴权 401/超限 413/
`sanitize_run_id` 纯函数/**真 hub + 替身 run_job 的整段集成**（逐轮在跑的过程中到达 hub）/hub 无人
听时整段照常跑完。

门禁：nn python gate **1171 passed / exit 0**；根 `bun run check` **1853 pass / 0 fail**；
dashboard typecheck 干净 + **468 pass / 0 fail**。

### 未做（不写成已做）

① 真云端（Kaggle/Colab）端到端一次——本机没有节点，整条链只在真 HTTP 端点 + 替身 `run_job`
上跑过；② 控制台里显示补传进度（账本 `offline_artifact`/`offline_result` 已在写，读方未接）；
③ push 形态的节点没有 hub 地址 ⇒ 默认不开补传（需要显式配 URL）。

---

## §11 全离线任务包落地：hub 导出 → Kaggle/Colab 上传 → 云机自主跑完（2026-09-17）

**为什么记这一笔**：新增一条「任务可以离线搬运」的交付面（协议外的文件格式 + 两处入口 + 一个
不可信输入的边界）；决策与理由（含四条被否决的备选）见 `DECISIONS.md`
§2026-09-17-goalnn-offline-task-bundle。

### 与半离线（§10）的关系

```
半离线（kind="run"）：hub **在线**发一次 job（计划随 payload）→ 云机自主跑完 → 回传尽力而为
全离线（task bundle）：hub **导出 zip**（计划+课程+权重+动量+代码+TS）→ 人搬上云（dataset/Drive）
                       → 云机 import 后自主跑完 → 产物落 Kaggle output / Drive
                       → **若中途能连上 hub**，自动补传（下一笔；本 § 未做）
```

半离线解决「云机不依赖 hub 也能跑完」，全离线解决「**hub 关机也能开工**」——任务本身不再走网络。

### 形状

```
hub:  run_rl --ppo remote --run-iters N --export-bundle tmp/task.zip
        └ 不训练、不等待：拼 plan + 复用发布链造 manifest（register=False：不记账本/不进待领池）
        └ remote/bundle.export_bundle：task.json 索引（逐件 sha256+字节数）+ README + 六件套
云机: python -m remote.bundle import task.zip --dest /kaggle/working/battle2-<run>
        └ 逐件对账（不符即拒收，一局不跑）→ 铺成可直接续跑的产物目录（it-{it}/ 起点 + ts_code/）
      python -m remote.run_loop --artifacts <dir> --device cuda      # 或 --bundle <zip> 一步到位
        └ code.zip / ts_code.zip 字节走 run_job 的 preloaded（节点无仓、不联网）
产物: it-NNN/weights.json + opt.tar + metrics.jsonl + state.json + LATEST.zip/artifacts.zip
      Kaggle = /kaggle/working（Save Version 即官方打包下载）；Colab = Drive / files.download
```

轮次对齐（整条最容易错的一处）：loop 的 `it` = 包里要跑的**第一轮** ⇒ `plan.start_it = it-1`、
`max_iters = n`（不是 n-1——这里没有「job 自己那一轮」要扣，`args.out` 就是包的起点）。

### 两处按教训卡出来的硬门

| 门 | 不卡的后果 |
|---|---|
| 导入逐件 sha/字节数对账 + zip-slip 拒绝 + magic 校验 | 一次截断的搬运会变成一堆无法归因的怪结果；恶意/损坏成员可在云机上写任意文件（包是人搬来的，最不可信） |
| standalone 入口「代码可用」硬门（`code.zip` 或 `code_cache/<sha>/` 命中） | `run_job` 拿空 base_url 去下载代码，报一个跟真因无关的错（重试/联网都治不了）——改前 `test_run_loop` 的两个用例正是这么红的 |

注意这个门**只卡 standalone 入口**，不卡 `_run_iteration`：半离线轮（hub 发的 kind=run）的代码是
worker 自己那一轮从 hub 下好、已落进内容寻址缓存的——那里没有 `code.zip` 字节也完全正常。

### 验证面（本机可跑的）

`tests/test_bundle.py`(7)：导出→导入的目录形状（起点 checkpoint + opt + TS 树）；改一个字节就
逐件对账拒收；zip-slip 成员拒收且不落盘；拿错包/坏 zip 在读索引这一步停；导出侧三道自检（计划
sha 不符 / 缺 code.zip / 缺 ts_code.zip）；**导入后 `run_standalone` 无网无仓跑到 end_it**
（替身断言 payload 里带了 code.zip 与 ts_code.zip 字节、`base_url/token` 必须为空），再跑一次是
noop（续跑判定认得出同一段，不从 start_it 重来）。

门禁：nn python gate **1154 passed / exit 0**；根 `bun run check` **1853 pass / 0 fail**。

### 未做（不写成已做）

① **自动补传**（用户同一条需求的后半句「中途能连上 hub 就自动恢复产物在线回传」）——设计已定：
每轮 best-effort `POST /offline/artifact`（按 `(run_id, it)` 幂等、首写锁定）+ 轮末
`POST /offline/result`；`GET /health` 轻量探活；连不上静默跳过、下轮再试；产物目录 `delivered.json`
记已投递项（重启后续投）；hub 侧落 `<job_root>/offline/<run_id>/` 并记 `offline_artifact` /
`offline_result` 账本事件；token 走 secret 不进包。② 真云端（Kaggle/Colab）端到端一次——本机无
GPU 节点。③ 控制台入口（导出按钮 / 离线进度显示）。

---

## §10 半离线整段落地：kind="run"（一次领走整段，节点自主跑完 + 产物可打包下载）（2026-09-17）

**为什么记这一笔**：新增一种 job 语义（跨层协议 + 新的执行器 + 新的产物面），并抓到两个真缺陷；
决策与理由（含四条被否决的备选）见 `DECISIONS.md` §2026-09-17-goalnn-halfoffline-run。

### 需求与判据（用户 2026-09-17）

> 「云机从 hub 领到训练任务（课程、初始权重、代码）后，即使本机 hub 一直失联，它也能全程自主
> 完成训练，并以 kaggle/colab 官方支持方式提供产物（每轮权重和指标）打包下载。」

三问已答：**一次领 = 整段**；**逐轮权重 + 指标 + 可续跑**；**不在云上跑评估**（只记训练指标）。

### 形状：kind="run" 是 kind="iter" 的延长，不是 fork

```
hub（哨兵）                                  云机（自主段执行器）
  build_plan → plan.json（对集入参 + argv 模板 + end_it）
  publish_job(kind="run", plan_bytes=…)  ───► payload: init_weights.json + plan.json + ts_code.zip
                                              ① verify_plan_file（sha / 形状 / 全段对集指纹）→ 一局不跑就拒收
                                              ② run_job(it)（与 kind=iter **同一**执行链）
                                              ③ run_plan_job 尾巴：逐轮合成同构 iter job → run_job
                                                 └ 每轮回传的权重 = 下一轮 payload 的 init_weights.json
                                                 └ opt 走 blob_cache（Adam 动量不丢）
                                              ④ 逐轮落产物：it-NNN/{weights.json,opt.tar} + metrics.jsonl
                                                 + state.json + LATEST.zip；收尾 artifacts.zip
  wait_job(timeout=8h)  ◄───── 合并结果（末轮形状 + iters 明细 + it_end + artifacts 元信息）
  verify_and_land（零新代码：三个指纹逐字段对的是本 job 自己）
  run_segment 事件（逐轮指标进控制台）+ it 跳到段尾，照常走本机结算/归档
```

轮次语义的一条硬约束：**段中间那些轮不派发本机 eval**——它们的权重不在本机归档里，用活指针充
`W(it-1)` 就是刚修过的「eval 标签超前一轮」。段尾权重由下一轮（或收官 drain）正常派发。

### 两个被测试抓出来的真缺陷（都已进回归）

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| 1 | `ArtifactStore.start` 用**调用方传入**的计划 sha 做续跑判据，而目录写盘的是另一份格式（hub 走 `dump_plan` 的规范形 `sort_keys=True`） | 新会话拿同一目录永远算不出相等值 ⇒ 每次都被当成**新段**从 `start_it` 重跑；「关掉会话明天接着跑」静默退化成重跑，只有对着日志才看得出来 | 判据改为**写盘之后**对磁盘上的 `plan.json` 算 sha（自描述）；传入值另存 `plan_sha256_declared` 供审计 |
| 2 | 起点快照（`_seed_start_checkpoint`）往 `metrics.jsonl` 写一行（无 agg/report） | 「账本一行 = 一轮、it 唯一」失效，并逼下游用「过滤掉没有 report 的行」绕开——用过滤器掩盖一条本不该写的行 | `checkpoint(..., row=None)` = 只落 checkpoint 不记账；`_combined` 的 report 过滤器随之删掉 |

两个都是**先写测试、后改代码**；`test_standalone_resume_continues_without_duplicate_rows` 就是缺陷 1 的回归。

### 验证面（本机可跑的）

- `tests/test_plan.py`(8)：对集重放逐位一致（rotate / curriculum / seed-rotate 三模式）、
  argv 模板重定向恒等、`PAIR_ARG_FIELDS` 漏字段在**发布期**就红、`pairs_fp` 顺序敏感。
- `tests/test_run_loop.py`(11)：**注入 run_job 替身**跑整条链——替身自己开 payload 验
  `init_weights.json` 的字节（所以「权重逐轮传下去了」不是自报），逐轮 job 与 kind=iter 逐字段同构
  （逐局 stage/seed、`--wver` = 该轮 init 的 sha），合并结果**过 `validate_result`**；预算/上限/重领/
  失败四种停机点都能续跑且账本 `it` 唯一。
- `tests/test_run_segment.py`(12)：段长与等待上限的解析优先级（CLI > courses > rl > 缺省关）、
  `max_iters=n-1` 的区间语义、`publish_job(kind="run")` 把 `plan.json` 放进 payload 且 manifest 记 sha、
  缺计划/缺规格/带本地 shard/缺 ts_code sha 一律拒发、同一计划重发同 job_id、发布结果过协议层校验。
- 门禁：nn python gate（ruff + mypy + pytest tests/ + e2e）**1147 passed / exit 0**；
  根 `bun run check` **1853 pass / 0 fail**。

### 未做（不写成已做）

① 真云端（Kaggle/Colab）端到端跑一次——本机无 GPU 节点，`run_remote` 这条路没跑过；
② 段内**进度上报到控制台**（长段期间 hub 只有等待，逐轮指标要等段尾才可见）；
③ 控制台启动弹窗的 `run_iters` 选项（现只有 rl-config / CLI）；
④ push 传输的等待预算仍 1800s（半离线的自然形态是 pull：Kaggle worker 在会话内长期驻留）。

---

## §9 节点确定性失败带原因回传控制面：`POST /jobs/{id}/fail` + `/result` 410（2026-09-17）

**为什么记这一笔**：这是**跨层协议**新增（新端点、新终局状态、新的失败分类语义），且改了
「失败即终局 vs 逐轮重试」两条既有语义的交界，按 §5 硬规则入账。决策与理由（含四条被否决的备选）
见 `DECISIONS.md` §2026-09-17-job-fail-report。

### 缺口（用户提问暴露，2026-09-17）

云机**确定性**失败（bun 装不上 / TS 运行时取不到 / argv 非法）此前只落在**云机日志**里：

| 链路 | 旧行为 | 训练侧看到 |
|---|---|---|
| pull（worker 轮询 hub） | `worker_loop` 的 `except ProtocolError` 只打一行 `REJECTED … skip (not retried)`——不回传、不还租约 | `wait_job` 等满 **25 分钟**超时 ⇒ 一行「超时」（把能力缺失写成了网络/排队问题） |
| push（hub 推节点） | 节点 `worker_server` 对 failed 返回 **500** | `push_client.wait_result` 把 500 当**瞬时错误**重试到 **1800s** 预算耗尽 |

两条路都白烧一个超时窗口，且重试后通常再撞同一堵墙。

### 改后契约

```
节点判定「这台机器跑不成」
   └─ POST /jobs/{id}/fail  {reason(必填), kind, detail, worker}      # bearer + 活租约须持有人
        └─ fail.json  首写锁定：已有结果不收失败 / 首个原因胜出（原子 tmp+replace）
             ├─ GET /jobs/{id}/result  → 410 + 原因 + fail_kind + fail_detail
             ├─ GET /jobs/{id}/status  → state="failed" + 原因
             ├─ claimable_job_ids()    → 排除（换节点只是重演同一失败）
             └─ 账本                   → job_failed{job_id, reason, kind, worker}
训练侧：wait_job/ wait_result  → 立刻抛 JobFailedError(reason, kind, detail)
        └─ _abort_node_failure    → 第一次就写 gate_verdict: ABORT（真原因入判决）+ 停腿
逐轮重试：publish_job 重发同 job（同幂等键 → 同 job_id）→ 清掉 fail.json（重发即重试）
```

用途边界（写进代码与决策）：**只有确定性失败走这条路**——瞬时失败（网络/5xx）仍走 `release`
回池、不报 `fail`；`CodeChangedError` 刻意不报（它靠重启进程 + 租约回池自愈，报了会把可恢复的
job 钉死）。

### 改动清单（file → 改后行为）

| 位置 | 改后行为 |
|---|---|
| `remote/protocol.py` | `FAIL_NAME="fail.json"` / `FAIL_BODY_MAX=64KB` / `JobFailedError(RuntimeError)`（携 `kind`/`detail`，文档写清与 Retryable/ProtocolError 的分界） |
| `remote/hub_server.py` | `store_job_failure`（有结果不收 / 首写胜 / 原子写）+ `job_failure` + `POST /jobs/{id}/fail`（`reason` 必填、体有界、租约同 result）+ `_get_result` 410 + `_get_status` failed + `claimable_job_ids` 排除 + 账本 `job_failed` |
| `remote/hub_client.py` | `report_job_failure`（尽力而为，绝不炸 worker 主循环）；`wait_job` 对 410 与收尾二次确认的 `state=failed` 立即抛；`publish_job` 重发时清 `fail.json` |
| `remote/worker.py` | `except ProtocolError` 分支补回报（带 `worker_tag()`=`host:pid`，多机共用 token 时定位现场的唯一线索）+ `_failure_detail`（traceback **尾段**：原因是最后一帧） |
| `remote/worker_server.py` | failed 响应 **500 → 410**（500 在 `wait_result` 里是「瞬时错误」语义）；`set_error` 记 `kind` |
| `remote/push_client.py` | `wait_result` 见 410 → 立即抛 `JobFailedError` |
| `rl/loop_steps.py` | `remote_retryable_exceptions()` 收 `JobFailedError`（不进来就会冒泡到 loop_core 通用兜底杀进程）；`_abort_node_failure` 写 ABORT（真原因）+ 停腿，不耗连败配额、不降级；`_push_job_round` 全节点失败时**原样抛**节点失败而非包成 `RetryableError` |
| `dashboard/src/server/api/ppo-queue.ts` | `job_failed`/`fail.json` 不算「排队超时」；账本读法改 **last-write-wins**（旧口径「出现过终局即关闭」会把重发的同一 job 永久当已关闭，真卡住时不告警） |

### 回归与实测

- `nn-training/tests/test_job_fail_report.py`（**9 例**）：端点/校验/租约/首写/410/status/账本、`wait_job`
  **秒级**失败（断言 `<10s`，旧路 1500s）、收尾二次确认也认失败、worker 回报到位、push 410、
  push round 不被包成 RetryableError、**重发清标记**（不清 = 重试永久钉死，故必须钉住）。
- `nn-training/tests/test_remote_degrade.py`（+2 例）：`JobFailedError` 首败即 ABORT 且 `_remote_fail==0`、
  不降级、`remote_calls==1`（不重试）；在捕获集合里。
- `dashboard/tests/server-api-ppo-queue.test.ts`（+2 例）：`job_failed`/`fail.json` 不误报；失败后重发又被盯排队。
- 实测（2026-09-17 本机）：整个 `wait_job` 快速失败用例含起服务仅 **0.52s**（预算 25min）。
- 门禁：nn python gate（ruff + mypy + pytest tests/+e2e/）**exit 0**（1112 例）· root `bun run check`
  **1853 pass / 0 fail** · dashboard typecheck + **457 pass / 0 fail** + 三份 bundle ok。

### 未做（不写成已做）

- 真远程轮次的端到端验证（本机无节点可跑）。
- 节点侧 preflight **硬门**（计划 §5.3：bun 版本对账不匹配直接拒单）仍只有记录与日志。

---

---

## §8 M3 rollout 上云落地：新 job kind「一整轮」（kind=iter）＋ TS 运行时打包（2026-09-17）


**为什么记这一笔**：这是本仓**训练架构**层面的新增（新的 job kind、新的传输实体、新的执行位置开关），
按 §5 硬规则入账。方案见 `plan/remote-wire-remediation.plan.md` §5；决策与理由见
`DECISIONS.md` §2026-09-17-goalnn-rollout-on-cloud。

### 开工依据（先把话说清楚：这是**人拍板**，不是门开了）

计划 §6 的原文是「门 1 命中 ⇒ M3 直接留档不做」，而门 1 在 M1 实测里已经命中（`http2` 把
2MiB 上行压到 p50 4.8s，见 §6）。本次开工依据是**用户指令**：目标腿是 TPU 实例
（v3-8 = 96 vCPU），rollout 上云在那里收益极高。即：**门的结论没变，是决策权变了**。
收益前提也照旧：≥16 vCPU 的腿才成立（GPU T4×2 = 4 vCPU 直接否，见可行性报告 §3.3）。

### 改动清单（file:line → 改后行为）

| 位置 | 改后行为 |
|---|---|
| `remote/protocol.py` | 新 `kind="iter"`（走 BC 开过的 kind 通道）：追加必填 `ts_code_sha256` + `rollout`；`mode` 红线互斥照旧；`validate_rollout_spec`（argv 白名单 = 只放行 `tools/sim/export-rl-rollout.ts`；`--out`/`--weights` 必须 job 内相对路径；逐局 stage/seed 不重复）；`iter_declared_entries`/`iter_expected_data_fp`（**声明集**的 data_fp，与实产集同一函数两侧各算一次）；result 增 `report` 必填校验（同 BC 分支先例） |
| `remote/hub_client.py` | `pack_ts_code_zip`（`src/**`+`tools/**` 的 `.ts/.jsonc/.wasm`，固定时间戳 ⇒ 内容寻址；**整棵 tools/** 不是只 tools/sim——实测依赖闭包跨到 `../eval/godai-score`）；`publish_job(kind="iter", rollout_spec=…)`：payload **不含 shard**、`data_fp` 用声明集、ts_code.zip 拷进 job 目录、有 opt blob 也仍带 `init_weights.json`（节点要用它跑 rollout）；`verify_and_land` 对 iter 轮按声明集校验（hub 侧无本地 shard 可重算） |
| `remote/iter_rollout.py`（新） | 节点侧执行器：线程池 `bun <argv...>`（cwd = TS 代码根、job 侧路径绝化）→ 逐位校验实产 shard 集 == 声明集 → `combine_reports` 聚合（与本机 rollout 同一个聚合函数）→ 返回 report/shard_dirs/逐局秒数/bun 版本 |
| `rl/iter_job.py`（新） | hub 侧规格构造：**复用** `rl/cmd.build_rollout_cmd`（三导出器 + 课程覆盖 + D14 血缘的唯一拼装点）只换路径，丢掉 argv[0]（本机 bun 绝对路径在云机上无意义） |
| `remote/worker.py` / `worker_server.py` / `hub_server.py` / `push_client.py` | TS 运行时四件套：内容寻址缓存 `ts_code_cache/<sha>`（**已加进 `prune_job_dirs` 豁免名单**，M2 的 `blob_cache` 就是漏了这个）、`/ts-code-sha` 探测、`/jobs/{id}/ts_code` 下载、`428 ts-code-missing` 门、wire 记 `ts_code_bytes/hit` |
| `rl/loop_steps.py` / `loop_core.py` / `cli.py` | `--rollout-src auto|local|node`（缺省 auto→local）；`_remote_iter` = 整轮上云；node 轮本机**完全不采样**（跳过 `_rollout_phase`，也不预采/不补波）；`_rollout_source()` 读 rl-config（CLI > `courses.<课>.rollout_src` > `rl.rollout_src` > local，D14 血缘：选项永不进 curricula） |
| `dashboard/src/{core,stack,server,web}` | 启动弹窗新增「rollout」选项（本机/上云(节点)/auto）+ 当前生效值；route 白名单（非法 400）；preset 落 `rl.rollout_src`（**字符串域，原样落，不过任何换算**——与 `slim` 的双域相反）；console-state additive |

**回退开关**：`--rollout-src local`（= 缺省；逐字节回到旧行为：本机采样 + 只把 PPO 送云）。

### 安全阀与互斥（配错要响亮，不许静默降级）

1. `rollout_src=node` **与 `--target-transitions` 互斥**（补波要求训练侧反复读本地 shard，而上云轮的
   shard 在节点上、跑完即毁）⇒ 配错即 `SystemExit`；
2. 发布 iter job 时 traj 目录**已有本地 shard** ⇒ 拒发（否则 = 双份采集，且下一轮又会被自己拒绝）；
3. 节点侧实产 shard 集 ≠ 声明集 ⇒ `ProtocolError` 拒收（漏局/多局/`wver` 漂都不会因为重试而变好）；
4. `opt_sha` 有值而 blob 取不到 ⇒ 响亮失败（M2 既有规矩，上云轮同样适用）。

### 控制面迁移清点（计划 §5.4 说的「最容易漏一半的地方」）

| 读取点 | 上云轮处理 |
|---|---|
| 本地采样 / 预采 | loop_core 直接跳过 `_rollout_phase` + `spawn_next_collect`（不是「配成 0」，是**结构上不可能**双采） |
| `rollout_sec` | 用节点回传 `report.elapsedSec`（t_rollout 含「等节点跑完 rollout + PPO」的整段墙钟，拿它当采集时间 = 假指标） |
| `metrics_stats` | **跳过**（shard 不在本地，硬跑只会写一份 `shards=0` 的假精度统计），逐维度口径改由 report 的 `dimMeans/scoreStats` 承担 |
| `_check_quota_incident` | **跳过**（上云轮本地零 shard 是预期，否则每轮喊「检查配额」把真事故淹掉） |
| eval | 零改动（仍在本地 hub 用 bun 跑，权重照旧下行归档） |
| 账本/控制台 | `wire.rollout_src` 落每轮取值；「传输」页已有该列（M0 铺的） |

### 实施中踩到 / 修掉的四个坑（都有回归测试）

1. **`_remote_iter` 绕过了 4xx 立即停腿判据**：上云轮不走 `_remote_ppo_or_degrade`（loop_core 跳过
   `_serial_ppo`），于是 x3-step 事故那种「403 白烧 5×30s 重发同一 job 才死」会重演 ⇒ 在
   `_remote_iter` 里补上同一分类（`fatal_remote_http` → 写 ABORT 判决 + `_leg_abort`）。
2. **`push_client` 的 `428 ts-code-missing` 没置 `need_ts`**：典型场景是「探针说缓存命中、真 POST 时
   缓存已不在」（并发清理 / 两课共享节点），此时 `need_ts` 本是 False ⇒ 一直重发不带 ts 的体、
   白烧满重试预算后整轮失败。与 `code-missing` 的 `need_code = True` 同规。**先复现再修**（§7.1）。
3. **`ts_code_cache` 必须进 `prune_job_dirs` 豁免名单**——否则每轮必 miss，而日志上一切正常。
   这正是 M2 `blob_cache` 的同一个坑，所以顺手把热替换测试的期望集也扩成三棵缓存树。
4. **`pack_ts_code_zip` 的白名单不能只放 `tools/sim`**：`export-rl-rollout.ts` 的依赖闭包跨到
   `../eval/godai-score`（v7 评分口径）。手写「该打哪几个子目录」就是在猜依赖图 ⇒ 打整棵
   `tools/**`，并靠 `test_remote_iter_real_bun.py`（真 bun 真跑）当哨兵。

### 本机验收（能测的都测了）

| 计划 §5.5 条目 | 状态 |
|---|---|
| ① 逐位对拍（节点 shard == 本机 rollout，逐字节 diff） | **已验**：`tests/test_remote_iter_real_bun.py`（真 bun + 真权重 + 真 `pack_ts_code_zip` 解包），同 argv 跑两遍逐文件比对，含 `_rl_report.json` 全字段（除 `elapsedSec`） |
| ② report 等价（winRate/outcomes/ticks 同种子） | **已验**：同上，聚合报告 `totalTicks/totalSamples/outcomes/winRate` 落在单局口径上 |
| ③ 电量账 `up_bytes ≈ 0` | **部分**：payload 确实只剩 `init_weights.json`（单测断言「不含 shard」），但**真远程轮次的绝对值未测**（本机无节点可跑） |
| ④ 可回退 `rollout_src=local` | **已验（构造性）**：非 iter 轮 manifest 不含新键、`shard_dirs` 走原路 ⇒ 逐字节旧行为 |
| ⑤ 协议用例（缺字段拒收 / mode 互斥 / report 校验） | **已验**：`tests/test_remote_iter.py`（51 例）+ `test_remote_ppo.py` 扩 1 例 |

**未做（不写成已做）**：真云机/真远程轮次的绝对值（`wire.up_sec`、每轮墙钟、TPU 腿上的 target ~10s 量级）；
本机也没有跑过「hub + localWorker + trainer `--rollout-src node`」的整条本机闭环（那需要一次真 PPO 轮）。
⇒ 下一步的开门条件：拿 TPU 实例跑 ≥2 个独立 run 各 ≥8 轮，比 `rollout_sec`/`ppo_sec`/`wire.up_bytes` 中位。

### 测试与门禁

新增/扩写：`tests/test_remote_iter.py`（协议 + 规格 + 节点执行器 + ts_code 缓存 + 失败语义 + 控制面两处跳过）、
`tests/test_remote_iter_real_bun.py`（逐位对拍）、`tests/test_remote_ppo.py`（`/ts_code` × `/blob` 两条 GET 端点）、
`e2e/test_push_mode_integration.py`（+5：ts 缓存命中/未命中/428 补传/缺字节响亮失败/真 worker_server 闭环）、
`tests/test_remote_hotswap.py`（豁免名单扩到三棵树）、`dashboard/tests/rollout-src-launch-option.test.ts`（+8）。
门禁：nn pytest+e2e **exit 0**（ruff/mypy 干净）、root `bun run check` **1853 pass / 0 fail**、
dashboard **427 pass / 0 fail** + 三份 bundle ok。

---

---

## §7 远程传输三改 M0–M2 落地：统一计量 + 隧道协议开关 + 协议瘦身（2026-09-17）


**为什么记这一笔**：本笔是**训练架构变更**（云腿线协议 + 校验语义 + 新的运行期开关），按 §5 硬规则入账。
方案与十项验收口径见 `plan/remote-wire-remediation.plan.md`；决策见 `DECISIONS.md` §2026-09-17-goalnn-remotewire-m0m2。
切片提交：**M0 `0bc7a69`**（计量）/ **M1 `fa6a34f`**（隧道协议）/ **M2 `a59b1ef`**（瘦身）。

### 改动清单（file → 改后行为）

| 位置 | 改后行为 |
|---|---|
| `rl/loop_steps.py`（`_remote_ppo`/`_push_job_round`）+ `rl/events.py` | iteration 事件增 additive `wire` 子字典（up/down_bytes、up/pack_sec、blobs_miss、protocol、edge_ip、slim、rollout_src）；`_wire_from_result` 把 worker 半与 hub 半合成一份账 |
| `remote/worker.py` | result 增 `wire`（payload_bytes/dl_sec/unpack/opt_restore/result_bytes/blob_hits/blob_miss_bytes）；`blob_cache/<sha>` 落盘自身产出的 opt；`prune_job_dirs` **豁免** `blob_cache`（否则下一轮必 miss——实施中踩到的真坑） |
| `remote/hub_server.py` | `/jobs/{id}/blob?name=opt\|ref`、serve payload/收 result 时记 `sent_bytes`/`recv_bytes`、`/admin/net-probe?bytes=N`（鉴权 + 固定种子填充，同 bytes 逐字节相同） |
| `remote/push_client.py` / `remote/worker_server.py` | `/blob-sha?sha=` 探测；`/job` 体默认 v2（BRJ2）+ 4xx 一次 JSON 退路 |
| `remote/{protocol,hub_client}.py` | B1 不打占位 `manifest.json`、B2 不打 `opt_init.tar.b64`、B4 有 opt 时不带 `init_weights.json`；B3 `opt_sha`/`ref_sha` 内容寻址（可选键，`slim=false` 逐字节回旧行为） |
| `dashboard/src/stack/{specs,hub,core/types}.ts` + `server/{actions,api}` + `TrainLaunchModal.tsx` | `cf_protocol`(`http2`|`quic`|`auto`，缺省 http2) / `cf_edge_ip`(4|6|auto) 一路到 cloudflared 命令行；抽出 `cfTunnelArgs` 供两处 spawn 共用；复用旧隧道时**比对登记的协议**，不一致则杀旧起新 |

**回退开关**：`slim`（瘦身，缺省关＝旧字节行为）、`cf_protocol`/`cf_edge_ip`（隧道，缺省 `http2`/`4`）。

### 实测数字（本机闭环，`tmp/m2-smoke.log` / `tmp/m2b-e2e.log`）

| 量 | it1（冷） | it2（同会话） |
|---|---:|---:|
| published = hub.sent = worker.payload | 1,683,776 | 1,403,724 |
| blob_hits / blob_miss_bytes | 0 / 0（首轮无 opt_sha） | **1 / 0** |
| worker 结果体（v2 vs 同内容 JSON） | 1,039,382 vs 1,385,656 | 1,041,907 vs 1,389,026 |

- it2 日志出现 `model/opt 从 opt_init（cache）恢复（Adam 动量延续，D5）` ⇒ **1.19MB 的 opt 整轮没过线**（B3 的设计意图）。
- push 侧 B5：真 `worker_server` × 真 `push_client` e2e 实测 v2 体比同内容 JSON 体小 **>20%**（断言在 `e2e/test_push_mode_integration.py`）。
- 门禁：`bun run check` 1853 pass / 0 fail；`bash tools/githook/nn-py-safe.sh -m pytest nn-training/tests nn-training/e2e -q` 310 pass / 0 fail；`bun dashboard/src/server/build.ts` 三份 bundle 通过。

> 📺 **在哪看**：控制台详情抽屉新增「**传输**」页（`dashboard/src/web/app/panels/WirePanel.tsx`）
> —— 上半是每轮 `wire` 账（上行/下行/打包/blob 未命中/协议/瘦身 + 最近 12 轮走势表，数据源
> `metrics.iters[].wire`），下半是 `tmp/tunnel-ab-*.json` 的 A/B 表（p50/p90/max + 倍率徽章，
> 新→旧）。从此不必翻日志/JSON 才能读到这两组数。

### M1 隧道 A/B 实测（已跑：`remote/tunnel_ab_probe.py`，2026-09-17 本机）

探针自建环境（真 hub-server + `cloudflared tunnel --protocol <p> --edge-ip-version 4` × 每腿一条
quick tunnel），每腿每方向 2MiB × N 发；`loopback` 腿作基线。**每臂 2 个独立 run**（run1/run2 各 5 发，
run3 两臂各 8 发）。原始 JSON：`tmp/tunnel-ab-{1,2,3}.json`。

| 腿 | 方向 | run | n | p50 | p90 | max | p50 吞吐 |
|---|---|---|---:|---:|---:|---:|---:|
| loopback | up | 1 | 5 | 0.02s | 0.02s | 0.03s | 890 Mbps |
| loopback | down | 1 | 5 | 0.03s | 0.03s | 0.03s | 576 Mbps |
| **http2** | **up** | 1 / 2 / 3 | 5/5/8 | **4.84 / 4.66 / 4.85s** | 4.84 / 5.90 / 5.39s | 5.26 / 6.49 / 9.41s | ~3.5 Mbps |
| http2 | down | 1 / 2 / 3 | 5/5/8 | 4.80 / 5.09 / 5.30s | 5.03 / 6.61 / 9.26s | 8.72 / 8.42 / 9.44s | ~3.3 Mbps |
| **quic** | **up** | 2 / 3 | 5/8 | **23.42 / 33.04s** | 25.40 / 36.34s | 43.54 / 49.88s | 0.5–0.7 Mbps |
| quic | down | 2 / 3 | 5/8 | 8.50 / 6.20s | 8.90 / 7.49s | 9.77 / 9.07s | 2.0–2.7 Mbps |

**判定（§3.4 判据）**：`http2` 腿 **p50 明显更快**（上行 4.7–4.9s vs 23–33s，**5–7×**；下行 1.2–1.8×），
且 **8 连发无退化趋势**（run3 上行 3.3–9.4s 抖动，p50 仍 4.85s）；`quic` 腿**复现退化**且抖动极大
（p50 23s 而 max 43.5s）—— 与「ISP 对 QUIC(UDP/443) QoS 降质」的病灶签名一致。
⇒ **决策门 1：命中。** 默认取 `http2`/`edge-ip-version 4` 是对的（即 M1 缺省值）。

⚠ **绝对数不可直接换算每轮耗时**：客户端在本机，请求出一遍家宽、响应回一遍（§2.3 已声明）。
真轮次的传输量约 1.2MB 上 + 0.86MB 下 ⇒ 按本表 p50 约 6–8s（远低于门 1 的 20s）。
真实 payload 形状 + 宿主路径下的绝对值仍需云机确认。

### 实施中发现的坑（同批）

- **本机环境代理会杀死隧道探测**：本机设了 `HTTPS_PROXY=http://127.0.0.1:7890`，而 `NO_PROXY` 只含
  localhost/127.0.0.1/内网网段 ⇒ 打 `https://*.trycloudflare.com` 被丢进本地代理，拿回
  `SSL: UNEXPECTED_EOF_WHILE_READING`（而 cloudflared 日志里 `Registered tunnel connection` 一切正常，
  极易误判成「隧道坏了」）。探针客户端已固定 `ProxyHandler({})` 绕过；**云端 worker 没有这种代理**，
  所以按直连量才是对的。
- quick tunnel 的 URL 在 edge **注册完成前**就写进日志 ⇒ 必须先用 `/ping` 就绪门等它真开始服务，
  否则「连接没建好」会被记成「协议慢」，恰好污染本探针唯一要量的东西。

### B6（xz preset 3→6）已量、**不采用**（2026-09-17，本机，真 payload）

测量口径：`tmp/measure-xz-preset.py` —— 取 `tmp/x3-power/remote-jobs/*/payload.tar.xz`（2026-09-15 那次真
self-node push 训练留下的真 job），解包出真 shard 目录，再调**真** `pack_payload`（`PAYLOAD_XZ_PRESET`
按档位改）。3 份独立真 payload，每份 240 个 shard / 裸 ~270 MB。

| job | preset 3 体积 / 打包 | preset 6 体积 / 打包 | 体积 | 打包耗时 |
|---|---|---:|---:|---:|
| `05cb2d44acd0c2d0` | 1,198,096 B / 3.24s | 1,162,552 B / 9.44s | **−3.0%** | **+191%** (+6.20s) |
| `0cb44569e07c7890` | 1,187,392 B / 3.13s | 1,156,264 B / 9.35s | **−2.6%** | **+199%** (+6.22s) |
| `132f5e1cea7c4e16` | 1,227,432 B / 3.47s | 1,192,388 B / 10.00s | **−2.9%** | **+188%** (+6.53s) |

**判定：不动档位**（实测写进 `remote/protocol.py` 常量旁的注释，防止将来重测）。理由：省 ~34 KB，
而按隧道实测 ~3.5 Mbps 那 34 KB 只值 ~0.08s 传输，代价是**每轮关键路径 +6.3s CPU** —— 净亏，
且远低于计划 §4.2 的「收益 <10% 就别做」门槛。解包侧未量到收益（3 份均落在噪声内）。

**副产物：B1/B2/B4 在真数据上的独立验证**（不是合成 shard）：同一批 job 的原盘 payload
（含占位 `manifest.json` + `opt_init.tar.b64` + `init_weights.json`）为 2,045,276 / 2,034,536 / 2,074,996 B，
只打 shard 后为 1,198,096 / 1,187,392 / 1,227,432 B ⇒ **−41.4% / −41.6% / −40.8%**。

### 补记：瘦身开关进启动选项 + 一个真实指标缺口修复（2026-09-17）

**① M2 的 `slim` 现在能在启动训练时选**（计划 §1.4 要求「每个改动都有运行期开关」＋「启动时
提供选项」）。M1 的隧道选项走齐了一条链（config → console-state → route 白名单 → preset → UI →
显示当前生效值），而 `slim` 之前只落了 config 键 + 指标 ⇒ 想 A/B 只能手改 `rl-config.json`，
等于没有「启动时提供选项」这条路。现已补齐同一条链：

| 环节 | 位置 | 行为 |
|---|---|---|
| 类型/双域 | `core/types.ts` `SlimMode` + `CourseConf.slim` + `rl.slim` | UI/console-state 用 `'on'\|'off'`；rl-config **必须** `1\|0` |
| 解析 | `stack/specs.ts` `resolveSlim` / `slimToCfg` | per-course > `rl.*` > 缺省 **on**（与 python `_d("slim",1)` 同口径）；换算只此一个入口 |
| 生效值 | `api/state-view.ts` `modes.slim` | UI 显示「当前生效」，避免「以为改了其实没改」 |
| 契约 | `api/route.ts` preset 白名单 `on\|off`（非法 400） | 与 `mode`/`cfProtocol` 同写法 |
| 落库 | `actions/preset.ts` | `rl.slim = slimToCfg(opts.slim)`（数值域）+ console-state 存 UI 域 |
| UI | `TrainLaunchModal.tsx` 「瘦身」分段控件 | 开 / 关（A/B 对照）；上次选择进 localStorage；当前生效值上屏 |

**② 修的缺口：`wire.protocol` / `wire.edge_ip` 原本**恒为 null***。`_wire_from_result` 读的是
`getattr(args, "remote_cf_protocol", None)`，但 CLI **从未声明这两个参数**（`cf_protocol` 在整个
python 侧只出现在那两行读取处）⇒ M1 §1.4「开关取值必须写进 iteration 事件」实际没被满足：
控制台能显示「此刻生效值」，却回答不了「改用 http2 之后那几轮 vs 之前那几轮」。修法：
`rl/cli.py` 加 `--remote-cf-protocol` / `--remote-cf-edge-ip`（缺省取 rl-config，即控制台回写的键），
`rl/loop_steps.py` 新增 `_course_cf_tunnel(args)`（与 `_course_push_url` 同口径：CLI >
`courses.<stem>.cf_*` > `rl.cf_*` > None；选项住 rl-config，**永不进 curricula**，D14）。
测试 `nn-training/tests/test_wire_cf_tunnel.py`（12 例：CLI 声明与缺省、四级优先级、不串课、
旧 args/坏 config 不炸训练、端到端进 wire）。

> ⚠ 修正一条早前的说法：前文表格里「push 侧 B5」的实测与本次均为本机闭环；
> `wire.protocol` 现在才会真的非 null —— 在此之前「按协议分组统计」是做不到的。

### 未做（不写成已做）

- **M2 云机绝对值确认**（≥8 轮中位 `wire.up_sec`）**未跑**。
- **M3（rollout 上云）按 §5.1 门留档不做**：门 1 已命中（`http2` 把传输打到 ≪20s），门 2 要等云机绝对值，
  且 M2 之后上行只剩 ~1.2MB —— 按 `plan/kaggle-rollout-feasibility.md` 的算术已经是负交易。

### 教训

- **验收 harness 是「待修资产」时先修再量**：`smoke_loopback` 盘上没有近期运行痕迹，实际跑起来三处已腐坏（v2 schema 权重文件、21 列 vs 39 列的 METRICS_DIM、`blob_cache` 被 prune）。**它碰的正好是要改的函数** ⇒ 修它本身就是交付物，而不是绕开它。
- **新缓存目录必须同时改 prune 名单**：`blob_cache` 第一版每轮必 miss，就是因为 `prune_job_dirs` 只豁免 `code_cache`——「缓存命中率」类 bug 会伪装成「协议没生效」。

---

## §6 hub-server 重启死锁收口：D9 只当「无效鉴权」的守门人 + 回环永不封禁 + 端口级实例锁（2026-09-17）

**为什么记这一笔**：本笔含**训练基础设施架构变更**（D9 鉴权/闭锁语义、hub 启动串行化），
按 §5 硬规则必须入账。事故现场：hub-server「自动崩溃后手动重启失败」，控制台只报「意外退出」。

### 一、事故链（三层同族问题，一次收口）

1. **封禁连坐**：旧 `_auth_ok` **先查 `is_blocked` 再验 token** ⇒ 一次误封（本机组件用陈旧 token
   连打 5 次 `/ping`）把该来源 IP 的**全部**流量（console 健康检查、训练循环、worker 拉活）403
   一小时；而封禁只住**进程内存**、只能靠重启清除。
2. **回环当替罪羊**：cloudflared 回源把**隧道流量也全归成 127.0.0.1** ⇒ 回环上的失败里混着隧道
   里的陌生来源，对回环封禁 = 整台机器的服务面连坐（用户口径：「本地 127.0.0.1 鉴权失败不要锁地址」）。
3. **重启被自己的守卫挡死**：端口守卫（`_port_guard.ensure_port_free`）是「探测 → bind」的 TOCTOU，
   且 Windows `SO_REUSEADDR` 允许双绑（后启动者静默变僵尸）；旧实例活着占着 8787 ⇒ 新实例被拒 ⇒
   **必须重启才能解封、重启却被自己占的端口挡死**，只能人工杀进程。

### 二、修复（四处）

- **D9 改序**（`remote/hub_server.py`）：先验 token；**合法 token 永远放行**，封禁只拒无效鉴权尝试
  （封禁期内的无效尝试 403，且不再计数/不延长）。
- **回环豁免**（`remote/hub_server.py::_is_loopback`）：`127.0.0.0/8` / `::1` / `::ffff:127.0.0.1`
  上的失败**不计数、不封禁**（`is_blocked` 防御性恒 False）；鉴权边界与 `AUTH FAIL` 审计行不变。
- **原子实例锁**（新 `remote/_instance_lock.py`，`nn-training/.<kind>.<port>.lock`，按端口键控）：
  拿锁 → 端口探测 → bind；陈旧锁按「持有者已死 / 命令行缺本服务指纹（PID 复用）」接管（指纹可
  给多个），身份读不到则 fail-closed 拒启；锁文件**写不下**（只读 FS）则 fail-open + 响亮告警。
  覆盖 `hub_server` 与 **`worker_server`**（push 端口，经 `remote_worker_serve`，2026-09-17 补）——
  worker 僵尸更贵：HUB 会把 job POST 进一个没人应答的监听端口，表现为推送静默卡死。
  自带安全存活探测（Windows `GetExitCodeProcess`——**不复用** `train/loop_util._pid_alive`，后者
  在 Windows 走 `os.kill(pid,0)`＝`TerminateProcess` 会杀持有者；该隐患已于同日单独修掉）。
- **控制台侧**：启动前 `reclaimPort` 回收端口幸存者（`stack/hub.ts`，接在 hub/selfNode）；
  「停止 trainer」释放本课 run_rl/run_bc 锁且**先核验进程身份**再停存活持有者
  （`launch/cli.ts::releaseTrainerLock`）。**`workerServe` 路径故意不接 reclaimPort**：
  worker_server 可能在跑数小时的 PPO job，`/ping` 失败就回收它 = 炮掉在途 job；该路径
  会先复用健康的幸存者，不通时锁会响亮拒启并指向日志（含持有者 PID），交人工处置。

### 二补、隧道来源还原（B，用户点名「确认隧道来源到底该不该计数/封禁」）

**背景**：回环豁免（2 号修复）把隧道入口一并豁免了 —— cloudflared 回源把隧道流量全归成
`127.0.0.1`，于是隧道侧**只 401、不计数、不封禁**，等于对公网暴露面零封禁（D9 只对 tailnet 直连 IP 有效）。

**决定 = B**（方案对比与 C+D/E 被否的理由见 DECISIONS 同条）：`remote/hub_server.py::attributed_source(peer, cf)`
—— **只在「TCP 对端是回环」时**采信 `CF-Connecting-IP`（须是合法 IP 字面量且非回环值）⇒ 按**归因 IP**
计数/封禁；其余（无头 / 头非 IP / 头写回环值 / 对端非回环）⇒ 归因 TCP 对端。直连（tailnet）对端
**只认对端 IP**：那台机器能自己写任何头。审计行带上 `peer=` / `src=` / `via=cf|peer`。

**为什么不需要再加「全局退避闸」**（C+D，曾被列为首选）：退避的收益完全建立在「封禁不可用」之上，
B 一生效就重复了；而 B 若失效（头可伪造），最坏后果**两条都良性** —— ① 轮换头值 ⇒ 拿不到封禁，
退化为回环豁免（不会更差）；② 伪造 tailnet 某 worker 的 IP ⇒ 那只拒它的**无效鉴权尝试**，它带
正确 token 的请求照常放行（改序使然）⇒ 不是对合法对端的 DoS。零收益增量 + 要给每条合法路径加一条
延迟分支 ⇒ 不做。

**未实测假设（登记待验）**：CF 边缘**会覆写** `CF-Connecting-IP`。仓里无 CF 头读取先例、quick tunnel
无 ingress 配置、沙箱无网络 ⇒ 无法离线验证。**实测法（2 分钟）**：向隧道发一次带伪造头的无效鉴权
（`curl -H 'Authorization: Bearer wrong' -H 'CF-Connecting-IP: 203.0.113.7' https://<隧道>/ping`），看
`hub-server.out` 的 `AUTH FAIL` 行 `src=` 是伪造值（⇒ 可伪造，回退 = 删 `attributed_source` 的 cf 分支）
还是真实公网出口 IP（⇒ 假设成立）。**预登记的失效后果**见 DECISIONS 同条，两条都良性。

**访问日志也补上来源**（`log_message`）：回源流量原本全写成 `[hub-server 127.0.0.1]`，正是本次事故
排查的最大阻雾（分不清「本机组件」与「隧道里的陌生人」）；现在归因到 `via=cf` 时写成
`[hub-server 127.0.0.1 src=<真实 IP> via=cf]`，本机组件与直连对端逐字保持旧格式（不加噪）。
`log_message` 只打非常规事件，不刷屏；`self.headers is None` 的早期错误路径有护栏。

**回归**：`nn-training/tests/test_hub_auth_d9_order.py` 11 → **19 例**（归因矩阵 / 隧道源 5 次封禁第 6 次
403 / 被封归因 IP 持合法 token 仍放行 / 本机无头组件仍豁免 / 直连对端自带头不算数 / 伪造头无害 /
访问日志带 `src=` 及两个负例 / `headers is None` 不抛）。
A/B 行为取证（`tmp/cf-red-behavior.log`，detached worktree 跑修复前代码）：
`PRE-FIX: 5 次回环+CF头 无效鉴权返回码 = [401,401,401,401,401]`、`is_blocked(203.0.113.7) = False`、
`_auth_fail = {}` ⇒ 隧道入口确实零计数。

### 三、教训（可迁移）

- **「活着但不健康」的进程是重启链路的头号敌人**：所有自我守卫（端口/单实例锁）都必须能区分
  「真双开」与「上一代残骸」，否则守卫本身变成死锁的一环。判据顺序一律：**存活 → 身份核验 →
  接管/拒启**，身份读不到就 fail-closed 且**响亮打印**（不静默共存）。
- **惩罚性状态机只能惩罚「确定恶意」的那一类**：把合法流量与可疑流量放在同一个计数器里，
  在共享来源 IP（回源/代理/NAT）下必然误伤整机；封禁的作用面必须比鉴权边界**更窄**，不能更宽。
- **取证纪律**：401/403 永不静默（2026-09-16 已立），且审计行必须写「不计数/不封禁」这类**语义**
  说明——否则下一次排障会把「回环不封禁」误读成「封禁失灵」；同理，一旦「对端」不再是封禁对象，
  审计行必须同时打印 `peer=` 与 `src=`——只打一个会让「封的是谁」永远无法事后重建。
- **一次收紧会开出新的口子，必须回头补上**：改序（A）与回环豁免（B）都是「减误伤」，但 A 把封禁
  面从「来源 IP」缩到「无效尝试」、B 又豁免了隧道整整一侧 —— 每一步都对，合起来却让公网入口
  零封禁。这就是 B（2026-09-17 追加）存在的原因：**每次放宽都要重新问一遍「那么谁在守门」**。

### 三补、存活探测的 Windows 隐患收口（同日补修，用户点名）

- **隐患**：`train/loop_util.py::_pid_alive` 在 Windows 侧一直是裸 `os.kill(pid, 0)` —— Windows 上
  那不是探测而是 `TerminateProcess(handle, 0)`：`acquire_lock` 判「锁持有者还活着吗」的**只读查询**
  会直接把持有者杀掉（最坏：打死正在训练的 trainer），而 `except Exception → False` 还会把
  「我杀了它」记成「它本来就是死的」，双开护栏静默失效。`run_rl._runrl_pid_alive` 早期就因这条
  隐患不复用它（自己写了安全分支），loop_util 侧因此露了很久。
- **修**：两处同口径 —— Windows 分支走 `GetExitCodeProcess == STILL_ACTIVE`，并补 `pid <= 0 → 不活`
  护栏（POSIX 上 `os.kill(0, 0)` / `os.kill(-1, 0)` 命中**进程组**、实测成功，会把残缺锁文件里的
  0/-1 当成活人持有 ⇒ 同名课永久拒启）。三处同源：`loop_util` / `run_rl` / `remote._instance_lock`。
- **回归**：`nn-training/tests/test_pid_probe_windows_safe.py`（6 例）——注入假 kernel32 + 监视
  `os.kill`，断言 Windows 分支**零 os.kill**、退出码语义、句柄不泄漏、POSIX 分支不变、残缺锁可清理，
  外加一条行程门禁（三处探测必须保留 `os.name == "nt"` 分支）。
  A/B：修复前红 —— `AssertionError: train.loop_util._pid_alive: Windows 分支不得调用 os.kill，实际: [(pid, 0)]`。
  ⚠️ 坑：伪装 Windows 时 `os.name="nt"` 必须**只在探针调用期间**生效并先于异常还原，否则
  `pathlib` 会把路径解析成 `WindowsPath`，pytest 在报错/cache 阶段直接 INTERNALERROR。
- **同类待收口**：`remote/notebook_runtime.py::_pid_alive`、`tools/tmp-clean.py`（同名写法，未动）。

### 三补二、存活探测唯一化 + 隧道 metrics 端口闸（2026-09-17 第三批，用户点名的两个收口）

**存活探测唯一化**（`nn-training/pid_probe.py`，新增）：18 小时内同类隐患在 3 个不同文件各自
踩过一次——`loop_util` 裸 `os.kill`（会杀锁持有者）、`notebook_runtime` **嵌套闭包**+裸 `os.kill`
（不可测，且用来判断 bootstrap 已起的 `serve_pid` 是否还活 ⇒ 在 Windows 上会把 worker_server
直接杀掉）、`tmp-clean` 缺 `pid<=0`（残锁里的 0/-1 命中**进程组**⇒ `training_running()` 恒真 ⇒
运行目录永远不再收敛，实测 `_pid_alive(0) = True`）。根因面 = 「每加一个调用点就多一份可漂移的
实现」，故收敛为**唯一实现** `pid_probe.pid_alive`（stdlib-only 顶层模块，与 `platform_utils` 同层，
`remote/` 与 `train/` 都直接 import 它——两方向都不能反向依赖对方的包）；四份具名薄壳全部委托；
**唯一保留副本** = 仓根 `tools/tmp-clean.py`（根级开发工具不依赖 nn-training 布局），契约由源码门禁守。
门禁升级：`test_pid_probe_windows_safe.py` 现在把**六处入口**放进同一组断言，并用 AST 断言
`nn-training/` 里真调用 `os.kill(pid, 0)` 的文件**只有 `pid_probe.py`**（AST 而非字符串：新写的
docstring 到处在讨论这个坑，且 `os.kill(pid, 15)` 是**故意发的信号**、不属本不变量）。

**隧道 metrics 端口闸**（`dashboard/src/stack/hub.ts`）：cloudflared 是**第三方二进制**，没法在
它内部装实例锁（hub/worker 那层是 python 自己拿 `O_CREAT|O_EXCL`）⇒ 控制台侧回收是它**唯一**的
一道闸。`stepCloudflared` 在 spawn 前 `reclaimPort(metricsPort)`（与 hub/selfNode 同族），
堵住 `supersedeSlotTunnels` 看不见的幸存者（孤儿/登记丢失/控制台重启竞态）。metrics 端口既是
`--metrics` 的 bind 目标、又是 `/ready` 的探测目标，被占着会**同时**造成「新隧道 bind 失败」与
「就绪读数读自旧僵尸」；后者另加**就绪归属**：`core/proc.ts::portOwnedBy(pid, port)`
（唯一实现，早期叫 `tunnelOwnsMetrics`，因 hub/worker 也要用而改成中性名），就绪 = 「本进程持有
该端口 ∧ /ready 200」。

### 三补三、就绪归属推广到监督器（2026-09-17 第四批，用户点名「监督器那条路径也要」）

`ProcSpec` 新增可选字段 **`ownsResource?: (pid) => Promise<boolean>`**，三处声明：
`hubServerSpec`（hub 端口）、`cloudflaredSpec`（metrics 端口）、`workerServeSpec`（push 端口）；
**监督器**（`server.ts::restart`，变更检测重启）与**启动步骤**的就绪判定都变成
「`ownsResource`（未声明 = 不阻塞）∧ `healthy()`」，未就绪时日志点出归因。

为什么监督器同样需要：它杀旧 pid 后紧接着拉起同一条 spec，若新进程 bind 失败
（EADDRINUSE / python 侧双监听守卫拒绝）**早已退出**，而端口上的旧实例照样答 `/ping` / `/ready`
⇒ 监督器把**僵尸的 200 记成「重启成功」**：账本写新 pid、实际服务的是旧进程 —— 这正是
hub-server 重启事故的相位（账本 pid ≠ 真在服务的那一个）。

**实现时发现并修掉的语义漏洞**（记一笔，避免以后重蹈）：`portOwnedBy` 最初把「占用者清单为空」
一律当成「探测不可用 ⇒ 不判死」——但**没人监听**时 lsof/netstat 也返回空，于是「新进程已死」
会被判成「归属 OK」。现用 TCP 探测二次区分：没人监听 ⇒ **false**（要抓的就是这个）；
有人在监听但列不出归属 ⇒ **true**（工具缺失不该让所有组件启动失败）。测试里就有一个真监听
用例把它担住了（hub 端口无人监听 ⇒ `hub.ownsResource!(child.pid) === false`）。

**保留的刻意选择**：监督器重启路径**不接** `reclaimPort`（它杀的是账本里确切 pid、紧接着拉起
同一 spec，无孤儿窗口；kill 失败时回收也一样杀不掉）——只核归属。

### 四、验收

- `nn-training/tests/test_hub_auth_d9_order.py`（11 例）、`nn-training/tests/test_instance_lock.py`（9 例，
  含真进程顺序双启被拒 / 同时三启恰好存活一个）、`dashboard/tests/trainer-lock-release.test.ts`（13 例）。
- A/B 取证：detached worktree 对 HEAD 跑新测试 → 红（`assert 403 == 200`；日志里 127.0.0.1 被 BLOCKED）。
- 第三/四批（存活探测/隧道闸/就绪归属）：`test_pid_probe_windows_safe.py` 6 → **8 例**、
  `dashboard/tests/training-port-reclaim.test.ts` 6 → **15 例**（含 `ownsResource` 声明门禁、
  监督器接线门禁、`portOwnedBy` 空清单两义、真监听下 `hub.ownsResource` 判 false）；A/B 红：worktree 对 HEAD 跑新测
  6 红（含行为级 `tmp-clean._pid_alive(0) = True`），cloudflared 侧 HEAD 上既无 `reclaimPort`
  也无 `tunnelOwnsMetrics`、就绪判定是裸 `tunnelEdgeReady`。门禁：nn python gate（205 源文件）✓、
  `cd dashboard && bun run typecheck && bun run test`（406 例）✓、`bun run check`（1849 例）✓。
  ⚠️ 本批 gate **首跑红过一次**：`tests/test_remote_ppo.py::test_hub_server_auth_and_job_lifecycle`
  报 `Con…`（连接错误）——单跑该文件绿、`--maxfail=99` 单跑绿、重跑全量 gate 也绿 ⇒ 满编 `-n 4`
  下的**负载型 flake**（同 §313 已归档的那一类），与本次改动无关（日志已删，仅存档此判定）。
- 门禁：`bash tools/githook/nn-python-gate.sh` ✓；`cd dashboard && bun run typecheck && bun run test` ✓；
  `bun run check` ✓。决策记录：DECISIONS §2026-09-17-hub-restart-deadlock-hardening · 全文 → 本文件 §32。

---

## §5 BC 训练整合进 云-HUB-LAN：kind=bc 第二任务类型全链（2026-09-13，用户指令五步）

**目标**：BC 从本地手工流程（export-godai-labels + train/bc.py 手工串）升级为与 PPO 同构的
分布式管线：控制台启动 → LAN 集群生成语料 → 云机 poll/接收任务训练 → 权重回传归档。
设计：`plan/bc-cloud-integration.plan.md`；**全部复用既有管线，只加任务类型，不建第二套体系**。

- **协议（remote/protocol.py）**：manifest 可选 `kind`（缺省 "ppo" wire 兼容）；bc 免必填
  reward/γ/λ、追加必填 `arch`、mode 红线 "bc"（串型互斥）；bc result 校验走 `metrics`
  （无 agg）。幂等键/job_id/data_fp 公式不变（bc `init_weights_fp` 恒 "bc"）。
- **云端（remote/worker.py::_run_bc_job）**：run_job 在 code 守卫后按 kind 分叉——D14 血缘
  校验共用 → import `train/bc.py::train`（code.zip 已含 nn-training 全部 .py）→ BC 权重
  （版本化归档字节）+ metrics 回传；cuda-dp→cuda、tpu 拒收；`--echo` 占位回传。
- **HUB 客户端**：publish_job 增 `kind/extra`，init_weights_path 可选（bc 不拷 init 文件）；
  `iter_bc_shard_dirs` + `verify_and_land_bc`（data_fp 重算/commit 对账，无 opt tar）。
  push 模式零改动（worker_server 同 normalize/run_job）。
- **LAN 语料（mode=bc）**：新导出器 `tools/sim/export-godai-bc.ts`（单局复用
  export-godai-labels.exportGame 纯函数 → npyBytes 内存序列化 → BCV2 容器；wins-only 败局
  = `kept:false` 空容器合法结果）；agent `/v1/task` 收 mode=bc（免权重桶）、ping 加
  `bcSupport` 能力位（fail-closed，旧 agent 不派）；入 codehash-files.txt（升级波）。
  `dist_common`：BC_SHARD_FILES/validate 分支/fetch_task wins+nearMissTimes 透传。
- **派发（rl/bc_dispatch.py）**：紧凑调度器——bcSupport 探活门 → (stage,seed) 队列 →
  节点槽位线程 → fetch_task(mode=bc) → 落盘 `<traj>/bc-data/it{r}/bc_s{stage}_seed{seed}/`
  （manifest 补 course_fp/corpus_fp 血缘）。无竞速（语料不需要最快者胜）；局失败重试一次。
- **BC 课程（rl/bc_config.py + curricula/*.bc.jsonc）**：独立文件种类（RL CourseConfig
  extra=forbid 不兼容、mode/gates/schedule 全不适用）；level 引用复用关卡抽离语义；
  `bc_corpus_identity_fp`（env+corpus 参数；train 超参刻意排除）；轮 r 种子
  `[1+(r-1)*seed_rotate, …]`（§15.1 轮转）。首课 **bc-c4.bc.jsonc**（level=arena4，
  student/60ep/wins-only/near-miss 3×，value_coef=0——`docs/nn/legacy.md` §20 M3 教训）。
- **编排器（run_bc.py，torch-free）**：per-course run_bc 锁；账本 `bc_round_completed`
  断点续跑；语料补采 → publish(kind=bc) → push/hub 等待 → verify_and_land_bc →
  backup_weights 归档（`nn-training/weights/<prefix>/<prefix>.it<N>.<ts>.json`）+
  WEIGHTS.md 行（torch-free 复刻）；bc-data 旧轮收敛。--smoke：1 局/max_ticks≤300/
  epochs=1 真一轮，落位即作废（`weights.smoke.json` scratch、不归档、账本零污染）。
- **控制台**：`.bc.jsonc` 全链认课（validateCourseArg/discoverCourses/setCourse/
  sanitizeViewCourse）；BC 课程复用 trainingLoop 组件键 → `bcLoopSpec`（run_bc.py，
  哨兵含 remote 四件套）——启停/监督/退出看门狗零改动；startBcLoop（无 BC 种子播种）；
  smokeTrainBc 三里程碑预演（published job → weights landed → BC SMOKE PASS，伪节点
  **真 BC 训练**非 echo）。
- **E2E 冒烟 PASS**（本机全链实测）：self 节点采语料（arena4 单局）→ push 发布 → 伪 GPU
  节点真 BC 训练（student 67.5K params，1 epoch，2.8s）→ 回传 → verify_and_land_bc 落位 →
  作废退出。回归：nn-python-gate 相关子集 162 绿 + `bun run check` 绿。
- **踩坑三则**：① 单 shard 语料 shard 级切分把全部样本划进 val（train=0 → DataLoader
  num_samples=0 崩）——`make_loaders` 回退样本级切分（test_shard_split 回归锁）；② smoke
  300 tick 内不可能 wins → smoke 关 wins_only；③ agent 结果缓存键不含 wins/nearMiss——
  smoke 轮用独立 iterId 命名空间（`-smoke` 后缀）防缓存回放假结果。
- **运维注意**：LAN 远端节点需 git pull 升级后才有 bcSupport（push 合并后人升）；升级前
  bc 语料只由 self/已升级节点承担（fail-closed 不污染）。

---

## §4 云端停机机制（2026-09-11，用户指令：停机 = 停云端省 GPU 配额，本地进程都不停）

承接 `docs/nn/training-stack.md` §1 的"未决"：§385 只修了 G13 口径，停车仍不省云配额。用户确认语义后落地（DECISIONS §2026-09-11-nntrain-cloud-halt · 全文 → 本文件 §32）：

- **hub**：`/admin/workers/{halt,resume,status}`（Bearer 鉴权，volatile）→ 置位后 `/jobs/next` 下发 `{"halt":true}`。
- **worker**：收到达令即干净退出（keepalive 停、cell 走完）——省配额的有效动作止于"worker 停 + 不再轮询空转"；
  Kaggle/Colab 无 release API，session 释放需手工断连或到 9h 上限（tpu-probe 调研，横幅文案如实写明）。
- **console**：exit-watchdog 在 TrainingLoop 死亡（含设计内停车与崩溃）时自动发 halt（幂等），
  console-state `cloudHalt{at,reason}` 持久化 → 顶栏红色横幅 +「恢复云端」按钮（另提供手动 `cloud-halt`/`cloud-resume` 动作）。
- **验证**：test_hub_workers_halt_flow（hub 侧 401/halt/resume）/ worker_loop halts（worker 退出）/
  poll_job 上浮 halt / cloud-halt.test.ts（幂等跳过、失败不写标、恢复清标）。
- 恢复路径：删停机标记 + hub resume 后，**必须重启云端 worker 会话**才重新入队（横幅明示）。
- **修订（2026-09-11 用户确认"停机≠省配额"）**：worker 退出≠云机释放——Kaggle/Colab 宿主会话不因此停止计费
  （Kaggle 无释放 API、Colab 空闲约 90min 才回收）。真释放只发生在：① Colab 部署下 halt 达令触发
  `google.colab.runtime.unassign()`；② 人工断开。横幅/动作文案改为**诚实警告**（配额照烧→必须人工断开），
  不再声称"省 GPU 配额"。测试断言同步（非 Colab 路径必须出现"手工断开"提示）。
- **最终语义（2026-09-11 用户五条，DECISIONS §2026-09-11-nntrain-cloud-halt-final · 全文 → docs/nn/training-stack.md §25）**：停机=发布"停机命令"
  而非杀 worker——{halt} 随 /jobs/next 同发（有任务同批、空闲单独）；worker 停机过渡**尝试一次**真释放
  （Colab unassign / 其余提示人工断开），**不退出、照常执行任务**（云机不闲置）；halt 清除复位。
  console-state cloudHalt 双态 halted/recovered：红横幅=停机中（含"停不掉继续干活"状态）；灰横幅=曾停机已恢复
  （保留历史，TrainingLoop 重启或手动恢复触发）；本地组件全程不动。

---

---

## §3 四项监控修复落地 + PPO job 竞速模型（§343）+ it24 孤儿租约事故复盘（2026-09-06）

§2 的三处发现（+ backup_prefix 第④项）经用户拍板「修全部问题」后全部落地，
回归测试 nn-training/tests/test_rl_remote_fixes.py（6 项）+ 全量 pytest 312 绿：

### 3.1 修复清单（DECISIONS §342）
1. **lr 折算**：`rl/loop_steps.py _course_iter` 把 `sch['lr']` 同步折进 `args.lr`
   （remote 模式经 publish_job → manifest → worker Adam 生效）。实战影响：it24–35
   本就该 1.5e-4（绝对 iter 查表），修复的实战价值是 **it36+ 精调段正确降到
   5e-5**（消除 3 倍超速 + 无 KL 惩罚叠加风险）。
2. **dup shard 退场**：`rl/dispatch.py` dup-settle 分支退役输家副本目录
   （`_dir` 目录名归一化兼容 local wave/远程 shard 两形态）；`remote/hub_client
   .iter_shard_dirs` 同名去重兜底（manifest mtime 最早者胜）。it24 实测：发布
   retire 2 份残留，`shards=150` 与 expectedGames 平，UserWarning 消失。
3. **EVAL_SEEDS 扩 100**：`rl/eval_local.py`。it25 起评估 100 局（CI ±13pp →
   ±10pp 内），旧 20 seed 前缀不变保持可比。
4. **backup_prefix/backup_dir 按课程生效**：归档落 `nn-training/weights/p4-onset/`。

### 3.2 §343 竞速模型（用户指令，详细权衡见 DECISIONS §343）
PPO job 分发从「租约独占」改为「竞速广播」：`/jobs/next` 对所有轮询者广播同一
open job，先回传结果者胜（store_result 首写锁定，迟到 409 丢弃），结果落盘未
验收的 job 从池中剔除。租约/心跳降级为兼容路径不再参与调度。动机 = it24 事故：

### 3.3 it24 孤儿租约事故复盘（08:45–09:20）
- 时间线：08:45:40 发布 it24 → 3s 后旧 Kaggle 会话领取 → 用户家庭网络断 2–3min
  → Kaggle 页面停更，用户重载页面 → 旧 worker 进程死亡，**租约独占下 job 被锁
  满 30min**（孤儿租约）；trainer `wait_job` 同为 30min 超时 → 09:15:45 超时 →
  iter_error → 幂等重发同一 job → 继续等待。断网期间 trainer 侧 502/URLError
  均被 §1 加固的重试吞掉（设计生效），零数据损失。
- 应急处置（已做）：hub_server 仅重启（租约纯内存态即清零，隧道/TrainingLoop
  不动，Kaggle 的 HUB_URL 不变）→ 自愈从 30min 压到即时。
- 根治（§343 已落地）：竞速模型下「断连重连」零等待——重连的 worker 立即重领
  同一 job 重算，不存在孤儿租约概念。
- **Kaggle 侧操作指引**：页面停更 ≠ worker 死了，先看 cell 状态；重载/重跑
  worker cell 安全（幂等键 + 首写锁定兜底）；worker 代码经 code.zip 每任务下发
  （GET /jobs/{id}/code），notebook 只需保持轮询 cell 运行，无需改代码。
- 监控教训：hub-server.out 的请求时序（payload 下载 / heartbeat / result POST）
  是判断「worker 活没活」的最直接证据——heartbeat 绝迹 + result 恒 404 = 租约
  孤儿，而不是 PPO 慢。

---

## §2 p4-onset 首日监控：lr 调度在 remote 模式未生效 + 重复 shard + 评估截断（2026-09-06，监控发现，待处置）

监控 06:21–08:11 远程推架构首跑（it1–10 完成，it5/it10 两次评估）。训练本身健康
（KL 0.0905→0.0077 单调收敛、熵 0.36 带稳定、value 6.2→2.6），it8 误熔断已由
F4/DECISIONS §339 修复（ENT 改相对崩塌语义 + ent_peak 基线继承，热启动课程自动
豁免，不会复发）。以下三处为监控新发现：

### 2.1 ppo_schedule 的 lr 三段表在 remote 模式下全程未生效（重要，待用户拍板）
- 证据链：`rl/loop_steps.py _course_iter` 只把 `sch['lr']` 写进 `self._opt.param_groups`
  （remote 模式 hub 侧无 optimizer，静默跳过）；`publish_job(..., lr=float(args.lr), ...)`
  传**静态** `args.lr`，全库无 `args.lr =` 赋值点；worker 侧 `remote/worker.py:531/538`
  以 `Adam(lr=manifest["lr"])` 建 optimizer。日志双印证：每轮打
  `[course] ppo_schedule@itN: lr=0.0003`，而 iteration 事件 lr 恒 0.00015
  （`rl/events.py` 写 `args.lr`）。
- 影响：it1–35 实际恒定 lr=1.5e-4——warmup 段（≤15，设计 3e-4）只有一半学习率；
  **it36+ 精调段（设计 5e-5）将 3 倍超速**，phase3 又 kl_coef=0 无 KL 惩罚，风险最大。
  kl_coef 经 `args._kl_coef` + manifest **生效**；`kl_cap` 仅 stream 路径消费
  （remote 强制 stream=0 ⇒ **本模式不接线**，打包≠生效；见
  DECISIONS §2026-09-15-goalnn-kl-cap-unwired · 全文 → docs/nn/experiments.md §33）。
- 按 ~2.2 min/iter，it36 约 1 小时内到达。选项：(a) `_course_iter` 把 `sch['lr']`
  同步折进 `args.lr`（一行改动）+ 重启 resume 续跑，it12 起与课程表对齐，
  it1–11 半速段记为既成事实；(b) 维持恒定 1.5e-4 跑完并改课程注释。属实验语义
  变更（AGENTS §15.5），由用户决定。

### 2.2 同 seed shard 双份落盘（dist/<node> 与 wNN 各一份，内容不同）
- 每轮 0–7 个 seed 在 `it{N}/dist/<node>/` 与 `it{N}/wNN/` 各有一份完整 shard
  （it3=7、it4=3、it5=4、it6=4、it7=3、it8=2、it10=1、it11=1；it9 本地-only=0），
  两份 obs sha **不同**（同 seed 同 wver 本应逐字节同——指向跨节点 agent 版本差，
  参数 AGENTS §16.6）。机制高度疑似 tail fan-out 竞速的迟到副本：败者已被拒绝
  结算却仍写盘共存（`rl/queue.py` `docs/nn/legacy.md` §21 修的是误回队，未管盘上残留）。
- 发布端 `remote/hub_client.pack_payload` 对重复 arcname 写 zip → zipfile
  UserWarning 刷屏；worker `unpack_payload` extractall 同名后者胜（排序保证 wNN
  在后）→ 实训只吃 150 个唯一 dir 的一份拷贝，**无双计**，但 payload 虚胖、
  账面 shards=151–157 与 expectedGames=150 不平。修复方向：结算后 retire 输家
  副本的盘上目录。

### 2.3 贪心评估被 EVAL_SEEDS 常量截断为 20 局（配置 100 局不生效）
- `rl/eval_local.py EVAL_SEEDS = (860001, 860002, *range(860003, 860021))` = 20 个
  种子；`rl/eval_dispatch.py:107` `EVAL_SEEDS[:n_seeds]` 把课程
  `eval_games_per_stage: 100` 静默截成 20。20 局胜率 95% CI ±13pp——it5/it10 的
  10%（2/20）与起点 p1-ep60 的 14%（100 局）统计上不可区分，阶段判断目前只能靠
  20 局粗评 + rollout 采样胜率双低通道。

### 2.4 其它观察（备忘）
- 备份正常但课程 `backup_prefix/backup_dir` 未被采用：`rl/loop_steps.py:458` 统一
  用模式前缀 `rl-weights` 落 `nn-training/weights/rl-weights.itN.*.json`
  （it1–11 全部已归档，权重安全）。
- it9 低谷（winRate 2.7%、128/150 超时、熵 0.246）归因：重启窗口内 mac 升级重启、
  a95/a97/a98 codeHash mismatch 被排除 → 150 局全本机采集（rollout 132s vs 常态
  40–66s）。it10 恢复 6.7%，it11 起全舰队回归。若 it11+ 超时占比仍 >50% 则需另行归因。
- it1 的 samples=68002 / mean_ret 8.0 为陈旧 shard 混入（resume=150，昨日多次失败
  尝试的同 init 残留）——趋势判断从 it2 起。
- 超时占比 ~45% 印证课程注释预判（max_ticks=2400 对 4 敌偏紧，后续重标定项）。
  走势数据：tmp/p4-onset/training_log.jsonl 与 eval_log.jsonl。

---

## §1 远程链路冒烟预演：worker --echo + TrainingLoop 作废轮（2026-09-05，DECISIONS §340）

用户拍板：不建虚拟课程——TrainingLoop 跑**真课程**，伪 Kaggle（echo worker）回传 init
权重并带 smoke 标记，TrainingLoop 走完正常落位后识别标记**作废本轮**（it 不前进）。
冒烟保真度 = 与真训练逐字节同路径；作废标记使任何消费方都不会把回显权重当真 PPO
吃进去（自保护）。

落地（`bun run check` / pytest test_remote_ppo 32 绿）：
- `remote/worker.py --echo`：下载/校验/commit/course_fp 全走，跳过 torch 与 PPO，
  result = init 权重回显 + manifest 指纹回显 + `smoke: true`（validate_result 不拒额外键）。
- `rl/loop_steps._remote_ppo`：`verify_and_land` + `mark_job_completed` 后查
  `result["smoke"]` → 抛 `SmokeVoidRoundError`；落位权重与发布时逐字节相同（init 回显），
  无需回滚。
- `rl/loop_core.run`：捕获 SmokeVoidRoundError——`--smoke` 干净退出，真训练 `it -= 1`
  原地重试（不计失败连击、不 sleep）。**作废轮不写 iteration 事件** →
  `last_completed_iter` 续跑锚点零污染（实测账本 0 iteration 事件）。
- `rl/cli.py --smoke`；`tools/hub-start.ts --smoke-only` 新增 Kaggle 交互预演阶段
  （真 job 发布 → echo worker 穿隧道 claim→payload→code→result → 落位 → 作废退出）。
- `remote/hub_client.wait_job` 加固：轮询容忍瞬时网络错误与 5xx（快速隧道抖动曾
  一击废掉整轮迭代 → 连击重试堆积陈旧 pending job）——24/7 隧道运营可靠性修复。
- 闪窗收尾：`archive.py`（git push）/`run_rl.py`（rev-parse）补接
  `platform_utils.POPEN_NO_WINDOW`（console-less 父进程拉 git 会新建可见控制台；
  其余文件的接线早已完成）。
- 闪窗第二三轮排查（看门狗实证法：EnumWindows 扫可见 ConsoleWindowClass 窗口记
  PID）：漏网点共两处——`remote/hub_client.git_head`（每次发布）与
  `dist_common.dirty_hash_files`（每轮派发的 dirty-tree 检查）——均已补接。dirty 检查
  本身保留：它只管**远端** pull 节点的 restart 循环护栏（self 节点经 is_self_node
  豁免、纯重启零 git 操作，2026-08-30 修订），与 code.zip 直接打包无关。
- `tools/hub-start.ts drainStaleJobs`：TrainingLoop 新启动前下架陈旧 pending job
  （删 payload.zip → hub claimable 跳过；账本保留 job_pending 真实历史），避免真
  worker 空烧 GPU 租约。

实测（20:56 冒烟）：发布 8s（断点续跑）→ echo worker 全趟 54s → 落位 → 作废退出；
账本 0 iteration 事件。教训：Bun Windows `detached` = DETACHED_PROCESS（子进程完全
无控制台，hwnd=0 实测），孙进程（git 等）会新建可见控制台闪窗——python 侧子进程
一律挂 POPEN_NO_WINDOW。

---


## §32 决策正文归档（搬自 `DECISIONS.md`，2026-09-23）

> 2026-09-23 把 `DECISIONS.md` 里这些条目的**正文全文**搬到这里（索引行与编号仍留在
> `DECISIONS.md` —— 编号永不重排）。锚点 = `### §<旧编号>`。

### §2026-09-11-nntrain-cloud-halt（2026-09-11，用户指令：停机=停云端省 GPU 限额、本地进程都不停、console 出横幅）

- **背景**：§385 只做了门口径修复，没动"停车不省云配额"：loop 一停只是不再派活，Kaggle/Colab worker 照烧
  （max_idle ≥1h 才退）。用户明确语义：**停机 = 停云端机器省 GPU 配额，本地 hub/console/trainingLoop 一律不停**；
  停机后 console 界面显示红色横幅。
- **备选与否决**：训练进程内自行发停机 —— 否，SIGKILL/OOM 时执行不到、还多一份调用面；只靠 worker 空闲退出 —— 否，
  要等 ≥1h、停机期间照烧；hub 拒发新 job 但不通知 worker —— 否，worker 继续空转轮询烧会话。
- **决定**：① hub 新增管理端点 /admin/workers/halt|resume|status（Bearer 同 worker 鉴权；volatile），置位后
  /jobs/next 下发 {"halt":true}；② worker 收到达令即干净退出（keepalive 停、cell 走完）；③ exit-watchdog 在
  TrainingLoop 死亡（含设计内停车与崩溃）时自动调 hub halt（幂等）+ console-state.json cloudHalt{at,reason}
  持久化；④ console 顶栏红色横幅显示停机原因 +「恢复云端」按钮（手动 cloud-halt/cloud-resume 动作随之提供）。
  云商无 release API 的事实（Kaggle/Colab 需手工断连或到 9h 上限）如实写进横幅文案与注释，不假装全释放。
- **违反后果**：不加则 loop 一停云端照烧（事故复诊/崩溃期间都在空转烧配额）；恢复时漏重启 worker 会话会卡派发——
  横幅与「恢复云端」动作就是主介入路径。
> **（2026-09-11 修订，用户确认＂停机≠省配额＂）**：worker 退出≠云机释放——Kaggle/Colab
> 宿主会话不因此停止计费（Kaggle 无释放 API、Colab 空闲约 90min 才回收）。停机的真实边界：
> ① Colab 部署下 halt 达令触发 `google.colab.runtime.unassign()` 真释放实例；② Kaggle 等无 API——
> 横幅与动作文案改为**诚实警告**（worker 已离线、宿主会话仍在计费、必须人工断开），不再声称＂省 GPU 配额＂。
> 恢复路径不变（hub resume + 重启云端 worker 会话）。

---

### §2026-09-11-remote-worker-hotswap-supervisor（2026-09-11，云端热更新事故修复：os.execve 打掉 notebook kernel，改监督器+子进程）

- **背景**：远程 worker 热更新（CodeChangedError → 重启换代码）原用 `os.execve` 原地替换进程
  镜像。pull 模式 notebook（battle-rl.ipynb）把 `worker_loop` 跑在 **kernel 进程内**——execv 后
  ipykernel 对 sys.stdout 的重定向对象丢失（日志只进 kernel server 控制台、单元格断流），ZMQ
  执行服务不再应答、Jupyter 判定 kernel 死；用户看到「自重启」后单元格无下文 → 按停止 →
  SIGINT → kernel 重启 → 云端会话报废。真实事故序列：ipynb 日志止于「自重启」行，系统日志显示
  worker 仍在跑 job 却被中断 → kernel restarted。
- **备选与否决**：保留 execv、仅让 notebook 拉子进程 —— 否，execv 替换的是 kernel 进程本体，
  任何进程模型下都吃 stdout 重定向与 kernel 服务，属根本错误；notebook 裸跑子进程不转发 —— 否，
  子进程 inherited stdout=fd1=server 控制台，单元格仍看不到（同一根因的另一面）。
- **决定**：① 热替换统一改为「以退出码 HOT_RELOAD_EXIT=86 干净退出，由**监督器**用同一套参数
  重新拉起子进程」——fresh 进程 sys.modules 必然为空，新代码一定生效；② worker.py 新增
  `supervise_worker(restart_argv)`：worker 跑在子进程，stdout/stderr 逐行转发到本进程 stdout
  （notebook 里本进程= kernel，转发保住单元格），退 86 → 同参重拉，KeyboardInterrupt → 先杀子
  再上抛（不留孤儿 worker）；③ `main()` 拆两模式：默认监督器，env `REMOTE_WORKER_CHILD=1`
  的子进程直跑 `worker_loop`；`worker_loop` 传 `restart_argv` = 有监督器（热替换退 86）、
  None = 无监督器（提示人工重启并返回，旧降级行为保持）；④ battle-rl.ipynb `run_pull_worker`
  改调 `supervise_worker(restart_argv)`；CLI `python -m remote_worker` 照常可用（supervisor 包
  一层、rc 原样上浮，M1 冒烟与 `--once` 退出码语义不变）。
- **违反后果**：任何人把热更新改回 execv、或把 worker_loop 直跑进 kernel 并 execv 自重启，都会
  复现「单元格断流 + kernel 判定死亡 + 中断毁会话」；监督器必须由**不 execv 的进程**承担。

---

### §2026-09-12-multi-course-p3b-supersedes-343（多课程：独占加超时租约重启用 §343；worker 侧有界 FIFO）

- **supersedes §343**：PPO job 分发从"竞速广播"改回**独占加超时**——`GET /jobs/next` 领取即设
  租约（owner + expiry + last_heartbeat **同时置**）并下发 `lease_token`；`claimable_job_ids` 排除
  持有未过期租约的 job；`POST /jobs/{id}/result` 有活租约时验 `X-Lease-Token`（无租约照收，兼容
  旧 worker/重发）；首写锁定保留（hub 重启丢租约的兜底）。新常量 `CLAIM_TTL_SEC = 300`；心跳
  60s 续租，且 `heartbeat()` 必须以它为**唯一** TTL 来源（沿用 `LEASE_SEC = 1800` 会让死 worker
  隐身 30min）。hub 记 `last_heartbeat` 并在 `/jobs/status` 暴露 `lease_expires_in`/
  `last_heartbeat_ago`（worker 侧吞错保持现状）。
- **为何敢重启租约**：§343 的竞速广播在多 worker 下让同 job 被重复算、慢者 409 白烧；单 worker
  时代它靠"孤儿零等待重领"避开 it24 白等 30min，但多课程并行需要 worker 之间不撞车。it24 的教训
  由四道闸抵消：TTL 300s + 60s 心跳续租 + 主动 release + 首写锁定兜底；大抖动双算/hub 重启丢租约
  属已知 edge，结果一致。halt 与租约正交（停机不拦分发、不清租约）。
- **备选与否决**：HUB 侧等待（状态应住执行方，竞态）/ worker 多线程并发 PPO（单 GPU 互挤）——
  均否；选 worker 侧**有界 FIFO**（`WORKER_QUEUE_MAX = 8`，满才 409）+ 同 jid 幂等（顺带修超时
  重试重复执行）+ 失败不堵队 + `/ping queued`；HUB 传输语义零改动。pull 侧 `--poll` 可多 hub
  轮询、`work_dir` 按源分区（`work_dir/<hub_id>/<jid>`），共享课程须同 commit（异 commit 走既有
  86 + 监督器重拉）。
- **违反后果**：回退到竞速广播 → 多 worker 重复算 PPO + 慢者白烧；TTL 调大 → it24 倒车（死 worker
  回收失灵）。plan：`plan/multi-course-parallel-training.md` §3.8/§3.9、P3b；实现见
  `nn-training/remote/{protocol,hub_server,worker_server}.py`。

---

### §2026-09-13-bc-cloud-integration（2026-09-13，BC 训练整合进 云-HUB-LAN：云端第二任务类型 kind=bc；用户指令五步）

- **决策**：远程任务协议引入 `kind` 维度（"ppo" 缺省 wire 兼容 | "bc"），BC job 复用
  HUB job 全套（发布/租约/账本/回传/落位）与 LAN 节点协议（/v1/task ?mode=bc），
  云端 worker 按 kind 分叉执行（bc → `train/bc.py::train`），控制台 BC 课程复用
  trainingLoop 组件键（spec 分叉为 run_bc.py）。设计全量：`plan/bc-cloud-integration.plan.md`。
- **拒绝的替代方案**：为 BC 建独立管线（第二个 hub/job 协议/独立控制台组件）——重复
  鉴权/租约/账本/落位/冒烟五套已验证机制，维护面翻倍；BC 用 Colab notebook 手工跑
  （现况）——无断点续跑、无节点并发、无归档纪律，且与多课程体系脱节。kind-branch 让
  "第三种任务类型"（如 offline eval job）有先例可循。
- **边界**：bc manifest 免必填 reward/γ/λ（无 RL 语义）；mode 红线 ppo↔bc 互斥（串型
  拒收）；BC 无 init-weights（`init_weights_fp` 恒 "bc"）、无 opt tar 往返（不跨轮续训）；
  wins-only 败局 = `kept:false` 空容器（合法结果非失败）；BC 课程独立文件种类
  `.bc.jsonc`（rl/bc_config.py，D14 同规 `bc_corpus_identity_fp`）；smoke = 尺寸压缩
  真一轮 + scratch 落位 + 账本零污染（不覆盖 out、不写 bc_round_completed）。
- **教训入册**（`docs/nn/remote-transport.md` §5）：单 shard 语料 shard 级切分 train=0 崩溃
  （make_loaders 回退样本级）；agent 结果缓存键不含任务参数 → smoke 独立 iterId 命名空间；
  LAN 节点需升级（git pull + restart）才有 `bcSupport` 能力位——升级前 fail-closed 不派。

---

### §2026-09-15-goalnn-local-ppo-worker（2026-09-15，本机 PPO 拆成独立 worker：控制台 `local` 预设 = hub-server + local-worker + trainer；用户指令）

- **背景（用户指令）**：「把本地 PPO 拆分为一个独立 worker，可以随时启停，与云端 worker 一致，
  同样支持 pull/push 模式。」此前 `--ppo local` 是**进程内**执行：`TrainingLoop`（run_rl）在自己的
  进程里 `load_episodes → chunk_episodes → ppo_update`，整轮阻塞——PPO 不能单独停、不能单独换代码、
  不能挪到另一台机器；而云端 worker（`remote_worker`）早就是无状态、可重连、可随时启停的独立进程。
- **决定（三条，用户逐项拍板）**：
  - ① **本机 PPO = 新的受管组件 `localWorker`**，跑的就是**云端同一个入口**
    `python -m remote_worker --poll http://127.0.0.1:<本课 hub> --out tmp/local-worker-<课>
    --device cpu`（+ 配了 `rl.torch_threads` 才透传 `--threads`）。协议、租约、心跳、幂等重拉、
    热替换退出码 86 + 内部监督器重拉**全部继承、零分叉**（用户选「完整继承」）。push 侧不新增实现：
    执行面就是既有 `workerServe` 组件（`remote_worker_serve`）。
  - ② **控制台 `local` 预设改义**：`hubServer → localWorker → trainingLoop(--ppo remote
    --remote-transport pull --remote-hub-url 本机 hub)`，并新增 `--remote-transport` 开关（run_rl 与
    run_bc 同步）把传输**钉死 pull**；控制台**不再提供进程内 PPO 入口**（run_rl `--ppo local` /
    run_bc `--local` 保留给直调 CLI 与 R9 远端失败降级落点，只是不再被预设编排）。
  - ③ **整树停止**：`localWorker` 是「父 supervise_worker + 子 worker_loop」两进程，停止/重启走
    `core/net.ts::killPidTree`（Windows `taskkill /T /F`；POSIX 先验 `pgid === pid` 再组杀，否则退回
    单进程 stop），判定唯一来源 `stack/specs.ts::COMPONENT_KILL_TREE`。
- **为什么必须 `--remote-transport pull`（本次唯一的新语义，也是最贵的一个坑）**：`_remote_ppo` 的
  历史优先级是「rl-config 里本课 `gpu_push` 节点 > hub」——某课用 push 跑过一次后
  `courses.<课>.push_node_url` 就留在配置里，此时 `local` 预设会把 job **静默推去云机**，本机 worker
  永远领不到活，而账本/日志看起来「训练正常」。`auto`（默认）保持历史行为零变化，`pull`/`push`
  是显式裁决，非法组合响亮 `SystemExit`（不静默回落）。
- **拒绝的替代方案**：① **不新增组件，只给 `workerServe` 加 pull 能力**（用户裁定的备选）——
  同一个进程既被推送又轮询会让「push 服务端」与「pull 轮询者」的生命周期/健康语义混在一起，
  而两者的失败形态与日志完全不同（无法一眼定位）；② **新增 `local-pull`/`local-push` 预设、
  `local` 保持进程内**（用户否）——两套本机 PPO 语义长期并存，等于把「已拆干净」这件事半途而废；
  ③ **控制台把 `courses.<课>.push_node_url` 清空来实现 pull**——该键是 push preset 的用户配置，
  静默改写别人的配置是比多传一个旗标严重得多的事故面；④ **让本机 worker 也走 `--local` 的进程内
  回退**——那就没有「随时启停」，正是本次要消灭的东西。
- **刻意不做的事**：不动 `rl.remote_hubs[<课>]`（它是 pull preset 隧道 URL 的家，`stepCloudflared`
  复用已建隧道时**不会重写**它，写本机 hub 进去会把 pull preset 悄悄改成打本机 hub）——本机 hub
  只经显式 `--remote-hub-url` 注入；不动 pull/push preset 的传输语义（`auto`，行为零变化）；
  不动 `rl/loop_steps._serial_ppo`（R9 降级与直调 CLI 仍走进程内路径）。
- **副产物（顺手修的一处既有红）**：`dashboard/tests/training-selfnode-cwd.test.ts` 在 HEAD 上就是
  红的——`selfNodeSpec` 缺 `cwd: REPO_ROOT`，控制台以 `dashboard/` 为 cwd 启动时
  `bun run tools/agent/sampler-agent.ts` 会 Module not found（2026-09-14 回归的护栏先落了红测试）。
  本次补上该字段（一行，spec 与测试同时在线）。
- **违反后果**：local 预设若不钉 pull ⇒「训练在跑但 PPO 其实在云机」的静默错位（最贵的一类）；
  停在 localWorker 上若不整树杀 ⇒ 孤儿 worker 继续轮询 hub 抢 job、抢租约，「随时启停」名存实亡；
  把本机 hub 写进 `rl.remote_hubs` ⇒ pull preset 的隧道 URL 被偷偷改写，云机再也领不到 job。
- **追加（2026-09-15，用户指令：「让 push 模式也能一键用本机 worker_server 作执行面（config
  无 gpu_push 时自动回落到本机 workerServe）」）—— push 的第三种执行面来源**：
  `configurePushEndpoint` 改为三档裁决，返回 `PushTarget {url, source: manual|config|local,
  viaLocalWorker}`：① 用户填 endpoint（ping 门 + upsert **云**节点）；② 留空且 config 有 ping 通的
  gpu_push → 只写课程 `push_node_url`；③ 都没有 → **回落本机 worker_server**：写 `nodes[]` 的
  `local_push` 节点（`gpu_push: true` + `local_push: true`，authKey = `rl.remote_token`，与本机
  `worker_server --token` 同源）+ 课程 `push_node_url` 指向 `http://127.0.0.1:<push 端口>`。
  预设顺序在 `viaLocalWorker` 时多一步 `workerServe`（本机 worker_server 是受管组件）。
  - **为什么必须落 config 而不只注入 `REMOTE_PUSH_NODE` env**：python `_gpu_push_nodes` 的
    gpu_push 分支要求 `push_node_url` 能**匹配到一个节点**——只给 env 而课程键仍指旧/空，
    会走到「匹配 0 个 → WARN → 回落 pull」，而 push 模式没有 hub（`wait_job` 空 URL）直接卡死。
  - **与云节点共存（拒绝的替代）**：直接复用 `applyPushNodeConfig` 覆盖唯一的 `gpu_push` 条目
    更简单，但会把用户填的云 URL 静默吃掉（本仓反复出现过“静默改写别人配置”类事故）。故云/本机
    各一份条目：`applyPushNodeConfig` 的 findIndex 加 `!n.local_push`，回落也从不碰云条目；
    `enabledGpuPushNodes`（复用门/健康探测）**含**本机节点——它同样是真执行面。
  - **`workerServe` 启动改幂等**：回落/复用路径下它可能已经活着，旧实现对每次「启动」都
    kill+重起，会把正在跑 PPO job 的 server 当场打死（还会撞端口）；改为「账本存活或 `/ping`
    通 → 已在运行，不动它」——与 hubServer/trainingLoop/localWorker 同规。
  - **验证**：dashboard `tsc` 干净 + **358 pass / 0 fail**（含 push-config 新增 7 用例：返回
    `source=local` 且写盘 URL 能被 python 过滤命中、两种执行面共存互不覆盖、`allowLocal:false`
    仍响亮报错、预设把 `workerServe` 排进顺序、workerServe 启动幂等的源码级接线门禁）。
- **追加（2026-09-15，用户指令：「把『本机 push 执行面』的当前指向显示出来——卡片上标出本课 job
  现在推给本机还是云机」）—— 执行面可见化**：新增 `pushTargetFromConfig(cfg, course)`（纯函数；唯一指针 =
  `courses.<课>.push_node_url` → 认领 `nodes[]` 条目 → `local | cloud | unresolved`）；慢快照做一次
  `{url}/ping` 直探（`PushTargetProbe{kind,url,nodeId,healthy}`，1500ms，后台刷新同一拍，不进请求路径）；
  `buildStateView` 再补 `active`（trainer 正以 push 模式在跑）。UI = trainingLoop 卡模式徽章旁的
  `tc-cc__push` 徽章（绿=本机 worker_server / 蓝=云 GPU / 红=未匹配；`--idle` = 当前未以 push 在跑，
  只是「配置指向」）。
  - **为什么直探 `/ping` 而不复用节点 pill 的 `/v1/ping`**：判据必须与 python `_gpu_push_nodes` 同一条
    URL——复用会造成两个真相（节点在线 ≠ 能被 push 到）。
  - **`unresolved` 必须显式上屏**：`push_node_url` 指向 config 里不存在的节点时，python 侧「匹配 0 个 →
    WARN → 回落 pull」，而 push 模式无 hub（`wait_job` 卡死）——旧界面看起来完全正常，这正是本次可见化
    的动机。
- **验证（2026-09-15，执行面可见化）**：dashboard `tsc` 干净 + **366 pass / 0 fail**（新增
  `pushTargetFromConfig` 5 用例 + `buildStateView.pushTarget` 快照 1 用例 + SSR 徽章 2 用例）；
  `bun dashboard/src/server/build.ts` 三份 bundle 通过（app gzip 51016B）。
- **验证（2026-09-15）**：dashboard `tsc` 干净 + **358 pass / 0 fail**（59 文件，含追加上面的
  push 回落 7 用例后计数；本条目首落时 351；含新增
  `tests/local-worker.test.ts` 11 用例：spec 形态/poll 目标/killTree/双课隔离/`--remote-transport pull`
  注射/重建逐字段一致/接线 grep 门禁）；根 `bun run check` **1819 pass / 4 skip / 0 fail**；
  nn-training python gate（ruff + mypy + pytest xdist -n 4）绿，含新增
  `tests/test_remote_transport.py`（run_rl 与 run_bc 两侧裁决 + argparse 接线）；
  `bun dashboard/src/server/build.ts` 三份 bundle 与根 `bun run build` 均通过。

---

### §2026-09-17-kaggle-cred-before-proxy（2026-09-17，Kaggle 引导期凭据必须在装 tailnet 代理之前读；诊断日志落文件）

- **背景**：`battle.tailscale.ipynb` 在 Kaggle 上「完整引导后」会话无声终结（Colab 一切正常；改了几轮
  tailscale/daemon/up-flags 都无效）。人工复制出来的日志止于 `Tailscale IP = …(mode=userspace)`。
- **根因（代码级，2026-09-17 定位）**：`notebook_boot.run()` 把 `HUB_TOKEN`/`PUSH_TOKEN` 的读取放在
  `tailscale_boot.ensure()` **之后**，而 ensure 会把 `HTTP_PROXY/ALL_PROXY` 指到 userspace tailscaled 的
  本地代理——**该代理只转发 Tailscale IP**。Kaggle 的凭据链必然落到 `kaggle_secrets`（对
  `www.kaggle.com` 的 HTTPS 调用），于是引导后读不到 token；`_secret()` 的 `except Exception` 又把它
  吞成空串 ⇒ `/code` 401 ⇒ `SystemExit` ⇒ 会话终结。Colab 的链在 `google.colab.userdata`（localhost
  通道，被 NO_PROXY 覆盖）就命中，`_kaggle()` 永不执行 ⇒ **平台差异**。同 cell 的**内联回退**本来就是
  「先读凭据、再 `_inline_ensure` 注入代理」，顺序是在迁移到远端模块时丢掉的（回归第 5 条）。
- **备选与否决**：让 tailnet 代理也能走公网——否，tailscaled userspace 出站代理设计上只转发 tailnet；
  只往 NO_PROXY 塞 `.kaggle.com`——否（清单不可穷举：Secrets/元数据/git/pip，且**顺序错误本身仍在**），
  但「NO_PROXY 合并平台条目」作为第二道保险保留；给 `_secret` 加重试/回退——否，代理方向本身就是错的。
- **决定**：① `notebook_boot.run()` 在 `ensure()` **之前**一次性读完三个凭据并下传（`_pull` 不再持有
  secret 句柄，签名级防回归）；② `tailscale_boot` 新增 `set_proxy_env()` / `platform_net_env()`：
  NO_PROXY **合并**（不再覆盖平台条目）、记录引导前原值、公网调用前临时还原为平台代理；cell 的
  `_secret` 用同一套（内联 `_platform_net_env()`）；③ 缺 token 时**响亮点名**，不再落到
  「`/code` 401 — HUB_TOKEN 不一致」这种误导措辞；④ cell 日志同时落文件
  （`/kaggle/working/battle-boot.log` → `/content` → `/tmp`），`SystemExit` 收尾/未捕获异常也落文件
  （此前第一手线索全靠人工复制 `[battle]` 行，**SystemExit 正文恰恰不带该前缀**，最容易被漏掉）；
  ⑤ `CFG["ts_engine"]` 阀门（引擎顺序实验，默认 `kernel,userspace` 不变；用于验证
  「kernel 模式那次 TUN/路由尝试动过容器网络」这条待验证假设）。
- **违反后果**：任何人再把「读平台 Secrets / pip / git」放到引导之后，Kaggle 上都会复现「无声终结」；
  任何只打 stdout 的引导都会在下一次无声死亡里丢掉全部证据（本轮排障成本的一大半在这里）。
- **配套事实（同批）**：`code.zip` 是 **TrainingLoop 启动时**的快照（`rl/loop_steps.py::pack_code_zip`，
  hub `/code` 直接回文件）——改了 `remote/` **必须重启 loop**，否则云机跑的是旧运行时；日志里的
  `sha12` 就是用来跟 loop 侧对账的（§2026-09-16-kaggle-kernel-no-torch 的子进程探测修复正是靠它才生效）。
- **回归测试**：`nn-training/tests/test_bootstrap_proxy.py`（7 例：NO_PROXY 合并 / 平台代理还原 /
  异常路径还原 / 引擎顺序 / ★凭据前置 / `_pull` 签名 / 缺 token 点名）；`tmp/repro-old-order.py`
  对 HEAD 的**修复前**代码复现了「引导后读 HUB_TOKEN」，断言当场抓住（§7.1）。
- **未决（下一步验证）**：E1 用落盘日志跑一次 Kaggle 定位真实死点；E2 「boot 完静置 5 分钟不 import
  torch」对照（排除 daemon/平台网络被杀）；E3 `ts_engine=userspace`（排除 kernel 尝试的副作用）；
  E4 对开卡 `sha12` 与 loop 日志核对 code.zip 新鲜度。
- **同日扩展（2026-09-17）：本机 hub 地址也走同一批凭据（键名 `HUB_IP`）**。
  - **背景**：`CFG["hub_url"]` 是「每个会话都要手填、值却长期不变」的值（hub 跑在操作者本机，
    Tailscale IP 在设备重注册前稳定）——**正是 secret 的用途**；留在 CFG 里等于每开一次
    Colab/Kaggle 就要人肉改一次，而它的模板值 `http://<本地TS_IP>:8787` 忘了改就会拿一个
    带尖括号的主机名去连（错在 DNS 层，比当场点名难查得多）。
  - **决定**：① `HUB_IP` 走与 `TS_AUTHKEY`/`HUB_TOKEN` 同一条取用链（环境变量 → Colab/Kaggle
    Secrets → CFG 手填），读进 `CFG["hub_ip"]`；有值时**压过** CFG `hub_url`。② 取值两吃：
    裸 IP/主机名自动配 `CFG["hub_port"]`（缺省 8787），整条 URL（自定义端口/域名/协议）原样用 ——
    同一个键不必记两套约定。③ CFG 模板值含 `<` 一律视为**未填**（返空串，由调用方响亮失败并
    点名该填哪个键）。④ 解析逻辑单源住在 `remote/tailscale_boot.py::resolve_hub_url`：两个
    notebook 都从 GitHub raw 拉这个模块**同一个文件**，而体检那边只拉这一个（消费点
    `notebook_boot.run()` 与 `diagnose()`）；⑤ `HUB_IP` 与其它凭据同批**在引导之前**读（同上条根因：
    引导后平台 Secrets 就够不着了）。
  - **备选与否决**：写进 CFG 让人肉填 —— 否（每会话一次的人肉步骤，且忘改就是带 `<` 的主机名）；
    只在训练 cell 支持、体检 cell 不改 —— 否（会出现「体检说连不上、真跑却连得上」这种最难信的
    诊断结论）；新增 `HUB_URL` 键名 —— 否（`HUB_TOKEN`/`HUB_IP` 同族命名更可猜，且 URL 形态仍
    由 `HUB_IP` 一个键容纳）。
  - **违反后果**：把 `HUB_IP` 的读取挪到 `ensure()`/`_inline_ensure()` 之后，Kaggle 上会复现
    「凭据读成空串 → /code 401 → 会话终结」；把内联回退的解析改得与 `resolve_hub_url` 不同源，
    则两条路会连到不同的 hub（GitHub raw 不可达时才暴露，最难复现的一种）。
  - **回归测试**：`nn-training/tests/test_notebook_hub_ip.py`（14 例：内联回退的解析与
    `resolve_hub_url` **逐例对账**（8 例取值 + 2 例未填 → `SystemExit` 且点名两个键）、缺省端口
    跟随模块常量、两个 cell 的 HUB_IP 读取位置早于代理引导、体检 cell 把 `hub_ip`/`hub_port`
    喂进 `diagnose`）；notebook 侧接线改动用**先红后绿**验过（把缺省端口字面量改成 9999，
    对账断言当场红）。

---

### §2026-09-17-goalnn-remotewire-m0m2（2026-09-17，远程 PPO 传输计量 + 协议瘦身落地；退路与安全阀是硬要求）

- **背景**：`plan/remote-wire-remediation.plan.md` M0–M2 实施（分析见 `plan/kaggle-rollout-feasibility.md`：
  push 上行 4.43MB/轮里 ≥3.2MB 是恒定或可再生的字节）。切片提交：M0 `0bc7a69`、M1 `fa6a34f`、M2 `a59b1ef`。
- **备选与否决**：① 只在 `_remote_ppo` 里记字节 —— 否（生产路径与验收 harness 会各记一套，数字对不上账）；
  改记在传输客户端层的返回值，两边共用同一份账。② M2 让节点「自己留着状态、hub 不管」 —— 否（违反 D12：
  缓存只决定「这坨字节要不要再传」，sha 对账必须仍能在 hub 侧重算）。③ B5 只在旧节点上试一次 JSON —— 否。
- **决定**：① `wire` 子字典（iteration 事件 + worker result 回传）是**唯一**传输口径，键为 additive，
  旧行无键 = None，`validate_result` 不校验未知字段；`/admin/net-probe?bytes=N` 走鉴权、确定性填充。
  ② 瘦身走**运行期开关**（`slim`，缺省关＝逐字节旧行为），开关取值必须进指标，否则事后无法按选项分组。
  ③ opt/ref 一律对 **raw（编码前）字节**取 sha256 做内容寻址；节点侧重写在 `blob_cache/<sha>` 的那个
  opt 就是它自己刚产出的那份 ⇒ 同会话命中率 100%。④ **安全阀**：`opt_sha` 存在但 blob 取不到 ⇒
  响亮失败（`RetryableError`/`ProtocolError`），**绝不**静默退回 `load_state_into` 开新 Adam ——
  那是把 D5 的动量延续悄悄改成「每轮零动量」，而日志上一切正常。⑤ B5 的 `/job` 体走二进制（BRJ2），
  但 4xx 时**保留一次 JSON 退路重发**：协议不匹配绝不能让一整轮 job 丢在最后一米（同 result v2 规矩）。
- **三问门（通过）**：① 被否决备选见上；② 未来再犯 ——「数字分别记两处」「取不到就静默 warm-start」
  「把退路优化掉」每条都会再出现；③ 无法就近表达 —— 横跨传输客户端 / 两个解析端 / 校验语义 / 回退开关。
- **违反后果**：再出现静默降级 warm-start ⇒ D5 语义被无声改写（本仓最怕那类 bug）；删掉退路重发 ⇒
  新旧混合部署时整轮 job 丢失；开瘦身却不在指标里记开关 ⇒ 无法归因。
- **配套事实（M1 实测，2026-09-17 本机）**：`http2` 上行 p50 4.7–4.9s / `quic` 23–33s（2MiB，每臂 2 轮
  独立 run，run3 八连发无退化）⇒ **决策门 1 命中，默认 `http2`/`edge-ip-version 4` 有实测支撑**；
  数字与探针用法见 `docs/nn/remote-transport.md` §7（合并时曾为避免撞号改号，2026-09-23 重组后统一重排）。同在那一轮量到的一个坑：本机 `HTTPS_PROXY=127.0.0.1:7890`
  而 `NO_PROXY` 不含 `*.trycloudflare.com` ⇒ **任何打隧道 URL 的本机客户端必须绕过代理**
  （不绕会拿回 `SSL: UNEXPECTED_EOF_WHILE_READING`，而 cloudflared 日志看起来完全健康）。
- **B6（xz preset 3→6）已量、不采用**（2026-09-17，3 份真 payload）：体积只 −2.6…−3.0%（~34KB），
  打包却 +188…+199%（关键路径 +6.3s/轮），按隧道实测 3.5 Mbps 那 34KB 只值 0.08s ⇒ 净亏。
  结论就写在 `remote/protocol.py` 常量旁（就近表达，防将来重测）。
- **补记（2026-09-17，同批）**：① M2 的 `slim` 补上「启动时提供选项」整条链（原来只能手改
  rl-config.json ⇒ A/B 无路）：类型/双域 `SlimMode`、`resolveSlim`/`slimToCfg`、console-state、
  preset 白名单与落库、启动弹窗控件与当前生效值。**双域是铁的**：UI/console-state/body 用
  `'on'|'off'`，rl-config 必须 `1|0`（python `--remote-slim` 是 `type=int,choices=(0,1)`，
  写字符串会让训练启动直接报错），换算只允许走 `slimToCfg()`。② 修掉一个真缺口：
  `wire.protocol`/`wire.edge_ip` 原**恒为 null**（CLI 从未声明这两个参数，而 `_wire_from_result`
  读的正是 args）⇒ §1.4「开关取值必须写进 iteration 事件」过去并未满足。现由 CLI 参数 +
  `_course_cf_tunnel`（CLI > `courses.<stem>.cf_*` > `rl.cf_*` > None）填上。
- **未做（不写成已做）**：M2 的云机绝对值确认**未跑**；M3（rollout 上云）按 §5.1 门 1 已命中、
  门 2 待测，且 M2 后上行仅 ~1.2MB ⇒ 当时留档不做（**后经人工指令开工，见下条**）。

---

### §2026-09-17-goalnn-rollout-on-cloud（2026-09-17，M3 rollout 上云：**人工指令覆盖计划的决策门**，

安全阀与「不许静默降级」是硬要求）

- **背景与授权**：`plan/remote-wire-remediation.plan.md` §5/§6 的原文是「门 1 命中 ⇒ M3 直接留档不做」，
  门 1 在 M1 实测中已命中（`http2` 把 2MiB 上行压到 p50 4.8s）。本次开工依据是**用户指令**：目标腿是
  TPU 实例（v3-8 = 96 vCPU），rollout 上云在那里收益极高。**门的结论没变，是决策权变了** —— 记录本条
  是为了让后续 agent 不会把「代码里有 M3」误读成「门开了」，也不会拿旧的『留档不做』去回退它。
- **备选与否决**：① 让节点自己拼 rollout 命令（否决 —— hub 的 `build_rollout_cmd` 是三导出器 + 课程覆盖
  + D14 血缘的唯一拼装点，节点重算 = 在协议里复制一份它的知识，早晚漂）；改为 hub 发 argv、节点只执行。
  ② 上云轮降级本机（否决 —— 本地没有 shard 可训，「降级」只能是静默丢掉一整轮）；③ 用 `local_slots=0`
  + 手工摘节点来关本地采样（否决 —— 靠配置正确；改为 loop_core 在 node 轮**结构上**跳过整个采样相位）。
- **决定**：① 新 job `kind="iter"`（走 BC 开过的 kind 通道），manifest 追加必填 `ts_code_sha256` + `rollout`；
  rollout 规格 = 逐局 argv（白名单只放行 `tools/sim/export-rl-rollout.ts`，`--out`/`--weights` 必须 job 内
  相对路径）。② TS 运行时按内容寻址（`pack_ts_code_zip` 固定时间戳 ⇒ 同内容同 sha ⇒ 节点缓存可命中）；
  白名单打**整棵 `tools/**` + `src/**`** 而不是手挑子目录（实测依赖闭包会跨出 `tools/sim` 到
  `../eval/godai-score`；手挑 = 在猜依赖图）。③ 声明集用 `iter_expected_data_fp`，节点侧对实产集
  用**同一个函数**复算并拒收不符 —— 这不是等价替代，是「上云轮没有本地副本可重算」的唯一替代。
- **开关与回退**：`--rollout-src auto|local|node`（缺省 auto → local，逐字节旧行为）；值住 rl-config
  （`rl.rollout_src` / `courses.<课>.rollout_src`，D14 血缘：选项永不进 curricula），并随每轮写入
  iteration 事件的 `wire.rollout_src`（否则事后无法按「实测在哪跑」分组）。控制台启动弹窗提供选项。
- **安全阀（配错一律响亮，禁止静默降级）**：node 与 `--target-transitions` 互斥（补波要读本地 shard）；
  发布时 traj 已有本地 shard ⇒ 拒发（双份采集）；实产 shard 集 ≠ 声明集 ⇒ 拒收；`_ensure_ts_code` 的
  sha 不符 ⇒ `RetryableError`。上云轮**不经过** `_remote_ppo_or_degrade`，所以 4xx（鉴权/闭锁）
  立即停腿的判据必须在 `_remote_iter` 里**另补一份**——否则 x3-step 事故的「403 白烧 5×30s 重发同一 job」
  会重演。
- **控制面同步（计划 §5.4 「最容易漏一半」）**：本地采样/预采结构上关闭；`rollout_sec` 用节点自报
  `elapsedSec`（用 t_rollout 会把 PPO + 传输算进采集 = 假指标）；`metrics_stats` 与 `_check_quota_incident`
  在两轮均**跳过**（上云轮本地零 shard 是预期，不跳就是假精度统计 + 每轮假配额告警）；eval 链零改动。
- **三问门（通过）**：① 被否决备选见上；② 未来再犯 ——「节点自己重算调度规格」「上云轮降级本机」
  「内容寻址缓存忘了加进 prune 豁免名单」每条都会再出现（第三条 M2 已经犯过一次）；③ 无法就近表达 ——
  横跨协议 / 两个执行端 / 控制面读取点 / 回退开关 / 控制台 UI。
- **配套事实**：逐位对拍已验（`tests/test_remote_iter_real_bun.py`，真 bun + 真权重，同 argv 跑两遍逐文件 diff，
  含 `_rl_report.json` 除 `elapsedSec` 全字段）；协议/规格/执行器/失败语义/传输端点共 ~70 例。
  **未做（不写成已做）**：真远程轮次的绝对值（`wire.up_sec`、每轮墙钟）与 TPU 腿上的 target ~10s ——
  本机无节点可跑；M2 的云机绝对值确认同样仍欠。细节见 `docs/nn/remote-transport.md` §8（合并时曾改号，2026-09-23 重组后统一重排）。
- **收益前提不变**：≥16 vCPU 的腿才成立（GPU T4×2 = 4 vCPU 直接否，见 `plan/kaggle-rollout-feasibility.md` §3.3）。

---

### §2026-09-17-hub-restart-deadlock-hardening（2026-09-17，hub-server 重启死锁：D9 改序 + 回环永不封禁 + 原子实例锁 + 停止 trainer 即释放锁）

- **背景**：hub-server「崩溃后手动重启失败」，控制台只报「意外退出」，日志为 `端口 127.0.0.1:8787
  已被占用——拒绝启动（禁止双监听）`。根因链与现场日志、备选否决的完整版见 `docs/nn/remote-transport.md` §6。
- **根因（三层同族）**：① 旧 `_auth_ok` **先查封禁再验 token** ⇒ 本机组件用陈旧 token 连打 5 次就被
  封一小时，且**连正确 token 的健康检查/训练循环/worker 拉活一起 403**；② cloudflared 回源把隧道
  流量也归成 127.0.0.1，回环被封 = 整机服务面连坐；③ 封禁只住**进程内存**、只能重启清除，而旧实例
  还活着占着端口 ⇒ **重启被自己占的端口挡死** = 只能人工杀进程。另两处同族障碍：双监听守卫的
  「探测→bind」TOCTOU（Windows 还能双绑成僵尸）、账本 pid ≠ 锁持有者时「停止→启动」被 trainer 锁卡死。
- **决定（四点）**：① **D9 改序**（`remote/hub_server.py::HubHandler._auth_ok`）——先验 token，
  **合法 token 永远放行**，封禁只拒无效鉴权尝试（封禁期的无效尝试 403、不计数、不延长）；
  ② **回环永不封禁**（`_is_loopback`）——`127.0.0.0/8` / `::1` / `::ffff:127.0.0.1` 的失败**不计数、
  不封禁**（鉴权边界与 401 审计行不变；用户口径「本地 127.0.0.1 鉴权失败不要锁地址」）；
  ③ **服务侧原子实例锁**（新 `remote/_instance_lock.py`，路径 `nn-training/.<kind>.<port>.lock`，
  按端口键控）——拿锁 → 端口探测 → bind 三道闸，陈旧锁按「持有者已死 / 命令行缺该服务指纹」
  接管（指纹可给多个：同一服务常有多个合法入口），身份读不到即 fail-closed，锁文件**写不下**
  （只读 FS）时 fail-open 且响亮告警（守卫是纵深防御的第二道闸）。接线：`hub_server`（8787 类）
  与 `worker_server`（push 端口，经 `remote_worker_serve`）——后者的僵尸更贵（HUB 会把 job POST 进
  无人应答的监听端口，表现为推送静默卡死）；④ **「停止 trainer」即释放**（`launch/cli.ts::releaseTrainerLock(s)` +
  `server/actions/stop.ts`）——释放本课 run_rl/run_bc 锁，**先核验进程身份**才停存活持有者，身份不符只告警；
  ⑤ **隧道来源还原（B，同日追加）**——②把回环整段豁免后，隧道入口（cloudflared 回源也归成 127.0.0.1）
  变成「只 401、不计数、不封禁」，等于对公网暴露面零封禁。新增 `hub_server.attributed_source(peer, cf)`：
  **只在「TCP 对端是回环」时**采信边缘注入的 `CF-Connecting-IP`（须为合法 IP 字面量且非回环值）
  ⇒ 按**归因 IP** 计数/封禁；无头 / 头不是合法 IP / 头写的还是回环值 / 对端不是回环 ⇒ 一律归因 TCP 对端。
  直连（tailnet）对端**只认 TCP 对端 IP**——那台机器能自己写任何头，采信它等于把封禁能力交给攻击者。
  审计行随之带上 `peer=` / `src=` / `via=cf|peer`（否则事后分不清封的是隧道来源还是直连来源）；
  **访问日志（`log_message`）同样补 `src=… via=cf`** —— 回源流量原本全写成 `[hub-server 127.0.0.1]`，
  这正是本次事故排查的最大阻雾（`log_message` 只打非常规事件，不刷屏；`self.headers is None` 的早期
  错误路径有护栏）。
  可采信的头**只认 `CF-Connecting-IP`**，不认 `X-Forwarded-For`（后者是可追加的逗号列表，取哪段都是语义游戏）。
- **被否决备选**：调大阈值/时长（本质是封禁**作用面**，不是次数）；回环也计数（回源流量冒充回环来源，
  惩罚无据）；加解封管理端点（真解药本就无需人工介入）；只靠端口守卫（TOCTOU + Windows 双绑）；
  按「锁里 PID 活着」直接杀（PID 复用误杀无辜）；**全局无效尝试退避闸（429/延迟，方案 C+D）**——
  它的全部收益建立在「封禁不可用」之上，而 B 一旦生效就重复了；B 失效时它也只能压速率、不加安全，
  却要给每条合法路径都加一条延迟分支（合法 token 先过、不受影响，但多一个恒常状态机）。收益重叠、
  成本不为零 ⇒ 不做。**删掉封禁机制（方案 E）**同理被否：tailnet 直连那一侧的来源标识是可信的，
  在那里封禁仍然有效，不该为隧道侧的难题陪葬。
- **头部可伪造时的失效代价（预登记，供事后对照）**：本方案唯一未实测的假设是「Cloudflare 边缘**会覆写**
  `CF-Connecting-IP`」（仓里无 CF 头读取先例、无 ingress 配置、此沙箱无网络，无法离线验证）。假设不成立时
  最坏后果**两条都良性**：① 攻击者轮换头值 ⇒ 拿不到封禁，效果退化为「回环豁免」（= 本方案之前的状态，
  不会更差）；② 伪造某个 tailnet worker 的 IP ⇒ 那个 IP **只**会被拒「无效鉴权尝试」，带正确 token 的
  请求照常放行（改序使然）⇒ 不构成对合法对端的 DoS。**实测法**（2 分钟，用户可随时做）：向隧道发一次
  带伪造头的无效鉴权（`curl -H 'Authorization: Bearer wrong' -H 'CF-Connecting-IP: 203.0.113.7'`），
  看 `hub-server.out` 的 `AUTH FAIL` 行 `src=` 显示伪造值（⇒ 可伪造，本方案降级为 B0）还是真实公网
  出口 IP（⇒ 假设成立）。若显示伪造值，回退动作 = 把 `attributed_source` 的 `cf` 分支删掉（一行），
  语义即回到「回环豁免」，不需要改测试以外的任何东西（`test_attributed_source_matrix` 是唯一直接
  断言该分支的用例）。
- **违反后果**：把封禁检查挪回 token 校验之前 = 整机自锁；让回环重新计数/封禁 = 隧道流量与本机组件
  互相连坐；停止 trainer 只杀账本 pid = 「停止→启动」死锁回归。
- **回归测试（第二批）**：`nn-training/tests/test_pid_probe_windows_safe.py`（6 → **8**，覆盖六处入口，
  含 AST 唯一实现门禁与 tmp-clean 副本契约）；`dashboard/tests/training-port-reclaim.test.ts`
  （6 → **15**：stepCloudflared 接线门禁、`ownsResource` 声明/监督器接线门禁、
  `portOwnedBy` 注入/真实监听/空清单两义三组）。
  A/B 取证（`tmp/probe-red.log`、`tmp/cf2_probe.py` 输出）：detached worktree 对 HEAD 跑新测试
  6 红（含 `tmp-clean._pid_alive(0) = True`、`_pid_alive(-1) = True` 的行为级红）；cloudflared 侧
  HEAD 上 `stepCloudflared` 既无 `reclaimPort` 也无端口归属校验、就绪判定是裸 `tunnelEdgeReady`；
  监督器侧 HEAD 上 `ownsResource` 在 `ProcSpec`/`specs.ts`/`server.ts` 三处**全都为 0 次**。
- **回归测试（第一批）**：`nn-training/tests/test_hub_auth_d9_order.py`（11 → **19**，2026-09-17 追加 8 例：
  归因矩阵 / 隧道来源 5 次即封且第 6 次 403 / 被封归因 IP 持合法 token 仍放行 / 本机无头组件仍豁免 /
  直连对端自带头不算数 / 伪造头无害 / 访问日志带 `src=`（含直连与无头两负例）/
  `headers is None` 的早期错误路径不抛）、`nn-training/tests/test_instance_lock.py`（9，含真进程同时三启
  恰好存活一个）、`dashboard/tests/trainer-lock-release.test.ts`（13）；A/B 取证 = detached worktree 跑
  新测试对 HEAD 红（`assert 403 == 200`；归因来源一节另附行为取证
  `tmp/cf-red-behavior.log`：修复前 5 次「回环+CF 头」无效鉴权后 `is_blocked(203.0.113.7) = False`、
  `_auth_fail = {}` ⇒ 隧道入口确实零计数）。
- **配套（同日同族）**：控制台启动前按端口回收幸存占用者（`stack/hub.ts::reclaimPort`，已接在
  `stepHubServer` / `stepSelfNode`）。**`workerServe` 控制台路径故意不接**：worker_server 可能在跑
  数小时的 PPO job，`/ping` 失败（如 token 临时不匹配）就回收它 = 直接炮掉在途 job；而该路径
  现在会先复用 `/ping` 通的幸存者，不通时拿不到锁也会**响亮拒绝并指向日志**（含持有者 PID 与
  锁路径），足以人工处置 —— 要不要让控制台代劳杀进程，留待用户拍板。
- **铁律（新增，实现唯一化）**：**存活探测在 Windows 侧禁止用 `os.kill(pid, 0)`**——它是
  `TerminateProcess(handle, 0)`，会把被探测的进程**直接杀掉**；且 `except Exception → 不活` 会把
  「我杀了它」记成「它本来就是死的」，护栏静默失效；`pid <= 0` 一律判不活（POSIX 上
  `os.kill(0/-1, 0)` 命中**进程组**，会把残缺锁当成活人持有 ⇒ 同名课永久拒启 / tmp-clean 永不收敛）。
  **唯一实现 = `nn-training/pid_probe.py::pid_alive`**（stdlib-only 顶层模块，与 `platform_utils`
  同层；`remote/` 与 `train/` 都直接 import 它而**不经过对方的 `__init__`**——`remote/` 要独立
  打进 code.zip、`train/loop_util` 刻意保持 torch-free，两个方向都不能反向依赖）。
  四份具名薄壳全部**委托**它：`train/loop_util._pid_alive`、`run_rl._runrl_pid_alive`、
  `remote/_instance_lock._pid_alive`、`remote/notebook_runtime._pid_alive`（后者原是**函数内的
  嵌套闭包**、不可被测试导入，已提到模块层）。**唯一的保留副本** = 仓根 `tools/tmp-clean.py`
  （根级开发工具不能依赖 nn-training 的包/路径布局），按契约自带 Windows 分支 + `pid<=0`，
  一致性由源码门禁守住。
  **为什么必须唯一**（18 小时内同类隐患在 3 个不同文件各自踩了一次：`loop_util` 裸 `os.kill`、
  `notebook_runtime` 嵌套闭包+裸 `os.kill`、`tmp-clean` 缺 `pid<=0`）：「每加一个调用点就多一份
  可漂移的实现」就是这类 bug 的根因面；收敛成一份后，“Windows 安全”只需在一个地方成立。
  回归 + 行程门禁：`nn-training/tests/test_pid_probe_windows_safe.py`（六处入口全纳入同一组断言：
  注入假 kernel32 断言 Windows 分支**零 os.kill**；AST 门禁断言 `nn-training/` 里真调用
  `os.kill(pid, 0)` 的文件**只有 `pid_probe.py` 一个**——已排除注释/docstring 与
  `os.kill(pid, 15)` 这类**故意发的信号**；并断言四份薄壳不得再自带 `import ctypes`）。
- **隧道 metrics 端口的双绑窗口**（同日追加）：cloudflared 是**第三方二进制**，没法在它内部
  装实例锁（hub/worker 那层是 python 自己拿 `O_CREAT|O_EXCL`）⇒ 控制台侧回收就是它**唯一**
  的一道闸。`stepCloudflared` 在 spawn 前 `reclaimPort(metricsPort)`（与 hub/selfNode 同族），
  堵住 `supersedeSlotTunnels` 看不见的那类幸存者（孤儿 / 登记丢失 / 控制台重启竞态）——
  metrics 端口既是 `--metrics` 的 bind 目标、又是 `/ready` 的探测目标，被占着会**同时**造成
  「新隧道 bind 失败」与「就绪读数读自旧僵尸」。后者另加一道：**就绪归属**
  ——`hub.ts` 的就绪判定改为「**本进程持有该端口** ∧ /ready 200」，算法是
  `core/proc.ts::portOwnedBy(pid, port)`（唯一实现；名字从早期的 `tunnelOwnsMetrics`
  改成中性名，因为 hub-server / worker_server 也要用它——见下一条）。
- **就绪归属推广到所有「独占端口的组件」**（同日追加，用户点名「监督器那条路径也要」）：
  `ProcSpec` 新增可选字段 **`ownsResource?: (pid) => Promise<boolean>`**，声明的三处：
  `hubServerSpec`（hub 端口）、`cloudflaredSpec`（metrics 端口）、`workerServeSpec`（push 端口）；
  **监督器**（`server.ts::restart`）与**启动步骤**的就绪判定都变成
  「`ownsResource`（未声明 = 不阻塞）∧ `healthy()`」。为什么监督器同样需要：它杀旧 pid 后
  紧接着拉起同一条 spec，若新进程 bind 失败（EADDRINUSE / python 侧双监听守卫拒绝）**早已退出**，
  而端口上的旧实例照样答 `/ping` / `/ready` ⇒ 监督器会把**僵尸的 200 记成「重启成功」**：
  账本写新 pid、实际服务的是旧进程——这正是 hub-server 重启事故的相位（账本 pid ≠ 服务者）。
  未就绪时的日志会点出归因（“新实例未持有该端口”），而不是只报「45s 未就绪」。
- **`portOwnedBy` 的空清单必须用 TCP 探测二次区分**（同日发现并修，否则语义荒谬）：
  lsof/netstat 无输出有两种含义——**根本没人监听**（⇒ 新进程显然没拿到端口 ⇒ **false**，
  这正是要抓的那类）与**列举不出归属**（⇒ 探测不可用 ⇒ **true**，不判死，否则工具缺失会让
  所有组件启动失败）。有清单时只认「包含自己」。fail-closed 只落在「确知不是自己」那一侧。
- **监督器重启路径不接 `reclaimPort`**（保留的刻意选择，非残留）：它杀的是账本里确切的
  pid、紧接着拉起同一条 spec，没有孤儿窗口（kill 失败时回收也一样杀不掉），故不回收、只核归属。

---

### §2026-09-17-job-fail-report（2026-09-17，节点**确定性**失败必须带原因回传控制面：`POST /jobs/{id}/fail` + `/result` 410 终局）

- **背景（真缺口，用户 2026-09-17 提问暴露）**：云机确定性失败（bun 装不上 / TS 运行时取不到 /
  argv 非法）此前只落在**云机日志**里。pull 侧 `worker_loop` 的 `except ProtocolError` 只打一行
  「REJECTED … skip (not retried)」——**不回传、不还租约**，训练侧只能等 `wait_job` 25 分钟超时，
  读到的是「超时」而不是「bun 缺失」；push 侧节点 `worker_server` 用 **500** 报 failed，而 500 在
  `push_client.wait_result` 里被当**瞬时错误**重试到 1800s 预算耗尽。两条路都把「确定性能力缺失」
  伪装成「网络/排队问题」，且每次重试再白烧一个超时窗口。
- **备选与否决**：① *只把云机日志写得更清楚*（否决——训练侧读不到，人的第一现场是控制台，不是云机
  stdout）；② *把确定性失败并进既有连败/降级链*（否决——重试只会撞同一堵墙，且
  `--remote-degrade-after` 会把上云轮静默切到本机 PPO，而上云轮**没有本地 shard**，降级即打穿）；
  ③ *启动前 smoke 探 bun 存在性*（否决——只覆盖「装没装」一类，TS 运行时取不到 / argv 非法 /
  commit 不符同样要回传，且探测本身多一次往返）；④ *调小 `wait_job` 超时预算*（否决——治不了
  「原因不可见」，还把真慢的云端 PPO 误杀）。采用：**失败即终局 + 原因随行**。
- **契约（硬要求）**：`POST /jobs/{id}/fail`（bearer 鉴权；活租约须持有人；`reason` 必填非空；
  体 ≤ 64KB）= 节点判定「这个 job 在这台机器上跑不成」→ 落 `fail.json`（**首写锁定**：已有结果
  不收失败、首个原因胜出；原子 tmp+replace）→ ① `GET /jobs/{id}/result` → **410 + 原因**
  （不是 404「还没回来」、不是 5xx「瞬时错误」）；② `/status` → `state="failed"` + 原因；
  ③ 该 job **不再回池**（`claimable_job_ids` 排除——换节点只会重演同一失败）；④ 账本追加
  `job_failed`（带 worker 身份：多机共用同一 token，这是定位现场的唯一线索）；⑤ 训练侧
  `JobFailedError` → **第一次**就写 `gate_verdict: ABORT`（真原因入判决）+ 停腿，不消耗连败
  配额、不降级。**重发同 job（同幂等键 → 同 job_id，逐轮重试走的正是这条路）由 `publish_job`
  清掉 `fail.json`**——不清 = 「失败即终局」把重试永久钉死（无人认领 + 立刻 410），这是两条
  语义共存的**唯一**接口。
- **名词纪律**：只有**确定性**失败（能力缺失 / 协议级拒收）走这条路；瞬时失败（网络 / 5xx）仍走
  `release` 回池、**不报** `fail`。搞反的代价单边且严重：瞬时失败报成终局 = 可恢复的 job 被钉死；
  确定性失败报成瞬时 = 白烧一个超时窗口（本次要治的病）。`CodeChangedError` 刻意不报——它靠
  重启进程 + 租约回池自愈。
- **落地**（9 个文件，逐处行为见 `docs/nn/remote-transport.md` §9）：protocol（`FAIL_NAME`/`JobFailedError` 带
  reason/kind/detail）、hub_server（端点 + 首写锁定 + 池排除 + 410 + `status=failed` + `job_failed` 账本）、
  hub_client（`report_job_failure` + 立即抛 + `publish_job` 清标记）、worker / worker_server（**500→410**）/ push_client、
  `rl/loop_steps.py`（首败即 ABORT + `_push_job_round` 不再包成 RetryableError）、dashboard `ppo-queue.ts`
  （`job_failed`/`fail.json` 不算排队超时；账本读法改 **last-write-wins**——重发同一 job 又该被盯排队，
  旧口径会把它永久当已关闭）。回归：`test_job_fail_report.py`（9）+ `test_remote_degrade.py`（2）+ dashboard（2）。
  实测（2026-09-17）：`wait_job` 从「等满 25min」变为**秒级抛错**（整个用例含起服务仅 0.52s）。
- **违反后果**：删掉 `publish_job` 的清标记 ⇒ 重试被永久钉死（整条腿再也跑不起来）；去掉池排除 ⇒
  每个节点轮询都重演同一失败；`worker_server` 退回 500 ⇒ 训练侧重新等满 1800s；把确定性失败并回连败链 ⇒
  上云轮静默降级打穿（无本地 shard 可训）。
- **遗留（未做，不写成已做）**：真远程轮次的端到端验证（本机无节点可跑）；节点侧 preflight
  **硬门**（bun 版本对账不匹配直接拒单，计划 §5.3）仍只有记录与日志。

---

### §2026-09-17-goalnn-halfoffline-run（2026-09-17，半离线整段：云机领一次就自主跑完，产物可打包下载；hub 失联不影响）

- **背景（用户需求，2026-09-17 两次确认）**：现状是 **hub 拥有迭代循环**——每轮 publish 一个 job、
  节点跑一轮就回等；hub 一断，云机除了等无事可做。用户要的是：云机领到（课程 + 初始权重 + 代码）
  后**即使本机 hub 一直失联**也能全程自主跑完，并以 Kaggle/Colab 官方方式（工作目录产物 zip）交付
  **逐轮**权重与指标。用户口径（三问已答）：一次领 = 整段；逐轮权重/指标/可续跑；**不在云上跑评估**。
- **备选与否决**：① *把 hub 的迭代循环抄一份到云上*（否决——`build_pairs`/`build_rollout_cmd`/课程重建
  各有**唯一**一份，抄一份就是造第二个真相）；② *等 hub 重连再逐轮问*（否决——正是要治的病）；
  ③ *产物边跑边 POST 回 hub*（否决——失联时 POST 必失败，产物会跟着丢；产物必须落**本机**，回传尽力而为）；
  ④ *新写一条「段」执行链*（否决——本轮语义与 kind=iter **逐字段同构**）。采用：**kind="run" = kind="iter"
  的延长**（同一轮 + 一个计划尾巴）。
- **契约（硬要求）**：① `plan.json`（`rl/plan.build_plan` + `dump_plan` 规范序列化）随 payload 下发，
  manifest 记 `plan_sha256`；节点在**跑第一局之前**过三道门——sha256 / 形状（`validate_plan`）/
  **全段对集指纹**（`plan_pairs_fp` 重放每一轮），任一条不符就一局不跑（跑到半途才发现语料漂了，
  已经产出一批不可信 shard）。② 计划只带「纯函数入参 + argv 模板」：对集靠 `build_pairs` 重放，逐局
  argv 只做四个动态 flag 重定向 + 自定义关 `--stage-json`（重定向不认识任何导出器细节）；发布期自检
  保证「重放 == 真 args」且「重定向对模板恒等」。③ 产物目录是**唯一**长期记录、**中断即有效**：
  `it-NNN/weights.json` + `opt.tar`（Adam 动量，缺它续训静默归零）+ `metrics.jsonl`（一行一轮）+
  `state.json` + `LATEST.zip`/`artifacts.zip`；TS 运行时随产物携带 ⇒「只下载产物 zip」的机器也能续跑。
  ④ 末轮形状 + `iters` 明细回传 ⇒ hub 侧落位链**零新代码**（三个指纹逐字段对的是本 job 自己）。
  ⑤ 续跑判据 = `run_id` + **磁盘上 plan.json 的 sha** + 该轮权重在盘（自描述，见下）。
- **两个被测试抓出来的真缺陷（都写进回归）**：① `ArtifactStore.start` 曾用**调用方传入**的计划 sha 做
  续跑判据，而目录写盘的是另一份格式（hub 走 `dump_plan` 规范形）⇒ 新会话拿着同一目录永远算不出相等
  的值，每次都被当成**新段**从 `start_it` 重跑——「关掉会话明天接着跑」静默退化成重跑。改为**写盘后
  再算**。② 起点快照曾往 `metrics.jsonl` 写一行（无 agg/report），使「账本一行 = 一轮、it 唯一」失效，
  逼下游用「过滤掉没有 report 的行」绕开——用过滤器掩盖一条本不该写的行。改为起点只落 checkpoint **不记账**。
- **开关与缺省**：`--run-iters N`（>0 一次领 N 轮；<0 到课程末尾）> `courses.<课>.run_iters` >
  `rl.run_iters` > **0 = 关**（历史行为逐字节不变）；等待上限 `--run-wait-sec` > `rl.run_wait_sec` > 8h
  （≈Kaggle 单会话上限）。要求 `--ppo remote`。段尾那一轮照常走本机结算（iteration 事件 / eval 派发 /
  归档），**段中间那些轮不派发本机 eval**——其权重不在本机归档，拿活指针充 W(it-1) 正是 P0 修过的
  「eval 标签超前一轮」。逐轮明细落成一条 `run_segment` 事件。
- **违反后果**：让云机自己算下一轮 ⇒ 第二份对集实现；省掉对集指纹门 ⇒ 产出不可信 shard 后才发现；
  产物只留 hub ⇒ 失联时产物与训练一起丢；`opt.tar` 缺失 ⇒ 续训静默丢 Adam 动量（D5）；用传入 sha 做
  续跑判据 ⇒ 每次续跑都重跑（本条抓出的缺陷）。
- **遗留（未做，不写成已做）**：真云端（Kaggle/Colab）端到端跑一次（本机无 GPU 节点）；段内**进度上报
  到控制台**（长段期间只有等待，逐轮指标要等段尾）；控制台启动弹窗的 `run_iters` 选项（现只有 rl-config /
  CLI）；push 传输等待预算仍 1800s（半离线的自然形态是 pull）。
- **落地**：`rl/plan.py`、`remote/artifacts.py`、`remote/run_loop.py`（含无 hub 续跑 CLI
  `python -m remote.run_loop --artifacts <dir>`）、`remote/protocol.py`、`remote/worker.py`、
  `remote/hub_client.publish_job`（计划进 payload）、`rl/loop_steps.py`（段长解析 + `_remote_run_segment`
  + `wait_timeout_sec`）、`rl/loop_core.py`（段优先 + 跳过中间轮 eval 派发）、`rl/cli.py`。
  回归：`test_plan.py`(8) + `test_run_loop.py`(11) + `test_run_segment.py`(12)；细节 `docs/nn/remote-transport.md` §10。

---

### §2026-09-17-goalnn-offline-task-bundle（2026-09-17，全离线任务包：hub 导出 → Kaggle/Colab 上传 → 云机自主跑完；包是搬文件不是配网络）

- **背景（用户需求，2026-09-17）**：「hub 支持打包导出训练任务（课程、初始权重、代码），以
  kaggle/colab **官方支持方式**上传云机后，云机全程自主完成训练，并以官方方式提供产物打包下载；
  若中途能连上 hub，云机自动恢复产物在线回传」。半离线（`kind="run"`）已经解决了「云机不依赖
  hub 也能跑完 + 产物落盘」，但**任务本身还在网络上**（云机要轮询 hub 领 job）——全离线要的是
  「hub 关机也能开工」。
- **备选与否决**：① *让云机 clone 仓库 / pip 装依赖*（否决——官方入口是「数据集 / Drive / 文件
  上传」，不是「配网络」；rollout 用 TS、训练用 torch，装环境本身是一整天）；② *拆成一堆 curl +
  环境变量*（否决——把「搬文件」变成「配网络」，正是要消除的东西）；
  ③ *导出时在 hub 登记 job 并把 token 塞进包*（**部分否决**：包会四处搬运，凭据不进包；导出也
  不该以 hub 可达为前提）；④ *重建一份 manifest 而不复用训练侧的*（否决——解析者只有训练侧一份）。
  采用：**单 zip + 两条命令 + 逐件 sha 对账**；token 走 Kaggle secret / `--hub-token-file`。
- **契约（硬要求）**：① 包内 = 跑完整段所需的一切：`plan.json` / `manifest.json` /
  `init_weights.json` / `opt.tar`（Adam 动量，缺它续训静默归零）/ **`code.zip`**（同 commit 的
  python + TS 源码——云机没有仓，`build_pairs` 的重放靠它成立）/ `ts_code.zip`（rollout 运行时）；
  `task.json` 是索引（逐件 sha256 + 字节数 + run_id + it/end_it + hub_url）。② 导入 = 铺成**可直接
  续跑的产物目录**（`it-{it}/weights.json` + `opt.tar` + `ts_code/`），随后
  `python -m remote.run_loop --artifacts <dir>` 即可；`run_loop --bundle <zip>` 一步到位。③ 包是
  **不可信输入**：越界成员（zip-slip：绝对路径 / `..` / 盘符）、magic 不符、任一件 sha/字节数不符
  一律**拒收且一局不跑**。④ 导出侧三道自检：计划 sha 与 manifest 不符 / 缺 code.zip / 缺
  ts_code.zip 都拒导（缺件的包在云上只会以更难懂的方式失败）。⑤ 无 hub 运行**必须有代码快照**：
  standalone 入口硬门（`code.zip` 或 `code_cache/<sha>/` 命中二者其一），否则拿空 base_url 去下载、
  报一个跟真因无关的错。⑥ 导出**不记账本、不进待领池**（`publish_job(register=False)`）：这条腿的 job
  没有人会来领，记一条 `job_pending` 只会让控制台显示一条永远等不到工人的任务。
- **轮次对齐（最容易错的一处）**：loop 的 `it` = 已完成的下一轮 = 包里要跑的**第一轮**，所以
  `plan.start_it = it - 1`（区间语义是 `start_it+1 .. end_it`）、`max_iters = n`（**不是 n-1**：
  这里没有「job 自己那一轮」要扣——那一轮已由 hub 跑完，`args.out` 就是它的产物 = 包的起点）。
- **违反后果**：把 `code.zip` 换成就地仓库 ⇒ 云机与 hub 的代码版本可以不一致，而 shard 血缘、
  `wver`、`pairs_fp` 全都建立在「同一 commit」上；不逐件对账 ⇒ 一次截断的搬运变成一堆无法归因的
  怪结果；standalone 不卡代码快照 ⇒ 空 base_url 下载报错把真因藏起来；导出记账本 ⇒ 控制台出现
  永远等不到工人的 job（并可能被别的节点领去做错事）。
- **遗留（未做，不写成已做）**：① **自动补传**（用户同一条需求的后半句）——设计已定：
  云机每轮 best-effort `POST /offline/artifact`（权重 + 指标，按 `(run_id, it)` 幂等，
  first-write-locked；含轮末 `POST /offline/result`），`GET /health` 做轻量探活，连不上静默跳过，
  产物目录里 `delivered.json` 记已投递项；hub 侧落在 `<job_root>/offline/<run_id>/` 并记
  `offline_artifact`/`offline_result` 账本事件；token 走 secret 不进包；② 真云端（Kaggle/Colab）
  端到端一次（本机无 GPU 节点）；③ 控制台入口（导出按钮 / 进度显示）。
- **落地**：`remote/bundle.py`（导出/导入/索引/README/zip-slip 防护）、`remote/run_loop.py`
  （`--bundle` 导入即跑；代码与 TS 字节走 `preloaded`；standalone 的代码快照硬门）、
  `remote/hub_client.publish_job(register=False)`、`rl/loop_steps.py`（`--export-bundle` 导出钩子 +
  `BundleExportedError` 干净退出）、`rl/loop_core.py`、`rl/cli.py`。回归：`tests/test_bundle.py`(7)。

---

### §2026-09-17-goalnn-offline-reconnect-delivery（2026-09-17，产物补传：中途能连上 hub 就自动恢复在线回传；不可影响训练是唯一硬约束）

- **背景（用户需求，2026-09-17）**：全离线任务包（上一条）交付了「hub 关机也能开工」，需求的后
  半句是「如果训练中途发现可以联通 hub，云机也能自动恢复产物在线回传」。补传 = 产物目录之外
  的**第二份拷贝**（产物本来就已经落在节点本地目录），让控制面不必等人搬 zip。
- **备选与否决**：① *新增无鉴权 `GET /health` 探活*（否决——探针要回答「我能不能用这条链」，
  只证可达会让「token 配错」在第一次上传 ~1.9MB 后才暴露，且多一个向公网泄露「hub 在线」的
  端点）；② *逐轮产物覆盖写*（否决——同一轮权重是不可变快照，覆盖 = 谁先到谁定义历史，而补
  传天然会重传）；③ *自定义裸二进制容器*（**部分否决**：比复用 `encode_weights_json` /
  `encode_opt_tar` 省 33% 体量 ≈0.5s/轮，但要多养一种容器格式 + 两端实现；best-effort 旁路不
  值这个复杂度）；④ *坏 token 每轮重试*（否决——D9 是「同 IP 五次无效鉴权封 3600s」，等于
  自己把自己封掉，且配置错重试一百次也不会对）；⑤ *把补传做成一种 job*（否决——这条腿没有
  job 也没有租约，记 `job_pending` 只会让控制台显示一条永远等不到工人的任务，还可能被别的
  节点领走）；⑥ *把补传做成逐字节续传/断点续传*（否决——整轮 ~1.9MB，代价大于收益）。
- **契约（硬要求）**：① **训练永不因网络停摆**：`sync()` / `deliver_result()` 永不抛（传输异常
  收在 `_post` 里，异常冒到外层会跳过 `_save_ledger` ⇒ 已投递的轮次没落盘、下次整批重传）；
  没有重试预算、没有退避等待、没有阻塞调用。② **重启续投**：`delivered.json`（原子写）是唯一
  记账，`pending()` = 「磁盘上有权重且不在记账里」——**第一次连上时把之前攒的积压一次补齐**。
  ③ **幂等 + 首写锁定**：hub 按 `(run_id, it)` 落盘，重复投递回 `duplicate`（200，**不改写**；
  回 409 会让节点每轮把已投过的再传一遍）。④ **不可信输入边界**：`run_id` 是 hub 侧目录名 ⇒
  `sanitize_run_id`（只放行 `[A-Za-z0-9][A-Za-z0-9._-]*`、≤64、拒 `..`、非字符串即非法）；权重
  指纹须与实收字节相符；账本行自称的指纹也须与权重相符（不一致 = 产物目录自相矛盾）；体上限
  在声明长度上挡（413）。⑤ **401/403/400/413 → 本会话停用**（配置/形状问题，重试无意义）；
  5xx / 网络异常 → 只记日志（节流），下轮再试。⑥ **逐轮落盘之后才补传**（先自洽，再尽力
  出网），一次 `sync()` 有上限（`SYNC_CAP=4`），积压留给下轮。
- **违反后果**：补传一旦能阻塞或以任何方式上抛，就会把「产物落在节点本地即交付完成」这条
  本已成立的契约毁掉（网络变成训练的必要条件）；不首写锁定 ⇒ 重连/重启/重试会改写历史产物；
  `run_id` 不净化 ⇒ hub 上任意文件写；坏 token 每轮重试 ⇒ 自己把来源 IP 封一小时。
- **遗留（未做，不写成已做）**：① 真云端端到端一次（本机没有节点：整条链只在真 HTTP 端点 +
  替身 `run_job` 上跑过）；② 控制台显示补传进度（账本已有 `offline_artifact` / `offline_result`
  事件，读方未接）；③ push 模式节点侧没有 hub 地址 ⇒ 该形态默认不开补传（需要显式配 URL）。
- **落地**：`remote/offline_deliver.py`（新：探活 / 积压扫描 / 逐轮投递 / 段末摘要 / 记账）、
  `remote/hub_server.py`（`POST /offline/artifact`·`/offline/result` + `store_offline_*` +
  `offline/<run_id>/` 落位 + 账本审计事件）、`remote/protocol.py`（`sanitize_run_id` + 契约常量）、
  `remote/run_loop.py`（逐轮/收尾钩子 + `--hub-url`/`--hub-token[-file]`/`BATTLE_HUB_TOKEN`）、
  `remote/worker.py`（kind=run 默认开启）。回归：`tests/test_offline_deliver.py`(16)。

---

### §2026-09-19-goalnn-slot-cap-5-and-loud-rejection（2026-09-19，plan R3-1：课程上限 5 + 越界槽位响亮拒启）

**背景**：用户口径「允许同时训练多个课程，上限先设为 5」（2026-09-18），而实现是
`dashboard/src/core/slots.ts::SLOT_COUNT = 4`，且 `slotOf` 对**越界值也静默回落 0**。
两者叠加 = **第 5 门课必然越界，然后静默用第 1 门课的 push 端口**：两门课的本机 push
互相顶掉，而且没有任何一行日志说这件事（只有端口占用冲突能间接看出）。这是真 bug，
不是「缺功能」。

**定案（三件事，都在 `slots.ts` 一个聚合点内）**：

1. **`SLOT_COUNT` 4 → 5**（= 用户口径的并行课程上限）。hub/隧道自 2026-09-18 起是单实例，
   槽位不再决定 hub 端口——它只剩「本机 push（worker_server）端口」与历史 metrics 命名，
   所以「槽位数 = 可并行课程数」是准确的语义，不存在第二个上限要同步。
2. **越界/非法槽位响亮拒启**（`slotOf` 抛错，点名课程 + 越界值 + 合法范围 + 总数）：
   未配置（`undefined`/`null`）⇒ 仍旧回落 0（legacy 单课行为零变化，§0.5-4）；
   **配置了非法值** ⇒ 报错。"只有未配置才回落"是本次要钉死的边界（旧注释与实现不符
   已在 `slotIssue` 的 docstring 里写明）。
3. **`slotError(cfg)` 配置级守卫 + 接进 `saveConfig`**：除非法槽位外，还拒绝
   **两门课显式配到同一槽位**（那才是撞端口的病根，比单课越界更早发生）并点名双方；
   未配置槽位的多课配置不拒（升级路上常态）。与 `capacityError` 同契约（null = 通过）。
   `allSlotPorts`（端口兜底清场清单）随 `SLOT_COUNT` 自动扩容，无需另改。

**取舍**：没有做「让课程数与槽位解耦」（plan R3-1 的备选）——hub/隧道已单例后槽位本就
只剩 push 端口，加一层抽象只会多一个概念；上限 5 与用户口径一致，越界现在响亮，成本为零。

**回归（先红后绿，§7）**：`dashboard/tests/training-multi-course.test.ts` 新增 W7 共 7 例
（上限 ≥5 / 5 门课 push 口互异且第 5 门不再回落 0 / 越界与非法值（5、-1、1.5、"2"）点名拒启
且 `slotPort` 同样响亮 / 未配置仍回落 0 且共享 hub 地址不受影响 / `allSlotPorts` 满 5 槽无重复 /
两课同槽位点名双方 / `saveConfig` 拒落盘且磁盘保持原样）。复现证据：修前该块 **4 红**
（`SLOT_COUNT` 实测 4、5 门课只算出 4 个端口、`slot: 4` 不抛而返回 0、`slotError` 不存在），
修后该文件 **35 pass / 0 fail**（W7 全绿）；dashboard 全量 **574 pass / 0 fail** + `tsc --noEmit` 干净。

---

---

### §2026-09-18-goalnn-multi-course-single-hub（2026-09-18，用户指令：多课程并行训练流程与操作重组；单进程服务所有课程 + hub 队列 + 分课程权重缓存）

- **背景**：多课程并行（`docs/multi-course-audit.md` / plan multi-course-parallel-training）当时定的形状是**课程 = 并行单元**：每门课各一套 hub-server / cloudflared / trainingLoop / localWorker / workerServe 进程，端口按槽位 `base + slot*10`，账本按课程键控。它解决了「第二课覆盖第一课登记」那类串账，但代价是进程数按课程线性增长（5 课 = 25 个进程），且 **hub 完全不知道「课程」这回事**（`--job-root`/`--jsonl` 都是每课程路径），所以没有跨课程的调度面：一门课积压 20 轮就独占自己的 hub，别的课的 worker 空转。
- **用户要求**（本次）：① 上限先设 5；② hubserver/trainingloop/selfNode/cloudflared **各只需一个进程**；③ hub 设 PPO 任务队列（push 按队列顺序推给空闲 worker，推送超时回落队首改推其它 worker；pull 按队列顺序分发给请求者）；④ 离线模式课程不实时派发 PPO、但接收 it 权重/指标回传；⑤ rollout 集群按课程缓存最近权重，避免同一份权重多次传递；⑥ 并行课程数 < PPO worker 数时用单课程那样的竞速；⑦ 面板重组（课程 select 不锁死、在训课程高亮等）；⑧ e2e 保证畅通。
- **决定（P1 已落地部分）**：
  - **单 hub 多课程 = 一个进程托管 N 份 `_JobStore`，磁盘契约逐字节不变**（每课程仍是 `tmp/<course>/remote-jobs` + `training_log.jsonl`）。这是本设计最关键的取舍：多课程只是「同一进程里多挂几份账本」，不是换一套磁盘约定 ⇒ 既有工具、既有 100 个单课程 hub 用例、`tmp/<course>` 约定全部照旧，回滚只需改启动参数。
  - **调度面 `_HubQueue`**：每课程一条 FIFO（沿用发布序）+ **跨课程轮转**（`rotation_order`，从上次派发的下一门开始——否则一门积压把其它课饿死）；**离线课程不参与实时派发**（只收回传）；`active_courses()`（非离线且有待领或在飞）是竞速分母。
  - **超时回落队首 + 改为派给别的 worker**：`claim` 在回收过期租约时记下「谁跑死的」（`_stale_holders`），队列在**还有别的活跃 worker** 时把该 job 避开那位前持有人（`may_avoid_stale_holder` 是闸，身份比对在 store 内——在队列层先读 stale 记录会踩时序，实测恒为空、避让永不生效）。独苗时恒允许自领（否则那台 worker 永远空转）。
  - **竞速口径改为按课程数**：`race_decision(..., active_courses=N)` 要求「窗口内不同 worker 数 **严格大于** 在派发课程数」。缺省 `active_courses=1` 时与旧口径（≥2 worker）**逐字节等价** ⇒ 旧调用与旧用例不变。多 hub worker 仍是安全条件的一票否决（scope 不随课程数放宽）。
  - **鉴权面提取为 `_AuthGuard` 且进程级一份**：多课程下不按课程各算一套失败计数（否则「同来源 5 次无效鉴权」的封禁阈值变成 5×N）；`_JobStore` 继承它，旧调用不变。单课程队列**借**那一份 store 的鉴权/竞速/停机状态（不是洁癖：既有用例会在 store 上预热封禁态再发 HTTP）。
  - **补传按课程归属**：`POST /offline/artifact|result` 的课程归属顺序 = 体里的 `course`/`course_name` → `?course=` → 已有 `offline/<run_id>/` 的课程（补传天然重传续投，第一条建目录后续自动归位）；归不到 → 400 且点名该填哪个。
  - **权重桶按 (course, kind)**（rollout 集群那一半）：改造前 5 门课的新 sha 全挤进同一个 `rollout` 桶（64 桶 LRU 被分摊，历史深度掉到 ~13 轮，慢节点回来取旧权重就 409）。纯逻辑出列到 `tools/agent/weight-buckets.ts`（可单测）。查找顺序 = 本课桶 → 旧单课程桶 → 任意同 kind 桶（sha 内容寻址 ⇒ 同一份字节），因此**升级顺序自由**（新训练侧 + 旧 agent / 旧训练侧 + 新 agent 都不破）。课程身份走**进程级环境变量 `RL_COURSE_NAME`**（在 `rl/config.apply_course` 里挂——那是训练进程唯一知道课程名的地方；出口散在 6 个文件的闭包里，逐点穿参要改十几个签名且每加一处都要记得再穿）。
  - **避免同一份权重多次传递** = 上传前预检 `GET /v1/weights?sha=&kind=&course=`（命中返回 `cached:true`，调用方连体都不传）；任何不确定（旧 agent 404 / 5xx / 空 sha）一律**保守上传**——少传一次是省流量，错判不传是 `409 wver not cached here` 停活。
  - **CLI**：`--course NAME[=online|offline]`（可重复）+ `--traj-root`（派生每课程 job-root/jsonl）；`--job-root`/`--jsonl` 保留为单课程旧形状，两者同时给 = 响亮拒启。观测面新增 `GET /admin/queue`（每课程 深度/在飞/心跳/队首 + 轮转游标 + 两个竞速判据数）与 `GET|POST /admin/courses`（看课程表 / 热切 online|offline，volatile）。
- **备选与否决**：把课程状态做成「无状态执行器 + 任务队列」（用户 2026-09-18 追问的方向）——**不作为本轮**：它要求把 12 个跨轮内存变量逐个迁到磁盘并把门禁语义从「内存计数」改成「扫账本」，是一次独立的、风险集中在**审计**上的改造（漏一个 = 静默语义漂移），已定为本轮之后的 P2（形态由用户拍板 = 迭代任务化；验收 = 「断开续跑 == 连续跑，逐字节等价」）。逐调用点加 `course` 参数而不是进程级 env —— 否（十几个签名 + 每加一处都要记得穿，且「这个进程是哪门课」本就是进程级身份）。给权重上传加「先 HEAD 再 POST」之外的第三条路（只在 hub 侧做去重）——否，白传发生在**训练侧到节点**这一段，hub 管不到。竞速的 auto 判据保留「worker 自报 scope」——是（它同时挡住「worker 还配着旧 hub」这类现场，见同日另一条）。
- **违反后果**：让多课程退回「每课一套进程」⇒ 进程数线性增长且无跨课程调度（一门课的积压独占自己的 worker 池）；把「找不到归属」写成空串而不是 None ⇒ 单课程队列（课程名**就是空串**）每一次 `/jobs/next`/补传都 500/400（本次实测踩过两次）；在队列层判 stale 身份 ⇒ 避让永不生效、超时过的 job 只会还给跑死它的那台；按课程各存一份鉴权计数 ⇒ 封禁阈值 5×N；按课程各存一份权重桶却让旧调用方落错桶 ⇒ 慢节点 409 停活（因此保留旧桶 + 同 kind 兜底查找）。
- **落地**：`nn-training/remote/protocol.py`（`COURSE_MODE_*`/`COURSE_MODES`/`parse_course_arg`/`rotation_order`/`may_avoid_stale_holder`/`race_decision(active_courses)`）、`nn-training/remote/hub_server.py`（`_AuthGuard` 提取、`_JobStore` 租约持有人身份 + stale 避让 + `inflight`、`_HubQueue` 调度面、`as_hub` 兼容包装、`make_server` 双形状、`/admin/queue`+`/admin/courses`、补传按课程路由、`--course`/`--traj-root`）、`nn-training/dist_common.py`（`COURSE_ENV`/`course_name_of`/`weights_cached_on_node`/`post_weights_cached`/`post_weights(course)`/`fetch_task(course)`）、`nn-training/rl/config.py`（`apply_course` 导出进程身份）、`nn-training/rl/{eval_dispatch,batch_eval,dispatch,queue_local,bc_eval}.py`（改用带预检的上报）、`tools/agent/weight-buckets.ts`（新，桶纯逻辑）+ `tools/agent/sampler-agent.ts`（按 (course,kind) 分桶 + `GET /v1/weights` 预检 + 任务 URL 带 `course`）；回归：`nn-training/tests/test_multi_course_hub.py`（17 例）、`nn-training/tests/test_weight_course_buckets.py`（12 例）、`tests/agent/weight-buckets.test.ts`（13 例）。
- **本轮未做（P1 余下 + P2）**：hub 中介的 push 派发（hub 主动推给空闲 worker + worker 登记入口 + 周期 ping 探活）与训练侧 push 改走 hub；单隧道（hub/cloudflared 收敛为单例，随单 hub 自然成立）；dashboard 重组（课程 select 自由可切 + 在训课程高亮 + 队列总览 + worker 登记入口）；多课程单 hub 的 e2e；P2 = 训练循环迭代任务化。

---

### §2026-09-18-goalnn-hub-push-dispatch（2026-09-18，用户指令：hub 中介的 push 派发 + worker 登记入口 + 周期探活，训练侧 push 改走 hub）

- **背景**：P1 已把 hub 变成多课程调度器（`_HubQueue`），但派发方向仍是 **pull 单边**：只有 worker 自己来 `GET /jobs/next`。而线上 `push` 模式是训练侧**直推**云机隧道（DECISIONS §340 补充 4）——于是「队列顺序 / 空闲判定 / 超时回落 / 多课程公平」这四件事在 push 腿上**一件都没有**：训练侧只能对着几张卡盲推，一台忙就等于等它，一台死就要等满 30min 才失败。用户口径（本次）：push 模式下 hub 按队列顺序轮番向空闲 ppo worker 推送任务；已推送任务超时则回落队首并改为推送其它 worker。
- **决定**：
  - **派发权归 hub，不归训练侧**：`manifest.dispatch = "push"` 是训练侧唯一要说的话（写定者 = `--remote-transport hubpush`，或 auto + `courses.<课>.hub_push`/`rl.hub_push`）；之后 hub 认领这份活自己推，训练侧回到 `wait_job`——与 pull 完全同一条收尾链（三重校验 → 落位 → 记账），**不新增第二条客户端**。
  - **登记表 = rl-config 的 `gpu_push` 节点**（控制台的 worker 登记入口回写它），按 mtime 热重载 ⇒ 写完配置不必重启 hub；判据与训练侧 `_gpu_push_nodes` **同一把尺子**（`gpu_push` + `enabled` 缺省 true + 非空 url）。运行时增删走 `POST /admin/push-workers`（volatile，且**在重载后仍然保留**——临时挂的机器不该被一次 mtime 变化静默抹掉）。
  - **探活 = 周期 `GET /ping`**：「在线」= 答过 200 且连续失败 < 3 次；**从没答过 = 不在线**（宁可这一拍不推，也不往一台可能是死的机器上推几十 MB）；**失败但未达阈值 ⇒ `busy=True`**（状态未知必须当局忙，否则一次隧道抖动就会让调度器认为它空闲并二次推送同一门课）。
  - **挑活**：跨课程轮转（复用 P1 的 `rotation_order` 游标）+ **每课程至多一份在途**（课程内轮次有硬序）+ 只推**队首**且队首必须是 push job（不越过它推后面的——那会把轮次跑成乱序）+ 四道闸（在线 / 不忙 / 在飞 < concurrency / 不在本次避让名单）。
  - **回落 = 放租约 + 避开那台 + 立刻换人**：超时（缺省 45min 兜底）、连续探活失败、拒收（409/428）、结果入账被拒，一律走这条路。job 留在该课队首（「每课程单在途」让这条天然成立），**不是**「跑得久就抢回来重算」——那会白扔已算掉的大半轮。
  - **结果两条腿共用一个入账函数**（`push_dispatch.accept_result`）：云机 POST 与 hub 代发取回的校验（对账 → 租约 → 首写锁定）不可能一条有一条无；推模式若跳过对账，一份对不上账的结果会被静默写成一轮「看起来正常」的训练。`_post_result` 改为调用它。
  - **租约同源**：hub 代持的推送用 `claim(worker_id="push:<id>")` + 轮询期间心跳续租 ⇒ pull worker 不会把同一份活领走；`/admin/queue` 的 inflight 持有人带 `push:` 前缀，一眼分清哪条腿（避让记录也按同一身份比对）。
  - **可观测**：`GET /admin/push-workers`（登记表 + 探活态 + 在途/避让/计数）；hub 侧实测字节回写 job 的 wire 账（`record_push_wire`），训练侧 `_wire_from_result(is_push=...)` 的读法与直推**逐字一致**——两种 push 的可观测性不该一个有一个无。
  - **缺省关**（`--push` 才启用）：不打开时连探活线程都不起，既有单课程用例与线上行为逐字节不变；控制台经 `rl.hub_push` 透传（`dashboard/src/stack/specs.ts`），`--push-config` 显式指向仓库那份 rl-config（登记表住那里，指到 per-course 目录 = 登记表恒空）。
- **备选与否决**：训练侧自己维护 worker 池并推 —— 否（那是把 P1 刚收敛掉的「每个训练进程各算各的」再放大 N 倍，且多课程下训练进程看不见别课的占用）；hub 侧另写一套 job 上传 —— 否（`push_client.submit_job` 已有 v2 体 / 内容寻址 / 428 补传 / 409 退避，两条腿必须是**同一份**传输语义，否则失败分类会漂）；按「worker 自报 scope」决定要不要推 —— 否（那是 pull 竞速的判据，与「这份活该谁推」无关）；把派发做成「拉不到时才兜底」 —— 否（push 腿的队头阻塞原样保留）；「跑太久就抢回来」当超时 —— 否（PPO 一轮 10–30min，正常与卡死无法区分，抢回来 = 白扔算力）。
- **违反后果**：训练侧与 hub 各写一份 `dispatch` 字面量 ⇒ job 永远躺在队首（两边日志都很安静，最难查的一种）；「从没答过」当在线 ⇒ 往死机器上推 payload 并白等一轮；失败未达阈值不当忙 ⇒ 一次抖动触发同一门课的二次推送（两份 PPO 抢同一轮）；不卡「每课程单在途」⇒ 同一课轮次并行跑、后一轮拿到过期 init 权重；push 结果跳过对账 ⇒ 错结果静默落盘成「看起来正常」的一轮。
- **落地**：`nn-training/remote/push_dispatch.py`（新：`PushWorkers` 登记表 + 探活、`PushDispatcher` 派发拍 + 每 job 线程 + 回落、`accept_result` 共用入账）、`nn-training/remote/protocol.py`（`DISPATCH_HUB_PUSH` / `PUSH_*` 常量、`push_worker_from_node` / `pick_push_worker` / `push_job_wants_hub_push` / `push_worker_id_of`）、`nn-training/remote/hub_server.py`（`_post_result` 改走 `accept_result`、`/admin/push-workers`、`--push` / `--push-config` / `--push-poll-sec` / `--push-timeout-sec`、`record_push_wire`）、`nn-training/remote/hub_client.py`（`publish_job(dispatch=)`）、`nn-training/rl/loop_steps.py`（`hubpush` 传输 + `_course_hub_push` + `resolve_hub_push` + wire 口径）、`nn-training/rl/cli.py`（choices 四值）、`dashboard/src/{stack/specs.ts,core/types.ts}`（`rl.hub_push` 透传）；回归：`nn-training/tests/test_hub_push_dispatch.py`（14 例）、`nn-training/tests/test_remote_transport.py`（+5 例）、`dashboard/tests/hub-server-push-arg.test.ts`（3 例）。
- **本轮未做（P1 余下）**：控制台「worker 登记入口」UI（写 `nodes[].gpu_push`）与面板重组（课程 select 自由可切 / 在训课程高亮 / 队列与 push 总览）、单隧道（hub/cloudflared 收敛为单例）、多课程单 hub 的端到端 e2e（训练侧 hubpush → hub → 真 worker_server + 假 PPO）。

---

### §2026-09-18-goalnn-single-hub-single-tunnel（2026-09-18，用户指令：hubserver/cloudflared 只需要开一个进程，就能同时支持所有并行训练课程）

- **背景**：P1 的前两步（多课程单 hub 的 python 侧、hub 中介 push 派发、控制台 worker 登记）已落地，但**进程形状**还是单课程时代的：控制台按课程拉起 hub-server（`hubServers[<课>]`，端口 = 槽位算术）与 cloudflared（每课一条隧道 + 每课 metrics 口 + 同槽位接管）。用户口径（本次）：hubserver / selfNode / cloudflared 都只需要开一个进程就同时支持所有并行课程。注：**trainingLoop 不在本次范围**——它是「有状态会话」（进度指针 / torch 与 Adam / 在飞任务集 / 资源占用），收敛成单进程要先把会话改成任务队列，属 P2；本轮只动 hub 与隧道这两条「无状态中介」。
- **决定**：
  - **共享槽 = 空串 `''`**：hub/隧道在账本里仍住 `hubServers` / `cloudflareds` 两张表，槽固定 `''`（沿用既有的「无课程槽」，语义正好重合：共享实例不属于任何单门课）。**所有读写一律经 `core/registry.ts::scopeOf(key, course)`**（唯一归一入口）。为什么不改成扁平单例键：旧账本里的 per-course 条目必须继续**可见、可枚举、可停止**（静默失监督是事故），共用一张表天然做到。
  - **课程表从盘上发现，不做注册**：hub 加 `--discover`，扫 `<traj-root>/<课>/{remote-jobs,offline}`，**新鲜窗口**（1h）内的自动登记、登记后不撤销。训练侧把 job 发布到 `tmp/<课>/remote-jobs` 就是「这门课在跑」的文件系统事实（hub 与 trainer 共享同一份盘）——再加一条 HTTP 注册旁路就是「会失败、会乱序、会忘了调」的第二事实源，而漏注册的后果是那门课**永久饿死**（跨课程轮转表里没有它），表面却一切正常。扫描有两处触发：`claim_next()` 前置（带 2s 最小间隔闸，新课程下一次轮询就能被领到）+ 后台节拍线程（5s，push 模式/无 worker 时兜底）。
  - **新鲜窗口而非「看到目录就登记」**：几天前的陈旧实验目录磁盘形状完全相同且同样残留 pending job，误登记会把死课程的 job 继续派给真 GPU worker（白烧租约）。窗口 1h ≫ 单个 PPO 轮次（10–30min），活课程每轮都在窗口内。
  - **单实例切换必须搬状态**：单课程时 halt / race / worker 登记 / 鉴权计数住在那一份 `_JobStore` 里（既有用例直接预热 store 字段），课程数变 2 时 `_adopt_solo()` 把它们搬到队列自己身上——不搬就是「多发现一门课，把停机达令、竞速模式、鉴权闭锁一起悄悄清了」。
  - **hub 端口 = `rl.hub_port` 基数本身**（`sharedHubPort`），隧道 metrics = 基数+1（`sharedTunnelMetricsPort`）；课程槽位此后只决定 push 端口。`slotPort(…, 'hub')` 不再有任何调用面（grep 门禁守）。
  - **旧形状换代接管**：共享实例启动前 `supersedeLegacyInstances(key)` 把所有非共享槽的旧条目**活则杀、死则清账**并点名。旧 hub 与共享 hub 服务的是同一个角色（同一棵 job 目录树），两个并存 = 双派发 / 双租约 / 结果回错家；旧隧道则在同一 metrics 口上撞车，且幸存者的 200 会被当成新隧道的就绪。
  - **旧条目拒重建（fail-closed）**：`restartSpecFor('hubServer'|'cloudflared', <非空课程>)` → null + 响亮告警。用共享 spec 去重建一个 per-course 条目等于凭空再造一个 hub。
  - **URL 是全局事实**：`writeRemoteHubUrl(url)` 只写单键 `rl.remote_hub_url`；python 侧（run_rl / run_bc）删掉「按课程回填 `rl.remote_hubs[<课>]`」那段——留着它就会把训练指向一个已不存在的每课隧道（控制台写单键，两边不一致）。
  - **配置面简化**：`resolveCfTunnel` / `cfTunnelArgs` 去掉 course 参数（一条隧道没有「谁的 cf_protocol 说了算」的问题）；`courses.<课>.cf_protocol` 读旧配置不报错但不再生效。
  - **共享在 UI 上必须标出来**：`ComponentView.shared` + 卡片「共享」徽章。不标，操作员会以为「这门课自己的 hub 停了」而重复启动。
  - **停机达令必须按课程（本轮的连带必修）**：halt / resume / status 一律支持 `?course=<课>`（空 = 全课程，供不带课程上下文的全局读取）。单课程时代「一个 hub 一份 halt 布尔」≈ 按课程；收敛成单进程后那个布尔升格为**进程级**——A 课门禁 ABORT 就会把 B 课的云机一起停掉，而被连坐的课表现只是「云机莫名停机」（最贵的一种静默故障）。故 hub 的停机态改为按课程存（`_HubQueue.set_halt(course)`，`is_halted(course)`），训练侧（门禁 `loop_guards` 经 `dist_common.course_name_of()`、`run_rl`/`run_bc` 的 `clear_halt_on_startup`）与控制台（`hubAdminOk(cfg, path, course)`）各自带上课程名。
  - **本机 worker 与共享 hub**：`localWorker` 的 `--poll` 指向共享 hub（两个本机 worker 轮询同一地址），「领到哪门课的 job 就干哪门课的活」——job 自带课程快照，结果按 job_id 回家；隔离面只剩工作目录与日志（per-course）。
- **备选与否决**：让控制台在启动时把课程表传给 hub（`--course A --course B`）+ 新课程走 HTTP 热加 —— 否（启动顺序脆弱 + 漏调即永久饿死；盘上事实已经够用）；把 hub/隧道改成扁平单例键 + 一次性迁移 —— 否（多出来的迁移要么静默丢监督、要么逼着挑一个 per-course 赢家，收益只是「形状好看」）；看到 `remote-jobs` 目录就登记（不做新鲜度判定）—— 否（见上，误登记 = 真金白银）；每课一条隧道 + 共享 hub —— 否（同一 hub 的连接多几份出网状态，还复现 2026-09-17 的隧道回源 → 127.0.0.1 归并 → D9 闭锁连坐训练主循环）；**训练循环也一并收敛** —— 否（有状态会话，P2 任务队列改造的命题）。
- **违反后果**：拿课程槽去读写共享组件 ⇒ 「看 A 课的卡片说 hub 停了」（其实在跑）、「停 A 课把共享 hub 杀了」；不搬 `_solo` 状态 ⇒ 新开一门课静默清掉停机达令 / 竞速模式 / 鉴权闭锁；不换代接管 ⇒ 两个进程读同一棵 job 目录（双派发、双租约、结果回错家）；允许 per-course 重建 ⇒ 凭空再造一个 hub；继续读 `remote_hubs[<课>]` ⇒ 训练指向不存在的每课隧道（job 永远发不出去）；误登记陈旧课程目录 ⇒ 死课程的 job 派给真 GPU worker；停机达令仍走进程级布尔 ⇒ 一门课的门禁 ABORT 连坐停掉其它课的云机（症状是「云机莫名停机」，极难归因）。
- **落地**：python —— `nn-training/remote/hub_server.py`（`--discover` / `--discover-sec` + `DISCOVER_SCAN_SEC` / `_HubQueue.add_course` / `_adopt_solo` / `discover` / `_course_dir_live` / `claim_next` 前置扫描 / 后台节拍线程；`--course` 显式路径与旧单课程 `--job-root/--jsonl` 行为不变），`run_rl.py` / `run_bc.py`（删 per-course hub URL 回填，只认单键；`clear_halt_on_startup(course=)` 按课程清停机态）、`remote/hub_client.py`（`set_cloud_halt(course=)` / `hub_halted(course=)`；hub 侧 `/admin/workers/{halt,resume,status}?course=`）、`rl/loop_guards.py`（门禁 ABORT 带本课课程名）；dashboard —— `core/registry.ts`（`SHARED_COMPONENTS` / `isSharedComponent` / `scopeOf`）、`core/slots.ts`（`sharedHubPort` / `sharedHubUrl` / `sharedTunnelMetricsPort`）、`core/config.ts`（`writeRemoteHubUrl` 单键）、`stack/specs.ts`（`hubServerSpec(cfg)` / `cloudflaredSpec` 单例化 + `--traj-root <REPO_ROOT>/tmp --discover`；`cfTunnelArgs` 去 course）、`stack/hub.ts`（`hubServerHealthy(cfg)` / `stepHubServer(cfg)` / `stepCloudflared(cfg, noTunnel)` / `supersedeLegacyInstances` 取代 `supersedeSlotTunnels`）、`dashboard/src/server/actions/cloud-halt.ts`（`hubAdminOk(cfg, path, course)` 拼 `?course=` + `triggerCloudHalt` / `markCloudHaltRecovered` 按课程下发达令）、`server/api/{component-meta,views}.ts`、`launch/cli.ts`、`stack/{hub-admin,local-worker}.ts`、`web/view/console-types.ts`（`shared?`）、`web/app/panels/ComponentCards.tsx` + `web/theme.css`（「共享」徽章）；回归 —— `dashboard/tests/single-hub-tunnel.test.ts`（新，7 例：槽位唯一 / 地址唯一 + grep 门禁 / 旧条目拒重建 / URL 全局）、`dashboard/tests/training-multi-course.test.ts`（改写 W1-W4-W6-P2-P3）、`dashboard/tests/cloud-halt.test.ts`（+2 例：假 hub 上的线上字节形状 `?course=` 与「A 课停机不碰 B 课记录」）、`nn-training/tests/test_loop_gate_{nopark,soft_remediate}.py`（门禁 ABORT 断言带课程名）、`dashboard/tests/local-worker.test.ts`、`hub-server-{push,race}-arg.test.ts`、`training-port-reclaim.test.ts`、`nn-training/tests/test_multi_course_hub.py`（+8 例：发现判定 5 例 + 真进程 `--discover` 主流程 1 例 + 节流/状态搬迁）。
- **本轮未做（P1 余下）**：多课程单 hub 的端到端 e2e（训练侧 hubpush → hub → 真 worker_server + 假 PPO）；组件卡片按「单例角色 / 按课程」**分组**（本轮只做到「共享徽章 + 共享槽取数」，卡片仍是同一形状）。

---

### §2026-09-18-goalnn-loopback-http-no-proxy（2026-09-18，门禁实测红：本机 127.0.0.1 请求被环境代理截走）

- **背景**：`tests/test_offline_deliver.py::test_offline_endpoints_require_auth` 在全量门禁里偶发红：hub-server 日志明明白白写了两次 `401`（鉴权边界是对的），测试侧读到的却是 **502**。根因不在被测代码：本机**用户级**环境带 `HTTP_PROXY`/`HTTPS_PROXY`（指向局域网代理），而 `no_proxy` 里写的是 `127.*` 这种通配——Python 的 `urllib.request.proxy_bypass()` 只认 `host == entry` / `*.suffix` / `.suffix` 三种形式，**不认 `127.*`**，实测 `proxy_bypass("127.0.0.1") is False`。于是每一发去 `http://127.0.0.1:<hub|worker|agent>` 的请求都被送进外部代理再转回来（代理抖动/回错误页 ⇒ 502），本机训练也凭空多一跳。
- **决定**：
  - **回环地址的 HTTP 一律绕开环境代理**，实现落在唯一的 `remote/net_http.py`（`is_loopback` / `no_proxy_opener` / `urlopen` 替身，与 `urllib.request.urlopen` 同签名、返回值同形）。
  - 四条本机出口全部接上它：`remote/hub_client.py::_request`（训练侧↔hub）、`remote/push_dispatch.py::_http`（hub↔GPU worker 的探活与推送）、`remote/worker.py::_request`（worker↔hub）、`remote/offline_deliver.py::_urllib_opener`（产物补传）。**非回环分支保持原样**：`net_http.urlopen` 在非回环时仍调 `urllib.request.urlopen`（保住测试的 monkeypatch 缝），`worker._get_opener()` 的显式 ProxyHandler 只服务非回环（Colab userspace 实测需求，不受影响）。
  - **判据只看 host**：`127.0.0.0/8`、`::1`、`localhost`、`*.localhost`；不做 LAN（10./172./192.168.）例外——那些在架构上不是「本机通信」，擅自绕过会改掉真实拓扑下的行为。
  - **测试侧另加一层兜底**：`nn-training/tests/conftest.py` 把**精确回环主名**（`127.0.0.1` / `localhost` / `::1`）补进 `no_proxy`/`NO_PROXY`——`proxy_bypass()` 认精确匹配，所以 8 个仍用**裸 `urllib.request.urlopen`** 打本机临时端口的既有用例（hub/worker/agent 的真实进程用例）一并脱离代理；生产侧不靠环境变量（就在 `net_http` 里）。两层分工：**生产靠代码、测试靠环境**，任一层单独失效都不会再让门禁变红（2026-09-18 实测：只改生产侧时 `test_multi_course_hub` 在满载下仍会吃到代理的 `Errno 111`）。
  - 回归（复现→修复，§7）：`nn-training/tests/test_loopback_http_no_proxy.py`（6 例，含一例钉 conftest 那层环境归一）——环境代理指到**死端口**后打本机真服务，三条出口必须仍通（修复前 `ConnectionRefused`，已用临时脚本实测 raw urllib 挂 / `net_http.urlopen` 200），另有一例钉「非回环仍走 urllib 默认」。
- **备选与否决**：让运维去改用户级 `no_proxy`（写成 `localhost,127.0.0.1`）——否（改环境不修代码，换台机器/换个人就复发，且**云机侧**同样可能带着代理变量）；一处处地改 `urlopen` 调用点、不建公共模块——否（同一个坑会被下一个新写的本机 HTTP 路径再踩一次，且「哪几条出口算本机」会失去唯一答案）；把回环判断塞进 `remote/worker.py::_get_opener()`——否（那个 opener 的存在意义就是「Colab 必须走代理」，两件事混在一个函数里迟早互相破坏）。
- **违反后果**：新写的本机 HTTP 路径若直接用 `urllib.request.urlopen`，在有代理变量的机器上会**静默**多一跳并可能收到代理的 502（症状像「hub 挂了」/「worker 离场」，实际两者都好好的）；反过来，若把非回环请求也一并绕开代理，Colab userspace 那条唯一出网路径会直接断（云机取不到 job/payload）。
- **落地**：`nn-training/remote/net_http.py`（新）、`remote/{hub_client,push_dispatch,worker,offline_deliver}.py`（改四处出口）、`nn-training/tests/conftest.py`（测试侧 `no_proxy` 归一，兜住裸 `urlopen` 的既有用例）；回归 `nn-training/tests/test_loopback_http_no_proxy.py`（6 例）。
- **本轮未做（P1 余下的形状整理）**：组件卡片按「单例角色 / 按课程」分组（见 §2026-09-18-goalnn-single-hub-single-tunnel 的「本轮未做」）。

---

### §2026-09-19-goalnn-shared-local-worker（2026-09-19，用户指令：localWorker 也不应绑定课程）

- **背景**：R3-3/R3-5 把 hub / 隧道 / trainer 收敛成共享实例后，本机 PPO worker 仍是「每课一份」——控制台按课起 `remote_worker --poll 本课 hub`，账本住 `localWorkers[<课>]`，work 目录与日志也 per-course。用户口径：「localWorker 也不应绑定课程，**它和云端 worker 一样，只与 hub 通信（pull/push），领到任务后直接执行，完成后回传结果**」。
- **为什么这是事实而不是需求**：`GET /jobs/next` **从来不看课程**——挑活的是 hub 的队列（每课程一条 FIFO + 跨课程轮转），响应里的 `course` 只是**随包告知的观测字段**；job 的 manifest 又自带整份课程快照（`course` 字段是课程文件正文），worker 侧连 reward 函数都是照 job 快照重建的。于是「按课程键控」只产生了三样副作用：① 一份进程只能服务一门课；② 同机多份进程抢同一份队列里的活；③ 「这门课的 worker」这个不存在的归属感。
- **决定**：`localWorker` 进 `SHARED_COMPONENTS`（账本槽恒 `''`，与 hub / 隧道 / trainer 同一张表同一套哨兵），spec **与课程无关**（`course: ''`、单一 `tmp/local-worker` work 目录、单一 `tmp/local-worker.log`）。
  - **启动幂等 + 换代接管**：已在跑 ⇒ 早退报「一个进程服务所有课程」；未跑 ⇒ 先 `supersedeLegacyInstances('localWorker')`（必须在 spawn **之前**：旧形状的每课实例与共享实例服务的是同一份队列里的活，多份并存就是互相抢）再 spawn，登记固定走 `''` 槽。
  - **重建路径拒每课条目**：`restartSpecFor('localWorker', <课>)` 在共享槽为空时返回 null——绝不允许凭一条每课残留就把共享 worker「重建」出来（那是操作员从未同意过的第二个进程）。旧的每课条目由启动/停止时的换代接管收掉。
  - **离开 local 的预设不连坐**（2026-09-16 那条「切到 pull/push 后卡片仍亮绿点」的修法升级版）：停共享 worker = 本机不再执行**任何**课程的 PPO job，故只在「本课是最后一门 local 课」时才停。判据是 `stack/local-worker.ts::coursesInLocalMode`——`courses.<课>.remote_transport === 'pull'` ∧ `remote_hub_url === sharedHubUrl(cfg)`，即 local 预设写下的那两个键（`prepareCourseForSharedTrainer` 的 local 分支）。**刻意不按「同端口就算本机 hub」放宽**：云机 pull 课程写的是 tailnet 地址但同一个 hub 端口，放宽会让「把唯一的课从 local 切到 pull」永远停不掉 worker——正是用户反馈要修的那个「以为未启动却在跑」。
  - **并发语义写进卡片文案**：一份进程同一时刻只干一份活（与云端 worker 逐字同语义——想要本机并发就多起几个云端 worker）；「停止」= 本机不再执行任何课程的 PPO job（云端 worker 不受影响）。
  - **读面的终点**：至此「服务面 · 单例」= selfNode + hubServer + cloudflared + trainingLoop + localWorker，「课程面」**没有成员**（`workerServe` 走节点行）。`cardFamilies` 因此只渲染服务面一组——**这不是坏了**，故在 `component-groups.ts` 里写明，并保留 `course` 族（它是 scope 的函数：日后真出现按课程的卡片会自然落进去，不必改代码）。
- **备选与否决**：① 保留每课一份 worker——否（见上三样副作用；且多份进程的日志把「谁在干活」彻底打散）；② 给 worker 加 `--course` 按课领活——否（hub 是**单队列**模型：job 自带课程快照，过滤只会制造「某门课的活没人领」这种静默饥饿）；③ 让 worker 按课起多个实例（worker 身份轴）——否（同一台机器多份进程抢同一份队列 = 旧的竞态，只是换了个名字）；④ 顺手把 `workerServe`（本机伪 GPU 节点）也收敛为节点轴——**本轮不做**：它的端口按课程派生（`slotPort(cfg, course, 'push')`，R3-1 刚把每课 push 端口摊开防撞），与 push 目标解析耦合，值得单独一轮。
- **违反后果**：任何「按课起一个 worker」的残留路径都会让同一份 job 被两份进程抢（一个白跑一轮）；任何无脑「离开 local 就停 worker」的写法会把其它 local 课的 job 变成无人领取（**表面训练正常**——最坏的一类静默失败）；反过来把 worker 常驻留着，则会把云机 pull 课的 job 抢来本机跑（云机空转，同样『正常』）——这三点正是判据要从配置算出来的理由。
- **落地（控制台）**：`core/registry.ts`（`localWorker` 进共享表；新增 `SharedComponent` 类型让换代/停止按它窄化，不再各自写名单）· `core/types.ts`（槽位契约注释）· `stack/specs.ts`（`localWorkerSpec(cfg, venv)`）· `stack/local-worker.ts`（共享启动 + `coursesInLocalMode`）· `stack/hub.ts`（`supersedeLegacyInstances` 收 localWorker）· `server/actions/{start,stop,restart,smoke,preset}.ts` · `server/api/component-meta.ts`（单一日志路径）。回归：`tests/local-worker.test.ts`（重写为共享形状 19 例：spec 与课程无关 / 槽归一 / 账本住空串槽 / 重建只认共享槽 / **换代不碰共享实例** / `coursesInLocalMode` 的五种配置 / 接线五处门禁）· `tests/{single-hub-tunnel,web-component-groups,web-components}.test.ts`（scope 与分族同步到真值）。
- **落地（训练侧）**：**一行未改**——worker 本来就行得通（`poll_job(base_url, token, …)` 无课程参数）。新增集成用例 `tests/test_local_worker_multi_course.py`(3，真 hub 进程内 HTTP + 真 worker 领活函数)：① 同一 worker 身份依次领到两门课的 job（跨课程轮转 + 响应自报 `course`）；②「先起 worker、后加课」时同一进程立刻能领新课的活；③ 形参围栏——领活链路里不得出现「课程」（哪天有人给 worker 加 `--course`，这条会红）。
- **未做（明确记录，不是漏）**：① `workerServe` 的节点轴收敛（见否决④）；② 本机多 worker 实例（worker 身份轴）——当前是「一份进程 + 云端多 worker」的并发模型；③ 真机实弹：本机 worker 领两门并行课的真实 PPO job（本轮是夹具级 + 进程内真 hub 的证据，与 R2e 同口径）。

---

### §2026-09-19-goalnn-course-worker-orthogonal（2026-09-19，用户口径：启动训练不选 pull/push 模式）

- **背景**：控制台启动一门课要在弹窗里选 Pull / Push / Local，并把选择折成**课程级传输耦合**写进 rl-config：`courses.<课>.remote_transport`（`auto|pull|push|hubpush|local`）、`courses.<课>.push_node_url`（把某课钉到某台机器）、`courses.<课>.remote_hub_url`、`courses.<课>.hub_push`。用户口径：**「启动课程训练时，trainloop 不需要指定 pull/push 模式。pull 模式是由远端 worker 自己请求，本机只需要保证 hub 在线，配以 tailscale/cloudflared tunnel。push 模式只看系统是否已经配置了 push worker 节点，界面留配置入口，节点数据存 rl-config.json」**，并强调「课程任务与 worker 节点互相正交！所有 worker 都可能接到在训的课程任务！本地 worker 与云端 worker 完全一致」。
- **决定**：**传输不再是课程属性，也不再是启动选项**——它是部署事实，由「有没有登记节点」推出来。
  - **启动编排只剩一条**：`selfNode → hubServer → trainer`（`TRAIN_START_ORDER`）。**pull 零配置**：hub 在线 + （可选）隧道就够，任何 worker 自己来领活；每次启动仍写 `rl.remote_hub_url`（pull 与 hub 派发都要它）。本机 worker 不再是「local 模式」的一部分——它是独立共享卡片，起它就参与领活，与云机逐字同权。
  - **push 只看登记事实**：`nodes[].gpu_push`（enabled）+ `rl.hub_push`（**缺省 true** = 配了节点就走 hub 中介派发）+ hub 地址/token ⇒ hub 按队列推给空闲 worker；缺任一 ⇒ 直推登记节点（按序 failover）；一个节点都没登记 ⇒ hub pull。判定是**纯函数** `stack/push-config.ts::remoteExecutionFace`（卡面徽章 / 启动详情 / 总览同一份）。
  - **课程级键全删**（类型表 + python 读面 + 写面一起）：`push_node_url` / `remote_transport` / `remote_hub_url` / `courses.<课>.hub_push`。python 侧对应：`_course_push_url` / `_course_hub_push` 删除，`_gpu_push_nodes(token)` 不再按课过滤（登记即全部候选），`_hub_push_opt_in()` 只读全局 `rl.hub_push` 且**缺省 True**；`--remote-transport` 仍在（运维钉死一条路的最后手段），但**控制台不再代写它**。
  - **清理而非停止读取**（`pruneLegacyCourseKnobs`，启动训练时跑一次、幂等）：残留的旧键会让「新配的 push 节点永远吃不到活」或「旧指针指向死端点」——都是**静默**失败，故从 rl-config 里剃掉并记一行日志。`local_push` 伪节点条目同时清（python 的 auto 会把它当真节点）。
  - **控制台界面随之收敛**：启动弹窗**删掉模式选择与 push 凭据输入**（只剩隧道/瘦身/rollout 三个 rl-config 选项 + 降级开关 + 预演入口）；`console-state.trainerPpo` 退役；localStorage 的 `tc.train.mode` 退役；`setMode('trainer.ppo')` 响亮拒绝。**唯一配置入口 = 「push worker 登记」面板**（增删改 `nodes[]` + `rl.hub_push` 开关 + 探活两列），节点数据住 rl-config.json，hub 按 mtime 热重载；执行面徽章从「本课指向谁」换成机群级「这轮 PPO 会去哪」。
  - **冒烟预演改用 env 独占**：`REMOTE_PUSH_NODE` 一旦设置就**只有它**（登记节点一律不参与），否则伪节点失败时 failover 会把预演的 job 送去真 GPU 上跑。
- **备选与否决**：① 保留模式但默认 `auto`（少改 UI）——否（「模式」这个词本身就是误诊源：它让人以为 pull/push 是每门课的属性，而实际是机群的部署形态）；② 保留 `courses.<课>.push_node_url` 但默认不写——否（留着就有「这次启动写了没写」的二义，且用户口径是彻底删掉；N:1 共享由「登记一次、全体候选」天然得到）；③ 只删读面、保留键「以防万一」——否（`local_push` 那条仍**有读者**：留一条指向本机死端点的 gpu_push 条目会让训练静默地跑不起来，而表面一切正常）；④ 让控制台继续往 `rl.hub_push` 写 `1`——否（缺省已是开，写死反而让「显式关掉」在下次启动被覆盖）；⑤ 保留 `trainerPpo` 只为展示历史模式——否（它的唯一用途就是启动时选路，没有読者就成了一个会误导人的死键）。
- **违反后果**：任何重新引入按课程的传输旋钮（`courses.<课>.remote_transport|push_node_url|hub_push`）的改动都会重新制造「同一门课换个机器就得改课程配置」与「某课被某台机器独占」的耦合；把 `rl.hub_push` 缺省改回 `false` 会让「配了节点」不再够用（还得记得去开开关）——用户明确要的是「配了就走 hub 派发」；把 `REMOTE_PUSH_NODE` 的独占性去掉会让冒烟预演在伪节点失败时把 job 送上真 GPU。
- **落地**：训练侧 `rl/{loop_steps,loop_serve,bc_loop,cli}.py` · `run_bc.py` · 回归 `tests/{test_course_push,test_serve_course_overrides}.py`；控制台 `core/types.ts` · `stack/{course-knobs,push-config,specs,local-worker}.ts` · `server/actions/{start,preset,workers,console-state,train-smoke}.ts` · `server/api/{route,courses,overview,snapshot-cache,state-view}.ts` · `web/view/{console-types,course-overview,legacy-keys}.ts` · `web/app/{app.tsx,panels/{TrainLaunchModal,WorkerRegistry,ComponentCards}.tsx}` · `theme.css`；回归 `tests/{push-config,web-train-launch-wiring,server-actions-worker-register,server-api-state-view,server-api-route,training-shared-trainer,training-multi-course,training-train,local-worker,slim-launch-option,rollout-src-launch-option,web-components,web-app-course-overview,web-app-bc-rl-exclusive,web-app-hero-overview,web-view-trend-range,training-console-busy}.test.ts`（`push-config` 重写为「部署事实推导表 + 防回流尺子」，其中一把尺子剥注释后扫代码，因为文档注释里恰恰写着这些键已退役）。
- **仍未做（明确记录）**：① 真机实弹——「一个 serve 进程同时带 RL + BC 并行课 + 云机登记节点」的端到端（本轮全在夹具下证明逻辑）；② `Dashboard README / docs/features.md` 里的模式说明未同步（属文档面）；③ 冒烟预演仍是「本机伪节点 + env 独占」形态——若将来支持「预演也用真节点」，需另开一轮设计（当前口径是预演绝不碰真训练）。

---

### §2026-09-19-evalcourse-dist-eval（2026-09-19，eval-course-ckpt 开分布式评估：同节点门 + 同规 stageJson）

- **背景**：判决类评估（T5/T6 体量：多权重 × 数百～800 局课程自定义关）在纯本地
  chunked 路径（一 chunk 一 fresh worker，物理核封顶）上耗时以小时计，而节点在跑
  rollout 的间隙具备 eval 能力（`evalSupport` + `stageJsonSupport`），且 m1-eval 早已
  用同一 HTTP 协议派发 eval 局。缺的是**把 eval-course-ckpt 的逐局行**（JSONL，
  `phase0-fingerprints`/`paired-pd` 消费）也搬到节点上。
- **备选与否决**：① 另起一个 dist 专用工具 —— 否，映射/汇总两套实现必然漂移；
  ② 把课程评估塞进 m1-eval —— 否，m1-eval 的行语义是 godai scorecard（stageIndex/dims），
  不带课程自定义关与逐局 Phase 0 列；③ 只靠本地并发不开 dist —— 否，节点算力闲置；
  ④ 强制显式 `--dist-nodes`（不 auto）—— 否，与 m1-eval auto-dist 同规（用户 2026-08-29
  指令：节点随时上线、每批都要吃满；`--no-dist` 显式关）。
- **决定**：eval-course-ckpt 增混合分派路径，**缺省仍是原本地 chunked 路径（行为逐字不变）**。
  节点门与 rollout/m1-eval 同源：`evalSupport ∧ stageJsonSupport ∧ bun major.minor ∧ codeHash`，
  不匹配只 skip（打印原因）不中断；局经 `mode=eval` + `kind=<rollout|none>` + `stageJson`
  （课程自定义关原文）+ `livesOverride`/`playerLevel` 派发，stageJson > 16KB 自动退回纯本地；
  回包 BCV2 manifest 顶层 → `manifestToCourseRow`（缺 Phase 0 键填零，旧节点不崩）。
  `--dist-local`（缺省 = `--workers`）保留本地份额；`--policy nn-goal` 强制本地（GOAL_* 未
  进 agent 协议）。**真相锚**：同 (stage, seed, 权重) 的 dist 行与本地行**逐字节相同**
  （2026-09-19 实测 `diff` 空；§16.6 并行==串行验收）。
- **违反后果**：节点门放宽成「能 ping 就派」⇒ 新旧代码混跑，Phase 0 列静默缺失/口径不同，
  判决用错读数；改 `export-eval-game.ts` 顶层 schema（**在 codehash-files.txt 集内**）而不
  等节点同步 ⇒ 门把全节点判 stale、dist 静默退化成本地（本条的已知代价，不是故障）；
  直接拿 dist 行的 wallSec/网络时段做节点算力对比 ⇒ 网络污染（见
  §2026-09-19-console-node-wallsec）。
- **落地**：`tools/sim/eval-course-ckpt.ts`（`buildCourseJobs`/`buildRemoteTaskUrl`/
  `manifestToCourseRow`/`nodeGateReason` + `runHybrid`）、`tools/sim/export-eval-game.ts`
  （报告顶层补 Phase 0 逐敌种列 hitsByKind/killsByKind/exposureByKind/firstHit/firstKill/
  killOrder/killerKinds；集内文件 ⇒ 需节点 resync）、回归 `tests/eval-course-ckpt.test.ts`
  （纯函数对拍）+ 实测：对 `self` 节点 2 局 god 全链（ping→权重下发→stageJson 派发→回包
  →JSONL 行，Phase 0 列齐全）通过。

---

### §2026-09-19-nn-decision-instant（2026-09-19，nn 决策时点归一到语料口径：NNInput 末帧决策 + nn 入列分派白名单）

- **决定**：① `m1-eval` 分派白名单加 `nn`（`--weights-dir` 解析出**最新**权重文件后上传，`kind='rollout'`）
  ——上一轮条目「nn 走 --weights-dir 自动发现、无文件可上传 ⇒ 留本机池」的**理由已被自身证伪**（解析出的
  文件就是可上传的权重）。② `src/nn/policy-input.ts`（NNInput）的**决策时点**改为「tick 末决策、下一 tick 生效」。
- **背景（2026-09-19 用户要求「nn 分派后与本地一致」时实测挖出）**：nn 有**两套实现且从不互相对账**
  （god 有 `tests/sim/eval-game-parity.test.ts` 钉远程/本地同局，nn 侧无对应用例）。
  同一 (权重=ep96, stage 0, seed 1..6)、同 maxTicks/difficulty：
  节点引擎 `export-eval-game` 得 4601/3167/6623/2113/2606/5580；本机池（`runSimulation`→NNInput）
  **6/6 全不同**（首个动作分歧在 seed 1 的 tick 290）。
- **根因两层，都在 NNInput**：
  1. **观察时点**：`Simulation.tick()` 先 `frame++`、递减 freeze/emp/spawn/pickup 计时器、跑
     `updateSpawning()`，**之后**才读玩家输入（Simulation.ts:205-245）；而三个构建器都在自己
     `sim.tick()` **之前**观察（`export-rl-rollout.ts:642`、`export-nn-replays.ts:116`、
     `export-eval-game.ts:482`）⇒ mid-tick 决策看到的是策略训练时从未见过的状态。
  2. **相位**：NNInput 用 `frame % K === 0`，构建器用 `t % K === 0`（`world.frame` = 已完成 tick 数
     = 构建器的 `t`；末帧 `frame` 即在读输入时是 `t+1`）⇒ 决策 tick 整体错一格。
- **修法**：决策只在 `reset()`（tick 0，调用方都在 `loadStageData` 之后 reset）与 `endFrame()`
  （`world.frame % K === 0`、且状态仍为 `playing`）发生；`getMoveDirection()`/`isFiring()` 只读已提交动作，
  不再 mid-tick 前向。推理次数仍是 1/K；`thinkNow()` 保留「当前状态强制一次前向」的诊断语义
  （divergence-probe 用）。
- **影响面（必须知道）**：历史所有**本机** nn 评估读数（m1-eval 本机池、eval-course-ckpt 本机 worker、
  `export-dagger-labels`、`nn-trace`）都是在该错时点上评的 ⇒ 这些数字会变；**远端节点侧不变**
  （export-eval-game 本来就是对的），故此前「本机池 vs 分派批」的混跑读数本就不可配对。
- **验证（四腿一致，stage 0 seeds 1-3 = 4601/3167/6623 gameover/gameover/stage_clear）**：
  真集群分派（weights 上传 → `mode=eval` kind=rollout → self 节点，`provenance: node:self=3 远端 3 / 本地 0`）、
  本机池、Python 分派路径（localSlots=3 实跑）、节点引擎 CLI 直跑——四者逐局 ticks/kills/outcome 全等；
  in-process 对拍 5 局（stage 0 seeds 1-3、stage 5 seeds 1/7）逐 tick 动作序列零分歧。
- **守卫（本案的副作用）**：`src/nn/policy-input.ts` 在 codeHash 集内 ⇒ 改它会让全集群 stale（节点 hash
  memo 到 `/v1/update` 真 pull 才失效）。self/回环节点**纯重启**（共享工作区、禁 pull、不受脏工作区护栏
  限制）即收敛到本机 live hash（实测 `c78d48de`）；其余节点须 **commit + push → `--upgrade-nodes`**
  （`dist_common.request_upgrade_guarded` 对脏工作区拒发，日志点名未提交的集内文件）。
- **回归守卫**：`tests/sim/eval-game-parity.test.ts` 新增 nn 分支对账（stage 0 seeds 1/2，maxTicks 3000，
  权重用 `tests/fixtures/student-golden.json` 的 params 落进临时目录的 `weights.json`）——已实测：旧
  `policy-input.ts` 下该用例**红**，修复后绿（≈0.3s）。
- **追记（2026-09-19 16:40，push 后真多节点复验）**：`908f7cf` 推到 `origin/goal-nn` 后集内脏集合清空
  （`{"dirty": []}`）⇒ 守卫放行远端升级：mac/a97 `restart-requested`、a95 首轮 `restart-failed` 但随即收敛，
  self 本就 current ⇒ **4/6 节点同本机 `c78d48de`**（a96 agent 不可达、gcs 拒重启，属用户已裁定的环境事实，不追 6/6）。
  12 局实跑（stage 0 seeds 1-12，`localSlots=0`）：**12/12 全落远端**（`provenance: node:mac=4, node:self=8`，
  7.5s），逐局 outcome/ticks/kills 与**本机池完全相等**（4601/3167/6623/2113/2606/5580/3535/3527/7183/2531/5875/2794），
  聚合也一致（`WIN RATE 33.3%`、`SCORE V7 suite=0.3621`）——跨两台不同机器验证，不再只是同机自证。

---

### §2026-09-19-eval-tier-channels（2026-09-19，B/C 层派发改为「每节点独立通道」：无阶段屏障、就绪即派单、失联重探、settled 满即断连）

**触发**：用户 2026-09-19 五条裁定（原话）：①「不要把分发权重和派发 eval 任务划分为串行的不同阶段！！！
一个节点权重分发成功后，立即！马上！right now！给它派发任务！！！不要等慢节点！」②「不要分什么 u0/u1/u2
阶段！！！一直持续不停派发，直到所有 eval 任务完成！」③「已经在正常工作的节点！！！就不要再 ping 它！
不要再给它分发同样的权重，一直派活就好了！」④「竞速后 settled 一满，直接关闭所有节点的连接！！！立即！
马上！right now！」⑤「失联的节点，每 20 秒 ping 一次，ping 通了就立即传权重派任务！」。两次追问收紧了细节：
「从 settled 满到 DONE 为什么还花 5 秒？能省掉吗？」（→ 收工只等写行的赢家）与「你这任务都没分出去呀」阶段
日志停在 13:17 的反面取证。

**实施（B/C 层 `rl/batch_eval.BatchEvalRunner`）**：派发由「ping 全部节点 → 全部节点收权重 → 才开派」
三段串行 + 每单元重跑，改为**每节点一条通道**（`_run` 内 lane 状态机）：

- **lane**：`{ready, tripped, next_try, tries, strikes, given_up, c(槽位)}` + 归一化节点描述
  `{"id","url","key"}`（旧实现直接拿配置 dict 取 `nd["key"]` 会 KeyError —— 本轮实测踩过：整批 0 局）。
  `supervise(nd)` 线程：未就绪 → `bringup`（ping → `post_weights`）；`ready=True` 才算消费者；就绪后**只在**
  该节点槽位线程里领活（永不重复 ping/传权重 = 裁定③）。
- **无阶段屏障**（裁定①）：每个节点的通道各自就绪、各自开派；`local` 槽位不需要门/权重 ⇒ 与节点通道并行、
  立刻开工。慢节点再也拖不住整批。
- **失联重探**（裁定⑤）：`recoverPingSec`（缺省 20s）重探一次，ping 通 → 立即传权重 → 立即派单；日志逐次
  响亮（`node <id>: ping 失败/超时（3.20s）— 20s 后重探`）。**有界**：连续 `nodeRecoveryTries`（缺省 3）轮
  「恢复后仍 0 局成功」⇒ 判定节点是坏的（不是一时失联）而非无限等；任意一局结算即清零轮次（有进展即信任）。
- **settled 满即断连**（裁定④）：最后结算的 worker 置位 `all_done` 后立即 `dist_common.abort_active_requests(scope)`
  —— 停发新请求 + **异步**（daemon 线程）关闭在飞连接。**作用域 = 线程 tag**（`batcheval:<iterId>`）：训练主循环
  里 rollout 与本层同进程并发，全局关连接会误伤别人的在飞请求。新单元开头 `clear_abort()`。
- **收工只等「写行的赢家」**（用户追问）：`writers` 计数（赢家在锁内自增、`record()` 落盘后自减），收工等它归零
  （≤1s 兜底，实测同秒）；线程 join 只给 0.25s 总预算（daemon 线程本就随进程退出）。慢节点/竞速副本的回包
  **一律不等**（那些行永不需要）——旧实现 join(5s×线程) 实测占 800 局墙钟 3%，更早还有 `close()` 阻塞主线程
  **81 秒**（占该批 32%）。
- **失败分类补洞**：主动断连/已结算任务的回包按「无关」丢弃（既非背压也非节点故障，否则会误熔断慢节点）；
  硬失败**每条都留痕**（原先只有「还会重排」的那条打日志 ⇒ 实测「真失败计数 gcs=1」却零原因可查）；竞速副本
  **不再消耗** `attempts` 配额（一局被 6 台各抢一次后一次真失败就会耗满配额而丢局）。

**单单元跨关（一次性评估，裁定②）**：`eval_course_once.build_course_units` 改为**每权重一个单元**、跨该权重的
全部关卡（`unit["pairs"]` = `[[stageId, seed], …]`，`unit["stageParams"][str(stageId)]` 带逐关
stageJson/lives/level/maxTicks/difficulty；B 层 `params_for` 逐字段回落 unit 级值）。ladder/corpora 的既有单元
（`stageId` × `seeds`）逐字不变。副作用修复：行元数据改按 **`(ckpt_sha16, stage, seed)`** 分键——原先
`meta[(stage, seed)]` 在多权重调用下被后一个权重覆盖，逐行 label 全错、`_row_id` 的 `wi * games` 也全错（多权重
产物实际不可用）。

**被否方案**：① 保留阶段结构只做并行（`ping_nodes_parallel`/`post_weights_parallel` 已有）——阶段**屏障**本身
才是病根，快节点仍要等慢节点；② 在 TS 侧实现同样的通道（`tools/sim/eval-course-ckpt.ts` 自带一套重试/探测）
——用户已裁定「Python 端有长期实战检验的机制，不要在 TS 重写一套」，本轮继续单一实现（TS 只写 spec/读行/打分）；
③ 无界重探失联节点——一个必坏节点会拖满整窗（一次性评估窗口缺省 86400s）；④ 收工 `join` 全部线程——实测 5s+，
且对结果毫无贡献。

**实测（800 局 × ladder-c20-lives1 × x20-rebirth it96 × seed0 418000，全远端 / 本地 0）**：
| 形态 | 墙钟 | 说明 |
|---|---|---|
| 旧（4 单元 × 3 阶段，17:34） | 297.0s | 全远端 |
| 旧（带事件追踪，17:42） | 653.2s | 慢节点兜底尾巴空转 170s |
| 新（首轮验证，18:41） | **268.3s** | 181s 跑完 800 局 + **86s 收工阻塞（已修）** |
| 新（断连非阻塞，18:48） | **166.8s** | 4.8 局/s；开跑时 3/6 节点失联，a95/a97 于 18:48:55 重探成功后立即投入 |
| 新（收工只等写行，18:58） | **174.0s** | `settled 满` → `DONE` **同秒**；`dup=20` |
| 新（与门禁并发，19:00） | **202.8s** | 本机槽位争用下的保守值 |
逐局对拍（对旧形态产物，三次）**0 差异**（800/800 行、passed 154/800、kills 7144）——本改动是纯调度，不改语义。

**配置旋钮（`policy` 段，均有缺省）**：`recoverPingSec`（20）· `nodeRecoveryTries`（3）· `noConsumerGraceSec`（180，
**只在整批从未有过任何消费者**时生效：无节点就绪过 + 本机槽位 0 ⇒ 有界响亮收摊而非等满窗）。

**已知局限（如实记录）**：阻塞在 `urlopen`（响应头都还没回来）的慢节点连接不在注册表里 ⇒ 关不到；那部分只能等
其自身超时（`taskTimeoutSec`）。`all_done` 已置位 + 新请求拒发 ⇒ 调用方不再依赖它们的结果。日志里的
「断连 0 条在飞连接」正是这个含义（此刻没有处于读体阶段的响应），不是没做事。

**守卫（`nn-training/tests/`）**：`test_batch_eval.py` +5（门判据纯函数 / 就绪节点只 ping 1 次 + 传权重 1 次 /
失联重探后可用 / 快节点不等慢节点 bring-up / settled 满即断连且不算节点故障）+ `test_dist_common_poll.py` +1
（`abort_active_requests` 不阻塞、置位后新请求按瞬停分类、别的作用域不受影响）+ `test_eval_course_once.py` +1
（每权重单单元跨关 + 多权重元数据分键）。python 门禁 1324 例全绿。

---

### §2026-09-19-node-fault-taxonomy（2026-09-19，A/B/C 三层共用瞬断判据 + 409 wver-not-cached 自愈：把「节点故障」与「可刷新条件/背压」分开）

**触发**：用户 2026-09-19 审计后指名修 A1+A2+A3（训练侧 rollout/eval 与一次性评估同源的三个洞）。
证据链（`tmp/x20-rebirth/training-loop.log`，08:00–10:36，235 个 rollout 轮 / 20 个 eval 轮）：

- **A1**：09:48:29 `weights[rollout] reuse wver=1a01aa045436… skip POST for ['self','mac','a97','gcs']`
  → 5 条 `HTTP 409 {"error":"wver not cached here"}` → `node a97: 3 consecutive failures —
  circuit-broken for this round`；该轮 `byNode={"self":11,"mac":8}`，**a97 的 7 个槽位整轮闲置**。
  而同一 wver 在 44 秒前（09:47:45）刚在 a97 上 POST 成功（`(purged)`）。
- **A3**：09:48:13 三条 `HTTP 502:`（cloudflared 隧道，非节点问题）同样记 streak → a97 熔断；
  实测分布：rollout 617×503（旧实现唯一豁免项）+ 9×502 + 5×409 + 1×10054；eval 层 153×503 +
  1×502 + 1×10054，**旧实现对这 155 次全部当节点故障**（3 次即熔断该节点整轮）。

**实施**：

1. **判据单源**：`dist_common.is_transient_error(e)`（408/425/429/500/502/503/504、`DistError.transient`、
   文案含 busy、非 DistError 的 OSError/TimeoutError）成为唯一实现；`rl/batch_eval.is_transient_error`
   退化为薄转发（保留名以兼容既有引用与单测）。A 层（`rl/dispatch.py`）与 C 层（`rl/eval_dispatch.py`）
   直调同一实现——旧实现只有 B 层有判据、A 层只豁免 503、C 层什么都不豁免。
2. **409 = 可刷新条件**（`dist_common.refresh_weights`，A/C 两层共用）：失败时**清进程内 reuse 缓存**
   （`_WEIGHTS_PUSHED` 原先只在 ping/codeHash 门失效 ⇒ 脏缓存让 409 持续到熔断）+ **就地重发**该 wver；
   成功则不入 streak、不耗 attempt 配额，同一节点继续用。重发也失败才按真失败记。
   （节点侧根因是 per-kind 桶只留 KEEP=4 份且**所有客户端共享**——别的训练作业/本机 eval 上传/agent
   重启都能把文件挤掉，而客户端看不出来；修节点侧要动 `tools/agent/sampler-agent.ts`（codeHash SSOT
   ⇒ 全集群 stale + 需 push），故本轮只做客户端自保。）
3. **瞬断不计节点故障，但有上界**：新增 `soft_streaks`（`policy.nodeSoftFailStreak`，缺省 3×`nodeFailStreak`）。
   单次瞬时错误不熔断；**连续**软失败达到上界则停派该节点并单独措辞记日志（`连续 N 次瞬时失败（背压/瞬断，
   非节点故障）— 本轮停派`），与真故障的 `circuit-broken` 区分。理由：不给上界的话，隧道/集群整体脉停时
   会无限重排把整轮拖到窗口超时（旧行为是快速熔断，也有害，但至少不空转）。
4. **可观测**：背压/瞬断重排日志带上「瞬断/背压（不计节点故障）」；409 自愈单独一条
   （`wver not cached（409）—— 已就地重发权重（…）并清 reuse 缓存`）。

**被否方案**：① 只在 A 层补 503 之外的豁免（不共用判据）——B/C 层仍在误熔断，且三处判据必然漂移；
② 把 409 也算 transient 一扔了事——409 是**可修**的（重发即恢复），扔进背压队列会让同一节点持续 409、
   任务被重排到窗口耗尽，还丢掉「节点侧到底有没有那份权重」这个信号；③ 瞬时错误完全不设上界——
   整体脉停时整轮空转到 deadline（见 3）；④ 让 rollout 也改用一次性评估的专用 kind 隔离权重——
   rollout 的权重就是节点采样要用的那份，无法隔离，只能保证丢了能立刻补。

**实测（单测，非仅源码断言）**：A 层新 `tests/test_rollout_dispatch_resilience.py`（7 例）——502×3 后
4/4 局全结算（`dist.nodes={"a97":4}`、retried=3、无 `circuit-broken`）／真故障连续 3 次即停派（只取活 3 次）／
409 触发一次就地重发且节点继续跑完整轮／软失败上界停派；C 层新 `tests/test_eval_dispatch_resilience.py`（8 例）
——502×3 后 4/4 结算、409 自愈、真故障仍熔断；`test_dist_common_poll.py` +4（分类表、刷新语义与缓存清空、
单源守卫：全仓只有 dist_common 一份实现、B 层必须是纯转发、A/C 层必须接线 409 分支）。
**两套行为测试都在旧代码上实测变红**（A 层：`missing=[全部 4 局]`；C 层：0/4 结算、`refreshed==[]`），
不是事后补的绿灯。nn-python-gate 1343 例全绿。

**已知局限**：客户端无法阻止别的客户端（或 agent 重启）挤掉权重，只能事后补；`soft_streak` 上界是启发式
（3×真故障阈值），不是测量结果；409 重发是整份权重上传（MB 级），高频 409 会吃带宽（实测一次/轮量级，可接受）。

---

### §2026-09-19-rollout-midround-recover（2026-09-19，A 层 rollout 中途重探真正跑起来 + 轮内回场：`rescan_nodes` 从「每轮 0 次 pass」到 5s 首探/20s 周期 + 已停派节点回场）

**触发**：用户 2026-09-19 指名修 A4（审计发现）。**证据（两条独立线索都指向 0 次执行）**：
① `tmp/x20-rebirth/training-loop.log` 2.5h / **235 个 rollout 轮**里 `rescan` 日志 **0 行**，而同期
节点排除 130+ 次（`ping failed`：a96 61 / a97 43 / a95 26）；② 机制上必然如此——旧实现
`sleep_sec = min(rescan_sec=120, …)` → `all_settled.wait(sleep_sec)` → 紧接
`if all_settled.is_set(): return`，而该 run **234 轮全部 <120s**（p50 7s，max 115s）⇒ 线程每轮都在
首个 sleep 里被结算事件唤醒并退出；且候选集用 `if nid in spawned_ids: continue` 过滤，**已熔断的
节点永远不是候选**（熔断即整轮出局，窗口 1800s）。

**实施（`rl/queue_local.rescan_nodes` + `rl/dispatch` 调用点）**：

- **首个 pass 提前**：`nodeRecoverFirstSec`（缺省 5s）后首探，之后每 `recoverPingSec`（缺省 20s，
  与 B/C 层同口径；旧的 `agentRescanSec` 仍可覆盖、显式 0 = 关闭）。防忙等地板从 0.5s 降到 0.05s
  ——0.5s 地板会让小值旋钮（测试/调频）名不副实。
- **候选 = 尚未孵化 ∪ 已停派**（`streaks ≥ nodeFailStreak` 或 `soft_streaks ≥ nodeSoftFailStreak`）；
  后者是新增的「回场」路径：清 reuse 缓存 → 强制重握手（`post_weights` 自带 cached 探针）→
  **重置失败计数** → 补孵 c_n 个采样线程。每节点每轮 `nodeRearmLimit`（缺省 3）次上界，
  用尽后告警一次并停手（防「永远失败的节点」无限起线程）。
- **ping 并行**（`dist_common.ping_nodes_parallel`，与 B/C 层同款）：串行 3s/台会让一个 pass 卡十几秒。
- **两个旧 bug 顺带修**：① 漏传 `kind=wkind` ⇒ goal/intent 腿的中途上线节点把权重发进 rollout 桶，
  任务全 409（与 §2026-09-19-node-fault-taxonomy 的 A1 同一类陷阱）；② `nd` 漏带 `ping`
  ⇒ stageJson 任务在回场/中途上线节点上被能力握手拒掉（自定义关课程下等于白孵这些节点）。
- **调用点全关键字传参**：该调用有 20+ 实参，历史上第 19 个位置参数错位过一次（线程启动即抛、
  运行中上线的节点永不被发现）——位置传参是这类 bug 的温床。

**被否方案**：① 只把 `rescan_sec` 缺省从 120 改小——线程仍会在结算瞬间退出，「短波轮里永不扫描」
不变；② 用 `time.sleep` 代替 `all_settled.wait`（保证 pass 跑到）——会让 round done 滞后一个
cadence（2026-09-05 修过的老毛病复活）；③ 回场不做上界——节点持续失败时会无限补孵线程；
④ 回场沿用进程内权重缓存（跳过 POST）——节点刚重启/桶被挤时缓存是脏的，正是 A1/A2 的教训。

**契约（测试钉住，11 例）**：`tests/test_rollout_dispatch_resilience.py`
中途上线节点在轮内供样（旧实现 `{'self': 6}`，新实现 a97 拿到 ≥2 局）／熔断后轮内回场把剩余任务跑完
（旧实现 6 局全 missing，新实现 6/6）／真失败「熔断 + 有界回场」= 3×(`nodeFailStreak`)×(1+`nodeRearmLimit`)
= 12 次取活、回场日志恰好 3 条／`nodeRearmLimit=1` 时上界告警且共 6 次取活／halt 置位后 3s 内收工
（不等满 30s 窗口）。**A4 三条行为测试在旧代码上实测变红**。nn-python-gate ✓（1347 例）。

**已知局限**：回场会暂时叠加线程（旧线程可能还在收尾一局，新线程又孵），故为「有界多孵」而非
精确替换——自愈性影响可忽略（同一节点、多余槽位在下一轮自然收敛）；节点侧根因（per-kind 桶
KEEP=4 且全客户端共享）仍未修，回场只是客户端侧的自保（见 §2026-09-19-node-fault-taxonomy）。

---

### §2026-09-19-eval-gate-lanes（2026-09-19，C 层（训练干净评估）的门与收工形态：并行 ping + 逐条留痕 + POST 全败走本地 + 本机槽位先开工 + settled 满即断连）

**触发**：用户 2026-09-19 连续两条指令（「把 C 层收工空等 4–76s/轮榨掉：all_done 即断连 + 只等写行的赢家」、
「把 eval 层的病态分支与串行门一起修」），底稿是同日的 C 层审计（`tmp/x20-rebirth/training-loop.log`，
08:00–10:36 共 20 个 eval 轮）。

**证据（五个缺陷，均为一手日志）**：① **B1 收工空等**：末局结算 → `DONE` 的墙钟 = it10 42s / it15 47s /
it40 32s / it80 76s，而这些行在 `all_done` 置位前就已全部落盘——旧收工是
`t_.join(timeout=max(30, window + task_timeout))`，卡在 HTTP 里的线程要等请求自己结束；② **B2 门串行**：
逐节点 `node_ping(timeout=3s)`，墙钟 = Σ 每台延迟（两台超时即 ~7s），而门每轮重跑一次；③ **B4 静默丢节点**：
`if ping is None: continue` 零日志——节点被丢时既看不出是谁、也看不出为什么（`ping failed` 计数只能靠
别的账本反推）；④ **B5 病态分支**：`if not nodes_ok` 时无条件 `return`，本机槽位明明可用却整轮 0 局
（且旧写法的 `if not alive and …` 条件自相矛盾）；⑤ **B3 门即屏障**：权重 POST 全部返回后才孵化线程，
本机槽位干等（本地权重就是本机冻结快照，根本没有下发开销）。

**实施（`rl/eval_dispatch.py`；常量 `EVAL_INFLIGHT_GRACE_SEC` 落 `rl/eval_local.py`）**：

- **B1**：收口**显式**成三段——① 等 `all_done`（或墙钟 `deadline`，或消费线程全退）；② 未满时给在飞局一个
  **有界**落账窗（`min(task_timeout, EVAL_INFLIGHT_GRACE_SEC=120)`，在飞清空即走）；③ 之后
  `abort_active_requests(req_scope)` 断连 + 拒发新请求，只等**正在写行的赢家**（`writers` 计数，1s 兜底）
  并把线程 join 预算压到 0.25s。`writers` 在 `seen.add` 时 +1、`record()` 落盘后 -1。
- **B2**：门改用 `dist_common.ping_nodes_parallel`（保序，== 最慢一台）。
- **B4**：`ping is None` 逐条 `[eval] node <id>: ping 失败/超时（并行探测，预算 Ns） — 本轮不参与`。
- **B5**：`alive` 非空但 POST 全败 ⇒ 本机可用则 **local-only**（响亮记一行），不可用才跳过；
  `alive` 为空时不再重复打「POST 失败」误导行。门/权重段整体移到闭包之后（B3 的前提）。
- **B3**：本机槽位在**门之前**孵化并启动（`_spawn_tracked`），门/权重只影响节点侧。
- **顺带（收工的护栏）**：`live_workers` 计数（孵化即 +1、线程退出 -1）——「任务全被 drop 且无在飞」时
  旧形态只能空等整个窗口（60/1500s）；现在全退即收工，并在日志里注明「消费线程已全退」。

**被否方案**：① 只加一个 `all_done.wait(…)` 超时——收工只是变慢而非**立即**，且窗口到期时仍会把在飞的
有效局一起砍掉；② 用 `join(timeout=2s)` 代替断连——线程仍卡在 socket 读上（进程内连接数按节点并发累积）；
③ 门/权重段整体前移到快照之前——`nodes_ok` 必须先于 `streaks`/日志，前移等于把闭包拆散；改为「本机先开工 +
门后移」；④ 本机槽位在门失败时也照旧 `return`（旧行为）——用户点名要「POST 全败走本地」。

**契约（测试钉住，新增 7 例）**：`tests/test_eval_dispatch_resilience.py`
并行门（3 台 × 0.3s 实测 <0.7s；串行基线实测 0.906s）／ping 失败逐条留痕（含节点 id）／
POST 全败 + 本机可用 ⇒ 2/2 局全由 `local` 结算且日志为 `— local-only eval this round`／
本机也不可用时仍响亮跳过／本机首局早于权重门完成（`post_delay=1.0s`）／
settled 满（4/4）不等慢节点（慢节点 fetch 睡 3s，整轮实测 <2s）／窗口到期不砍在飞局（有界宽限）。
**其中 5 例（B1/B2/B3/B4/B5）在旧代码上实测变红**（`git show HEAD:…` 换回旧实现跑同一套测试：5 failed /
10 passed；旧实现必须在 `--maxfail=99` 下一次拿全，因为 pyproject 的 addopts 带 `-x`）。

**已知局限**：① `clear_abort()` 是**全局**清账（同进程并发的另一轮 eval/baseline 的收工态会被清）——
与 B 层 `batch_eval` 同款语义，重叠窗口只在 baseline 与 A-eval 同迭代并发时出现，后果是少量 dup 回包
（丢弃、不双计）；② 阻塞在 `urlopen`（响应头都未回）的连接不在断连注册表里，只能等其自身超时
（`all_done` 已置位，结果本就被丢弃）；③ 本机槽位在门失败时立即开闸（`release_local_gate_if_starved`）
⇒ 本机局可能与训练主循环的资源窗重叠（与「无可用节点」分支同语义）。

---

### §2026-09-19-eval-weights-kind（2026-09-19，训练干净评估的权重 kind 独立成 'eval'——不再与训练 rollout 共用节点权重桶）

**触发**：用户 2026-09-19（「给训练评估独立 kind（我定名 eval），顺带一次现场验证」）。来源是同日审计的
**B6** 项：`rl/eval_dispatch.py` 的权重下发与局请求都走 `kind="rollout"`，与训练 rollout 的 churn 共用节点
同一个权重桶。

**根因（节点侧分桶语义，已核实代码）**：节点按 `(kind, sha)` 分桶缓存（`sampler-agent.ts::weightsByKindSha`）：
内存桶**每 kind 上限 64**（`WEIGHT_BUCKETS_PER_KIND`）、落盘文件**每 kind 保留最新 4 份**
（`workdir-cleanup.ts::WEIGHT_FILES_KEEP`，在飞桶引用的文件豁免），而 `/v1/task` **只查内存桶**
（`weightsOf`，不回查磁盘）。⇒ eval 与训练 rollout 同 kind 时，eval 那份与训练每轮的 churn 共用同一组
计数（64 / 4），任一侧轮换都可能把对方挤掉；被挤掉后节点答 409「wver not cached here」，客户端只能靠
409 自愈重发兜（A1）。

**决定**：`EVAL_WEIGHTS_KIND = "eval"`（`rl/eval_dispatch.py` 单源常量），**三处同源**——权重 POST 的
`x-kind`、局请求的 `?kind=`、409 自愈重发的 `kind`。协议侧 kind 是**不透明字符串** ⇒ 旧节点无需任何改动
（现场实测 5/5 台未升级节点直接接受并正常出局），也不触 codeHash（`nn-training/**` 不在 SSOT 内）。

**配套（同一坑的另一面）**：进程内下发账本 `dist_common._WEIGHTS_PUSHED` 由**键 = wver** 改为
**键 = (kind, wver)**（`note_weights_pushed` / `weights_already_pushed` / `partition_weights_nodes` /
`refresh_weights` 同改；缺省 `kind="rollout"` ⇒ 既有调用方行为逐字不变）。理由：同一个权重文件（同一 sha）
会被两条腿使用（干净评估评的就是刚训练出的那份 θ）——账本不带 kind 时，先跑那条腿的 note 会让另一条腿
被判成 reuse 而**跳过 POST** ⇒ 该节点对另一条腿整轮 409（脏缓存，与 A1 同类陷阱、方向相反）。
A 层调用点同步接线：`rl/dispatch.py`（`kind=wkind`）、`rl/queue_local.py`（`kind=wkind`）。

**顺带修（现场探针实测踩到）**：`dist_common.ping_nodes_parallel` 只读 `authKey`，而 `post_weights_parallel`
两种都认（`key` / `authKey`）⇒ 把归一化配置（`{id,url,key}`，eval_dispatch / batch_eval 用的形态）喂进来会
静默 401、整批节点判「ping 失败」（探针第一版 6/6 台全灭，第二版才对）。已统一键名兼容。

**现场验证（真实集群 2026-09-19 21:19，`tmp/b6probe/run2.log`）**：x20-rebirth `it96`（sha `232158d9…`）
× 6 台 enabled 节点 —— ① 并行门 6 台 2.58s（gcs 超时，其余 5 台 evalSupport / stageJsonSupport /
bun 1.4.2 / codeHash 全过）；② `kind='eval'` 下发 **5/5 台 ok，0.20s**（self `purged`、其余 `kept`）；
③ 桶隔离实测 `eval=True / rollout=False`（self / mac / a95）——两条腿的桶确实分开；④ 以 `kind='eval'`
取一局：**HTTP 200，1.2s**，`wver=232158d9…` 对账一致。

**被否方案**：① 继续共用 'rollout'、只靠 409 自愈兜——把可预防的故障做成常态，还掩盖节点侧真实丢失；
② 用新 **mode** 而非 kind 区分——节点早已按 kind 分桶（v3.7），新增 mode 要动 `sampler-agent.ts`（在
codeHash SSOT 内 ⇒ 需 push + 集群重启），而 kind 是现成的**零升级**通道；③ 账本保持 wver 单键、只在 eval
侧「发前 forget 节点」——治不了另半边（rollout 腿复用 eval 的账）；④ 一次性工具链
（`tools/sim/eval-course-ckpt.ts` / `rl/batch_eval.py`，kind 走 `'rollout'`/`'none'`）**本轮不动**：
它是独立命名空间（iterId 自带 `evalcourse-`），且迭代节奏与训练循环无关。

**契约（测试钉住，新增 3 例 + 补 1 例断言）**：`tests/test_dist_weights.py::test_push_cache_is_keyed_by_kind`
（同 sha 的 eval 不得被 rollout 的账判成 reuse；缺省 kind 行为不变；`forget_weights_node` 两条腿一起清）／
`tests/test_eval_dispatch_resilience.py::test_eval_leg_uses_its_own_weights_kind`（POST 与请求同 kind）＋
`test_wver_409_reposts_weights_and_keeps_node` 补断言（重发也走 `EVAL_WEIGHTS_KIND`）＋
`test_eval_dispatch_kind_is_single_sourced`（源码守卫：该文件不得残留 `kind="rollout"` 字面量）／
`tests/test_dist_common_poll.py::test_ping_nodes_parallel_accepts_key_and_authkey`。
**其中 3 例在旧实现上实测变红**（`git show HEAD:` 换回旧实现跑同一套：`AttributeError: module
'rl.eval_dispatch' has no attribute 'EVAL_WEIGHTS_KIND'` ×2 + 键参数 `TypeError` ×1）。

**已知局限（未修，如实记）**：① 本改动只消除「两条腿互相驱逐」；**节点重启清空内存桶**后该 kind 仍会 409
（`weightsOf` 不回查磁盘）——那是节点侧根因，要动 `sampler-agent.ts`（codeHash SSOT ⇒ 需 push + 集群重启）；
② 节点上会多出一份 kind 目录（`weights-eval-*`，每 kind 4 份 ≈ 1.5MB/台）——换来两条腿的桶与日志都可分；
③ `rl/bc_eval.py` 仍是 `kind="rollout"`（BC 每 epoch 评估）——同类站点，但属另一条腿、且改动会连带其
请求侧，未并入本轮。

---

### §2026-09-19-node-weights-disk-fallback（2026-09-19，节点侧权重查找在内存桶未命中时回查磁盘——根治「agent 重启后对盘上已有的权重答 409」）

**触发**：用户 2026-09-19「让节点侧的权重查找在内存桶未命中时回查磁盘，根治重启后的 409」。承接
`§2026-09-19-eval-weights-kind` 的已知局限 ①（kind 拆桶只消除**两条腿互相驱逐**，重启仍会 409）。

**根因（已核实代码）**：`/v1/task` 只查内存桶（`sampler-agent.ts::weightsOf` → `weightsByKindSha`）。
agent 一重启，内存桶就空了，而权重文件**仍在 `WORK_DIR`**（boot 收敛只按 kind 删到最新 `KEEP=4` 份）
⇒ 节点对**盘上就有的**权重答 409「wver not cached here」，客户端只能靠 409 自愈重发兜（每节点每次重启
白传一份；在 A1 之前还会把该节点**当故障熔断整轮**）。

**决定（`tools/agent/sampler-agent.ts`）**：
- `weightsOf(kind, wver)`：内存命中 → 原路；未命中 → **磁盘回查** `readWeightsFile`，命中即**回填桶**
  （后续任务零额外开销）并按 `evictWeightBucket` 做同规驱逐。
- `readWeightsFile`：文件名只有 **16 hex 前缀**，**不足以判定内容** ⇒ 回查时**按字节重算 sha256 全量
  比对**，不符即视为未命中（坏/被截断的文件留给 sweep 收拾）。`iterId` 只在 POST 时记账（无消费方），
  回查来的置空。
- `weightFileBase(kind, sha)` 提为**单一来源**：POST 落盘、磁盘回查、retention 正则三处必须同名
  （名字一旦漂移，回查文件会被 boot/切换时的清扫当垃圾删掉）。`weightsKeyOk` 守门（kind 直接进文件名 ⇒
  防路径穿越；sha 必须全量 64 hex）。
- `/v1/weights/cached` 探针同样**磁盘感知**（`weightsCachedInBucket(...) || weightsOf(...) !== null`）：
  否则重启后探针答 false，客户端会重传一份**盘上已有**的权重。
- `latestWeightsOfKind(kind)`（intent/goal 评估的「最新桶」语义：`policy=intent-exec|goal` 的 409 前置检查 +
  runGame 的 `--intent-weights/--goal-weights`）：内存桶按插入序取最后一个；**桶空则回查磁盘**取 mtime 最新的
  一份（逐候选按字节重算 sha256，并要求 sha 前 16 hex 与文件名一致，改名/损坏的跳过；命中即回填桶）。
  这是另一类重启 409：intent-exec / goal 评估会整轮答「intent weights not cached」。

**代价与影响（必须知道）**：本文件**在 codeHash SSOT 内**（`tools/agent/codehash-files.txt`）⇒
codeHash `c78d48de…` → **`355f0738…`**，**必须 push + 节点升级/重启才生效**。升级窗口内未升级节点会被
门排除（可用节点数变少，属预期，不会污染数据）。`freeze:check` 不受影响（不触 God-AI/仿真签名）。

**被否方案**：① 回查只信文件名（16 hex 前缀）——前缀碰撞/坏文件会被放行，故障从「409」变成「局跑错
权重」，更糟；② boot 时把盘上全部权重文件预载进内存——要逐文件哈希、启动变慢，且把「最新 KEEP 份」
当权威；懒惰按需回查更小、语义相同；③ 客户端侧继续只靠 409 自愈——把可预防的故障做成常态，重启后每
节点每轮白传一份；④ 探针直接答 `true` 不校验内容——把「缓存」变成谎言。

**契约（测试钉住，`tests/dist-agent.test.ts` 新增 4 例）**：`readWeightsFile` 内容不符 / 前缀碰撞 /
缺文件 / 路径穿越与短 sha 一律未命中；`weightsOf` 未命中回查磁盘、命中后**文件消失也仍命中**（证明回填）；
`weightFileBase` 与 retention 正则 `WEIGHT_RE` 同域（5 个 kind 全覆盖）；`latestWeightsOfKind` 桶空时取盘上
mtime 最新的一份（不串 kind）、**最新那份是改名坏文件时跳过它取旧的**、回填后文件删掉仍返回。
旧实现上该文件 **1 fail + 1 error**（`Export named 'readWeightsFile' not found`）；变异体（删掉内容校验那行
`sha.slice(0, 16) !== c.prefix`）**只让「坏文件跳过」那条断言变红**——证明它守住的正是「文件名 16 hex 后缀
不可信」这个性质，而不是顺手写绿的。

**现场实测 A/B（`self` 节点，同一权重 `it96` sha `232158d9…`，同一探针 `tmp/restart-probe.py`；
证据 `tmp/restart-probe/{213330-before,213405-after}.json` + 两份 .log）**：
- **BEFORE（HEAD 代码，codeHash `c78d48de…`）**：① 重启前取局 seed900 → **200**；② `/v1/restart` accepted
  → 新进程 uptime=0s；③ **不重传** → 探针 `eval=False`、取局 seed901 → **409 `{"error":"wver not cached
  here"}`** ← 缺陷在真实节点上复现（而 `weights-eval-232158d94975e6a5.json` 379KB 全程躺在盘上）。
- **AFTER（本次代码，codeHash `355f0738…`）**：重启（等过 30s grace 窗口后发出，`waited=2.0s`）→ 新进程
  codeHash=`355f0738…`；**不重传** → 探针 `eval=True`（探针也已磁盘感知）、取局 seed951 → **200**
  （`outcome=gameover ticks=1483`）；节点日志出现
  `weights[eval] rehydrated 232158d94975… from disk (in-memory bucket was empty — agent restarted)`。
- **注意**：本次 A/B 跑在 `self`（本机 agent 直接执行工作区 TS）⇒ 验证的是**代码路径**；远端 5 台仍是
  `c78d48de…`，需 push + 升级后才具备同样行为（本地工作区 hash 已是 `355f0738…`，未升级节点会被门判 stale）。

**已知局限**：① 盘上只留最近 `KEEP=4` 份/kind（更早的被 sweep 删）⇒ 更早的 sha 仍 409（客户端重传，
这是正确行为）；② `latestWeightsOfKind`（intent/goal 评估的「最新桶」语义）仍只查内存——同类站点，未并入
②（**已并入本轮**）`latestWeightsOfKind` 同样磁盘回查，但它的「最新」只能按 **mtime 近似**（POST 命中 kept 不重写文件时 mtime 偏旧；与内存桶插入序语义等价、但有此边界），且「最新」是**跨客户端共享**的语义（别的训练流 POST 的 intent/goal 权重也会成为最新——与改动前一致）；③ 回查是请求路径上的同步 IO（`readFileSync` + sha256）——每个 `(kind, sha)` 每进程只付一次
（命中即回填），首次命中的那一局多几毫秒；④ 未做重启预载，故「重启后第一局」付出这次哈希。

---

### §2026-09-19-node-memory-restart-blindspots（2026-09-19，依赖节点内存态的三处重启盲区：取包丢失 404 判据 / `/v1/update` 假收敛 / 升级去重冷却窗）

**触发**：用户 2026-09-19「系统排查其它依赖节点内存态的地方（结果缓存、inflight 表）是否也有重启后行为
盲区」，随后裁定按 F1 → F3 → F2 修。

**审计（只读，判据 = 进程内可变状态重启后消失，而**是否有人据此做过判断**）**：
节点侧逐个过：`weightsByKindSha`（已覆盖，见 §node-weights-disk-fallback）/ `resultCache` / `inflight` /
`failedTasks`（**F1**）/ 计数器（`gamesDoneTotal/ByIter`、`cacheHits/Evicted`、`rejectedCount`、`activeWorkers`、
`lastError`）→ 只喂 `/v1/status`，面板的贡献度算的是**客户端写的** `dist-agent-meta.jsonl`，live 计数只显示、
无跨轮 delta 运算 ⇒ **无盲区**；`persistPool` 子进程 → 父进程退出即关掉它们的 stdin 管道 ⇒
`export-rl-rollout.ts` 的 `stdin.on('end') → process.exit(0)` 自灭，boot 的 `sweepWorkdir()` 另清孤儿
`game-*`/陈旧 pid；`gameSeq` → 目录名带 pid，重启不碰撞；`codeHashMemo/gitShortMemo` → **F2**；
`persistFailStreak`/`updating`/`restartPending` → 更宽松的闩，无消费方。
客户端跨轮、按节点为键的内存态：`_WEIGHTS_PUSHED`（已由 409 自愈 + 节点磁盘回查覆盖）/
`_RESTART_SEEN` + TS 落盘 memo（**F3**）/ `_ACTIVE`·`_ABORTED_TAGS`（进程内、按轮）/ `DEDUP_STREAK`（日志告警
计数）。`/v1/ping` **没有**权重字段 ⇒ 客户端从不信节点内存来判权重。

**F1（客户端，`dist_common` + A/C 层）——取包丢失 404 不许当节点故障**：
`resultCache/failedTasks/inflight` 都是节点进程内状态，agent 一重启即空；轮询 `/v1/result` 得到 404
（文案明写 `expired/purged/restart`）。旧实现在 A 层按确定性失败记 streak（3 条即 `circuit-broken for this
round`）并在 C 层按 attempt 打光即 `dropped`（**丢局**）——与 §node-fault-taxonomy 里 409 的错误同族、方向相反。
- 新增 `dist_common.TASK_LOST_MARKER` + `is_task_lost_error(e)`：判据 = **状态 404 ∧ 文案带标记**（裸 404 =
  路径写错等客户端 bug，必须继续响亮失败，绝不静默成无限回队）。与 `is_transient_error` **刻意分开**
  （处置不同：一个回队重跑、一个背压退避），但两层都按「不计节点故障、不耗 attempt 配额」处理。
- `forget_weights_node(nid, kind=None) -> int`：摘某节点账本（缺省全 kind；给 kind 只摘那条腿）。404 ⇒ 立刻
  摘该节点这条腿的 reuse 账本——重启同时也意味着它的权重桶可能空了（升级后的节点会靠磁盘回查零重传）。
- 可达性（精确）：async 只在 `abandon_event`（仅 fanout 竞速副本；副本失败有独立分支静默丢弃）或
  `DIST_TASK_ASYNC=1` 运维模式；面板 `smoke.ts` 把 404 当「继续轮询」⇒ 重启节点表现为 30s 朦胧超时（诊断噪声）。

**F2（节点侧，`tools/agent/sampler-agent.ts`）——`/v1/update` 不得让节点「报新代码、跑旧代码」**：
旧实现在 pull 成功后 `codeHashMemo.value = null` / `gitShortMemo.value = null`，而 `/v1/ping` 报的正是
`memoizedCodeHash()` ⇒ pull 过但**没重启**的节点会以**新 hash** 通过 codeHash 门
（`dist_common.check_code_hash`）、静默跑启动时那份代码——正是该门要拦的东西的反向漏网。
- 决定：memo 的**生命周期 = 本进程**，永不因 pull 失效；pull 只留一行响亮日志（新代码重启后生效）。抽
  `applyPullResult(r)`（导出，供单测钉住不变量），HTTP 分支只调它。要换 hash 只有 `/v1/restart`（可带 pullBranch）。
- 顺带：`/v1/restart` 分支在 `process.exit(0)` 前显式 `killPersistPool()`——池靠 stdin EOF 自灭，但**忙** worker
  要跑完当前那局才回到事件循环 ⇒ 旧代码会顶着旧代码继续算一段、把没人消费的 `game-*` 留在盘上。

**F3（`dist_common` + CLI/TS memo）——升级去重从「永久」改成「冷却窗」**：
旧 `_RESTART_SEEN` 命中即永久 dedup；pull 失败 / 环境不支持远端升级的节点带着**同一个** codeHash 回来 ⇒
该节点再也收不到升级指令（训练循环里直到训练机有新提交；TS 工具那条腿还把 memo 落盘
`tmp/node-upgrade-memo.json`，跨调用继续压制）。
- `RESTART_DEDUP_COOLDOWN_SEC = 600`（env `NN_RESTART_DEDUP_COOLDOWN_S` 可覆盖，0 = 关闭去重）；
  `_RESTART_SEEN[nid] = (pingHash, expectedHash, at)`；窗内 dedup、**窗过期 ⇒ 允许再发一次并重置时钟**
  （防连环杀 §2026-09-01 不破）。
- memo 的时刻要**进判据**：`seed_restart_state` 接受可选 `atSec`（缺省 = 现在 ⇒ 旧调用方语义逐字不变）；
  CLI spec 新增可选 `cooldown_sec` / `seen[].atSec`；TS 侧 `latestSeenEntries` 把 memo 值（ISO 时刻）换算成
  `atSec` 一并送过去（**判据仍只在 dist_common**，TS 只送时刻）。`memoAtSec` 先判纯数字再试 ISO——实测
  `Date.parse('1758300000')` 会给一个毫不相干的日期（2001-05-01），静默把新鲜 memo 变成「一小时前」。
- A 层 dedup 的 WARN 文案补上冷却窗秒数（运维知道它会自愈，不必手删 memo）。

**证据**：
- F1 两例行为测试在旧层上实测变红：A 层 `circuit-broken for this round`；C 层 `dropped=2 / games=0`；新实现
  A 层 `byNode={"a97":2}`、C 层 2/2 结算，且 `forget_weights_node` 被调用、`refreshed == []`（404 不走 409 路径）。
- F3 行为 A/B（`seed` 一条 1 小时前的 memo）：旧 `(False, 'dedup')` → 新 `(True, 'restart-requested')`。
- F2 守卫 A/B（HEAD vs 工作区）：`无 memo 置空 false→true`、`restart 前收池 false→true`、`导出 applyPullResult false→true`。
- 门禁：`nn-python-gate` ✓（ruff/mypy + pytest）· 根 `bun run check` ✓ · `bun run build` ✓ · `freeze:check` ✓。

**代价与影响**：`tools/agent/sampler-agent.ts` **在 codeHash SSOT 内** ⇒ F2 需 **push + 节点升级/重启**才生效；
F1/F3 是 `nn-training/**`（不在 SSOT）⇒ 无需 push。另：F2 改动期间工作区对 SSOT 变脏 ⇒ 从本仓跑工具会把远端
判 stale/拒发升级（既有护栏语义），提交后消失；两个 scan 用例顺手钉死「与工作区脏不脏无关」
（`dirty_hash_files → []`），否则改 `tools/agent/**` 就会把它们弄红（实测）。

**被否方案**：① F1 把 404 并入 `is_transient_error`——措辞与处置都不同，且会让「裸 404 客户端 bug」也变
静默回队；② F1 只豁免 streak 但照旧耗 attempt——C 层 attempt 打光即 `dropped`，等于照旧丢局；③ F2 让
`/v1/update` 顺带自重启——把「拉代码」这个可逆操作变成不可逆的杀进程，且 `test-dist-ops --pull` 的语义会变；
④ F3 把去重彻底删掉——2026-09-01 重启循环事故会回来；⑤ F3 只在 TS 侧按 memo 时间过滤——判据会一分为二。

**已知局限**：① F3 冷却窗是**时间**判据，窗内仍不重发（连续 3 轮 dedup 的 WARN 已在 A 层，TS 工具那条腿只有
日志行 + 面板 `versionOk=false`）；② F1 只覆盖 async 取包路径（同步路径没有 `/v1/result`，重启表现为连接被
重置 = 瞬断，已豁免）；③ F2 不做运行中进程的自我重启（要重启请 `/v1/restart`，这条刻意保留人工/协调器触发）。

---

---

### §2026-09-20-publish-lineage-filter（2026-09-20，用户报障：云机领到 ppo 任务后一直报错）

**症状（用户贴的云机日志）**：worker 领到 job 后逐份失败，刷屏三类错误：

```
job 2e2dabb810bf9299 REJECTED: D14 course_fp 不匹配：job=abbf8045102c… shard=e6c69a46167c… — skip (not retried)
job 991b9e70a3e1efed FAILED: RuntimeError: CUDA error: uncorrectable ECC error（硬件，非本次）
job 8d2ff3ab54e2b72b: 代码已变更：本进程加载 f4e2e4041c95… != job 要求 6224d4b41404… ⇒ 退出码 86 重启
```

**取证（`tmp/<课>/remote-jobs/<job>/payload.tar.xz` 逐 shard 读 manifest）**：`c6-chip` it16 那份
payload 里**混着两个血缘**——550 份里 21 份的 `course_fp` 是 `e6c69a46…`（课程文件编辑前的旧版本），
其余是 `abbf80451…`（= 当时的 job/课程指纹）。云端 `worker.py` 对**每一份** shard 判 D14
（`d14_corpus_match`），一份不合就整份 job 拒收 ⇒ hub 侧永远等不到结果、worker 反复领同一份死活。

**根因 = 发布端漏了血缘过滤**：`hub_client.iter_shard_dirs` 只按 `it{it}` 目录枚举（同名去重），
而**对账端**（`rl/resume._scan_shards` 的 `course_fp` 过滤）与**云端**（逐 shard 拒收）都在判血缘
——同一个目录三种口径。`it{it}` 是**累积**目录：课程文件被编辑过（改语料身份语义）/ 换过 runId 时，
里面会同时躺着新旧两代 shard，于是「打进 payload 的集合」⊃「云端会接受的集合」。

**定案**：

1. **判据只有一份**：`d14_corpus_match` 从 `remote/worker.py` 搬到 **`remote/protocol.py`**
   （`worker.py` 只做名字转发，既有 import/调用面/测试一字不改）。发布端与云端**同函数** ⇒
   「打包集 ≡ 云端接受集」成为结构事实，而不是两处约定的巧合。
2. **发布端过滤**：`iter_shard_dirs` / `iter_bc_shard_dirs` 新增 `course_fp=` / `corpus_fp=`
   （仅关键字参数，缺省空串 = 旧行为逐字节不变），逐 shard 用同一条判据挑；剔除的**响亮日志**
   （`D14: 剔除 N 个异血缘 shard …` + 每份一行），manifest 不可读的一律不收（宁可少一份，
   也不让云端整份退回）。PPO 与 BC 同规（BC 语料同样带 `course_fp`，同一条链同一处坑）。
3. **落位侧重算同参**：`verify_and_land` / `verify_and_land_bc` 用 manifest 自带的
   `course_fp`/`corpus_fp` 走**同一次过滤**再算 `data_fp`（发布集 == 重算集 == 云接受集；
   三者只要有一处口径不同，data_fp 校验就会把好结果判成「云训了别的语料」）。
4. **调用方顺序**：`loop_steps._remote_ppo` 里「算血缘（`course_fp`/`corpus_fp`）」**提到**
   「扫 shard」之前——扫描要用它们过滤（原来顺序相反，正是漏过滤的温床）。

**备选与否决**：

- **云端放宽（把拒收降级为「跳过该 shard 继续训」）**——否：那等于让「跨课程语料混训」静默发生
  （D14 熔断的全部意义就是不许），且每份 payload 里混进来源不明的语料，结果无法归因到任何课程。
- **云端多轮重试同一份 job**——否：payload 是**不可变**的（内容哈希进了 `payload_sha256`），
  重试只是把同一个必然失败再烧一遍租约；病根在发布端。
- **发布端过滤 + 云端仍逐 shard 判**——采纳：云端那道是**协议层不可绕**的守卫（防篡改/防旧 hub），
  发布端这道是**不让坏包产生**。两道都在，且共用同一个判据函数。
- **让 `it{it}` 目录物理隔离两代课程（每代一个目录）**——否：`it` 目录名是 `data_fp`/resume/
  账本的公共契约（改名 = 全线血缘破裂）；过滤是局部且可逆的修复。
- **顺带清掉盘上那份已在队的坏 job**——否：不删任何历史产物（非破坏纪律）；它属于未开课课程，
  新派发闸已不再把它发给任何 worker，重开该课时训练侧的下一次发布也会按 §381 作废更早迭代的 pending。

**gate**：nn python 全量（ruff + mypy + **1834 passed**）绿；新增回归
`test_iter_shard_dirs_drops_foreign_lineage` / `test_iter_bc_shard_dirs_drops_foreign_lineage` /
`test_d14_predicate_is_single_implementation`（首个用例同时断言「不过滤时会打进 3 份」= 修复前形状）。

---

### §2026-09-20-hub-dispatch-gate-and-stale-adoption（2026-09-20，同一故障的第二/第三层根因）

**为什么还有两层**：D14 那条修的是「坏包被造出来」；用户贴的日志里还有两个独立症状——
**worker 领到的是 11 门课的陈旧 job**（x20-floor 6.8h、x20-powered 15.2h、c6-chip 157.8h…），
且**盘上今天新加的课程闸对它无效**。

**取证**：

- `netstat` + `tmp/training-start/registry.json`：监听 :8787 的 hub = **PID 20860，今早 08:04 起的进程**
  （新闸是之后落的盘）。`tmp/*/training-enabled.txt` 全盘只有 `x20-steady` 一个（10:38 开课）。
  ⇒ 在跑的 hub 还把 20 门历史课留在自己的课程表里，继续把它们残留的 pending job 派给真 GPU worker。
- 真盘只读探针（新代码）：`discover()` 只登记 `x20-steady` 并派发它那份新 job，20 个历史课目录一个不碰。

**定案**：

1. **派发闸 `_HubQueue._serves_course`**（发现模式）：课程目录必须仍带 `training-enabled.txt`，
   否则 `claim_next` **当拍**跳过（告警每课一次，队列/账本一个字不动 = 停课仍是非破坏的）。
   发现时判过还要在派发时再判，因为课程表是**发现那一刻**建的，而 `remote-jobs/` 里的 pending job
   不会自己消失——没有这道闸，任何在旧表/旧代码里登记过的课程都能把陈旧 job 继续喂给云机。
2. **控制台接管旧码进程**（`server.ts::reconcileWatch` + `core/reload.ts::runningStaleCode` +
   账本新字段 `RegistryEntry.startedAt`）：`watch()` 以**当下**指纹为基线，而 watch 表住内存——
   **控制台一重启就丢**，重启后的对账把在跑进程重新基线化成「就绪」，「这个进程早于最后一次改码」
   从此永久不可见。现在启动对账先判 `runningStaleCode(spec, startedAt)`（哨兵 mtime > 启动时刻
   +2s 容差；无记录 = 旧条目 ⇒ 按旧码处理），是旧码就**先 restart 再 watch**；每个 spawn 点
   （selfNode / hubServer / trainer / localWorker / 监督重启 / 冒烟）都写 `startedAt`。
   **不含 cloudflared**：第三方二进制跑的不是我们的代码，而重启它的代价是**隧道 URL 变化**
   （云机手上那个 URL 立刻作废、在跑 job 无法回传）——零收益高代价，单独豁免。

**备选与否决**：

- **只在控制台 UI 提示「hub 可能是旧码，请手动重启」**——否：这正是今天发生的形态（「hub-server
  已在运行」= 幂等成功），而「旧码」是不可见状态；把不可见交给操作员记着 = 下次照旧。
- **hub 侧自行热重载代码**——否：python 进程热换模块语义不清（已 import 的模块/闭包/线程），
  而仓库既有的契约就是**控制台监督重启**（reload.ts），这里只是补上「重启控制台后仍成立」。
- **判据用已存在但未接线的 `lastMonitorChange()`（全局触点）**——否：它是**全局**时刻（任何组件
  spawn 都会刷新），跨组件互相掩盖；`startedAt` 是每进程一份的精确值，且它本身就是证据。
- **把「无 startedAt」也算作未知而不重启**——否：那正好放过今天这件事（旧条目就是问题所在）；
  代价是**一次**接管重启，之后账本自带启动时刻，稳态零误报。
- **重启 cloudflared 也纳入旧码接管**——否（见上：URL 作废）。
- **顺手停掉/清掉那 11 份陈旧 pending job**——否：非破坏纪律（停课/历史产物一个不删）；闸挡住
  派发就够了，重启该课时 `cancel_stale_jobs`（§381）自会作废更早迭代的 pending。

**gate**：dashboard **760 pass / 0 fail**（新增 `supervisor-stale-code.test.ts` 8 例：判据边界 +
接线门禁——含「每个 spawn 点都写 startedAt」「cloudflared 必须豁免」「restart 早于 watch」）；
`tsc --noEmit` + oxlint 0 warning；根 `bun run check` 绿；nn python 全量
（ruff + mypy + 1834 passed，含新 `test_stopped_course_stops_being_dispatched`）绿。

---

---

### §2026-09-20-body-transfer-stall-guard（2026-09-20，用户报障：云机 claim 第二个 job 后几分钟无动静、无日志）

**背景**：云机领到 `it2` 的 PPO job（`a4b2e1e2d438c28a`）后卡在 payload 下载——
日志从 `claim ... downloading payload` 到 5 分钟后的 socket 超时**一行都没有**（用户原话：
「几分钟一直没有动静，也没有 log 打出」）。取证：`cloudflared` 日志同期每 5 分钟一条
`lookup region1.v2.argotunnel.com: i/o timeout`（DNS 劣化窗口，隧道只有 1 条 ha-connection），
**小 POST（心跳/轮询）照常、大 body 卡死**；hub 侧 `/payload` 的访问行属于高频静默规则 ⇒
两端日志同时沉默。这不是「网络抖动」一种病，是**两侧都没有停滞判据**：

1. worker `download_payload` 只有一把 `timeout=300` 的**整读**：停滞时静默等满 5 分钟，
   而 socket 超时抛出的 `TimeoutError()` **没有正文**（`_get_with_retry` 只把 `repr(e)` 写进
   日志）⇒ 连「为什么失败」都写不出来。下载过程本身**零进度输出**（分不清「在下」与「死了」）。
2. hub `wfile.write()` **没有发送超时**：对端半开（隧道/代理侧掉了、本机 TCP 还挂着）时
   永久阻塞，那个 handler 线程永久卡在写里——客户端永远拿不到 payload，而 hub 日志一个字没有。

**决定**：传输层加**有界 + 有名字 + 有进度**三件套（数字都在源码注释里，改它们要连本文一起改）：

| 位置 | 判据 | 行为 |
|---|---|---|
| `remote/worker.py::_read_body` | 空闲 45s（`BODY_IDLE_TIMEOUT_SEC`）无新字节 | 抛**有正文**的 `TimeoutError`：`body 停滞：45s 内没有新字节（已收 N bytes / 共 M）` |
| 同上 | 总预算 300s（`BODY_TOTAL_TIMEOUT_SEC`） | 治「永远在滴水」（每个读都有字节、但总也读不完） |
| `download_payload/code/ts_code/blob` | 进度回调（≥5s 一行） | `job X: payload 下载中 3.20 MB / 4.85 MB (66%) 用时 12s（270 KB/s）` |
| `remote/hub_server.py::_bytes` | 发送超时 60s（`SEND_TIMEOUT_SEC`）+ 256KB 分片 | 停滞即断并打印 `已发 N/M bytes`；≥256KB 的 body 完成时打一行（可对账速率） |

**关键不变量**：缺省路径（不给 `idle_timeout`/`progress`）**逐字节不变**——`_request` 只在
下载路径上改走分块读，`poll_job`/`post_result` 等小请求行为不动。

**备选与否决**：

- **只把 `timeout` 由 300 调小（如 60）**——否：仍然只会在**整读**上炸一次，中间零输出；
  而且分不清「链路慢但活」与「链路死了」（前者本来该让它跑完）。
- **只在 worker 侧重试/加长租约**——否：治不了「不知道发生了什么」；重试前的 5 分钟静默仍在。
- **把停滞做成静默重试（不打日志）**——否：这次事故的**全部**损失就是「静默」；
  响亮一行（带字节数与原因）比安静重试值钱得多。
- **hub 侧改成流式读盘（避免 4.8MB 进内存）**——本次不做：现有行为是 `read_bytes()`，
  改动面（`record_payload_sent` 的字节口径、Content-Length 来源）大于收益；
  分片**写**已经解掉「永久阻塞」这个真问题。
- **顺手把 `/payload` 从高频静默规则里拿掉**——否：多 worker 下会刷爆 hub 日志；
  改成「大 body 完成/停滞各一行」（有信息量、无噪声）。

**同窗口的 hub 停服 = 人工操作（用户 2026-09-20 确认是手动关的）**：这也解释了取证里那个
「疑点」——`hub-server.out` 停在 11:34:19、账本条目 `hubServers['']` 于 11:34:36 被清空、
无"意外退出"标记：`stopComponent` 杀进程后本来就是 `clearAnyComponent`（条目没了 ⇒
exit-watchdog 没有可标记的对象），控制台由此显示 `stopped`。**不是崩溃**，本次改动与它无关。

**但它留下一条真教训**：组件级决策（谁在何时停/重启了什么）目前**只打控制台 stdout**、不落
文件——所以从盘上的证据（组件日志 + 账本）**无法**区分「人工停的」与「自己死的」，
排查会往「谁杀的」方向空转（本次就绕了这一圈）。下次同类「集群突然静默」的报障，**先问
一句是不是手动停的**，再翻日志；要让机器自己回答，得把组件级决策也落盘。

**gate**：nn python 全量 **1842 passed**（ruff + mypy 绿，含新 `tests/test_body_transfer_guard.py`
8 例：停滞有名/进度可查/预算生效/停滞即响亮重试，以及真 TCP socket 的
「读端不读 ⇒ hub ≤发送超时断开并打印已发字节数」）。

---

---

### §2026-09-20-wire-slow-reroll（2026-09-20，用户报障：云机传输长时间卡在 6–9 KB/s；评审 plan/minimize-payload.plan.md 时的实测）

**背景**：上一节（body 停滞判据）让「卡住」变得可见之后，现场读数暴露出**真正的病**：

| 时刻 | 端点 | 字节 | 耗时 | 速率 |
|------|------|------|------|------|
| 10:41:35→10:43:04 | payload | 2,012,020 | 88.6 s | 22.7 KB/s |
| 11:25:01→11:25:06 | payload | 4,848,536 | 4.9 s | 990 KB/s |
| 12:49:28→12:49:32 | payload | 3,454,884 | 3.9 s | 885 KB/s |
| 12:49:58→12:51:30 | **code** | 1,433,893 | 106 s | **13.5 KB/s** |
| 12:51:30→12:51:42 | **blob（opt_init）** | —（无进度行 ⇒ <5 s） | — | ≥120 KB/s |
| 12:59:29→13:04:51 | payload | 3,486,200 | 321.8 s | 首连接 **6–9 KB/s** 烧完 300 s 预算；**重试换连接后 354 KB/s** |

12:49 那两行是**同机、同 hub、相邻 3 秒**的请求；12:59 那次「重试」本质就是一次**意外重抽**，
只是由总预算在 300 s 后触发（321.8 s 里约 280 s 白等）。`urllib` 每个请求新建连接
（`net_http.urlopen` → `OpenerDirector.open`，无 keep-alive 池）⇒ 速率由**这条连接**的
路由质量决定，双峰 44×（13 KB/s ↔ 990 KB/s）。

**决定**：把「换一条连接」做成**一等机制**（而不是继续优化平均字节数）——
**先重抽，再瘦身**（`nn-training/remote/worker.py`；阈值只此一处实现）：

| 判据 | 值 | 理由 |
|---|---|---|
| 坏签阈值 | `max(WIRE_MIN_RATE=80KB/s, 本会话最好速率/4)`（`_reroll_decision` 纯函数） | 相对判据：云机、本机、不同出口各自定标；只在「明显偏离」时动手 |
| 触发条件 | 实测速率 < 阈值 **且** 按此速率**预计剩余 > 20 s** | 快跑完了不值得折腾 |
| 判据时机 | **只在首块判一次**（`probed` 一次性） | 每次重抽浪费 ≤ 一块（256 KB）⇒ **不可能退化成「再整份重传一遍」** |
| 重抽次数 | ≤ `WIRE_REROLL_MAX=3`，**不退避**（重抽的价值就在快） | |
| 最后一次尝试 | **永不可重抽** | 6 KB/s 的坏签真实存在；重抽是赌，不能把赌注全压在赌上 ⇒ 慢链路不会变成「永远下不完」 |
| 适用范围 | **只给幂等 GET**（payload/code/ts_code/blob）；POST result 永不重抽 | 产物上行不可重放，只走既有退避重试 |
| 传输账 | 每 job 一行 `wire payload=… code=cache-hit … reroll=1(wasted 0.25MB) 合计=…`（`_wire_flush`） | 逐条进度行看不出全局；**零字节命中也要进账**（否则读数失真）；push 模式在 `worker_server` 的 finally flush；未 flush 的 job 封顶 4 个 |

**备选与否决**：

- **先做瘦身（`omit`/`Have` 协商，plan §4.1/M3）**——本次不做：实测慢腿是**连接**，
  瘦身把 1.37 MB 变 0.5 MB 在 13 KB/s 下仍是 37 s，而重抽把它变成 ~1 s（重抽成本 ≈ 建连）。
  协商机制仍留在 plan 里（M2/M3），但优先级降到「重抽之后」。
- **把「出口限速」当环境事实（plan §1.4 原话）**——否：同机 3 秒后的 blob GET 有 ≥120 KB/s，
  限速解释不了 13.5 KB/s；那是单连接抽签。
- **发现问题就整份重下（无限重抽）**——否：坏签下会永远下不完；上限 3 次 + 末次禁止重抽。
- **重抽也走指数退避**——否：重抽的价值就是**快**；退避只用于「链路真的坏了」的瞬态失败。
- **把「同 sha 传两遍」当噪声**（本次同时查明的 code.zip 重复下载）——不当噪声：
  引导 `notebook_boot` 已解到 `/tmp/worker-code`，而 worker 按 `work_dir/code_cache/<sha>` 判命中
  （缺省 `/tmp/remote-worker`）⇒ 冷缓存重下**同一份 1,433,893 字节**。交接方案见
  `plan/minimize-payload.plan.md` M0（零协议改动，本次**未实现**，只记在 plan 里）。

**顺带修掉的 e2e flake（残留根）**：§97 的 `weights_push_cache_reset()` 只治「同进程顺序跑」，
治不了**在途线程**——前一个用例的收尾 push 可能在下一个用例 reset **之后**才记账，而全仓库
哑权重内容都是 `{"stub": true}` ⇒ `wver`（文件指纹）相同 ⇒ 被判 `kept / skip POST` ⇒
`test_it_stream_smoke` 的 I3 断言假红（xdist 下随机）。修法：哑权重内容带**每用例唯一**标记
（`{"stub": true, "case": <tmp 名>}`）——只有**键不同**才与线程时序无关；reset 保留作双保险。**gate**：nn python 全量 **1859 passed**（ruff + mypy 绿；新增 `tests/test_wire_reroll.py` 17 例：判据/相对阈值/上限/末次不重抽/浪费有界/账行形状/产物不重抽）。
签入前与 §108（continuous 报告真源）合并复跑：**1863 passed in 34.65s**（ruff/mypy 绿）。

**收尾（同日）**：本条的覆盖范围只到 worker 侧四段 GET；引导期两段（code.zip / task-pack）与账的
聚合后续补齐 —— 见 §2026-09-20-boot-wire-guard。

---

### §2026-09-20-boot-wire-guard（2026-09-20，收尾 §2026-09-20-wire-slow-reroll：引导期两个大 body 也在同一次连接抽签里，却跑在 code.zip 之前）

**缺口**：上一节的护栏住在 `remote/worker.py` —— 而引导期有**两个大 body 幂等 GET 跑在
code.zip 之前**（那时 `remote.worker` 不存在，因为 code.zip 正是被下载的那个东西）：hub `/code`
（code.zip；实测最坏 1.43 MB / 106 s / **13.5 KB/s**）与离线盘的 `task-pack`（自己文档里就写着
「几 MB～几十 MB」）。两处都是 `opener.open(req, timeout=…).read()` 整读：**没有进度行、没有停滞
判据、没有墙钟预算、没有重抽** ⇒ 坏签下 106 s 一行日志都不多；几十 MB 的包在 10 KB/s 下就是
85 分钟零输出（这正是 §104「静默」事故的形状）。

**决定**：护栏做成**引导期**的一份，住 `remote/tailscale_boot.py::fetch_guarded`：
分块读 256 KB + 进度行 + 停滞 45 s（`BOOT_IDLE_TIMEOUT_SEC`）+ 调用方给的墙钟预算 +
**首块判一次**的低速重抽（`max(80KB/s, 本会话最好速率/4)`、预计剩余 >20 s、≤3 次、**末次必硬传**）；
`HTTPError` 原样上抛（401/404 是**确定性的答案**，不是传输抖动，由调用方按状态码处置）。
接线：`notebook_boot._pull`（code.zip，预算 300 s）与 `offline_boot.fetch_task_pack`（预算 = 既有
`PACK_TIMEOUT`）。成功即落一行与 worker 同族形状的账：
`wire: code.zip 1.37MB/106.0s(13KB/s) attempts=1 rerolls=0`（一个解析器吃两类行）。

**为什么住 `tailscale_boot`（而不是新开 `remote/boot_wire.py`、也不是 `net_http.py`）**：三个
notebook（训练 cell / 连接体检 / 离线盘）的 GitHub raw 拉取清单**都已含** `tailscale_boot.py` ——
与既有 `resolve_hub_url` 同一条「单源一份，两边不会漂」的理由；新开文件要同时改三份 notebook 的
清单，而 notebook 是**要重发**的交付物；`net_http.py` 在 code.zip 里，引导期够不到。

**与 worker 侧的关系 = 孪生实现**（阈值同值、「首块判一次」同纪律、末次不重抽同保证）。不能抽
共享件：引导期能 import 的只有它自己与 `tailscale_boot`。**改一边要同步另一边**；两侧单测各自钉住
同一组数字（`tests/test_wire_reroll.py` · `tests/test_boot_wire_guard.py`）。

**同一批的另两个口子**：

- **`wire` 账只有原文、没有聚合** ⇒ `nn-training/tools/wire_report.py`（三类行：worker 每 job
  摘要 / 引导期摘要 / hub 发送完成行 → 逐段 p50/p90 **秒** · p50/worst **速率** · bad% · reroll · 命中）。
  ★ **速率只报 p50 + worst**：坏签是**低尾**，报「速率 p90」量到的是好签那一端（会把 321.8 s 的
  坏签读成「一切正常」）；秒数相反——坏签就是高尾，p90 才有意义。这是 plan §7.2「报 p50/p90，
  不报均值」的可复算落点。
- **ts_code 缓存命中不进账**（code/blob 都记了，只漏这一处）⇒ `_ensure_ts_code` 命中即
  `_wire_hit(jid, "ts_code")`。

**备选与否决**：

- **把护栏也复制进 cell 的内联回退**——否：那是「GitHub raw 拉不到时的最小 pull 路径」；往 cell 里
  复刻 80 行与「cell 只留 CFG / 凭据 / 保活」的既有口径正面冲突。代价写明在 plan §4.0.1，真疼再议。
- **引导期只加进度行、不做重抽**——否：进度行解决「看不见」，但 106 s 的坏签仍要付；重抽成本
  ≈建连、收益≈100 s，两件事的性价比不在一个量级。
- **判据单源化（`worker.py` 反过来 import `tailscale_boot`）**——否：方向反了（worker 是产品、
  `tailscale_boot` 是引导脚手架），且把两个已发布、各有测试的实现绑成一个发布单元。
- **给 `tailscale_boot` 改名（它现在不止 tailscale）**——否：改名要动三份 notebook 的 raw 清单与
  缓存文件名（`/tmp/battle-boot/tailscale_boot.py`），收益只是名字好听。
- **把 `tools/remote_wire_scan.py` 当作已有的复核手段**——否（事实纠正）：**该文件不在仓里**
  （`git ls-files` 只有 `test_wire_cf_tunnel.py` / `test_wire_reroll.py`）⇒ plan §1.2 的体积表
  无法就地复现；已在 plan §6/§7.3/§9 标注，替代口径 = worker 的 `result=` 字节数 + 协议层用例。

**gate**：nn python 全量绿（ruff + mypy **303 files** + pytest `tests/` & `e2e/`，门禁 **37s**）；
新增 `tests/test_boot_wire_guard.py` 17 例 + `tests/test_wire_report.py` 11 例（用例数 与 `ts_code` 命中
那条改动一并在改后全量中确认）。`tools/wire_report.py` 对现场原文的实跑输出已核对：
`job:payload` p50 3.9 s / p90 **321.8 s**、worst 11 KB/s、bad% 50%、reroll 1。

---

---

### §2026-09-22-offline-bundle-autoready（2026-09-22，离线课开课自动出包 + 随时可导 + 行内操作 + 状态列修正）

承接 §2026-09-22-course-error-isolation-loud 的离线链路收口（用户三条指令叠加）：
①「离线课程开课后自动生成任务包，kaggle 配好接离线任务后经 hub 下载执行」；②「开课后
也不应锁定任务包不给导出，应支持随时导出」；③「首页任务包区域去掉、导出/导入放课程行
操作列；iter/本轮/在等什么/队列 列未正确显示离线课程状态，要修正」「总览离线课不应是
「it1 推进中」」。

**落地**：
- **随时导出（python）**：`run_rl.py` 的 `--export-bundle` 分支**不取 per-course 单实例锁**
  （导出=只读快照：打 zip 不推进账本/不落新权重）；`loop_steps._remote_ppo` 的导出分支同步
  **跳过 commit-journal attach**（否则并行导出会给调度器在飞集写一条永远等不到的幽灵 job，
  与 `register=export_path is None` 同一门）。共享 trainer 服务中也能导出/重导。
- **开课自动导出（dashboard）**：`openCourse` 离线开课成功即 `launchTaskBundleExport`
  （异步起进程、回执 note 带状态与日志路径；`BCITY_NO_AUTO_TASK_BUNDLE` 测试逃生阀）。
  `exportGuard` 删除「run_rl 锁被占」拒绝（保留 weights 存在 + busy 互斥）。
- **hub 分发**：`GET /offline/task-pack?course=` 已然在（`hub_server.py`），zip 落盘即原子
  （`bundle.export_bundle` tmp+replace）——Kaggle/Colab 离线 worker 探到 hub 即自动拉取执行。
- **UI 改版**：首页「任务包」面板下线（删 `TaskBundlePanel.tsx`）；导出/下载/导入下沉到
  `CourseMatrix` 每行「操作」列（新 `BundleRowActions.tsx`，仅离线课行渲染；导出=postAction
  `exportTaskBundle` + `/api/taskBundleInfo` 轮询自动亮「下载」；导入=`uploadDeliverZip`）。
- **离线课状态列修正（任务包+回传维度；执行模型不变，本地照常派发）**：
  `matrix` 离线行 iter 列读 `offlineLastIter`（云机回传最新 it，非本地「下一轮」指针）、
  本轮列=「云机 itN」、在等什么=「云机运行中 · 已回传 N 轮」、队列列为「只收回传」；
  总览 pill 离线课一律「回传中」非「推进中」（`coursePills.offline`，确定性事实暂停/收官/
  中止优先级不动）；`ParallelOverviewView` 新字段 `offline: string[]`（来自 hub queue）。

**否决的备选**：
- 直扩调度器 `except BaseException`——否（§2026-09-22-course-error-isolation-loud 已拒）。
- 导出继续锁互斥、让操作员先停课再导——否：用户要求随时可导；锁语义从「同课两写者护栏」
  收窄为「训练写者独占」，导出按只读快照放行。

---

### §2026-09-22-goalnn-transfer-scheduling-pull（2026-09-22，传输∥PPO 的优先级调度面：P1/P1.5/P2 落地）

- **背景**：多课程并行下网络传输耗时 ≈ rollout/PPO，串行 `claim → 下载 → PPO → 回传 → 轮询`
  把 GPU 饿死在传输上。plan `transfer-scheduling.plan.md`（同日两轮评审 R1/R2）给出解法的
  完整语义；本条目只记**已落地部分**里「后来者极容易重新做错」的几条决定与它们的否决面。
- **落地范围（"绿"的判据看这些名字，不看行号）**：`remote/worker.py::acquire_job`
  （= `peek_jobs` → `request_priority` → `claim_job` 取活三件套）、`peek_jobs` / `request_priority`
  / `claim_job` / `job_started` / `job_ready` / `abandon_job` / `job_status` / `start_cancel_watcher`；
  `remote/hub_server.py` 的 `GET /jobs/peek` 与 `POST /jobs/{priority|claim|start|ready|abandon}`、
  `_JobStore.claim_outcome/_claim_locked`、`_claimed/_computing/_ready/_epoch/_backup_authorized`、
  `scheduling_facts/priority_for/start_job/set_ready/abandon_job`；`remote/protocol.py` 的
  `job_priority`（§1.4 优先级表纯函数）与 `JobCancelledError`。
  门禁 = `nn-training/tests/test_priority_schedule.py`（17 例：纯函数五分支、两把时钟、备份租约、
  abandon 零 reclaim、highest 唯一性闸、peek 无副作用、403 丢弃、取活三件套、取消环）。
- **否决与否决理由（这几条是本条目的存在理由）**：
  ① **`mode="backup"` 不是「pop 掉原租约」**（评审第二轮 R2-3 推翻第一版）：pop 掉会让原 worker
    硬死之后**无租约可过期 ⇒ 毒包熔断失明**，job 立刻回池 ⇒ 第三/第四份可自由领取，且 push 腿
    「hub 持租约防同一份活两处跑」的自保失效。改用「逐 job 的 `_backup_authorized` 标记 + 
    `result_token_ok` 对该 job 放行」——只放行**回传**，租约与 `_claimed` 一字不动。
    `tests/test_priority_schedule.py::test_backup_claim_does_not_poison_or_reclaim` 就是那个失明的探针。
  ② **删 race 判定 ≠ 删机制**：`claim` 的无租约分支（原 `race=True`）换名为 `mode="backup"` 保留
    ——它是「多卡空转防护」的唯一实现面；删掉就等于「空闲的卡只能空转」。
  ③ **`highest` 的唯一性闸在 claim 的同一临界区**（不是「问询即授予」）：N 个 worker 同拍问询必然
    都看到 highest（问询是纯读），唯一的闸门是 `_claim_locked` 里的 `_claimed` + `expected_epoch`；
    一个 job 只能有一个「承诺在跑」的人。漏掉它 ⇒ 行为退化成「无排序的同 job 硬抢」= race 换名字。
  ④ **`epoch` 只在 claim 一处校验**（`/jobs/{id}/start` 不校，R2-C4）：两处各自校验 = 第二个事实源。
    且 epoch 不匹配**不是错误**（是「有人比我快」的正常信号）：回 `demoted`、不得转成
    `ProtocolError`、不得触发 `report_job_failure`。
  ⑤ **`ready` 判在 `computing` 之前**（同 job 上 ready 就是 computing 的后一阶段）：读成中档会丢掉
    「算完待回传 = 只降优先级、别人尽管开备份」这个 §1.3.2 信号。
  ⑥ **掉队阈值只认 `computing_at`**（`/jobs/{id}/start` 打点 = PPO 真启动），**不认 claim**：拿 claim
    起算会把「下载慢」误判成「算得慢」⇒ 多开备份把本来就慢的链路压得更死（两把时钟在观测行上也分得出来）。
  ⑦ **取消只认 `landed`**（结果已落盘）且必须是**独立异常** `JobCancelledError`：落进 `ProtocolError` ⇒
    `report_job_failure`（把合法放弃报成确定性失败 ⇒ 训练停腿）、落进 `RetryableError` ⇒ 把别人已赢下的
    活 release 回池。取消点 = `ppo_update` 的 **epoch 边界**（复用 `on_epoch_done`，实测延迟写
    `cancel_latency_s`；**不**在 chunk 内层加回调——用户拍板不值得为 15s→1s 动内层结构）。
  ⑧ **`abandon` = release 租约 + 清可见性 + 零 reclaim**：只清可见性不清租约 ⇒ job 在
    `CLAIM_TTL_SEC=300` 内被挡在池外、过期后 `_reclaims+1` ⇒ 三度达阈被冻成毒包（合法放弃读成「认领后零回传」）。
  ⑨ **`peek` 无副作用**（`claim` 才是认领）：不动 `_cursor`（只读轮转序），「先看一眼」不得移走别人的轮次。
  ⑩ **登记表 `note_worker` 必须换源保留**（R2-2）：它是 `active_worker_count()`（避让链唯一输入）的
    唯一写入点，原住在 `/jobs/next` 里。新面（peek/priority）接手，且 `hub_scope` 照旧上报——否则
    「超时回落队首改为推送其它 worker」（2026-09-18 用户口径）会**静默消失**，而纯函数单测测不出「调用点为 0」。
- **未做（明确入口，别当成已交付）**：**P0**（控制面旁路 + bulk 单通道的传输 QoS）、**P0.5**（先量
  `T_in/T_out/T_ppo` 与 GPU 空转占比的基线）、**P3**（退役 `/jobs/next` + race 判定 + `poll_job`，
  含 **push 腿 R1-7** 与 **控制台同批改造 R2-1**）。本批**只换 pull 线的取活面**：`/jobs/next`、
  `poll_job`、race 判定原样保留继续可用（`tests/test_race_broadcast.py` 仍绿），故
  2026-09-17 的 race broadcast 条目**尚未**被 supersede。P3 落地时按 plan §8【R2-10c】写 supersede。
- **违反后果**：把 backup 改回 pop 租约 ⇒ 熔断失明 + 多份自由领取（无报错，只有冻结阈值悄悄失效）；
  把 highest 闸去掉 ⇒ 多卡同抢一份（看着像「机群更快」，实际白烧 GPU）；把 `ready` 判在 computing 之后
  ⇒ 备份保险消失；让取消落进 ProtocolError ⇒ 训练停腿且现场看着像「worker 确定性失败」。

---

---

### 2026-09-22-goalnn-bulk-single-channel — bulk 单通道 / 让路预算 / 软持有预取（plan/transfer-scheduling P0+P2）

**背景**：§2026-09-22-goalnn-transfer-scheduling-pull 只换了 pull 线的取活面；本条落地「传输那半场」
（`docs/nn/remote-transport.md` §29）：三条流分层（P0 控制面 / P1 关键 bulk / P2 预取），让 GPU 不再被传输饿死。

**决定（后来者极容易做错，故入册）**

① **bulk 单通道是硬不变量**（`remote/bulk_sched.py::BulkScheduler.slot`）：`post_result` 与任何下载
**不得同时在途**。看起来「两条一起传更快」，实测是链路双侧静默（§104 现场）＋控制环读不回；且 POST 大
body **没有安全 Range**，并发只会互相拖慢。**唯一的槽位入口**，不要在别处再加一个「临时并行」旁路。

② **让路预算有界，且上界要同时看两个**（`BULK_YIELD_BUDGET_SEC=5s`）：worker 侧
`BODY_IDLE_TIMEOUT_SEC=45s`（分块读的空闲判停）与 hub 侧 `SEND_TIMEOUT_SEC=60s`（分片写超时）。
只按 45s 写会不会更安全？——不，**撞上 60s 那一侧同样被判停滞**（写超时 ⇒ hub 断流 ⇒ worker 看到
的是「瞬时失败」）。常量化 + 用例钉住两个不等式。

③ **P1 不可抢断、P2 必须可弃**：P1（`post_result`、开算前关键下载）一旦开传就等它传完——抢断 = 整份白传，
浪费的是最贵的产物；P2（预取）相反，被高优/控制面挤到就 `BulkPreemptError` **丢半截**（payload 幂等 +
sha 校验，重下即可）。把两者合成一条「可中断的队列」是最省事也最错的写法。

④ **预取只走 `download_payload` 一条路**（`remote/prefetch.py` 的填充器只调它）：omit 协商
（minimize-payload §4.1）落在那一个函数里，预取落地后自动继承；在预取路径另写「整包 GET」= 把已瘦身的
部分又吹回去，而且**不会报错**（只是白付带宽）。同理：预取**不复制字节**，落内容寻址 `blob_cache`。

⑤ **预取失败不是失败**：`BulkPreemptError` / 404 / 瞬时 / sha 不符 / 预算不足 —— 一律就地丢弃、记一行，
**绝不**转 `ProtocolError` / `report_job_failure`。预取是提前量，不是任务；把网络抖动报成节点故障会让
「谁坏了」的判断彻底失真（一次误报一个 worker 被摘牌，比少赚一次命中贵得多）。

⑥ **`prefetch/` 必须在 `prune_job_dirs` 豁免名单里**：`blob_cache` 漏过一次（2026-09-17），现象是
「缓存永远未命中」而看起来像协议没生效。新增任何内容寻址/暂存目录，**同一步**加进名单。

⑦ **阶段账（P0.5）先于收益结论**：`T_in / T_out / T_ppo / other` 是判 P2 盈亏的唯一依据。
机制可以落地，但「预取值不值」的结论**必须**等实机阶段表（`tools/wire_report.py`）——本批只落了仪器，
预取可用 `--prefetch-depth 0` 关掉。写方/读方格式一致性有用例钉住（防阶段表**静默变空**被读成「没数据」）。

**违反后果**

- 去掉单通道（或加第二条旁路）⇒ 传输互相拖慢 + 控制环分钟级（现场像「hub 卡死」）。
- 让路预算写大（比如 30s）⇒ 自己触发 45s 空闲判停/60s 写超时 ⇒ 每次让路换来一次整份重传。
- 把 P2 的抢占搬给 P1 ⇒ 结果回传被中途打断，白传整份产物。
- 预取绕开 `download_payload` ⇒ 白付带宽且**无任何报错**。
- 预取失败上报 ⇒ 一次网络抖动摘掉一个健康 worker（毒包熔断被误触发）。
- `prefetch/` 漏进 prune 名单 ⇒ 预取永远不命中，现象与「协议没生效」不可区分。

**未随本批落地（明确入口）**：P0.5 的**实机数字**（需真机会话）与 **P3 竞速退役**（`/jobs/next` /
race 判定 / `poll_job` / `--race` / `/admin/race` + 控制台同批改造 + push 腿 R1-7）。P3 落地时按
plan §8【R2-10c】写 supersede §2026-09-17，并**保留** `claim(mode="backup")` 机制（删判定不删机制）。

---

---

### §2026-09-22-goalnn-race-retired-priority-only（2026-09-22，落地 `plan/transfer-scheduling.plan.md` P3：竞速广播**判定**退役，取活只剩「peek → priority → claim」一条路；push 腿同表 + 备份副本）

**supersede §2026-09-17-goalnn-race-broadcast**（以及它在 `dashboard/src/**` 的落地物）。该条目的
**机制**被本条目取代：不再有「最新 job 广播给每个 worker、先回先胜」的判定，也不再需要
`race_decision` / `race_mode` / `--race` / `/admin/race` / `hub_scope`。**保留**的是它当年解决掉的
真实问题（多 worker 无排序地抢同一批 job）的替代解，以及两样与判定无关的机制：

- **`claim(mode="backup")` 机制保留**（R1-1）——删的是**判定**，不是**机制**。备份副本仍无租约、
  不进 `_stale_holders`、不产 `_reclaims`、回传经 `_backup_authorized` 放行（403 单列丢弃）。
- **worker 登记表保留**（R2-2）——`WORKER_SEEN_WINDOW_SEC`（原 `RACE_WORKER_WINDOW_SEC`）窗口与
  `note_worker` 仍在，只是换源到新面（peek/priority）；避让链 `active_worker_count()` →
  `may_avoid_stale_holder()` 是 2026-09-18 用户口径的唯一实现，**输入不能断**（有端到端用例，
  因为纯函数用例测不出「调用点为 0」）。

**为什么必须 supersede 而不是并存**：竞速判定与「highest 唯一性闸 + 1 主 + N 备份上限」是
**互斥**的两套语义——前者靠「无排序地重复烧卡」换延迟，后者靠「有排序地授权重复」换延迟。
并存时两张调度器会同时生效：`highest` 的 epoch 闸会被判成「别处在做 ⇒ 全体 highest ⇒ 多卡同抢」，
1 主 + N 备份的上限也随之失效。所以这是一次**协议面一次性切换**（R1-9）：不给旧 worker 兼容层，
`/jobs/next` 已删 ⇒ 旧 worker 拿到 404，**响亮**而不是静默错跑。回退粒度 = 按 PR revert；
**revert 时必须保留 `mode="backup"`**，否则等于连备份能力一起回退。

**落地物**：`remote/protocol.py`（删 `RACE_MODE_*` / `race_decision` / `parse_hub_scope` /
`HUB_SCOPE_HEADER`；`RACE_WORKER_WINDOW_SEC` → `WORKER_SEEN_WINDOW_SEC`）· `remote/hub_server.py`
（删 `race_mode/race_active/set_race_mode/race_state/clear_workers`、`/admin/race`、`--race`、
`/jobs/next` 与 `claim_next`/`claim(race=)` 的 race 分支）· `remote/worker.py`（`poll_job` 整函数删除，
取活只剩 `acquire_job`）· `remote/push_dispatch.py`（同一张优先级表 + 1 主 + N 备份 +
`hub.start_job` 打 `computing_at` + `_cancel_others` landed 取消帧）· `dashboard/src/**`（去
`--race` / `RaceMode` / `raceActive`；注释里也不留退役面名）· `tests/helpers/hub_poll.py`（新面拼回
旧同形，10 个登录点机械替换）· `tests/helpers/push_worker.py` + `tests/conftest.py`（共享假 worker：
跨测试文件 import 夹具会撞 ruff `F811`）。

**回归（常驻闸）**：`tests/test_priority_schedule.py::test_race_judgment_has_no_production_path`
（判定零命中）· `tests/test_jobs_next_retired.py`（真 HTTP 404 + 生产零命中）·
`tests/test_scope_unrelated_race.py`（R1-10 负向：eval 侧长尾竞速**没有**被误删）·
`tests/test_push_priority_dispatch.py`（6）· `tests/test_pause_budget.py`（4）。

**违反后果**

- 把 `/jobs/next` 或 `race_decision` 当「兼容别名」加回来 ⇒ 两张调度器同时生效，highest 唯一性
  与备份上限一起失效（现象：同 job 被多台机重复烧，日志上像「队列异常」）。
- 顺手删备份副本（把它当竞速遗留）⇒ 掉队救援整条消失，重新掉进「多 worker 无排序硬抢」。
- 删 `HUB_SCOPE_HEADER` 时连**登记表**一起删 ⇒ 避让链输入恒 0，静默失效（独苗也不肯自领或
  反复避让同一台死机）——这正是 R2-2 那条端到端用例存在的理由。

---

### §2026-09-22-goalnn-async-result-upload（2026-09-22，plan/transfer-scheduling **P2.5**：结果回传异步化 —— 把 `out` 从关键路径上摘下来；阶段账新增 `overlap=` 字段）

**决定**：`post_result` 不再同步阻塞主循环。结果**入队即返回**（`remote/result_upload.py::ResultUploader`，
有界队列 + 专用上传线程），主循环立刻去领下一份 job；`--result-upload {async,sync}` 缺省 `async`。
配套三条硬契约：① **退出前必 drain**（`close()` = drain + join，包住整段主循环的 `try/finally`，
覆盖 `break` / `--once` / 热替换 `SystemExit(86)` / 任何异常）；② **绝不丢**（队列满、入队超时、
上传器已收尾 ⇒ 一律**退回同步**发出）；③ **失败响亮**（重试耗尽/确定性拒绝落带 jid 的 `★` 行 +
收尾汇总计数）。记账侧：`out` 的秒数**照报**，阶段行只多一个 `overlap=` 说明它被重叠掉了 ——
判据从「`in+out+ppo+other ≈ wall`」变成「`in+ppo+other ≈ wall`（关键路径）」。

**背景（用户 2026-09-22 给的两组事实，组成完整推导）**：
- **双课程单 worker 是最典型场景**，A 课 rollout 与 B 课 PPO **交错填空** ⇒ **算力已经满了**，
  再快只能把传输从关键路径上摘掉（挤算力没有余地）。
- 现场账 `rollout/in/ppo/out = 45/15/50/25 s`。云机侧只感知 `in/ppo/out`（rollout 在**本机**跑，
  `rollout_src=local` 缺省，账在本机 `rollout_sec`）。**`out` 25s 是 40s 传输里更大的那一半，
  而改造前它全程压在关键路径上**——预取（P2）只治 `in`，`out` 当时无人管。
- 与 P2 是**正交**收益：命中让 `in`→0，`out` 不动；两者叠加才是「传输全部离开关键路径」。

**为什么是异步上传而不是别的**：下一份 job 的字节**多半已在本地**（P2 软持有预取），上传只吃
**链路**、不吃 CPU/GPU ⇒ 两者资源不相交，天然可叠。反过来「让 hub 接受更晚的结果」或
「少回传内容」都不解决**关键路径占用**（前者改变语义，后者是 minimize-payload 的另一条线）。

**否决项**：
- 只把 `post_result` 挪到别的线程但**无人收尾** ⇒ 进程退出时队列里的结果随 daemon 蒸发，
  现象是训练侧干等租约过期才「发现」——**静默**，正是本仓最贵的一类事故（3.5 小时静默那族）。
- async 下**照旧在 finally 收账** ⇒ `out` 还没记进来就 flush，阶段账把回传读成 `0s`
  （最该看见的一段凭空消失），且落定回调会再收一次 = 每 job 两行账。
- 把 `out` 从阶段账里**删掉**（只报 overlap）⇒ 读成「回传不花钱」（链路照样跑满），
  比错报更危险。
- 队列**无界**（永不背压）⇒ 结果是大 dict（权重），链路长期卡住时无界涨内存；
  正确方向是背压 + 超时退同步。
- `--once` 不等落定就判成败 ⇒ 回传失败被静默当成成功（退出码 0），而 smoke 只判 returncode。

**违反后果**：任何「让结果回传重新同步阻塞」的写法都会把 `out` 那 25s 重新压回关键路径
（双课程交错下 = 每份 job 白等 25s，吞吐掉约两成）；任何「退出不收尾」的写法会丢结果且**不报警**；
任何「revert 时只删 async 但留着 `could`-style 半状态」的写法会让记账与关键路径口径分叉。
回退粒度 = `--result-upload sync`（逐字回旧行为，无需改代码）或按 commit revert。

**落地物**：`remote/result_upload.py`（新：`ResultUploader` / `UploadTask` / `Outcome` /
`RESULT_UPLOAD_MODES`）· `remote/worker.py`（`worker_loop` 接线 + `_result_settled` 落定回调 +
job 级 `uploaded` 标志 + 主循环 `try/finally` 收尾 + `--result-upload`；`_wire_flush(wall_end=)` +
`overlap=` 字段；`WIRE_MAX_JOBS` 4→8）· `tools/wire_report.py`（可选 `overlap=` 组 +
`out_overlap_sec` + 渲染行；旧日志缺省当 0）· `tests/test_async_result_upload.py`（新，18 例）·
`tests/test_wire_report.py`（async/sync 对照 + 旧日志兼容）。设计稿 `plan/transfer-scheduling.plan.md`
§1.1 / §4 P2.5 / §9.5；进度 `docs/nn/remote-transport.md` §31。

---

### §2026-09-24-offline-it0-baseline

**背景**：离线腿（`rollout_src:'run'`）没有 it0 读数——云机 `offline_eval.due()` 对 `it<1` 恒 False、
本机主循环不在场上；而控制台的配对基线必须有 it0 才不随 run 起点漂移（跨腿配对失去共同锚，见
`docs/nn/remote-transport.md` §42）。

**决定**：把这一格交给**控制台在离线开课那一刻**补评（`launchEvalA(course, '', 0, {baseline:true})`），
评课程活动权重 `out`（= 任务包 `init_weights` 同一份字节 = 段起点 W(0)），`iter` 恒 0，同 wver 已落账
即早退；**云机侧一律不动**。判据按 wver（不是按「开课次数」）——换起点权重才重评。

**被否决**：
- **云机侧评 it0**（`due()` 放行 `it==0` + boot 期用包内 init_weights 评一轮）：字节同一性由构造保证，
  但要在云侧另接一套派发/账本/产物目录约定，而 hub 侧 `dispatch_eval_round` 本来就是两腿共用那条路
  （`iter=0` 与 A-eval 的账本隔离早已为共存设计）。收益（消灭秒级时序竞态）不抵接线面。
- **控制台加「补跑 it0」按钮**：UI 改动 + `dashboard/src/server/build.ts` 三件套，而恢复路径已有等价物
  ——「停课 → 重新开课」会再派一次（同 wver 命中即秒退，代价零）。
- **包内 `init_weights` 自动对账**：要在导出/评估之间搬一份可写快照，成本不抵收益；改为在
  `[evalA] … wver=…` 日志行里留核对入口。

**违反后果**：① 缺 it0 ⇒ 配对基线退化成「首条 eval 轮」（跨腿不可比，`iters.ts:1034`）；
② baseline 的 `iter` 被写成非 0 ⇒ 控制台把它当成那一轮的读数（启动期已响亮拒守护）；
③ 幂等判据若改回 `baseline_summary_landed(课程目录, …)` ⇒ 恒 False，每次开课白评一轮；
④ `--ckpt ''` 落到 python 手里是 `.`（存在）⇒ 拿目录算指纹（TS 侧空就不传 flag，已守护）。

**落地物**：`nn-training/rl/eval_a_once.py`（`--baseline` / ckpt 缺省取 `ns.out` / `iter` 必 0 /
同 wver 早退 / 透传 `baseline`）· `dashboard/src/server/eval-a-run.ts`（`evalAArgs` 纯函数 + opts）·
`dashboard/src/server/actions/course-lifecycle.ts`（`shouldAutoBaseline` + 开课后 best-effort 派发 + 回执 note）·
`nn-training/tests/test_eval_a_once.py`（+4 例）· `dashboard/tests/eval-a-baseline.test.ts`（新）·
`dashboard/tests/course-lifecycle.test.ts`（逃生阀 + 不派发断言）。plan：`plan/offline-it0-baseline-eval.plan.md`。

---

<!-- OLD-NUMBER-MAP: 自动生成，勿手改 -->

## 附：本文件旧编号对照（拆分前 `docs/nn.progress.md` 的 § 号）

旧 §18→§1 · 旧 §19→§2 · 旧 §20→§3 · 旧 §24→§4 · 旧 §39→§5 · 旧 §56→§6 · 旧 §58→§7 · 旧 §59→§8 · 旧 §60→§9 · 旧 §61→§10 · 旧 §62→§11 · 旧 §63→§12 · 旧 §70→§13 · 旧 §69→§14 · 旧 §71→§15 · 旧 §72→§16 · 旧 §74→§17 · 旧 §75→§18 · 旧 §92→§19 · 旧 §94→§20 · 旧 §104→§21 · 旧 §105→§22 · 旧 §109→§23 · 旧 §119→§24 · 旧 §129→§25 · 旧 §130→§26 · 旧 §137→§27 · 旧 §127→§28 · 旧 §128→§29 · 旧 §129→§30 · 旧 §130→§31
