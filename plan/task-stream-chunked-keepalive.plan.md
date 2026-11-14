# Plan: 节点链路 keep-alive 的真实瓶颈 —— 先量归因，再修 LAN 代理跳

> **状态**：2026-11-14 评审后**重写**（原版「给同步 `/v1/task` 加 `Transfer-Encoding: chunked`」的前提
> 已被实测推翻，见 §1）。P0/P1 已落地；P2（重新测量）待现场跑。
> **来源**：用户讨论「rollout 节点与 trainer 长连接是否该改成一节点一多路复用 + 跨 it 保持」。
> 结论：**多路复用不是第一步**，甚至 chunked 也不是——先回答「连接到底为什么掉」，再谈协议。
> **非目标**：HTTP/2/自定义多路复用、跨 it 进程级连接池、任务语义/权重/调度改动（§8 缓议）。

---

## 0. 一句话目标

让「节点连接复用率」这个指标**可归因**（谁、哪一跳、什么原因掉连接），然后修掉唯一被实测钉死的
每请求重连点（LAN 三台节点被本地代理挡掉 keep-alive）；任务字节、权重、taskKey、调度语义逐字节不变。

---

## 1. 评审结论：原版的核心前提是错的（不要重做）

原版 §1.1 的根因链是「同步 `/v1/task` 没有 `Content-Length`/`Transfer-Encoding` ⇒ close-delimited ⇒
`http.client` 置 `will_close=True` ⇒ 每局重建 TCP/TLS」。**第 1 环就是假的**：

1. **Bun 1.4.4 对流式 `Response` 自己就发 chunked**（实测，不是推断）：

   ```
   $ curl -sS --http1.1 -D - -o /dev/null http://127.0.0.1:18453/     # 只给 Content-Type 的流式响应
   HTTP/1.1 200 OK
   Content-Type: application/octet-stream
   Transfer-Encoding: chunked          ← 本来就有
   ```

   Python 侧：`chunked=True will_close=False len=None`。显式再加 `'Transfer-Encoding': 'chunked'`
   头出来的响应**逐字节无差别** ⇒ 原 P1 是空操作，`SAMPLER_TASK_CHUNKED=0` 也是空操作。
   顺带：`sampler-agent.ts:2513-2516` 的注释本来就把 20s 空格写成「合法 chunk」——代码一直在
   假设这就是 chunked 帧。
2. **现有池 + 现有 agent 就已经复用**。用真 `common.http_pool.request` 打**逐字节复刻**的 sync
   `/v1/task` 形状（空格心跳 + 末尾 payload）连发三次：`{'reuse': 2, 'connect': 1, 'drop': 0}`。
   ⇒ 原版 M1（≥90% 复用）在直连路径上**改动前就已成立**，拿它当 DoD 无法验证任何改动。
3. 因此原版 §1.2 的「connect=79 ≈ 65 局 + 6 ping + 6 权重」是错误归因（§2.2 另有更硬的理由）。

**教训（写在这里防重犯）**：任何 framing 结论必须落在**线上字节**上（`curl -D -` / `resp.chunked`），
不能从源码里「没写 header」推出传输行为——Bun/Node 会自动补框架。

---

## 2. 评审期实测到的真事实

### 2.1 LAN 三台节点（a96/a97/a98）被本地代理挡掉 keep-alive —— 唯一钉死的每请求重连点

同一个 a97 的 `/v1/status`（有 `Content-Length` 的 JSON，与 framing 无关）：

| 路径 | will_close | 响应头 | 3 请求耗时 |
|---|---|---|---|
| 经 `127.0.0.1:7890` | **True** | `Connection: close` | 0.282s |
| 直连 `192.168.0.97:8443` | False | 无该头 | 0.109s |

附带发现（**本次未修**，记账在 `docs/nn/remote-transport.md` §153）：https 目标经代理时池路径用
`HTTPConnection` + `set_tunnel`（缺 TLS 包装）⇒ 代理回「plain HTTP to HTTPS port」；生产没触发是因为
控制台把 `.trycloudflare.com` 写进 `NO_PROXY`（走直连）。修它需要一条 CONNECT 代理 stub 才够测。

根因：`http_pool._proxy_for` 依赖 `urllib.request.proxy_bypass()`，而 Windows 上它读注册表、
**不认 env `NO_PROXY`**（本机 `NO_PROXY=localhost,127.0.0.1,169.254.0.0/16,192.168.0.0/16`，
operator 明确写着 LAN 不过代理；实测 `proxy_bypass('192.168.0.97') is False`）。于是 LAN 流量
被塞进代理，代理对每条响应回 `Connection: close` ⇒ 池必然 drop，**agent 侧改什么 header 都救不了**。

