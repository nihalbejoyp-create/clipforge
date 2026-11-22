"""Post finished clips straight to the user's own YouTube channel.

OAuth 2.0 with a loopback redirect served by this app (127.0.0.1 on the same
port), then the Data API v3 resumable upload protocol. The rendered file is
streamed from disk to Google by the server, so publishing a clip never needs a
manual download-and-upload round trip.

Token storage lives in the local SQLite ``kv`` table; the client secret and the
tokens are never returned to the browser.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import db, metadata
from .config import PORT

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNEL_URL = "https://www.googleapis.com/youtube/v3/channels"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
PLAYLIST_URL = "https://www.googleapis.com/youtube/v3/playlistItems"

# Upload-only, plus read access for the channel name on the connect screen.
# Asking for the narrower youtube.upload alone would leave no way to show which
# channel is connected before the first upload.
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
PRIVACY = ("private", "unlisted", "public")
# Google asks for 256 KB multiples; 8 MB keeps progress moving on a 200 MB 4K
# clip without turning the upload into thousands of round trips.
CHUNK_BYTES = 8 * 1024 * 1024
CATEGORY_ID = "22"  # People & Blogs, the category Google's own upload sample uses.

K_CLIENT = "youtube.client"
K_ACCOUNTS = "youtube.accounts"
K_PENDING = "youtube.pending"
# Single-account keys from before multi-channel support; read once and folded
# into K_ACCOUNTS so an existing connection survives the upgrade.
K_TOKENS = "youtube.tokens"
K_CHANNEL = "youtube.channel"


class YouTubeError(RuntimeError):
    """A failure the user can act on; the message is shown verbatim."""


def redirect_uri() -> str:
    return f"http://127.0.0.1:{PORT}/api/youtube/callback"


def oauth_error_hint(code: str) -> str:
    """Turn Google's OAuth error codes into something the user can fix.

    These are the failures people actually hit while setting up their own
    client: a consent screen still in Testing, a Web client without the
    loopback redirect, or the API never enabled.
    """
    hints = {
        "access_denied": (
            "Google blocked this sign-in. A consent screen in Testing mode only admits accounts "
            "listed as Test users. Without touching the console: sign in with the same Google "
            "account and choose a different YouTube channel (brand account) on the chooser. "
            "To admit another Google account, publish the consent screen \u2014 Audience \u2192 Publish app."
        ),
        "redirect_uri_mismatch": (
            "Google rejected the redirect URI. Create a Desktop app OAuth client \u2014 it needs no "
            f"redirect registration \u2014 or add {redirect_uri()} to the Web client's authorised redirect URIs."
        ),
        "invalid_client": (
            "Google doesn't recognise that OAuth client \u2014 the client ID or secret is wrong. "
            "Paste the downloaded JSON again."
        ),
        "invalid_grant": "That sign-in code has already been used or expired \u2014 press Connect again.",
        "admin_policy_enforced": (
            "A Google Workspace admin on this account blocked the app. A personal Google account works."
        ),
        "access_not_configured": (
            "The YouTube Data API v3 is not enabled on this Google Cloud project. Enable it in the console, "
            "wait a minute, then press Connect again."
        ),
        "org_internal": "That OAuth consent screen is limited to one organisation \u2014 set the user type to External.",
    }
    return hints.get((code or "").strip(), "")


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

def _http(url: str, *, method: str = "GET", body: bytes | None = None,
          headers: dict | None = None, timeout: float = 60) -> tuple[int, dict, bytes]:
    """One request. HTTP error statuses are returned, not raised."""
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read()
        except Exception:  # noqa: BLE001 - a broken error body is not fatal
            payload = b""
        return exc.code, dict(exc.headers or {}), payload
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise YouTubeError(f"Can't reach Google ({reason}). Check the connection and retry.") from exc


def _json(raw: bytes) -> dict:
    try:
        data = json.loads(raw or b"{}")
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _error(data: dict, fallback: str) -> str:
    """Google's error shape is either {"error": {...}} or the OAuth form."""
    detail = data.get("error")
    if isinstance(detail, dict):
        return str(detail.get("message") or fallback)
    if isinstance(detail, str):
        hint = data.get("error_description")
        return f"{detail}: {hint}" if hint else detail
    return fallback


