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

**同一格里的另一笔（不是本次改动的账）**：抬格前实测 HEAD `1cca709c` **已是 1449**（≠ 1445）——
10-12 基线之后新加的用例文件（`tests/trainer/test_loop_serve_switches.py` 3 · `tests/remote/test_payload_slim.py` 1 ·
`tests/trainer/test_ppo_watchdog.py` 1，另一处 −1）没跟着抬基线。本次一并显性化（1445 + 4 + 8 = 1457），
不替它们改判据（那属于那些用例自己的账）。

"""

from __future__ import annotations

from pathlib import Path

from tests.helpers import text_asserts as TA

ROOT = Path(__file__).resolve().parents[1]

#: 形态口径基线（2026-10-09 T1/T3/T2 删除后实测 = 1435；清理前 = 1488；
#: 2026-10-11 aistudio 传输硬化抬到 1441；同日探针上界抬到 1442；2026-10-12 预取解包抬到 1445；
#: 2026-10-14 离线 A/B 腿抬到 1457（本次 +8 与接手时既有 +4 的账见模块头）
#: 2026-10-14 离线本地盘 A 腿抬到 1460（+5 读数行 − 2 旧读数行；理由见模块头）
#: 2026-10-14 离线本地盘 B1 腿抬到 1462（两个根 + 恢复腿的 2 条读数行；理由见模块头）
#: 2026-10-14 离线本地盘 C1/B4 腿抬到 1466（备份/交付面的 4 条响亮行；理由见模块头）
#: 2026-10-14 离线本地盘 B2 腿抬到 1469（容量判据/段中盘满的 3 条响亮行；理由见模块头）
#: —— 每笔理由都见模块头）。
BASELINE_FORM = 1469
#: 生产源码文本口径基线（同上；清理前 = 1084；2026-10-10 因新 notebook 接线守卫抬到 1091；
#: 2026-10-14 wire-diet M4 切片 2 的产物段结构守卫抬到 1092 —— 两笔理由都见模块头）。
BASELINE_SRC = 1092
#: 计数异常下界：护栏最怕的是「口径坏了 ⇒ 恒 0 ⇒ 恒绿」。
SANITY_FLOOR = (400, 200)


def test_text_assert_counts_only_go_down() -> None:
    form, src, per_file = TA.totals(ROOT)
    assert form > SANITY_FLOOR[0] and src > SANITY_FLOOR[1], (
        f"计数异常偏小（形态 {form} / 源码面 {src}）—— 扫描面或口径坏了，护栏会恒绿"
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
