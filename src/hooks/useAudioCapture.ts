"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  float32ToBase64PCM16,
  resample,
  createCaptureWorkletUrl,
  getMicConstraints,
} from "@/lib/audio-processor";
import { AUDIO_SAMPLE_RATE } from "@/lib/constants";

interface UseAudioCaptureOptions {
  /** Called with base64-encoded PCM16 chunks at ~100ms intervals */
  onAudioChunk: (base64: string) => void;
  /** Whether to capture (mute/unmute without destroying the stream) */
  enabled: boolean;
}

interface UseAudioCaptureReturn {
  /** Start mic capture — must be called from user gesture */
  startCapture: () => Promise<void>;
  /** Stop mic capture and release resources */
  stopCapture: () => void;
  /** Whether the mic stream is active */
  isCapturing: boolean;
  /** Error message if mic access failed */
  error: string | null;
}

/**
 * Hook to capture microphone audio, downsample to 16kHz PCM16,
 * and stream base64 chunks to the caller (for WebSocket transmission).
 *
 * Uses AudioWorklet for off-main-thread processing when available,
 * falls back to ScriptProcessorNode.
 */
export function useAudioCapture({
  onAudioChunk,
  enabled,
}: UseAudioCaptureOptions): UseAudioCaptureReturn {
  const [isCapturing, setIsCapturing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const scriptNodeRef = useRef<ScriptProcessorNode | null>(null);
  const onChunkRef = useRef(onAudioChunk);
  const enabledRef = useRef(enabled);

  // Keep refs in sync without re-creating audio pipeline
  useEffect(() => {
    onChunkRef.current = onAudioChunk;
  }, [onAudioChunk]);

  useEffect(() => {
    enabledRef.current = enabled;
  }, [enabled]);

  const startCapture = useCallback(async () => {
    if (streamRef.current) return; // already capturing

    try {
      setError(null);
      const stream = await navigator.mediaDevices.getUserMedia(
        getMicConstraints()
      );
      streamRef.current = stream;

      const ctx = new AudioContext({ sampleRate: AUDIO_SAMPLE_RATE });
      contextRef.current = ctx;

      const source = ctx.createMediaStreamSource(stream);
      const actualRate = ctx.sampleRate;

      // Try AudioWorklet first (better performance, off-main-thread)
      let workletUsed = false;
      try {
        const workletUrl = createCaptureWorkletUrl();
        await ctx.audioWorklet.addModule(workletUrl);
        URL.revokeObjectURL(workletUrl);

        const workletNode = new AudioWorkletNode(ctx, "pcm16-capture");
        workletNodeRef.current = workletNode;

        workletNode.port.onmessage = (e) => {
          if (!enabledRef.current) return;
          if (e.data.type === "audio") {
            let samples: Float32Array = e.data.samples;
            if (actualRate !== AUDIO_SAMPLE_RATE) {
              samples = resample(samples, actualRate, AUDIO_SAMPLE_RATE);
            }
            onChunkRef.current(float32ToBase64PCM16(samples));
          }
        };

        source.connect(workletNode);
        workletNode.connect(ctx.destination); // needed for processing
        workletUsed = true;
      } catch {
        console.warn("[AudioCapture] Worklet unavailable, using ScriptProcessor");
      }

      // Fallback: ScriptProcessorNode (deprecated but widely supported)
      if (!workletUsed) {
        const bufferSize = 4096;
        const scriptNode = ctx.createScriptProcessor(bufferSize, 1, 1);
        scriptNodeRef.current = scriptNode;

        scriptNode.onaudioprocess = (event) => {
          if (!enabledRef.current) return;
          const raw = event.inputBuffer.getChannelData(0);
          const samples = actualRate !== AUDIO_SAMPLE_RATE
            ? resample(new Float32Array(raw), actualRate, AUDIO_SAMPLE_RATE)
            : new Float32Array(raw);
          onChunkRef.current(float32ToBase64PCM16(samples));
        };

        source.connect(scriptNode);
        scriptNode.connect(ctx.destination);
      }

      setIsCapturing(true);
    } catch (err) {
      const message =
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "Microphone access denied. Please allow mic access to use Play."
          : `Microphone error: ${err instanceof Error ? err.message : String(err)}`;
      setError(message);
      setIsCapturing(false);
    }
  }, []);

  const stopCapture = useCallback(() => {
    workletNodeRef.current?.disconnect();
    workletNodeRef.current = null;

    scriptNodeRef.current?.disconnect();
    scriptNodeRef.current = null;

    contextRef.current?.close();
    contextRef.current = null;

    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    setIsCapturing(false);
  }, []);

  // When audio devices change (Bluetooth connect/disconnect),
  // restart capture so the mic picks up the new default input.
  useEffect(() => {
    if (!navigator.mediaDevices?.addEventListener) return;

    const handleDeviceChange = () => {
      if (!streamRef.current || !contextRef.current) return;

      // Check if the current mic track is still active.
      // When BT disconnects, the track may end; when BT connects,
      // we want to switch to the BT mic if the user prefers it.
      const track = streamRef.current.getAudioTracks()[0];
      if (track && track.readyState === "ended") {
        console.log("[AudioCapture] Mic track ended after device change — restarting");
        stopCapture();
        startCapture();
      }
      // If the track is still live, the browser usually handles the
      // switch automatically. No action needed.
    };

    navigator.mediaDevices.addEventListener("devicechange", handleDeviceChange);
    return () => {
      navigator.mediaDevices.removeEventListener("devicechange", handleDeviceChange);
    };
  }, [stopCapture, startCapture]);

  // Cleanup on unmount
  useEffect(() => {
    return () => stopCapture();
  }, [stopCapture]);

  return { startCapture, stopCapture, isCapturing, error };
}
