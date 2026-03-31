import { AUDIO_SAMPLE_RATE, AUDIO_CHANNELS } from "./constants";

/**
 * Audio processing utilities for Gemini Multimodal Live API.
 * Format: PCM16 (16-bit signed little-endian), 16kHz, mono, base64-encoded.
 */

/** Convert Float32Array audio samples to base64-encoded PCM16 */
export function float32ToBase64PCM16(samples: Float32Array): string {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);

  for (let i = 0; i < samples.length; i++) {
    // Clamp to [-1, 1] then scale to Int16 range
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    const int16 = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    view.setInt16(i * 2, int16, true); // little-endian
  }

  return arrayBufferToBase64(buffer);
}

/** Convert base64-encoded PCM16 to Float32Array for Web Audio playback */
export function base64PCM16ToFloat32(base64: string): Float32Array {
  const buffer = base64ToArrayBuffer(base64);
  const view = new DataView(buffer);
  const samples = new Float32Array(buffer.byteLength / 2);

  for (let i = 0; i < samples.length; i++) {
    const int16 = view.getInt16(i * 2, true); // little-endian
    samples[i] = int16 / (int16 < 0 ? 0x8000 : 0x7fff);
  }

  return samples;
}

/** Resample audio from source rate to target rate using linear interpolation */
export function resample(
  samples: Float32Array,
  sourceRate: number,
  targetRate: number
): Float32Array {
  if (sourceRate === targetRate) return samples;

  const ratio = sourceRate / targetRate;
  const outputLength = Math.round(samples.length / ratio);
  const output = new Float32Array(outputLength);

  for (let i = 0; i < outputLength; i++) {
    const srcIndex = i * ratio;
    const lower = Math.floor(srcIndex);
    const upper = Math.min(lower + 1, samples.length - 1);
    const frac = srcIndex - lower;
    output[i] = samples[lower] * (1 - frac) + samples[upper] * frac;
  }

  return output;
}

/**
 * Create an AudioWorklet processor script as a Blob URL.
 * This runs in a separate thread to capture mic audio without jank.
 */
export function createCaptureWorkletUrl(): string {
  const code = `
    class PCM16CaptureProcessor extends AudioWorkletProcessor {
      constructor() {
        super();
        this._buffer = new Float32Array(0);
        this._chunkSize = ${AUDIO_SAMPLE_RATE} * 0.1; // 100ms chunks
      }

      process(inputs) {
        const input = inputs[0];
        if (!input || !input[0]) return true;

        const channelData = input[0];

        // Append to buffer
        const newBuffer = new Float32Array(this._buffer.length + channelData.length);
        newBuffer.set(this._buffer);
        newBuffer.set(channelData, this._buffer.length);
        this._buffer = newBuffer;

        // Emit chunks
        while (this._buffer.length >= this._chunkSize) {
          const chunk = this._buffer.slice(0, this._chunkSize);
          this._buffer = this._buffer.slice(this._chunkSize);
          this.port.postMessage({ type: 'audio', samples: chunk });
        }

        return true;
      }
    }

    registerProcessor('pcm16-capture', PCM16CaptureProcessor);
  `;

  const blob = new Blob([code], { type: "application/javascript" });
  return URL.createObjectURL(blob);
}

// ------------------------------------------------------------------
// Base64 helpers (browser-native, no deps)
// ------------------------------------------------------------------

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

/** Get an AudioContext for playback — uses device default rate.
 *  AudioBuffers created at 24kHz are upsampled automatically.
 *  On creation, attempts setSinkId to follow the system default output
 *  (important for Bluetooth: a context created pre-BT sticks to the speaker). */
export function createPlaybackContext(): AudioContext {
  return new AudioContext({ latencyHint: "playback" });
}

/** Try to switch an AudioContext's output to the system default.
 *  setSinkId("") = "use whatever the OS default output is right now".
 *  Returns true if the switch succeeded. */
export async function rerouteContextToDefault(ctx: AudioContext): Promise<boolean> {
  try {
    // setSinkId is available on AudioContext in Chrome 110+, Edge, Opera
    if ("setSinkId" in ctx && typeof (ctx as any).setSinkId === "function") {
      await (ctx as any).setSinkId("");
      return true;
    }
  } catch {
    // Not supported or failed — caller should recreate the context
  }
  return false;
}

/** Get audio constraints for getUserMedia — 16kHz mono */
export function getMicConstraints(): MediaStreamConstraints {
  return {
    audio: {
      sampleRate: AUDIO_SAMPLE_RATE,
      channelCount: AUDIO_CHANNELS,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: false,
  };
}
