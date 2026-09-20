# Plan: minimize-payload — 多课程并行下的 wire 瘦身与缓存协商

> **交付物**：本 plan。实现交给后续 agent；未写进本文件的细节以代码与 `DECISIONS.md` 为准。
> **触发**：AI Studio GPU worker 上网络耗时已接近 PPO 计算耗时；且训练栈已支持**多课程并行**，
> 单 worker 会**交替领取不同课程**的 PPO job，payload/result 往返与 opt/权重重复传输成为主成本。

---

## 1. 问题

### 1.1 实测墙钟（AI Studio，单 job）

| 阶段 | 字节 | 耗时 | 変率（估） |
|------|------|------|-----------|
| payload 下行 | 1,708,460 | 5.0 s | ~340 KB/s |
| PPO 计算 | — | 26.5 s（steps=24000, chunks=47） | GPU |
| result 上行 | 931,183（v2；同内容 JSON 为 1,241,371） | 14.8 s | **~63 KB/s** |

网络合计 ≈ 20 s，已接近计算 26.5 s。上行明显慢于下行，**result 是优化重心**。

> ⚠ **上表是一次抽样，不是链路常数**（2026-09-20 评审加入）：同一台 V100 / 同一 hub / 同一隧道
> 在 2026-09-20 的三次会话里实测速率差 **44×**（payload 22.7 KB/s ↔ 990 KB/s；code 13.5 KB/s，
> 而 3 秒后的 opt_init blob ≥120 KB/s）——完整表格与结论见 **§4.0**。
> **按上表均值做设计 = 优化错变量**：痛在尾部（一次坏签 100 s+，比一次 PPO 计算还贵 4 倍）。

### 1.2 字段体积扫描结论（合成路径，与线上量级一致）

工具：`nn-training/tools/remote_wire_scan.py`（真实 `save_weights_json` / `_ppo_save` /
`pack_opt_tar` / `pack_result_v2`）。PPOStudent ≈ 70k 参数。

**Result（上行，`application/x-battle-result-v2`）**

| 字段 | 线上（gzip 后） | 占比 |
|------|-----------------|------|
| `opt_tar_b64`（v2 内为裸二进制 blob） | **752,315** | **~73%** |
| `weights_json` | 280,895 | ~27% |
| JSON 头 / 魔数 | 167 | ~0 |
| **v2 合计** | **1,033,377** | 100% |

**opt tar 内部**

| 成员 | 原始 | 说明 |
|------|------|------|
| `model.pt` | 291,077 (33%) | 与 `weights_json` 语义重复（模型权重） |
| `opt.pt` | 590,451 (67%) | Adam 动量，**每课程每轮必变** |
| gzip 后 | 752,315 | torch pickle 几乎压不动 |

**Payload（下行）**：用户实测 1.7 MB 量级。slim/iter 路径下多为 code + init/opt blob +
少量 shard；PPO 在线语料 `data_fp` 每轮变，稳态下 shard 无命中空间。BC 静态语料若多轮
重复打包同一 shard 集，则 payload 级缓存收益大（见 §4.6 / M5）。

### 1.3 约束（用户拍板，实现不得违反）

1. **`opt.pt` 必须回传**，不得从 result 中删减/降质（多课程续跑依赖 Adam 动量完整性）。
2. 不得为训练方便降级任务定义（MANIFEST / AGENTS §0.2 口径不变）。
3. 协议改动须 **向后兼容**：旧 worker 无协商头 ⇒ hub 全量下发（行为与今日一致）。
4. 缓存 miss / sha 不符 ⇒ **响亮失败或回源重下**，绝不静默 warm-start / 丢动量
   （对齐 `remote/worker.py::_resolve_blob` 安全阀）。

### 1.4 非目标

- 不改 PPO 算法、课程难度、命数/敌数。
- 不把 `opt.pt` 从 result 拿掉（§1.3）。
- 不去改善 AI Studio 的**出口带宽均值**（限速是环境事实，见 `.workbuddy/memory/2026-09-18.md`）。
  ⚠ 但这**不是**把「传输质量」整体排除在外——同机同 hub 实测有 44× 双峰（§4.0），
  「一次坏签 100 s+」在 **scope 内且优先级最高**；被排除的只是「指望出口变快」。
- 本 plan **不包含**「加大单 job 步数摊薄网络」的课程侧调参（可另开 plan；那是计算:网络比，
  与本 wire/缓存方案正交）。

---

## 2. 现状：已有缓存与缺口

### 2.1 Worker 侧已具备（内容寻址，命中即跳过下载）

| 资产 | 指纹 | 缓存路径 | 跳过逻辑 |
|------|------|----------|----------|
| code.zip | `manifest.code_sha256` | `work_dir/code_cache/<sha>/` | `worker.py` 解包前存在即跳过下载 |
| TS 运行时 zip | `manifest.ts_code_sha256` | `…/ts_code_cache/` | `_ensure_ts_code` 同理 |
| opt / ref blob（slim） | `manifest.opt_sha` / `ref_sha` | `…/blob_cache/<sha>` | `_resolve_blob`：命中且 sha 校验通过则不下载 |
| 本机刚产出的 opt | sha256(raw tar) | 回传后 `_cache_blob` 写入 | 下一轮同 `opt_sha` 可直接命中 |

清理：`prune_job_dirs` **豁免** `code_cache` / `blob_cache` / `ts_code_cache`（勿误删）。

**★ 上表漏掉的一个盲区 = 2026-09-20 实测的「同一 sha 传两次」**：引导侧 `notebook_boot._pull()`
也拉**同一个** `GET /code`（同一份 `<job_root>/code.zip`，见 `hub_server._get_shared_code`），
解到 `CODE_DIR = /tmp/worker-code`；而 worker 的命中判据是 `<work_dir>/code_cache/<sha>`
（缺省 `/tmp/remote-worker`，由 `notebook_runtime.py` 经 `--out` 传入）。**两个路径互不知道
⇒ 同一 sha 在一次会话里被传两次**。现场：

