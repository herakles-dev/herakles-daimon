"use client";

import { useState, useEffect, useRef, useCallback } from "react";

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

export interface ChatMessage {
  id: string;
  author: string;
  text: string;
  type: "regular" | "superchat" | "membership";
  amount?: string;   // e.g. "$5.00"
  color?: string;    // Tier color hex from backend
  timestamp: number;
}

interface BroadcastChatHookResult {
  messages: ChatMessage[];
  currentSuperChat: ChatMessage | null;
  isConnected: boolean;
}

// ------------------------------------------------------------------
// Unified tier info — single source of truth for frontend display
// Thresholds match backend SUPERCHAT_TIERS and TIER_COLORS
// ------------------------------------------------------------------

export interface TierInfo {
  label: string;
  name: "blue" | "yellow" | "orange" | "red" | "diamond";
  color: string;
}

export function getTierInfo(amount?: string): TierInfo {
  if (!amount) return { label: "Super Chat", name: "blue", color: "#1565C0" };
  const n = parseFloat(amount.replace(/[^0-9.]/g, ""));
  if (isNaN(n)) return { label: "Super Chat", name: "blue", color: "#1565C0" };
  if (n >= 25) return { label: "Diamond", name: "diamond", color: "#7C3AED" };
  if (n >= 10) return { label: "VIP", name: "red", color: "#E62117" };
  if (n >= 5)  return { label: "Super Chat", name: "orange", color: "#F57C00" };
  if (n >= 2)  return { label: "Super Chat", name: "yellow", color: "#FFCA28" };
  return { label: "Super Chat", name: "blue", color: "#1565C0" };
}

/** Convenience — returns just the color. Prefer getTierInfo() for full info. */
export function superChatColor(amount?: string): string {
  return getTierInfo(amount).color;
}

// ------------------------------------------------------------------
// Hook
// ------------------------------------------------------------------

const MAX_MESSAGES = 100;
const MAX_SUPERCHAT_QUEUE = 10;
const SUPERCHAT_DISPLAY_MS = 8000;
const SUPERCHAT_GAP_MS = 500;
const RECONNECT_DELAY_MS = 3000;

export function useBroadcastChat(): BroadcastChatHookResult {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [superChatQueue, setSuperChatQueue] = useState<ChatMessage[]>([]);
  const [currentSuperChat, setCurrentSuperChat] = useState<ChatMessage | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const superChatTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const superChatGapTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const isShowingRef = useRef(false);
  const connectGenRef = useRef(0); // generation counter to discard stale reconnects

  // Drain the super chat queue — shows one at a time for SUPERCHAT_DISPLAY_MS
  const drainSuperChatQueue = useCallback(() => {
    if (!mountedRef.current || isShowingRef.current) return;

    setSuperChatQueue((prev) => {
      if (prev.length === 0) return prev;
      const [next, ...rest] = prev;
      isShowingRef.current = true;
      setCurrentSuperChat(next);
      return rest;
    });

    // Schedule clear + gap outside of setState (concurrent-mode safe)
    if (superChatTimerRef.current) clearTimeout(superChatTimerRef.current);
    superChatTimerRef.current = setTimeout(() => {
      if (!mountedRef.current) return;
      setCurrentSuperChat(null);
      isShowingRef.current = false;

      if (superChatGapTimerRef.current) clearTimeout(superChatGapTimerRef.current);
      superChatGapTimerRef.current = setTimeout(() => {
        if (!mountedRef.current) return;
        drainSuperChatQueue();
      }, SUPERCHAT_GAP_MS);
    }, SUPERCHAT_DISPLAY_MS);
  }, []);

  // When a new super chat arrives and nothing is showing, start displaying
  useEffect(() => {
    if (!isShowingRef.current && superChatQueue.length > 0) {
      drainSuperChatQueue();
    }
  }, [superChatQueue, drainSuperChatQueue]);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) return;

    const gen = ++connectGenRef.current;

    const wsBase =
      typeof window !== "undefined"
        ? window.location.protocol === "https:"
          ? `wss://${window.location.host}`
          : `ws://${window.location.host}`
        : `ws://localhost:${process.env.NEXT_PUBLIC_BACKEND_PORT || "8150"}`;

    // Backend serves the WS. In dev (port 3000 or 8151), go direct to backend.
    const port = typeof window !== "undefined" ? window.location.port : "";
    const isDev = port === "3000" || port === "8151";
    const wsUrl = isDev
      ? `ws://localhost:${process.env.NEXT_PUBLIC_BACKEND_PORT || "8150"}/ws/broadcast-chat`
      : `${wsBase}/ws/broadcast-chat`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setIsConnected(true);
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse(event.data as string) as {
          type: string;
          message?: Record<string, unknown>;
        };

        if (data.type === "ping") return;
        if (!data.message) return;

        const raw = data.message;

        // Validate required fields with safe defaults
        const normalizedMsg: ChatMessage = {
          id: typeof raw.id === "string" ? raw.id : `${Date.now()}-${Math.random()}`,
          author: typeof raw.author === "string" && raw.author ? raw.author : "Unknown",
          text: typeof raw.text === "string" ? raw.text.slice(0, 2000) : "",
          type:
            data.type === "superchat" || data.type === "membership"
              ? data.type
              : typeof raw.type === "string" && (raw.type === "superchat" || raw.type === "membership")
                ? (raw.type as ChatMessage["type"])
                : "regular",
          amount: typeof raw.amount === "string" ? raw.amount : undefined,
          color: typeof raw.color === "string" ? raw.color : undefined,
          timestamp: typeof raw.timestamp === "number" ? raw.timestamp : Date.now(),
        };

        // All messages go into the scrolling panel (single-copy to reduce GC pressure)
        setMessages((prev) =>
          prev.length >= MAX_MESSAGES
            ? [...prev.slice(1), normalizedMsg]
            : [...prev, normalizedMsg]
        );

        // Super chats and memberships also go into the popup queue
        if (normalizedMsg.type === "superchat" || normalizedMsg.type === "membership") {
          setSuperChatQueue((prev) =>
            prev.length >= MAX_SUPERCHAT_QUEUE ? prev : [...prev, normalizedMsg]
          );
        }
      } catch (err) {
        console.warn("[BroadcastChat] Failed to parse message:", err);
      }
    };

    ws.onerror = (err) => {
      console.warn("[BroadcastChat] WebSocket error:", err);
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      // Only reconnect if this is still the current generation (prevent stale timers)
      if (gen !== connectGenRef.current) return;
      setIsConnected(false);
      wsRef.current = null;
      reconnectTimerRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
    };
  }, []);

  // Mount — connect once
  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (superChatTimerRef.current) clearTimeout(superChatTimerRef.current);
      if (superChatGapTimerRef.current) clearTimeout(superChatGapTimerRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

  return { messages, currentSuperChat, isConnected };
}
