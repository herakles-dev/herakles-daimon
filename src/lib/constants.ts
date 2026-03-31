import type { VoiceEffectsConfig, VoiceEffectPreset } from "./types";

/** WebSocket endpoint for the Gemini proxy backend */
export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8150/ws/gemini";

/** Audio config — Gemini Live API uses 16kHz input, 24kHz output */
export const AUDIO_INPUT_SAMPLE_RATE = 16000;  // mic capture → Gemini
export const AUDIO_OUTPUT_SAMPLE_RATE = 24000;  // Gemini → speakers
export const AUDIO_SAMPLE_RATE = AUDIO_INPUT_SAMPLE_RATE; // alias for capture code
export const AUDIO_CHANNELS = 1;
export const AUDIO_CHUNK_MS = 100; // send audio every 100ms
export const AUDIO_CHUNK_SAMPLES = (AUDIO_INPUT_SAMPLE_RATE * AUDIO_CHUNK_MS) / 1000;

/** Max transcript lines to keep in state */
export const MAX_TRANSCRIPT_LINES = 20;

/** Available Gemini voices */
export const GEMINI_VOICES = [
  { id: "Charon", label: "Charon", description: "Deep, warm, authoritative" },
  { id: "Puck", label: "Puck", description: "Playful, energetic" },
  { id: "Kore", label: "Kore", description: "Clear, youthful" },
  { id: "Fenrir", label: "Fenrir", description: "Low, deliberate" },
  { id: "Aoede", label: "Aoede", description: "Warm, musical" },
  { id: "Leda", label: "Leda", description: "Bright, confident" },
  { id: "Orus", label: "Orus", description: "Deep, smooth" },
  { id: "Zephyr", label: "Zephyr", description: "Light, breezy" },
] as const;

export const DEFAULT_VOICE_EFFECTS: VoiceEffectsConfig = {
  preset: "clean",
  bypass: true,
  eqLow: 0,
  eqMid: 0,
  eqHigh: 0,
  reverbMix: 0,
  reverbSize: "medium",
  delayTime: 0,
  delayFeedback: 0,
  compressionThreshold: -24,
  compressionRatio: 4,
  masterGain: 1,
  pitchShift: 0,
};

export const VOICE_EFFECT_PRESETS: Record<VoiceEffectPreset, Omit<VoiceEffectsConfig, "preset" | "bypass">> = {
  clean: {
    eqLow: 0, eqMid: 0, eqHigh: 0,
    reverbMix: 0, reverbSize: "medium",
    delayTime: 0, delayFeedback: 0,
    compressionThreshold: -24, compressionRatio: 4,
    masterGain: 1, pitchShift: 0,
  },
  radio: {
    eqLow: -3, eqMid: 4, eqHigh: 2,
    reverbMix: 0.05, reverbSize: "small",
    delayTime: 0, delayFeedback: 0,
    compressionThreshold: -20, compressionRatio: 6,
    masterGain: 1.2, pitchShift: 0,
  },
  cathedral: {
    eqLow: 2, eqMid: -1, eqHigh: -2,
    reverbMix: 0.6, reverbSize: "hall",
    delayTime: 0.15, delayFeedback: 0.3,
    compressionThreshold: -30, compressionRatio: 3,
    masterGain: 0.9, pitchShift: 0,
  },
  warm: {
    eqLow: 4, eqMid: 1, eqHigh: -3,
    reverbMix: 0.1, reverbSize: "medium",
    delayTime: 0, delayFeedback: 0,
    compressionThreshold: -18, compressionRatio: 3,
    masterGain: 1.1, pitchShift: 0,
  },
  robot: {
    eqLow: -6, eqMid: 6, eqHigh: -4,
    reverbMix: 0.15, reverbSize: "small",
    delayTime: 0.03, delayFeedback: 0.5,
    compressionThreshold: -10, compressionRatio: 12,
    masterGain: 1.3, pitchShift: 0,
  },
};

