# Gemini Live API — Voice Agent Reference

> Tested against `gemini-3.1-flash-live-preview` via `google-genai` Python SDK.
> Last verified: 2026-03-28.

## Architecture

```
Client (browser/app)
  ├─ Captures mic → PCM16 16kHz mono → base64 → WebSocket
  ├─ Receives audio → base64 → PCM16 24kHz → AudioContext → speakers
  └─ Receives tool calls, transcripts, status messages

Backend (Python/FastAPI)
  ├─ WebSocket proxy: client ↔ Gemini Live API
  ├─ Intercepts tool calls server-side (DB, APIs, etc.)
  └─ Forwards audio + metadata bidirectionally

Gemini Live API (wss://generativelanguage.googleapis.com)
  ├─ Bidirectional streaming: audio in, audio + text + tools out
  ├─ Real-time voice conversation with sub-second latency
  └─ Built-in tools: Google Search, Code Execution, URL Context
```

## Connection Setup

```python
from google import genai
from google.genai import types

client = genai.Client(api_key=API_KEY)

config = types.LiveConnectConfig(
    response_modalities=["AUDIO"],          # or ["TEXT"] or ["AUDIO", "TEXT"]
    system_instruction="You are a helpful assistant.",
    tools=[...],                            # see Tools section
    speech_config=types.SpeechConfig(...),  # see Voice section
    # ... additional config below
)

async with client.aio.live.connect(
    model="gemini-3.1-flash-live-preview",
    config=config,
) as session:
    # Send audio/text, receive responses
    await session.send_realtime_input(audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000"))
    await session.send_realtime_input(text="Hello")

    async for response in session.receive():
        # Handle response.server_content, response.tool_call, etc.
        pass
```

## LiveConnectConfig — All Fields

| Field | Type | Status | Description |
|-------|------|--------|-------------|
| `response_modalities` | `list[Modality]` | Required | `["AUDIO"]`, `["TEXT"]`, or `["AUDIO", "TEXT"]` |
| `system_instruction` | `str \| Content` | Required | System prompt defining agent persona and behavior |
| `tools` | `list[Tool]` | Optional | Function declarations + built-in tools |
| `speech_config` | `SpeechConfig` | Optional | Voice selection, language, multi-speaker |
| `input_audio_transcription` | `AudioTranscriptionConfig` | Supported | Transcribe user's speech to text |
| `output_audio_transcription` | `AudioTranscriptionConfig` | Supported | Transcribe model's speech to text |
| `context_window_compression` | `ContextWindowCompressionConfig` | Supported | Sliding window to keep long sessions alive |
| `realtime_input_config` | `RealtimeInputConfig` | Supported | VAD sensitivity, interruption behavior |
| `session_resumption` | `SessionResumptionConfig` | Supported | Resume sessions across disconnects |
| `temperature` | `float` | Supported | Sampling temperature (0.0-2.0) |
| `top_p` | `float` | Supported | Nucleus sampling |
| `top_k` | `float` | Supported | Top-k sampling |
| `max_output_tokens` | `int` | Supported | Max tokens per response |
| `seed` | `int` | Supported | Deterministic sampling seed |
| `media_resolution` | `MediaResolution` | Supported | `LOW`, `MEDIUM`, `HIGH` for image/video input |
| `thinking_config` | `ThinkingConfig` | Supported | Enable chain-of-thought reasoning |
| `history_config` | `HistoryConfig` | Supported | Pre-load conversation history |
| `enable_affective_dialog` | `bool` | NOT SUPPORTED | Rejected by `gemini-3.1-flash-live-preview` |
| `proactivity` | `ProactivityConfig` | NOT SUPPORTED | Rejected by `gemini-3.1-flash-live-preview` |
| `explicit_vad_signal` | `bool` | Untested | Manual voice activity detection control |

## Tools

### Built-in Tools (no function declarations needed)

