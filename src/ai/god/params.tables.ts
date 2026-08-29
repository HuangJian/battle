// params.tables.ts — the four God-AI parameter tables (§3.1 split; pure
// relocation): DEFAULT (CMA-ES P4 R7), CLASSIC_MODEL restore (§115),
// SKILLED_HUMAN proxy (plan §3.3C), GUARD profile (§159).
import type { GodAIParams } from './params.interface'

/** Default God AI parameters — optimized via CMA-ES P4 round 7 (2026-07-29).
 * See .workbuddy/optimization-p4-r7/ for details.
 *
 * P4 R7 CMA-ES used the floor-aware v5.0 fitness over ALL 35 classic
 * stages × 20 seeds (18000 ticks), warm-started from R6, with the
 * then-active per-stage override table in the inner loop — the
 * optimizer pushed the global mean while the overrides guarded the
 * per-stage floor.
 *
 * P4 R7 truth-scale results (35 stages × 60 seeds, classic, 18000 ticks):
 *   Mean win rate: 81.9%  (target > 80% — PASS)
 *   Below 60% floor: 1/35 — S32 Diamond 52% (known structural hard case;
 *   verified not param-tunable at 60 seeds: manual probes on R6+R7 bases
 *   and a dedicated single-stage CMA-ES all scored at or below base).
 *
 * Key changes from P3:
 *   - defenseColSpread 9→5, threatRangeCells 20→10 (defense triggers only
 *     on real threats; the race-to-base check covers flankers)
 *   - maxPlayerDistFromBase 19→26 (roam freely; race check guards base)
 *   - baseRaceRangeCells 12→11, margin 2→0 (leaner race-to-base trigger)
 *   - t8MaxInterceptDistCells 2→8, baseWallScanRadius 1→3 (protect base
 *     bricks more actively)
 *   - powerupMaxDivertDistance 3→16 (power-ups are worth a detour)
 *   - endgameEnemyThreshold 4→6, huntAllyCount 6→1 (hunt earlier, alone)
 *   - aimError 0→0.03 (counter-intuitive: tiny aim noise breaks mutual-
 *     block standoffs; per-stage overrides set it back to 0 where armor
 *     density punishes wasted shots)
 */
