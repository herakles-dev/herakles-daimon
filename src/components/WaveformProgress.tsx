"use client";

import { useRef, useEffect, useCallback } from "react";

export interface WaveformProgressProps {
  /** 0-1 amplitude values, typically 800 points */
  waveformData: number[];
  /** Playback position in the range [0, 1] */
  progress: number;
  /** Total track duration in seconds */
  duration: number;
  /** Called with the seek position in seconds when the user clicks/touches */
  onSeek: (positionSeconds: number) => void;
  /** Canvas height in px — defaults to 48 (minimum touch target) */
  height?: number;
  className?: string;
}

/**
 * WaveformProgress
 *
 * Canvas-based waveform visualiser that doubles as a seek bar.
 *
 * - Played bars: cyan accent (#06b6d4)
 * - Unplayed bars: dim gray (#374151)
 * - Playhead:     thin white vertical line
 * - Click / touch anywhere to seek
 * - Fully responsive: fills container width via ResizeObserver
 * - Transparent background — inherits from parent
 */
export function WaveformProgress({
  waveformData,
  progress,
  duration,
  onSeek,
  height = 48,
  className = "",
}: WaveformProgressProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Store latest props in refs so the draw function always has fresh values
  // without needing to be re-created on every render.
  const progressRef = useRef(progress);
  const waveformRef = useRef(waveformData);

  progressRef.current = progress;
  waveformRef.current = waveformData;

  // -------------------------------------------------------------------------
  // Drawing
  // -------------------------------------------------------------------------

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { width, height: h } = canvas;
    const data = waveformRef.current;
    const pos = progressRef.current; // 0-1

    ctx.clearRect(0, 0, width, h);

    if (!data.length) return;

    const numBars = data.length;
    // Gap between bars is 1px; bar width fills the rest evenly.
    const gap = 1;
    const barWidth = Math.max(1, (width - gap * (numBars - 1)) / numBars);
    // Reserve 3px top + bottom padding so the playhead line is visible above
    // the tallest bar.
    const vertPadding = 3;
    const maxBarHeight = h - vertPadding * 2;

    const playedColor = "#06b6d4"; // cyan-500
    const unplayedColor = "#374151"; // gray-700
    const playheadColor = "#ffffff";

    const playheadX = pos * width;

    for (let i = 0; i < numBars; i++) {
      const x = i * (barWidth + gap);
      const amplitude = data[i] ?? 0; // clamp undefined to 0
      const barH = Math.max(2, amplitude * maxBarHeight);
      const y = vertPadding + (maxBarHeight - barH) / 2;

      ctx.fillStyle = x + barWidth <= playheadX ? playedColor : unplayedColor;
      ctx.fillRect(x, y, barWidth, barH);
    }

    // Playhead — thin white vertical line spanning full height
    if (playheadX > 0 && playheadX < width) {
      ctx.fillStyle = playheadColor;
      ctx.fillRect(Math.round(playheadX) - 1, 0, 2, h);
    }
  }, []);

  // -------------------------------------------------------------------------
  // Resize observer — keep canvas logical pixels in sync with CSS pixels
  // -------------------------------------------------------------------------

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    let rafId: number | null = null;

    const scheduleRedraw = () => {
      if (rafId !== null) return;
      rafId = requestAnimationFrame(() => {
        rafId = null;
        draw();
      });
    };

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const dpr = window.devicePixelRatio || 1;
        const cssWidth = entry.contentRect.width;
        const cssHeight = height;
        canvas.width = Math.round(cssWidth * dpr);
        canvas.height = Math.round(cssHeight * dpr);
        const ctx = canvas.getContext("2d");
        if (ctx) ctx.scale(dpr, dpr);
        scheduleRedraw();
      }
    });

    observer.observe(canvas.parentElement ?? canvas);

    return () => {
      observer.disconnect();
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, [draw, height]);

  // -------------------------------------------------------------------------
  // Redraw on progress or waveformData change
  // -------------------------------------------------------------------------

  useEffect(() => {
    let rafId: number | null = null;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      draw();
    });
    return () => {
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, [progress, waveformData, draw]);

  // -------------------------------------------------------------------------
  // Seek interaction
  // -------------------------------------------------------------------------

  const getSeekPosition = useCallback(
    (clientX: number): number => {
      const canvas = canvasRef.current;
      if (!canvas || !duration) return 0;
      const rect = canvas.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      return ratio * duration;
    },
    [duration]
  );

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      onSeek(getSeekPosition(e.clientX));
    },
    [onSeek, getSeekPosition]
  );

  const handleTouchStart = useCallback(
    (e: React.TouchEvent<HTMLCanvasElement>) => {
      if (e.touches.length > 0) {
        onSeek(getSeekPosition(e.touches[0].clientX));
      }
    },
    [onSeek, getSeekPosition]
  );

  return (
    <canvas
      ref={canvasRef}
      role="slider"
      aria-label="Waveform seek bar"
      aria-valuenow={Math.round(progress * duration)}
      aria-valuemin={0}
      aria-valuemax={Math.round(duration)}
      style={{
        display: "block",
        width: "100%",
        height: `${height}px`,
        cursor: "pointer",
        touchAction: "none",
      }}
      className={className}
      onClick={handleClick}
      onTouchStart={handleTouchStart}
    />
  );
}
