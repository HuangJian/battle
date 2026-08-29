// params.interface.ts — the GodAIParams type + its 218-field documentation
// (plan/refactor.trae.md §3.1). Split out of the former monolithic
// params.ts (3,635 lines = interface + 4 param tables + stage-adapt logic);
// pure relocation, zero semantic change.
import type { ActionId } from './DecisionCore'
// ============================================================
// Parameters
// ============================================================

/**
 * Configurable parameters (plan/God-AI-Tuning §1).
 *
 * Contains both "imperfection" params (reactionDelay, aimError,
 * suboptimalPathProb) and "strategy/threshold" params (defense positioning,
 * threat ranges, replan intervals, etc.). All tunable values live here per
 * the plan's "implementation discipline" — data over code.
 */

/**
 * ─── 留档旋钮清单（指针）── refactor.trae.md §1.1-1 + plan/God-AI-Organization.md §6 C1 ──
 * 「默认关闭 / 被否决但保留」的实验旋钮已数据化为**本文件底部的 ARCHIVED_KNOB_GROUPS**
 * （唯一事实源；tests/godai-archived-knobs.test.ts 消费：断言每个门控在 DEFAULT 表中 === 0）。
 * 新增实验旋钮时在该常量登记，勿再维护散文清单（两源必漂移）。完整 A/B 结论见
 * DECISIONS.md 与 docs/god-ai-tuning.progress.md；2026-08-25 版散文清单见 git 历史。
 */
export interface GodAIParams {
  // ---- Imperfection params ----
  /** Ticks of delay before reacting to a new threat. */
  reactionDelay: number
  /** Probability of a fire-control mistake. */
  aimError: number
  /** Probability of taking a suboptimal route step. */
  suboptimalPathProb: number

  // ---- Strategy / threshold params ----
  /** How many rows above the base to position for defense. */
  defenseRowOffset: number
  /** Max columns to shift from base column for defense. */
  defenseColSpread: number
  /** Only consider enemies within this Manhattan distance from base as threats. */
  threatRangeCells: number
  /** Player returns to base if farther than this from the base. */
  maxPlayerDistFromBase: number
  /**
   * P4: race-to-base check range. An enemy within this Manhattan distance
   * of the base triggers emergency defense IF the player would lose the
   * race back (player farther from base than the enemy, plus margin).
   * Catches flanking runners that the static ±3-col threat box misses
   * (S6 Iron Curtain: enemies sweep the bottom edge from the far corner
   * while the player roams the steel maze 20+ cells away).
   */
  baseRaceRangeCells: number
  /**
   * P4: safety margin (cells) for the race check. The player must be at
   * least this many cells CLOSER to the base than the enemy to keep
   * hunting; otherwise it returns to defense.
   */
  baseRaceMarginCells: number
  /**
   * P4.2: outnumbered retreat — if at least this many live enemies are
   * within `outnumberedRadiusCells` of the player, fall back to the
   * defense position instead of pressing the attack. Fixes the S18
   * (Frozen Field) failure family: the AI over-extends into the corridor
   * band below the enemy spawn rows and dies in 3-way crossfire.
   * Set to 5+ to effectively disable (max 4 enemies alive on field).
   */
  outnumberedEnemyCount: number
  /** P4.2: radius (cells) for the outnumbered check. */
  outnumberedRadiusCells: number
  /** T8: max distance (cells) to travel to intercept a base-bound bullet. */
  t8MaxInterceptDistCells: number
  /**
   * §134 / 方向 D (defense-position lane intercept): 0 = OFF (byte-identical).
   * 1 = ON: when the player is within `defenseInterceptMaxDist` of the base
   * (i.e. at/near the defense position) and a live enemy is ALREADY aligned
   * with the base — `enemyCanShootBase`, it can destroy the base with its
   * next bullet — AND aligned with the player (same row/col, clear LOS), the
   * player stops and fires at that enemy WITHOUT leaving the defense
   * position (outranked by dodge/interceptBase/aggro; weight 550 > engage 500
   * so it pre-empts the aimDir-based T2a which may be aiming elsewhere).
   *
   * Why (hard 35×120 forensics, §131-§133): three prior directions failed on
   * Battlement (base lost, 59% fast killers) — T8 intercept (bullet already
   * in flight), target re-rank (fast 4.5cps outruns the 1★ player), defense
   * distance tightening (returning early = chasing kills for the enemy). The
   * survivor is to shoot the enemy ON the base's firing lane from the defense
   * position — the base's own defenders stop the rush before it fires.
   */
  defenseInterceptMode: number
  /** §134: max player dist-to-base (cells) for the lane-intercept to apply. */
  defenseInterceptMaxDist: number
  /** §134: max player→enemy distance (cells) for the intercept shot. */
  defenseInterceptRangeCells: number
  /** §X 基地车道哨兵: 0 = OFF（byte-identical）; 1 = ON（doc 见默认值处）。 */
  baseLaneSentryMode: number
  /** §X: 哨兵站位搜索半径与开火距离上限（曼哈顿格数）。 */
  baseLaneSentryRange: number
  /** §193-B: 卫位导航（station-approach）— 非对齐/被挡时走向 ±1 列清晰站位。0 = OFF（v5 字节不变）。 */
  baseLaneSentryStation: number
  /** §225-A: 带内应急进 lane — ring 已破 + 敌人已在基地带内（row ≥ 23）+ 玩家
   * 与敌人不同列时，横移到敌人同列（colGap ≤ 3，保持当前行）堵口；到位后由
   * 上方对齐开火段接管。补站台导航（仅服务带外 row 20-22）的带内空白
   * （§225 实证：62.5% 败局窗口内 sentry 0 tick）。0 = OFF（byte-identical）。 */
  baseLaneSentryInBandNav: number
  /** §225-B: 危局拾取抑制 — ring 已被击穿时 MID tier（star/tank/shield）拾取
   * 让位（救不了基地）；HIGH tier（bomb/freeze/fence）豁免（危局有效道具，
   * §180 fence=基地结构防守）。0 = OFF（byte-identical）。 */
  baseAlertPickupSuppress: number
  /**
   * §137 / 基地守位格 (base guard anchor): 0 = OFF (byte-identical — the
   * defense position stays at (BASE_POS.col, baseRow − defenseRowOffset),
   * which sits on the base protection ring on ALL 35 stages and is therefore
   * never reachable by navigate). 1 = ON: when the default defense position
   * is not standable, compute a guard cell in the base box (cols bc−2..bc+3
   * × rows br−3..br+1) scoring ring-defense coverage + lane coverage + cover
   * − distance, and hold it as the defense anchor. Battlement picks (12,22)
   * — the row-22 antechamber mouth above the ring — intercepting enemies
   * before they breach the ring (data-driven, no stage-name overrides §81).
   */
  baseGuardAnchorMode: number
  /**
   * Phase 2 / plan/God-AI-Hard-Breakthrough-Implementation.md §6.1 — 行动有效性
   * 契约: 0 = OFF（byte-identical）。1 = ON: defenseIntercept / baseLaneSentry
   * 的「站立 + 冷却中」提交（_moveDir=null, _fire=false — M0 台账 60% 失败族的
   * 无产出站桩）必须先通过 ActionContract.contractStandingHold — 敌弹在射线
   * （拦截在即）/ 自己子弹在飞（击杀在落地）/ 站桩击杀 killSlack>0（本次开火
   * 在基地期限前击杀）任一成立才允许提交；否则放弃本分支（fall through 到
   * engage/hunt/navigate 产生输出）。绝不否决有移动或有开火的提交 — §199 弃守
   * 教训不重演。
   */
  actionContractMode: number
  /**
   * Phase 2 / plan/God-AI-Hard-Breakthrough-Implementation.md §6.2 — 进攻分支
   * 动态目标价值排序键: 0 = OFF（byte-identical）。1 = ON: selectTarget 的 hunt
   * 分支（canHunt / 正常最近敌）不再按曼哈顿距离排序，改按
   *   targetValue(e) = expectedBaseDamagePrevented(e) / (reachEta + killEta)
   * 取最大 — expectedBaseDamagePrevented = 击杀落地前该敌会对基地造成的伤害
   * （fp × floor((horizon − enemyToShootEta) / (cadence+flight))，cap baseHp），
   * horizon = 到达+瞄准+冷却+再装填+末弹飞行。价值随敌人期限（越接近 csb 越
   * 高）、玩家距离（越近 horizon 越小）与射击成本（再装填次数）动态变化 —
   * 不是静态 bonusHuntBias。与 pathTargetMode（§171 真实路径成本）正交可叠加。
   */
  targetValueMode: number
  /**
   * Phase 2 §6.3 (plan/God-AI-Hard-Breakthrough-Implementation.md §6.3):
   * short-term action intent for hunt/engage target selection. When > 0, the
   * chosen hunt target is locked as an intent with a lease
   * (intentLeaseTicks) and revalidation: released when the target dies or
   * becomes unreachable, the lease expires, the player stalls (no move, no
   * fire for intentProgressWindowTicks), the target's base-damage deadline
   * tightens past the committed slack, a new enemy appears with a clearly
   * worse deadline, or the target flees beyond expectedProgress. This is NOT
   * a longer huntCommitTicks — the intent has an expiry, progress and
   * threat constraints (plan §6.3). The defense cascade runs above
   * selectTarget, so an intent never delays threat response. Mode 0 = plain
   * per-tick selection (byte-identical). A/B 前不发货.
   */
  intentMode: number
  /** ActionIntent lease in ticks (plan band 6~15). Only read when intentMode > 0. */
  intentLeaseTicks: number
  /** Stall-release window: player unmoved and not firing for this many ticks releases the intent. */
  intentProgressWindowTicks: number
  /**
   * Phase 3 (plan/God-AI-Hard-Breakthrough-Implementation.md §7): dynamic
   * attack coverage point. When the base is NOT under threat but a major
   * threat (damage deadline inside the horizon) exists, holding a coverage
   * point (throat / lane / firing intersection) with positive intercept
   * slack beats roaming hunt — the S34/S8 fix for "回基地驻守反而失去全场
   * 压制". Candidates are geometric only (no stage IDs), capped at 8;
   * scoring per §7.2 (Σ damage prevented − travel − turn − exposure), move
   * only when the best point beats the baseline by a margin AND ≥1 threat
   * has positive slack; §7.3 guardrails hard-block (3+ enemies with a
   * tighter second threat / independent rays not both covered / player too
   * far with return ETA > base slack). The point is a lease
   * (coverageLeaseTicks), released on threat death, flank threat, slack ≤ 0
   * or expiry; falls back to the normal hunt. Mode 0 = OFF (byte-identical).
   * A/B 前不发货.
   */
  coverageMode: number
  /** Coverage point lease in ticks (plan band 6~15). Only read when coverageMode > 0. */
  coverageLeaseTicks: number
  /** Low-frequency cache grid: full candidate re-score at most this often. */
  coverageReplanTicks: number
  /**
   * M4 (plan/God-AI-Hard-Open-Test-Protocol.md §7, 2026-08-16): 统一行动候选.
   * When the base has a DIRECT threat (csb/cbr), compare four fixed
   * candidates — kill-current / intercept-base / clear-lane /
   * return-defense — on the M1 metric set (killSlack / interceptSlack /
   * firstOutputTick / secondThreatRisk) and commit the first one passing
   * every §7.2 gate (safe-deadline slack > 0, real output not a statue, no
   * second threat closing, fire rays never cross an intact ring cell —
   * S30s27). Gates closed → the existing branch cascade runs unchanged
   * (M3: the window usually closes BEFORE the stall, so the layer only
   * preempts genuinely winnable states). Weight 860, below interceptBase.
   * Mode 0 = OFF (byte-identical). A/B 前不发货.
   */
  candidateMode: number
  /**
   * §217 (open-test round 2): travel-phase fire-line detour — 决策点前移.
   * During HUNT/navigate travel, when an aligned, ray-clear, off-cooldown
   * target (csb/cbr/base-approach band) sits one turn away with killSlack >
   * 13, turn + fire this tick instead of continuing the nav plan (probe:
   * 33% of baseline losses have this opportunity, 75% before first base
   * damage; S3s46 walked past a +209-slack target firing 0 shots in 187t).
   * One turn window (13t) of movement is the detour cost — killSlack > 13
   * guarantees the kill still beats the enemy deadline. Pure geometry, no
   * RNG perturbation. Mode 0 = OFF (byte-identical). A/B 前不发货.
   */
  fireLineDetourMode: number
  /**
   * §217 M5 detour minimum killSlack (ticks). One turn window (13t) of
   * movement is the detour cost — killSlack > 13 guarantees the kill still
   * beats the enemy deadline. (S30 tuning probes: slack 13/18/22/26 showed
   * no headroom — the S30 quality drop is structural, DECISIONS §229.)
   */
  fireLineDetourMinSlack: number
  /**
   * §137 v2: max player→anchor distance (cells) for the anchor HOLD to
   * apply. When the base is under threat, no enemy has a clear shot at it
   * yet, and the player is within this many cells of the guard anchor, hold
   * the anchor instead of chasing — the §134 lane-intercept shoots enemies
   * crossing the approach band before they reach the ring. 0 = never hold
   * (only the v1 defense-position replacement applies). Only read when
   * baseGuardAnchorMode > 0.
   */
  baseGuardAnchorHoldRange: number
  /**
   * §139 / 方向 A（进攻侧）: 火力死区解除 (firing-lane re-engage). 0 = OFF
   * (byte-identical). 1 = ON: when the player has NO enemy LOS in any of the
   * 4 directions (dead zone — standing with nothing to shoot) and all live
   * enemies are beyond firingLaneMinEnemyDist (too far to chase directly),
   * the FIRING_LANE candidate searches the radius-firingLaneRadius box for
   * the best standable cell that can SEE an enemy (same row/col, clear LOS)
   * and navigates there instead of idling. Re-engage, not hold — the
   * opposite of §137/§138 (which parked the player at a guard cell).
   * Battlement: 34% of all ticks parked in the (11,24) firing dead zone is
   * the #1 output bottleneck (shots/run 24.9 vs 67.7 on winning stages).
   */
  firingLaneMode: number
  /** §139: lookout-search radius (cells) around the player. */
  firingLaneRadius: number
  /** §139: enemies closer than this (cells) are chased directly, not diverted. */
  firingLaneMinEnemyDist: number
  /** §139: ticks between lookout-cell re-searches (throttle). */
  firingLaneReplanTicks: number
  /**
   * D5 (plan §D5): confine the §139 firing-deadzone redirect to the BASE
   * BOX — the candidate only fires when the player's row >= firingLaneBoxRow
   * (target 20). §139 failed (mode 0 archived) because the trigger ran
   * across the whole maze: no LOS with distant enemies is the NORMAL maze
   * state, so the player churned between lookout cells instead of pressing.
   * Inside the base box the same state is a genuine deadzone (Battlement:
   * parked fireless at (11,24) while the right wing breaches the ring).
   * 0 = OFF (byte-identical to §139 mode=0).
   */
  firingLaneBoxRow: number
  /**
   * §135 / 方向 D 预测版: cells of approach lead-time to ALSO intercept. 0 =
   * OFF (byte-identical to §134 SHIPPED — only enemies already ON the lane,
   * enemyCanShootBase, trigger). >0: an enemy that shares the base's column
   * (or the base's row) and is FACING the base within this many cells of the
   * lane is treated as an imminent base threat too — the DEFENSE_INTERCEPT
   * candidate stops and fires at it from the defense position BEFORE it
   * reaches the ring and fires (Battlement: the fast arrives at the ring and
   * fires before the static §134 predicate ever becomes true — §135 closes
   * that final-approach gap). The candidate still re-verifies LOS via
   * scanAheadImpl before committing (brick between player and enemy blocks
   * the shot, same as §134).
   */
  defenseInterceptPredictCells: number
  /**
   * §136 / 方向 D 破砖版: when the §135 predict predicate fires but the shot
   * is blocked by a SCENE brick (not the base-protection ring, not steel),
   * the player fires at the brick to open the lane (first shot clears the
   * brick, the enemy walking in eats the next). 0 = OFF (byte-identical to
   * §134 SHIPPED — predict commits only when scan.enemy confirms a clear
   * shot). 1 = ON.
   *
   * §135 probe: the predict predicate hit 168× on Battlement but ZERO
   * became commits — every one was blocked by the scan LOS check. Most were
   * the base ring (correctly untouchable — never dig baseWall), but on
   * stages where scene bricks sit between the defense position and the
   * enemy's approach lane, digging opens a real firing window the enemy
   * must cross. Uses shouldFireInDirImpl (default allowWallFire=true) which
   * inherently forbids baseWall/steel — the bullet only ever clears scene
   * brick.
   */
  defenseInterceptDigBricks: number
  /** S7: cells around the base to scan for wall integrity. */
  baseWallScanRadius: number
  /**
   * §D4 (2026-08-05): base-protection flag semantics. 0 = legacy loose
   * rectangle (baseWallScanRadius × ≤2 band) flags ANY brick near the base —
   * including ordinary bricks that are NOT bullet-stopping ring cells
   * (Battlement (10,19): dc=2/dr=5). On a dual-offset scan, one line hitting
   * such a far brick suppresses break-through fire at the REAL ordinary brick
   * in front → the player never digs → spawn-pocket lock (Battlement hard
   * ~5% win rate). 1 = SHIPPED fix: flag only the actual ring cells
   * (identical predicate to SimulationCombat.isBaseProtectionCell — row 23
   * cols 11-14, cols 11/14 at rows 24-25). classic restored to 0 via
   * CLASSIC_MODEL_PARAMS (radius 3 there — the false positive cannot occur;
   * classic gate byte-identical).
   */
  baseWallExactRing: number
  /** Re-plan interval (ticks). */
  replanInterval: number
  /**
   * §231 (perf): decision-chain throttle. 1 = think() runs every tick
   * (byte-identical baseline). >1 = the full candidate chain runs every Nth
   * tick and the previous _moveDir/_fire is HELD on off-ticks. A/B on hard
   * 35×60 REFUTED the naive throttle: thinkInterval=2 costs −2.8pp win rate
   * (−0.022 mean score, 691/2100 outcome diffs) — the 1-tick decision
   * latency is NOT free (dodge/fire windows). Kept as an experiment knob
   * only; default 1 (see DECISIONS §231).
   */
  thinkInterval: number
  /**
   * §290/M0b: intent-tagger observation hook (plan/Intent-Policy-NN-Plan.md
   * §5.1). 0 = OFF (byte-identical, default). >0 = collectIntentSample pushes
   * an IntentLogSample every INTENT_REPLAN_TICKS — pure observation, zero
   * RNG/World access (tagger.ts); state lives on the GodAIInput instance and
   * reset() clears it. ON/OFF byte-identical asserted by tests.
   */
  intentTaggerMode: number
  /**
   * §127 (perf): 1 = cross-tick replan cache ON (default). 0 = OFF
   * (byte-identical to pre-§127 — replanImpl re-runs full A* every tick).
   *
   * replanInterval defaults to 1, so followPath→replanImpl ran A* EVERY tick
   * — measured 73-89% of all findPath calls (docs/perf-optimization.progress.md
   * §2.10). The cache reuses the (playerCell, target) key + 60-tick safety
   * timer discipline of the §68 navigateTowards cache, applied to the MAIN
   * navigation path §68 missed. replanImpl draws NO RNG, so the cache is
   * BYTE-IDENTICAL — no §68-style signature relaxation needed.
   */
  replanCache: number
  /**
   * §129 (perf): 1 = pickup-reachability memo ON (default). 0 = OFF
   * (byte-identical to pre-§129 — powerUpCellReachable runs corridor A*
   * then dig A*, uncached, on every query).
   *
   * §129 turns the pickup reachability check into a strict pure memo of
   * (playerCell, target, terrain): 1) the corridor A* call is dropped
   * (breakBrick's search space strictly contains the corridor space, so the
   * boolean answer is identical — halves the per-query A* count), and 2) the
   * dig A* result is memoized under the §127-style (playerCell, target) +
   * tileMap.revision key. findPath draws no RNG, so this is byte-identical.
   */
  pickupReachCache: number
  /** S5: max distance (cells) to divert for a power-up. */
  powerupMaxDivertDistance: number
  /**
   * S6/S10: enemies remaining in the spawn queue at which the AI switches
   * from defense to aggressive hunting (plan/God-AI-Curriculum §5.3).
   * Previously hardcoded to 3 (too conservative — 0% win rate root cause).
   * Raising it lets the AI start clearing earlier instead of turtling.
   */
  endgameEnemyThreshold: number
  /**
   * S6: max enemies *alive on field* at which the AI may enter hunt mode
   * (plan/God-AI-Curriculum §5.3). Replaces the hardcoded `2` in `canHunt`.
   * Set to 4 (= MAX_ENEMIES_ALIVE) so the binding constraint is
   * `endgameEnemyThreshold` (queue count), not field count.
   */
  huntAllyCount: number

