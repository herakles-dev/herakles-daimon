"use client";

import { useCallback, useRef, useEffect, useState } from "react";
import { base64PCM16ToFloat32, createPlaybackContext, rerouteContextToDefault } from "@/lib/audio-processor";
import { AUDIO_OUTPUT_SAMPLE_RATE, DEFAULT_VOICE_EFFECTS } from "@/lib/constants";
import { VoiceEffectsChain } from "@/lib/voice-effects";
import type { VoiceEffectsConfig } from "@/lib/types";

interface UseAudioPlaybackReturn {
  /** Queue a base64 PCM16 chunk for seamless playback */
  enqueue: (base64: string) => void;
  /** Stop all audio and clear the queue (barge-in) */
  flush: () => void;
  /** Whether audio is currently playing */
  isPlaying: boolean;
  /** Apply a new VoiceEffectsConfig instantly — no reconnect needed */
  updateEffects: (config: VoiceEffectsConfig) => void;
}

/**
 * Hook for seamless playback of Gemini's voice response.
 * Buffers incoming PCM16 chunks and schedules them for
 * gapless sequential playback via Web Audio API.
 *
 * @param initialEffectsConfig - Seed the effects chain with saved settings so
 *   the very first enqueue uses the user's config rather than DEFAULT_VOICE_EFFECTS.
 *   Prevents a brief flash of wrong effects before the useEffect sync fires.
 */
export function useAudioPlayback(initialEffectsConfig?: VoiceEffectsConfig): UseAudioPlaybackReturn {
  const [isPlaying, setIsPlaying] = useState(false);
  const contextRef = useRef<AudioContext | null>(null);
  const nextStartTimeRef = useRef(0);
  const activeSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const effectsChainRef = useRef<VoiceEffectsChain | null>(null);
  // Seed with the caller's saved config (or DEFAULT_VOICE_EFFECTS as fallback)
  // so the first-created chain already matches the user's stored preferences.
  const effectsConfigRef = useRef<VoiceEffectsConfig>(initialEffectsConfig ?? DEFAULT_VOICE_EFFECTS);

  // Lazily create AudioContext (must be after user gesture).
  // Also creates the VoiceEffectsChain and connects it to the context destination.
  const getContext = useCallback(() => {
    if (!contextRef.current || contextRef.current.state === "closed") {
      const ctx = createPlaybackContext();
      contextRef.current = ctx;
      // Create a fresh effects chain for the new context
      const chain = new VoiceEffectsChain(ctx);
      chain.connectOutput(ctx.destination);
      effectsChainRef.current = chain;
      // Reapply the current config so settings survive context recreation
      chain.update(effectsConfigRef.current);
    }
    // Resume if suspended (browser autoplay policy)
    if (contextRef.current.state === "suspended") {
      contextRef.current.resume();
    }
    return contextRef.current;
  }, []);

  const enqueue = useCallback(
    (base64: string) => {
      const ctx = getContext();
      const samples = base64PCM16ToFloat32(base64);

      // Create an AudioBuffer from the PCM16 samples
      const audioBuffer = ctx.createBuffer(1, samples.length, AUDIO_OUTPUT_SAMPLE_RATE);
      audioBuffer.getChannelData(0).set(samples);

      // Schedule for gapless playback
      const source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      // Route through the effects chain when available; fall back to direct output
      if (effectsChainRef.current) {
        source.connect(effectsChainRef.current.input);
      } else {
        source.connect(ctx.destination);
      }

      // Apply pitch shift (detune in cents) — also affects playback speed
      const pitchCents = effectsConfigRef.current.pitchShift ?? 0;
      if (pitchCents !== 0) {
        source.detune.value = pitchCents;
      }
      // playbackRate factor from detune: lower pitch = slower playback
      const playbackRate = Math.pow(2, pitchCents / 1200);
      const actualDuration = audioBuffer.duration / playbackRate;

      const now = ctx.currentTime;
      const startTime = Math.max(now, nextStartTimeRef.current);
      source.start(startTime);
      nextStartTimeRef.current = startTime + actualDuration;

      // Track active sources for flush
      activeSourcesRef.current.add(source);
      source.onended = () => {
        activeSourcesRef.current.delete(source);
        if (activeSourcesRef.current.size === 0) {
          setIsPlaying(false);
        }
      };

      setIsPlaying(true);
    },
    [getContext]
  );

  const flush = useCallback(() => {
    // Stop all playing audio (barge-in)
    activeSourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch {
        // Already stopped
      }
    });
    activeSourcesRef.current.clear();
    nextStartTimeRef.current = 0;
    setIsPlaying(false);
    // Quench the delay feedback loop so old audio doesn't bleed through for seconds
    effectsChainRef.current?.flush();
  }, []);

  // Apply a new VoiceEffectsConfig to the chain.
  // Persists the config so it can be re-applied after an AudioContext recreation.
  const updateEffects = useCallback((config: VoiceEffectsConfig) => {
    effectsConfigRef.current = config;
    effectsChainRef.current?.update(config);
  }, []);

  // When audio output device changes (Bluetooth connect/disconnect),
  // reroute the AudioContext to the new default output.
  useEffect(() => {
    if (!navigator.mediaDevices?.addEventListener) return;

    const handleDeviceChange = async () => {
      const ctx = contextRef.current;
      if (!ctx || ctx.state === "closed") return;

      // Try the lightweight path first — setSinkId("") tells the context
      // to follow whatever the OS default output is right now
      const rerouted = await rerouteContextToDefault(ctx);
      if (rerouted) {
        console.log("[AudioPlayback] Rerouted to new default output via setSinkId");
        return;
      }

      // Fallback: close the old context and create a fresh one.
      // The next enqueue() call will lazily create it, picking up
      // the new default output (Bluetooth or speaker).
      console.log("[AudioPlayback] Device changed — recreating AudioContext");
      flush();
      // Dispose the effects chain before closing the context it was built on
      effectsChainRef.current?.dispose();
      effectsChainRef.current = null;
      ctx.close();
      contextRef.current = null;
      // getContext() will recreate both the AudioContext and effects chain on next enqueue
    };

    navigator.mediaDevices.addEventListener("devicechange", handleDeviceChange);
    return () => {
      navigator.mediaDevices.removeEventListener("devicechange", handleDeviceChange);
    };
  }, [flush]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      flush();
      effectsChainRef.current?.dispose();
      effectsChainRef.current = null;
      contextRef.current?.close();
      contextRef.current = null;
    };
  }, [flush]);

  return { enqueue, flush, isPlaying, updateEffects };
}
