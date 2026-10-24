# Plan: offline-deliver-full-and-gaps — 每轮回传 + 漏轮统计（2026-10-24 评审修订）

> **交付物**：本 plan + 对应实现/测试。**路径基准**：除注明外相对 `nn-training/`。
> **状态**：立案 2026-10-22；2026-10-23 按用户口径重写；**2026-10-24 按评审收敛为两档并修掉
> P0/P1**。R1/R2 的「每一轮都发整份 weight+opt」**已被 `plan/anchor-full-rounds` 推翻**
> （2026-10-23 08:56 用户口径：「首轮和 eval 轮发整份，其它轮发 delta」）。
> 取代 `plan/offline-deliver-stall.plan.md` §4-W1-C 的 D-2 兜底与 §4-W1-D 的 metrics 分流。

---

## 0. 口径（以最新一条为准）

> **原始需求（2026-10-22）**：每个 eval 轮回传完整权重、完整 opt，以及上一次回传至今遗漏的
> 所有轮的统计数据。
>
> **用户修正（2026-10-23 00:15）**：网络顺畅时**每一轮都回传**；抖动严重时自动改为只传评估轮
> （整份权重、opt、之前遗漏的各轮统计）；网络恢复后继续每一轮都回传。
>
> **用户修正（2026-10-23 08:56）**：**首轮和 eval 轮发整份 weight 和 opt，其它轮发 delta。**

三者合并后的交付物：

| # | 对象 | `every` 档（网络顺畅） | `anchors` 档（抖动/断连） |
|---|---|---|---|
| R1 | 权重 | **每一轮**都发；锚点轮整份、非锚点轮 delta（写盘时定） | 每个锚点轮整份 |
| R2 | opt | 同上（锚点整份、非锚点 `BOD1` delta） | 每个锚点轮整份 |
| R3 | 漏轮统计 | 每轮随权重体自带 row；没有随行的轮走统计批 | **所有**未上行轮的 row（批量） |
| R4 | 恢复 | — | 升档后按升序补发全部积压，不设轮数上限 |

「完整」= **未经 delta 压缩的整份字节**，只在锚点轮出现。2026-10-22 y30-stack 十轮
`BWD1`/`BOD1` 全失败、y31-fire 只有 it1–it5 可重建，是「delta 链没有第三方持有」的现场证据；
`anchor-full-rounds` 用「每 5 轮一个可独立装载的整份落点」解决它，而不是每轮都展开。

---

## 1. 现状（代码实测）

### 1.1 已落地

* **形态在写盘时定**：`remote/round_forms.py`（锚点轮整份、其余 `opt.tar` 写成 `BOD1`），
  判据唯一 = `deliver_wire.is_full_round`。
* **补传体**：`deliver_parts.build_round_body`（权重 + opt + row + eval_rows 同体，8 MB 上限）。
* **两档候选集**：`offline_deliver._sync` 由 `ModeTracker.tick_mode()` 决定
  `pending_anchors()`（anchors）或 `pending()`（every）。
* **统计批**：`deliver_parts.DeliverMetricsMixin` → `?parts=metrics` →
  `hub/store_offline.store_offline_metrics`；第三本账 `delivered.json.metrics`。
* **锚点兜底**：无 A 层读数时 `pick_anchors(every=课程 eval_every)`。
* **观测**：`status()` 有 `mode` / `avail_bps` / `disconnects` / `round_period` /
  `need_bps` / `backlog` / `eta` / `metrics_backlog`。

### 1.2 明确退场/未做

| 项 | 状态 |
|---|---|
| `full` / `bulk` 三档 | **退场**：两者发同一批字节，只留标签没有意义；统一为 `every` / `anchors` |
| 投递侧展开（`full_parts_for`） | **死代码，不接线**：会把 `anchor-full-rounds` 的省量归零。保留只为契约测试 |
| `?parts=weights` / `?parts=opt` | **未做**：hub 只认 `metrics`，未知 parts 现在响亮 400 |
| 按件幂等 / 三本账中的 opt 账 | **未做**：S2 依赖 S3 的首写锁定粒度，当前仍是「一轮一体」 |
| 现场吞吐实测（S-1） | **未做**：阈值用 §2 的缺省；云机真机回标仍欠 |