# Google's wording for these two is a single opaque sentence, and both look like
# bugs to whoever reads them. Name the limit, who owns it, and the way out.
_HINT_UPLOAD_LIMIT = (
    "YouTube's daily upload limit for this channel is used up. That limit belongs to your "
    "YouTube channel (it counts Studio, the phone app and this app together) and resets 24 "
    "hours after the day's first upload \u2014 it is not a ClipForge limit. Unverified channels "
    "start at a very low limit: verify your phone at m.youtube.com/verify, or in YouTube "
    "Studio \u2192 Settings \u2192 Channel \u2192 Feature eligibility, and it rises. Nothing was posted."
)
_HINT_QUOTA = (
    "This Google Cloud project's YouTube API quota is used up for today. It resets at "
    "midnight Pacific time \u2014 try again after that. Nothing was posted."
)


def _api_error(data: dict, fallback: str) -> str:
    """Like ``_error``, but spells out the two limits Google hides behind a sentence."""
    detail = data.get("error")
    reasons: set[str] = set()
    if isinstance(detail, dict):
        for item in detail.get("errors") or []:
            if isinstance(item, dict) and item.get("reason"):
                reasons.add(str(item["reason"]))
        for key in ("reason", "status"):
            if isinstance(detail.get(key), str):
                reasons.add(detail[key])
    if reasons & {"uploadLimitExceeded"}:
        return _HINT_UPLOAD_LIMIT
    if reasons & {"quotaExceeded", "dailyLimitExceeded", "RESOURCE_EXHAUSTED"}:
        return _HINT_QUOTA
    return _error(data, fallback)


# ---------------------------------------------------------------------------
# Configuration and connection state
# ---------------------------------------------------------------------------

def _client() -> dict:
    return db.kv_get(K_CLIENT) or {}


def _forget_all() -> None:
    db.kv_set(K_ACCOUNTS, {"items": {}, "active": ""})
    db.kv_set(K_TOKENS, {})
    db.kv_set(K_CHANNEL, {})


def _save_state(state: dict) -> None:
    db.kv_set(K_ACCOUNTS, state)


def _state() -> dict:
    """Every connected channel, keyed by its YouTube channel id."""
    raw = db.kv_get(K_ACCOUNTS)
    stored = raw if isinstance(raw, dict) else {}
    items = stored.get("items")
    state = {
        "items": items if isinstance(items, dict) else {},
        "active": str(stored.get("active") or ""),
    }
    _adopt_legacy(state)
    return state


def _adopt_legacy(state: dict) -> None:
    """Fold a pre-multi-channel connection into the account list exactly once."""
    tokens = db.kv_get(K_TOKENS) or {}
    if not tokens.get("refresh_token"):
        return
    channel = db.kv_get(K_CHANNEL) or {}
    account_id = str(channel.get("id") or "").strip() or f"account-{db.new_id()}"
    state["items"].setdefault(account_id, {
        "id": account_id,
        "channel": {
            "id": account_id,
            "title": str(channel.get("title") or "YouTube channel"),
            "thumbnail": str(channel.get("thumbnail") or ""),
        },
        "tokens": tokens,
        "added_at": time.time(),
    })
    if not state.get("active"):
        state["active"] = account_id
    _save_state(state)
    db.kv_set(K_TOKENS, {})
    db.kv_set(K_CHANNEL, {})


def accounts() -> list[dict]:
    return list(_state()["items"].values())


def _account(account_id: str | None = None) -> dict:
    """The channel to act for: the one asked for, else the chosen default."""
    state = _state()
    items = state["items"]
    wanted = (account_id or state.get("active") or "").strip()
    if wanted and wanted in items:
        return items[wanted]
    if not items:
        raise YouTubeError("Not connected to YouTube \u2014 connect your channel in Settings.")
    if wanted:
        raise YouTubeError("That YouTube channel isn't connected any more \u2014 pick another one.")
    return next(iter(items.values()))


