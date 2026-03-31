"use client";

import { useEffect, useRef, useCallback } from "react";

interface UseGesturesOptions {
  /** Called when user swipes left or presses right arrow / clicks next */
  onSkip: () => void;
  /** Called when user taps/clicks the video area (toggle overlay) */
  onTap: () => void;
  /** Element ref to attach touch listeners to */
  containerRef: React.RefObject<HTMLElement | null>;
  /** Disable gesture handling */
  disabled?: boolean;
}

const SWIPE_THRESHOLD = 50; // px
const SWIPE_VELOCITY = 0.3; // px/ms

/**
 * Hook for mobile swipe + desktop keyboard/click gesture detection.
 * - Swipe left (mobile) or Right Arrow / N key (desktop) = skip
 * - Tap (mobile) or Space (desktop) = toggle overlay
 */
export function useGestures({
  onSkip,
  onTap,
  containerRef,
  disabled = false,
}: UseGesturesOptions): void {
  const touchStartRef = useRef<{ x: number; y: number; time: number } | null>(null);

  const handleSkip = useCallback(() => {
    if (!disabled) onSkip();
  }, [onSkip, disabled]);

  const handleTap = useCallback(() => {
    if (!disabled) onTap();
  }, [onTap, disabled]);

  // Keyboard listeners
  useEffect(() => {
    if (disabled) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't capture shortcuts when user is typing in an input
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      switch (e.key) {
        case "ArrowRight":
        case "n":
        case "N":
          e.preventDefault();
          handleSkip();
          break;
        case " ":
          e.preventDefault();
          handleTap();
          break;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleSkip, handleTap, disabled]);

  // Touch listeners
  useEffect(() => {
    const el = containerRef.current;
    if (!el || disabled) return;

    const handleTouchStart = (e: TouchEvent) => {
      const touch = e.touches[0];
      touchStartRef.current = {
        x: touch.clientX,
        y: touch.clientY,
        time: Date.now(),
      };
    };

    const handleTouchEnd = (e: TouchEvent) => {
      if (!touchStartRef.current) return;

      const touch = e.changedTouches[0];
      const dx = touch.clientX - touchStartRef.current.x;
      const dy = touch.clientY - touchStartRef.current.y;
      const dt = Date.now() - touchStartRef.current.time;
      const velocity = Math.abs(dx) / dt;

      touchStartRef.current = null;

      // Horizontal swipe detection
      if (
        Math.abs(dx) > SWIPE_THRESHOLD &&
        Math.abs(dx) > Math.abs(dy) * 1.5 &&
        velocity > SWIPE_VELOCITY
      ) {
        if (dx < 0) {
          // Swipe left = skip/next
          handleSkip();
        }
        // Swipe right could be "go back" — not implemented yet
        return;
      }

      // If no swipe detected, treat as tap
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) {
        handleTap();
      }
    };

    el.addEventListener("touchstart", handleTouchStart, { passive: true });
    el.addEventListener("touchend", handleTouchEnd, { passive: true });

    return () => {
      el.removeEventListener("touchstart", handleTouchStart);
      el.removeEventListener("touchend", handleTouchEnd);
    };
  }, [containerRef, handleSkip, handleTap, disabled]);
}
