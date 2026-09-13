# NN 阶梯实施总纲（nn-ladder-roadmap v2.0）

状态：**v2.0 — 体系化重构，待用户最终批准后开工**。课程结构、命名规范、门均已用户拍板（见 §1
决策宪法）；四轮评审（第二轮自审 + `v3.review-dsf/ms/hy.md`）全部处理完毕，结论已并入正文。
本文件是**唯一施工总纲**：任意 agent 按 §6 的 Phase 任务清单与 §7 的每级运营手册执行，不需要
重读评审文档。变更历史压缩在 §10，逐条证据可回溯评审原文。

---

## 0. 文档地图（实施者从这里开始）

| 要做什么 | 读什么 |
|---|---|
| 判断"现在该做哪个 Phase、出口是什么" | 本文件 §6 |
| 实施某一级课程（出配置、起腿、过门、登记） | 本文件 §7 每级运营手册 + §5 阶梯表 |
| 实施 obs schema v3（编码器/schema.py/wasm/dataset） | `plan/obs-schema-v3.plan.md` v4.0（byte 级规格，N1 实施者必读） |
| 改 reward / 剂量 | 本文件 §5-N3（语义与定案）+ §7 第 2 步 |
| 查某个决定能不能违反 | 本文件 §1 决策宪法 |
| 查某个失败模式有没有防线 | 本文件 §8 |
| 追溯某条结论的证据 | `plan/v3.review-{dsf,ms,hy}.md` + `.workbuddy/memory/2026-09-13.md` |
| 历史项目文档 | `c4k1.attempt.md`（**已由本总纲收编，勿再按其执行**）、`plan/archive/`（本总纲 v1.x 存档） |

## 1. 决策宪法（用户拍板 + 已定案——实施者不得违反）

| # | 决策 | 来源 |
|---|---|---|
| D1 | 阶梯结构：c1→c20→经典 35。主题：c1 走位开火 / c2 多敌 / c3 破包围 / c4 存活闪避 / c5 捡道具 / c6-c7 单命结课 / c8-14 双命 / c15-20 三命 / 经典 35 收官 | 用户 |
| D2 | 命名规范：`cN` ⇔ `count = N`（敌总数），与现有 c4/c5/c6 课程同一套 | 用户 |
| D3 | 全阶梯无基地、无掩体：c01-c20 纯空场（仅边界钢环，已核实 arena 网格即此形态）；base/地形观测留经典 tier 兑现 | 用户 |
| D4 | 晋级门 = **每级 cN pooled 400 局点估计 ≥80%**（单轨门；开放问题 5 已拍板，双轨门提案否决）；毕业门即进度门，卡门是常态 | 用户 |
| D5 | c1-c4 允许纯 BC 毕业（BC 优先写死，PPO 仅 BC 卡门后启用） | 用户 + hy E3 |
| D6 | 先补后重训：obs v3 优先于一切长跑（2026-09-13 15:24 拍板）；v3 有效性由 R1 检查点证伪，null 上报用户、不自动改道 | 用户 |
| D7 | 任务侧不可动（§0.2）：命数、敌数、关卡形态、终局标准（含 max_ticks——取值规则工厂定型时一次性 DECISIONS 立案，不再逐调）、掉落规则 | 用户 + §0.2 |
| D8 | 观测一次加满、阶梯中途零 schema bump；v4 唯一合法窗口 = Phase 6c 经典缺口复盘 | 定案 |
| D9 | 掉落规则全阶梯 **modern**（`bonusEnemyEveryNSpawns=4` + score 里程碑掉宝）；classic 规则只进经典 tier | 定案（dsf B4） |
| D10 | 权重暖启合法（同 schema），但**暖启 = 初始化手段，不是能力继承**；每级的 80% 由级内训练侧杠杆达成 | 定案（hy R2） |
| D11 | 2 命 / 3 命 tier = 新任务线：DECISIONS 立案 + fresh out/traj + reward 重验算 + bc 零样本重测 | 定案（c4k1 §7） |
| D12 | 不降难度、不用教师表现判关卡难度/门/梯度（§0.2 四禁令）；门数值 80% 不降，改的只能是测量统计量与训练侧杠杆 | §0.2 |

## 2. 已核事实底座（全部带行号，实施者可直接引用勿重查）