  // ---- P0: Anti-camp / T2a deadlock fix (plan/God-AI-Next-Round) ----
  /**
   * P0.1: max ticks the player may camp (stop-and-aim) at the same cell
   * with no kills before falling through to navigate. Eliminates the T2a
   * deadlock where the player stops and fires at a wall/enemy forever.
   */
  campTimeoutTicks: number
  /**
   * P0.1: ticks to suppress T2a camping after an anti-camp escape. Ensures
   * the player gets enough consecutive navigate ticks to actually move
   * away from the stuck position (otherwise the next tick re-enters T2a
   * and the cycle repeats with no movement).
   */
  antiCampSuppressTicks: number
  /**
   * P0.3: max ticks the player may stay at the same cell in the navigate
   * branch without a kill before forcing a roam to the map center. Breaks
   * pursuit loops where the player chases a faster enemy indefinitely.
   */
  navStuckTicks: number
  /**
   * §168: zone-based nav-stuck detection. 0 = OFF (exact-cell comparison,
   * byte-identical). 1 = the `_navStuckTicks` same-cell check becomes a
   * ±1-cell zone check (same as §152 aggNavStuckTicks): sub-pixel jitter
   * that flips `playerCell()`'s center between two adjacent cells no
   * longer resets the counter every few ticks (S34 Battlement s8: pinned
   * ~1200 ticks, nst never exceeded 6, the 180-tick escape never fired).
   */
  navStuckZone: number
  /**
   * §168: nav-stuck escape suppression window (HUNT evaluations). 0 = OFF.
   * When navStuckZone triggers the escape, keep escaping for this many
   * HUNT evaluations — a single trigger is not enough (leaving the zone
   * resets the counter and the oscillating target selection pulls the
   * player straight back). Only consulted when navStuckZone > 0.
   */
  navStuckSuppressTicks: number
  /** §162: nav-stuck break-out — try breakable directions when fully blocked. */
  navBreakStuck: number
  /**
   * §nav-cost 3.2: Base-protection brick multiplier for A* breakBrick
   * pathfinding. 0 = OFF (byte-identical — no extra cost for base ring
   * bricks). When > 0, base ring bricks cost (navBaseRingMult) instead of
   * 1, discouraging the AI from breaking its own base walls.
   *
   * The old PoC used 1e6 (impassable), which caused S7/S12/S13 base losses
   * because the sole defender was forced to绕行 instead of guarding near
   * the base. 1.5–2.0 is the safe range (plan §3.2).
   */
  navBaseRingMult: number
  /**
   * §nav-cost 3.3: Fire stop cost added to every brick edge in breakBrick
   * A* (flat model). Represents a constant worst-case time cost of clearing
   * a brick. 0 = OFF (brick = 1 = empty, per §3.1). When `navFireStopModel`
   * is 'firecontrol', this value gates the firecontrol model ON (> 0) or
   * OFF (0), but the actual stop cost is computed dynamically from the
   * tank's real fire state (cooldown, direction alignment) — not this
   * constant. A turn cost of 1 is also added when the path changes direction
   * at a brick cell (the tank can't fire during a turn).
   */
  navBrickStopCost: number
  /**
   * §nav-cost 3.3(c): Selects the fire stop cost model for A* breakBrick
   * pathfinding.
   * - 'flat': constant `navBrickStopCost` per brick edge + 1 turn cost.
   *   This is the §190 approximation — simple but doesn't reflect real fire
   *   state.
   * - 'firecontrol': dynamic stop ticks computed from the tank's real fire
   *   state (tank.lastFire, tank.nextFireInterval, tank.dir, tank.speed).
   *   The A* loop tracks cooldown along the path via `fireClearStopTicks` —
   *   the shared pure function that mirrors `shouldFireInDir`'s geometric
   *   alignment + `think.ts`'s cooldown logic. This is the §3.3(c) "与
   *   FireControl 联动" implementation.
   *
   * Gated by `navBrickStopCost > 0` (0 = OFF = old brick=5 behavior).
   */
  navFireStopModel: 'flat' | 'firecontrol'
  /**
   * §162: carve-dig session timeout (ticks). When navBreakStuck > 0 and the
   * player is nav-stuck, the AI starts a persistent carve-dig session toward
   * an escape target (findCarveEscapeImpl) and follows the exact-ring-safe
   * dig path until it exits the sealed pocket or this timeout expires.
   */
  carveDigMaxTicks: number
  /**
   * §162: net displacement (px) from the anchor that counts as "real
   * movement" — resets the pixel-stuck counter. Player speed ≈ 0.7px/tick,
   * so a free player re-anchors every ~20 ticks; a wall-blocked pocket
   * oscillater stays within a few px and never re-anchors.
   */
  carveDigNetEscape: number
  /** §162: min consecutive blocked ticks to trigger the carve-dig. */
  carveDigBlockTicks: number
  /**
   * §190: min pixel-stuck ticks (_digBlockTicks) to bypass A* pathfinding
   * and use directMove. When the player has been pixel-stuck for this many
   * ticks AND no carve-dig is active, directMove takes over — it picks a
   * stable direction based on the target's relative position, breaking the
   * A* first-step oscillation caused by replanInterval=1 + target movement.
   * 0 = OFF (byte-identical). Default 300 (5s) — above the nav-stuck escape
   * (180 ticks = 3s) so that mechanism fires first, but well below the 10s
   * idle-alert threshold.
   */
  pixelStuckDirectMoveTicks: number
  /**
   * §186: min pixel-stuck ticks (_digBlockTicks) to skip powerup
   * navigation in all pickup branches. When the player has been
   * pixel-stuck for this many ticks, the A* path to the powerup is
   * likely blocked/unreachable — skip the powerup and let HUNT's
   * nav-stuck escape run. 0 = OFF (byte-identical). Default 300 (5s)
   * — conservative: only fires after 5s of true immobility, well
   * below the 15s idle-alert threshold but above normal maze nav.
   */
  powerupStuckTicks: number
  /**
   * §163: mid-lane defense mode. 0 = OFF (byte-identical). When ON, the
   * player anchors to the base-column lane defense point (standable cell
   * above the base, carve-dug when sealed): the base lane has no steel
   * guard on many maps, so an enemy in the base column can carve straight
   * down to the eagle while the player hunts side lanes. See the
   * MID_LANE_DEFENSE candidate for the full semantics.
   */
  midLaneDefense: number
  /**
   * §164: 中路钻探粘性驻守 — once a lane drill bullet is seen in the base
   * column (laneThreatImpl true), keep MID_LANE_DEFENSE engaged for this
   * many ticks even after the bullet dies on a brick. 0 = OFF
   * (byte-identical).
   *
   * Root cause (S8 Riverbed hard forensics): an enemy parked in the base
   * column above the base fires down repeatedly; each bullet chews 1-2
   * bricks and dies, so laneThreatImpl flickers ON only ~10-60 ticks per
   * shot with 70-130 tick gaps. The player starts walking to the lane
   * point during a bullet, then releases in the gap and wanders away —
   * never arriving. When the ring bricks finally fall, the next bullet
   * has a clear 71-tick lane to the base while the player is 6+ cells
   * away (bullet 4px/tick vs player 1px/tick) — base dies. The sticky
   * bridges the gaps so the player commits to the walk and holds at the
   * lane point through the whole drill.
   */
  midLaneStickyTicks: number
  /**
   * §163: distance (cells) from the lane defense point within which the
   * player HOLDS the lane (face up, fire to cancel bullets / kill the
   * carver) instead of moving. Outside this, navigate back to the point.
   */
  midLaneHoldRange: number
  /**
   * §163: max distance (cells) from the lane defense point at which the
   * candidate may engage at all. Beyond this the player is in the field
   * fighting — pulling them cross-map to the lane point would be a
   * tug-of-war with HUNT and starve the whole map of kills. The anchor
   * only matters near the base.
   */
  midLaneMaxDist: number
  /**
   * §163: max carve-dig length (cells) allowed when navigating to the lane
   * defense point. A long dig through a sealed pocket is self-defeating
   * (the player just escaped it via §162) — only accept a SHORT dig, or
   * a pure corridor route (user: 开路时尽量不要打破基地所在列的砖墙，如果
   * 必须要打，最多打掉一个 cell 能通过即可).
   */
  midLaneMaxDigCells: number
  /**
   * §164: proactive mid-lane flank hold mode. 0 = OFF (byte-identical).
   * Once the player is out of the spawn pocket and in the top half of the
   * map, prefer holding a standable parry cell beside/inside the base
   * column (Battlement's open plaza rows 4-14) instead of camping the side
   * spawns — the base column often has no steel guard, so the mid-lane is
   * where base-carving shells fly. See the MID_LANE_HOLD candidate.
   */
  midLaneHold: number
  /** §164: only engage while the player's row <= this (top/mid half — never drag across the bottom maze). */
  midLaneHoldMaxRow: number
  /** §164: enemy within this many cells of the lane keeps the hold active when no shell is in the column. */
  midLaneHoldEnemyDist: number
  /**
   * §146 B: defensePosStandable — 集合点可达性修复。默认防守位 (12,
   * 24-offset) 在全部 35 关都是环砖格，A* 到砖格目标返回空路径 → 回防路由失效
   * （S8 实测 corridor=0 breakBrick=0）。开启时：默认点不可站则在小盒内扫最近
   * 可站格。0 默认 OFF → byte-identical。
   */
  defensePosStandable: number
  /** 远位触发阈值：仅当玩家距基地超过该格数时启用集合点回退（近基 byte-identical）。 */
  defensePosStandableMinDist: number
  /**
   * §145 iceGlideControl: 冰上滑行控制 — 转弯/反向前先松键（null）让滑行
   * 自然衰减。冰面物理：反向输入 = 以 ICE_ACCEL_TRACTION 向反方向加速（真
   * 倒车）→ 倒过头 → Math.round 玩家格边界抖动 → A* 路径缓存翻转 → 方向振荡
   * （S24 seed 23 t4506-4511 实测 md 上下翻转 6 tick）。正确冰上操控 = 反向
   * 前先松键，滑行以 ICE_DECEL_TRACTION 衰减，不倒退不过冲。0 默认 OFF →
   * byte-identical（HUNT 不调用 iceGlideAdjust）。
   * 注意：仅在 HUNT navigate 段生效（其余移动型候选 pickup/firingLane/aggro/
   * defenseIntercept 不受影响）；且无法区分"路径转弯制动"与"冰上战术后退"——
   * 后者同样被压制（A/B 净负的部分原因，§145）。
   */
  iceGlideControl: number
  /** 冰上视为"正在滑行"的最小速度（px/tick）。低于该值不干预（刚起步/将停）。 */
  iceGlideMinSpeed: number

  // ---- M0.5 退役（2026-08-03, DECISIONS §96）----
  // guardBandMode/Row/HalfWidth + damagedArmorBonus（D1/D2 否决）与
  // smartThreatModel 族（Phase A 否决）已退役归档。
  /**
   * Close-combat T2a range: max distance (in cells) at which the player
   * stops to aim-and-fire at an enemy in the same row/col. Default 15
   * (AIM_RANGE_CELLS — unchanged behavior). When set lower (e.g. 5), the
   * player only stops at POINT-BLANK range, maximizing fire rate against
   * high-HP armor tanks (bullet travel time ≈ 0 → kills 4-HP armor in
   * ~0.5s instead of ~4s at long range). Beyond this range, the player
   * keeps moving to close the distance.
   *
   * This applies to 1-HP enemies (basic/fast/power). For multi-HP enemies
   * (armor, maxHp > 1), see `t2aHighHpMaxRange`.
   */
  t2aMaxRange: number
  /**
   * §56: Close-combat T2a range for HIGH-HP enemies (maxHp > 1, i.e. armor).
   * Default 2 — the player only stops to aim at multi-HP enemies when at
   * point-blank range. At longer ranges, the player keeps NAVIGATING to
   * close the distance instead of stopping for a low-DPS long-range duel.
   *
   * Rationale: a 4-HP armor tank at 15 cells takes ~4s to kill (4 bullets
   * × ~1s travel time each). During that time the enemy advances ~12 cells
   * and fires back. At 2 cells, bullet travel time ≈ 0, so 4 shots land in
   * ~0.5s — the enemy barely moves or fires. This generalizes the S32
   * close-combat strategy into a universal mechanism triggered by enemy
   * HP, not by stage name.
   *
   * For 1-HP enemies, `t2aMaxRange` (default 15) still applies — one shot
   * kills regardless of distance, so there's no DPS penalty for long range.
   */
  t2aHighHpMaxRange: number

  // ---- §58: Stage-level adaptive params (Strategy G, data-driven adaptation) ----
  /**
   * §58 / Strategy G: armor ratio in the stage's enemy queue above which the
   * AI switches to close-combat camp/nav timing. 0 = never adapt.
   *
   * Armor-heavy stages (S32 Diamond 40%, S18 Frozen Field 45%, S25 Ice Palace
   * 50%) suffer from T2a deadlocks and pursuit loops: armor is slow and creates
   * traffic jams, so the player gets stuck camping or chasing. Shorter camp/nav
   * timers (the old S32 override values) break these loops faster.
   *
   * Default 0.35 — catches S18/S25/S32/S16/S24/S27/S30/S34 (≥35% armor) without
   * affecting low-armor stages like S6 (0%), S10 (25%), S15 (10%). The threshold
   * was chosen so the adaptation fires on stages where the failure mode is
   * lives_exhausted (stuck chasing armor) rather than base_destroyed.
   */
  armorAdaptRatio: number
  /** §58: camp timeout for armor-heavy stages (default 50 = S32 override value). */
  armorCampTimeoutTicks: number
  /** §58: anti-camp suppress for armor-heavy stages (default 50). */
  armorAntiCampSuppressTicks: number
  /** §58: nav stuck for armor-heavy stages (default 90). */
  armorNavStuckTicks: number
  /**
   * §58: brick density (fraction of solid cells that are brick) above which
   * the AI uses faster replanning + path noise. 0 = never adapt.
   *
   * Brick-dense stages (S26 Brick Maze, S4 Maze) cause deadlock patrol loops
   * where the player and enemies circle each other through narrow corridors.
   * Faster replan (30 vs 50) + small path noise (0.05) break the symmetry.
   * Default 0.45 — catches S26/S4 without affecting open stages.
   */
  brickDenseAdaptRatio: number
  /** §58: replan interval for brick-dense stages (default 30 = S26 override). */
  brickDenseReplanInterval: number
  /** §58: path noise for brick-dense stages (default 0.05). */
  brickDenseSuboptimalPathProb: number

