import type { EmitterConfig } from '../types'

/**
 * Particle emitter 手感参数 (feel configs) — plan/refactor.trae.md §2.5.
 *
 * "Data over code" (AGENTS §2.4): these five factories are pure parameter
 * packs for the particle system. They used to be private methods inside
 * PresentationLayer — an agent tuning "爆炸手感" had to open the render
 * orchestration file to find them. Now they live next to the other config.
 *
 * All functions are pure: same args → identical EmitterConfig. Coordinates
 * and counts are passed in by the presentation layer; everything else here
 * IS the feel.
 */

/** Hit / small-blast sparks. `speed*0.5` halves the caller's speed range. */
export function makeSparkEmitter(
  x: number,
  y: number,
  count: number,
  speedMin: number,
  speedMax: number,
): EmitterConfig {
  return {
    x,
    y,
    count,
    speedMin: speedMin * 0.5,
    speedMax: speedMax * 0.5,
    lifeMin: 200,
    lifeMax: 500,
    sizeMin: 1,
    sizeMax: 3,
    colors: ['#ffe040', '#ff8020', '#ff4020', '#ffffff'],
    type: 'spark',
    gravity: 0.05,
    drag: 0.92,
    angleMin: 0,
    angleMax: Math.PI * 2,
    spread: 4,
  }
}

/** Grey rubble chips thrown by terrain destruction. */
export function makeDebrisEmitter(x: number, y: number, count: number): EmitterConfig {
  return {
    x,
    y,
    count,
    speedMin: 1,
    speedMax: 3,
    lifeMin: 400,
    lifeMax: 800,
    sizeMin: 2,
    sizeMax: 4,
    colors: ['#808080', '#606060', '#a0a0a0', '#404040'],
    type: 'debris',
    gravity: 0.15,
    drag: 0.95,
    angleMin: 0,
    angleMax: Math.PI * 2,
    spread: 8,
  }
}

/** Slow-rising smoke puffs (negative gravity = buoyant). */
export function makeSmokeEmitter(x: number, y: number, count: number): EmitterConfig {
  return {
    x,
    y,
    count,
    speedMin: 0.2,
    speedMax: 0.8,
    lifeMin: 600,
    lifeMax: 1000,
    sizeMin: 4,
    sizeMax: 8,
    colors: ['#606060', '#404040', '#808080'],
    type: 'smoke',
    gravity: -0.02,
    drag: 0.96,
    angleMin: -Math.PI / 2 - 0.5,
    angleMax: -Math.PI / 2 + 0.5,
    spread: 6,
  }
}

/** Single-frame white impact flash. */
export function makeFlashEmitter(x: number, y: number): EmitterConfig {
  return {
    x,
    y,
    count: 1,
    speedMin: 0,
    speedMax: 0,
    lifeMin: 150,
    lifeMax: 150,
    sizeMin: 20,
    sizeMax: 20,
    colors: ['#ffffff'],
    type: 'flash',
    gravity: 0,
    drag: 1,
    angleMin: 0,
    angleMax: 0,
    spread: 0,
  }
}

/** Expanding golden shock ring for tank kills. */
export function makeRingEmitter(x: number, y: number): EmitterConfig {
  return {
    x,
    y,
    count: 1,
    speedMin: 0,
    speedMax: 0,
    lifeMin: 300,
    lifeMax: 300,
    sizeMin: 8,
    sizeMax: 8,
    colors: ['#ffe040'],
    type: 'ring',
    gravity: 0,
    drag: 1,
    angleMin: 0,
    angleMax: 0,
    spread: 0,
  }
}
