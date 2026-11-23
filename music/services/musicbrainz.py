import time

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import Artist, Recording, RecordingArtist, ReleaseGroup

API_BASE = "https://musicbrainz.org/ws/2"
_last_request = 0.0


class MusicBrainzError(Exception):
    pass


def _throttle():
    global _last_request
    wait = 1.0 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _release_choice(data):
    releases = [item for item in data.get("releases") or [] if item.get("release-group")]
    if not releases:
        return None, None
    releases.sort(key=lambda item: item.get("date") or "9999")
    return releases[0], releases[0]["release-group"]


def enrich_recording(mbid):
    existing = Recording.objects.filter(pk=mbid).first()
    if existing and existing.fetched_at:
        return existing

    _throttle()
    try:
        response = requests.get(
            f"{API_BASE}/recording/{mbid}",
            params={"inc": "artist-credits+releases", "fmt": "json"},
            headers={"User-Agent": settings.MUSICBRAINZ_USER_AGENT},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MusicBrainzError(f"MusicBrainz lookup failed for {mbid}.") from exc

    release, release_group = _release_choice(data)
    release_group_obj = None
    if release_group and release_group.get("id"):
        release_group_obj, _ = ReleaseGroup.objects.update_or_create(
            mbid=release_group["id"],
            defaults={"title": release_group.get("title") or "Unknown release"},
        )

    with transaction.atomic():
        recording, _ = Recording.objects.update_or_create(
            mbid=mbid,
            defaults={
                "title": data.get("title") or "Unknown recording",
                "length_ms": data.get("length"),
                "release_group": release_group_obj,
                "caa_release_mbid": release.get("id") if release else None,
                "fetched_at": timezone.now(),
            },
        )

        recording.recording_artists.all().delete()
        for position, credit in enumerate(data.get("artist-credit") or []):
            artist_data = credit.get("artist") or {}
            if not artist_data.get("id"):
                continue
            artist, _ = Artist.objects.update_or_create(
                mbid=artist_data["id"],
                defaults={
                    "name": artist_data.get("name")
                    or credit.get("name")
                    or "Unknown artist"
                },
            )
            RecordingArtist.objects.create(
                recording=recording,
                position=position,
                artist=artist,
                credited_name=credit.get("name") or artist.name,
                join_phrase=credit.get("joinphrase") or "",
            )

    return recording
