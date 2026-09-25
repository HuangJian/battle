# Plan: job-identity-collision — job 身份加入课程维度（跨课程幂等键碰撞根治）

> **交付物**：本 plan。实现交给后续 agent；未写进本文件的细节以代码与 `DECISIONS.md` 为准。
> **状态**：**已实施（2026-09-24，二稿）** —— 现场事故定案 + 一次独立评审（M1–M5，见 §11）后落地；
> 实现与本文的差异只有三处，均已回写进本文（§3.3 快速闸、§5-E0 实测、§6.1 用例落点）。
> 一稿的 L2「带 `course` 参数的逐端点迁移」**降级不做**（理由 §2.2）；守卫判据由「四分量 + course_fp 不同」
> 改成「**完整新键已被别的 store 占用**」（一稿的判据会把 L1 已经修好的场景也拒掉，见 M5）。
> **触发**：用户报障「双课程并行，调度请求 409」，排查发现根因不在传输层而在 **job 身份**：
> `idempotency_key = (runId, it, init_weights_fp, data_fp)` **不含课程**，两门课发布出**同一个 `job_id`**，
> hub 的 per-job 路由取「第一个匹配」⇒ 租约/结果/账本各写一份、另一份永远 pending，
> 且**一份结果被两门课同时落位**（污染）。
> **阅读顺序**：§1.1 现场五条实测 → §1.3 红线 → §3 语义规格（先定死再写码）→ §4 落点清单 → §6 DoD。
> 行号只作定位加速，判据看函数名。

---

## 0. 一句话目标

**让「一个 job 身份只属于一个课程 store」**：幂等键纳入课程身份（`course_fp`）⇒ 不同课程永不共享 id；
归属解析从「取第一个匹配」改成「**唯一才认，歧义一律响亮拒答**」；发布端补一道「同一身份不许跨课程」的守卫。
**目标是让这类事故在发生的当秒就响亮，而不是靠两门课的 weights 逐字节相同才被发现。**

---

## 1. 背景与已定事实

### 1.1 现场实测（2026-09-24，本机取证，非推断）

**① 两门课发布了同一个 job_id。** `tmp/x20-dodge-{l1,l3}/remote-jobs/` 下三轮都成对出现：
`2bf293c29f33c561`(it1)、`d275349b46463d3f`(it2)、`8effb3be5adc8448`(it3)。

**② 两份 manifest 只差课程身份。** `d275349b46463d3f` 的 l1/l3 两份逐字段比对：

| 字段 | l1 | l3 |
|------|----|----|
| `course_fp` / `corpus_fp` | `8637ea02fa99…` / `b9d3311667bb…` | `33ad7da814dd…` / `2524b1cd8db5…` |
| `payload_sha256` | `d729126e37ce…` | `f64fc2e93f02…` |
| `reward_formula` / `formula_hash` / `course_name` / `course` | … | …（均不同） |
| **`runId` / `it` / `init_weights_fp` / `data_fp`** | `923c663c35dc3e19` / `2` / `a11a96231269` / `41f7d73f5d14` | **逐字相同** |

⇒ `job_id = sha256(runId, it, init_weights_fp, data_fp)[:16]`（`remote/protocol.py::idempotency_key`）
在这四个分量上完全碰撞。四个分量为什么全同：
- `runId`：`rl/queue.py:11` 是**进程级** `RUN_ID`，单进程多课程（`--serve` 共享 trainer）⇒ 两门课同源；
- `init_weights_fp`：两门课同一份 warm-start；
- `data_fp`：`protocol.data_fp_entries` 只哈希 `(shard 目录名, wver, stage, seed)`，**不含课程/语料身份**，
  而 per-stage seed 与课程无关；
- `it`：同一轮。

**③ 结果/租约在两份副本间交替落位**（marker 表）：

| job | `…/l1/remote-jobs/<jid>/` | `…/l3/remote-jobs/<jid>/` |
|-----|---------------------------|---------------------------|
| `2bf293…`（it1） | `result` + `claimed` | 都没有 |
| `d275…`（it2） | 都没有 | `result` + `claimed` |
| `8eff…`（it3） | `result` + `claimed` | 都没有 |

**④ 409 = `held`。** 现场 `GET /admin/queue`（快照 `tmp/hub_queue.json`）：l1 `inflight=[8eff…,
worker=jupyter-20267332-10706908:1890, heartbeat_ago=171.7s]`、l3 `inflight=[]`、两课 `pending_n=0`、
`frozen={}`、`active_workers=2`。

机理：peek 把 **l3 的那份 `8eff`** 当队首列出 → `POST /jobs/8eff/claim` 被
`_store_of(jid)`（`hub_server.py::course_of` → 第一个匹配）路由到 **l1 的那份**，它此刻
`_claimed` 尚在、租约活着、`expected_epoch` 相同 ⇒ `_claim_locked` 命中 `held`（`:655-661`）
⇒ `_post_claim` 回 409（`:2979`）。优先级视图不拦是因为 `_facts_locked(exclude_worker=自己)`
把**自己的**痕迹排除了 ⇒ 对自己看是 `highest`。
**不是** `frozen`、**不是** `stale_holder`/避让、**不是** hub 崩/隧道 —— worker 那句
「hub 异常，请检查 hub 进程与隧道」是 `_warn_non_200` 对**所有非 200** 的兜底文案，且它把响应体丢掉了。

