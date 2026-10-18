# y22 阶段 B′ —— demo 只训 move 头（fire 头不做 BC）· R2

> 本文件只给规格，**不产生课程腿**；开课 = 人在控制台点。
> 前情：`plan/y22-phaseB-demo-data.plan.md` 的 S4 判负。**R2（2026-10-18）**按评审重写：
> 撤回「干净单变量」与「move_head 必须不同」两条错误断言；把熵/背题/熔断/DoD 落到可执行口径。
> 数据侧（v5 语料 / `demo_bank.npz` sha `c7d41c60…` / 9.37MB）**已经可用，不需要再导**。

## 0. 一句话

**fire 的 demo 监督被 mask+类别不平衡压成「几乎只教 release」；move 的 5 类在语料里全部合法。
所以把 demo BC 的 fire 项删掉，只留 move 项——这是一次「点火项是不是毒性来源」的机制消融，
不是「move BC 已证明能涨胜率」的结论。**

两条必须写在前面的更正：

- **不要拿 `held-out 0.7975` 当干净 held-out。** 它来自 S4a 的 32 局子样本、NNPolicy，且
  `best_state` 按同一份 val 的 `move+fire` 选点后再报同一份 val（选择偏差）；只能当上界估计。
  正式开腿前要用 StudentNet + fixed final epoch（或 nested split）重测并给 CI。
- **「与 §33 同 coef」≠「同剂量」。** §33 的 CE 是 move+fire 相加；删掉 fire 后总 BC 梯度更小。
  同 coef 只用于**匹配消融**，不能拿 `demo_bc` 绝对值与 §33 比。

## 1. 为什么是 move（数字与口径）

| 事实 | 数 | 口径/出处 |
|---|---|---|
| move 掩码全合法 | **101153/101153** 样本都是 5 类可动 | `human-x20-corpus-v5` 实测 |
| fire 可训练子集 | **43744** 样本（mask 两类都合法） | 同上 |
| 其中 hold | **806 = 1.84%** | `806 / 43744` |
| hold 标签总数 | **29254**（全样本 28.92%） | 同上 |
| hold 在自身 mask 里非法 | **28448 / 29254 = 97.2%** | 与 `_masked_ce` 单合法类跳过语义一致 |
| 只剩 release 的样本 | **57409 = 56.8%**，整样本被跳过 | 同上 |

⇒ fire CE 在可训练子集里 **98.16% 是 release**，实际效果是「教别按」，不是「教何时按」。
**806 是决策样本，不是发数**；不要再写「806/9900」这种跨单位比值（评审 P1）。
人类开火量另算（遥测口径 12.38 实弹/千拍），与本条的 806 不是同一单位。

move 的可学性证据只能这样写：

- S4a（32 局、NNPolicy、10 epoch）move top-1 **0.7975**、多数类 0.3216、STOP recall 0.7197；
  但它是 **val 选点后的乐观值**，不是 StudentNet、也不是全 160 局。
- 因此本腿的假设是：**move 的监督在 mask 层面是干净的**；它能否在 PPO 分布上转化为行为，
  由本腿的读数回答，不能由 0.7975 预先回答。

## 2. 用户口径与数据对一遍

你说「开火还行，缺预判开火和火线对枪，差的是走位」。逐条核（NN 口径统一用
`stack2.it200` 的 **137 胜局**，人类 160 局；不要再混用「全部 NN 局」表）：

| 你的说法 | 数据 | 判 |
|---|---|---|
| 差的是**走位** | 停占比 **0.133 vs 人类 0.288**（2.17×）、威胁 lane **34.9 vs 84.5**（2.42×）、贴脸 **110.4 vs 68.3**（0.62×）、包围 130.5 vs 88.2、低血时长 196.9 vs 102.4 | **强支持**。人类是「站到线位上、少贴脸、低血时间只有一半」 |
| 开火「还行」 | 实弹/千拍 **7.70 vs 12.38**（少 38%）；命中率 0.833 vs 0.789（更准） | **半支持**：效率不差、**量少**。量由 C 阶段处理，不在本条腿上 |
| 缺**火线对枪** | 人类 threat lane 2.42× 而火线承伤同档（7.3 vs 7.4）⇒ 人类确实在线上对枪且不吃亏 | 支持；但它是**走位+开火**的联合行为，move BC 只能补前半 |

