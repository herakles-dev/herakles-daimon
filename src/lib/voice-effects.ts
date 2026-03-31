import type { VoiceEffectsConfig, ReverbSize } from "./types";

// ============================================================
// VoiceEffectsChain — Web Audio API effects processing chain
// for Gemini voice output.
//
// Signal path:
//   input → inputGain → eqLow → eqMid → eqHigh
//         → [split] → reverbDry ──────────────────┐
//                  └─ convolver → reverbWet ───────┤
//                                                  ↓
//                                            delay ← feedbackGain (loop)
//                                                  ↓
//                                           compressor → masterGain → [destination]
// ============================================================

/** Impulse response parameters keyed by ReverbSize */
const IR_PARAMS: Record<ReverbSize, { duration: number; decay: number }> = {
  small:  { duration: 0.5, decay: 1.5 },
  medium: { duration: 1.0, decay: 2.0 },
  large:  { duration: 2.0, decay: 3.0 },
  hall:   { duration: 3.5, decay: 4.0 },
};

/** Cache of generated impulse responses, keyed by sample-rate + size */
const irCache = new Map<string, AudioBuffer>();

/**
 * Generate a synthetic reverb impulse response using white noise * exponential decay.
 * Results are cached by "<sampleRate>:<size>" to avoid redundant allocation.
 */
function generateImpulseResponse(ctx: AudioContext, size: ReverbSize): AudioBuffer {
  const cacheKey = `${ctx.sampleRate}:${size}`;
  const cached = irCache.get(cacheKey);
  if (cached) return cached;

  const { duration, decay } = IR_PARAMS[size];
  const length = Math.ceil(ctx.sampleRate * duration);
  const buffer = ctx.createBuffer(2, length, ctx.sampleRate);

  for (let channel = 0; channel < 2; channel++) {
    const data = buffer.getChannelData(channel);
    for (let i = 0; i < length; i++) {
      // White noise scaled by exponential decay envelope
      const noise = Math.random() * 2 - 1;
      const envelope = Math.exp(-decay * i / length);
      data[i] = noise * envelope;
    }
  }

  irCache.set(cacheKey, buffer);
  return buffer;
}

export class VoiceEffectsChain {
  private ctx: AudioContext;

  // Chain nodes (in signal order)
  private inputGain: GainNode;
  private eqLow: BiquadFilterNode;
  private eqMid: BiquadFilterNode;
  private eqHigh: BiquadFilterNode;
  private reverbDry: GainNode;
  private reverbWet: GainNode;
  private convolver: ConvolverNode;
  private delay: DelayNode;
  private feedbackGain: GainNode;
  private compressor: DynamicsCompressorNode;
  private masterGain: GainNode;

  private bypassGain: GainNode;
  private bypassed: boolean = false;
  private disposed: boolean = false;

  /** Track the last reverb size so we only rebuild the IR on actual changes */
  private currentReverbSize: ReverbSize | null = null;

  /** Last applied config — used by flush() to restore delay/feedback after hard-cut */
  private _lastConfig: { delayTime: number; delayFeedback: number; reverbMix: number } = { delayTime: 0, delayFeedback: 0, reverbMix: 0 };

  constructor(ctx: AudioContext) {
    this.ctx = ctx;

    // Invalidate any cached AudioBuffers from a previous (now closed) AudioContext.
    // Reusing an AudioBuffer across contexts causes InvalidStateError on ConvolverNode.
    irCache.clear();

    // --- Input gain ---
    this.inputGain = ctx.createGain();
    this.inputGain.gain.value = 1;

    // --- EQ: low shelf ---
    this.eqLow = ctx.createBiquadFilter();
    this.eqLow.type = "lowshelf";
    this.eqLow.frequency.value = 320;
    this.eqLow.gain.value = 0;

    // --- EQ: peaking mid ---
    this.eqMid = ctx.createBiquadFilter();
    this.eqMid.type = "peaking";
    this.eqMid.frequency.value = 1000;
    this.eqMid.Q.value = 1.0;
    this.eqMid.gain.value = 0;

    // --- EQ: high shelf ---
    this.eqHigh = ctx.createBiquadFilter();
    this.eqHigh.type = "highshelf";
    this.eqHigh.frequency.value = 3200;
    this.eqHigh.gain.value = 0;

    // --- Reverb: dry path ---
    this.reverbDry = ctx.createGain();
    this.reverbDry.gain.value = 1;

    // --- Reverb: wet path ---
    this.convolver = ctx.createConvolver();
    this.reverbWet = ctx.createGain();
    this.reverbWet.gain.value = 0;

    // --- Delay with feedback loop ---
    this.delay = ctx.createDelay(1.0); // max 1 second
    this.delay.delayTime.value = 0;

    this.feedbackGain = ctx.createGain();
    this.feedbackGain.gain.value = 0;

    // --- Compressor ---
    this.compressor = ctx.createDynamicsCompressor();
    this.compressor.threshold.value = -24;
    this.compressor.ratio.value = 4;
    this.compressor.attack.value = 0.003;
    this.compressor.release.value = 0.25;
    this.compressor.knee.value = 30;

    // --- Master output ---
    this.masterGain = ctx.createGain();
    this.masterGain.gain.value = 1;

    // --- Bypass direct path (input → bypassGain → masterGain) ---
    // Starts silent; setBypass() crossfades between this and the effects path.
    this.bypassGain = ctx.createGain();
    this.bypassGain.gain.value = 0;

    // Wire the full chain (permanent topology — bypass is controlled via gains, not disconnects)
    this._connectChain();
  }

