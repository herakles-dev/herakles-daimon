"use client";

import { useRef, type ReactNode } from "react";
import { useGestures } from "@/hooks/useGestures";
import { useGemini } from "@/context/GeminiProvider";
import type { ContentMode } from "@/lib/types";

interface SwipeContainerProps {
  children: ReactNode;
  onToggleOverlay: () => void;
  /**
   * The current content mode determines which skip action is triggered
   * by a left-swipe gesture.  Defaults to "video" behaviour if omitted
   * so that the original gesture contract is preserved.
   */
  contentMode?: ContentMode;
}

/**
 * Full-viewport touch/keyboard gesture container.
 *
 * Gesture routing:
 *   contentMode === "video"  → swipe-left calls skip()      (existing)
 *   contentMode === "music"  → swipe-left calls skipTrack()  (new)
 *   tap / spacebar           → toggle overlay                (unchanged)
 */
export function SwipeContainer({
  children,
  onToggleOverlay,
  contentMode,
}: SwipeContainerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const { skip, skipTrack, connectionStatus } = useGemini();

  // Route swipe-left to the correct action for the current content mode
  const handleSkip = contentMode === "music" ? skipTrack : skip;

  useGestures({
    onSkip: handleSkip,
    onTap: onToggleOverlay,
    containerRef,
    disabled: connectionStatus !== "ready",
  });

  return (
    <div
      ref={containerRef}
      className="relative h-screen w-screen overflow-hidden touch-pan-y"
    >
      {children}
    </div>
  );
}
