"use client";

import { useRef, useEffect, useState, useCallback } from "react";
import type { TrackMeta } from "@/lib/types";
import { WaveformProgress } from "@/components/WaveformProgress";

// ---------------------------------------------------------------------------
// hls.js — loaded dynamically for Chrome/Firefox HLS playback.
// Safari has native HLS support and doesn't need this.
// ---------------------------------------------------------------------------
type HlsClass = {
  new (config?: Record<string, unknown>): HlsInstance;
  isSupported(): boolean;
};

type HlsInstance = {
  loadSource(url: string): void;
  attachMedia(media: HTMLAudioElement): void;
  destroy(): void;
  on(event: string, callback: (...args: unknown[]) => void): void;
  startLoad(): void;
  recoverMediaError(): void;
};

let HlsConstructor: HlsClass | null = null;
let hlsLoadPromise: Promise<HlsClass | null> | null = null;

function getHlsPromise(): Promise<HlsClass | null> {
  if (hlsLoadPromise) return hlsLoadPromise;
  if (typeof window === "undefined") return Promise.resolve(null);
  hlsLoadPromise = import("hls.js")
    .then((mod) => {
      HlsConstructor = mod.default as unknown as HlsClass;
      return HlsConstructor;
    })
    .catch(() => null);
  return hlsLoadPromise;
}

// ---------------------------------------------------------------------------
// Source badge label
// ---------------------------------------------------------------------------

const SOURCE_LABELS: Record<string, string> = {
  upload: "Your Library",
  jamendo: "Jamendo",
  openverse: "Openverse",
  incompetech: "Incompetech",
  ccmixter: "ccMixter",
};

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface MusicPlayerProps {
  track: TrackMeta | null;
  isPlaying: boolean;
  isGeminiSpeaking: boolean;
  volume: number;
  onPlay: () => void;
  onPause: () => void;
  onSkip: () => void;
  onPrevious?: () => void;
  onSeek: (time: number) => void;
  onTrackEnd: () => void;
}

// ---------------------------------------------------------------------------
// MusicPlayer
// ---------------------------------------------------------------------------