**⑤ 比 409 严重：两门课在练同一条结果（污染）。**

```
it1/ppo_ckpt_remote.tar  l1=64D92B3258236ABF  l3=64D92B3258236ABF  ← 逐字节相同
it2/ppo_ckpt_remote.tar  l1=33359C9E69ECEFDE  l3=33359C9E69ECEFDE
it3/ppo_ckpt_remote.tar  l1=60F416489E710602  l3=60F416489E710602
活动权重 tmp/<课>/weights.json        两课同为 CB0246D5C320941D
```

污染的**完整机制**（一稿只说了一半）：`verify_and_land` 的三重校验比的是
`init_weights_fp` / `data_fp` / `commit_echo` —— **正是幂等键的那三个分量**，两边天然相等 ⇒ 静默通过。
而「两边拿到同一份结果」是因为**训练侧的读路径也走同一个首匹配**：`wait_job` → `GET /jobs/{id}/result`
→ `hub._job_dir(jid)` → `course_of(jid)` 首匹配 ⇒ **两个 trainer 轮询到的是同一份 `result.json`**，
各自落进自己的 `args.out`。D14 的 `course_fp` 也拦不住（job 与它自己的 payload 自洽）。
而且这是**自持循环**：污染让两课 `init_weights_fp` 持续相同 ⇒ 下一轮 key 又撞 ⇒ 继续撞，不会自愈。

### 1.2 现状接缝（改造落点，按函数名定位）

| 环节 | 现状 | 位置 |
|------|------|------|
| 幂等键 / job id | `(runId, it, init_weights_fp, data_fp)` → `sha256[:16]` | `remote/protocol.py::idempotency_key` / `::job_id` |
| 发布端生成 id | `m["job_id"] = make_job_id(m)`（`course_fp` 已在 `m` 里，**在 id 之前注入**） | `remote/hub_client.py::publish_job` |
| 离线合成 job | 同一条 `make_job_id`（`m` 由 hub 的 manifest 派生 ⇒ 自动继承新键） | `remote/run_loop.py::_build_iter_manifest`（`:655`） |
| 归属解析 | `course_of(jid)`：**第一个** `job_root/<jid>` 存在的课程（带 `_locate_cache` 记忆化） | `remote/hub_server.py::course_of`（`:1816`） |
| store / 目录解析 | `_store_of(jid)` = `course_of` → store；`_job_dir(jid)` 同源（找不到落 `_MISSING_ROOT` 哨兵） | 同上（`:1842` / `:2145`） |
| claim 拒绝 | `frozen` / `held` / `demoted` / `stale_holder` / `unknown` → 409（除 `demoted` 回 200） | `hub_server.py::_claim_locked`（`:615`）/ `::_post_claim`（`:2914`） |
| **委派层（全部走 `_store_of` 首匹配）** | `heartbeat` / `release` / `result_token_ok` / `store_result` / `store_job_failure` / `job_failure` / `get_result` / `mark_completed` / `append_ledger` / `lease_expires_in` / `last_heartbeat_ago` / `epoch_of` | `hub_server.py::_HubQueue`（`:2425-2506` 一带） |
| 结果落位 | `_post_result` 用 `hub._job_dir(jid)` + `accept_result(hub, jid, …)` | `hub_server.py::_post_result`（`:3819`）、`push_dispatch.accept_result`（`:116`） |
| worker 侧 | `acquire_job` 知道每条候选的 `course`（peek 给的），但 claim/result 不带（**本 plan 不改**，见 §2.2） | `remote/worker.py::acquire_job` / `::claim_job` / `::post_result` |
| 拒绝不可见 | `_warn_non_200(base, status, log)` **丢掉响应体**，文案对所有非 200 都是「hub 异常」 | `remote/worker.py::_warn_non_200`（`:660`） |
| 既有意图 | e2e 已断言「不同课程的同 it 活必须是两个 job」——但夹具 `run_id=f"e2e-{course}"` 本就不同，**这条不变量从未被真正压过** | `e2e/test_multi_course_single_hub_e2e.py::test_single_hub_dispatches_two_courses_to_one_worker` |
| 陈旧前提（要改） | `_HubQueue` docstring：「runId 是每进程随机的 ⇒ 跨课程**天然不撞**」——这句只在「一课程一进程」成立，单 hub 多课程后已不成立 | `hub_server.py::_HubQueue` docstring（`:1508-1510`） |

### 1.3 约束（红线，实现不得违反）

1. **不改训练语义**：不碰 PPO 数学、课程、命数/敌数、DoD 阈值（AGENTS §0.2）。唯一的副作用是
   `job_id` 变化（它不进任何数值路径）。
2. **幂等语义不变**：同一 `(course, 幂等键)` 重发布 ⇒ **同 id**（hub 重启重发布不产生重复 job）。
3. **向后兼容、可分批升级**：本 plan 只动协议键 + hub 侧归属解析 + 发布端守卫 + 日志文案，
   **不改任何请求/响应形状** ⇒ 旧 worker / 旧 hub 混跑无需升级顺序（§3.5）。
