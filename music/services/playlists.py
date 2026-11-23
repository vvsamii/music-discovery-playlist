from uuid import UUID

from django.db import transaction
from django.db.models import Max

from ..models import Playlist, PlaylistItem, Recording


class UnknownRecordings(Exception):
    def __init__(self, mbids):
        self.mbids = mbids
        super().__init__("Unknown recordings.")


def resolve_recordings(mbids):
    try:
        unique = list(dict.fromkeys(str(UUID(str(mbid))) for mbid in mbids))
    except (ValueError, TypeError, AttributeError) as exc:
        raise UnknownRecordings([str(mbid) for mbid in mbids]) from exc

    by_id = {str(recording.mbid): recording for recording in Recording.objects.filter(pk__in=unique)}
    missing = [mbid for mbid in unique if mbid not in by_id]
    if missing:
        raise UnknownRecordings(missing)
    return [by_id[mbid] for mbid in unique]


def create_playlist(user, name, mbids):
    recordings = resolve_recordings(mbids)
    with transaction.atomic():
        playlist = Playlist.objects.create(user=user, name=name)
        PlaylistItem.objects.bulk_create(
            [
                PlaylistItem(playlist=playlist, recording=recording, position=position)
                for position, recording in enumerate(recordings)
            ]
        )
    return playlist


def add_tracks(playlist, mbids):
    recordings = resolve_recordings(mbids)
    existing = set(playlist.items.values_list("recording_id", flat=True))
    last_position = playlist.items.aggregate(last=Max("position")).get("last")
    start = 0 if last_position is None else last_position + 1
    new_items = []
    for recording in recordings:
        if recording.pk in existing:
            continue
        new_items.append(
            PlaylistItem(
                playlist=playlist,
                recording=recording,
                position=start + len(new_items),
            )
        )
        existing.add(recording.pk)

    PlaylistItem.objects.bulk_create(new_items)
    if new_items:
        playlist.save(update_fields=["updated_at"])
    return playlist


def reorder(playlist, item_ids):
    items = {item.id: item for item in playlist.items.all()}
    if len(item_ids) != len(items) or set(item_ids) != set(items):
        raise ValueError("item_ids must list every playlist item exactly once.")

    with transaction.atomic():
        for position, item_id in enumerate(item_ids):
            items[item_id].position = position
        PlaylistItem.objects.bulk_update(items.values(), ["position"])
    playlist.save(update_fields=["updated_at"])
    return playlist
