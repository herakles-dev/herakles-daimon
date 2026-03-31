"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { GeminiWebSocket } from "@/lib/gemini-ws";
import type {
  ConnectionStatus,
  ContentMode,
  MusicState,
  PlaybackContext,
  QueueItem,
  ServerMessage,
  TrackMeta,
  UIMode,
  VideoMeta,
  VideoQueueItem,
  PlayState,
} from "@/lib/types";
import { MAX_TRANSCRIPT_LINES, DEFAULT_MUSE_SETTINGS, DEFAULT_VOICE_EFFECTS } from "@/lib/constants";
import type { MuseSettings, VoiceEffectsConfig } from "@/lib/types";
import { useAudioCapture } from "./useAudioCapture";
import { useAudioPlayback } from "./useAudioPlayback";

const SETTINGS_STORAGE_KEY = "muse-settings";

const VALID_VOICES = new Set(["Charon", "Puck", "Kore", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"]);
const VALID_PRESETS = new Set(["clean", "radio", "cathedral", "warm", "robot"]);
const VALID_REVERB_SIZES = new Set(["small", "medium", "large", "hall"]);

function sanitizeVoiceEffects(raw: unknown): VoiceEffectsConfig {
  const d = DEFAULT_VOICE_EFFECTS;
  if (!raw || typeof raw !== "object") return { ...d };
  const r = raw as Record<string, unknown>;
  // num() treats 0 as a valid value — Number(v) || fallback would silently
  // replace 0 with the default (e.g. compressionThreshold: 0 → -24).
  const num = (v: unknown, fallback: number) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  };
  return {
    preset: VALID_PRESETS.has(r.preset as string) ? (r.preset as VoiceEffectsConfig["preset"]) : d.preset,
    bypass: typeof r.bypass === "boolean" ? r.bypass : d.bypass,
    eqLow: Math.max(-12, Math.min(12, num(r.eqLow, d.eqLow))),
    eqMid: Math.max(-12, Math.min(12, num(r.eqMid, d.eqMid))),
    eqHigh: Math.max(-12, Math.min(12, num(r.eqHigh, d.eqHigh))),
    reverbMix: Math.max(0, Math.min(1, num(r.reverbMix, d.reverbMix))),
    reverbSize: VALID_REVERB_SIZES.has(r.reverbSize as string) ? (r.reverbSize as VoiceEffectsConfig["reverbSize"]) : d.reverbSize,
    delayTime: Math.max(0, Math.min(1, num(r.delayTime, d.delayTime))),
    delayFeedback: Math.max(0, Math.min(0.8, num(r.delayFeedback, d.delayFeedback))),
    compressionThreshold: Math.max(-60, Math.min(0, num(r.compressionThreshold, d.compressionThreshold))),
    compressionRatio: Math.max(1, Math.min(20, num(r.compressionRatio, d.compressionRatio))),
    masterGain: Math.max(0, Math.min(2, num(r.masterGain, d.masterGain))),
    pitchShift: Math.max(-600, Math.min(600, num(r.pitchShift, d.pitchShift))),
  };
}

function sanitizeSettings(raw: Record<string, unknown>): MuseSettings {
  const d = DEFAULT_MUSE_SETTINGS;
  return {
    ...d,
    ...raw,
    voice: VALID_VOICES.has(raw.voice as string) ? (raw.voice as MuseSettings["voice"]) : d.voice,
    silenceDurationMs: Math.max(200, Math.min(2000, Number(raw.silenceDurationMs) || d.silenceDurationMs)),
    prefixPaddingMs: Math.max(0, Math.min(1000, Number(raw.prefixPaddingMs) || d.prefixPaddingMs)),
    contextWindowTokens: Math.max(10000, Math.min(100000, Number(raw.contextWindowTokens) || d.contextWindowTokens)),
    maxTranscriptLines: Math.max(5, Math.min(50, Number(raw.maxTranscriptLines) || d.maxTranscriptLines)),
    voiceEffects: sanitizeVoiceEffects(raw.voiceEffects),
  };
}

function loadSettings(): MuseSettings {
  if (typeof window === "undefined") return { ...DEFAULT_MUSE_SETTINGS };
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (raw) return sanitizeSettings(JSON.parse(raw));
  } catch { /* ignore corrupt data */ }
  return { ...DEFAULT_MUSE_SETTINGS };
}

