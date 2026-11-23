from django.urls import path

from . import views

urlpatterns = [
    path("csrf", views.csrf, name="csrf"),
    path("auth/register", views.register, name="register"),
    path("auth/login", views.login_view, name="login"),
    path("auth/logout", views.logout_view, name="logout"),
    path("me", views.me, name="me"),
    path("me/listenbrainz", views.listenbrainz_link, name="listenbrainz-link"),
    path("recommendations", views.recommendations, name="recommendations"),
    path("recommendations/sync", views.recommendations_sync, name="recommendations-sync"),
    path(
        "recommendations/<uuid:recommendation_id>/dismiss",
        views.recommendation_dismiss,
        name="recommendation-dismiss",
    ),
    path(
        "recordings/<uuid:mbid>/feedback",
        views.recording_feedback,
        name="recording-feedback",
    ),
    path("playlists", views.playlists, name="playlists"),
    path("playlists/<uuid:playlist_id>", views.playlist_detail, name="playlist-detail"),
    path(
        "playlists/<uuid:playlist_id>/items",
        views.playlist_items,
        name="playlist-items",
    ),
    path(
        "playlists/<uuid:playlist_id>/items/<int:item_id>",
        views.playlist_item_detail,
        name="playlist-item-detail",
    ),
    path("chat", views.chat, name="chat"),
]