4. **不许静默**：身份歧义、拒绝原因一律响亮（日志 + 观测面）。
5. **不造第二份真相**：归属判据只有一处实现（`course_of`）；碰撞判据只有一处实现
   （`protocol.collision_rows` + `protocol.idempotency_key`）；不许在 handler 里各写一遍。
6. **不引入新的停腿路径**：守卫只拒「同一个身份被两门课占用」这一种情形，其余一律放行。

---

## 2. 目标 / 非目标

### 2.1 目标（可判据）

1. **id 分叉**：两门课在 `(runId, it, init_weights_fp, data_fp)` 全同、**`course_fp` 不同**时，
   `job_id` **必须不同**。（`course_fp` 也相同的两门课无法用 id 分开——那一类由目标 4 在发布端**响亮拒发**兜住。）
2. **绝不跨课程兜底**：归属解析唯一命中 ⇒ 精确路由；**≥2 门课都认识这个 jid ⇒ 一律拒答（404 `unknown`）**，
   绝不取第一个。这条同时守住**读路径**（`wait_job`/`GET /result`）与**写路径**（claim/result）。
3. **落位唯一**：结果只落在「归属唯一」的那个 job 目录；两门课的结果不可能互相落位。
4. **发布端守卫**：检测到「本 job 的**完整新键**已被**别的课程 store** 占用」⇒ **响亮拒发**
   （`HubClientError`），不写 job 目录、不记账本。
5. **原因可读**：claim/result 的任何 4xx 在 worker 日志里带 **reason**（`held`/`frozen`/`unknown`/…）与 jid；
   hub 侧对每次拒绝打一行。
6. **歧义可见**：`GET /admin/queue` 暴露 `ambiguous_jids`（同一 jid 挂在 ≥2 门课）。

### 2.2 非目标（明确不做）

- **不做 L2「带 `course` 参数的路由」（一稿的 E3/E4，本次降级）**：一稿要给它加形参的端点有 **17 个**
  （GET `payload|ts_code|code|blob|status|result|resume|bc-metrics` + POST `heartbeat|claim|start|ready|abandon|release|fail|result|epoch`），
  外加 worker 全链路、push 腿、trainer 的 `wait_job`。而 L1 落地后 id 已按 store 唯一、目标 2 的歧义拒答
  又已经**堵死唯一的静默通道**（跨课程误路由），L2 的边际收益只剩「显式意图」。
  代价（17 处半套迁移的第二事实源风险，见 §7）明显大于收益 ⇒ **不做**。
  **触发重开**：出现新的 id 碰撞源（手写 manifest / 跨 hub 搬运 job 目录 / 回滚到旧代码再前进），
  或需要「同 id 跨课程共存」的正当场景。重开时按本 plan §11-M3 的完整端点表逐条迁移。
- **不改 `data_fp` 的构成**（§8-1）：把 `corpus_fp` 纳入 `data_fp` 能顺带堵住「同局不同课程」，
  但会改所有 `data_fp` ⇒ 波及 D12 验收、`iter_expected_data_fp`、离线包与锚点，需要单独评估。
  L1 分开 **job 身份**已足够。
- **不改 `job_seed`**（§8-2）：现在两门课同 seed ⇒ 同一 minibatch 顺序。不构成 bug（数据不同），
  但改它 = 换数值 ⇒ 与"修复"分开裁。
- **不改 `runId` 的进程级语义**（`rl/queue.py`）：课程维度已由 `course_fp` 表达，不靠拆进程。
- 不做「同 `job_id` 跨课程副本自动合并/去重」这类聪明机制（那正是把两个真相焊在一起）。
- 不恢复多 hub 设计（单 hub 是唯一形态）。
- 不动 `verify_and_land` 的三重校验口径（D12 契约；补比 `payload_sha256` 属 §8-3，单列）。
- 不动 worker 的 `acquire_job`/`claim_job`/`post_result` 请求形状（随 L2 一起降级）。

---

## 3. 语义规格（先定死，再写码）

### 3.1 job 身份 = `(runId, course_fp, it, init_weights_fp, data_fp)`

- **`job_id`** 仍是 16 hex、仍是幂等键的 `sha256[:16]`，只是键的构成多一个 `course_fp`。
- **`course_fp` 是什么**：课程 jsonc 的 `sha256(字节)`，manifest 必填字段（旧 job 就有）。
  ⚠ 它不是「语料身份」（那是 `corpus_fp`），也不等于 hub 的**课程键**（`<discover-root>/<目录名>`，
  = 课程文件 stem；jsonc 内部的 `name` 只是归属标注，不进命名空间 —— `train/loop_util.py::course_key_from_path`）。
  本 plan 只用它做「课程身份」的**键分量**，不把它当路由键，也不对外暴露成 `course`。
- **稳定性依据（这是选它成立的关键前提，一稿漏了）**：课程字节在**装载时冻结**
  （`rl/config.py:1264 args.course_frozen_bytes = p.read_bytes()`），发布腿用的正是冻结字节
  （`rl/loop_steps.py:1189-1192`、`rl/cmd.py:120-138`）⇒ **同一进程内 `course_fp` 恒定**，
  mid-run 热加载编辑（哪怕只改注释）**不换 job id**、不产生孤儿。
  只有在 `course_frozen_bytes` 缺席的旁路调用方（非训练主循环）才会漂。