def set_active(account_id: str) -> dict:
    """Choose which channel one-click uploads post to."""
    state = _state()
    account_id = (account_id or "").strip()
    if account_id not in state["items"]:
        raise YouTubeError("That YouTube channel isn't connected.")
    state["active"] = account_id
    _save_state(state)
    return status()


_ID_RE = re.compile(r"[0-9][0-9A-Za-z._\-]*\.apps\.googleusercontent\.com")
_SECRET_RE = re.compile(r"GOCSPX-[0-9A-Za-z._\-]+")
_LABELS = {"client", "id", "secret", "oauth", "json", "download", "copy", "value", "your", "google", "cloud"}


def parse_client(raw: str | None) -> tuple[str, str, str]:
    """Pull (client_id, client_secret, kind) out of whatever the console gave you.

    Takes the downloaded ``client_secret_*.json``, a ``client_id=…&client_secret=…``
    blob, or the console's copy block with the two values on separate lines \u2014 so
    the user pastes one thing instead of hunting for two fields.
    """
    text = (raw or "").strip().lstrip("\ufeff")
    if not text:
        return "", "", ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise YouTubeError(
                "That JSON didn't read \u2014 paste the whole client_secret_\u2026.json file, or use the manual fields."
            ) from exc
        if not isinstance(data, dict):
            raise YouTubeError("That JSON isn't a Google OAuth client file.")
        kind = "installed" if isinstance(data.get("installed"), dict) else "web" if isinstance(data.get("web"), dict) else ""
        node = data.get("installed") or data.get("web") or data
        if not isinstance(node, dict):
            raise YouTubeError("That JSON isn't a Google OAuth client file.")
        return (
            str(node.get("client_id") or node.get("clientId") or "").strip(),
            str(node.get("client_secret") or node.get("clientSecret") or "").strip(),
            kind,
        )
    if "client_id" in text:
        pairs = dict(urllib.parse.parse_qsl(re.sub(r"[\r\n]+", "&", text.strip().strip("&")), keep_blank_values=True))
        if pairs.get("client_id"):
            return pairs["client_id"].strip(), (pairs.get("client_secret") or "").strip(), ""
    match = _ID_RE.search(text)
    cid = match.group(0) if match else ""
    rest = text.replace(cid, " ") if cid else text
    secret_match = _SECRET_RE.search(rest)
    if secret_match:
        return cid, secret_match.group(0), ""
    for token in re.findall(r"[0-9A-Za-z._\-]{16,}", rest):
        if token.lower() in _LABELS or token.endswith(".googleusercontent.com"):
            continue
        return cid, token, ""
    return cid, "", ""


def save_client(
    client_id: str | None = None,
    client_secret: str | None = None,
    blob: str | None = None,
) -> dict:
    """Store the OAuth client the user created in their own Google Cloud project.

    A blank secret keeps the stored one, so the settings form can be reopened
    without forcing the secret back out of Google's console.
    """
    current = _client()
    cid = (client_id or "").strip().strip('"')
    secret = (client_secret or "").strip().strip('"')
    kind = ""
    if blob and blob.strip():
        parsed_id, parsed_secret, parsed_kind = parse_client(blob)
        cid = cid or parsed_id
        secret = secret or parsed_secret
        kind = parsed_kind
    cid = cid or current.get("client_id") or ""
    secret = secret or current.get("client_secret") or ""
    if not cid:
        raise YouTubeError(
            "Paste the client_secret_\u2026.json Google gave you \u2014 it already contains the client ID and secret."
        )
    if not cid.endswith(".apps.googleusercontent.com"):
        raise YouTubeError("That client ID looks wrong \u2014 Google's end with .apps.googleusercontent.com.")
    if not secret:
        raise YouTubeError(
            "No client secret in that paste. Download the JSON from Google again (or copy the secret) and retry."
        )
    if cid != current.get("client_id"):
        # Tokens were issued to the previous client; they cannot be reused.
        _forget_all()
    db.kv_set(K_CLIENT, {
        "client_id": cid,
        "client_secret": secret,
        "kind": kind or current.get("kind") or "",
    })
    return status()