  // ------------------------------------------------------------------
  // Public API
  // ------------------------------------------------------------------

  /** AudioNode that external sources should connect to */
  get input(): AudioNode {
    return this.inputGain;
  }

  /** Connect the chain's output to an AudioNode (typically ctx.destination) */
  connectOutput(destination: AudioNode): void {
    if (this.disposed) return;
    this.masterGain.connect(destination);
  }

  /**
   * Apply a new VoiceEffectsConfig to the chain.
   * All AudioParam changes use setTargetAtTime with a 20ms time constant
   * to avoid clicks and pops on rapid updates.
   */
  update(config: VoiceEffectsConfig): void {
    if (this.disposed) return;

    const { currentTime } = this.ctx;
    const tc = 0.02; // 20ms time constant

    // EQ gains (dB)
    this.eqLow.gain.setTargetAtTime(config.eqLow, currentTime, tc);
    this.eqMid.gain.setTargetAtTime(config.eqMid, currentTime, tc);
    this.eqHigh.gain.setTargetAtTime(config.eqHigh, currentTime, tc);

    // Reverb dry/wet mix (clamp to [0,1] — values >1 phase-invert the dry signal)
    const safeMix = Math.min(1, Math.max(0, config.reverbMix));
    this.reverbDry.gain.setTargetAtTime(1 - safeMix, currentTime, tc);
    this.reverbWet.gain.setTargetAtTime(safeMix, currentTime, tc);

    // Reverb IR — rebuild only when size actually changes
    if (config.reverbSize !== this.currentReverbSize) {
      this.currentReverbSize = config.reverbSize;
      this.convolver.buffer = generateImpulseResponse(this.ctx, config.reverbSize);
    }

    // Delay
    this.delay.delayTime.setTargetAtTime(config.delayTime, currentTime, tc);
    // Clamp feedback to [0, 0.8] — never allow >= 1 (infinite loop)
    const safeFeedback = Math.min(0.8, Math.max(0, config.delayFeedback));
    this.feedbackGain.gain.setTargetAtTime(safeFeedback, currentTime, tc);
    // Persist for flush() restore
    this._lastConfig = { delayTime: config.delayTime, delayFeedback: safeFeedback, reverbMix: safeMix };

    // Compressor
    this.compressor.threshold.setTargetAtTime(config.compressionThreshold, currentTime, tc);
    this.compressor.ratio.setTargetAtTime(config.compressionRatio, currentTime, tc);

    // Master gain
    this.masterGain.gain.setTargetAtTime(config.masterGain, currentTime, tc);

    // Bypass state
    this.setBypass(config.bypass);
  }

  /**
   * Instantly quench the delay feedback loop (called on barge-in).
   * Hard-cuts feedbackGain and flushes the delay buffer to zero, then
   * restores both to their configured values after 50ms.
   */
  flush(): void {
    if (this.disposed) return;

    // Hard cut — no time constant, immediate
    this.feedbackGain.gain.value = 0;
    this.delay.delayTime.value = 0;

    // Restore to last configured values after 50ms so normal playback resumes cleanly
    const { delayTime, delayFeedback } = this._lastConfig;
    setTimeout(() => {
      if (this.disposed) return;
      const { currentTime } = this.ctx;
      const tc = 0.02;
      this.feedbackGain.gain.setTargetAtTime(delayFeedback, currentTime, tc);
      this.delay.delayTime.setTargetAtTime(delayTime, currentTime, tc);
    }, 50);
  }

