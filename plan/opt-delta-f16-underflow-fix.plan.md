# opt delta-f16 下溢修复 + y21-temper 重开 plan（2026-10-17，评审修订 v2）

> **起因**：y21-temper 于 it11 出现 KL 2.5432 尖峰（it13 触发 F4 熔断）——根因定案：跨机 opt 传输的
> `delta-f16` 把 `exp_avg_sq` 的小增量下溢抹零 ⇒ 权威 opt 出现 **v=0 且 m≠0**（真 Adam 不可能态）⇒
> 该特征零梯度轮每步 `lr·(0.9^k·m)/(√0+ε)`，96 步几何和 ≈ 1.35e5·m ⇒ 单参弹跳
> （实测 max −32.6，q=−32767）。根因全文 + 全部证据 → `docs/nn/experiments.md` §115；
> 决策 → `DECISIONS.md` §2026-10-17-goalnn-opt-delta-f16-underflow。
>
> 本 plan 只做两件事：**①opt 装载链下溢消毒（§7：先红测试）②重开 `y21-temper2`**。
> 传输格式（`BOD1` / `delta-f16`）、Adam 超参、历史 landed 件、y20 判决、在跑三腿已有数据**一律不动**。
> 验收口径链 `plan/mvp.md` §10（能跑 · 无 TS/运行期错 · 60 FPS 不涉 · 可重启 · 无隐藏状态）。
>
> **评审修订 v2（2026-10-17）**：本版已吸收评审的 3 个阻断项与测试/论证修正 ——
> ① temper2 必须同时换 `out/traj/backup_dir/backup_prefix`（不能只改课程名/头注）；
> ② P0 不是“立即生效”：集群代码快照按会话冻结，必须重启当前 snapshot anchor（本次 = hub）才生效；
> ③ 装载点枚举改为“枚举 + 分类”（`_ppo_load` 与两处 delta base 都要列）；
> ④ 健康态上界改为 7.27（不是 3.16），下限仍有 1.9× 余量；
> ⑤ T1 固定 `step≈960` 并用真 `torch.optim.Adam`，另加一条传输下溢断言；
> ⑥ T3 抽 `_load_opt_state` helper + AST 接线守卫（不驱动 400+ 行 `run_training_core`）；
> ⑦ “次轮归零”改为“权重单点跳≤2 / KL 不弹”；P1 增加可选计数日志；⑧ 门禁路径按仓根/`nn-training` 修正。

---

## 1. 根因一屏（引 §115，不重复全文）

| # | 事实 | 出处 |
|---|---|---|
| 1 | 回传 opt 走 `delta-f16`：逐元素 `(new−base).to(float16)`，无缩放；\|Δ\| < ~2.98e-8 归零（5.96e-8 是最小次正规，半值为舍入门） | `remote/opt_delta.py:173` |
| 2 | 罕见弱特征参数 g ≲ 1.7e-4 ⇒ v 增量（g² ~ 1e-8）永远下溢、m 增量（~1e-4）存活 ⇒ 权威 v 恒 0 而 m 累积 | §115 |
| 3 | 该特征整轮零梯度时步长 = `lr·(0.9^k·m)/(√0+ε)`；96 步和 ≈ `9.0·lr·m/ε` | §115 |
| 4 | 数值闭合：`1.5e-4·2.415e-4/1e-8·9.0 = 32.6` = 实测 it10→11 stem 单点跳（idx 6622=(40,15,2,1)） | §115 |

- 铁证：it11 入机 `blob.opt` 的 `v=0` 索引集 ≡ 权重跳变索引集；y20-wall it210 同弹（idx 9941
  m=1.18e-5 ⇒ 预测 1.59 vs 实测 1.239）；y20 三腿 stem 单点跳>2 各 28/49/20/20 轮（跨代全中）。
- 以前没炸的余量：v 恰落在 f16 **次正规地板 5.96e-8** 时 `√v≈2.44e-4 ≫ ε` ⇒ 自动拆弹；**v=0（精确零）才是弹**。
  （v2 修正：原稿写 `√v≈7.7e-6` 是 `sqrt(5.9e-11)`，不是 `sqrt(5.96e-8)`。）
- 反向排除：退火无关（it11 时 wHurt 只降 6%）；非退火三腿同病；`value_anchor` 只碰 value 切片。

## 2. 修复设计

### 2.1 消毒规则（唯一实现，`remote/opt_delta.py`）

```
exp_avg_sq ← max(exp_avg_sq, (|exp_avg| / K)²)        K = ADAM_SQ_FLOOR_K = 10
```