```
12:49:06  [battle-rl] code.zip 就绪: 1433893 bytes sha12=5075d5eba9e3…   ← 引导 GET /code
12:49:58  [worker] code 下载中 0.25 MB / 1.37 MB …                        ← worker GET /jobs/{id}/code
12:51:30  [worker] code.zip unpacked (1433893 bytes, 157 .py files) / sha=5075d5eba9e3…
```

同机、同 sha、同大小，坏签下第二次花了 **106 s**。修法 = **M0（§5）**。
另注：引导的 `/code` 与 job 的 `code_sha256` **不保证同源**（10:41 现场：引导
`54dfe438…/1,023,787 B` vs 首 job `f4e2e404…`）⇒ 交接必须按 manifest sha 校验（§1.3-4）。

### 2.2 Manifest / 发布侧已有字段

`hub_client.publish_job` / manifest：

- `code_sha256`, `payload_sha256`, `init_weights_fp`
- slim：`opt_sha`, `ref_sha`, `opt_bytes`, `ref_bytes`
- iter/run：`ts_code_sha256`, `rollout`, `plan_sha256`（kind=run）

**结果回传**（`pack_result_v2`）：`weights_json` + `opt_tar_b64` 两 blob 必带。

### 2.3 缺口（本 plan 要补的）

1. **Poll 不携带 worker 本地 inventory**  
   `GET /jobs/next` 仅 `X-Worker-Id` + hub_scope；hub 不知道机器上已有哪些 sha。

2. **Payload 无条件全量 GET**  
   `GET /jobs/{id}/payload` 每次整包；`payload_sha256` 只做校验，不做「已有则跳过」。

3. **缺 course 级索引 —— 但今天缺的不是 opt（2026-09-20 评审纠正：勿重复造）**  
   先读纠正：`blob_cache/<sha>` 已在正确的位置上——① result 回传后立刻 `_cache_blob`；
   ② `prune_job_dirs` **豁免**它（豁免名单就 3 个目录）⇒ **「同课程连续 job」与「A 插队后
   领回 B」两种场景今天已经命中**（键是内容 sha，不是课程名）；而「同课程换 worker」必须传一次，
   协议省不掉那一次。所以「按课程索引 opt」不是真实需求。剩下的是三件（按收益排序）：
   - ① **引导 ↔ worker 的 code 交接**（**M0**，现场实测省 106 s / 1.43 MB）；
   - ② payload 内 `init_weights` 那一小块（**先量后建**，见 §4.3 门槛）；
   - ③ **`blob_cache` 没有任何上限/清理**（全仓无 quota）：它既是上面命中的功臣，也是磁盘
     风险 ⇒ 给它设上限会把命中变回重下，这是**要显式拍板的权衡**，不是风险表里一行字。
   以下旧述保留作对照（其中「被 prune」在当前代码里不会发生）：
   `blob_cache` 是全局 sha→bytes，**没有** `course_fp → {opt_sha, init_weights_fp}` 的索引，
   也不记录「本机是否仍持有该课程最近一轮权重」。多课程交替时：
   - 同课程、同 worker 连续 job：opt 可能命中 blob_cache（尚可）。
   - A 课程插队后再领回 B：**当前代码不会 prune `blob_cache`**（无 quota、无 LRU）⇒ 仍命中；
     这一条是假设风险，不是今天的缺口。
   - 同课程换 worker：对端无缓存，必须传一次（协议省不掉「执行该 job 的那台机器」的那一次）。

4. **Hub 不下发 omit / 不按 inventory 裁剪**  
   push 模式（preloaded）可能把 code/opt 整包推进 POST body；pull 模式 worker 仍可能
   在 miss 时重复下载已存在的静态段。

5. **多课程亲和缺失**  
   claim 逻辑未优先「上次碰过该 `course_fp` 的 worker」，opt/权重命中靠调度运气。

---

## 3. 目标

1. **下行**：worker 已持有且 sha 有效的资产（code / ts_code / opt / ref / 可选 payload、
   历史权重），hub **不再重复下发**；协商失败则全量，行为安全。
2. **多课程本地缓存**：worker 按 `course_fp` 保留「最近权重 + opt」的可查找副本，
   交替领取课程时优先本地命中，避免来回拉。
3. **协商可观测**：每次 poll/claim 能回答：本 job 各段是 hit / miss / omitted / downloaded。
4. **上行不变约束**：result 仍完整回传 `weights_json` + `opt_tar`（含 `opt.pt`）。
5. **兼容**：旧 worker、旧 hub、race 广播、lease/fail/release 语义不破坏。
6. **同一份字节不在一次会话内被传输两次**（含「引导已下过、worker 又下一遍」这一类；
   判定口径 = **sha 相同即重复**）。
7. **传输质量进 scope**：坏签（低速连接）有界重抽，且重抽**不**产生重复的全量传输（§4.0）。

---

## 4. 方案

### 4.0 先测速，再瘦身：传输计量与坏签重抽（**优先级最高，先做**）

**实测证据（2026-09-20，同一台 Tesla V100 / 同一 hub / 同一 cloudflared 隧道）**

| 时刻 | 端点 | 字节 | 耗时 | 速率 |
|------|------|------|------|------|
| 10:41:35→10:43:04 | payload | 2,012,020 | 88.6 s | **22.7 KB/s** |
| 11:25:01→11:25:06 | payload | 4,848,536 | 4.9 s | 990 KB/s |
| 11:26:26 | result POST | 928,857 | 3.7 s | 251 KB/s |
| 12:49:28→12:49:32 | payload | 3,454,884 | 3.9 s | 885 KB/s |
| 12:49:58→12:51:30 | code | 1,433,893 | 106 s | **13.5 KB/s** |
| 12:51:30→12:51:42 | opt_init blob | —（无进度行 ⇒ <5 s） | — | ≥120 KB/s |

最后两行是**同机同 hub 相邻 3 秒的两个请求**（慢的 code GET 之后紧跟一个快的 blob GET）⇒ 慢的
不是端点、也不是「出口限速」的均值，而是 **每条连接抽签**：`urllib` 每个请求新建连接
（`net_http.py::urlopen` → `OpenerDirector.open`，无 keep-alive 池）⇒ 云机侧 ha-connection /
边缘节点决定这条连接的质量：**好签 ≈1 MB/s、坏签 ≈13–23 KB/s**。

