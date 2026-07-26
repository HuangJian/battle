import { describe, it, expect } from 'bun:test'
import { getHpLevel, HP_LEVEL_CONFIGS } from '../src/config/hp-level'

describe('HP Level Configuration & Calculation', () => {
  it('correctly maps HP values to HP levels (1~6)', () => {
    // Level 1: HP <= 100
    expect(getHpLevel(0)).toBe(1)
    expect(getHpLevel(50)).toBe(1)
    expect(getHpLevel(100)).toBe(1)

    // Level 2: 100 < HP <= 200
    expect(getHpLevel(101)).toBe(2)
    expect(getHpLevel(150)).toBe(2)
    expect(getHpLevel(200)).toBe(2)

    // Level 3: 200 < HP <= 300
    expect(getHpLevel(201)).toBe(3)
    expect(getHpLevel(250)).toBe(3)
    expect(getHpLevel(300)).toBe(3)

    // Level 4: 300 < HP <= 400
    expect(getHpLevel(301)).toBe(4)
    expect(getHpLevel(350)).toBe(4)
    expect(getHpLevel(400)).toBe(4)

    // Level 5: 400 < HP <= 500
    expect(getHpLevel(401)).toBe(5)
    expect(getHpLevel(450)).toBe(5)
    expect(getHpLevel(500)).toBe(5)

    // Level 6: 500 < HP <= 600 (Elite Heavy / Boss tier)
    expect(getHpLevel(501)).toBe(6)
    expect(getHpLevel(550)).toBe(6)
    expect(getHpLevel(600)).toBe(6)

    // Clamped at 6 for HP > 600
    expect(getHpLevel(700)).toBe(6)
  })

  it('has valid visual configurations for levels 1 to 6', () => {
    expect(HP_LEVEL_CONFIGS[1].shape).toBe('none')
    expect(HP_LEVEL_CONFIGS[2].shape).toBe('square')
    expect(HP_LEVEL_CONFIGS[3].shape).toBe('double-square')
    expect(HP_LEVEL_CONFIGS[4].shape).toBe('jagged-square')
    expect(HP_LEVEL_CONFIGS[5].shape).toBe('hexagon-square')
    expect(HP_LEVEL_CONFIGS[6].shape).toBe('solar-square')

    expect(HP_LEVEL_CONFIGS[2].color).toBe('#2ecc71')
    expect(HP_LEVEL_CONFIGS[3].color).toBe('#3498db')
    expect(HP_LEVEL_CONFIGS[4].color).toBe('#9b59b6')
    expect(HP_LEVEL_CONFIGS[5].color).toBe('#e67e22')
    expect(HP_LEVEL_CONFIGS[6].color).toBe('#e74c3c')
  })
})
