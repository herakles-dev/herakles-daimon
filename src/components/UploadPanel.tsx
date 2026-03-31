'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import type { UploadProgress } from '@/lib/types';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface UploadPanelProps {
  visible: boolean;
  onClose: () => void;
  onUploadComplete?: (trackId: number) => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ACCEPTED_EXTENSIONS = ['.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.opus', '.wma', '.zip'];
const AUDIO_EXTENSIONS    = ['.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.opus', '.wma'];
const MAX_SIZE_MB = 200;
const MAX_CONCURRENT = 3;
const POLL_INTERVAL_MS = 2000;

// ---------------------------------------------------------------------------
// Inline SVG icons — no external deps
// ---------------------------------------------------------------------------

function XIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6"  y1="6" x2="18" y2="18" />
    </svg>
  );
}

function CloudUploadIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="16 16 12 12 8 16" />
      <line x1="12" y1="12" x2="12" y2="21" />
      <path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3" />
    </svg>
  );
}

function CheckCircleIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}

function AlertCircleIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
  );
}

function FileAudioIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <path d="M10 13a2 2 0 1 0 4 0v-3h2" />
    </svg>
  );
}

/** Archive/zip icon */
function ZipIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
      <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
      <line x1="12" y1="22.08" x2="12" y2="12" />
    </svg>
  );
}

/** Folder icon for the folder-picker button */
function FolderIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function truncateFilename(name: string, maxLen = 36): string {
  if (name.length <= maxLen) return name;
  const ext = name.lastIndexOf('.');
  if (ext > 0) {
    const base = name.slice(0, ext);
    const extension = name.slice(ext);
    const truncated = base.slice(0, maxLen - extension.length - 3);
    return `${truncated}...${extension}`;
  }
  return `${name.slice(0, maxLen - 3)}...`;
}

function getFileExt(name: string): string {
  return '.' + (name.split('.').pop()?.toLowerCase() ?? '');
}

// ---------------------------------------------------------------------------
// StatusBadge sub-component
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: UploadProgress['status'] }) {
  if (status === 'uploading') {
    return (
      <span className="flex items-center gap-1 text-xs text-blue-400 font-medium">
        <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" aria-hidden="true" />
        Uploading
      </span>
    );
  }
  if (status === 'extracting') {
    return (
      <span className="flex items-center gap-1 text-xs text-purple-400 font-medium">
        <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse" aria-hidden="true" />
        Extracting...
      </span>
    );
  }
  if (status === 'transcoding') {
    return (
      <span className="flex items-center gap-1 text-xs text-yellow-400 font-medium">
        <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" aria-hidden="true" />
        Processing
      </span>
    );
  }
  if (status === 'ready') {
    return (
      <span className="flex items-center gap-1 text-xs text-green-400 font-medium">
        <CheckCircleIcon className="w-3.5 h-3.5" />
        Ready
      </span>
    );
  }
  // error
  return (
    <span className="flex items-center gap-1 text-xs text-red-400 font-medium">
      <AlertCircleIcon className="w-3.5 h-3.5" />
      Failed
    </span>
  );
}

// ---------------------------------------------------------------------------
// UploadRow sub-component
// ---------------------------------------------------------------------------

interface UploadRowProps {
  item: UploadProgress;
  fileSize?: number;
}

