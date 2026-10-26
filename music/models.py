import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import (
    Case,
    Count,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Sum,
    When,
)
from django.db.models.functions import Lower

class AppUser(AbstractUser):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    display_name = models.CharField(max_length=200, blank=True)

    lb_username = models.CharField(max_length=100, null=True, blank=True, unique=True)
    lb_token = models.CharField(max_length=200, null=True, blank=True)

    synced_through = models.DateTimeField(null=True, blank=True)
    backfilled_to = models.DateTimeField(null=True, blank=True)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="appuser_email_ci_uk",
                condition=~Q(email=""),
            ),
        ]

    def __str__(self):
        return self.display_name or self.username

    @property
    def has_listenbrainz(self):
        return bool(self.lb_username and self.lb_token)


class Artist(models.Model):
    mbid = models.UUIDField(primary_key=True)
    name = models.CharField(max_length=500)
    type = models.CharField(max_length=50, null=True, blank=True)

    fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["name"], name="artist_name_idx")]

    def __str__(self):
        return self.name


class ReleaseGroup(models.Model):

    mbid = models.UUIDField(primary_key=True)
    title = models.CharField(max_length=500)
    primary_type = models.CharField(max_length=50, null=True, blank=True)
    first_release_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.title


class Recording(models.Model):

    mbid = models.UUIDField(primary_key=True)
    title = models.CharField(max_length=500)
    length_ms = models.IntegerField(null=True, blank=True)
    release_group = models.ForeignKey(
        ReleaseGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recordings",
    )

    caa_release_mbid = models.UUIDField(null=True, blank=True)
    caa_id = models.BigIntegerField(null=True, blank=True)

    streaming_urls = models.JSONField(null=True, blank=True)

    fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["title"], name="recording_title_idx")]

    def __str__(self):
        return self.title

    @property
    def cover_art_url(self):
        if self.caa_id and self.caa_release_mbid:
            return (
                f"https://coverartarchive.org/release/"
                f"{self.caa_release_mbid}/{self.caa_id}-250.jpg"
            )
        return None

    @property
    def credit(self):
        return "".join(
            ra.credited_name + ra.join_phrase for ra in self.recording_artists.all()
        )


class RecordingArtist(models.Model):

    recording = models.ForeignKey(
        Recording, on_delete=models.CASCADE, related_name="recording_artists"
    )
    position = models.PositiveSmallIntegerField()
    artist = models.ForeignKey(
        Artist, on_delete=models.CASCADE, related_name="recording_credits"
    )
    credited_name = models.CharField(max_length=500)
    join_phrase = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["recording", "position"], name="recording_artist_position_uk"
            ),
        ]

    def __str__(self):
        return f"{self.credited_name}{self.join_phrase}"


class Tag(models.Model):

    name = models.CharField(primary_key=True, max_length=200)
    is_genre = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class RecordingTag(models.Model):
    recording = models.ForeignKey(
        Recording, on_delete=models.CASCADE, related_name="recording_tags"
    )
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="recording_tags")

    count = models.IntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["recording", "tag"], name="recording_tag_uk"
            ),
        ]
        indexes = [
            models.Index(fields=["tag", "-count"], name="recording_tag_by_tag_idx"),
        ]


class ArtistTag(models.Model):
    artist = models.ForeignKey(
        Artist, on_delete=models.CASCADE, related_name="artist_tags"
    )
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="artist_tags")
    count = models.IntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["artist", "tag"], name="artist_tag_uk"),
        ]
        indexes = [
            models.Index(fields=["tag", "-count"], name="artist_tag_by_tag_idx"),
        ]



class ListenQuerySet(models.QuerySet):
    def enriched(self):
        return self.select_related(
            "recording", "recording__release_group"
        ).prefetch_related("recording__recording_artists__artist")

    def matched(self):
        return self.filter(recording__isnull=False)

    def unmatched(self):
        return self.filter(recording__isnull=True)


