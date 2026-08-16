# God-AI Hard 开放测试协议 — 第 1 轮执行交接(2026-08-16)

协议: `plan/God-AI-Hard-Open-Test-Protocol.md`。本文件是接手 agent 的完整状态快照。

## 当前树状态(未提交)

- `bun run typecheck`: **绿**
- 全量测试 `bun test --parallel`: **1330 pass / 1 fail / 23 skip**(`bun run check` 因此红)
- 唯一失败: `tests/godai-intent.test.ts` > "holds the committed target within the lease even when a closer enemy appears"(诊断见下)
- 未提交改动: `tools/lib/stage-spec.ts`(新)、`tests/stage-spec.test.ts`(新)、6 个工具、`src/ai/god/{ThreatBudget,ActionContract,CoveragePlanner,StrategyPlanner,think}.ts`、5 个测试文件。**全部为 mode=0 门控的 dormant 路径或纯工具改动,默认行为应 byte-identical(尚未做正式验证)**。

## 已完成

### M0(全部完成)
- **M0.1/M0.2** 共享 stage parser `tools/lib/stage-spec.ts`: `parseStageSpec`(all/范围/逗号/单关统一,拒绝空/反向/越界/垃圾 token,修复 §213 `--stages 1-35`→S1 口径 bug)+ `paramsHash`(FNV-1a over key-sorted JSON)+ `runHeader`(口径行)。测试 `tests/stage-spec.test.ts` 11 个全过。
- **M0.3** parser + 口径行接入 6 个工具: optimize-godai / ab-param / ab-multi-param / threat-ledger / run-forensics / eval-suite。口径行格式: `caliber: difficulty=X stages=N seeds=N stageIndex=0 maxTicks=N params=xxxxxxxx`。
- **M0.4** 三份 baseline 已生成(hard 35×60):
  - `tmp/open-test-threat-baseline.json`(85MB): 胜率 75.3%,base_destroyed 447(21.3%),lives_exhausted 70(3.3%);失败族: no_output_commit 311(69.4%)/multi_threat_overload 99(22.1%)/travel_late 24/turn_locked 14;最弱关 S34/S8/S24/S3/S12/S20/S28/S5
  - `tmp/open-test-forensics-baseline.json`(10.8MB)
  - `tmp/open-test-eval-baseline.json`(423KB): SUITE 0.5392(lcb 0.5330±0.0062),最弱关 Battlement 0.310/win 28%
  - 注意: eval-suite 默认 maxTicks=18000,forensics/threat-ledger 默认 36000(各自口径行可见)

### M1(完成,默认 OFF 路径)
- **playerActionEta 去重复计费**: 五字段互斥,`total` 为严格和;转向等待只计一次;瞄准转向与路径换轴共享一个 turn window(`aimAlignmentEta`);字段语义全部写明包含/不包含。
- **enemyDeadline 语义分层**: `enemyArrivalLowerBound`(排序用下界)/`enemyDamageEarliest`(最早落地伤害 = fireReady + 破环 + 飞行)/`enemyDamageDeadline`(= earliest − `enemyEtaSafetyMargin`(一个合法转向窗,200ms→12 ticks),唯一可作为"允许行动"依据);cbr 破环成本只计一次(原来 window 里再收一遍);环没了不收幻影破环;`ticksUntilFire` 计入敌方 fireReady。
- **targetValue horizon 修复**: `horizon = eta.total`(原来 reach 加了两遍)。
- **坐标协议**: `tankCell`→`tankCenterCell`(floor(中心)=corner+1,显式命名)。三种约定已在测试钉死: ThreatBudget center-floor / CoveragePlanner corner-floor / Navigator round(在格中点翻转,§210 教训)。
- 测试 `tests/godai-threat-budget.test.ts` 26 个全过(含 §4.1 单次计费、200/500ms 单调、§4.2 单调性、cbr 去重、确定性、坐标协议)。

### M2(完成,默认 OFF)
- **enemyBulletOnRay 重写为拦截段判定**(签名改为传 `aimDir`): 横向重叠 + 朝基地 + 炮口前方 + 未越过基地近缘(`v·(pos−nearEdge)≥0` 判越过)+ 速度可行性(追击需 vp>ve 且在基地缘前追上;ready 延迟含合法转向等待)。think.ts 三处调用已改传 `dir`。反例测试 9 个全过(`tests/godai-action-contract.test.ts` 18/18)。
- **midLaneDefense 两个站桩提交接入 actionContractMode**(对齐壳线持枪 hold + 哨位 in-hold),与 defenseIntercept/baseLaneSentry 统一;mode=0 短路。
- **intent 真正 slack**: `minSlack`→`committedSlack`(= deadline − killAssessment ETA);revalidation: currentSlack<0 释放、< committed−relax 释放、新威胁 deadline 差 >30 释放(`INTENT_THREAT_DELTA` 从 15 提到 30,原因: 1 格走近 ≈15.5 ticks,15 会被距离抖动触发)。

