'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import { aspectLabel, resolutionLabel } from '@/lib/format';
import { toast as notify } from '@/lib/toast';
import type { Mode, Settings, SubtitleStyle } from '@/lib/types';
import { IconSparkles } from './Icons';
import { Field, Segmented, Slider, Spinner, Toggle } from './ui';

const LAYOUT_OPTIONS = [
  { id: 'vertical', label: '9:16', note: 'Vertical', ratio: 0.5625 },
  { id: 'horizontal', label: '16:9', note: 'Horizontal', ratio: 1.777 },
  { id: 'square', label: '1:1', note: 'Square', ratio: 1 },
  { id: 'portrait', label: '4:5', note: 'Portrait', ratio: 0.8 },
  { id: 'facecam_top', label: 'Cam↑', note: 'Facecam top', ratio: 0.5625 },
  { id: 'facecam_bottom', label: 'Cam↓', note: 'Facecam bottom', ratio: 0.5625 },
  { id: 'facecam', label: 'Cam', note: 'Facecam only', ratio: 0.5625 },
  { id: 'gameplay', label: 'Game', note: 'Gameplay only', ratio: 1.777 },
  { id: 'auto', label: 'Auto', note: 'Smart reframe', ratio: 0.5625 },
];

const PRESETS = [
  { id: 'bold_pop', label: 'Bold pop' },
  { id: 'karaoke', label: 'Karaoke' },
  { id: 'boxed', label: 'Boxed' },
  { id: 'minimal', label: 'Minimal' },
  { id: 'neon', label: 'Neon' },
  { id: 'clean_line', label: 'Clean' },
];

const FALLBACK_FONTS = [
  'Inter', 'Arial', 'Roboto', 'Montserrat', 'Poppins', 'Bebas Neue', 'Anton', 'Oswald',
  'League Spartan', 'Impact', 'Open Sans', 'Lato', 'Archivo Black', 'Barlow Condensed',
  'DM Sans', 'Playfair Display',
];

function LayoutPreview({ ratio, id }: { ratio: number; id: string }) {
  const isSplit = id.startsWith('facecam_');
  const height = 34;
  const width = Math.round(height * ratio);
  return (
    <div
      className="relative shrink-0 overflow-hidden rounded-[4px] border border-white/20 bg-ink-900"
      style={{ width: Math.max(width, 16), height }}
    >
      {isSplit ? (
        <>
          <div className="absolute inset-x-0 top-0 h-[42%] bg-accent/30" />
          <div className="absolute inset-x-0 bottom-0 top-[42%] bg-white/[0.08]" />
        </>
      ) : id === 'facecam' ? (
        <div className="absolute inset-0 bg-accent/30" />
      ) : id === 'gameplay' ? (
        <div className="absolute inset-0 bg-white/[0.1]" />
      ) : (
        <>
          <div className="absolute inset-0 bg-white/[0.07]" />
          <div className="absolute inset-x-1 bottom-1 h-1 rounded-full bg-accent/50" />
        </>
      )}
    </div>
  );
}

