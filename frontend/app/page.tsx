'use client';

import Link from 'next/link';
import { useState } from 'react';
import { api } from '@/lib/api';
import { MODE_LABEL, relativeTime, secondsLabel } from '@/lib/format';
import { usePolling, useSystem } from '@/lib/hooks';
import { IconChevronRight, IconDownload, IconFilm, IconLive, IconPlus, IconSparkles, IconUpload } from '@/components/Icons';
import { EmptyState, ProgressBar, SectionHeading, Spinner, StatusBadge } from '@/components/ui';

const MODES = [
  {
    id: 'video',
    title: 'Normal video clipping',
    description: 'Drop a long video or paste a YouTube link. Whisper transcribes it, the analyser finds the strongest moments and exports ready-to-post shorts.',
    bullets: ['MP4 · MOV · MKV · WebM', 'YouTube & 1000+ sites', 'Full clip editor'],
    icon: IconFilm,
    gradient: 'from-accent/25 to-accent/0',
    ring: 'group-hover:border-accent/40',
  },
  {
    id: 'stream',
    title: 'Recorded stream clipping',
    description: 'Turn a VOD into highlights with facecam + gameplay layouts, smart face reframing and the same subtitle engine.',
    bullets: ['Facecam & gameplay layouts', 'Auto facecam detection', 'React picks & energy spikes'],
    icon: IconUpload,
    gradient: 'from-accent-purple/25 to-accent-purple/0',
    ring: 'group-hover:border-accent-purple/40',
  },
  {
    id: 'live',
    title: 'Live stream clipping',
    description: 'Connect a running stream. ClipForge analyses the inbound feed and cuts clips while the broadcast is still live.',
    bullets: ['YouTube · Twitch · HLS · RTMP', 'Rolling buffer, auto reconnect', 'Clips appear live'],
    icon: IconLive,
    gradient: 'from-emerald-400/20 to-emerald-400/0',
    ring: 'group-hover:border-emerald-400/40',
    live: true,
  },
];

