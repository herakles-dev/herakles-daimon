import {
  type ClientMessage,
  type ServerMessage,
  type ConnectionStatus,
  type ToolResponse,
  type MuseSettings,
  type PlaybackContext,
} from "./types";
import {
  WS_URL,
  RECONNECT_DELAY_MS,
  MAX_RECONNECT_ATTEMPTS,
  GEMINI_TOOLS,
} from "./constants";

type MessageHandler = (msg: ServerMessage) => void;
type StatusHandler = (status: ConnectionStatus) => void;

/**
 * Low-level WebSocket manager for the Gemini Multimodal Live API.
 *
 * Handles:
 * - Connection lifecycle + auto-reconnect + session resumption
 * - Setup message with tool declarations (prompt + voice are server-side)
 * - Sending audio chunks, text, interrupts, tool responses
 * - Dispatching parsed server messages to registered handlers
 *
 * This class is framework-agnostic — the React hook wraps it.
 */
export class GeminiWebSocket {
  private ws: WebSocket | null = null;
  private status: ConnectionStatus = "disconnected";
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private messageHandlers = new Set<MessageHandler>();
  private statusHandlers = new Set<StatusHandler>();
  private resumeHandle: string | null = null;
  private settings: Partial<MuseSettings> | null = null;
  private playbackContextProvider: (() => PlaybackContext) | null = null;
  private broadcastMode = false;

  /** Enable broadcast mode — tells backend this is the livestream session */
  setBroadcastMode(enabled: boolean): void {
    this.broadcastMode = enabled;
  }

  /** Update settings — takes effect on next connect/reconnect */
  setSettings(settings: Partial<MuseSettings>): void {
    this.settings = settings;
  }

  /** Set a provider that returns the current playback state.
   *  Called fresh inside sendSetup() on every connect/reconnect
   *  so Muse always knows what's currently playing. */
  setPlaybackContextProvider(provider: (() => PlaybackContext) | null): void {
    this.playbackContextProvider = provider;
  }

  /** Register a handler for incoming Gemini messages */
  onMessage(handler: MessageHandler): () => void {
    this.messageHandlers.add(handler);
    return () => this.messageHandlers.delete(handler);
  }

  /** Register a handler for connection status changes */
  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler);
    return () => this.statusHandlers.delete(handler);
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  /** Connect to the backend WebSocket proxy */
  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return;
    this.setStatus("connecting");

    try {
      // Broadcast mode runs on localhost — bypass SSO, connect directly to backend
      const url = this.broadcastMode && typeof window !== "undefined" && window.location.hostname === "localhost"
        ? `ws://localhost:${typeof process !== "undefined" && process.env?.NEXT_PUBLIC_BACKEND_PORT || "8150"}/ws/gemini`
        : WS_URL;
      this.ws = new WebSocket(url);
      this.ws.binaryType = "arraybuffer";

      this.ws.onopen = () => {
        this.reconnectAttempts = 0;
        this.setStatus("connected");
        this.sendSetup();
      };

      this.ws.onmessage = (event) => {
        try {
          const msg: ServerMessage = JSON.parse(event.data);
          if (msg.type === "setupComplete") {
            this.setStatus("ready");
          }
          // Store session resume handle for reconnects
          if (msg.type === "sessionResumeHandle") {
            this.resumeHandle = msg.handle;
          }
          // Dispatch all messages to handlers first
          this.messageHandlers.forEach((h) => h(msg));
          // Gemini is about to kill the session — reconnect immediately
          if (msg.type === "goAway") {
            console.warn("[GeminiWS] Received goAway — reconnecting");
            this.ws?.close(1000, "goAway");
            return;
          }
        } catch {
          console.error("[GeminiWS] Failed to parse message:", event.data);
        }
      };

      this.ws.onclose = (event) => {
        console.warn("[GeminiWS] Closed:", event.code, event.reason);
        this.ws = null;
        this.setStatus("disconnected");
        this.scheduleReconnect();
      };

      this.ws.onerror = (event) => {
        console.error("[GeminiWS] Error:", event);
        this.setStatus("error");
      };
    } catch (err) {
      console.error("[GeminiWS] Connection failed:", err);
      this.setStatus("error");
      this.scheduleReconnect();
    }
  }

  /** Disconnect and stop reconnecting */
  disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.reconnectAttempts = MAX_RECONNECT_ATTEMPTS; // prevent reconnect
    if (this.ws) {
      this.ws.close(1000, "Client disconnect");
      this.ws = null;
    }
    this.setStatus("disconnected");
  }

  /** Send base64-encoded PCM16 audio chunk */
  sendAudio(base64Data: string): void {
    this.send({ type: "audio", data: base64Data });
  }

  /** Send a text message (e.g., skip command injection) */
  sendText(text: string): void {
    this.send({ type: "text", text });
  }

  /** Interrupt Gemini's current response (barge-in) */
  sendInterrupt(): void {
    this.send({ type: "interrupt" });
  }

  /** Send tool execution result back to Gemini */
  sendToolResponse(response: ToolResponse): void {
    this.send({ type: "toolResponse", toolResponse: response });
  }


  // ------------------------------------------------------------------
  // Private
  // ------------------------------------------------------------------

  private sendSetup(): void {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const msg: any = {
      type: "setup",
      tools: GEMINI_TOOLS,
    };
    // Include resume handle if reconnecting — preserves Gemini session context
    if (this.resumeHandle) {
      msg.resumeHandle = this.resumeHandle;
      console.log("[GeminiWS] Resuming session with handle");
    }
    // Include user settings if set — backend applies to Gemini config
    if (this.settings) {
      msg.settings = this.settings;
    }
    // Broadcast mode — tells backend to register this session for chat injection
    if (this.broadcastMode) {
      msg.broadcast = true;
    }
    // Get a live snapshot of playback state — fresh on every connect/reconnect
    if (this.playbackContextProvider) {
      const ctx = this.playbackContextProvider();
      if (ctx.contentMode !== "idle") {
        msg.playbackContext = ctx;
      }
    }
    this.send(msg);
  }

  private send(msg: ClientMessage): void {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      console.warn("[GeminiWS] Cannot send — not connected");
      return;
    }
    this.ws.send(JSON.stringify(msg));
  }

  private setStatus(status: ConnectionStatus): void {
    this.status = status;
    this.statusHandlers.forEach((h) => h(status));
  }

  private scheduleReconnect(): void {
    // In broadcast mode, never give up — the stream must stay alive
    const maxAttempts = this.broadcastMode ? Infinity : MAX_RECONNECT_ATTEMPTS;
    if (this.reconnectAttempts >= maxAttempts) {
      console.error("[GeminiWS] Max reconnect attempts reached");
      this.setStatus("error");
      return;
    }
    // Cap backoff at 30s for broadcast (don't wait 2+ minutes between retries)
    const maxDelay = this.broadcastMode ? 30000 : 64000;
    const delay = Math.min(RECONNECT_DELAY_MS * Math.pow(2, this.reconnectAttempts), maxDelay);
    this.reconnectAttempts++;
    console.log(
      `[GeminiWS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}${this.broadcastMode ? "" : "/" + MAX_RECONNECT_ATTEMPTS})`
    );
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }
}