export const DEFAULT_GOD_AI_PARAMS: GodAIParams = {
  reactionDelay: 0,
  aimError: 0.03030591179971963,
  suboptimalPathProb: 0,
  // §233: decision-chain throttle. A/B on hard 35×60: thinkInterval=2 costs
  // −2.8pp win rate (−0.022 mean score, 691/2100 outcome diffs) vs 1 — the
  // 1-tick decision latency is NOT free (dodge/fire windows). NOT shipped;
  // default 1 = byte-identical baseline. Kept as an experiment knob.
  thinkInterval: 1,
  // §290/M0b: intent-tagger observation hook — OFF default (byte-identical).
  intentTaggerMode: 0,

  defenseRowOffset: 1,
  // §115 (M4 round-2, 2026-08-04): full-corpus CMA-ES search shipped values —
  // hard/chaos (pool model) +5.0pp / +8.2pp at 60 seeds vs the §113 shipped
  // baseline (49.0% / 48.3% → 54.0% / 56.5%). See CLASSIC_MODEL_PARAMS below
  // for the instant/classic restore table (91% gate byte-identical).
  defenseColSpread: 3,
  threatRangeCells: 23,
  maxPlayerDistFromBase: 26,
  // P4: race-to-base emergency defense (see interface docs). Range 18 /
  // margin 2 — M4 search widened the race window (earlier, more committed
  // defense before the enemy reaches the base).
  baseRaceRangeCells: 18,
  baseRaceMarginCells: 2,
  // P4.2: retreat when 3+ enemies converge within 9 cells — the player
  // trades 1-for-1 at best in open crossfire; falling back to the defense
  // row funnels enemies into single-file corridors instead.
  // §115: M4 search disabled P4.2 (outnumberedEnemyCount 3→5 = never fires,
  // max 4 enemies alive) — the replan=1 + wider threat-range combo made the
  // nearby-retreat counterproductive; the field-wide M13 retreat still guards
  // the base. Kept as a knob (classic keeps 3/9 via CLASSIC_MODEL_PARAMS).
  outnumberedEnemyCount: 5,
  outnumberedRadiusCells: 7,
  t8MaxInterceptDistCells: 2,
  // §134 / 方向 D: 防守位停射拦截 — SHIPPED（2026-08-05, DECISIONS §134）。
  // A/B 官方口径：20-seed +8/+11/+8；60-seed hard +0.76pp（p=0.17，S32 +15pp /
  // S34 +10pp / Battlement +2.5pp）、chaos +2.15pp（p=0.0087 显著）→ 双难度净正。
  // classic（instant 1-HP）未 A/B — 经 CLASSIC_MODEL_PARAMS restore 0。
  defenseInterceptMode: 1,
  defenseInterceptMaxDist: 12,
  defenseInterceptRangeCells: 15,
  // §X / 基地车道哨兵: 0 = OFF（byte-identical）。1 = ON: 基地危局态
  // （环砖被拆 / 存在拆环者或能直射基地的车道敌人）下，玩家不再被
  // midLaneDefense(545)/closePickup(540)/pickupHigh(800) 摆布 — 锁定车道
  // 司机 → 走到与其同排/同列的站位（格对齐走廊判定，挡板为单层砖则打砖
  // 开路）→ 持位射击。权重 850 = interceptBase(900) 之下、pickupHigh(800)
  // 之上，威胁成立时压制远距拾取与中路锚定直到车道敌人被处理。
  // 来源（Battlement hard seed 14 弹道级还原）：拆环 fast 在 (16,25)↔(15,25)
  // 口袋横走被打到 24hp（一枪线），玩家在 (16,21) 与其同列 40 ticks — 但
  // 唯一一枪从 (16,23) 砖格内穿过被墙吃掉（双偏线扫描看见敌人、真实子弹
  // 中线打墙），下一抢要等 800ms 冷却而敌人已转身逃离；随后 midLaneDefense
  // 把玩家拖去中路横向火力送死。同一缺陷在 46/60 败局中复现。
  baseLaneSentryMode: 1,
  // §146 C: 哨兵站位搜索半径（曼哈顿格数）与开火距离上限。
  baseLaneSentryRange: 6,
  // §193-B/§198: 卫位导航 — SHIPPED（2026-08-15）。默认 1 = ON。
  // 当前基线（含 §195 sticky=90）决定性 60-seed paired A/B：
  // hard 净 +3（1591→1594，8 L→W / 5 W→L）、classic 净 0（byte-identical —
  // classic sentry mode=0 自关）、chaos 净 +10（1520→1530，17 L→W / 7 W→L）。
  // 与 §193-B 原 A/B（S34 +1 / 全关 +6 / classic 0 / chaos +2）三轮证据链一致。
  baseLaneSentryStation: 1,
  // §225-A: 带内应急进 lane — 候选（默认 0 = OFF, byte-identical）。A/B 后升格。
  baseLaneSentryInBandNav: 0,
  // §225-B: 危局拾取抑制 — 候选（默认 0 = OFF, byte-identical）。A/B 后升格。
  baseAlertPickupSuppress: 0,
  // §137 / 基地守位格: 默认防守位 (12,23) 在全部 35 关都是环砖、navigate 永远到不了
  // ——AI 没有有效防守锚点（Battlement 漏斗几何把这个洞暴露了）。默认 0 = OFF
  // （byte-identical）。A/B 候选：mode=1（Battlement 应选 (12,22) 前厅口）。
  baseGuardAnchorMode: 0,
  // Phase 2 §6.1 行动有效性契约: 默认 0 = OFF（byte-identical）。A/B 前不发货。
  actionContractMode: 0,
  // Phase 2 §6.2 目标价值排序键: 默认 0 = OFF（byte-identical）。A/B 前不发货。
  targetValueMode: 0,
  // Phase 2 §6.3 短期 intent: 默认 0 = OFF（byte-identical）。A/B 前不发货。
  intentMode: 0,
  intentLeaseTicks: 12,
  intentProgressWindowTicks: 10,
  // Phase 3 动态攻击覆盖点: 默认 0 = OFF（byte-identical）。A/B 前不发货。
  coverageMode: 0,
  coverageLeaseTicks: 12,
  coverageReplanTicks: 12,
  // M4 统一行动候选: 默认 0 = OFF（byte-identical）。A/B 前不发货。
  candidateMode: 0,
  // §217/§221/§228: M5 travel-phase fire-line detour — SHIPPED（2026-08-17,
  // DECISIONS §229, 用户拍板默认打开）。hard 35×60 三批验证: +12 wins /
  // base_destroyed 447→433（−14）/ lives 中性 / classic 0 / chaos +8 →
  // §218 gated 四项条款全满足。人工 playtest（?fireLineDetour=1 入口, §228）
  // 后默认 1 = ON。已知代价: S30 (Concentric) 赢局质量 −7% (score 0.905→0.84,
  // clearSpeed/baseIntegrity, 胜率 60-seed 0 翻转不变) — DECISIONS §229 权衡记录。
  fireLineDetourMode: 1,
  // §217 原值 (DETOUR_TURN_WINDOW_TICKS 同义)。§229 探针: S30 上 slack 13→26
  // 无实质提升 (0.839→0.843), 不设更高默认 — 无证据的改动违背纪律。
  fireLineDetourMinSlack: 13,
  // §137 v2: 受威胁且无 clear-shot 敌人时、玩家距守位格 ≤ 此值 → 驻守守位格
  // （让 §134 在前厅口拦截）。仅 mode>0 时读。A/B 候选：holdRange 0/6/10。
  baseGuardAnchorHoldRange: 6,
  // §139 / 方向 A（进攻侧）: 火力死区解除。默认 0 = OFF（byte-identical）。
  // A/B 候选：mode=1（Battlement 死区 34% 占用 → 去有射界的瞭望格重新接战）。
  firingLaneMode: 0,
  firingLaneRadius: 5,
  firingLaneMinEnemyDist: 4,
  firingLaneReplanTicks: 15,
  // D5 (plan §D5): base-box confinement for the §139 deadzone redirect
  // (0 = OFF, byte-identical to §139 mode=0). A/B candidate: 20 (rows 20-25).
  firingLaneBoxRow: 0,
  // §135 / 方向 D 预测版: 提前拦截格数。默认 0 = OFF（byte-identical 到 §134
  // SHIPPED——只拦已上车道者）。A/B 候选：predict=1/2/3。
  defenseInterceptPredictCells: 0,
  // §136 / 方向 D 破砖版: 预测命中但被场景砖挡时打砖开路。默认 0 = OFF
  // （byte-identical 到 §134——预测只在 scan.enemy 确认时才提交）。
  defenseInterceptDigBricks: 0,
  baseWallScanRadius: 5,
  // §D4 (2026-08-05): base-protection flag = exact ring cells (SHIPPED bug
  // fix). The legacy radius-5 rectangle flagged ordinary bricks near the base
  // as "base walls", poisoning dual-offset scans → break-through fire
  // suppressed at the real brick in front → spawn-pocket lock (Battlement
  // hard ~5%). classic: restored to 0 via CLASSIC_MODEL_PARAMS.
  baseWallExactRing: 1,
  replanInterval: 1,
  replanCache: 1,
  pickupReachCache: 1,
  powerupMaxDivertDistance: 18,
  endgameEnemyThreshold: 10,
  huntAllyCount: 1,

  // P0: Anti-camp / T2a deadlock fix (plan/God-AI-Next-Round).
  // campTimeoutTicks=20 (M4 search: far less patient — replan=1 keeps the
  // player moving, so camping patience is worth less) — if the player hasn't
  // gotten a kill in the timeout, something is wrong (enemy dodging, wall in
  // the way, etc.). antiCampSuppressTicks=60 (1s) — enough to move ~2 cells
  // at player speed, changing the tactical situation before T2a can re-trigger.
  campTimeoutTicks: 20,
  antiCampSuppressTicks: 60,
  // P0.3: navStuckTicks=180 (3s) — if the player hasn't progressed (stayed
  // at the same cell) for 3 seconds of navigating, force a roam to the map
  // center. This breaks pursuit loops with faster enemies.
  navStuckTicks: 180,
  // §168: zone-based nav-stuck detection — ON by default (hard/chaos).
  // The P0.3 escape (navStuckTicks=180) is defeated by center-cell jitter
  // without it: playerCell() bounces between two adjacent cells every few
  // ticks, resetting _navStuckTicks before it can reach 180. The ±1 zone
  // check keeps the counter alive through jitter. Classic keeps OFF via
  // CLASSIC_MODEL_PARAMS (byte-identical classic gate).
  navStuckZone: 1,
  // §168: escape suppression window — keep escaping for 60 HUNT evaluations
  // (1s) after a nav-stuck trigger so the player actually clears the region.
  navStuckSuppressTicks: 60,
  // §186: powerup stuck threshold — 5s of pixel-stuck before skipping
  // powerup navigation. Conservative: maze nav rarely exceeds 3s stuck.
  powerupStuckTicks: 300,
  // §146 B: 集合点可达性 — default 0 (OFF, byte-identical)。
  // defensePosStandableMinDist=8：仅远位（S8 口袋 dist 25-32）启用，近基不动。
  defensePosStandable: 0,
  defensePosStandableMinDist: 8,
  // §145: 冰上滑行控制 — default 0 (OFF, byte-identical)。iceGlideMinSpeed
  // 0.3 < ICE_ACCEL_TRACTION(0.35)：滑行中判定不压制正常起步/急停。
  iceGlideControl: 0,
  iceGlideMinSpeed: 0.3,

  // M0.5 退役（2026-08-03）: D1/D2 guardBand + damagedArmor、smartThreatModel
  // 族已退役归档。
  // Close-combat: default 15 (= AIM_RANGE_CELLS, unchanged behavior for
  // 1-HP enemies). For multi-HP enemies (armor), t2aHighHpMaxRange=2
  // triggers point-blank engagement (§56 — generalizes S32 close-combat).
  t2aMaxRange: 15,
  t2aHighHpMaxRange: 2,

  // §58: Stage-level adaptive params (Strategy G). These generalize the old
  // per-stage override mechanism into a data-driven adaptation based on
  // stage characteristics computed in reset(). Default ON
  // — the thresholds and adapted values are tuned to match the old overrides
  // exactly on the stages they covered (S26 Brick Maze, S32 Diamond), while
  // leaving other stages on the base params. See DECISIONS §58.
  armorAdaptRatio: 0.35,
  armorCampTimeoutTicks: 50,
  armorAntiCampSuppressTicks: 50,
  armorNavStuckTicks: 90,
  brickDenseAdaptRatio: 0.45,
  brickDenseReplanInterval: 30,
  brickDenseSuboptimalPathProb: 0.05,

  // §59 / Strategy C: clear-shot bonus in defense-mode target selection.
  // Default 500 — prioritizes enemies with a clear line of fire to the base.
  // 0 = OFF (byte-identical to pre-§59).
  defenseClearShotBonus: 500,

  // D2 / 拆环威胁: ring-breach score bonus in defense-mode target selection
  // (canBreachRingFrom — enemy aligned with an intact ring brick). Default
  // 0 = OFF (byte-identical to pre-D2). A/B sweep candidate: 300 (between
  // the proximity noise band and clearShotBonus 500).
  defenseBreachBonus: 0,
  // Dual central breach strategy (plan/dual-central-breach-strategy.md):
  // override values applied by computeStageAdaptedParams ONLY when
  // world.spectateDual === true && centralBreachRisk === true. Single-player
  // never touches the existing knobs (defenseBreachBonus=0 etc.) → byte-identical.
  dualCentralBreachDefenseBreachBonus: 600,
  dualCentralBreachAnchorMode: 1,
  dualCentralBreachStickyTicks: 30,
  dualCentralBreachDamageRecall: 1,
  dualCentralBreachMaxPlayerDistFromBase: 8,
  // §6.3: P2 fence pickup bypass + P1 dig-while-moving (dual central breach only).
  dualCentralBreachP2FencePickup: 1,
  dualCentralBreachP1DigFire: 1,
  // §177: P2 navigation — directMove instead of A* + enemy-spawn-point patrol
  // (dual central breach only; the gate short-circuits for single-player).
  // A/B (120-seed sweep, §177) showed directMove/patrol REGRESS win-rate (both
  // tanks still chase the same top threat, so 3 enemies keep digging the ring).
  // The effective fix is defenseSecond: P2 takes the runner-up threat so the two
  // tanks de-conflict and cover more lanes. directMove/patrol are left as opt-in
  // knobs (default 0) for future tuning; they are gated and never active in SP.
  // §180: directMove=1 enabled — fixes P2 spawn oscillation (A* ping-pong
  // left↔right for ~10s at game start). Neutral on 120-seed with the current
  // config (defenseSecond=1, anchorSplit=2). The §177 A/B regression (-1.7pp)
  // was measured before defenseSecond was the default.
  dualCentralBreachP2DirectMove: 1,
  dualCentralBreachP2Patrol: 0,
  dualCentralBreachP2PatrolEnemyDist: 6,
  dualCentralBreachP2PatrolRow: 0,
  dualCentralBreachP2DefenseSecond: 1,
  dualCentralBreachP2AnchorSplit: 2,
  // §178 (autopsy seed2): let carve punch through the central wall so both tanks
  // reach their guard anchors instead of being pinned at the top perimeter.
  // 99 base-column breaks / cost 5 (normal brick) ⇒ carve-dig escape routes
  // straight through the central brick wall (no steel in a central-breach stage,
  // so the intact ring still protects the eagle). §179 adds a P1 dig-fire
  // direction guard in think.ts that prevents P1 from firing DOWN at walls —
  // the primary protection against carving through the base's central shield.
  // Gated → SP byte-identical.
  dualCentralBreachCarveMaxBaseColumn: 99,
  dualCentralBreachCarveBaseColumnCost: 5,
  // §178: P1 central hold anchor (intercept col-12 spawn lane). (12,12) = mid-
  // board center: covers the col-12 spawn lane with LOS both up (snipe spawn)
  // and down (cover base approach) without being pinned at the top edge. -1 ⇒
  // auto via findDualCentralHoldImpl. Gated → SP byte-identical.
  dualCentralBreachP1Anchor: 1,
  dualCentralBreachP1AnchorCol: 12,
  dualCentralBreachP1AnchorRow: 12,
  // §178: sticky central hold — suppress P1 power-up diversion in dual central
  // breach. 1 = ON (pure defender). Gated → SP byte-identical.
  dualCentralBreachP1HoldSticky: 1,
  // §181: P1 directMove (same rationale as P2's dualCentralBreachP2DirectMove).
  // Fixes P1 spawn oscillation: A* ping-pong left↔right at 128↔136px while
  // enemies destroy the base. directMove goes straight up toward the anchor.
  dualCentralBreachP1DirectMove: 1,
  // §161 / 开路策略 (carve path): default OFF — byte-identical to pre-§161.
  // A/B-measured on hard (Stage 33 Battlement + all 35); flip per result.
  carvePathMode: 0,
  carveLowerRow: 13,
  carveAtPostCells: 2,
  carveChaseCells: 5,
  carveThreatDistCells: 8,
  carveMaxBaseColumn: 1,
  carveBaseColumnCost: 1e9,
  carveReplanTicks: 240,
  // §189 / 开局联通清墙: enabled — proactively clear lower-half brick walls
  // to connect base-left, base-right, and the defense post at game start.
  baseConnectClearMode: 1,
  baseConnectClearLowerRow: 13,
  baseConnectClearMaxKills: 1,
  baseConnectClearMaxTicks: 480,
  // §162 / nav 卡死破墙逃生 (nav-stuck break-out, user request 2026-08-06,
  // replay hard-s34 seed 2050197249): when every preferred direction is
  // blocked (directMove / followPath fallback, nav-stuck escape), also try
  // BREAKABLE directions (canMoveOrBreak) instead of only passable ones —
  // the Battlement spawn pocket is sealed by wide-box protection bricks the
  // player otherwise never breaks, oscillating at spawn for 17-30s. 0 = OFF.
  // §162: nav-stuck break-out SHIPPED (hard 60-seed A/B p=0.019, Battlement
  // +0.05, suite 75%→77%). Pixel-stuck carve-dig escape: when the player is
  // wall-blocked in a sealed spawn pocket (net displacement < carveDigNetEscape
  // px for carveDigBlockTicks), HUNT starts a persistent exact-ring-safe
  // carve-dig toward an escape target; followPath/directMove also fall back to
  // BREAKABLE directions when fully blocked. 1 = ON (default).
  navBreakStuck: 1,
  // §nav-cost 3.2: base ring brick multiplier. 1.5 = base ring bricks cost
  // 1.5× normal (1+0.5 extra). Tuned via gate scan {1.5,1.75,2.0,2.5}. The old
  // PoC's 1e6 caused S7/S12/S13 base losses (defender forced to绕行); 1.5 is
  // a温和 penalty that discourages breaking base walls without preventing it.
  navBaseRingMult: 1.5,
  // §nav-cost 3.3: gates the fire stop cost model. >0 = ON. When
  // navFireStopModel='firecontrol', the actual stop cost is computed
  // dynamically from tank.lastFire/nextFireInterval/dir/speed via
  // fireClearStopTicks() — this value is only the gate, not the cost.
  // The flat model (navFireStopModel='flat') uses this as a constant per-brick
  // cost. 2 was the tuned flat-model value; kept as the gate for firecontrol.
  navBrickStopCost: 2,
  // §nav-cost 3.3(c): firecontrol model — compute real stop ticks from
  // tank fire state (cooldown, direction alignment) via fireClearStopTicks(),
  // the shared pure function mirroring shouldFireInDir + think.ts cooldown.
  // A* tracks arriveTick + cooldownExpiry along the path via parallel buffers.
  navFireStopModel: 'firecontrol',
  // §162: carve-dig session cap — 45s max before giving up (Battlement
  // pocket exits in ~10-25s; 2700 ticks is generous but bounded).
  carveDigMaxTicks: 2700,
  // §162: pixel-stuck detector — 24px (1.5 cells) net escape; 90 ticks
  // (1.5s) of not moving that far = wall-blocked.
  carveDigNetEscape: 24,
  carveDigBlockTicks: 90,
  // §190: pixel-stuck directMove fallback — DEFAULT OFF (0). The feature was
  // merged from origin/idle but a paired A/B on --difficulty hard showed it is
  // NET-NEGATIVE: suite score 0.5308 (ON) → 0.5363 (OFF), paired Δ +0.0053,
  // p=0.0185 (significant). It failed to help its own target seeds (S31 Eagle
  // Nest 80%→85% better with it OFF) while dragging the weak tail. Disabled by
  // default 2026-08-13; kept as a gated, tunable path (set >0 to re-enable,
  // but note 300 caused chaos S5/S8 regressions and 480 still regressed hard).
  pixelStuckDirectMoveTicks: 0,
  // §163: 中路防守默认 OFF（byte-identical）。hold=1 cell、maxDist=8
  // （近基才锚定，防止与 hunt 跨图拉锯）、maxDig=3 cells（只接受短挖，
  // 避免重复挖刚逃出的密封口袋）。
  // §165 (user request 2026-08-07): SHIPPED ON — the base column has no
  // steel guard on many maps (S8 Riverbed, etc.), so enemies in the base
  // column can carve straight down to the eagle with流弹. The trigger is
  // precise (laneThreatImpl = actual enemy bullet in the base column heading
  // down with no steel/water between it and the base), so it does NOT fire on
  // mere enemy presence (§163 A/B: 29/35 stages worse with enemy-presence
  // triggers). 0 = OFF (byte-identical to pre-§165).
  midLaneDefense: 1,
  midLaneHoldRange: 1,
  midLaneMaxDist: 8,
  midLaneMaxDigCells: 3,
  // §164: mid-lane drill sticky — SHIPPED hard/chaos = 90 (2026-08-14,
  // DECISIONS §195; classic restore 0 via CLASSIC_MODEL_PARAMS). 60-seed
  // paired A/B: hard SUITE 0.5333→0.5380, S8 Riverbed 37%→45% (5 L→W, 0
  // W→L on seeds 1-30); classic/chaos no regression. Sweep 60/90/120/150/
  // 180/240 peaked at 90 — must bridge the 70-130 tick drill gaps without
  // over-anchoring the player (≥120 starts trading away map control).
  midLaneStickyTicks: 90,
  // §164: proactive mid-lane flank hold. 0 = OFF (byte-identical).
  // §165 round 2: A/B tested — CATASTROPHIC (-4.1pp). The enemy-near-lane
  // trigger fires 14-35% of ticks on most maps → player statue at the base
  // column, neglects enemy engagement. The reactive midLaneDefense (bullet-
  // only trigger) is the correct approach. Keep OFF.
  midLaneHold: 0,
  midLaneHoldMaxRow: 14,
  midLaneHoldEnemyDist: 12,

  // §132 / 方向 B: speed × base-proximity threat weight in defense target
  // selection. Default 0 = OFF (byte-identical — the scoring term short-
  // circuits). A/B sweep candidates: weight 500/1000 × range 10/12.
  fastBaseApproachWeight: 0,
  fastBaseApproachRangeCells: 10,

  // §60: Open-defense adaptation. On non-steel-maze stages, widen
  // baseRaceRangeCells from 11 to 14 for earlier threat detection. Steel
  // mazes (brick/(brick+steel) < 0.10) keep the default 11 — early retreat
  // hurts there because enemies bypass the defense position via corridors.
  openDefenseBrickWallRatio: 0.1,
  openDefenseBaseRaceRangeCells: 14,

  // §133 / 方向 C: brick-heavy defense tightening. Default 0 = OFF
  // (byte-identical — the adaptation block never runs). Candidate values
  // for the 20-seed sweep (all injected together via --params):
  //   mild   race=20 maxDist=20 fieldDist=16
  //   balance race=22 maxDist=18 fieldDist=12
  //   tight  race=24 maxDist=14 fieldDist=8
  brickHeavyDefenseWallRatio: 0,
  brickHeavyBaseRaceRangeCells: 22,
  brickHeavyMaxPlayerDistFromBase: 18,
  brickHeavyFieldDistCells: 12,

  // §61: Terrain-adaptive T2a range. On open-sightline stages (low forest or
  // high water), engage armor from range 4 instead of 2 — faster kills, less
  // damage taken. On forest-dense stages, keep point-blank (range 2).
  // §62: suppressed when armor ratio ≥ 25% — armor-heavy stages need
  // point-blank regardless of sightline (range 4 trades inefficiently).
  openT2aForestRatio: 0.15,
  openT2aHighHpMaxRange: 4,
  openT2aWaterRatio: 0.25,
  openT2aMaxArmorRatio: 0.25,
  openT2aSteelRatio: 0.15,
  // §62: forest-dense armor T2a range. On armor-heavy stages with forest
  // ≥ 25%, use range 3 (not 2) — the forest absorbs enemy bullets, giving
  // the player room to maneuver at range 3 without taking damage. Range 2
  // is too close (player takes point-blank hits), range 4 is too far in
  // forest (bullets hit trees). Probes: S14 +10pp with range 3.
  armorForestDenseRatio: 0.25,
  armorForestDenseRange: 3,
  // §64: armor-heavy + high-steel + non-steel-maze → widen outnumberedRadius.
  // Only S26 matches. Probes: S26 +10pp (75% → 85%, 30 seeds). 9 = no change.
  armorSteelOutnumberedRadiusCells: 12,
  // §66: steel-maze non-armor camp timeout. Only S6 matches. +16pp (60 seeds).
  steelMazeCampTimeoutTicks: 20,

  // M0.5 退役: §63 openT2a1HpMaxRange / §65 armorMazeSuboptimalPathProb /
  // crossfire 族（§68-v2/§69/§69-B）已退役归档（60-seed 验证
  // 均为净负或否决）。

  // §48-revisit: Steel-only evasion occlusion. 0 = OFF (byte-identical to
  // pre-§48-revisit). See interface docs. Only steel (permanent for enemy
  // bullets) is treated as occlusion; brick (temporary) is NOT — dodging
  // brick-blocked bullets is load-bearing anticipatory dodging (DECISIONS §48).
  // Default range 0 = suppress ALL steel-blocked (per-seed tick-diff showed
  // this is NET NEUTRAL — see interface docs for the pinning mechanism).
  evasionSteelOcclusion: 0,
  // Distance gate for steel occlusion (cells). 0 = no gate (suppress all).
  // >0 suppresses only blocked bullets at dist >= range.
  evasionSteelOcclusionRange: 0,
  // §48-revisit terrain gate: 0 = never auto-enable (byte-identical).
  // 0.10 = auto-enable occlusion on steel-maze stages (brickWallRatio below
  // 0.10 — S32 Diamond 0.063, S6 Iron Curtain 0.04). Brick-heavy stages
  // (S14 0.915, S26 0.254) stay OFF — they regress under occlusion (the
  // dodge is load-bearing repositioning). Verified 2026-08-01: 35×60 net 0
  // with zero per-stage regressions (S14/S26 byte-identical); 120-seed
  // confirmations S32 +2.5pp (68.3→70.8), S6 +0.8pp (80.0→80.8).
  evasionSteelOcclusionBrickRatio: 0.1,
  // M0.5 退役（2026-08-03）: trapAvoidance 族已退役归档
  // （默认 0 未发布；"包围风险"输入并入 v2 survive 候选设计 §3.2）。
  // §49-revisit: 炮口相向对枪抵消 (§52 v2). 1 = ON (current shipped
  // behavior, byte-identical to pre-parameterization). 0 = OFF (plain T2a).
  counterFire: 1,
  // Max range (cells) for the facing-enemy block. 5 = the original §52 v2
  // hardcoded value.
  counterFireMaxRange: 5,
  // §74: Steel-fire gate — 1 = ON (default). 0 = OFF (pre-§74 behavior,
  // A/B baseline). See interface docs.
  steelFireGate: 1,
  // §80: Turn-snap aim guard — 1 = ON (default, the fix). 0 = OFF (pre-§80
  // behavior, A/B baseline). See interface docs.
  aimTurnSnapGuard: 1,
  // §84: Aggressive stall detection — 120 ticks (2s). 0 = OFF (byte-identical).
  aggCampTimeoutTicks: 120,
  // §85: Close-range enemy exposure check — 1 = ON (default). 0 = OFF.
  closeCombatDangerCheck: 1,
  // §85: max distance (cells) for the exposure check. Default 2 (point-blank)
  // — at range 4 the check was too aggressive, causing -1.6pp regression by
  // cancelling legitimate navigation. At range 2, the check only fires when
  // the enemy is truly adjacent (32px), where fleeing is almost certainly death.
  closeCombatDangerRange: 2,
  // §153-W2: fire-rate-aware close combat. 0 = OFF (byte-identical to §85).
  // A/B candidate: 1. Promote to default only after a clean hard 35-stage
  // sweep (see DECISIONS §153).
  closeCombatDuel: 1,
  // M5: 站位提前规避 — 0 = OFF (byte-identical to M0). 1 = ON (A/B knob).
  // §165 round 2: OFF — detection fixes (tightened alignment 32px→19px +
  // steel-only occlusion + 1-cell lookahead) reduced false positives from
  // -1.5pp to -0.7pp, but the direction swap itself is fundamentally harmful
  // (disrupts navigation, sends into dead-ends). bulletLaneWait=1 handles
  // immediate collisions; DODGE handles approaching bullets. 0 = OFF.
  pathThreatAvoidance: 0,
  // §153-W1: wait-for-bullet body-proximity margin. 0 = OFF (byte-identical
  // baseline). A/B candidate: 6-8. Promote to default only after a clean hard
  // 35-stage sweep (see DECISIONS §153).
  bulletLaneWait: 1,
  // ── §86 oscillation-experiment params (A/B-only knobs) ──────────────
  // Evaluated for the §86 dodge-oscillation fix. Only `dodgeOscillationCounterFire`
  // ships ON. The other three are A/B-only and are NEVER part of the shipped
  // default (intentionally left OFF — see interface docs). The canonical fix
  // is the simulation-layer turn cooldown (§86c), not these AI-layer patches.
  // M0.5 退役（2026-08-03）: dodgeHysteresis / dodgeDirPersistence /
  // canMoveDirFloorSnap 已退役归档（A/B 均净负，从未发布；
  // §86c 模拟层转弯冷却为规范修复，dodgeOscillationCounterFire 为唯一发布项）。
  dodgeOscillationCounterFire: 1,

  // ── §M3: Dodge quality (plan/God-AI-Redesign-v2 M3) ──────────────────
  // dodgeCounterFire (round 1, distance-gated) REVERTED to OFF (DECISIONS
  // §98): official-shape 35x20 showed chaos 34.6→34.1% (flat-to-negative)
  // with a deterministic S25 Ice Palace regression (5/20→1/20 — counter-fire
  // interrupted a working dodge mid-move). Round 3 (DECISIONS §101) replaces
  // the distance gate with the PINNED gate (isDodgePinnedImpl) — still OFF by
  // default; the A/B runs in the M3 milestone. The old distance-gate param
  // dodgeCounterFireRangeCells was removed (§101: the pinned gate is
  // geometric, not distance-based); the live emergency-range gate is a
  // hardcoded 5 * CELL at candidates/Dodge.ts.
  // Align 6px = bullet half-width sum (cancellation needs a near-dead-on shot).
  dodgeCounterFire: 0,
  dodgeCounterFireAlignPx: 6,
  dodgeClearanceScore: 0,
  // M9: survival-horizon dodge commitment (DECISIONS §107 pending) — OFF by
  // default; the A/B runs in the M9 milestone. See interface docs for the
  // measured failure mode (commitment failure 32-35% of dodge deaths).
  dodgeHorizonScore: 0,
  dodgeEscapeDepth: 0,
  // §223: multi-bullet centroid escape (counterfactual-dodge hard-away arm —
  // 75.3% survival vs 0% factual in the death-window probe). When ≥2 enemy
  // bullets threaten within 6 cells, the default dodge path picks the
  // passable+safe direction maximizing distance AWAY from the bullet
  // centroid (vs the legacy binary next-cell pick). 0 = OFF (byte-identical).
  dodgeCentroidMode: 0,
  // M10: time-margin + distance-to-base gates for the horizon commitment
  // (both 0 = no gate = pure M9 semantics; A/B knobs, DECISIONS §108 pending).
  dodgeHorizonMinMarginTicks: 0,
  dodgeHorizonMaxDistCells: 0,
  // M12: player HP buffer awareness (DECISIONS §112) — OFF by default; the
  // A/B runs in the M12 milestone. All five params 0 = byte-identical.
  playerHpAwareness: 0,
  hpDangerHits: 0,
  hpDangerCommitMargin: 0,
  hpTradeHits: 0,
  hpTradeCommitPenalty: 0,

  // ── §87: Urgent power-up pickup priority (user request 2026-08-02) ───
  // SHIPPED default: ON with the A/B-validated ranges/gates (DECISIONS §87).
  // 35×60 A/B (classic, 18000t): 1899/2100 → 1908/2100 (+9 wins), win rate
  // 90%→91%, suite 0.7439→0.7551, net flips +54/−45, zero significant
  // regressions (Lattice −8→−2, Star Fort −5→+1 after the gates).
  // Setting pickupPriorityMode=0 (and/or any gate to 0) restores the
  // pre-§87 behavior — the OFF state was verified byte-identical via
  // per-seed tick-diff.
  pickupPriorityMode: 1,
  pickupPriorityHighRange: 8,
  pickupPriorityMidRange: 4,
  pickupPriorityLowRange: 2,
  pickupPriorityMaxDanger: 0,
  pickupPriorityMinEnemyDist: 5,
  pickupPrioritySpawnRowMax: 3,
  // D5 (plan §D5): star/tank base-box gate relaxation (0 = OFF, byte-identical
  // to §87). A/B candidate: 20 (rows 20+).
  pickupStarBoxRow: 0,
  // E1 / 道具经济: 危急道具拾取 (0 = OFF, byte-identical). A/B candidate: 1
  // with minEnemies 3 / approach 6 / ringLow 4 / range 10.
  direItemMode: 0,
  direItemMinEnemies: 3,
  direItemApproachCells: 6,
  direItemRingLow: 4,
  direItemRangeCells: 10,

  // ── §88: 据守咽喉要地 (chokepoint holding) ────────────────────────
  // SHIPPED ON (2026-08-03, DECISIONS §93/§94): 120-seed A/B on S6/S16/S32
  // confirmed 全面持平或提升 (S6 0.000, S16 +0.018, S32 +0.011; win 83→85%,
  // 13 better / 8 worse / 339 tied), no stage regression. Tuned knobs:
  // margin 1 (威胁点外 1 格 — A/B round 3 从用户规格 2 下调: margin=2 在
  // S32 把玩家拖离击杀过频), hold threshold 2 (敌人数目 > 2), minRow 13
  // (下半区), steel 10 / brick 1 (钢铁优先远高于砖墙), facing gate ON,
  // 4 paths/enemy, replan every 30 ticks, chaseMaxDist 3 / holdMaxDist 6 /
  // chaseMaxPlayerDist 10 (速度缩放).
  chokepointMode: 1,
  threatPointMargin: 1,
  chokepointHoldThreshold: 2,
  chokepointMinRow: 13,
  chokepointSteelWeight: 10,
  chokepointBrickWeight: 1,
  chokepointFacingGate: 1,
  chokepointPathsPerEnemy: 4,
  chokepointMaxThreatDist: 14,
  chokepointReplanTicks: 30,
  // A/B round 2 (per-seed tick-diff): chase 分支把玩家引去追距威胁点 10 格
  // 的远敌（S15 seed 24），拖慢清场。chase 的本意是拦截「即将到达威胁点」
  // 的敌人——距威胁点超过 chaseMaxDist 格不算紧迫威胁，fall-through 到原
  // 最近敌人追杀（S6/正常选择），与 OFF 字节相同。
  chokepointChaseMaxDist: 3,
  // A/B round 2 (per-seed tick-diff): 玩家到达据守点后敌人已转向、威胁路径
  // 消失，缓存计划仍锁死玩家守株待兔（S19 seed 23：玩家在 (4,20) 空转 ~1200
  // tick 直到 base 从另一侧被破）。到达据守点后若威胁态已解除（threatState
  // false），fall-through 到正常目标选择。
  // A/B round 3: 据守点超过 6 格（chokepointHoldMaxDist）不值得走过去——
  // 敌人中途转向/被杀，玩家空转且路径残留污染导航（S26 seed 12）。
  chokepointHoldMaxDist: 6,
  // A/B round 3: chase 目标距玩家超过 10 格同样不值得追（S32 seed 10：玩家在
  // (8,3) 被引去 27 格外追 (0,22)，敌先到威胁点，玩家白跑整局）。
  chokepointChaseMaxPlayerDist: 10,

  // ── M3: 敌情感知 EnemyModel + 命数感知 (plan/God-AI-Redesign-v2 §4.2b/§4.3) ──
  // 全部默认 OFF/0 = 逐字节不变（M3 里程碑验收：开启后无回退，而非默认启用）。
  // enemyModelMode: 0=OFF, 1=纯动态, 2=混合（+静态 aiState 先验）。
  enemyModelMode: 0,
  // EMA 窗口（ticks）。>0 且 enemyModelMode>0 时模型每 tick 更新。
  enemyModelWindowTicks: 0,
  // 感知敌人强度 → T9 威胁加权（FireControl）。
  tierWeightScale: 0,
  // 感知闪避率（discipline）→ 有效 T2a 射程缩放（ENGAGE）。
  dodgeRateShrinksT2a: 0,
  // 配合压力（coordination）→ 保命压力提前。
  coordinationRiskWeight: 0,
  // 命中率阈值（0..1）→ 保命压力提前。
  enemyAccuracyRaisesSurvival: 0,
  // 混合模式静态先验权重（0 = 无先验）。
  enemyTierWeightCommander: 0,
  enemyTierWeightVeteran: 0,
  // 命数 ≤ 该值激活保命压力（chaos 3 命 → 1 = 末命保命）。
  survivalModeLives: 0,
  // 保命压力下高风险候选（hunt/远距 engage）的抑制系数。
  survivalRiskWeight: 0,
  // survive 候选（主动换位）：0 = OFF（不提交）。
  surviveMinEnemies: 0,
  surviveEnemyRadiusCells: 3,
  // M13: 全场压力撤退（DECISIONS §113，SHIPPED 2026-08-04）— 默认 ON，
  // pool 模型专属（classic instant 无磨血死亡，91% 门禁字节不变）。
  // 60-seed：hard +2.3pp / chaos +0.6pp（无 chaos 负向；基地失守与死亡
  // 双难度均下降）。ON4@10 实测有害（-5.3pp 过于被动）——3 只即撤 + 15 格。
  outnumberedFieldRetreat: 1,
  // §115: M4 search widened M13's field retreat (enemies 3→4, dist 15→26) —
  // with replan=1 + wider threatRange the player defends more dynamically and
  // the retreat fires only in truly full-pressure states. 60-seed cross-check
  // (HARD_BEST set): hard +4.2pp / chaos +8.6pp vs shipped — net positive
  // even with the retreat weakened.
  outnumberedFieldEnemies: 4,
  outnumberedFieldDistCells: 26,
  // §146 C: M13 pickup gate — 0 = OFF (byte-identical). Pool-model only
  // (the predicate itself checks combatModel === 'pool', so classic stays
  // byte-identical with or without a CLASSIC restore entry).
  fieldRetreatPickupGate: 0,
  // 自杀秒回 (suicide quick-return, §116/§117): default OFF — A/B tested
  // (per-seed tick-diff + §117 forensics) before enabling, per the §88
  // methodology. When ON, the player trades a life for a better position to
  // save the base.
  suicideReturnMode: 0,
  suicideReturnBulletTimeTicks: 60, // 1s
  suicideReturnEnemyDistTicks: 300, // 5s
  suicideReturnMinLives: 2, // at least 1 spare life
  suicideReturnSpawnDistCells: 6, // spawn within 6 cells of the threat enemy
  suicideReturnStandMaxTicks: 300, // 5s — mode-2 standing timeout
  // §118 strict-doom guard (modes 2/3): 0 = OFF — A/B tested before enabling.
  suicideReturnBaseHpFrac: 0, // base must be at/below this × baseMaxHp
  suicideReturnDefendDistCells: 0, // player must be farther than this from base
  // §121 t2a/aggressive 停射自毁守卫 — SHIPPED default 2 (lenient). A/B
  // (35 关 × 120 seeds × hard+chaos, 3 arms): strict(mode 1) regresses
  // (hard −29 / chaos −24 flips — over-suppresses legitimate kill shots),
  // lenient(mode 2) wins on both (hard +12 / chaos +8 flips, Δbase_destroyed
  // −7/−12, guardBlocks 16K vs 82K). Classic restored to 0 via
  // CLASSIC_MODEL_PARAMS (instant 1-HP combat has zero margin — untested,
  // keep byte-identical per §115).
  selfFireBaseGuard: 2,
  // §152: S12 replay fixes — SHIPPED defaults. 0 = OFF (A/B baseline).
  // W3 (pickupCommitTicks) is NOT shipped: the 35×60 hard A/B + per-seed
  // isolation showed the commit persistence is net-negative — on the 4
  // Battlement (S34) flip seeds it hijacks base defense (all 4 runs die with
  // baseHp=0), and on S12 seed 934391936 it turns the W1+W2 win back into a
  // loss (each fix alone wins; ALL ON loses). The W3 oscillation window is
  // already fixed by W1+W2's trajectory change (the player navigates the
  // window instead of bouncing). Kept as an experimental knob, 0 = OFF
  // (byte-identical).
  t2aSteelPathBlock: 1,
  // §193-C: 中线火力门 —— SHIPPED（2026-08-13, DECISIONS §193-C）。
  // A/B 官方口径（60-seed，三工具交叉验证）：hard 全关净 +41（147/106）、
  // S34 净 +2（9/7）；chaos 全关净 +6（127/121）、S34 净 −5。full 版（无
  // march-dig 豁免）为何更优：豁免版（仅抑制非行进方向）只剩 hard +2 /
  // chaos 0 — march-dig 大多是 6px 中线实心砖的无效挖路，抑制反而盘活
  // 冷却弹窗。classic instant 未 A/B — 经 CLASSIC_MODEL_PARAMS restore 0。
  centerLineFireGate: 1,
  // §193-D: 预测前移门 — SHIPPED（2026-08-13, DECISIONS §193-D）。
  // 60-seed A/B：hard 全关净 +7（16/9，无崩盘关，最差 S10 -2）、classic +1、
  // chaos +1（S34 +1）。子弹飞行期内目标滑出命中窗时抑制必miss开火
  // （P2.4 接管时机窗口）。S34 触发面小（fast 1.2px/tick 慢，2-3 格内弹道
  // 先到）——门主要作用在 waist/ring 区远距横走目标。
  predictiveFireGate: 1,
  aggNavStuckTicks: 120,
  pickupCommitTicks: 0,

  // §156: freeze-window power-up pickup (unlimited range).
  // Default 999: during freeze, pick up ANY reachable power-up before
  // stop-and-aim. Enemies are frozen — zero threat. The frozen enemy is a
  // free kill that will still be there after the pickup.
  // (§156-v2: changed from 2 to 999 per user request — freeze = safe traverse)
  freezePickupRange: 999,

  // §158: non-freeze close-range power-up pickup.
  // Default 2: in normal mode, pick up power-ups within 2 cells when no
  // bullet threat is active (DODGE declined). Range 4 caused seed-999
  // base-destroyed (player 19 cells from base); range 3 caused seed-2
  // lives-exhausted (player 22 cells from base). Range 2 is safe for both
  // split-parity seeds — conservative but still grabs adjacent items.
  // No enemy-proximity gate — close items are worth grabbing even with
  // enemies nearby.
  closePickupRange: 2,

  // §166 / B1: star rush (星经济冲刺). Default 0 = OFF (byte-identical).
  // A/B arms: mode=1 × range 8/12 × liftGates 0/1 on the hard 35×20 screen.
  starRushMode: 0,
  starRushMaxLevel: 2,
  starRushRangeCells: 8,
  starRushLiftGates: 1,

  // §167 / B4: super-item strategic activation — RE-ENABLED by default
  // (2026-08-28, unfreeze DECISIONS §293; reverse of AI-No-Items-Warmstart
  // M0 / DECISIONS §167-rev / §289). The God AI presses F5 (guard, when the
  // base is under threat) / F6 (frenzy, when the facing corridor holds an
  // enemy and no bullet is inbound). Original 60-seed paired A/B (§167):
  // hard 75.9→76.5% (L→W 25 / W→L 12, z=2.14 significant); chaos neutral.
  // M0 measured the no-items cost at −1pt hard (Lattice 65→58%, p=0.0002).
  // superItemFrenzyAim stays 0 (archived knob — §273/§293, L1 guard).
  // classic kept 0 via CLASSIC_MODEL_PARAMS (§115); guards never activate via
  // GUARD_GOD_AI_PARAMS (they own no inventory).
  superItemMode: 1,
  superItemGuardThreat: 1,
  superItemFrenzyAim: 0,

  // §157: base clear-shot threat detection.
  // Default 1: an enemy with a clear line of sight to the base IS a threat,
  // regardless of distance. The next bullet could destroy the base.
  baseClearShotThreat: 1,

  // §169: base-threat signal stickiness — default 0 (OFF, byte-identical).
  // Candidate value 120 (2s ≈ one flicker period); needs A/B before shipping.
  threatStickyTicks: 0,

  // §170: hunt commit — default 0 (OFF, byte-identical). Candidate value
  // 120 (2s); needs A/B before shipping.
  huntCommitTicks: 0,

  // §171: path-aware target selection — default 0 (OFF, byte-identical).
  // Candidate value 1; needs A/B before shipping.
  pathTargetMode: 0,

  // §172: bonus enemy hunt bias — default 2 (= historical hardcoded constant,
  // byte-identical). Candidate values 4 / 6; needs A/B before shipping.
  bonusHuntBias: 2,

  // §173: base damage recall — default 0 (OFF, byte-identical). Arm 1
  // (unconditional) net −24; candidate arm 12 (distance gate); needs A/B.
  baseDamageRecall: 0,

  // §179: emergency base defense — default 0.25 (base at 25% HP triggers
  // forced return). 0 = OFF (byte-identical).
  emergencyBaseHpFrac: 0.25,
  // §179: freeze-period base-priority targeting — default 1 (ON).
  freezeBasePriority: 1,

  // §159: T2a defense override — allow ENGAGE when a close enemy is in the
  // line of fire, even past maxPlayerDistFromBase. 4 cells = quick kill range
  // (bullet arrives in ~15 ticks; one-shot for 1-HP kinds, 2-3 shots for armor).
  t2aDefenseOverrideRange: 4,
  t2aOutnumberedRetreat: 0,
  t2aOutnumberedRange: 8,
  t2aOutnumberedCount: 2,

  // §187: Guard/P2 A* player-obstacle + target blacklist + powerup overlap
  navAvoidPlayer: 1,
  targetBlacklistStuckTicks: 240,
  targetBlacklistDuration: 180,
  powerupEnemyOverlapSkip: 1,

  // §302/§303: pursuit-tail navigation (merge behind the chase target instead
  // of firing from a parallel lane). ENABLED 2026-08-29 (DECISIONS §303) after
  // the user-directed yield-then-tail redesign (AlongMode=3) measured net +29
  // on hard 35×60 — the best arm of the three-round §302 program (arc −39/−58/
  // +1/−4/+16/+29 tracking mechanic completeness). plan/Intent-Policy-NN-Plan.md
  // §12.1 defect #3.
  pursuitTailMode: 7,
  pursuitTailCells: 2,
  pursuitTailMinCells: 3,
  pursuitTailMaxCells: 9,
  pursuitTailMaxLaneGap: 4,
  pursuitTailAlongWindow: 3,
  pursuitTailAlongMode: 3,
}