⇒ 结论：**这条腿补「站位形态」，不补「开火量」。** 两者分开记，别混成一个胜负读数。

「人类停着开火」是联合量：人类 `P(stop)=0.288`、`P(stop∧fire)=0.166`（**57.6%**）；
NN 的 `stop`/`fireHeld` 是两个边际量，**eval 行没有 `stopFireTicks` 联合列**。
所以本腿的空停哨兵必须按 §5 的口径做，不能只拿两个边际量各看各的。

## 3. 唯一的技术改动 + 正确的回归测试

`nn-training/worker/ppo/engine.py` 的 demo 项现在是：

```python
demo_bc_mean = _demo_masked_ce(_dm, _dact[:, 0], _dmm[:, :MOVE_DIM]) \
             + _demo_masked_ce(_df, _dact[:, 1], _dmm[:, MOVE_DIM:MOVE_DIM+FIRE_DIM])
```

**改成只留第一项**，并把理由写在 `_demo_masked_ce` 的 docstring 与调用点：

- fire 的 mask 语义是「当前打得出去吗」，label 是「有没有按住」；冷却期两者互斥。
- `43744` 个可训练 fire 样本里 hold 只有 `806`（1.84%），28448/29254（97.2%）的 hold
  落在单合法类样本里被整样本跳过 ⇒ fire CE 实际只教 release。
- 恢复方式：在调用点加回 `_df` / `_dact[:, 1]` 的第二项 CE（一行，见 git 历史）。

**不做成课程键**（不加 `demo_heads` 之类）：那会为一个已知坏项新建一套机制。
**全局删，反悔成本是一行 diff。** 这条决策记入 `DECISIONS.md`。

### 3.1 测试（修正评审 P0）

**错误写法（不要用）**：把 bank 的 fire 标签全翻成 1，断言 `fire_head` 与基线逐位相同、
而 `move_head` 必须不同。删掉 fire CE 后 `_dact[:,1]` 不再被读，两次运行**逐位相同**——
`move_head` 也不会变；若「基线」指初始权重，`fire_head` 又会被 PPO 更新，也不相同。
两种解释都必红。实测：翻转后 `all params identical: True`、`move_head identical: True`。

**正确写法**（加到 `nn-training/tests/worker/test_ppo_demo_mix.py`）：

1. 保存 `PPOStudent` 初始 `state_dict`；
2. 原 bank 跑一次；fire 标签全翻 1 再跑一次（同 seed、同 `demo_bc_coef`/`demo_per_mb`）；
3. 断言**全部参数**逐位相同（证明 fire 标签没进 loss；若有人把 fire CE 加回来，这条会红）；
4. 断言 `move_head.weight` 与**初始权重**不同（证明更新确实跑了）。
5. 可选灵敏度：旧代码（带 fire CE）+ 翻标签，断言 fire head 不同。

现有 5 条测试必须继续全绿，尤其 `test_demo_off_is_bitwise_pure_ppo`。

## 4. 课程/对照设计（不再冒充 §33 单变量）

### 4.1 为什么不能叫「干净单变量」

| 项 | §33 `x20-demo-mix` | 本腿 |
|---|---|---|
| 语料 | 61 shards / 38564 | 160 shards / 101153 |
| 起点 | `x20-noexplore.it105` | 阶段 A 胜者 |
| 轮数 | 300 | 先 30–40，机制过线才续 |
| `target_transitions` | 49152 | 随 A 腿 |
| 种子 | 1789876303 | 20270206 |
| schema | v3（30 scalars，无 extra） | v5（40 scalars，37 extra；extra 对 y22 是 gate-only） |

至少 5 个变量同时不同；历史 §33 **不是匹配对照**。本腿只能写成「新腿」，
不能写「锅精确落在 fire 头上」。

