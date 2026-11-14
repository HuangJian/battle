/**
 * pathfind.ts — compatibility re-export.
 *
 * The A* engine now lives in `src/utils/pathfind.ts` so the NN layer can use
 * it without importing `src/ai/god`. Existing God-AI call sites keep this path.
 */
export * from '../../utils/pathfind'