/**
 * §115 (M4 round-2): instant/classic restore table. The M4 search was
 * SINGLE-SOURCE (refactor.trae.md §1.1-2): DEFAULT_GOD_AI_PARAMS is the only
 * authoritative table; CLASSIC_MODEL_PARAMS = { ...DEFAULT, ...CLASSIC_OVERRIDES }
 * below, so a param added to DEFAULT auto-propagates to classic unless
 * explicitly restored here. The GodAIInput.reset() loop only restores keys
 * still at DEFAULT → byte-identical to the prior Partial table.
 * optimized on the POOL combat model (hard/chaos — HP buffers, 磨血死亡).
 * classic ('instant': flat per-bullet damage, 1 hit ≈ death for most kinds)
 * has no 磨血死亡 and the search-tuned aggression is MEASURED HARMFUL there
 * (classic 91.0% → 88.6% at 35×20 if the M4 defaults leak in). GodAIInput.reset()
 * applies this restore when world.rules.combatModel === 'instant', keeping
 * the classic regression gate byte-identical (DECISIONS §115).
 */
const CLASSIC_OVERRIDES: Partial<GodAIParams> = {
  defenseColSpread: 5,
  threatRangeCells: 10,
  baseRaceRangeCells: 11,
  baseRaceMarginCells: 0,
  outnumberedEnemyCount: 3,
  outnumberedRadiusCells: 9,
  t8MaxInterceptDistCells: 8,
  baseWallScanRadius: 3,
  // §D4: exact-ring base-wall flag is a pool-model (radius-5) fix; classic
  // uses radius 3 where the false positive cannot occur — restore 0
  // (byte-identical classic gate).
  baseWallExactRing: 0,
  replanInterval: 50,
  // §233: decision-chain throttle is a pool-model (hard/chaos) perf fix —
  // classic instant 1-HP 未 A/B，restore 1（byte-identical classic gate）。
  thinkInterval: 1,
  // §190: classic has replanInterval=50 (stable path) — no A* oscillation.
  // Restore 0 (byte-identical classic gate).
  pixelStuckDirectMoveTicks: 0,
  powerupMaxDivertDistance: 16,
  endgameEnemyThreshold: 6,
  campTimeoutTicks: 90,
  outnumberedFieldEnemies: 3,
  outnumberedFieldDistCells: 15,
  // §121: the lenient self-fire base guard is a pool-model (hard/chaos) fix.
  // classic is instant 1-HP combat with zero margin for suppressed kill shots
  // and was never A/B'd here — restore 0 (byte-identical classic gate).
  selfFireBaseGuard: 0,
  // §134: 防守位停射拦截是 pool-model（hard/chaos）修复 — classic instant
  // 1-HP 未 A/B，restore 0（byte-identical classic gate）。
  defenseInterceptMode: 0,
  // §X: 车道哨兵是 hard/chaos 基地防御池修复 — classic instant 未 A/B，
  // restore 0（byte-identical classic gate）。
  baseLaneSentryMode: 0,
  // Phase 2 §6.1: 行动有效性契约未在任何难度 A/B — restore 0（byte-identical
  // classic gate；且 classic 防御分支本就 restore 0，无站桩提交可管）。
  actionContractMode: 0,
  // Phase 2 §6.2: 目标价值排序键未在任何难度 A/B — restore 0（byte-identical
  // classic gate）。
  targetValueMode: 0,
  // Phase 2 §6.3: 短期 intent 未在任何难度 A/B — restore 0（byte-identical
  // classic gate；微旋钮 intentLeaseTicks/intentProgressWindowTicks 在
  // intentMode=0 下不可读，保持默认值）。
  intentMode: 0,
  // Phase 3: 动态攻击覆盖点未在任何难度 A/B — restore 0（byte-identical
  // classic gate；微旋钮在 coverageMode=0 下不可读，保持默认值）。
  coverageMode: 0,
  // §193-B: 卫位导航 —— classic instant 未 A/B，restore 0（byte-identical）。
  baseLaneSentryStation: 1,
  // §195: 中路钻探粘性驻守是 hard/chaos 基地防御修复 — classic instant
  // 未 A/B（classic S8 本就 100%），restore 0（byte-identical classic gate）。
  midLaneStickyTicks: 0,
  // §145: 冰上滑行控制未在 classic 上 A/B — restore 0（byte-identical
  // classic gate，classic 同样有 S25 Ice Palace 冰关，后续可单独评估）。
  iceGlideControl: 0,
  // §146 B: 集合点可达性未在 classic 上 A/B — restore 0（byte-identical）。
  defensePosStandable: 0,
  // §189: 开局联通清墙是 hard/chaos pool-model 修复 — classic S34 regressed
  // 10/20 < floor 16，restore 0（byte-identical classic gate）。
  baseConnectClearMode: 0,
  // §152: 三项 S12 修复均为 pool-model（hard/chaos）调优，classic instant 未 A/B
  // —— restore 0（byte-identical classic gate）。
  t2aSteelPathBlock: 0,
  // §193-A: 中线火力门 —— classic instant 未 A/B，restore 0（byte-identical）。
  centerLineFireGate: 0,
  // §193-D: 预测前移门 —— classic instant 未 A/B，restore 0（byte-identical）。
  predictiveFireGate: 0,
  aggNavStuckTicks: 0,
  pickupCommitTicks: 0,
  // §159: T2a defense override is a pool-model (hard/chaos) fix — classic
  // instant 未 A/B，restore 0（byte-identical classic gate）。
  t2aDefenseOverrideRange: 0,
  // §165: T2a outnumbered retreat is a pool-model (hard/chaos) fix — classic
  // instant 未 A/B，restore 0（byte-identical classic gate）。
  t2aOutnumberedRetreat: 0,
  t2aOutnumberedRange: 5,
  t2aOutnumberedCount: 2,
  // §167 / B4: super-item guard activation is a pool-model (hard/chaos)
  // A/B — classic instant 未 A/B，restore 0（byte-identical classic gate）。
  superItemMode: 0,
  // §168: nav-stuck zone detection is a pool-model (hard/chaos) fix —
  // classic instant 未 A/B，restore 0（byte-identical classic gate）。
  navStuckZone: 0,
  navStuckSuppressTicks: 0,
  // §186: powerup stuck detection is a pool-model fix — classic OFF.
  powerupStuckTicks: 0,
  // §169: threat signal stickiness is a pool-model (hard/chaos) fix —
  // classic instant 未 A/B，restore 0（byte-identical classic gate）。
  threatStickyTicks: 0,
  // §170: hunt commit — pool-model fix, classic 未 A/B，restore 0。
  huntCommitTicks: 0,
  // §171: path-aware target selection — pool-model fix, classic 未 A/B，restore 0。
  pathTargetMode: 0,
  // §172: bonus hunt bias — restore the historical constant 2（classic 未 A/B）。
  bonusHuntBias: 2,
  // §173: base damage recall — pool-model fix, classic 未 A/B，restore 0。
  baseDamageRecall: 0,
  // §179: emergency base defense + freeze base priority — pool-model
  // (hard/chaos) fixes, classic instant 1-HP 未 A/B，restore 0（byte-identical）。
  emergencyBaseHpFrac: 0,
  freezeBasePriority: 0,
  // §187: guard/P2 A* player-obstacle + target blacklist + powerup overlap —
  // pool-model (hard/chaos) fixes, classic instant 未 A/B，restore 0
  // （byte-identical classic gate）。
  navAvoidPlayer: 0,
  targetBlacklistStuckTicks: 0,
  targetBlacklistDuration: 0,
  powerupEnemyOverlapSkip: 0,
  // §nav-cost: A* brick cost model is a pool-model (hard/chaos) tuning —
  // classic instant 1-HP 未 A/B，restore 0/0（byte-identical classic gate）。
  navBaseRingMult: 0,
  navBrickStopCost: 0,
  // §nav-cost 3.3(c): firecontrol model gated by navBrickStopCost=0 → OFF.
  navFireStopModel: 'flat',
  // §302: pursuit-tail is a pool-model (hard/chaos) candidate — classic
  // instant 1-HP 未 A/B，restore 0（byte-identical classic gate）.
  pursuitTailMode: 0,
}

