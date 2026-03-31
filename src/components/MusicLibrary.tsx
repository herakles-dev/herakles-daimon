'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import type { TrackMeta } from '@/lib/types';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface MusicLibraryProps {
  onSelectTrack: (track: TrackMeta) => void;
  onClose: () => void;
  visible: boolean;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const FILTERS = [
  { key: 'all',       label: 'All' },
  { key: 'upload',    label: 'My Uploads' },
  { key: 'chill',     label: 'Chill' },
  { key: 'focus',     label: 'Focus' },
  { key: 'energetic', label: 'Energetic' },
  { key: 'jazz',      label: 'Jazz' },
  { key: 'ambient',   label: 'Ambient' },
  { key: 'cinematic', label: 'Cinematic' },
] as const;

const SORT_OPTIONS = [
  { key: 'recent',   label: 'Recent' },
  { key: 'title',    label: 'Title' },
  { key: 'artist',   label: 'Artist' },
  { key: 'duration', label: 'Duration' },
  { key: 'energy',   label: 'Energy' },
] as const;

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

// ---------------------------------------------------------------------------
// Source colour dot
// ---------------------------------------------------------------------------

const SOURCE_COLORS: Record<string, string> = {
  upload:      'bg-blue-400',
  jamendo:     'bg-green-400',
  openverse:   'bg-orange-400',
  incompetech: 'bg-yellow-400',
  ccmixter:    'bg-pink-400',
};

function sourceDotClass(source?: TrackMeta['source']): string {
  return source ? (SOURCE_COLORS[source] ?? 'bg-purple-400') : 'bg-purple-400';
}

// ---------------------------------------------------------------------------
// Duration formatter
// ---------------------------------------------------------------------------

function formatDuration(sec?: number): string {
  if (!sec || sec <= 0) return '--:--';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

// ---------------------------------------------------------------------------
// Inline SVG icons (no external deps — matches GeminiOverlay pattern)
// ---------------------------------------------------------------------------

function XIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6"  y1="6" x2="18" y2="18" />
    </svg>
  );
}

function SearchIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function MusicNoteIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 18V5l12-2v13" />
      <circle cx="6"  cy="18" r="3" />
      <circle cx="18" cy="16" r="3" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// TrackRow sub-component (memoised to avoid list churn)
// ---------------------------------------------------------------------------

interface TrackRowProps {
  track: TrackMeta;
  onSelect: (track: TrackMeta) => void;
}