**观测现状**：`OBS_CHANNELS=14`、`SCALAR_DIM=19`、`OBS_SCHEMA_MAJOR=2`（`schema.py:14/22/31`，
TS 镜像 `obs-encoder.ts:42-45`）；敌机值 `(tier<<3)|(d+1)` 1-36；子弹 `d+1`/`d+1+4`（**加法**）；
obs 缓冲是 **`Uint8Array`**（`:120`，赋值截断）；标量缓冲 Float32。**现存缺陷**：waveHeat 硬编码
3 点而 arena 是 4 点（`obs-encoder.ts:261-267` vs `levels/arena4.jsonc:48-53` 四角）。

**训练现状**：BC checkpoint **无 value 头**（`student.py:172`「BC checkpoints lack it」）；
`warmup_iters` 字段现成（`c5-gae.jsonc:109`，值 0）；PPO 提交崩溃两起同位置无堆栈
（c5-tick / c6-bonus，memory 账本）；`min_train_samples: 4000000` vs 实测 ~1.67 万 transition/轮
矛盾；`weights/` 已 434MB；timeout 口径**已修**（d17e9f0：`oc in ("timeout","max_ticks") and
not cleared`——勿重复修）。

**实测单价与梯度**（NN 数据，非教师表现）：≈4.5 分钟/轮（c5-gae 160 轮 / max_hours 12）；
同权重跨 count 胜率 c4 60% / c5 47% / c6 38%（每 +1 count 掉 9-13pp）；唯一显著提升来自级内
单点杠杆（c5-gae λ=0.95，+18pp/160 轮，爬升 it100-160）；满压段暖启续训有 −9pp 反例
（c6-pickup3，p=0.050）；c6-bonus 74 轮盲跑净零。承伤基数 c4=150 / c5≈196 / c6=218。

**关卡资产**：`levels/{arena4,arena6}.jsonc` 持有 grid/forces/count/lives/star/difficulty/
max_ticks/player_spawn(12,12)/enemy_spawns(四角 4 点)；forces 20 字符，spawn 取**循环**
`enemies[i % len]`（`World.ts:501-505`，count≤20 时与截断等价）；`World.ts:471-474` 支持任意
出生点数。经典 35 关在库（`stageData.ts`：35 关/35 兵力串，冰关 17/24/28/32，水关 28 个，
`CODE[6]='s'` 边界钢环）；`ENEMIES_PER_STAGE=20`、cap `MAX_ENEMIES_ALIVE=4`。
星盾机制：三星 player 被击中掉回两星、HP 回满净失血 0（`SimulationCombat.ts:618/736`）；
`playerHits` = 死亡 + 星盾消耗（`export-eval-game.ts:14`）。常量：POWERUP/RESPAWN_SHIELD/
BOAT/EMP/TIMEOUT = 20000/3000/20000/8000/20000 ms；弹速敌 4 档 {40,45,50,70}，玩家弹速随星级变
（per-star dimension gain，`combat.ts:172-181/318`）。

**基建在线（阶梯直接复用，勿重建）**：训练控制台（`bun run train` → :8900，LAN 只读）·
课程热加载（每 iter 重读课程文件，corpus_identity_fp 分流白名单/拒收 + 控制台横幅）·
D14 语料身份（corpus_identity_fp 内联==引用同指纹，mid-run 编辑防脑裂）· EVAL_SEEDS 200 ·
hazard 分档探针（`--from-json` 失败子集重跑）· reward library + `validate_reward` ·
mirror 增广 + `test_dataset_mirror.py` · G4-cloud halt 解耦 + 控制台门禁开关（d17e9f0）·
timeout 口径修复（同 commit）· freeze gates（`bun run freeze:check`/`freeze:l2`）。

## 3. 支柱 N —— 网络补全（详案 = obs spec v4.0）

- **N1 obs schema v3**：30 标量 + 16 通道 + 3 打包位 + waveHeat N 点通用化 + SCHEMA_FINGERPRINT。
  byte 级规格、同步链 13+ 处、测试清单全部在 `plan/obs-schema-v3.plan.md` v4.0，此处不重复。
  实施顺序要点：tiny 双课冒烟先行（§16.1 先测速）→ 全链同步 → wasm golden → R1 检查点。
- **N1-R1 有效性检查点（可证伪判据）**：全链绿后、阶梯开工前，在 c5（λ=0.95、同 reward、
  同种子、40 轮）跑 **v2 基线 vs v3 双臂对照**（个位数机器时），比 **value R²（主信号）** +
  同种子配对胜率（辅助）。任一优于 v2（超噪声带）⇒ 押注成立、阶梯开工；**双双不优 ⇒ 上报
  用户带分析**（含 c5-gae「爬升偏晚 it100-160」教训）——null 不自动改道，改道权在用户（D6）。