def status() -> dict:
    client = _client()
    state = _state()
    items = state["items"]
    active = state.get("active") or (next(iter(items), "") if items else "")
    current = items.get(active) or {}
    listed = [
        {
            "id": str(account.get("id") or ""),
            "title": str((account.get("channel") or {}).get("title") or "YouTube channel"),
            "thumbnail": str((account.get("channel") or {}).get("thumbnail") or ""),
            "active": str(account.get("id") or "") == active,
        }
        for account in items.values()
    ]
    return {
        "configured": bool(client.get("client_id") and client.get("client_secret")),
        "client_id": client.get("client_id") or "",
        "client_kind": client.get("kind") or "",
        "has_secret": bool(client.get("client_secret")),
        "connected": bool(listed),
        "channel": current.get("channel") or None,
        "accounts": listed,
        "active_account": active if listed else "",
        "redirect_uri": redirect_uri(),
        "scopes": list(SCOPES),
    }


def disconnect(account_id: str | None = None) -> dict:
    """Revoke one channel's grant at Google, then forget it locally."""
    state = _state()
    target = (account_id or state.get("active") or "").strip()
    account = state["items"].pop(target, None)
    if account:
        tokens = account.get("tokens") or {}
        token = tokens.get("refresh_token") or tokens.get("access_token")
        if token:
            try:
                _http(f"{REVOKE_URL}?token={urllib.parse.quote(str(token))}", method="POST", timeout=15)
            except YouTubeError:
                pass  # disconnecting must work even offline
    if target == state.get("active"):
        state["active"] = next(iter(state["items"]), "")
    _save_state(state)
    return status()


# ---------------------------------------------------------------------------
# OAuth 2.0 (loopback redirect + PKCE)
# ---------------------------------------------------------------------------

