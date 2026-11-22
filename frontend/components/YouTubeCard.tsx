'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { toast as notify } from '@/lib/toast';
import type { YouTubeStatus } from '@/lib/types';
import { IconAlert, IconCheck, IconExternal, IconYouTube } from './Icons';
import { Field, Spinner } from './ui';

const CONSOLE_LINKS = {
  api: 'https://console.cloud.google.com/apis/library/youtube.googleapis.com',
  consent: 'https://console.cloud.google.com/apis/credentials/consent',
  credentials: 'https://console.cloud.google.com/apis/credentials',
};

export default function YouTubeCard() {
  const [status, setStatus] = useState<YouTubeStatus | null>(null);
  const [blob, setBlob] = useState('');
  const [clientId, setClientId] = useState('');
  const [secret, setSecret] = useState('');
  const [saving, setSaving] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [busyAccount, setBusyAccount] = useState('');
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [error, setError] = useState('');
  const waitingRef = useRef(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api.youtube.status();
      setStatus(next);
      setClientId((current) => current || next.client_id);
      if (next.connected && waitingRef.current) {
        waitingRef.current = false;
        setWaiting(false);
        setAuthUrl(null);
        setSecret('');
        notify(`Connected to ${next.channel?.title || 'YouTube'}`, 'success');
      }
    } catch (err: any) {
      setError(err?.message || 'Cannot reach the ClipForge engine.');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // The Google tab does the actual sign-in; poll until its callback lands.
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => {
      void refresh();
    }, 2500);
    return () => clearInterval(timer);
  }, [waiting, refresh]);

  const saveClient = async (input: { blob?: string; client_id?: string; client_secret?: string }) => {
    setSaving(true);
    setError('');
    try {
      const next = await api.youtube.saveClient(input);
      setStatus(next);
      setSecret('');
      setBlob('');
      notify('Google client saved', 'success');
      // Saving a fresh client is the step people stall on, so go straight to
      // Google instead of making them find a second button.
      if (!next.connected) await connect();
    } catch (err: any) {
      setError(err?.message || 'Could not save the client');
    } finally {
      setSaving(false);
    }
  };

  const savePasted = () => {
    if (!blob.trim()) {
      setError('Paste the client_secret_….json Google gave you, or drop the file in the box.');
      return;
    }
    void saveClient({ blob });
  };

  const saveManual = () => {
    if (!clientId.trim()) {
      setError('Paste the client ID first.');
      return;
    }
    void saveClient({ client_id: clientId.trim(), client_secret: secret.trim() });
  };

  const readFile = async (file: File | null | undefined) => {
    if (!file) return;
    try {
      const text = await file.text();
      setBlob(text);
      void saveClient({ blob: text });
    } catch {
      setError('Could not read that file — paste its contents instead.');
    }
  };

  const connect = async () => {
    setError('');
    try {
      const { url } = await api.youtube.connect();
      setAuthUrl(url);
      waitingRef.current = true;
      setWaiting(true);
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (err: any) {
      setError(err?.message || 'Could not start the sign-in');
    }
  };

  // Links from a clip can point straight at this card ("Open Settings → YouTube channel").
  // Deferred a tick because the router resets the scroll position right after a page change.
  useEffect(() => {
    if (window.location.hash !== '#youtube') return;
    const timer = setTimeout(() => {
      document.getElementById('youtube')?.scrollIntoView({ block: 'start' });
    }, 180);
    return () => clearTimeout(timer);
  }, []);

  const disconnect = async (accountId?: string) => {
    const account = status?.accounts.find((item) => item.id === accountId);
    if (account && !window.confirm(`Remove ${account.title} from ClipForge? The channel keeps every video already uploaded; you can add it again any time.`)) {
      return;
    }
    setError('');
    setBusyAccount(accountId || 'all');
    try {
      setStatus(await api.youtube.disconnect(accountId));
      notify(account ? `${account.title} removed` : 'YouTube disconnected');
    } catch (err: any) {
      setError(err?.message || 'Could not disconnect');
    } finally {
      setBusyAccount('');
    }
  };

  const makeDefault = async (accountId: string) => {
    setError('');
    setBusyAccount(accountId);
    try {
      setStatus(await api.youtube.setActive(accountId));
      notify('Default channel changed', 'success');
    } catch (err: any) {
      setError(err?.message || 'Could not switch the default channel');
    } finally {
      setBusyAccount('');
    }
  };

  if (!status) {
    return (
      <section className="card p-5">
        <div className="flex items-center gap-2 text-sm text-mist-400">
          <Spinner size={14} /> Reading YouTube connection…
        </div>
      </section>
    );
  }

  const credentialsForm = (
    <div className="space-y-2.5">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void readFile(e.dataTransfer.files?.[0]);
        }}
        className={`rounded-xl border border-dashed p-2.5 transition-colors ${
          dragging ? 'border-accent/60 bg-accent/[0.06]' : 'border-white/[0.12] bg-ink-900/40'
        }`}
      >
        <textarea
          className="input min-h-[78px] resize-y font-mono text-[10.5px] leading-relaxed"
          value={blob}
          onChange={(e) => setBlob(e.target.value)}
          placeholder={'Paste client_secret_….json here — or drop the file in this box\n{"installed":{"client_id":"…","client_secret":"…"}}'}
          spellCheck={false}
          autoComplete="off"
        />
        <div className="flex flex-wrap items-center gap-2 pt-2">
          <button className="btn btn-primary btn-sm" onClick={savePasted} disabled={saving || !blob.trim()}>
            {saving ? <Spinner size={12} /> : <IconCheck width={13} height={13} />}
            Save &amp; connect
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()} disabled={saving}>
            Choose the JSON file
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(e) => {
              void readFile(e.target.files?.[0]);
              e.target.value = '';
            }}
          />
        </div>
      </div>
      <details>
        <summary className="cursor-pointer text-[11px] text-mist-500 hover:text-mist-300">
          Or paste the ID and secret by hand
        </summary>
        <div className="space-y-2.5 pt-2.5">
          <Field label="Client ID">
            <input
              className="input"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="1234567890-abc123.apps.googleusercontent.com"
              spellCheck={false}
              autoComplete="off"
            />
          </Field>
          <Field
            label="Client secret"
            hint={
              status.has_secret
                ? 'A secret is already stored locally. Leave this empty to keep it.'
                : 'Shown once when you create the OAuth client — paste it here.'
            }
          >
            <input
              className="input"
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder={status.has_secret ? '•••••••• (stored)' : 'GOCSPX-…'}
              autoComplete="off"
            />
          </Field>
          <button className="btn btn-secondary btn-sm" onClick={saveManual} disabled={saving || !clientId.trim()}>
            {saving ? <Spinner size={12} /> : <IconCheck width={13} height={13} />}
            Save &amp; connect
          </button>
        </div>
      </details>
    </div>
  );

  return (
    <section id="youtube" className="card scroll-mt-24 p-5">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-mist-100">
            <IconYouTube width={16} height={16} /> YouTube channel
          </h2>
          <p className="text-xs text-mist-500">
            Upload renders straight from this Mac to your channel — no download, no re-upload.
          </p>
        </div>
        {status.connected ? (
          <span className="badge badge-green">
            <IconCheck width={11} height={11} />{' '}
            {status.accounts.length > 1 ? `${status.accounts.length} channels` : 'connected'}
          </span>
        ) : (
          <span className={`badge ${status.configured ? 'badge-neutral' : 'badge-neutral'}`}>
            {status.configured ? 'not connected' : 'not set up'}
          </span>
        )}
      </div>

      {error ? (
        <div className="mb-3 flex items-start gap-2 rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-2 text-[12px] leading-relaxed text-red-200">
          <IconAlert width={14} height={14} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      {status.connected ? (
        <div className="space-y-3">
          <div className="space-y-2">
            {status.accounts.map((account) => (
              <div
                key={account.id}
                className={`flex flex-wrap items-center gap-3 rounded-xl border p-3 ${
                  account.active ? 'border-accent/40 bg-accent/[0.06]' : 'border-white/[0.07] bg-ink-900/50'
                }`}
              >
                {account.thumbnail ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={account.thumbnail} alt="" className="h-9 w-9 rounded-full bg-ink-800" />
                ) : (
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-ink-800 text-mist-400">
                    <IconYouTube width={16} height={16} />
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-mist-100">{account.title}</p>
                  <p className="text-[11px] leading-relaxed text-mist-500">
                    {account.active
                      ? 'One-click uploads post here.'
                      : 'Connected — pick it for a batch, or make it the default.'}
                  </p>
                </div>
                {account.active ? (
                  <span className="badge badge-green">
                    <IconCheck width={11} height={11} /> default
                  </span>
                ) : (
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => makeDefault(account.id)}
                    disabled={busyAccount === account.id}
                  >
                    {busyAccount === account.id ? <Spinner size={12} /> : null} Make default
                  </button>
                )}
                <button
                  className="btn btn-ghost btn-sm text-red-300/80 hover:text-red-200"
                  onClick={() => disconnect(account.id)}
                  disabled={busyAccount === account.id}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-primary btn-sm" onClick={connect} disabled={waiting}>
              {waiting ? <Spinner size={12} /> : <IconYouTube width={14} height={14} />}
              {waiting ? 'Waiting for Google…' : 'Add another channel'}
            </button>
            <a className="btn btn-ghost btn-sm" href={CONSOLE_LINKS.credentials} target="_blank" rel="noreferrer">
              Google console <IconExternal width={12} height={12} />
            </a>
          </div>
          <p className="text-[11px] leading-relaxed text-mist-500">
            <span className="text-mist-300">Add another channel</span> always opens Google&apos;s account chooser, so you
            pick the Google login or the YouTube channel you want — including your own <span className="text-mist-300">brand
            channels</span>, which need no console setup at all. Every connected channel shares this one Google OAuth
            client, keeps its own tokens, and the upload window lets you choose which one receives each batch.
          </p>
          <p className="text-[11px] leading-relaxed text-mist-500">
            Vertical clips under three minutes go up as <span className="text-mist-300">Shorts</span> automatically —
            no separate setting to tick.
          </p>
          <p className="text-[11px] leading-relaxed text-mist-500">
            A Google Cloud project still in <span className="text-mist-300">Testing</span> mode admits only accounts
            added as Test users, caps the project at 100 accounts and expires these connections after 7 days — open
            <span className="text-mist-300">Add another channel</span> and sign in again to renew one. Brand channels of an
            account that already signs in are unaffected by all three.
          </p>
          <details className="pt-1">
            <summary className="cursor-pointer text-[11px] text-mist-500 hover:text-mist-300">
              Replace the Google client
            </summary>
            <div className="pt-3">{credentialsForm}</div>
          </details>
        </div>
      ) : (
        <div className="space-y-4">
          {status.configured ? (
            <div className="space-y-3">
              <p className="text-[12px] leading-relaxed text-mist-400">
                Client <span className="font-mono text-[10.5px] text-mist-300">{status.client_id.slice(0, 28)}…</span>{' '}
                is saved. Sign in with the Google account that owns the channel.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <button className="btn btn-primary btn-sm" onClick={connect} disabled={waiting}>
                  {waiting ? <Spinner size={13} /> : <IconYouTube width={14} height={14} />}
                  {waiting ? 'Waiting for Google…' : 'Connect YouTube channel'}
                </button>
                {waiting ? (
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => {
                      waitingRef.current = false;
                      setWaiting(false);
                      setAuthUrl(null);
                    }}
                  >
                    Cancel
                  </button>
                ) : null}
                {waiting && authUrl ? (
                  <a className="text-[11px] text-accent-soft underline" href={authUrl} target="_blank" rel="noreferrer">
                    Didn’t open? Use this link
                  </a>
                ) : null}
              </div>
              <p className="text-[11px] leading-relaxed text-mist-500">
                A new tab opens on accounts.google.com. Approve the two YouTube permissions, then come back here — this
                card updates itself.
              </p>
              <details>
                <summary className="cursor-pointer text-[11px] text-mist-500 hover:text-mist-300">
                  Replace the Google client
                </summary>
                <div className="pt-3">{credentialsForm}</div>
              </details>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-[12px] leading-relaxed text-mist-400">
                YouTube only allows an app to post to a channel with your own Google credentials. It is a one-time,
                five-minute job in a browser — nothing here costs money and no key leaves this Mac.
              </p>
              <ol className="space-y-1.5 text-[12px] leading-relaxed text-mist-400">
                <li>
                  1. Press{' '}
                  <a className="text-accent-soft underline" href={CONSOLE_LINKS.api} target="_blank" rel="noreferrer">
                    Enable “YouTube Data API v3”
                  </a>{' '}
                  in the Google Cloud console (pick or create a project first).
                </li>
                <li>
                  2. On the{' '}
                  <a className="text-accent-soft underline" href={CONSOLE_LINKS.consent} target="_blank" rel="noreferrer">
                    OAuth consent screen
                  </a>
                  : user type <span className="text-mist-200">External</span>, add your own Google account under{' '}
                  <span className="text-mist-200">Test users</span>, and add the two scopes below. This is what stops
                  Google from blocking the sign-in.
                </li>
                <li>
                  3. On{' '}
                  <a className="text-accent-soft underline" href={CONSOLE_LINKS.credentials} target="_blank" rel="noreferrer">
                    Credentials
                  </a>{' '}
                  → <span className="text-mist-200">Create credentials</span> →{' '}
                  <span className="text-mist-200">OAuth client ID</span> → type{' '}
                  <span className="text-mist-200">Desktop app</span> → <span className="text-mist-200">Create</span>,
                  then <span className="text-mist-200">Download JSON</span>.
                </li>
                <li>
                  4. Drop that file in the box below and press{' '}
                  <span className="text-mist-200">Save &amp; connect</span> — the Google tab handles the rest.
                </li>
              </ol>
              <div className="rounded-xl border border-white/[0.07] bg-ink-900/50 p-3 text-[11px] leading-relaxed text-mist-500">
                <p className="text-mist-400">Scopes to add on the consent screen:</p>
                {status.scopes.map((scope) => (
                  <p key={scope} className="mt-1 font-mono text-[10.5px] break-all text-mist-300">
                    {scope}
                  </p>
                ))}
                <p className="mt-2">
                  A <span className="text-mist-300">Desktop app</span> client needs no redirect registration. If you
                  made a <span className="text-mist-300">Web application</span> client instead, add this authorised
                  redirect URI to it:{' '}
                  <span className="font-mono text-[10.5px] text-mist-300 break-all">{status.redirect_uri}</span>
                </p>
              </div>
              {status.client_kind === 'web' ? (
                <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-amber-300">
                  <IconAlert width={12} height={12} className="mt-0.5 shrink-0" />
                  That JSON is a Web application client. It works once{' '}
                  <span className="font-mono text-[10.5px] break-all">{status.redirect_uri}</span> is listed under its
                  authorised redirect URIs — a Desktop app client skips that step.
                </p>
              ) : null}
              {credentialsForm}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
