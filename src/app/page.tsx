"use client";

import { useState, useCallback } from "react";
import { GeminiProvider, useGemini } from "@/context/GeminiProvider";
import { VideoPlayer } from "@/components/VideoPlayer";
import { GeminiOverlay } from "@/components/GeminiOverlay";
import { SwipeContainer } from "@/components/SwipeContainer";
import { StartScreen } from "@/components/StartScreen";
import { RotatePrompt } from "@/components/RotatePrompt";
import MusicPlayer from "@/components/MusicPlayer";
import NowPlayingBar from "@/components/NowPlayingBar";
import DrivingMode from "@/components/DrivingMode";
import MusicLibrary from "@/components/MusicLibrary";
import UploadPanel from "@/components/UploadPanel";
import { SettingsPanel } from "@/components/SettingsPanel";

function PlayApp() {
  const {
    connectionStatus,
    contentMode,
    uiMode,
    isGeminiSpeaking,
    // Music playback
    currentTrack,
    isMusicPlaying,
    musicVolume,
    trackCurrentTime,
    trackDuration,
    playTrack,
    pauseTrack,
    skipTrack,
    onTrackEnd,
    // Interrupting Gemini (tap-to-talk in driving mode)
    toggleMic,
    // Music queue for previous-track support
    music,
    // Direct setters (library selection, driving mode exit)
    setCurrentTrack,
    setUiMode,
    setContentMode,
    setIsMusicPlaying,
    // Muse settings (for voice effects indicator in driving mode)
    museSettings,
    // Voice effects
    updateVoiceEffects,
  } = useGemini();

  const [overlayVisible, setOverlayVisible] = useState(true);
  const [libraryVisible, setLibraryVisible] = useState(false);
  const [uploadVisible, setUploadVisible] = useState(false);
  const [settingsVisible, setSettingsVisible] = useState(false);

  // F19: User-initiated track selection from library — bypass Gemini tool call
  const handleLibrarySelect = useCallback(
    (track: Parameters<typeof setCurrentTrack>[0]) => {
      setLibraryVisible(false);
      if (track) {
        setCurrentTrack(track);
        setContentMode("music");
        setIsMusicPlaying(true);
      }
    },
    [setCurrentTrack, setContentMode, setIsMusicPlaying]
  );

  const isActive =
    connectionStatus === "ready" || connectionStatus === "connected";

  // Before connection: show start screen — BUT keep the player alive if
  // music is loaded. The Gemini session can drop (1008 abort) while a track
  // is playing; unmounting MusicPlayer kills the <audio> element mid-song.
  const hasMusicLoaded = contentMode === "music" && currentTrack?.hls_url;
  if (!isActive && connectionStatus !== "connecting" && !hasMusicLoaded) {
    return <StartScreen />;
  }

  // ------------------------------------------------------------------
  // Driving mode — full-screen, owns its own gesture layer
  // ------------------------------------------------------------------
  if (contentMode === "music" && uiMode === "driving") {
    return (
      <DrivingMode
        track={currentTrack}
        isPlaying={isMusicPlaying}
        isGeminiSpeaking={isGeminiSpeaking}
        currentTime={trackCurrentTime}
        duration={trackDuration}
        onPlay={playTrack}
        onPause={pauseTrack}
        onSkip={skipTrack}
        onPrevious={() => {
          // Previous: if a queue item exists promote it, otherwise no-op.
          // Full previous-track logic lives in the hook's onTrackEnd / queue.
          skipTrack();
        }}
        onTapGemini={toggleMic}
        onExit={() => {
          // Exit driving mode — return to the standard fullscreen music player
          setUiMode("music");
        }}
        voiceEffects={museSettings.voiceEffects}
      />
    );
  }

  // ------------------------------------------------------------------
  // Library overlay — full-screen modal, rendered over the active player
  // ------------------------------------------------------------------
  const showLibrary = libraryVisible || uiMode === "library";

  // ------------------------------------------------------------------
  // Main session layout
  // ------------------------------------------------------------------
  return (
    <SwipeContainer
      onToggleOverlay={() => setOverlayVisible((v) => !v)}
      contentMode={contentMode}
    >
      {/* ── Video mode ───────────────────────────────────────────── */}
      {contentMode === "video" && <VideoPlayer />}

      {/* ── Music mode (non-driving) ─────────────────────────────── */}
      {contentMode === "music" && uiMode !== "driving" && (
        <MusicPlayer
          track={currentTrack}
          isPlaying={isMusicPlaying}
          isGeminiSpeaking={isGeminiSpeaking}
          volume={musicVolume}
          onPlay={playTrack}
          onPause={pauseTrack}
          onSkip={skipTrack}
          onPrevious={
            music && music.queue.length > 0 ? skipTrack : undefined
          }
          onSeek={() => {
            // Seek is handled internally by MusicPlayer's audio element.
            // No hook-level state update needed for the seek position.
          }}
          onTrackEnd={onTrackEnd}
        />
      )}

      {/* ── Idle state — nothing playing yet ─────────────────────── */}
      {contentMode === "idle" && (
        <div className="flex items-center justify-center w-full h-full bg-black">
          {/* Gemini will call fetch_video or fetch_track after connecting */}
        </div>
      )}

      {/* ── Persistent Gemini overlay (all modes except driving) ─── */}
      <GeminiOverlay visible={overlayVisible} onUpload={() => setUploadVisible(true)} onSettings={() => setSettingsVisible(true)} />

      {/* ── Mini now-playing bar (music, non-fullscreen/driving) ─── */}
      <NowPlayingBar
        track={currentTrack}
        isPlaying={isMusicPlaying}
        currentTime={trackCurrentTime}
        duration={trackDuration}
        onPlay={playTrack}
        onPause={pauseTrack}
        onSkip={skipTrack}
        onExpand={() => {
          // Expand means switch to full MusicPlayer — contentMode is already
          // "music" at this point; the bar is hidden in fullscreen uiMode.
          setOverlayVisible(false);
        }}
        visible={
          contentMode === "music" &&
          uiMode !== "fullscreen" &&
          uiMode !== "driving"
        }
      />

      {/* ── Music library overlay ────────────────────────────────── */}
      <MusicLibrary
        visible={showLibrary}
        onSelectTrack={handleLibrarySelect}
        onClose={() => setLibraryVisible(false)}
      />

      {/* Upload panel overlay */}
      <UploadPanel
        visible={uploadVisible}
        onClose={() => setUploadVisible(false)}
      />

      {/* ── Settings panel overlay ────────────────────────────── */}
      <SettingsPanel
        visible={settingsVisible}
        onClose={() => setSettingsVisible(false)}
        onEffectsChange={updateVoiceEffects}
      />
    </SwipeContainer>
  );
}

export default function Page() {
  return (
    <GeminiProvider>
      <PlayApp />
      <RotatePrompt />
    </GeminiProvider>
  );
}
