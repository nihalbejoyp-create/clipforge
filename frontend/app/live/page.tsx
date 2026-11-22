'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ClipEditor from '@/components/ClipEditor';
import SettingsPanel from '@/components/SettingsPanel';
import {
  IconAlert,
  IconArrowLeft,
  IconDownload,
  IconFilm,
  IconLive,
  IconRefresh,
  IconSliders,
  IconTrash,
} from '@/components/Icons';
import { EmptyState, Field, Modal, ProgressBar, SectionHeading, Spinner, StatusBadge } from '@/components/ui';
import { api } from '@/lib/api';
import { relativeTime, secondsLabel, timecode } from '@/lib/format';
import { usePolling, useQueryParam, useSystem } from '@/lib/hooks';
import { applyPatch, loadDefaults } from '@/lib/settings';
import { toast as notify } from '@/lib/toast';
import type { Clip, LiveSession, Project, Settings } from '@/lib/types';

export default function LivePage() {
  const system = useSystem();
  const initial = useQueryParam('id');
  const [activeId, setActiveId] = useState('');
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [settings, setSettings] = useState<Settings>(() => loadDefaults());
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [editing, setEditing] = useState<Clip | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const hlsRef = useRef<any>(null);

  useEffect(() => {
    if (initial) setActiveId(initial);
  }, [initial]);

  useEffect(() => {
    if (system?.defaults) setSettings((s) => loadDefaults({ ...system.defaults, ...s }));
  }, [system]);

  const sessionsQ = usePolling(() => api.liveSessions(), 4000);
  const sessions: LiveSession[] = sessionsQ.data?.sessions || [];
  const detailQ = usePolling(
    () => (activeId ? api.liveDetail(activeId) : Promise.reject(new Error('no session'))),
    activeId ? 2500 : null,
    [activeId],
  );
  const session = detailQ.data?.session || null;
  const projectQ = usePolling(
    () => (session?.project_id ? api.project(session.project_id) : Promise.reject(new Error('no project'))),
    session?.project_id ? 6000 : null,
    [session?.project_id],
  );
  const project: Project | null = projectQ.data?.project || null;
  const clips: Clip[] = session?.clips_list || [];
  const rendering = clips.filter((c) => c.status === 'rendering' || c.status === 'pending').length;

  // Live preview through the captured HLS window.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !activeId) return;
    const src = `/api/live/${activeId}/playlist.m3u8`;
    let cancelled = false;
    const attach = async () => {
      if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = src;
        video.play().catch(() => undefined);
        return;
      }
      const mod = await import('hls.js');
      if (cancelled) return;
      const Hls = mod.default;
      if (!Hls.isSupported()) return;
      const hls = new Hls({ lowLatencyMode: false, liveSyncDurationCount: 3, maxBufferLength: 12 });
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => undefined));
      hlsRef.current = hls;
    };
    attach();
    return () => {
      cancelled = true;
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [activeId]);

  const start = async () => {
    if (!url.trim()) return;
    setStarting(true);
    setError(null);
    try {
      const res = await api.liveStart(url.trim(), settings, title.trim() || undefined);
      setActiveId(res.session.id);
      setShowSettings(false);
      notify('Connected — analysing the live feed', 'success');
      sessionsQ.refresh();
    } catch (err: any) {
      setError(err.message || 'Could not start that stream');
    } finally {
      setStarting(false);
    }
  };

  const stop = async () => {
    if (!session) return;
    if (!window.confirm('Stop capturing this stream? Clips already generated stay available.')) return;
    try {
      await api.liveStop(session.id);
      notify('Stream capture stopped');
      detailQ.refresh();
      sessionsQ.refresh();
    } catch (err: any) {
      notify(err.message, 'error');
    }
  };

  const remove = async () => {
    if (!session?.project_id) return;
    if (!window.confirm('Delete this live project, its clips and captured segments?')) return;
    try {
      await api.liveStop(session.id).catch(() => undefined);
      await api.deleteProject(session.project_id, true);
      notify('Live project deleted');
      setActiveId('');
      sessionsQ.refresh();
    } catch (err: any) {
      notify(err.message, 'error');
    }
  };

  const activeSessions = useMemo(
    () => sessions.filter((s) => ['live', 'reconnecting', 'starting'].includes(s.status)),
    [sessions],
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {activeId ? (
            <button className="btn btn-ghost btn-sm !px-2" onClick={() => setActiveId('')}>
              <IconArrowLeft width={16} height={16} />
            </button>
          ) : null}
          <div>
            <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
              <IconLive width={18} height={18} className="text-emerald-300" /> Live clipping
            </h1>
            <p className="text-xs text-mist-500">
              Connect a running stream — clips are cut and rendered while it is still broadcasting.
            </p>
          </div>
        </div>
        {!activeId ? (
          <button className="btn btn-secondary btn-sm" onClick={() => setShowSettings((v) => !v)}>
            <IconSliders width={14} height={14} /> {showSettings ? 'Hide settings' : 'Clip settings'}
          </button>
        ) : null}
      </div>

      {!activeId ? (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-4">
            <section className="card p-5">
              <h2 className="text-sm font-semibold text-mist-100">Connect a stream</h2>
              <p className="mt-0.5 text-xs text-mist-500">
                YouTube Live, Twitch, any HLS (.m3u8) or RTMP source. Use authorized streams only.
              </p>
              <div className="mt-4 space-y-3">
                <Field label="Stream URL">
                  <input
                    className="input"
                    placeholder="https://www.youtube.com/watch?v=… or https://example.com/live.m3u8"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') start();
                    }}
                  />
                </Field>
                <Field label="Session name (optional)">
                  <input className="input" placeholder="Auto from stream title" value={title} onChange={(e) => setTitle(e.target.value)} />
                </Field>
                <div className="rounded-xl border border-white/[0.07] bg-ink-900/50 p-3 text-[11.5px] leading-relaxed text-mist-400">
                  <p className="mb-1 flex items-center gap-1.5 font-medium text-mist-200">
                    <IconAlert width={13} height={13} className="text-amber-300" /> How live capture works
                  </p>
                  The stream is re-muxed into 2s HLS segments on this Mac. A rolling analyser reads audio energy and
                  speech, and when a highlight is detected the covering segments are materialised and rendered with the
                  same subtitle + layout engine. Capture reconnects automatically.
                </div>
                {error ? (
                  <div className="flex items-start gap-2 rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
                    <IconAlert width={14} height={14} className="mt-0.5 shrink-0" />
                    {error}
                  </div>
                ) : null}
                <div className="flex items-center justify-between gap-3">
                  <p className="text-[11px] text-mist-500">
                    {settings.clip_count}
                    {settings.live_unlimited ? '' : '+'} clips · {settings.clip_duration}s · subtitles{' '}
                    {settings.subtitle.enabled ? settings.subtitle.font : 'off'}
                  </p>
                  <button className="btn btn-primary" onClick={start} disabled={starting || !url.trim()}>
                    {starting ? <Spinner size={15} /> : <IconLive width={16} height={16} />}
                    {starting ? 'Connecting…' : 'Start live clipping'}
                  </button>
                </div>
              </div>
            </section>

            {showSettings ? (
              <SettingsPanel
                value={settings}
                onChange={(p) => setSettings((s) => applyPatch(s, p))}
                mode="live"
                fonts={system?.limits.fonts}
              />
            ) : null}
          </div>

          <aside className="space-y-3">
            <SectionHeading title="Sessions" subtitle={activeSessions.length ? `${activeSessions.length} running` : 'none running'} />
            {sessions.length === 0 ? (
              <EmptyState
                icon={<IconLive width={22} height={22} />}
                title="No live sessions yet"
                description="Start one on the left. Clips appear here as they render."
              />
            ) : (
              <div className="space-y-2">
                {sessions.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => setActiveId(s.id)}
                    className="card card-hover w-full p-3 text-left"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="truncate text-[12.5px] font-medium text-mist-100">{s.title || s.url}</p>
                      <StatusBadge status={s.status} />
                    </div>
                    <p className="mt-1 text-[10.5px] text-mist-500">
                      {secondsLabel(s.duration || 0)} captured · {s.clips || 0} clips · {relativeTime(s.started_at)}
                    </p>
                  </button>
                ))}
              </div>
            )}
          </aside>
        </div>
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <StatusBadge status={session?.status || 'starting'} />
              <span className="text-xs text-mist-400">{secondsLabel(session?.duration || 0)} captured</span>
              {session?.error ? (
                <span className="flex items-center gap-1 text-[11px] text-amber-300">
                  <IconAlert width={12} height={12} /> {session.error}
                </span>
              ) : null}
            </div>
            <div className="flex gap-2">
              <a
                className={`btn btn-secondary btn-sm ${clips.some((c) => c.status === 'done') ? '' : 'pointer-events-none opacity-40'}`}
                href={session?.project_id ? `/api/projects/${session.project_id}/download-all` : '#'}
                download
              >
                <IconDownload width={14} height={14} /> Download all
              </a>
              {['live', 'reconnecting', 'starting'].includes(session?.status || '') ? (
                <button className="btn btn-danger btn-sm" onClick={stop}>
                  Stop capture
                </button>
              ) : (
                <button className="btn btn-danger btn-sm" onClick={remove}>
                  <IconTrash width={14} height={14} />
                </button>
              )}
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
            <div className="card overflow-hidden">
              <div className="relative aspect-video bg-black">
                <video ref={videoRef} controls muted playsInline className="h-full w-full" poster={project?.thumb_url || undefined} />
                {['starting', 'reconnecting'].includes(session?.status || '') ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/70 text-center">
                    <Spinner size={20} className="text-accent-soft" />
                    <p className="text-xs text-mist-200">
                      {session?.status === 'reconnecting' ? 'Reconnecting to the stream…' : 'Waiting for the first segments…'}
                    </p>
                  </div>
                ) : null}
              </div>
              <div className="grid grid-cols-2 gap-px bg-white/[0.06] sm:grid-cols-4">
                {[
                  ['Highlights', String(session?.highlights || 0)],
                  ['Clips', String(clips.length)],
                  ['Rendering', String(rendering)],
                  ['Buffer', `${session?.segments || 0} segs`],
                ].map(([label, value]) => (
                  <div key={label} className="bg-ink-850 px-3 py-2.5">
                    <p className="text-[10px] uppercase tracking-[0.08em] text-mist-500">{label}</p>
                    <p className="mt-0.5 text-sm font-semibold text-mist-100">{value}</p>
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-px border-t border-white/[0.06] bg-white/[0.06] sm:grid-cols-4">
                {[
                  ['Resolution', session?.width ? `${session.width}×${session.height}` : '—'],
                  ['Audio', session?.has_audio ? 'receiving' : 'none'],
                  ['Disk free', `${session?.disk_free_gb ?? '—'} GB`],
                  ['Mode', 'live capture'],
                ].map(([label, value]) => (
                  <div key={label} className="bg-ink-850 px-3 py-2.5">
                    <p className="text-[10px] uppercase tracking-[0.08em] text-mist-500">{label}</p>
                    <p className="mt-0.5 text-[12px] font-medium text-mist-200">{value}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="card p-4">
              <SectionHeading title="Live clip gallery" subtitle="New clips land here automatically." />
              {clips.length === 0 ? (
                <div className="rounded-xl border border-dashed border-white/[0.09] px-4 py-8 text-center">
                  <Spinner size={16} className="mx-auto mb-2 text-mist-500" />
                  <p className="text-xs text-mist-400">Listening for highlights…</p>
                  <p className="mt-1 text-[11px] text-mist-500">
                    A clip is cut when the analyser spots a spike in energy or a strong spoken hook.
                  </p>
                </div>
              ) : (
                <div className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
                  {clips.map((clip) => (
                    <button
                      key={clip.id}
                      onClick={() => setEditing(clip)}
                      className="flex w-full items-center gap-3 rounded-xl border border-white/[0.06] bg-ink-900/50 p-2 text-left transition hover:border-white/15"
                    >
                      <div className="relative h-14 w-10 shrink-0 overflow-hidden rounded-md bg-ink-800">
                        {clip.thumb_url ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={clip.thumb_url} alt="" className="h-full w-full object-cover" />
                        ) : (
                          <div className="flex h-full items-center justify-center text-mist-600">
                            <IconFilm width={14} height={14} />
                          </div>
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[12px] font-medium text-mist-100">{clip.title || 'Live moment'}</p>
                        <div className="mt-1 flex items-center gap-2">
                          <StatusBadge status={clip.status} />
                          <span className="font-mono text-[10px] text-mist-500">{timecode(clip.duration || 0)}</span>
                          {clip.score ? (
                            <span className="font-mono text-[10px] text-accent-soft">{Math.round(clip.score)}</span>
                          ) : null}
                        </div>
                        {clip.status === 'rendering' || clip.status === 'pending' ? (
                          <div className="mt-1.5">
                            <ProgressBar value={clip.status === 'pending' ? 4 : clip.progress} />
                          </div>
                        ) : null}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="card flex flex-wrap items-center justify-between gap-3 p-4">
            <p className="text-[11.5px] leading-relaxed text-mist-400">
              Captured segments stay on disk at <span className="font-mono text-mist-300">data/live/{session?.id}</span> so
              nothing is lost. Delete the session to reclaim the space.
            </p>
            <div className="flex gap-2">
              <Link href={`/project/?id=${session?.project_id || ''}`} className="btn btn-secondary btn-sm">
                Open in project view
              </Link>
              <button className="btn btn-ghost btn-sm" onClick={() => (detailQ.refresh(), sessionsQ.refresh())}>
                <IconRefresh width={13} height={13} /> Refresh
              </button>
            </div>
          </div>
        </div>
      )}

      <Modal open={Boolean(editing)} onClose={() => setEditing(null)} title="Edit live clip" wide>
        {editing && project ? (
          <ClipEditor
            clip={editing}
            project={project}
            onUpdated={() => {
              detailQ.refresh();
              projectQ.refresh();
            }}
            onClose={() => setEditing(null)}
          />
        ) : (
          <p className="text-xs text-mist-400">Loading project…</p>
        )}
      </Modal>
    </div>
  );
}
