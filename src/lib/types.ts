// ============================================================
// Gemini Multimodal Live API — Client-side type definitions
// Protocol: WebSocket with JSON messages + base64 audio chunks
// ============================================================

/** Connection states for the WebSocket bridge */
export type ConnectionStatus =
  | "disconnected"
  | "connecting"
  | "connected"
  | "ready"    // setup message sent, Gemini acknowledged
  | "error";

/** Gemini tool declarations sent during setup */
export interface ToolDeclaration {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

/** Tool call from Gemini — arrives in serverContent messages */
export interface GeminiToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

/** Tool response we send back after executing a tool call */
export interface ToolResponse {
  id: string;
  name: string;
  response: Record<string, unknown>;
}

// ------------------------------------------------------------------
// Client → Server messages (sent over WebSocket to backend proxy)
// ------------------------------------------------------------------

/** Current playback state sent on reconnect so Muse knows what's already playing */
export interface PlaybackContext {
  contentMode: ContentMode;
  videoUrl?: string;
  videoTitle?: string;
  trackId?: number;
  trackTitle?: string;
  trackArtist?: string;
  // QW4: conversation memory
  recentTranscript?: string[];
  recentHistory?: string[];
}

/** Initial setup message — tools + optional settings; system prompt is server-side */
export interface SetupMessage {
  type: "setup";
  tools: ToolDeclaration[];
  settings?: Partial<MuseSettings>;
  playbackContext?: PlaybackContext;
}

/** Continuous audio stream from microphone */
export interface AudioInputMessage {
  type: "audio";
  data: string; // base64-encoded PCM16, 16kHz mono
}

/** Text injection (used for skip commands, etc.) */
export interface TextInputMessage {
  type: "text";
  text: string;
}

/** Cancel/interrupt Gemini's current response */
export interface InterruptMessage {
  type: "interrupt";
}

/** Tool call response back to Gemini */
export interface ToolResponseMessage {
  type: "toolResponse";
  toolResponse: ToolResponse;
}

export type ClientMessage =
  | SetupMessage
  | AudioInputMessage
  | TextInputMessage
  | InterruptMessage
  | ToolResponseMessage;

// ------------------------------------------------------------------
// Server → Client messages (received from backend proxy)
// ------------------------------------------------------------------

/** Setup acknowledgement */
export interface SetupCompleteMessage {
  type: "setupComplete";
}

/** Gemini audio response chunk */
export interface AudioOutputMessage {
  type: "audio";
  data: string; // base64-encoded PCM16
}

/** Gemini text response */
export interface TextOutputMessage {
  type: "text";
  text: string;
}

/** Gemini is calling a tool */
export interface ToolCallMessage {
  type: "toolCall";
  toolCall: GeminiToolCall;
}

/** Gemini finished its current turn */
export interface TurnCompleteMessage {
  type: "turnComplete";
}

/** Gemini was interrupted (barge-in acknowledged) */
export interface InterruptedMessage {
  type: "interrupted";
}

/** Error from server/Gemini */
export interface ErrorMessage {
  type: "error";
  error: string;
  code?: string;
}

/** Emitted after update_user_profile executes; client can display updated profile */
export interface ProfileUpdateMessage {
  type: "profileUpdate";
  interest_tags: string[];
}

/** Session resume handle from Gemini — store and send on reconnect */
export interface SessionResumeHandleMessage {
  type: "sessionResumeHandle";
  handle: string;
}

/** Gemini is about to kill the session — reconnect immediately */
export interface GoAwayMessage {
  type: "goAway";
}

/** Server is executing a slow tool — show a status indicator */
export interface ToolStatusMessage {
  type: "toolStatus";
  tool: string;
  status: "executing" | "done";
  message?: string;
}

export type ServerMessage =
  | SetupCompleteMessage
  | AudioOutputMessage
  | TextOutputMessage
  | ToolCallMessage
  | TurnCompleteMessage
  | InterruptedMessage
  | ErrorMessage
  | ProfileUpdateMessage
  | TrackUpdateMessage
  | TrackSkippedMessage
  | QueueUpdateMessage
  | VideoUpdateMessage
  | VideosDiscoveredMessage
  | InputTranscriptMessage
  | OutputTranscriptMessage
  | VideoSkipAckMessage
  | VideoQueueUpdateMessage
  | SessionResumeHandleMessage
  | GoAwayMessage
  | ToolStatusMessage;

// ------------------------------------------------------------------
// Application state types
// ------------------------------------------------------------------

export interface VideoMeta {
  url: string;
  title?: string;
  channel?: string;
  pacing?: number;       // 1-10
  stimulation?: number;  // 1-10
  novelty?: number;      // 1-10
  vibe?: string;
  duration?: number;     // seconds
  source?: 'youtube' | 'direct' | 'unknown';
}

export type UIMode = "fullscreen" | "split" | "overlay" | "music" | "library" | "driving";

export interface PlayState {
  connectionStatus: ConnectionStatus;
  currentVideo: VideoMeta | null;
  isGeminiSpeaking: boolean;
  isMicActive: boolean;
  uiMode: UIMode;
  transcript: string[];   // rolling last N lines of Gemini text
  skipCount: number;
  contentMode: ContentMode;
  music: MusicState | null;
}

// ------------------------------------------------------------------
// Music types
// ------------------------------------------------------------------

export interface TrackMeta {
  id: number;
  url?: string;
  title: string;
  artist?: string;
  album?: string;
  duration_sec?: number;
  artwork_url?: string;
  hls_url: string;
  energy?: number;
  vibe?: string;
  mood_tags?: string[];
  genre_tags?: string[];
  source?: 'upload' | 'jamendo' | 'openverse' | 'incompetech' | 'ccmixter';
}

export interface QueueItem {
  track: TrackMeta;
  position: number;
}

export type ContentMode = "video" | "music" | "idle";

export interface MusicState {
  currentTrack: TrackMeta | null;
  isPlaying: boolean;
  queue: QueueItem[];
  volume: number;
  quality: '128k' | '64k' | 'auto';
  contentMode: ContentMode;
}

// New server → client messages for music

export interface TrackUpdateMessage {
  type: "trackUpdate";
  track: TrackMeta;
}

export interface TrackSkippedMessage {
  type: "trackSkipped";
}

export interface QueueUpdateMessage {
  type: "queueUpdate";
  action: "add" | "remove";
  track?: TrackMeta;
  position?: "next" | "end";
}

export interface VideoUpdateMessage {
  type: "videoUpdate";
  video: VideoMeta;
}

export interface VideosDiscoveredMessage {
  type: "videosDiscovered";
  discovery: {
    discovered: number;
    videos: VideoMeta[];
    channels_checked?: string[];
    message?: string;
  };
}

export interface InputTranscriptMessage {
  type: "inputTranscript";
  text: string;
}

export interface OutputTranscriptMessage {
  type: "outputTranscript";
  text: string;
}

export interface VideoSkipAckMessage {
  type: "skipAck";
  skip: Record<string, unknown>;
}

export interface VideoQueueItem {
  video: VideoMeta;
  position: number;
}

export interface VideoQueueUpdateMessage {
  type: "videoQueueUpdate";
  action: "add" | "clear";
  video?: VideoMeta;
  position?: "next" | "end";
}

// ------------------------------------------------------------------
// Muse settings (user-configurable)
// ------------------------------------------------------------------

export type GeminiVoice = "Charon" | "Puck" | "Kore" | "Fenrir" | "Aoede" | "Leda" | "Orus" | "Zephyr";

export type SpeechSensitivity = "START_SENSITIVITY_LOW" | "START_SENSITIVITY_MEDIUM" | "START_SENSITIVITY_HIGH";
export type EndSensitivity = "END_SENSITIVITY_LOW" | "END_SENSITIVITY_MEDIUM" | "END_SENSITIVITY_HIGH";

export interface MuseSettings {
  voice: GeminiVoice;
  language: string;
  startSensitivity: SpeechSensitivity;
  endSensitivity: EndSensitivity;
  silenceDurationMs: number;
  prefixPaddingMs: number;
  contextWindowTokens: number;
  maxTranscriptLines: number;
  voiceEffects: VoiceEffectsConfig;
}

export type VoiceEffectPreset = "clean" | "radio" | "cathedral" | "warm" | "robot";

export type ReverbSize = "small" | "medium" | "large" | "hall";

export interface VoiceEffectsConfig {
  preset: VoiceEffectPreset;
  bypass: boolean;
  eqLow: number;      // dB, -12 to +12
  eqMid: number;      // dB, -12 to +12
  eqHigh: number;     // dB, -12 to +12
  reverbMix: number;  // 0 to 1
  reverbSize: ReverbSize;
  delayTime: number;  // seconds, 0 to 1
  delayFeedback: number; // 0 to 0.8
  compressionThreshold: number; // dB, -60 to 0
  compressionRatio: number;     // 1 to 20
  masterGain: number; // 0 to 2
  pitchShift: number; // cents, -600 to +600 (100 = 1 semitone)
}

// Upload types

export interface UploadProgress {
  filename: string;
  progress: number; // 0-100
  status: 'uploading' | 'extracting' | 'transcoding' | 'ready' | 'error';
  trackId?: number;
  error?: string;
  isZip?: boolean;
}