function saveSettings(s: MuseSettings): void {
  try { localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(s)); } catch { /* quota */ }
}

interface UseGeminiLiveReturn extends PlayState {
  /** Connect to Gemini and start the session */
  connect: () => Promise<void>;
  /** Disconnect from Gemini */
  disconnect: () => void;
  /** Skip the current video (barge-in + request new content) */
  skip: () => void;
  /** Skip the current track (barge-in + request new track) */
  skipTrack: () => void;
  /** Resume playback of the current track */
  playTrack: () => void;
  /** Pause playback of the current track */
  pauseTrack: () => void;
  /** Called by MusicPlayer when the audio element fires onEnded */
  onTrackEnd: () => void;
  /** Toggle microphone mute */
  toggleMic: () => void;
  /** Current music playback state */
  isMusicPlaying: boolean;
  /** Master volume for music (0–1) */
  musicVolume: number;
  /** Set master volume for music (0–1) */
  setMusicVolume: (volume: number) => void;
  /** Set the current track directly (user-initiated library selection) */
  setCurrentTrack: (track: TrackMeta | null) => void;
  /** Set the UI mode directly (e.g. exit driving mode) */
  setUiMode: (mode: UIMode) => void;
  /** Set the content mode directly */
  setContentMode: (mode: ContentMode) => void;
  /** Set music playing state directly */
  setIsMusicPlaying: (playing: boolean) => void;
  /** Current track position in seconds (for NowPlayingBar / DrivingMode) */
  trackCurrentTime: number;
  /** Total track duration in seconds */
  trackDuration: number;
  /** Expose the audio element so MusicPlayer can sync currentTime/duration */
  audioRef: React.RefObject<HTMLAudioElement | null>;
  /** Send a text message to Gemini (typed chat). Returns false if not connected. */
  sendChat: (text: string) => boolean;
  /** Called when a YouTube video ends naturally or is detected as unplayable */
  onVideoEnd: (reason?: "ended" | "stalled" | "error") => void;
  /** Non-null when Muse is executing a slow tool (e.g. "Searching YouTube...") */
  toolBusy: string | null;
  /** Current Muse settings */
  museSettings: MuseSettings;
  /** Update settings (persists to localStorage, does NOT reconnect) */
  updateSettings: (patch: Partial<MuseSettings>) => void;
  /** Apply settings and reconnect the Gemini session */
  applySettings: (patch: Partial<MuseSettings>) => Promise<void>;
  /** Video queue for QW3 — videos stacked ahead by Muse */
  videoQueue: VideoQueueItem[];
  /** Next video in the queue (convenience) */
  nextVideo: VideoMeta | null;
  /** Current voice effects config */
  voiceEffects: VoiceEffectsConfig;
  /** Update voice effects instantly — no reconnect, client-side only */
  updateVoiceEffects: (config: VoiceEffectsConfig) => void;
}

/**
 * Main hook integrating:
 * - GeminiWebSocket (connection + messaging)
 * - Audio capture (mic → PCM16 → Gemini)
 * - Audio playback (Gemini voice → speakers)
 * - Tool call execution (fetch_video, skip_video, fetch_track, skip_track, change_ui_state)
 * - Barge-in / interrupt system
 * - Music state (currentTrack, queue, playback controls)
 */