### 4.2 匹配两臂（必须）

要在同一份数据/起点/种子/轮数上隔离 fire 项，需要两条臂：

- **C 臂（control，fire CE on）**：从 **父提交的 worktree** 跑（`git worktree add --detach
  tmp/wt-fire-ce <父提交>`），课程/数据/起点/种子/轮数逐字相同；这是唯一代码差异
  （只差 engine 的 fire CE 那一项）。**不 merge、不 push 这个 worktree。**
- **M 臂（move-only，fire CE off）**：当前提交跑，其余逐字相同。

两臂都先 **30–40 轮**；只有 C/M 的 `demo_bc` 形状、entropy、首杀、承伤、timeout 都读出
「M 不差于 C」才续到 150（cap 250）。**不要一上来跑 150 轮**（评审 P1：§33 的毒性 it10
就出现，150 轮只多烧算力）。

### 4.3 参数

| 项 | 值 | 说明 |
|---|---|---|
| `demo_bank` | `nn-training/data/human-x20-corpus-v5/demo_bank.npz` | sha `c7d41c60…`，9.37MB；两臂同 bank |
| `demo_bc_coef` | **0.02**（匹配消融首档） | 只用于 C/M 同条件对比；**不是**「同剂量」 |
| `demo_per_mb` | **128** | 与 §33 同值，不引入第二个未知 |
| 起点 | 阶段 A 胜者（规则见 §4.4） | 两臂逐字相同 |
| 轮数 | **30–40 首段** → 150（cap 250） | 机制读数过线才续 |
| 生产档（若 M 有信号） | `demo_bc_coef=0.005` 起 | §33 的 0.02 已判毒性；生产不复用消融剂量 |

### 4.4 起点规则（消掉「itN」歧义）

1. 阶段 A 四条腿里，取 Φ 斜率 ≥ 0 且 Φ 最高者；
2. A 无胜者 ⇒ `y22-null` 的**最后一个已落盘 checkpoint**；
3. 再退 ⇒ `stack2.it200`（sha16 `1a7821240d3c47b9`）。
   两臂必须同起点；开跑前在**同一 200 局 anchor** 上测 it0，后面全部读数相对 it0，
   不再拿 stack2 的数字冒充本腿起点。

## 5. 门与读数（先测 it0，再谈目标）

主读数（每 25 轮，同一 200 局 anchor、同种子配对；报告给均值 + 配对 CI）：

| 读数 | 人类参照 | 本腿口径 |
|---|---|---|
| **威胁 lane / 千拍**（主） | 84.5 | 相对 it0 ↑；目标 ≥ it0 + 15%（不是硬编码 55） |
| 贴脸加权 / 千拍 | 68.3 | 相对 it0 ↓ |
| 停占比 | 0.288 | ↑，但必须过空停哨兵 |
| 低血时长 / 千拍 | 102.4 | 相对 it0 ↓ |
| 承伤 / 千拍 | 22.3 | ≤ it0 |
| 局长 | 4971 | ↓（仅参照，不作主门） |

副门（全部相对 it0，不用 stack2 的 0.685/16.5 冒充）：`win ≥ it0`、`kills ≥ it0`、
`timeout ≤ 5%`、`kill/shot ≥ 0.25`（`playerShots>0` 才判）。分关（s2000–s2003）各报一行；
s2003 单独标红/绿。

**★ 空停哨兵（本腿最重要的一条）**：

- 人类：`P(stop)=0.288`、`P(stop∧fire)=0.166`（57.6%）。
- **开腿前**给 eval 加 `stopFireTicks`（或至少用 `min(stop, fireHeld)` 作上界）；
  联合量没有落地前，不得用「stop 涨、fireHeld 不涨」的边际量单独判生死。
- 预注册：连续两个 25 轮点，`P(stop)` 比 it0 高 **≥0.03** 且 `P(stop∧fire)` 没有同向
  上升（或上界 `min(stop,fireHeld)` 不涨）⇒ **停腿**（学到的不是站线，是发呆）。
- 若 `P(stop∧fire)` 同向上升，才算「停下来打」；仍要看 kill/shot 与 timeout。

