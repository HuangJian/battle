/** Shared tick/time conversions. */
import { TICK_MS } from '../constants'

/** Float ms→ticks (no rounding, min 0). Threat-timing arithmetic uses
 *  fractional ticks intentionally. */
export const msToTicksFloat = (ms: number): number => Math.max(0, ms / TICK_MS)