export function useGeminiLive(options?: { broadcastMode?: boolean }): UseGeminiLiveReturn {
  const broadcastMode = options?.broadcastMode ?? false;
  // ------------------------------------------------------------------
  // Video + connection state (unchanged from original)
  // ------------------------------------------------------------------

  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>("disconnected");
  const [currentVideo, setCurrentVideo] = useState<VideoMeta | null>(null);
  const currentVideoRef = useRef<VideoMeta | null>(null);
  useEffect(() => { currentVideoRef.current = currentVideo; }, [currentVideo]);
  const [isGeminiSpeaking, setIsGeminiSpeaking] = useState(false);
  const [toolBusy, setToolBusy] = useState<string | null>(null);
  const [isMicActive, setIsMicActive] = useState(true);
  const [uiMode, setUiMode] = useState<UIMode>("fullscreen");
  const [transcript, setTranscript] = useState<string[]>([]);
  const [skipCount, setSkipCount] = useState(0);

  // Video update guard — prevents Muse from auto-playing over a video on reconnect.
  // true = accept videoUpdate messages (normal operation)
  // false = reconnect grace period, reject unsolicited videoUpdate
  // Unlocks on: first turnComplete after reconnect, user skip, video end, switch to music
  const videoAcceptingUpdateRef = useRef(true);


  // ------------------------------------------------------------------
  // Music state
  // ------------------------------------------------------------------

  // Accumulate streaming transcription fragments into full sentences
  const pendingOutputTranscript = useRef<string>("");
  const pendingInputTranscript = useRef<string>("");

  const [contentMode, setContentMode] = useState<ContentMode>("idle");
  const contentModeRef = useRef<ContentMode>("idle");
  useEffect(() => { contentModeRef.current = contentMode; }, [contentMode]);
  const [currentTrack, setCurrentTrack] = useState<TrackMeta | null>(null);
  const currentTrackRef = useRef<TrackMeta | null>(null);
  useEffect(() => { currentTrackRef.current = currentTrack; }, [currentTrack]);
  const [trackQueue, setTrackQueue] = useState<QueueItem[]>([]);
  const trackQueueRef = useRef<QueueItem[]>([]);
  const [videoQueue, setVideoQueue] = useState<VideoQueueItem[]>([]);
  const videoQueueRef = useRef<VideoQueueItem[]>([]);
  const [isMusicPlaying, setIsMusicPlaying] = useState(false);
  const [musicVolume, setMusicVolume] = useState(1.0);
  const [trackCurrentTime, setTrackCurrentTime] = useState(0);
  const [trackDuration, setTrackDuration] = useState(0);

  // Sync helper: keeps trackQueueRef in step with trackQueue state so that
  // stale-closure callbacks (onTrackEnd) can always read the latest queue.
  const updateTrackQueue = useCallback(
    (updater: (prev: QueueItem[]) => QueueItem[]) => {
      setTrackQueue((prev) => {
        const next = updater(prev);
        trackQueueRef.current = next;
        return next;
      });
    },
    []
  );

  // Sync helper: keeps videoQueueRef in step with videoQueue state.
  const updateVideoQueue = useCallback(
    (updater: (prev: VideoQueueItem[]) => VideoQueueItem[]) => {
      setVideoQueue((prev) => {
        const next = updater(prev);
        videoQueueRef.current = next;
        return next;
      });
    },
    []
  );

  // ------------------------------------------------------------------
  // Muse settings (persisted to localStorage)
  // ------------------------------------------------------------------
  const [museSettings, setMuseSettings] = useState<MuseSettings>(loadSettings);

  const updateSettings = useCallback((patch: Partial<MuseSettings>) => {
    setMuseSettings((prev) => {
      const next = { ...prev, ...patch };
      saveSettings(next);
      return next;
    });
  }, []);

  // Exposed so the page can pass it down to MusicPlayer / NowPlayingBar
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const wsRef = useRef<GeminiWebSocket | null>(null);

  // ------------------------------------------------------------------
  // Audio playback for Gemini's voice
  // ------------------------------------------------------------------

  const { enqueue: enqueueAudio, flush: flushAudio, isPlaying, updateEffects } =
    useAudioPlayback(museSettings.voiceEffects);

  // Sync speaking state with audio playback
  useEffect(() => {
    setIsGeminiSpeaking(isPlaying);
  }, [isPlaying]);

  // Sync voice effects config to the playback chain whenever settings change.
  // Covers both slider drags (updateSettings) and full loads (applySettings).
  // No reconnect needed — Web Audio params update in real time.
  useEffect(() => {
    updateEffects(museSettings.voiceEffects);
  }, [museSettings.voiceEffects, updateEffects]);

  // Audio capture — sends mic data to Gemini
  const handleAudioChunk = useCallback(
    (base64: string) => {
      wsRef.current?.sendAudio(base64);
    },
    []
  );

  const {
    startCapture,
    stopCapture,
    isCapturing,
    error: micError,
  } = useAudioCapture({
    onAudioChunk: handleAudioChunk,
    enabled: isMicActive && connectionStatus === "ready",
  });

  // Surface mic errors so they're visible in console
  useEffect(() => {
    if (micError) console.error("[Mic]", micError);
    console.log("[Mic] isCapturing:", isCapturing, "enabled:", isMicActive && connectionStatus === "ready");
  }, [micError, isCapturing, isMicActive, connectionStatus]);

  // ------------------------------------------------------------------
  // Tool call execution — client-side only (non-SERVER_SIDE_TOOLS)
  // All video/music tools are intercepted server-side and never reach
  // the client as toolCall messages. Only change_ui_state is client-side.
  // ------------------------------------------------------------------

  const executeChangeUI = useCallback((args: Record<string, unknown>) => {
    const mode = args.mode as UIMode;
    if (["fullscreen", "split", "overlay", "music", "library", "driving"].includes(mode)) {
      setUiMode(mode);
      return { success: true, mode };
    }
    return { success: false, error: `Invalid mode: ${mode}` };
  }, []);

  // ------------------------------------------------------------------
  // Message handler
  // ------------------------------------------------------------------

  const handleMessage = useCallback(
    (msg: ServerMessage) => {
      switch (msg.type) {
        case "audio":
          enqueueAudio(msg.data);
          break;

        case "text":
          setTranscript((prev) => {
            const next = [...prev, msg.text];
            return next.slice(-(museSettingsRef.current?.maxTranscriptLines ?? MAX_TRANSCRIPT_LINES));
          });
          break;

        case "toolCall": {
          // Only non-SERVER_SIDE_TOOLS reach the client as toolCall messages.
          // fetch_video, skip_video, fetch_track, skip_track, queue_track,
          // discover_videos, etc. are all intercepted server-side.
          const { id, name, args } = msg.toolCall;
          let response: Record<string, unknown>;

          switch (name) {
            case "change_ui_state":
              response = executeChangeUI(args);
              break;
            default:
              response = { error: `Unknown tool: ${name}` };
          }

          // Send tool response back to Gemini
          wsRef.current?.sendToolResponse({ id, name, response });
          break;
        }

        case "trackUpdate": {
          const track = msg.track as TrackMeta;
          setCurrentTrack(track);
          setContentMode("music");
          // Switching to music unlocks video updates for when user goes back
          videoAcceptingUpdateRef.current = true;
          setTrackCurrentTime(0);
          setTrackDuration(track.duration_sec ?? 0);
          // Only auto-play if the track has a playable URL (not a sentinel)
          if (track.hls_url) {
            setIsMusicPlaying(true);
            setTranscript((prev) =>
              [...prev, `Now playing: ${track.title}${track.artist ? ` — ${track.artist}` : ""}`].slice(-(museSettingsRef.current?.maxTranscriptLines ?? MAX_TRANSCRIPT_LINES))
            );
          } else {
            setIsMusicPlaying(false);
          }
          break;
        }

        case "trackSkipped":
          setIsMusicPlaying(false);
          setSkipCount((n) => n + 1);
          break;

        case "videoUpdate":
          // Guard: reject unsolicited videoUpdate during reconnect grace period
          if (!videoAcceptingUpdateRef.current) {
            console.log("[GeminiLive] Rejected videoUpdate — reconnect grace period");
            break;
          }
          setCurrentVideo(msg.video);
          setContentMode("video");
          setIsMusicPlaying(false);
          setTranscript((prev) =>
            [...prev, `Loading video: ${msg.video?.title || "..."}`].slice(-(museSettingsRef.current?.maxTranscriptLines ?? MAX_TRANSCRIPT_LINES))
          );
          break;

        case "skipAck":
          setSkipCount((n) => n + 1);
          break;

        case "videoQueueUpdate":
          if (msg.action === "add" && msg.video) {
            updateVideoQueue((prev) => {
              const item: VideoQueueItem = { video: msg.video!, position: prev.length };
              return msg.position === "next" ? [item, ...prev] : [...prev, item];
            });
          } else if (msg.action === "clear") {
            updateVideoQueue(() => []);
          }
          break;

        case "profileUpdate":
          // Profile updated — could store interest_tags if needed
          break;

        case "queueUpdate":
          if (msg.action === "add" && msg.track) {
            updateTrackQueue((prev) => {
              const item: QueueItem = { track: msg.track!, position: prev.length };
              return msg.position === "next" ? [item, ...prev] : [...prev, item];
            });
          }
          break;

        case "videosDiscovered":
          // Videos discovered from subscribed channels
          if (msg.discovery?.videos?.length) {
            setTranscript((prev) =>
              [...prev, `Discovered ${msg.discovery.videos.length} video(s)`].slice(-(museSettingsRef.current?.maxTranscriptLines ?? MAX_TRANSCRIPT_LINES))
            );
          }
          break;

        case "inputTranscript":
          // Accumulate user speech fragments — each fragment is a word/phrase
          if (msg.text) {
            const cur = pendingInputTranscript.current;
            pendingInputTranscript.current = cur ? cur + " " + msg.text : msg.text;
          }
          break;

        case "outputTranscript":
          // Accumulate Muse speech fragments
          if (msg.text) {
            const cur = pendingOutputTranscript.current;
            pendingOutputTranscript.current = cur ? cur + " " + msg.text : msg.text;
          }
          break;

        case "toolStatus":
          if (msg.status === "executing" && msg.message) {
            setToolBusy(msg.message);
            setTranscript((prev) =>
              [...prev, `[${msg.message}]`].slice(-(museSettingsRef.current?.maxTranscriptLines ?? MAX_TRANSCRIPT_LINES))
            );
          } else {
            setToolBusy(null);
          }
          break;

        case "turnComplete": {
          setToolBusy(null);
          // Unlock video guard after Muse's first turn (reconnect greeting done)
          if (!videoAcceptingUpdateRef.current) {
            videoAcceptingUpdateRef.current = true;
          }
          // Flush accumulated transcripts as complete lines
          const userText = pendingInputTranscript.current.trim();
          const museText = pendingOutputTranscript.current.trim();
          if (userText || museText) {
            setTranscript((prev) => {
              const next = [...prev];
              if (userText) next.push(`You: ${userText}`);
              const hostLabel = broadcastMode ? "Daimon" : "Muse";
              if (museText) next.push(`${hostLabel}: ${museText}`);
              return next.slice(-(museSettingsRef.current?.maxTranscriptLines ?? MAX_TRANSCRIPT_LINES));
            });
          }
          pendingInputTranscript.current = "";
          pendingOutputTranscript.current = "";
          break;
        }

        case "interrupted":
          flushAudio();
          break;

        case "error":
          console.error("[GeminiLive] Error:", msg.error, msg.code);
          setTranscript((prev) => [
            ...prev.slice(-(museSettingsRef.current?.maxTranscriptLines ?? MAX_TRANSCRIPT_LINES) + 1),
            `[Error: ${msg.error}]`,
          ]);
          break;
      }
    },
    [
      enqueueAudio,
      flushAudio,
      executeChangeUI,
      updateTrackQueue,
      updateVideoQueue,
    ]
  );

  // ------------------------------------------------------------------
  // Connection lifecycle
  // ------------------------------------------------------------------

  // Stable ref for handleMessage so the WebSocket always calls the latest version
  const handleMessageRef = useRef(handleMessage);
  useEffect(() => { handleMessageRef.current = handleMessage; }, [handleMessage]);

  // Keep a ref to museSettings so connect/reconnect always reads the latest
  const museSettingsRef = useRef(museSettings);
  useEffect(() => { museSettingsRef.current = museSettings; }, [museSettings]);

  /** Build current playback context for the WS setup message (pure — no side effects). */
  const buildPlaybackContext = useCallback((): PlaybackContext => {
    const mode = contentModeRef.current;
    const video = currentVideoRef.current;
    const track = currentTrackRef.current;
    // QW4: include recent transcript for reconnect context
    let recentTranscript: string[] | undefined;
    let recentHistory: string[] | undefined;
    try {
      const saved = sessionStorage.getItem("muse-memory");
      if (saved) {
        const mem = JSON.parse(saved);
        recentTranscript = mem.transcript;
        recentHistory = mem.history;
      }
    } catch {}
    return {
      contentMode: mode,
      videoUrl: video?.url,
      videoTitle: video?.title,
      trackId: track?.id,
      trackTitle: track?.title,
      trackArtist: track?.artist,
      recentTranscript,
      recentHistory,
    };
  }, []);

  const connect = useCallback(async () => {
    if (wsRef.current) return;

    const ws = new GeminiWebSocket();
    ws.setSettings(museSettingsRef.current);
    ws.setPlaybackContextProvider(buildPlaybackContext);
    if (broadcastMode) ws.setBroadcastMode(true);
    wsRef.current = ws;

    ws.onStatus((status) => {
      setConnectionStatus(status);
      // Lock the video guard on reconnect if a video is playing
      if (status === "connected" && contentModeRef.current === "video" && currentVideoRef.current?.url) {
        videoAcceptingUpdateRef.current = false;
      }
    });
    ws.onMessage((msg) => handleMessageRef.current(msg));
    ws.connect();

    // Start mic capture (needs user gesture — this function is called from a click)
    // In broadcast mode, skip mic capture (no local user)
    if (!broadcastMode) {
      await startCapture();
    }
  }, [startCapture, buildPlaybackContext, broadcastMode]);

  const disconnect = useCallback(() => {
    wsRef.current?.disconnect();
    wsRef.current = null;
    stopCapture();
    flushAudio();
    setConnectionStatus("disconnected");
  }, [stopCapture, flushAudio]);

  /** Apply new settings and reconnect the Gemini session */
  const applySettings = useCallback(async (patch: Partial<MuseSettings>) => {
    // Compute merged settings before state update (ref is still pre-patch)
    const merged = { ...museSettingsRef.current, ...patch };
    updateSettings(patch);
    // Reconnect so the backend picks up the new config
    if (wsRef.current) {
      wsRef.current.disconnect();
      wsRef.current = null;
      stopCapture();
      flushAudio();
      setConnectionStatus("disconnected");
      // Small delay to let the old session tear down
      await new Promise((r) => setTimeout(r, 300));
      const ws = new GeminiWebSocket();
      ws.setSettings(merged);
      ws.setPlaybackContextProvider(buildPlaybackContext);
      if (broadcastMode) ws.setBroadcastMode(true);
      wsRef.current = ws;
      ws.onStatus((status) => {
        setConnectionStatus(status);
        if (status === "connected" && contentModeRef.current === "video" && currentVideoRef.current?.url) {
          videoAcceptingUpdateRef.current = false;
        }
      });
      ws.onMessage((msg) => handleMessageRef.current(msg));
      ws.connect();
      await startCapture();
    }
  }, [updateSettings, stopCapture, flushAudio, startCapture, buildPlaybackContext]);

  // ------------------------------------------------------------------
  // Voice effects — client-side only, no reconnect needed
  // ------------------------------------------------------------------

  const updateVoiceEffects = useCallback((config: VoiceEffectsConfig) => {
    // Sanitize before persisting — guards against extreme values (e.g. masterGain: 999)
    // reaching the GainNode directly via updateEffects.
    const sanitized = sanitizeVoiceEffects(config);
    // Apply immediately to the audio chain (synchronous — no React render lag)
    updateEffects(sanitized);
    // Persist into settings state + localStorage
    updateSettings({ voiceEffects: sanitized });
  }, [updateSettings, updateEffects]);

  // ------------------------------------------------------------------
  // Skip video / barge-in
  // ------------------------------------------------------------------

  const skip = useCallback(() => {
    if (!wsRef.current) return;

    // Unlock video updates so the next fetch_video result is accepted
    videoAcceptingUpdateRef.current = true;

    // 1. Interrupt Gemini's current audio (barge-in)
    flushAudio();
    wsRef.current.sendInterrupt();

    // 2. Inject a text command so Gemini knows to serve new content
    wsRef.current.sendText(
      "User skipped this video. Serve something different. Call fetch_video with adjusted tags."
    );
  }, [flushAudio]);

  // ------------------------------------------------------------------
  // Video ended naturally (YouTube onStateChange → ENDED)
  // ------------------------------------------------------------------

  const onVideoEnd = useCallback((reason: "ended" | "stalled" | "error" = "ended") => {
    // Unlock video updates so the next video can load
    videoAcceptingUpdateRef.current = true;
    // Check video queue first — dequeue next video if available
    const queue = videoQueueRef.current;
    if (queue.length > 0) {
      const [next, ...rest] = queue;
      updateVideoQueue(() => rest.map((item, i) => ({ ...item, position: i })));
      setCurrentVideo(next.video);
      setContentMode("video");
      return;
    }
    // Ask Gemini to pick the next video — tell it WHY this one ended
    if (wsRef.current?.getStatus() === "ready") {
      if (reason === "stalled" || reason === "error") {
        wsRef.current.sendText(
          "That video wasn't playable — it stalled or failed to load. Move on immediately. " +
          "Call discover_videos or fetch_video for the next one. Don't dwell on it."
        );
      } else {
        wsRef.current.sendText(
          "That video just ended. What should we watch next? Call fetch_video or discover_videos."
        );
      }
    }
  }, [updateVideoQueue]);

  // ------------------------------------------------------------------
  // Skip track / barge-in
  // ------------------------------------------------------------------

  const skipTrack = useCallback(() => {
    if (!wsRef.current) return;

    flushAudio();
    wsRef.current.sendInterrupt();
    wsRef.current.sendText(
      "User skipped this track. Serve a different one. Call fetch_track with adjusted mood tags."
    );
  }, [flushAudio]);

  // ------------------------------------------------------------------
  // Music playback controls
  // ------------------------------------------------------------------

  const playTrack = useCallback(() => setIsMusicPlaying(true), []);
  const pauseTrack = useCallback(() => setIsMusicPlaying(false), []);

  const onTrackEnd = useCallback(() => {
    // Read from ref to avoid stale-closure issue with trackQueue state
    const queue = trackQueueRef.current;
    if (queue.length > 0) {
      // Dequeue the next track and promote it
      const [next, ...rest] = queue;
      setCurrentTrack(next.track);
      updateTrackQueue(() => rest.map((item, i) => ({ ...item, position: i })));
      setTrackCurrentTime(0);
      setTrackDuration(next.track.duration_sec ?? 0);
      setIsMusicPlaying(true);
    } else if (wsRef.current?.getStatus() === "ready") {
      // Ask Gemini to pick the next track automatically
      wsRef.current.sendText(
        "That track just ended. What should we play next? Call fetch_track."
      );
    } else {
      // WS dead (screen off, backgrounded) — REST fallback for continuous play
      const currentId = currentTrackRef.current?.id;
      const tags = currentTrackRef.current?.mood_tags;
      const energy = currentTrackRef.current?.energy ?? 5;
      const mood = tags?.length ? tags.join(",") : "chill";
      const params = new URLSearchParams({ mood, energy: String(energy) });
      if (currentId) params.set("current_track_id", String(currentId));

      fetch(`/api/tracks/next?${params}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((track) => {
          if (track?.hls_url) {
            setCurrentTrack(track as TrackMeta);
            setTrackCurrentTime(0);
            setTrackDuration(track.duration_sec ?? 0);
            setIsMusicPlaying(true);
          }
        })
        .catch(() => {
          // Truly offline — nothing to play
        });
    }
  }, [updateTrackQueue]);

  // ------------------------------------------------------------------
  // Mic toggle
  // ------------------------------------------------------------------

  const toggleMic = useCallback(() => {
    setIsMicActive((prev) => !prev);
  }, []);

  // ------------------------------------------------------------------
  // Text chat (user-typed messages)
  // ------------------------------------------------------------------

  // Use ref to avoid recreating sendChat on every status change
  const connectionStatusRef = useRef(connectionStatus);
  useEffect(() => { connectionStatusRef.current = connectionStatus; }, [connectionStatus]);

  const sendChat = useCallback((text: string): boolean => {
    if (!wsRef.current || !text.trim() || connectionStatusRef.current !== "ready") return false;
    const trimmed = text.trim().slice(0, 2000);
    wsRef.current.sendText(trimmed);
    setTranscript((prev) => [...prev, `You: ${trimmed}`].slice(-(museSettingsRef.current?.maxTranscriptLines ?? MAX_TRANSCRIPT_LINES)));
    return true;
  }, []);

  // QW4: Persist transcript + recent plays to sessionStorage for reconnect memory
  // QW4: Debounced sessionStorage write for reconnect memory (500ms trailing)
  const memoryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (memoryTimerRef.current) clearTimeout(memoryTimerRef.current);
    memoryTimerRef.current = setTimeout(() => {
      try {
        const history: string[] = [];
        if (currentVideo?.title) history.push(`Video: ${currentVideo.title}`);
        if (currentTrack?.title) history.push(`Track: ${currentTrack.title}${currentTrack.artist ? ` by ${currentTrack.artist}` : ""}`);
        sessionStorage.setItem("muse-memory", JSON.stringify({
          transcript: transcript.slice(-10),
          history: history.slice(-5),
        }));
      } catch (e) {
        console.warn("[GeminiLive] sessionStorage write failed:", e);
      }
    }, 500);
    return () => { if (memoryTimerRef.current) clearTimeout(memoryTimerRef.current); };
  }, [transcript, currentVideo, currentTrack]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      wsRef.current?.disconnect();
      wsRef.current = null;
    };
  }, []);

  // ------------------------------------------------------------------
  // Derived MusicState (satisfies PlayState.music shape)
  // ------------------------------------------------------------------

  const music: MusicState = {
    currentTrack,
    isPlaying: isMusicPlaying,
    queue: trackQueue,
    volume: musicVolume,
    quality: "auto",
    contentMode,
  };

  return {
    // Connection + video (unchanged public surface)
    connectionStatus,
    currentVideo,
    isGeminiSpeaking,
    isMicActive,
    uiMode,
    transcript,
    skipCount,
    // Content mode + music state (new)
    contentMode,
    music,
    // Actions — video
    connect,
    disconnect,
    skip,
    toggleMic,
    // Actions — music
    skipTrack,
    playTrack,
    pauseTrack,
    onTrackEnd,
    // Music playback values surfaced separately for convenience
    isMusicPlaying,
    musicVolume,
    setMusicVolume,
    setCurrentTrack,
    setUiMode,
    setContentMode,
    setIsMusicPlaying,
    trackCurrentTime,
    trackDuration,
    audioRef,
    sendChat,
    onVideoEnd,
    toolBusy,
    museSettings,
    updateSettings,
    applySettings,
    videoQueue,
    nextVideo: videoQueue[0]?.video ?? null,
    voiceEffects: museSettings.voiceEffects,
    updateVoiceEffects,
  };
}