- **N2 冰面动量 vx/vy 已并入 v3**（sN10=vy/speed、sN11=vx/speed，X_INDICES=[15,18,29]）：
  阶梯全程空场恒 0（「零直到兑现」= 幻影基地修复先例），经典 tier 兑现。
- **N3 奖励语义分 tier**（剂量公式进 I3 工厂，不手抄 20 份）：
  - **1 命（c01-c07）**：wDmg 处置——**致死命中只由 terminal 计价**（去重复，实测
    `{playerHits:{1:110,2:1}}`）；非致命星盾命中若计价需事件子类拆分。c04 起手 wDmg=0 +
    探针哨兵「星盾命中占比」（现有遥测可算 `starHits ≈ playerHits − deaths`），占比显著再上
    拆分。剂量：`wChip = k/承伤基数(level)`、`wKill` 固定；c01-c03 承伤基数极小**不上 wChip**；
    **v2 时代剂量数值不可跨 schema 继承**（chip 先验 0.02/0.03 是无 HP 观测下测得）——方向
    继承、数值 v3 后在 c05 两档小剂量扫描（2×30 轮）重标定再爬坡。
  - **2 命（c08-c14）**：wDmg 复活为真信号 + per-life 损失项（`-wLife*livesLost`，量级 <
    terminal；wLife 定价 Phase 4 立案时按承伤基数换算）。**「同一代价只罚一次」核算表**：
    中间命损失 = wLife 单独、最后一命死亡 = terminal 单独、非致命命中 = wDmg(非致命) 单独。
  - **3 命（c15-c20）**：续用 2 命语义，剂量随承伤基数重算。
  - 通用哨兵：kills 不降、pickup 不降、dmg/kill 不升、KL/entropy/value/gnorm 四数必读。
- **N4 教师策略**：c01-c04 **BC 优先写死**；c05-c20 PPO 主导（bc = 上一级毕业生暖启）。
  - **教师胜率探针**（Phase 1 出口）：c01-c07 × EVAL_SEEDS 200 headless God 扫描（个位数
    机器时），teacherWR 进 LEDGER——教师 <80% 的级直接走「BC 暖启 + PPO」；探针只回答 BC
    通道可行性，不判难度/门/梯度（§0.2 合规）；顺带落盘 score 分布 + sN8 方差（F5）。
  - **BC value 头（E2）**：BC checkpoint 无 value 头 ⇒ 采 a+b：**a. BC 阶段同训 value 头**
    （God 轨迹 MC return 回归，顺带提前观测 value R²——服务 R1）；**b. 兜底暖启 value 头不
    继承 + `warmup_iters` value 预训练**。c05 开工日志必记 value loss 起始值，>0.8 先 warmup
    （进 I4 报告字段）。
  - BC 导出保留 `--wins 1` 默认（wins-only，参数化改造中不得弄丢）。

## 4. 支柱 I —— 基础设施