/**
 * Classic restore = single-source spread (refactor.trae.md §1.1-2). Every
 * value not listed in CLASSIC_OVERRIDES above is inherited verbatim from
 * DEFAULT_GOD_AI_PARAMS, so the table has exactly one authoritative copy.
 */
export const CLASSIC_MODEL_PARAMS: GodAIParams = {
  ...DEFAULT_GOD_AI_PARAMS,
  ...CLASSIC_OVERRIDES,
}

/**
 * Skilled Human proxy parameters (plan §3.3C): God AI + double reaction
 * delay + 20% aim error. Represents an experienced but non-perfect human.
 * MUST remain derived from God params — God gets stronger → human proxy
 * gets stronger automatically. Strategy thresholds are inherited as-is.
 * Minimums ensure the human is always weaker than God (even when God is perfect).
 */
export const SKILLED_HUMAN_PARAMS: GodAIParams = {
  ...DEFAULT_GOD_AI_PARAMS,
  reactionDelay: Math.max(2, DEFAULT_GOD_AI_PARAMS.reactionDelay * 2),
  aimError: Math.max(0.15, DEFAULT_GOD_AI_PARAMS.aimError + 0.15),
  suboptimalPathProb: Math.max(0.15, DEFAULT_GOD_AI_PARAMS.suboptimalPathProb * 1.5),
}

