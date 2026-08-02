import type { ThemeColors, ThemeDefinition } from '../types'

/**
 * Classic NES-inspired theme — enhanced with modern UI colors.
 */
export const CLASSIC_THEME: ThemeColors = {
  // ── NES-authentic Battle City (FC 1985) palette ──────────────
  // Background: pure black, matching the original playfield.
  bg: '#000000',
  // Terrain — NES PPU-inspired warm tones.
  brick: '#c84830', // warm orange-brown (NES brick)
  brickDark: '#782818', // mortar / shadow
  steel: '#bcbcbc', // NES light gray
  steelDark: '#7c7c7c',
  water: '#3cbcfc', // NES bright blue
  waterDark: '#0058f8', // NES dark blue (wave lines)
  forest: '#00a800', // NES green
  forestDark: '#005800',
  ice: '#b8f8f8', // NES ice cyan-white
  base: '#f8d878', // NES gold (eagle / base crystal)
  baseDark: '#b89020',

  // Tanks — each enemy kind has a distinct NES hue.
  playerBody: '#d8a000', // warm gold (1P body, level 0)
  playerTurret: '#f0e8c0', // lighter cream turret for clear body/turret distinction
  playerBody2: '#d8a000', // level 2+ same gold
  playerBody3: '#f0e8c0', // level 3 brightens slightly

  enemyBasic: '#bcbcbc', // NES gray (the grunts)
  enemyFast: '#00e8d8', // NES cyan (speed demon)
  enemyPower: '#d800cc', // NES magenta (heavy hitter)
  enemyArmor: '#a8a800', // NES olive-green (armored tank)
  enemyArmorFlash: '#fcfcfc', // white flash on hit

  // HUD — dark, unobtrusive, matching the black field.
  hudBg: '#000000',
  hudText: '#e0e0e0',
  hudAccent: '#f8d878', // gold eagle color carried to HUD

  // Effects — NES fire/explosion palette.
  explosion1: '#f8d878', // bright core (gold)
  explosion2: '#e07020', // mid ring (orange)
  explosion3: '#a01010', // outer ring (dark red)
  bullet: '#fcfcfc', // white bullet (NES style)
  bulletGlow: '#f8d878', // golden glow trail
  powerUp: '#e04040', // red flashing item
  powerUpGlow: '#f8d878',
  powerUpFence: '#bcbcbc',
  powerUpFenceGlow: '#e0e0e0',
  powerUpBoat: '#3cbcfc',
  powerUpBoatGlow: '#80d8ff',
  spawn: '#fcfcfc', // white spawn flash

  // HTML UI — high-contrast light-on-dark, NES terminal aesthetic.
  panelBg: 'rgba(0, 0, 0, 0.88)',
  panelBorder: 'rgba(248, 216, 120, 0.3)',
  panelShadow: 'rgba(0, 0, 0, 0.7)',
  textPrimary: '#f0f0f0',
  textSecondary: '#dcdcdc', // bright enough to read on 62%-black overlay
  textMuted: '#b0b0b0', // lifted from #7c7c7c — hint/label text must be legible
  accentPrimary: '#f8d878', // gold — the signature NES hue
  accentSecondary: '#e07020', // warm orange accent
  buttonBg: 'rgba(248, 216, 120, 0.08)',
  buttonHover: 'rgba(248, 216, 120, 0.18)',
  buttonActive: 'rgba(248, 216, 120, 0.28)',
  // Semi-transparent overlay: dark enough for text readability,
  // transparent enough for the map to show through on the menu.
  overlayBg: 'rgba(0, 0, 0, 0.62)',
  danger: '#e04040',
  success: '#40c060',

  // Ambient — subtle dark vignette, NES-like.
  vignetteColor: 'rgba(0, 0, 0, 0.45)',
}

/**
 * Neon cyberpunk theme — vibrant glowing colors on dark background.
 */
