'use client';

import { useEffect } from 'react';
import type { TrackMeta } from '@/lib/types';

interface UseMediaSessionOptions {
  track: TrackMeta | null;
  isPlaying: boolean;
  audioElement: HTMLAudioElement | null;
  onPlay: () => void;
  onPause: () => void;
  onNextTrack: () => void;
  onPreviousTrack: () => void;
}

/**
 * Manages the Media Session API for lock screen controls,
 * Bluetooth metadata, and notification media controls.
 *
 * Enables:
 * - Lock screen play/pause/skip on Android + iOS
 * - Bluetooth A2DP metadata (track title + artist on car display)
 * - Notification tray controls (Android)
 * - iOS Control Center integration
 * - Hardware media keys (Bluetooth headphones)
 */
export function useMediaSession({
  track,
  isPlaying,
  audioElement,
  onPlay,
  onPause,
  onNextTrack,
  onPreviousTrack,
}: UseMediaSessionOptions) {

  // Update metadata when track changes
  useEffect(() => {
    if (!('mediaSession' in navigator) || !track) return;

    const artwork = track.artwork_url
      ? [
          { src: track.artwork_url, sizes: '96x96',  type: 'image/webp' },
          { src: track.artwork_url, sizes: '256x256', type: 'image/webp' },
          { src: track.artwork_url, sizes: '512x512', type: 'image/webp' },
        ]
      : [];

    navigator.mediaSession.metadata = new MediaMetadata({
      title:  track.title  || 'Unknown Track',
      artist: track.artist || 'Unknown Artist',
      album:  track.album  || '',
      artwork,
    });
  }, [track]);

  // Update playback state whenever isPlaying changes
  useEffect(() => {
    if (!('mediaSession' in navigator)) return;
    navigator.mediaSession.playbackState = isPlaying ? 'playing' : 'paused';
  }, [isPlaying]);

  // Register action handlers — re-register whenever callbacks or audioElement change
  useEffect(() => {
    if (!('mediaSession' in navigator)) return;

    navigator.mediaSession.setActionHandler('play',          onPlay);
    navigator.mediaSession.setActionHandler('pause',         onPause);
    navigator.mediaSession.setActionHandler('nexttrack',     onNextTrack);
    navigator.mediaSession.setActionHandler('previoustrack', onPreviousTrack);

    // Seek support — only when we have a real audio element
    if (audioElement) {
      navigator.mediaSession.setActionHandler('seekto', (details) => {
        if (details.seekTime !== undefined && audioElement) {
          audioElement.currentTime = details.seekTime;
        }
      });
    }

    return () => {
      navigator.mediaSession.setActionHandler('play',          null);
      navigator.mediaSession.setActionHandler('pause',         null);
      navigator.mediaSession.setActionHandler('nexttrack',     null);
      navigator.mediaSession.setActionHandler('previoustrack', null);
      navigator.mediaSession.setActionHandler('seekto',        null);
    };
  }, [onPlay, onPause, onNextTrack, onPreviousTrack, audioElement]);

  // Update position state every second while playing so the OS scrubber is accurate
  useEffect(() => {
    if (!('mediaSession' in navigator) || !audioElement || !isPlaying) return;

    const updatePosition = () => {
      if (audioElement.duration && !isNaN(audioElement.duration)) {
        navigator.mediaSession.setPositionState({
          duration:     audioElement.duration,
          playbackRate: audioElement.playbackRate,
          position:     audioElement.currentTime,
        });
      }
    };

    const interval = setInterval(updatePosition, 1000);
    updatePosition(); // immediate first update

    return () => clearInterval(interval);
  }, [audioElement, isPlaying]);
}
