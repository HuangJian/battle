/**
 * all-on-experiment.ts — §GOD-AI-all-strategies-CMA-ES.md M0 实验 profiles。
 *
 * 纯数据 profile：只在 `DEFAULT_GOD_AI_PARAMS` 之上翻转 manifest 中列出的关闭
 * 开关，永不修改任何 shipped 默认值。paramsHash（tools/lib/stage-spec.ts 的
 * key-sorted FNV-1a）在运行日志/artifact 中标识每个 profile。
 *
 * Manifest（§2.1 激活规则逐项核实过代码路径）：
 *   - 二进制开关 0→1：均为主闸门（> 0 触发对应分支），1 是合法值。
 *   - 离散多值开关钉值：defenseInterceptPredictCells=2（§135 候选 1/2/3 取中）、
 *     suicideReturnMode=1（§116 原版，最保守——仅在必死时交易）。
 *   - 依赖参数最小激活：
 *       survivalModeLives=1 单独是 no-op —— think.ts 的 survival retreat 门控
 *         `survivalRiskWeight > 0`，故设 survivalRiskWeight=1（纯门控，1=最小）。
 *       playerHpAwareness=1 的 danger 分支要求 hpDangerHits > 0，取 2（§111 口径
 *         危险区 = hits-to-die ≤ 2）；hpDangerCommitMargin 保持默认 0（无 A/B
 *         记录不发明值）→ margin 部分 inert，如实记录，M2 数值搜索是激活路径。
 *   - 其余依赖参数保持当前默认（intent/coverage lease=12、replan=12、
 *     baseGuardAnchorHoldRange=6、firingLane 半径族、dodge 各权重）。
 *   - contest 既有 DECISIONS：coverageMode（§204–211 四轮净负保持 OFF）、
 *     actionContractMode/targetValueMode/intentMode（§207 Phase 2 三机制 OFF）
 *     —— 本实验检验「独立否决、协同复活」假设。
 */
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from './params'

/** all-on：current shipped default + manifest 关闭开关全部打开（M5 保持 ON）。 */
export const ALL_ON_EXPERIMENT_PARAMS: GodAIParams = {
  ...DEFAULT_GOD_AI_PARAMS,
  // ---- 二进制开关 0→1 ----
  baseLaneSentryInBandNav: 1,
  baseAlertPickupSuppress: 1,
  baseGuardAnchorMode: 1,
  actionContractMode: 1,
  targetValueMode: 1,
  intentMode: 1,
  coverageMode: 1,
  candidateMode: 1,
  firingLaneMode: 1,
  defenseInterceptDigBricks: 1,
  dodgeCentroidMode: 1,
  pathThreatAvoidance: 1,
  dodgeCounterFire: 1,
  dodgeClearanceScore: 1,
  dodgeHorizonScore: 1,
  baseDamageRecall: 1,
  pathTargetMode: 1,
  // ---- 离散多值开关（钉值） ----
  defenseInterceptPredictCells: 2,
  suicideReturnMode: 1,
  // ---- 依赖参数最小激活 ----
  survivalModeLives: 1,
  survivalRiskWeight: 1,
  playerHpAwareness: 1,
  hpDangerHits: 2,
  // hpDangerCommitMargin 保持默认 0 → margin 部分 inert（见文件头 manifest）。
}

/** all-on 关闭 M5：M0 的第三个 artifact，隔离 fireLineDetourMode 在 all-on
 *  语境下的贡献（default 已含 M5，故「只开 M5」无信息量，必须用 all-on−M5）。 */
export const ALL_ON_M5_OFF_CONTROL_PARAMS: GodAIParams = {
  ...ALL_ON_EXPERIMENT_PARAMS,
  fireLineDetourMode: 0,
}

/** all-on 关闭 firingLaneMode：§277 M1 LOO 定位的头号冲突（all-on 中关掉
 *  +416/2100 @60-seed，+19.8pp）。用户拍板：以此为基础 profile 跑 CMA-ES
 *  （M2 search 的 base 不再是纯 default 而是 all-on−flm）。 */
export const ALL_ON_MINUS_FLM_PARAMS: GodAIParams = {
  ...ALL_ON_EXPERIMENT_PARAMS,
  firingLaneMode: 0,
}