class Listen(models.Model):

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="listens"
    )
    listened_at = models.DateTimeField()

    recording_msid = models.UUIDField()

    artist_name = models.CharField(max_length=500)
    track_name = models.CharField(max_length=500)
    release_name = models.CharField(max_length=500, null=True, blank=True)

    recording = models.ForeignKey(
        Recording,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_constraint=False,
        related_name="listens",
    )

    duration_ms = models.IntegerField(null=True, blank=True)

    raw = models.JSONField(null=True, blank=True)

    objects = ListenQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "listened_at", "recording_msid"],
                name="listen_dedup_uk",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-listened_at"], name="listen_user_time_idx"),
            models.Index(
                fields=["recording"],
                name="listen_recording_idx",
                condition=Q(recording__isnull=False),
            ),
            models.Index(
                fields=["recording_msid"],
                name="listen_unmapped_idx",
                condition=Q(recording__isnull=True),
            ),
        ]

    def __str__(self):
        return f"{self.artist_name} — {self.track_name}"

    @property
    def is_matched(self):
        return self.recording_id is not None

    @property
    def display_track(self):
        return self.recording.title if self.recording_id else self.track_name

    @property
    def display_artist(self):
        if self.recording_id:
            credit = self.recording.credit
            if credit:
                return credit
        return self.artist_name

    @property
    def display_album(self):
        if self.recording_id and self.recording.release_group_id:
            return self.recording.release_group.title
        return self.release_name



class Feedback(models.Model):

    LOVE = 1
    NEUTRAL = 0
    HATE = -1
    SCORE_CHOICES = [(LOVE, "Love"), (NEUTRAL, "Neutral"), (HATE, "Hate")]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="feedback"
    )
    recording = models.ForeignKey(
        Recording,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_constraint=False,
        related_name="feedback",
    )
    recording_msid = models.UUIDField(null=True, blank=True)
    score = models.SmallIntegerField(choices=SCORE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "recording"],
                name="feedback_recording_uk",
                condition=Q(recording__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["user", "recording_msid"],
                name="feedback_msid_uk",
                condition=Q(recording_msid__isnull=False),
            ),
            models.CheckConstraint(
                condition=Q(recording__isnull=False) | Q(recording_msid__isnull=False),
                name="feedback_has_identifier",
            ),
            models.CheckConstraint(
                condition=Q(score__in=[-1, 0, 1]),
                name="feedback_score_range",
            ),
        ]


class Recommendation(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recommendations"
    )
    recording = models.ForeignKey(
        Recording, on_delete=models.CASCADE, related_name="recommendations"
    )
    score = models.FloatField(null=True, blank=True)
    reason = models.CharField(max_length=200, null=True, blank=True)
    source = models.CharField(max_length=50, null=True, blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)
    shown_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["user", "-score"],
                name="recommendation_pending_idx",
                condition=Q(shown_at__isnull=True) & Q(dismissed_at__isnull=True),
            ),
            models.Index(
                fields=["user", "recording"], name="recommendation_seen_idx"
            ),
        ]


def tag_candidates_for(user, min_votes=2):

    genre_plays = (
        RecordingTag.objects.filter(
            tag__is_genre=True,
            count__gte=min_votes,
            recording__listens__user=user,
        )
        .values_list("tag_id")
        .annotate(plays=Count("recording__listens"))
    )
    weights = dict(genre_plays)
    if not weights:
        return Recording.objects.none()

    already_heard = Listen.objects.filter(user=user, recording=OuterRef("pk"))
    already_shown = Recommendation.objects.filter(user=user, recording=OuterRef("pk"))

    score = Sum(
        Case(
            *[
                When(recording_tags__tag_id=tag_id, then=F("recording_tags__count") * plays)
                for tag_id, plays in weights.items()
            ],
            default=0,
            output_field=IntegerField(),
        )
    )

    return (
        Recording.objects.filter(recording_tags__tag_id__in=weights.keys())
        .annotate(heard=Exists(already_heard), shown=Exists(already_shown))
        .filter(heard=False, shown=False)
        .annotate(score=score)
        .order_by("-score")
        .distinct()
    )