| Tool | Constructor | What it does |
|------|-------------|-------------|
| **Google Search** | `Tool(google_search=GoogleSearch())` | Live web search — current events, facts, weather, anything |
| **Code Execution** | `Tool(code_execution=ToolCodeExecution())` | Run Python code for calculations, data processing |
| **URL Context** | `Tool(url_context=UrlContext())` | Fetch and understand web page content from URLs |

These are passed as separate `Tool` objects alongside your function declarations:

```python
tools = [
    # Your custom functions
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="play_music",
            description="Play a music track",
            parameters={"type": "object", "properties": {...}},
        ),
    ]),
    # Built-in capabilities
    types.Tool(google_search=types.GoogleSearch()),
    types.Tool(code_execution=types.ToolCodeExecution()),
    types.Tool(url_context=types.UrlContext()),
]
```

### Additional Tool Types (available but not all tested with Live API)

| Tool | Constructor | Notes |
|------|-------------|-------|
| Google Maps | `Tool(google_maps=GoogleMaps())` | Location/maps data |
| Retrieval | `Tool(retrieval=Retrieval())` | RAG over uploaded documents |
| File Search | `Tool(file_search=FileSearch())` | Search uploaded files |
| Enterprise Web Search | `Tool(enterprise_web_search=EnterpriseWebSearch())` | Custom search engine |
| Parallel AI Search | `Tool(parallel_ai_search=ToolParallelAiSearch())` | Multi-query search |
| MCP Servers | `Tool(mcp_servers=[McpServer(...)])` | Model Context Protocol |
| Computer Use | `Tool(computer_use=ComputerUse())` | Desktop automation |

### Custom Function Declarations

```python
types.FunctionDeclaration(
    name="get_weather",
    description="Get current weather for a location",
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City name or coordinates",
            },
        },
        "required": ["location"],
    },
)
```

**Limit**: Max 20 function declarations per session.

### Handling Tool Calls

```python
async for response in session.receive():
    if response.tool_call:
        results = []
        for fc in response.tool_call.function_calls:
            # Execute your function
            result = await my_handler(fc.name, dict(fc.args))
            results.append(types.FunctionResponse(
                name=fc.name,
                id=fc.id,
                response=result,  # dict
            ))
        await session.send_tool_response(function_responses=results)
```

Google Search, Code Execution, and URL Context are handled automatically by Gemini — you don't intercept those. They execute internally and the model incorporates the results into its response.

## Voice Configuration

### Prebuilt Voices

```python
config.speech_config = types.SpeechConfig(
    voice_config=types.VoiceConfig(
        prebuilt_voice_config=types.PrebuiltVoiceConfig(
            voice_name="Puck"  # see list below
        )
    )
)
```

**Available voices**: Puck, Charon, Kore, Fenrir, Aoede, Leda, Orus, Zephyr

### Language

```python
config.speech_config = types.SpeechConfig(
    language_code="en-US",  # BCP-47 language code
    voice_config=types.VoiceConfig(
        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
    ),
)
```

### Multi-Speaker (multiple voice personas)

```python
config.speech_config = types.SpeechConfig(
    multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
        speaker_voice_configs=[
            types.SpeakerVoiceConfig(
                speaker="Narrator",
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                ),
            ),
            types.SpeakerVoiceConfig(
                speaker="Character",
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Kore")
                ),
            ),
        ]
    ),
)
```

### Voice Cloning (replicated voice)

```python
config.speech_config = types.SpeechConfig(
    voice_config=types.VoiceConfig(
        replicated_voice_config=types.ReplicatedVoiceConfig(
            voice_sample_audio=audio_bytes,
            mime_type="audio/wav",
        )
    ),
)
```

## Audio Format

| Direction | Format | Sample Rate | Channels | Encoding |
|-----------|--------|-------------|----------|----------|
| Input (mic → Gemini) | PCM16 | 16,000 Hz | Mono | Little-endian signed 16-bit |
| Output (Gemini → speakers) | PCM16 | 24,000 Hz | Mono | Little-endian signed 16-bit |