/**
 * §159 / 天降神兵守卫 (base guard, DECISIONS §31 Phase 2): the God AI profile
 * for allied guards — the same decision pipeline as the God AI player, with
 * two deliberate deltas:
 *
 * 1. **Imperfection sims zeroed** (`aimError` / `suboptimalPathProb`). Those
 *    two params exist to imitate a HUMAN player. A computer-controlled base
 *    guard plays perfect, and — critically — with both at 0 every
 *    `rng.next()` result is CONSTANT, so the guard's decisions are pure
 *    functions of World state. This is what makes the guard AI byte-identical
 *    across the original run and replay playback: `world.seed` differs
 *    between recording and playback (PlaybackController restores the
 *    snapshot, not the seed) and `genId()` is not reproducible across
 *    Worlds, so a seed-dependent guard brain could not be faithful to the
 *    recorded run. (A mid-run REWIND restores the World but not the brain's
 *    history-dependent counters — the same accepted semantics as the player
 *    GodAIInput — and the constant draw results guarantee a rewind can never
 *    introduce RNG-seed divergence.) Note: `computeStageAdaptedParams` may
 *    still re-enable `suboptimalPathProb` on brick-dense stages (§58) —
 *    SimulationEnemies re-zeros both after `reset()` (see there).
 *
 * 2. **Power-up targeting disabled**. Guards are allies — SimulationPowerUps
 *    only grants pickups to `w.player`/`w.player2` — so every pickup branch
 *    (PICKUP_HIGH/MID/LOW, CLOSE_PICKUP, DIRE, freeze-window, aggressive
 *    pickup) would be wasted navigation for a base defender. Each gate is
 *    zeroed explicitly (`powerupMaxDivertDistance: 0` also disables the S5
 *    base economy; a dist-0 item the guard happens to stand on is inert —
 *    navigateTowards(own cell) returns null).
 *
 * Everything else is inherited: the guard dodges enemy bullets, intercepts
 * base-bound fire (T8), holds a defense position (§137), stop-and-aim
 * engages (T2a), and — crucially for an ally — never fires at base
 * protection bricks or unpierceable steel (T6/T11/§121, enforced inside
 * shouldFireInDir, which the §159 yield also uses as its fire gate).
 */
