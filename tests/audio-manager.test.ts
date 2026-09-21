import { describe, it, expect, beforeEach, afterAll } from 'bun:test'
import {
  AudioManager,
  DEFAULT_VOLUME,
  VOLUME_MAX,
  VOLUME_MIN,
  sanitizeVolume,
} from '../src/audio/AudioManager'

// ================================================================
// A minimal Web Audio fake. The load-bearing detail is that setting a
// NON-FINITE value on an AudioParam throws — exactly like Chrome:
//   "Failed to set the 'value' property on 'AudioParam':
//    The provided float value is non-finite."
// Without that behaviour the regression these tests pin (a corrupt
// persisted volume disabling audio for the whole session) is invisible.
// ================================================================

function makeAudioParam(initial = 0): AudioParam {
  let v = initial
  const param = {
    get value(): number {
      return v
    },
    set value(next: number) {
      if (!Number.isFinite(next)) {
        throw new TypeError(
          "Failed to set the 'value' property on 'AudioParam': The provided float value is non-finite.",
        )
      }
      v = next
    },
    setValueAtTime: (next: number) => next,
    linearRampToValueAtTime: () => param,
    exponentialRampToValueAtTime: () => param,
    cancelScheduledValues: () => param,
  }
  return param as unknown as AudioParam
}

function makeNode(): { connect: () => void; disconnect: () => void } {
  return { connect: () => {}, disconnect: () => {} }
}

/** Suspended-by-default context, like a page before any user gesture. */
class FakeAudioContext {
  state: AudioContextState = 'suspended'
  currentTime = 0
  sampleRate = 48000
  destination = makeNode()
  createGain(): GainNode {
    return { ...makeNode(), gain: makeAudioParam() } as unknown as GainNode
  }
  createOscillator(): OscillatorNode {
    return {
      ...makeNode(),
      type: 'square',
      frequency: makeAudioParam(440),
      start: () => {},
      stop: () => {},
    } as unknown as OscillatorNode
  }
  createBuffer(channels: number, length: number): AudioBuffer {
    return {
      length,
      numberOfChannels: channels,
      getChannelData: () => new Float32Array(length),
    } as unknown as AudioBuffer
  }
  createBufferSource(): AudioBufferSourceNode {
    return {
      ...makeNode(),
      buffer: null,
      start: () => {},
      stop: () => {},
    } as unknown as AudioBufferSourceNode
  }
  createBiquadFilter(): BiquadFilterNode {
    return {
      ...makeNode(),
      type: 'lowpass',
      frequency: makeAudioParam(1000),
    } as unknown as BiquadFilterNode
  }
  resume(): Promise<void> {
    this.state = 'running'
    return Promise.resolve()
  }
  suspend(): Promise<void> {
    this.state = 'suspended'
    return Promise.resolve()
  }
}

function setWindow(value: unknown): void {
  ;(globalThis as unknown as { window: unknown }).window = value
}

function installFakeWebAudio(): void {
  setWindow({ AudioContext: FakeAudioContext as unknown as typeof AudioContext })
}

/** Private-field peek — the manager exposes no getters for these. */
interface AudioInternals {
  enabled: boolean
  ctx: AudioContext | null
  masterGain: GainNode | null
}
const internals = (audio: AudioManager): AudioInternals => audio as unknown as AudioInternals

afterAll(() => {
  setWindow(undefined)
})

describe('sanitizeVolume', () => {
  it('passes a finite volume through', () => {
    expect(sanitizeVolume(0.5)).toBe(0.5)
  })

  it('falls back to the default for a non-number', () => {
    expect(sanitizeVolume(null)).toBe(DEFAULT_VOLUME)
    expect(sanitizeVolume(undefined)).toBe(DEFAULT_VOLUME)
    expect(sanitizeVolume('0.8')).toBe(DEFAULT_VOLUME)
  })

  it('falls back to the default for a non-finite number', () => {
    expect(sanitizeVolume(Number.NaN)).toBe(DEFAULT_VOLUME)
    expect(sanitizeVolume(Number.POSITIVE_INFINITY)).toBe(DEFAULT_VOLUME)
    expect(sanitizeVolume(Number.NEGATIVE_INFINITY)).toBe(DEFAULT_VOLUME)
  })

  it('clamps to the gain range', () => {
    expect(sanitizeVolume(4)).toBe(VOLUME_MAX)
    expect(sanitizeVolume(-4)).toBe(VOLUME_MIN)
  })
})

describe('AudioManager — volume handling', () => {
  beforeEach(() => {
    installFakeWebAudio()
  })

  it('initialises the master gain with the default volume', () => {
    const audio = new AudioManager()
    audio.init()
    expect(internals(audio).enabled).toBe(true)
    expect(internals(audio).masterGain?.gain.value).toBe(DEFAULT_VOLUME)
  })

  it('a non-finite volume does NOT disable audio for the session', () => {
    // Regression: assigning it to the GainNode threw inside init(), the catch
    // classified it as "Web Audio unavailable", and the game stayed mute.
    const audio = new AudioManager()
    audio.setVolume(Number.NaN)
    audio.init()
    expect(internals(audio).enabled).toBe(true)
    expect(internals(audio).masterGain?.gain.value).toBe(DEFAULT_VOLUME)
    expect(() => audio.playMenuSelect()).not.toThrow()
  })

  it('an out-of-range volume is clamped, not passed on', () => {
    const audio = new AudioManager()
    audio.setVolume(9)
    audio.init()
    expect(internals(audio).masterGain?.gain.value).toBe(VOLUME_MAX)
    audio.setVolume(-9)
    expect(internals(audio).masterGain?.gain.value).toBe(VOLUME_MIN)
  })

  it('keeps playing sounds after a bad volume was offered', () => {
    const audio = new AudioManager()
    audio.init()
    audio.setVolume(Number.NaN)
    audio.handleEvents([
      { type: 'explosion', x: 0, y: 0, kind: 'small' },
      { type: 'stage_clear', stage: 0 },
      { type: 'player_hit' },
      { type: 'powerup_collected', powerUp: 'star', by: 'player' },
    ])
    expect(internals(audio).enabled).toBe(true)
  })

  it('is silent (no context) until init() runs', () => {
    const audio = new AudioManager()
    expect(() => audio.playShoot()).not.toThrow()
    expect(internals(audio).ctx).toBeNull()
  })
})