**结论（决定整个 plan 的重心）**：瘦身只能把坏签的 1.37 MB 变成 0.5 MB（仍要 ~37 s）；
**重抽能把它变成 ~1 s**。所以顺序是「先测量 → 再重抽 → 最后瘦身」。

**★ 现场第二例：一次「意外重抽」的自然实验（2026-09-20 12:59，实现 M1 当天采到）**

```
12:59:29  job 3c8cf83ce824aef8 claimed — downloading payload
13:00:13  payload 下载中 0.25 MB / 3.32 MB (7%)  用时 39s（6 KB/s）
13:01:12  payload 下载中 0.75 MB / 3.32 MB (22%) 用时 99s（8 KB/s）
…（持续 6–9 KB/s）
13:04:25  payload 下载中 2.50 MB / 3.32 MB (75%) 用时 292s（9 KB/s）
13:04:38  /jobs/…/payload: 瞬时失败("body 超时：总耗时 > 300s（已收 2883584 / 共 3486200）——
          链路太慢，重试") — 2s 后第 2/3 次重试
13:04:47  payload 下载中 1.75 MB / 3.32 MB (52%) 用时 5s（354 KB/s）   ← 同一份、换连接
13:04:51  payload downloaded (3486200 bytes in 321.8s)
13:04:56  code cache 命中（5075d5eba9e3…）——跳过下载解压
```

三件事一次看清：

1. **重抽是真的有用**：第一条连接 6–9 KB/s（2883584 B 烧掉整个 300 s 预算），第 2 次尝试的
   **新连接 354 KB/s**（剩下 0.6 MB 只用 5 s）——同一份 payload、同一个 hub，就差一条连接。
   那次「重试」其实就是一次**意外重抽**，代价是它由 `BODY_TOTAL_TIMEOUT_SEC` 在**300 s 之后**
   触发，而不是在首块就触发的 ~39 s ⇒ 321.8 s 里约 **280 s 是白等的**。
2. **阀值形状被现场校准**：首块（256 KB）就在 39 s 才到 ⇒ 速率 6.4 KB/s，远低于
   `WIRE_MIN_RATE=80 KB/s`，且预计剩余 ~1180 s > `WIRE_REROLL_BUDGET_SEC=20 s` ⇒
   M1 会在**首块**就断开重发，预期耗时 ~50 s（而非 321.8 s）。**这就是 M1 的验收基准。**
3. **M0 类效果已可见**：同一会话第二个 job 走 `code cache 命中`——`code_cache` 按 sha 跳过下载
   本来就成立（§2.1）；M0 补的是**引导那份也没白传**。

**做法**

1. **每段传输计量**（worker 已有进度行；hub 已有 `hub._wire` 按 job 累计 `sent_bytes`）：
   - worker：每段落 `(label, bytes, sec, rate)`（payload / code / ts_code / blob / result POST），
     收尾一行 `wire:` 摘要。
   - **引导期**（2026-09-20 收尾）：`boot` 的 code.zip 与离线盘的 task-pack 也落一行同族形状
     `wire: code.zip 1.37MB/106.0s(13KB/s) attempts=1 rerolls=0`——这两段跑在 **code.zip 之前**，
     拿不到 `remote.worker` 的计量器，所以计量与重抽一起住在 `remote/tailscale_boot.py`（§4.0.1）。
   - hub：`_bytes` 对 ≥256 KB 的 body 打一行完成行（**已有**，带字节数）⇒ 拿它与 worker 侧
     elapsed 对账即可**本地化慢腿**（hub→cloudflared 还是 cloudflared→worker）。先量后改。
   - **聚合成 p50/p90**（§7.2 的验收口径）：`nn-training/tools/wire_report.py`
     （三类行 → 逐段 p50/p90 秒、p50/worst 速率、bad%、reroll、命中数）。**速率只报
     p50 + worst**：坏签是**低尾**，报「速率 p90」量到的是好签那一端，会把 321.8 s 的坏签
     读成「一切正常」（秒数那边相反，坏签就是高尾 ⇒ p90 有意义）。
2. **坏签重抽（bounded re-roll）**：
   - 判据：收到首个数据块后实测速率 < `WIRE_MIN_RATE`（建议 80 KB/s）**且**按当前速率预计剩余
     时间 > `WIRE_REROLL_BUDGET`（建议 20 s）⇒ 主动断开、重发同一 GET。
   - 上限 `WIRE_REROLL_MAX = 3`；**只对幂等 GET**（payload / code / ts_code / blob）。
     **POST result 不重抽**——它是产物上行，走既有的 5 次退避重试，判据与目的都不同。
   - 只在**明显偏离**时触发（阈值取「本会话已知最好速率」的 1/4 量级），避免把共享隧道打得更糟。
   - 每次重抽打一行 `wire: re-roll #n（实测 13 KB/s < 80 KB/s）`，便于事后统计坏签比例。
3. **与既有停滞判据共存**（2026-09-20 已生效）：`BODY_IDLE_TIMEOUT_SEC = 45` 管「停滞」、
   `BODY_TOTAL_TIMEOUT_SEC = 300` 管「永远在滴水」，重抽管「在动但慢得离谱」。三者都**不得**
   把本地缓存当成失败退路——命中判定永远只信 manifest sha（§4.5）。
4. **A/B 方法学（方差下必须配对）**：单跑对比是噪声（§4.0 表就是同机同 hub 的 44× 双峰）。
   每组 ≥10 个 job，优先用**同一会话内的配对量**（如「同 job 的 code 段 vs blob 段」）；
   DoD 报 **p50/p90**，不报均值（§7.2）。

### 4.1 协议：poll 时上报 inventory，hub 下发 omit

**Worker → Hub（`GET /jobs/next`）**

新增请求头（建议，小写规范化由 HTTP 层处理）：

