"use client";

import { useEffect, useRef } from "react";
import type { ChatMessage } from "@/hooks/useBroadcastChat";
import { getTierInfo } from "@/hooks/useBroadcastChat";

// ------------------------------------------------------------------
// Props
// ------------------------------------------------------------------

interface BroadcastChatProps {
  messages: ChatMessage[];
  currentSuperChat: ChatMessage | null;
}

// ------------------------------------------------------------------
// Individual chat message row
// ------------------------------------------------------------------

function ChatRow({ msg }: { msg: ChatMessage }) {
  const isSpecial = msg.type === "superchat" || msg.type === "membership";
  const isMembership = msg.type === "membership";
  const tier = isSpecial ? getTierInfo(msg.amount) : null;
  const tierColor = isMembership ? "#22c55e" : (msg.color || tier?.color || null);

  return (
    <div
      className="chat-row"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "3px",
        padding: isSpecial ? "12px 18px" : "8px 18px",
        borderRadius: "12px",
        background: isSpecial ? `${tierColor}18` : "transparent",
        borderLeft: isSpecial
          ? `5px solid ${tierColor}`
          : "5px solid transparent",
        animation: "chatSlideIn 0.25s ease-out forwards",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
        <span
          style={{
            fontSize: "22px",
            fontWeight: 700,
            color: isSpecial
              ? (tierColor ?? "#fff")
              : "rgba(255,255,255,0.6)",
            flexShrink: 0,
            maxWidth: "240px",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {msg.author}
        </span>
        {msg.type === "superchat" && msg.amount && (
          <span
            style={{
              fontSize: "18px",
              fontWeight: 700,
              color: tierColor ?? "#fff",
              background: `${tierColor}28`,
              borderRadius: "6px",
              padding: "3px 10px",
              flexShrink: 0,
            }}
          >
            {msg.amount}
          </span>
        )}
        {isMembership && (
          <span
            style={{
              fontSize: "16px",
              fontWeight: 700,
              color: "#22c55e",
              background: "rgba(34,197,94,0.15)",
              borderRadius: "6px",
              padding: "3px 10px",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            New Member
          </span>
        )}
      </div>
      <span
        style={{
          fontSize: "24px",
          color: "rgba(255,255,255,0.9)",
          lineHeight: 1.4,
          wordBreak: "break-word",
        }}
      >
        {msg.text.slice(0, 300)}
      </span>
    </div>
  );
}

// ------------------------------------------------------------------
// Super Chat popup — center screen, animated glow
// ------------------------------------------------------------------

function SuperChatPopup({ msg }: { msg: ChatMessage }) {
  const tier = getTierInfo(msg.amount);
  const color = msg.color || tier.color;
  const isMembership = msg.type === "membership";
  const isDiamond = tier.name === "diamond";
  const isVIP = tier.name === "red" || isDiamond;

  return (
    <div
      style={{
        position: "absolute",
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
        zIndex: 100,
        pointerEvents: "none",
        animation:
          "superChatAppear 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) forwards",
      }}
    >
      {isVIP && (
        <div
          style={{
            position: "absolute",
            inset: "-8px",
            borderRadius: "22px",
            background: `linear-gradient(135deg, ${color}44, transparent, ${color}33)`,
            filter: "blur(12px)",
          }}
        />
      )}

      <div
        style={{
          position: "relative",
          background: "rgba(8, 8, 12, 0.94)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          border: `2px solid ${color}`,
          borderRadius: "16px",
          padding: isDiamond ? "28px 36px" : "22px 28px",
          minWidth: isDiamond ? "420px" : "340px",
          maxWidth: "520px",
          boxShadow: `0 0 ${isVIP ? "60" : "30"}px ${color}44, 0 0 ${isVIP ? "100" : "50"}px ${color}18, 0 20px 60px rgba(0,0,0,0.8)`,
        }}
      >
        <div
          style={{
            position: "absolute",
            top: 0,
            left: "20%",
            right: "20%",
            height: "2px",
            background: `linear-gradient(90deg, transparent, ${color}, transparent)`,
            borderRadius: "0 0 2px 2px",
          }}
        />

        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: msg.text ? "14px" : 0,
            gap: "16px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "12px", minWidth: 0 }}>
            <div
              style={{
                width: isDiamond ? "48px" : "40px",
                height: isDiamond ? "48px" : "40px",
                borderRadius: "50%",
                background: `linear-gradient(135deg, ${color}55, ${color}22)`,
                border: `2px solid ${color}88`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: isDiamond ? "20px" : "17px",
                fontWeight: 700,
                color: color,
                flexShrink: 0,
              }}
            >
              {(msg.author[0] ?? "?").toUpperCase()}
            </div>
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontSize: isDiamond ? "20px" : "17px",
                  fontWeight: 700,
                  color: "#fff",
                  lineHeight: 1.2,
                  maxWidth: "240px",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {msg.author}
              </div>
              <div
                style={{
                  fontSize: "11px",
                  color: `${color}cc`,
                  textTransform: "uppercase",
                  letterSpacing: "0.1em",
                  fontWeight: 600,
                  marginTop: "2px",
                }}
              >
                {isMembership ? "New Member" : tier.label}
              </div>
            </div>
          </div>

          {msg.amount && (
            <div
              style={{
                fontSize: isDiamond ? "26px" : "22px",
                fontWeight: 800,
                color: color,
                background: `${color}18`,
                borderRadius: "10px",
                padding: "6px 14px",
                border: `1px solid ${color}44`,
                flexShrink: 0,
                textShadow: `0 0 20px ${color}44`,
              }}
            >
              {msg.amount}
            </div>
          )}
          {isMembership && !msg.amount && (
            <div
              style={{
                fontSize: "14px",
                fontWeight: 700,
                color: "#22c55e",
                background: "rgba(34,197,94,0.12)",
                borderRadius: "10px",
                padding: "6px 14px",
                border: "1px solid rgba(34,197,94,0.3)",
                flexShrink: 0,
              }}
            >
              Welcome!
            </div>
          )}
        </div>

        {msg.text && (
          <div
            style={{
              fontSize: isDiamond ? "18px" : "16px",
              color: "rgba(255,255,255,0.92)",
              lineHeight: 1.5,
              wordBreak: "break-word",
              borderTop: `1px solid ${color}22`,
              paddingTop: "12px",
            }}
          >
            {msg.text.slice(0, 300)}
          </div>
        )}

        {isVIP && (
          <div
            style={{
              marginTop: "10px",
              fontSize: "11px",
              color: `${color}88`,
              letterSpacing: "0.06em",
            }}
          >
            {isDiamond
              ? "Full VIP segment \u2014 10 messages + any request"
              : "VIP \u2014 5 messages + video request"}
          </div>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// BroadcastChat — the full overlay component
// ------------------------------------------------------------------

export function BroadcastChat({
  messages,
  currentSuperChat,
}: BroadcastChatProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const visibleMessages = messages.slice(-12);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  const hasMessages = visibleMessages.length > 0;

  return (
    <>
      {currentSuperChat && <SuperChatPopup msg={currentSuperChat} />}

      {/* Chat panel — bottom-left, frosted glass */}
      {hasMessages && (
        <div
          style={{
            position: "absolute",
            bottom: "32px",
            left: "36px",
            width: "400px",
            maxHeight: "380px",
            display: "flex",
            flexDirection: "column",
            pointerEvents: "none",
            zIndex: 10,
          }}
        >
          <div
            style={{
              background: "rgba(10, 10, 15, 0.45)",
              backdropFilter: "blur(24px) saturate(1.2)",
              WebkitBackdropFilter: "blur(24px) saturate(1.2)",
              borderRadius: "22px",
              border: "1.5px solid rgba(255,255,255,0.08)",
              boxShadow: "0 12px 40px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05)",
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
            }}
          >
            {/* Header */}
            <div
              style={{
                padding: "16px 22px 12px",
                fontSize: "20px",
                fontWeight: 600,
                color: "rgba(255,255,255,0.35)",
                textTransform: "uppercase",
                letterSpacing: "0.12em",
                borderBottom: "1px solid rgba(255,255,255,0.06)",
                display: "flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              <div
                style={{
                  width: "10px",
                  height: "10px",
                  borderRadius: "50%",
                  background: "rgba(34,197,94,0.8)",
                  boxShadow: "0 0 8px rgba(34,197,94,0.4)",
                }}
              />
              Live Chat
            </div>

            {/* Messages */}
            <div
              ref={scrollRef}
              style={{
                overflowY: "auto",
                padding: "10px 8px",
                display: "flex",
                flexDirection: "column",
                gap: "6px",
                maskImage:
                  "linear-gradient(to bottom, transparent 0%, black 10%, black 100%)",
                WebkitMaskImage:
                  "linear-gradient(to bottom, transparent 0%, black 10%, black 100%)",
              }}
            >
              {visibleMessages.map((msg) => (
                <ChatRow key={msg.id} msg={msg} />
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
