import type { ThemeColors } from '../types'

/**
 * Classic NES-inspired theme.
 * Future themes = add new entries to a theme registry.
 */
export const CLASSIC_THEME: ThemeColors = {
  bg: '#000000',
  brick: '#b85c28',
  brickDark: '#7a3c18',
  steel: '#b0b0b0',
  steelDark: '#707070',
  water: '#2038d8',
  waterDark: '#1020a0',
  forest: '#00a000',
  forestDark: '#006000',
  ice: '#80d0ff',
  base: '#e8c840',
  baseDark: '#a88820',

  playerBody: '#e8c840',
  playerTurret: '#f8f8f8',
  playerBody2: '#e8c840',
  playerBody3: '#f0f0f0',

  enemyBasic: '#b0b0b0',
  enemyFast: '#50d0d0',
  enemyPower: '#d080d0',
  enemyArmor: '#d0d050',
  enemyArmorFlash: '#f0f0f0',

  hudBg: '#1a1a1a',
  hudText: '#e0e0e0',
  hudAccent: '#e8c840',

  explosion1: '#ffe040',
  explosion2: '#ff8020',
  explosion3: '#c04020',

  bullet: '#f8f8f8',
  powerUp: '#e84040',
  powerUpGlow: '#ffe040',
  spawn: '#f0f0f0',
}

export const THEMES: Record<string, ThemeColors> = {
  classic: CLASSIC_THEME,
}

export const DEFAULT_THEME = 'classic'
