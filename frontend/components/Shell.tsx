'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { useSystem } from '@/lib/hooks';
import {
  IconDownload,
  IconFilm,
  IconFolder,
  IconGrid,
  IconLive,
  IconPlus,
  IconScissors,
  IconSettings,
} from './Icons';
import { Toaster } from './ui';

const NAV = [
  { href: '/', label: 'Dashboard', icon: IconGrid },
  { href: '/new', label: 'New project', icon: IconPlus },
  { href: '/live', label: 'Live clipping', icon: IconLive },
  { href: '/projects', label: 'Projects', icon: IconFolder },
  { href: '/exports', label: 'Exports', icon: IconDownload },
  { href: '/settings', label: 'Settings', icon: IconSettings },
];

export default function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname() || '/';
  const system = useSystem();
  const isActive = (href: string) => (href === '/' ? pathname === '/' : pathname.startsWith(href));

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[230px] flex-col border-r border-white/[0.05] bg-ink-950/80 px-4 py-5 backdrop-blur-xl lg:flex">
        <Link href="/" className="mb-7 flex items-center gap-2.5 px-1">
          <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-accent to-accent-purple text-white shadow-[0_6px_20px_-6px_rgba(109,140,255,0.9)]">
            <IconScissors width={17} height={17} />
          </span>
          <span className="text-[15px] font-semibold tracking-tight">
            Clip<span className="text-accent-soft">Forge</span>
          </span>
        </Link>

        <nav className="flex flex-1 flex-col gap-0.5">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = isActive(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`group flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] font-medium transition ${
                  active
                    ? 'bg-white/[0.07] text-white shadow-[0_1px_0_rgba(255,255,255,0.05)_inset]'
                    : 'text-mist-400 hover:bg-white/[0.04] hover:text-mist-100'
                }`}
              >
                <Icon
                  width={17}
                  height={17}
                  className={active ? 'text-accent-soft' : 'text-mist-500 group-hover:text-mist-300'}
                />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-4 rounded-xl border border-white/[0.06] bg-ink-900/70 p-3">
          <div className="flex items-center gap-2">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                system ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]' : 'bg-amber-400 animate-pulseSoft'
              }`}
            />
            <span className="text-[11px] font-semibold text-mist-300">
              {system ? 'Engine online' : 'Connecting…'}
            </span>
          </div>
          {system ? (
            <div className="mt-2 space-y-1 text-[10.5px] leading-relaxed text-mist-500">
              <p>
                {system.machine.chip?.replace('Apple ', '')} · {system.machine.cores} cores
                {system.machine.ram_gb ? ` · ${system.machine.ram_gb}GB` : ''}
              </p>
              <p>
                {system.ffmpeg.videotoolbox ? 'VideoToolbox ON' : 'Software encode'} ·{' '}
                {system.ffmpeg.libass ? 'Subtitles ON' : 'No libass'}
              </p>
              <p>{system.disk_free_gb} GB free</p>
            </div>
          ) : null}
        </div>
      </aside>

      <header className="sticky top-0 z-20 border-b border-white/[0.05] bg-ink-950/85 backdrop-blur-xl lg:hidden">
        <div className="flex items-center justify-between px-4 py-3">
          <Link href="/" className="flex items-center gap-2">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-accent to-accent-purple">
              <IconScissors width={15} height={15} />
            </span>
            <span className="text-sm font-semibold">ClipForge</span>
          </Link>
          <span className="flex items-center gap-1.5 text-[11px] text-mist-400">
            <span className={`h-1.5 w-1.5 rounded-full ${system ? 'bg-emerald-400' : 'bg-amber-400'}`} />
            {system ? 'online' : 'connecting'}
          </span>
        </div>
        <div className="flex gap-1 overflow-x-auto px-3 pb-2.5">
          {NAV.map((item) => {
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium ${
                  isActive(item.href) ? 'bg-white/[0.08] text-white' : 'text-mist-400'
                }`}
              >
                <Icon width={14} height={14} />
                {item.label}
              </Link>
            );
          })}
        </div>
      </header>

      <main className="lg:pl-[230px]">
        <div className="mx-auto w-full max-w-[1440px] px-4 py-6 sm:px-7 lg:py-9">{children}</div>
      </main>
      <Toaster />
    </div>
  );
}
