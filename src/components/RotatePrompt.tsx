"use client";

import { useState, useEffect } from "react";

/**
 * RotatePrompt — shown when the device is in portrait orientation.
 *
 * Behaviour:
 *  - Appears immediately on portrait mount.
 *  - Auto-dismisses after 3 seconds.
 *  - Also dismisses when the device rotates to landscape.
 *  - Uses the CSS `.rotate-prompt` class (defined in globals.css) so that
 *    the element is display:none in landscape even if JS state hasn't caught
 *    up yet — no flash on desktop or landscape-first loads.
 */
export function RotatePrompt() {
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    // Auto-dismiss timer
    const timer = setTimeout(() => setDismissed(true), 3000);

    // Dismiss on rotate to landscape
    const mq = window.matchMedia("(orientation: landscape)");
    const onOrientationChange = (e: MediaQueryListEvent) => {
      if (e.matches) setDismissed(true);
    };

    mq.addEventListener("change", onOrientationChange);

    return () => {
      clearTimeout(timer);
      mq.removeEventListener("change", onOrientationChange);
    };
  }, []);

  if (dismissed) return null;

  return (
    // rotate-prompt class is display:none in landscape via globals.css
    <div className="rotate-prompt" aria-hidden="true">
      <RotateIcon />
      <span>Rotate for best experience</span>
    </div>
  );
}

function RotateIcon() {
  return (
    <svg
      className="rotate-hint-icon"
      width="40"
      height="40"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {/* Phone outline */}
      <rect x="5" y="2" width="14" height="20" rx="2" ry="2" />
      {/* Rotation arrow hint */}
      <path d="M12 18h.01" />
    </svg>
  );
}