- **为什么不是 `course_name`**：它是 jsonc 内部的 `name`，与 hub 课程键**不是一回事**
  （`remote/worker.py:2833-2836` 既有注释），改名/移动 ⇒ 身份漂移；且旧 manifest 可能缺席。
- **为什么不是 hub 课程键（stem）**：它确实更贴路由命名空间，但需要**新增一个 manifest 字段**
  并引入第二个派生点（`Path(job_root).parent.name`），违反红线 5；且 `course_fp` 已能分开本事故的两门课。
  被否决备选记录在案，重开条件见 §2.2。
- `job_seed(runId, it, init_weights_fp)` **不动**（§2.2）。
- `data_fp` **不动**（§2.2 / §8-1）。

### 3.2 归属解析唯一化（`course_of` 是唯一判据）

```
course_of(jid) -> str | None
    收集「哪几门课的 job_root 里真有 <jid>」：
      0 门 ⇒ None（→ unknown 404）
      1 门 ⇒ 该课程名（可能是空串！单课程约定，必须与「找不到」区分开）
      ≥2 门 ⇒ None（→ unknown 404）+ 打一行「job 身份歧义」+ 进观测面
```

- `_locate_cache` 只缓存**唯一命中**；歧义/未命中**绝不**写缓存（否则一次歧义会被记成永久归属）。
- 与旧行为的唯一差别：**歧义不再取第一个**。旧行为正是事故的静默通道，故这是有意的行为变更
  （§3.5 兼容矩阵已按此更新）。
- 成本：原实现本来就是「按 `_order` 顺序 `exists()` 到第一个为止」；唯一化后**多扫剩余课程**
  （课程数 ≤ 5，一次 fs 探测/课）⇒ 可忽略。
- 歧义行按 jid 去重（同一 jid 只打一次），不刷屏。

### 3.3 发布端守卫（判据 = **完整新键**已被别的 store 占用）

```
publish_job(...)：
  1) 建 manifest（含 course_fp / course_name）
  2) 计算键与 jid
  3) 扫兄弟课程：<job_root>/../*/remote-jobs/*/manifest.json
       命中的行 = 该 manifest 的 idempotency_key == 本次的 idempotency_key
                 且 该 job 目录所属课程 != 本课（即**别的 store**）
     ⇒ HubClientError（消息带：本课 course/course_fp[:12]、冲突课、jid、以及处置建议）
  4) 无冲突才落 job 目录 + 记账本
```

- ⚠ **一稿的判据（「同四分量 且 `course_fp` 不同」）是错的**（评审 M5）：那正是**本事故的配置**，
  而 L1 之后它是**合法**的（两门课各有各的 id）。一稿的守卫会把 L1 已经修好的场景**全部拒掉**，
  与 §6.2「两门课刻意同 warm-start 各跑 3 轮必须成功」的 DoD **直接矛盾**。
  改后的判据语义只有一句：**同一个 job 身份不许被两个 store 拥有** —— 它恰好是路由的前置条件，
  且在 L1 落地后**不可能**被正常发布触发（触发即说明存在 id 碰撞源），正是哨兵该有的样子。
- **位置**：`job_id` 算完之后、**任何写盘之前**（含 `job_root/.extra_tmp` 的 mkdir）——
  故 `publish_job` 里 `tmp_extra_dir.mkdir()` 要挪到守卫之后。
- ★ **快速闸（E0 实测驱动，实施时补）**：job 身份 = `job_id`，而 **job 目录名就是 `job_id`**
  （`course_of` 的归属证据用的同一条不变量）⇒ 先按目录名筛，只有同名目录才读 manifest。
  没有它，每次发布要把兄弟课程的全部 manifest 都 `json.loads` 一遍：真语料 82 份实测
  **399ms/次**；加闸后 **14ms/次**（一次 glob + 至多 1 次读取）。
- **纯函数** `collision_rows(job_root: Path, manifest: dict) -> list[dict]` 住 `remote/protocol.py`
  （与 `d14_corpus_match` 同规：判据只有一份实现，`publish_job` 只负责调它并抛）。
  兄弟课程目录名从 `job_root.parent.name` 取（本课）；扫描根 `job_root.parent.parent`。
- **容忍旧 manifest**：命中的 manifest 缺 `course_fp` 等键时（手写/更旧）跳过该行，
  不让守卫自己 KeyError（`idempotency_key` 保持严格直取，`collision_rows` 里 try/except）。
- 扫描根不存在（离线/节点侧、非 `<root>/<课>/remote-jobs` 布局）⇒ 返回 `[]`（守卫静默放过，
  它是**best-effort 哨兵**，不承担「发现所有碰撞」的责任）。
- 成本上界 ≈ 兄弟课程数 × 每课 `remote-jobs/` 现存 job 目录数（**先量后定**，E0）。

### 3.4 观测面

- `HubQueueState`（`/admin/queue`）新增 `ambiguous_jids: {jid: [course…]}`：
  由 `queue_state()` 扫各课程 `job_root` 的子目录名求交集得出（只读观测面，不在热路径）。
  单课程（`<2` 门课）直接返回 `{}`，零开销。