  /**
   * Toggle bypass mode using gain crossfade — no topology changes, no audible pop.
   * Bypass=true:  bypassGain → 1, all wet gains → 0 (direct path active).
   * Bypass=false: bypassGain → 0, wet gains restored from config (effects active).
   */
  setBypass(bypassed: boolean): void {
    if (this.disposed) return;
    if (bypassed === this.bypassed) return;

    this.bypassed = bypassed;
    const { currentTime } = this.ctx;
    const tc = 0.02; // 20ms time constant

    if (bypassed) {
      this.bypassGain.gain.setTargetAtTime(1, currentTime, tc);
      this.reverbDry.gain.setTargetAtTime(0, currentTime, tc);
      this.reverbWet.gain.setTargetAtTime(0, currentTime, tc);
      this.feedbackGain.gain.setTargetAtTime(0, currentTime, tc);
    } else {
      this.bypassGain.gain.setTargetAtTime(0, currentTime, tc);
      // Restore dry/wet/feedback from last config so effects work immediately
      this.reverbDry.gain.setTargetAtTime(1 - (this._lastConfig?.reverbMix ?? 0), currentTime, tc);
      this.reverbWet.gain.setTargetAtTime(this._lastConfig?.reverbMix ?? 0, currentTime, tc);
      this.feedbackGain.gain.setTargetAtTime(
        Math.min(0.8, Math.max(0, this._lastConfig?.delayFeedback ?? 0)), currentTime, tc
      );
    }
  }

  /** Disconnect all nodes and mark as disposed. All methods become no-ops. */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;

    try { this.inputGain.disconnect(); } catch { /* already disconnected */ }
    try { this.eqLow.disconnect(); } catch { /* already disconnected */ }
    try { this.eqMid.disconnect(); } catch { /* already disconnected */ }
    try { this.eqHigh.disconnect(); } catch { /* already disconnected */ }
    try { this.reverbDry.disconnect(); } catch { /* already disconnected */ }
    try { this.convolver.disconnect(); } catch { /* already disconnected */ }
    try { this.reverbWet.disconnect(); } catch { /* already disconnected */ }
    try { this.delay.disconnect(); } catch { /* already disconnected */ }
    try { this.feedbackGain.disconnect(); } catch { /* already disconnected */ }
    try { this.compressor.disconnect(); } catch { /* already disconnected */ }
    try { this.bypassGain.disconnect(); } catch { /* already disconnected */ }
    try { this.masterGain.disconnect(); } catch { /* already disconnected */ }
  }

  // ------------------------------------------------------------------
  // Private helpers
  // ------------------------------------------------------------------

  /**
   * Wire all nodes into their permanent topology (called once from constructor).
   * Bypass is controlled entirely via gain values — no nodes are ever
   * disconnected or reconnected after construction, which eliminates the
   * audible pop caused by topology changes during playback.
   *
   * Signal paths (always connected):
   *
   *   Bypass path:
   *     inputGain → bypassGain → masterGain     (bypassGain.gain = 0 by default)
   *
   *   Effects path:
   *     inputGain → eqLow → eqMid → eqHigh
   *                                       ├→ reverbDry ─────────────────────┐
   *                                       └→ convolver → reverbWet ─────────┤
   *                                                                         ↓
   *                                              (delay ← feedbackGain loop)
   *                                                       delay → compressor → masterGain
   */
  private _connectChain(): void {
    // Bypass direct path
    this.inputGain.connect(this.bypassGain);
    this.bypassGain.connect(this.masterGain);

    // EQ series
    this.inputGain.connect(this.eqLow);
    this.eqLow.connect(this.eqMid);
    this.eqMid.connect(this.eqHigh);

    // Split into dry and wet reverb paths
    this.eqHigh.connect(this.reverbDry);
    this.eqHigh.connect(this.convolver);
    this.convolver.connect(this.reverbWet);

    // Merge dry + wet into delay
    this.reverbDry.connect(this.delay);
    this.reverbWet.connect(this.delay);

    // Feedback loop: delay output → feedbackGain → delay input
    this.delay.connect(this.feedbackGain);
    this.feedbackGain.connect(this.delay);

    // Delay → compressor → master output
    this.delay.connect(this.compressor);
    this.compressor.connect(this.masterGain);
  }
}