export const GUARD_GOD_AI_PARAMS: GodAIParams = {
  ...DEFAULT_GOD_AI_PARAMS,
  // §162: guards must NOT inherit the player's nav-stuck carve-dig — guard
  // yield/stand behavior (§159/§160) is replay-locked and a guard digging
  // through walls could unseat the player's own §159 yield lane. Keep 0.
  navBreakStuck: 0,
  // §168: guards keep exact-cell nav-stuck detection — their yield geometry
  // is replay-locked (same reason navBreakStuck is zeroed above).
  navStuckZone: 0,
  navStuckSuppressTicks: 0,
  // §186: guards don't use powerup stuck detection.
  powerupStuckTicks: 0,
  // §169: guards keep the raw (non-sticky) threat signal — their defense
  // behavior is replay-locked (same reason navBreakStuck is zeroed above).
  threatStickyTicks: 0,
  // §170: guards never hunt-commit — their targeting is replay-locked.
  huntCommitTicks: 0,
  // §171: guards keep Manhattan-nearest targeting — replay-locked.
  pathTargetMode: 0,
  // §172: guards keep the historical −2 bonus bias — replay-locked.
  bonusHuntBias: 2,
  // §173: guards keep the raw (non-damage) threat signal — replay-locked.
  baseDamageRecall: 0,
  // §164: guards must NOT hold the mid-lane (their §159/§160 yield geometry is
  // replay-locked; wandering to the plaza would unseat the player's lane).
  midLaneHold: 0,
  // §167: guards never activate super items — they own no inventory (pickups
  // resolve for w.player/w.player2 only) and a guard pressing F5/F6 would be
  // dead code at best, a determinism hazard at worst.
  superItemMode: 0,
  aimError: 0,
  suboptimalPathProb: 0,
  pickupPriorityMode: 0,
  closePickupRange: 0,
  freezePickupRange: 0,
  direItemMode: 0,
  powerupMaxDivertDistance: 0,
  // §166 / B1: guards don't collect pickups (SimulationPowerUps grants them
  // to players only) — keep the star rush off for the same reason the other
  // pickup branches are zeroed above.
  starRushMode: 0,
  // §165: guards don't do T2a outnumbered retreat (replay-locked yield).
  t2aOutnumberedRetreat: 0,
}
