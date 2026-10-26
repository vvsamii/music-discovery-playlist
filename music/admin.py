from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AppUser


@admin.register(AppUser)
class AppUserAdmin(UserAdmin):

    fieldsets = UserAdmin.fieldsets + (
        ("ListenBrainz", {"fields": ("display_name", "lb_username",
                                     "synced_through", "backfilled_to")}),
    )
    list_display = ("username", "email", "lb_username", "is_staff")
    search_fields = ("username", "email", "lb_username")