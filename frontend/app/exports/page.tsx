'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import { IconDownload, IconFilm } from '@/components/Icons';
import { EmptyState, SectionHeading, Spinner } from '@/components/ui';
import { api } from '@/lib/api';
import { MODE_LABEL, timecode } from '@/lib/format';
import { usePolling } from '@/lib/hooks';
import type { Clip } from '@/lib/types';

export default function ExportsPage() {
  const clipsQ = usePolling(() => api.allClips('done'), 10000);
  const [filter, setFilter] = useState('');
  const clips: Clip[] = clipsQ.data?.clips || [];

  const groups = useMemo(() => {
    const map = new Map<string, { project_id: string; name: string; mode?: string; clips: Clip[] }>();
    clips
      .filter((c) => !filter || c.title.toLowerCase().includes(filter.toLowerCase()))
      .forEach((c) => {
        const key = c.project_id;
        if (!map.has(key)) {
          map.set(key, { project_id: key, name: c.project_name || 'Project', mode: c.project_mode, clips: [] });
        }
        map.get(key)!.clips.push(c);
      });
    return Array.from(map.values());
  }, [clips, filter]);

  return (
    <div className="space-y-5">
      <SectionHeading
        title="Exported clips"
        subtitle={`${clips.length} rendered files · no watermarks`}
        action={
          <input
            className="input max-w-[220px]"
            placeholder="Filter by title…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        }
      />

      {clipsQ.loading ? (
        <div className="flex items-center gap-2 text-sm text-mist-400">
          <Spinner size={15} /> Loading exports…
        </div>
      ) : groups.length === 0 ? (
        <EmptyState
          icon={<IconDownload width={24} height={24} />}
          title="Nothing exported yet"
          description="Rendered clips show up here. They are also stored in data/projects/<id>/clips."
          action={
            <Link href="/new" className="btn btn-primary btn-sm">
              Create a project
            </Link>
          }
        />
      ) : (
        <div className="space-y-7">
          {groups.map((group) => (
            <section key={group.project_id}>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <Link href={`/project/?id=${group.project_id}`} className="truncate text-sm font-semibold text-mist-100 hover:text-white">
                    {group.name}
                  </Link>
                  <p className="text-[11px] text-mist-500">
                    {MODE_LABEL[group.mode || 'video']} · {group.clips.length} clip
                    {group.clips.length === 1 ? '' : 's'}
                  </p>
                </div>
                <a className="btn btn-secondary btn-sm" href={`/api/projects/${group.project_id}/download-all`} download>
                  <IconDownload width={13} height={13} /> Download all (zip)
                </a>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {group.clips.map((clip) => (
                  <div key={clip.id} className="card overflow-hidden">
                    <div className="relative aspect-[9/12] bg-ink-900">
                      {clip.thumb_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={clip.thumb_url} alt="" className="h-full w-full object-cover" />
                      ) : (
                        <div className="flex h-full items-center justify-center text-mist-600">
                          <IconFilm width={18} height={18} />
                        </div>
                      )}
                      <span className="absolute bottom-2 left-2 rounded bg-black/70 px-1.5 py-0.5 font-mono text-[10px] text-white">
                        {timecode(clip.duration || 0)}
                      </span>
                    </div>
                    <div className="p-3">
                      <p className="line-clamp-2 min-h-[32px] text-[12px] font-medium leading-snug text-mist-100">
                        {clip.title}
                      </p>
                      <div className="mt-2 flex gap-2">
                        <a className="btn btn-secondary btn-sm flex-1" href={clip.download_url || '#'} download>
                          <IconDownload width={12} height={12} /> MP4
                        </a>
                        <Link href={`/project/?id=${clip.project_id}`} className="btn btn-ghost btn-sm">
                          Open
                        </Link>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