- `X-Worker-Have`: 逗号分隔的 **64 hex sha 列表**，但**只报「按用途可能命中」的**（封顶见 §4.2）：
  - `code_cache/*` 的**目录名**（= 该 sha 的代码已在本地）。★ **不要用 `_ACTIVE_CODE_SHA`**：
    那是「本进程已加载」，不是「盘上有」；扫目录名既便宜又是真实事实。
  - `ts_code_cache/*`（通常 0–1 个）。
  - `blob_cache/*` 里**本 job 的 opt_sha / ref_sha 命中与否**——这是 retry / race 副本唯一的
    有用位（同一 job 被重领时省一次重下）。
  - ❌ **不报「最近 N 个 opt/ref sha」**：opt/init 每轮必变（§1.2），历史 sha 永远不会命中，
    白白把请求头顶到 4 KB。
- ❌ **不设 `X-Worker-Have-Json` 备选**：按上面形状封顶 ≤16 个 sha（§4.2），单头永远够用——
  少一条「实现时再选」的开放问题。

可选（**仅当 §4.3 的「先量后建」门槛通过、`course_cache` 真的落地时才做**）：

- `X-Worker-Courses`: `course_fp=opt_sha,weights_fp;…` 最近 N 门课（N 建议 ≤ 16）。

**Hub → Worker（`GET /jobs/next` response，manifest 旁）**

```json
{
  "job_id": "…",
  "manifest": { …原样… },
  "lease_token": "…",
  "halt": false,
  "omit": ["code", "ts_code", "opt_blob"],
  "have": {
    "code": true,
    "ts_code": true,
    "opt_blob": true,
    "ref_blob": false,
    "payload": false,
    "init_weights": false
  }
}
```

- `omit` / `have` **只表示 hub 侧裁剪建议**；worker 仍以 manifest 中的 sha 为准做本地校验。
- 旧 hub 无此字段 ⇒ worker 按全量路径，与今日相同。
- 旧 worker 不发 Have ⇒ hub `have` 全 false / `omit` 空。

**段名约定（与实现对齐，禁止各写各的）**

| 逻辑段 | manifest / 端点 | omit 键 |
|--------|-----------------|---------|
| code | `code_sha256`，`GET /jobs/{id}/code` | `code` |
| ts_code | `ts_code_sha256`，`GET …/ts_code` | `ts_code` |
| opt blob | `opt_sha`，`GET …/blob?name=opt` 或 push `blobs.opt` | `opt_blob` |
| ref blob | `ref_sha`，`GET …/blob?name=ref` 或 push `blobs.ref` | `ref_blob` |
| payload | `payload_sha256`，`GET …/payload` | `payload`（仅当 sha 已缓存） |
| init 权重文件 | payload 内 `init_weights.json` 或独立权重缓存 | `init_weights` |

**Push 模式（preloaded / worker_server POST job）**

Hub 组装 body 时按「目标 worker 的 Have」裁剪 `code_zip` / `blobs` 等段；
manifest 中 sha **不删**，供对端校验与 miss 回源。（先复用**已有**的 `preloaded["code_zip"]`
机制，不要另造一套并行裁剪逻辑。）

**★ omit 的边际价值有限（决定实现顺序，不是否决）**：pull 路径上 code / ts_code / blob 的命中
判定**本来就全在 worker 本地**（`code_cache/<sha>` 是否存在、`_resolve_blob` 的 sha 校验），
不依赖 hub；push 路径已有 `preloaded`。所以 omit 省下的只是「一次探测 GET 的往返」⇒ 其优先级
**低于 §4.0 与 M0**（§5 的顺序与理由）。

### 4.2 Inventory 封顶与压缩

- Have 列表只带 **按用途可复用** 的 shas（§4.1）：`code_cache` 目录名 + `ts_code_cache` +
  本 job 的 opt/ref 命中位（retry / race 用）。
- 上限：建议 **≤ 16** 个 sha（≈1 KB 头）。
  ★ 旧稿写的 64 个是**错量级**：opt/init 每轮必变，报历史 sha 永不命中；而真正跨 job 可复用的
  只有 code（sha 跨轮稳定）与 ts_code，其余都属 retry/race 这一类短命需求。
- 超限策略：必带 code sha + ts_code，其余按 LRU 截断。
- 校验成本：hub 对每个 claim job 至多几次集合运算，O(1)～O(inventory)。

### 4.3 多课程本地缓存（worker）

**⚠ 门槛：先量后建（2026-09-20 评审加入）**。本节的动机在旧稿里被**高估**了：opt 的复用
**今天已经成立**（`blob_cache` 内容寻址 + 回传即写 + prune 豁免，键是 sha 不是课程 ⇒ 连续 job
与交错回来都命中；换 worker 那一次协议省不掉）。所以动工前先量一个数：

> **payload 里的 `init_weights` 占多少字节？**（`tools/remote_wire_scan.py` 有合成路径；
> 线上可从 job 的 `init_weights_fp` 对应物 + payload 解包后统计）

- 若 **< 100 KB** ⇒ **不要建 `course_cache`**：省下的字节不值得「目录 + `latest.json` + LRU +
  prune 白名单」这一整套复杂度；把力气放到 §4.0 与 M0。
- 若 **≥ 100 KB**，再按下面的设计做，且**只做能证明省下这条字节的部分**。

**问题（门槛通过后才成立）**：全局 `blob_cache/<sha>` 已能存 opt 字节，但缺少：

1. 与 result 对应的 **weights** 副本（`weights_json` 原始/gzip 字节）；
2. 课程交错时的**磁盘上限**（`course_cache` 自己的保留策略；`blob_cache` 的上限是**另一件要
   拍板的事**，见 §8）。

**设计（实现落在 worker 侧，目录仍在 `work_dir` 共享根，跨 job 存活）**

```
work_dir/
  code_cache/<code_sha>/…
  ts_code_cache/…
  blob_cache/<sha>              # 已有：opt/ref raw
  course_cache/<course_fp>/
    latest.json                 # {opt_sha, weights_fp, weights_sha256, updated_at, runId}
    opt.pt 或 <opt_sha>.tar     # 可 symlink/硬链到 blob_cache
    weights.json 或 weights.bin # 最近一轮 result 权重（或 hub 下发的 init 同源）
```

**规则**