| # | 项 | 规格 |
|---|---|---|
| I1 | **PPO 提交崩溃修复**（P0） | **第 0 步（半天）OOM/磁盘取证**：提交前后 RSS 峰值（psutil/getrusage）+ torch 分配器峰值 + `df` 基线 + 写盘字节数——批量 obs 1.6 万 × ~10.8KB ≈ 190MB、mirror ×2/梯度/优化器峰值翻倍，OOM killer 无堆栈与现象吻合；坐实则修法 = batch 切分/内存映射/减一份 mirror 副本。→ `faulthandler` → PPO 提交前 checkpoint → 幂等重试 → 确认断点续训覆盖提交环节；提交收尾写 **WAL journal**（started/done，恢复见 started 无 done ⇒ 幂等重放）；出口测试两条注入路径：提交点 kill-9 + 断点重放、幂等重放 |
| I2 | 配置审计 | min_train_samples 矛盾（I-P1）；G4 REMEDIATE「N 次即停」；阶梯课程一律不配 gates |
| I3 | **阶梯工厂** | 模板生成 `levels/ladder/c01..c20.jsonc` + `curricula/ladder/*.jsonc`，参数 **count/lives/max_ticks**（几何=空场常量）+ reward 剂量公式 + `rollout_games`（c01-c03 600-1000 局/轮）+ `shard_keep_policy` + **级×候选腿矩阵**（默认 2 腿：暖启基线腿 + 假说腿；攻坚级 c06/c07 3 腿 +BC 重起）。**定型前钉死四参数**：① forces 长 **恒 20** + 循环语义断言（`i%len`）；② 掉落 modern profile（D9）；③ EVAL_SEEDS 与双轮门种子集合参数化（§15.1 键控）；④ 出生点数模板参数（arena 现四角 4 点，与 waveHeat N 点编码一致性由单测保证；C4 出生点对照关复用）。CI 预检 `load_course`+`validate_reward`。经典关由 `stageData.ts` 生成 `levels/classic/*`。**max_ticks 取值规则 `600×count + 900`（c01=1500 … c20=12900）已一次性立案 = DECISIONS §2026-09-13-goalnn-max-ticks-rule** —— 注意 roadmap 原拟式 `ceil(2400×count/4)` 在低 count 端塌缩（c01=600 实测截断教师 61/200 局、pass 69% < 80% 门，构造性不可达），已否决 |
| I4 | **晋级门 runner** | 两轮各 200 局 vs 本级 bc（种子不重叠、间隔 ≥5 训练轮）→ **pooled 400 点估计 ≥80%**（Wilson 95% LB 随报告；pass = win ∪ cleared）；报告必附：hazard-by-k + 局长分布 + bonus 台数三行（M1）、跨级召回行（前 1-2 级各 50 局，B8）、value loss 起始值（E2）、超时占比列（口径已修勿重复）、c01/c02 行为探针（首杀 tick 分布 + dmg/kill，M3）；**机器可读 verdict（graduate/stay/escalate）**：双条件满足自动起下一级（warm-start 自动填，LEDGER = verdict 追加日志），人工只处理 escalate——**tier 边界（c07→c08/c14→c15）必须人工放行**（DECISIONS 立案处）；同级多腿同种子配对 McNemar（X1） |
| I5 | **LEDGER 台账** | `nn-training/ladder/LEDGER.jsonc` = **统一 identity 台账**：20 级 + 经典 35 关一张表（level_id → corpus_fp → 课程/语料/权重/门证据），不维护两套状态机（X3）；列：status（pending/bc/ppo/graduated/stuck）、`hypothesis`（每级开工假说，R2.3）、`teacherWR`、`disk_bytes`/`ckpt_keep_list`、毕业生权重 id、门证据指针、日期；控制台 LAN 渲染 |
| I6 | BC 蒸馏管线 | `export-godai-labels` 按 level 八路分片（§16.5）；BC 训练 + 评估 + value 头（N4）；「BC 毕业 vs 转 PPO」判定自动化进 I4；保留 `--wins 1` |
| I7 | 语料 + 磁盘纪律 | 每轮 (runSeed, it) 键控语料轮换（§15.1）；greedy/sampling 双口径（§15.3）；stream mode 保持 exercised（§15.6）；换语义 = fresh out/traj（§15.5）；`tmp/` 定期清理 + shard 保留策略（v3 shard +14% × mirror ×2 × 20 级 fresh traj，账进 LEDGER `disk_bytes`） |
| I8 | 编码一致性 | SCHEMA_FINGERPRINT 双端断言 + 写进 shard manifest（规格见 obs spec §3.5）；编码器单测清单见 obs spec §4 |

## 5. 支柱 C —— 课程阶梯

### 5.1 设计原则

1. **进度轴只有两根**：count（tier 内单调）与 lives（跨 tier 1→2→3）；几何全程空场（D3）。
   **归因如实（M1）**：count 一次拧动 ≥5 个训练条件（增援波数/满压时长/局长/bonus 台数/score
   掉宝概率）——它是进度轴不是单变量归因轴；归因靠 I4 分桶报告三行。
2. 任务侧旋钮由 §1-D7 定死，训练中禁动；训练侧杠杆（奖励/观测/算法/评估统计量）随便动。
3. **教师定位（§0.2）**：God 只承担 BC 起步；已知缺陷中**并道追击**在 c03/c04 预期暴露
   （空场无墙，「清墙」缺陷不适用）；教师胜率不是门或天花板（c4 教师 68% < 80% 门，BC 封顶
   ≈ 教师 ≠ 关卡封顶，学生靠 PPO 超越）。
4. 增援波数 = count − cap4（c4 无增援、c5 起 +1 敌多一波，与现线 hazard 分析一致）。

### 5.2 晋级门（每级统一，D4 单轨门）

- 两轮各 200 局 vs 本级 bc（种子不重叠、间隔 ≥5 训练轮；pass = win ∪ cleared）；**判定 =
  pooled 400 点估计 ≥80%**（Wilson 95% LB 随报告；门数值 80% 不降——D4/D12）。