- **worker**：`_warn_non_200(base_url, status, log, *, body=b"", jid="")` ——
  文案表按 status 分派：401/403 = 鉴权/封禁；**409 = 调度面拒绝（被持有/冻结/身份歧义），不是 hub 异常**；
  404 = 「job/归属不明」（含 `unknown`）；5xx = hub 异常。并把响应体里的 reason（`claim 被拒: held (…)`）
  与 jid 打进同一行。调用点：`peek_jobs` / `request_priority` / `claim_job` 的非 200 路径
  （`post_result` 的 4xx 早已把响应体带进 `ProtocolError`，不动）。
- **hub**：`_post_claim`（非 ok 且非 demoted）与 `_post_result` 的 4xx 路径各打一行
  `claim 被拒: jid=… course=… worker=… status=held reason=…`（按 `(jid,status)` 60s 节流）。

### 3.5 兼容矩阵与迁移

| hub | worker | 结果 |
|-----|--------|------|
| 新 | 旧 | 正常：请求形状没变；L1 下 id 已分开 |
| 旧 | 新 | 正常：本 plan 不改任何请求形状（只有日志文案变化） |
| 新 | 新 | 目标态 |

- **本 plan 不要求升级顺序**（与 `plan/opt-blob-diet` 不同：那条腿改了 wire 形状）。
- 唯一要注意的是**旧代码的 trainer + 新 hub** 这个窗口：旧 trainer 可能已经发布了碰撞 id 的 job，
  新 hub 的 `course_of` 对它一律 404 ⇒ `wait_job` 会把它当 `pending` 等到超时才响亮报错
  （25min，不污染）。所以迁移按 **A** 走。

**迁移（运维步骤）**
- **A（推荐）**：现场直接**换 runId 重跑**（`--serve` 重启即换）——旧 `remote-jobs/*` 由轮转/清理
  策略自然清掉；本次污染的 it1–it3 本来就不可用。**顺手清** `tmp/<课>/{it*,remote-jobs,training_log.jsonl}`
  可让 §3.4 的 `ambiguous_jids` 立刻归零（不清也能跑，只是观测面会亮着旧残影）。
- **B**：保留 runId ⇒ 旧 job 变孤儿 ⇒ 必须跑 `cancel_stale_jobs` + 清 `remote-jobs` 里非当前 id 的目录
  （否则控制台会显示一批永远等不到工人的 pending，且若与新课同 jid 会让归属歧义）。

### 3.6 不变式（写进测试）

1. 同 `(runId, it, init_weights_fp, data_fp)` + **不同 `course_fp`** ⇒ `job_id` 必须不同。
2. 同 `course` + 同键 ⇒ 同 `job_id`（幂等重发布不变）。
3. 归属唯一才认：≥2 门课都认识同一个 jid ⇒ `course_of` 返回 `None`，且**不写** `_locate_cache`。
4. 结果只能落在唯一归属的 `<course>/remote-jobs/<jid>/result`。
5. 两课各跑一轮后，两课的 `weights.json` / `ppo_ckpt_remote.tar` **必须不同**（污染的回归判据）。
6. 任何拒绝（`held`/`frozen`/`unknown`/`stale_holder`）在 worker 日志里带 reason；hub 侧有一行。
7. 旧形状（单课程、空串课程名、无 course 参数）的行为与改造前**逐字一致**（golden 对照）——
   除「歧义拒答」这一条有意的变更外。
8. 发布端：同一个完整键落在两个 store ⇒ 第二次发布抛 `HubClientError` 且**不落任何文件**。

---

## 4. 落点表（file → 改后行为）

| 文件 | 改动 |
|------|------|
| `remote/protocol.py` | `idempotency_key` 加 `course_fp`（§3.1）；新增纯函数 `collision_rows(job_root, manifest)`（§3.3 判据唯一实现）。`job_id`/`job_seed`/`data_fp_entries` 其余不动 |
| `remote/hub_client.py::publish_job` | 计算 jid 后、**写盘前**调 `collision_rows`，命中 ⇒ `HubClientError`（§3.3）；`tmp_extra_dir.mkdir()` 后移到守卫之后 |
| `remote/run_loop.py::_build_iter_manifest` | **无需改**：`m` 由 hub 的 manifest 派生、`make_job_id` 同实现 ⇒ 自动继承新键。它是节点侧（无兄弟课程目录），守卫在此无意义 |
| `remote/hub_server.py::course_of` | 唯一化（§3.2）：≥2 命中 ⇒ `None` + 一行歧义日志；`_locate_cache` 只缓存唯一命中 |
| `remote/hub_server.py::_HubQueue.__init__` | 新增歧义日志去重集（jid → 已报过） |
| `remote/hub_server.py::queue_state` | 新增 `ambiguous_jids`（§3.4） |
| `remote/hub_server.py::_HubQueue` docstring | 改掉「runId ⇒ 跨课程天然不撞」的陈旧前提（§1.2 末行） |
| `remote/hub_server.py::_post_claim` / `::_post_result` | 4xx 路径打一行拒绝日志（§3.4 hub 侧） |
| `remote/worker.py::_warn_non_200` | 签名加 `body`/`jid`，按 §3.4 文案表分派（**409 ≠ hub 异常**）；三个调用点传体与 jid |
| 控制台（dashboard） | 二期：`/admin/queue` 的 `ambiguous_jids` 非空 ⇒ 红标。本 plan 只保证字段在 hub 侧产出 |
| **不改** | `remote/worker.py` 的请求形状、`remote/push_client.py`、`remote/push_dispatch.py`、`rl/**`（§2.2） |