**MIME type for input**: `audio/pcm;rate=16000`

```python
await session.send_realtime_input(
    audio=types.Blob(
        data=pcm16_bytes,
        mime_type="audio/pcm;rate=16000",
    )
)
```

## Transcription

Enable both to get text versions of all speech:

```python
config = types.LiveConnectConfig(
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
    ...
)
```

Transcriptions arrive on `server_content`:

```python
if server_content.input_transcription:
    user_said = server_content.input_transcription.text

if server_content.output_transcription:
    model_said = server_content.output_transcription.text
```

## Context Window Compression

Keeps long sessions alive by compressing older context:

```python
config = types.LiveConnectConfig(
    context_window_compression=types.ContextWindowCompressionConfig(
        sliding_window=types.SlidingWindow(
            target_tokens=40000,  # compress when context exceeds this
        ),
    ),
    ...
)
```

## Voice Activity Detection (VAD)

Control how the model detects when the user starts/stops speaking:

```python
config = types.LiveConnectConfig(
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=False,
            start_of_speech_sensitivity=0.5,  # 0.0-1.0
            end_of_speech_sensitivity=0.5,
            prefix_padding_ms=300,
            silence_duration_ms=1000,
        ),
        activity_handling="START_OF_ACTIVITY_INTERRUPTS",
        # Options: START_OF_ACTIVITY_INTERRUPTS, NO_INTERRUPTION
        turn_coverage="TURN_INCLUDES_ALL_INPUT",
        # Options: TURN_INCLUDES_ONLY_ACTIVITY, TURN_INCLUDES_ALL_INPUT
    ),
    ...
)
```

## Session Resumption

Resume a session after disconnect without losing context:

```python
# First connection — get a handle
config = types.LiveConnectConfig(
    session_resumption=types.SessionResumptionConfig(
        transparent=True,  # auto-resume
    ),
    ...
)

# The handle comes back in responses:
# response.session_resumption_update.handle

# Reconnect with the handle:
config = types.LiveConnectConfig(
    session_resumption=types.SessionResumptionConfig(
        handle=saved_handle,
    ),
    ...
)
```

## Thinking (Chain-of-Thought)

```python
config = types.LiveConnectConfig(
    thinking_config=types.ThinkingConfig(
        include_thoughts=True,
        thinking_budget=1024,      # max thinking tokens
    ),
    ...
)
```

## Conversation History

Pre-load conversation context:

```python
config = types.LiveConnectConfig(
    history_config=types.HistoryConfig(
        initial_history_in_client_content=[
            types.Content(role="user", parts=[types.Part(text="I like jazz")]),
            types.Content(role="model", parts=[types.Part(text="Great taste!")]),
        ],
    ),
    ...
)
```

## Response Handling

```python
async for response in session.receive():
    server_content = response.server_content
    tool_call = response.tool_call

    if server_content:
        # Audio/text from the model
        if server_content.model_turn and server_content.model_turn.parts:
            for part in server_content.model_turn.parts:
                if part.inline_data and part.inline_data.data:
                    # Audio bytes — play through speakers
                    audio_b64 = base64.b64encode(part.inline_data.data).decode()
                if part.text:
                    # Text response (if TEXT in response_modalities)
                    print(part.text)

        # Transcriptions
        if server_content.input_transcription:
            print(f"User said: {server_content.input_transcription.text}")
        if server_content.output_transcription:
            print(f"Model said: {server_content.output_transcription.text}")

        # Turn management
        if server_content.turn_complete:
            pass  # Model finished speaking
        if server_content.interrupted:
            pass  # User interrupted (barge-in)

        # Grounding metadata (from Google Search results)
        if server_content.grounding_metadata:
            pass  # Contains search result citations

    if tool_call:
        # Handle function calls (see Tools section)
        pass
```