---

## 2. 体积与吞吐（按 2026-10-23 之后的真实形态）

| 件 | 锚点轮 | 非锚点轮 | 说明 |
|---|---|---|---|
| weights | 394 685 B 整份 / ~390 KB 线上 | 122 892 B delta / ~123 KB | `anchor-full-rounds` |
| opt | 624 640 B 整份 / ~506 KB 线上 | ~154 KB delta | `BOD1` |
| row + perGame | ~5 KB 线上 | ~5 KB 线上 | S6 不摘逐局明细 |
| **整轮** | **~900 KB** | **~282 KB** | 1/5 锚点 |

`eval_every=5` 的每轮均值 ≈ **405 KB**。`anchors` 档只发 1/5 的权重（~180 KB/轮均），
加上每轮统计（`perGame` 保留，~24 KB/轮）≈ **203 KB/轮**。

| 档 | 每轮线上（均值） | 需要（15 s/轮） | 绝对下限（×SAFETY 1.33） |
|---|---|---|---|
| `every` | 405 KB | 27.0 KB/s | **36 000 B/s** |
| `anchors` | 203 KB | 13.5 KB/s | **19 000 B/s** |

> ⚠ **旧账不要再引用**：`896 KB/轮 ⇒ 59.7 KB/s` 是「每轮都整份」的账，已随 R1/R2 退场。
> `bulk` 的 276 852 B 只覆盖非锚点轮；`every` 档的均值必须按 1/5 锚点重算。

现场量级（用户 2026-10-23）：正常上行 >500 KB/s、晚高峰 <50 KB/s、cloudflared 会频繁断连。
正常档 `every` 余量 ~14×；50 KB/s 仍可 `every`（405/50 = 8.1 s < 15 s）；20 KB/s 必须
`anchors`。S-1 仍要量 p10/p50/p90 与每小时断连次数，用来回标 36K/19K。

---

## 3. 设计

### 3.0 两档自适应（骨架）

```
mode ∈ {every, anchors}
avail_bps = Σ(最近 8 次成功权重投递的 wire bytes) / Σ(墙钟秒)
round_period = 最近 5 轮 row.wall_sec 的中位数（缺省 15 s）
disconnects = 最近 60 s 内「建连失败 / 传输中断 / 超时 / 5xx」次数（窗口按当前时间过期）
下一拍:
  disconnects ≥ 3 ⇒ anchors（与带宽无关）
  avail 为 None ⇒ every（初始档；慢链路靠失败自然降档）
  avail ≥ need(every, period) × 4/3 ⇒ every
  否则 anchors
  连续 3 拍同向才切（SWITCH_CONFIRM），切换打一行（avail/period/disconnects）
```

四个硬约束：

1. **`anchors` 永不消失**：没有 A 层读数时按课程 `eval_every` 兜底，不退回「一轮也不发」。
2. **积压不参与降级**（用户拍板：不设上限），只做 `backlog` / `eta` 读数。
3. **断连是独立一维**，窗口必须**自己过期**——否则一次 3 连断会把整段永久钉在 `anchors`。
4. **`anchors` 档必须自带 R3**：每拍都尝试统计批，哪怕没有任何锚点待发。

### 3.1 发什么：写盘时已定，投递侧原样发

`remote/offline_deliver._post_artifact` 不做展开：
* 锚点轮的 `weights.json` / `opt.tar` 本来就是整份；
* 非锚点轮本来就是 `BWD1` / `BOD1`；
* `full_parts_for` / `_full_parts_for` 是**死代码**，两个 docstring 都钉死「别接线」。
  `wire_form` 不再需要（形态由 hub 的 `opt_form` / `landed_fp` 与账本给出）。

