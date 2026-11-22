export function timecode(seconds: number, withMs = false): string {
  const s = Math.max(0, seconds || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  const base = h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
  if (!withMs) return base;
  const ms = Math.floor((s % 1) * 10);
  return `${base}.${ms}`;
}

export function secondsLabel(seconds: number): string {
  const s = Math.round(seconds || 0);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return rest ? `${m}m ${rest}s` : `${m}m`;
}

export function bytes(n: number): string {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let value = n;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function relativeTime(ts: number): string {
  if (!ts) return '';
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export const MODE_LABEL: Record<string, string> = {
  video: 'Video clipping',
  stream: 'Stream clipping',
  live: 'Live clipping',
};

export const STATUS_TONE: Record<string, string> = {
  done: 'badge-green',
  ready: 'badge-blue',
  rendering: 'badge-blue',
  analyzing: 'badge-amber',
  ingesting: 'badge-amber',
  pending: 'badge-neutral',
  queued: 'badge-neutral',
  running: 'badge-blue',
  failed: 'badge-red',
  canceled: 'badge-neutral',
  created: 'badge-neutral',
  live: 'badge-green',
  starting: 'badge-amber',
  reconnecting: 'badge-amber',
  stopped: 'badge-neutral',
};

export function resolutionLabel(resolution: number): string {
  return resolution >= 2160 ? '4K' : `${resolution}p`;
}

export function aspectLabel(layout: string): string {
  switch (layout) {
    case 'vertical':
    case 'facecam_top':
    case 'facecam_bottom':
    case 'facecam':
    case 'auto':
      return '9:16';
    case 'square':
      return '1:1';
    case 'portrait':
      return '4:5';
    default:
      return '16:9';
  }
}