  /**
   * §59 / Strategy C: score bonus in defense-mode target selection for
   * enemies that have a CLEAR shot at the base (aligned + no brick/steel
   * in between). Such enemies can destroy the base with their next bullet,
   * so they must be the highest-priority target. 0 = OFF (byte-identical
   * to pre-§59). Default 500 — large enough to dominate the scoring
   * (urgencyBonus maxes at ~1000 for row 25, proximityBonus is 50, kind
   * weight is 30-120). Gated independently for A/B testing.
   */
  defenseClearShotBonus: number

  /**
   * D2 / 拆环威胁 (ring-breach threat score, plan/Battlement-Hard-Exploration
   * §D2): score bonus in defense-mode target selection for enemies that have
   * a clear shot at an INTACT base-ring brick (canBreachRingFrom — aligned
   * with a ring cell, no brick/steel between, ring still brick). The static
   * §59 predicate (defenseClearShotBonus) stays false until the ring falls,
   * so the breacher is invisible to the scorer until the fatal bullet is in
   * flight; this term fires EARLY while the ring stands, and grows as the
   * ring weakens (×1 at full ring → ×1.875 at 1 brick left) — the breach is
   * more urgent the closer it is to becoming a direct base shot. 0 = OFF
   * (byte-identical to pre-D2). Scoring-only: navigation goes to the
   * breacher's static cell (a breacher does not move while firing); no
   * shooting logic changes (§135/§136 already covered blocked-lane fire).
   */
  defenseBreachBonus: number

  // ---- Dual central breach strategy (plan/dual-central-breach-strategy.md) ----
  /**
   * When `world.spectateDual === true && centralBreachRisk === true`,
   * computeStageAdaptedParams overrides `defenseBreachBonus` with this value.
   * Default 400 (A/B candidate). The override is ONLY applied in dual mode on
   * central-breach stages — single-player keeps defenseBreachBonus=0 (byte-
   * identical). 0 = dual central breach D2 disabled.
   */
  dualCentralBreachDefenseBreachBonus: number
  /**
   * Same gating — overrides `baseGuardAnchorMode` in dual central breach.
   * Default 1 (enable §137 guard anchor at the antechamber mouth, e.g. (12,22)).
   */
  dualCentralBreachAnchorMode: number
  /**
   * Same gating — overrides `threatStickyTicks` in dual central breach.
   * Default 30 (0.5s sticky hold to bridge threat-signal flicker gaps).
   */
  dualCentralBreachStickyTicks: number
  /**
   * Same gating — overrides `baseDamageRecall` in dual central breach.
   * Default 1 (arm 1: unconditional recall once base is damaged — the dual
   * central breach stages need both players to react instantly to ring
   * damage, and the player-distance gate that made arm 1 net-negative in
   * single-player doesn't apply when P1 is already anchored at center).
   */
  dualCentralBreachDamageRecall: number
  /**
   * Same gating — overrides `maxPlayerDistFromBase` in dual central breach.
   * Default 8 (P1 returns to the guard anchor as soon as the base is
   * threatened and P1 is > 8 cells away — the default 26 is far too loose
   * for a center-guard role, P1 would be half the map away when the breach
   * starts). 8 = just outside the anchor hold range (6) so P1 returns,
   * arrives, and then holds the anchor.
   */
  dualCentralBreachMaxPlayerDistFromBase: number

  /**
   * Dual central breach (plan/dual-central-breach-strategy.md §6.3-A):
   * When > 0, P2 in dual central breach mode bypasses the nearby-enemy
   * gate for fence/shovel pickup. Fence = steel walls for the base,
   * structurally solving the breach. P1 handles center defense, so P2
   * is free to grab fence even with enemies nearby. 0 = OFF (byte-
   * identical). Only activates when spectateDual && centralBreachRisk.
   */
  dualCentralBreachP2FencePickup: number

  /**
   * Dual central breach (plan/dual-central-breach-strategy.md §6.3-C):
   * When > 0, P1 in dual central breach mode fires at brick walls while
   * navigating toward the guard anchor (dig-while-moving). Without this,
   * P1 waits for the navStuck/canMoveOrBreak detector to fire, losing
   * ~1s at start and oscillating in brick corridors. 0 = OFF (byte-
   * identical). Only activates when spectateDual && centralBreachRisk.
   */
  dualCentralBreachP1DigFire: number

  /**
   * §177 (plan/dual-central-breach-strategy.md §6.5 follow-up):
   * When > 0, P2 in dual central breach mode navigates with `directMove`
   * (chase straight at the target, breaking thin brick on the way) instead
   * of the A* `followPath` used by the HUNT candidate's long-range branch.
   * A* routes AROUND walls through corridors, so P2 never ends up sharing a
   * row/column with an enemy on open ground and `shouldFireInDir` never gets
   * a clear shot — measured P2 fire rate was 0% for a whole run. directMove
   * closes the row gap first (Navigator's vertical-first preference), which
   * is exactly the alignment the fire logic needs. followPath stays as the
   * fallback when directMove finds nothing. 0 = OFF (byte-identical).
   * Only activates when spectateDual && centralBreachRisk && isPlayer2.
   */
  dualCentralBreachP2DirectMove: number

  /**
   * §177: When > 0, P2 in dual central breach mode targets the NEAREST enemy
   * spawn point whenever no enemy is close enough to engage, instead of
   * holding a static flank cell. Enemies descend from the spawn columns, so
   * sweeping toward a spawn point keeps P2 on the lanes they travel — the
   * cheapest way to manufacture the row/column alignment the fire logic
   * needs. Once an enemy comes within `dualCentralBreachP2PatrolEnemyDist`
   * the patrol yields to the normal nearest-enemy hunt. 0 = OFF
   * (byte-identical). Only activates when spectateDual && centralBreachRisk
   * && isPlayer2 && the base is not under threat.
   */
  dualCentralBreachP2Patrol: number

  /**
   * §177: Manhattan cell distance at which a live enemy cancels the P2 spawn
   * patrol and hands the target back to the normal nearest-enemy hunt.
   * Only read when `dualCentralBreachP2Patrol > 0`.
   */
  dualCentralBreachP2PatrolEnemyDist: number

  /**
   * §177: the ROW P2 patrols on the chosen enemy-spawn column. 0 = the
   * literal spawn point (top of the board). Higher rows keep P2 on the same
   * descent lane while staying near the base — every measured Battlement
   * dual loss is `base_destroyed`, so a patrol that walks P2 to row 0 trades
   * lane coverage for an undefended base. Only read when
   * `dualCentralBreachP2Patrol > 0`.
   */
  dualCentralBreachP2PatrolRow: number

  /**
   * §177: When > 0, the gated P2 targets the RUNNER-UP threat in the
   * base-under-threat defense branch instead of the top-scoring one.
   * The defense score is a pure function of (enemy, base) — the player's own
   * position is not an input — so P1 and P2 otherwise rank threats
   * identically and both drive at the same tank while the rest of the wave
   * keeps digging the ring. Splitting by player index covers two lanes
   * instead of one and is deterministic (no "who is closer" oscillation).
   * 0 = OFF (byte-identical). Only activates when spectateDual &&
   * centralBreachRisk && isPlayer2 && a living partner exists.
   */
  dualCentralBreachP2DefenseSecond: number

  /**
   * §177: how the gated P2 treats the §137 guard-anchor hold inside the
   * base-under-threat branch. The anchor is base-relative, so both tanks
   * hold the same cell and §159 yield leaves one shuffling instead of
   * covering a lane.
   *   0 = OFF (shared anchor, byte-identical)
   *   1 = P2 holds its own shifted defense post instead
   *   2 = P2 skips the hold and drives at its (runner-up) threat
   * Default 2: with defenseSecond (runner-up threat) it gives the best
   * measured Battlement dual win-rate (see §177). Only activates when
   * spectateDual && centralBreachRisk && isPlayer2 && a living partner exists.
   */
  dualCentralBreachP2AnchorSplit: number
  /**
   * §178 (autopsy hard-s34-base-l3-t25-seed2): dual central breach — let the
   * carve-dig nav-stuck escape punch THROUGH the central wall (base-column
   * bricks cols BASE_POS/col..+1 above the ring). Without this the two tanks
   * are pinned at the top perimeter, oscillating, and never reach their guard
   * anchors while the base is breached from the bottom. In a central-breach
   * stage there is NO steel in the central band (detectCentralBreachRisk),
   * so punching the brick wall cannot open a lane to the eagle (the ring is
   * still intact below). Gated by spectateDual && centralBreachRisk — SP never
   * enters the override, so it stays byte-identical.
   *   0 = OFF (carve still caps base-column breaks at carveMaxBaseColumn).
   */
  dualCentralBreachCarveMaxBaseColumn: number
  /** §178: carve cost assigned to base-column bricks in the central-breach
   * override (default 1e9 keeps them effectively unbreakable; lowering to a
   * normal brick-break cost lets the carve A* route through the central wall
   * instead of around the top perimeter). */
  dualCentralBreachCarveBaseColumnCost: number
  /**
   * §178 (autopsy seed2): dual central breach — P1 holds a CENTRAL position
   * (dig up and intercept the col-12 spawn lane) instead of the right-wing
   * guard anchor. Matches the user expectation "开墙抵达中路驻守点". P2 keeps
   * the flank/hunting split (§177). Gated by spectateDual && centralBreachRisk
   * && !isPlayer2 — single-player and P2 unchanged (byte-identical).
   *   0 = OFF (P1 uses the normal flank anchor)
   */
  dualCentralBreachP1Anchor: number
  /** §178: the central hold cell (col,row) for P1 when dualCentralBreachP1Anchor>0.
   * Computed via findDualCentralHoldImpl when either is <0; the knobs are a
   * per-stage override. Defaults target the open top-center of a central-breach
   * stage (intercept the col-12 spawn lane). */
  dualCentralBreachP1AnchorCol: number
  dualCentralBreachP1AnchorRow: number
  /**
   * §181 (autopsy seed115): when > 0, P1 in dual central breach mode navigates
   * with `directMove` (chase straight at the anchor, breaking thin brick on
   * the way) instead of the A* `followPath`. A* routes around base-protection
   * bricks, but the route changes as the player moves, causing left↔right
   * oscillation at spawn (P1 ping-pongs 128↔136px for the entire game while
   * enemies destroy the base). directMove closes the row gap first (up),
   * which is exactly the alignment the fire logic needs. Gated by
   * spectateDual && centralBreachRisk && !isPlayer2 — single-player and P2
   * keep the A* long-range branch (byte-identical).
   */
  dualCentralBreachP1DirectMove: number
  /**
   * §178: when > 0, the dual-central-breach P1 is a PURE defender — its
   * power-up candidates (high/mid/low/close + aggressive freeze-pickup) are
   * suppressed so it never abandons the central hold to chase items (P2 is
   * the free tank that handles pickups). This is the "sticky hold" that keeps
   * P1 sniping the col-12 spawn lane. 0 = OFF (byte-identical — P1 may divert
   * to power-ups like normal). Gated by spectateDual && centralBreachRisk &&
   * !isPlayer2.
   */
  dualCentralBreachP1HoldSticky: number

  // ---- §161 / 开路策略 (carve path, user request 2026-08-06, Stage 33 Battlement) ----
  /**
   * §161 / 开路策略: when the spawn point is trapped in a brick maze and
   * cannot smoothly reach the standable defense post near the base, the
   * player shoots through LOWER-HALF brick walls to carve a through-route
   * to the post (R1/R2), generalizing to every map (no stage names). Two
   * phases:
   *   Mode A (dig to post): player row >= carveLowerRow, base not under
   *     threat, NO corridor path from the player to the post (R4: a smooth
   *     route ⇒ no carving) but a carve-safe dig path exists → follow it,
   *     breaking plain brick.
   *   Mode B (dig to threat): player already AT the post (dist <=
   *     carveAtPostCells) with no enemy within carveChaseCells and no
   *     fightable enemy (ENGAGE declined) → dig toward the enemy most
   *     likely to threaten the base (breacher / direct-shooter first, else
   *     nearest-to-base within carveThreatDistCells).
   * Hard constraints on every carve path: never route through steel (R5 —
   * even when the player could pierce it) and never break base-ring bricks;
   * break AT MOST carveMaxBaseColumn bricks in the base's own columns
   * (cols BASE_POS..+1 above the ring) when no alternative route exists
   * (R6 — prefer a 0-break route; allow 1 only if forced; reject 2+).
   * 0 = OFF (byte-identical to pre-§161).
   */
  carvePathMode: number
  /** §161: player-row gate — the carve is the lower-half spawn→post journey. */
  carveLowerRow: number
  /** §161 Mode B: player considered "at the post" within this many cells. */
  carveAtPostCells: number
  /** §161 Mode B: no enemy within this many cells of the player (don't steal close chases). */
  carveChaseCells: number
  /** §161 Mode B: the threat enemy must be within this many cells of the base. */
  carveThreatDistCells: number
  /** §161 R6: max base-column bricks a carve path may break (prefer 0). */
  carveMaxBaseColumn: number
  /** §161: cost assigned to base-column bricks in the carve A* (ring bricks stay
   * 1e9 and are never carveable). Default 1e9 ⇒ base-column walls are effectively
   * unbreakable by carve. §178 overrides this to a normal break cost in dual
   * central-breach so the nav-stuck carve-dig escape punches through the central
   * wall. SP keeps 1e9 (byte-identical). */
  carveBaseColumnCost: number
  /** §161: carve path safety replan timer (ticks). */
  carveReplanTicks: number

  // ---- §189 / 开局联通清墙 (base connectivity clear, user request 2026-08-11) ----
  /**
   * §189 / 开局联通清墙: at game start, the player observes the lower-half
   * layout and checks whether the three key strategic points — base-left,
   * base-right, and the central defense post — are connected by smooth
   * corridor paths. If brick walls partition these points, the player
   * proactively fires to clear a through-route BEFORE enemies arrive,
   * so that when a threat appears on either side the player can quickly
   * reposition without pathfinding failure.
   *
   * The candidate runs in the lower half (row >= baseConnectClearLowerRow),
   * only when the base is NOT under threat (calm opening phase), and only
   * when a wing anchor is NOT corridor-connected to the defense post. The
   * player follows the carve dig path toward the disconnected wing, firing
   * at brick walls to open a corridor.
   *
   * Same hard constraints as §161 carve (R5/R6): never break steel, never
   * break base-ring bricks, break at most carveMaxBaseColumn base-column
   * bricks. Reuses carvePathInfoCached for path-finding.
   * 0 = OFF (byte-identical to pre-§189).
   */
  baseConnectClearMode: number
  /** §189: player-row gate — only clear walls in the lower half. */
  baseConnectClearLowerRow: number
  /**
   * §189: only fire while the player has killed fewer than this many enemies
   * (opening phase). Once the player starts fighting, the candidate yields to
   * combat candidates. Prevents wall-clearing from interfering with mid/late
   * game behavior on stages where the lower half is walled but the player
   * needs to fight, not carve.
   */
  baseConnectClearMaxKills: number
  /** §189: max ticks the travel mode can stay active (bounds opening phase). */
  baseConnectClearMaxTicks: number

  /**
   * §132 / 方向 B (fast × base-proximity threat weight): score bonus in
   * defense-mode target selection for FAST enemies approaching the base.
   * A fast tank 6 cells from the base is a bigger threat than a basic tank
   * 3 cells out — the fast reaches the base in half the time, and the static
   * defenseKindWeight (fast=2) + linear -distToBase*10 cannot express that
   * (both score identically). When > 0, the base-threat score adds
   *   weight × speedRatio(kind) × clamp01((range − distToBase) / range)
   * where speedRatio = kind's BASE_SPEED_CPS / BALANCED_ENEMY_CPS
   * (fast 1.2, basic 1.0, power 0.95, armor 0.85) and the approach factor
   * ramps 1 at the base ring → 0 at fastBaseApproachRangeCells. Battlement
   * forensics (§132): 59% of base deaths are fast-tank kills, with the
   * player 4+ cells away 74% of the time. 0 = OFF (byte-identical).
   */
  fastBaseApproachWeight: number
  /** §132: distance (cells) at which the speed×proximity term fully fades. */
  fastBaseApproachRangeCells: number

  // ---- §60: Open-defense adaptation (baseRaceRangeCells by terrain) ----
  /**
   * §60: brick/(brick+steel) ratio at or above which the stage is eligible
   * for "open defense" (wider baseRaceRangeCells). Stages below this ratio
   * are "steel mazes" — enemies approach through indestructible corridors
   * that bypass the defense position, so early retreat HURTS (the player
   * can't intercept from the defense cell). 0 = never adapt.
   *
   * Default 0.10 — protects S6 Iron Curtain (3.7%) and S32 Diamond (5.3%)
   * while letting all other stages (≥12.5%) use the wider range. Verified
   * via 30-seed probes: baseRaceRangeCells=14 gives +13pp on S12/S26/S33
   * but -16pp on S6 and -33pp on S32.
   */
  openDefenseBrickWallRatio: number
  /** §60: baseRaceRangeCells for open-defense stages (default 14). */
  openDefenseBaseRaceRangeCells: number

