from django.core.management.base import BaseCommand, CommandError

from music.models import AppUser
from music.services import recommendations
from music.services.listenbrainz import ListenBrainzError


class Command(BaseCommand):
    help = "Fetch ListenBrainz recommendations and cache enriched recordings."

    def add_arguments(self, parser):
        parser.add_argument("username", nargs="?", help="Only sync this app username.")
        parser.add_argument("--limit", type=int, default=25)

    def handle(self, *args, **options):
        users = AppUser.objects.filter(lb_username__isnull=False, lb_token__isnull=False)
        if options["username"]:
            users = users.filter(username=options["username"])
        if not users.exists():
            raise CommandError("No linked users found.")

        for user in users:
            try:
                result = recommendations.sync(user, limit=options["limit"])
            except ListenBrainzError as exc:
                self.stderr.write(f"{user.username}: {exc}")
                continue
            self.stdout.write(f"{user.username}: {result}")
