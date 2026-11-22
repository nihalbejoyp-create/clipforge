"""Write YouTube copy for a finished clip from that clip's own transcript.

No model and no API key: the analyser already picked the hook, the transcript is
on disk and the project knows where the source came from, so the title,
description and tags are built out of real sentences in the clip plus the words
that keep coming back in it. The result is deterministic — the same clip always
produces the same draft until you edit it.

What this deliberately does not do is promise reach. It writes clean,
searchable copy: a hook-first title, a description a viewer can read, and tags
and hashtags taken from what is actually said, which is what the upload form
and YouTube's own guidance ask for.
"""
from __future__ import annotations

import re
from collections import Counter

MAX_TITLE = 100
MAX_DESCRIPTION = 4900
MAX_TAGS = 18
MAX_TAG_CHARS = 40
# YouTube counts the whole tag list against a 500-character budget per video.
MAX_TAG_TOTAL = 470

# Words that carry no topic. A word only becomes a tag when it repeats or shows
# up in the hook, so this list can stay short.
STOPWORDS = frozenset(
    """
    a about above actually after again against all almost along already also although always am among an and
    another any anyone anything anyway are around as at away back be became because become been before began
    behind being below best better between both but by came can cannot could did didn do does doing done down
    during each either else enough even ever every everything few for from further get gets getting go goes
    going gone got gotta had has have having he her here hers herself him himself his how however i if in into
    is it its itself just keep kind knew know last least less let like little look lot made make makes making
    many may maybe me mean might mine more most much must my myself near need never new next no nobody none nor
    not nothing now of off often oh okay on once one only or other others otherwise our ours out over own part
    perhaps put quite rather really right said same saw say says see seem seen she should since so some
    somebody something sometimes still such sure take talk tell than thank thanks that the their theirs them
    then there these they thing things think this those though through time to today together too took toward
    try two under until up upon us use used very want was way we well went were what whatever when where whether
    which while who whole whom whose why will with within without won would yeah yes yet you your yours yourself
    um uh uhh erm hmm ah eh gonna wanna kinda sorta
    """.split()
)

_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
_UNSAFE_TITLE = re.compile(r"[<>]")
_UNSAFE_TAG = re.compile(r"[^0-9A-Za-z\u00c0-\uffff _'-]")
# "um, so, okay then" at the front of a spoken line is not a hook.
_LEAD_FILLER = re.compile(
    r"^(?:(?:um+|uh+|erm|hmm|ah|oh|okay|ok|so|well|right|anyway|yeah|hey|like)\b[ ,.!-]*)+",
    re.IGNORECASE,
)
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
# Weak words still repeat in speech but say nothing about the topic, so they are
# kept out of both the tag list and the hashtags.
WEAK = frozenset(
    """
    great good best better nice cool pretty crazy insane awesome amazing liked likes looks look idea ideas
    trying saying stop shut literally actually honestly basically totally seriously guys bro dude girl boy
    yeah okay alright fine lot lots thing things stuff way ways person people somewhere something
    """.split()
)
# A spoken hook cut with "…" usually ends on a dangling word; dropping it leaves
# a title that reads finished.
_DANGLING = frozenset(
    """
    a an the and but or nor so yet for of to in on at by with from as is are was were be been being am do does
    did have has had that this these those it its i you your he she they we my our their his her one ones just
    like than then when while if because about into over after before there here what which who whom whose
    """.split()
)


def _squash(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "").translate(_QUOTES)).strip()


def _sentences(text: object) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", _squash(text))
    return [p.strip(" -–—,;:") for p in parts if p.strip(" -–—,;:")]


def _fit(text: str, limit: int) -> str:
    """Cut on a word boundary instead of mid-word when a line is too long."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.5:
        cut = cut[:space]
    return cut.rstrip(" ,;:-–—")


def _distinct_words(text: str) -> int:
    return len({w.lower() for w in text.split()})


def _dedupe_repeats(sentences: list[str]) -> list[str]:
    """Whisper repeats a line when the audio loops; keep it once."""
    out: list[str] = []
    for sentence in sentences:
        key = sentence.lower().rstrip(".?! ")
        if out and out[-1].lower().rstrip(".?! ") == key:
            continue
        out.append(sentence)
    return out


def _usable(word: str) -> bool:
    # Possessives and contractions ("you're", "i'll") read as noise in a tag list.
    return (
        len(word) >= 3
        and not word.isdigit()
        and "'" not in word
        and "’" not in word
        and word not in STOPWORDS
        and word not in WEAK
    )


def _drop_dangling(text: str) -> str:
    words = text.split()
    while len(words) > 3 and words[-1].lower().strip(",.;:-–—”\"") in _DANGLING:
        words.pop()
    return " ".join(words).rstrip(" ,;:-–—")


def title_for(clip: dict, project: dict | None = None) -> str:
    """Hook-first title: the analyser's own pick, cleaned of spoken filler."""
    project = project or {}
    name = _squash(project.get("name") or project.get("title") or "Clip")
    raw = _squash(clip.get("title"))
    if not raw:
        sentences = _sentences(clip.get("text"))
        raw = sentences[0] if sentences else ""
    if not raw:
        raw = name

    # The analyser stores a hook truncated with an ellipsis; a half sentence
    # reads like a bug on YouTube, so drop it and keep the complete part.
    raw = _UNSAFE_TITLE.sub("", raw.replace("…", " ").replace("...", " "))
    raw = _LEAD_FILLER.sub("", raw)
    raw = _squash(raw).strip(" -–—,;:")
    if raw and raw[-1] not in ".!?”\"":
        complete = _sentences(raw)
        if len(complete) > 1 and 20 <= len(" ".join(complete[:-1])) <= MAX_TITLE:
            raw = " ".join(complete[:-1]).strip(" -–—,;:")
        elif raw.rstrip()[-1:] not in ".!?”\"":
            raw = _drop_dangling(raw.rstrip())

    # Spoken hooks are long; the first whole sentence is the punchiest thing we
    # have when the clip title runs past a readable length.
    if len(raw) > 70:
        first = _sentences(raw)
        if first and 30 <= len(first[0]) <= 90:
            raw = first[0]

    raw = _fit(raw, MAX_TITLE).strip(" -–—,;:")
    # A hallucinated or wordless transcript ("na na na na") is not a title.
    if len(raw) < 8 or _distinct_words(raw) < 3:
        idx = int(clip.get("idx") or 0) + 1
        raw = f"{name} · clip {idx}"
    raw = _fit(raw, MAX_TITLE)
    return raw[:1].upper() + raw[1:] if raw[:1].islower() else raw


