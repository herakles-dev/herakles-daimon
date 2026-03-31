'use client';

import { useState, useEffect, useCallback } from 'react';

// -----------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------

export type Quality = '128k' | '64k' | 'auto';

interface NetworkInfo {
  effectiveType?: string;
  saveData?: boolean;
  type?: string;
  addEventListener?: (event: string, handler: () => void) => void;
  removeEventListener?: (event: string, handler: () => void) => void;
}

declare global {
  interface Navigator {
    connection?: NetworkInfo;
    mozConnection?: NetworkInfo;
    webkitConnection?: NetworkInfo;
  }
}

export interface UseAudioQualityReturn {
  /** User-selected preference: 'auto' | '128k' | '64k' */
  quality: Quality;
  /** Resolved quality after auto-detection — always '128k' or '64k' */
  effectiveQuality: '128k' | '64k';
  /** Override the quality preference manually */
  setQuality: (q: Quality) => void;
  /** Cycle: auto → 128k → 64k → auto */
  cycleQuality: () => void;
  /** Raw value from Network Information API: 'wifi' | '4g' | '3g' | 'unknown' … */
  connectionType: string;
  /** true when effectiveQuality resolves to '64k' */
  isLowBandwidth: boolean;
}

// Ordered cycle used by cycleQuality()
const QUALITY_CYCLE: Quality[] = ['auto', '128k', '64k'];

// -----------------------------------------------------------------------
// Hook
// -----------------------------------------------------------------------

/**
 * Manages audio quality selection with connection-aware auto mode.
 *
 * Auto mode uses the Network Information API to select quality:
 *   - WiFi or 4G  → 128 kbps AAC
 *   - 3G or worse → 64 kbps AAC
 *   - Data saver  → 64 kbps AAC (overrides everything)
 *
 * Falls back to 128k when the Network Information API is unavailable
 * (e.g. Firefox, Safari, SSR).
 *
 * @param initialQuality - Starting preference (default: 'auto')
 */
export function useAudioQuality(initialQuality: Quality = 'auto'): UseAudioQualityReturn {
  const [quality, setQualityState] = useState<Quality>(initialQuality);
  const [connectionType, setConnectionType] = useState<string>('unknown');

  // ------------------------------------------------------------------
  // Network Information API helpers
  // ------------------------------------------------------------------

  /** Safely returns the vendor-prefixed connection object or null. */
  const getConnection = useCallback((): NetworkInfo | null => {
    if (typeof navigator === 'undefined') return null;
    return (
      navigator.connection ??
      navigator.mozConnection ??
      navigator.webkitConnection ??
      null
    );
  }, []);

  /**
   * Derive the optimal bitrate from current network conditions.
   * Priority: saveData flag → effective connection type → connection type.
   */
  const getOptimalQuality = useCallback((): '128k' | '64k' => {
    const conn = getConnection();
    if (!conn) return '128k'; // API unavailable — assume good connection

    // Data-saver flag takes precedence over everything
    if (conn.saveData) return '64k';

    const eff = conn.effectiveType ?? '';
    const connType = conn.type ?? '';

    // High-bandwidth connections
    if (eff === '4g' || connType === 'wifi' || connType === 'ethernet') return '128k';

    // Anything slower (2g, 3g, slow-2g, bluetooth, …) gets low quality
    if (eff === '3g' || eff === '2g' || eff === 'slow-2g') return '64k';

    // Unknown effective type but a known fast physical type
    if (connType === 'wifi' || connType === 'ethernet') return '128k';

    // Default for ambiguous cases (e.g. effectiveType not set but conn exists)
    return '64k';
  }, [getConnection]);

  // ------------------------------------------------------------------
  // Subscribe to connection changes
  // ------------------------------------------------------------------

  useEffect(() => {
    const conn = getConnection();
    if (!conn) return;

    const update = () => {
      const type = conn.effectiveType ?? conn.type ?? 'unknown';
      setConnectionType(type);
    };

    update(); // Sync on mount
    conn.addEventListener?.('change', update);
    return () => conn.removeEventListener?.('change', update);
  }, [getConnection]);

  // ------------------------------------------------------------------
  // Public API
  // ------------------------------------------------------------------

  /** Resolve auto to an actual bitrate. */
  const effectiveQuality: '128k' | '64k' =
    quality === 'auto' ? getOptimalQuality() : quality;

  /** Public setter — kept stable via useCallback. */
  const setQuality = useCallback((q: Quality) => {
    setQualityState(q);
  }, []);

  /** Advance to next step in the cycle. */
  const cycleQuality = useCallback(() => {
    setQualityState(prev => {
      const idx = QUALITY_CYCLE.indexOf(prev);
      return QUALITY_CYCLE[(idx + 1) % QUALITY_CYCLE.length];
    });
  }, []);

  return {
    quality,
    effectiveQuality,
    setQuality,
    cycleQuality,
    connectionType,
    isLowBandwidth: effectiveQuality === '64k',
  };
}