---

## 5. 实施步骤

### E0 — 先量成本（不留性能债）
- 真机上数一遍 `<tmp>/*/remote-jobs/*/manifest.json` 的总数与 `collision_rows` 单次扫描耗时。
  超过 50ms/次发布才需要考虑按 `(runId,it)` 建小索引。
- **实测（2026-09-24，主 worktree 真 `tmp/`：131 个目录 / 82 份 job manifest）**：
  全量 `json.loads` = **399ms/次**（manifest 含内联 `opt_init`/`course` 全文，很大）⇒ 加了 §3.3
  的**快速闸**（目录名 == `job_id`）⇒ **14ms/次**。结论：线性扫 + 快速闸即可，不建索引。
- **产出**：写进 `docs/nn/remote-transport.md §38`。

### E1 — L4 观测先行（worker `_warn_non_200` + hub 拒绝日志）
- 先做这一步：它**不改任何语义**，但让现场排障从「hub 异常」变成「claim 被拒: held」。
- 测试：worker 侧「409 时日志含 reason 与 jid」；hub 侧拒绝行断言。

### E2 — L1 幂等键加 `course_fp`（含失败测试先行，AGENTS §7）
- 先在 `tests/test_job_identity_collision.py` 写**红**用例：两个 manifest 只差 `course`/`course_fp`
  ⇒ 现在断言 id 相同（复现事故），改后断言不同；同时保留既有「同键同 id」幂等用例。
- 改 `protocol.idempotency_key`；跑全量红→绿。
- **迁移**：事故课程按 §3.5-A 换 runId 重跑。

### E3 — L1' 归属唯一化（§3.2）+ 观测面（§3.4）
- 改 `course_of`（唯一化 + 歧义日志），再加 `queue_state` 的 `ambiguous_jids`。
- 测试：手工在两课 `remote-jobs/` 各放一个同 jid 目录 ⇒ ① 空 course 的 job 作用域请求一律 404；
  ② `course_of` 返回 None；③ `queue_state()["ambiguous_jids"]` 列出它；④ 单课程（空串课程名）行为逐字不变。

### E4 — L3 发布端守卫
- `protocol.collision_rows` + `publish_job` 接入；
  测试：造「同完整键、两个 store」⇒ 第二次发布必须抛，且**不落任何文件、不记账本**；
  且「不同 `course_fp` 的同四分量」（= 本事故配置）**必须放行**（M5 的回归锚）。

### E5 — e2e 强化（把「未压过的不变量」变成真夹具）
- 改 `e2e/test_multi_course_single_hub_e2e.py`：让两门课**刻意**同 `(runId, it, init_weights_fp, data_fp)`
  （同一 `run_id`、同一假 warm-start、同一假 shard 集）**且 `course_fp` 两课不同**（夹具原来两课同 `"f"*64`，
  只改 run_id 不改它 ⇒ L1 后仍同 id、守卫还会拒发 ⇒ 用例必红），断言：① id 不同；② 各自结果只落自己课程；
  ③ 两课 `weights.json` 内容不同（假 starter 把 `course_fp` 烙进权重，让「串课」可判）；④ 无 409。

### E6 — 文档与决策
- `docs/nn/remote-transport.md` 顶部加一节（编号取该文件实施时**最大号 +1**；`§36` 已被
  「hub 的课程发现」占用、`§37` 是「缺 bun 拒单」，**故为 §38** —— 一稿写的「§36 被 opt-blob-diet 预留」已过期）；
- `DECISIONS.md` 一条：幂等键纳入课程身份 + 归属唯一化（链本次事故、M5 的判据修正、被否决备选）；
- `docs/nn.progress.md` 索引一行；`.workbuddy/memory/<日期>.md` 一行。

---

## 6. 测试与 DoD

### 6.1 测试清单（先红后绿，AGENTS §7）

