'use client';

import { useEffect, type ReactNode } from 'react';
import { IconAlert, IconCheck, IconX } from './Icons';
import { useToasts } from '@/lib/toast';

export function ProgressBar({ value, tone = 'accent' }: { value: number; tone?: 'accent' | 'green' | 'red' }) {
  const clamped = Math.max(0, Math.min(100, value || 0));
  return (
    <div className="track">
      <div
        className={`track-fill ${tone === 'green' ? '!bg-emerald-400' : tone === 'red' ? '!bg-red-400' : ''}`}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
  className = '',
}: {
  label?: string;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      {label ? <span className="label">{label}</span> : null}
      {children}
      {hint ? <p className="hint mt-1.5">{hint}</p> : null}
    </div>
  );
}

export function Segmented<T extends string | number>({
  options,
  value,
  onChange,
  className = '',
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div className={`seg ${className}`}>
      {options.map((opt) => (
        <button
          key={String(opt.value)}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`seg-item ${value === opt.value ? 'seg-item-active' : ''}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  description?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="flex w-full items-start gap-3 rounded-xl px-1 py-1.5 text-left"
    >
      <span
        className={`mt-0.5 flex h-5 w-9 shrink-0 items-center rounded-full border transition ${
          checked ? 'border-accent/50 bg-accent/80' : 'border-white/12 bg-ink-700'
        }`}
      >
        <span
          className={`block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
            checked ? 'translate-x-[19px]' : 'translate-x-[3px]'
          }`}
        />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-mist-100">{label}</span>
        {description ? <span className="mt-0.5 block text-xs leading-relaxed text-mist-500">{description}</span> : null}
      </span>
    </button>
  );
}

export function Slider({
  value,
  min,
  max,
  step = 1,
  onChange,
  format,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  format?: (v: number) => string;
}) {
  return (
    <div className="flex items-center gap-3">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-white/10 accent-accent [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-accent"
      />
      <span className="w-14 shrink-0 text-right font-mono text-xs text-mist-300">
        {format ? format(value) : value}
      </span>
    </div>
  );
}

export function Spinner({ size = 16, className = '' }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={`animate-spin ${className}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.4}
    >
      <path d="M12 3a9 9 0 1 0 9 9" strokeLinecap="round" />
    </svg>
  );
}

export function StatusBadge({ status, children }: { status: string; children?: ReactNode }) {
  const tone =
    {
      done: 'badge-green',
      ready: 'badge-blue',
      live: 'badge-green',
      rendering: 'badge-blue',
      running: 'badge-blue',
      analyzing: 'badge-amber',
      ingesting: 'badge-amber',
      starting: 'badge-amber',
      reconnecting: 'badge-amber',
      pending: 'badge-neutral',
      queued: 'badge-neutral',
      failed: 'badge-red',
      stopped: 'badge-neutral',
      canceled: 'badge-neutral',
      created: 'badge-neutral',
    }[status] || 'badge-neutral';
  return <span className={`badge ${tone}`}>{children ?? status}</span>;
}

export function SectionHeading({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4">
      <div>
        <h2 className="text-[15px] font-semibold tracking-tight text-mist-100">{title}</h2>
        {subtitle ? <p className="mt-0.5 text-xs text-mist-500">{subtitle}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-white/[0.09] bg-ink-900/40 px-6 py-14 text-center">
      {icon ? <div className="mb-3 text-mist-500">{icon}</div> : null}
      <p className="text-sm font-semibold text-mist-200">{title}</p>
      {description ? <p className="mt-1 max-w-md text-xs leading-relaxed text-mist-500">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm sm:p-8">
      <div
        className={`card animate-fadeUp w-full ${wide ? 'max-w-5xl' : 'max-w-2xl'} mt-4 overflow-hidden p-0`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-white/[0.06] px-5 py-3.5">
          <h3 className="text-sm font-semibold text-mist-100">{title}</h3>
          <button onClick={onClose} className="btn btn-ghost btn-sm !px-2" aria-label="Close">
            <IconX />
          </button>
        </div>
        <div className="max-h-[78vh] overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  );
}

export function Toaster() {
  const toasts = useToasts();
  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-full max-w-sm flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`animate-fadeUp pointer-events-auto flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm shadow-panel backdrop-blur-md ${
            t.tone === 'error'
              ? 'border-red-400/30 bg-red-950/80 text-red-100'
              : t.tone === 'success'
                ? 'border-emerald-400/30 bg-emerald-950/80 text-emerald-100'
                : 'border-white/10 bg-ink-800/95 text-mist-100'
          }`}
        >
          <span className="mt-0.5 shrink-0">
            {t.tone === 'error' ? <IconAlert width={16} height={16} /> : <IconCheck width={16} height={16} />}
          </span>
          <span className="leading-snug">{t.message}</span>
        </div>
      ))}
    </div>
  );
}
