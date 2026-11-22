import type { Clip, Job, LiveSession, Project, Settings, SystemInfo, YouTubeDraft, YouTubeStatus } from './types';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    });
  } catch (err) {
    throw new ApiError('Cannot reach the ClipForge engine. Is it still running?');
  }
  const text = await res.text();
  let data: any = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) || (typeof data === 'string' ? data : '') || res.statusText;
    throw new ApiError(typeof detail === 'string' ? detail : JSON.stringify(detail), res.status);
  }
  return data as T;
}

export const api = {
  system: () => request<SystemInfo>('/api/system'),
  projects: () => request<{ projects: Project[] }>('/api/projects'),
  project: (id: string) => request<{ project: Project; jobs: Job[] }>(`/api/projects/${id}`),
  clips: (id: string) => request<{ clips: Clip[] }>(`/api/projects/${id}/clips`),
  allClips: (status?: string) =>
    request<{ clips: Clip[] }>(`/api/clips${status ? `?status=${status}` : ''}`),
  job: (id: string) => request<{ job: Job }>(`/api/jobs/${id}`),
  cancelJob: (id: string) => request<{ ok: boolean }>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  jobs: () => request<{ jobs: Job[] }>('/api/jobs?limit=30'),
  validateUrl: (url: string) =>
    request<{ ok: boolean; title: string; duration: number; is_live: boolean; uploader?: string }>(
      '/api/validate-url',
      { method: 'POST', body: JSON.stringify({ url }) },
    ),
  createProject: (body: Record<string, unknown>) =>
    request<{ ok: boolean; project_id: string; job_id?: string; live?: LiveSession }>('/api/projects', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  analyze: (id: string, settings: Settings) =>
    request<{ ok: boolean; job_id: string }>(`/api/projects/${id}/analyze`, {
      method: 'POST',
      body: JSON.stringify({ settings }),
    }),
  deleteProject: (id: string, files = true) =>
    request<{ ok: boolean }>(`/api/projects/${id}?delete_files=${files}`, { method: 'DELETE' }),
  exportAll: (id: string) =>
    request<{ ok: boolean; queued: number }>(`/api/projects/${id}/export-all`, { method: 'POST' }),
  updateClip: (id: string, body: Record<string, unknown>) =>
    request<{ ok: boolean; clip: Clip }>(`/api/clips/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  renderClip: (id: string) =>
    request<{ ok: boolean; job_id: string }>(`/api/clips/${id}/render`, { method: 'POST' }),
  regenerateClip: (id: string) =>
    request<{ ok: boolean; clip: Clip }>(`/api/clips/${id}/regenerate`, { method: 'POST' }),
  uploadToYouTube: (id: string, body: { title: string; description: string; privacy: string; account?: string }) =>
    request<{ ok: boolean; job_id: string; job: Job }>(`/api/clips/${id}/youtube`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  youtube: {
    status: () => request<YouTubeStatus>('/api/youtube/status'),
    prepare: (clipIds: string[]) =>
      request<{ items: YouTubeDraft[]; connected: boolean }>('/api/youtube/prepare', {
        method: 'POST',
        body: JSON.stringify({ clip_ids: clipIds }),
      }),
    uploadMany: (
      items: { clip_id: string; title: string; description: string; tags: string[] }[],
      privacy: string,
      account?: string,
    ) =>
      request<{
        ok: boolean;
        queued: number;
        job_ids: string[];
        skipped: { clip_id: string; reason: string }[];
        account: string;
      }>('/api/youtube/bulk', { method: 'POST', body: JSON.stringify({ items, privacy, account }) }),
    saveClient: (input: { blob?: string; client_id?: string; client_secret?: string }) =>
      request<YouTubeStatus>('/api/youtube/client', { method: 'POST', body: JSON.stringify(input) }),
    connect: () => request<{ ok: boolean; url: string }>('/api/youtube/connect', { method: 'POST' }),
    setActive: (accountId: string) =>
      request<YouTubeStatus>('/api/youtube/account', {
        method: 'POST',
        body: JSON.stringify({ account_id: accountId }),
      }),
    disconnect: (accountId?: string) =>
      request<YouTubeStatus>('/api/youtube/disconnect', {
        method: 'POST',
        body: JSON.stringify({ account_id: accountId }),
      }),
  },
  subtitlePreview: (settings: Settings) =>
    request<{ ok: boolean; url: string }>('/api/preview/subtitle', {
      method: 'POST',
      body: JSON.stringify({ settings }),
    }),
  liveSessions: () => request<{ sessions: LiveSession[] }>('/api/live'),
  liveStart: (url: string, settings: Settings, title?: string) =>
    request<{ ok: boolean; session: LiveSession }>('/api/live', {
      method: 'POST',
      body: JSON.stringify({ url, settings, title }),
    }),
  liveDetail: (id: string) => request<{ session: LiveSession }>(`/api/live/${id}`),
  liveStop: (id: string) => request<{ ok: boolean }>(`/api/live/${id}/stop`, { method: 'POST' }),
  transcript: (id: string) =>
    request<{ words: { start: number; end: number; word: string }[]; segments: any[]; language: string }>(
      `/api/projects/${id}/transcript`,
    ),
};

export async function uploadFile(
  file: File,
  onProgress?: (pct: number) => void,
): Promise<{ temp_path: string; filename: string; size: number; probe: any }> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload');
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText || '{}');
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new ApiError(data.detail || 'Upload failed', xhr.status));
      } catch (err) {
        reject(new ApiError('Upload failed'));
      }
    };
    xhr.onerror = () => reject(new ApiError('Upload failed — check the engine is running'));
    xhr.send(form);
  });
}

export function streamUrl(path: string) {
  return path;
}
