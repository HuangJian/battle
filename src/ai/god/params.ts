// params.ts — re-export facade (plan/refactor.trae.md §3.1).
//
// The former 3,635-line monolith was three unrelated artifacts fused
// together; the CONTENT now lives in three sibling files and this module
// keeps the canonical `god/params` import path stable for every consumer:
//
//   params.interface.ts — the GodAIParams type (+218 fields of docs)
//   params.tables.ts    — DEFAULT / CLASSIC_MODEL / SKILLED_HUMAN / GUARD
//   stage-adapt.ts      — detectCentralBreachRisk / computeStageAdaptedParams
export type { GodAIParams } from './params.interface'
export {
  DEFAULT_GOD_AI_PARAMS,
  CLASSIC_MODEL_PARAMS,
  SKILLED_HUMAN_PARAMS,
  GUARD_GOD_AI_PARAMS,
} from './params.tables'
export { detectCentralBreachRisk, computeStageAdaptedParams } from './stage-adapt'