注意与原决定的冲突：`docs/nn/remote-transport.md` §149 定过「判据只看 host，不做 LAN
（10./172./192.168.）例外」。本 plan 的修法**不硬编码私网段**，而是「**尊重 operator 显式写在
`NO_PROXY` 里的网段（含 CIDR）**」——同一条政策的一次实现修正，已记 DECISIONS
§2026-11-14-goalnn-env-no-proxy。

### 2.2 原版 §1.2 的基线数据不可归因

- `http_pool._STATS` 是**进程级**，`stats_suffix()` 走 `take_stats()`（**读后清零**）⇒ 同一进程里
  谁先收轮谁把别人的计数拿走。
- 同一个 serve 进程（hub pid=22208）在 19:40–19:59 并发跑着 10+ 课程：
  `y26-clear 2576 / g4-special 2417 / y26-dist 2364 / o2-gq 1922 / z2-r5stuck 1882 /
  o2-gqfix 1802 / g4-mobility 1797 / ...`。所以「干净轮（无 eval 完成）」只排掉了 eval，
  没排掉别的课程的 rollout；原版 P3 的「同一课程、同一节点集合」A/B 也**无法**执行。
- `ok=n` 是**结算局数**不是请求数；`connect` 还含 ping/status/weights/rescan/竞速副本/收工中断。
- 原版 §1.3「非服务开销 = wall − elapsed」被整段归给握手也不成立（含导出子进程冷启动、
  权重下载、排队、重排）。它在 §6 反对用单次 ping 当判据——同一标准先用在它自己的表上。

### 2.3 tunnel（kaggle/dsw）这条跳是好的

同一个 dsw 的 `/v1/status` 两连发：`Connection: keep-alive`，`num_connects` 第二条为 0。
所以「远端每局新建连接」也不能归给隧道——残余 connects 的归因要靠 §2.4 的仪表，而不是猜。

### 2.4 收工/竞速本身就在制造 drop（原版没提）

`abort_scope` 关掉在飞响应 → `read()` 抛错 → `_drop_conn` + `URLError`（`URLError` 是 `OSError`，
被 `is_transient_error` 判成瞬断重排）。竞速副本/收工剪枝越多，drop 越多，且与 framing 无关
⇒ 「drop 随局数线性增长」很可能来自这里。

---

## 3. 目标与验收（DoD）

### 3.1 已落地的口径（P0 + P1）

1. **A1 可归因**：`[dist] round done` 行的池计数按 **`<course>/<kind>` 分桶**（`kind ∈
   rollout|eval|batch|weights|probe|other`），并且同一条行**同时**打 rollout 与 eval 两个桶；
   两个数字加起来才等于旧的全局数（用例钉住）。
2. **A2 掉连接有原因**：`drop` 拆成 `drop_will_close / drop_error / drop_abort / drop_redirect /
   drop_idle`，行内只列非零项。
3. **A3 LAN 不再多一跳**：`NO_PROXY` 里的 host/CIDR 一律绕开环境代理（含 `NODE_HTTP_POOL=0` 的
   urllib 回退路径，两条腿同判据）；`NODE_HTTP_POOL_IDLE_SEC` 默认 0 = 不老化。
4. **A4 字节不变**：除日志行格式外，请求 URL、header、body、taskKey、调度语义逐字节不变；
   `NODE_HTTP_POOL=0` 仍逐字节回退。
5. **A5 门禁**：`bun run pygate` 绿（ruff + mypy + pytest）；根项目 `bun run check` 不受影响。

### 3.2 待现场测（P2，判据先写死）

1. a96/a97/a98 上的 `kind=rollout` 桶：`reuse/(reuse+connect) ≥ 90%`，`connect/局 ≤ 0.6`；
2. kaggle/dsw 同口径读数 + `drop_*` 明细（目标：先看清，再决定要不要动协议）；
3. 每局 `wall` 中位数不劣化（±1s），503/URLError/retry 不增加。

### 3.3 明确不做

- 不改任务语义、taskKey、权重桶、评测评语料、难度、命数、调度/竞速策略。
- 不引入新依赖（Python 仍 stdlib-only；TS 不加包）。
- 不做 HTTP/2/多路复用、不做跨 it 进程池（§8）。

---

## 4. 已实施的改动（按仓库位置）

### P0 — 观测归因（`common/http_pool.py` + `common/distribution.py` + 三个调用方）

1. `http_pool.request(..., bucket="")`：桶 = 上层给的标签串，计数按桶存
   （`_STATS: dict[bucket, dict[key, int]]`；空桶 = 未打标，`pool_stats()/take_stats()` 不带参
   仍返回**合并全桶**视图，向后兼容）。