/** Daimon broadcast voice presets — swap via ?voice=preset query param */
export const DAIMON_PRESETS: Record<string, VoiceEffectsConfig> = {
  // Original — deep, warm, radio-broadcast. The "too deep" one.
  original: {
    preset: "clean" as const, bypass: false,
    eqLow: 4, eqMid: -1, eqHigh: -2,
    reverbMix: 0.08, reverbSize: "small" as const,
    delayTime: 0, delayFeedback: 0,
    compressionThreshold: -15, compressionRatio: 8,
    masterGain: 1.2, pitchShift: -200,
  },
  // Late Night Radio — less deep, more presence, cuts through mixes
  radio: {
    preset: "clean" as const, bypass: false,
    eqLow: 2, eqMid: 2, eqHigh: 0,
    reverbMix: 0.06, reverbSize: "small" as const,
    delayTime: 0, delayFeedback: 0,
    compressionThreshold: -18, compressionRatio: 6,
    masterGain: 1.15, pitchShift: -80,
  },
  // Interdimensional — natural pitch, spatial, otherworldly doubling
  interdimensional: {
    preset: "clean" as const, bypass: false,
    eqLow: 1, eqMid: 0, eqHigh: 2,
    reverbMix: 0.15, reverbSize: "medium" as const,
    delayTime: 80, delayFeedback: 0.15,
    compressionThreshold: -14, compressionRatio: 5,
    masterGain: 1.1, pitchShift: 0,
  },
  // Ghost — subtle drop, tight slapback, robotic transmission feel
  ghost: {
    preset: "clean" as const, bypass: false,
    eqLow: 2, eqMid: 1, eqHigh: -1,
    reverbMix: 0.12, reverbSize: "small" as const,
    delayTime: 40, delayFeedback: 0.1,
    compressionThreshold: -18, compressionRatio: 12,
    masterGain: 1.15, pitchShift: -60,
  },
  // Oracle — slightly pitched up, bright, clear, ethereal authority
  oracle: {
    preset: "clean" as const, bypass: false,
    eqLow: -2, eqMid: 1, eqHigh: 3,
    reverbMix: 0.2, reverbSize: "large" as const,
    delayTime: 120, delayFeedback: 0.12,
    compressionThreshold: -12, compressionRatio: 4,
    masterGain: 1.05, pitchShift: 100,
  },
  // Void — barely processed, intimate, uncomfortably close
  void: {
    preset: "clean" as const, bypass: false,
    eqLow: 3, eqMid: 3, eqHigh: -3,
    reverbMix: 0, reverbSize: "small" as const,
    delayTime: 0, delayFeedback: 0,
    compressionThreshold: -20, compressionRatio: 15,
    masterGain: 1.4, pitchShift: -30,
  },
  // Clean — bypass all effects, hear raw Charon voice
  clean: {
    preset: "clean" as const, bypass: true,
    eqLow: 0, eqMid: 0, eqHigh: 0,
    reverbMix: 0, reverbSize: "small" as const,
    delayTime: 0, delayFeedback: 0,
    compressionThreshold: 0, compressionRatio: 1,
    masterGain: 1, pitchShift: 0,
  },
};

/** Default Daimon preset — change this to swap the active voice */
export const DAIMON_VOICE_EFFECTS: VoiceEffectsConfig = DAIMON_PRESETS.radio;

/** Default Muse settings — matches the current hardcoded server config */
export const DEFAULT_MUSE_SETTINGS = {
  voice: "Charon" as const,
  language: "en-US",
  startSensitivity: "START_SENSITIVITY_LOW" as const,
  endSensitivity: "END_SENSITIVITY_LOW" as const,
  silenceDurationMs: 700,
  prefixPaddingMs: 300,
  contextWindowTokens: 40000,
  maxTranscriptLines: 20,
  voiceEffects: DEFAULT_VOICE_EFFECTS,
};

/** Reconnect config */
export const RECONNECT_DELAY_MS = 2000;
export const MAX_RECONNECT_ATTEMPTS = 5;