  // ---- §133 / 方向 C: brick-heavy defense tightening (brickW ≥ threshold) ----
  /**
   * §133 / 方向 C (DECISIONS §133): brick/(brick+steel) ratio AT OR ABOVE
   * which the stage gets TIGHTER defense distances — the player returns to
   * the base EARLIER instead of deep-hunting while fast tanks rush the
   * base through pure-brick lanes. 0 = never adapt (byte-identical).
   *
   * Why (hard 35×120 forensics, §131/§132): brick-heavy stages have NO
   * indestructible steel — enemies (59% fast on Battlement) reach the base
   * ring through breakable brick, and the base dies early (Battlement
   * median tick ≈ 3244) with the player 9+ cells away 42% of the time. The
   * §115 M4 search widened all three defense distances GLOBALLY
   * (maxPlayerDistFromBase 26 / outnumberedFieldDistCells 26 / race 18),
   * and §60's open-defense even TIGHTENS race to 14 on these very stages —
   * the combination leaves the player deep-hunting when the base is
   * undefendable. §133 re-tightens ONLY the brick-heavy set: earlier race
   * trigger (bigger range), earlier forced return under threat (smaller
   * maxPlayerDistFromBase), earlier M13 field-pressure retreat (smaller
   * outnumberedFieldDistCells).
   *
   * Default 0.9 — matches the pure-brick set S0/S3/S14/S30/S33/S34 while
   * leaving S2 (0.833) and S16 (0.892) on the global values.
   */
  brickHeavyDefenseWallRatio: number
  /** §133: baseRaceRangeCells for brick-heavy stages (bigger = earlier race trigger). */
  brickHeavyBaseRaceRangeCells: number
  /** §133: maxPlayerDistFromBase for brick-heavy stages (smaller = earlier return). */
  brickHeavyMaxPlayerDistFromBase: number
  /** §133: outnumberedFieldDistCells for brick-heavy stages (smaller = earlier M13 retreat). */
  brickHeavyFieldDistCells: number

  // ---- §61: Terrain-adaptive T2a range for high-HP enemies ----
  /**
   * §61: forest density below which the stage is "open sightline" for armor
   * engagement. On open stages, the player can see and hit armor from range 4
   * (bullets travel through empty space fast). On forest-dense stages, enemies
   * are hidden in trees — engaging from range 4 wastes bullets on invisible
   * targets and the player takes ambush damage. 0 = never adapt.
   *
   * Default 0.15 — catches S26 (7%), S30 (12%), S16/S19/S24/S31 (0-9%) while
   * protecting S14 (31%), S18 (18%), S32 (16%), S34 (17%). Verified via
   * 40-seed probes: t2aHighHpMaxRange=4 gives +12pp on S26, +17pp on S30,
   * but -25pp on S18, -8pp on S34.
   */
  openT2aForestRatio: number
  /**
   * §61: t2aHighHpMaxRange for open-sightline stages (default 4). At range 4,
   * the player engages armor from 4 cells away instead of 2 — bullet travel
   * time is still short (~0.3s for 4 cells) but the player has room to dodge
   * return fire. On forest-dense stages, keep the default 2 (point-blank).
   */
  openT2aHighHpMaxRange: number
  /**
   * §61: water density at or above which the stage is "open sightline"
   * (water creates unobstructed lanes). Overrides the forest check — even
   * with forest, high water means open firing lanes. Default 0.25 — catches
   * S30 Eagle Nest (36% water) which has 12% forest (below threshold anyway)
   * but this also future-proofs for high-water stages.
   */
  openT2aWaterRatio: number
  /**
   * §62: armor ratio at or above which open-T2a is SUPPRESSED (on forest-triggered,
   * non-steel-heavy stages). Stages with significant armor need point-blank
   * engagement (range 2) in open terrain — armor tanks take 4 hits, and at
   * range 4 the player trades kills inefficiently. 1 = never suppress.
   *
   * SUPPRESSION IS BYPASSED (open-T2a fires) when:
   *   - The open-sightline trigger was WATER (waterRatio ≥ openT2aWaterRatio) —
   *     water lanes allow safe long-range engagement (S30: 36% water, 30% armor).
   *   - OR the stage is steel-heavy (steelRatio ≥ openT2aSteelRatio) — steel
   *     corridors force head-on encounters where range 4 gives reaction time
   *     (S26: 26% steel, 40% armor).
   *
   * Default 0.25 — suppresses on S11 (30% armor, 4% steel), S16 (50%, 3%),
   * S19 (40%, 4%) while keeping open-T2a on S26 (40% armor, 26% steel) and
   * S30 (30% armor, 36% water).
   */
  openT2aMaxArmorRatio: number
  /**
   * §62: steel density at or above which armor suppression is BYPASSED
   * (open-T2a fires even with heavy armor). Steel corridors force head-on
   * encounters where the player needs range 4 to react. Default 0.15.
   */
  openT2aSteelRatio: number
  /**
   * §62: forest density at or above which armor-heavy stages use range 3
   * (instead of the default 2) for high-HP T2a. On forest-dense armor stages,
   * the forest absorbs enemy bullets, giving the player room to maneuver at
   * range 3 without taking damage. Range 2 is too close (point-blank hits),
   * range 4 is too far in forest (bullets hit trees). 0 = never adapt.
   * Default 0.25 — catches S14 (31%) while leaving S18 (18%) and S32 (16%)
   * at range 2 (range 3 hurt both in probes).
   */
  armorForestDenseRatio: number
  /** §62: t2aHighHpMaxRange for forest-dense armor-heavy stages (default 3). */
  armorForestDenseRange: number

  /**
   * §64: outnumberedRadiusCells for armor-heavy + high-steel + non-steel-maze
   * stages. On these stages (only S26 Brick Maze: 40% armor, 26% steel,
   * brickWallRatio 0.26), the player gets swarmed in steel corridors and
   * needs to retreat from a wider radius (12 vs default 9) to avoid being
   * pinned. 9 = no change (byte-identical to pre-§64).
   *
   * Gated by: armorHeavy AND steelRatio ≥ openT2aSteelRatio AND !isSteelMaze.
   * Only S26 matches this regime across all 35 stages. Probes: S26 +10pp
   * (75% → 85%, 30 seeds). S32 (steel-maze) is excluded — it needs the
   * opposite (range 12 hurts S32 by -7pp).
   */
  armorSteelOutnumberedRadiusCells: number

  /**
   * §66: campTimeoutTicks for steel-maze stages with low armor. On these
   * stages (only S6 Iron Curtain: 0% armor, brickWallRatio 0.04), the
   * player gets stuck camping at indestructible steel walls — the default
   * 60-tick camp timeout is far too long, wasting ~1s per deadlock. A
   * shorter timeout (20) breaks the camping deadlock fast, letting the
   * player reposition through alternative corridors.
   *
   * Gated by: isSteelMaze AND !armorHeavy. Only S6 matches this regime
   * across all 35 stages. Probes: S6 +16pp (72% → 88%, 60 seeds).
   */
  steelMazeCampTimeoutTicks: number

  // M0.5 退役（2026-08-03）: §68-v2 crossfireAwareness / §69-B crossfirePathCost
  // 双否决退役。路径威胁基础设施现状：findPathThreat 存活于 ThreatAssessor；
  // findSafeMoveDir / computeThreatCosts 已随归档文件删除（git 史可考），供
  // v2 survive 候选复活时取回（设计 §3.2 / §4.4 整合条款）。

  /**
   * §48-revisit: Steel-only evasion occlusion. When > 0, the bullet-threat
   * scanner (`findMostDangerousBulletImpl`) skips enemy bullets whose path to
   * the player is blocked by STEEL. Steel is a permanent barrier for enemy
   * bullets — `STEEL_PIERCE_PLAYER_LEVEL = 3` is a player-only privilege, so
   * every enemy bullet (basic/fast/power/armor) dies on steel and can never
   * pass through, nor can any future bullet from the same direction. Dodging
   * a steel-blocked bullet is therefore purely wasteful: it moves the player
   * off-position for a threat that can never arrive.
   *
   * BRICK is deliberately NOT treated as occlusion (the original §48 fix's
   * mistake). Brick is temporary — the bullet destroys it and dies, but the
   * enemy fires again and the next bullet comes through. Dodging a
   * brick-blocked bullet is load-bearing "anticipatory dodging" (DECISIONS
   * §48 negative result: blocking brick cost S32 −10pp @120).
   *
   * The scan walks the full bullet→player path checking every cell for steel.
   * It does NOT stop at brick (there may be steel behind a brick that the
   * bullet will eventually clear through to). OOB cells cause a break (not a
   * false "steel" — avoiding the TileMap.get OOB→'steel' default that caused
   * §70's baseSteel false positive).
   *
   * 0 = OFF (byte-identical to pre-§48-revisit). 1 = ON.
   *
   * NOTE (per-seed tick-diff finding, S32 seed 11): a blanket steel occlusion
   * is NET NEUTRAL — suppressing a dodge removes load-bearing repositioning,
   * so the player can get pinned in a corner (mechanism confirmed at tick 738:
   * near steel-blocked bullet, player stayed at (0,1) and died, while the
   * baseline dodged down and won). The `evasionSteelOcclusionRange` gate
   * addresses this by only suppressing FAR blocked bullets (dist >= range
   * cells), whose dodge is genuinely wasteful, while keeping NEAR dodges.
   */
  evasionSteelOcclusion: number

  /**
   * §48-revisit: distance gate (cells) for `evasionSteelOcclusion`. Only
   * suppress the dodge for a steel-blocked bullet when it is at least this
   * many cells from the player. A far blocked bullet's dodge is genuinely
   * wasteful (the player is not in imminent danger); a near blocked bullet's
   * dodge is load-bearing repositioning (the player is being pressured and
   * needs to move) — keeping it avoids the S32 pinning regression.
   *
   * 0 = suppress ALL steel-blocked bullets (no distance gate). N = suppress
   * only blocked bullets at dist >= N cells.
   */
  evasionSteelOcclusionRange: number

  /**
   * §48-revisit (terrain gate): brick/(brick+steel) ratio BELOW which
   * `evasionSteelOcclusion` is auto-enabled in computeStageAdaptedParams.
   * 0 = never auto-enable (byte-identical to pre-§48-revisit).
   *
   * Discriminator discovered via per-seed tick-diff + stage probes: steel
   * ratio is NOT the predictor (S26 Brick Maze has MORE steel than S32
   * Diamond — 26% vs 18% — yet regresses while S32 gains). The predictor
   * is brickWallRatio:
   *   - Steel mazes (S32 0.063, S6 0.04): occlusion HELPS (+2.5~3.3pp S32
   *     @120). The player fights in open guard bands / steel corridors;
   *     a steel-blocked bullet's dodge is genuinely wasteful.
   *   - Brick-heavy (S14 0.915, S26 0.254): occlusion HURTS (-5pp S14,
   *     -6.7pp S26 @120). Dodging a blocked bullet is load-bearing
   *     repositioning through breakable cover; skipping it re-ranks the
   *     scan to a farther bullet (S26 seed-7: player dodged down one tick
   *     early and lost).
   * Default 0.10 = enable on steel mazes only (matches isSteelMaze / §60).
   */
  evasionSteelOcclusionBrickRatio: number

  // M0.5 退役（2026-08-03）: trapAvoidance 族（3 项）已退役归档
  // 归档（默认 0 未发布）。"包围风险"输入并入 v2 survive 候选设计（§3.2）。

  /**
   * §49-revisit: 炮口相向对枪抵消（§52 v2，T2a 内联，当前保留形态）。
   *
   * When > 0, the T2a branch detects an enemy facing the player within
   * `counterFireMaxRange` cells (and not on ice) and: (a) if an enemy bullet
   * is already in the line of fire, fires to cancel it (对枪抵消 — bullet
   * elimination is safer than trading hits); (b) otherwise keeps alignment
   * toward the enemy without strafing (保持对齐以备对枪).
   *
   * When 0, T2a uses the plain stop-and-aim behavior (pre-§52 form — turn
   * to face and fire, no facing-enemy special-casing).
   *
   * Default 1 = current shipped behavior (byte-identical to pre-parameter-
   * ization). OFF (0) is the A/B baseline (pre-§52 v2).
   */
  counterFire: number
  /**
   * §49-revisit: max distance (cells) for the facing-enemy counter-fire /
   * keep-alignment block in T2a. The original hardcoded value was 5.
   */
  counterFireMaxRange: number

  /**
   * §74: Steel-fire gate — don't fire at steel walls the player cannot
   * pierce while trying to BREAK THROUGH a wall. When > 0, the two
   * break-through fire sites in think() that bypass shouldFireInDirImpl
   * (aggressive navigate break-through + T2b navigate break-through) apply
   * the same steel gate that shouldFireInDirImpl's T11 already enforces:
   * steel blocks fire while `p.level < STEEL_PIERCE_PLAYER_LEVEL` (3).
   *
   * Without this gate, the AI fires at indestructible steel to open a path
   * (wasting the bullet cap) and then camps at the wall for the full
   * campTimeoutTicks (90 default) before the anti-camp escape — the
   * reported "shoot steel, can't break through, stuck in place" behavior.
   * With the gate ON, the AI falls through to navigation instead, which
   * routes AROUND the steel via corridors (steel is impassable to A*),
   * restoring mobility.
   *
   * Scope note (per-seed A/B, 2026-08-01): deliberately NOT applied to the
   * T2a/aggressive stop-and-aim sites (which fire when scan.enemy is true)
   * — the dual-offset case (steel on one scan line, enemy on the other)
   * means the enemy is genuinely reachable by the center-line bullet, and
   * suppressing that fire costs kills (arena A/B: 20 kills → 7 kills).
   *
   * At level ≥ 3 the player CAN pierce steel, so break-through fire at
   * steel is correct and the gate is inert (byte-identical to pre-§74
   * behavior).
   *
   * 0 = OFF (byte-identical to pre-§74 behavior — A/B baseline).
   * 1 = ON (default — the fix).
   */
  steelFireGate: number

  /**
   * §80: Turn-snap aim guard — don't commit to a stop-and-aim TURN whose own
   * grid-snap would take the tank off the line it wanted to fire along.
   *
   * Root cause (replay classic-s11-…-seed1785622102123, 0:31–0:47):
   * turning is not free. `Simulation.updateMovement` axis-locks the tank and
   * snaps the PERPENDICULAR coordinate to the grid on every direction change
   * (`axis === 'x' ? tank.y = snap(tank.y, CELL) : tank.x = snap(...)`).
   * A tank sitting at a non-grid-aligned sub-cell offset therefore MOVES up
   * to CELL/2 px sideways just by turning — which can slide the enemy out of
   * its firing line.
   *
   * That creates a stable period-2 deadlock in the `aggressive` (freeze)
   * branch, which has no anti-stall guard of its own (T2a has the
   * `_campTicks` escape, navigate has `_navStuckTicks`; aggressive has
   * neither, and during a freeze window it is the ONLY branch that runs):
   *
   *   tick A: not aligned → `scanAhead(aimDir).enemy` false → fall through to
   *           navigate → move along path (perpendicular) → snap puts the tank
   *           BACK on the firing line
   *   tick B: `scanAhead(aimDir).enemy` true → turn to aim → snap puts the
   *           tank back OFF the firing line → back to tick A
   *
   * Net displacement per 2 ticks: zero. The AI burns the entire freeze
   * window — the single highest-value window in the game, when enemies are
   * helpless — jittering between two sub-cell positions while firing at
   * nothing. Measured pre-fix over 35 stages × 10 seeds: 4.3% of all freeze
   * ticks (coop) / 2.9% (single) were this oscillation, with worst-case runs
   * losing 1166 of 1200 freeze ticks (97% of the window).
   *
   * The guard re-runs the line-of-fire scan from the position the tank would
   * actually occupy AFTER the turn-snap. If the enemy is no longer on that
   * line, the aim is a lie — fall through to navigate and keep moving.
   *
   * Inert when the tank is already grid-aligned on the perpendicular axis
   * (`snap(v) === v`), which is the overwhelmingly common case — so this is
   * byte-identical to pre-§80 behavior except in the pathological geometry
   * that causes the deadlock.
   *
   * Known residual (measured, 2026-08-01): the guard does NOT eliminate ALL
   * freeze-window oscillation — the worst measured streak (Brick Maze
   * s27/seed1, 1166 of 1200 freeze ticks) is byte-identical with the guard
   * ON: the guard is inert on that run (its scans never change the outcome).
   * The mechanism was not per-tick traced; a grid-aligned early-return is one
   * candidate. The aggressive branch still lacks an anti-stall guard for this
   * residual — see DECISIONS §80.
   *
   * 0 = OFF (byte-identical to pre-§80 behavior — A/B baseline).
   * 1 = ON (default — the fix).
   */
  aimTurnSnapGuard: number

  /**
   * §84: Aggressive branch stall detection. When > 0, the aggressive
   * (freeze/shield) stop-and-aim code tracks how long the player has been
   * stopped at the same cell with no kills. If the player camps for more
   * than `aggCampTimeoutTicks` ticks without a kill, the AI falls through
   * to navigate (which repositions the player toward the enemy).
   *
   * Root cause (replay classic-s03-…-seed1785643123096, 0:20–0:36):
   * the aggressive branch has NO anti-stall guard (unlike T2a which has
   * `_campTicks` and navigate which has `_navStuckTicks`). When the player
   * stops to aim at an enemy but the bullet keeps missing (enemy slightly
   * offset in the perpendicular axis, so the 6px bullet passes above/below
   * the 32px tank), the player stays put firing at nothing for the ENTIRE
   * freeze window — measured at 1080+ ticks (18 seconds) in the replay.
   *
   * 0 = OFF (byte-identical to pre-§84 behavior). 120 = 2 seconds (default).
   */
  aggCampTimeoutTicks: number

