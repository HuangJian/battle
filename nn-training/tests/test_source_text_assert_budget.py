"""文本断言的**计数护栏**（2026-10-09，`plan/nn-training-test-debt-cleanup.plan.md` §3-S3-2）。

口径 = `tests/helpers/text_asserts.py` 模块头（全仓**唯一**的计数定义）。这里只做一件事：
**只许降，不许升**。

为什么需要它（本 plan 的原始痛点）：`assert "…" in <源码/输出文本>` 这类判据挂在**措辞**上——
重构必炸、改标题字也炸，而且它绿不代表行为对。T5 清掉一批后，若没有这条护栏，下一次重构
又会顺手撒一批回去。

基线（= 本文件写死的那两个数；历史水位只留在这里）：

| 时点 | 形态口径 | 生产源码文本口径 |
|---|---|---|
| 2026-10-09 清理前（HEAD `e7cbc0a8`） | 1488 条 / 228 文件 | 1084 条 / 106 文件 |
| 2026-10-09 T1/T3/T2 删除后（写死在这里） | **1435 条** | **1082 条** |
| 2026-10-10 新增 `tests/remote/test_rollout_node_notebook.py`（抬 SOURCE 一格） | 1435 条 | **1091 条** |
| 2026-10-11 aistudio 传输硬化新增 5 个用例文件（抬 FORM 一格，+6） | **1441 条** | 1091 条 |
| 2026-10-11 探针上界（`plan/rollout-topup-fastpath.plan.md` §3.1；抬 FORM 一格，+1） | **1442 条** | 1091 条 |
| 2026-10-12 预取解包（`plan/prefetch-stage-unpack.plan.md`；抬 FORM 一格，+3） | **1445 条** | 1091 条 |
| 2026-10-14 离线 A/B 腿（`plan/offline-code-authority-and-width.plan.md`；抬 FORM 一格，+8，含接手时既有的 +4） | **1457 条** | 1092 条 |
| 2026-10-14 离线本地盘 A 腿（`plan/offline-local-disk-first.plan.md`；净 +3 = 新增 5 条读数行 − 删 2 条旧读数行） | **1460 条** | 1092 条 |
| 2026-10-14 离线本地盘 B1 腿（同 plan §3.2 两个根 + §3.5 恢复腿；抬 FORM 一格，+2） | **1462 条** | 1092 条 |
| 2026-10-14 离线本地盘 C1/B4 腿（同 plan §3.4 备份抽屉 + 段末交付面判据；抬 FORM 一格，+4） | **1466 条** | 1092 条 |

降 53 条全是本次删掉的用例（T1 的 6 条账本门端到端、T3 的 2 条 legacy 重复点）带走的。

**2026-10-10 抬 SOURCE 一格的账**（plan/rollout-node-auto-register v2；形态口径**没抬**，当时 = 1435）：
采样节点 notebook 改薄 cell 后要一条接线守卫（同 `test_offline_notebook.py` 那族）：它检查的对象**就是**
cell 文本与 `remote/rollout_node.py` 的源码 —— 「cell 能不能 `compile()`」「CFG 是否覆盖模块读的每个键」
「凭据读取是否先于 `ensure()`」「公网下载是否包 `_public_net()`」「`finally` 是否 unregister + 清子进程」。
这些契约没有可调用的运行时入口（notebook 不在 pytest 里执行），只能读源码。抬格前已先把能改的都改成
非文本判据（字段成员、状态码、`.startswith`、AST 结构、`set ==` 等：形态口径 20 条 → 0），
剩下的 9 条全是「只读源码才能判」的那一类。

要抬基线：**不**直接改数字——先在 commit message 里写清「为什么这条判据只能看文本」，
再改；抬一格是一次有意的欠债，不是顺手。降了就同步改小（历史高水位只留在这里）。

**2026-10-11 抬 FORM 一格的账**（plan/aistudio-transfer-hardening；形态 1435 → 1441 = +6，SOURCE 未动）：
这 6 条全在**新增的 5 个用例文件**里（`test_yield_adaptive.py` 3 · `test_prefetch_adaptive.py` 2 ·
`test_control_pool.py` 1），且全是**对日志行**的子串断言 —— 断言的就是**交付物本身**：
方案要求 A1/A2「必须是**可观测地关**（打一行带原因 + 回退旋钮）」，B3 要求「日志必须写明重试次数」
（旧文案只写「失败」会把「6s×2」读成「一次没接上」）。这些日志行是给**操作员**的接口，
被测物没有可调用的结构化出口来替代它们（行为面已在同文件里另有用例钉住：「自停后零 peek」
「降到 never 语义 + 退出还原」）。⚠ 这**不**意味着告警（分位/白传）的判据可以继续挂在文本上：
本轮 32 条阈值/读数判据全部是**常量相等 / 集合相等 / 布尔返回**形态（口径 A 零命中）。

**2026-10-11 抬 FORM 一格的账**（`plan/rollout-topup-fastpath.plan.md` §3.1 探针上界；+1，SOURCE 未动）：
新增两个用例的四条判据已全部改成**行为断言**（墙钟上界、候选盘上有没有 `nn-rollout-*`、产出在不在
`job_dir`），只剩**一条**：探针超界时那句「WARN 探针超界 … + 轮报 `probe_probe` 读数」。它只能看文本
——这一行的**交付物就是日志本身**（操作员的现场读数：这一轮到底探没探、是不是在等盘；`LogBundle` 只往日志出，没有结构化出口 ⇒ 无法用返回值/状态表达）。同族先例 = 上面 aistudio 那 6 条。

**2026-10-18 抬 FORM 一格的账**（`plan/eval-gap-honesty.plan.md`；形态 1480 → 1483 = +3，SOURCE 未动）：
这三条全在本机评估腿的新用例里，断的都是**操作员现场读数行**（该 plan §9 的 T4/T5/T7 把「响亮一行」
写成验收标准）：

  · `[eval] local: 连续 N 次瞬时失败 — 本轮停派`（熔断唯一的可观测出口：停派后的行为另有用例钉
    ——「两条本机线程都不再取任务」，但「有没有响、为什么停」只有日志面能判）；
  · `— 改派给在线节点（本机第 N 次）`（追溯面：缺口不是丢了，是换了台机器，见 plan §10 风险行）；
  · `机器级故障收工 —— <node> 独占 n/lost（errno=…）`（`close_reason=machine-fault` 的人读面；
    结构化出口在 summary 里，但「哪台机器、什么 errno」只在日志行上）。

同一批新用例里能改成行为判据的全改了（errno/首行/阈值/队列收敛/落盘集合/镜像文件的**计数**均走
等值或属性判据），所以这一格是 3 而不是 20（首轮实测 +20 已压到 +3）。

**2026-11-17 抬 FORM 一格的账**（探活失败归因：hub 的 422 读面 + 云机转述；形态 1493 → **1498**
= +5，SOURCE 未动 = 1121）：这 5 条全在**操作员现场读数行**上 —— 真机上「HTTP 400 / 连接超时 /
authKey 不符」被压成一句「无应答，或 authKey 不符」，现场只能另起 curl 猜（`6e4e42cf` 的故障）：

  · `tests/hub/test_node_register.py` **4**：`节点探活失败` 那一行必须同时带 URL、端点、原因与耗时
    （`ms）——回 422，不写盘`）。同一用例的机读面已按结构判据钉住（`probe["url"/"path"/"reason"/"ms"]`、
    `last[2] == 422`、`error.startswith("节点 ping 不通")`），文本只剩「这行有没有把归因写出来」。
  · `tests/remote/test_push_register.py` **1**：老 hub 的 422 没有 `probe` 键 ⇒ 云机日志必须照旧转述
    原始 detail（不静默、不编原因）。`hint == ""`、`status == 422` 与 `ok is False` 已另按结构判据钉住。

⚠ 同一批里能改成结构判据的都改了（节点探测的返回/落盘/422 状态全部读字段与返回值）——这一格是
**+5** 而不是整段测试的 +20；留下的都是「日志行本身就是交付物」的 A 类（模块头的分类）。

**2026-11-18 抬 FORM 一格的账**（relay 连不上 hub 的现场读面；形态 1498 → **1502** = +4，
SOURCE 未动 = 1121）：DSW 现场 6 个线程各卡 35s，日志只有 `relay 轮询失败（URLError）` ——
`URLError` 一个词同时意味着 DNS 失败/被拒/TLS 失败/代理吊死/地址黑洞，而地址与耗时一个字没打，
排查只能靠猜（正是这次事故本身）。`remote/rollout_relay.probe_hub` 因此多了一条预检行：

  · `tests/remote/test_rollout_relay.py` **2**：① 预检判词必须点出该改哪个开关（`hub_url`）；
    ② 放弃时那两行必须带 `hub=<host:port>` 且**不再**把人指向 cloudflared（relay 模式没有那个进程）。
  · `tests/remote/test_rollout_node.py` **1**：relay 模式的启动横幅同理（空 URL 时不许说
    「看 cloudflared 日志尾部」）。第三条在 `test_rollout_relay.py` 里已并进同一 assert。

⚠ 这一格能压到 +4 的原因和上一格一样：预检新增了**结构化出口** `facts = {host, port, dns, routes}`，
DNS/逐路由结论（`ok` / `http-401` / `fail`）全部走 `facts` 断言，`_err_text` 的三种死因走
`.startswith(...)` 而非子串；文本只剩「这行有没有把现场信息写出来」。

**2026-10-14 抬 SOURCE 一格的账**（`plan/wire-diet.plan.md` §12 切片 2；+1，FORM 未动）：
R3/M4 把 opt 回传改成 **delta 容器**，而「哪个形态位 + 哪几个调用**同处产物段**」是这一刀的
**接口面**：`run_training_core` 的产物段必须同时调 `opt_fmt_of`（形态位唯一口径）· `pack_opt_delta`
（回传字节）· `pack_opt_tar`（**本机 f32 全量**：身份映射与 blob_cache 的输入）· `record_pending`。
漏掉后两者里的任一个都是「M0 的本机权威副本断链 / 回传没瘦」，而这两条都**没有可调用的运行时
入口**（跑 `run_training_core` 要真 torch + 真课程）—— 行为面已能测的部分全部在行为面测了：
容器往返/决定性/拒收在 `tests/remote/test_opt_delta.py`（含 `opt_fmt_of` 三档读法）、hub 侧重建在
`tests/remote/test_opt_delta_land.py`（真 torch：重建误差界 + 权威 tar 成员表 + 缺 base 抛）
—— 两边零文本判据。剩下的这一条 = 单文件内的 `_named_calls(...)` 集合判据（与措辞无关，
不按文本子串），按本条同族先例记在这里。

**2026-10-12 抬 FORM 一格的账**（`plan/prefetch-stage-unpack.plan.md`；+3，SOURCE 未动）：
本 plan 新增三类**降级路径**（`store(unpack=True)` 解包失败、解包产物超单份上界、staged 暂存不在位
⇒ 回退关键下载），plan §9「不静默」要求这三条各打一行 —— 那一行**就是**交付物（操作员唯一的现场
读数：这一轮为什么没吃到预取）。行为面在同族用例里另有用例钉住（`staged is False`、`payload.zip`
回到盘上、`take()` 仍能命中、照旧走关键下载），但「有没有响」只有日志面能判。
⚠ 同一批新用例里**能改的都改了**：4 条字典成员判据（`"staged_dir" in got` 族）已换成
`set(got) == {…}` / `get(...) is None` / 真值判据（零文本命中），所以这一格是 3 而不是 7。

**2026-10-14 抬 FORM 一格的账**（`plan/offline-code-authority-and-width.plan.md` v2.1；形态 1445 → 1457，
其中**本次 +8**、**接手时既有 +4**，SOURCE 未动）：净 +8 = 新增下列读数行 11 条 − 同时删掉的 3 条旧读数行
（`本机产物`/`修复`/`不重解`，语义已被行为判据吸收）。这 11 条全是计划里写死的「必须留一行读数」——
它们**就是**操作员的现场判据：

  · `remote/offline_boot` 的**段契约改写行**（DoD#3：一次换码要报「哪个键、旧 sha12 → 新 sha12」）；
  · **哨兵拒候选行**（DoD#4：混血树在训练前被拦下，且带**现场那条错误原文** `ImportError: cannot import
    name 'METRICS_NAME' …`）—— 行为面已钉「换了下一个候选」，但「为什么换」只有日志面能判；
  · **本机兜底 WARN**（P2：「本机缓存只作最后兜底且**响亮**」——选中哪份由字节判，「有没有响」只有日志面）；
  · **环境缺件放行 WARN**（P0-3 的另一半：「放行」由选中的字节判，「凭什么放行」只有日志面）；
  · **hub 打包端的脏树行**（DoD#5：`dirty=true` + 文件清单 + 计数）· **哨兵分类行**（`code` 类 WARN 必须
    点名 `code`：B4 的「导出那一刻就发现」）；
  · `tests/worker/test_iter_topup.py` 的 3 条（`宽度退化` WARN + 「槽位档不许出现退化 WARN」+ 批行的
    `槽位=4`）：A4/P3「退化必须可见」的交付物**就是**那几行字（批行读数在 plan DoD#1 里被点名）。
    ⚠ 2026-10-27：该文件随「逐局流式采集」退役（`worker/iter_topup.py` 删除，批这个形态不存在了）
    ⇒ 这 3 条文本判据**一并消失**（计数只降不升，无需改基线）。

⚠ 能改的都改成了行为判据：这一批新用例里 `(ok, kind)` 元组相等 · `{…} <= set(…)` 子集 · 字节相等 ·
manifest 字段相等 · `time` 上界（带 `timing-ok`）——权威序 / 惰性取 hub / 换码事务 / 哨兵分类全在行为面钉住。

**2026-10-14 抬 FORM 一格的账**（`plan/offline-local-disk-first.plan.md` v2 §3.1/§3.3 A1+A3+A4；形态 1457 → **1460**
= 新增 5 − 删 2，SOURCE 未动）：A1/A3 删掉的是**活守卫**（网络盘测速探针 + 它的 5s 上界），围着它建的两条旧读数行
（`probe_probe=超界回退｜…`、`云机评估热路径换到本地盘：…`）随用例一起删（−2）。新增的 5 条**全是** §3.6 点名要留的
现场读数行 —— 它们**就是**操作员的判据（139s 现场最大的遗憾正是「人只能拼时间戳」，而 `LogBundle` 只往日志出、
没有结构化出口）：

  · rollout 轮报 `落点=本地:<path>｜建布局 x.xxs`（取代 `probe_probe`）；
  · 它的**反探针**（DoD 逐字：「任何一轮的日志里不得再出现网络盘基准探针这一档耗时」——这条判据本身就是文本判据）；
  · `NN_ROLLOUT_SCRATCH=off ⇒ 强制回退档`（A2 的逃生门确认行；行为面已钉「不建 `off` 目录 / 不碰候选」）；
  · eval 腿 `[eval-cloud] 落点=…｜建布局 x.xxs`（此前该腿**零读数**，§3.6 点名要补）；
  · eval 腿超界降档的 `WARN … 超界或失败（≤…s/步）`（DoD-4 的「响亮一行」；行为面已钉 `layout=None` + 回退档三件读数）。

⚠ 同批新用例里能改的都改成了行为判据：落点解析在 job 目录上**零写入**（哨兵文件 mtime 对账）· `off` 档不建目录不碰候选 ·
超界/降档的**墙钟上界** + 回退档的 `weights_path`/`work_dir`/`ts_root` 三件读数逐字相等 · `submit()` 注入慢 build 仍 < 0.1s ·
`test_round_ctx.py` 的两条计数判据换成 `{"pick": 1, …}` 纯数据断言（该文件文本面保持 0）。
⚠ plan §3.7 的迁移表只算了「删探针读数行」这一笔（预期 −1），**漏算了 §3.6 要求的新增读数**——这里如实并账（+5 −2），
plan §11 同步记了这条偏差。

**2026-10-14 抬 FORM 一格的账**（`plan/offline-local-disk-first.plan.md` §3.2 B1 腿（两个根 + 恢复腿）；形态 1460 → **1462**
= +2，SOURCE 未动）：两条都是**落点/恢复的现场读数**，且都是计划点名要留的那类：

  · 本地产物根不可用 ⇒ 成因行里要能读到**为什么**（拍板-2「回落网络盘 + 响亮一行」；用例的判据 =
    那一行里带 `NotADirectoryError` 与失败路径 —— 行为面已另钉「落到 `net_artifacts_dir`」）；
  · 恢复腿生效时的那一行（§3.5 的「本地空 ⇒ 从网络盘备份恢复 itN」；行为面已钉 `local_artifacts()`
    立刻可用、`it-N/weights.json` 在位、暂存目录不留、`--bundle` 零次）。

⚠ 同批能改的都改了：`--bundle` 在场/不在场改成 `argv.count("--bundle") == 0`、`--backup-root` 的在场
改由 `argv.index()` 隐含（找不到就 ValueError）—— 所以这一格是 **+2** 而不是 +4。

**2026-10-14 抬 FORM 一格的账**（`plan/offline-local-disk-first.plan.md` §3.4 C1/B4 腿（备份抽屉 + 段末交付面）；
形态 1462 → **1466** = +4，SOURCE 未动）：四条全是计划**点名要留的响亮行**（「拍板-2/3 不静默」的交付物
就是那一行字，`LogBundle`/`deliver` 侧没有结构化出口）：

  · 抽屉建不出来 / 判据算不出时的 `备份跳过：…`（DoD-8 的「只 WARN、不抛」——行为面已钉「返回 0 且不抛」）；
  · 段末**无 hub** 的「产物只在本地根、仍落一次交付物」（DoD-12 的一半；行为面已钉 zip 真的写出来）；
  · 段末**有备份抽屉** ⇒ 仍打交付档那一行（行为面已钉 zip 字节相相等）；
  · 段末**全成功跳过**交付档那一行（带本地根路径；行为面已钉 `deliver-<课>.zip` **不在**下载目录里）。

**2026-10-14 抬 FORM 一格的账**（`plan/offline-local-disk-first.plan.md` §3.1/§8 B2 腿（持久面容量判据）；
形态 1466 → **1469** = +3，SOURCE 未动）：三条全是拍板-2 / DoD-14 **点名要留的响亮行**（“回落网络盘 + 响亮一行”
与“段中盘满必须响亮（WARN 带路径）”的交付物就是那行字；`LogBundle` 只往日志出、没有结构化出口）：

  · 容量不够时 `resolve_local_root` 的**成因串**（带 `free=100MB` / `need=600MB` 两个读数与预算口径）；
  · 端到端那条 `本地产物根不可用（…容量不够…）⇒ 本段产物回落网络盘` 的 WARN；
  · 段中盘满的 `WARN: 产物落盘失败（…）—产物根=<path>｜…换根续跑`（DoD-14 的“不谎报成功”由
    `ProtocolError` + 不补传承担）。

⚠ 同批能改的都改成了行为判据：“段长进了判据”改成「同一盘上短段落本地 / 长段回落网络根」·
回落落点改成 `== net_artifacts_dir` · 错误类型改成 `pytest.raises(ProtocolError)` + `__cause__` 是 `OSError`
· 不补传改成 `sent == []`（当时 `_checkpoint` 排在 checkpoint 之后）—— 所以这一格是 **+3** 而不是 +6。

**2026-10-15 抬 FORM 一格的账**（`plan/offline-open-ended-iters-and-loud-export.plan.md` §3.3 J3（导出成败
的权威落点）；形态 1469 → **1470** = +1，SOURCE 未动）：这一条断言的是 hub **递给云机的那行字**
（`trigger_note`，409 正文里的 `# 已经在导包，但**上次导出失败**：<原因>`）——而那行字**就是**这条腿的
交付物（用户 2026-10-14 报障第二条：「导出离线包失败应响亮报错」；旧读面里每一方都在演「成功」，
失败原因只留在日志末行、没有任何一方读它）。为什么不改成行为断言：同一用例里已经钉了
`give_up` / `export_failed` / 「只问过一次控制台」/ 缓存的 `failed_reason`（全是非文本判据），
但那些都盖不住「**缓存对了而读面没带上**」这种回归——而它正是要修的那个缺陷。

⚠ 同批能改的都改了：python 侧那条「异常原样上抛」从 `"…" in str(e)` 改成 `str(e) == msg`（全等，
不计口径 A）；hub 侧另两条（`failed` 分支的原因、旧失败不得入读面）分别换成
`auto_handoff_failure(...) == (…, …)` 与 `export_failed is False` 两个纯数据断言 ——
所以这一格是 **+1** 而不是 +4。

**同一格里的另一笔（不是本次改动的账）**：抬格前实测 HEAD `1cca709c` **已是 1449**（≠ 1445）——
10-12 基线之后新加的用例文件（`tests/trainer/test_loop_serve_switches.py` 3 · `tests/remote/test_payload_slim.py` 1 ·
`tests/trainer/test_ppo_watchdog.py` 1，另一处 −1）没跟着抬基线。本次一并显性化（1445 + 4 + 8 = 1457），
不替它们改判据（那属于那些用例自己的账）。

**2026-10-17 抬 FORM 一格的账**（`plan/offline-backfeed-resilience.plan.md` E/F/G/H 四片：§4 自愈重试
+ 租约重领 + 判据化删除、§5 notebook 一键包、§6.2 报错表、§7 停课导入；形态 1470 → **1480** = **+10**，
SOURCE 未动）：这四片的交付物大量是**给操作员的一行读数**（「这一包为什么没落位 / 为什么没删抽屉 /
为什么打不了包」），而它们**没有**可调用的结构化出口；同族先例见上面各笔（aistudio 日志行、探针超界、
三个降级行、段契约改写行）。逐笔分摊（抬格前先把**能改的全改了**：见下段，−6）：

  · `tests/remote/test_offline_boot.py` **3**：`package_pending` 判据表第 4 行要求「**响亮**说明是哪个
    目录为空、缺哪一件」（`两边都没有可打包的产物` + 两个锚点名 + 本地根路径）·「抽屉齐 ⇒ 从抽屉打」
    （`抽屉`）· 清单模式（`CFG.course` 空）必须给一行人话而不是静默空跑。行为面已在同文件另钉（返回的
    文件名/成员集合、`artifacts.zip` 逐字节相同、不造空包）；「有没有响」只有日志面能判。
  · `tests/remote/test_offline_notebook.py` **2**：notebook **不在 pytest 里执行**（同 `test_offline_notebook`
    那族先例）⇒ cell 文本**就是**契约：一键包 cell 调 `package_pending`、老习惯名与
    `@<run>-it<N>` 附注名两支都要认。
  · `tests/remote/test_offline_deliver.py` **2**：「删不动只 WARN（下拍再试）」（§4.2-3：删不动绝不能把
    补传腿拖住/弄抛）与「账本不可读 ⇒ 一个字节都不删」（DoD-3c 反探针，§1-6 不静默失败）——
    前者的计数器（`prune_failed`）与后者的行为面（抽屉一个都没少）都已另钉，这两条只补「响不响」。
  · `tests/remote/test_deliver_zip.py` **2**：包自述课名与本次导入冲突时的**拒收人话**（必须带两个课名
    + `切到 X 再导` 的动作，§6.2-2/DoD-5b）——「不静默切课」的可见面就是这句话（`ProtocolError` 的正文），
    没有第三个可断的字段。
  · `tests/remote/test_offline_deliverable_split.py` **2**：notebook→新入口的**接线守卫**（调
    `package_pending`、且不得再调 `package_partial`）：与 notebook 族同规（改的是 notebook 文本）。

⚠ 同批能改的都改了（**−6**）：三个「名单里没有 `deliver`」的否定式子串判据换成**目标集合全等**
（`tests/common/test_tmp_clean_keeps_deliver.py` 3 条 → 0）；包内成员从逐条 f-string 子串改成集合包含
（`{f"it-{it:03d}/weights.json"} <= names`，`<=` 不是 `in`）1 条；体检的 `package_source`/未知 magic
两条从 `"pending" in detail` 改成**结构化字段相等**（本仓既有口径：控制台与用例按数据判）2 条。

**2026-10-18 抬 SOURCE 一格的账**（`plan/course-metrics-persistence.plan.md`；SOURCE 1092 → **1096** = +4，
FORM 未动）：新用例 `tests/common/test_tmp_clean_needs_mirror.py` 按**文件路径加载** `tools/tmp-clean.py`
（与 `test_tmp_clean_keeps_deliver.py` 同一接缝），于是断言表达式里引用的 `mod` 被口径 B 的
「读 `.py` 的派生名字」判成源码面（helper 模块头「B 不区分」的已知边界）。这些断言实际读的是
**运行时返回值** —— `uncovered_reason()` 返回的覆盖原因串（全等比较）· `main()` 的退出码 · 捕获的
stdout 行集合 —— 不是源码文本；同一批里能改成行为判据的已改（转存失败只看「目标是否还在」、
命令形状用 argv 切片相等）。**不**靠再抬一格来掩盖措辞判据：新文件里没有 `assert "…" in <文本>` 形态。

**2026-10-21 抬 SOURCE 一格的账**（`docs/nn/engineering.md` §21「同族第二例」：跨进程比两个
事件时间戳的 flake；SOURCE 1096 → **1100** = +4，FORM 未动）：新增**第三条静态守卫**
（`tests/test_no_sleep_as_sync.py` 的「比较两个事件时间戳」）—— 它的 4 条断言**按设计**要读
`.py` 源码：静态守卫的被测对象**就是源码本身**，没有行为替身（这与该文件既有的两条规则
——`.sleep` 与上界墙钟断言——同族同理由：它们本来就在这条基线上）。
触发它的假红：`tests/worker/test_remote_serve_pool.py::test_start_batches_spawns` 拿**父进程**
的 `time.time()` 与**子进程**的 `time.time()` 互比，而那条缝实测只有 +2~+19ms（Windows 的
`time.time()` 量化到 15.6ms ⇒ 量出来的差值能落到负数）⇒ 每三次全量门禁假红一次；**该用例本身
的修法是预算中性的**（口径 A/B 各 +0：判据从墙钟比较换成「spawn 时刻已就绪的 worker 数」，
且新变量名避开本文件里已被判成派生名的 `seen`）。

**2026-10-22 抬 FORM 一格的账**（`hub/offline.py::_claim_without_pack` 的节流窗读面；形态 1483 → **1484**
= +1，SOURCE 未动）：节流窗里原来写死「已经在导包了（节流窗内不重复触发）」——那是**假陈述**
（账本只记得「本窗口触发过」），现场 22:33 四门课全说这句、三门连 `export-bundle.log` 都没有。
新用例的判据主体是**结构化字段** `export_state`（`triggered`/`running`/`done`/`failed`/`not_started`/
`unknown`，三条用例各断言一枚）与 `give_up`/`export_failed`；唯一留下的文本判据是**反探针**
`"已经在导包了" not in trigger_note` —— 它守的正是「旧谎言不许回来」，而 `trigger_note` 就是递给
云机/人看的那行字（同族先例 = 上面 2026-10-15 J3 那笔）。

同一批里能改成行为判据的都改了（3 条原本要撒在措辞上的断言 → `export_state` 等值），
所以这一格是 **+1** 而不是 +5。

**2026-10-23 抬 FORM 一格的账**（`plan/push-mode-notebooks` W0–W2；形态 1484 → **1490** = +6，
SOURCE **未动** = 1099，比基线还低 1）：新增 `tests/remote/test_notebook_push_contract.py`。
notebook **不在 pytest 里执行**（没有可调用的运行时入口），而这份 plan 的非目标就是「pull 行为逐字节
不变」——只能读 cell 源码判：pull 分支仍是 1h + 401/403 FATAL、且不含 `code_wait_sec`；push 分支顺序
`/code` → `git clone` → push-first；`allow_push_first` 关掉 ⇒ FATAL；push-first 内联注册带 `kind=gpu_push`
并写回 `node_id`。同族先例 = 2026-10-10 的采样节点 notebook 接线守卫（`test_rollout_node_notebook.py`）。

⚠ 同一批里能改成行为判据的都改了：注册/派发面 26 条新判据中 20 条是键集/字典等值/状态机行为
（`set(p) == {…}`、`capabilities == ["gpu_push"]`、`reg.give_up`、真 hub loopback 落盘对账），
notebook 的 12 条首版也合并成 5 条（同一语义相关项共一个 assert），所以这一格是 **+6** 而不是 +26。

**2026-10-23 抬 FORM 一格的账**（`plan/push-mode-notebooks` W3；形态 1490 → **1493** = +3，SOURCE 未动）：
新增的三条都是「响亮行」判据（文本**就是**被测对象）——
① `tests/remote/test_offline_deliver_proc.py` 的 outbox 降级：`"降级为进程内线程模式" in logs`（该行是操作员
唯一能区分「真进程」与「降级」的证据）；② 同文件的反探针 `"降级" not in logs`（进程真的起来时**不许**报降级）；
③ `tests/remote/test_deliver_outbox.py::test_close_reports_unsent_work_loudly` 的 `"还有 1 轮没物化" in logs`
（"静默收线"正是该用例要防的那个形态）。同族先例 = 2026-10-15 J3 / 2026-10-22 反探针那一笔：
被测行为**只**表现为一行人读时，文本断言就是行为断言。同一批里能改成结构判据的都改了：
outbox 的状态/rev/ack/字节对账全部读 `state.json` 与文件字节（不靠日志）。

**2026-10-24 抬 FORM 一格的账**（`tests/worker/test_kickstart_burn_guard.py` 随 `b9635cdc` 入库时漏记；
形态 1493 → **1495** = +2，SOURCE 未动）：该用例原有的 5 条文本断言里 **3 条已改成结构化判据**
（`gate_verdict` 的 `verdict/decider` 字段 + `reason.startswith("kickstart-burn(notify")`；
第二次调用改为断言捕获输出**为空**；halt 改 `_halt_calls == ["ABORT"]`）。剩下 2 条是 notify 模式的
**操作员交付物**：`CRITICAL KICKSTART-BURN` 告警行 +「判据只提示，本腿不停车」那句——
行为面已由 `_stop_cause == ""` / `_burn_streak == 3` / 账本判决钉住，但「有没有响、为什么不停」
只在日志行上（`LogBundle` 没有结构化出口）。同族先例 = 2026-10-23 W3 的 outbox 降级行那一笔。

**2026-10-24 抬 FORM 一格的账**（离线 push 真机首测的三处断点；形态 1495 → **1501** = +6，SOURCE 未动）：
新增的 6 条全在 `tests/remote/test_push_register.py`，断的都是**操作员现场读数行**——这次的故障
本身就是「成功不打日志 + 失败说错原因」（真机：13:40:23 被 422 拒 → **13:40:38 已注册成功却没说话**
→ 13:40:40 操作员把会话掐了），所以「说了什么」正是被测对象：

 ① `"已自动注册"`（启动横幅：窗口内成功后必须给一句，别让人去手工登记）；
 ② `"探活门"`（422 必须说成「hub 打不通你的隧道」而非「hub 不可达」，否则排障方向全错）；
 ③ `"hub 不可达" not in …`（反探针：422 那条路**不许**出现这句假话）；
 ④ `'"offline_push":true'`（可粘贴的手工片段 = 兜底交付物本身）；
 ⑤ `"未配置"`（缺 hub/token 是配置错，不许说成隧道没起来）；
 ⑥ `"注册成功"`（**失败后转成功**必须自己说一句——这条静默正是真机误判的源头）。

同一批里能改成结构判据的都改了：成功/失败/放弃三态读 `reg.registered` / `reg.give_up` /
`reg.last_status`，重试次数与退避步数读**调用计数**与假钟推导出的 sleep 序列，「首次成功保持安静」
断言的是 `msgs == []`（不是反探针），`sampler_nodes` 的判据是**节点 id 列表等值**——零文本命中。
同族先例 = 2026-10-23 W3 的 outbox 降级行、2026-10-24 kickstart-burn 告警行那两笔。

**2026-10-25 抬 FORM 一格的账**（进度行「说人话」：`common/progress_text.py` + 两条调用点；
形态 1501 → **1503** = +2，SOURCE 未动）：新增 `tests/common/test_progress_text.py`。
这个模块的**交付物就是那句话本身**（用户口径：「log 要写清楚：它在干什么（rollout? ppo? eval?）
进度如何？有什么问题吗？」）—— 原来的 `进度 seq=8 iter it102 6/17` 被真机连读错两次
（`seq` 当轮号、`it0` 当成在处理第 0 轮）。同一批里能改成结构判据的都改了：
期望串全部用**插值**拼（不写字面量）、`⚠` 标记走模块常量 `WARN_MARK`、
PPO/eval 的规模断言读 `total` 字段而不是文本，所以这一格是 **+2** 而不是 +9。
同族先例 = 2026-10-24 离线 push 那 6 行操作员读数。

**2026-10-26 抬 FORM 一格的账**（push 引导第二起真机事故：`--push` 缺省分歧 + 隧道死了看不出来；
形态 1504 → **1516** = +12，SOURCE 未动 = 1099）：两笔，全部是「被测物**没有**可调用的运行时入口」：

  · **+6 `tests/remote/test_notebook_push_contract.py`**（notebook **不在 pytest 里执行**，只能读 cell 源码）：
    GitHub 兜底必须只拷 `<clone>/nn-training`（拷仓库根就是那条 `ModuleNotFoundError: No module named
    'remote'`）、两个来源都要过「`<code_dir>/remote` 存在」同一道门、缺 `registry_url`/`hub_token` 时
    不许静默降级。这 6 条都已合并成最小条数（同语义共一个 assert），语义相同的另一条用例没再加。
  · **+6 `tests/remote/test_notebook_runtime.py`**：4 条是**反静默**读数行（`cloudflared 已退出…hub 再也
    够不到本机` / `请重跑本 cell` / `没有 remote/ 包` —— 云机断连后操作员唯一能看到的证据就是这几行，
    `LogBundle` 没有结构化出口），2 条是「隧道死 ⇒ 返回 -1 / 接管态 ⇒ -2」的判据里的日志面那一半
    （返回码本身另有断言，已不是文本判据）。

⚠ 同一批里能改成行为判据的都改了：`rc == -1 / -2`、「一个子进程都不起」、「不接受仓库根布局」
全部读返回值与文件系统事实；notebook 那 6 条确实只能读文本（同族先例 = 2026-10-23 W0–W2 的 +6）。

**2026-10-28 抬 FORM 一格的账**（并 `origin/goal-nn` 的「云机设备探测按后端分派」；形态 1516 → **1519**
= +3，SOURCE 未动）：这是**合并账**，不是新增判据 —— 两支单看都绿（各自恰好都停在 1516：本支的 +3 在
轮换用例里，远端那 3 条在 `tests/remote/test_notebook_runtime.py` 的新用例里），合起来才 1519。
三条全是**操作员现场话术**，且被测物没有结构化出口：

  · `paddle 不可用` + `libcudart` + `白烧` + `paddlepaddle-gpu`（`SystemExit` 的字符串 = 真因 +
    为什么早停 + 修法；与 2026-10-26 那条 torch 腿同族，作者已把理由写在用例里）；
  · `未知 … CPU`（探测子进程没起来 ⇒ 按 cpu 开跑但留一行；device 落点另有行为断言，这条只补「有没有响」）；
  · `torch 专属实验臂`（`tpu`/`cuda-dp`/`mps` 在 paddle 后端下**开跑前**拒收；拒收本身由
    `pytest.raises` 钉住，这条断的是「点名是哪个臂」）。

⚠ 同一批 9 个新用例里能改成结构判据的都改了：device / `gpu_count` / `backend_resolved` 的落点、
三态探测（可用 / rc≠0 / 不可解析）、`set(cfg) - set(base)` 的键集全部是数据断言 ——
所以这一格是 **+3** 而不是 +9。

**2026-11-01 降账：形态 1528 → 1473（−55）· 生产源码面 1105 → 1094（−11）**
（`tests/helpers/text_asserts.py` 模块头新增「A/B/C 三类」判准）。这次不是删用例，是换判据：
**B 类（架构接线，即「源码长什么样」）全部改成 AST/对象判据** —— 契约是「谁定义了什么、
谁调了谁、谁 import 了谁」，不是「文件里有没有这串字符」。典型：
`assert "def m(" not in src`（换行/装饰器就漏）→ 定义表交集；`assert "trainer." not in src`
（注释提到就误红）→ 导入图；`assert "x" in src` 的调用/形参/常量判据 → AST 调用名 /
`ast.arg` / `Constant`；`"x" in mapping` → 集合比较（C 类噪音）。
留下的仍以 **A 类**（操作员读数行 / notebook cell / `SystemExit` 正文）为主：
那些的交付物**就是**文本，行为断言替不了。逐文件明细：
`python -m tests.helpers.text_asserts --scope source`（消者是 116 个文件里前几个大户）。

"""

