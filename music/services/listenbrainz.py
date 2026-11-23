from urllib.parse import quote

import requests
from django.conf import settings

API_BASE = "https://api.listenbrainz.org"


class ListenBrainzError(Exception):
    pass


def _headers(token=None):
    headers = {"User-Agent": settings.MUSICBRAINZ_USER_AGENT}
    if token:
        headers["Authorization"] = f"Token {token}"
    return headers


def validate_token(token):
    try:
        response = requests.get(
            f"{API_BASE}/1/validate-token", headers=_headers(token), timeout=10
        )
        if response.status_code == 401:
            return None
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ListenBrainzError("ListenBrainz is unreachable, try again later.") from exc
    return payload.get("user_name") if payload.get("valid") else None


def fetch_recommendations(username, token=None, count=25, offset=0):
    try:
        response = requests.get(
            f"{API_BASE}/1/cf/recommendation/user/{quote(username, safe='')}/recording",
            params={"count": count, "offset": offset},
            headers=_headers(token),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise ListenBrainzError("ListenBrainz is unreachable, try again later.") from exc

    if response.status_code == 204:
        return None
    if response.status_code == 404:
        raise ListenBrainzError("ListenBrainz user not found.")
    if response.status_code == 401:
        raise ListenBrainzError("ListenBrainz rejected the linked token.")
    if response.status_code == 429:
        raise ListenBrainzError("ListenBrainz is rate limiting, try again later.")

    try:
        response.raise_for_status()
        return response.json().get("payload", {})
    except (requests.RequestException, ValueError) as exc:
        raise ListenBrainzError("ListenBrainz returned an invalid response.") from exc
