"use client";

import {
  createContext,
  useContext,
  type ReactNode,
} from "react";
import { useGeminiLive } from "@/hooks/useGeminiLive";
import type {
  ConnectionStatus,
  ContentMode,
  MuseSettings,
  MusicState,
  TrackMeta,
  UIMode,
  VideoMeta,
  VideoQueueItem,
  VoiceEffectsConfig,
} from "@/lib/types";

interface GeminiContextValue {
  // Connection + video (unchanged)
  connectionStatus: ConnectionStatus;
  currentVideo: VideoMeta | null;
  isGeminiSpeaking: boolean;
  isMicActive: boolean;
  uiMode: UIMode;
  transcript: string[];
  skipCount: number;
  connect: () => Promise<void>;
  disconnect: () => void;
  skip: () => void;
  toggleMic: () => void;
  // Content mode + music state (new)
  contentMode: ContentMode;
  music: MusicState | null;
  // Music actions
  skipTrack: () => void;
  playTrack: () => void;
  pauseTrack: () => void;
  onTrackEnd: () => void;
  // Music playback primitives for MusicPlayer / NowPlayingBar / DrivingMode
  isMusicPlaying: boolean;
  musicVolume: number;
  setMusicVolume: (volume: number) => void;
  setCurrentTrack: (track: TrackMeta | null) => void;
  setUiMode: (mode: UIMode) => void;
  setContentMode: (mode: ContentMode) => void;
  setIsMusicPlaying: (playing: boolean) => void;
  trackCurrentTime: number;
  trackDuration: number;
  audioRef: React.RefObject<HTMLAudioElement | null>;
  // Convenience: the current track (mirrors music.currentTrack)
  currentTrack: TrackMeta | null;
  // Text chat — returns false if not connected
  sendChat: (text: string) => boolean;
  // Video end callback (reason: why the video ended)
  onVideoEnd: (reason?: "ended" | "stalled" | "error") => void;
  // Tool execution status
  toolBusy: string | null;
  // Muse settings
  museSettings: MuseSettings;
  updateSettings: (patch: Partial<MuseSettings>) => void;
  applySettings: (patch: Partial<MuseSettings>) => Promise<void>;
  // Video queue (QW3)
  videoQueue: VideoQueueItem[];
  nextVideo: VideoMeta | null;
  // Voice effects — client-side only, instant, no reconnect
  voiceEffects: VoiceEffectsConfig;
  updateVoiceEffects: (config: VoiceEffectsConfig) => void;
}

const GeminiContext = createContext<GeminiContextValue | null>(null);

export function GeminiProvider({ children, broadcastMode }: { children: ReactNode; broadcastMode?: boolean }) {
  const gemini = useGeminiLive(broadcastMode ? { broadcastMode: true } : undefined);

  const value: GeminiContextValue = {
    // Connection + video
    connectionStatus: gemini.connectionStatus,
    currentVideo: gemini.currentVideo,
    isGeminiSpeaking: gemini.isGeminiSpeaking,
    isMicActive: gemini.isMicActive,
    uiMode: gemini.uiMode,
    transcript: gemini.transcript,
    skipCount: gemini.skipCount,
    connect: gemini.connect,
    disconnect: gemini.disconnect,
    skip: gemini.skip,
    toggleMic: gemini.toggleMic,
    // Content mode + music
    contentMode: gemini.contentMode,
    music: gemini.music,
    skipTrack: gemini.skipTrack,
    playTrack: gemini.playTrack,
    pauseTrack: gemini.pauseTrack,
    onTrackEnd: gemini.onTrackEnd,
    isMusicPlaying: gemini.isMusicPlaying,
    musicVolume: gemini.musicVolume,
    setMusicVolume: gemini.setMusicVolume,
    setCurrentTrack: gemini.setCurrentTrack,
    setUiMode: gemini.setUiMode,
    setContentMode: gemini.setContentMode,
    setIsMusicPlaying: gemini.setIsMusicPlaying,
    trackCurrentTime: gemini.trackCurrentTime,
    trackDuration: gemini.trackDuration,
    audioRef: gemini.audioRef,
    currentTrack: gemini.music?.currentTrack ?? null,
    sendChat: gemini.sendChat,
    onVideoEnd: gemini.onVideoEnd,
    toolBusy: gemini.toolBusy,
    museSettings: gemini.museSettings,
    updateSettings: gemini.updateSettings,
    applySettings: gemini.applySettings,
    videoQueue: gemini.videoQueue,
    nextVideo: gemini.nextVideo,
    voiceEffects: gemini.voiceEffects,
    updateVoiceEffects: gemini.updateVoiceEffects,
  };

  return (
    <GeminiContext.Provider value={value}>
      {children}
    </GeminiContext.Provider>
  );
}

export function useGemini(): GeminiContextValue {
  const ctx = useContext(GeminiContext);
  if (!ctx) {
    throw new Error("useGemini must be used within a GeminiProvider");
  }
  return ctx;
}