function UploadRow({ item, fileSize }: UploadRowProps) {
  const isActive = item.status === 'uploading' || item.status === 'transcoding' || item.status === 'extracting';

  const barColor =
    item.status === 'uploading'   ? 'bg-blue-500' :
    item.status === 'extracting'  ? 'bg-purple-500' :
    item.status === 'transcoding' ? 'bg-yellow-500' :
    item.status === 'ready'       ? 'bg-green-500' :
                                    'bg-red-500';

  const displayProgress =
    item.status === 'ready'       ? 100 :
    item.status === 'transcoding' ? 100 :
    item.status === 'extracting'  ? 100 :
    item.progress;

  const isZip = item.isZip ?? getFileExt(item.filename) === '.zip';

  return (
    <div className="py-3 border-b border-gray-900/80 last:border-0">
      <div className="flex items-start gap-3">
        {/* File icon */}
        <div
          className="w-9 h-9 rounded-lg bg-gray-900 flex items-center justify-center flex-shrink-0 mt-0.5"
          aria-hidden="true"
        >
          {isZip
            ? <ZipIcon className="w-4 h-4 text-purple-500" />
            : <FileAudioIcon className="w-4 h-4 text-gray-500" />
          }
        </div>

        {/* Info column */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2 mb-1.5">
            <p className="text-sm text-white leading-snug truncate" title={item.filename}>
              {truncateFilename(item.filename)}
            </p>
            <div className="flex-shrink-0">
              <StatusBadge status={item.status} />
            </div>
          </div>

          {/* Progress bar */}
          <div
            className="w-full h-1 bg-gray-800 rounded-full overflow-hidden"
            role="progressbar"
            aria-valuenow={Math.round(displayProgress)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${item.filename} upload progress`}
          >
            <div
              className={`h-full rounded-full transition-all duration-300 ${barColor} ${isActive ? 'transition-width' : ''}`}
              style={{ width: `${Math.min(100, displayProgress)}%` }}
            />
          </div>

          {/* Meta row */}
          <div className="flex items-center justify-between mt-1.5">
            {fileSize !== undefined && (
              <span className="text-xs text-gray-600">{formatBytes(fileSize)}</span>
            )}
            {item.status === 'uploading' && (
              <span className="text-xs text-gray-600 tabular-nums ml-auto">
                {Math.round(item.progress)}%
              </span>
            )}
            {item.error && (
              <span className="text-xs text-red-500/80 truncate ml-auto max-w-[16rem]" title={item.error}>
                {item.error}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Batch progress bar sub-component
// ---------------------------------------------------------------------------

interface BatchProgressBarProps {
  total: number;
  ready: number;
  errors: number;
  active: number;
}

function BatchProgressBar({ total, ready, errors, active }: BatchProgressBarProps) {
  const done = ready + errors;
  const pct  = total > 0 ? (done / total) * 100 : 0;

  return (
    <div className="mb-4 rounded-xl bg-gray-900/60 border border-gray-800 px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-gray-400">
          {active > 0
            ? `${active} uploading\u2026`
            : `${ready} of ${total} done`}
        </span>
        <span className="text-xs text-gray-600 tabular-nums">{Math.round(pct)}%</span>
      </div>
      <div className="w-full h-1.5 bg-gray-800 rounded-full overflow-hidden" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div
          className="h-full rounded-full bg-indigo-500 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      {errors > 0 && (
        <p className="mt-1.5 text-xs text-red-400">{errors} failed</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function UploadPanel({ visible, onClose, onUploadComplete }: UploadPanelProps) {
  const [queue, setQueue]           = useState<UploadProgress[]>([]);
  const [fileSizes, setFileSizes]   = useState<Record<string, number>>({});
  const [isDragOver, setIsDragOver] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [isBatchMode, setIsBatchMode]   = useState(false);

  const fileInputRef   = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  // Track active poll intervals so we can clean them up
  const pollsRef = useRef<Record<number, ReturnType<typeof setInterval>>>({});

  // ------------------------------------------------------------------
  // Cleanup all polls on unmount
  // ------------------------------------------------------------------

  useEffect(() => {
    return () => {
      Object.values(pollsRef.current).forEach(clearInterval);
    };
  }, []);

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
  // Progress helpers
  // ------------------------------------------------------------------

  const updateProgress = useCallback((filename: string, patch: Partial<UploadProgress>) => {
    setQueue(prev =>
      prev.map(item =>
        item.filename === filename ? { ...item, ...patch } : item
      )
    );
  }, []);

  // ------------------------------------------------------------------
  // Transcode poller
  // ------------------------------------------------------------------

  const pollTrackStatus = useCallback((trackId: number, filename: string) => {
    // Clear any existing poll for this track
    if (pollsRef.current[trackId]) clearInterval(pollsRef.current[trackId]);

    pollsRef.current[trackId] = setInterval(async () => {
      try {
        const resp = await fetch(`/api/tracks/${trackId}`);
        if (!resp.ok) return; // transient — keep polling
        const data = await resp.json();
        if (data.status === 'ready') {
          clearInterval(pollsRef.current[trackId]);
          delete pollsRef.current[trackId];
          updateProgress(filename, { status: 'ready' });
          onUploadComplete?.(trackId);
        } else if (data.status === 'error') {
          clearInterval(pollsRef.current[trackId]);
          delete pollsRef.current[trackId];
          updateProgress(filename, { status: 'error', error: 'Transcoding failed' });
        }
      } catch {
        // network blip — keep polling
      }
    }, POLL_INTERVAL_MS);
  }, [updateProgress, onUploadComplete]);

  // ------------------------------------------------------------------
  // Zip upload function
  // ------------------------------------------------------------------

  const uploadZip = useCallback(async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);

    updateProgress(file.name, { status: 'uploading' as const, progress: 0, isZip: true });

    try {
      const resp = await fetch('/api/tracks/upload/zip', {
        method: 'POST',
        body: formData,
      });

      if (resp.ok) {
        updateProgress(file.name, { status: 'extracting' as const });
        const data = await resp.json();

        // Remove the zip row and expand into individual track rows
        setQueue(prev => {
          const withoutZip = prev.filter(p => p.filename !== file.name);
          const newRows: UploadProgress[] = (data.results ?? []).map((r: { filename: string; error?: string; track?: { id: number } }) => ({
            filename: r.filename,
            progress: 100,
            status: r.error ? 'error' as const : 'transcoding' as const,
            trackId: r.track?.id,
            error: r.error,
          }));
          return [...withoutZip, ...newRows];
        });

        // Poll for each extracted track
        (data.results ?? [])
          .filter((r: { track?: { id: number } }) => r.track?.id)
          .forEach((r: { track: { id: number }; filename: string }) => pollTrackStatus(r.track.id, r.filename));
      } else {
        updateProgress(file.name, { status: 'error' as const, error: 'Zip processing failed' });
      }
    } catch {
      updateProgress(file.name, { status: 'error' as const, error: 'Network error' });
    }
  }, [updateProgress, pollTrackStatus]);

  // ------------------------------------------------------------------
  // Core single-file upload (XHR for progress events)
  // ------------------------------------------------------------------

  const uploadFile = useCallback((file: File): Promise<void> => {
    return new Promise((resolve, reject) => {
      // Client-side validation
      const ext = getFileExt(file.name);
      if (!AUDIO_EXTENSIONS.includes(ext)) {
        updateProgress(file.name, { status: 'error', error: 'Unsupported format' });
        resolve(); // treated as handled, not re-queued
        return;
      }
      if (file.size > MAX_SIZE_MB * 1024 * 1024) {
        updateProgress(file.name, { status: 'error', error: `Too large (max ${MAX_SIZE_MB} MB)` });
        resolve();
        return;
      }

      const formData = new FormData();
      formData.append('file', file);

      const xhr = new XMLHttpRequest();

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          updateProgress(file.name, {
            progress: (e.loaded / e.total) * 100,
            status: 'uploading',
          });
        }
      };

      xhr.onload = () => {
        if (xhr.status === 201) {
          try {
            const data = JSON.parse(xhr.responseText) as { id: number };
            updateProgress(file.name, { status: 'transcoding', trackId: data.id, progress: 100 });
            pollTrackStatus(data.id, file.name);
          } catch {
            updateProgress(file.name, { status: 'error', error: 'Invalid server response' });
          }
          resolve();
        } else {
          let msg = 'Upload failed';
          try {
            const body = JSON.parse(xhr.responseText) as { detail?: string };
            if (body.detail) msg = body.detail;
          } catch { /* ignore */ }
          updateProgress(file.name, { status: 'error', error: msg });
          reject(new Error(msg));
        }
      };

      xhr.onerror = () => {
        updateProgress(file.name, { status: 'error', error: 'Network error' });
        reject(new Error('Network error'));
      };

      xhr.open('POST', '/api/tracks/upload');
      xhr.send(formData);
    });
  }, [updateProgress, pollTrackStatus]);

  // ------------------------------------------------------------------
  // Bulk upload (>1 audio files — single multipart request)
  // ------------------------------------------------------------------

  const uploadBulk = useCallback(async (files: File[]): Promise<void> => {
    const formData = new FormData();
    for (const f of files) {
      formData.append('files', f);
    }

    try {
      const resp = await fetch('/api/tracks/upload/bulk', {
        method: 'POST',
        body: formData,
      });

      if (resp.ok) {
        const data = await resp.json();
        const results: Array<{ filename: string; error?: string; track?: { id: number } }> = data.results ?? [];

        for (const r of results) {
          if (r.error) {
            updateProgress(r.filename, { status: 'error', error: r.error });
          } else if (r.track?.id) {
            updateProgress(r.filename, { status: 'transcoding', trackId: r.track.id, progress: 100 });
            pollTrackStatus(r.track.id, r.filename);
          }
        }
      } else {
        // Fallback: individual uploads
        await processQueueIndividual(files);
      }
    } catch {
      // Fallback: individual uploads
      await processQueueIndividual(files);
    }
  }, [updateProgress, pollTrackStatus]); // eslint-disable-line react-hooks/exhaustive-deps
  // Note: processQueueIndividual defined below — hoisted via ref pattern

  // ------------------------------------------------------------------
  // Concurrent individual-upload queue processor
  // ------------------------------------------------------------------

  const processQueueIndividual = useCallback(async (files: File[]) => {
    const pending = [...files];
    const workers = Array(Math.min(MAX_CONCURRENT, pending.length))
      .fill(null)
      .map(async () => {
        while (pending.length > 0) {
          const file = pending.shift()!;
          await uploadFile(file).catch(() => {}); // errors already reflected in state
        }
      });
    await Promise.all(workers);
  }, [uploadFile]);

  // Upload strategy: always use individual concurrent uploads.
  // Bulk endpoint is unreliable for large files (WAVs can be 100MB+)
  // and the concurrent worker pool already handles parallelism well.
  const processQueue = useCallback(async (files: File[]) => {
    await processQueueIndividual(files);
  }, [processQueueIndividual]);

  // ------------------------------------------------------------------
  // File intake — validates + enqueues + kicks off upload
  // ------------------------------------------------------------------

  const handleFiles = useCallback((files: FileList | File[]) => {
    const arr = Array.from(files);
    if (arr.length === 0) return;

    const zipFiles   = arr.filter(f => getFileExt(f.name) === '.zip');
    const audioFiles = arr.filter(f => getFileExt(f.name) !== '.zip');

    // Deduplicate against already-queued items
    let existingNames: Set<string> | undefined;

    setQueue(prev => {
      existingNames = new Set(prev.map(p => p.filename));

      const audioEntries: UploadProgress[] = audioFiles
        .filter(f => !existingNames!.has(f.name))
        .map(f => ({ filename: f.name, progress: 0, status: 'uploading' as const }));

      const zipEntries: UploadProgress[] = zipFiles
        .filter(f => !existingNames!.has(f.name))
        .map(f => ({ filename: f.name, progress: 0, status: 'uploading' as const, isZip: true }));

      return [...prev, ...audioEntries, ...zipEntries];
    });

    // Record file sizes for display
    setFileSizes(prev => {
      const updated = { ...prev };
      arr.forEach(f => { updated[f.name] = f.size; });
      return updated;
    });

    // Upload audio files
    if (audioFiles.length > 0) {
      void processQueue(audioFiles);
    }

    // Upload zip files via zip endpoint
    for (const zip of zipFiles) {
      void uploadZip(zip);
    }
  }, [processQueue, uploadZip]);

  // ------------------------------------------------------------------
  // Staged (batch) flow — queue files for review before uploading
  // ------------------------------------------------------------------

  const handleStagedFiles = useCallback((files: FileList | File[]) => {
    const arr = Array.from(files);
    if (arr.length === 0) return;

    if (arr.length > 1) {
      // Show staging UI so user can confirm before sending
      setPendingFiles(prev => {
        const existingNames = new Set(prev.map(f => f.name));
        return [...prev, ...arr.filter(f => !existingNames.has(f.name))];
      });
      setIsBatchMode(true);
    } else {
      // Single file: upload immediately
      handleFiles(arr);
    }
  }, [handleFiles]);

  const commitBatch = useCallback(() => {
    if (pendingFiles.length === 0) return;
    setIsBatchMode(false);
    handleFiles(pendingFiles);
    setPendingFiles([]);
  }, [pendingFiles, handleFiles]);

  const cancelBatch = useCallback(() => {
    setIsBatchMode(false);
    setPendingFiles([]);
  }, []);

  // ------------------------------------------------------------------
  // Drag-and-drop handlers
  // ------------------------------------------------------------------

  const onDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }, []);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = 'copy';
    setIsDragOver(true);
  }, []);

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) {
      setIsDragOver(false);
    }
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    handleStagedFiles(e.dataTransfer.files);
  }, [handleStagedFiles]);

  // ------------------------------------------------------------------
  // File input change handlers
  // ------------------------------------------------------------------

  const onFileInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      handleStagedFiles(e.target.files);
      e.target.value = '';
    }
  }, [handleStagedFiles]);

  const onFolderInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      // Filter to audio files only — folder may contain other file types
      const audioFiles = Array.from(e.target.files).filter(f => {
        const ext = getFileExt(f.name);
        return AUDIO_EXTENSIONS.includes(ext);
      });
      if (audioFiles.length > 0) {
        // Skip staging — start uploading immediately for folder picks
        handleFiles(audioFiles);
      }
      e.target.value = '';
    }
  }, [handleFiles]);

  // ------------------------------------------------------------------
  // Summary stats
  // ------------------------------------------------------------------

  const totalCount  = queue.length;
  const readyCount  = queue.filter(q => q.status === 'ready').length;
  const errorCount  = queue.filter(q => q.status === 'error').length;
  const activeCount = queue.filter(q => q.status === 'uploading' || q.status === 'transcoding' || q.status === 'extracting').length;
  const showBatchBar = totalCount > 1 && activeCount > 0;

  const clearCompleted = useCallback(() => {
    setQueue(prev => prev.filter(q => q.status !== 'ready'));
  }, []);

  // ------------------------------------------------------------------
  // Render guard
  // ------------------------------------------------------------------

  if (!visible) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Upload music"
      className="fixed inset-0 z-40 bg-black/95 flex flex-col"
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 pb-3 flex-shrink-0"
        style={{ paddingTop: 'max(1rem, env(safe-area-inset-top))' }}
      >
        <h2 className="text-lg font-semibold text-white tracking-tight">Upload Music</h2>
        <button
          onClick={onClose}
          className="w-9 h-9 flex items-center justify-center rounded-full
                     text-gray-400 hover:text-white hover:bg-gray-800/60
                     transition-colors focus-visible:outline-none focus-visible:ring-1
                     focus-visible:ring-indigo-500/60"
          aria-label="Close upload panel"
        >
          <XIcon className="w-5 h-5" />
        </button>
      </div>

      {/* Scrollable body */}
      <div
        className="flex-1 overflow-y-auto px-4 overscroll-contain"
        style={{ WebkitOverflowScrolling: 'touch' } as React.CSSProperties}
      >
        {/* Drag-drop zone */}
        <div
          onDragEnter={onDragEnter}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          role="region"
          aria-label="Drop zone for audio files"
          className={`
            w-full rounded-2xl border-2 border-dashed transition-colors duration-150 mb-5
            flex flex-col items-center justify-center gap-3 py-10 px-6 text-center
            ${isDragOver
              ? 'border-indigo-500 bg-indigo-500/5'
              : 'border-gray-700 bg-gray-900/40 hover:border-gray-600 hover:bg-gray-900/60'}
          `}
        >
          <CloudUploadIcon
            className={`w-10 h-10 transition-colors duration-150 ${isDragOver ? 'text-indigo-400' : 'text-gray-600'}`}
          />
          <div>
            <p className={`text-sm font-medium transition-colors duration-150 ${isDragOver ? 'text-indigo-300' : 'text-gray-300'}`}>
              {isDragOver ? 'Release to upload' : 'Drop music files or a folder here'}
            </p>
            <p className="text-xs text-gray-600 mt-1">
              or use the buttons below
            </p>
          </div>
        </div>

        {/* File picker buttons */}
        <div className="flex flex-col items-center gap-3 mb-6">
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*,.zip"
            multiple
            className="sr-only"
            aria-label="Choose audio files"
            tabIndex={-1}
            onChange={onFileInputChange}
          />
          {/* Hidden folder input */}
          <input
            ref={folderInputRef}
            type="file"
            // @ts-expect-error — webkitdirectory is not in React types
            webkitdirectory=""
            directory=""
            multiple
            className="sr-only"
            aria-label="Choose a folder of audio files"
            tabIndex={-1}
            onChange={onFolderInputChange}
          />

          {/* Button row */}
          <div className="flex items-center gap-3">
            <button
              onClick={() => fileInputRef.current?.click()}
              className="min-h-[44px] px-6 py-2.5 rounded-xl bg-indigo-500 hover:bg-indigo-400 active:bg-indigo-600
                         text-white text-sm font-semibold transition-colors
                         focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-black"
            >
              Choose Files
            </button>
            <button
              onClick={() => folderInputRef.current?.click()}
              className="min-h-[44px] px-4 py-2.5 rounded-xl bg-gray-800 hover:bg-gray-700 active:bg-gray-900
                         text-gray-300 text-sm font-medium transition-colors flex items-center gap-2
                         border border-gray-700 hover:border-gray-600
                         focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-500 focus-visible:ring-offset-2 focus-visible:ring-offset-black"
              aria-label="Choose a folder to upload"
            >
              <FolderIcon className="w-4 h-4" />
              Choose Folder
            </button>
          </div>

          {/* Accepted formats list */}
          <p className="text-xs text-gray-600 text-center leading-relaxed">
            {ACCEPTED_EXTENSIONS.join('  ')}
            <span className="block mt-0.5">Max {MAX_SIZE_MB} MB per file</span>
          </p>
        </div>

        {/* Batch staging area — shown when multiple files are staged but not yet sent */}
        {isBatchMode && pendingFiles.length > 0 && (
          <div className="mb-5 rounded-2xl border border-indigo-500/40 bg-indigo-950/30 px-4 py-4">
            <div className="flex items-center justify-between mb-3">
              <p className="text-sm font-medium text-indigo-300">
                <span className="inline-flex items-center gap-1.5">
                  <span className="inline-flex items-center justify-center min-w-[1.5rem] h-6 px-1.5 rounded-full
                                   bg-indigo-500 text-white text-xs font-bold tabular-nums">
                    {pendingFiles.length}
                  </span>
                  {pendingFiles.length === 1 ? 'file' : 'files'} selected
                </span>
              </p>
              <p className="text-xs text-gray-500">
                {formatBytes(pendingFiles.reduce((s, f) => s + f.size, 0))} total
              </p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={commitBatch}
                className="flex-1 min-h-[44px] rounded-xl bg-indigo-500 hover:bg-indigo-400 active:bg-indigo-600
                           text-white text-sm font-semibold transition-colors
                           focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-black"
              >
                Upload All
              </button>
              <button
                onClick={cancelBatch}
                className="min-h-[44px] px-4 rounded-xl bg-gray-800 hover:bg-gray-700 active:bg-gray-900
                           text-gray-400 text-sm font-medium transition-colors border border-gray-700
                           focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-500 focus-visible:ring-offset-2 focus-visible:ring-offset-black"
                aria-label="Cancel batch upload"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Batch summary progress bar — visible while a multi-file batch is in progress */}
        {showBatchBar && (
          <BatchProgressBar
            total={totalCount}
            ready={readyCount}
            errors={errorCount}
            active={activeCount}
          />
        )}

        {/* Upload queue */}
        {queue.length > 0 && (
          <section aria-label="Upload queue">
            {/* Section header + clear button */}
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                Queue
                {activeCount > 0 && (
                  <span className="ml-2 text-indigo-400 normal-case font-normal">
                    {activeCount} in progress
                  </span>
                )}
                {/* File count badge */}
                {totalCount > 0 && (
                  <span className="ml-2 inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1
                                   rounded-full bg-gray-800 text-gray-400 normal-case font-normal text-xs tabular-nums">
                    {totalCount}
                  </span>
                )}
              </h3>
              {readyCount > 0 && (
                <button
                  onClick={clearCompleted}
                  className="text-xs text-gray-500 hover:text-gray-300 transition-colors
                             focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500/60 rounded"
                >
                  Clear completed
                </button>
              )}
            </div>

            {/* File rows */}
            <div
              className="rounded-xl bg-gray-900/50 border border-gray-800 px-3"
              role="list"
              aria-label="Uploading files"
              aria-live="polite"
            >
              {queue.map(item => (
                <div key={item.filename} role="listitem">
                  <UploadRow item={item} fileSize={fileSizes[item.filename]} />
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Summary bar — shown when batch is complete */}
        {totalCount > 0 && activeCount === 0 && (
          <div
            className="mt-4 flex items-center justify-between gap-3 rounded-xl
                       bg-gray-900/60 border border-gray-800 px-4 py-3"
            role="status"
            aria-live="polite"
          >
            <p className="text-sm text-gray-400">
              {readyCount > 0 && errorCount === 0 && (
                <span className="text-green-400 font-medium">{readyCount} uploaded successfully.</span>
              )}
              {readyCount > 0 && errorCount > 0 && (
                <>
                  <span className="text-green-400 font-medium">{readyCount}/{totalCount} uploaded.</span>
                  {' '}
                  <span className="text-red-400">{errorCount} failed.</span>
                </>
              )}
              {readyCount === 0 && errorCount > 0 && (
                <span className="text-red-400 font-medium">All {errorCount} uploads failed.</span>
              )}
            </p>
            {readyCount > 0 && (
              <button
                onClick={clearCompleted}
                className="text-xs text-gray-500 hover:text-gray-300 transition-colors flex-shrink-0
                           focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500/60 rounded"
              >
                Clear completed
              </button>
            )}
          </div>
        )}

        {/* Bottom safe-area padding */}
        <div style={{ height: 'max(1.5rem, env(safe-area-inset-bottom))' }} aria-hidden="true" />
      </div>
    </div>
  );
}
