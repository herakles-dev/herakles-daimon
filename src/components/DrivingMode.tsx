'use client';

import { useRef, useCallback } from 'react';
import type { TrackMeta, VoiceEffectsConfig } from '@/lib/types';

interface DrivingModeProps {
  track: TrackMeta | null;
  isPlaying: boolean;
  isGeminiSpeaking: boolean;
  currentTime: number;
  duration: number;
  onPlay: () => void;
  onPause: () => void;
  onSkip: () => void;
  onPrevious: () => void;
  onTapGemini: () => void;
  onExit: () => void;
  voiceEffects?: VoiceEffectsConfig | null;
}

// Touch state tracked via ref — no state needed, avoids re-renders on touch
interface TouchState {
  startX: number;
  startY: number;
  startTime: number;
  lastTap: number;
}

const SWIPE_THRESHOLD = 50;   // px — minimum horizontal distance to register a swipe
const DOUBLE_TAP_MS  = 300;   // ms — max gap between two taps to count as double-tap
const SWIPE_MAX_VERTICAL = 80; // px — vertical tolerance before cancelling a horizontal swipe

export default function DrivingMode({
  track,
  isPlaying,
  isGeminiSpeaking,
  currentTime,
  duration,
  onPlay,
  onPause,
  onSkip,
  onPrevious,
  onTapGemini,
  onExit,
  voiceEffects,
}: DrivingModeProps) {
  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;
  const effectsActive = voiceEffects && !voiceEffects.bypass && voiceEffects.preset !== "clean";
  const touch = useRef<TouchState>({ startX: 0, startY: 0, startTime: 0, lastTap: 0 });

  // -------------------------------------------------------------------
  // Gesture handlers — attached to the full-screen container so any
  // part of the screen registers the swipe / double-tap.
  // -------------------------------------------------------------------
  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    const t = e.touches[0];
    touch.current.startX = t.clientX;
    touch.current.startY = t.clientY;
    touch.current.startTime = Date.now();
  }, []);

  const handleTouchEnd = useCallback(
    (e: React.TouchEvent) => {
      const t = e.changedTouches[0];
      const dx = t.clientX - touch.current.startX;
      const dy = t.clientY - touch.current.startY;
      const elapsed = Date.now() - touch.current.startTime;

      const absDx = Math.abs(dx);
      const absDy = Math.abs(dy);

      // --- Swipe detection (horizontal, within vertical tolerance) -----
      if (absDx >= SWIPE_THRESHOLD && absDy <= SWIPE_MAX_VERTICAL) {
        if (dx < 0) {
          // Swipe left → skip forward
          onSkip();
        } else {
          // Swipe right → go to previous
          onPrevious();
        }
        return;
      }

      // --- Double-tap detection (short distance, fast successive taps) -
      // Only treat as a tap if the finger barely moved
      if (absDx < 20 && absDy < 20 && elapsed < 300) {
        const now = Date.now();
        if (now - touch.current.lastTap < DOUBLE_TAP_MS) {
          // Double-tap anywhere on the container → play / pause
          isPlaying ? onPause() : onPlay();
          touch.current.lastTap = 0; // reset so triple-tap doesn't fire again
        } else {
          touch.current.lastTap = now;
        }
      }
    },
    [isPlaying, onPlay, onPause, onSkip, onPrevious],
  );

  return (
    <div
      className="fixed inset-0 z-50 bg-black flex flex-col select-none"
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      // Prevent browser back-swipe / pull-to-refresh from firing on gesture
      style={{ touchAction: 'none' }}
    >
      {/* ----------------------------------------------------------------
          Exit — deliberately small and hard to tap by accident.
          Top-left, below the status-bar notch.
      ----------------------------------------------------------------- */}
      <button
        onClick={onExit}
        className="absolute z-10 text-gray-600 text-xs px-3 py-2 leading-none"
        style={{ top: 'max(1rem, env(safe-area-inset-top, 1rem))', left: '1rem' }}
        aria-label="Exit driving mode"
      >
        Exit
      </button>

      {/* ----------------------------------------------------------------
          Album art — 55 % of the screen height.
          Blurred version of the artwork fills the background; the sharp
          artwork sits on top as a rounded card.
      ----------------------------------------------------------------- */}
      <div className="relative flex-[55] flex items-center justify-center overflow-hidden">
        {/* Blurred ambient background */}
        {track?.artwork_url && (
          <img
            src={track.artwork_url}
            alt=""
            aria-hidden="true"
            className="absolute inset-0 w-full h-full object-cover scale-110 blur-3xl opacity-20"
          />
        )}

        {/* Sharp artwork card */}
        {track?.artwork_url ? (
          <img
            src={track.artwork_url}
            alt={track.title ? `Album art for ${track.title}` : 'Album art'}
            className="relative w-48 h-48 sm:w-64 sm:h-64 rounded-3xl object-cover shadow-2xl"
          />
        ) : (
          /* Placeholder when no artwork is available */
          <div
            className="relative w-48 h-48 sm:w-64 sm:h-64 rounded-3xl
                       bg-gradient-to-br from-indigo-900/30 to-gray-900
                       flex items-center justify-center shadow-2xl"
          >
            <MusicNoteIcon className="w-24 h-24 text-indigo-400/20" />
          </div>
        )}
      </div>

      {/* ----------------------------------------------------------------
          Track info — largest text allowed without crowding the controls.
          truncate keeps long titles on a single line so nothing wraps.
      ----------------------------------------------------------------- */}
      <div className="px-8 py-4 text-center">
        <h1 className="text-2xl font-bold text-white truncate leading-tight">
          {track?.title ?? 'No Track'}
        </h1>
        <p className="text-lg text-gray-300 truncate mt-1 leading-tight">
          {track?.artist ?? '\u00A0' /* non-breaking space preserves row height */}
        </p>
      </div>

      {/* ----------------------------------------------------------------
          Transport controls.
          Previous: 72 × 72 px  |  Play/Pause: 96 × 96 px  |  Skip: 72 × 72 px
          All buttons use active:scale-90 for tactile feedback without
          adding any hover state that looks odd on touch screens.
      ----------------------------------------------------------------- */}
      <div className="flex items-center justify-center gap-12 py-6">
        {/* Previous */}
        <button
          onClick={onPrevious}
          className="w-[72px] h-[72px] flex items-center justify-center
                     text-white active:scale-90 transition-transform duration-100"
          aria-label="Previous track"
        >
          <PreviousIcon className="w-10 h-10" />
        </button>

        {/* Play / Pause — filled white circle, clearly the primary action */}
        <button
          onClick={isPlaying ? onPause : onPlay}
          className="w-[96px] h-[96px] flex items-center justify-center
                     bg-white rounded-full text-black shadow-lg
                     active:scale-90 transition-transform duration-100"
          aria-label={isPlaying ? 'Pause' : 'Play'}
        >
          {isPlaying ? (
            <PauseIcon className="w-12 h-12" />
          ) : (
            <PlayIcon className="w-12 h-12 translate-x-0.5" />
          )}
        </button>

        {/* Skip */}
        <button
          onClick={onSkip}
          className="w-[72px] h-[72px] flex items-center justify-center
                     text-white active:scale-90 transition-transform duration-100"
          aria-label="Skip track"
        >
          <SkipIcon className="w-10 h-10" />
        </button>
      </div>

      {/* ----------------------------------------------------------------
          Gemini orb — always visible.
          Pulsing indigo ring when Gemini is speaking; muted gray when
          idle. Single tap toggles the voice interaction.
          Pointer-events are explicitly set so swipe gestures on the
          container still propagate, but the button itself is tappable.
      ----------------------------------------------------------------- */}
      <button
        onClick={onTapGemini}
        className="mx-auto mb-4 relative pointer-events-auto"
        aria-label={isGeminiSpeaking ? 'Gemini is speaking — tap to interrupt' : 'Tap to talk to Gemini'}
      >
        {/* Outer pulse ring — only rendered while speaking */}
        {isGeminiSpeaking && (
          <span
            className={`absolute inset-0 rounded-full animate-ping ${
              effectsActive ? 'bg-purple-500/30' : 'bg-indigo-500/30'
            }`}
            aria-hidden="true"
          />
        )}
        {/* Effects active ring — shown when voice effects are on */}
        {effectsActive && (
          <span
            className="absolute inset-0 rounded-full effects-active-ring"
            aria-hidden="true"
          />
        )}
        <div
          className={`relative w-14 h-14 rounded-full flex items-center justify-center transition-colors duration-300
            ${isGeminiSpeaking
              ? effectsActive
                ? 'bg-gradient-to-br from-purple-500 to-violet-500 shadow-lg shadow-purple-500/50'
                : 'bg-indigo-500 shadow-lg shadow-indigo-500/50'
              : 'bg-gray-800 hover:bg-gray-700'
            }`}
        >
          <MicrophoneIcon className="w-6 h-6 text-white" />
        </div>
      </button>

      {/* ----------------------------------------------------------------
          Progress bar — no time labels, just a thin strip at the bottom.
          Smooth linear transition matches HLS segment boundaries.
          Bottom padding respects the iPhone home indicator.
      ----------------------------------------------------------------- */}
      <div
        className="px-8"
        style={{ paddingBottom: 'max(2rem, env(safe-area-inset-bottom, 2rem))' }}
      >
        <div
          className="h-1 bg-gray-800 rounded-full overflow-hidden"
          role="progressbar"
          aria-valuenow={Math.round(progress)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Track progress"
        >
          <div
            className="h-full bg-indigo-500 rounded-full transition-all duration-1000 ease-linear"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Inline SVG icons — zero external dependencies, matches GeminiOverlay
// pattern. Each accepts an optional className for sizing.
// ------------------------------------------------------------------

function PlayIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M8 5v14l11-7z" />
    </svg>
  );
}

function PauseIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
    </svg>
  );
}

function PreviousIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      {/* Vertical bar + left-pointing triangle */}
      <path d="M6 6h2v12H6V6zm3.5 6 8.5 6V6l-8.5 6z" />
    </svg>
  );
}

function SkipIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      {/* Right-pointing triangle + vertical bar */}
      <path d="M6 18 14.5 12 6 6v12zm10.5-12v12h2V6h-2z" />
    </svg>
  );
}

function MicrophoneIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"
      />
    </svg>
  );
}

function MusicNoteIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3v10.55A4 4 0 1014 17V7h4V3h-6z" />
    </svg>
  );
}
