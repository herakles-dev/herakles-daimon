'use client';

import { useInstallPrompt } from '@/hooks/useInstallPrompt';

export default function InstallBanner() {
  const { canInstall, install, dismiss } = useInstallPrompt();

  if (!canInstall) return null;

  return (
    <div
      className="fixed top-0 left-0 right-0 z-50 bg-gray-900/95 backdrop-blur-lg border-b border-gray-800/50 px-4 py-3"
      style={{ paddingTop: 'max(0.75rem, env(safe-area-inset-top))' }}
    >
      <div className="flex items-center gap-3 max-w-md mx-auto">
        <div className="flex-1">
          <p className="text-sm font-medium text-white">Install Play</p>
          <p className="text-xs text-gray-400">Best music experience with offline support</p>
        </div>
        <button
          onClick={install}
          className="px-4 py-1.5 bg-indigo-500 text-white text-sm font-medium rounded-full hover:bg-indigo-400 transition-colors"
        >
          Install
        </button>
        <button
          onClick={dismiss}
          className="text-gray-500 hover:text-gray-300 p-1"
          aria-label="Dismiss install prompt"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  );
}