  /**
   * §85: Close-range enemy exposure check in navigate. When > 0, the
   * navigate branch checks if the player's _moveDir would expose the
   * player to a close-range enemy's line of fire. If an enemy is within
   * `closeCombatDangerRange` cells, aligned with the player (same row/col),
   * has no wall between them, and the player's moveDir is NOT toward that
   * enemy, the move is cancelled — the player stops and fires at the
   * enemy instead (or turns to face it).
   *
   * Root cause (replay classic-s03-…-seed1785643123096, 1:03): the player
   * was in close combat with an enemy, turned away (moveDir = away from
   * enemy), and was killed by the enemy's bullet before it could dodge.
   * The navigate branch only checks for BULLET threats (findPathThreat),
   * not for enemy tanks that could fire. This check adds enemy-tank
   * threat assessment: "don't turn your back on a close enemy."
   *
   * 0 = OFF (byte-identical to pre-§85). 1 = ON (default).
   */
  closeCombatDangerCheck: number
  /** §85: max distance (cells) for the close-range enemy exposure check. */
  closeCombatDangerRange: number

  /**
   * §153-W2: fire-rate-aware close combat. When ON (with the §85
   * `closeCombatDangerCheck` also ON), the §85 stand-and-fire response becomes
   * fire-rate aware: an aligned close enemy that fires FASTER than the player
   * means a stand-and-duel is a losing trade → dodge perpendicular to a safe
   * position instead. When the player fires faster (or no enemy is aligned),
   * the §85 stand-and-fire (duel) is kept. Default 0 = OFF (byte-identical to
   * §85). Verify with a hard 35-stage sweep before promoting.
   */
  closeCombatDuel: number

  /**
   * M5 (plan/God-AI-Redesign-v2, DECISIONS §103): 站位提前规避 — path-threat
   * avoidance in the navigate/hunt branch.
   *
   * When > 0, after `_moveDir` is chosen in the HUNT candidate (long-range
   * followPath or close-range directMove), the path ahead is checked for
   * in-flight enemy bullets via `findPathThreat` (time-aware, ±10-tick window
   * over 3 lookahead cells). If a bullet would arrive while the player is
   * walking into that cell, `findSafeMoveDir` finds a safer alternative
   * (perpendicular first, backward as fallback, cell-1 only) and overrides
   * `_moveDir` — the player does NOT walk into crossfire, from the root
   * reducing the number of times it enters the reactive dodge branch
   * (83% of deaths on hard/chaos, DECISIONS §96).
   *
   * Distinction from the retired §68-v2 diversion (DECISIONS §73): §68
   * committed to a perpendicular A* path diversion at 12-23 ticks lead time
   * and died (premature commitment off the path); M5 only swaps the
   * IMMEDIATE next step (cell-1) when `findPathThreat` already flagged an
   * imminent collision, and re-evaluates every tick (no path commitment).
   * The A* path cache is untouched — the next tick re-plans from the new
   * cell. This is strictly less committed than §68: same detection, but the
   * response never leaves the current corridor plan for more than one step.
   *
   * 0 = OFF (byte-identical to M0). 1 = ON.
   */
  pathThreatAvoidance: number

  /**
   * §153-W1: "wait for the bullet to clear" — predictive next-move collision
   * guard (px expansion of the predicted body; 0 = exact prediction).
   *
   * > 0: in the HUNT/navigate branch, after `_moveDir` is chosen, compute the
   * player's body at its NEXT position (one step along `_moveDir`, off-axis
   * grid-snapped exactly like the axis-lock in SimulationCombat) and, if any
   * enemy bullet's box overlaps it, set `_moveDir` to null for the tick (the
   * player holds still) — re-evaluated next tick, so the bullet clears in 1-3
   * ticks and the lane is safe. Fixes "the player drives/snaps INTO a bullet
   * passing in an adjacent lane or near-miss" (hard S12 Lattice seed
   * 3214953618, tick 1599): the center-based threat detector
   * (`findMostDangerousBullet`) misses such a bullet (center already past /
   * adjacent column), so only the predictive hold is load-bearing. Distinct
   * from `pathThreatAvoidance` (future-cell, time-windowed, swaps direction):
   * this is a next-tick body-footprint hold.
   *
   * §154 round 2 (final, 2026-08-06): the original expanded-box version (any
   * bullet within `marginPx` of the body) was NET-NEGATIVE on hard — 18 losing
   * seeds per-seed diffed to first-divergence holds on bullets PERPENDICULAR
   * to the intended move (freezing in crossfire = §48 stationary death) or
   * bullets the move could never reach (S12-1: the turn cooldown would have
   * let it pass anyway). The final design: predictive next-body check (one
   * step along `moveDir`, off-axis grid-snapped like the SimulationCombat
   * axis-lock) + exclusive AABB (same semantics as the sim's own collision —
   * a 0px edge touch never holds, S1 s48 @4723 artifact) + `marginPx = 1`
   * (1px expansion catches bullets within a tick of grazing the body; margin
   * 0 loses the S12 s5/s44/s57 protections) + a turn-cooldown gate at the
   * call site (when `p.dir !== moveDir` and the sim's turn cooldown is
   * active, the player is involuntarily halted anyway — the hold is free, so
   * skip it; fixes the S9-5 family of wasteful freezes). Measured (hard,
   * 35×60 full sweep): net +15 (39W/24L); focus stages (S1/6/9/12/13/33)
   * net +7 (10W/3L, S12 34→38/60). Residual 3 to-lose are the documented
   * freeze-vs-hit context trade (S6 s21: same-axis genuine protection that
   * still loses; S9 s5: the player took the 100hp snap-hit and won anyway;
   * S1 s48: perpendicular 1px corner graze). An axis filter (perpendicular
   * bullets never hold) was measured and REJECTED: it removed protective
   * perpendicular 1px grazes in corridor stages (S12 s5/s57, S9 s3) — net
   * dropped to +3. findSafeMoveDir substitution (safe-alternative direction)
   * was also rejected (net +3, derailed navigation).
   *
   * **Ship (2026-08-06):** `bulletLaneWait` = 1 is the SHIPPED global default
   * (user decision — chaos is out of scope for this change; the hard/classic
   * full-sweep measurement is net +15 / +2, chaos −7 and not gate-touching at
   * 60-seed granularity).
   */
  bulletLaneWait: number

  // M0.5 退役（2026-08-03）: dodgeHysteresis 已退役归档
  // （A/B -1.1pp，未发布）。

  /**
   * §86: Oscillation detection + counter-fire. When the dodge direction
   * flips 3+ consecutive times for the same threat (oscillation caused by
   * snap() Math.round discontinuity), face the bullet and fire to cancel it
   * (对枪抵消). 0 = OFF (A/B baseline). 1 = ON and is the SHIPPED default —
   * the only §86 param enabled in production. The simulation-layer turn
   * cooldown (§86c) is the canonical fix, so this AI-layer counter-fire is a
   * rare-boundary fallback (C-B = +0.1pp per A/B/C).
   */
  dodgeOscillationCounterFire: number

  // ---- §M3: Dodge quality (plan/God-AI-Redesign-v2 M3) ----
  /**
   * §M3-revisit (round 3): PINNED-GATED counter-fire in the DODGE branch —
   * "counter-only-when-pinned". When the threat's perpendicular dodge is
   * mathematically infeasible (isDodgePinnedImpl: no perpendicular direction
   * can clear the bullet's hit band before arrival, or all are blocked /
   * covered by other bullets), the player faces the bullet and fires to
   * cancel it (对枪抵消 — bullet-bullet collision,
   * SimulationCombat.bulletHitsBullet). Only then is counter-fire the ONLY
   * reliable survival move.
   *
   * Why (DECISIONS §96 death attribution + per-tick traces): 83% of
   * hard/chaos player deaths occur in the dodge branch; the branch never
   * fires at the incoming bullet (fire is gated on the MOVE direction only);
   * a close bullet (~4px/tick) arrives in ~8 ticks while a perpendicular
   * dodge needs ~19 — the dodge is mathematically futile at close range
   * (maze seeds 2/16 failure mode). Round 1 gated on DISTANCE alone and was
   * reverted (DECISIONS §98, 2026-08-03): it counter-fired mid-maneuver
   * during a VIABLE dodge (S25 Ice Palace seed 10 → deterministic
   * 5/20→1/20 regression). The pinned gate is the refinement: a dodge that
   * can actually clear (offset-aware — the player already partially off the
   * line needs less movement) stays a dodge; only when dodging cannot
   * possibly save the player does the AI stand and cancel.
   *
   * 0 = OFF (shipped default, byte-identical to M0). 1 = ON (A/B knob).
   * Must be validated with the official 口径 (no stageIndex) + 60-seed truth.
   */
  dodgeCounterFire: number
  /**
   * §M3: max lateral offset (px) between the player center and the threat
   * bullet for the counter-fire to trigger. The player's 6px bullet must
   * actually collide with the enemy bullet (half-width sum ≈ 6px) — a
   * bullet more than ~6px off-center passes beside the player's shot.
   */
  dodgeCounterFireAlignPx: number
  /**
   * §M3: multi-bullet dodge direction scoring. When > 0,
   * `dodgeDirectionImpl` scores each passable perpendicular candidate by its
   * nearest-bullet CLEARANCE (minimum arrival tick of any other enemy bullet
   * at the cell the player would move into) and picks the candidate with the
   * most clearance, instead of the binary next-cell isSafeDir check.
   * Addresses crossfire deaths: dodging INTO a cell where another bullet
   * arrives in 2 ticks scores badly vs one with 15 ticks of clearance.
   * 0 = OFF (binary, byte-identical). 1 = ON.
   */
  dodgeClearanceScore: number
  /**
   * §223: multi-bullet centroid escape. When > 0, the default dodge path
   * (no horizon/clearance arm active) switches to centroid escape once ≥2
   * enemy bullets are within CENTROID_RADIUS_PX of the player: among the
   * four directions that are passable (canMoveDir) and safe (isSafeDir —
   * not entering another bullet's lane), pick the one whose new cell lies
   * FURTHEST from the bullet centroid. Base gate: on base stages the new
   * cell may not exceed the current base distance by more than 2 cells
   * (prevents S10s6-style runaway escapes). Single bullet / no bullets →
   * legacy binary path (byte-identical). 0 = OFF (byte-identical).
   * 1 = ON (A/B knob).
   */
  dodgeCentroidMode: number
  /**
   * M9 (plan/God-AI-Redesign-v2): survival-horizon dodge commitment scoring.
   * When > 0, `dodgeDirectionImpl` scores each passable perpendicular candidate
   * by its SURVIVAL HORIZON — the earliest tick any enemy bullet could hit the
   * player if it COMMITS to moving that way (per-tick hit-time estimation:
   * t_arrive vs perpendicular escape time to clear the bullet's hit band,
   * clamped by the terrain-limited free path) — and commits to the
   * longer-horizon direction.
   *
   * Addresses the measured dominant dodge-death failure mode (M9 probe,
   * probe-m9-commit.ts): hard 31.8% / chaos 35.0% of dodge-branch deaths are
   * "commitment failures" — the threat was escapable when the dodge started
   * (t_arrive >= escape time) but the player oscillated within the 32px hit
   * band (perpendicular displacement < 19px) instead of sustaining an escape
   * (crossfire direction-mischoice measured ~0%, so the binary next-cell
   * isSafeDir was NOT the bottleneck). The horizon model fixes both: it
   * commits to the escaping side and downgrades sides covered by other bullets.
   * 0 = OFF (byte-identical to M0). 1 = ON (A/B knob).
   */
  dodgeHorizonScore: number

  /**
   * §201: escape-depth-aware dodge — perpendicular dead-end escape. On
   * cooldown-model maps with water/wall bands (S14 Bastion r14-15 water
   * belt), the binary dodge picks a perpendicular candidate by ONE-step
   * passability+safety: the player steps into a 1-cell pocket, the next
   * tick that side is blocked (water/brick), so it flips back — an
   * up/down death oscillation inside the hit band (S14 hard s60:
   * (5,12)↔(5,13) for 93 ticks, hp 315→0). When BOTH perpendicular
   * escape depths are < this threshold (cells), probe the bullet-axis
   * directions (left/right for a vertical bullet) for a LONGER escape
   * (depth ≥ threshold + safe) and move there instead. §83 (never flee
   * in the bullet's own travel direction) is respected: the axis probe
   * only fires when the perpendicular pockets are dead ends, and it is
   * safe-gated by isSafeDir like every dodge. 0 = OFF (byte-identical).
   * >= 1 = trigger when both perpendicular depths are below this value.
   */
  dodgeEscapeDepth: number

  /**
   * M10: min escape-margin gate for the horizon commitment (tick). Only
   * commit to a dodge direction when its survival margin exceeds this
   * threshold — i.e. the escape is CLEARLY winnable, not a marginal knife-edge.
   * 0 = no margin gate: any escapable side commits; when BOTH perpendiculars
   * are doomed (both margins ≤ 0) the gate also fails and the legacy binary
   * path runs (differs from M9's later-hit pick — see ThreatAssessor).
   * Positive values filter out low-value commitments (DECISIONS §108: MARGIN6
   * was hard +1.6pp but chaos -2.4pp at 60-seed → not shipped).
   */
  dodgeHorizonMinMarginTicks: number

  /**
   * M10: max distance-to-base gate for the horizon commitment (cells). When
   * the player is farther than this from the base, horizon commitment is
   * skipped (legacy binary dodge) — the player must not chase survival
   * escapes far from the base while the base is undefended. Only applies
   * when the stage has a base (hasBase). 0 = unlimited (no distance gate).
   * NOTE: A/B-measured HARMFUL (chaos -4.0pp at maxDist=8, DECISIONS §108)
   * — kept only as an experimental knob, do not ship.
   */
  dodgeHorizonMaxDistCells: number

  // ---- M12: player HP buffer awareness (DECISIONS §112) ----
  /**
   * M12: master gate for HP-adaptive dodge commitment. 0 = OFF
   * (byte-identical — the margin stays dodgeHorizonMinMarginTicks).
   * 1 = ON. Only applies in the 'pool' combat model (instant/classic has no
   * HP buffer — 1 hit = death, there is no trade/commit gradient).
   * Mechanism: adjusts the horizon commit gate inside dodgeDirectionImpl
   * based on the player's hits-to-die vs the current threat bullet
   * (ceil(player.hp / bullet.damage)).
   */
  playerHpAwareness: number

  /**
   * M12 danger mode: when hits-to-die <= this value, the player is in the
   * danger zone (measured: hard 70% / chaos 67% of deaths absorb >= 3 hits,
   * and ~1/5 of survival time is spent at <= 2 hits — §111 probe). In danger
   * mode the horizon commit margin is RELAXED to hpDangerCommitMargin — the
   * escape is survival, not efficiency, so the player commits to the
   * longer-horizon perpendicular side instead of oscillating inside the hit
   * band (M9 measured commitment-failure as 32-35% of dodge deaths).
   * 0 = danger mode disabled.
   */
  hpDangerHits: number

  /**
   * M12 danger-mode commit margin (ticks). When hpDangerHits is satisfied,
   * the horizon commit gate uses this margin instead of
   * dodgeHorizonMinMarginTicks. 0 = keep dodgeHorizonMinMarginTicks (danger
   * mode only removes the distance gate, not tested — keep simple).
   */
  hpDangerCommitMargin: number

  /**
   * M12 trade mode: when hits-to-die >= this value, the player has a full HP
   * buffer (pool 1★ = 315 HP ≈ 4 hits) and can afford to TRADE a hit for
   * progress. In trade mode the commit margin is TIGHTENED by
   * hpTradeCommitPenalty — the player accepts the partial dodge (legacy
   * binary path, keeps moving/attacking) instead of over-committing to an
   * escape that costs base-defense and kill efficiency (M9/M10 measured the
   * ungated escape as an efficiency loss). 0 = trade mode disabled.
   */
  hpTradeHits: number

  /**
   * M12 trade-mode margin penalty (ticks), ADDED to
   * dodgeHorizonMinMarginTicks when hpTradeHits is satisfied. Larger = more
   * risk acceptance (fewer horizon commits at high HP).
   */
  hpTradeCommitPenalty: number

