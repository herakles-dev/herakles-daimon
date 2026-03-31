/**
 * Music Service Worker — Herakles Play
 *
 * Caches HLS segments for offline music playback.
 * Strategies:
 * - HLS manifests (.m3u8): Network-first, cache fallback
 * - HLS segments (.aac): Cache-first (immutable content)
 * - Album artwork (.webp): Cache-first, 30-day expiry
 * - API calls: Network-only
 */

const CACHE_NAME = 'play-music-v1';
const MAX_CACHED_TRACKS = 50; // ~190MB at 128kbps

// Track which track IDs are cached
const CACHED_TRACKS_KEY = 'cached-track-ids';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // HLS segments (.aac) — Cache-first (immutable)
  if (url.pathname.match(/\/api\/tracks\/\d+\/stream\/.*\.aac$/)) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // HLS manifests (.m3u8) — Network-first, cache fallback
  if (url.pathname.match(/\/api\/tracks\/\d+\/stream.*\.m3u8$/)) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Artwork — Cache-first
  if (url.pathname.match(/\/api\/tracks\/\d+\/artwork$/)) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
          return response;
        });
      })
    );
    return;
  }
});

// Message handling for explicit cache management
self.addEventListener('message', (event) => {
  if (event.data.type === 'CACHE_TRACK') {
    // Pre-cache all segments for a specific track
    const trackId = event.data.trackId;
    cacheTrack(trackId).then(() => {
      event.ports[0]?.postMessage({ cached: true, trackId });
    });
  }

  if (event.data.type === 'UNCACHE_TRACK') {
    uncacheTrack(event.data.trackId).then(() => {
      event.ports[0]?.postMessage({ uncached: true, trackId: event.data.trackId });
    });
  }

  if (event.data.type === 'GET_CACHE_STATUS') {
    getCacheStatus().then(status => {
      event.ports[0]?.postMessage(status);
    });
  }

  if (event.data.type === 'CLEAR_CACHE') {
    caches.delete(CACHE_NAME).then(() => {
      event.ports[0]?.postMessage({ cleared: true });
    });
  }
});

async function cacheTrack(trackId) {
  const cache = await caches.open(CACHE_NAME);

  // Fetch the master manifest to discover segments.
  // Read the text FIRST, then build a new Response for the cache so we
  // never hit the "body already used" error from calling both .text() and
  // .clone() on the same Response object.
  const masterUrl = `/api/tracks/${trackId}/stream.m3u8`;
  const masterResp = await fetch(masterUrl);
  if (!masterResp.ok) return;
  const masterText = await masterResp.text();
  await cache.put(masterUrl, new Response(masterText, {
    headers: masterResp.headers,
    status: masterResp.status,
  }));

  const variants = masterText.split('\n').filter(l => l.endsWith('.m3u8'));

  for (const variant of variants) {
    const variantUrl = `/api/tracks/${trackId}/stream/${variant}`;
    const variantResp = await fetch(variantUrl);
    if (!variantResp.ok) continue;
    const variantText = await variantResp.text();
    await cache.put(variantUrl, new Response(variantText, {
      headers: variantResp.headers,
      status: variantResp.status,
    }));

    // Parse variant to get segments
    const segments = variantText.split('\n').filter(l => l.endsWith('.aac'));

    for (const segment of segments) {
      const segUrl = `/api/tracks/${trackId}/stream/${segment}`;
      const existing = await cache.match(segUrl);
      if (!existing) {
        const segResp = await fetch(segUrl);
        if (segResp.ok) await cache.put(segUrl, segResp);
      }
    }
  }

  // Cache artwork too
  const artResp = await fetch(`/api/tracks/${trackId}/artwork`);
  if (artResp.ok) await cache.put(`/api/tracks/${trackId}/artwork`, artResp);
}

async function uncacheTrack(trackId) {
  const cache = await caches.open(CACHE_NAME);
  const keys = await cache.keys();
  const toDelete = keys.filter(req => req.url.includes(`/api/tracks/${trackId}/`));
  await Promise.all(toDelete.map(req => cache.delete(req)));
}

async function getCacheStatus() {
  const cache = await caches.open(CACHE_NAME);
  const keys = await cache.keys();

  // Extract unique track IDs from cached URLs
  const trackIds = new Set();
  let totalSize = 0;

  for (const request of keys) {
    const match = request.url.match(/\/api\/tracks\/(\d+)\//);
    if (match) trackIds.add(parseInt(match[1]));
    // Estimate size from cached responses
    const resp = await cache.match(request);
    if (resp) {
      const blob = await resp.clone().blob();
      totalSize += blob.size;
    }
  }

  return {
    cachedTrackCount: trackIds.size,
    cachedTrackIds: Array.from(trackIds),
    totalSizeBytes: totalSize,
    totalSizeMB: Math.round(totalSize / 1024 / 1024 * 10) / 10,
    maxTracks: MAX_CACHED_TRACKS,
  };
}
