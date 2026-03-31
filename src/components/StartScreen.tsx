"use client";

import { useGemini } from "@/context/GeminiProvider";

/**
 * Initial screen shown before Gemini session starts.
 * Single button to connect — required for browser audio permissions
 * (getUserMedia + AudioContext both need a user gesture).
 */
export function StartScreen() {
  const { connect, connectionStatus } = useGemini();

  const isConnecting = connectionStatus === "connecting" || connectionStatus === "connected";

  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-surface">
      <div className="text-center">
        {/* Logo / Title */}
        <h1 className="text-4xl font-light tracking-widest text-white/90 mb-2">
          PLAY
        </h1>
        <p className="text-sm text-white/30 mb-12 font-light">
          Your AI content curator
        </p>

        {/* Connect button */}
        <button
          onClick={connect}
          disabled={isConnecting}
          className="group relative px-8 py-4 rounded-full
                     bg-accent/20 border border-accent/40
                     hover:bg-accent/30 hover:border-accent/60
                     disabled:opacity-50 disabled:cursor-wait
                     transition-all duration-300"
        >
          <span className="text-lg font-light tracking-wide text-white/90">
            {isConnecting ? "Connecting..." : "Start Session"}
          </span>

          {/* Subtle glow */}
          <div className="absolute inset-0 rounded-full bg-accent/10 blur-xl
                          opacity-0 group-hover:opacity-100 transition-opacity -z-10" />
        </button>

        {/* Hint */}
        <p className="mt-8 text-xs text-white/20">
          Microphone access required for voice interaction
        </p>

        {connectionStatus === "error" && (
          <p className="mt-4 text-sm text-red-400/70">
            Connection failed. Check that the backend is running.
          </p>
        )}
      </div>
    </div>
  );
}