1. **写入**：
   - job 完成回传后：将本次 `opt_tar_raw` 写入 `blob_cache`（已有）并更新
     `course_cache/<course_fp>/latest.json`；权重文件一并落（可从 `save_weights_json`
     路径直接拷贝，避免再从 hub 拉）。
   - 从 hub 下载到的 opt/init：若 manifest 含 `course_fp`，同样更新 latest（download 路径）。
2. **读取（领取 job 时）**：
   - `manifest.opt_sha` 非空：先 `blob_cache/opt_sha`，再查该 `course_fp` 的 latest
     是否同 sha；命中则 `omit` 语义上无需下载。
   - `init_weights_fp` / 权重：若 `course_cache` 有同 `weights_fp` 的文件，可跳过 payload
     内 `init_weights.json` 的重复物化（**payload 本体若仍含 shard 不能整包 skip**，
     除非 `payload_sha256` 也在 payload 缓存且校验通过）。
3. **保留**：
   - 每课程 **只保留最近 1 套**（latest）；旧 sha 依赖 `blob_cache` 自身策略——
     ⚠ 而 `blob_cache` **当前没有任何上限/清理**（全仓 grep：无 quota、无 LRU、只有写）：
     给它设上限 = 把 §2.3-3 里那些「已经命中」变回重下。这个权衡必须**显式拍板**（§8），
     不许默默加一个数字。
   - 全局 `course_cache` 按 `updated_at` LRU，默认最多 **K 门课**（建议 K=8～32，
     可配置）；超限删最旧课程目录。
   - 与 `prune_job_dirs` 豁免名单对齐：`course_cache` **不得**被 job 目录清理误删
     （若代码用白名单豁免，必须把 `course_cache` 加进去）。
4. **校验**：任何本地命中必须 `sha256(file) == manifest.*_sha`（或 `init_weights_fp`）；
   不符则删除坏文件并记 log，按 miss 处理。

### 4.4 Hub 侧 claim 与裁剪

1. `note_worker` 已登记 worker 身份；扩展为（或并列）缓存 inventory：
   - 短 TTL（建议与 poll 间隔同量级，或 claim 时读本次请求头即可，
     **避免持久化陈旧 inventory**——推荐：**每次 poll 即时计算 omit，随本 job 响应下发**，
     不把 Have 长期落盘当真理）。
2. `GET /jobs/next`：
   - 解析 Have；
   - 选中 job 后读 manifest；
   - 计算 `have`/`omit`；
   - 响应带上该字段；**lease/claim 逻辑不变**。
3. Push 路径：目标 worker 已知时用同一套 omit 裁剪 body 段。
4. **Race 广播**：多 worker 同 job 时，各 worker 各自 Have ⇒ 各自 omit；
   先回传者胜语义不变。输家已下载部分仍走本地 cache，不回滚 hub 状态。

### 4.5 与 hub 协商「缓存有效性」的完整语义

| 场景 | Hub 认为 worker 有 | Worker 实际 | 行为 |
|------|-------------------|-------------|------|
| 双方一致命中 | 有 | 有且 sha 对 | omit 下发；worker 本地 load；零字节下载 |
| Hub 误判 omit | 有 | 无/损坏 | worker 本地 miss ⇒ **仍 GET 对应端点**；sha 必须与 manifest 一致 |
| Worker 谎报 Have | 有 | 无 | 同上，回源；可对连续「声称有却 miss」打日志/指标 |
| 旧 worker | （无 Have） | — | hub 全量发 |
| 旧 hub | （无 omit） | — | worker 仍本地 cache 命中则 skip（**今日已可**） |

**有效性判定唯一权威 = manifest 中的 sha / `init_weights_fp`**。  
Have/omit 只是 **带宽优化提示**，不是正确性来源。实现与测试必须按此写。

### 4.6 Payload / 权重的额外缓存（可选第二期）

- `payload_cache/<payload_sha256>`：仅当字节已存在且 sha 相同才 skip `GET /payload`。
  - PPO 在线：命中率低。
  - BC / 重复数据集 / job 重派：命中率高。
- 权重：`course_cache`（**若过 §4.3 门槛而落地**）覆盖「最近一轮」；**历史多轮权重**若课程回滚需要，再扩展
  `course_cache/<fp>/hist/<weights_fp>`（默认不做，防磁盘膨胀）。

---

## 5. 实现步骤（顺序 = **按实测省下的秒数**排，不按新机制的漂亮程度排）

> 旧编号对应：旧 M1 → 现 M2；旧 M2 → 现 M3；旧 M3 → 现 M4；旧 M4 → 现 M5。
> 顺序理由：M0 / M1 都是**零协议改动**（或仅传输层改动），却能吃掉现场实测最大的两块
> （重复 1.43 MB / 106 s + 坏签 100 s+）；omit 协商（M3）排在后面，因为 pull 路径的命中判定
> 本来就在 worker 本地（§4.1 末）。

### M0 — 引导 ↔ worker 的 code 交接（**零协议、现场实测省 106 s / 1.43 MB**）

**现状（2026-09-20 取证）**：引导 `notebook_boot._pull()` 拉 `GET /code` 解到
`CODE_DIR = /tmp/worker-code`；worker 的命中判据是 `<work_dir>/code_cache/<sha>`（`--out` 缺省
`/tmp/remote-worker`）。两个路径互不知道 ⇒ **同一 sha 在一次会话里被传两次**（§2.1 盲区）。

1. `remote/notebook_boot.py`：拉到的 raw zip 留在已知路径（如 `/tmp/worker-code.zip`），并把
   路径与**它已算出的 sha**（现在只为打日志算）交给 `notebook_runtime`（cfg 加 `code_zip_path`）。
2. `remote/notebook_runtime.py`：透传给 worker（新增 CLI 参数，如 `--boot-code-zip`）。
3. `remote/worker.py`：code cache **miss 时先试这个本地文件**——
   `sha256(zip) == manifest["code_sha256"]` ⇒ 本地解包进 `code_cache/<sha>/`（与下载路径**同一套**
   原子改名逻辑）；不等 ⇒ 记一行 log 后照旧 `download_code`（10:41 现场就是这种情况：引导
   `54dfe438…/1,023,787 B` vs 首 job `f4e2e404…`）。