export default function DashboardPage() {
  const system = useSystem();
  const projects = usePolling(() => api.projects(), 8000);
  const jobs = usePolling(() => api.jobs(), 3000);
  const [clipsData, setClips] = useState<{ clips: any[] } | null>(null);
  usePolling(async () => {
    const res = await api.allClips('done');
    setClips(res);
    return res;
  }, 15000);

  const list = projects.data?.projects || [];
  const activeJobs = (jobs.data?.jobs || []).filter((j) => j.status === 'running' || j.status === 'queued');
  const rendered = clipsData?.clips?.length ?? 0;
  const totalClips = list.reduce((acc, p) => acc + (p.clip_count || 0), 0);

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <span className="badge badge-blue">
              <IconSparkles width={11} height={11} /> Local AI · no API keys
            </span>
            {system ? <span className="badge badge-neutral">{system.ffmpeg.videotoolbox ? 'VideoToolbox' : 'CPU encode'}</span> : null}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">Clipping studio</h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-mist-400">
            Find the moments worth posting, cut them automatically and export clean, watermark-free shorts.
          </p>
        </div>
        <Link href="/new" className="btn btn-primary">
          <IconPlus width={16} height={16} />
          New project
        </Link>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        {MODES.map((mode) => {
          const Icon = mode.icon;
          const href = mode.live ? '/live' : `/new?mode=${mode.id}`;
          return (
            <Link
              key={mode.id}
              href={href}
              className={`card card-hover group relative overflow-hidden p-5 ${mode.ring}`}
            >
              <div className={`pointer-events-none absolute inset-x-0 -top-24 h-48 bg-gradient-to-b ${mode.gradient} opacity-70 blur-2xl`} />
              <div className="relative">
                <span className="mb-4 flex h-9 w-9 items-center justify-center rounded-xl border border-white/10 bg-white/[0.05] text-mist-100">
                  <Icon width={18} height={18} />
                </span>
                <h2 className="text-[14.5px] font-semibold leading-snug">{mode.title}</h2>
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-mist-400">{mode.description}</p>
                <ul className="mt-3 space-y-1">
                  {mode.bullets.map((b) => (
                    <li key={b} className="flex items-center gap-1.5 text-[11.5px] text-mist-500">
                      <span className="h-1 w-1 rounded-full bg-accent/70" />
                      {b}
                    </li>
                  ))}
                </ul>
                <span className="mt-4 inline-flex items-center gap-1 text-[12px] font-semibold text-accent-soft">
                  {mode.live ? 'Connect a stream' : 'Start clipping'}
                  <IconChevronRight width={13} height={13} className="transition group-hover:translate-x-0.5" />
                </span>
              </div>
            </Link>
          );
        })}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { label: 'Projects', value: String(list.length), hint: `${totalClips} clips planned` },
          { label: 'Exported clips', value: String(rendered), hint: 'ready to download' },
          { label: 'Rendering queue', value: String(activeJobs.length), hint: activeJobs.length ? 'jobs in flight' : 'idle' },
          { label: 'Free disk', value: `${system?.disk_free_gb ?? '—'} GB`, hint: system ? `${system.fonts.length} fonts installed` : 'checking' },
        ].map((stat) => (
          <div key={stat.label} className="card p-4">
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-mist-500">{stat.label}</p>
            <p className="mt-1 text-xl font-semibold tracking-tight">{stat.value}</p>
            <p className="mt-0.5 text-[11px] text-mist-500">{stat.hint}</p>
          </div>
        ))}
      </div>

      {activeJobs.length > 0 ? (
        <section className="card p-5">
          <SectionHeading title="Rendering queue" subtitle="Clips render in the background — you can keep working." />
          <div className="space-y-3">
            {activeJobs.slice(0, 4).map((job) => (
              <div key={job.id} className="flex items-center gap-3">
                <Spinner size={14} className="text-accent-soft" />
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex items-center justify-between gap-3">
                    <p className="truncate text-xs font-medium text-mist-200">
                      {job.kind} · {job.message || job.status}
                    </p>
                    <span className="shrink-0 font-mono text-[10.5px] text-mist-500">{Math.round(job.progress)}%</span>
                  </div>
                  <ProgressBar value={job.progress} />
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section>
        <SectionHeading
          title="Recent projects"
          subtitle="Everything stays on this Mac."
          action={
            list.length > 0 ? (
              <Link href="/projects" className="btn btn-ghost btn-sm">
                View all <IconChevronRight width={13} height={13} />
              </Link>
            ) : null
          }
        />
        {projects.loading ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="card h-[132px] skeleton" />
            ))}
          </div>
        ) : list.length === 0 ? (
          <EmptyState
            icon={<IconFilm width={26} height={26} />}
            title="No projects yet"
            description="Start with a long video, a recorded stream, or connect a livestream that is happening right now."
            action={
              <Link href="/new" className="btn btn-primary btn-sm">
                <IconPlus width={14} height={14} /> Create your first project
              </Link>
            }
          />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {list.slice(0, 6).map((project) => (
              <Link
                key={project.id}
                href={`/project/?id=${project.id}`}
                className="card card-hover group flex gap-3 overflow-hidden p-3"
              >
                <div className="relative h-[76px] w-[112px] shrink-0 overflow-hidden rounded-lg bg-ink-900">
                  {project.thumb_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={project.thumb_url} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full w-full items-center justify-center text-mist-600">
                      <IconFilm width={20} height={20} />
                    </div>
                  )}
                  <span className="absolute bottom-1 right-1 rounded bg-black/75 px-1 py-0.5 font-mono text-[9.5px] text-white">
                    {secondsLabel(project.duration || 0)}
                  </span>
                </div>
                <div className="min-w-0 flex-1 py-0.5">
                  <p className="truncate text-[13px] font-semibold text-mist-100">{project.name}</p>
                  <p className="mt-0.5 text-[11px] text-mist-500">
                    {MODE_LABEL[project.mode]} · {relativeTime(project.created_at)}
                  </p>
                  <div className="mt-2 flex items-center gap-1.5">
                    <StatusBadge status={project.status} />
                    <span className="text-[10.5px] text-mist-500">
                      {project.done_count}/{project.clip_count} clips
                    </span>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="card flex flex-wrap items-center justify-between gap-4 p-5">
        <div>
          <h3 className="text-sm font-semibold text-mist-100">Your engine</h3>
          <p className="mt-1 text-xs leading-relaxed text-mist-400">
            {system
              ? `${system.machine.chip} · ${system.machine.cores} cores · ${system.ffmpeg.version || 'ffmpeg'} · subtitle burn-in ${system.ffmpeg.libass ? 'enabled' : 'unavailable'} · Whisper ${system.whisper.available ? 'ready' : 'missing'}`
              : 'Reading hardware…'}
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/exports" className="btn btn-secondary btn-sm">
            <IconDownload width={14} height={14} /> Exports
          </Link>
          <Link href="/settings" className="btn btn-ghost btn-sm">
            System details
          </Link>
        </div>
      </section>
    </div>
  );
}