export const NEON_THEME: ThemeColors = {
  // ── Neon cyberpunk — vibrant glowing colors on deep dark background ──
  bg: '#0a0014',
  // Terrain — saturated neon hues.
  brick: '#ff006e', // hot pink brick
  brickDark: '#8a0040',
  steel: '#4a4a6c', // slightly brighter steel for visibility
  steelDark: '#2e2e48',
  water: '#00f0ff', // electric cyan
  waterDark: '#0088aa',
  forest: '#39ff14', // laser green
  forestDark: '#1a8a08',
  ice: '#b8ffff', // pale cyan
  base: '#ffea00', // bright neon yellow
  baseDark: '#aa9800',

  // Tanks — each kind gets a unique neon color.
  playerBody: '#00e8ff', // electric cyan (player)
  playerTurret: '#ffffff',
  playerBody2: '#00e8ff',
  playerBody3: '#ffffff',

  enemyBasic: '#ff00ff', // magenta
  enemyFast: '#00ff88', // neon green
  enemyPower: '#ff6600', // neon orange
  enemyArmor: '#ffee00', // bright yellow
  enemyArmorFlash: '#ffffff',

  // HUD — deep purple-black, matching the void.
  hudBg: '#0a0014',
  hudText: '#d8d8ff', // light lavender for readability
  hudAccent: '#00f0ff',

  // Effects — neon fire palette.
  explosion1: '#ffee00', // yellow core
  explosion2: '#ff00ff', // magenta mid
  explosion3: '#ff0066', // hot pink outer
  bullet: '#ffffff',
  bulletGlow: '#00f0ff',
  powerUp: '#ff00ff',
  powerUpGlow: '#00f0ff',
  powerUpFence: '#4a4a6c',
  powerUpFenceGlow: '#6a6a9c',
  powerUpBoat: '#00f0ff',
  powerUpBoatGlow: '#80ffff',
  spawn: '#00f0ff',

  // HTML UI — deep dark panels with cyan accents.
  panelBg: 'rgba(10, 0, 24, 0.92)',
  panelBorder: 'rgba(0, 240, 255, 0.35)', // brighter border for clarity
  panelShadow: 'rgba(0, 0, 0, 0.8)',
  textPrimary: '#e8e8ff', // near-white lavender
  textSecondary: '#b0b0d8', // brighter lavender for menu readability
  textMuted: '#8888aa', // lifted from #606088 — hint text must be legible on dark overlay
  accentPrimary: '#00f0ff', // cyan accent
  accentSecondary: '#ff00ff', // magenta secondary
  buttonBg: 'rgba(0, 240, 255, 0.08)',
  buttonHover: 'rgba(0, 240, 255, 0.2)',
  buttonActive: 'rgba(0, 240, 255, 0.32)',
  overlayBg: 'rgba(6, 0, 16, 0.75)', // slightly less opaque for map visibility
  danger: '#ff0066',
  success: '#39ff14',

  // Ambient — deep vignette for the void feel.
  vignetteColor: 'rgba(0, 0, 0, 0.5)',
}

/**
 * Modern Retro theme — the cream canvas from the redesign.
 * Warm, light, playful; matches the Ardot "Modern Presentation" art direction.
 */
export const MODERN_RETRO_THEME: ThemeColors = {
  // ── Modern Retro — warm cream canvas, playful & light ──────────
  bg: '#fbe6c4',
  bgGradient: ['#FFF7E6', '#FCE3B4'],
  // Terrain — muted, earthy tones that complement the cream.
  brick: '#c87a42', // slightly deeper orange for better contrast on cream
  brickDark: '#905028',
  steel: '#b8b8b8', // cooler gray to distinguish from warm cream
  steelDark: '#8a8a8a',
  water: '#4aa8dc', // friendly blue
  waterDark: '#2880b0',
  forest: '#50b860', // fresh green
  forestDark: '#288038',
  ice: '#a0d8f0', // soft ice blue
  base: '#f0b828', // warm amber-gold
  baseDark: '#c08818',

  // Tanks — bold, distinguishable colors on cream.
  playerBody: '#f0b828', // warm amber-gold
  playerTurret: '#fff8ee', // near-white cream
  playerBody2: '#f0b828',
  playerBody3: '#fff8ee',

  enemyBasic: '#d83828', // warm red
  enemyFast: '#18b8d0', // teal
  enemyPower: '#7c4ce0', // violet
  enemyArmor: '#687888', // slate gray
  enemyArmorFlash: '#ffffff',

  // HUD — cream-tinted glass panel.
  hudBg: 'rgba(255, 247, 230, 0.94)',
  hudText: '#282018', // near-black warm brown
  hudAccent: '#d06848', // terracotta accent

  // Effects — warm fire palette.
  explosion1: '#f8c830',
  explosion2: '#e86830',
  explosion3: '#c83820',
  bullet: '#282018', // dark bullet (reads on cream)
  bulletGlow: '#f0b828',
  powerUp: '#d06848',
  powerUpGlow: '#f0b828',
  powerUpFence: '#b8b8b8',
  powerUpFenceGlow: '#f0b828',
  powerUpBoat: '#4aa8dc',
  powerUpBoatGlow: '#f0b828',
  spawn: '#f0b828',

  // HTML UI — warm, light panels with terracotta accents.
  panelBg: 'rgba(255, 248, 232, 0.94)',
  panelBorder: 'rgba(208, 104, 72, 0.35)', // stronger border for definition
  panelShadow: 'rgba(0, 0, 0, 0.12)',
  textPrimary: '#282018', // warm near-black
  textSecondary: '#605040', // warm mid-brown (for cream canvas)
  textMuted: '#988068', // muted tan (for cream canvas)
  accentPrimary: '#f0b828', // amber-gold
  accentSecondary: '#d06848', // terracotta
  buttonBg: 'rgba(208, 104, 72, 0.08)',
  buttonHover: 'rgba(208, 104, 72, 0.18)',
  buttonActive: 'rgba(208, 104, 72, 0.28)',
  // Semi-transparent dark overlay on cream — map shows through,
  // menu text stays sharp with dark-on-dark.
  overlayBg: 'rgba(40, 32, 24, 0.55)',
  danger: '#c83820',
  success: '#38a050',

  // Ambient — very subtle, light feel.
  vignetteColor: 'rgba(40, 32, 24, 0.06)',
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

/** All theme keys, in registration order (menu cycling / selection). */
export const THEME_KEYS = Object.keys(THEMES)

export const DEFAULT_THEME = 'modern'