2. 计数键：`reuse / connect / retry / connect_ms`（握手耗时只在 miss 分支计时）+
   `drop` 与 `drop_<reason>`。
3. `stats_suffix(bucket)`：行尾标签取 kind 段（`pool[rollout] …`），drop 明细只列非零；
   `NODE_HTTP_POOL=0` ⇒ 空串（不拿 0% 误导）。
4. `distribution.pool_bucket(kind)`（唯一一处拼 `<course>/<kind>`）+ `_kind_from_url(url)`
   （`/v1/ping|status|restart` → probe · `/v1/weights|blob-sha|code-sha` → weights ·
   `/v1/task|result` 按 `mode=eval` 分 eval / rollout · 其余 other）；`_request` 用
   `bucket=pool_bucket(request_kind() or _kind_from_url(url))` 透传。
5. 用途段覆盖靠已有的 `set_request_tag(tag, kind=…)`（**不另写一行**：`trainer/dispatch.py` 与
   `trainer/batch_runner.py` 都在 1000 LOC 预算内顶格，多一行就红）：
   `worker/eval_exec.py` → `kind="eval"`，`trainer/batch_runner.py` 的 supervise/worker →
   `kind="batch"`；rollout 不需要显式设——URL 推断就是 rollout。
6. `[dist] round done` 行尾改成 `+ common.distribution.pool_round_suffix()`（rollout + eval 两段）。

### P1 — LAN 代理跳（`common/net_http.py` + `http_pool._proxy_for`）

1. `net_http.env_no_proxy(host|url)`：按 `no_proxy`/`NO_PROXY` 判定，支持 `*`、精确 host、
   `.suffix`/`*.suffix`、IPv4 CIDR（`ipaddress`，stdlib）。
2. `net_http.bypass_proxy(url)` = `is_loopback(url) or env_no_proxy(url)`；
   `urlopen` / `racing_urlopen` 两条腿统一用它 ⇒ `NODE_HTTP_POOL=0` 回退路径同一政策。
3. `http_pool._proxy_for` 先问 `bypass_proxy`，再保留 `proxy_bypass`（注册表/后缀）与原代理解析。
4. 代理判据的唯一答案仍在 `net_http`（没有第二份实现）。

### P2 — idle 老化（默认关）

`NODE_HTTP_POOL_IDLE_SEC`（默认 **0 = 关闭**）；>0 时复用前比 `last_used`（**响应读完**时才更新，
不是请求开始时——否则一局 480s 会把还热着的连接判老），超龄则 `drop_idle` 并重连。
为什么默认关：超龄常量还没有现场读数（Bun `idleTimeout=255s`、代理 per-response 关、
Cloudflare 边缘空闲窗口未知），拍一个数会把「drop 变少」变成假绿。

### P3 — 重新测量（人/下一会话做）

1. rollout 桶的 `connect/局`、`reuse 率`、`drop_*` 明细按节点分组（`byNode` 已给局数分布）。
2. 数据源：`tmp/<course>/dist-agent-meta.jsonl`、`tmp/trainer-cluster.log` 的
   `[<course>] [dist] round done`、`/v1/status`。
3. 判据见 §3.2。**只有**在 LAN 修好后 rollout 桶仍显示 `connect/局 > 0.6` 且主要为
   `drop_will_close`，才重新评估 framing/async（§8.1）。

---

## 5. 测试

### Python

1. `nn-training/tests/common/test_http_pool.py`（新增）
   - `test_kind_buckets_are_isolated`：两个 bucket 各发一次，`take_stats("a")` 只含 a；
     `pool_stats()`（合并）等于两者之和。
   - `test_drop_reason_will_close` / `test_drop_reason_error` / `test_drop_reason_abort`：
     分别命中 `Connection: close`、连接在响应头前断（`drop_error`）、`abort_scope`（`drop_abort`，
     且 `retry == 0`）。
   - `test_idle_age_eviction`：`NODE_HTTP_POOL_IDLE_SEC` 极小 → 第二次请求重连且 `drop_idle ≥ 1`；
     默认（未设）→ 正常复用。
   - `test_connect_ms_recorded`：`connect == 1` 时 `connect_ms >= 0`（只在 miss 分支计）。
2. `nn-training/tests/common/test_net_http_env_no_proxy.py`（新增）
   - `*` / 精确 host / `.suffix` / `*.suffix` / IPv4 CIDR（`192.168.0.97` ∈ `192.168.0.0/16`）
     / 不匹配（`10.0.0.1` ∉ `192.168.0.0/16`）/ URL 形态 / 大小写与尾点。
   - `bypass_proxy` 对回环仍为 True（回归 §149 的既有语义）。

