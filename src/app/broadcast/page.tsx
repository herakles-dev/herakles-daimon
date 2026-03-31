"use client";

/**
 * /broadcast — DAIMON Live stream overlay
 * Layout: 1280x720 fixed canvas. All UI 2x scale for mobile YouTube readability.
 */

import React, { memo, useEffect, useRef, useState } from "react";
import { GeminiProvider, useGemini } from "@/context/GeminiProvider";
import { DAIMON_VOICE_EFFECTS, DAIMON_PRESETS } from "@/lib/constants";
import { VideoPlayer } from "@/components/VideoPlayer";
import MusicPlayer from "@/components/MusicPlayer";
import { BroadcastChat } from "@/components/BroadcastChat";
import { useBroadcastChat } from "@/hooks/useBroadcastChat";

class BroadcastErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false };
  static getDerivedStateFromError() { return { hasError: true }; }
  componentDidCatch(error: Error) {
    console.error("[Broadcast] Render crash:", error);
    // Auto-reload after 5 seconds
    setTimeout(() => window.location.reload(), 5000);
  }
  render() {
    if (this.state.hasError) {
      return <div style={{ position: "fixed", inset: 0, background: "#000" }} />;
    }
    return this.props.children;
  }
}

const UptimeClock = memo(function UptimeClock() {
  const [uptime, setUptime] = useState(0);
  const startRef = useRef(Date.now());
  useEffect(() => {
    const iv = setInterval(() => setUptime(Math.floor((Date.now() - startRef.current) / 1000)), 1000);
    return () => clearInterval(iv);
  }, []);
  const h = Math.floor(uptime / 3600);
  const m = Math.floor((uptime % 3600) / 60);
  const s = uptime % 60;
  const text = h > 0
    ? `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
    : `${m}:${s.toString().padStart(2, "0")}`;
  return <span style={{ fontSize: "28px", color: "rgba(255,255,255,0.3)", fontVariantNumeric: "tabular-nums" }}>{text}</span>;
});

function TierTicker() {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: "10px",
      fontSize: "22px", color: "rgba(255,255,255,0.45)", letterSpacing: "0.02em",
    }}>
      <span style={{ color: "#1565C0", fontWeight: 700 }}>$1</span><span>Shoutout</span>
      <span style={{ color: "rgba(255,255,255,0.15)" }}>|</span>
      <span style={{ color: "#FFCA28", fontWeight: 700 }}>$2</span><span>Response</span>
      <span style={{ color: "rgba(255,255,255,0.15)" }}>|</span>
      <span style={{ color: "#F57C00", fontWeight: 700 }}>$5</span><span>Chat</span>
      <span style={{ color: "rgba(255,255,255,0.15)" }}>|</span>
      <span style={{ color: "#E62117", fontWeight: 700 }}>$10</span><span>VIP</span>
      <span style={{ color: "rgba(255,255,255,0.15)" }}>|</span>
      <span style={{ color: "#7C3AED", fontWeight: 700 }}>$25</span><span>Full Set</span>
    </div>
  );
}

function BroadcastApp() {
  const {
    connectionStatus, contentMode, isGeminiSpeaking, toolBusy,
    nextVideo, currentVideo, transcript,
    currentTrack, isMusicPlaying, musicVolume,
    trackCurrentTime, trackDuration,
    playTrack, pauseTrack, skipTrack, onTrackEnd, connect,
    updateVoiceEffects,
  } = useGemini();

  const { messages, currentSuperChat, isConnected: chatConnected } = useBroadcastChat();

  // Apply Daimon voice effects on mount — read ?voice=preset from URL
  const effectsAppliedRef = useRef(false);
  useEffect(() => {
    if (effectsAppliedRef.current) return;
    effectsAppliedRef.current = true;
    const params = new URLSearchParams(window.location.search);
    const presetName = params.get("voice");
    const effects = (presetName && DAIMON_PRESETS[presetName]) || DAIMON_VOICE_EFFECTS;
    if (presetName && DAIMON_PRESETS[presetName]) {
      console.log(`[Broadcast] Voice preset: ${presetName}`);
    }
    updateVoiceEffects(effects);
  }, [updateVoiceEffects]);

  const hasConnectedRef = useRef(false);
  useEffect(() => {
    if (hasConnectedRef.current) return;
    hasConnectedRef.current = true;
    connect().catch(() => {});
  }, [connect]);

  const isActive = connectionStatus === "ready" || connectionStatus === "connected";

  useEffect(() => {
    document.body.classList.add('broadcast-mode');
    return () => document.body.classList.remove('broadcast-mode');
  }, []);

  const [caption, setCaption] = useState("");
  const captionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastHostLineRef = useRef<string>("");
  useEffect(() => {
    const hostLines = transcript.filter((l) => l.startsWith("Daimon:") || l.startsWith("Muse:"));
    const lastHost = hostLines[hostLines.length - 1];
    if (lastHost && lastHost !== lastHostLineRef.current) {
      lastHostLineRef.current = lastHost;
      setCaption(lastHost.replace(/^(?:Daimon|Muse):\s*/, "").slice(0, 300));
      if (captionTimerRef.current) clearTimeout(captionTimerRef.current);
      captionTimerRef.current = setTimeout(() => setCaption(""), 8000);
    }
  }, [transcript]);
  // Separate unmount-only cleanup for the caption timer
  useEffect(() => () => { if (captionTimerRef.current) clearTimeout(captionTimerRef.current); }, []);

  const nowTitle = contentMode === "video" ? currentVideo?.title : currentTrack?.title;
  const nowSub = contentMode === "video" ? currentVideo?.channel : currentTrack?.artist;

  return (
    <div style={{
      position: "fixed", inset: 0, width: "1280px", height: "720px",
      background: "#000", overflow: "hidden", cursor: "none",
      fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
      WebkitFontSmoothing: "antialiased",
    }}>
      {/* Content */}
      {contentMode === "video" && <div style={{ position: "absolute", inset: 0 }}><VideoPlayer /></div>}
      {contentMode === "music" && currentTrack && (
        <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
          <MusicPlayer track={currentTrack} isPlaying={isMusicPlaying}
            isGeminiSpeaking={isGeminiSpeaking} volume={musicVolume}
            onPlay={playTrack} onPause={pauseTrack} onSkip={skipTrack}
            onSeek={() => {}} onTrackEnd={onTrackEnd} />
        </div>
      )}
      {contentMode === "idle" && (
        <div style={{
          position: "absolute", inset: 0, display: "flex", alignItems: "center",
          justifyContent: "center", background: "radial-gradient(ellipse at center, #0a0a1a 0%, #000 70%)",
        }}>
          <div style={{ textAlign: "center" }}>
            <div style={{
              width: "120px", height: "120px", borderRadius: "50%",
              background: "radial-gradient(circle, rgba(139,92,246,0.4) 0%, rgba(139,92,246,0.05) 70%, transparent 100%)",
              margin: "0 auto 32px", animation: "orbPulse 3s ease-in-out infinite",
            }} />
            <div style={{
              color: "rgba(255,255,255,0.3)", fontSize: "32px", letterSpacing: "0.2em",
              fontWeight: 300, textTransform: "uppercase",
            }}>
              {isActive ? (toolBusy ? "Searching..." : "Starting...") : "Connecting..."}
            </div>
          </div>
        </div>
      )}

      {/* Top gradient */}
      <div style={{
        position: "absolute", top: 0, left: 0, right: 0, height: "120px",
        background: "linear-gradient(to bottom, rgba(0,0,0,0.8) 0%, rgba(0,0,0,0.3) 60%, transparent 100%)",
        pointerEvents: "none", zIndex: 10,
      }} />

      {/* Bottom gradient */}
      <div style={{
        position: "absolute", bottom: 0, left: 0, right: 0, height: "240px",
        background: "linear-gradient(to top, rgba(0,0,0,0.9) 0%, rgba(0,0,0,0.4) 50%, transparent 100%)",
        pointerEvents: "none", zIndex: 5,
      }} />

      {/* TOP-LEFT: LIVE + DAIMON + uptime */}
      <div style={{
        position: "absolute", top: "28px", left: "36px",
        display: "flex", alignItems: "center", gap: "20px",
        pointerEvents: "none", zIndex: 20,
      }}>
        <div style={{
          display: "flex", alignItems: "center", gap: "10px",
          background: isActive ? "rgba(220,38,38,0.9)" : "rgba(100,100,100,0.7)",
          borderRadius: "10px", padding: "10px 22px",
          animation: isActive ? "livePulse 2.5s ease-in-out infinite" : "none",
        }}>
          <div style={{
            width: "14px", height: "14px", borderRadius: "50%", background: "#fff",
            animation: isActive ? "liveDot 1.5s ease-in-out infinite" : "none",
          }} />
          <span style={{ fontSize: "26px", fontWeight: 800, color: "#fff", letterSpacing: "0.12em" }}>
            LIVE
          </span>
        </div>

        <span style={{
          fontSize: "48px", fontWeight: 800, color: "rgba(255,255,255,0.95)",
          letterSpacing: "0.1em", textTransform: "uppercase",
        }}>
          DAIMON
        </span>
        <span style={{
          fontSize: "22px", fontWeight: 400, color: "rgba(255,255,255,0.3)",
          letterSpacing: "0.06em", marginLeft: "-8px", alignSelf: "flex-end", marginBottom: "8px",
        }}>
          by Herakles
        </span>

        <UptimeClock />
      </div>

      {/* TOP-RIGHT: Speaking / tool */}
      <div style={{
        position: "absolute", top: "32px", right: "36px",
        display: "flex", alignItems: "center", gap: "16px",
        pointerEvents: "none", zIndex: 20,
      }}>
        {toolBusy && (
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{ fontSize: "24px", color: "rgba(255,255,255,0.45)" }}>{toolBusy}</span>
            <div style={{
              width: "32px", height: "32px", borderRadius: "50%",
              border: "3px solid rgba(255,255,255,0.15)", borderTopColor: "rgba(139,92,246,0.7)",
              animation: "spin 0.8s linear infinite",
            }} />
          </div>
        )}

        {isGeminiSpeaking && !toolBusy && (
          <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
            <span style={{ fontSize: "26px", color: "rgba(139,92,246,0.8)", fontWeight: 700, letterSpacing: "0.08em" }}>
              DAIMON
            </span>
            <div style={{ display: "flex", alignItems: "center", gap: "5px", height: "40px" }}>
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} style={{
                  width: "6px", height: "8px", borderRadius: "3px",
                  background: `rgba(139,92,246,${0.5 + i * 0.1})`,
                  animation: `audioBar ${0.4 + i * 0.12}s ease-in-out ${i * 0.08}s infinite alternate`,
                }} />
              ))}
            </div>
          </div>
        )}

        {!isGeminiSpeaking && !toolBusy && (
          <div style={{
            width: "16px", height: "16px", borderRadius: "50%",
            background: isActive ? "rgba(139,92,246,0.6)" : connectionStatus === "error" ? "#ef4444" : "#6b7280",
            boxShadow: isActive ? "0 0 12px rgba(139,92,246,0.4)" : "none",
            animation: connectionStatus === "connecting" ? "statusPulse 1.2s ease-in-out infinite" : "none",
          }} />
        )}
      </div>

      {/* BOTTOM-LEFT: Chat */}
      <BroadcastChat messages={messages} currentSuperChat={currentSuperChat} />

      {!chatConnected && (
        <div style={{
          position: "absolute", bottom: "12px", left: "36px", fontSize: "18px",
          color: "rgba(255,255,255,0.15)", pointerEvents: "none", zIndex: 15,
        }}>
          chat offline
        </div>
      )}

      {/* BOTTOM-RIGHT: Now playing */}
      {nowTitle && (
        <div style={{
          position: "absolute", bottom: "80px", right: "36px",
          maxWidth: "540px", pointerEvents: "none", zIndex: 10,
        }}>
          <div style={{
            background: "rgba(10,10,15,0.5)",
            backdropFilter: "blur(24px) saturate(1.2)",
            WebkitBackdropFilter: "blur(24px) saturate(1.2)",
            borderRadius: "20px", padding: "22px 30px",
            border: "1px solid rgba(255,255,255,0.08)",
            boxShadow: "0 8px 40px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.04)",
            display: "flex", alignItems: "center", gap: "20px",
          }}>
            {contentMode === "music" && currentTrack?.artwork_url && (
              <img src={currentTrack.artwork_url} alt="" style={{
                width: "80px", height: "80px", borderRadius: "14px",
                objectFit: "cover", flexShrink: 0, boxShadow: "0 4px 16px rgba(0,0,0,0.5)",
              }} />
            )}
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{
                fontSize: "16px", fontWeight: 700, color: "rgba(139,92,246,0.7)",
                textTransform: "uppercase", letterSpacing: "0.12em", marginBottom: "6px",
              }}>
                Now Playing
              </div>
              <div style={{
                fontSize: "30px", fontWeight: 700, color: "rgba(255,255,255,0.95)",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", lineHeight: 1.2,
              }}>
                {nowTitle}
              </div>
              {nowSub && (
                <div style={{
                  fontSize: "22px", color: "rgba(255,255,255,0.45)", marginTop: "4px", fontWeight: 500,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {nowSub}
                </div>
              )}
              {contentMode === "music" && trackDuration > 0 && (
                <div style={{
                  height: "4px", background: "rgba(255,255,255,0.08)",
                  borderRadius: "2px", marginTop: "12px", overflow: "hidden",
                }}>
                  <div style={{
                    height: "100%", width: `${(trackCurrentTime / trackDuration) * 100}%`,
                    background: "rgba(139,92,246,0.6)", borderRadius: "2px", transition: "width 1s linear",
                  }} />
                </div>
              )}
            </div>
            {contentMode === "music" && trackDuration > 0 && (
              <span style={{ fontSize: "20px", color: "rgba(255,255,255,0.3)", flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
                {fmtTime(trackCurrentTime)}/{fmtTime(trackDuration)}
              </span>
            )}
          </div>
        </div>
      )}

      {/* BOTTOM-CENTER: Tier ticker */}
      <div style={{
        position: "absolute", bottom: "16px", left: "440px", right: "24px",
        pointerEvents: "none", zIndex: 10,
        display: "flex", justifyContent: "center", alignItems: "center", gap: "24px",
      }}>
        <TierTicker />
        {nextVideo?.title && (
          <>
            <span style={{ color: "rgba(255,255,255,0.1)", fontSize: "22px" }}>|</span>
            <div style={{
              fontSize: "20px", color: "rgba(255,255,255,0.35)",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "400px",
            }}>
              <span style={{ color: "rgba(139,92,246,0.6)", fontWeight: 600 }}>Up next </span>
              {nextVideo.title}
            </div>
          </>
        )}
      </div>

      {/* CAPTIONS — raised higher (bottom: 220px) */}
      {caption && !currentSuperChat && (
        <div style={{
          position: "absolute", bottom: "260px", left: "50%",
          transform: "translateX(-50%)", pointerEvents: "none", zIndex: 15,
          maxWidth: "800px", textAlign: "center",
        }}>
          <div style={{
            background: "rgba(0,0,0,0.35)", backdropFilter: "blur(16px)",
            WebkitBackdropFilter: "blur(16px)",
            borderRadius: "12px", padding: "12px 32px",
            fontSize: "26px", fontWeight: 400, color: "rgba(255,255,255,0.9)",
            lineHeight: 1.5, textShadow: "0 1px 4px rgba(0,0,0,0.5)",
            animation: "captionFadeIn 0.3s ease-out",
            maxWidth: "900px",
            whiteSpace: "normal",
            wordBreak: "break-word",
          }}>
            {caption}
          </div>
        </div>
      )}

    </div>
  );
}

function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${Math.floor(s % 60).toString().padStart(2, "0")}`;
}

export default function BroadcastPage() {
  return (
    <BroadcastErrorBoundary>
      <GeminiProvider broadcastMode><BroadcastApp /></GeminiProvider>
    </BroadcastErrorBoundary>
  );
}
