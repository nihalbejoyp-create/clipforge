'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { SystemInfo } from './types';

export function useQueryParam(key: string): string {
  const [value, setValue] = useState('');
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setValue(params.get(key) || '');
  }, [key]);
  return value;
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number | null,
  deps: unknown[] = [],
  options: { stopWhen?: (err: any) => boolean } = {},
): {
  data: T | null;
  error: string | null;
  loading: boolean;
  refresh: () => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  // A permanent failure (deleted project, bad id) never fixes itself, so the
  // poll loop stops instead of hammering a 404 every few seconds.
  const halted = useRef(false);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const stopWhenRef = useRef(options.stopWhen);
  stopWhenRef.current = options.stopWhen;

  const run = useCallback(async () => {
    try {
      const result = await fetcherRef.current();
      if (!alive.current) return;
      halted.current = false;
      setData(result);
      setError(null);
    } catch (err: any) {
      if (!alive.current) return;
      setError(err?.message || 'request failed');
      if (stopWhenRef.current?.(err)) halted.current = true;
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    halted.current = false;
    setLoading(true);
    run();
    let timer: ReturnType<typeof setTimeout> | null = null;
    if (intervalMs && intervalMs > 0) {
      const tick = () => {
        timer = setTimeout(async () => {
          if (!alive.current || halted.current) return;
          // A hidden tab has nobody looking at it; polling it only burns CPU
          // that the renderer needs. Refresh immediately on return.
          if (document.visibilityState === 'visible') await run();
          if (alive.current && !halted.current) tick();
        }, intervalMs);
      };
      tick();
    }
    const onVisible = () => {
      if (document.visibilityState === 'visible' && !halted.current) run();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      alive.current = false;
      document.removeEventListener('visibilitychange', onVisible);
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps]);

  return { data, error, loading, refresh: run };
}

let systemCache: SystemInfo | null = null;

export function useSystem(): SystemInfo | null {
  const [system, setSystem] = useState<SystemInfo | null>(systemCache);
  useEffect(() => {
    if (systemCache) return;
    api
      .system()
      .then((s) => {
        systemCache = s;
        setSystem(s);
      })
      .catch(() => undefined);
  }, []);
  return system;
}