/** Tool declarations sent to Gemini during setup */
export const GEMINI_TOOLS = [
  {
    name: "fetch_video",
    description:
      "Play a video. If you have a specific YouTube URL (from discover_videos results), pass video_url and title. Otherwise, pass mood_tags and pacing to get a recommendation from the database.",
    parameters: {
      type: "object",
      properties: {
        video_url: {
          type: "string",
          description: "Direct YouTube URL to play (from discover_videos results). When provided, mood_tags and pacing are ignored.",
        },
        title: {
          type: "string",
          description: "Title of the video (when using video_url).",
        },
        mood_tags: {
          type: "array",
          items: { type: "string" },
          description:
            "Tags describing the user's current mood/interest, e.g. ['focused', 'technical', 'high-energy']. Used when no video_url is provided.",
        },
        pacing: {
          type: "number",
          description: "Desired pacing 1-10 (1=slow meditative, 10=rapid fire cuts). Used when no video_url is provided.",
        },
        max_duration: {
          type: "number",
          description: "Maximum video length in seconds. Use shorter for restless users.",
        },
        exclude_vibes: {
          type: "array",
          items: { type: "string" },
          description: "Vibes to avoid based on recent skips, e.g. ['tutorial', 'talking-head']",
        },
      },
    },
  },
  {
    name: "skip_video",
    description:
      "Log that the user skipped the current video. Use this to update preference weights.",
    parameters: {
      type: "object",
      properties: {
        reason: {
          type: "string",
          description:
            "Why the user skipped: 'too_slow', 'too_basic', 'not_interested', 'too_long', 'user_requested', 'unknown'",
        },
        watch_duration_seconds: {
          type: "number",
          description: "How many seconds the user watched before skipping",
        },
      },
      required: ["reason"],
    },
  },
  {
    name: "change_ui_state",
    description:
      "Change the UI layout. Use 'fullscreen' for immersive watching, 'split' to show related info alongside video, 'overlay' for minimal HUD.",
    parameters: {
      type: "object",
      properties: {
        mode: {
          type: "string",
          enum: ["fullscreen", "split", "overlay", "music", "library", "driving"],
          description: "UI layout mode",
        },
      },
      required: ["mode"],
    },
  },
  {
    name: "update_user_profile",
    description:
      "Update the user's interest profile when you discover they have a new interest or preference through conversation. This permanently affects future video recommendations.",
    parameters: {
      type: "object",
      properties: {
        new_interest_tags: {
          type: "array",
          items: { type: "string" },
          description:
            "New interest tags to add to the user's profile, e.g. ['ethical-hacking', 'local-server-hardware']",
        },
        remove_interest_tags: {
          type: "array",
          items: { type: "string" },
          description: "Interest tags to remove from the user's profile",
        },
      },
      required: ["new_interest_tags"],
    },
  },
  {
    name: "fetch_track",
    description:
      "Fetch a music track based on the user's current mood and energy level. Use this when the user wants music instead of video, or when the mood calls for audio-only content like when driving, working, or relaxing.",
    parameters: {
      type: "object",
      properties: {
        mood_tags: {
          type: "array",
          items: { type: "string" },
          description:
            "1-4 mood tags describing the desired vibe (e.g., chill, focused, energetic, melancholic)",
        },
        energy: {
          type: "integer",
          minimum: 1,
          maximum: 10,
          description:
            "Energy level 1-10. 1=very calm/ambient, 5=moderate, 10=very energetic/intense",
        },
        genre: {
          type: "string",
          description:
            "Optional genre preference: jazz, electronic, ambient, classical, rock, hip-hop, indie, etc.",
        },
        max_duration: {
          type: "integer",
          description:
            "Maximum track duration in seconds. Omit for no limit.",
        },
        exclude_vibes: {
          type: "array",
          items: { type: "string" },
          description: "Vibes to avoid based on recent skips",
        },
      },
      required: ["mood_tags", "energy"],
    },
  },
  {
    name: "skip_track",
    description:
      "Log that the user skipped or disliked the current music track. Call this when the user swipes, says skip/next, or expresses displeasure with the music.",
    parameters: {
      type: "object",
      properties: {
        reason: {
          type: "string",
          enum: [
            "not_my_taste",
            "too_slow",
            "too_intense",
            "heard_recently",
            "user_requested",
          ],
          description: "Why the track was skipped",
        },
        listen_duration_seconds: {
          type: "integer",
          description: "How many seconds the user listened before skipping",
        },
      },
      required: ["reason"],
    },
  },
  {
    name: "queue_track",
    description:
      "Add a specific track to the playback queue. Use when the user asks for a specific song or when building a set.",
    parameters: {
      type: "object",
      properties: {
        track_id: {
          type: "integer",
          description: "The track ID to queue",
        },
        position: {
          type: "string",
          enum: ["next", "end"],
          description: "Play next or add to end of queue",
        },
      },
      required: ["track_id"],
    },
  },
  {
    name: "discover_music",
    description:
      "Search for NEW music from online CC-licensed sources (Jamendo), download it, and play it immediately. Use this when the user asks for something specific not in the library, wants to explore new music, or says 'find me something new'. This searches the internet for free music, downloads it, and makes it playable within seconds.",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description:
            "Search query — a genre, mood, or style (e.g., 'chill jazz', 'upbeat electronic', 'acoustic folk', 'ambient')",
        },
        limit: {
          type: "integer",
          minimum: 1,
          maximum: 10,
          description:
            "How many tracks to discover and download (default 3). More = longer wait.",
        },
      },
      required: ["query"],
    },
  },
  {
    name: "find_similar",
    description:
      "Find tracks that sound similar to the currently playing track using audio fingerprint analysis. Use this when the user says 'more like this', 'something similar', 'keep this vibe going', or after they express enthusiasm about the current track.",
    parameters: {
      type: "object",
      properties: {
        track_id: {
          type: "integer",
          description:
            "ID of the track to find similar tracks for. Use the currently playing track's ID.",
        },
        limit: {
          type: "integer",
          minimum: 1,
          maximum: 20,
          description:
            "How many similar tracks to return (default 10).",
        },
      },
      required: ["track_id"],
    },
  },
  {
    name: "discover_videos",
    description:
      "Search YouTube and find videos to play. YOU construct the search query based on conversation context — be specific and creative. This is your primary way to find videos. You can also browse specific subscribed channels or categories. Videos play instantly via YouTube embed.",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description:
            "YouTube search query — YOU write this based on what the user wants. Be specific: 'Tipper live set 2024', 'best skateboarding parts 2025', 'Andrew Callaghan newest documentary', 'low level programming C tutorial', 'funny standup comedy 2024'. Think like you're typing into YouTube search.",
        },
        channel_name: {
          type: "string",
          description:
            "Browse a specific subscribed channel by name (e.g., 'Skrillex', 'NASA', 'Channel 5'). Use when user asks about a specific creator.",
        },
        category: {
          type: "string",
          description:
            "Browse channels in a category: tech, music, music-production, comedy, journalism, science, making, action-sports, creativity, outdoors, finance",
        },
        max_results: {
          type: "integer",
          minimum: 1,
          maximum: 10,
          description: "How many videos to find (default 5).",
        },
      },
    },
  },
  {
    name: "get_video_comments",
    description:
      "Read YouTube comments on the current video — thousands of strangers reacting to sound. Use proactively after a new video starts. Find what's revealing, surprising, or wrong in interesting ways.",
    parameters: {
      type: "object",
      properties: {
        video_url: {
          type: "string",
          description: "The YouTube URL of the video to get comments for. Use the currently playing video's URL.",
        },
        max_comments: {
          type: "integer",
          minimum: 1,
          maximum: 20,
          description: "How many top comments to fetch (default 10).",
        },
      },
      required: ["video_url"],
    },
  },
  {
    name: "list_channels",
    description:
      "List the subscribed YouTube channels and categories. Use this to see what's available before discovering, or to tell the user what channels are in the library.",
    parameters: {
      type: "object",
      properties: {
        category: {
          type: "string",
          description: "Optional: filter by category to see channels in that category",
        },
      },
    },
  },
  {
    name: "queue_video",
    description: "Add a video to the playback queue. Use after discover_videos to stack multiple picks.",
    parameters: {
      type: "object",
      properties: {
        video_url: { type: "string", description: "YouTube URL to queue" },
        title: { type: "string", description: "Video title" },
        position: { type: "string", enum: ["next", "end"], description: "Queue position" },
      },
      required: ["video_url", "title"],
    },
  },
];
