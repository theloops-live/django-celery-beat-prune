"""Find and remove PeriodicTask rows that CELERY_BEAT_SCHEDULE no longer defines.

django_celery_beat's DatabaseScheduler copies every entry of the beat schedule
into the database when Beat starts, with `update_or_create`. Nothing ever
deletes, so an entry removed from settings keeps its row, stays enabled and
keeps firing. This module is the missing half: it treats the settings as the
schedule and the table as a copy of it.

Rows are deleted rather than disabled. `update_or_create` never writes
`enabled`, so a row disabled by mistake would stay disabled through every later
restart; a row deleted by mistake is recreated from settings at the next start.
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Iterable

from django_celery_beat.models import PeriodicTask, PeriodicTasks

logger = logging.getLogger("django_celery_beat_prune")

# Entries DatabaseScheduler.install_default_entries writes at every start
# without them ever being in the beat schedule. Pruning them would only have
# Beat recreate them a moment later.
DEFAULT_ENTRY_NAMES = frozenset({"celery.backend_cleanup"})


@dataclass(frozen=True)
class Options:
    """How pruning behaves. Read from settings by `options_for(app)`."""

    keep: tuple[str, ...] = ()
    """Names or shell-style patterns (`admin-*`) never pruned."""

    dry_run: bool = False
    """Log what would be removed and remove nothing."""

    prune_one_off: bool = False
    """Also remove one-off rows. Off by default: those are nearly always
    created from code, for a single run, and are not meant to be in settings."""


@dataclass
class Result:
    removed: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    dry_run: bool = False


def _setting(app, name: str, default):
    """Read `name` from the Celery config, then from Django's `CELERY_<NAME>`.

    A Celery app configured with `namespace="CELERY"` reads its own settings
    from Django, but a key Celery does not know about is not guaranteed to be
    mapped, so the Django setting is consulted directly as well.
    """
    value = app.conf.get(name)
    if value is not None:
        return value
    try:
        from django.conf import settings

        return getattr(settings, f"CELERY_{name.upper()}", default)
    except Exception:  # settings not configured
        return default


def options_for(app) -> Options:
    keep = _setting(app, "beat_prune_keep", ()) or ()
    if isinstance(keep, str):
        keep = (keep,)
    return Options(
        keep=tuple(keep),
        dry_run=bool(_setting(app, "beat_prune_dry_run", False)),
        prune_one_off=bool(_setting(app, "beat_prune_one_off", False)),
    )


def _kept(name: str, patterns: Iterable[str]) -> bool:
    return any(name == p or fnmatch.fnmatchcase(name, p) for p in patterns)


def stale_names(app, options: Options) -> list[str] | None:
    """Rows with no entry behind them, or None when it is unsafe to decide.

    An empty beat schedule means settings failed to load, not that every task
    was removed on purpose. Reading it literally would turn a configuration
    error into a silent stop of everything scheduled, so nothing is stale then.
    """
    configured = set(app.conf.beat_schedule or ())
    if not configured:
        return None
    rows = PeriodicTask.objects.exclude(name__in=configured | DEFAULT_ENTRY_NAMES)
    if not options.prune_one_off:
        rows = rows.exclude(one_off=True)
    return sorted(n for n in rows.values_list("name", flat=True) if not _kept(n, options.keep))


def prune(app, options: Options | None = None, schedule: dict | None = None) -> Result:
    """Delete stale rows; also drop them from a scheduler's in-memory schedule.

    `schedule` is the dict a running scheduler already holds. Without removing
    the names from it, a task just deleted could fire once more before Beat's
    next full re-read of the table.
    """
    options = options or options_for(app)
    names = stale_names(app, options)
    if names is None:
        logger.warning(
            "Beat schedule is empty; not pruning, because deleting every "
            "PeriodicTask row would stop all scheduled work"
        )
        return Result(skipped_reason="empty_schedule", dry_run=options.dry_run)
    if not names:
        return Result(dry_run=options.dry_run)

    if options.dry_run:
        logger.warning(
            "Would remove %d periodic task(s) with no entry in the beat schedule "
            "(dry run): %s",
            len(names),
            ", ".join(names),
        )
        return Result(removed=names, dry_run=True)

    logger.warning(
        "Removing %d periodic task(s) with no entry in the beat schedule: %s",
        len(names),
        ", ".join(names),
    )
    PeriodicTask.objects.filter(name__in=names).delete()
    # A Beat running elsewhere re-reads the table only when this changes.
    PeriodicTasks.update_changed()

    if schedule:
        for name in names:
            schedule.pop(name, None)
    return Result(removed=names)