### 3.2 分件投递（未做，保留为后续）

`parts=weights` / `parts=opt` 需要 hub 首写锁定从「整轮」降到「按件」。当前 S2/S3 不做；
在它落地前，客户端**不要**发 `parts=weights/opt`，hub 对未知 parts 返回 400。

### 3.3 漏轮统计（R3，三档都跑）

* `gap = [it for it in pending() if it not in metrics_sent]`；
* **先于 `todo` 空早退**尝试：这一条是「抖动期统计也不能丢」的落地（评审 P0）；
* 每批 ≤100 轮且 ≤200 KB（按 `row` 实际字节；`perGame` 保留，所以实际约 4–8 轮/批）；
* `every` 档下，**这一拍就要发权重**的轮次不再单独发统计（row 随权重体走；避免同一轮
  两发、课程账本两条 `iteration`）；`anchors` 档相反——锚点轮权重可能发不出去，统计先走；
* 成功才销账；**权重体里真的带了 row** 才把该轮记进 `metrics_sent`（评审 P1）；
* 失败不销账、不进 `owed_reposts`（统计可再生）。

### 3.4 hub：`?parts=metrics` 与统计行的身份

| 项 | 落地 |
|---|---|
| 路由 | `hub/http_face._query_str("parts")` → `hub/offline_ingest` |
| 落点 | `<run>/metrics.jsonl` + 课程 `training_log.jsonl`（`_land_round_metrics`），**不创建 `it-N/`** |
| 幂等 | `(it, weights_fp)`；重复投递跳过 |
| 课程账本身份 | 统计行写 `parts="metrics"` + **`weights_landed=false`**；`_ledger_has_newer_iter` 必须忽略它 |
| 未知 parts | 400，不静默落进整轮分支 |

### 3.5 锚点兜底（只服务 `anchors` 档）

`pick_anchors(pending, eval_iters, *, with_newest, every=课程 eval_every)`：
`eval_iters` 为空时取 `it % every == 0` ∪ `pending[-1]`，打 WARN。
`every` 由 `plan_handoff` 从课程对象注入（不再吃工厂缺省 5）。

### 3.6 恢复与积压

* `pending()` 即「磁盘上有、账本里没有」⇒ 升档后自然按升序补发；
* `_push_backlog` 的继续判据改为 `pending()`（不是 `pending_anchors()`），否则 `every` 档
  每拍只前进 `sync_cap` 轮；
* 段末 `budget_sec` 从**这一拍开头**算，统计批不能重复借预算；
* `backlog` / `eta` 每拍可见，`eta > 1h` 打 WARN（不降级）。

### 3.7 兼容

* 旧 hub（无 `parts` 分支）⇒ 客户端收 404、本会话停发统计，权重照旧；
* 旧 `delivered.json` 缺 `metrics` 键 ⇒ 空表（重发一次，hub 幂等兜住）；
* 旧云机 `code.zip` 不认新 argv ⇒ 子进程起不来 ⇒ **sticky 降级到线程模式，且三项开关照传**
  （`adaptive` / `metrics_parts` / `eval_every`），不退回旧行为；
* 历史 delta 轮不回溯（`hub_delta_sweep` 是另一件事）。

---

## 4. 实施状态

