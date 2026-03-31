"use client";

import { useRef, useEffect, useCallback, useState } from "react";
import { usePathname } from "next/navigation";
import { useGemini } from "@/context/GeminiProvider";

// YouTube IFrame API types
declare global {
  interface Window {
    YT?: {
      Player: new (
        el: string | HTMLElement,
        config: Record<string, unknown>
      ) => YTPlayer;
      PlayerState?: Record<string, number>;
    };
    onYouTubeIframeAPIReady?: () => void;
  }
}

interface YTPlayer {
  destroy: () => void;
  loadVideoById: (id: string) => void;
  playVideo: () => void;
  pauseVideo: () => void;
  getPlayerState: () => number;
  getCurrentTime: () => number;
  getDuration: () => number;
}

// Extend HTMLVideoElement to include iOS Safari fullscreen API
interface IOSVideoElement extends HTMLVideoElement {
  webkitEnterFullscreen?: () => void;
  webkitExitFullscreen?: () => void;
  webkitDisplayingFullscreen?: boolean;
}

/**
 * Extract the YouTube video ID from common YouTube URL formats.
 */
function extractYouTubeId(url: string): string | null {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./, "");

    if (host === "youtu.be") {
      const id = parsed.pathname.slice(1).split("/")[0];
      return id || null;
    }

    if (host === "youtube.com") {
      if (parsed.pathname.startsWith("/embed/")) {
        const id = parsed.pathname.replace("/embed/", "").split("/")[0];
        return id || null;
      }
      const id = parsed.searchParams.get("v");
      return id || null;
    }
  } catch {
    // invalid URL
  }
  return null;
}

// Load the YouTube IFrame API script once globally
let ytApiLoaded = false;
let ytApiReady = false;
const ytReadyCallbacks: (() => void)[] = [];

function ensureYTApi(callback: () => void) {
  if (typeof window !== 'undefined' && window.YT && window.YT.Player) {
    ytApiReady = true;
    ytApiLoaded = true;
    callback();
    return;
  }

  if (ytApiReady) {
    callback();
    return;
  }

  ytReadyCallbacks.push(callback);

  if (!ytApiLoaded) {
    ytApiLoaded = true;
    const tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);

    window.onYouTubeIframeAPIReady = () => {
      ytApiReady = true;
      ytReadyCallbacks.forEach((cb) => cb());
      ytReadyCallbacks.length = 0;
    };
  }
}

/**
 * Full-viewport video player.
 *
 * Uses the official YouTube IFrame Player API for programmatic player
 * lifecycle management. The player is created once; subsequent videos
 * use loadVideoById() to avoid page reloads and black-flash transitions.
 */
