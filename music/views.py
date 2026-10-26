import json
import urllib.error
import urllib.request
import requests
from functools import wraps

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie, get_token
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import AppUser

LISTENBRAINZ_VALIDATE_URL = "https://api.listenbrainz.org/1/validate-token"


class ListenBrainzUnavailable(Exception):
    pass


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


def _listenbrainz_username(token):
    response = requests.get(LISTENBRAINZ_VALIDATE_URL, headers={"Authorization": f"Token {token}"}, timeout=10)
    try:
        payload = response.json()
    except (OSError, ValueError) as exc:
        raise ListenBrainzUnavailable from exc
    return payload.get("user_name") if payload.get("valid") else None


@require_GET
@ensure_csrf_cookie
def csrf(request):
    print("CSRF token requested by user:", request.user)
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
        lb_username = _listenbrainz_username(token)
    except ListenBrainzUnavailable:
        return JsonResponse(
            {"detail": "ListenBrainz is unreachable, try again later."}, status=502
        )
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
