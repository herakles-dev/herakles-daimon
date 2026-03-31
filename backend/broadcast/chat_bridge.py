"""
YouTube Chat Bridge — reads live chat and Super Chats, bridges to Daimon.

Two-layer approach:
  1. Official YouTube Live Streaming API (OAuth2) — reliable Super Chat detection
  2. Innertube API fallback — no auth needed, scrapes live chat directly

Messages flow:
  YouTube Chat → ChatBridge → chat_subscribers (→ frontend overlay via WS)
                            → _broadcast_inject_queue in main.py (→ Daimon via _inject_text)
"""

import asyncio
import json
import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger("broadcast.chat_bridge")

# Proxy is optional — set SOCKS_PROXY env var to route through VPN
YOUTUBE_PROXY = os.environ.get("SOCKS_PROXY", "")


class MessageType(str, Enum):
    REGULAR = "regular"
    SUPERCHAT = "superchat"
    SUPER_STICKER = "super_sticker"
    MEMBERSHIP = "membership"


# Super Chat tier colors (low thresholds for a growing stream)
TIER_COLORS = {
    1_000_000: "#1565C0",   # Blue: $1-$1.99
    2_000_000: "#FFCA28",   # Yellow: $2-$4.99
    5_000_000: "#F57C00",   # Orange: $5-$9.99
    10_000_000: "#E62117",  # Red: $10-$24.99
    25_000_000: "#7C3AED",  # Diamond: $25+ (purple)
}


def _tier_color(amount_micros: int) -> str:
    """Get Super Chat tier color based on amount."""
    color = "#1565C0"  # default blue
    for threshold, c in sorted(TIER_COLORS.items()):
        if amount_micros >= threshold:
            color = c
    return color


def _sanitize_viewer_text(text: str, max_len: int = 200) -> str:
    """Sanitize individual viewer message text. Strips control chars but preserves readability."""
    text = re.sub(r'[\x00-\x1f]', '', text)  # strip control chars
    # Strip Unicode bidi overrides, zero-width chars, and line/paragraph separators
    text = re.sub(r'[\u200e\u200f\u202a-\u202e\u2066-\u2069\u200b\u200c\u200d\ufeff\u2028\u2029]', '', text)
    return text[:max_len].strip()


@dataclass
class ChatMessage:
    id: str
    author: str
    text: str
    type: MessageType = MessageType.REGULAR
    amount: str | None = None
    amount_micros: int = 0
    currency: str = "USD"
    color: str | None = None
    timestamp: float = field(default_factory=time.time)
    profile_image: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d

    def to_muse_text(self) -> str:
        """Format message for Daimon text injection."""
        safe_text = _sanitize_viewer_text(self.text)
        # Sanitize author: strip privileged prefixes to prevent spoofing
        safe_author = _sanitize_viewer_text(self.author, max_len=50)
        safe_author = re.sub(
            r'(?i)Super\s*Chat|SYSTEM|Daimon|Muse|LIVE\s*CHAT',
            '', safe_author,
        ).strip() or "viewer"
        if self.type in (MessageType.SUPERCHAT, MessageType.SUPER_STICKER):
            return (
                f"[LIVE CHAT — Super Chat from {safe_author} "
                f"({self.amount})]: {safe_text}"
            )
        elif self.type == MessageType.MEMBERSHIP:
            return f"[LIVE CHAT — New member {safe_author} just joined!]"
        else:
            return f"[LIVE CHAT — {safe_author}]: {safe_text}"


