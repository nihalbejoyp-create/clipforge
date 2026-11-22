export type Mode = 'video' | 'stream' | 'live';
export type ClipStatus = 'pending' | 'rendering' | 'done' | 'failed';
export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'canceled';

export interface SubtitleStyle {
  enabled: boolean;
  preset: string;
  font: string;
  font_size: number;
  weight: number;
  position: 'top' | 'middle' | 'bottom';
  offset: number;
  uppercase: boolean;
  primary_color: string;
  highlight_color: string;
  outline_color: string;
  outline_width: number;
  shadow: number;
  background: boolean;
  background_color: string;
  background_opacity: number;
  animation: 'word_pop' | 'karaoke' | 'fade' | 'none';
  max_words: number;
  emoji: boolean;
}

export interface Settings {
  clip_count: number;
  clip_duration: number;
  custom_duration: number | null;
  resolution: number;
  audio_mode: 'original' | 'high' | 'custom';
  audio_bitrate: number;
  format: 'mp4' | 'mp4_hevc' | 'mov';
  layout: string;
  reframe: 'fit_blur' | 'crop';
  facecam_region: { x: number; y: number; w: number; h: number } | null;
  quality: 'compact' | 'balanced' | 'high';
  highlight_sensitivity: number;
  allow_overlap: boolean;
  whisper_model: string;
  language: string;
  normalize_audio: boolean;
  loudness_target: number;
  encoder: string;
  subtitle: SubtitleStyle;
  live_pre_roll: number;
  live_buffer_seconds: number;
  live_unlimited: boolean;
  live_sensitivity: number;
  live_analysis_window: number;
}

export interface Project {
  id: string;
  name: string;
  mode: Mode;
  source_type: string;
  source_url: string | null;
  source_path: string | null;
  title: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  has_audio: number;
  status: string;
  error: string | null;
  settings: Settings;
  thumb_url: string | null;
  source_url_local: string | null;
  clip_count: number;
  done_count: number;
  created_at: number;
  updated_at: number;
}

export interface Clip {
  id: string;
  project_id: string;
  idx: number;
  title: string;
  text: string;
  start: number;
  end: number;
  duration: number;
  score: number;
  reason: string;
  status: ClipStatus;
  progress: number;
  path: string | null;
  url: string | null;
  thumb_url: string | null;
  download_url: string | null;
  error: string | null;
  youtube_id: string | null;
  youtube_url: string | null;
  youtube_account?: string | null;
  settings: Partial<Settings>;
  source: 'ai' | 'live' | 'manual';
  created_at: number;
  updated_at: number;
  project_name?: string;
  project_mode?: Mode;
}

export interface Job {
  id: string;
  project_id: string | null;
  kind: 'ingest' | 'analyze' | 'render' | 'export' | 'upload';
  status: JobStatus;
  progress: number;
  message: string;
  error: string | null;
  payload?: { clip_id?: string; video_id?: string; url?: string };
  created_at: number;
}

export interface YouTubeDraft {
  clip_id: string;
  project_id: string;
  idx: number;
  duration: number;
  thumb_url: string | null;
  status: ClipStatus;
  rendered: boolean;
  youtube_url: string | null;
  youtube_account: string | null;
  title: string;
  description: string;
  tags: string[];
}

export interface YouTubeAccount {
  id: string;
  title: string;
  thumbnail: string;
  active: boolean;
}

export interface YouTubeStatus {
  configured: boolean;
  client_id: string;
  client_kind: string;
  has_secret: boolean;
  connected: boolean;
  channel: { id: string; title: string; thumbnail: string } | null;
  accounts: YouTubeAccount[];
  active_account: string;
  redirect_uri: string;
  scopes: string[];
}

export interface LiveSession {
  id: string;
  url: string;
  title: string;
  status: 'starting' | 'live' | 'reconnecting' | 'stopped' | 'failed';
  started_at: number;
  stopped_at: number | null;
  duration: number;
  segments: number;
  highlights: number;
  clips: number;
  width: number;
  height: number;
  fps: number;
  has_audio: number;
  error: string | null;
  disk_free_gb: number;
  project_id: string;
  settings: Settings;
  clips_list?: Clip[];
  jobs?: Job[];
}

export interface SystemInfo {
  machine: { chip: string; arch: string; cores: number; ram_gb: number | null; apple_silicon: boolean };
  ffmpeg: {
    ffmpeg: boolean;
    ffprobe: boolean;
    version: string | null;
    videotoolbox: boolean;
    libass: boolean;
    encoders: Record<string, boolean>;
    ffmpeg_path: string;
  };
  disk_free_gb: number;
  data_dir: string;
  fonts: { family: string; file: string }[];
  missing_fonts: string[];
  whisper: { available: boolean };
  opencv: boolean;
  frontend_built: boolean;
  limits: {
    clip_count: number[];
    durations: number[];
    resolutions: number[];
    formats: string[];
    layouts: { id: string; label: string }[];
    subtitle_presets: string[];
    fonts: string[];
  };
  defaults: Settings;
}
