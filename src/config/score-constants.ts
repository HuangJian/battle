// Leaf module: plain score constants needed by BOTH src/config/score.ts and
// src/config/rules.ts. It intentionally imports nothing from either module so
// it breaks the score<->rules circular dependency, which under certain module
// load orders (e.g. `bun test --parallel`) produced a TDZ:
//   ReferenceError: Cannot access 'SCORE_DROP_INTERVAL' before initialization
// (rules.ts's top-level `dropOnScoreMilestone: SCORE_DROP_INTERVAL` ran while
// score.ts had not yet initialized the binding). Keep this file dependency-free.

/** Points granted per power-up collected. */
export const ITEM_SCORE = 100

/**
 * Score milestone that guarantees a power-up drop. Every time the player's
 * accumulated score crosses a multiple of this value (5000), one power-up is
 * dropped. A single large score gain can cross several milestones at once and
 * therefore drop several power-ups.
 */
export const SCORE_DROP_INTERVAL = 5000
