'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { MODE_LABEL, resolutionLabel, timecode } from '@/lib/format';
import { applyPatch, FALLBACK_SETTINGS } from '@/lib/settings';
import { toast as notify } from '@/lib/toast';
import type { Clip, Project, Settings } from '@/lib/types';
import { IconDownload, IconRefresh, IconSparkles } from './Icons';
import YouTubeUpload from './YouTubeUpload';
import { Field, ProgressBar, Segmented, Slider, Spinner } from './ui';

const LAYOUTS = [
  { value: 'vertical', label: '9:16' },
  { value: 'horizontal', label: '16:9' },
  { value: 'square', label: '1:1' },
  { value: 'portrait', label: '4:5' },
  { value: 'facecam_top', label: 'Cam top' },
  { value: 'facecam_bottom', label: 'Cam bottom' },
  { value: 'facecam', label: 'Cam only' },
  { value: 'gameplay', label: 'Gameplay' },
  { value: 'auto', label: 'Auto' },
];

export default function ClipEditor({
  clip,
  project,
  onUpdated,
  onClose,
}: {
  clip: Clip;
  project: Project;
  onUpdated: () => void;
  onClose: () => void;
}) {
  const base: Settings = {
    ...FALLBACK_SETTINGS,
    ...(project.settings || {}),
    ...(clip.settings || {}),
    subtitle: {
      ...FALLBACK_SETTINGS.subtitle,
      ...((project.settings || {}).subtitle || {}),
      ...((clip.settings || {}).subtitle || {}),
    },
  };
  const [settings, setSettings] = useState<Settings>(base);
  const [start, setStart] = useState(clip.start);
  const [end, setEnd] = useState(clip.end);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [title, setTitle] = useState(clip.title);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    setSettings(base);
    setStart(clip.start);
    setEnd(clip.end);
    setTitle(clip.title);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip.id]);

  const patch = (p: Partial<Settings>) => setSettings((s) => applyPatch(s, p));
  const setSub = (p: Partial<Settings['subtitle']>) => patch({ subtitle: { ...settings.subtitle, ...p } });

  const isLiveClip = project.mode === 'live';
  const maxEnd = isLiveClip ? Math.max(clip.end + 60, end + 5) : Math.max(project.duration || end, end);
  const previewSrc = clip.status === 'done' && clip.url ? clip.url : project.source_url_local;

  const seek = () => videoRef.current?.currentTime ?? 0;

  const apply = async () => {
    setSaving(true);
    try {
      await api.updateClip(clip.id, { start, end, title, settings, render: true });
      notify('Re-rendering clip with your changes', 'success');
      onUpdated();
      onClose();
    } catch (err: any) {
      notify(err.message || 'Could not update the clip', 'error');
    } finally {
      setSaving(false);
    }
  };

  const regenerate = async () => {
    setRegenerating(true);
    try {
      await api.regenerateClip(clip.id);
      notify('Picked a new moment and queued the render', 'success');
      onUpdated();
      onClose();
    } catch (err: any) {
      notify(err.message || 'Could not regenerate this clip', 'error');
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-4">
        <div className="overflow-hidden rounded-xl border border-white/10 bg-black">
          {previewSrc ? (
            <video
              ref={videoRef}
              key={previewSrc}
              src={previewSrc}
              controls
              preload="metadata"
              className="max-h-[420px] w-full bg-black"
              onLoadedMetadata={() => {
                const v = videoRef.current;
                if (v && !isLiveClip) v.currentTime = Math.min(start, Math.max(0, (project.duration || 0) - 0.5));
              }}
            />
          ) : (
            <div className="flex h-48 items-center justify-center text-xs text-mist-500">
              Clip is still rendering — the preview appears when it is done.
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[11px] text-mist-500">
          <span className="badge badge-neutral">{MODE_LABEL[project.mode]}</span>
          <span>source {Math.round(project.duration)}s</span>
          <span>·</span>
          <span>{clip.reason || 'AI selected'}</span>
          {clip.error ? <span className="text-red-300">· {clip.error}</span> : null}
        </div>

        <Field
          label="Trim"
          hint={
            isLiveClip
              ? 'Live clips are trimmed inside their captured window.'
              : 'Use the player controls, then set the in/out points.'
          }
        >
          <div className="space-y-3 rounded-xl border border-white/[0.07] bg-ink-900/50 p-3">

            <div>
              <div className="mb-1 flex items-center justify-between text-[11px]">
                <span className="text-mist-400">Start</span>
                <span className="font-mono text-mist-200">{timecode(start, true)}</span>
              </div>
              <input
                type="range"
                min={0}
                max={Math.max(1, maxEnd - 1)}
                step={0.1}
                value={start}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setStart(v);
                  if (end - v < 1) setEnd(v + 1);
                }}
                className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-white/10 [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-accent"
              />
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between text-[11px]">
                <span className="text-mist-400">End</span>
                <span className="font-mono text-mist-200">{timecode(end, true)}</span>
              </div>
              <input
                type="range"
                min={Math.max(0, start + 1)}
                max={Math.max(start + 2, maxEnd)}
                step={0.1}
                value={end}
                onChange={(e) => setEnd(Number(e.target.value))}
                className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-white/10 [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-accent"
              />
            </div>
            {!isLiveClip ? (
              <div className="flex gap-2">
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setStart(Math.min(seek(), end - 1))}
                >
                  Start = playhead
                </button>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setEnd(Math.max(seek(), start + 1))}
                >
                  End = playhead
                </button>
              </div>
            ) : null}
            <p className="text-[11px] text-mist-500">
              Length {timecode(end - start, true)} · {clip.score ? `AI score ${Math.round(clip.score)}` : ''}
            </p>
          </div>
        </Field>

        <Field label="Clip title">
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
      </div>

      <div className="space-y-4">
        {clip.status === 'rendering' ? (
          <div className="rounded-xl border border-accent/25 bg-accent/[0.06] p-3">
            <div className="mb-1.5 flex items-center gap-2 text-xs text-accent-soft">
              <Spinner size={12} /> Rendering… {Math.round(clip.progress)}%
            </div>
            <ProgressBar value={clip.progress} />
          </div>
        ) : null}

        <Field label="Aspect ratio / layout">
          <div className="grid grid-cols-3 gap-1.5">
            {LAYOUTS.map((l) => (
              <button
                key={l.value}
                onClick={() => patch({ layout: l.value })}
                className={`rounded-lg border px-2 py-1.5 text-[11px] font-semibold transition ${
                  settings.layout === l.value
                    ? 'border-accent/60 bg-accent/10 text-white'
                    : 'border-white/[0.08] bg-ink-900/60 text-mist-400 hover:text-mist-100'
                }`}
              >
                {l.label}
              </button>
            ))}
          </div>
        </Field>

        <Field label="Resolution">
          <Segmented
            options={[720, 1080, 1440, 2160].map((r) => ({ value: r, label: resolutionLabel(r) }))}
            value={settings.resolution}
            onChange={(resolution) => patch({ resolution })}
          />
        </Field>

        <div className="rounded-xl border border-white/[0.07] bg-ink-900/50 p-3">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-mist-500">Subtitles</p>
          <div className="space-y-3">
            <select className="select !py-2" value={settings.subtitle.preset} onChange={(e) => setSub({ preset: e.target.value })}>
              {['bold_pop', 'karaoke', 'boxed', 'minimal', 'neon', 'clean_line'].map((p) => (
                <option key={p} value={p}>
                  {p.replace('_', ' ')}
                </option>
              ))}
            </select>
            <select className="select !py-2" value={settings.subtitle.font} onChange={(e) => setSub({ font: e.target.value })}>
              {[
                'Inter', 'Arial', 'Roboto', 'Montserrat', 'Poppins', 'Bebas Neue', 'Anton', 'Oswald',
                'League Spartan', 'Impact', 'Open Sans', 'Lato', 'Archivo Black', 'Barlow Condensed',
                'DM Sans', 'Playfair Display',
              ].map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
            <Segmented
              options={[
                { value: 'top', label: 'Top' },
                { value: 'middle', label: 'Mid' },
                { value: 'bottom', label: 'Bottom' },
              ]}
              value={settings.subtitle.position}
              onChange={(position) => setSub({ position: position as Settings['subtitle']['position'] })}
            />
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-mist-500">Size</span>
              <div className="flex-1">
                <Slider
                  value={settings.subtitle.font_size}
                  min={0.6}
                  max={1.8}
                  step={0.05}
                  onChange={(font_size) => setSub({ font_size })}
                  format={(v) => `${v.toFixed(2)}×`}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="flex items-center gap-2 text-[11px] text-mist-400">
                Text
                <input
                  type="color"
                  value={settings.subtitle.primary_color}
                  onChange={(e) => setSub({ primary_color: e.target.value })}
                  className="h-7 w-9 rounded border border-white/10 bg-ink-900 p-0.5"
                />
              </label>
              <label className="flex items-center gap-2 text-[11px] text-mist-400">
                Highlight
                <input
                  type="color"
                  value={settings.subtitle.highlight_color}
                  onChange={(e) => setSub({ highlight_color: e.target.value })}
                  className="h-7 w-9 rounded border border-white/10 bg-ink-900 p-0.5"
                />
              </label>
            </div>
            <label className="flex items-center justify-between text-[11px] text-mist-400">
              Subtitles on this clip
              <input
                type="checkbox"
                checked={settings.subtitle.enabled}
                onChange={(e) => setSub({ enabled: e.target.checked })}
                className="h-4 w-4 accent-accent"
              />
            </label>
          </div>
        </div>

        <Field label="Audio">
          <Segmented
            options={[
              { value: 'original', label: 'Original' },
              { value: 'high', label: 'High' },
              { value: 'custom', label: 'Custom' },
            ]}
            value={settings.audio_mode}
            onChange={(audio_mode) => patch({ audio_mode: audio_mode as Settings['audio_mode'] })}
          />
        </Field>

        <div className="space-y-2 pt-1">
          <button className="btn btn-primary w-full" onClick={apply} disabled={saving}>
            {saving ? <Spinner size={14} /> : <IconSparkles width={15} height={15} />}
            Apply & re-render
          </button>
          <div className="grid grid-cols-2 gap-2">
            <button className="btn btn-secondary btn-sm" onClick={regenerate} disabled={regenerating}>
              {regenerating ? <Spinner size={12} /> : <IconRefresh width={13} height={13} />}
              New moment
            </button>
            <a
              className={`btn btn-secondary btn-sm ${clip.status === 'done' ? '' : 'pointer-events-none opacity-40'}`}
              href={clip.download_url || '#'}
              download
            >
              <IconDownload width={13} height={13} />
              Download
            </a>
          </div>
          <YouTubeUpload clip={clip} onDone={onUpdated} />
        </div>
      </div>
    </div>
  );
}
