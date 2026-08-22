// ================================================================
// pathfind.ts — compatibility re-exports.
//
// The module was split in two (plan/refactor.agy.md §2.7):
//   • offline connectivity helpers → utils/grid-search.ts
//   • God-AI A* navigation engine  → ai/god/pathfind.ts
// This shim keeps existing import paths alive — notably protected files
// (ai/god/think.ts, AGENTS §5.1) that must not be edited. New code:
// import from the split modules directly.
// ================================================================
export type { Cell } from './grid-search'
export { pxToCell, isReachable, floodFill } from './grid-search'
export { findPath, fireClearStopTicks } from '../ai/god/pathfind'
export type { PathConstraints } from '../ai/god/pathfind'