| 步 | 状态 | 落点 |
|---|---|---|
| **S0 档位机** | ✅ 2026-10-24 收敛为两档；断连窗口过期；轮周期接线 | `remote/deliver_mode.py`、`offline_deliver._sync` |
| **S1 整份/原件** | ⛔ 退场（死代码） | `deliver_parts.full_parts_for` 仅保留契约测试 |
| **S2 分件投递** | ❌ 未做（依赖 S3） | 仍是一轮一体 |
| **S3 hub `?parts=`** | ◐ 只做 `metrics`；未知 parts 400 | `hub/offline_ingest.py`、`hub/store_offline.py` |
| **S4 hub metrics 分支** | ✅ + `weights_landed=false` 身份 | `store_offline.store_offline_metrics` |
| **S5 统计批** | ✅ + 不被早退饿死 + 只对真带 row 的权重销账 | `deliver_parts.DeliverMetricsMixin`、`offline_deliver._sync` |
| **S6 锚点兜底** | ✅ + 课程 `eval_every` 注入 | `deliver_wire.pick_anchors`、`plan_handoff` |
| **S7 观测** | ✅ `mode/avail/disconnects/round_period/need_bps/backlog/eta/metrics_*` | `status()`、`_log_diag` |
| **S-1 现场吞吐** | ❌ 本机测不了；阈值用 §2 缺省 | 云机实测后回标 |
| **S8 端到端限速** | ❌ 本机无 `tc`；单测覆盖判据 | D9/D10 留给真机 |

---

## 5. DoD（2026-10-24 重写）

* [x] **D1（形态）**：锚点轮 `it-N/weights.json` 是整份（首字节 `{`，非 `BWD1`）、`opt.tar`
      是整份（非 `BOD1`）；非锚点轮允许且只允许 delta；hub 账本 `opt_form` 与之一致。
* [x] **D2（漏轮统计）**：**每一个**未上行轮的 `row` 在 ≤2 拍内出现在 `<run>/metrics.jsonl`
      与课程 `training_log.jsonl`；20 轮批次 20 条都在。
* [x] **D3（体积）**：单件 body ≤8 MB；统计批按实际 row 字节 ≤200 KB（`perGame` 计入）。
* [ ] **D4（慢链路）**：5 KB/s 下统计全到岸；权重允许滞后，恢复后 ≤15 s 自动续上。
      （单测覆盖判据，真机未跑。）
* [x] **D5（断链自愈）**：新段至少每个锚点轮有整份，不依赖 hub 侧 delta 链；`hub_delta_sweep`
      对新段是 no-op。
* [x] **D6（幂等）**：同一轮统计重发 = duplicate（`(it, weights_fp)`）；不同字节不覆盖。
      `weights/opt` 的按件幂等未做（与 S2 一起）。
* [x] **D7（不变量）**：`resume_anchor` 仍只选非容器 `weights.json`；统计行走
      `weights_landed=false`，不冻结活动权重指针、不改变训练语义。
* [x] **D8**：`bash tools/githook/nn-py-safe.sh -m pytest tests/remote tests/hub -q` 绿；
      `docs/nn/remote-transport.md` 有 parts/两档/体积表。
* [ ] **D9（档位）**：`tc` 复现 **≥36 KB/s ⇒ every**、**<36 KB/s ⇒ anchors**；断连 3 次/60 s
      ⇒ anchors，且 61 s 后窗口过期、连续 3 拍可回升。单测已覆盖，限速端到端未跑。
* [ ] **D10（恢复）**：5 KB/s 跑 10 轮 → 恢复 500 KB/s ⇒ ≤3 拍升档、积压按升序补完、
      实测 ≤1.5 × `backlog × bytes_per_round / avail`。单测覆盖 drain，真机未跑。
* [x] **D11（不丢统计）**：`anchors` 档持续 10 轮期间，10 条 `row` 一条不少（含没有锚点待发的拍）。

---

## 6. 2026-10-24 评审处理

