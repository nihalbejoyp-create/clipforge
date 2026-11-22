'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { readYouTubePrivacy, rememberYouTubePrivacy, type YouTubePrivacy } from '@/lib/settings';
import { toast as notify } from '@/lib/toast';
import type { Clip, Job, YouTubeStatus } from '@/lib/types';
import { IconAlert, IconExternal, IconYouTube } from './Icons';
import { Field, ProgressBar, Segmented, Spinner } from './ui';

type Privacy = YouTubePrivacy;

const PRIVACY: { value: Privacy; label: string }[] = [
  { value: 'private', label: 'Private' },
  { value: 'unlisted', label: 'Unlisted' },
  { value: 'public', label: 'Public' },
];

export default function YouTubeUpload({ clip, onDone }: { clip: Clip; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<YouTubeStatus | null>(null);
  const [title, setTitle] = useState(clip.title);
  const [description, setDescription] = useState((clip.text || '').slice(0, 400));
  const [privacy, setPrivacy] = useState<Privacy>('private');
  const [account, setAccount] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const saved = readYouTubePrivacy();
    if (saved) setPrivacy(saved);
  }, []);

  useEffect(() => {
    // The editor is reused when another clip is opened; start clean for it.
    setTitle(clip.title);
    setDescription((clip.text || '').slice(0, 400));
    setJob(null);
    setOpen(false);
    setError('');
  }, [clip.id]);

  useEffect(() => {
    if (!open || status) return;
    api.youtube
      .status()
      .then((next) => {
        setStatus(next);
        setAccount(next.active_account || next.accounts[0]?.id || '');
      })
      .catch((err) => setError(err?.message || 'Cannot reach the ClipForge engine.'));
  }, [open, status]);

  const jobId = job?.id ?? null;
  const running = Boolean(job && (job.status === 'queued' || job.status === 'running'));

  useEffect(() => {
    if (!jobId || !running) return;
    const timer = setInterval(async () => {
      try {
        const { job: fresh } = await api.job(jobId);
        setJob(fresh);
        if (fresh.status === 'done') {
          notify('Uploaded to YouTube', 'success');
          onDone();
        } else if (fresh.status === 'failed') {
          notify(fresh.error || 'The upload failed', 'error');
        }
      } catch {
        // A single missed poll is harmless; the next one catches up.
      }
    }, 1200);
    return () => clearInterval(timer);
  }, [jobId, running, onDone]);

  const start = async () => {
    setStarting(true);
    setError('');
    try {
      const res = await api.uploadToYouTube(clip.id, {
        title: title.trim() || clip.title,
        description,
        privacy,
        account: account || undefined,
      });
      setJob(res.job);
      notify('Upload started', 'success');
    } catch (err: any) {
      setError(err?.message || 'Could not start the upload');
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!job) return;
    try {
      await api.cancelJob(job.id);
      notify('Upload canceled');
    } catch (err: any) {
      notify(err?.message || 'Could not cancel the upload', 'error');
    }
  };

  const publishedUrl = job?.payload?.url || clip.youtube_url || null;
  const rendered = clip.status === 'done';

  return (
    <div className="space-y-2">
      <button
        className={`btn btn-secondary btn-sm w-full ${rendered ? '' : 'pointer-events-none opacity-40'}`}
        onClick={() => setOpen((v) => !v)}
        disabled={!rendered}
      >
        <IconYouTube width={14} height={14} />
        {clip.youtube_url ? 'Upload again to YouTube' : 'Upload to YouTube'}
      </button>

      {clip.youtube_url && !open ? (
        <a
          className="flex items-center justify-center gap-1 text-[11px] text-emerald-300 hover:underline"
          href={clip.youtube_url}
          target="_blank"
          rel="noreferrer"
        >
          On YouTube <IconExternal width={11} height={11} />
        </a>
      ) : null}

      {open ? (
        <div className="space-y-3 rounded-xl border border-white/[0.08] bg-ink-900/50 p-3">
          {error ? (
            <p className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-red-300">
              <IconAlert width={12} height={12} className="mt-0.5 shrink-0" /> {error}
            </p>
          ) : null}

          {status && !status.connected ? (
            <div className="space-y-2 text-[11.5px] leading-relaxed text-mist-400">
              <p>Connect your channel first — it takes one click once the Google client is saved.</p>
              <Link href="/settings#youtube" className="text-accent-soft underline">
                Open Settings → YouTube channel
              </Link>
            </div>
          ) : null}

          {job?.status === 'failed' && job.error ? (
            <div className="space-y-1 rounded-lg border border-red-400/25 bg-red-500/[0.08] px-2.5 py-2 text-[11.5px] leading-relaxed text-red-200">
              <p className="flex items-start gap-1.5 font-medium">
                <IconAlert width={12} height={12} className="mt-0.5 shrink-0" /> Upload failed
              </p>
              <p className="text-red-200/85">{job.error}</p>
            </div>
          ) : null}

          {publishedUrl ? (
            <div className="rounded-lg border border-emerald-400/25 bg-emerald-400/[0.07] px-2.5 py-2 text-[11.5px] text-emerald-200">
              <p className="font-medium">Uploaded</p>
              <a className="mt-0.5 inline-flex items-center gap-1 underline" href={publishedUrl} target="_blank" rel="noreferrer">
                {publishedUrl.replace('https://', '')} <IconExternal width={11} height={11} />
              </a>
              <p className="mt-1 text-[10.5px] text-emerald-200/70">
                New uploads land as {privacy} — change visibility any time in YouTube Studio.
              </p>
            </div>
          ) : null}

          {running && job ? (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-[11px] text-mist-300">
                <span className="flex items-center gap-1.5">
                  <Spinner size={11} /> {job.message || 'uploading'}
                </span>
                <span className="font-mono text-mist-500">{Math.round(job.progress)}%</span>
              </div>
              <ProgressBar value={job.progress} />
              <button className="btn btn-ghost btn-sm !py-0.5" onClick={cancel}>
                Cancel upload
              </button>
            </div>
          ) : null}

          {status?.connected && !running ? (
            <div className="space-y-2.5">
              <Field label="Title">
                <input
                  className="input !py-1.5 text-[12px]"
                  value={title}
                  maxLength={100}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </Field>
              <Field label="Description" hint="Appears under the video. Shorts also use the title.">
                <textarea
                  className="input !py-1.5 text-[12px]"
                  rows={3}
                  value={description}
                  maxLength={5000}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </Field>
              {status.accounts.length > 1 ? (
                <Field label="Channel" hint="Any channel connected in Settings can receive this clip.">
                  <select
                    className="input !py-1.5 text-[12px]"
                    value={account}
                    onChange={(e) => setAccount(e.target.value)}
                  >
                    {status.accounts.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.title}
                      </option>
                    ))}
                  </select>
                </Field>
              ) : null}
              <Field label="Visibility" hint="Remembered for your next upload.">
                <Segmented
                  options={PRIVACY}
                  value={privacy}
                  onChange={(value) => {
                    setPrivacy(value);
                    rememberYouTubePrivacy(value);
                  }}
                />
              </Field>
              <button className="btn btn-primary btn-sm w-full" onClick={start} disabled={starting}>
                {starting ? <Spinner size={12} /> : <IconYouTube width={13} height={13} />}
                Publish to{' '}
                {status.accounts.find((item) => item.id === account)?.title || status.channel?.title || 'YouTube'}
              </button>
              <p className="text-[10.5px] leading-relaxed text-mist-500">
                The rendered file is streamed from this Mac — nothing is re-downloaded.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