export default function MusicPlayer({
  track,
  isPlaying,
  isGeminiSpeaking,
  volume,
  onPlay,
  onPause,
  onSkip,
  onPrevious,
  onSeek,
  onTrackEnd,
}: MusicPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const hlsRef = useRef<HlsInstance | null>(null);
  const progressBarRef = useRef<HTMLDivElement>(null);
  const volumeIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Stable refs so the HLS setup effect can read current values without
  // being destroyed/recreated on every play/pause toggle.
  const isPlayingRef = useRef(isPlaying);
  useEffect(() => { isPlayingRef.current = isPlaying; }, [isPlaying]);
  const onTrackEndRef = useRef(onTrackEnd);
  useEffect(() => { onTrackEndRef.current = onTrackEnd; }, [onTrackEnd]);
  const onPauseRef = useRef(onPause);
  useEffect(() => { onPauseRef.current = onPause; }, [onPause]);

  /** Try to play; if browser blocks it (autoplay policy), flip the UI to
   *  "paused" so the user sees a play button and can tap to unblock. */
  const tryPlay = useCallback((audio: HTMLAudioElement) => {
    console.log("[MusicPlayer] tryPlay called — readyState:", audio.readyState, "paused:", audio.paused, "src:", audio.src?.slice(0, 60));
    const p = audio.play();
    if (p) {
      p.then(() => {
        console.log("[MusicPlayer] play() SUCCESS — paused:", audio.paused, "volume:", audio.volume);
      }).catch((err: DOMException) => {
        console.error("[MusicPlayer] play() REJECTED:", err.name, err.message);
        if (err.name === "NotAllowedError") {
          onPauseRef.current();
        }
      });
    }
  }, []);

  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffered, setBuffered] = useState(0);
  const [imageError, setImageError] = useState(false);

  // Waveform: null = not loaded / not available, [] = loading, number[] = ready
  const [waveformData, setWaveformData] = useState<number[] | null>(null);
  const [waveformLoading, setWaveformLoading] = useState(false);

  // Reset image error state when track changes
  useEffect(() => {
    setImageError(false);
  }, [track?.id]);

  // ------------------------------------------------------------------
  // Waveform fetch — fire when track id changes, fall back gracefully
  // ------------------------------------------------------------------

  useEffect(() => {
    if (!track?.id) {
      setWaveformData(null);
      return;
    }

    let cancelled = false;
    setWaveformLoading(true);
    setWaveformData(null);

    fetch(`/api/tracks/${track.id}/waveform`)
      .then((res) => {
        if (!res.ok) return null; // 404 = no waveform yet — use flat bar
        return res.json() as Promise<{ waveform_data: number[] }>;
      })
      .then((json) => {
        if (cancelled) return;
        setWaveformData(json?.waveform_data ?? null);
      })
      .catch(() => {
        if (!cancelled) setWaveformData(null);
      })
      .finally(() => {
        if (!cancelled) setWaveformLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [track?.id]);

  // ------------------------------------------------------------------
  // HLS setup — attaches a new source whenever track.hls_url changes
  // ------------------------------------------------------------------

  useEffect(() => {
    if (!track?.hls_url || !audioRef.current) {
      console.log("[MusicPlayer] HLS effect skip — hls_url:", track?.hls_url, "audioRef:", !!audioRef.current);
      return;
    }
    const audio = audioRef.current;
    let cancelled = false;
    console.log("[MusicPlayer] HLS effect: loading", track.hls_url);

    // Tear down any existing hls.js instance
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    // Always try hls.js first via async import. Brave/Chrome report native
    // HLS support via canPlayType but their demuxer is broken (COULD_NOT_PARSE).
    // hls.js uses MediaSource Extensions which works on all desktop browsers.
    // Only fall back to native HLS if hls.js is genuinely unsupported (Safari iOS).
    const hlsUrl = track.hls_url;

    getHlsPromise().then((Hls) => {
      if (cancelled) { console.log("[MusicPlayer] HLS setup cancelled"); return; }

      if (Hls && Hls.isSupported()) {
        // hls.js path — Chrome, Firefox, Brave, desktop Safari, etc.
        console.log("[MusicPlayer] hls.js path");
        const hls = new Hls({
          enableWorker: true,
          lowLatencyMode: false,
          startLevel: 0,
          abrEwmaDefaultEstimate: 500000,
          maxBufferLength: 30,
          maxMaxBufferLength: 60,
        });
        hls.loadSource(hlsUrl);
        hls.attachMedia(audio);

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        hls.on("hlsManifestParsed" as any, () => {
          if (cancelled) { console.log("[MusicPlayer] manifest parsed but cancelled"); return; }
          console.log("[MusicPlayer] HLS manifest parsed — isPlayingRef:", isPlayingRef.current);
          setCurrentTime(0);
          setDuration(0);
          setBuffered(0);
          if (isPlayingRef.current) tryPlay(audio);
        });

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        hls.on("hlsError" as any, (_event: any, data: any) => {
          if (data.fatal) {
            console.error("Fatal HLS error:", data.type, data.details);
            if (data.type === "networkError") {
              hls.startLoad();
            } else if (data.type === "mediaError") {
              hls.recoverMediaError();
            } else {
              onTrackEndRef.current();
            }
          }
        });

        hlsRef.current = hls;
      } else if (audio.canPlayType("application/vnd.apple.mpegurl")) {
        // Native HLS fallback — Safari iOS only (no MSE)
        console.log("[MusicPlayer] Native HLS fallback (Safari iOS)");
        setCurrentTime(0);
        setDuration(0);
        setBuffered(0);

        audio.addEventListener("loadedmetadata", () => {
          if (cancelled) return;
          console.log("[MusicPlayer] Native HLS loaded — readyState:", audio.readyState);
          if (isPlayingRef.current) tryPlay(audio);
        }, { once: true });
        audio.addEventListener("error", () => {
          if (!cancelled) console.error("[MusicPlayer] Native HLS error:", audio.error?.message);
        }, { once: true });

        audio.src = hlsUrl;
        audio.load();
      } else {
        console.error("[MusicPlayer] No HLS support — hls.js failed to load and native unsupported");
      }
    });

    return () => {
      cancelled = true;
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
    // Only re-run when the HLS URL changes — play/pause is handled by
    // the separate isPlaying effect, and onTrackEnd is accessed via ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [track?.hls_url]);

  // ------------------------------------------------------------------
  // Play / pause
  // ------------------------------------------------------------------

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      // Guard: only play if a source is loaded (HLS attached or native src set).
      // Without this, play() on an empty element rejects and the real HLS play
      // (in hlsManifestParsed) may get blocked by autoplay policy.
      if (audio.readyState >= 1 || hlsRef.current) {
        tryPlay(audio);
      }
    } else {
      audio.pause();
    }
  }, [isPlaying, tryPlay]);

  // ------------------------------------------------------------------
  // Volume — duck to 15% while Gemini is speaking, smooth transition
  // ------------------------------------------------------------------

  useEffect(() => {
    if (!audioRef.current) return;
    const audio = audioRef.current;
    const targetVolume = isGeminiSpeaking ? volume * 0.15 : volume;
    const step = (targetVolume - audio.volume) / 10;

    // Clear any in-flight transition
    if (volumeIntervalRef.current) {
      clearInterval(volumeIntervalRef.current);
    }

    let frame = 0;
    volumeIntervalRef.current = setInterval(() => {
      audio.volume = Math.max(0, Math.min(1, audio.volume + step));
      frame++;
      if (frame >= 10) {
        if (volumeIntervalRef.current) clearInterval(volumeIntervalRef.current);
        volumeIntervalRef.current = null;
        audio.volume = targetVolume; // snap to target in case of float drift
      }
    }, 30);

    return () => {
      if (volumeIntervalRef.current) {
        clearInterval(volumeIntervalRef.current);
        volumeIntervalRef.current = null;
      }
    };
  }, [isGeminiSpeaking, volume]);

  // ------------------------------------------------------------------
  // Progress bar interaction — click or touch to seek
  // ------------------------------------------------------------------

  const handleProgressInteraction = useCallback(
    (clientX: number) => {
      if (!progressBarRef.current || !duration) return;
      const rect = progressBarRef.current.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const seekTime = ratio * duration;
      if (audioRef.current) {
        audioRef.current.currentTime = seekTime;
        setCurrentTime(seekTime);
      }
      onSeek(seekTime);
    },
    [duration, onSeek]
  );

  const handleWaveformSeek = useCallback(
    (positionSeconds: number) => {
      if (audioRef.current) {
        audioRef.current.currentTime = positionSeconds;
        setCurrentTime(positionSeconds);
      }
      onSeek(positionSeconds);
    },
    [onSeek]
  );

  const handleProgressClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      handleProgressInteraction(e.clientX);
    },
    [handleProgressInteraction]
  );

  const handleProgressTouch = useCallback(
    (e: React.TouchEvent<HTMLDivElement>) => {
      if (e.touches.length > 0) {
        handleProgressInteraction(e.touches[0].clientX);
      }
    },
    [handleProgressInteraction]
  );

  // ------------------------------------------------------------------
  // Computed values
  // ------------------------------------------------------------------

  const progressPercent = duration > 0 ? (currentTime / duration) * 100 : 0;
  const bufferedPercent = duration > 0 ? (buffered / duration) * 100 : 0;
  const sourceLabel = track?.source ? SOURCE_LABELS[track.source] : undefined;
  const showAlbumArt = !!track?.artwork_url && !imageError;

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  return (
    <div
      className="flex flex-col items-center justify-center w-full h-full
                 bg-gradient-to-b from-gray-950 to-black
                 select-none overflow-hidden"
      style={{ paddingBottom: "calc(2rem + var(--safe-bottom, 0px))" }}
    >
      {/* Hidden audio element — controlled imperatively */}
      <audio
        ref={audioRef}
        preload="auto"
        onTimeUpdate={() =>
          setCurrentTime(audioRef.current?.currentTime ?? 0)
        }
        onLoadedMetadata={() =>
          setDuration(audioRef.current?.duration ?? 0)
        }
        onEnded={onTrackEnd}
        onProgress={() => {
          const audio = audioRef.current;
          if (audio?.buffered.length) {
            setBuffered(audio.buffered.end(audio.buffered.length - 1));
          }
        }}
      />

      {/* Album art */}
      <div className="flex-shrink-0 mb-8 mt-4">
        <div
          className={[
            "relative aspect-square w-[80vw] max-w-[400px] rounded-2xl shadow-2xl overflow-hidden",
            // Pulsing glow ring while playing
            isPlaying
              ? "ring-2 ring-indigo-500/20 shadow-indigo-900/30"
              : "ring-1 ring-white/5",
          ].join(" ")}
          style={{
            transition: "box-shadow 0.6s ease, ring 0.6s ease",
            animation: isPlaying ? "albumPulse 3s ease-in-out infinite" : "none",
          }}
        >
          {showAlbumArt ? (
            <img
              src={track!.artwork_url!}
              alt={track?.album ?? track?.title ?? "Album art"}
              className="w-full h-full object-cover"
              onError={() => setImageError(true)}
              draggable={false}
            />
          ) : (
            /* Fallback — gradient with artist initial or music note */
            <div className="w-full h-full bg-gradient-to-br from-indigo-900/40 to-gray-900 flex items-center justify-center">
              <span className="text-6xl font-bold text-indigo-400/30 leading-none">
                {track?.artist?.[0]?.toUpperCase() ?? "♪"}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Track info */}
      <div className="w-[80vw] max-w-[400px] mb-6 text-center px-1">
        <div
          className="text-lg font-semibold text-white truncate leading-tight mb-1"
          title={track?.title}
        >
          {track?.title ?? "No track loaded"}
        </div>
        <div
          className="text-sm text-gray-400 truncate mb-0.5"
          title={track?.artist}
        >
          {track?.artist ?? ""}
        </div>
        <div
          className="text-xs text-gray-600 truncate"
          title={track?.album}
        >
          {track?.album ?? ""}
        </div>
      </div>

      {/* Progress / Waveform */}
      <div className="w-[80vw] max-w-[400px] mb-5">
        {/* Time labels */}
        <div className="flex justify-between mb-2">
          <span className="text-xs text-gray-500 tabular-nums">
            {formatTime(currentTime)}
          </span>
          <span className="text-xs text-gray-500 tabular-nums">
            {duration > 0 ? formatTime(duration) : "--:--"}
          </span>
        </div>

        {/* Waveform visualiser — shown when data is available */}
        {waveformData && waveformData.length > 0 ? (
          <WaveformProgress
            waveformData={waveformData}
            progress={duration > 0 ? currentTime / duration : 0}
            duration={duration}
            onSeek={handleWaveformSeek}
            height={48}
          />
        ) : waveformLoading ? (
          /* Skeleton shimmer while waveform is loading */
          <div className="relative h-12 rounded-md overflow-hidden bg-gray-800">
            <div
              className="absolute inset-0 -translate-x-full animate-[shimmer_1.5s_infinite]
                         bg-gradient-to-r from-transparent via-gray-700/40 to-transparent"
            />
          </div>
        ) : (
          /* Flat progress bar fallback when no waveform data */
          <div
            ref={progressBarRef}
            role="slider"
            aria-label="Seek"
            aria-valuenow={Math.round(currentTime)}
            aria-valuemin={0}
            aria-valuemax={Math.round(duration)}
            className="relative h-1 rounded-full bg-gray-800 cursor-pointer group"
            onClick={handleProgressClick}
            onTouchStart={handleProgressTouch}
            style={{ touchAction: "none" }}
          >
            {/* Buffered indicator */}
            <div
              className="absolute inset-y-0 left-0 rounded-full bg-gray-600/50"
              style={{ width: `${bufferedPercent}%`, transition: "width 0.5s linear" }}
            />
            {/* Played fill */}
            <div
              className="absolute inset-y-0 left-0 rounded-full bg-indigo-500"
              style={{ width: `${progressPercent}%`, transition: "width 0.25s linear" }}
            />
            {/* Scrubber thumb — appears on hover/focus */}
            <div
              className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2
                         w-3 h-3 rounded-full bg-indigo-400
                         opacity-0 group-hover:opacity-100
                         transition-opacity duration-150 pointer-events-none"
              style={{ left: `${progressPercent}%` }}
            />
          </div>
        )}
      </div>

      {/* Controls */}
      <div className="flex items-center gap-8 mb-4">
        {/* Previous */}
        <button
          onClick={onPrevious}
          disabled={!onPrevious}
          className="w-12 h-12 flex items-center justify-center
                     text-white/70 hover:text-indigo-400 disabled:text-white/20
                     hover:scale-110 active:scale-95
                     transition-all duration-150 rounded-full"
          aria-label="Previous track"
        >
          <PreviousIcon className="w-6 h-6" />
        </button>

        {/* Play / Pause — larger touch target */}
        <button
          onClick={() => {
            if (isPlaying) {
              onPause();
            } else {
              // CRITICAL: call audio.play() synchronously inside the click
              // handler so the browser treats it as a user gesture. Going
              // through state → useEffect loses the gesture context and
              // mobile browsers will block autoplay.
              audioRef.current?.play().catch(() => {});
              onPlay();
            }
          }}
          disabled={!track}
          className="w-16 h-16 flex items-center justify-center rounded-full
                     bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-700
                     text-white
                     hover:scale-110 active:scale-95
                     transition-all duration-150
                     shadow-lg shadow-indigo-900/40 ring-2 ring-indigo-500/20"
          aria-label={isPlaying ? "Pause" : "Play"}
        >
          {isPlaying ? (
            <PauseIcon className="w-7 h-7" />
          ) : (
            <PlayIcon className="w-7 h-7 translate-x-0.5" />
          )}
        </button>

        {/* Skip / Next */}
        <button
          onClick={onSkip}
          className="w-12 h-12 flex items-center justify-center
                     text-white/70 hover:text-indigo-400
                     hover:scale-110 active:scale-95
                     transition-all duration-150 rounded-full"
          aria-label="Skip to next track"
        >
          <NextIcon className="w-6 h-6" />
        </button>
      </div>

      {/* Source badge */}
      {sourceLabel && (
        <div className="text-[10px] text-gray-600 tracking-wide uppercase mt-1">
          {sourceLabel}
        </div>
      )}

      {/* Inline keyframes — album art pulse + waveform shimmer */}
      <style>{`
        @keyframes albumPulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(99,102,241,0.08), 0 25px 50px -12px rgba(0,0,0,0.9); }
          50%       { box-shadow: 0 0 0 8px rgba(99,102,241,0.04), 0 25px 50px -12px rgba(99,102,241,0.15); }
        }
        @keyframes shimmer {
          100% { transform: translateX(200%); }
        }
      `}</style>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline SVG icons — no external icon dep required
// ---------------------------------------------------------------------------

function PlayIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M8 5v14l11-7L8 5z" />
    </svg>
  );
}

function PauseIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <rect x="6" y="4" width="4" height="16" rx="1" />
      <rect x="14" y="4" width="4" height="16" rx="1" />
    </svg>
  );
}

function PreviousIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M6 6h2v12H6zm3.5 6L20 18V6z" />
    </svg>
  );
}

function NextIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M6 18l8.5-6L6 6v12zm2.5-6L16 18V6z" />
      <rect x="16" y="6" width="2" height="12" rx="1" />
    </svg>
  );
}