| 评审项 | 处理 |
|---|---|
| **P0-1 R1/R2 与三档失效** | 本文件 §0/§2/§3.0 重写；代码收敛为 `every`/`anchors`；`full_parts_for` 标记死代码 |
| **P0-2 断连窗口永不过期** | `ModeTracker.disconnects` 按当前时间裁剪；新增 61 s 恢复用例 |
| **P0-3 `anchors` 统计饿死** | `_sync` 先算 `gap`，`todo` 空也尝试统计批；新增 1…4 轮用例 |
| **P0-4 统计行冻结活动权重** | 统计行走 `weights_landed=false` + `parts=metrics`；`_ledger_has_newer_iter` 忽略 |
| **P1-5 积压被 `sync_cap` 截断** | `_push_backlog` 改用 `pending()` 判空；新增 6 轮/sync_cap=2 用例 |
| **P1-6 fallback 丢开关** | `deliver_proc._degrade` 构造 `OfflineDeliverer` 时照传三项 |
| **P1-7 无 row 也销统计账** | `_last_round_had_row` 门；新增指纹不符用例 |
| **P1-8 课程 `eval_every` 未注入** | `plan_handoff.make_deliverer(eval_every=course.eval_every)` |
| **P1-9 预算重复借** | `deadline` 在统计批之前算；剩余预算传给统计批 |
| **P1-10 `perGame` 体积模型错** | 统计批保留 `perGame`（与 `anchor-full-rounds` S6 一致）；§2/§3.3 按 24 KB/轮重算 |
| **P1-11 重复 `iteration` 行** | 统计行带 `weights_landed=false`，读方按身份过滤；不再声称「同一轮只有一行」 |
| **P2-12 未知 parts 静默** | `hub/offline_ingest` 对未知 parts 返回 400 |
| **P2-13 缺 DECISIONS/文档** | 见 §9；本 plan + DECISIONS 条目 + remote-transport § |
| **P2-14 S1 验收缺往返** | S1 退场，不再声称支持投递侧展开；`full_parts_for` 仅保留死代码契约测试 |

---

## 7. 风险与回退

| 风险 | 处置 |
|---|---|
| 积压无上限 ⇒ 隧道饱和 | 统计批每拍先发；打点走独立通道；`backlog`/`eta` 每拍可见，`eta>1h` WARN |
| 整份只在锚点轮 ⇒ 非锚点轮依赖 base | 每个锚点轮都是可独立装载的落点；链断最多损失 ≤`eval_every-1` 轮 |
| 统计批带 `perGame` ⇒ 批次变小 | 按 200 KB 硬上限切批；`metrics_backlog` 可见；必要时再调上限 |
| `every` 档在 20–35 KB/s 降 `anchors` | 保守但安全；`SAFETY=4/3`，S-1 回标后可调 |
| 旧 hub 404 / 旧云机 argv | 统计停发、权重照旧；子进程降级但开关照传 |
| 统计行污染课程账本 | `weights_landed=false` + `_ledger_has_newer_iter` 忽略；D7 用例 |

回退：`adaptive=False` ⇒ 恒 `anchors`（旧行为）；`metrics_parts=False` ⇒ 不发统计；
hub 的 `?parts=metrics` 是 additive（缺省整轮路径不变）。

---

## 8. 已拍板（不再讨论）

| 项 | 结论 |
|---|---|
| 积压阈值 | **不设上限**，只做读数 |
| 断连判据 | 60 s 内 ≥3 次 ⇒ `anchors`（与带宽无关），窗口自动过期 |
| 漏轮权重 | 恢复后按升序补发全部积压 |
| 轮周期 | `row.wall_sec` 最近 5 轮中位数；冷启动 15 s |
| 统计批上限 | 100 轮 / 200 KB 起步，按真 row 字节调 |
| `perGame` | 保留（补传腿是云机逐局明细的唯一实时通道） |
| 形态 | 写盘时定；投递侧不展开 |

---

## 9. 剩余 / 下一步

1. **S-1 真机吞吐**：云机 → hub 打 ≥5 次已知大小的体，量 p10/p50/p90 + 每小时断连；回标
   `EVERY_MIN_BPS` / `ANCHORS_MIN_BPS`。
2. **S2/S3 分件**：`parts=weights/opt` + 按件首写锁定；在此之前不要接客户端分件。
3. **D9/D10 限速端到端**：`tc` 两档 + 断连 20 s 一次；当前只有单测。
4. **DECISIONS**：`?parts=metrics` 契约、统计行 `weights_landed=false`、两档收敛。
5. **文档**：`docs/nn/remote-transport.md` 增「两档表 / parts 契约 / 统计行身份」。
