"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useGemini } from "@/context/GeminiProvider";
import { GEMINI_VOICES, DEFAULT_MUSE_SETTINGS, VOICE_EFFECT_PRESETS } from "@/lib/constants";
import type { GeminiVoice, SpeechSensitivity, EndSensitivity, MuseSettings, VoiceEffectsConfig, VoiceEffectPreset, ReverbSize } from "@/lib/types";

interface SettingsPanelProps {
  visible: boolean;
  onClose: () => void;
  onEffectsChange?: (effects: VoiceEffectsConfig) => void;
}

export function SettingsPanel({ visible, onClose, onEffectsChange }: SettingsPanelProps) {
  const { museSettings, applySettings, connectionStatus } = useGemini();

  // Local draft state — only applied on "Apply & Reconnect"
  const [draft, setDraft] = useState<MuseSettings>(museSettings);
  const [applying, setApplying] = useState(false);
  const [effectsAdvancedOpen, setEffectsAdvancedOpen] = useState(false);

  // Sync draft when settings change externally or panel opens
  useEffect(() => {
    if (visible) setDraft(museSettings);
  }, [visible, museSettings]);

  const hasChanges = JSON.stringify(draft) !== JSON.stringify(museSettings);
  const isConnected = connectionStatus === "ready" || connectionStatus === "connected";

  const handleApply = useCallback(async () => {
    setApplying(true);
    try {
      await applySettings(draft);
      onClose();
    } catch (err) {
      console.error("[SettingsPanel] Failed to apply settings:", err);
    } finally {
      setApplying(false);
    }
  }, [draft, applySettings, onClose]);

  const handleReset = useCallback(() => {
    setDraft({ ...DEFAULT_MUSE_SETTINGS });
    // Flush default effects to the audio chain immediately so the reset
    // takes effect even before the user clicks "Apply & Reconnect".
    onEffectsChange?.(DEFAULT_MUSE_SETTINGS.voiceEffects);
  }, [onEffectsChange]);

  // Voice effects helpers — no reconnect needed, applied instantly
  const handlePresetSelect = useCallback((preset: VoiceEffectPreset) => {
    setDraft((d) => {
      const next: VoiceEffectsConfig = {
        ...d.voiceEffects,
        ...VOICE_EFFECT_PRESETS[preset],
        preset,
      };
      onEffectsChange?.(next);
      return { ...d, voiceEffects: next };
    });
  }, [onEffectsChange]);

  const handleEffectChange = useCallback(<K extends keyof VoiceEffectsConfig>(
    key: K,
    value: VoiceEffectsConfig[K],
  ) => {
    setDraft((d) => {
      const next: VoiceEffectsConfig = { ...d.voiceEffects, [key]: value };
      onEffectsChange?.(next);
      return { ...d, voiceEffects: next };
    });
  }, [onEffectsChange]);

  // Close on Escape
  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, onClose]);

  if (!visible) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50"
        onClick={onClose}
      />

      {/* Panel */}
      <div
        ref={panelRef}
        className="fixed right-0 top-0 bottom-0 w-full max-w-sm bg-zinc-900/95 backdrop-blur-md
                   border-l border-white/10 z-50 overflow-y-auto"
        style={{ paddingTop: "calc(1rem + var(--safe-top))", paddingBottom: "calc(1rem + var(--safe-bottom))" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 pb-6">
          {/* Header */}
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-medium text-white/90">Muse Settings</h2>
            <button
              onClick={onClose}
              className="w-8 h-8 rounded-full flex items-center justify-center
                         bg-white/10 hover:bg-white/20 transition-colors"
              aria-label="Close settings"
            >
              <CloseIcon className="w-4 h-4 text-white/70" />
            </button>
          </div>

          {/* Voice Selection */}
          <Section title="Voice">
            <div className="grid grid-cols-2 gap-2">
              {GEMINI_VOICES.map((v) => (
                <button
                  key={v.id}
                  onClick={() => setDraft((d) => ({ ...d, voice: v.id as GeminiVoice }))}
                  className={`px-3 py-2.5 rounded-lg text-left transition-colors border ${
                    draft.voice === v.id
                      ? "bg-white/15 border-white/30 text-white"
                      : "bg-white/5 border-white/5 text-white/60 hover:bg-white/10 hover:text-white/80"
                  }`}
                >
                  <div className="text-sm font-medium">{v.label}</div>
                  <div className="text-xs text-white/40 mt-0.5">{v.description}</div>
                </button>
              ))}
            </div>
          </Section>

          {/* Voice Effects */}
          <Section title="Voice Effects">
            <p className="text-xs text-white/30 -mt-1 mb-3">
              Audio effects applied locally — no reconnect needed
            </p>

            {/* Preset row */}
            <div className="flex gap-1.5 mb-3">
              {(Object.keys(VOICE_EFFECT_PRESETS) as VoiceEffectPreset[]).map((p) => (
                <button
                  key={p}
                  onClick={() => handlePresetSelect(p)}
                  className={`flex-1 py-1.5 rounded-md text-xs font-medium capitalize transition-colors border ${
                    draft.voiceEffects.preset === p
                      ? "bg-white/15 border-white/30 text-white"
                      : "bg-white/5 border-white/5 text-white/50 hover:bg-white/10 hover:text-white/70"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>

            {/* Bypass toggle */}
            <label className={`flex items-center justify-between mb-3 cursor-pointer ${draft.voiceEffects.bypass ? "opacity-60" : ""}`}>
              <span className="text-sm text-white/70">Effects Active</span>
              <button
                role="switch"
                aria-checked={!draft.voiceEffects.bypass}
                onClick={() => handleEffectChange("bypass", !draft.voiceEffects.bypass)}
                className={`relative w-10 h-5.5 rounded-full transition-colors border ${
                  !draft.voiceEffects.bypass
                    ? "bg-white/20 border-white/30"
                    : "bg-white/5 border-white/10"
                }`}
                style={{ height: "1.375rem" }}
              >
                <span
                  className={`absolute top-0.5 w-4 h-4 rounded-full bg-white/80 transition-transform ${
                    !draft.voiceEffects.bypass ? "translate-x-5" : "translate-x-0.5"
                  }`}
                />
              </button>
            </label>

            {/* Advanced collapsible */}
            <div className={draft.voiceEffects.bypass ? "opacity-40 pointer-events-none" : ""}>
              <button
                onClick={() => setEffectsAdvancedOpen((o) => !o)}
                className="flex items-center gap-1.5 text-xs text-white/40 hover:text-white/60 transition-colors mb-2"
              >
                <span>Advanced</span>
                <span className="text-[10px]">{effectsAdvancedOpen ? "▲" : "▼"}</span>
              </button>

              {effectsAdvancedOpen && (
                <div className="space-y-4 pl-0.5">
                  {/* EQ */}
                  <div>
                    <p className="text-xs text-white/30 uppercase tracking-wider mb-2">EQ</p>
                    <div className="space-y-3">
                      <SliderField
                        label="Low"
                        hint=""
                        value={draft.voiceEffects.eqLow}
                        min={-12}
                        max={12}
                        step={1}
                        unit=" dB"
                        onChange={(v) => handleEffectChange("eqLow", v)}
                      />
                      <SliderField
                        label="Mid"
                        hint=""
                        value={draft.voiceEffects.eqMid}
                        min={-12}
                        max={12}
                        step={1}
                        unit=" dB"
                        onChange={(v) => handleEffectChange("eqMid", v)}
                      />
                      <SliderField
                        label="High"
                        hint=""
                        value={draft.voiceEffects.eqHigh}
                        min={-12}
                        max={12}
                        step={1}
                        unit=" dB"
                        onChange={(v) => handleEffectChange("eqHigh", v)}
                      />
                    </div>
                  </div>

                  {/* Reverb */}
                  <div>
                    <p className="text-xs text-white/30 uppercase tracking-wider mb-2">Reverb</p>
                    <div className="space-y-3">
                      <SelectField
                        label="Room Size"
                        hint=""
                        value={draft.voiceEffects.reverbSize}
                        onChange={(v) => handleEffectChange("reverbSize", v as ReverbSize)}
                        options={[
                          { value: "small", label: "Small" },
                          { value: "medium", label: "Medium" },
                          { value: "large", label: "Large" },
                          { value: "hall", label: "Hall" },
                        ]}
                      />
                      <SliderField
                        label="Reverb Mix"
                        hint=""
                        value={Math.round(draft.voiceEffects.reverbMix * 100)}
                        min={0}
                        max={100}
                        step={1}
                        unit="%"
                        onChange={(v) => handleEffectChange("reverbMix", v / 100)}
                      />
                    </div>
                  </div>

                  {/* Delay */}
                  <div>
                    <p className="text-xs text-white/30 uppercase tracking-wider mb-2">Delay</p>
                    <div className="space-y-3">
                      <SliderField
                        label="Delay Time"
                        hint=""
                        value={Math.round(draft.voiceEffects.delayTime * 1000)}
                        min={0}
                        max={1000}
                        step={10}
                        unit=" ms"
                        onChange={(v) => handleEffectChange("delayTime", v / 1000)}
                      />
                      <SliderField
                        label="Feedback"
                        hint=""
                        value={Math.round((draft.voiceEffects.delayFeedback / 0.8) * 100)}
                        min={0}
                        max={100}
                        step={1}
                        unit="%"
                        onChange={(v) => handleEffectChange("delayFeedback", Math.min((v / 100) * 0.8, 0.8))}
                      />
                    </div>
                  </div>

                  {/* Compression */}
                  <div>
                    <p className="text-xs text-white/30 uppercase tracking-wider mb-2">Compression</p>
                    <div className="space-y-3">
                      <SliderField
                        label="Threshold"
                        hint=""
                        value={draft.voiceEffects.compressionThreshold}
                        min={-60}
                        max={0}
                        step={1}
                        unit=" dB"
                        onChange={(v) => handleEffectChange("compressionThreshold", v)}
                      />
                      <SliderField
                        label="Ratio"
                        hint=""
                        value={draft.voiceEffects.compressionRatio}
                        min={1}
                        max={20}
                        step={1}
                        unit=":1"
                        onChange={(v) => handleEffectChange("compressionRatio", v)}
                      />
                    </div>
                  </div>

                  {/* Master */}
                  <div>
                    <p className="text-xs text-white/30 uppercase tracking-wider mb-2">Master</p>
                    <SliderField
                      label="Master Gain"
                      hint=""
                      value={Math.round(draft.voiceEffects.masterGain * 100)}
                      min={0}
                      max={200}
                      step={5}
                      unit="%"
                      onChange={(v) => handleEffectChange("masterGain", v / 100)}
                    />
                  </div>
                </div>
              )}
            </div>
          </Section>

          {/* Language */}
          <Section title="Language">
            <select
              value={draft.language}
              onChange={(e) => setDraft((d) => ({ ...d, language: e.target.value }))}
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2
                         text-sm text-white/80 outline-none focus:border-white/25
                   [&>option]:bg-zinc-900 [&>option]:text-white"
            >
              <option value="en-US">English (US)</option>
              <option value="en-GB">English (UK)</option>
              <option value="en-AU">English (AU)</option>
              <option value="es-ES">Spanish</option>
              <option value="fr-FR">French</option>
              <option value="de-DE">German</option>
              <option value="it-IT">Italian</option>
              <option value="pt-BR">Portuguese (BR)</option>
              <option value="ja-JP">Japanese</option>
              <option value="ko-KR">Korean</option>
              <option value="zh-CN">Chinese (Simplified)</option>
            </select>
          </Section>

          {/* Voice Activity Detection */}
          <Section title="Voice Detection">
            <div className="space-y-4">
              <SelectField
                label="Start sensitivity"
                hint="How easily Muse detects you started speaking"
                value={draft.startSensitivity}
                onChange={(v) => setDraft((d) => ({ ...d, startSensitivity: v as SpeechSensitivity }))}
                options={[
                  { value: "START_SENSITIVITY_LOW", label: "Low (fewer false triggers)" },
                  { value: "START_SENSITIVITY_MEDIUM", label: "Medium" },
                  { value: "START_SENSITIVITY_HIGH", label: "High (more responsive)" },
                ]}
              />
              <SelectField
                label="End sensitivity"
                hint="How quickly Muse decides you stopped speaking"
                value={draft.endSensitivity}
                onChange={(v) => setDraft((d) => ({ ...d, endSensitivity: v as EndSensitivity }))}
                options={[
                  { value: "END_SENSITIVITY_LOW", label: "Low (waits longer)" },
                  { value: "END_SENSITIVITY_MEDIUM", label: "Medium" },
                  { value: "END_SENSITIVITY_HIGH", label: "High (cuts off faster)" },
                ]}
              />
              <SliderField
                label="Silence duration"
                hint="Milliseconds of silence before turn ends"
                value={draft.silenceDurationMs}
                min={200}
                max={2000}
                step={100}
                unit="ms"
                onChange={(v) => setDraft((d) => ({ ...d, silenceDurationMs: v }))}
              />
              <SliderField
                label="Prefix padding"
                hint="Audio captured before speech detection"
                value={draft.prefixPaddingMs}
                min={0}
                max={1000}
                step={50}
                unit="ms"
                onChange={(v) => setDraft((d) => ({ ...d, prefixPaddingMs: v }))}
              />
            </div>
          </Section>

          {/* Session */}
          <Section title="Session">
            <SliderField
              label="Context window"
              hint="Tokens kept in Gemini's memory"
              value={draft.contextWindowTokens}
              min={10000}
              max={100000}
              step={5000}
              unit=" tokens"
              onChange={(v) => setDraft((d) => ({ ...d, contextWindowTokens: v }))}
            />
          </Section>

          {/* Display */}
          <Section title="Display">
            <SliderField
              label="Transcript lines"
              hint="Max lines kept in the overlay"
              value={draft.maxTranscriptLines}
              min={5}
              max={50}
              step={5}
              unit=""
              onChange={(v) => setDraft((d) => ({ ...d, maxTranscriptLines: v }))}
            />
          </Section>

          {/* Action buttons */}
          <div className="flex gap-3 mt-6 pt-4 border-t border-white/10">
            <button
              onClick={handleReset}
              className="flex-1 px-4 py-2.5 rounded-lg text-sm
                         bg-white/5 border border-white/10 text-white/60
                         hover:bg-white/10 hover:text-white/80 transition-colors"
            >
              Reset defaults
            </button>
            <button
              onClick={handleApply}
              disabled={!hasChanges || applying}
              className="flex-1 px-4 py-2.5 rounded-lg text-sm font-medium
                         bg-white/15 border border-white/20 text-white
                         hover:bg-white/25 transition-colors
                         disabled:opacity-30 disabled:cursor-not-allowed"
            >
              {applying ? "Reconnecting..." : isConnected ? "Apply & Reconnect" : "Apply"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------
// Sub-components
// ------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <h3 className="text-xs font-medium text-white/40 uppercase tracking-wider mb-3">{title}</h3>
      {children}
    </div>
  );
}

function SelectField({
  label,
  hint,
  value,
  onChange,
  options,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div>
      <label className="text-sm text-white/70">{label}</label>
      <p className="text-xs text-white/30 mb-1.5">{hint}</p>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2
                   text-sm text-white/80 outline-none focus:border-white/25
                   [&>option]:bg-zinc-900 [&>option]:text-white"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

function SliderField({
  label,
  hint,
  value,
  min,
  max,
  step,
  unit,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <label className="text-sm text-white/70">{label}</label>
        <span className="text-sm text-white/50 tabular-nums">{value.toLocaleString()}{unit}</span>
      </div>
      <p className="text-xs text-white/30 mb-2">{hint}</p>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full h-1.5 rounded-full appearance-none bg-white/10
                   [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
                   [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white/80
                   [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:shadow-sm
                   [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:h-4
                   [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:bg-white/80
                   [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:cursor-pointer"
      />
    </div>
  );
}

function CloseIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}
