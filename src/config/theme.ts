import type { ThemeColors, ThemeDefinition } from '../types'

/**
 * Classic NES-inspired theme — enhanced with modern UI colors.
 */
export const CLASSIC_THEME: ThemeColors = {
  bg: '#0d0d0d',
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
  bulletGlow: '#ffe060',
  powerUp: '#e84040',
  powerUpGlow: '#ffe040',
  spawn: '#f0f0f0',

  // HTML UI
  panelBg: 'rgba(20, 20, 24, 0.92)',
  panelBorder: 'rgba(232, 200, 64, 0.25)',
  panelShadow: 'rgba(0, 0, 0, 0.6)',
  textPrimary: '#f0f0f0',
  textSecondary: '#b0b0b0',
  textMuted: '#707070',
  accentPrimary: '#e8c840',
  accentSecondary: '#ff8040',
  buttonBg: 'rgba(232, 200, 64, 0.1)',
  buttonHover: 'rgba(232, 200, 64, 0.2)',
  buttonActive: 'rgba(232, 200, 64, 0.3)',
  overlayBg: 'rgba(0, 0, 0, 0.7)',
  danger: '#ff4040',
  success: '#40e060',

  // Ambient
  gridLineColor: 'rgba(255, 255, 255, 0.03)',
  vignetteColor: 'rgba(0, 0, 0, 0.4)',
}

/**
 * Neon cyberpunk theme — vibrant glowing colors on dark background.
 */
export const NEON_THEME: ThemeColors = {
  bg: '#0a0014',
  brick: '#ff006e',
  brickDark: '#8a0040',
  steel: '#3a3a5c',
  steelDark: '#2a2a3c',
  water: '#00f0ff',
  waterDark: '#0080a0',
  forest: '#39ff14',
  forestDark: '#1a8a08',
  ice: '#b0ffff',
  base: '#ffea00',
  baseDark: '#8a8000',

  playerBody: '#00ffff',
  playerTurret: '#ffffff',
  playerBody2: '#00ffff',
  playerBody3: '#ffffff',

  enemyBasic: '#ff00ff',
  enemyFast: '#00ff88',
  enemyPower: '#ff6600',
  enemyArmor: '#ffff00',
  enemyArmorFlash: '#ffffff',

  hudBg: '#0d001a',
  hudText: '#e0e0ff',
  hudAccent: '#00ffff',

  explosion1: '#ffff00',
  explosion2: '#ff00ff',
  explosion3: '#ff0066',
  bullet: '#ffffff',
  bulletGlow: '#00ffff',
  powerUp: '#ff00ff',
  powerUpGlow: '#00ffff',
  spawn: '#00ffff',

  // HTML UI
  panelBg: 'rgba(10, 0, 20, 0.92)',
  panelBorder: 'rgba(0, 255, 255, 0.3)',
  panelShadow: 'rgba(0, 0, 0, 0.8)',
  textPrimary: '#e0e0ff',
  textSecondary: '#8080c0',
  textMuted: '#505070',
  accentPrimary: '#00ffff',
  accentSecondary: '#ff00ff',
  buttonBg: 'rgba(0, 255, 255, 0.08)',
  buttonHover: 'rgba(0, 255, 255, 0.18)',
  buttonActive: 'rgba(0, 255, 255, 0.28)',
  overlayBg: 'rgba(0, 0, 0, 0.8)',
  danger: '#ff0066',
  success: '#39ff14',

  // Ambient
  gridLineColor: 'rgba(0, 255, 255, 0.04)',
  vignetteColor: 'rgba(0, 0, 0, 0.5)',
}

/**
 * Modern Retro theme — the cream canvas from the redesign.
 * Warm, light, playful; matches the Ardot "Modern Presentation" art direction.
 */
export const MODERN_RETRO_THEME: ThemeColors = {
  bg: '#fbe6c4', // mid-cream, used for spawn-flash masking
  bgGradient: ['#FFF7E6', '#FCE3B4'], // warm cream → deeper cream (vertical)
  brick: '#d98a4f',
  brickDark: '#a85f2c',
  steel: '#c9c9c9',
  steelDark: '#9a9a9a',
  water: '#5bb8e8',
  waterDark: '#2f8fc4',
  forest: '#5bbf6a',
  forestDark: '#2f8f43',
  ice: '#a9e0f5',
  base: '#f4c430',
  baseDark: '#c8941f',

  playerBody: '#f4c430',
  playerTurret: '#fff7e6',
  playerBody2: '#f4c430',
  playerBody3: '#fff7e6',

  enemyBasic: '#e23b2c',
  enemyFast: '#22c3dc',
  enemyPower: '#8b5cf6',
  enemyArmor: '#7a8290',
  enemyArmorFlash: '#ffffff',

  hudBg: 'rgba(255, 247, 230, 0.92)',
  hudText: '#2b2b2b',
  hudAccent: '#e07a5f',

  explosion1: '#ffd23f',
  explosion2: '#ff7a3c',
  explosion3: '#e0432b',
  bullet: '#2b2b2b',
  bulletGlow: '#f4c430',
  powerUp: '#e07a5f',
  powerUpGlow: '#f4c430',
  spawn: '#f4c430',

  // HTML UI
  panelBg: 'rgba(255, 247, 230, 0.92)',
  panelBorder: 'rgba(224, 122, 95, 0.3)',
  panelShadow: 'rgba(0, 0, 0, 0.15)',
  textPrimary: '#2b2b2b',
  textSecondary: '#6b5848',
  textMuted: '#a08a72',
  accentPrimary: '#f4c430',
  accentSecondary: '#e07a5f',
  buttonBg: 'rgba(224, 122, 95, 0.1)',
  buttonHover: 'rgba(224, 122, 95, 0.18)',
  buttonActive: 'rgba(224, 122, 95, 0.28)',
  overlayBg: 'rgba(43, 43, 43, 0.5)',
  danger: '#e0432b',
  success: '#3fae5a',

  // Ambient
  gridLineColor: 'rgba(43, 43, 43, 0.05)',
  vignetteColor: 'rgba(43, 43, 43, 0.06)',
}

export const THEME_DEFINITIONS: ThemeDefinition[] = [
  {
    key: 'classic',
    name: 'Classic',
    description: 'Faithful NES-inspired colors',
    colors: CLASSIC_THEME,
  },
  {
    key: 'neon',
    name: 'Neon',
    description: 'Vibrant cyberpunk glow',
    colors: NEON_THEME,
  },
  {
    key: 'modern',
    name: 'Modern Retro',
    description: 'Warm cream canvas, faithful to the redesign',
    colors: MODERN_RETRO_THEME,
  },
]

export const THEMES: Record<string, ThemeColors> = {
  classic: CLASSIC_THEME,
  neon: NEON_THEME,
  modern: MODERN_RETRO_THEME,
}

export const DEFAULT_THEME = 'modern'