class ChatBridge:
    """Manages YouTube chat reading and message distribution."""

    def __init__(self):
        self.chat_subscribers: list[asyncio.Queue] = []

        # State
        self._running = False
        self._live_chat_id: str | None = None
        self._current_video_id: str | None = None
        self._task: asyncio.Task | None = None
        self._innertube_task: asyncio.Task | None = None
        self._flush_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None

        # Rate limiting for regular chat → Daimon injection
        # 0.0 means first regular chat message triggers an immediate flush
        self._last_regular_inject = 0.0
        self._regular_inject_interval = 15.0
        self._regular_chat_buffer: list[ChatMessage] = []

        self.stats = {
            "messages_read": 0,
            "superchats_read": 0,
            "superchats_total_amount": 0.0,
            "errors": 0,
        }

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.chat_subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.chat_subscribers = [s for s in self.chat_subscribers if s is not q]

    async def _broadcast(self, msg: ChatMessage) -> None:
        """Send message to all frontend subscribers."""
        dead = []
        for q in self.chat_subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    dead.append(q)
        for d in dead:
            self.chat_subscribers = [s for s in self.chat_subscribers if s is not d]

    async def _handle_message(self, msg: ChatMessage) -> None:
        """Process a chat message — broadcast to frontend and push to Daimon inject queue."""
        self.stats["messages_read"] += 1
        await self._broadcast(msg)

        from main import _broadcast_inject_queue

        if msg.type in (MessageType.SUPERCHAT, MessageType.SUPER_STICKER):
            self.stats["superchats_read"] += 1
            self.stats["superchats_total_amount"] += msg.amount_micros / 1_000_000
            try:
                _broadcast_inject_queue.put_nowait(msg)
            except asyncio.QueueFull:
                logger.warning("Inject queue full — dropping Super Chat from %s", msg.author)
            logger.info("Super Chat from %s (%s): %s", msg.author, msg.amount, msg.text[:100])
        elif msg.type == MessageType.MEMBERSHIP:
            try:
                _broadcast_inject_queue.put_nowait(msg)
            except asyncio.QueueFull:
                logger.warning("Inject queue full — dropping membership from %s", msg.author)
            logger.info("New member: %s", msg.author)
        else:
            # Check if this author has an active Super Chat conversation —
            # if so, route directly to inject queue so the drain task sees
            # the real author name (batch summaries use author='SYSTEM').
            from main import _broadcast_conversations
            if msg.author in _broadcast_conversations:
                try:
                    _broadcast_inject_queue.put_nowait(msg)
                except asyncio.QueueFull:
                    logger.warning("Inject queue full — dropping conversation msg from %s", msg.author)
            else:
                # Regular chat: buffer and inject periodically
                self._regular_chat_buffer.append(msg)
                if len(self._regular_chat_buffer) > 500:
                    self._regular_chat_buffer = self._regular_chat_buffer[-500:]
                now = time.time()
                if now - self._last_regular_inject >= self._regular_inject_interval:
                    await self._flush_regular_chat()

    async def _flush_regular_chat(self) -> None:
        """Inject a summary of recent regular chat messages to Daimon."""
        if not self._regular_chat_buffer:
            return

        from main import _broadcast_inject_queue, _sanitize_for_gemini

        recent = self._regular_chat_buffer[-5:]
        self._regular_chat_buffer.clear()
        self._last_regular_inject = time.time()

        # Sanitize text for prompt injection without mutating originals
        # (frontend subscribers may still hold references to these ChatMessage objects).
        # Create shallow copies with sanitized text, then call to_muse_text() on the copies.
        sanitized = []
        for m in recent:
            from copy import copy
            mc = copy(m)
            mc.text = _sanitize_for_gemini(m.text, max_len=200)
            sanitized.append(mc)
        lines = [m.to_muse_text() for m in sanitized]
        summary_text = (
            "Recent live chat activity:\n" + "\n".join(lines) +
            "\nYour audience is here. Read the energy — if someone said something interesting, "
            "funny, or worth responding to, engage with one line in your voice. "
            "If it's just vibes and reactions, let it flow. You see everything even when you're quiet."
        )
        summary_msg = ChatMessage(
            id=f"chat-summary-{int(time.time()*1000)}",
            author="SYSTEM",
            text=summary_text,
            type=MessageType.REGULAR,
        )
        try:
            _broadcast_inject_queue.put_nowait(summary_msg)
        except asyncio.QueueFull:
            logger.warning("Inject queue full — dropping chat summary")

    async def _periodic_flush_loop(self) -> None:
        """Background loop: flush buffered chat every 15s regardless of message arrival."""
        try:
            while self._running:
                await asyncio.sleep(self._regular_inject_interval)
                if self._regular_chat_buffer:
                    await self._flush_regular_chat()
        except asyncio.CancelledError:
            pass

    # ─── YouTube Live Streaming API ─────────────────────────────────────────

    async def start_youtube_api(self, video_id: str | None = None) -> None:
        """Start reading chat via YouTube Live Streaming API."""
        if self._running:
            logger.warning("Chat bridge already running — ignoring duplicate start")
            return

        try:
            from broadcast.youtube_auth import get_credentials
            from googleapiclient.discovery import build

            creds = get_credentials()
            if not creds:
                logger.error("No YouTube API credentials — falling back to innertube")
                await self.start_innertube(video_id)
                return

            youtube = build("youtube", "v3", credentials=creds)

            if not video_id:
                video_id = await self._find_active_broadcast(youtube)
            if not video_id:
                logger.error("No active YouTube broadcast found")
                return

            resp = youtube.videos().list(
                part="liveStreamingDetails", id=video_id
            ).execute()
            items = resp.get("items", [])
            if not items or "liveStreamingDetails" not in items[0]:
                logger.error("Video %s is not a live stream", video_id)
                return

            self._live_chat_id = items[0]["liveStreamingDetails"].get("activeLiveChatId")
            if not self._live_chat_id:
                logger.error("No active chat for video %s", video_id)
                return

            logger.info("Connected to YouTube live chat: %s", self._live_chat_id)
            self._running = True
            self._current_video_id = video_id
            self._video_id_for_fallback = video_id  # Used by quota error handler
            self._task = asyncio.create_task(self._poll_youtube_api(youtube))
            self._innertube_task = asyncio.create_task(self._run_innertube_fallback(video_id))
            self._flush_task = asyncio.create_task(self._periodic_flush_loop())

        except ImportError:
            logger.warning("googleapiclient not available — using innertube")
            await self.start_innertube(video_id)
        except Exception as e:
            logger.error("YouTube API setup failed: %s", e)
            if video_id:
                await self.start_innertube(video_id)

    async def _find_active_broadcast(self, youtube) -> str | None:
        try:
            resp = youtube.liveBroadcasts().list(
                part="id,snippet", broadcastStatus="active", broadcastType="all"
            ).execute()
            items = resp.get("items", [])
            if items:
                vid = items[0]["id"]
                logger.info("Found active broadcast: %s", vid)
                return vid
        except Exception as e:
            logger.error("Error finding active broadcast: %s", e)
        return None

    async def _poll_youtube_api(self, youtube) -> None:
        page_token = None
        poll_interval = 5.0

        while self._running:
            try:
                kwargs = {
                    "liveChatId": self._live_chat_id,
                    "part": "id,snippet,authorDetails",
                    "maxResults": 200,
                }
                if page_token:
                    kwargs["pageToken"] = page_token

                resp = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: youtube.liveChatMessages().list(**kwargs).execute(),
                )

                page_token = resp.get("nextPageToken")
                poll_interval = max(2.0, resp.get("pollingIntervalMillis", 5000) / 1000)

                for item in resp.get("items", []):
                    msg = self._parse_api_message(item)
                    if msg:
                        await self._handle_message(msg)

            except Exception as e:
                self.stats["errors"] += 1
                # Check for quota exhaustion or rate limiting before generic handling
                try:
                    from googleapiclient.errors import HttpError
                    if isinstance(e, HttpError) and e.resp.status in (403, 429):
                        logger.error(
                            "YouTube API quota exhausted or rate limited (HTTP %d) — "
                            "switching to innertube permanently",
                            e.resp.status,
                        )
                        # Activate innertube as the permanent chat source
                        video_id = getattr(self, '_video_id_for_fallback', None)
                        if video_id:
                            self._running = True  # Keep running — innertube takes over
                            self._innertube_task = asyncio.create_task(self._run_innertube_chat(video_id))
                        else:
                            self._running = False
                        return
                except ImportError:
                    pass
                logger.error("YouTube API poll error: %s", e)
                poll_interval = min(poll_interval * 2, 30.0)

            await asyncio.sleep(poll_interval)

    def _parse_api_message(self, item: dict) -> ChatMessage | None:
        snippet = item.get("snippet", {})
        author = item.get("authorDetails", {})
        msg_type = snippet.get("type", "")

        base = {
            "id": item.get("id", ""),
            "author": author.get("displayName", "Unknown"),
            "profile_image": author.get("profileImageUrl"),
            "timestamp": time.time(),
        }

        if msg_type == "textMessageEvent":
            return ChatMessage(
                **base,
                text=snippet.get("textMessageDetails", {}).get("messageText", ""),
                type=MessageType.REGULAR,
            )
        elif msg_type == "superChatEvent":
            details = snippet.get("superChatDetails", {})
            try:
                amount_micros = int(details.get("amountMicros") or 0)
            except (ValueError, TypeError):
                amount_micros = 0
            return ChatMessage(
                **base,
                text=details.get("userComment", ""),
                type=MessageType.SUPERCHAT,
                amount=details.get("amountDisplayString", ""),
                amount_micros=amount_micros,
                currency=details.get("currency", "USD"),
                color=_tier_color(amount_micros),
            )
        elif msg_type == "superStickerEvent":
            details = snippet.get("superStickerDetails", {})
            try:
                amount_micros = int(details.get("amountMicros") or 0)
            except (ValueError, TypeError):
                amount_micros = 0
            return ChatMessage(
                **base,
                text="sent a Super Sticker",
                type=MessageType.SUPER_STICKER,
                amount=details.get("amountDisplayString", ""),
                amount_micros=amount_micros,
                currency=details.get("currency", "USD"),
                color=_tier_color(amount_micros),
            )
        elif msg_type in ("membershipGiftingEvent", "newSponsorEvent"):
            return ChatMessage(**base, text="joined as a member!", type=MessageType.MEMBERSHIP)

        return None

    # ─── Innertube (no auth required) ──────────────────────────────────────

    async def start_innertube(self, video_id: str | None = None) -> None:
        """Start reading chat via YouTube innertube API (no auth needed)."""
        if self._running:
            logger.warning("Chat bridge already running — ignoring duplicate start")
            return
        if not video_id:
            logger.error("Innertube chat reader requires a video_id")
            return

        self._running = True
        self._current_video_id = video_id
        self._innertube_task = asyncio.create_task(self._run_innertube_chat(video_id))
        self._innertube_task.add_done_callback(
            lambda t: logger.error("Innertube task crashed: %s", t.exception())
            if not t.cancelled() and t.exception() else None
        )
        self._flush_task = asyncio.create_task(self._periodic_flush_loop())

    async def _run_innertube_chat(self, video_id: str) -> None:
        """Read live chat via YouTube innertube API — routes through proxy if configured.

        Wraps the entire reader in a retry loop so transient failures
        (proxy blips, missing token on stream startup) don't kill chat
        permanently. Retries up to 60 times with exponential backoff
        (max 60s between attempts).
        """
        import httpx

        logger.info("Starting innertube chat reader for video %s", video_id)
        seen_ids: OrderedDict = OrderedDict()
        MAX_SEEN = 10_000  # cap to prevent memory leak on long streams
        retry_delay = 10.0
        attempt = 0

        while self._running:
            if not self._running:
                return

            try:
                # Phase 1: get initial continuation token
                async with httpx.AsyncClient(proxy=YOUTUBE_PROXY or None, timeout=15) as client:
                    r = await client.get(
                        f"https://www.youtube.com/watch?v={video_id}",
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                        follow_redirects=True,
                    )
                    match = re.search(r'"continuation":"([^"]+)"', r.text)
                    if not match:
                        # Detect ended streams to avoid infinite retry
                        is_ended = '"isPostLiveDvr":true' in r.text or '"This live event has ended' in r.text
                        attempt += 1
                        if is_ended:
                            logger.warning(
                                "Stream %s has ended — chat unavailable. "
                                "Will keep checking in case a new stream starts (attempt %d)",
                                video_id, attempt,
                            )
                        else:
                            logger.warning(
                                "No chat continuation token for %s (attempt %d) — "
                                "stream may not be live yet, retrying in %.0fs",
                                video_id, attempt, retry_delay,
                            )
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 1.5, 60.0)
                        continue
                    continuation = match.group(1)
                    logger.info("innertube chat connected to video %s (attempt %d)", video_id, attempt + 1)
                    retry_delay = 10.0  # reset backoff on success
                    attempt = 0  # reset counter on success

                # Phase 2: poll loop
                poll_interval = 5.0
                async with httpx.AsyncClient(proxy=YOUTUBE_PROXY or None, timeout=15) as client:
                    while self._running:
                        try:
                            r = await client.post(
                                "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?prettyPrint=false",
                                json={
                                    "context": {"client": {"clientName": "WEB", "clientVersion": "2.20240101.00.00"}},
                                    "continuation": continuation,
                                },
                                headers={"User-Agent": "Mozilla/5.0"},
                            )
                            data = r.json()

                            # Update continuation token
                            continuations = (
                                data.get("continuationContents", {})
                                .get("liveChatContinuation", {})
                                .get("continuations", [])
                            )
                            for c in continuations:
                                cont_data = c.get("invalidationContinuationData") or c.get("timedContinuationData") or {}
                                if "continuation" in cont_data:
                                    continuation = cont_data["continuation"]
                                    if "timeoutMs" in cont_data:
                                        poll_interval = max(2.0, int(cont_data["timeoutMs"]) / 1000)
                                    break

                            # Process messages
                            actions = (
                                data.get("continuationContents", {})
                                .get("liveChatContinuation", {})
                                .get("actions", [])
                            )
                            for action in actions:
                                item = action.get("addChatItemAction", {}).get("item", {})
                                msg = self._parse_innertube_message(item, seen_ids)
                                if msg:
                                    await self._handle_message(msg)

                            # Cap seen_ids to prevent unbounded memory growth
                            # Keep most recent half by insertion order
                            if len(seen_ids) > MAX_SEEN:
                                keys = list(seen_ids.keys())
                                seen_ids = OrderedDict.fromkeys(keys[-MAX_SEEN // 2:])

                        except Exception as e:
                            self.stats["errors"] += 1
                            logger.error("innertube chat poll error: %s", e)
                            poll_interval = min(poll_interval * 2, 30.0)

                        await asyncio.sleep(poll_interval)

                # If we exited the poll loop normally (self._running went False), we're done
                return

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    "innertube chat reader error (attempt %d): %s — retrying in %.0fs",
                    attempt + 1, e, retry_delay,
                )
                self.stats["errors"] += 1
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 60.0)

            attempt += 1

    def _parse_innertube_message(self, item: dict, seen_ids: OrderedDict) -> ChatMessage | None:
        # Regular text message
        renderer = item.get("liveChatTextMessageRenderer")
        if renderer:
            msg_id = renderer.get("id", "")
            if msg_id in seen_ids:
                return None
            seen_ids[msg_id] = None
            author = renderer.get("authorName", {}).get("simpleText", "Unknown")
            runs = renderer.get("message", {}).get("runs", [])
            text = "".join(r.get("text", "") for r in runs)
            return ChatMessage(id=msg_id, author=author, text=text, type=MessageType.REGULAR)

        # Super Chat
        sc_renderer = item.get("liveChatPaidMessageRenderer")
        if sc_renderer:
            msg_id = sc_renderer.get("id", "")
            if msg_id in seen_ids:
                return None
            seen_ids[msg_id] = None
            author = sc_renderer.get("authorName", {}).get("simpleText", "Unknown")
            runs = sc_renderer.get("message", {}).get("runs", [])
            text = "".join(r.get("text", "") for r in runs) if runs else ""
            amount_str = sc_renderer.get("purchaseAmountText", {}).get("simpleText", "")
            amount_micros = 0
            if amount_str:
                match = re.search(r"[\d,.]+", amount_str)
                if match:
                    try:
                        amount_micros = int(float(match.group().replace(",", "")) * 1_000_000)
                    except ValueError:
                        pass
            return ChatMessage(
                id=msg_id, author=author, text=text, type=MessageType.SUPERCHAT,
                amount=amount_str, amount_micros=amount_micros,
                color=_tier_color(amount_micros) if amount_micros > 0 else None,
            )

        # Membership
        member_renderer = item.get("liveChatMembershipItemRenderer")
        if member_renderer:
            msg_id = member_renderer.get("id", "")
            if msg_id in seen_ids:
                return None
            seen_ids[msg_id] = None
            author = member_renderer.get("authorName", {}).get("simpleText", "Unknown")
            return ChatMessage(id=msg_id, author=author, text="joined as a member!", type=MessageType.MEMBERSHIP)

        return None

    async def _run_innertube_fallback(self, video_id: str) -> None:
        """Innertube as secondary source — activates if YouTube API stops working."""
        await asyncio.sleep(60)
        if self._running and self.stats["errors"] > 5:
            if self._task:
                self._task.cancel()
                self._task = None
            logger.info("Activating innertube fallback due to API errors")
            # Spawn as a proper task so stop() can cancel it cleanly
            self._innertube_task = asyncio.create_task(self._run_innertube_chat(video_id))

    # ─── Broadcast Watchdog ──────────────────────────────────────────────────

    async def start_watchdog(self) -> None:
        """Start background watchdog that auto-detects stream restarts.

        Polls liveBroadcasts.list every 30s. When the active broadcast ID
        changes (stream restarted on YouTube), stops the old chat bridge
        and starts a new one for the new broadcast — fully automatic.
        """
        if self._watchdog_task and not self._watchdog_task.done():
            logger.warning("Broadcast watchdog already running")
            return
        self._watchdog_task = asyncio.create_task(self._broadcast_watchdog_loop())
        logger.info("Broadcast watchdog started")

    async def _broadcast_watchdog_loop(self) -> None:
        """Poll for active broadcast changes every 30s."""
        poll_interval = 30.0
        consecutive_errors = 0

        while True:
            try:
                await asyncio.sleep(poll_interval)

                from broadcast.youtube_auth import get_credentials
                from googleapiclient.discovery import build

                creds = get_credentials()
                if not creds:
                    consecutive_errors += 1
                    if consecutive_errors % 10 == 0:
                        logger.warning("Watchdog: no YouTube credentials (attempt %d)", consecutive_errors)
                    continue

                youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
                resp = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: youtube.liveBroadcasts().list(
                        broadcastStatus="active", part="id,snippet", broadcastType="all",
                    ).execute(),
                )
                items = resp.get("items", [])
                consecutive_errors = 0

                if not items:
                    # No active broadcast — if chat bridge is running, it will
                    # handle the dead stream via its own error handling
                    if self._running and self._current_video_id:
                        logger.info("Watchdog: no active broadcast found (was: %s) — "
                                    "stream may have ended", self._current_video_id)
                    continue

                new_vid = items[0]["id"]
                new_title = items[0]["snippet"]["title"]

                if new_vid == self._current_video_id:
                    # Same stream, all good
                    continue

                # Stream changed! Auto-reconnect.
                old_vid = self._current_video_id
                logger.info(
                    "Watchdog: broadcast changed %s → %s (%s) — restarting chat bridge",
                    old_vid or "(none)", new_vid, new_title,
                )

                # Stop existing chat bridge (but not the watchdog itself)
                await self._stop_chat_tasks()

                # Reset state for fresh start
                self._live_chat_id = None
                self.stats["errors"] = 0

                # Start new chat bridge for the new broadcast
                await self.start_youtube_api(video_id=new_vid)

                logger.info("Watchdog: chat bridge restarted for %s", new_vid)

            except asyncio.CancelledError:
                logger.info("Broadcast watchdog cancelled")
                return
            except Exception as e:
                consecutive_errors += 1
                logger.error("Watchdog error (attempt %d): %s", consecutive_errors, e)
                # Back off on repeated errors, cap at 120s
                poll_interval = min(30.0 * (1.5 ** min(consecutive_errors, 5)), 120.0)

    async def _stop_chat_tasks(self) -> None:
        """Stop chat polling tasks without stopping the watchdog."""
        self._running = False
        tasks_to_cancel = []
        if self._task:
            self._task.cancel()
            tasks_to_cancel.append(self._task)
        if self._innertube_task:
            self._innertube_task.cancel()
            tasks_to_cancel.append(self._innertube_task)
        if self._flush_task:
            self._flush_task.cancel()
            tasks_to_cancel.append(self._flush_task)

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        self._task = None
        self._innertube_task = None
        self._flush_task = None

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    async def stop(self) -> None:
        """Stop all chat reading and watchdog. Awaits task cancellation for clean shutdown."""
        self._running = False
        tasks_to_cancel = []
        if self._task:
            self._task.cancel()
            tasks_to_cancel.append(self._task)
        if self._innertube_task:
            self._innertube_task.cancel()
            tasks_to_cancel.append(self._innertube_task)
        if self._flush_task:
            self._flush_task.cancel()
            tasks_to_cancel.append(self._flush_task)
        if self._watchdog_task:
            self._watchdog_task.cancel()
            tasks_to_cancel.append(self._watchdog_task)

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        self._task = None
        self._innertube_task = None
        self._flush_task = None
        self._watchdog_task = None

        logger.info("Chat bridge stopped. Stats: %s", json.dumps(self.stats))


# Singleton instance
chat_bridge = ChatBridge()