4. **不许**因命中而跳过 `_ACTIVE_CODE_SHA` 热替换护栏（§8）。
5. 单测：同 sha ⇒ 零 `download_code` 调用；sha 不等 ⇒ 回源；本地文件损坏 ⇒ 忽略并删除后回源。
6. DoD 项：「同一 sha 在一次会话内不得被传输两次（含引导→worker 交接）」（§7.2）。

### M1 — 传输计量 + 坏签重抽（§4.0 的实现）——**已实现（2026-09-20）**

实现面（`nn-training/`）：

| 件 | 位置 | 说明 |
|----|------|------|
| 常量与阀值 | `remote/worker.py` 头部 `WIRE_*` | `WIRE_MIN_RATE=80KB/s`、`WIRE_PROBE_BYTES=128KB`、`WIRE_PROBE_SEC=3s`、`WIRE_REROLL_BUDGET_SEC=20s`、`WIRE_REROLL_MAX=3`、`WIRE_RATE_SAMPLE_MIN_BYTES=256KB` |
| 判据（纯函数） | `_reroll_decision(got,total,elapsed)→(要不要, 实测速率, 预计剩余)` | 阈值只有一处实现；单测直接钉它 |
| 会话最好速率 | `_note_rate` / `_min_rate` | 阈值 = `max(80KB/s, 最好速率/4)`（只在**明显偏离**时动手；小 body 不采样） |
| 断连信号 | `WireSlowError(bytes, rate, remain)` | **带正文**的异常（同裸 `TimeoutError()` 的教训） |
| 重抽点 | `_read_body(..., allow_reroll)` | **只在首块判一次**（`probed`）⇒ 每次浪费 ≤ 一块（256KB），**不可能退化成整份重传** |
| 重抽循环 | `_get_with_retry(..., reroll=True)` | 不退避（重抽的价值就在快）；日志 `wire: re-roll #n/3 …`；**最后一次尝试永不可重抽**（慢链路不会变成「永远下不完」） |
| 传输账 | `_wire_add/_wire_hit/_wire_note_reroll/_wire_flush` | 每 job 一行 `job X: wire payload=3.32MB/321.8s(10KB/s) code=cache-hit result=0.89MB/3.7s(241KB/s) reroll=1(wasted 0.25MB) 合计=…`；未 flush 的 job 封顶 4（防 push 模式无界增长） |
| flush 点 | pull：job 循环 `finally`；push：`worker_server` 后台作业 `finally` | 两条路径都有一行账 |
| 不重抽面 | `post_result`（POST result） | 只记账不算重抽（它是产物上行，走既有 5 次退避） |

单测：`nn-training/tests/test_wire_reroll.py`（判据/上限/不退避/产物不重抽/账行形状）。

**★ 收尾（2026-09-20，同一批）——**原表只盖了 worker 侧四段 GET，三个口子已堵：

| 口子 | 为什么漏 | 落法 |
|------|---------|------|
| 引导期 code.zip / task-pack | 跑在 **code.zip 之前**，`remote.worker` 还不存在（鸡生蛋） | `remote/tailscale_boot.py::fetch_guarded`（§4.0.1），`notebook_boot._pull` 与 `offline_boot.fetch_task_pack` 改走它 |
| `wire` 只有原文、没有聚合 | 验收口径是 p50/p90 + 每组 ≥10 job，人眼扫不出来 | `nn-training/tools/wire_report.py`（三类行 → 逐段 p50/p90 秒 · p50/worst 速率 · bad% · reroll · 命中） |
| ts_code 缓存命中不进账 | 只漏了这一处（code/blob 都记了） | `_ensure_ts_code` 命中即 `_wire_hit(jid, "ts_code")`——否则「本段没走网络」在账上看不见 |

单测：`nn-training/tests/test_boot_wire_guard.py`（判据/停滞正文/超预算/有界重抽/最后一次硬传/
两处接线）· `nn-training/tests/test_wire_report.py`（现场原文解析/最近秩/命中不采样/重抽不重复
计数/CLI）。DoD 项：坏签场景 p50/p90 有记录（§7.2）→ 现在可复算。

### 4.0.1 引导期大 body 护栏（与 worker 侧**孪生**，`remote/tailscale_boot.py::fetch_guarded`）

引导期有两个**大 body** 幂等 GET，且都发生在 **code.zip 之前**：hub `/code` 的 code.zip
（实测最坏 1.43 MB / 106 s / 13.5 KB/s）与离线盘的 `task-pack`（几 MB～几十 MB）。那时
`remote.worker` 的停滞/低速重抽根本不存在——而 code.zip 正是被下载的那个东西。

- **住哪**：`remote/tailscale_boot.py`。三个 notebook（训练 cell / 连接体检 / 离线盘）都从
  GitHub raw 拉它，与既有 `resolve_hub_url` 同一条「单源一份，两边不会漂」的理由；新开一个
  `remote/boot_wire.py` 就要同时改三份 notebook 的拉取清单，而 notebook 是**要重发**的交付物。
- **形状**：分块读（256 KB）+ 进度行 + 停滞 `BOOT_IDLE_TIMEOUT_SEC=45s` + 调用方给的墙钟预算
  （code.zip 300 s）+ **首块判一次**的低速重抽（`max(80KB/s, 本会话最好速率/4)`、预计剩余
  >20 s、≤3 次、**最后一次必然硬传**）；`HTTPError` 原样上抛（401/404 是确定性的答案，不是抖动）。
- **与 worker 侧的关系**：**孪生实现**（阈值同值、同一条「首块判一次」纪律）。不能抽共享件：
  引导侧要能 import 的只有它自己与 `tailscale_boot`，而 `worker.py` 在那时还没解包。改一边要
  同步另一边（两侧单测各自钉住同一组数字）。
- **账**：成功即落 `wire: code.zip 1.37MB/106.0s(13KB/s) attempts=N rerolls=M`——与 worker 的
  `job X: wire …` 同族形状，`tools/wire_report.py` 一份解析器吃两类行。
