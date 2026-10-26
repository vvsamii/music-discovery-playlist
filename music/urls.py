from django.urls import path

from . import views

urlpatterns = [
    path("csrf", views.csrf, name="csrf"),
    path("auth/register", views.register, name="register"),
    path("auth/login", views.login_view, name="login"),
    path("auth/logout", views.logout_view, name="logout"),
    path("me", views.me, name="me"),
    path("me/listenbrainz", views.listenbrainz_link, name="listenbrainz-link"),
]