推导（写进代码注释）：真 Adam 的 `|m̂|/√v̂` 上界**不是**单步尖峰的 3.16；对 β1=0.9、β2=0.999 的原始动量，
Cauchy-Schwarz 给 `|m|/√v ≤ (1−β1)/√(1−β2) / √(1−β1²/β2) = 7.27` ⇒ 健康态 `v ≥ m²/52.8`；
触发域 `v < m²/100` 距最坏健康态还有 **1.9×** 余量 ⇒ 对健康态按位 no-op（不是原稿说的 10×）。
单步尖峰的 3.16 只是这条界的一个特例。触发时单步 ≤ K·lr = 1.5e-3、96 步轮和 ≈ 90·lr = 0.0135
（m/v 同步衰减；实测 0.0107）vs 原 32.6。只抬 v、不动 m（m 是量化后的真值、v 是丢的那一半）
· 幂等 · 不碰 `step` / `param_groups` · 非 Adam 节点（缺 `exp_avg`、形状不符、非 tensor）一律跳过；
amsgrad 的 `max_exp_avg_sq` 目前不在用（plain Adam），helper 显式跳过整节点并注明，避免 half-fix。

新增 `sanitize_adam_sq(state) -> int`（`remote/` L0 叶子；纯张量方法；返回值 = **被抬高的元素数**，
不是参数组数；`__all__` 加名；模块 docstring 加“下溢消毒”一节）。接受可选的 `log` 由
`apply_opt_delta` 传入（见 P1）。

**不消毒基座**：`train_core.py:737`（回传 Δ 的 base）与 `land_opt_delta` 的 base 必须保持原样 ——
两侧 base 逐字相同是 Δ 语义前提；消毒只发生在「装入活优化器」与「写出新权威」两处。
P0 只改 `torch.load` 返回的内存副本，**不回写** `opt_dir/opt.pt`；737 重新从盘上 load，天然是原样。

### 2.2 两个落点

| 落点 | 位置 | 生效时机 | 说明 |
|---|---|---|---|
| **P0** worker 装载口 | `remote/train_core.py` 的 restore 段（约 430 行）：抽 `_load_opt_state(path)` = `torch.load` + `sanitize_adam_sq`，再 `load_state_dict`；count>0 时 `prep_b` 记一行 | **必须等集群代码快照重打**（重启当前 snapshot anchor；本次 anchor = hub；`tmp/.code-snapshot/snapshot.json` 的 `anchor.kind` / `pid` 是判据）| 所有 job（云/本机/离线）唯一装载路；快照按会话冻结，不是 per-job 重打 —— 原稿“立即 / hub 不用先重启”是错的 |
| **P1** 重建口 | `remote/opt_delta.py apply_opt_delta`：`:286 out = rebuild(base)` 之后、`return` 之前 post-pass，可选 `log` 计数 | 随同一份代码快照；hub 重启后生效 | 盘上权威件从「worker 真态」变「worker 真态+消毒」。`apply_opt_delta` 也被 `opt_standard._landed_bytes_for` 的**读侧落地**调用 ⇒ 本机缓存/离线读侧同样会消毒，不只是 hub。P1 是纵深防御（P0 的 delta 以未消毒 base 算，消毒增量会随 delta 落到 hub）；保留理由 = 盘上权威件 + 读侧重建两个真实消费者 |
| **P1 日志** | `apply_opt_delta` 新增 keyword-only `log=None`；`rebuilt_opt_tar` 透传；`land_opt_delta` / `opt_standard._landed_bytes_for` 传入既有 `log` | 同上 | 离线腿的 `opt_raw` 是 BOD1 容器 ⇒ 读侧 P1 先消毒，P0 再看到就是 0；P1 必须有计数行，否则 §5 的“三腿出现消毒行”在离线腿看不到 |

## 3. 测试（§7：先在未改码上确认红）

- **T1a**（红→绿）`tests/remote/test_opt_delta_sanitize.py::test_underflow_poison_is_neutralized`
  —— base=new={m=2.415e-4, v=0, step≈960} 走 `pack_opt_delta → apply_opt_delta` 往返，把结果装进
  真 `torch.optim.Adam`，跑 96 步零梯度：现码 `Σ|Δw| ≈ 32.601` ⇒ 断言 ≤0.05 **红**；P1 后 ≈0.0107 **绿**。
  **必须固定 step≈960**（it10 量级）：`step=0` 时 bias correction 把现码红值抬到 98.119，
  与 §5 的“报告落 32.6”对不上。不要手写 Adam 公式，用真 optimizer。
- **T1b**（传输下溢）`test_f16_transport_loses_the_v_increment` —— base v=0、new v≈2.9e-11：
  解析 `pack_opt_delta` 容器的 `exp_avg_sq` 项，断言 f16 payload 为 `b"\x00\x00"`；再接 `apply_opt_delta`
  断言 v 被抬到 `m²/100`。这条钉的是根因（传输抹零），T1a 钉的是消毒器。