## 6. 熔断（先落地，再开腿）

**前置（缺一不可）**：

- `tmp/gate-halt.json` 必须是 `mode=halt`；当前是 `notify`，`notify` 下这些线不会停。
- entropy/首杀不在平台 guard 里 ⇒ 要么加逐轮 guard，要么指定人盯并在 DoD 写名字；
  「表里有阈值」不等于「会停」。
- **entropy 要分头**：`engine.py` 现在只落 move+fire 的总和；删掉 fire CE 后 fire 熵
  自己可能上升。开腿前加 `entropy_move`/`entropy_fire`（同一份 telemetry 补丁同时打到
  C/M 两臂，保证唯一代码差异仍是 fire CE），门只看 **`entropy_move`**。拿不到分头读数
  就不许用总 entropy 做「fire 项是毒性」的归因。

| 检查 | 阈值 | 口径 |
|---|---|---|
| `demo_bc` 遥测 | it1 非零；5 轮趋势向下 | 不比 §33 绝对值；恒 0 = 没上桌 |
| **`entropy_move`** | 比 it0 涨 **>0.15** 即停 | 总 entropy 只作辅助；fire 熵单独看 |
| 首杀拍 | 比 it0 退 **>60 拍** 即红 | 同 anchor/同种子 |
| 承伤 / 局 | 比 it0 高 **>15%** 即红 | 换血式莽化 |
| timeout | **>5%** 即停 | 反苟 |
| `kill/shot` | **<0.25**（shots>0） | 防乱扫；shots=0 另报 |

## 7. 防污染/背题（修正空转检查）

- 评估面板仍是 **860xxx**（`batch_plan.py: EVAL_SEED0=860001`）；语料是 **414xxx**，零重叠。
- **因此 eval 里没有「demo 座位」**；原计划的「demo 座位 vs 非 demo 座位的 pass 差」
  在 860xxx 上**无法计算**，是空转检查（评审 P1）。
- 改为：开腿前从 160 个人类座位里**留出 32 个（20%）**，用 128 个重建 bank
  （新 sha 记录在课程头注）；留出的 32 个只做一次性 probe（不是 eval 面板），
  比较「见过的 128 座位 vs 留出的 32 座位」的 pass/kills；差 >15pp 判背题。
- **不把 414xxx 加进任何 eval 面板**（`§15.1` 的防记忆化红线）；probe 报告里注明它
  不是 verdict 面板，不进训练日志的 headline。

## 8. 风险（按可能性排）

1. **协变量漂移照旧**（§33 的真正机制）：BC 权重来自人类状态，PPO 访问自己的分布。
   开腿前加一个廉价诊断：比较 demo 状态与 rollout 状态的 `cellsVisited`/`idle`/`enemyDist`
   分位（或做一次 policy-state BC 小探针）；没有诊断就别把负结果归给「move 不可学」。
2. **教会「停」但没教会「站线」**：人类停是为了守线；空停哨兵按 §5 的联合口径判。
3. **4 关泛化**：语料只有 2000–2003；§33 的 s2003 最弱，分关必报。
4. **`demo_bc` 绝对值不可比**：删 fire 后只剩 move CE，比 §33 低一截；只看形状。
5. **单步 BC 表达不了多步守线**：如果走位四件套不动，**不能**直接判「单步 BC 这条路
   走不通」——还可能是剂量太小/协变量漂移/指标噪声；先做 §8 第 1 条的诊断，再决定转 reward 侧。

## 9. DoD

**本提交（代码/测试/文档）**

- [x] `engine.py` 的 fire CE 删除，docstring/调用点写明理由与恢复方式
- [x] 新测试：fire 标签翻转 ⇒ **全部参数逐位相同**；`move_head` 与初始权重不同
- [x] 现有 5 条 demo 测试全绿；`nn-py-safe.sh -m pytest tests/worker/test_ppo_demo_mix.py -q`
- [x] `bash tools/githook/nn-python-gate.sh` 绿（ruff/mypy/pytest）
- [x] `DECISIONS.md` 记「fire BC 全局删除 + 拒绝 demo_heads 键」
- [x] `docs/nn/experiments.md` §118 更正：24 局哨兵无效（legacy NNModel 14ch 读 16ch），
      判负理由改为 fire 可训练子集 hold 1.84%
