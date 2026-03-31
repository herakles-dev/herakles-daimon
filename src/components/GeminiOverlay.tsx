"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { useGemini } from "@/context/GeminiProvider";

/**
 * Minimal HUD overlay showing:
 * - Connection status (top-right dot)
 * - Gemini speaking indicator (pulsing orb)
 * - Mic status toggle
 * - Transcript (last few lines, fades out)
 *
 * Visibility toggled by tapping the screen.
 */
export function GeminiOverlay({ visible, onUpload, onSettings }: { visible: boolean; onUpload?: () => void; onSettings?: () => void }) {
  const {
    connectionStatus,
    isGeminiSpeaking,
    isMicActive,
    transcript,
    skipCount,
    toggleMic,
    sendChat,
    toolBusy,
    nextVideo,
    museSettings,
  } = useGemini();

  const voiceEffects = museSettings.voiceEffects;
  const effectsActive = voiceEffects && !voiceEffects.bypass && voiceEffects.preset !== "clean";

  const [chatOpen, setChatOpen] = useState(false);
  const [chatText, setChatText] = useState("");
  const [sendFailed, setSendFailed] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const sendFailedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Focus input when chat opens or overlay becomes visible with chat open
  useEffect(() => {
    if (chatOpen && visible) inputRef.current?.focus();
  }, [chatOpen, visible]);

  // Cleanup sendFailed timer on unmount
  useEffect(() => {
    return () => {
      if (sendFailedTimerRef.current) clearTimeout(sendFailedTimerRef.current);
    };
  }, []);

  const handleSend = useCallback(() => {
    if (!chatText.trim()) return;
    const ok = sendChat(chatText);
    if (ok) {
      setChatText("");
      setSendFailed(false);
      if (sendFailedTimerRef.current) clearTimeout(sendFailedTimerRef.current);
    } else {
      setSendFailed(true);
      if (sendFailedTimerRef.current) clearTimeout(sendFailedTimerRef.current);
      sendFailedTimerRef.current = setTimeout(() => setSendFailed(false), 1500);
    }
  }, [chatText, sendChat]);

  const isReady = connectionStatus === "ready";

  if (!visible) {
    // Even when hidden, show the speaking indicator and keep settings accessible
    return (
      <div className="absolute inset-0 pointer-events-none">
        {isGeminiSpeaking && <SpeakingOrb effectsActive={effectsActive} />}
        {!isGeminiSpeaking && toolBusy && <BusyIndicator message={toolBusy} />}
        {/* Settings gear stays visible even when overlay is hidden */}
        {onSettings && (
          <div
            className="absolute flex items-center gap-3 pointer-events-auto"
            style={{ top: "calc(1rem + var(--safe-top))", right: "calc(1rem + var(--safe-right))" }}
          >
            <button
              onClick={onSettings}
              className="w-9 h-9 rounded-full flex items-center justify-center
                         bg-white/10 backdrop-blur-sm border border-white/15
                         hover:bg-white/20 active:bg-white/25 transition-colors"
              aria-label="Muse settings"
            >
              <SettingsIcon className="w-4 h-4 text-white/60" />
            </button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="absolute inset-0 pointer-events-none">
      {/* Top bar — status dot + settings gear, respects notch safe area */}
      <div
        className="absolute flex items-center gap-3 pointer-events-auto"
        style={{ top: "calc(1rem + var(--safe-top))", right: "calc(1rem + var(--safe-right))" }}
      >
        {onSettings && (
          <button
            onClick={onSettings}
            className="w-9 h-9 rounded-full flex items-center justify-center
                       bg-white/10 backdrop-blur-sm border border-white/15
                       hover:bg-white/20 active:bg-white/25 transition-colors"
            aria-label="Muse settings"
          >
            <SettingsIcon className="w-4 h-4 text-white/60" />
          </button>
        )}
        <StatusDot status={connectionStatus} effectsActive={!!effectsActive} />
      </div>

      {/* Speaking indicator — center */}
      {isGeminiSpeaking && <SpeakingOrb effectsActive={effectsActive} />}

      {/* Tool busy indicator — center, shows when Muse is searching */}
      {!isGeminiSpeaking && toolBusy && <BusyIndicator message={toolBusy} />}

      {/* Button stack — bottom right */}
      <div
        className="absolute flex flex-col items-center gap-2 pointer-events-auto"
        style={{
          bottom: "calc(1.5rem + var(--safe-bottom))",
          right: "calc(1.5rem + var(--safe-right))",
        }}
      >
        {/* Upload button */}
        {onUpload && (
          <button
            onClick={onUpload}
            className="w-10 h-10 rounded-full flex items-center justify-center
                       bg-white/10 backdrop-blur-sm border border-white/10
                       hover:bg-white/20 transition-colors"
            aria-label="Upload music"
          >
            <UploadIcon className="w-4 h-4 text-white/70" />
          </button>
        )}

        {/* Chat toggle */}
        <button
          onClick={() => {
            setChatOpen((p) => {
              if (p) setSendFailed(false); // clear stale error on close
              return !p;
            });
          }}
          className={`w-10 h-10 rounded-full flex items-center justify-center
                      backdrop-blur-sm border border-white/10
                      hover:bg-white/20 transition-colors
                      ${chatOpen ? "bg-white/25" : "bg-white/10"}`}
          aria-label="Chat"
          aria-expanded={chatOpen}
          aria-controls="chat-compose"
        >
          <ChatIcon className="w-4 h-4 text-white/70" />
        </button>

        {/* Mic toggle */}
        <button
          onClick={toggleMic}
          className="w-12 h-12 rounded-full flex items-center justify-center
                     bg-white/10 backdrop-blur-sm border border-white/10
                     hover:bg-white/20 transition-colors"
          aria-label={isMicActive ? "Mute microphone" : "Unmute microphone"}
        >
          {isMicActive ? (
            <MicIcon className="w-5 h-5 text-white/80" />
          ) : (
            <MicOffIcon className="w-5 h-5 text-red-400" />
          )}
        </button>
      </div>

      {/* Chat compose bar — bottom, above safe area */}
      {chatOpen && (
        <div
          className="absolute left-0 right-0 pointer-events-auto px-3"
          style={{ bottom: "calc(5.5rem + var(--safe-bottom))" }}
          role="group"
          aria-label="Chat message input"
        >
          <form
            id="chat-compose"
            onSubmit={(e) => { e.preventDefault(); handleSend(); }}
            className={`flex items-center gap-2 bg-black/80 backdrop-blur-md border rounded-2xl px-4 py-2 max-w-lg mx-auto transition-colors ${
              sendFailed ? "border-red-500/60" : "border-white/15"
            }`}
          >
            <input
              ref={inputRef}
              type="text"
              value={chatText}
              onChange={(e) => setChatText(e.target.value)}
              placeholder={isReady ? "Type a message..." : "Connecting..."}
              disabled={!isReady}
              maxLength={2000}
              enterKeyHint="send"
              className="flex-1 bg-transparent text-sm text-white placeholder-white/30
                         outline-none border-none min-w-0 disabled:opacity-40"
              autoComplete="off"
            />
            <button
              type="submit"
              disabled={!chatText.trim() || !isReady}
              className="w-8 h-8 rounded-full flex items-center justify-center
                         bg-white/15 hover:bg-white/25 disabled:opacity-30
                         transition-colors flex-shrink-0"
              aria-label="Send message"
            >
              <SendIcon className="w-4 h-4 text-white/80" />
            </button>
          </form>
        </div>
      )}

      {/* Up next pill — bottom center, above safe area */}
      {nextVideo?.title && (
        <div className="absolute bottom-16 left-4 right-4 pointer-events-none">
          <div className="inline-block bg-white/10 backdrop-blur-sm rounded-full px-3 py-1 text-xs text-white/50">
            Up next: {nextVideo.title}
          </div>
        </div>
      )}

      {/* Transcript — bottom left, above compose bar if open */}
      {transcript.length > 0 && (
        <div
          className="absolute max-w-md"
          style={{
            bottom: chatOpen ? "calc(9rem + var(--safe-bottom))" : "calc(1.5rem + var(--safe-bottom))",
            left: "calc(1.5rem + var(--safe-left))",
            right: "calc(6rem + var(--safe-right))",
            transition: "bottom 0.2s ease",
          }}
          aria-live="polite"
          aria-label="Conversation transcript"
        >
          {transcript.slice(-5).map((line, i) => {
            const globalIdx = Math.max(0, transcript.length - 5) + i;
            return (
              <div
                key={globalIdx}
                className={`text-sm leading-relaxed ${
                  line.startsWith("You:") ? "text-blue-400/70" : "text-white/50"
                }`}
                style={{ opacity: 0.3 + (i / Math.min(transcript.length, 5)) * 0.7 }}
              >
                {/* line is raw text from Gemini API — do NOT render as HTML without sanitization */}
                {line}
              </div>
            );
          })}
        </div>
      )}

      {/* Skip counter — top left, below status bar notch */}
      {skipCount > 0 && (
        <div
          className="absolute text-xs text-white/20"
          style={{
            top: "calc(1rem + var(--safe-top))",
            left: "calc(1rem + var(--safe-left))",
          }}
        >
          {skipCount} skipped
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------
// Sub-components
// ------------------------------------------------------------------

function StatusDot({ status, effectsActive }: { status: string; effectsActive?: boolean }) {
  const colors: Record<string, string> = {
    disconnected: "bg-gray-500",
    connecting: "bg-yellow-500 animate-pulse",
    connected: "bg-yellow-500",
    ready: "bg-green-500",
    error: "bg-red-500",
  };

  return (
    <div className="flex items-center gap-2">
      <div className={`w-2 h-2 rounded-full ${colors[status] || "bg-gray-500"}`} />
      {status !== "ready" && (
        <span className="text-xs text-white/40 capitalize">{status}</span>
      )}
      {effectsActive && (
        <span className="text-[10px] font-bold text-purple-400 ml-1">FX</span>
      )}
    </div>
  );
}

function SpeakingOrb({ effectsActive }: { effectsActive?: boolean | null }) {
  return (
    <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none">
      <div className="relative">
        <div className="gemini-speaking w-16 h-16 rounded-full bg-accent/30 border border-accent/50 backdrop-blur-sm" />
        {effectsActive && (
          <div className="absolute inset-0 rounded-full effects-active-ring" />
        )}
      </div>
    </div>
  );
}

function BusyIndicator({ message }: { message: string }) {
  return (
    <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none">
      <div className="flex flex-col items-center gap-2">
        <div className="w-10 h-10 rounded-full border-2 border-white/20 border-t-white/60 animate-spin" />
        <span className="text-xs text-white/50">{message}</span>
      </div>
    </div>
  );
}

// Inline SVG icons to avoid deps
function MicIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" y1="19" x2="12" y2="23" />
      <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
  );
}

function UploadIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}

function ChatIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function SendIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

function MicOffIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <line x1="1" y1="1" x2="23" y2="23" />
      <path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6" />
      <path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2c0 .67-.09 1.31-.27 1.92" />
      <line x1="12" y1="19" x2="12" y2="23" />
      <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
  );
}

function SettingsIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}