function TrackRow({ track, onSelect }: TrackRowProps) {
  return (
    <button
      onClick={() => onSelect(track)}
      className="w-full flex items-center gap-3 py-3 px-2 border-b border-gray-900/80
                 hover:bg-gray-900/50 active:bg-gray-800/60 transition-colors rounded-lg
                 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500/60"
      aria-label={`Play ${track.title}${track.artist ? ` by ${track.artist}` : ''}`}
    >
      {/* Artwork thumbnail */}
      {track.artwork_url ? (
        <img
          src={track.artwork_url}
          alt=""
          aria-hidden="true"
          className="w-12 h-12 rounded-lg object-cover flex-shrink-0 bg-gray-800"
        />
      ) : (
        <div
          aria-hidden="true"
          className="w-12 h-12 rounded-lg bg-gradient-to-br from-indigo-900/40 to-gray-800
                     flex items-center justify-center flex-shrink-0"
        >
          <MusicNoteIcon className="w-5 h-5 text-indigo-400/50" />
        </div>
      )}

      {/* Title + artist */}
      <div className="flex-1 min-w-0 text-left">
        <p className="text-sm font-medium text-white truncate leading-snug">{track.title}</p>
        <p className="text-xs text-gray-400 truncate mt-0.5">{track.artist || 'Unknown Artist'}</p>
      </div>

      {/* Duration + source dot */}
      <div className="flex-shrink-0 text-right flex flex-col items-end gap-1.5">
        <span className="text-xs text-gray-500 tabular-nums">{formatDuration(track.duration_sec)}</span>
        <span
          className={`w-1.5 h-1.5 rounded-full ${sourceDotClass(track.source)}`}
          title={track.source ?? 'unknown'}
        />
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function MusicLibrary({ onSelectTrack, onClose, visible }: MusicLibraryProps) {
  const [tracks, setTracks]           = useState<TrackMeta[]>([]);
  const [search, setSearch]           = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [activeFilter, setActiveFilter] = useState<string>('all');
  const [sortBy, setSortBy]           = useState<string>('recent');
  const [loading, setLoading]         = useState(false);
  const [hasMore, setHasMore]         = useState(true);
  const [offset, setOffset]           = useState(0);

  // Stable refs so the IntersectionObserver callback captures fresh state
  const loadingRef   = useRef(false);
  const hasMoreRef   = useRef(true);
  const offsetRef    = useRef(0);

  const observerRef  = useRef<IntersectionObserver | null>(null);
  const loadMoreRef  = useRef<HTMLDivElement>(null);
  const searchRef    = useRef<HTMLInputElement>(null);

  // Keep refs in sync with state
  useEffect(() => { loadingRef.current = loading; }, [loading]);
  useEffect(() => { hasMoreRef.current = hasMore; }, [hasMore]);
  useEffect(() => { offsetRef.current = offset; }, [offset]);

  // ------------------------------------------------------------------
  // Debounce search input
  // ------------------------------------------------------------------

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  // ------------------------------------------------------------------
  // Fetch logic — stable signature so it can be called from observer
  // ------------------------------------------------------------------

  const fetchTracks = useCallback(async (reset: boolean) => {
    if (loadingRef.current) return;
    if (!reset && !hasMoreRef.current) return;

    setLoading(true);
    loadingRef.current = true;

    const currentOffset = reset ? 0 : offsetRef.current;

    const params = new URLSearchParams({
      sort_by: sortBy,
      limit:   String(PAGE_SIZE),
      offset:  String(currentOffset),
    });
    if (debouncedSearch) params.set('search', debouncedSearch);
    if (activeFilter === 'upload')      params.set('source', 'upload');
    else if (activeFilter !== 'all')    params.set('mood', activeFilter);

    try {
      const resp = await fetch(`/api/tracks/library?${params}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const newTracks: TrackMeta[] = data.tracks ?? [];

      if (reset) {
        setTracks(newTracks);
        setOffset(newTracks.length);
        offsetRef.current = newTracks.length;
      } else {
        setTracks(prev => [...prev, ...newTracks]);
        setOffset(prev => prev + newTracks.length);
        offsetRef.current = currentOffset + newTracks.length;
      }

      const more = newTracks.length === PAGE_SIZE;
      setHasMore(more);
      hasMoreRef.current = more;
    } catch (e) {
      console.error('[MusicLibrary] Failed to fetch library:', e);
    } finally {
      setLoading(false);
      loadingRef.current = false;
    }
  // fetchTracks intentionally depends on sortBy/debouncedSearch/activeFilter
  // so the reset-on-filter-change effect below captures the right closure.
  }, [sortBy, debouncedSearch, activeFilter]);

  // ------------------------------------------------------------------
  // Reset + refetch when panel opens or any filter changes
  // ------------------------------------------------------------------

  useEffect(() => {
    if (!visible) return;
    fetchTracks(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, debouncedSearch, sortBy, activeFilter]);

  // Auto-focus search when panel opens
  useEffect(() => {
    if (visible) {
      const timer = setTimeout(() => searchRef.current?.focus(), 100);
      return () => clearTimeout(timer);
    }
  }, [visible]);

  // ------------------------------------------------------------------
  // Infinite scroll — IntersectionObserver on sentinel div
  // ------------------------------------------------------------------

  useEffect(() => {
    if (!loadMoreRef.current) return;

    observerRef.current?.disconnect();

    observerRef.current = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !loadingRef.current && hasMoreRef.current) {
          fetchTracks(false);
        }
      },
      { threshold: 0.1 }
    );

    observerRef.current.observe(loadMoreRef.current);

    return () => {
      observerRef.current?.disconnect();
      observerRef.current = null;
    };
  }, [fetchTracks]);

  // ------------------------------------------------------------------
  // Keyboard: Escape closes the panel
  // ------------------------------------------------------------------

  useEffect(() => {
    if (!visible) return;
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [visible, onClose]);

  // ------------------------------------------------------------------
  // Render guard
  // ------------------------------------------------------------------

  if (!visible) return null;

  const isEmpty = !loading && tracks.length === 0;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Music library"
      className="fixed inset-0 z-30 bg-black/95 flex flex-col"
    >
      {/* ── Header ──────────────────────────────────────────────── */}
      <div
        className="flex items-center justify-between px-4 pb-2 flex-shrink-0"
        style={{ paddingTop: 'max(1rem, env(safe-area-inset-top))' }}
      >
        <h2 className="text-lg font-semibold text-white tracking-tight">Library</h2>
        <button
          onClick={onClose}
          className="w-9 h-9 flex items-center justify-center rounded-full
                     text-gray-400 hover:text-white hover:bg-gray-800/60
                     transition-colors focus-visible:outline-none focus-visible:ring-1
                     focus-visible:ring-indigo-500/60"
          aria-label="Close library"
        >
          <XIcon className="w-5 h-5" />
        </button>
      </div>

      {/* ── Search bar ──────────────────────────────────────────── */}
      <div className="px-4 pb-3 flex-shrink-0">
        <div className="relative">
          <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500 pointer-events-none" />
          <input
            ref={searchRef}
            type="search"
            placeholder="Search music..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search music"
            className="w-full h-10 pl-9 pr-4 rounded-xl bg-gray-900 text-white text-sm
                       placeholder-gray-500 outline-none
                       focus:ring-1 focus:ring-indigo-500/50
                       border border-gray-800
                       [&::-webkit-search-cancel-button]:hidden"
          />
          {/* Clear button — only visible when there is text */}
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 transition-colors"
              aria-label="Clear search"
            >
              <XIcon className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* ── Filter chips ────────────────────────────────────────── */}
      <div
        className="flex gap-2 px-4 pb-3 overflow-x-auto flex-shrink-0"
        style={{ scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch' } as React.CSSProperties}
        role="group"
        aria-label="Filter by category"
      >
        {FILTERS.map(f => (
          <button
            key={f.key}
            onClick={() => setActiveFilter(f.key)}
            className={`flex-shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-colors
                        focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500/60
                        ${activeFilter === f.key
                          ? 'bg-indigo-500 text-white'
                          : 'bg-gray-900 text-gray-400 hover:text-white border border-gray-800'
                        }`}
            aria-pressed={activeFilter === f.key}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* ── Sort selector ───────────────────────────────────────── */}
      <div className="flex items-center justify-end px-4 pb-2 gap-1.5 flex-shrink-0">
        <span className="text-xs text-gray-600">Sort:</span>
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
          aria-label="Sort tracks by"
          className="text-xs text-gray-400 bg-transparent border-none outline-none
                     cursor-pointer focus-visible:ring-1 focus-visible:ring-indigo-500/60
                     rounded pr-1"
        >
          {SORT_OPTIONS.map(s => (
            <option key={s.key} value={s.key} className="bg-gray-900 text-white">
              {s.label}
            </option>
          ))}
        </select>
      </div>

      {/* ── Track list ──────────────────────────────────────────── */}
      <div
        className="flex-1 overflow-y-auto px-4 overscroll-contain"
        style={{ WebkitOverflowScrolling: 'touch' } as React.CSSProperties}
        role="list"
        aria-label="Tracks"
        aria-live="polite"
        aria-busy={loading}
      >
        {tracks.map(track => (
          <div key={track.id} role="listitem">
            <TrackRow track={track} onSelect={onSelectTrack} />
          </div>
        ))}

        {/* Infinite scroll sentinel */}
        <div
          ref={loadMoreRef}
          className="h-16 flex items-center justify-center"
          aria-hidden="true"
        >
          {loading && (
            <div
              className="w-5 h-5 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin"
              role="status"
              aria-label="Loading more tracks"
            />
          )}
        </div>

        {/* End-of-list message */}
        {!loading && !hasMore && tracks.length > 0 && (
          <p
            className="text-center text-xs text-gray-700 pb-4"
            style={{ paddingBottom: 'max(1rem, env(safe-area-inset-bottom))' }}
          >
            {tracks.length} {tracks.length === 1 ? 'track' : 'tracks'}
          </p>
        )}

        {/* Empty states */}
        {isEmpty && (
          <div className="flex flex-col items-center justify-center py-20 gap-3">
            <MusicNoteIcon className="w-10 h-10 text-gray-700" />
            <p className="text-gray-500 text-sm text-center max-w-xs leading-relaxed">
              {debouncedSearch
                ? `No tracks match "${debouncedSearch}".`
                : activeFilter !== 'all'
                  ? `No ${activeFilter} tracks found. Try a different filter.`
                  : 'Your library is empty. Upload music or let Play discover tracks for you.'}
            </p>
            {debouncedSearch && (
              <button
                onClick={() => setSearch('')}
                className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors mt-1"
              >
                Clear search
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
