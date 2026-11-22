'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import { IconFilm, IconPlus, IconTrash } from '@/components/Icons';
import { EmptyState, SectionHeading, Spinner, StatusBadge } from '@/components/ui';
import { api } from '@/lib/api';
import { MODE_LABEL, relativeTime, secondsLabel } from '@/lib/format';
import { usePolling } from '@/lib/hooks';
import { toast as notify } from '@/lib/toast';

export default function ProjectsPage() {
  const projectsQ = usePolling(() => api.projects(), 7000);
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'all' | 'video' | 'stream' | 'live'>('all');
  const list = projectsQ.data?.projects || [];

  const filtered = useMemo(
    () =>
      list.filter(
        (p) =>
          (mode === 'all' || p.mode === mode) &&
          (!query || p.name.toLowerCase().includes(query.toLowerCase())),
      ),
    [list, mode, query],
  );

  const remove = async (id: string, name: string) => {
    if (!window.confirm(`Delete “${name}” and its rendered clips?`)) return;
    try {
      await api.deleteProject(id, true);
      notify('Project deleted');
      projectsQ.refresh();
    } catch (err: any) {
      notify(err.message, 'error');
    }
  };

  return (
    <div className="space-y-5">
      <SectionHeading
        title="Projects"
        subtitle={`${list.length} total · everything is stored under data/projects`}
        action={
          <Link href="/new" className="btn btn-primary btn-sm">
            <IconPlus width={14} height={14} /> New project
          </Link>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <input
          className="input max-w-xs"
          placeholder="Search projects…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="seg">
          {(['all', 'video', 'stream', 'live'] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)} className={`seg-item ${mode === m ? 'seg-item-active' : ''}`}>
              {m === 'all' ? 'All' : m === 'video' ? 'Video' : m === 'stream' ? 'Recorded stream' : 'Live'}
            </button>
          ))}
        </div>
        {projectsQ.loading ? <Spinner size={14} className="text-mist-500" /> : null}
      </div>

      {projectsQ.error ? (
        <div className="card p-4 text-sm text-red-300">{projectsQ.error}</div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<IconFilm width={24} height={24} />}
          title={list.length ? 'No projects match that filter' : 'No projects yet'}
          description={list.length ? 'Try a different search or mode.' : 'Create a project to start clipping.'}
          action={
            <Link href="/new" className="btn btn-primary btn-sm">
              <IconPlus width={14} height={14} /> New project
            </Link>
          }
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((project) => (
            <div key={project.id} className="card card-hover overflow-hidden">
              <Link href={`/project/?id=${project.id}`} className="block">
                <div className="relative aspect-video w-full bg-ink-900">
                  {project.thumb_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={project.thumb_url} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full items-center justify-center text-mist-600">
                      <IconFilm width={22} height={22} />
                    </div>
                  )}
                  <span className="absolute bottom-2 right-2 rounded bg-black/70 px-1.5 py-0.5 font-mono text-[10px] text-white">
                    {secondsLabel(project.duration || 0)}
                  </span>
                  <span className="absolute left-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-mist-200">
                    {MODE_LABEL[project.mode]}
                  </span>
                </div>
              </Link>
              <div className="p-3.5">
                <div className="flex items-start justify-between gap-2">
                  <Link href={`/project/?id=${project.id}`} className="min-w-0">
                    <p className="truncate text-[13px] font-semibold text-mist-100">{project.name}</p>
                    <p className="mt-0.5 text-[11px] text-mist-500">{relativeTime(project.created_at)}</p>
                  </Link>
                  <button
                    onClick={() => remove(project.id, project.name)}
                    className="btn btn-ghost btn-sm !px-1.5 text-mist-500 hover:text-red-300"
                    aria-label="Delete project"
                  >
                    <IconTrash width={14} height={14} />
                  </button>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <StatusBadge status={project.status} />
                  <span className="text-[10.5px] text-mist-500">
                    {project.done_count}/{project.clip_count} clips rendered
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