def keywords_for(clip: dict, project: dict | None = None, limit: int = MAX_TAGS) -> list[str]:
    """Topic words the clip actually keeps saying, best first."""
    project = project or {}
    text = " ".join([_squash(clip.get("text")), _squash(clip.get("title"))])
    tokens = [t.lower() for t in _WORD.findall(text)]
    if not tokens:
        return ["shorts"]
    title_tokens = {t.lower() for t in _WORD.findall(_squash(clip.get("title")))}
    name = _squash(project.get("name") or project.get("title"))
    name_tokens = {t.lower() for t in _WORD.findall(name)}

    counts = Counter(t for t in tokens if _usable(t))
    scored: list[tuple[float, str]] = []
    for word, count in counts.items():
        if count < 2 and word not in title_tokens and word not in name_tokens:
            continue
        score = float(count)
        if word in title_tokens:
            score += 4
        if word in name_tokens:
            score += 2
        scored.append((score, word))

    # "yellow cars" is a better tag than "yellow" or "cars" alone when the pair
    # is said more than once.
    phrases = Counter()
    for first, second in zip(tokens, tokens[1:]):
        if _usable(first) and _usable(second) and first != second:
            phrases[f"{first} {second}"] += 1
    for phrase, count in phrases.items():
        if count >= 2:
            scored.append((count * 1.6 + 1, phrase))

    scored.sort(key=lambda pair: (-pair[0], -len(pair[1]), pair[1]))
    tags: list[str] = []
    for _, tag in scored:
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= limit - 2:
            break
    if name and 3 <= len(name) <= 30 and name.lower() not in tags:
        tags.append(name)
    if "shorts" not in tags:
        tags.append("shorts")
    return clean_tags(tags, limit=limit)


def hashtags_for(tags: list[str], limit: int = 4) -> list[str]:
    out = ["#shorts"]
    for tag in tags:
        clean = re.sub(r"[^\w]", "", tag, flags=re.UNICODE)
        if len(clean) < 3 or len(clean) > 24:
            continue
        handle = f"#{clean.lower()}"
        if handle not in out:
            out.append(handle)
        if len(out) > limit:
            break
    return out[: limit + 1]


def description_for(clip: dict, project: dict | None, tags: list[str]) -> str:
    project = project or {}
    sentences = _dedupe_repeats(_sentences(clip.get("text")))
    hook = _fit(" ".join(sentences[:3]), 320)
    if hook[:1].islower():
        hook = hook[0].upper() + hook[1:]
    lines: list[str] = []
    if hook:
        lines.append(hook)

    name = _squash(project.get("name") or project.get("title"))
    source = _squash(project.get("source_url"))
    context: list[str] = []
    if name:
        context.append(f'From "{name}".')
    if source.lower().startswith("http"):
        # Drop share tracking ("?si=…") — it is noise in a published caption.
        context.append(f"Full video: {source.split('?')[0]}")
    if context:
        lines.append("")
        lines.extend(context)

    hashtags = hashtags_for(tags)
    if hashtags:
        lines.extend(["", " ".join(hashtags)])
    return "\n".join(lines).strip()[:MAX_DESCRIPTION]


def clean_tags(values: object, limit: int = MAX_TAGS) -> list[str]:
    """Normalise hand-edited tags: no hashes, no duplicates, inside the budget."""
    tags: list[str] = []
    seen: set[str] = set()
    total = 0
    for value in values if isinstance(values, (list, tuple)) else []:
        tag = _squash(value).lstrip("#").strip()
        tag = _squash(_UNSAFE_TAG.sub(" ", tag))
        if len(tag) < 2:
            continue
        tag = _fit(tag, MAX_TAG_CHARS)
        key = tag.lower()
        if key in seen or tag.lower() in STOPWORDS:
            continue
        if total + len(tag) + 1 > MAX_TAG_TOTAL or len(tags) >= limit:
            break
        seen.add(key)
        tags.append(tag)
        total += len(tag) + 1
    return tags


def draft(clip: dict, project: dict | None = None) -> dict:
    """Title, description and tags for one clip, ready to review or edit."""
    title = title_for(clip, project)
    tags = keywords_for(clip, project)
    return {
        "title": title,
        "description": description_for(clip, project, tags),
        "tags": tags,
    }
