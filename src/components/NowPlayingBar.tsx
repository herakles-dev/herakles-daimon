'use client';

import type { TrackMeta } from '@/lib/types';

interface NowPlayingBarProps {
  track: TrackMeta | null;
  isPlaying: boolean;
  currentTime: number;
  duration: number;
  onPlay: () => void;
  onPause: () => void;
  onSkip: () => void;
  /** Expand to the full MusicPlayer view */
  onExpand: () => void;
  /** Only show when in music mode with a track loaded */
  visible: boolean;
}

/**
 * Persistent mini-player bar pinned to the bottom of the viewport.
 *
 * Layout:
 *   [art] Title – Artist    ▶/❚❚  ⏭
 *   ━━━━━━━━━━ (2px progress line at the very top of the bar)
 *
 * The bar respects safe-area-inset-bottom so it clears the home indicator
 * on notched phones and curved-corner Android devices.
 */
export default function NowPlayingBar({
  track,
  isPlaying,
  currentTime,
  duration,
  onPlay,
  onPause,
  onSkip,
  onExpand,
  visible,
}: NowPlayingBarProps) {
  if (!visible || !track) return null;

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  return (
    <div
      role="region"
      aria-label="Now playing"
      className="fixed bottom-0 left-0 right-0 z-40 bg-gray-950/95 backdrop-blur-lg border-t border-gray-800/50"
      style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
    >
      {/* Progress line — sits at the very top of the bar */}
      <div className="h-0.5 bg-gray-800" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}>
        <div
          className="h-full bg-indigo-500 transition-all duration-1000 ease-linear"
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* Bar content */}
      <div className="flex items-center h-16 px-4 gap-3">

        {/* Album art — tappable to expand */}
        <button
          onClick={onExpand}
          className="flex-shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded-lg"
          aria-label="Expand player"
          tabIndex={0}
        >
          {track.artwork_url ? (
            <img
              src={track.artwork_url}
              alt=""
              aria-hidden="true"
              className="w-10 h-10 rounded-lg object-cover"
            />
          ) : (
            <div
              className="w-10 h-10 rounded-lg bg-gradient-to-br from-indigo-900/40 to-gray-800 flex items-center justify-center"
              aria-hidden="true"
            >
              <span className="text-sm text-indigo-400/50">♪</span>
            </div>
          )}
        </button>

        {/* Track info — tappable to expand */}
        <button
          onClick={onExpand}
          className="flex-1 min-w-0 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded"
          aria-label={`${track.title} by ${track.artist || 'Unknown Artist'} — expand player`}
        >
          <p className="text-sm font-medium text-white truncate">{track.title}</p>
          <p className="text-xs text-gray-400 truncate">{track.artist || 'Unknown Artist'}</p>
        </button>

        {/* Playback controls */}
        <div className="flex items-center gap-1">
          {/* Play / Pause */}
          <button
            onClick={isPlaying ? onPause : onPlay}
            className="w-10 h-10 flex items-center justify-center text-white hover:text-indigo-400 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded-full"
            aria-label={isPlaying ? 'Pause' : 'Play'}
          >
            {isPlaying ? (
              <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
              </svg>
            ) : (
              <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M8 5v14l11-7z" />
              </svg>
            )}
          </button>

          {/* Skip to next */}
          <button
            onClick={onSkip}
            className="w-10 h-10 flex items-center justify-center text-gray-400 hover:text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded-full"
            aria-label="Skip to next track"
          >
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
