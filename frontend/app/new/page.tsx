'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import SettingsPanel from '@/components/SettingsPanel';
import { IconAlert, IconArrowLeft, IconCheck, IconFilm, IconLink, IconLive, IconUpload } from '@/components/Icons';
import { Field, ProgressBar, Spinner } from '@/components/ui';
import { api, uploadFile } from '@/lib/api';
import { bytes, secondsLabel } from '@/lib/format';
import { useQueryParam, useSystem } from '@/lib/hooks';
import { applyPatch, loadDefaults, saveDefaults } from '@/lib/settings';
import { toast as notify } from '@/lib/toast';
import type { Settings } from '@/lib/types';

type Stage = 'idle' | 'uploading' | 'creating' | 'ingesting' | 'starting' | 'done';

interface Uploaded {
  temp_path: string;
  filename: string;
  size: number;
  probe: { duration: number; width: number; height: number; has_audio: boolean };
}

export default function NewProjectPage() {
  const modeParam = useQueryParam('mode');
  const system = useSystem();
  const router = useRouter();
  const [mode, setMode] = useState<'video' | 'stream'>('video');
  const [sourceType, setSourceType] = useState<'file' | 'url'>('file');
  const [file, setFile] = useState<File | null>(null);
  const [uploaded, setUploaded] = useState<Uploaded | null>(null);
  const [uploadPct, setUploadPct] = useState(0);
  const [url, setUrl] = useState('');
  const [urlInfo, setUrlInfo] = useState<{ title: string; duration: number; is_live: boolean } | null>(null);
  const [validating, setValidating] = useState(false);
  const [name, setName] = useState('');
  const [nameTouched, setNameTouched] = useState(false);
  const [settings, setSettings] = useState<Settings>(() => loadDefaults());
  const [stage, setStage] = useState<Stage>('idle');
  const [stageMessage, setStageMessage] = useState('');
  const [stagePct, setStagePct] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const modeApplied = useRef(false);

  useEffect(() => {
    if (modeApplied.current) return;
    const chosen = modeParam === 'stream' ? 'stream' : 'video';
    setMode(chosen);
    if (modeParam) modeApplied.current = true;
    if (chosen === 'stream') setSettings((s) => ({ ...s, layout: 'facecam_top' }));
  }, [modeParam]);

  useEffect(() => {
    if (system?.defaults) setSettings((s) => loadDefaults({ ...system.defaults, ...withoutEmpty(s) }));
  }, [system]);

  function withoutEmpty(s: Settings) {
    return s;
  }

  const patch = useCallback((p: Partial<Settings>) => {
    setSettings((s) => applyPatch(s, p));
  }, []);

  const probe = uploaded?.probe || null;
  const duration = probe?.duration || urlInfo?.duration || 0;
  const canStart = Boolean((sourceType === 'file' && uploaded) || (sourceType === 'url' && urlInfo)) && !stage.includes('ing');

  const applyMode = (next: 'video' | 'stream') => {
    setMode(next);
    if (next === 'stream' && settings.layout === 'vertical') patch({ layout: 'facecam_top' });
  };

  const handleFile = async (f: File) => {
    setError(null);
    const ext = f.name.split('.').pop()?.toLowerCase() || '';
    if (!['mp4', 'mov', 'mkv', 'webm', 'm4v'].includes(ext)) {
      setError('Unsupported format. Use MP4, MOV, MKV or WebM.');
      return;
    }
    setFile(f);
    setUploaded(null);
    setStage('uploading');
    setUploadPct(0);
    try {
      const res = await uploadFile(f, setUploadPct);
      setUploaded(res as Uploaded);
      if (!nameTouched) setName(f.name.replace(/\.[^.]+$/, ''));
      setStage('idle');
      setStageMessage('');
    } catch (err: any) {
      setError(err.message || 'Upload failed');
      setStage('idle');
    }
  };

  const validateUrl = async () => {
    if (!url.trim()) return;
    setValidating(true);
    setError(null);
    setUrlInfo(null);
    try {
      const info = await api.validateUrl(url.trim());
      if (info.is_live) {
        setError('That link is a live stream. Use Live clipping instead.');
        return;
      }
      setUrlInfo({ title: info.title, duration: info.duration, is_live: false });
      if (!nameTouched) setName(info.title || 'Untitled');
    } catch (err: any) {
      setError(err.message || 'Could not read that link');
    } finally {
      setValidating(false);
    }
  };

  const start = async () => {
    if (!canStart) return;
    setError(null);
    saveDefaults(settings);
    try {
      setStage('creating');
      setStageMessage('Creating project…');
      setStagePct(5);
      const created = await api.createProject({
        name: name || 'Untitled project',
        mode,
        source_type: sourceType,
        url: sourceType === 'url' ? url.trim() : undefined,
        temp_path: sourceType === 'file' ? uploaded?.temp_path : undefined,
        settings,
      });
      const id = created.project_id;
      // Hand off to the project page right away: it follows the real import job
      // with live progress and the engine kicks off analysis on its own once the
      // source is ready. Nothing here can time out while a long VOD downloads.
      setStageMessage('Project created — opening it…');
      setStagePct(60);
      setStage('done');
      router.push(`/project/?id=${id}`);
    } catch (err: any) {
      setError(err.message || 'Something went wrong starting the project');
      setStage('idle');
      setStagePct(0);
    }
  };

  const busy = stage !== 'idle' && stage !== 'done';
  const sourceSummary = useMemo(() => {
    if (sourceType === 'file' && uploaded) {
      return `${uploaded.filename} · ${bytes(uploaded.size)} · ${secondsLabel(uploaded.probe.duration)} · ${uploaded.probe.width}×${uploaded.probe.height}`;
    }
    if (sourceType === 'url' && urlInfo) {
      return `${urlInfo.title} · ${secondsLabel(urlInfo.duration)}`;
    }
    return '';
  }, [sourceType, uploaded, urlInfo]);

  return (
    <div className="space-y-6 pb-28">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="btn btn-ghost btn-sm !px-2">
            <IconArrowLeft width={16} height={16} />
          </Link>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">New clipping project</h1>
            <p className="text-xs text-mist-500">Same engine, subtitles and export pipeline for every mode.</p>
          </div>
        </div>
        <div className="seg">
          {(['video', 'stream'] as const).map((m) => (
            <button
              key={m}
              onClick={() => applyMode(m)}
              className={`seg-item ${mode === m ? 'seg-item-active' : ''}`}
            >
              {m === 'video' ? 'Normal video' : 'Recorded stream'}
            </button>
          ))}
          <Link href="/live" className="seg-item flex items-center gap-1.5">
            <IconLive width={13} height={13} /> Live
          </Link>
        </div>
      </div>

      <section className="card p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-mist-100">1 · Source</h2>
            <p className="mt-0.5 text-xs text-mist-500">
              {mode === 'stream'
                ? 'Upload the recorded stream (VOD). Facecam regions are detected automatically.'
                : 'Upload a video file or paste a supported link.'}
            </p>
          </div>
          <div className="seg">
            <button
              onClick={() => setSourceType('file')}
              className={`seg-item flex items-center gap-1.5 ${sourceType === 'file' ? 'seg-item-active' : ''}`}
            >
              <IconUpload width={13} height={13} /> File
            </button>
            <button
              onClick={() => setSourceType('url')}
              className={`seg-item flex items-center gap-1.5 ${sourceType === 'url' ? 'seg-item-active' : ''}`}
            >
              <IconLink width={13} height={13} /> URL
            </button>
          </div>
        </div>

        {sourceType === 'file' ? (
          <div>
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                const f = e.dataTransfer.files?.[0];
                if (f) handleFile(f);
              }}
              onClick={() => inputRef.current?.click()}
              className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed px-6 py-10 text-center transition ${
                dragging ? 'border-accent/70 bg-accent/[0.07]' : 'border-white/[0.12] bg-ink-900/40 hover:border-white/25'
              }`}
            >
              <input
                ref={inputRef}
                type="file"
                accept="video/*,.mkv,.mov,.mp4,.webm"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleFile(f);
                }}
              />
              {stage === 'uploading' ? (
                <div className="w-full max-w-sm">
                  <div className="mb-2 flex items-center justify-between text-xs text-mist-300">
                    <span className="truncate">{file?.name}</span>
                    <span className="font-mono">{uploadPct}%</span>
                  </div>
                  <ProgressBar value={uploadPct} />
                </div>
              ) : uploaded ? (
                <div className="flex flex-col items-center gap-1">
                  <span className="flex items-center gap-2 text-sm font-medium text-emerald-300">
                    <IconCheck width={16} height={16} /> {uploaded.filename}
                  </span>
                  <span className="text-xs text-mist-400">
                    {secondsLabel(uploaded.probe.duration)} · {uploaded.probe.width}×{uploaded.probe.height} ·{' '}
                    {bytes(uploaded.size)}
                  </span>
                  <span className="mt-1 text-[11px] text-mist-500">Drop another file to replace it</span>
                </div>
              ) : (
                <>
                  <span className="mb-2 flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-mist-300">
                    <IconUpload width={18} height={18} />
                  </span>
                  <p className="text-sm font-medium text-mist-200">Drop a video here, or click to browse</p>
                  <p className="mt-1 text-xs text-mist-500">MP4 · MOV · MKV · WebM — original files are never deleted</p>
                </>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                className="input"
                placeholder="https://www.youtube.com/watch?v=…"
                value={url}
                onChange={(e) => {
                  setUrl(e.target.value);
                  setUrlInfo(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') validateUrl();
                }}
              />
              <button className="btn btn-secondary shrink-0" onClick={validateUrl} disabled={validating || !url.trim()}>
                {validating ? <Spinner size={14} /> : <IconLink width={14} height={14} />}
                Check link
              </button>
            </div>
            {urlInfo ? (
              <p className="flex items-center gap-2 text-xs text-emerald-300">
                <IconCheck width={13} height={13} /> {urlInfo.title} · {secondsLabel(urlInfo.duration)}
              </p>
            ) : (
              <p className="hint">
                YouTube, Twitch VODs and other sites supported by yt-dlp. DRM-protected or paywalled content is not
                supported.
              </p>
            )}
          </div>
        )}

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <Field label="Project name">
            <input
              className="input"
              value={name}
              placeholder="e.g. Podcast ep. 42 highlights"
              onChange={(e) => {
                setName(e.target.value);
                setNameTouched(true);
              }}
            />
          </Field>
          <div className="sm:pt-6">
            {sourceSummary ? (
              <p className="truncate text-xs text-mist-400">{sourceSummary}</p>
            ) : (
              <p className="text-xs text-mist-500">Add a source to continue.</p>
            )}
          </div>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-sm font-semibold text-mist-100">2 · Clips, layout & style</h2>
        <SettingsPanel
          value={settings}
          onChange={patch}
          mode={mode === 'stream' ? 'stream' : 'video'}
          sourceHeight={probe?.height}
          sourceDuration={duration}
          fonts={system?.limits.fonts}
        />
      </section>

      {error ? (
        <div className="flex items-start gap-2.5 rounded-xl border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          <IconAlert width={16} height={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-white/[0.07] bg-ink-950/92 backdrop-blur-xl lg:left-[230px]">
        <div className="mx-auto flex max-w-[1440px] flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-7">
          <div className="min-w-0">
            {busy ? (
              <div className="w-full max-w-md">
                <div className="mb-1 flex items-center justify-between gap-4 text-xs text-mist-300">
                  <span className="flex items-center gap-2">
                    <Spinner size={12} /> {stageMessage}
                  </span>
                  <span className="font-mono">{stagePct}%</span>
                </div>
                <ProgressBar value={stagePct} />
              </div>
            ) : (
              <p className="truncate text-xs text-mist-400">
                {duration ? `${secondsLabel(duration)} source · ` : ''}
                {settings.clip_count} clips × {settings.clip_duration}s · {settings.layout.replace('_', ' ')} ·{' '}
                {settings.resolution >= 2160 ? '4K' : `${settings.resolution}p`}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Link href="/" className="btn btn-ghost btn-sm">
              Cancel
            </Link>
            <button className="btn btn-primary" onClick={start} disabled={!canStart || busy}>
              {busy ? <Spinner size={15} /> : <IconFilm width={16} height={16} />}
              {busy ? 'Working…' : 'Analyse & generate clips'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
