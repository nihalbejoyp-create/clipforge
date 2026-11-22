'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { timecode } from '@/lib/format';
import { readYouTubePrivacy, rememberYouTubePrivacy, type YouTubePrivacy } from '@/lib/settings';
import { toast as notify } from '@/lib/toast';
import type { Clip, Job, YouTubeDraft, YouTubeStatus } from '@/lib/types';
import { IconAlert, IconCheck, IconExternal, IconFilm, IconYouTube } from './Icons';
import { ProgressBar, Segmented, Spinner } from './ui';

const PRIVACY: { value: YouTubePrivacy; label: string }[] = [
  { value: 'private', label: 'Private' },
  { value: 'unlisted', label: 'Unlisted' },
  { value: 'public', label: 'Public' },
];

type Row = YouTubeDraft & { checked: boolean; expanded: boolean; tagsText: string };
type Started = { clip_id: string; job_id: string };

const isBusy = (job?: Job | null) => !job || job.status === 'queued' || job.status === 'running';

export default function YouTubeBulk({
  clips,
  onDone,
  onClose,
}: {
  clips: Clip[];
  onDone: () => void;
  onClose: () => void;
}) {
  const rendered = useMemo(() => clips.filter((clip) => clip.status === 'done'), [clips]);
  const loadKey = rendered.map((clip) => clip.id).join(',');

  const [rows, setRows] = useState<Row[] | null>(null);
  const [status, setStatus] = useState<YouTubeStatus | null>(null);
  const [account, setAccount] = useState('');
  const [privacy, setPrivacy] = useState<YouTubePrivacy>('private');
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const [jobs, setJobs] = useState<Started[]>([]);
  const [progress, setProgress] = useState<Record<string, Job>>({});

  const loaded = useRef(false);
  const reported = useRef(false);
  // Clips posted before multi-channel support have no channel recorded; they
  // belong to whichever channel was connected at that time.
  const loadAccount = useRef('');

  useEffect(() => {
    const saved = readYouTubePrivacy();
    if (saved) setPrivacy(saved);
  }, []);

  // The project page polls every few seconds, so `clips` is a fresh array each
  // time; load the drafts once so in-progress edits are never thrown away.
  useEffect(() => {
    if (loaded.current || !loadKey) return;
    loaded.current = true;
    let alive = true;
    Promise.all([api.youtube.status(), api.youtube.prepare(loadKey.split(','))])
      .then(([state, draft]) => {
        if (!alive) return;
        setStatus(state);
        setAccount(state.active_account || state.accounts[0]?.id || '');
        loadAccount.current = state.active_account || state.accounts[0]?.id || '';
        setRows(
          draft.items.map((item) => ({
            ...item,
            // Everything not already posted starts ticked: this is the "upload
            // all" path, and unticking is how you pick a shorter list.
            checked: item.rendered && !item.youtube_url,
            expanded: false,
            tagsText: item.tags.join(', '),
          })),
        );
      })
      .catch((err) => {
        if (alive) setError(err?.message || 'Could not prepare the uploads.');
      });
    return () => {
      alive = false;
    };
  }, [loadKey]);

  const jobsKey = jobs.map((job) => job.job_id).join(',');

  useEffect(() => {
    if (!jobs.length) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      const fresh = await Promise.all(
        jobs.map(async (entry) => {
          try {
            return (await api.job(entry.job_id)).job;
          } catch {
            return null;
          }
        }),
      );
      if (!alive) return;
      const next: Record<string, Job> = {};
      fresh.forEach((job, index) => {
        if (job) next[jobs[index].clip_id] = job;
      });
      setProgress((prev) => ({ ...prev, ...next }));
      onDone();

      if (jobs.some((entry) => !next[entry.clip_id] || isBusy(next[entry.clip_id]))) {
        timer = setTimeout(tick, 2000);
        return;
      }
      if (reported.current) return;
      reported.current = true;
      const done = jobs.filter((entry) => next[entry.clip_id]?.status === 'done').length;
      const failed = jobs.length - done;
      notify(
        failed
          ? `${done} uploaded · ${failed} failed`
          : `${done} clip${done === 1 ? '' : 's'} uploaded to YouTube`,
        failed ? 'error' : 'success',
      );
    };

    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // `jobs` only changes when a batch starts; `jobsKey` is the stable handle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobsKey]);

  const patch = (clipId: string, change: Partial<Row>) =>
    setRows((current) =>
      current ? current.map((row) => (row.clip_id === clipId ? { ...row, ...change } : row)) : current,
    );

  const channelOf = (row: Row) => row.youtube_account || loadAccount.current || '';
  const postedOn = (row: Row, accountId: string) => Boolean(row.youtube_url) && channelOf(row) === accountId;

  // Switching the target channel re-ticks what has not been posted there yet,
  // so posting the same clips to a second channel is two clicks.
  const chooseAccount = (accountId: string) => {
    setAccount(accountId);
    setRows((current) =>
      current
        ? current.map((row) => ({ ...row, checked: row.rendered && !postedOn(row, accountId) }))
        : current,
    );
  };

  const selectAll = (checked: boolean) =>
    setRows((current) =>
      current ? current.map((row) => ({ ...row, checked: checked && row.rendered })) : current,
    );

  const start = async () => {
    const chosen = (rows || []).filter((row) => row.checked && row.rendered);
    if (!chosen.length) return;
    setStarting(true);
    setError('');
    try {
      const res = await api.youtube.uploadMany(
        chosen.map((row) => ({
          clip_id: row.clip_id,
          title: row.title.trim(),
          description: row.description,
          tags: row.tagsText.split(',').map((tag) => tag.trim()).filter(Boolean),
        })),
        privacy,
        account || undefined,
      );
      if (!res.queued) {
        throw new Error(res.skipped[0]?.reason || 'Nothing could be queued.');
      }
      setJobs(res.job_ids.map((jobId, index) => ({ clip_id: chosen[index].clip_id, job_id: jobId })));
      notify(`Queued ${res.queued} upload${res.queued === 1 ? '' : 's'} — one at a time`, 'success');
      if (res.skipped.length) {
        notify(`${res.skipped.length} clip${res.skipped.length === 1 ? '' : 's'} skipped`, 'error');
      }
    } catch (err: any) {
      setError(err?.message || 'Could not start the uploads.');
    } finally {
      setStarting(false);
    }
  };

  if (jobs.length) {
    const finished = jobs.filter((entry) => {
      const job = progress[entry.clip_id];
      return job && job.status !== 'queued' && job.status !== 'running';
    }).length;
    const posted = jobs.filter((entry) => progress[entry.clip_id]?.status === 'done').length;
    const failed = jobs.filter((entry) => progress[entry.clip_id]?.status === 'failed').length;
    return (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium text-mist-100">
            {failed
              ? `${posted} posted · ${failed} failed${finished === jobs.length ? '' : ` · ${jobs.length - finished} left`}`
              : `${finished} of ${jobs.length} finished`}
          </p>
          <p className="text-[11px] text-mist-500">
            Uploads run one at a time and keep going if you close this.
          </p>
        </div>

        <div className="max-h-[46vh] space-y-2 overflow-y-auto pr-1">
          {jobs.map((entry) => {
            const row = (rows || []).find((item) => item.clip_id === entry.clip_id);
            const job = progress[entry.clip_id];
            const url = (job?.payload as { url?: string } | undefined)?.url || row?.youtube_url || null;
            return (
              <div key={entry.clip_id} className="flex items-start gap-2.5 rounded-xl border border-white/[0.07] bg-ink-900/40 p-2.5">
                <div className="h-14 w-9 shrink-0 overflow-hidden rounded-md bg-ink-900">
                  {row?.thumb_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={row.thumb_url} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full items-center justify-center text-mist-600">
                      <IconFilm width={14} height={14} />
                    </div>
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="line-clamp-1 text-[12.5px] font-medium text-mist-100">{row?.title}</p>
                  {job?.status === 'failed' ? (
                    <p className="mt-0.5 flex items-start gap-1 text-[11px] leading-relaxed text-red-300">
                      <IconAlert width={11} height={11} className="mt-0.5 shrink-0" /> {job.error || 'Upload failed'}
                    </p>
                  ) : url && job?.status === 'done' ? (
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-emerald-300 underline"
                    >
                      {url.replace('https://', '')} <IconExternal width={10} height={10} />
                    </a>
                  ) : (
                    <div className="mt-1.5 space-y-1">
                      <div className="flex items-center justify-between text-[10.5px] text-mist-400">
                        <span className="flex items-center gap-1.5">
                          <Spinner size={10} /> {job?.message || 'queued'}
                        </span>
                        <span className="font-mono text-mist-500">{Math.round(job?.progress ?? 0)}%</span>
                      </div>
                      <ProgressBar value={job?.progress ?? 0} />
                    </div>
                  )}
                </div>
                {job?.status === 'done' ? (
                  <IconCheck width={14} height={14} className="mt-1 shrink-0 text-emerald-300" />
                ) : null}
              </div>
            );
          })}
        </div>

        <div className="flex justify-end">
          <button className="btn btn-secondary btn-sm" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    );
  }

  if (error && !rows) {
    return (
      <div className="space-y-3">
        <p className="flex items-start gap-1.5 text-[12px] leading-relaxed text-red-300">
          <IconAlert width={13} height={13} className="mt-0.5 shrink-0" /> {error}
        </p>
        <div className="flex justify-end">
          <button className="btn btn-ghost btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    );
  }

  if (!rows) {
    return (
      <div className="flex items-center gap-2 py-10 text-sm text-mist-400">
        <Spinner size={14} /> Writing titles, captions and tags…
      </div>
    );
  }

  if (!rows.length) {
    return (
      <div className="space-y-3 py-4 text-center">
        <p className="text-sm text-mist-200">No rendered clips in this project yet.</p>
        <p className="text-xs text-mist-500">Render the clips first — you can only post a finished file.</p>
        <button className="btn btn-secondary btn-sm" onClick={onClose}>
          Close
        </button>
      </div>
    );
  }

  const selected = rows.filter((row) => row.checked).length;
  const connected = Boolean(status?.connected);
  const target = status?.accounts.find((item) => item.id === account) || null;

  return (
    <div className="space-y-3">
      {target || status?.channel ? (
        <div className="flex flex-wrap items-center gap-2.5 rounded-xl border border-white/[0.07] bg-ink-900/40 px-3 py-2">
          {(target?.thumbnail || status?.channel?.thumbnail) ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={target?.thumbnail || status?.channel?.thumbnail || ''}
              alt=""
              className="h-7 w-7 rounded-full bg-ink-800"
            />
          ) : null}
          <div className="min-w-0 flex-1">
            <p className="text-[11px] text-mist-500">Posting to</p>
            <p className="truncate text-[12.5px] font-medium text-mist-100">
              {target?.title || status?.channel?.title}
            </p>
          </div>
          {status && status.accounts.length > 1 ? (
            <select
              className="input w-auto !py-1 text-[11.5px]"
              value={account}
              onChange={(event) => chooseAccount(event.target.value)}
              aria-label="Channel to upload to"
            >
              {status.accounts.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          ) : (
            <span className="badge badge-green">connected</span>
          )}
        </div>
      ) : null}

      {!connected ? (
        <div className="rounded-xl border border-amber-400/25 bg-amber-400/[0.07] px-3 py-2 text-[11.5px] leading-relaxed text-amber-200">
          Connect your YouTube channel first —{' '}
          <Link href="/settings#youtube" className="underline">
            Settings → YouTube channel
          </Link>
          .
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn btn-ghost btn-sm" onClick={() => selectAll(true)}>
            Select all
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => selectAll(false)}>
            Select none
          </button>
          <span className="text-[11px] text-mist-500">
            {selected} of {rows.length} selected
          </span>
        </div>
        <Segmented
          options={PRIVACY}
          value={privacy}
          onChange={(value) => {
            setPrivacy(value);
            rememberYouTubePrivacy(value);
          }}
        />
      </div>

      <div className="max-h-[44vh] space-y-2 overflow-y-auto pr-1">
        {rows.map((row) => (
          <div
            key={row.clip_id}
            className={`rounded-xl border p-2.5 transition ${
              row.checked ? 'border-accent/40 bg-accent/[0.06]' : 'border-white/[0.07] bg-ink-900/40'
            }`}
          >
            <div className="flex items-start gap-2.5">
              <input
                type="checkbox"
                checked={row.checked}
                disabled={!row.rendered}
                onChange={(event) => patch(row.clip_id, { checked: event.target.checked })}
                className="mt-1 h-4 w-4 shrink-0 cursor-pointer accent-accent disabled:cursor-not-allowed"
              />
              <div className="h-14 w-9 shrink-0 overflow-hidden rounded-md bg-ink-900">
                {row.thumb_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={row.thumb_url} alt="" className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full items-center justify-center text-mist-600">
                    <IconFilm width={14} height={14} />
                  </div>
                )}
              </div>
              <div className="min-w-0 flex-1 space-y-1">
                <input
                  className="input !py-1 text-[12px]"
                  value={row.title}
                  maxLength={100}
                  onChange={(event) => patch(row.clip_id, { title: event.target.value })}
                />
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10.5px] text-mist-500">
                  <span className="font-mono">{timecode(row.duration)}</span>
                  <span>· clip {String(row.idx + 1).padStart(2, '0')}</span>
                  {row.youtube_url ? (
                    postedOn(row, account) ? (
                      <span className="text-emerald-300">already on this channel</span>
                    ) : (
                      <span className="text-mist-500">on another channel</span>
                    )
                  ) : null}
                  {!row.rendered ? <span className="text-amber-300">render first</span> : null}
                  <button
                    type="button"
                    onClick={() => patch(row.clip_id, { expanded: !row.expanded })}
                    className="ml-auto text-mist-400 underline transition hover:text-mist-200"
                  >
                    {row.expanded ? 'Hide caption & tags' : 'Caption & tags'}
                  </button>
                </div>
                {row.expanded ? (
                  <div className="space-y-1.5 pt-1">
                    <textarea
                      className="input text-[11.5px] leading-relaxed"
                      rows={4}
                      maxLength={4900}
                      value={row.description}
                      onChange={(event) => patch(row.clip_id, { description: event.target.value })}
                    />
                    <input
                      className="input !py-1 text-[11.5px]"
                      value={row.tagsText}
                      placeholder="tags, comma separated"
                      onChange={(event) => patch(row.clip_id, { tagsText: event.target.value })}
                    />
                    <p className="text-[10px] text-mist-600">
                      Hashtags come from these tags — keep the ones that describe the clip.
                    </p>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ))}
      </div>

      {error ? (
        <p className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-red-300">
          <IconAlert width={12} height={12} className="mt-0.5 shrink-0" /> {error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-3">
        <p className="max-w-md text-[11px] leading-relaxed text-mist-500">
          Titles, captions, tags and hashtags are written from each clip&apos;s own transcript — open{' '}
          <span className="text-mist-400">Caption &amp; tags</span> to change any of them.
        </p>
        <div className="flex items-center gap-2">
          <button className="btn btn-ghost btn-sm" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-primary btn-sm"
            onClick={start}
            disabled={!selected || starting || !connected || !account}
          >
            {starting ? <Spinner size={13} /> : <IconYouTube width={14} height={14} />}
            Upload {selected || ''} clip{selected === 1 ? '' : 's'}
          </button>
        </div>
      </div>
    </div>
  );
}