  // ---- §87: Urgent power-up pickup priority (user request 2026-08-02) ----
  /**
   * §87: 0 = OFF (byte-identical to pre-§87). 1 = ON: a CLOSE power-up with a
   * SAFE PATH outranks base defense (回防) and enemy-kill (杀敌) targets.
   *
   * When ON, the think() loop checks — before T2a stop-and-aim and before the
   * normal S5 power-up economy — whether a power-up within its category range
   * (see below) has a safe path (route danger <= pickupPriorityMaxDanger,
   * i.e. no enemy between the player and the item) and is actually reachable
   * (A*, same corridor/dig path the navigator drives). If so, the player
   * diverts to collect it instead of camping on an aligned enemy or returning
   * to the defense position.
   *
   * Deliberately placed AFTER dodge (survive) and T8 (intercept an in-flight
   * bullet aimed at the base — an immediate loss) and gated to NORMAL mode
   * (not freeze/aggressive: during a freeze window an aligned frozen enemy
   * is a free kill the aggressive branch already exploits, and it already
   * grabs power-ups when no enemy is aligned).
   *
   * Why: in classic the AI ignored nearby power-ups whenever it was fighting
   * (aimDir set, not on cooldown) or defending (base under threat) — the S5
   * gate `(!aimDir || onCooldown) && !baseUnderThreat && no enemies within 5
   * cells` never fired. A bomb/freeze/fence at 5 cells that would clear the
   * screen or buy the base a steel ring was walked past. Ranges are the
   * tuning knobs for the A/B sweep (default target: 8/4/2).
   */
  pickupPriorityMode: number
  /** §87: max distance (cells) for HIGH-value power-ups: bomb/freeze/fence. */
  pickupPriorityHighRange: number
  /** §87: max distance (cells) for MID-value power-ups: star/tank/shield. */
  pickupPriorityMidRange: number
  /** §87: max distance (cells) for LOW-value power-ups: boat. */
  pickupPriorityLowRange: number
  /**
   * §87: max route danger for the path to be considered "safe" (see
   * calculateRouteDanger). 0 = no enemy between player and item — the
   * strictest, intended default.
   */
  pickupPriorityMaxDanger: number
  /**
   * §87: nearby-enemy radius (cells). When any fully-spawned enemy is within
   * this Manhattan distance of the PLAYER, urgent pickups are skipped — even
   * if no enemy lies strictly between the player and the item.
   *
   * per-seed tick-diff finding (2026-08-02, Lattice s2 / Battlement s3):
   * the original "path safe" gate (danger <= maxDanger) only counted enemies
   * BETWEEN player and item. An enemy 5 cells away, or an active firefight,
   * still got abandoned while the player walked to the item — the player
   * then stalled or stopped firing and died. Same radius as S5's P3.2 gate
   * (5 cells) — this is the "don't divert while enemies are breathing down
   * your neck" rule, applied to the urgent branch.
   */
  pickupPriorityMinEnemyDist: number
  /**
   * D5 (plan §D5): un-starve the star economy — when > 0, star/tank urgent
   * pickups whose ITEM cell row >= pickupStarBoxRow (the base box, target 20)
   * bypass the §87 nearby-enemy gate AND the route-danger gate. With 4
   * enemies on field an enemy is almost always within pickupPriorityMinEnemyDist
   * (5) or between player and item, so the gates blocked star/tank pickups
   * forever (Battlement star 0.07/run → stuck at 1★). A star/tank inside the
   * base box is a permanent-DPS upgrade worth the risk. 0 = OFF (byte-identical).
   */
  pickupStarBoxRow: number
  /**
   * E1 / 道具经济 (plan/Battlement-Hard-Exploration 反证判据, 清环前带+补环):
   * 危急道具拾取 (dire-item pickup). 0 = OFF (byte-identical). 1 = ON: when
   * the base is under a DIRE state — enemies swarming within
   * direItemApproachCells of the base (liveEnemies >= direItemMinEnemies) OR
   * the base ring is damaged (ring intact <= direItemRingLow) — a bomb/freeze/
   * fence/emp item within direItemRangeCells is worth a divert even with
   * enemies nearby: bomb clears the staging field, freeze buys a kill window,
   * fence/steel reinforces the breached ring (补环). The §87 nearby-enemy +
   * route-danger gates (which block under exactly this 4-enemy pressure, §143
   * D5(b)) are bypassed; reachability + spawn-band gates still apply.
   * Rationale (probe-verified 2026-08-05): 7-seed forensics — 2/7 losses
   * (the high-kill ones) had uncollected HIGH items within 10 cells of the
   * player in the final 400 ticks; the other 5/7 are kill-starved (zero
   * drops upstream) and out of scope for this knob. The divert trade is the
   * plan's last untried lever; the item's active effect resolves the dire
   * state directly, unlike star (passive, D5(b) flat).
   */
  direItemMode: number
  /** E1: min live enemies for the swarm trigger (target 3). */
  direItemMinEnemies: number
  /** E1: enemy→base Manhattan range (cells) for the swarm trigger (target 6). */
  direItemApproachCells: number
  /** E1: ring intact count (of 8) at/below which the fence trigger fires (target 4). */
  direItemRingLow: number
  /** E1: max player→item distance (cells) for the dire divert (target 10). */
  direItemRangeCells: number
  /**
   * §87: enemy spawn-zone gate. When > 0, urgent pickups whose ITEM cell row
   * is <= this value are skipped — classic enemies spawn at row 0 (ENEMY_SPAWNS
   * {0,0}/{12,0}/{6,0}), so rows 0..max are the spawn band where the player
   * gets funneled into fresh enemies.
   *
   * per-seed tick-diff finding (2026-08-02, Lattice s2/s32): diving for a
   * "safe" pickup in the top band put the player inside the enemy spawn
   * corridor — s32 walked up to a fence at (1,0) while an enemy spawned at
   * (0,0) beside it; s2 diverted to a star at row 2. Both died. The gate
   * excludes the band outright — the S5 economy can still fetch far items
   * there via normal navigation, but the AI never treats the spawn band as
   * an urgent short-range errand.
   */
  pickupPrioritySpawnRowMax: number

  // ---- §88: 据守咽喉要地 (chokepoint holding, user request 2026-08-02) ----
  /**
   * §88: 0 = OFF (byte-identical to pre-§88). 1 = ON: when the base is NOT
   * under threat, the player HOLDS a 咽喉要地 (chokepoint) instead of chasing
   * the nearest enemy — a lower-half map cell from which the player can
   * shoot enemies traversing the most 威胁路径 (threat paths: A* routes
   * from each enemy to its nearest 威胁点, facing-gated per rule 3). When
   * few enemies remain (<= chokepointHoldThreshold), the player chases the
   * enemy nearest a threat point instead.
   *
   * 威胁点 (threat point) = a cell from which an enemy can directly shoot the
   * base — exactly the `canShootBaseFrom` predicate (SmartThreatModel).
   *
   * §88 also reorders the §87 urgent-pickup priority chain to the user's
   * spec: HIGH tier (bomb/freeze/fence 8格) > 回防基地 > MID tier
   * (star/tank/shield 4格) > 据守咽喉要地. Only active when chokepointMode
   * is ON; when OFF, §87 keeps its shipped all-tiers-before-defense order
   * (byte-identical).
   */
  chokepointMode: number
  /**
   * §88: margin (cells) around a threat point that still counts as "base
   * threatened" (rule 1: 威胁点外 2 格, needs tuning). An enemy within this
   * Manhattan distance of ANY threat point triggers the base-threat state.
   */
  threatPointMargin: number
  /**
   * §88: hold-threshold (rule 2: 敌人数目 > 2 → 据守). When the number of
   * live enemies on field is GREATER than this value (and the base is not
   * threatened), the player holds the chokepoint; at or below it, the
   * player chases the enemy nearest a threat point.
   */
  chokepointHoldThreshold: number
  /** §88: lowest row for chokepoint candidates (地图下半区, default 13). */
  chokepointMinRow: number
  /**
   * §88: tie-break weight for STEEL cover around a chokepoint candidate
   * (rule 5: 钢铁优先级远高于砖墙). Steel-adjacent cells score
   * chokepointSteelWeight each; brick cells chokepointBrickWeight.
   */
  chokepointSteelWeight: number
  /** §88: tie-break weight for BRICK cover around a chokepoint candidate. */
  chokepointBrickWeight: number
  /**
   * §88: facing gate (rule 3). 1 = a threat path is only counted when the
   * enemy's turret (炮口朝向) is along the path's dominant direction toward
   * the threat point (an enemy facing AWAY from the base is not about to
   * attack it). 0 = ignore facing (all paths counted).
   */
  chokepointFacingGate: number
  /**
   * §88: max number of nearest threat points per enemy to A*-path (throttled
   * chokepoint computation — bounds cost; default 4). The threat-point set
   * itself is terrain-derived and small (base column + base rows).
   */
  chokepointPathsPerEnemy: number
  /**
   * §88: enemies farther than this Manhattan distance from their NEAREST
   * threat point contribute NO threat paths (perf bound: a failed A* explores
   * the whole grid — skipping far enemies keeps the throttled plan cheap; a
   * distant enemy is not an imminent base threat, so its path adds little to
   * chokepoint selection). Default 14. 0 = no cap.
   */
  chokepointMaxThreatDist: number
  /** §88: recompute interval (ticks) for the throttled chokepoint plan. */
  chokepointReplanTicks: number
  /**
   * §88 A/B round 2: chase arm imminence gate (cells). Only enemies whose
   * NEAREST threat point is within this Manhattan distance count as imminent
   * base threats worth diverting for — otherwise the chase arm dragged the
   * player after distant enemies (S15 seed 24) and the hold arm idled at a
   * stale chokepoint (S19 seed 23). 0 = gate off (chase any nearest).
   */
  chokepointChaseMaxDist: number
  /**
   * §88 A/B round 3: hold-arm max distance. A chokepoint 9 cells away is not
   * worth marching to — the enemy turns / gets killed en route and the player
   * idles (S26 seed 12: player at (7,14) marched to hold (14,16), the fast
   * threat died, and the stale path derailed navigation). When the hold cell
   * is farther than this, prefer chasing the imminent threat directly. 0 = unlimited.
   */
  chokepointHoldMaxDist: number
  /**
   * §88 A/B round 3: chase-arm max PLAYER distance. The chase arm intercepts
   * an enemy about to reach a threat point, but only pays off when the player
   * can actually get there in time. S32 seed 10: chase sent the player from
   * (8,3) on a 27-cell march to intercept (0,22) — the enemy reached the
   * threat point long before the player arrived, and the march derailed the
   * game while A's normal nearest-enemy hunt won. 0 = unlimited (pre-round-3).
   */
  chokepointChaseMaxPlayerDist: number

  /**
   * M2 (plan/God-AI-Redesign-v2 §3.2): per-params decision-chain weight
   * overrides. When provided, the M1 candidate chain is evaluated in
   * effective-weight order (`overrides[id] ?? ACTION_WEIGHTS[id]`, descending,
   * stable) instead of the fixed chain order. The ordered candidate list is
   * pre-built once per reset in GodAIInput — never sorted per tick
   * (AGENTS §14.3/14.5).
   *
   * Default (undefined) = the M1 chain order (dodge > interceptBase >
   * pickupHigh > aggro > pickupMid > engage > pickupLow > hunt) —
   * byte-identical to pre-M2. Changing a weight IS a behavior change and
   * must go through the 60-seed A/B discipline (official 口径, no stageIndex).
   */
  actionWeights?: Partial<Record<ActionId, number>>

  // ---- M3: 敌情感知 EnemyModel + 命数感知 (plan/God-AI-Redesign-v2 §4.2b/§4.3) ----
  /**
   * M3: 0 = OFF (byte-identical to pre-M3 — the model never updates and all
   * consumers return 0). 1 = pure dynamic EnemyModel (features only).
   * 2 = 混合模式 (50/50 blend with the static `aiState.level` prior, which
   * requires enemyTierWeightCommander/Veteran > 0). The model is a per-tick
   * EMA of observable enemy behavior (fire accuracy / base approach /
   * alignment / turn discipline) — pure World observation, no RNG, no
   * difficultyKey reads (评审决议 3).
   */
  enemyModelMode: number
  /** M3: EMA window (ticks) for the EnemyModel features. >0 required for
   *  the model to run (`enemyModelMode > 0 && window > 0`). */
  enemyModelWindowTicks: number
  /**
   * M3: 感知敌人强度 → 目标选择加权. When > 0, findEnemyDirectionImpl scales
   * the T9 threat score by `1 + tierWeightScale * estimatedEnemyLevel` — the
   * AI commits its fire priority to the enemies it has SEEN play well, rather
   * than a static kind weight. Default 0 (byte-identical).
   */
  tierWeightScale: number
  /**
   * M3: 感知闪避率 → 有效 T2a 射程缩放. When > 0, the ENGAGE candidate's
   * effective 1-HP range shrinks as the model's `discipline` (turn frequency)
   * rises: `effectiveRange = t2aMaxRange * (1 - dodgeRateShrinksT2a * discipline)`.
   * A stage where enemies dodge/redirect a lot forces point-blank engagement
   * (shorter bullet travel → fewer dodged shots). Default 0 (byte-identical).
   */
  dodgeRateShrinksT2a: number
  /**
   * M3: 配合压力 → 保命压力提前. When > 0, survival pressure activates when
   * `coordination * weight >= 1` (many enemies aligned with the player at
   * once) — even while lives are still plentiful. Default 0 (byte-identical).
   */
  coordinationRiskWeight: number
  /**
   * M3: 敌人命中率 → 保命压力提前. When > 0, survival pressure activates when
   * the model's estimated fire accuracy reaches this threshold (0..1) — the
   * enemy is hitting, so stop taking risks NOW, not when lives run out.
   * Default 0 (byte-identical).
   */
  enemyAccuracyRaisesSurvival: number
  /** M3 (混合模式 prior): static weight for commander-tier enemies in
   *  `staticPriorLevel`. 0 = no prior signal from commanders. */
  enemyTierWeightCommander: number
  /** M3 (混合模式 prior): static weight for veteran-tier enemies. */
  enemyTierWeightVeteran: number
  /**
   * M3 (P0-3 命数盲 fix, plan §4.3): 0 = OFF (byte-identical). >0 = survival
   * pressure activates when `world.lives <= this value` — the AI stops taking
   * high-risk actions (deep hunts, long-range duels) on its last lives.
   * chaos startLives=3 ⇒ survivalModeLives=1 means "act safe on the last life".
   */
  survivalModeLives: number
  /**
   * M3: 生存模式下高风险动作的风险惩罚系数 γ. When survival pressure is 1,
   * high-risk candidates (hunt / long-range engage) are suppressed — the
   * higher this weight, the earlier/deeper the retreat to defense. Default 0
   * (byte-identical — no suppression).
   */
  survivalRiskWeight: number
  /**
   * M3: survive 候选（主动换位）的包围触发阈值. When > 0, the survive
   * candidate may activate: at least this many live enemies within
   * `surviveEnemyRadiusCells` of a ≤2-exit cell the player occupies. 0 = OFF
   * (candidate never commits — byte-identical). Default 0.
   */
  surviveMinEnemies: number
  /** M3: survive 候选的包围判定半径 (cells). */
  surviveEnemyRadiusCells: number

  // ---- M13: field-wide outnumbered positioning (DECISIONS §113) ----
  /**
   * M13: 0 = OFF (byte-identical). 1 = ON: when the FIELD-wide live enemy
   * count is at/over `outnumberedFieldEnemies` (P4.2 only counts enemies
   * WITHIN outnumberedRadiusCells — converging) AND the player is beyond
   * `outnumberedFieldDistCells` from the base, selectTarget returns the
   * defense position instead of deep-hunting. Targets the dominant death
   * mode (M13 probe): 70% of hard/chaos deaths happen with the full 4-enemy
   * field alive, 39% at >20 cells from base, 85% at 1★ — the player
   * over-extends while outnumbered and gets ground down (1★ single bullet
   * cannot out-race 4 enemies). Skipped when the base is under threat or in
   * aggressive (freeze) mode.
   */
  outnumberedFieldRetreat: number

  /** M13: field-wide live-enemy threshold for the retreat (default 4 = MAX_ENEMIES_ALIVE). */
  outnumberedFieldEnemies: number

  /** M13: min player dist-to-base (cells) for the retreat. */
  outnumberedFieldDistCells: number

  /**
   * §146 C: when the M13 field-pressure retreat condition holds (far from
   * base + full enemy field, base NOT under threat), suppress the HIGH-tier
   * urgent pickup so HUNT's M13 retreat fires instead of being hijacked by
   * the pickup branch (weight 800 > hunt 200). S8: the player stayed in the
   * dead-end pocket chasing items while the base fell. The predicate is
   * shared with selectTarget (isFieldRetreatConditionImpl) — the item may
   * still be picked on the way back. Pool-model only (M13 is pool-only).
   * 0 = OFF (byte-identical).
   */
  fieldRetreatPickupGate: number