- 哨兵同轮全绿：kills 不降、pickup 不降、dmg/kill 不升；**entropy 分档（M4）**：c04+ ≥0.32，
  c01-c02 观察线（报告不停训——单敌决斗最优策略本就接近确定性）；Phase 2 前两级实测后与预算
  重估同节拍定档，备选「5 轮内 −0.05 突降」信号。
- 行为探针（M3）：c01/c02 必附首杀 tick 分布与 dmg/kill，防「过门但只会一条路线」。
- BC 毕业与 PPO 毕业同门同口径；BC 不过 ⇒ 暖启转 PPO（既定路径，不是失败）。

### 5.3 1 命 tier：c01-c07（`lives: 1`）

| 级 | count | 机制/主题 | 主学能力 | obs 依赖 | 奖励焦点 | 教师策略 |
|---|---|---|---|---|---|---|
| c01 走位开火 | 1 | 单敌决斗（单出生点） | 接敌-开火节奏-躲单弹 | ch11 弹速档、ch14 剩余命中 | wKill + 轻量 wTick（不上 wChip，R5） | **BC 应足** |
| c02 多敌 | 2 | 双敌两线 | 多目标优先级、两线走位 | ch14、ch15 生成中敌 | +wChip 轻剂量 | **BC 应足** |
| c03 破包围 | 3 | 多出生点合围（**4 点轮转，count=3 时三点活跃**——F1 勘误：arena 四角 4 点） | 脱夹叉、风筝走位、集火逐个破（纯走位解围） | ch13 waveHeat、ch15 | wKill 主导 | BC 起步，**预期转 PPO**（God 并道追击缺陷暴露点） |
| c04 存活闪避 | 4 | cap4 满场、无增援（**=现 c4 线同 count 收编**） | 开阔地弹幕生存（无掩体，纯走位闪避）、距离管理 | s19 hp、s20 盾、ch11 | **wDmg=0 起手** + wChip 主导（chip 方向先验，数值 v3 后重标定） | BC 起步（教师 68%），PPO 补到门 |
| c05 捡道具 | 5 | 1 波增援（**=现 c5 线同 count 收编**）、bonus 车全开 | 道具价值优先级（star/bomb/tank/shield/freeze 高价值优先；**repair 用处几乎最低，不值得绕路**）+ 顺路拾取 | bonus 位、ch12 寿命、s26 score | **wPickup 2.5 扎根**（不按 repair 场景设计）+ wChip 两档重标定 | PPO（bc=c04 毕业生） |
| c06 满压 | 6 | 2 波增援（**=现 c6 线同 count 收编**，k2 起恒满压）——**机制攻坚级** | 满压换血效率 | 全量 | c6-chip 方向先验 + 数值重标定 | PPO，**开工即 3 腿**（暖启/BC 重起/假说腿） |
| c07 单命结课 | 7 | 3 波增援，1 命最长线——**机制攻坚级** | 长局耐力 + 增援节奏下全技能集成 | 全量 | c06 语义，剂量按承伤时长重算 | PPO，**开工即 3 腿** |

### 5.4 2 命 tier：c08-c14（`lives: 2` —— 新任务线，Phase 4 开工时 DECISIONS 立案）

`c08..c14` ⇔ count=8..14（增援波数 4..10），几何同空场，无基地；局长线性涨（max_ticks 按
§6-Phase 1 立案规则）。

- **c08 适应层**：wDmg 复活 + per-life 损失项落地 + bc 零样本重测；**双变量边界（M2，采测量
  分离）**：c07→c08 同时变 lives 与 count，毕业报告必含「c07 权重在 c08 零样本」与「c08 权重
  回测 c07（1 命）」两行（不改 count=8——D2 命名定案优先）；**s2 OOD（R6）**：s2=lives/3 在
  1 命 tier 恒 0.333、c08 起手 0.667——c08 前 5 轮单独记录 value loss 尖峰，出现归因 OOD 不误判
  「任务变难」。
- c09-c13：纯 count 爬坡，每级一个门。
- **c14 结课考**：count=14，tier 毕业 → 解锁 3 命 tier。

### 5.5 3 命 tier：c15-c20（`lives: 3`）

`c15..c20` ⇔ count=15..20（增援波数 11..16），几何同空场，无基地。**c20 = count 20 =
`ENEMIES_PER_STAGE` 经典敌数**——经典条件的「无地形/无基地版」毕业桥。

### 5.6 经典 35 关（终点，拆 6a/6b/6c——C3）