export function VideoPlayer() {
  const { currentVideo, connectionStatus, onVideoEnd } = useGemini();
  const pathname = usePathname();
  const isBroadcast = pathname === "/broadcast";
  const videoRef = useRef<IOSVideoElement>(null);
  const ytPlayerRef = useRef<YTPlayer | null>(null);
  const ytContainerRef = useRef<HTMLDivElement>(null);
  const [ytReady, setYtReady] = useState(false);

  // Ref to avoid stale closure in YT player event handlers
  const onVideoEndRef = useRef(onVideoEnd);
  useEffect(() => { onVideoEndRef.current = onVideoEnd; }, [onVideoEnd]);

  const youtubeId = currentVideo?.url
    ? extractYouTubeId(currentVideo.url)
    : null;
  const isYouTube = youtubeId !== null;

  // Initialize YouTube IFrame API player ONCE
  useEffect(() => {
    if (!isYouTube) return;

    ensureYTApi(() => {
      if (!window.YT || ytPlayerRef.current) return; // Already have a player

      const container = ytContainerRef.current;
      if (!container) return;

      const playerDiv = document.createElement("div");
      playerDiv.id = "yt-player-" + Date.now();
      container.innerHTML = "";
      container.appendChild(playerDiv);

      ytPlayerRef.current = new window.YT.Player(playerDiv.id, {
        videoId: youtubeId || "",
        width: "100%",
        height: "100%",
        playerVars: {
          autoplay: 1,
          controls: 1,
          rel: 0,
          modestbranding: 1,
          playsinline: 1,
          enablejsapi: 1,
          mute: 0,
          // Always use the public domain as origin — headless Chrome loads
          // from localhost:8151 but YouTube rejects localhost as an embed
          // origin (Error 153). The public domain works for both broadcast
          // and normal users. Set NEXT_PUBLIC_APP_ORIGIN to your domain.
          origin: process.env.NEXT_PUBLIC_APP_ORIGIN || "http://localhost:8151",
        },
        events: {
          onReady: (event: { target: any }) => {
            setYtReady(true);
            // Patch the iframe Chrome inserted with the autoplay permissions policy.
            // --autoplay-policy=no-user-gesture-required bypasses the top-level
            // document's gesture requirement but NOT cross-origin iframe Permissions
            // Policy. Without allow="autoplay" on the iframe element, the YouTube
            // player's AudioContext stays suspended and produces no audio.
            try {
              const iframe = container?.querySelector("iframe");
              if (iframe) {
                const current = iframe.getAttribute("allow") || "";
                if (!current.includes("autoplay")) {
                  iframe.setAttribute(
                    "allow",
                    current ? `${current}; autoplay` : "autoplay"
                  );
                }
              }
            } catch {}
            // Unmute and restart playback so the now-permitted audio context activates
            try {
              event.target.unMute();
              event.target.setVolume(100);
              event.target.playVideo();
            } catch {}
          },
          onStateChange: (event: { data: number }) => {
            // YT.PlayerState.ENDED = 0
            if (event.data === 0) {
              console.log("[VideoPlayer] Video ended naturally");
              onVideoEndRef.current("ended");
            }
          },
          onError: (event: { data: number }) => {
            console.warn("[VideoPlayer] YT error:", event.data);
            onVideoEndRef.current("error");
          },
        },
      });
    });

    return () => {
      if (ytPlayerRef.current) {
        try { ytPlayerRef.current.destroy(); } catch {}
        ytPlayerRef.current = null;
        setYtReady(false);
      }
    };
  }, [isYouTube]); // Only recreate when switching TO/FROM YouTube

  // Load new video into existing player (no destroy/create — eliminates black flash)
  useEffect(() => {
    if (!youtubeId || !ytPlayerRef.current || !ytReady) return;
    try {
      ytPlayerRef.current.loadVideoById(youtubeId);
    } catch {
      // Force player recreation on next render
      console.warn("[VideoPlayer] loadVideoById failed, forcing player recreation");
      ytPlayerRef.current = null;
      setYtReady(false);
    }
  }, [youtubeId, ytReady]);

  // Stall detection — skip unplayable videos that don't trigger onError
  // Catches: stuck buffering, black screen with no progress, UNSTARTED forever
  useEffect(() => {
    if (!youtubeId || !ytReady || !ytPlayerRef.current) return;

    const CHECK_INTERVAL = 3000;  // Check every 3s
    const MAX_STALL_CHECKS = 5;   // 15s total before skip
    const MAX_UNSTARTED = 4;      // 12s in UNSTARTED = won't play
    let stallCount = 0;
    let unstartedCount = 0;
    let lastTime = -1;

    const timer = setInterval(() => {
      const player = ytPlayerRef.current;
      if (!player) return;

      let state: number;
      let currentTime: number;
      try {
        state = player.getPlayerState();
        currentTime = player.getCurrentTime();
      } catch {
        return; // Player not ready yet
      }

      // State constants: -1=UNSTARTED, 0=ENDED, 1=PLAYING, 2=PAUSED, 3=BUFFERING
      if (state === -1) {
        unstartedCount++;
        if (unstartedCount >= MAX_UNSTARTED) {
          console.warn("[VideoPlayer] Stall: UNSTARTED for %ds — skipping", unstartedCount * 3);
          clearInterval(timer);
          onVideoEndRef.current("stalled");
          return;
        }
      } else {
        unstartedCount = 0;
      }

      if (state === 1 || state === 3) {
        // PLAYING or BUFFERING — check if time is advancing
        if (currentTime === lastTime || currentTime === 0) {
          stallCount++;
          if (stallCount >= MAX_STALL_CHECKS) {
            console.warn("[VideoPlayer] Stall: no progress for %ds (state=%d, time=%d) — skipping",
              stallCount * 3, state, currentTime);
            clearInterval(timer);
            onVideoEndRef.current("stalled");
            return;
          }
        } else {
          // Progressing — reset
          stallCount = 0;
        }
        lastTime = currentTime;
      }
    }, CHECK_INTERVAL);

    return () => clearInterval(timer);
  }, [youtubeId, ytReady]);

  // Auto-play when video source changes (non-YouTube only)
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !currentVideo?.url || isYouTube) return;

    video.src = currentVideo.url;
    video.load();

    const playPromise = video.play();
    if (playPromise) {
      playPromise.catch((err) => {
        console.warn("[VideoPlayer] Autoplay blocked:", err.message);
      });
    }
  }, [currentVideo?.url, isYouTube]);

  const handleDoubleClick = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;

    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else if (video.requestFullscreen) {
      video.requestFullscreen().catch(() => {});
    } else if (video.webkitEnterFullscreen) {
      video.webkitEnterFullscreen();
    }
  }, []);

  const handleError = useCallback(() => {
    console.error("[VideoPlayer] Playback error:", videoRef.current?.error);
  }, []);

  return (
    <div
      className="absolute inset-0 bg-black"
      onDoubleClick={isYouTube ? undefined : handleDoubleClick}
    >
      {isYouTube ? (
        /* YouTube IFrame Player API — programmatic player lifecycle */
        <div
          ref={ytContainerRef}
          className="absolute inset-0 w-full h-full"
          style={{ background: "#000" }}
        />
      ) : (
        <video
          ref={videoRef}
          className="h-full w-full object-cover"
          playsInline
          /* eslint-disable-next-line @typescript-eslint/ban-ts-comment */
          // @ts-ignore — non-standard iOS attribute
          webkit-playsinline="true"
          x-webkit-airplay="allow"
          muted={false}
          loop={false}
          onError={handleError}
          style={{
            minWidth: "100%",
            minHeight: "100%",
          }}
        />
      )}

      {/* Waiting state */}
      {!currentVideo && connectionStatus === "ready" && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-center opacity-40">
            <div className="text-lg font-light tracking-wide">
              Listening...
            </div>
            <div className="mt-2 text-sm text-white/30">
              Play is finding your first video
            </div>
          </div>
        </div>
      )}

      {/* Video metadata overlay — hidden in broadcast mode to avoid double title */}
      {!isBroadcast && currentVideo?.title && (
        <div
          className="absolute left-6 right-20 pointer-events-none safe-landscape"
          style={{ bottom: "calc(1.25rem + var(--safe-bottom))" }}
        >
          <div className="text-sm font-medium text-white/70 drop-shadow-lg line-clamp-2">
            {currentVideo.title}
          </div>
          {currentVideo.vibe && (
            <div className="mt-1 text-xs text-white/40">
              {currentVideo.vibe}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