- **T2**（helper 单测）幂等 · 最坏健康态 `|m|/√v≈7.27`（v=m²/52.8）按位 no-op · 边界（略低于 m²/100 ⇒
  恰抬到 m²/100）· 异常节点/形状不符/非 tensor 跳过不抛 · **元素计数**（2 元素里 1 个被抬 ⇒ 返回 1）·
  amsgrad 节点显式跳过。
- **T3**（P0 装载路）`train_core._load_opt_state`：构造 v=0 & m≠0 的 `opt.pt`，断言返回 v 抬底 + count=1，
  且**盘上那份仍是 v=0**（不消毒基座）；另加 AST 守卫：`run_training_core` 确实调用 `_load_opt_state`
  （不驱动 400+ 行全链 —— 仓库既有口径：全链要真 torch + 真课程，不在单测里跑）。
- **P1 日志**：`apply_opt_delta(..., log=lines.append)` 在 v=0 时 `len(lines) == 1`（只断“响没响”，
  不断措辞 —— 源码文本断言预算不抬）。
- **地毯**：`tests/remote/{test_opt_delta,test_opt_delta_land,test_opt_standard,test_delta_files,test_train_core_split}.py`
  全绿 —— P1 会改 `apply_opt_delta` 输出：既有断言里的 `exp_avg_sq` 都在 f16 容差内（已核对
  `test_opt_delta` 的 6e-4、`test_opt_standard` 的 1e-3），无需改语义；若真出现“零 v + 非零 m”的
  精确断言，按新语义更新并注明 DECISIONS 号。

## 4. 明确不做（划界）

- **不改传输格式**（i16 缩放 / log 域 / 逐张量 scale）：数据面大改，且弹源已被消毒闭合（理由备查记 DECISIONS）。
- **不动 Adam eps**（1e-8 保持；上调到 ~1e-5 是备选保险，先不做）。
- 不回改历史 landed opt 件 · 不重放已死 temper 的 it11–13 · 不动 y20 判决与在跑三腿已有数据。
- 不顺带把 tmp 诊断脚本转正/清理（另开任务）。

## 5. 门禁与验收

- [ ] **T1 未改码先红**（只跑 `-k test_underflow_poison_is_neutralized`，报告落 **32.601**）→ 改码后绿（≤0.05）。
- [ ] `bash tools/githook/nn-py-safe.sh -m pytest -q nn-training/tests/remote/test_opt_delta_sanitize.py nn-training/tests/remote/test_opt_delta.py nn-training/tests/remote/test_opt_delta_land.py nn-training/tests/remote/test_opt_standard.py nn-training/tests/remote/test_delta_files.py nn-training/tests/remote/test_train_core_split.py -x`（从**仓根**跑；workdir=nn-training 时脚本路径应为 `../tools/githook/nn-py-safe.sh`）绿。
- [ ] **装载点枚举 + 分类核验**：`rg` 全仓 `torch.load(.*opt|load_state_dict` 命中
  `train_core.py` 的 restore/打包 base、`worker/ppo/common.py:231`（`_ppo_load`：本 job 自己写出的已消毒 checkpoint，无需第三处）、
  `remote/opt_standard.py:241` / `remote/opt_delta.py:407`（两处 delta base，必须保持原样）+ 测试夹具；无未分类的第三条消费路。
- [ ] `bun run check` 绿（根项目例行确认）；改过 `opt_delta.py`/`train_core.py` 后跑 `bun run build` 不涉 TS，按根门禁口径记录。
- [ ] **快照/重启**：改码先提交（或干净 worktree）；重启当前 snapshot anchor（本次 `anchor.kind=hub`, pid 见 `tmp/.code-snapshot/snapshot.json`）让快照重打。
  ⚠ 快照打整棵工作区：`trainer/dispatch.py`/`queue_local.py` 的在途 race-copy 改动会一起发给三腿 —— 先提交/隔离，或在 §116 记录这次续跑同时换了 dispatch 行为。
- [ ] **生效实证 A**（重启后）：三腿下一轮 job 日志出现 P0 或 P1 消毒计数行（存量毒首现）；
  后续**不要求“次轮归零”**（下限随 m 走，v 增量 ~1e-11 远低于 f16 门，可能周期性重复），
  判据改为“stem 单点跳≤2 / KL 无 >0.075 弹跳”。
- [ ] **生效实证 B**：temper2 头 20 轮 stem 单点跳 ≤2（`tmp/y20-scan-jumps.mjs` 口径扫 `nn-training/weights/y21-temper2/`）。
- [ ] 记录落盘：`docs/nn/experiments.md` 新 §116（修复 + 重开规格 + 首 20 轮实证，未跑前标 pending）·
  `DECISIONS.md` 新条目（含 P1 语义、消毒永久改变受影响参数动力、同 seed 配对复现理由）· memory append。

