/**
 * Seeded random number generator (mulberry32).
 * Enables deterministic simulation for future replay/debugging.
 */
export class RNG {
  private state: number

  constructor(seed: number) {
    this.state = seed >>> 0
  }

  next(): number {
    this.state = (this.state + 0x6d2b79f5) >>> 0
    let t = this.state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  int(max: number): number {
    return Math.floor(this.next() * max)
  }

  pick<T>(arr: T[]): T {
    return arr[this.int(arr.length)]
  }

  reseed(seed: number): void {
    this.state = seed >>> 0
  }
}