- **6a 探针+规划**：levels/classic 生成 → c20 毕业生 × 各级 20 局/级预热损耗探针（B3：win
  降幅 + 地形/base/冰面观测首次非零曲线）+ **出生点对照关**（C4：同 count 空场、仅出生点换
  下方 (8,24)，分离出生几何轴，差值≈0 则删）→ 产出分批混训计划（B7 地形族：砖/钢/水/冰每批
  6-10 关，批内并行批间串行+跨批召回）+ 经典预算三档 + 评测口径 DECISIONS 立案。
- **6b 分批混训**：经典 LEDGER 复用 I5（35 关状态机）；各批独立毕业门。
- **6c 战役验收**：逐关评测 + 完整战役通关率（测量定义见开放问题）+ 人工体验验收（MANIFEST §1
  最终裁判）；缺口复盘 = **v4 唯一合法窗口**（D8）。
- v3 预留项兑现清单：ch0(brick)/ch2(water)/ch3(forest)/ch4(ice)、ch5 base、fence s25、boat s23、
  vx/vy sN10/sN11 阶梯全程恒 0，baseHP s24 恒 1.0 不变化——**ch1 steel 例外恒非零常量**（边界
  钢环，`CODE[6]='s'`）：阶梯已学「钢=边界墙」常量模式，对经典钢关**正迁移**；**brick（可破坏）
  才是真正的零直到兑现教学维度**。零块面 ≈ 全 schema 三分之一（设计如此，换 20 级零重训）。
- **适应期应急手段（v1.3 定案保留）**：经典入口预期有一段地形/基地适应期，若 6a 探针显示停滞，
  备选是经典 tier 内加「同 count 空场地形过渡关」（应急、不属阶梯、不违 D3）。

### 5.7 跨级规则

- **暖启 = 初始化手段，不是能力继承（D10）**：同权重跨 count 实测每 +1 count 掉 9-13pp，唯一
  显著提升来自级内杠杆，满压段暖启有 −9pp 反例；**c06/c07 机制攻坚级开工即 3 腿**，不等卡门。
- **每级开工前必写训练侧假说**：动哪个杠杆 / 预期信号 / 多少轮内看不到即证伪 → LEDGER
  `hypothesis`（c6-bonus 74 轮盲跑不再重演）。
- **新任务线纪律（D11）** + s2 OOD 项（见 §5.4）。
- **卡门 >3 个门周期** ⇒ 升报用户 + 训练侧杠杆清单逐项过（先查观测信息、再查奖励、再查算法）；
  永不减敌/放松形态/中途加命（D7/D12）。

## 6. 实施时序（Phase 0-6c：前置 / 任务 / 出口）

| Phase | 前置 | 任务清单 | 出口判据 |
|---|---|---|---|
| **0 基建止血** | 无 | ① I1 第 0 步 OOM/磁盘取证（半天）；② I1 快速缓解四件（faulthandler/WAL/checkpoint/幂等重试）+ 双注入测试；③ 根因排查时间盒 1 周超盒转后台；④ I2 配置审计 | 取证有结论 + 缓解四件落地 + **短腿 PPO（iters≤30）绿**；根因闭合或转后台有案。**并行**：I3/I4/I5 本地工程与 I1 全程并行（C1） |
| **1 网络补全 + 工厂** | Phase 0 短腿绿 | ① **I3/I4/I5/I8 先在 v2 schema 上建成**（与 schema 版本正交，ms C1 反串行）；② N1 obs v3.4 全链落地（tiny 冒烟先行，§16.1 测速）→ wasm golden → R1 检查点（c5 双臂）；③ 教师探针（c01-c07 × 200 + score 分布/sN8 方差）落 LEDGER；④ max_ticks 规则 DECISIONS 立案 | obs 全链绿 + R1 判据执行 + 工厂能出一对合法课程（四参数断言过）+ teacherWR 落账 |
| **2 BC 阶梯** | Phase 1 出口 | c01-c04 按 §7 运营手册：工厂出级 → BC（value 头）→ 双轮门 → 毕业或转 PPO（c01-c03 PPO 需 rollout_games 600-1000） | 四级 graduated（或假说证伪上报） |
| **3 PPO 阶梯** | c04 毕业 | c05（wPickup 扎根 + wChip 两档重标定）→ c06（攻坚 3 腿）→ c07（攻坚 3 腿） | 三级 graduated |
| **4 2 命 tier** | c07 毕业 + **DECISIONS 立案** | c08（适应层 + M2 测量分离 + s2 OOD 监控）→ c09-c13 爬坡 → c14 结课 | 七级 graduated |
| **5 3 命 tier** | c14 毕业 + 立案续 | c15-c20 | 六级 graduated |
| **6a 经典探针+规划** | c20 毕业 | levels/classic 生成 + 预热探针 + 出生点对照 + 分批计划 + 经典预算 + 口径立案 | 探针落盘 + 计划与口径批准 |
| **6b 分批混训** | 6a 批准 | 地形族分批（B7），批内并行批间串行 + 跨批召回 | 各批毕业门绿 |
| **6c 战役验收** | 6b 毕业 | 逐关评测 + 战役通关率 + 人工体验验收 + 缺口复盘（v4 窗口） | 战役通关演示 |