- [x] `plan/y22-phaseB-demo-data.plan.md` §10 同步更正，避免旧结论继续传播

**开腿前（不在本提交）**

- [ ] `gate-halt=halt`；entropy/首杀 guard 或指定人盯
- [ ] `entropy_move`/`entropy_fire` 分头遥测（C/M 两臂同补丁）
- [ ] `stopFireTicks` 或 `min(stop,fireHeld)` 上界落地
- [ ] 留出 32 个人类座位 + 128-shard bank 的 sha 写进课程头注
- [ ] C 臂（父提交 worktree，fire CE on）与 M 臂同起点/同种子/同 30–40 轮
- [ ] 课程文件 `nn-training/curricula/y22-demo-move.jsonc`（A 结课后才写）

**报告**

- [ ] 走位四件套 + 空停哨兵（联合量）+ `entropy_move`/`entropy_fire` + 分关 + 配对 CI
- [ ] 不再写「与 §33 并排」的绝对值对比；只比形状/机制
- [ ] 结论按「M vs C 同条件」写；没有 C 臂就只能写「新腿观察」，不能写因果

## 10. 不确定的地方

- **0.7975 的选择偏差**：S4a 是 val 选点后的乐观值、32 局、NNPolicy；正式开腿前重测。
- **coef 0.02 对 move-only 是否还太大**：匹配消融先用 0.02；生产档 0.005 起。
  如果 it20 前 `entropy_move` 抬头，直接砍到 0.01/停腿。
- **move BC 能不能表达「守线」**：单步 5 类只能给局部偏好；多步守线未必学得到。
- **fire 头仍由 PPO 训练**：删的是 BC 项，不是冻结 fire 头；开火量变化不归因 move BC。
- **806/29254 与 9900 发不是同一单位**：不要再做比值；要开火量就看遥测 `playerShots`。

## 11. 评审落账（R2）

| 评审发现 | 本文件落点 |
|---|---|
| P0：测试断言「fire 翻转后 move_head 必须不同」自相矛盾 | §3.1 改为全参数逐位相同 + move 相对初始变化 |
| P0：「与 §33 干净单变量」不成立 | §4.1 列出 5 个 confound；§4.2 用父提交 worktree 做 C 臂 |
| P1：0.7975 是 val 选点后的乐观值/NNPolicy | §0/§1/§10 降级为上界估计，开腿前重测 |
| P1：总 entropy 混淆 move/fire | §6 前置 `entropy_move`/`entropy_fire`，门只看 move |
| P1：防背题检查在 860xxx 上空转 | §7 改为 32 座位 holdout probe，明确不是 eval 面板 |
| P1：熔断未落地（gate-halt=notify；entropy/首杀无 guard） | §6 前置 halt + guard/人盯 |
| P1：150 轮未分段 | §4.2/§4.3 先 30–40 轮，机制过线才续 |
| P1：DoD 缺口禁/决策/归档 | §9 加 `nn-python-gate`、DECISIONS、§118/phaseB 更正 |
| P1：起点读数锚在 stack2 | §4.4 先测 it0；§5 全部相对 it0 |
| P1：806/9900 跨单位 | §1/§10 改用 806/43744 与 806/29254；9900 只作另一事实 |
| P1：空停哨兵无阈值/联合量 | §5 预注册 +0.03/+0.02，先落 `stopFireTicks` |
| P2：0.130 vs 0.133、0.322 vs 0.318 混表 | §2 统一 NN 胜局口径（0.133/0.076；人类 0.288/0.318） |
| P2：9900 无出处、引 gitignored report | §1/§10 不再依赖；结论引 durable `docs/nn/experiments.md` |
| P2：「1 行」含 docstring；「dos」typo；「fire 学不了」过宽 | §3/§9/§10 改为准确措辞 |