- **已知边界（有意不堵）**：cell 的**内联回退**（GitHub raw 拉不到时的最小 pull 路径）仍是朴素
  整读。往 cell 里复刻护栏等于把 80 行塞进 notebook，与「cell 只留 CFG / 凭据 / 保活」的既有
  口径正面冲突；代价写明在此，真嫌疼时再议。

### M2 — Worker inventory + 本地 course_cache（**先过 §4.3 门槛再动工**）

1. 实现 `course_cache` 目录、latest.json、写入点（result 回传后 + blob 下载后）。
2. `prune` 豁免名单加入 `course_cache`。
3. `_resolve_blob` / code / ts_code：命中路径打统一 log：`hit cache blob=… sha=…`。
4. `poll_job`：组装 `X-Worker-Have`（形状与封顶见 §4.1 / §4.2：**code 目录名 + ts_code +
   本 job 的 opt/ref 命中位**；course 级字段**仅当 course_cache 真的落地**）。
5. 单测：
   - 同 sha 二次 job 不触发 download（可 mock `_request`）。
   - 多课程交错：A→B→A，A 的 **weights** 仍本地命中（★ 别写「opt 命中」：那一条**今天就已经
     成立**、测不出本里程碑的东西——见 §7.2 那条被删除的假验收）。
   - 坏文件 sha 不符 ⇒ 回源且删除坏缓存。

### M3 — Hub omit 协商（**优先级低于 M0/M1/M2**：pull 路径命中判定本就在 worker 本地）

1. `/jobs/next` 解析 Have，response 增加 `have`/`omit`。
2. Worker 读 omit：仅作 log/指标；**下载逻辑仍以本地 sha 查询为准**（避免双真相）。
   - 可选严格模式：omit 命中时跳过探测 GET（信任本地）；miss 仍 GET。
3. Push 模式按 Have 裁剪 preloaded 段。
4. 兼容测试：无头/空 omit ⇒ 字节级行为与改前一致（回归：旧 golden / 协议测试）。

### M4 — 观测面的剩余部分 + 调度亲和（可选）

1. Hub/worker 日志：每 job 一行 `wire: payload=… code=hit opt=miss weights=hit omit=[…]`。
2. Console/仪表（dashboard 若已有节点页）可后续接；本 plan 不强制 dashboard 改动。
3. **课程亲和**（独立开关）：claim 时若多个 open job，优先匹配该 worker
   `X-Worker-Courses` 里出现的 `course_fp`；**不得**破坏 P3b 独占与 race 语义
   （亲和只在可自由选择时打破平局）。

### M5 — Payload 缓存（**暂缓**：在线 PPO 命中率≈0，本 plan 自己承认；BC/重派场景真出现再做）

1. `payload_cache/<sha>` + skip download；BC/重派场景验证。
2. 磁盘配额与 LRU。

---

## 6. 涉及代码位置（实现入口）

| 区域 | 文件 | 备注 |
|------|------|------|
| Poll / Have 头 | `nn-training/remote/worker.py` `poll_job` | 已有 WORKER_ID / HUB_SCOPE |
| 本地 cache 解析 | `worker.py` `_resolve_blob` / code 段 / `_ensure_ts_code` | 命中语义 |
| **引导 code 交接（M0）** | `remote/notebook_boot.py` `_pull` · `notebook_runtime.py` · `worker.py` code 段 | 零协议；按 manifest sha 校验 |
| **传输计量 / 坏签重抽（M1）** | `worker.py` `_request` / `_read_body`（已有停滞判据）· `hub_server.py` `_bytes` | 只动传输层 |
| course_cache 写入 | `worker.py` result 组装与 `_cache_blob` 附近 | **过 §4.3 门槛后** |
| prune 豁免 | `worker.py` `prune_job_dirs` | 仅当 `course_cache` 落地 |
| Hub next | `nn-training/remote/hub_server.py` `_get_next` | omit 计算与响应 |
| Blob 端点 | `hub_server.py` `_get_blob` | 不变；miss 照旧 |
| 发布 manifest 字段 | `hub_client.py` `publish_job` | 已有 opt_sha 等；一般不必加字段 |
| 线格式 | `protocol.py` `pack_result_v2` / `pack_job_v2` | result **不减** opt；job push 可裁段 |
| 扫描/诊断 | `tools/remote_wire_scan.py` | 改前后对比体积 |

**方向红线**：`src/` 游戏核不依赖 `remote/`；本 plan 只动 `nn-training/remote/**`、
必要时 `nn-training/tools/**` 与测试。`dashboard/` 非必须。

---

## 7. 测试与 DoD

### 7.1 测试

- 单测（`nn-training/tests/`，镜像 concern）：
  - **同一 sha 一次会话内不传两次**：引导侧已有该 zip ⇒ worker 零 `download_code`（M0）；
    引导 sha ≠ manifest sha ⇒ 回源（M0）；
  - **坏签重抽**：低速响应 ⇒ 有界重抽（≤3）后成功且**不产生重复全量传输**；好签不重抽；
    POST result 不重抽（M1）；
  - inventory 封顶（≤16）与排序稳定性；
  - omit 计算：有/无 Have、旧 worker；
  - course_cache LRU 与 latest 更新（**仅当 §4.3 门槛通过**）；
  - sha 不符回源（含**本地交接文件** sha 不符）；
  - 协议兼容：无 omit 字段的 response 旧 worker 可跑。
- 若改 hub/worker 集成：按仓库惯例 `bash tools/githook/nn-py-safe.sh` 跑 pytest，
  **禁止**裸 `python -m pytest`（AGENTS §0.1）。

### 7.2 Definition of Done

- [ ] **同一 sha 在一次会话内不得被传输两次**（含引导→worker 交接）：一次干净会话的 `wire:`
      摘要里每个 sha 只出现一次 download。
- [ ] **坏签有界重抽**：低速（< `WIRE_MIN_RATE`）时 ≤3 次重抽内可完成；重抽不产生重复的全量
      传输；日志有 `wire: re-roll` 行可统计坏签比例。
