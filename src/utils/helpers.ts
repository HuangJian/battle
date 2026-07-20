import type { Direction } from '../constants'
import { DIR_VECTORS } from '../constants'

/** Clamp value to range */
export function clamp(v: number, min: number, max: number): number {
  return v < min ? min : v > max ? max : v
}

/** Snap value to nearest multiple of `grid` */
export function snap(v: number, grid: number): number {
  return Math.round(v / grid) * grid
}

/** AABB overlap test */
export function aabb(
  ax: number,
  ay: number,
  aw: number,
  ah: number,
  bx: number,
  by: number,
  bw: number,
  bh: number,
): boolean {
  return ax < bx + bw && ax + aw > bx && ay < by + bh && ay + ah > by
}

/** Get opposite direction */
export function opposite(dir: Direction): Direction {
  switch (dir) {
    case 'up':
      return 'down'
    case 'down':
      return 'up'
    case 'left':
      return 'right'
    case 'right':
      return 'left'
  }
}

/** Turn direction 90° clockwise */
export function turnCW(dir: Direction): Direction {
  switch (dir) {
    case 'up':
      return 'right'
    case 'right':
      return 'down'
    case 'down':
      return 'left'
    case 'left':
      return 'up'
  }
}

/** Turn direction 90° counter-clockwise */
export function turnCCW(dir: Direction): Direction {
  switch (dir) {
    case 'up':
      return 'left'
    case 'left':
      return 'down'
    case 'down':
      return 'right'
    case 'right':
      return 'up'
  }
}

/** Move a position by direction vector × distance */
export function moveDir(
  x: number,
  y: number,
  dir: Direction,
  dist: number,
): { x: number; y: number } {
  const v = DIR_VECTORS[dir]
  return { x: x + v.dx * dist, y: y + v.dy * dist }
}

/** All four directions in order */
export const ALL_DIRS: Direction[] = ['up', 'down', 'left', 'right']

/** Random integer in [min, max] inclusive */
export function randInt(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}
