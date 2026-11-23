from django.utils import timezone

from ..models import Recommendation
from . import listenbrainz, musicbrainz


def _mark_synced(user):
    user.recommendations_synced_at = timezone.now()
    user.save(update_fields=["recommendations_synced_at"])


def sync(user, limit=25):
    if not user.has_listenbrainz:
        raise listenbrainz.ListenBrainzError("Link a ListenBrainz account first.")

    payload = listenbrainz.fetch_recommendations(
        user.lb_username, user.lb_token, count=min(limit, 100)
    )
    if payload is None:
        _mark_synced(user)
        return {"status": "not_ready", "created": 0, "updated": 0, "skipped": 0}

    created = updated = skipped = 0
    for item in payload.get("mbids") or []:
        mbid = item.get("recording_mbid")
        if not mbid:
            skipped += 1
            continue
        try:
            recording = musicbrainz.enrich_recording(mbid)
        except musicbrainz.MusicBrainzError:
            skipped += 1
            continue
        _, was_created = Recommendation.objects.update_or_create(
            user=user,
            recording=recording,
            defaults={"score": item.get("score"), "source": "listenbrainz_cf"},
        )
        if was_created:
            created += 1
        else:
            updated += 1

    _mark_synced(user)
    return {
        "status": "ok",
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "last_updated": payload.get("last_updated"),
    }
