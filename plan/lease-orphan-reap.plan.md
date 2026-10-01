# Plan: lease-orphan-reap — 孤儿租约早收（hub-only，不动 worker）

> **交付物**：本 plan。实现交给后续 agent；未写进本文件的细节以代码与 `DECISIONS.md` 为准。
> **状态**：待实施（2026-09-28 根因定案，用户指令“从根源修掉”）。
> **触发**：bc-human-retrial job `460b637b` 实测——worker 首次 acquire 读超时，
> hub 侧租约已设、worker 侧 token 随响应一起丢，还不了租约，job 在 peek 里隐身整整
> 一个 `CLAIM_TTL_SEC=300`（16:09:39 → 16:14:12），V100 干烧 5 分钟。

## 0. 一句话目标与诚实账

认领是单程：租约设立（hub 侧，claim POST 到达即设）与 token 送达（响应读回）不在一个
原子里，中间读超时必产**孤儿租约**（hub 有主、世上无人持有 token）。本次修掉它：
claim 后无声一段即提前回池，不等满 300s TTL。

**诚实账**：触发条件苛刻（慢 tunnel ＋ 超时恰好落在认领读上，一年几次）；单次收益
约 2~8 分钟 GPU 空闲 ＋ 消灭一种误导性日志形状（“no job yet”而队列非空）。代价是
hub 调度语义的一次加法（纯 server 侧，worker 零改动——云机旧码照跑）。立项理由不是
省机时，是**队列诚实**：peek 看不见的 job 不该存在。

**非目标**：不改认领协议（无两阶段 claim，旧 worker 兼容）；不放宽 H2（无 blind-abandon，
开放 hub 上等于允许别人 abandon 你的 job）；不碰 tunnel/传输调参（让路/重抽已覆盖慢读本身）。

## 1. 根因（定案，不重证）

- `remote/job_lifecycle.py::claim_job`：`lease_token` 装在 claim 响应 body 里；
  hub 侧（`hub/schedule.py::_claim_locked`）POST 到达即设 owner+expiry。
  读超时 ⇒ hub 有租约、worker 无 token（`acquire failed: The read operation timed out`）。
- 还租两条路（`abandon`/`release`）都要出示 token（H2，`schedule.py` 注释行“须与原租者一致”）；
  心跳也要 token。孤儿租约在 hub 看来与“正常下载中的租约”**不可区分**——直到 TTL 跑完。
- 反证已排除：payload 早已静态备好（publish 时确定性构建，tar.xz 61 shards→1.72MB），
  code.sha 对得上（worker 自检通过），worker 版本门放行（第二次认领即成功开训）。
  慢的只是 tunnel（~240KB/s＋让路），放大的全是租约。

## 2. 语义规格（先定死再写码）

- **新规则（唯一一条）**：exclusive 模式租约，若 `now - claimed_at > ORPHAN_GRACE_SEC`
  且期间**零心跳、零 `/start`、零下载进度、零结果/epoch 回传** ⇒ hub 自动释放回池，
  并记账本事件 `lease-orphan-reaped {jid, silent_sec}`（可审计；控制台读账本即见，
  不另开端点）。
- `ORPHAN_GRACE_SEC = 180`（定值，不做旋钮）：心跳周期 60s ⇒ 活 worker 到 180s 时至少
  心跳过 2 次——心跳是 tiny POST，60s 级 tunnel 停顿都盖得住；连心跳都发不出的 worker
  事实上已死，早收正确。backup 模式（无租约）永不触发；hub 重启丢租约既有语义不变。
- **H2 不动**：不新增端点，不放宽鉴权；早收走 hub 内部既有 release 路径（同 TTL 过期回池
  的同一函数，只是提前触发）。
- **worker 零改动**：旧码云机照跑（它们甚至感知不到这条规则——早收只发生在它们已经
  失联的情况下）。

## 3. 被否决的备选（三问存档，免重犯）

| 备选 | 否决理由 |
|---|---|
| (a) 认领下载两阶段（意向锁短 TTL＋就绪转正） | 两端都动；云机旧码不认新语义就全退役一次，不值得为一年几次的事故付全网升级税 |
| (d) blind-abandon（凭 worker_id 放宽 H2） | 安全口径：开放 hub 上任何人可 abandon 别人的 job＝可 DoS；私有 tunnel 可议但本仓库按开放 hub 设计 |
| (b) 过期回池后优先重领（治标） | 治的是发现延迟，不是隐身本身；peek 在 TTL 内根本看不见它，省不掉 5 分钟 |

## 4. 分阶段

- P0：`_JobStore` 加早收判定（fake 时钟可测）＋ `lease-orphan-reaped` 账本事件；
  单测进 `nn-training/tests/hub/test_hub_leases.py`（与 P3b 同文件，§5 有现成 fake 时钟）。
- P1：DECISIONS 索引入账（传输语义加法，按 HOW-TO-ADD 三问：被否决备选 ✓ 会重犯 ✓
  不能就近表达 ✓）；`bun run check` 绿；hub-server 经控制台重启生效（本地组件，
  worker 不动）。

## 5. 测试 / DoD

- `test_claim_silence_past_grace_reaps_and_logs`：claim 后推进 179s 无声 ⇒ 仍租住；
  181s ⇒ 回池 ＋ `lease-orphan-reaped` 事件（fake 时钟，禁睡真 180s）。
- `test_heartbeat_once_never_reaped`：60s 处一次心跳 ⇒ 1000s 仍租住（慢下载不误杀）。
- `test_backup_never_reaped` ＋ H2 回归（既有矩阵全绿）。
- DoD：上列全绿 ＋ `bun run check` 绿 ＋ DECISIONS 条目 ＋ 无 worker 侧改动
  （`git diff --stat` 自证 remote/worker* 零行）。

## 6. 验收（真机，不烧训练）

- 人工复现：开任意小课 → 发布后 kill -9 worker 进程（模拟读超时孤儿）→ hub 日志
  `lease-orphan-reaped` 应在 ~180s 出现（而非 300s），peek 恢复可见。worker 侧无需任何操作。
