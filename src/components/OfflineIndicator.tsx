'use client';

import { useState, useEffect } from 'react';

export default function OfflineIndicator() {
  const [isOnline, setIsOnline] = useState(true);

  useEffect(() => {
    setIsOnline(navigator.onLine);
    const on = () => setIsOnline(true);
    const off = () => setIsOnline(false);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => {
      window.removeEventListener('online', on);
      window.removeEventListener('offline', off);
    };
  }, []);

  if (isOnline) return null;

  return (
    <div
      className="fixed top-0 left-0 right-0 z-50 bg-amber-900/90 text-amber-200 text-xs text-center py-1.5 backdrop-blur"
      style={{ paddingTop: 'max(0.375rem, env(safe-area-inset-top))' }}
    >
      Offline — playing cached music
    </div>
  );
}
