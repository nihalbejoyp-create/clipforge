'use client';

import { useEffect, useState } from 'react';
import SettingsPanel from '@/components/SettingsPanel';
import YouTubeCard from '@/components/YouTubeCard';
import { IconCheck, IconDisk, IconSettings, IconSparkles } from '@/components/Icons';
import { Field, SectionHeading, Spinner } from '@/components/ui';
import { FALLBACK_SETTINGS, loadDefaults, saveDefaults } from '@/lib/settings';
import { useSystem } from '@/lib/hooks';
import { toast as notify } from '@/lib/toast';
import type { Settings } from '@/lib/types';
import { applyPatch } from '@/lib/settings';

export default function SettingsPage() {
  const system = useSystem();
  const [defaults, setDefaults] = useState<Settings>(FALLBACK_SETTINGS);

  useEffect(() => {
    setDefaults(loadDefaults(system?.defaults));
  }, [system]);

  if (!system) {
    return (
      <div className="flex items-center gap-2 py-20 text-sm text-mist-400">
        <Spinner size={15} /> Reading system…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <SectionHeading title="Settings & system" subtitle="Hardware, engine capabilities and the defaults used for new projects." />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div className="card p-4">
          <p className="label">Hardware</p>
          <p className="text-sm font-medium text-mist-100">{system.machine.chip}</p>
          <p className="mt-1 text-[11.5px] leading-relaxed text-mist-500">
            {system.machine.cores} cores · {system.machine.ram_gb ?? '?'} GB RAM ·{' '}
            {system.machine.apple_silicon ? 'Apple Silicon' : system.machine.arch}
          </p>
        </div>
        <div className="card p-4">
          <p className="label">Video engine</p>
          <p className="text-sm font-medium text-mist-100">
            VideoToolbox {system.ffmpeg.videotoolbox ? 'enabled' : 'unavailable'}
          </p>
          <p className="mt-1 break-all text-[11px] leading-relaxed text-mist-500">{system.ffmpeg.version}</p>
          <p className="mt-1 flex flex-wrap gap-1.5">
            <span className={`badge ${system.ffmpeg.libass ? 'badge-green' : 'badge-red'}`}>
              subtitles {system.ffmpeg.libass ? 'ok' : 'missing'}
            </span>
            <span className={`badge ${system.ffmpeg.encoders.h264_videotoolbox ? 'badge-green' : 'badge-neutral'}`}>
              H.264 HW
            </span>
            <span className={`badge ${system.ffmpeg.encoders.hevc_videotoolbox ? 'badge-green' : 'badge-neutral'}`}>
              H.265 HW
            </span>
          </p>
        </div>
        <div className="card p-4">
          <p className="label">AI</p>
          <p className="text-sm font-medium text-mist-100">
            Whisper {system.whisper.available ? 'ready' : 'unavailable'}
          </p>
          <p className="mt-1 text-[11.5px] text-mist-500">
            Face detection {system.opencv ? 'enabled' : 'unavailable'} · runs fully on-device
          </p>
          <p className="mt-2 flex items-center gap-1.5 text-[11px] text-mist-500">
            <IconDisk width={13} height={13} /> {system.disk_free_gb} GB free · data at{' '}
            <span className="font-mono text-[10.5px] text-mist-400">{system.data_dir.split('/').slice(-2).join('/')}</span>
          </p>
        </div>
      </div>

      <YouTubeCard />

      <section className="card p-5">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-mist-100">Subtitle fonts</h2>
            <p className="text-xs text-mist-500">
              {system.fonts.length} font files installed · used directly by the renderer
            </p>
          </div>
          <span className="badge badge-green">
            <IconCheck width={11} height={11} /> {system.fonts.length} ready
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {system.fonts.map((f) => (
            <span key={f.file} className="rounded-md border border-white/[0.08] bg-ink-900/60 px-2 py-1 text-[11px] text-mist-300">
              {f.family}
            </span>
          ))}
        </div>
        {system.missing_fonts.length ? (
          <p className="mt-3 text-[11px] text-amber-300">
            Not installed: {system.missing_fonts.join(', ')} — run <span className="font-mono">START.command</span> to
            fetch them.
          </p>
        ) : null}
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-mist-100">
              <IconSettings width={15} height={15} /> Default clip settings
            </h2>
            <p className="text-xs text-mist-500">Applied to every new project. Saved on this Mac.</p>
          </div>
          <div className="flex gap-2">
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setDefaults(loadDefaults(system.defaults));
                saveDefaults(loadDefaults(system.defaults));
                notify('Defaults reset');
              }}
            >
              Reset
            </button>
            <button
              className="btn btn-primary btn-sm"
              onClick={() => {
                saveDefaults(defaults);
                notify('Defaults saved', 'success');
              }}
            >
              <IconSparkles width={13} height={13} /> Save defaults
            </button>
          </div>
        </div>
        <SettingsPanel
          value={defaults}
          onChange={(p) => setDefaults((s) => applyPatch(s, p))}
          mode="video"
          fonts={system.limits.fonts}
        />
      </section>

      <section className="card p-5">
        <h2 className="text-sm font-semibold text-mist-100">Storage layout</h2>
        <div className="mt-2 space-y-1.5 font-mono text-[11px] leading-relaxed text-mist-400">
          <p>data/clipforge.sqlite3 — projects, clips, jobs, live sessions</p>
          <p>data/projects/&lt;id&gt;/source.* — imported footage (never auto-deleted)</p>
          <p>data/projects/&lt;id&gt;/clips/ — rendered clips</p>
          <p>data/live/&lt;id&gt;/segments/ — captured livestream segments</p>
          <p>data/models/ — local Whisper models</p>
        </div>
        <Field label="" className="mt-3">
          <p className="text-[11px] leading-relaxed text-mist-500">
            Deleting a project from the Projects page removes its clips and working copy. Original files you uploaded
            stay in the project folder until you delete the project — nothing is removed in the background.
          </p>
        </Field>
      </section>
    </div>
  );
}