export default function SettingsPanel({
  value,
  onChange,
  mode,
  sourceHeight,
  sourceDuration,
  fonts,
}: {
  value: Settings;
  onChange: (patch: Partial<Settings>) => void;
  mode: Mode;
  sourceHeight?: number;
  sourceDuration?: number;
  fonts?: string[];
}) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const sub = value.subtitle;
  const fontList = fonts && fonts.length ? fonts : FALLBACK_FONTS;
  const setSub = (patch: Partial<SubtitleStyle>) => onChange({ subtitle: { ...sub, ...patch } });
  const isReframeable = ['vertical', 'square', 'portrait', 'auto'].includes(value.layout);
  const needsFacecam = ['facecam_top', 'facecam_bottom', 'facecam'].includes(value.layout);
  const effectiveHeight = sourceHeight
    ? Math.min(value.resolution, sourceHeight >= 2160 ? 2160 : sourceHeight >= 1440 ? 1440 : sourceHeight >= 1080 ? 1080 : 720)
    : value.resolution;
  const upscaleWarning = sourceHeight ? value.resolution > effectiveHeight : false;
  const speechSpeed = ({ tiny: 60, base: 28, small: 12, medium: 4 } as Record<string, number>)[
    value.whisper_model
  ] || 28;
  const speechEta = sourceDuration && sourceDuration > 600 ? sourceDuration / speechSpeed : 0;
  const rough = (s: number) => (s < 90 ? `${Math.round(s / 10) * 10}s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);

  const runPreview = async () => {
    setPreviewing(true);
    try {
      const res = await api.subtitlePreview(value);
      setPreviewUrl(`${res.url}?t=${Date.now()}`);
    } catch (err: any) {
      notify(err.message || 'Preview failed', 'error');
    } finally {
      setPreviewing(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* OUTPUT ------------------------------------------------------------ */}
      <section className="card p-5">
        <h3 className="mb-4 text-sm font-semibold text-mist-100">Clips & output</h3>
        <div className="grid gap-5 md:grid-cols-2">
          <Field label="Number of clips" hint="Up to 100 per job. Rendering runs in the background queue.">
            <div className="flex flex-wrap gap-1.5">
              {[1, 2, 5, 10, 20, 50, 100].map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => onChange({ clip_count: n })}
                  className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                    value.clip_count === n && ![1, 2, 5, 10, 20, 50, 100].includes(0)
                      ? 'border-accent/60 bg-accent/15 text-white'
                      : 'border-white/10 bg-ink-900/60 text-mist-400 hover:text-mist-100'
                  }`}
                >
                  {n}
                </button>
              ))}
              <input
                type="number"
                min={1}
                max={100}
                value={value.clip_count}
                onChange={(e) => onChange({ clip_count: Math.max(1, Math.min(100, Number(e.target.value) || 1)) })}
                className="input !w-20 !py-1.5 text-center text-xs"
                aria-label="Custom clip count"
              />
            </div>
          </Field>

          <Field label="Clip duration">
            <div className="flex flex-wrap gap-1.5">
              {[15, 30, 45, 60, 90, 120].map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => onChange({ clip_duration: d, custom_duration: null })}
                  className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                    value.clip_duration === d
                      ? 'border-accent/60 bg-accent/15 text-white'
                      : 'border-white/10 bg-ink-900/60 text-mist-400 hover:text-mist-100'
                  }`}
                >
                  {d}s
                </button>
              ))}
              <input
                type="number"
                min={5}
                max={600}
                value={value.clip_duration}
                onChange={(e) =>
                  onChange({
                    clip_duration: Math.max(5, Math.min(600, Number(e.target.value) || 30)),
                    custom_duration: Math.max(5, Math.min(600, Number(e.target.value) || 30)),
                  })
                }
                className="input !w-20 !py-1.5 text-center text-xs"
                aria-label="Custom duration"
              />
            </div>
          </Field>

          <Field label="Resolution" hint={upscaleWarning ? `Source is ${sourceHeight}p — clips will render at ${resolutionLabel(effectiveHeight)} to avoid fake upscaling.` : 'Never upscaled beyond the source footage.'}>
            <Segmented
              options={[720, 1080, 1440, 2160].map((r) => ({ value: r, label: resolutionLabel(r) }))}
              value={value.resolution}
              onChange={(resolution) => onChange({ resolution })}
            />
          </Field>

          <Field label="Container">
            <Segmented
              options={[
                { value: 'mp4', label: 'MP4 H.264' },
                { value: 'mp4_hevc', label: 'MP4 H.265' },
                { value: 'mov', label: 'MOV' },
              ]}
              value={value.format}
              onChange={(format) => onChange({ format: format as Settings['format'] })}
            />
          </Field>

          <Field label="Encode quality">
            <Segmented
              options={[
                { value: 'compact', label: 'Compact' },
                { value: 'balanced', label: 'Balanced' },
                { value: 'high', label: 'High' },
              ]}
              value={value.quality}
              onChange={(quality) => onChange({ quality: quality as Settings['quality'] })}
            />
          </Field>

          <Field label="Encoder">
            <Segmented
              options={[
                { value: 'auto', label: 'Auto (VideoToolbox)' },
                { value: 'software', label: 'x264 / x265' },
              ]}
              value={value.encoder === 'software' ? 'software' : 'auto'}
              onChange={(encoder) => onChange({ encoder })}
            />
          </Field>
        </div>
      </section>

      {/* LAYOUT ------------------------------------------------------------ */}
      <section className="card p-5">
        <h3 className="mb-1 text-sm font-semibold text-mist-100">Layout & framing</h3>
        <p className="mb-4 text-xs text-mist-500">
          Face detection keeps the speaker in frame. Facecam layouts crop the cam region and stack it with gameplay.
        </p>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-9">
          {LAYOUT_OPTIONS.map((opt) => (
            <button
              key={opt.id}
              type="button"
              onClick={() => onChange({ layout: opt.id as Settings['layout'] })}
              className={`flex flex-col items-center gap-2 rounded-xl border px-2 py-2.5 transition ${
                value.layout === opt.id
                  ? 'border-accent/60 bg-accent/10'
                  : 'border-white/[0.07] bg-ink-900/50 hover:border-white/20'
              }`}
            >
              <LayoutPreview ratio={opt.ratio} id={opt.id} />
              <span className="text-[10.5px] font-semibold leading-tight text-mist-200">{opt.note}</span>
              <span className="text-[9.5px] text-mist-500">{opt.label}</span>
            </button>
          ))}
        </div>

        {isReframeable ? (
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Field label="Reframing" hint="Blur padding keeps the whole frame visible; crop fills the canvas.">
              <Segmented
                options={[
                  { value: 'fit_blur', label: 'Fit + blur' },
                  { value: 'crop', label: 'Fill + crop' },
                ]}
                value={value.reframe}
                onChange={(reframe) => onChange({ reframe: reframe as Settings['reframe'] })}
              />
            </Field>
          </div>
        ) : null}

        {needsFacecam ? (
          <div className="mt-4 rounded-xl border border-white/[0.07] bg-ink-900/50 p-4">
            <div className="mb-3 flex items-center justify-between">
              <span className="label !mb-0">Facecam region (% of frame)</span>
              <span className="text-[10.5px] text-mist-500">auto-detected on upload</span>
            </div>
            <div className="grid grid-cols-4 gap-2">
              {(['x', 'y', 'w', 'h'] as const).map((key) => (
                <Field key={key} label={key.toUpperCase()}>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={Math.round((value.facecam_region?.[key] ?? (key === 'w' || key === 'h' ? 0.4 : 0)) * 100)}
                    onChange={(e) => {
                      const raw = Math.max(0, Math.min(100, Number(e.target.value) || 0)) / 100;
                      const current = value.facecam_region ?? { x: 0, y: 0, w: 0.4, h: 0.4 };
                      onChange({ facecam_region: { ...current, [key]: raw } });
                    }}
                    className="input !py-1.5 text-center text-xs"
                  />
                </Field>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      {/* AUDIO ------------------------------------------------------------- */}
      <section className="card p-5">
        <h3 className="mb-4 text-sm font-semibold text-mist-100">Audio</h3>
        <div className="grid gap-5 md:grid-cols-2">
          <Field label="Audio quality">
            <Segmented
              options={[
                { value: 'original', label: 'Original' },
                { value: 'high', label: 'High (256k+)' },
                { value: 'custom', label: 'Custom' },
              ]}
              value={value.audio_mode}
              onChange={(audio_mode) => onChange({ audio_mode: audio_mode as Settings['audio_mode'] })}
            />
          </Field>
          <Field label={value.audio_mode === 'custom' ? 'Audio bitrate' : 'Maximum bitrate'}>
            <Slider
              value={value.audio_bitrate}
              min={96}
              max={320}
              step={32}
              onChange={(audio_bitrate) => onChange({ audio_bitrate })}
              format={(v) => `${v}k`}
            />
          </Field>
          <div className="space-y-2 md:col-span-2">
            <Toggle
              checked={value.normalize_audio}
              onChange={(normalize_audio) => onChange({ normalize_audio })}
              label="Normalise loudness (EBU R128)"
              description="Prevents clipping and evens out quiet/loud moments. Turn off to keep the original mix untouched."
            />
          </div>
        </div>
      </section>

      {/* SUBTITLES --------------------------------------------------------- */}
      <section className="card p-5">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h3 className="text-sm font-semibold text-mist-100">AI subtitles</h3>
            <p className="mt-0.5 text-xs text-mist-500">
              Word-by-word captions burned into the exported video. Recognised locally with Whisper.
            </p>
          </div>
          <Toggle checked={sub.enabled} onChange={(enabled) => setSub({ enabled })} label="" />
        </div>

        {sub.enabled ? (
          <div className="space-y-5">
            <div>
              <span className="label">Style preset</span>
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                {PRESETS.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => setSub({ preset: p.id })}
                    className={`rounded-lg border px-2 py-2 text-[11.5px] font-semibold transition ${
                      sub.preset === p.id
                        ? 'border-accent/60 bg-accent/10 text-white'
                        : 'border-white/[0.07] bg-ink-900/50 text-mist-400 hover:text-mist-100'
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Font">
                <select className="select" value={sub.font} onChange={(e) => setSub({ font: e.target.value })}>
                  {fontList.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Position">
                <Segmented
                  options={[
                    { value: 'top', label: 'Top' },
                    { value: 'middle', label: 'Middle' },
                    { value: 'bottom', label: 'Bottom' },
                  ]}
                  value={sub.position}
                  onChange={(position) => setSub({ position: position as SubtitleStyle['position'] })}
                />
              </Field>
              <Field label="Animation">
                <select
                  className="select"
                  value={sub.animation}
                  onChange={(e) => setSub({ animation: e.target.value as SubtitleStyle['animation'] })}
                >
                  <option value="word_pop">Word pop (highlight)</option>
                  <option value="karaoke">Karaoke fill</option>
                  <option value="fade">Fade</option>
                  <option value="none">Static</option>
                </select>
              </Field>
              <Field label="Font size">
                <Slider
                  value={sub.font_size}
                  min={0.6}
                  max={1.8}
                  step={0.05}
                  onChange={(font_size) => setSub({ font_size })}
                  format={(v) => `${v.toFixed(2)}×`}
                />
              </Field>
              <Field label="Weight">
                <Slider
                  value={sub.weight}
                  min={300}
                  max={900}
                  step={100}
                  onChange={(weight) => setSub({ weight })}
                  format={(v) => String(v)}
                />
              </Field>
              <Field label="Words per line">
                <Slider
                  value={sub.max_words}
                  min={1}
                  max={8}
                  onChange={(max_words) => setSub({ max_words })}
                  format={(v) => String(v)}
                />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {(
                [
                  ['primary_color', 'Text'],
                  ['highlight_color', 'Highlight'],
                  ['outline_color', 'Outline'],
                  ['background_color', 'Box'],
                ] as const
              ).map(([key, label]) => (
                <Field key={key} label={label}>
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={sub[key]}
                      onChange={(e) => setSub({ [key]: e.target.value } as Partial<SubtitleStyle>)}
                      className="h-9 w-11 cursor-pointer rounded-lg border border-white/10 bg-ink-900 p-0.5"
                    />
                    <input
                      className="input !py-1.5 font-mono !text-xs uppercase"
                      value={sub[key]}
                      onChange={(e) => setSub({ [key]: e.target.value } as Partial<SubtitleStyle>)}
                    />
                  </div>
                </Field>
              ))}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Outline thickness">
                <Slider
                  value={sub.outline_width}
                  min={0}
                  max={12}
                  step={0.5}
                  onChange={(outline_width) => setSub({ outline_width })}
                />
              </Field>
              <Field label="Shadow">
                <Slider value={sub.shadow} min={0} max={12} step={0.5} onChange={(shadow) => setSub({ shadow })} />
              </Field>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <Toggle
                checked={sub.uppercase}
                onChange={(uppercase) => setSub({ uppercase })}
                label="UPPERCASE"
                description="Viral-caption casing"
              />
              <Toggle
                checked={sub.background}
                onChange={(background) => setSub({ background })}
                label="Background box"
                description="Solid plate behind text"
              />
              <Toggle
                checked={sub.emoji}
                onChange={(emoji) => setSub({ emoji })}
                label="Auto emoji"
                description="Adds emoji to matching lines"
              />
            </div>

            {sub.background ? (
              <Field label="Box opacity">
                <Slider
                  value={Math.round(sub.background_opacity * 100)}
                  min={0}
                  max={100}
                  onChange={(v) => setSub({ background_opacity: v / 100 })}
                  format={(v) => `${v}%`}
                />
              </Field>
            ) : null}

            <div className="flex flex-wrap items-center gap-3 pt-1">
              <button type="button" onClick={runPreview} className="btn btn-secondary btn-sm" disabled={previewing}>
                {previewing ? <Spinner size={13} /> : <IconSparkles width={14} height={14} />}
                Preview style
              </button>
              <span className="text-[11px] text-mist-500">Renders a 5s sample with the real engine.</span>
            </div>
            {previewUrl ? (
              <video
                key={previewUrl}
                src={previewUrl}
                controls
                autoPlay
                loop
                muted
                className="max-h-[320px] w-full max-w-[240px] rounded-xl border border-white/10 bg-black"
              />
            ) : null}
          </div>
        ) : null}
      </section>

      {/* AI ----------------------------------------------------------------- */}
      <section className="card p-5">
        <h3 className="mb-4 text-sm font-semibold text-mist-100">
          {mode === 'live' ? 'Live detection' : 'Highlight detection'}
        </h3>
        <div className="grid gap-5 md:grid-cols-2">
          <Field
            label={mode === 'live' ? 'Live sensitivity' : 'Sensitivity'}
            hint="Higher = catches quieter moments. Lower = only picks peak energy and strong hooks."
          >
            <Slider
              value={mode === 'live' ? value.live_sensitivity : value.highlight_sensitivity}
              min={0}
              max={100}
              onChange={(v) =>
                onChange(mode === 'live' ? { live_sensitivity: v } : { highlight_sensitivity: v })
              }
              format={(v) => `${v}`}
            />
          </Field>
          <Field
            label="Speech model"
            hint={
              speechEta
                ? `Runs on-device. On this ${rough(sourceDuration!)} source, ${value.whisper_model} takes roughly ${rough(speechEta)} — Base is the practical choice for long VODs.`
                : 'Runs on-device. Base handles a 20-minute video in minutes; Medium/Large are more accurate but can take several times longer.'
            }
          >
            <Segmented
              options={[
                { value: 'tiny', label: 'Tiny' },
                { value: 'base', label: 'Base' },
                { value: 'small', label: 'Small' },
                { value: 'medium', label: 'Medium' },
              ]}
              value={value.whisper_model}
              onChange={(whisper_model) => onChange({ whisper_model })}
            />
          </Field>
          <Field label="Language">
            <select className="select" value={value.language} onChange={(e) => onChange({ language: e.target.value })}>
              <option value="auto">Auto-detect</option>
              {['en', 'es', 'fr', 'de', 'pt', 'it', 'nl', 'hi', 'ja', 'ko', 'zh', 'ru', 'ar', 'tr', 'pl'].map((l) => (
                <option key={l} value={l}>
                  {l.toUpperCase()}
                </option>
              ))}
            </select>
          </Field>
          <div className="pt-1">
            <Toggle
              checked={value.allow_overlap}
              onChange={(allow_overlap) => onChange({ allow_overlap })}
              label="Allow overlapping moments"
              description="By default clips never repeat the same footage."
            />
          </div>
        </div>

        {mode === 'live' ? (
          <div className="mt-5 grid gap-5 border-t border-white/[0.06] pt-5 md:grid-cols-2">
            <Field label="Pre-roll" hint="How much of the clip comes before the detected peak.">
              <Slider
                value={Math.round(value.live_pre_roll * 100)}
                min={25}
                max={85}
                onChange={(v) => onChange({ live_pre_roll: v / 100 })}
                format={(v) => `${v}%`}
              />
            </Field>
            <Field label="Analysis window" hint="Seconds of stream considered per highlight decision.">
              <Slider
                value={value.live_analysis_window}
                min={15}
                max={90}
                step={5}
                onChange={(live_analysis_window) => onChange({ live_analysis_window })}
                format={(v) => `${v}s`}
              />
            </Field>
            <div className="md:col-span-2">
              <Toggle
                checked={value.live_unlimited}
                onChange={(live_unlimited) => onChange({ live_unlimited })}
                label="Unlimited clips"
                description="Keeps clipping until you stop the session (storage permitting)."
              />
            </div>
          </div>
        ) : null}
      </section>

      <p className="px-1 text-[11px] leading-relaxed text-mist-500">
        Output {aspectLabel(value.layout)} · {resolutionLabel(effectiveHeight)} ·{' '}
        {value.format === 'mp4_hevc' ? 'H.265' : value.format === 'mov' ? 'MOV' : 'H.264'} ·{' '}
        {value.clip_count} × {value.clip_duration}s · subtitles {sub.enabled ? sub.font : 'off'}
      </p>
    </div>
  );
}
