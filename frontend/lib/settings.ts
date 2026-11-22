import type { Settings } from './types';

export const FALLBACK_SETTINGS: Settings = {
  clip_count: 5,
  clip_duration: 30,
  custom_duration: null,
  resolution: 1080,
  audio_mode: 'high',
  audio_bitrate: 192,
  format: 'mp4',
  layout: 'vertical',
  reframe: 'fit_blur',
  facecam_region: null,
  quality: 'high',
  highlight_sensitivity: 50,
  allow_overlap: false,
  whisper_model: 'base',
  language: 'auto',
  normalize_audio: true,
  loudness_target: -14,
  encoder: 'auto',
  subtitle: {
    enabled: true,
    preset: 'bold_pop',
    font: 'Inter',
    font_size: 1,
    weight: 800,
    position: 'bottom',
    offset: 0,
    uppercase: true,
    primary_color: '#FFFFFF',
    highlight_color: '#FFD400',
    outline_color: '#000000',
    outline_width: 4,
    shadow: 2,
    background: false,
    background_color: '#000000',
    background_opacity: 0.55,
    animation: 'word_pop',
    max_words: 4,
    emoji: false,
  },
  live_pre_roll: 0.55,
  live_buffer_seconds: 240,
  live_unlimited: false,
  live_sensitivity: 55,
  live_analysis_window: 30,
};

const KEY = 'clipforge.settings.v1';

export function loadDefaults(base?: Settings | null): Settings {
  const merged: Settings = {
    ...FALLBACK_SETTINGS,
    ...(base || {}),
    subtitle: { ...FALLBACK_SETTINGS.subtitle, ...((base as Settings | null)?.subtitle || {}) },
  };
  if (typeof window === 'undefined') return merged;
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return merged;
    const stored = JSON.parse(raw);
    return {
      ...merged,
      ...stored,
      subtitle: { ...merged.subtitle, ...(stored.subtitle || {}) },
    };
  } catch {
    return merged;
  }
}

export function saveDefaults(settings: Settings) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(settings));
  } catch {
    /* storage unavailable */
  }
}

export type YouTubePrivacy = 'private' | 'unlisted' | 'public';

// The last visibility picked for a YouTube upload, so a bulk post starts where
// the last one left off instead of resetting to Private.
const PRIVACY_KEY = 'clipforge.youtube.privacy.v1';
const PRIVACIES: YouTubePrivacy[] = ['private', 'unlisted', 'public'];

export function readYouTubePrivacy(): YouTubePrivacy | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(PRIVACY_KEY);
    return PRIVACIES.includes(raw as YouTubePrivacy) ? (raw as YouTubePrivacy) : null;
  } catch {
    return null;
  }
}

export function rememberYouTubePrivacy(value: YouTubePrivacy) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(PRIVACY_KEY, value);
  } catch {
    /* storage unavailable */
  }
}

export function applyPatch(current: Settings, patch: Partial<Settings>): Settings {
  return {
    ...current,
    ...patch,
    subtitle: patch.subtitle ? { ...current.subtitle, ...patch.subtitle } : current.subtitle,
  };
}
