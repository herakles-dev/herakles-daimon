'use client';

import { useState, useEffect, useCallback } from 'react';

interface CacheStatus {
  cachedTrackCount: number;
  cachedTrackIds: number[];
  totalSizeMB: number;
  maxTracks: number;
  isOnline: boolean;
}

export function useOfflineCache() {
  const [cacheStatus, setCacheStatus] = useState<CacheStatus>({
    cachedTrackCount: 0,
    cachedTrackIds: [],
    totalSizeMB: 0,
    maxTracks: 50,
    isOnline: true,
  });
  const [swReady, setSwReady] = useState(false);

  // Register service worker
  useEffect(() => {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker
      .register('/music-sw.js')
      .then(() => {
        setSwReady(true);
      })
      .catch(err => console.warn('SW registration failed:', err));
  }, []);

  // Online/offline detection
  useEffect(() => {
    const handleOnline = () => setCacheStatus(prev => ({ ...prev, isOnline: true }));
    const handleOffline = () => setCacheStatus(prev => ({ ...prev, isOnline: false }));
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    setCacheStatus(prev => ({ ...prev, isOnline: navigator.onLine }));
    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  // Send message to SW and get response via MessageChannel.
  // Returns null immediately if there is no active SW controller,
  // and times out after 5 s to prevent the promise from hanging forever.
  const sendToSW = useCallback((data: unknown): Promise<unknown> => {
    return new Promise((resolve) => {
      if (!navigator.serviceWorker?.controller) {
        resolve(null);
        return;
      }
      const channel = new MessageChannel();
      const timer = setTimeout(() => resolve(null), 5000);
      channel.port1.onmessage = (e) => {
        clearTimeout(timer);
        resolve(e.data);
      };
      navigator.serviceWorker.controller.postMessage(data, [channel.port2]);
    });
  }, []);

  const cacheTrack = useCallback(
    async (trackId: number) => {
      if (!swReady) return;
      return sendToSW({ type: 'CACHE_TRACK', trackId });
    },
    [swReady, sendToSW]
  );

  const uncacheTrack = useCallback(
    async (trackId: number) => {
      if (!swReady) return;
      return sendToSW({ type: 'UNCACHE_TRACK', trackId });
    },
    [swReady, sendToSW]
  );

  const refreshStatus = useCallback(async () => {
    if (!swReady) return;
    const status = await sendToSW({ type: 'GET_CACHE_STATUS' });
    if (status) setCacheStatus(prev => ({ ...prev, ...(status as Partial<CacheStatus>) }));
  }, [swReady, sendToSW]);

  const clearCache = useCallback(async () => {
    if (!swReady) return;
    await sendToSW({ type: 'CLEAR_CACHE' });
    await refreshStatus();
  }, [swReady, sendToSW, refreshStatus]);

  const isTrackCached = useCallback(
    (trackId: number) => {
      return cacheStatus.cachedTrackIds.includes(trackId);
    },
    [cacheStatus.cachedTrackIds]
  );

  // Refresh status periodically
  useEffect(() => {
    if (!swReady) return;
    refreshStatus();
    const interval = setInterval(refreshStatus, 30000);
    return () => clearInterval(interval);
  }, [swReady, refreshStatus]);

  return {
    ...cacheStatus,
    cacheTrack,
    uncacheTrack,
    clearCache,
    refreshStatus,
    isTrackCached,
    swReady,
  };
}