**旧线收编**：c4-chip03（dmg/kill −21% 定案）、c6-chip（跑到 it20 取剂量结论即收）按各自退出
条件收尾；**现线与阶梯 c04/c05/c06 同 count 同几何，baseline/hazard/剂量-响应证据直接沿用**；
课程动物园冻结归档，不再新开非阶梯课程。

**预算三档（hy R3 实测单价重标；单轨门定稿后正式生效）**：乐观 120-200 / 基准 **300-600** /
悲观 1000+ 机器时，串行 wall **4-12 周**。**重估触发点：c04 第一个门跑完立即用实测单价重算
全阶梯**（一人标定 17 级到 ±30%）。缓解：多腿并行（X1）/ 自动晋级（C2）/ 卡门杠杆清单（§5.7）
（双轨门的「同 tier 并行下探」缓解随 D4 单轨门定稿移除）。
评估 200 局分钟级（八路分片）；局长随 count 线性涨（c04 ≈1100 tick → c20 预计 ~5×）；同 tier
内并行、跨 tier 串行。

## 7. 每级运营手册（Runbook——每级 cN 开工到毕业的固定流程）

1. **出级**：I3 工厂生成 levels + curriculum（count/lives/max_ticks/腿矩阵/剂量公式）；
   `load_course` + `validate_reward` 预检绿。
2. **写假说**：该级训练侧假说（动哪个杠杆 / 预期信号 / 证伪轮数）→ LEDGER `hypothesis`。
   无假说不开工（盲跑禁令）。
3. **起腿**：默认 2 腿（暖启基线腿 + 假说腿）；攻坚级 c06/c07 3 腿（+BC 重起腿）。
   同 tier 内多级可并行，跨 tier 串行。
4. **监控**：KL / entropy（分档）/ value loss（起始值 >0.8 先 warmup）/ gnorm 四数必读；
   每轮语料 (runSeed, it) 轮换；长跑日志落文件（§16.2，`> run.log 2>&1`，禁管道）、每轮观测行
   可读、checkpoint 按 N 轮落盘（§16.3）。
5. **毕业评估**：I4 双轮（各 200 局、种子不重叠、间隔 ≥5 轮）→ pooled 400 判定 + 哨兵 +
   报告三行（hazard-by-k/局长/bonus）+ 跨级召回行 + value loss 字段。
6. **晋级**：verdict=graduate ⇒ 自动起下一级（warm-start 自动填）；escalate ⇒ 人工。
   **tier 边界人工放行**（DECISIONS 立案）。
7. **登记**：LEDGER 追加 verdict + 权重 id + 门证据 + `disk_bytes`；2 命/3 命 tier 检查
   s2 OOD 监控与「同一代价只罚一次」核算表。

## 8. 已知失败模式与防线（实施者对照自查）