## 当前唯一失败测试(待接手者修)

`tests/godai-intent.test.ts:103` "holds the committed target within the lease": 敌 a(15,17) 已 commit,新敌 c(15,18) 出现后 selectTarget 返回了 c(row 17)而非 a(row 16)→ intent 被释放。

- 已排除: `INTENT_THREAT_DELTA`(提到 30 后仍失败)。
- 剩余嫌疑(均在 `src/ai/god/StrategyPlanner.ts` intentRead,我新加的两条):
  1. `currentSlack < 0` 释放 — killAssessment(w,p,a).playerKillEta ≥ enemyDeadline(a).enemyDamageDeadline?注意新 deadline 含 −12 margin 且语义是"首次伤害",openArena 后 a 是 walk 分支;
  2. `currentSlack < committedSlack − INTENT_SLACK_RELAX(10)` — committedSlack 在第一次 selectTarget 时写入;两次调用之间 field 只加了 c,killAssessment(a) 理论不变——除非第一次 selectTarget 走的 intentWrite 时机/玩家状态不同,或 stalls/flight 分支先释放。
- 调试入口: 复刻 `buildWorld/openArena/placePlayer/addEnemy`(helpers 在测试文件 24-79 行,`pinSeed` 种子 2),在 intentRead 各 return null 处打点。测试自身的历史意图: 更近的无害敌人不应释放 intent。
- 修法建议: 若确认是 `<0` 释放过于激进(几何下界+margin 对远敌天然负 slack?),考虑只在与威胁相关的敌人(csb/cbr)上施加 `<0` 释放,或回到"仅 collapse 超过 relax 才释放"。

## 待办(按协议顺序)

1. 修上述 intent 测试 → `bun run check` 全绿 → `bun run build`。
2. **默认行为 byte-identical 验证**(M0 通过条件): 用 failure subset 重跑 forensics 与 baseline JSON 对比,或 ab-param 同参对照(所有改动都在 mode=0 门控内,理论 byte-identical,但必须实证)。
3. **M3 反事实工具(协议主突破方向,未开始)**: `tools/diag/counterfactual-idle.ts`。要点:
   - 输入: `tmp/open-test-forensics-baseline.json` 的 base_destroyed 失败局;
   - 检测 idle 事件: 仿 `tools/sim/simulation-runner.ts` 主循环(sim.tick() → 读 `input._lastBranch/_moveDir/_fire` → endFrame() → consumeEvents),取"防守/hunt 分支提交 + _moveDir=null + _fire=false + onCooldown"且距首次基地受伤 ≤ 固定窗口、同段只取首个;
   - 分支: 用 `cloneWorld/restoreWorld`(src/snapshot/WorldSerializer.ts)+ 恢复 GodAI 的 rng 状态(RNG 有 getState/setState? 需确认)复制出 continue / turn-and-fire / move-to-intercept / clear-or-advance 四支,跑 60/120/240 ticks 短窗,记录 baseHp/目标死亡/有效输出/玩家死亡;
   - 分类: idle_causal / idle_legitimate / travel_or_turn_causal / unresolved;M3 通过条件: ≥10 个代表性失败逐 tick 人工抽查,自动分类一致率 ≥80%。
4. M4(统一行动候选)只有 M1–M3 通过后才允许。
5. 文档: `docs/god-ai-tuning.progress.md` 记 §215 实验日志(含 §213 CMA-ES 结论作废声明——旧"35 关搜索"实际只在 S1 上跑);`DECISIONS.md` 加压缩索引行。
6. 清理: `tmp/m0-*.log` 保留(baseline 佐证)。

## 本轮发现的重要事实(接手者必读)

- **`world.rules` 是对全局 `RULES[difficulty]` 的共享引用** — 测试里改 `turnCooldownMs` 会污染全局,必须 `w.rules = {...w.rules!, turnCooldownMs: X}` 克隆(tests 里已全部改为克隆)。
- **placePlayer 类 helper 返回共享 `w.player`** — 两次放置后旧引用指向新位置,必须按顺序快照。
- **createTank 从 world.rng 抽 speed jitter** — 测试需 reseed(threat-budget 测试用 20260816,intent 测试 pinSeed 种子 2)。
- 常量: hard turnCooldownMs=200(12 ticks);basic firePower=50;basic 弹速 flight(2 cells)≈2.88 ticks;basic cadence≈57.14 ticks;敌 walk tpc≈15.5 ticks/格;addEnemy(col,row) 在 threat-budget 测试文件里 center=(col+1,row+1),在 target-value/intent 文件里 center=(col,row)(后者用 (col−1)*CELL 放置)——两文件约定不同,易踩。
- BASE_POS.col=12,ring 列 = 11 与 14(不是 15)。
- 协议 §5.1 反例语义最终裁定: TRUE = 炮口前方 + 未过基地近缘 + 朝基地 + 速度可行;竖直向下弹的"未过"= pos<baseTop(v·(pos−nearEdge)<0 统一式)。