### 门禁

```bash
bash tools/githook/nn-py-safe.sh -m pytest -q nn-training/tests/common/test_http_pool.py \
    nn-training/tests/common/test_net_http_env_no_proxy.py
bun run pygate
```

（`bun test`/pytest 的 worker 数 = 物理核；pytest 一律经 `nn-py-safe.sh`，禁止裸 `python -m pytest`。）

---

## 6. 硬约束 / 禁止

- **不改任务语义**：`taskKey`、`wver`、权重桶、eval 语料、难度、命数、调度/竞速全部不动。
- **不加依赖**：Python stdlib-only；TS 不新增包。
- **不碰 `Simulation`/`World`**：本 plan 只动 transport。
- **日志行是 interface**：`[dist] round done` 的既有字段位置不变，新信息只追加。
- **不做 git push**；提交用临时消息文件；不 `git add -A`/`.`。

---

## 7. 上线与观察

1. P0+P1 都不改协议，trainer 重启即可（走控制台）；agent 无需重启（没有 agent 侧改动）。
2. 观察 `[dist] round done` 的两个桶：LAN 三台的 rollout 复用率应显著上升、`drop_will_close`
   归零；kaggle/dsw 若 `drop_will_close` 仍高，才是「协议侧还有事」的证据。
3. 回退：`NODE_HTTP_POOL=0`（逐字节回退，且同样绕开 LAN 代理）；`NODE_HTTP_POOL_IDLE_SEC` 无需回退（默认关）。

---

## 8. 被否决 / 缓议（写下来防止重犯）

### 8.1 同步 `/v1/task` 加 `Transfer-Encoding: chunked`：**前提被证伪，作废**

Bun 1.4.4 本来就发 chunked，显式加头是空操作（§1）。`SAMPLER_TASK_CHUNKED` 开关与
`taskChunkedSupport` 能力位一并取消（后者只是把 Bun 的既成事实写进 ping 契约，纯噪声）。

### 8.2 HTTP/2 / 自定义多路复用：缓议

理由换了：不是「chunked 已消除每局握手」（不成立），而是「**先查清连接为什么掉**」。
额外能省的只有「38 条线程首连接 + 约 10 条竞速跨节点连接」（并行，轮墙约 0.45s），代价是
stdlib 无 h2 客户端、队头阻塞、流级取消语义、双端协议。只有 P3 显示「每局握手仍 >5% 轮墙」
且不是代理/收工造成的，才重新评估。

### 8.3 跨 it 进程级连接池：缓议

只省每轮每线程第一次连接（并行，约 0.45s/轮）；要处理 Bun 255s idle、quick tunnel 换名、
agent 重启、代理空闲回收。等 P3 数据。

### 8.4 整包缓冲 + `Content-Length`：不可行

单局最长约 480s > Bun `idleTimeout=255s`；不发 20s 心跳会被 server 回收。

### 8.5 只改 trainer 池：不够（但也不太对）

原版这句是「`http.client` 无法把 close-delimited 变成可复用」。真实情况更细：直连 + tunnel
本来就复用；LAN 之所以不复用是**代理**造成的，所以修的是「谁决定走代理」，不是池。

### 8.6 硬编码私网段绕开代理：否决

与 §149「不做 LAN 例外」冲突，且换台机器/换个 operator 就变味。改法见 §2.1：尊重 operator
写在 `NO_PROXY` 里的内容。

---

## 9. 交接清单（新 agent 从这里开始）

**先读**：本文件 §1（别重做 chunked）、§2、§4；`docs/nn/remote-transport.md` §153（本轮现场与读数）、
§152（池原始设计）；`DECISIONS.md` §2026-11-14-goalnn-dispatch-slots-and-keepalive 与
§2026-11-14-goalnn-env-no-proxy。

**代码入口**：`nn-training/common/{net_http,http_pool,distribution}.py`、
`nn-training/trainer/dispatch.py:1267`、`nn-training/worker/eval_exec.py:190`、
`nn-training/trainer/batch_runner.py`。

**不要做**：不要重做 chunked/多路复用；不要把 `[dist] round done` 的合并池数当某一门课的读数；
不要用单次 ping 的 total 时间证明收益；不要在没读 §1 之前改 `sampler-agent.ts`。

---

## 10. 一句话总结

**原来的「每局握手」结论建立在一次错误归因上：chunked 早就有了，池早就复用了；真正每请求
重连的是 LAN 三台被本地代理吞掉的连接。先把计数按课程/用途分桶、把 drop 拆成原因，再修掉
代理跳——协议层的事等数据说话。**