| 失败模式 | 事故先例 | 防线 |
|---|---|---|
| 浮点写 Uint8 通道恒 0 | ch15 clamp01（hy E1，v3.2 自伤） | obs spec 全局规则 + 三值单测 255/128/0 |
| 打包位字段逐位碰撞 | bonus bit4 vs tier（dsf A1） | bit6 + (bonus,tier,d) 全组合唯一性枚举测试 |
| 位运算 OR 记法陷阱 | ch11 owner/d+1 位重叠（dsf A2） | 显式加法记法 + 混合基注释 + 32 值唯一性 |
| mirror 丢位/误判 | speedBucket 清零、rest>>2 误判（hy E6） | 显式 LUT + 双向唯一 + 速率不变断言 |
| obs 与 reward 口径错位 | stuckTicks 两处自算（dsf A4） | 共享纯函数 + obs/reward 序列 golden + 同 seed 双跑 |
| wasm 漏编静默降速 41ms | bufA 事故 2026-09-10 | conv_feats.c 同步 + h64/d8 wasm 路径 golden |
| BC 暖启链 value 头随机 | student.py:172（hy E2） | BC 同训 value 头 + warmup 兜底 + 起始值门 |
| 短局 transition 塌量 | c01 1.5-3 千/轮（hy E3） | rollout_games 反推 600-1000 |
| 盲跑长腿 | c6-bonus 74 轮净零 | 每级假说必写 + 证伪轮数 |
| 暖启当能力继承 | c6-pickup3 −9pp | §5.7 措辞定案 + 攻坚级多腿 |
| 噪声峰误晋级 | pickup3 门线 | pooled 400 + 双轮间隔 ≥5 轮 |
| 评估口径瞎眼 | timeout_frac 恒 0（已修 d17e9f0） | 勿重复修；报告超时占比列核对非零 |
| 同名不同参 | chip02 改名事故 | 工厂单一模板 + 四参数断言 |
| 语料身份脑裂 | mid-run 编辑课程（D14 拒收） | corpus_identity_fp + fresh out/traj |
| 提交崩溃丢轮 | c5-tick/c6-bonus 两起 | I1 取证 + 四件 + WAL + 注入测试 |
| 磁盘膨胀 | weights/ 434MB + tmp/ 17 万 npy | LEDGER disk_bytes + shard_keep_policy + tmp/ 清理 |

## 9. DoD 总表

- [ ] I1/I2 闭合：取证有结论 + 缓解四件 + 短腿 PPO 绿 + 根因闭合或转后台有案 + 注入测试；配置矛盾清零
- [ ] N1/N2 落地：obs v3.4（30 标量+16 通道+3 打包位+waveHeat N 点+uint8 规则+指纹）全链同步 + wasm golden 绿
- [ ] **R1 检查点执行**：c5 双臂对照落盘（value R² 主信号），判据执行（null 上报用户）
- [ ] **BC value 头落地**：BC 同训 value 头 + warmup 兜底；c05 value loss 起始值入报告
- [ ] **教师探针落盘**：c01-c07 teacherWR + score 分布/sN8 方差进 LEDGER（Phase 1 出口）
- [ ] I3-I5 建成：工厂一键出级（四参数 + 腿矩阵 + rollout_games + 剂量公式）、门 runner
      （pooled 判定 + 三行报告 + 召回行 + 机器 verdict 自动晋级、tier 边界人工）、统一 identity
      台账 55 关可查
- [ ] 阶梯 20 级全部 graduated（pooled 400 ≥80% + 哨兵分档绿，台账留痕）
- [ ] 经典 6a 探针/6b 分批混训/6c 评测落盘 + 战役通关演示（人工体验验收）
- [ ] 全程零计划外 schema bump；`bun run check` / freeze gate 全绿维护

## 10. 开放问题（剩余）

1. **wLife 定价**（2 命 per-life 损失项）：Phase 4 立案时按承伤基数换算，不预设数值。
2. **经典 35 评测口径**：逐关 ≥80% + 战役通关率 + 人工体验的权重组合；战役通关率的测量定义
   （n 次全程、seed 键控 §15.1 跨关如何轮换）——6a 立案时定。
3. **经典混训批次划分**：倾向地形族分批（B7），批次数量由 6a 探针数据定。
4. **阶梯地形假设**：「c01-c20 全程空场（仅 count/lives 两轴）」是对命名规范的既定读法；若要
   tier 内同名 count 换地形，Phase 1 工厂定型前提出。
（已决：c07 无基地（D3）；门的粒度 = 单轨门（D4）；c08 维持 count=8（D2 优先）。）

## 11. 变更记录

- **v2.0（2026-09-13）**：体系化重构——合并 v1.0-v1.5 全部定案与四轮评审结论为实施型文档；
  新增 §0 文档地图、§1 决策宪法、§7 每级运营手册、§8 失败模式防线；奖励/时序/容量内容归位
  （配方换算归 N3/§5，编码细节归 obs spec v4.0）。v1.x 原文存档 `plan/archive/`。
- v1.0-v1.5：初版 → 命名规范定案（cN=count、无基地）→ dsf 评审（A1/A2 位布局硬伤、B 组）→
  ms 评审（F1 waveHeat N 点、门统计量、Phase 6 拆分）→ hy 评审（E1 uint8 截断、E2 value 头、
  R1 检查点、预算重标）→ 用户拍板单轨门。逐条证据：`plan/v3.review-{dsf,ms,hy}.md`。