  // ---- 自杀秒回 (suicide quick-return, user request 2026-08-04, §116/§117) ----
  /**
   * 0 = OFF (byte-identical to pre-§116). 1 = §116 original — trigger on
   * condition ⑤ (a LETHAL bullet hits within suicideReturnBulletTimeTicks;
   * the player is about to die anyway, so the life-trade is nearly free).
   * 2 = §117 condition-① variant, STAND — trigger when an enemy is at a
   *   threat point while a bullet is actively flying at the base; the player
   *   stands still and waits to be killed, with a suicideReturnStandMaxTicks
   *   timeout (resumes if no death comes). 3 = §117 condition-① variant,
   *   CHARGE — same trigger, but the player actively drives at the threat
   *   enemy (no dodging) to die fast and respawn near the base, or to kill
   *   the enemy first; whichever happens first ends the trade.
   *
   * The shared preconditions (checked in the SUICIDE_RETURN candidate,
   * think.ts; condition ⑤ only for mode 1):
   *   1. An enemy is at a threat point (can directly shoot the base).
   *   2. The player's spawn point can hit that enemy (immediately or 1 turn).
   *   3. The player has spare lives (lives ≥ suicideReturnMinLives).
   *   4. The player is too far from that enemy (> suicideReturnEnemyDistTicks
   *      at full speed — the player can't save the base by running back).
   *   5. (mode 1 only) A lethal bullet will hit the player within
   *      suicideReturnBulletTimeTicks (dodging is futile or the player is
   *      about to die anyway — trade the life for a better position).
   *   GATE (all modes): a bullet is actively flying at the base
   *   (findBulletThreatToBaseImpl) — the S23 seed-14 regression fix.
   */
  suicideReturnMode: number
  /** Max ticks for the lethal bullet to reach the player (default 60 = 1s). */
  suicideReturnBulletTimeTicks: number
  /** Min ticks the player would need at full speed to reach the threat enemy
   * (default 300 = 5s). If the player can reach it faster, no suicide. */
  suicideReturnEnemyDistTicks: number
  /** Min lives required (default 2 = at least 1 spare life beyond the current).
   * When the player dies, lives-- then respawn only if lives > 0, so lives
   * must be ≥ 2 before death to guarantee a respawn. */
  suicideReturnMinLives: number
  /**
   * Spawn-point usefulness margin for the suicide return (condition 2). The
   * spawn must be able to deal with a threat enemy in time — either a clear
   * 0-1-turn shot (spawnCanHitEnemyImpl) OR within this many Manhattan cells
   * of the enemy (respawn puts the player close enough to reach it fast with
   * its 3s shield). Strict 0-1-turn geometry is rare on real stages (base
   * walls/topology), so the distance fallback makes the strategy fire where
   * it genuinely helps. Larger = more permissive (respawn more often); 0 = the
   * distance fallback is disabled (only the strict 0-1-turn shot counts).
   */
  suicideReturnSpawnDistCells: number
  /**
   * Mode-2 (STAND) standing timeout in ticks (default 300 = 5s). The player
   * stands still waiting to be killed for at most this many ticks; if no
   * death comes (nobody shoots the stationary player), it resumes normal
   * play instead of freezing forever (the §116 S30 standing-freeze
   * pathology, moved to the healthy-player case). Only used when
   * suicideReturnMode === 2.
   */
  suicideReturnStandMaxTicks: number

  // ---- §118: strict-doom guard for modes 2/3 (baseHp + defense-lost) ----
  /**
   * §118: base-HP doom threshold for modes 2/3, as a FRACTION of baseMaxHp.
   *
   * Root cause of the §117 flip losses: the condition-① trade committed while
   * the base was at FULL HP with the normal defense (T8 intercept / 据守 /
   * return) still running — one in-flight bullet is NOT proof the base will
   * fall (hard S35 seed-8: base 120/120, the OFF arm returned and cleared;
   * the ON arm abandoned the defense and lost). When > 0, modes 2/3 require
   * baseHp ≤ this fraction × baseMaxHp — the base is genuinely a hit or two
   * from falling, so trading a life for a respawn near it is a true last
   * resort. 0 = disabled (byte-identical to §117).
   *
   * Pool-model oriented (hard/chaos). On classic (baseMaxHp = 1) any positive
   * fraction < 1 is unsatisfiable, which safely disables the trade there.
   */
  suicideReturnBaseHpFrac: number
  /**
   * §118: defense-position-lost distance for modes 2/3 (Manhattan cells).
   *
   * When > 0, the trade only commits when the player is farther than this
   * from the base — i.e. the player CANNOT return in time to intercept the
   * in-flight base bullet (measured: killer-bullet travel to base is 14-15
   * ticks ≈ 0.24s). A close player must NOT abandon a working defense
   * position for a gamble. 0 = disabled (byte-identical to §117).
   */
  suicideReturnDefendDistCells: number

  // ---- §121: t2a/aggressive 停射自毁守卫 (self-fire base guard, §120 forensics) ----
  /**
   * 0 = OFF (byte-identical to pre-§121). 1 = STRICT: suppress the
   * t2a/aggressive stop-and-aim fire when the bullet's CENTER line (the
   * actual 6px path — NOT the scan's ±8px offset lines) can reach the base
   * eagle; the terrain walk ignores tanks (they can dodge off the line).
   * 2 = LENIENT: same walk, but only suppress when NO enemy tank body
   * overlaps the bullet corridor before the base (keeps true point-blank
   * overlap kills where the bullet provably hits the enemy first).
   *
   * Root cause (§120, 32/32 self-kill forensics): the scan's two ±8px
   * offset lines catch an enemy up to ~25px off the bullet's 6px center
   * path and report scan.enemy with the enemy CLOSER than the base eagle —
   * the §74 dual-offset guard then allows fire ("the enemy is in the way").
   * The 6px bullet misses the off-line enemy and continues into the base
   * (hard S6 s43: killer shot at x=200, enemy body at x∈[206,238] — 6px
   * bullet [197,203] passed beside it into the eagle).
   */
  selfFireBaseGuard: number

  // ---- §152: S12 replay fixes (2026-08-05) ----
  /**
   * §152-W1: steel-path stop-and-aim gate. 0 = OFF (byte-identical).
   * 1 = ON (default): the T2a/aggressive stop-and-aim fire is suppressed when
   * the bullet's ACTUAL 6px path (not the scan's ±8px offset lines) hits
   * non-ring steel BEFORE the aligned enemy. Root cause (hard S12 Lattice
   * seed 934391936, 0:59-1:01): the player stopped at (17,18) with its center
   * x=288 exactly on the col-17/18 boundary and fired up at a fast enemy at
   * (17,3) — the bullet box [285,291] clips the steel column 18 [288,304) at
   * rows 8-9 and dies there, never reaching the enemy, while the scan saw the
   * enemy on one offset line (scan.enemy=true). The scan-steel gate is
   * deliberately NOT used (it over-suppresses the dual-offset case where the
   * enemy is genuinely reachable — §74 A/B: 20 kills → 7 kills); the precise
   * 6px-center-line walk only blocks when the sim would actually stop the
   * bullet. Inert at level ≥ 3 (steel-pierce).
   */
  t2aSteelPathBlock: number
  /**
   * §193-A: 中线火力门（center-line fire gate）。1 = ON：敌人-aim 开火前，
   * 若双偏线扫描同时报了 enemy 与 wall（墙在一条偏线、敌在另一条），用真实
   * 弹道中线（6px box）从炮口走到敌人格；中线被砖/钢/环/基地提前挡住时
   * 不开火（子弹必死在墙上，白费冷却）——seed-14 (16,23) 砖格吃弹根因。
   * 0 = OFF（byte-identical）。
   */
  centerLineFireGate: number
  /**
   * §193-D: 预测前移门（predictive lead gate）。扫描看到的是目标的当前
   * 位置；子弹飞行 dist/bulletSpeed ticks 后，横向穿行的 fast 已滑出
   * ±(TANK+BULLET)/2 命中窗 → 必miss 白费冷却（时间版的 §193-A 墙吃弹）。
   * >0: 当目标以垂直方向横穿弹道线、且子弹到达时其身体已滑出命中窗，
   * 抑制本次开火，把时机窗口交给 P2.4 predictEnemyCrossing。
   * 0 = OFF（byte-identical）。
   */
  predictiveFireGate: number
  /**
   * §152-W2: aggressive-branch movement-stuck guard (ticks). 0 = OFF
   * (byte-identical). >0: during a freeze window (aggressive mode) with no
   * aimable enemy and no power-up, the navigate path tracks how long the
   * player stays within a ±1-cell zone with no kills (zone-based — the
   * exact-cell check misses the classic two-cell ping-pong). After
   * aggNavStuckTicks the player commits a navigate-to-center escape for the
   * antiCampSuppressTicks window (A* routes around the blocking frozen tank /
   * water — the only open direction leads out of the dead-end corridor).
   * Root cause (hard S12 Lattice seed 934391936, 1:04-1:16): A* ignores
   * tanks, so a frozen enemy's 0.8px body overlap in the next cell made the
   * path first-step blocked every replan; followPath's fallback ping-ponged
   * up/down at (8,16)↔(8,17) for the whole freeze window (720+ ticks, zero
   * kills).
   */
  aggNavStuckTicks: number
  /**
   * §152-W3: urgent-pickup commit persistence (ticks). 0 = OFF — the SHIPPED
   * default (byte-identical to pre-§152). >0: once PICKUP_HIGH/PICKUP_MID
   * commits to an item, the pursuit continues for up to pickupCommitTicks
   * while the item is still alive — the transient "dist > range" exclusion
   * (the player MOVING toward the item pushed its manhattan distance past the
   * category range) must not cancel an active pursuit. Root cause (hard S12
   * Lattice seed 934391936, 1:38-1:56): the decoy at (21,14) sat exactly at
   * the mid-range boundary (4 cells = pickupPriorityMidRange); from (21,18)
   * dist=4 (commit → move right), from (22,18) dist=5 (skip → navigate left)
   * — the player ping-ponged at (21,18)↔(22,18) for ~800 ticks with zero
   * kills while enemies swarmed the base. NOT SHIPPED: the 35×60 hard A/B +
   * per-seed isolation showed the commit hijacks base defense on the 4 S34
   * Battlement flip seeds (all die with baseHp=0) and turns the S12
   * seed-934391936 win back into a loss — each fix alone wins, ALL ON loses.
   * The W3 window is already fixed by W1+W2's trajectory change (the player
   * navigates instead of bouncing). Experimental knob only.
   */
  pickupCommitTicks: number

  // ---- §156: freeze-window power-up pickup (unlimited range) ----
  /**
   * §156: 0 = OFF (byte-identical). >0 = during freeze (aggressive mode),
   * ANY reachable power-up is picked up BEFORE stop-and-aim at frozen
   * enemies. Enemies are frozen — they cannot move or fire — so the only
   * threat is in-flight bullets (handled by DODGE, which has higher
   * priority). The frozen enemy will still be there after the few ticks
   * it takes to grab the item.
   *
   * §156-v2 (user request 2026-08-06): range changed from 2 to 999
   * (effectively unlimited — grid is 26×26, max Manhattan = 50). During
   * freeze the player should traverse the map to grab any reachable
   * power-up; the frozen enemies pose zero threat.
   *
   * Root cause (hard S12 Lattice, 0:18~0:28): PICKUP_HIGH (weight 800) is
   * gated by `!self.aggressive` and skipped during freeze. AGGRO (700) then
   * prioritizes stop-and-aim at any aligned frozen enemy, never checking for
   * nearby power-ups. The S5 economy scan in AGGRO only runs when NO enemy
   * is aligned. A power-up 2 cells away was ignored for the entire freeze
   * window while the player camped shooting a frozen enemy.
   */
  freezePickupRange: number

  // ---- §158: non-freeze close-range power-up pickup ----
  /**
   * §158: 0 = OFF (byte-identical). >0 = in normal (non-freeze) mode, a
   * power-up within this many cells (Manhattan) is picked up when there is
   * no immediate bullet threat (DODGE at weight 1000 already declined).
   * Unlike PICKUP_HIGH/MID (which gate on nearby-enemy proximity and route
   * danger), this candidate has NO enemy gates — close items are worth
   * grabbing even with enemies nearby, as long as no bullet is currently
   * threatening the player. The player fires at enemies in the move
   * direction while navigating (随手开火).
   *
   * Skips when the base is under threat (defense outranks a nearby item).
   * The candidate chain guarantees safety: DODGE (1000) > INTERCEPT_BASE
   * (900) > PICKUP_HIGH (800) > AGGRO (700) > PICKUP_MID (600) >
   * DEFENSE_INTERCEPT (550) > CLOSE_PICKUP (540) — by the time this runs,
   * all higher-priority threats/items and defense intercepts have declined.
   */
  closePickupRange: number

  // ---- §166 / B1: star rush — 星经济冲刺 (star-economy sprint) ----
  /**
   * §166 / B1: 0 = OFF (byte-identical). 1 = ON: while the controlled tank's
   * star level is below `starRushMaxLevel`, STAR power-ups get an extended
   * urgent range (`starRushRangeCells` instead of pickupPriorityMidRange=4)
   * and — with `starRushLiftGates` — bypass the §87 nearby-enemy + route-
   * danger gates (same exemption shape as D5's base-box star exception, but
   * field-wide).
   *
   * Data (hard 35×120 forensics, 2026-08-07): 91% of losses are base
   * deaths at ~62s with only 9.4/20 kills; 75% of those runs never picked a
   * star and 84% died at 1★. Runs that DID grab a star had +3.8 kills and
   * lived +15s. Causal backing: M6 (0★→1★, +9pp) and M11 (1★→2★, +9.4pp —
   * the start-level variant was rejected by the user as unfair to humans,
   * but MID-RUN star pickup is pure AI skill). Each star ≈ fire-rate +
   * bullet-speed up; the first star (1★→2★) is the one that matters, hence
   * the level gate (rush stops at starRushMaxLevel).
   */
  starRushMode: number
  /** §166 / B1: rush stars only while level < this (2 = rush the first star). */
  starRushMaxLevel: number
  /** §166 / B1: extended urgent range (cells) for stars while rushing. */
  starRushRangeCells: number
  /**
   * §166 / B1: 1 = lift the §87 nearby-enemy + route-danger gates for rush
   * stars — under 4-enemy field pressure both gates block forever (the D5
   * finding), starving the player at 1★. 0 = gates apply (range-only rush).
   */
  starRushLiftGates: number

  // ---- §167 / B4: super-item strategic activation (超级道具战略激活) ----
  /**
   * §167 / B4: 0 = OFF (byte-identical — God AI never presses F5/F6, the
   * pre-§167 "super items are human-only" behavior). 1 = ON: think() sets
   * per-tick item-press flags (see superItemGuardThreat / superItemFrenzyAim)
   * that wasItemPressed() reports, letting the Simulation activate stocked
   * super items (guard summon / frenzy barrage) on the AI's behalf.
   *
   * Data (hard 35×120 forensics, 2026-08-07): ~8% of losing runs finish with
   * an UNUSED guard stock, ~8.5% with an unused frenzy stock (pickup census =
   * stock, since the AI never consumed any). The guard is a full GOD-AI-brain
   * base defender (§159) — exactly the tool for the 91% base_destroyed loss
   * cause.
   */
  superItemMode: number
  /**
   * §167 / B4: guard release gate (F5). >0 = press guard when the base is
   * under threat (isBaseUnderThreat — the same reactive signal the defense
   * branches use) and no allied guard is currently alive. Reactive trigger
   * only — never pins the player (§163/§164 lesson), zero interference with
   * the kill rhythm.
   */
  superItemGuardThreat: number
  /**
   * §167 / B4: frenzy release gate (F6). >0 = press frenzy when the player's
   * CURRENT facing (p.dir) has an enemy in its shot corridor
   * (enemyInShotCorridor), no incoming-bullet threat is active (frenzy locks
   * movement — releasing into a threat is a free death), and no frenzy
   * barrage is already running.
   */
  superItemFrenzyAim: number

  // ---- §157: base clear-shot threat detection ----
  /**
   * §157: 0 = OFF (byte-identical). 1 = ON: isBaseUnderThreat() also returns
   * true when any alive, spawned enemy can currently shoot the base
   * (enemyCanShootBase — aligned + clear line of sight, no brick/steel in
   * between). This catches enemies firing at the base from beyond the static
   * box (row < 18) or the race range (baseRaceRangeCells), which the existing
   * position-based checks miss.
   *
   * Root cause (hard S12 Lattice, 0:38~0:48): an enemy aligned with the base
   * column from row ~10 (14+ cells away, beyond baseRaceRangeCells) was
   * actively shooting the base through a cleared lane. isBaseUnderThreat()
   * returned false (row < 18, distance > race range), so selectTarget didn't
   * return the defense position and ENGAGE's skipT2aForDefense didn't fire.
   * The player kept hunting/engaging at the top of the map while the base
   * was destroyed.
   */
  baseClearShotThreat: number

  // ---- §169: base-threat signal stickiness ----
  /**
   * §169: 0 = OFF (byte-identical). >0 = once isBaseUnderThreat() goes true,
   * it stays true for at least this many ticks even if the underlying
   * detection clears (enemy steps out of the race range / aligned column for
   * a moment). The hold refreshes every tick the underlying signal is true;
   * it only EXTENDS, never shortens.
   *
   * Root cause (defeat decision-chain probe, 363 base_destroyed losses):
   * the threat signal FLICKERS — in the 10s before the base's first hit the
   * signal is true only 69.6% of ticks, flipping ~9.8× per 10s. Every false
   * gap drops selectTarget into the nearest-enemy hunt branch, yanking the
   * player off the base approach; the defense branch runs 87.6% of the
   * window yet the player closes distance on only 3% of ticks. Stickiness
   * closes the gaps so the defense cascade stays engaged.
   */
  threatStickyTicks: number

  // ---- §170: hunt commit (追击承诺) ----
  /**
   * §170: 0 = OFF (byte-identical). >0 = after the NORMAL hunt branch
   * (!baseUnderThreat nearest-enemy selection) picks a target, commit to
   * that enemy for this many ticks: while the window is open and the
   * committed enemy is still alive, keep chasing it even if another enemy
   * becomes briefly nearer. On expiry the free nearest-selection resumes (and
   * re-commits if the same enemy is still the pick).
   *
   * Root cause (tail-stage probe + S34 deep dive, DECISIONS §170): losses
   * spend 73.8% of ticks in navigate (wins 59.1%) with a longer mean
   * distance to the nearest enemy (8.0 vs 7.2 cells) — the per-tick
   * nearest-enemy reselection re-routes the mid-approach player whenever
   * the nearest identity flips, sinking the approach cost repeatedly. S34
   * losses close to within 5 cells of an enemy on only 9.0% of ticks
   * (wins 31.8%) while parked enemies live p75 65.6s. The commit keeps the
   * approach on one enemy until the kill. Defense cascade / canHunt /
   * aggressive branches are untouched; no commit is written under threat.
   */
  huntCommitTicks: number