from __future__ import annotations

from pathlib import Path

from tests.helpers import scan_cache
from tests.helpers import text_asserts as TA
from tests.helpers.budget import sanity_floor_of

ROOT = Path(__file__).resolve().parents[1]

#: 形态口径基线（2026-10-09 T1/T3/T2 删除后实测 = 1435；清理前 = 1488；
#: 2026-10-11 aistudio 传输硬化抬到 1441；同日探针上界抬到 1442；2026-10-12 预取解包抬到 1445；
#: 2026-10-14 离线 A/B 腿抬到 1457（本次 +8 与接手时既有 +4 的账见模块头）
#: 2026-10-14 离线本地盘 A 腿抬到 1460（+5 读数行 − 2 旧读数行；理由见模块头）
#: 2026-10-14 离线本地盘 B1 腿抬到 1462（两个根 + 恢复腿的 2 条读数行；理由见模块头）
#: 2026-10-14 离线本地盘 C1/B4 腿抬到 1466（备份/交付面的 4 条响亮行；理由见模块头）
#: 2026-10-14 离线本地盘 B2 腿抬到 1469（容量判据/段中盘满的 3 条响亮行；理由见模块头）
#: 2026-10-15 离线 J3 腿抬到 1470（hub 递给云机的那行 `trigger_note`；理由见模块头）
#: 2026-10-17 离线 backfeed-resilience 的 E/F/G/H 四片抬到 1480（+10 操作员读数行 −6 已改成非文本；
#:   逐笔分摊见模块头）
#: 2026-10-18 plan/eval-gap-honesty 抬到 1483（+3 收工/裁定行；理由见模块头）
#: 2026-10-22 节流窗读面抬到 **1484**（+1 反探针 `"已经在导包了" not in trigger_note`；理由见模块头）
#: 2026-10-23 push-mode-notebooks W0–W2 抬到 **1490**（+6 = notebook push/pull 契约；理由见模块头）
#: 2026-10-23 push-mode-notebooks W3 抬到 **1493**（+3 = outbox 降级行 + 反探针 + 收线欠账行；理由见模块头）
#: 2026-10-24 kickstart-burn 守卫抬到 **1495**（+2 = notify 告警/不停车两行；另 3 条已改结构判据；理由见模块头）
#: 2026-10-24 离线 push 真机首测抬到 **1501**（+6 = 注册横幅 / 422 话术 / 反探针 / 可粘贴片段 /
#:   未配置 / 失败后转成功共 6 行操作员读数；其余全改结构判据；理由见模块头）
#: 2026-10-25 进度行人读化抬到 **1503**（+2，全在 `tests/common/test_progress_text.py`：那个模块的
#:   **存在意义就是措辞**（一行说清在干什么/进度/有没有问题），期望串已尽量改成插值，只剩
#:   「⚠ 标记在不在」与「不许出现第0轮」两条必须看文本；理由见模块头末段）。
#: 2026-10-26 抬到 **1504**（+1，`tests/remote/test_notebook_runtime.py` 的
#:   `test_resolve_device_torch_broken_is_fatal_not_cpu`：torch 装坏时的 FATAL 文案必须**逐字**
#:   含 `libcudart`（现场证据）、`白烧`（为什么要早停）、`pip install torch`（修法）——
#:   那是操作员在云机断连后唯一的自救信息，只能用文本判据钉）。
#: 2026-10-26 再抬到 **1516**（+12 = notebook push 契约 6 + 反静默读数行 6；逐笔见模块头末段）。
#: 2026-10-28 并 `origin/goal-nn` 的「设备探测按后端分派」抬到 **1519**（+3 是**合并账**：两支各自
#:   恰好都停在 1516，合起来 1519；逐笔见模块头末段）。
#: 2026-10-29 抬到 **1522**（+3，`tests/remote/test_offline_local_first.py` 的
#:   `test_run_one_course_code_changed_says_restart_session`：那条 `SystemExit` 正文**就是产品**
#:   —— 操作员在 ipynb 里看到「代码已变更」后唯一该做的事是 Restart session；两个 sha 必须能
#:   直接对账、且必须写明「重跑本 cell 没用」。三句都是给人读的，行为断言替不了）。
#: 2026-10-29 再抬到 **1528**（+6，日志瘦身那两处**行为**只能靠文本钉：`iter` 族日志行
#:   60s 窗（`test_progress_hooks` 的 `test_iter_progress_logs_at_most_once_a_minute`）与
#:   「流式采集收官读数并进轮末那一行」（`test_remote_iter` 的
#:   `test_volume_line_merges_into_the_rollout_line`）——「打了几行、打在哪一行」就是判据本身）。
#: 2026-11-01 降到 **1473**（−55：B 类架构接线判据改 AST/对象判据，不删用例；理由见模块头末段）。
#: 同日再回 **1474**（+1：`test_lock_reentrancy` 新增的"嵌套 with 内层锁点"用例带一句
#:   `assert "bump" in rep.problems[0]` —— 报告正文点名凶手，是操作员唯一的证据；同文件
#:   已有五条同形判据，不是新开的文风）。
#: 2026-11-02（并入 origin/goal-nn 时两条并行改动撞在同一格）：另一支的 +1 是
#:   `tests/common/test_dist_weights.py` 的节点剔除原因行（kaggle 的权重 POST 失败 ⇒ 排除该节点
#:   —— 操作员在轮次异常时唯一的归因读数，行为面 `alive` 只断言剔了谁；同批另 3 条已改成
#:   全等/行为判据，不计）。两笔都是真的 ⇒ **1475**（并入时实测）。
#: —— 每笔理由都见模块头）。
#: 2026-11-07 抬到 **1493**（+18，实测）：C1–C9 只读探针的 8 个新测试里，断言对象是
#:   `build_report` / `render_markdown` 产出的 **markdown 报告本身**（表头、分位数、
#:   MDE、口径说明行）——探针的交付物就是文本产物，改成行为断言等于不再验产物；
#:   同批其余判据均为数值/对象断言。涉及 `tests/tools/test_p0_*`、
#:   `test_c9_critic_calibration.py`、`test_z11_seed_level.py`、
#:   `tests/biz/test_w1_horizon_guard.py`。这是**产物契约**，不是新开的文风。
#: 2026-11-14 抬到 **1498**（+5，实测）：`tests/remote/test_remote_hotswap.py` 新增两条停机留痕用例
#:   —— `_release_cloud_machine` 的 `unassign()` 失败时，**日志正文本身**就是交付物（异常 repr + 「机器
#:   未释放」+ 该怎么做）。行为面只能验「调了一次」，验不了「读日志的人知道机器还在计费」；
#:   与上面 `test_notebook_runtime` / `test_offline_local_first` 同一类：操作员唯一的证据是那几行字。
#: 2026-11-14 再抬到 **1500**（+2，实测）：`tests/trainer/test_loop_heartbeat_visibility.py` +
#:   `tests/worker/test_eval_heartbeat.py` 的心跳留痕用例 —— 要钉的是**那行 WARN 里的后果说明**
#:   （「console 会显示旧状态」/「runner_state 会停在旧值」）。行为面能验「喊了一次」，验不了
#:   「读日志的人知道要不要管」；与上面几条同一类：措辞本身是交付物。
#: 2026-11-15 抬到 **1501**（+1，实测）：`tests/remote/test_lifecycle.py` 的 SIGTERM 自投失败用例 ——
#:   要钉的正是那句「进程仍在跑，hub 会当这台已下线」（重复计费）。行为面只能验「没抛 + 写了一次」，
#:   验不了「读日志的人知不知道机器还在计费」；与上面几条同一类：措辞本身是交付物。
#: 2026-11-15 抬到 **1511**（+3，实测）：G9 第十五批（`worker/terminal_stats` 写盘失败留痕 /
#:   `worker/serve_pool` kill 失败留痕）—— 两条判据都是「盘上/日志上分不分得清『没有』与『坏了』」，
#:   行为面只验得到「没抛」。逐笔：`iteration-terminal.jsonl` + `No space left on device`（2 条）、
#:   `kill 失败` + `OSError`（2 条）；同批删掉的 `remote/job_fs._git_head` 测试（-1）抵消后净 +3。
#: 2026-11-15 早前抬到 **1508**（+7，实测）：G9 第十四批（`trainer/batch_runner._heartbeat` 留痕 /
#:   `worker/loop_guards_sweep` 收窄 `BaseException` / `remote/hub_client.git_head` 带成因）的
#:   判据就是「读日志的人看没看到这一行、上面有没有成因」—— 行为面只能验「没抛 + 调了一次」，
#:   验不了「`swept 0` 与『清理器炸了』在日志里长得不一样」。逐笔：心跳 2 条
#:   （`runner_state 会停在旧值` / `ModuleNotFoundError`）、git_head 2 条（`TimeoutExpired` /
#:   退出码 `128`）、`workdir-sweep it5 skipped` + `OSError` + `磁盘只读` 3 条。与上面几条同类：
#:   措辞本身是交付物 ⇒ 这是口径 A 里少数几个**该有**的文本判据。
#: 2026-11-15 抬到 **1513**（+2，实测）：B 类三处窄捕的用例 —— `processor` 侧只验了「没抛/返回值」，
#:   但「节点回执不是 JSON 时要炸」「校验器崩了不许被读成缓存坏」这两条**只能看错误信息是否说明
#:   了后果**（`_probe_sha` 的那句 docstring 就是交付物）。逐笔 2 条，全在 `test_push_probe_errors.py`。
#: 2026-11-19 抬到 **1514**（+1，实测）：B7-P4b 的 `_JobStore.__init__` 断言「唯一的两相回填
#:   `self.ledger.leases = self.leases` 恰好在场」——这条契约没有运行时出口（回填漏了只会在
#:   `publish` 时炸），只能看源码文本。
#: 2026-11-19 抬到 **1525**（+11，实测）：P0-8 第三项把 9 个脚本壳文件的 `check(cond, msg)`
#:   全部换成真 `assert`（`python.refactor.md` §P0-8）。这些文本形态判据**原先就存在**，只是
#:   藏在 `check()` 的调用实参里、扫描器看不见（`check("x" in text, …)` 不是 Assert 节点）；
#:   去掉包装后第一次被计入。不是新判据，也没有放宽任何断言 —— 逐条仍是原来那句。
#: 2026-11-18 再抬 **+4**（relay 通道连不上 hub 的三处操作员读面 —— `probe_hub` 的机读面已按
#:   `facts["host"/"dns"/"routes"]` 钉住；文本只补「这行有没有把现场写出来」）。
#: 2026-11-19 合入上游 goal-nn（74 提交）后实测 1534（+9：上游的 relay / replay / eval
#: 读面用例；其中 4 条已在前一行记过账，其余同上口径 —— 文本本身是交付物）。
#: 2026-11-20 抬到 **1535**（+1，实测）：门禁新增「worker 数按提交余量封顶」（`nn-python-gate.sh`
#:   的 `memory_budget_workers` 接线）。这是**shell 脚本**：它没有可 import 的入口，“只在装不下
#:   时才减 worker / 探不到时 fail-open” 除了读文本没有第二个出口（同文件里其它 `test_gate_*`
#:   也是这么钉结构）。行为面已由 `common/platform_utils.memory_budget_workers` 的纯函数用例
#:   覆盖，这里只钉接线（四个断言计进一格，其余不是文本形态）。
#: 2026-11-21 抬到 **1536**（+1，实测）：P0-8 把训练机侧三家的 `bun_version` 收进
#:   `common.proc.Toolchain` 端口后，`tests/trainer/test_batch_runner_split.py` 的「裸名注入点」
#:   契约从 `bun_version` 改成 `TOOLCHAIN`，并新增一条守卫「模块里只有端口这一套接缝」。
#:   新守卫 4 条断言里只有 2 条是形态判据（`TOOLCHAIN in loads` / `bun_version not in loads`
#:   ——「名字被裸名读、旧名不再绑」没有运行时出口，只能看源码结构）；另 2 条（`hasattr` /
#:   `is`）两个口径都不计。该文件口径 A 4 → 6；上一格基线 1535 比 HEAD 实测 1534 宽 1
#:   ⇒ 本笔实测 1536（名义 +1）。
BASELINE_FORM = 1536
#: 生产源码文本口径基线（同上；清理前 = 1084；2026-10-10 因新 notebook 接线守卫抬到 1091；
#: 2026-10-14 wire-diet M4 切片 2 的产物段结构守卫抬到 1092；
#: 2026-10-18 course-metrics 备份库用例抬到 **1096**（+4，实测值；理由见模块头末段）；
#: 2026-10-21 第三条静态守卫（比较两个事件时间戳）抬到 **1100**（+4；理由见模块头末段）。）
#: 2026-10-29 抬到 **1105**（+5，与上一格同一批改动：`test_remote_iter.py` 是「写桩 .py 再跑」的文件
#:   （口径 B 的 `.py` 门对它整体成立）⇒ 那 3 条「并进同一行 / 不许单起一行 / 拼接坏了仍打轮末行」
#:   全计进 B；另 2 条在 `test_progress_hook.py`。判据是「打了几行、打在哪一行」，只能看文本）。
#: 2026-11-01 回 **1100**（+6，全在 `tests/test_lock_reentrancy.py`：新用例的 3 条 + 仓库扫描
#:   用例那 3 条。这是口径 B 已经写在模块头的已知限边 ——「B 不区分读的是生产源码还是 `.py`
#:   路径字符串」，而该文件的合成样本本来就用 `"*.py"` 作字典键 ⇒ 计数增、判据性质未变）。
#: 2026-11-02 抬到 **1114**（+14，全在新增 `tests/test_scan_cache.py`：那边用 `"a.py"` 作
#:   缓存键、逐条断行为，于是整个文件的 assert 都被 B 当成「读 .py 面」计进 —— 与上一格同一
#:   条已知限边。口径 A 仍 0 条（不涉及文本成员比较），是真「行为断言」。
#: 2026-11-04 抬到 **1115**（+1，`tests/common/test_dist_common_poll.py` 任务级仲裁后补的
#:   「派发器不得内联传输判据」守卫：`assert c_consts.isdisjoint({409, "task lost on node"})`
#:   是 **AST 结构**判据（口径 A 0 条），只是输入来自读 `.py` ⇒ 按同一已知限边计进一格。
#:   同批把 eval 腿执行面从 `eval_dispatch` 挪进 `worker/eval_exec.py`，旧文本判据删/改成
#:   AST 判据后 B 净 +1、A 净 −1）。
#: 2026-11-07 抬到 **1121**（+6，实测）：与上一格同批 C1–C9 探针，其中 6 条判据读
#:   `.py` 源码面/产物文本（课程公式守卫、报告表头契约），按口径 B 的已知限边计进。
#:   口径 A 仍 0 条；行为/数值断言未变。每笔理由同上，均为产物/源码契约。
#: 2026-11-13 抬到 **1126**（+5，实测）：新增两条门禁
#:   `tests/test_python_version_matrix.py` / `tests/test_python_shape_budget.py`。
#:   它们判的是**数值/结构**（版本区间、每文件函数计数），不是措辞；被计进来是口径 B 的已知
#:   边界（`.python-version` 含 `.py` 子串；扫描器里出现 `*.py`）。口径 A 仍 0 条。
#:   ⚠ 一旦口径 B 能把「路径字符串」与「源码文本」分开，这 5 条应回退。
#: 2026-11-14 抬到 **1132**（+6，实测）：G3 生产 print 门禁 `tests/test_no_print_in_prod.py`
#:   用 AST 扫 `*.py` 源码面，整个文件的 assert 按口径 B 计进（判据是结构计数，不是措辞；
#:   口径 A 仍 0 条）。与上面「扫描器里出现 `*.py`」同一已知限边。
#: 2026-11-14 抬到 **1140**（+8，实测）：G4 宽泛异常门禁 `tests/test_broad_except_budget.py`
#:   同样用 AST 扫 `*.py` 源码面（口径 B 的同一已知限边：判据是结构计数，不是措辞；
#:   口径 A 仍 0 条）。同批 G6 抑制符门禁 `tests/test_suppression_budget.py` 再 +2 到 **1142**
#:   （同样读 `*.py` 面）；G5 私有断言门禁 `tests/test_private_assert_budget.py` 再 +4 到 **1146**；
#:   同日 G3 补一条「基线只许调小」（新增 1 条读 `*.py` 面的断言）⇒ **1147**。
#: 2026-11-14 抬到 **1154**（+7，实测）：G9「宽泛捕获 + 只 pass」门禁
#:   `tests/test_silent_except_budget.py` 同样用 AST 扫 `*.py` 源码面（口径 B 的同一已知限边：
#:   判据是结构计数与基线账，不是措辞；口径 A 仍 0 条，是真「行为断言」）。
#: 2026-11-15 抬到 **1155**（+1，实测）：新门禁 `tests/test_worker_forward_names_are_live.py`
#:   （`remote/worker.py` 的转发名不得是死名）扫描面就是 `*.py` 全文 —— 与上面几格同一已知限边
#:   （判据是「名字还有没有引用」，不是措辞）。口径 A 仍 0 条。
#: 2026-11-16 抬到 **1157**（+2，实测）：新门禁 `tests/test_env_reads_budget.py`（G13）用 AST 扫
#:   `*.py` 面数 env 直读 —— 与 G3/G4/G6/G9 同一已知限边（判据是「每个文件读了几次、旋钮有没有
#:   第二个家」，不是措辞；口径 A 仍 0 条）。同批 `tests/trainer/test_batch_plan_split.py` 删掉一条
#:   文本判据（那份路径常量已搬进 `common/settings.py`，改由 G13 行为化地抓）⇒ FORM 未动。
#: 2026-11-16 抬到 **1159**（+2，实测）：新门禁 `tests/test_any_budget.py`（G14）用 AST 扫 `*.py` 面
#:   数 `Any` 注解，并在同一文件里对**自己那两段示例源码**做口径自检（「注释里的字样不算 / `*.Any`
#:   算」）—— 与 G13/G4/G6/G9 同一已知限边（判据是结构计数，不是措辞；口径 A 仍 0 条）。
#: 2026-11-17 抬 SOURCE 一格到 **1160**（+1，实测）：新门禁 `tests/test_executor_ownership.py`（G16：
#:   线程池只能由 `common.runtime.Runtime` 建）用 AST 扫 `*.py` 面 —— 与 G3/G4/G6/G9/G13/G14 同一
#:   已知限边（判据是「谁构造了池」的结构计数 + 迁移有没有被回退，不是措辞；口径 A 仍 0 条）。
#:   该文件里只有那条**两向护栏**（`Runtime(` 处数不低于迁移后的实测值）被口径 B 计进 ——
#:   「迁移被无声回退」这个形态只能数处数，行为面没有可调的入口。FORM 未动。
#: 2026-11-18 抬 SOURCE 一格到 **1165**（+5，实测）：G7 扩面（`tests/test_module_asserts.py`，
#:   原 `test_common_module_asserts.py` 只 1 条）—— 新加的四组判据全在「生产源码的结构」上：
#:   模块级 assert 零容忍 · 函数级存量只降不涨 · 基线不许留旧数字 · 命令面不许出现 `python -O`。
#:   它们与 G13/G14/G16 同一已知限边（判据是 AST 结构计数，不是措辞；口径 A 仍 0 条）：要判
#:   「生产包里有没有 import 期 assert」只能读生产源码，而行为面（import 一次）只能证明当前这些
#:   模块碰巧不炸 —— 下一次有人写回模块级 assert，行为面看不出。FORM 未动。
#: 2026-11-19 抬 SOURCE 一格到 **1170**（+5，实测）：B8 ④ 的新守卫
#:   `tests/worker/test_ppo_loop_core.py`（goal ↔ intent 共用循环核心的六条，本文件实测 6 条）
#:   全部是 **AST 结构判据**：核心的字符串字面量里有没有头名、入口体是不是单条
#:   `return run_ppo(...)`、两个头之间还有没有 12 行克隆窗口、形参表是否冻结。
#:   它们与 G13/G14/G16/G7 同一已知限边（“读 `.py` 面的断言”一律计进，不看是否真在比措辞）：
#:   口径 A 仍 0 条。基线前一格本就比实测宽 1（HEAD 实测 1164）⇒ 净 +5。FORM 未动。
#: 2026-11-19 再抬到 **1173**（+3，实测）：B8 第十三刀的守卫
#:   `tests/worker/test_ppo_backend_parity.py`（共享核零后端 import · 两张算子表字段全接 ·
#:   两个引擎都调 `run_step`）—— 三条全是 **AST 判据**（读源码只为 parse，不比措辞），
#:   与 G13/G14/G16/G7/上一格同一已知限边；口径 A 仍 0 条。净 +3 全来自这道新守卫；
#:   同批把 `test_xla_step_diag.py` 的 demo 索引钉从「源码子串」改成 AST 接线 + 行为判据
#:   （那条本来就在口径 B 之外，不抵账）。FORM 未动。
#: 2026-11-19 再抬到 **1174**（+1，实测）：B8 第十四刀的守卫
#:   `tests/worker/test_ppo_cli_core.py` 里唯一的源码读点 —— 「goal / intent 的 `main()` 只剩
#:   `run_cli(_spec())`、两个模块里不再有 `add_argument`」。这条契约**没有运行时出口**：
#:   重新内联一份 parser 之后 `--help` 依旧逐字相同（另两条用例正是断言它相同），只有
#:   看源码结构才能发现重复又长回来了。其余断言（文案 / 顺序 / 默认值）全是运行时 Action 表，
#:   不读 `.py`；同一条用例已压成**单条** `_shell_violations(tree) == []`（首版 9 条，这就是
#:   口径 B 的已知限边：它数函数内 assert，不看是否真在比措辞）。FORM 未动。
#: 2026-11-19 再抬到 **1183**（+9，实测）：B7-P1 的结构守卫在
#:   `tests/hub/test_hub_job_store_split.py` 增加 9 条 **AST 判据**（调度协作者的状态归属 /
#:   MRO / 构造注入 / 组合类不复制状态 / 层号）。与上面几格同一已知限边：「`_JobStore` 的
#:   基类集合与状态主人」没有运行时出口（类对象本身就是契约）；口径 A 仍 0 条。
#: 2026-11-19 再抬到 **1195**（+12，实测）：B7-P2 的同一份结构守卫新增租约协作者的
#:   状态归属 / 构造注入 / 39 个转发壳闭集 + 5 个内部接缝 / 协作者 import 隔离 / 层号判据。
#:   与上一格同一已知限边（类对象与基类集合没有运行时出口）；口径 A 仍 0 条。
#: 2026-11-19 再抬到 **1196**（+1，实测）：B7-P3 的队列结构守卫新增「10 个状态字段只住
#:   `course_table` / `board`、组合类只留转发属性」一条 AST 判据（同一已知限边）。
#: 2026-11-19 再抬到 **1197**（+1，实测）：B7-P4 把同一条判据扩到 `Dispatch` 的 11 个字段
#:   与 5 把旁路锁的身份对账（同一已知限边：状态主人没有运行时读数，只能看 AST）。
#: 2026-11-19 再抬到 **1207**（+10，实测）：B7-P4b 的账本协作者结构判据（16 个转发壳、
#:   3 个状态 + 6 个注入面、MRO/层号、两相回填）。同一已知限边：类结构与构造顺序没有
#:   运行时出口；口径 A 仍 0 条。
#: 2026-11-19 再抬到 **1210**（+3，实测）：B7-S2 的队列结构守卫新增「26 个 scope 转发壳 /
#:   `QueueScope` 签名与 QueuePeer 逐参数一致 / 状态写者表包含 `self.hub.X` 形态」三条 AST
#:   判据（同一已知限边：基类集合与转发壳没有运行时出口；口径 A 仍 0 条）。
#: 2026-11-20 再抬到 **1221**（+11，实测）：`trainer/batch_runner.py` 拆出
#:   `trainer/batch_lanes.py`（通道机器 + `UnitLedger`）—— 三个搬家守卫现在**逐条检两个模块**
#:   （定义表 / import 闭集 / 裸名注入点 / 常量归属 / 入边归属者），判据全是 AST 与 `hasattr`
#:   （没有一个是在比措辞），但口径 B 的已知限边就是「引用由 `.py` 读出来的名字的 assert 一律
#:   计入」。逐笔：`test_batch_runner_phases.py` +6（`_settle_unit(self, ledger)` 与
#:   `_UnitLanes.ledger()` 的装配点守卫）、`test_batch_runner_split.py` +5（两家对照的闭集 /
#:   常量归属 / 无反向边），`test_batch_lanes_split.py` 净 0（删掉的那条文本判据已改成
#:   AST 定义表判据，计数不变）。口径 A 仍 1534（新判据无一挂文本）。
#: 2026-11-21 抬到 **1224**（+3，实测）：P0-5 的两簇状态对象化各带一条**结构**守卫 ——
#:   `tests/hub/test_pack_triggers.py` +1（`hub/task_pack.py` 顶层不许再有可变容器字面量）与
#:   `tests/remote/test_deliver_outbox.py` +2（闸表必须是 `OutboxGates()` 实例 / 不再有
#:   `_GATES_LOCK`）。两条都只能读 `.py` 源码：被测的是「可变状态住在哪」，没有任何运行时出口
#:   （对象存在 ≠ 对象拥有状态）。口径 A 同笔 −1（1535 → 1534）：两条 `x in <dict>` 的集合判据
#:   改成 `set(...) == {...}`（§口径 C 的写法，语义不变）。
#: 2026-11-21 抬到 **1226**（+2，实测）：同一笔 P0-8 端口化在
#:   `tests/trainer/test_batch_runner_split.py` 新增的裸名守卫里，2 条断言读的是 AST 解析出的
#:   `RUNNER_SRC`（`_load_names_in`）—— 判据是「模块以裸名读端口、不再绑旧名」，只能看源码；
#:   口径 A 同为 +2，属同笔改动（文件 16 → 18）。
#: 2026-11-21 再 +1（1226 → 1227，实测）：`tests/tools/test_dist_upgrade_cli.py` 新增并行下发
#: 回归夹具（threading.Barrier 钉住三个节点同时在飞）与重复 id 拒绝用例，四条断言只比
#: 运行期收集到的 id / reason / 长度，没有任何源码措辞；口径 B 的已知限边是「文件里出现
#: .py 字面量 ⇒ 该文件所有 assert 计入」，该文件本来就有 CLI 端到端用例的 .py 路径，
#: 于是新断言被连坐算 +1。属于误计，不是新增文本判据。
#: 2026-11-23 抬到 **1228**（+1，实测）：`tests/remote/test_rollout_node_notebook.py` 的 hub
#: 兜底守卫断言两份代码包**铺成了同一棵树**（`nn-training/remote/rollout_node.py` 里是假包
#: 写的那份内容、`tools/agent/sampler-agent.ts` 在仓根）—— 判据是落盘数据，不是源码措辞；
#: 计进去只因路径带 `.py` 字面量（口径 B 的门是文本级的）。同一条用例里那个撞名的局部量
#: 已改名（`calls` → `rounds`，与第 274 行无关的 AST 收集量不再连坐）。这里剩下的 1 条无法
#: 换写法：包内成员名就是 `remote/rollout_node.py`，cell 侧也按这个名字校验。口径 A 不变。
#: 2026-11-23 抬到 **1236**（+8，实测）：新增 `tests/worker/test_bc_epoch_seams.py` —— BC 两个
#:   后端的 epoch 循环必须同形（`run_epochs` 只编排，训练 / 验证 / 回调各在自己接缝里）。
#:   判据全是 AST（只看有几层循环、接缝行数），没有一条在比措辞；计进口径 B 只因文件里带
#:   `bc.py` / `bc_paddle.py` 两个 `.py` 字面量。口径 A 不变（1536）。
#: 2026-11-23 抬到 **1239**（+3，实测）：函数形状门禁从 200 收紧到 150（旁边留 200 硬顶）。
#:   三条新判据全在 `tests/test_python_shape_budget.py`：>150 存量增长、存量表与实测算相等、
#:   >200 硬顶。该文件本来就带 `*.py` 字面量（`PROD_DIRS` 的 rglob）⇒ 口径 B 计入。口径 A 不变。
#: 2026-11-27 抬到 **1241**（+2，实测）：`tests/test_subproc_util.py` 新增两条**结构**守卫 ——
#:   `test_no_private_dead_port_helper_anywhere`（不许再有 `def _dead_port`）与
#:   `test_http_servers_in_tests_use_serve_fast`（`serve_forever` 裸引用 vs 传了间隔，AST 判据）。
#:   两条都不在比措辞；计进口径 B 只因该文件带 `*.py` 字面量（`_test_files()` 的 rglob）——
#:   与前面几格同一已知限边。口径 A 不变（1536）。
#: 2026-11-28 抬到 **1243**（+2，实测）：新增 `tests/remote/test_wire_seam.py`（P0-8 第二代
#:   线路端口）。两条判据都只能读源码：`test_hosts_never_call_transport_primitives_directly`
#:   （宿主里 `_request` / `_wire_add` 的**调用点必须为 0**）与
#:   `test_port_name_is_resolved_at_call_time`（`WIRE` 不许绑进缺省值 / 类属性 / 模块级别名）
#:   —— 被测物是「这个模块还直调私有名吗」「这个名字是不是被提前绑死了」，没有任何运行期出口
#:   （绑死了照样能跑，只是 patch 打空）。计进口径 B 只因文件里带 `job_lifecycle.py` /
#:   `bc_job.py` 两个 `.py` 字面量（`HOSTS` 元组）；口径 A 不变（1536）。
BASELINE_SRC = 1243