## 6. 执行顺序

1. **T1 红**（现码只跑 `-k test_underflow_poison_is_neutralized`；值 32.601 落报告）。
2. **helper + P0**（`opt_delta.sanitize_adam_sq` + `train_core._load_opt_state` + restore 段日志）→ T2/T3 绿。
3. **P1**（`apply_opt_delta` post-pass + 可选 log）→ 地毯绿。
4. `bun run check` + `bun run build`（不涉 TS，按门禁口径）+ 装载点枚举 + 全量 `nn-training/tests/`（或与改动相关的子集，超时如实报告）。
5. 提交（临时消息文件；只提交本 plan 点名的文件，不碰 `trainer/dispatch.py`/`queue_local.py`）。
6. （人）重启当前 snapshot anchor（本次 hub；跨两轮之间；重启丢 volatile 登记表、磁盘为事实源 D8），确认快照 sha 更新。
7. 复制 `nn-training/curricula/y21-temper.jsonc` → `y21-temper2.jsonc`：**改课程名、头注与四个路径字段**
   （`name` / `out` / `traj` / `backup_dir` / `backup_prefix` 全部 `y21-temper2`；头注写：重开原因 =
   opt delta-f16 下溢 bug 已修复；规格/seed/起点逐字相同；前次现场 → `tmp/y21-temper/` 熔断 it11 + §115 指针）；
   seed 20270205、起点 sha16 `1a7821240d3c47b9`、参数/门模板零改动；过 jsonc 门（`pytest -k jsonc`）。
   ⚠ 不能只改课程名/头注：`out/traj` 复用会覆盖 `tmp/y21-temper/` 的取证现场。
8. （人）**控制台开课** y21-temper2。
9. 监控（§5 实证 A/B）与记录（§7）。

## 7. 待记 DECISIONS（执行时补，不预先写）

- **temper2 重开**：§15.5 新实验 = 新课程文件（新 out/traj/backup）；规格/seed/起点逐字继承 temper；
  前次熔断现场引 §115。同 seed 20270205 与 §15.1“每轮换语料”的字面冲突要写明：这是**修 bug 后的配对复现**，
  不是闭环调参的语料复用（评估→更新环不拿这份语料选点）。
- **P1 语义注记**：landed opt 字节从「worker 真态」变「真态+消毒」——`opt_sha` 链自 hub 重启轮起换语义
  （链内自洽，跨重启段的 sha 对账口径要照实注）；读侧 `rebuilt_opt_tar` 同样受益/受影响。
- **消毒不是恢复**：受影响参数的 v 被钉在 `m²/100`，步长上限 K·lr（健康 Adam 的 10×），负增量也下溢 ⇒
  v 只升不降（棘轮）；temper2 与 temper 是“同课程 + 修传输”的对比，不是逐位复制。
- **快照/复现**：提交后才能从 commit 复现快照；记录本次快照 sha 与重启时间。

## 8. 评审修订对照（v2）

| 评审项 | v2 处理 |
|---|---|
| 课程文件只改 name/头注会复用 temper 现场 | §6 step 7 改为五个字段（name + out/traj/backup_dir/backup_prefix） |
| P0 不是立即生效 | §2.2 / §5 / §6 改为“重启 snapshot anchor 后生效”；记录当前 anchor 判据 |
| 装载点枚举错误 | §5 改为枚举 + 分类（`_ppo_load`、两处 delta base、测试夹具） |
| 健康态上界 3.16 → 7.27 | §2.1 重写推导；保留 K=10（10>7.27），余量 1.9×；T2 测 7.27 |
| P1 只管 hub / 无日志 | §2.2 写明读侧作用域；P1 可选 log；离线腿靠 P1 行可见 |
| “次轮归零”不成立 | §5 判据改为单点跳/KL；日志只作观测 |
| 消毒永久改变动力 | §7 新增“消毒不是恢复”条目 |
| T1 数字依赖 step / 没复现传输下溢 | T1a 固定 step≈960 + 真 Adam；新增 T1b 传输断言 |
| T3 不可驱动全链 | 抽 `_load_opt_state` + AST 守卫；helper 单测 |
| 源码文本断言预算 | P1 日志只断 `len(lines)==1`，不断措辞；T3 用 AST |
| 门禁路径 | §5 从仓根跑，注明 workdir 差异；T1 红跑只选单测 |
| 同 seed vs §15.1 | §7 写明配对复现理由 |
| 其他数值 | §1 修正 f16 门 ~2.98e-8、√v=2.44e-4；“it11 尖峰、it13 熔断” |
