def recording_payload(recording):
    return {
        "mbid": str(recording.mbid),
        "title": recording.title,
        "artist": recording.credit or "Unknown artist",
        "album": recording.release_group.title if recording.release_group_id else None,
        "cover_url": recording.cover_art_url,
        "length_ms": recording.length_ms,
    }


def recommendation_payload(recommendation):
    data = recording_payload(recommendation.recording)
    data.update(
        {
            "recommendation_id": str(recommendation.id),
            "score": recommendation.score,
            "reason": recommendation.reason,
            "source": recommendation.source,
        }
    )
    return data


def playlist_payload(playlist, include_items=False):
    items = []
    if include_items:
        items = list(
            playlist.items.select_related("recording__release_group").prefetch_related(
                "recording__recording_artists__artist"
            )
        )
    track_count = len(items) if include_items else getattr(playlist, "track_count", None)
    if track_count is None:
        track_count = playlist.items.count()
    data = {
        "id": str(playlist.id),
        "name": playlist.name,
        "track_count": track_count,
        "created_at": playlist.created_at.isoformat(),
        "updated_at": playlist.updated_at.isoformat(),
    }
    if include_items:
        data["items"] = [
            {"item_id": item.id, "position": item.position, **recording_payload(item.recording)}
            for item in items
        ]
    return data