#: 计数异常下界：护栏最怕的是「口径坏了 ⇒ 恒 0 ⇒ 恒绿」。派生式（基线九成，见
#: `tests/helpers/budget.py`）：两个计数器各自随自己的基线收紧，清理基线时不必回填数字。
def _floor_of_form() -> int:
    return sanity_floor_of(BASELINE_FORM)


def _floor_of_src() -> int:
    return sanity_floor_of(BASELINE_SRC)


def test_text_assert_counts_only_go_down() -> None:
    # 内容哈希缓存（`tests/helpers/scan_cache.py`）：只省「重算一遍同一份源码」的时间，
    # 判据与口径不变；命中率坏了也只影响快慢，不影响结论。
    cache = scan_cache.Cache("text-asserts", version=TA.CACHE_VERSION, root=ROOT)
    form, src, per_file = TA.totals(ROOT, cache=cache)
    assert form > _floor_of_form() and src > _floor_of_src(), (
        f"计数异常偏小（形态 {form} / 源码面 {src}，地板 {_floor_of_form()}/{_floor_of_src()}）"
        "—— 扫描面或口径坏了，护栏会恒绿"
    )
    top = sorted(per_file.items(), key=lambda kv: -kv[1][1])[:5]
    hint = "；".join(f"{rel}={s}" for rel, (_, s) in top)
    assert form <= BASELINE_FORM, (
        f"文本形态断言 {form} > 基线 {BASELINE_FORM}（多的见 {hint}）—— "
        f"新判据请改成行为断言；确需文本判据就在本文件抬一格并写明理由"
    )
    assert src <= BASELINE_SRC, (
        f"生产源码文本断言 {src} > 基线 {BASELINE_SRC}（多的见 {hint}）—— "
        f"同上：源码长什么样 ≠ 行为对"
    )