- [ ] **报 p50/p90，不报均值**；每组 ≥10 job（§4.0-4）。
- [ ] 旧 worker ↔ 新 hub、新 worker ↔ 旧 hub：任务可完成，无协议硬失败。
- [ ] result 仍含完整 `weights_json` + `opt_tar`（含 `opt.pt`）；scan 工具可复核。
- [ ] miss 路径：删掉本地 cache（**含删掉引导交接文件**）后仍能从 hub 全量装回并跑通。
- [ ] `prune` 不清理 `code_cache` / `blob_cache` / `ts_code_cache`（+ 落地后的 `course_cache`）。
- [ ] Race：双 worker 不因 omit/Have/重抽产生双写或 hub 结果错乱（先回传者胜不变）。
- [ ] 相关 pytest 门禁绿；不在 Simulation 热路径引入逻辑（本 plan 不碰 `src/game/`）。
- [ ] 若有非显然协议决策 → 记入 `DECISIONS.md`（§6.3 门槛：否决项/再犯/不可就近注释）。
- [ ] ~~多课程交错：A→B→A 第二次 A 的 `opt_sha` 命中本地~~ —— **删除这条**：2026-09-20 复核
      确认今天就已命中（`blob_cache` 内容寻址 + prune 豁免），拿它当验收会得到「改前也绿」的假绿。

### 7.3 验证方法（开发自测）

1. 本地 loopback hub + 两门假课程，连续发布 job，统计 download 次数应随命中下降。
2. `tools/remote_wire_scan.py` 对改前后 result 体积：上行不应变大。
3. AI Studio 实机：看 `wire:` 日志，对比优化前后「payload/POST 耗时 / PPO 耗时」。
   ⚠ **方差下必须配对**：单跑对比是噪声（同机同 hub 实测 44× 双峰，§4.0）⇒ 每组 ≥10 job，
   优先用同一会话内的配对量（同 job 的 code 段 vs blob 段），报 p50/p90。
4. `wire: re-roll` 行数 = **坏签比例的直接观测**（改前恒为 0——那时没有重抽）。

---

## 8. 风险与开放问题

| 风险 | 缓解 |
|------|------|
| Have 谎报 / 缓存损坏 | 一律以 manifest sha 校验；miss 回源 |
| **坏签重抽把共享隧道打得更糟** | 只在明显偏离（阈值 = 本会话最好速率的 1/4 量级）时触发；上限 3 次；POST result 不重抽 |
| **给 `blob_cache` 设上限 ⇒ 把命中变回重下** | **显式拍板**（要数字：上限多少、命中率损失多少），不许默默加 |
| **引导 sha ≠ job sha** | 交接按 manifest sha 校验；不等即回源（10:41 现场实例） |
| **方差下用单跑 A/B 得出结论** | 每组 ≥10 job + 配对 + p50/p90（§4.0-4） |
| course_cache 磁盘膨胀 | 每课仅 latest + 全局 LRU K；**先过 §4.3 门槛**；`blob_cache` 见上 |
| Header 过长被代理截断 | 封顶 **≤16** sha（≈1 KB）；必要时改 body 字段 poll |
| 多课程 code 变更热替换 | 保留 `_ACTIVE_CODE_SHA` 护栏：code sha 变必须自重启，不因 cache 命中而跳过 |
| 亲和调度饿死其它课 | 亲和只打破平局；仍保证各 open job 可被非亲和 worker 领取 |
| 与竞速/P3b 独占交互 | omit 不参与 lease；测试覆盖 race |

**开放问题（实现时按 MANIFEST→DECISIONS→代码一致性推导，必要时再记 DECISIONS）**

1. Have 用请求头还是 `/jobs/next` POST body？（默认头；按 §4.2 封顶 ≤16 sha 后**单头永远够用**，
   这条基本已关闭）
2. `init_weights` 与 payload 内文件的关系：skip payload 是否允许仅当 **无 shard**（iter）
   且 weights 已在 course_cache？
3. Hub 是否要持久化 worker inventory 供 console 展示？（M4 可选）
4. `WIRE_MIN_RATE` / `WIRE_REROLL_BUDGET` / `WIRE_REROLL_MAX` 的具体取值（先按 §4.0-2 的
   80 KB/s · 20 s · 3 次，用实机 `wire:` 数据校准）
5. **`blob_cache` 上限**（拍板项，见 §8 与 §2.3-3）

---

## 9. 证据与参考（仓内）

- 字段体积扫描：`nn-training/tools/remote_wire_scan.py`
- Result v2 / Job v2：`nn-training/remote/protocol.py`（`pack_result_v2` / `pack_job_v2`）
- Blob 安全阀：`nn-training/remote/worker.py` `_resolve_blob`
- 发布 slim 字段：`nn-training/remote/hub_client.py` `publish_job`
- AI Studio 环境与限速：`.workbuddy/memory/2026-09-18.md`
  （**注意**：它解释的是出口**均值**，不解释 §4.0 的 44× 双峰 ⇒ 不要拿它否掉传输质量这条线）
- **现场证据（2026-09-20 会话日志，本 plan §2.1 盲区 + §4.0 表的来源）**：引导重复下载同 sha
  code.zip（1,433,893 B / sha12=`5075d5eba9e3`）· 同机同 hub 的 44× 双峰速率 ·
  引导 `/code` 与 job `code_sha256` 不同源的实例（10:41 `54dfe438…` vs `f4e2e404…`）
- 多课程/竞速背景：`remote/worker.py` `poll_job` 注释、`hub_server.py` `_get_next`

---

## 10. 一句话给接手 agent

> **正确性只信 manifest 里的 sha；Have/omit 只为少传字节。**  
> **先做 M0（引导↔worker 交接：同一 sha 不传两次）与 M1（测速 + 坏签重抽）**：这两件吃掉现场
> 实测最大的两块（重复 1.43 MB / 106 s；坏签 100 s+），而且**零协议改动**。  
> 再谈 omit 协商（pull 路径命中判定本就在 worker 本地，边际价值只剩一次探测往返）；
> `course_cache` **先过 §4.3「init_weights 占多少字节」的门槛**再决定建不建。  
> **result 必须继续完整回传 opt.pt。**