## Server Message Fields

| Field | Type | When |
|-------|------|------|
| `server_content.model_turn.parts` | `list[Part]` | Audio/text output |
| `server_content.turn_complete` | `bool` | Model finished speaking |
| `server_content.interrupted` | `bool` | User barged in |
| `server_content.input_transcription` | `TranscriptionContent` | User speech → text |
| `server_content.output_transcription` | `TranscriptionContent` | Model speech → text |
| `server_content.grounding_metadata` | `GroundingMetadata` | Google Search citations |
| `server_content.url_context_metadata` | `UrlContextMetadata` | URL fetch results |
| `server_content.generation_complete` | `bool` | Full generation done |
| `tool_call.function_calls` | `list[FunctionCall]` | Custom tool invocations |
| `tool_call_cancellation` | `ToolCallCancellation` | Tool call was cancelled |
| `usage_metadata` | `UsageMetadata` | Token counts |
| `go_away` | `GoAway` | Server asking to disconnect |
| `session_resumption_update` | `SessionResumptionUpdate` | Resume handle |

## Sending Input

```python
# Audio (PCM16 16kHz mono)
await session.send_realtime_input(
    audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
)

# Text
await session.send_realtime_input(text="What's the weather like?")

# Tool response
await session.send_tool_response(function_responses=[
    types.FunctionResponse(name="get_weather", id=fc_id, response={"temp": 72}),
])

# End of turn signal (for manual VAD)
await session.send_client_content(turn_complete=True)
```

## Interruption (Barge-in)

When the user starts speaking while the model is talking, the model is automatically
interrupted. The `server_content.interrupted` flag fires, and you should:

1. Stop playing buffered audio immediately
2. Flush the audio queue
3. The model will process the user's new input

## Known Limitations

- **Session duration**: Sessions timeout after ~15 minutes of inactivity. Use `session_resumption` to reconnect.
- **Gemini 1008 abort**: Live sessions die periodically with WebSocket code 1008. Implement auto-reconnect.
- **`enable_affective_dialog`**: Not supported on `gemini-3.1-flash-live-preview` (rejected with field error).
- **`proactivity`**: Not supported on `gemini-3.1-flash-live-preview` (rejected with field error).
- **Tool limit**: Max 20 function declarations. Built-in tools (Search, Code, URL) don't count against this.
- **Audio format is fixed**: Input must be PCM16 16kHz. Output is always PCM16 24kHz. No negotiation.
- **No video input in Live API**: Despite `media_resolution` field, live video streaming isn't supported. Use image frames instead.

## Models

| Model | Capabilities |
|-------|-------------|
| `gemini-3.1-flash-live-preview` | Voice, Search, Code Exec, URL Context, Transcription, Compression |
| `gemini-2.5-flash-preview-native-audio-dialog` | Experimental native audio with affective dialog |
| `gemini-2.0-flash-live-001` | Older stable model, fewer features |

## Quick Start Template

```python
import asyncio, os
from google import genai
from google.genai import types

async def main():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction="You are a helpful voice assistant.",
        tools=[
            types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name="get_time",
                    description="Get the current time",
                    parameters={"type": "object", "properties": {}},
                ),
            ]),
            types.Tool(google_search=types.GoogleSearch()),
            types.Tool(code_execution=types.ToolCodeExecution()),
        ],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
            ),
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow(target_tokens=40000),
        ),
    )

    async with client.aio.live.connect(
        model="gemini-3.1-flash-live-preview",
        config=config,
    ) as session:
        # Send text input
        await session.send_realtime_input(text="What's happening in the news today?")

        async for response in session.receive():
            sc = response.server_content
            if sc and sc.output_transcription:
                print(f"Assistant: {sc.output_transcription.text}")
            if sc and sc.turn_complete:
                break

asyncio.run(main())
```
