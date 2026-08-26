/**
 * intent/tagger.ts — M1 God-AI 意图 tagger 接地钩子（plan/Intent-Policy-NN-Plan.md
 * §5.1，M0b 探针之后启用；本模块为 M1 的落点，先以 intentTaggerMode 门控落地）。
 *
 * 采样口径（与探针机械 tagger 同源，禁两份）：
 *   - 细分支 → 意图经 vocab.forwardMapLabel；战斗族分支经 vocab.classifyCombatIntent
 *     （combat 原语在此从 GodAIInput 现算）；
 *   - 采样网格 = 均匀 replan（INTENT_REPLAN_TICKS，与探针 gridPeriod 同值）；
 *   - 注入特征：prev-intent（上一采样意图）+ duration（当前意图持续 tick）。
 *
 * 纪律：零 world.rng 消费、零决策路径改动、默认 OFF（intentTaggerMode=0 字节等价）；
 * 状态只存在 GodAIInput 实例（InputLike 先例），reset() 清空，不进 World。
 * tagger ON/OFF 同 seed 逐字节一致由 tests/intent-tagger-hook.test.ts 断言。
 */
import { type GodAIInput } from '../GodAIInput'
import { BASE_POS } from '../../constants'
import {
  classifyCombatIntent,
  forwardMapLabel,
  enemySlotOf,
  type CombatChainInput,
  type IntentId,
} from './vocab'

/** 均匀 replan 采样周期（预注册 #1 初值；最终值由 M7① replan 扫描定）。 */
export const INTENT_REPLAN_TICKS = 30

export interface IntentLogSample {
  /** world.frame 采样点（replan 网格帧）。 */
  tick: number
  intent: IntentId
  /** 目标敌槽：本 tick 提交目标的槽位；无目标/非敌头意图 = 0（none）。 */
  targetEnemySlot: number
  /** 上一采样意图；首个样本 = null。 */
  prevIntent: IntentId | null
  /** 当前意图已持续 tick 数（去抖后），饱和到 65535。 */
  duration: number
}

/** 日志上限（环形覆盖最旧）——Input 实例内存有界（AGENTS §9 内存纪律）。 */
export const INTENT_LOG_CAP = 16_384

/** GodAIInput → 战斗链谓词原语（vocab 不依赖 GodAIInput，适配层在此收敛）。 */
export function combatChainFromGod(input: GodAIInput): CombatChainInput | null {
  const w = input.world
  const m = forwardMapLabel(input._lastBranch)
  if (m.kind !== 'combat-chain') return null
  const pc = input.playerCell()
  return {
    isBaseUnderThreat: input.hasBase && input.isBaseUnderThreat(),
    playerDistToBase: Math.abs(pc.col - BASE_POS.col) + Math.abs(pc.row - BASE_POS.row),
    maxPlayerDistFromBase: input.params.maxPlayerDistFromBase,
    isEndgame: w.enemiesRemaining <= input.params.endgameEnemyThreshold,
  }
}

/**
 * 当前 tick 的意图定性（与探针机械 tagger/branch-map 同口径）。
 * 返回 null = 非意图帧（shell/reflex/战斗链但 combat 原语缺失——正常运行时
 * combatChainFromGod 覆盖战斗链，null 仅发生在 reset 边界）。
 * 未知细分支：vocab 契约要求显式 throw（开发期抓漏挂靠），但运行时观测必须
 * 降级为"跳过本样本"而非让游戏循环崩溃（M1 语义：tagger 是纯观测，绝不阻断
 * 决策链）——未知标签计入 _intentUnknownLabels。
 */
export function currentIntent(input: GodAIInput): IntentId | null {
  let m: ReturnType<typeof forwardMapLabel>
  try {
    m = forwardMapLabel(input._lastBranch)
  } catch {
    input._intentUnknownLabels++
    return null
  }
  if (m.kind === 'static') return m.intent
  if (m.kind === 'combat-chain') {
    const combat = combatChainFromGod(input)
    if (combat) return classifyCombatIntent(combat)
  }
  return null
}

/** 采样一个 replan 网格帧；非网格帧直接返回（不污染 prev/duration）。 */
export function collectIntentSample(input: GodAIInput, tick: number): void {
  if (tick % INTENT_REPLAN_TICKS !== 0) return
  const intent = currentIntent(input)
  if (intent === null) return // 边界帧（reset 交界）：跳过，prev/duration 保持

  const prev = input._intentPrev
  const duration = intent === prev ? input._intentDuration + 1 : 1
  input._intentDuration = duration
  input._intentPrev = intent

  // 每样本只需一次 enemySlotOf（单调函数，含排序）——非热路径（30 tick 一次）。
  let slot = 0
  if (input._lastSelectTargetId >= 0) slot = enemySlotOf(input.world, input._lastSelectTargetId)

  input._intentLog.push({
    tick,
    intent,
    targetEnemySlot: slot,
    prevIntent: prev,
    duration: Math.min(duration, 65_535),
  })
  if (input._intentLog.length > INTENT_LOG_CAP) input._intentLog.shift()
}

/** 重置 tagger 内部态（reset()/关卡切换调用）。 */
export function resetIntentTagger(input: GodAIInput): void {
  input._intentPrev = null
  input._intentDuration = 0
  input._intentUnknownLabels = 0
  input._intentLog.length = 0
}