def auth_url() -> str:
    client = _client()
    if not (client.get("client_id") and client.get("client_secret")):
        raise YouTubeError("Add your Google OAuth client ID and secret first.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    db.kv_set(K_PENDING, {"state": state, "verifier": verifier, "at": time.time()})
    query = urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        # prompt=consent is what makes a repeat sign-in return a refresh token.
        # select_account forces Google's account chooser, and with YouTube scopes
        # that chooser is also the brand-account channel picker: without it the
        # browser silently reuses the last identity, so a second channel can
        # never be picked and the only apparent remedy looks like the console.
        "prompt": "consent select_account",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{AUTH_URL}?{query}"


def complete(code: str, state: str) -> dict:
    """Exchange the callback code for tokens and remember the channel."""
    pending = db.kv_get(K_PENDING) or {}
    db.kv_set(K_PENDING, {})
    if not pending or not state or state != pending.get("state"):
        raise YouTubeError("This sign-in has already been used or was not started here — press Connect again.")
    if time.time() - float(pending.get("at") or 0) > 900:
        raise YouTubeError("The sign-in took too long — press Connect again.")
    client = _client()
    status_code, _, raw = _http(
        TOKEN_URL,
        method="POST",
        body=urllib.parse.urlencode({
            "code": code,
            "client_id": client.get("client_id") or "",
            "client_secret": client.get("client_secret") or "",
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
            "code_verifier": pending.get("verifier") or "",
        }).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    data = _json(raw)
    if status_code != 200 or not data.get("access_token"):
        hint = oauth_error_hint(str(data.get("error") or ""))
        raise YouTubeError(hint or _error(data, "Google rejected the sign-in."))
    fresh = {
        "access_token": data.get("access_token") or "",
        "refresh_token": data.get("refresh_token") or "",
        "expires_at": time.time() + max(0.0, float(data.get("expires_in") or 0)) - 60,
        "scope": data.get("scope") or "",
    }
    channel = fetch_channel(fresh)
    state = _state()
    account_id = str(channel.get("id") or "").strip() or f"account-{db.new_id()}"
    previous = state["items"].get(account_id) or {}
    # Signing in to the same channel again keeps its stored refresh token when
    # Google sends none, so a reconnect never leaves it half-broken.
    fresh["refresh_token"] = (
        fresh["refresh_token"] or (previous.get("tokens") or {}).get("refresh_token") or ""
    )
    state["items"][account_id] = {
        "id": account_id,
        "channel": channel,
        "tokens": fresh,
        "added_at": previous.get("added_at") or time.time(),
    }
    state["active"] = account_id
    _save_state(state)
    return channel


def _store_tokens(data: dict, account_id: str) -> None:
    state = _state()
    account = state["items"].get(account_id)
    if not account:
        return
    previous = account.get("tokens") or {}
    account["tokens"] = {
        "access_token": data.get("access_token") or "",
        # A refresh only returns a new access token; keep the stored refresh one.
        "refresh_token": data.get("refresh_token") or previous.get("refresh_token") or "",
        "expires_at": time.time() + max(0.0, float(data.get("expires_in") or 0)) - 60,
        "scope": data.get("scope") or previous.get("scope") or "",
    }
    _save_state(state)


def _access_token(account_id: str | None = None, force: bool = False) -> str:
    account = _account(account_id)
    account_id = str(account.get("id") or "")
    tokens = account.get("tokens") or {}
    if not force and tokens.get("access_token") and float(tokens.get("expires_at") or 0) > time.time():
        return str(tokens["access_token"])
    refresh = tokens.get("refresh_token")
    client = _client()
    if not refresh or not client.get("client_id"):
        raise YouTubeError("Not connected to YouTube — connect your channel in Settings.")
    status_code, _, raw = _http(
        TOKEN_URL,
        method="POST",
        body=urllib.parse.urlencode({
            "client_id": client["client_id"],
            "client_secret": client.get("client_secret") or "",
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        }).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    data = _json(raw)
    if status_code != 200 or not data.get("access_token"):
        if data.get("error") == "invalid_grant":
            # An OAuth consent screen in "Testing" mode expires refresh tokens
            # after 7 days; the user reconnects rather than us retrying forever.
            state = _state()
            gone = state["items"].pop(account_id, None)
            if state.get("active") == account_id:
                state["active"] = next(iter(state["items"]), "")
            _save_state(state)
            name = ((gone or {}).get("channel") or {}).get("title") or "that channel"
            raise YouTubeError(f"YouTube access expired for {name} — reconnect it in Settings.")
        raise YouTubeError(_error(data, "Google would not renew the YouTube sign-in."))
    _store_tokens(data, account_id)
    return str(data["access_token"])


def fetch_channel(tokens: dict | None = None) -> dict:
    token = str((tokens or {}).get("access_token") or "") or _access_token()
    status_code, _, raw = _http(
        f"{CHANNEL_URL}?part=snippet&mine=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = _json(raw)
    if status_code != 200:
        raise YouTubeError(_error(data, "Could not read your YouTube channel."))
    items = data.get("items") or []
    if not items:
        raise YouTubeError("That Google account has no YouTube channel yet — create one, then connect again.")
    snippet = items[0].get("snippet") or {}
    thumbs = snippet.get("thumbnails") or {}
    return {
        "id": items[0].get("id") or "",
        "title": snippet.get("title") or "YouTube channel",
        "thumbnail": (thumbs.get("default") or {}).get("url") or "",
    }


# ---------------------------------------------------------------------------
# Upload (resumable protocol)
# ---------------------------------------------------------------------------

_TITLE_BAD = re.compile(r"[<>\"]")


def _clean_title(title: str) -> str:
    # YouTube rejects < and > in titles and caps them at 100 characters.
    text = _TITLE_BAD.sub("", title or "").strip()
    return (text or "Clip")[:100]


def upload_clip(
    job: dict,
    progress_cb: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    """Upload the clip named by this job row; returns the new video id."""
    payload = job.get("payload") or {}
    clip_id = payload.get("clip_id")
    clip = db.get("clips", clip_id) if clip_id else None
    if not clip:
        raise YouTubeError("Clip not found — it may have been deleted.")
    path = clip.get("path")
    if not path or not Path(path).exists():
        raise YouTubeError("Render this clip before uploading it.")
    size = Path(path).stat().st_size
    if size <= 0:
        raise YouTubeError("The rendered file is empty — re-render the clip first.")

    privacy = payload.get("privacy")
    # One job carries the channel it posts to; without one it uses the default.
    account = _account(str(payload.get("account") or "") or None)
    account_id = str(account.get("id") or "")
    snippet = {
        "title": _clean_title(payload.get("title") or clip.get("title") or ""),
        "description": (payload.get("description") or "")[:4900],
        "categoryId": CATEGORY_ID,
    }
    tags = metadata.clean_tags(payload.get("tags"))
    if tags:
        snippet["tags"] = tags
    body = {
        "snippet": snippet,
        "status": {
            "privacyStatus": privacy if privacy in PRIVACY else "private",
            "selfDeclaredMadeForKids": False,
        },
    }

    token = _access_token(account_id)
    if progress_cb:
        progress_cb(0.02, f"starting the upload to {((account.get('channel') or {}).get('title') or 'YouTube')}")
    session = _start_session(token, body, size)
    try:
        return _send_file(session, token, Path(path), size, progress_cb, should_cancel, account_id)
    except YouTubeError as exc:
        # A reply lost on the wire still leaves the video on the channel: Google
        # finished it and only the answer went missing. Adopt that video rather
        # than reporting a failure and posting the same clip twice later.
        if str(exc).startswith("Can't reach Google"):
            adopted = recent_upload(snippet["title"], account_id)
            if adopted:
                return adopted
        raise


def recent_upload(title: str, account_id: str | None = None, within: float = 1800.0) -> str:
    """Id of a just-uploaded video with this exact title, or "".

    Only used to rescue an upload whose response was lost, so the window is
    short and the title has to match exactly \u2014 a genuine re-upload hours
    later stays a separate video.
    """
    try:
        channel = _account(account_id).get("channel") or {}
    except YouTubeError:
        return ""
    cid = str(channel.get("id") or "")
    if not cid.startswith("UC") or not (title or "").strip():
        return ""
    query = urllib.parse.urlencode({
        "part": "snippet",
        "playlistId": f"UU{cid[2:]}",  # the channel's uploads playlist
        "maxResults": "10",
    })
    try:
        status_code, _, raw = _http(
            f"{PLAYLIST_URL}?{query}",
            headers={"Authorization": f"Bearer {_access_token(str(account_id) or None)}"},
        )
    except YouTubeError:
        return ""
    if status_code != 200:
        return ""
    cutoff = time.time() - within
    for item in _json(raw).get("items") or []:
        snippet = item.get("snippet") or {}
        if str(snippet.get("title") or "") != title:
            continue
        try:
            stamp = datetime.fromisoformat(
                str(snippet.get("publishedAt") or "").replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            stamp = time.time()
        if stamp >= cutoff:
            return str((snippet.get("resourceId") or {}).get("videoId") or "")
    return ""


def _start_session(token: str, metadata: dict, size: int) -> str:
    """Open a resumable session and return its upload URL."""
    status_code, headers, raw = _http(
        f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
        method="POST",
        body=json.dumps(metadata).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        },
    )
    location = headers.get("Location") or headers.get("location") or ""
    if status_code not in (200, 201) or not location:
        raise YouTubeError(_api_error(_json(raw), "YouTube refused to start the upload."))
    return str(location)


def _resume_offset(headers: dict, fallback: int, size: int) -> int:
    """Bytes Google reports it already stored, taken from a 308 Range header."""
    for key, value in headers.items():
        if key.lower() != "range":
            continue
        match = re.search(r"bytes=0-(\d+)", str(value))
        if match:
            return min(size, int(match.group(1)) + 1)
    return min(size, fallback)


def _send_file(
    session: str,
    token: str,
    path: Path,
    size: int,
    progress_cb: Callable[[float, str], None] | None,
    should_cancel: Callable[[], bool] | None,
    account_id: str | None = None,
) -> str:
    offset = 0
    retries = 0
    auth_retries = 0
    with path.open("rb") as handle:
        while offset < size:
            if should_cancel and should_cancel():
                raise YouTubeError("Upload canceled.")
            handle.seek(offset)
            chunk = handle.read(min(CHUNK_BYTES, size - offset))
            if not chunk:
                raise YouTubeError("Could not read the rendered file.")
            status_code, headers, raw = _http(
                session,
                method="PUT",
                body=chunk,
                timeout=180,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{size}",
                },
            )
            if status_code in (200, 201):
                video_id = str(_json(raw).get("id") or "")
                if not video_id:
                    raise YouTubeError("YouTube accepted the upload but returned no video id.")
                if progress_cb:
                    progress_cb(1.0, "uploaded")
                return video_id
            if status_code == 308:  # resume incomplete: more chunks expected
                offset = _resume_offset(headers, offset + len(chunk), size)
                retries = 0
                if progress_cb:
                    progress_cb(offset / size, (
                        f"uploading {int(offset * 100 / size)}% · "
                        f"{offset / 1e6:.0f} of {size / 1e6:.0f} MB"
                    ))
                continue
            if status_code == 401:  # token expired mid-upload; same session is fine
                # A persistently rejected token must fail, not loop forever.
                auth_retries += 1
                if auth_retries > 3:
                    raise YouTubeError(
                        "YouTube kept rejecting the upload sign-in — reconnect your channel in Settings."
                    )
                token = _access_token(account_id, force=True)
                continue
            if status_code in (429, 500, 502, 503, 504):
                # Re-sending the chunk is safe: Google reports what it stored,
                # so the next 308 moves the offset past anything it kept.
                retries += 1
                if retries > 5:
                    raise YouTubeError("YouTube kept failing the upload — try again in a few minutes.")
                time.sleep(min(8.0, 1.5 ** retries))
                continue
            raise YouTubeError(_api_error(_json(raw), f"YouTube rejected the upload (HTTP {status_code})."))
    raise YouTubeError("The upload ended without YouTube confirming it — try again.")


# ---------------------------------------------------------------------------
# Browser page shown after the Google redirect
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title} · ClipForge</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#0b0d10;
         color:#e7ecf3; font:14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  .card {{ width:min(440px, calc(100vw - 48px)); border:1px solid rgba(255,255,255,.09);
           border-radius:14px; background:#12151b; padding:26px 24px; }}
  h1 {{ margin:0 0 8px; font-size:17px; font-weight:600; }}
  p {{ margin:0; color:#9aa4b2; font-size:13px; }}
  .row {{ display:flex; align-items:center; gap:10px; margin-top:18px; }}
  img {{ width:36px; height:36px; border-radius:50%; background:#1d2229; }}
  .name {{ font-weight:600; }}
  button {{ margin-top:20px; border:0; border-radius:9px; padding:8px 14px; background:#4b7cff;
            color:#fff; font:inherit; cursor:pointer; }}
</style></head>
<body><div class="card">{body}</div></body></html>"""


def page(title: str, message: str, channel: dict | None = None) -> str:
    """Dark, self-contained confirmation page for the OAuth redirect."""
    parts = [f"<h1>{html.escape(title)}</h1>", f"<p>{html.escape(message)}</p>"]
    if channel:
        avatar = (
            f'<img src="{html.escape(channel.get("thumbnail") or "")}" alt="">'
            if channel.get("thumbnail") else ""
        )
        parts.append(
            f'<div class="row">{avatar}<span class="name">{html.escape(channel.get("title") or "Channel")}</span></div>'
        )
    parts.append('<button onclick="window.close()">Close this tab</button>')
    return _PAGE.format(title=html.escape(title), body="".join(parts))
