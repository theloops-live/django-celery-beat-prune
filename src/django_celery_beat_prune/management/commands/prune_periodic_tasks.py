from celery import current_app
from django.core.management.base import BaseCommand, CommandError

from django_celery_beat_prune.core import Options, options_for, prune


class Command(BaseCommand):
    help = (
        "Delete PeriodicTask rows that have no entry in CELERY_BEAT_SCHEDULE. "
        "Run with --dry-run first to see what would go."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List the rows, delete nothing.")
        parser.add_argument(
            "--keep",
            action="append",
            default=[],
            metavar="PATTERN",
            help="A name or shell-style pattern to keep, in addition to CELERY_BEAT_PRUNE_KEEP. Repeatable.",
        )

    def handle(self, *args, dry_run=False, keep=(), **options):
        app = current_app
        configured = options_for(app)
        opts = Options(
            keep=configured.keep + tuple(keep),
            dry_run=dry_run or configured.dry_run,
            prune_one_off=configured.prune_one_off,
        )
        result = prune(app, opts)
        if result.skipped_reason == "empty_schedule":
            raise CommandError(
                "The beat schedule is empty, so nothing was pruned. Is the Celery "
                "app imported (usually from your project's __init__.py)?"
            )
        if not result.removed:
            self.stdout.write("Nothing to prune.")
            return
        verb = "Would remove" if result.dry_run else "Removed"
        self.stdout.write(f"{verb} {len(result.removed)} periodic task(s):")
        for name in result.removed:
            self.stdout.write(f"  {name}")