| 位置 | 用例 | 压哪条不变式 |
|------|------|--------------|
| 新增 `tests/test_job_identity_collision.py` | `test_job_id_differs_across_courses_same_key_components`：两 manifest 只差 `course`/`course_fp` ⇒ id 不同 | §3.6-1 |
| 同上 | `test_old_key_formula_still_collides`：测试内**重实现旧公式**（4 分量）⇒ 断言旧公式下确实撞——把 bug 焊成回归锚 | §3.6-1 + 事故锚 |
| 同上 | `test_collision_rows_flags_only_other_store`：同完整键异 store ⇒ 命中且消息含双方 course；同 store ⇒ 不命中；不同 it/course_fp ⇒ 不命中 | §3.3 |
| 同上 | `test_publish_refuses_cross_store_identity`：同完整键、两个 job_root ⇒ 第二次抛 `HubClientError`，且**无 job 目录、无 job_pending 账本行** | §3.6-8 |
| 同上 | `test_publish_allows_same_components_different_course_fp`：本事故配置必须**放行**（M5 回归锚） | §3.6-1 |
| 同上 | `test_claim_reject_reason_is_logged`：假 `_request` 回 409 + `{"error": "claim 被拒: held (…)"}` ⇒ 日志含 `held` 与 jid，且**不含**「hub 异常」 | §3.6-6 |
| 同上（`test_job_identity_collision.py`，实施时归到这里 —— 与 L1/L3 同文件便于对读；hub 侧用例的夹具与 `test_multi_course_hub.py` 同构） | `test_ambiguous_job_id_is_refused_not_guessed`：两课各放同 jid 目录 ⇒ `course_of` 为 None、job 作用域请求 404、`_locate_cache` 未写入 | §3.6-3 |
| 同上 | `test_queue_state_reports_ambiguous_jids`：`queue_state()["ambiguous_jids"]` 列出该 jid 与两门课 | §3.4 |
| 同上 | `test_single_course_legacy_semantics_unchanged`：单课程（空串课程名）下 `course_of` 仍返回 `""`、`ambiguous_jids == {}` | §3.6-7 |
| `tests/test_remote_ppo.py` / `test_bc_protocol.py` | 既有「同键同 id」幂等用例保持绿 | §3.6-2 |
| `e2e/test_multi_course_single_hub_e2e.py` | E5 改造（刻意同四分量 + 异 course_fp） | §3.6-1/4/5 |

### 6.2 DoD（全过才收）

- [ ] 复现用例在未改代码上**确认红**，改后绿（§7 纪律）。
- [ ] 根项目：`bun run check` 绿、`bun run build` 过。
- [ ] nn 侧：`bash tools/githook/nn-py-safe.sh -m pytest tests/ -n 4`（480s 外墙钟内）全绿；
  e2e 改动按既有 e2e 跑法绿。
- [ ] **真机验收**：两门课刻意同 warm-start 各跑 3 轮 ⇒
  - `/admin/queue` 无 `ambiguous_jids`，两课 `pending_n` 正常流转，无 409 日志行；
  - 两课 `remote-jobs/` 的 job 目录集合**不相交**；
  - 两课 3 轮各 3 条 `job_completed`，各自的 `ppo_ckpt_remote.tar` / `weights.json` **两两不同**；
  - worker 日志里任何非 200 都带 reason。
- [ ] 文档三件套：remote-transport 新节（§38）+ DECISIONS 一条 + progress 索引；memory 一行。
- [ ] plan 本身保持 untracked（AGENTS §5：不得 `git add` 未跟踪 `*.md`）。
- [ ] ⚠ 环境例外（与本次改动无关）：`tests/test_githook_scripts.py::test_py_safe_wrapper_launches_pytest`
      在**本机**必红 —— 这里的 bash 是 WSL 且 interop 关着（`cannot execute binary file: Exec format error`），
      主 worktree 跑同一条命令同样报错。判据：`git diff --name-only` 不含 `tools/githook/**`。

---

## 7. 风险与回退

| 风险 | 缓解 |
|------|------|
| L1 改了所有新 job 的 id，旧 pending 变孤儿 | §3.5 迁移 A/B 二选一；首选 A（换 runId + 清 `tmp/<课>/{it*,remote-jobs,training_log.jsonl}`）——本次污染已使旧轮次无保留价值 |
| 「歧义一律 404」把旧残影变成 25min 超时（不再静默误路由） | 这是**有意**的：不污染 > 不卡。迁移 A 清残影即归零；`ambiguous_jids` + 歧义日志让现场当秒看见 |
| `course_fp` 相同的两门课被守卫**拒发**（同内容跑两份） | 响亮 + 可处置（给其中一门不同的课程文件，或换 runId 起第二条腿）；比静默互串好。若这类需求变常态 ⇒ 按 §2.2 重开 L2/换键 |
| `collision_rows` 扫描变慢（课程数 × job 目录数增长） | E0 先量；上界 = 课程数 × 每课 `remote-jobs/` 现存目录数，预期 ms 级；超阈值才建索引 |
| 守卫扫描根不对（`--remote-job-root` 自定义布局） | 它是 best-effort 哨兵：扫不到就放过（不误伤）；真正的兜底是 §3.2 的歧义拒答 |
| 回退 | PR 粒度 revert：E1（观测）可独立保留；E2–E5 一组 revert 即回到现状（不变式 7 兜底） |

---

## 8. 开放问题（不在本 plan，落地前必须登记）

1. **`data_fp` 是否纳入 `corpus_fp`**：能把「同局不同课程」在 D12 层面就撞出来（比 L1 更深），
   但会改所有 `data_fp` ⇒ 波及 D12 验收 / `iter_expected_data_fp` / 离线包 / 锚点。
   需要单独 plan + 影响面清单。**倾向：做，但不是现在。**
2. **`job_seed` 是否纳入 course**：现两门课同 seed ⇒ 同 minibatch 顺序。改 = 换数值（AGENTS §15.5 新实验口径）。
   倾向：随下一次"课程维度"大扫除一起裁。
3. **控制台红标 `ambiguous_jids`**：二期（dashboard 侧），本 plan 只保证 hub 产出字段。
4. **`verify_and_land` 是否加比 `payload_sha256`**：本次污染中它是唯一能区分两份 manifest 的字段；
   但三重校验是 D12 契约，动它要过 D12 评审。L1 落地后此问题的紧迫性大降。
