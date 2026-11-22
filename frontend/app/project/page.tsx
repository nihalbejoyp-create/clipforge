'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState, memo } from 'react';
import ClipEditor from '@/components/ClipEditor';
import {
  IconAlert,
  IconArrowLeft,
  IconDownload,
  IconFilm,
  IconPlay,
  IconRefresh,
  IconScissors,
  IconSliders,
  IconTrash,
  IconYouTube,
} from '@/components/Icons';
import { EmptyState, Field, Modal, ProgressBar, SectionHeading, Spinner, StatusBadge } from '@/components/ui';
import SettingsPanel from '@/components/SettingsPanel';
import YouTubeBulk from '@/components/YouTubeBulk';
import { api } from '@/lib/api';
import { MODE_LABEL, relativeTime, resolutionLabel, secondsLabel, timecode } from '@/lib/format';
import { usePolling, useQueryParam } from '@/lib/hooks';
import { applyPatch, FALLBACK_SETTINGS } from '@/lib/settings';
import { toast as notify } from '@/lib/toast';
import type { Clip, Project, Settings } from '@/lib/types';

// A deleted project (or an unknown id) stays gone; stop polling it.
const stopWhenGone = (err: any) => err?.status === 404;

const ClipCard = memo(
  function ClipCard({
    clip,
    onSelect,
  }: {
    clip: Clip;
    onSelect: (c: Clip) => void;
  }) {
  return (
    <button
      onClick={() => onSelect(clip)}
      className="card card-hover group overflow-hidden text-left"
    >
      <div className="relative aspect-[9/12] w-full overflow-hidden bg-ink-900">
        {clip.thumb_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={clip.thumb_url} alt="" className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full items-center justify-center text-mist-600">
            <IconFilm width={20} height={20} />
          </div>
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-black/25" />
        <span className="absolute left-2 top-2 rounded-md bg-black/65 px-1.5 py-0.5 font-mono text-[10px] text-white">
          {String(clip.idx + 1).padStart(2, '0')}
        </span>
        {clip.score ? (
          <span className="absolute right-2 top-2 rounded-md bg-black/65 px-1.5 py-0.5 font-mono text-[10px] text-accent-soft">
            {Math.round(clip.score)}
          </span>
        ) : null}
        <span className="absolute bottom-2 left-2 rounded bg-black/70 px-1.5 py-0.5 font-mono text-[10px] text-white">
          {timecode(clip.duration || clip.end - clip.start)}
        </span>
        {clip.status === 'done' ? (
          <span className="absolute bottom-2 right-2 flex h-7 w-7 items-center justify-center rounded-full bg-white/90 text-black opacity-0 transition group-hover:opacity-100">
            <IconPlay width={12} height={12} />
          </span>
        ) : null}
        {clip.status === 'rendering' || clip.status === 'pending' ? (
          <div className="absolute inset-x-2 bottom-2">
            <ProgressBar value={clip.status === 'pending' ? 4 : clip.progress} />
          </div>
        ) : null}
        {clip.status === 'failed' ? (
          <span className="absolute inset-x-2 bottom-2 rounded bg-red-500/85 px-1.5 py-0.5 text-center text-[10px] text-white">
            render failed
          </span>
        ) : null}
      </div>
      <div className="p-3">
        <p className="line-clamp-2 min-h-[32px] text-[12.5px] font-medium leading-snug text-mist-100">
          {clip.title}
        </p>
        <div className="mt-1.5 flex items-center justify-between gap-2">
          <StatusBadge status={clip.status} />
          <span className="flex items-center gap-1.5">
            {clip.youtube_id ? (
              <span className="flex items-center gap-0.5 text-[10px] text-red-300" title="Uploaded to YouTube">
                <IconYouTube width={11} height={11} />
              </span>
            ) : null}
            <span className="text-[10px] text-mist-500">{clip.start ? timecode(clip.start) : '0:00'}</span>
          </span>
        </div>
      </div>
    </button>
  );
},
(prev, next) =>
  prev.clip.id === next.clip.id &&
  prev.clip.updated_at === next.clip.updated_at &&
  prev.clip.progress === next.clip.progress &&
  prev.clip.status === next.clip.status &&
  prev.clip.youtube_id === next.clip.youtube_id &&
  prev.clip.thumb_url === next.clip.thumb_url,
);

export default function ProjectPage() {
  const id = useQueryParam('id');
  const router = useRouter();
  const [selected, setSelected] = useState<Clip | null>(null);
  const [reanalyzeOpen, setReanalyzeOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [analyzeSettings, setAnalyzeSettings] = useState<Settings>(FALLBACK_SETTINGS);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);

  const projectQ = usePolling(
    () => (id ? api.project(id) : Promise.reject(new Error('missing project id'))),
    4000,
    [id],
    { stopWhen: stopWhenGone },
  );
  const clipsQ = usePolling(
    () => (id ? api.clips(id) : Promise.reject(new Error('missing project id'))),
    3000,
    [id],
    { stopWhen: stopWhenGone },
  );

  const project: Project | null = projectQ.data?.project || null;
  const jobs = projectQ.data?.jobs || [];
  const clips: Clip[] = clipsQ.data?.clips || [];

  const analyzeJob = jobs.find((j) => j.kind === 'analyze');
  const ingestJob = jobs.find((j) => j.kind === 'ingest');
  const renderingCount = clips.filter((c) => c.status === 'rendering' || c.status === 'pending').length;
  const busy = useMemo(() => {
    if (!project) return false;
    if (['ingesting', 'analyzing', 'rendering'].includes(project.status)) return true;
    return renderingCount > 0;
  }, [project, renderingCount]);

  useEffect(() => {
    if (project && reanalyzeOpen) setAnalyzeSettings(project.settings || FALLBACK_SETTINGS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id]);

  useEffect(() => {
    if (!selected) return;
    const fresh = clips.find((c) => c.id === selected.id);
    if (fresh && fresh.updated_at !== selected.updated_at) setSelected(fresh);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipsQ.data]);

  const runAnalyze = async (settings: Settings) => {
    if (!project) return;
    setStarting(true);
    try {
      await api.analyze(project.id, settings);
      setReanalyzeOpen(false);
      notify('Analysis started', 'success');
      projectQ.refresh();
      clipsQ.refresh();
    } catch (err: any) {
      notify(err.message || 'Could not start analysis', 'error');
    } finally {
      setStarting(false);
    }
  };

  const exportAll = async () => {
    if (!project) return;
    try {
      const res = await api.exportAll(project.id);
      notify(res.queued ? `Queued ${res.queued} clips for rendering` : 'All clips are already rendered', 'success');
      clipsQ.refresh();
    } catch (err: any) {
      notify(err.message, 'error');
    }
  };

  const remove = async () => {
    if (!project) return;
    if (!window.confirm(`Delete “${project.name}”? Rendered clips and the working copy are removed. Original uploads already moved into the project folder are deleted too.`)) return;
    try {
      await api.deleteProject(project.id, true);
      notify('Project deleted');
      router.push('/projects');
    } catch (err: any) {
      notify(err.message, 'error');
    }
  };

  if (!id) {
    return <EmptyState title="No project selected" description="Open a project from the projects list." />;
  }
  if (projectQ.error) {
    return (
      <EmptyState
        icon={<IconAlert width={24} height={24} />}
        title="Project not available"
        description={projectQ.error}
        action={
          <Link href="/projects" className="btn btn-secondary btn-sm">
            Back to projects
          </Link>
        }
      />
    );
  }
  if (!project) {
    return (
      <div className="flex items-center gap-3 py-20 text-sm text-mist-400">
        <Spinner size={16} /> Loading project…
      </div>
    );
  }

  const activeJob = <T extends { status: string }>(job?: T | null): T | null =>
    job && (job.status === 'running' || job.status === 'queued') ? job : null;
  const progressJob = activeJob(analyzeJob) || activeJob(ingestJob) || null;
  const analysisRunning = project.status === 'analyzing' || project.status === 'ingesting';

  const stopJob = async () => {
    if (!progressJob) return;
    setStopping(true);
    try {
      await api.cancelJob(progressJob.id);
      notify('Stopped');
      projectQ.refresh();
      clipsQ.refresh();
    } catch (err: any) {
      notify(err.message || 'Could not stop the job', 'error');
    } finally {
      setStopping(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <Link href="/projects" className="btn btn-ghost btn-sm !px-2">
            <IconArrowLeft width={16} height={16} />
          </Link>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-xl font-semibold tracking-tight">{project.name}</h1>
              <StatusBadge status={project.status} />
            </div>
            <p className="mt-1 text-xs text-mist-500">
              {MODE_LABEL[project.mode]} · {secondsLabel(project.duration)} · {project.width}×{project.height} ·{' '}
              {Math.round(project.fps)}fps · {project.has_audio ? 'audio' : 'no audio'} · {relativeTime(project.created_at)}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn btn-secondary btn-sm" onClick={() => setReanalyzeOpen(true)} disabled={analysisRunning}>
            <IconSliders width={14} height={14} /> Re-analyse
          </button>
          <button className="btn btn-secondary btn-sm" onClick={exportAll} disabled={!clips.length}>
            <IconRefresh width={14} height={14} /> Render all
          </button>
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => setBulkOpen(true)}
            disabled={!clips.some((clip) => clip.status === 'done')}
          >
            <IconYouTube width={14} height={14} /> Upload all
          </button>
          <a
            className={`btn btn-secondary btn-sm ${project.done_count ? '' : 'pointer-events-none opacity-40'}`}
            href={`/api/projects/${project.id}/download-all`}
            download
          >
            <IconDownload width={14} height={14} /> Download all
          </a>
          <button className="btn btn-danger btn-sm" onClick={remove}>
            <IconTrash width={14} height={14} />
          </button>
        </div>
      </div>

      {analysisRunning || progressJob ? (
        <div className="card p-4">
          <div className="mb-2 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-xs text-mist-200">
              <Spinner size={13} className="text-accent-soft" />
              {progressJob?.message || (project.status === 'ingesting' ? 'Preparing source…' : 'Analysing moments…')}
            </div>
            <div className="flex items-center gap-3">
              <span className="font-mono text-[11px] text-mist-500">
                {Math.round(progressJob?.progress ?? 0)}%
              </span>
              {progressJob ? (
                <button className="btn btn-ghost btn-sm !py-1" onClick={stopJob} disabled={stopping}>
                  {stopping ? <Spinner size={12} /> : null} Cancel
                </button>
              ) : null}
            </div>
          </div>
          <ProgressBar value={progressJob?.progress ?? 0} />
          <p className="mt-2 text-[11px] text-mist-500">
            Transcription runs locally with Whisper — you can leave this page, it keeps working.
          </p>
        </div>
      ) : null}

      {project.error ? (
        <div className="flex items-start gap-2.5 rounded-xl border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          <IconAlert width={16} height={16} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-medium">Something went wrong</p>
            <p className="mt-0.5 text-xs leading-relaxed opacity-90">{project.error}</p>
          </div>
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="space-y-3">
          <div className="card overflow-hidden">
            <div className="aspect-video w-full bg-ink-900">
              {project.source_url_local ? (
                // preload="none": the source can be a multi-GB 4K file. Loading
                // and decoding it just to show a card made the page crawl.
                <video
                  src={project.source_url_local}
                  poster={project.thumb_url || undefined}
                  preload="none"
                  controls
                  className="h-full w-full"
                />
              ) : project.thumb_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={project.thumb_url} alt="" className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full items-center justify-center text-mist-600">
                  <IconFilm width={22} height={22} />
                </div>
              )}
            </div>
            <div className="space-y-1.5 p-3.5 text-[11.5px] text-mist-400">
              <p className="flex justify-between">
                <span className="text-mist-500">Output</span>
                <span>
                  {project.settings?.layout?.replace('_', ' ')} ·{' '}
                  {resolutionLabel(project.settings?.resolution || 1080)}
                </span>
              </p>
              <p className="flex justify-between">
                <span className="text-mist-500">Clips</span>
                <span>
                  {project.done_count}/{clips.length || project.settings?.clip_count}
                </span>
              </p>
              <p className="flex justify-between">
                <span className="text-mist-500">Subtitles</span>
                <span>{project.settings?.subtitle?.enabled ? project.settings.subtitle.font : 'off'}</span>
              </p>
              <p className="flex justify-between">
                <span className="text-mist-500">Duration</span>
                <span>{project.settings?.clip_duration}s each</span>
              </p>
            </div>
          </div>

          <div className="card p-4">
            <p className="label">Pipeline</p>
            <ul className="space-y-2 text-[11.5px]">
              {[
                ['Source imported', project.duration > 0],
                ['Speech recognised', Boolean(analyzeJob && ['done'].includes(analyzeJob.status))],
                ['Highlights ranked', clips.length > 0],
                ['Clips rendered', project.done_count > 0],
              ].map(([label, done]) => (
                <li key={String(label)} className="flex items-center gap-2 text-mist-400">
                  <span
                    className={`flex h-4 w-4 items-center justify-center rounded-full text-[9px] ${
                      done ? 'bg-emerald-400/20 text-emerald-300' : 'bg-white/[0.06] text-mist-600'
                    }`}
                  >
                    ●
                  </span>
                  {label}
                </li>
              ))}
            </ul>
          </div>
        </aside>

        <section className="min-w-0">
          <SectionHeading
            title={`Clips (${clips.length})`}
            subtitle={
              renderingCount
                ? `${renderingCount} clip${renderingCount === 1 ? '' : 's'} in the render queue`
                : 'Click a clip to edit trim, captions and layout.'
            }
          />
          {clips.length === 0 ? (
            analysisRunning ? (
              <div className="grid gap-3 sm:grid-cols-2">
                {[0, 1, 2, 3].map((i) => (
                  <div key={i} className="card h-[150px] skeleton" />
                ))}
              </div>
            ) : (
              <EmptyState
                icon={<IconScissors width={24} height={24} />}
                title="No clips yet"
                description={
                  project.status === 'failed'
                    ? 'The last run failed. Adjust the settings and try again.'
                    : 'Run the analyser to find the strongest moments in this source.'
                }
                action={
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={() => runAnalyze(project.settings || FALLBACK_SETTINGS)}
                    disabled={starting}
                  >
                    {starting ? <Spinner size={13} /> : <IconScissors width={14} height={14} />}
                    Generate clips
                  </button>
                }
              />
            )
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {clips.map((clip) => (
                <ClipCard key={clip.id} clip={clip} onSelect={setSelected} />
              ))}
            </div>
          )}
        </section>
      </div>

      <Modal open={Boolean(selected)} onClose={() => setSelected(null)} title={selected ? `Edit clip ${selected.idx + 1}` : ''} wide>
        {selected ? (
          <ClipEditor
            clip={selected}
            project={project}
            onUpdated={() => {
              clipsQ.refresh();
              projectQ.refresh();
            }}
            onClose={() => setSelected(null)}
          />
        ) : null}
      </Modal>

      <Modal open={bulkOpen} onClose={() => setBulkOpen(false)} title="Upload clips to YouTube" wide>
        {bulkOpen ? (
          <YouTubeBulk
            clips={clips}
            onClose={() => setBulkOpen(false)}
            onDone={() => {
              clipsQ.refresh();
              projectQ.refresh();
            }}
          />
        ) : null}
      </Modal>

      <Modal open={reanalyzeOpen} onClose={() => setReanalyzeOpen(false)} title="Re-analyse with new settings" wide>
        <div className="space-y-4">
          <p className="text-xs leading-relaxed text-mist-400">
            This replaces the AI-generated clips with a fresh selection using the settings below. Edited clips are
            replaced too.
          </p>
          <SettingsPanel
            value={analyzeSettings}
            onChange={(p) => setAnalyzeSettings((s) => applyPatch(s, p))}
            mode={project.mode}
            sourceHeight={project.height}
            sourceDuration={project.duration}
          />
          <div className="flex justify-end gap-2 pt-2">
            <button className="btn btn-ghost" onClick={() => setReanalyzeOpen(false)}>
              Cancel
            </button>
            <button className="btn btn-primary" onClick={() => runAnalyze(analyzeSettings)} disabled={starting}>
              {starting ? <Spinner size={14} /> : <IconScissors width={15} height={15} />}
              Run analysis
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
