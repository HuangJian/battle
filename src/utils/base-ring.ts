/**
 * base-ring.ts — canonical 8-cell base protection ring.
 *
 * Shared by God-AI threat math and the NN-side policy/opening layer. Keep this
 * module pure (BASE_POS only) so neither side has to import the other's
 * engine internals.
 */
import { BASE_POS } from '../constants'

/** The 8 cells of the base protection ring — mirror of isBaseProtectionCell. */
export const RING_CELLS: ReadonlyArray<{ col: number; row: number }> = (() => {
  const cells: Array<{ col: number; row: number }> = []
  const bc = BASE_POS.col
  const br = BASE_POS.row
  for (let col = bc - 1; col <= bc + 2; col++) cells.push({ col, row: br - 1 })
  for (let row = br; row <= br + 1; row++) {
    cells.push({ col: bc - 1, row })
    cells.push({ col: bc + 2, row })
  }
  return cells
})()

/** Membership test for the 8-cell base protection ring. */
export function isBaseRingCell(col: number, row: number): boolean {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  if (row === br - 1 && col >= bc - 1 && col <= bc + 2) return true
  if ((col === bc - 1 || col === bc + 2) && (row === br || row === br + 1)) return true
  return false
}