  // ---- §171: path-aware target selection (路径长度感知目标选择) ----
  /**
   * §171: 0 = OFF (byte-identical, Manhattan-nearest). 1 = the NORMAL hunt
   * branch (!baseUnderThreat) scores each enemy by TRUE travel cost instead
   * of Manhattan distance: corridor path length when `findPath` connects,
   * dig path length + penalty otherwise (digging costs brick-clearing time
   * far beyond step count), Manhattan + large penalty when neither exists.
   * The bonus −2 adjustment still applies. Selection stays per-tick — only
   * the RULER changes.
   *
   * Root cause (divergence probe, tmp/probe-pathdiv.ts, DECISIONS §171):
   * the Manhattan-chosen target's true path overhead averages 20.8 cells in
   * losses vs 3.3 in wins (6.3×); on diverged frames losses pay +4.2 extra
   * path cells vs wins +2.3. Maze-stage losses (S15/S11/S10 gaps 117–230)
   * are pulled at "Manhattan-near but wall-separated" enemies, burning the
   * approach budget (navigate share 73.8% vs 59.1%) and starving kills.
   */
  pathTargetMode: number

  // ---- §172: bonus enemy hunt bias (bonus 敌人追猎偏置) ----
  /**
   * §172: Manhattan-distance bias applied to bonus enemies in the NORMAL
   * hunt branch (!baseUnderThreat) nearest-enemy loop. Default 2 = the
   * historical hardcoded constant (byte-identical). Drop-economy lever:
   * only bonus enemies drop power-ups; 75% of losses never see a star and
   * item spawns run 531 vs 948 (loss vs win). At 10–30 cell distance scales
   * a 2-cell bias barely ever flips the pick, so candidate arms 4 / 6 test
   * whether a stronger bonus preference converts into more drops → more
   * stars → kill throughput. Per-tick reselection is untouched (§170).
   */
  bonusHuntBias: number

  // ---- §173: base damage recall (基地损伤召回) ----
  /**
   * §173: 0 = OFF (byte-identical). >0 = the distance gate (cells): once
   * the base has actually TAKEN A HIT (baseHp < baseMaxHp) AND the player
   * is farther than this many cells away, isBaseUnderThreat() returns true.
   * Unlike the predictive threat checks (box / race / clear-shot — guesses
   * about what an enemy MIGHT do), base damage is a FACT: the ring bricks
   * are breached and direct fire is landing. While engaged, every downstream
   * defense consumer (selectTarget recall, skipT2a, item gates, F5 guard
   * summon, carve gate) stays on; the distance gate releases the cascade as
   * soon as the player comes home.
   *
   * Root cause (hp-leash probe, tmp/probe-hpleash.ts, DECISIONS §173): in
   * S34 losses the base takes its first hit with the player at median 25
   * cells away and only a 5.1s median survival window left; in wins the
   * player is already home (median 10 cells). The predictive signal flickers
   * through that window (§169: 9.8 flips/10s), so the recall at
   * StrategyPlanner L1254 never holds. Damage is the flicker-free trigger.
   *
   * Arm history: 1 (unconditional) was net −24 (z=−1.40) — the permanent
   * cascade dragged open stages (S8 −8, S12 −7, S11 −6) while the target
   * stages improved (S34 +3, S31 +5, S5 +4). The probe asymmetry is
   * player-distance, so the knob is the gate itself.
   */
  baseDamageRecall: number

  // ---- §179: emergency base defense (autopsy seed6) ----
  /**
   * §179 (autopsy hard-s34-base-l3-t92-seed6): 0 = OFF (byte-identical).
   * >0 = when baseHp / baseMaxHp ≤ this fraction, ALL tanks are forced to
   * return to the defense position regardless of their current target.
   * The autopsy showed both tanks oscillating in the top-right corner for
   * 18 seconds while the base dropped from 48→12→0 HP — no emergency
   * override existed. Default 0.25 (base at 25% — one or two hits from
   * death). Also overrides the navStuck escape target from map center to
   * the defense position while active.
   */
  emergencyBaseHpFrac: number

  /**
   * §179 (autopsy seed6 失误 D): 0 = OFF (byte-identical). >0 = during
   * freeze (aggressive mode), selectTarget picks the enemy nearest to the
   * BASE instead of nearest to the player. The autopsy showed a 20-second
   * freeze window completely wasted — both tanks navigated to enemies in
   * the top-right while an enemy sat at (7,24), 5 cells from the base, for
   * the entire freeze. Default 1 (ON).
   */
  freezeBasePriority: number

  // ---- §159: T2a defense override for close enemies ----
  /**
   * §159: 0 = OFF (byte-identical). >0 = when the base is under threat and
   * the player is past `maxPlayerDistFromBase`, the ENGAGE candidate's
   * `skipT2aForDefense` gate is STILL bypassed if an enemy is within this
   * many cells (scan distance) in the `aimDir` direction.
   *
   * Root cause (hard S20 Bastion seed 383912762, 0:39~0:42): the player at
   * cell (17,2) was 1 cell past `maxPlayerDistFromBase` (dist 27 > 26) while
   * an armor enemy sat 2 cells to the left with a clear bullet lane.
   * `skipT2aForDefense` blocked ENGAGE, the player fell through to HUNT,
   * and the navigation target alternated between the base defense position
   * and the enemy — a sub-cell up/down oscillation that burned 160+ ticks
   * (the enemy slowly walked away unharmed). At row 3 (dist 26 ≤ 26) the
   * gate cleared, but the scan's ±CELL/2 offset lines missed the enemy by
   * <1px due to sub-cell alignment — so ENGAGE never fired from either
   * position.
   *
   * The fix: a close enemy in the line of fire is an immediate opportunity.
   * Killing it takes 1–2 shots (a few ticks) and directly helps defense
   * (one fewer enemy threatening the base). The override only applies at
   * CLOSE range (≤ this many cells), so the player still retreats to defense
   * when the aligned enemy is far away.
   */
  t2aDefenseOverrideRange: number

  /**
   * §165 (user request 2026-08-07): T2a outnumbered retreat — when 2+ aligned
   * enemies are within `t2aOutnumberedRange` cells in the same direction, the
   * player is outgunned and must NOT stop-and-aim (a 2v1 stationary duel is a
   * losing trade). Instead, fall through to navigate (which moves to a safer
   * angle or triggers the P4.2 outnumbered retreat). This prevents the
   * “player 在左路与两个敌人对枪被火力压制而死亡” death pattern.
   *
   * 0 = OFF (byte-identical to pre-§165). 1 = ON (count aligned enemies in
   * the scan direction within `t2aOutnumberedRange` cells; retreat when >=
   * `t2aOutnumberedCount`).
   */
  t2aOutnumberedRetreat: number
  /** §165: max range (cells) for the aligned-enemy count. */
  t2aOutnumberedRange: number
  /** §165: minimum aligned-enemy count to trigger the retreat. */
  t2aOutnumberedCount: number

  // ---- §187: Guard/P2 A* player-obstacle + target blacklist + powerup overlap ----

  /**
   * §187: 0 = OFF (byte-identical). 1 = guard and P2 A* pathfinding treats
   * the player (P1) as an impassable, indestructible obstacle. P1 does NOT
   * treat P2 or guard as obstacle. Prevents the guard/player mutual-block
   * deadlock (S7@seed54: 23.9s stuck).
   */
  navAvoidPlayer: number

  /**
   * §187: 0 = OFF (byte-identical). >0 = when the player has been
   * pixel-stuck (`_digBlockTicks`) for >= this many ticks while targeting
   * enemy A, A is temporarily removed from the target pool. The player
   * picks a different target, and A returns to the pool after
   * `targetBlacklistDuration` ticks. Default 240 (4s) — raised from initial
   * 120 (2s) which caused S35 chaos regression (18→12/20).
   */
  targetBlacklistStuckTicks: number

  /**
   * §187: how many ticks a blacklisted enemy stays out of the target pool.
   * Default 180 (3s).
   */
  targetBlacklistDuration: number

  /**
   * §187: 0 = OFF (byte-identical). 1 = when a powerup's cell overlaps with
   * a live enemy, skip that powerup — the player kills the enemy first
   * (via hunt/aggro), then picks up the powerup. Prevents the player from
   * getting stuck trying to reach a powerup blocked by an enemy
   * (S2@seed83: 17.6s stuck).
   */
  powerupEnemyOverlapSkip: number

  /**
   * §302: pursuit-tail navigation (plan/Intent-Policy-NN-Plan.md §12.1
   * defect #3 — "追击走并行车道横向开火，不并入目标车道后方").
   *
   * 0 = OFF (byte-identical). 1 = while HUNT chases a live enemy, steer to
   * the cell `pursuitTailCells` BEHIND the target along its travel axis and
   * close the perpendicular (lane) gap first, instead of walking a parallel
   * lane and firing sideways. See pursuitTailDirImpl (Navigator.ts) for the
   * root-cause analysis of why directMove's vertical-first priority produces
   * the parallel-lane pattern against vertically travelling targets.
   */
  pursuitTailMode: number
  /** §302: how many cells behind the target the merge point sits. */
  pursuitTailCells: number
  /** §302: don't tail-gate closer than this (cells) — close combat owns it. */
  pursuitTailMinCells: number
  /** §302: don't tail-gate beyond this (cells) — the lane detour costs more
   * than the aligned shot is worth. */
  pursuitTailMaxCells: number
  /**
   * §302 mode 2: max perpendicular (lane) gap the player will cross to merge
   * onto the target's lane. Beyond this the detour costs more time than the
   * aligned shot is worth, and the normal chase is left alone.
   */
  pursuitTailMaxLaneGap: number
  /**
   * §302 mode 7: how close along its travel axis the target must be for the
   * adjacent-lane merge to fire (cells, either side). The merge is intended
   * for a target that is PASSING or ABOUT TO PASS in the neighbouring lane —
   * not for a far-away target the player would have to march across the map
   * to reach (that was the rejected modes 1-6 behaviour).
   */
  pursuitTailAlongWindow: number
  /**
   * §302 mode 7: which side of the target the merge may fire on.
   * 0 = either side (default); 1 = only when the player is in the target's
   * wake (`along < 0`, a true tail-chase); 2 = only when the target is level
   * with or closing on the player (`along >= 0`, side-by-side / intercept);
   * 3 = yield-then-tail — hold (`_moveDir = null`) while `along >= 0` so the
   * target sweeps past the player's row, then merge into its wake. Measured
   * net −58 for merging at `along >= 0` directly (the player cuts the target's
   * bow, facing sideways, and eats the ram); the user directive is to wait,
   * not to cut in early. See Navigator.pursuitTailDirImpl.
   */
  pursuitTailAlongMode: number
}

// ============================================================
// Archived knobs registry — 留档旋钮注册表（唯一事实源）
// (plan/God-AI-Organization.md §6 C1 · DECISIONS §272 · 2026-08-26 数据化)
// ============================================================

/** One archived-knob group: a gate field whose DEFAULT value must stay 0. */
export interface ArchivedKnobGroup {
  /** 门控字段；DEFAULT_GOD_AI_PARAMS 中必须 === 0（守卫测试强制）。 */
  gate: keyof GodAIParams
  /** 归组说明 + 决策号（同组子参数随门控一起 OFF）。 */
  note: string
}

/**
 * 「被否决但保留 / 仅留档 / 实验旋钮」：参与类型、默认不参与决策（门控 = 0 →
 * byte-identical 基线）。
 *
 * un-archive 闸门（四步缺一不可，plan/God-AI-Organization.md §6 C1）：
 *   ① 改本常量 + 守卫测试断言（L1 会红——预期的显式摩擦）
 *   ② 新 DECISIONS 条目说明重开依据
 *   ③ 更新冻结签名 golden（tools/det-golden.v1.sha256）
 *   ④ 重跑 60-seed 三难度基线（eval-suite v7 官方口径）
 */
export const ARCHIVED_KNOB_GROUPS: readonly ArchivedKnobGroup[] = [
  { gate: 'candidateMode', note: 'UNIFIED_CANDIDATES + u* 子参数 (§221 reject)' },
  {
    gate: 'firingLaneMode',
    note: 'FIRING_LANE + firingLaneRadius/MinEnemyDist/ReplanTicks/BoxRow (§139 灾难性阴性)',
  },
  { gate: 'carvePathMode', note: 'CARVE_PATH + carve* 子参数 (§161 诚实阴性)' },
  { gate: 'midLaneHold', note: 'MID_LANE_HOLD + midLaneHoldMaxRow/EnemyDist (§164 灾难性阴性)' },
  {
    gate: 'suicideReturnMode',
    note: 'SUICIDE_RETURN + suicideReturnBaseHpFrac/DefendDistCells (§116/§117 阴性)',
  },
  { gate: 'defenseInterceptPredictCells', note: '方向 D 预测版 (§135, byte-identical)' },
  { gate: 'defenseInterceptDigBricks', note: '方向 D 破砖版 (§136, byte-identical)' },
  { gate: 'defensePosStandable', note: '+ minDist 子参数 (§146B 收窄版外全关, §149 边际≈0)' },
  { gate: 'iceGlideControl', note: '+ minSpeed 子参数 (§145, S24 难度地板关)' },
  { gate: 'defenseBreachBonus', note: 'D2 拆环威胁评分 (§141)' },
  { gate: 'baseLaneSentryInBandNav', note: '带内应急进 lane (§225A/§226 双否决)' },
  { gate: 'baseAlertPickupSuppress', note: '危局拾取抑制 (§225B/§226 双否决)' },
  { gate: 'baseGuardAnchorMode', note: '基地守位格 (+ baseGuardAnchorHoldRange §138 v2) (§137)' },
  { gate: 'actionContractMode', note: 'Phase2 §6.1 防守站桩门控 (§204; §220 三线微负)' },
  { gate: 'targetValueMode', note: 'Phase2 §6.2 targetValue 排序键 (§205)' },
  { gate: 'intentMode', note: 'Phase2 §6.3 短期 intent (+ intent* 子参数) (§206)' },
  {
    gate: 'coverageMode',
    note: 'Phase3 §7 动态攻击覆盖点 (+ coverage* 子参数) (§207–§211 五轮收口)',
  },
  { gate: 'fastBaseApproachWeight', note: '+ fastBaseApproachRangeCells (§132 方向 B)' },
  {
    gate: 'brickHeavyDefenseWallRatio',
    note: 'brick-heavy 防守再校准族 (+ Race/MaxPlayerDist/FieldDist) (§133 全负)',
  },
  { gate: 'evasionSteelOcclusion', note: '+ evasionSteelOcclusionRange (§48-revisit, stays OFF)' },
  { gate: 'pathThreatAvoidance', note: 'M5 站位提前规避 (~1% 触发无信号)' },
  { gate: 'dodgeCounterFire', note: 'M3 对枪抵消 (+ alignPx/clearanceScore; §90b 复核维持 OFF)' },
  {
    gate: 'dodgeHorizonScore',
    note: 'M9 horizon 承诺闪避 (+ dodgeEscapeDepth §200 / MinMarginTicks / MaxDistCells)',
  },
  { gate: 'dodgeCentroidMode', note: '§223/§224 质心远离闪避 (dodge 族第四度确认无杠杆)' },
  { gate: 'dodgeClearanceScore', note: 'dodge clearance 变体 (M3 族)' },
  { gate: 'playerHpAwareness', note: 'M12 HP 感知 (+ hpDanger*/hpTrade* 子参数)' },
  { gate: 'pickupStarBoxRow', note: 'D5 星经济拾取行' },
  { gate: 'direItemMode', note: 'E1 危急道具拾取 (+ direItem* 子参数) (§144 反证判据收束)' },
  {
    gate: 'enemyModelMode',
    note: 'M3 敌人模型族 (+ WindowTicks/tierWeightScale/dodgeRateShrinksT2a/coordinationRiskWeight/enemyAccuracyRaisesSurvival/enemyTierWeight*/survivalModeLives/survivalRiskWeight/surviveMinEnemies)',
  },
  { gate: 'fieldRetreatPickupGate', note: '§146C/§148 HIGH-only 定稿后全局关闭' },
  { gate: 'pickupCommitTicks', note: '§152 拾取承诺' },
  { gate: 'starRushMode', note: '星经济冲刺 (+ maxLevel/range/liftGates) (§166)' },
  { gate: 'superItemFrenzyAim', note: '§167 super item 连射瞄准' },
  { gate: 'threatStickyTicks', note: '§169 威胁信号粘滞' },
  { gate: 'huntCommitTicks', note: '§170 追击承诺' },
  { gate: 'pathTargetMode', note: '§171 路径长度感知目标选择' },
  { gate: 'baseDamageRecall', note: '§173 基地损伤召回' },
  { gate: 't2aOutnumberedRetreat', note: '§159 T2a 多敌撤退' },
  { gate: 'pixelStuckDirectMoveTicks', note: '§190 像素卡死 directMove 兜底 (净负, 保留为门控)' },
  {
    gate: 'dualCentralBreachP2Patrol',
    note: 'dual P2 patrol 导航 (+ PatrolRow) (§177 实测回退; 2026-08-26 盘点补登记)',
  },
  // §302 pursuitTailMode 已于 2026-08-29 启用（DECISIONS §303, AlongMode=3
  // 净 +29），移出归档组；机制与诊断字段留在测试钉内。
]
