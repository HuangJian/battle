/**
 * ThreatBudget.ts — compatibility re-export.
 *
 * The threat-slack module now lives in `src/ai/threat-budget.ts` so the NN
 * layer can import it without reaching into `src/ai/god`.
 */
export * from '../threat-budget'
