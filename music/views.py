import json
from datetime import timedelta
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie, get_token
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import AppUser, Feedback, Playlist, Recommendation, Recording
from .serializers import playlist_payload, recommendation_payload
from .services import chat as chat_service
from .services import listenbrainz
from .services import playlists as playlist_service
from .services import recommendations as recommendation_service


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _me(user):
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "listenbrainz": {
            "linked": user.has_listenbrainz,
            "username": user.lb_username,
            "synced_through": user.synced_through,
            "backfilled_to": user.backfilled_to,
        },
    }


def api_login_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"detail": "Authentication required."}, status=401)
        return view(request, *args, **kwargs)

    return wrapped


def _mbid_list(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 200:
        return None
    mbids = []
    for item in value:
        try:
            mbid = str(UUID(str(item)))
        except (ValueError, TypeError, AttributeError):
            return None
        if mbid not in mbids:
            mbids.append(mbid)
    return mbids


def _clean_history(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 10:
        return None
    cleaned = []
    for item in value:
        if not isinstance(item, dict):
            return None
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            return None
        cleaned.append({"role": role, "content": content[:4000]})
    return cleaned


@require_GET
@ensure_csrf_cookie
def csrf(request):
    return JsonResponse({"csrfToken": get_token(request)})


@require_POST
def register(request):
    data = _body(request)
    if data is None:
        return JsonResponse({"detail": "Body must be a JSON object."}, status=400)

    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return JsonResponse({"detail": "username and password are required."}, status=400)

    try:
        validate_password(password)
    except ValidationError as exc:
        return JsonResponse({"detail": exc.messages}, status=400)

    try:
        with transaction.atomic():
            user = AppUser.objects.create_user(
                username=username,
                password=password,
                email=(data.get("email") or "").strip(),
                display_name=(data.get("display_name") or "").strip(),
            )
    except IntegrityError:
        return JsonResponse({"detail": "Username or email already in use."}, status=409)

    login(request, user)
    return JsonResponse(_me(user), status=201)


@require_POST
def login_view(request):
    data = _body(request) or {}
    user = authenticate(
        request,
        username=(data.get("username") or "").strip(),
        password=data.get("password") or "",
    )
    if user is None:
        return JsonResponse({"detail": "Invalid username or password."}, status=401)
    login(request, user)
    return JsonResponse(_me(user))


@require_POST
def logout_view(request):
    logout(request)
    return HttpResponse(status=204)


@require_GET
@api_login_required
def me(request):
    return JsonResponse(_me(request.user))


@require_http_methods(["PUT", "DELETE"])
@api_login_required
def listenbrainz_link(request):
    if request.method == "DELETE":
        request.user.lb_username = None
        request.user.lb_token = None
        request.user.save(update_fields=["lb_username", "lb_token"])
        return HttpResponse(status=204)

    data = _body(request)
    token = ((data or {}).get("token") or "").strip()
    if not token:
        return JsonResponse({"detail": "token is required."}, status=400)

    try:
        lb_username = listenbrainz.validate_token(token)
    except listenbrainz.ListenBrainzError as exc:
        return JsonResponse({"detail": str(exc)}, status=502)
    if not lb_username:
        return JsonResponse({"detail": "ListenBrainz rejected that token."}, status=400)

    request.user.lb_username = lb_username
    request.user.lb_token = token
    try:
        with transaction.atomic():
            request.user.save(update_fields=["lb_username", "lb_token"])
    except IntegrityError:
        return JsonResponse(
            {"detail": "That ListenBrainz account is already linked to another user."},
            status=409,
        )

    return JsonResponse(_me(request.user))


@require_GET
@api_login_required
def recommendations(request):
    if not request.user.has_listenbrainz:
        return JsonResponse({"detail": "Link a ListenBrainz account first."}, status=400)

    pending = Recommendation.objects.filter(user=request.user, dismissed_at__isnull=True)
    status = "ok"
    if not pending.exists():
        last_sync = request.user.recommendations_synced_at
        if last_sync is None or last_sync < timezone.now() - timedelta(hours=1):
            try:
                status = recommendation_service.sync(request.user)["status"]
            except listenbrainz.ListenBrainzError as exc:
                return JsonResponse({"detail": str(exc)}, status=502)

    rows = list(
        pending.select_related("recording__release_group")
        .prefetch_related("recording__recording_artists__artist")
        .order_by("-score")[:50]
    )
    Recommendation.objects.filter(
        id__in=[row.id for row in rows], shown_at__isnull=True
    ).update(shown_at=timezone.now())
    scores = dict(
        Feedback.objects.filter(
            user=request.user, recording_id__in=[row.recording_id for row in rows]
        ).values_list("recording_id", "score")
    )
    payload = []
    for row in rows:
        item = recommendation_payload(row)
        item["feedback"] = scores.get(row.recording_id)
        payload.append(item)
    return JsonResponse({"status": status, "recommendations": payload})


@require_POST
@api_login_required
def recommendations_sync(request):
    if not request.user.has_listenbrainz:
        return JsonResponse({"detail": "Link a ListenBrainz account first."}, status=400)
    try:
        # shortcut: MusicBrainz enrichment is throttled to 1 req/sec; use the sync_recommendations command for a cold cache.
        result = recommendation_service.sync(request.user)
    except listenbrainz.ListenBrainzError as exc:
        return JsonResponse({"detail": str(exc)}, status=502)
    return JsonResponse(result)


@require_POST
@api_login_required
def recommendation_dismiss(request, recommendation_id):
    updated = Recommendation.objects.filter(
        id=recommendation_id, user=request.user
    ).update(dismissed_at=timezone.now())
    if not updated:
        return JsonResponse({"detail": "Recommendation not found."}, status=404)
    return HttpResponse(status=204)


@require_http_methods(["PUT", "DELETE"])
@api_login_required
def recording_feedback(request, mbid):
    recording = Recording.objects.filter(pk=mbid).first()
    if recording is None:
        return JsonResponse({"detail": "Recording not found."}, status=404)

    if request.method == "DELETE":
        Feedback.objects.filter(user=request.user, recording=recording).delete()
        return HttpResponse(status=204)

    data = _body(request)
    score = (data or {}).get("score")
    if isinstance(score, bool) or not isinstance(score, int) or score not in (-1, 0, 1):
        return JsonResponse({"detail": "score must be -1, 0 or 1."}, status=400)
    feedback, _ = Feedback.objects.update_or_create(
        user=request.user,
        recording=recording,
        defaults={"score": score},
    )
    return JsonResponse({"recording": str(recording.mbid), "score": feedback.score})


@require_http_methods(["GET", "POST"])
@api_login_required
def playlists(request):
    if request.method == "GET":
        rows = (
            Playlist.objects.filter(user=request.user)
            .annotate(track_count=Count("items"))
            .order_by("-updated_at")
        )
        return JsonResponse({"playlists": [playlist_payload(row) for row in rows]})

    data = _body(request)
    name = (data or {}).get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        return JsonResponse({"detail": "name is required (max 200 chars)."}, status=400)
    mbids = _mbid_list((data or {}).get("recording_mbids"))
    if mbids is None:
        return JsonResponse(
            {"detail": "recording_mbids must be a list of MBIDs."}, status=400
        )
    try:
        playlist = playlist_service.create_playlist(request.user, name.strip(), mbids)
    except playlist_service.UnknownRecordings as exc:
        return JsonResponse(
            {"detail": "Unknown recording(s).", "mbids": exc.mbids}, status=400
        )
    return JsonResponse(playlist_payload(playlist, include_items=True), status=201)


@require_http_methods(["GET", "PATCH", "DELETE"])
@api_login_required
def playlist_detail(request, playlist_id):
    playlist = Playlist.objects.filter(id=playlist_id, user=request.user).first()
    if playlist is None:
        return JsonResponse({"detail": "Playlist not found."}, status=404)

    if request.method == "GET":
        return JsonResponse(playlist_payload(playlist, include_items=True))
    if request.method == "DELETE":
        playlist.delete()
        return HttpResponse(status=204)

    data = _body(request)
    name = (data or {}).get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        return JsonResponse({"detail": "name is required (max 200 chars)."}, status=400)
    playlist.name = name.strip()
    playlist.save(update_fields=["name", "updated_at"])
    return JsonResponse(playlist_payload(playlist, include_items=True))


@require_http_methods(["POST", "PUT"])
@api_login_required
def playlist_items(request, playlist_id):
    playlist = Playlist.objects.filter(id=playlist_id, user=request.user).first()
    if playlist is None:
        return JsonResponse({"detail": "Playlist not found."}, status=404)

    data = _body(request)
    if request.method == "PUT":
        item_ids = (data or {}).get("item_ids")
        if not isinstance(item_ids, list):
            return JsonResponse({"detail": "item_ids must be a list."}, status=400)
        if not all(isinstance(item_id, int) and not isinstance(item_id, bool) for item_id in item_ids):
            return JsonResponse({"detail": "Invalid item id."}, status=400)
        try:
            playlist_service.reorder(playlist, item_ids)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse(playlist_payload(playlist, include_items=True))

    mbids = _mbid_list((data or {}).get("recording_mbids"))
    if mbids is None:
        return JsonResponse(
            {"detail": "recording_mbids must be a list of MBIDs."}, status=400
        )
    if not mbids:
        return JsonResponse({"detail": "recording_mbids is required."}, status=400)
    try:
        playlist_service.add_tracks(playlist, mbids)
    except playlist_service.UnknownRecordings as exc:
        return JsonResponse(
            {"detail": "Unknown recording(s).", "mbids": exc.mbids}, status=400
        )
    return JsonResponse(playlist_payload(playlist, include_items=True))


@require_http_methods(["DELETE"])
@api_login_required
def playlist_item_detail(request, playlist_id, item_id):
    playlist = Playlist.objects.filter(id=playlist_id, user=request.user).first()
    if playlist is None:
        return JsonResponse({"detail": "Playlist not found."}, status=404)
    deleted, _ = playlist.items.filter(id=item_id).delete()
    if not deleted:
        return JsonResponse({"detail": "Playlist item not found."}, status=404)
    playlist.save(update_fields=["updated_at"])
    return HttpResponse(status=204)


@require_POST
@api_login_required
def chat(request):
    data = _body(request)
    if data is None:
        return JsonResponse({"detail": "Body must be a JSON object."}, status=400)
    message = data.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 2000:
        return JsonResponse({"detail": "message is required (max 2000 chars)."}, status=400)
    history = _clean_history(data.get("history"))
    if history is None:
        return JsonResponse(
            {"detail": "history must be a list of user/assistant messages."}, status=400
        )
    if not settings.OPENROUTER_API_KEY:
        return JsonResponse({"detail": "Chat is not configured."}, status=503)

    cache_key = f"chat-rate:{request.user.id}"
    count = cache.get(cache_key, 0)
    if count >= 20:
        return JsonResponse({"detail": "Chat limit reached, try again later."}, status=429)
    cache.set(cache_key, count + 1, timeout=3600)

    try:
        result = chat_service.reply(request.user, message.strip(), history)
    except chat_service.ChatUnavailable as exc:
        return JsonResponse({"detail": str(exc)}, status=502)
    return JsonResponse(result)