5. **L2（带 `course` 参数的路由）重开条件**：见 §2.2。

---

## 9. 证据与参考

- 事故取证与机理链：`.workbuddy/memory/2026-09-24.md`（job-id 碰撞段）；现场快照 `tmp/hub_queue.json`。
- 幂等键与键分量：`remote/protocol.py::idempotency_key` / `::job_id` / `::data_fp_entries`。
- 课程字节冻结（选 `course_fp` 的依据）：`rl/config.py:1264`、`rl/cmd.py:120-138`、`rl/loop_steps.py:1189-1192`。
- 课程键 ≠ course_name：`train/loop_util.py::course_key_from_path`、`remote/worker.py:2833-2836`。
- 路由与拒绝面：`remote/hub_server.py::course_of` / `::_store_of` / `::_job_dir` / `::_claim_locked`
  （`held` 在 `:655-661`）/ `::_post_claim`（409 出口 `:2979`）/ `::_post_result` / `::_facts_locked(exclude_worker=…)`。
- 发布端：`remote/hub_client.py::publish_job`（`course_fp` 在 `make_job_id` 之前注入）；离线腿
  `remote/run_loop.py::_build_iter_manifest`；会话载体 `rl/loop_steps.py::_remote_ppo_round`。
- 进程级 runId：`rl/queue.py:11`。
- 既有意图未被压过的证据：`e2e/test_multi_course_single_hub_e2e.py::test_single_hub_dispatches_two_courses_to_one_worker`。
- 多课程测试基座：`tests/test_multi_course_hub.py`（`_HubFixture`、两课 store、`course_of` 语义用例）。
- 相关 plan：`plan/opt-blob-diet.plan.md`（共享 remote-transport 章节编号预算）。

---

## 10. 给接手 agent 的一句话

先跑 §6.1 的**红**用例复现碰撞（改前必须红），然后按 E1→E5 顺序落 L4→L1→L1'→L3→e2e，
每个 E 都过它对应的既有测试再进下一个；`course_of` 是归属判据的唯一实现，
`collision_rows` + `idempotency_key` 是碰撞判据的唯一实现——**不许在任何 handler 里写第二遍**。

---

## 11. 评审修订记录（二稿，2026-09-24）

一稿（`D:/github/battle2/plan/job-identity-collision.plan.md` 的 2026-09-24 版本）经一次独立评审，
诊断链与 L1 落点判定为**正确且最小**，但发现 5 处必须先修的问题：

| 编号 | 一稿的问题 | 二稿的处置 |
|------|-----------|-----------|
| **M1** | §3.1 把 `course` 定义成 `manifest.course_name`，而 hub 的课程键是**目录名/stem**（`loop_util.course_key_from_path` 与 `worker.py:2833` 都明说二者不是一回事）；§4 又用 `cand["course"]`（hub 键）——自相矛盾。按字面实现会把 L2 的全部请求打成 404 | 二稿不再引入 `course` 形参（L2 降级）；§3.1 明确写出三者的区别，并规定 `course_fp` **只做键分量、不当路由键** |
| **M2** | §2.1 目标 1「四分量全同 ⇒ id 必须不同」在 §3.2 的机制下不成立（`course_fp` 也相同时仍碰撞），且 §3.4 守卫对这一类不触发 ⇒ 静默 | 目标 1 改成条件式；那一类由目标 4 在发布端**响亮拒发**兜住（§3.3）；备选（hub 课程键）与重开条件记入 §2.2/§3.1 |
| **M3** | §4 的 handler 清单漏 4 个 job 作用域端点（`heartbeat`/`release`/`resume`/`bc-metrics`）与整个委派层（`result_token_ok`/`store_result`/`get_result`/…）——而心跳走错 store 会让租约过期 ⇒ 可能被冻成毒包 | 二稿的 L2 降级 ⇒ 不再需要逐端点迁移；完整端点表留档在本记录（`GET payload/ts_code/code/blob/status/result/resume/bc-metrics` + `POST heartbeat/claim/start/ready/abandon/release/fail/result/epoch`），供重开 L2 时使用 |
| **M4** | §3.2 的 ⚠「mid-run 编辑课程文件（哪怕只改注释）⇒ 换 job id」在训练主路径上**不成立**（课程字节装载时冻结），把选 `course_fp` 的前提写成了缺点 | §3.1 补「稳定性依据」段：冻结字节 ⇒ 同进程内恒定；只有旁路调用方才漂 |
| **M5** | §3.4 守卫判据「同四分量 **且 course_fp 不同**」= **本事故的配置**，而 L1 之后它是合法的 ⇒ 守卫会把 L1 修好的场景全部拒掉，与 §6.2 的 DoD（两课同 warm-start 各跑 3 轮必须成功）**直接矛盾** | 守卫判据改为「**完整新键**已被别的 store 占用」（§3.3），并加 `test_publish_allows_same_components_different_course_fp` 作回归锚 |

另修 4 处小问题：`run_loop._build_iter_manifest` 无需接守卫（节点侧无兄弟课程）；remote-transport 新节编号
应为 **§38**（§36 已被占用）；worker 侧函数名实为 `abandon_job` / `report_job_failure`；
e2e 夹具只改 `run_id` 不改 `course_fp` 会让 L1 后用例必红（E5 已写明）。
