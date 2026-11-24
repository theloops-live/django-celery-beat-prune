"""The schedule in the database has to stay settings-shaped.

Stock DatabaseScheduler only adds and updates rows, so what is worth testing is
the part this package adds: an entry deleted from CELERY_BEAT_SCHEDULE stops
running. The rest guards the ways pruning could go wrong - deleting Celery's
own default entry, emptying the table when settings failed to load, or eating
rows someone created on purpose.
"""
import logging
from datetime import timedelta

import pytest
from celery.schedules import crontab
from django.core.management import CommandError, call_command
from django.test import override_settings
from django_celery_beat.models import ClockedSchedule, IntervalSchedule, PeriodicTask

from django_celery_beat_prune import DEFAULT_ENTRY_NAMES, Options, PruningScheduler, options_for, prune
from tests.celery_app import app

pytestmark = pytest.mark.django_db

CLEANUP = "celery.backend_cleanup"
KEPT = {"task": "tests.heartbeat", "schedule": timedelta(minutes=1)}
DROPPED = {"task": "tests.heartbeat", "schedule": timedelta(hours=6)}


@pytest.fixture
def schedule():
    """Set CELERY_BEAT_SCHEDULE for the rest of the test.

    Assigning to app.conf.beat_schedule looks like it works and does not:
    Celery's config is a ChainMap and the Django settings layer, where
    CELERY_BEAT_SCHEDULE lives, is consulted first. Overriding the setting is
    also the honest test, because settings is what Beat reads.
    """
    overrides = []

    def use(value, **extra):
        override = override_settings(CELERY_BEAT_SCHEDULE=value, **extra)
        override.enable()
        overrides.append(override)

    yield use
    for override in reversed(overrides):
        override.disable()


def start_beat(schedule, value, **extra):
    """What Beat does at start: build the scheduler and set the schedule up."""
    schedule(value, **extra)
    scheduler = PruningScheduler(app=app, lazy=True)
    # DatabaseScheduler registers an at-exit sync() that would touch the
    # database after pytest-django has closed it; a test has nothing to sync.
    scheduler._finalize.cancel()
    scheduler.setup_schedule()
    return scheduler


def names():
    return set(PeriodicTask.objects.values_list("name", flat=True))


def add_row(name, **fields):
    interval, _ = IntervalSchedule.objects.get_or_create(every=10, period=IntervalSchedule.MINUTES)
    return PeriodicTask.objects.create(name=name, task="tests.heartbeat", interval=interval, **fields)


class TestStockBehaviourIsKept:
    def test_every_entry_becomes_a_row(self, schedule):
        start_beat(
            schedule,
            {
                "alpha": {**KEPT, "options": {"queue": "periodic"}},
                "beta": {"task": "tests.heartbeat", "schedule": crontab(hour=7, minute=7)},
            },
        )
        assert names() == {"alpha", "beta", CLEANUP}
        assert PeriodicTask.objects.get(name="alpha").queue == "periodic"

    def test_starting_twice_changes_nothing(self, schedule):
        start_beat(schedule, {"alpha": KEPT})
        first = set(PeriodicTask.objects.values_list("id", flat=True))
        start_beat(schedule, {"alpha": KEPT})
        assert set(PeriodicTask.objects.values_list("id", flat=True)) == first


class TestPruning:
    def test_an_entry_removed_from_settings_stops_running(self, schedule):
        start_beat(schedule, {"kept": KEPT, "dropped": DROPPED})
        assert "dropped" in names()

        scheduler = start_beat(schedule, {"kept": KEPT})

        assert names() == {"kept", CLEANUP}
        # Gone from the schedule this process already holds too, so it cannot
        # fire once more before Beat next re-reads the table.
        assert "dropped" not in scheduler.schedule
        assert "kept" in scheduler.schedule

    def test_a_row_created_outside_settings_is_removed(self, schedule):
        add_row("added-in-the-admin")
        start_beat(schedule, {"kept": KEPT})
        assert names() == {"kept", CLEANUP}

    def test_celerys_own_default_entry_survives(self, schedule):
        start_beat(schedule, {"kept": KEPT})
        assert CLEANUP in names()
        assert DEFAULT_ENTRY_NAMES == {CLEANUP}

    def test_an_empty_schedule_deletes_nothing(self, schedule, caplog):
        """Empty means settings failed to load, not that everything was removed."""
        start_beat(schedule, {"kept": KEPT})
        schedule({})
        with caplog.at_level(logging.WARNING, logger="django_celery_beat_prune"):
            result = prune(app)
        assert result.skipped_reason == "empty_schedule"
        assert names() == {"kept", CLEANUP}
        assert "not pruning" in caplog.text

    def test_a_disabled_entry_still_in_settings_is_left_alone(self, schedule):
        """Disabling in the admin is the emergency lever that needs no deploy.
        Pruning must not delete the row and quietly re-enable the task."""
        start_beat(schedule, {"kept": KEPT})
        PeriodicTask.objects.filter(name="kept").update(enabled=False)
        start_beat(schedule, {"kept": KEPT})
        assert PeriodicTask.objects.get(name="kept").enabled is False

    def test_the_removal_is_logged_by_name(self, schedule, caplog):
        add_row("old-report")
        with caplog.at_level(logging.WARNING, logger="django_celery_beat_prune"):
            start_beat(schedule, {"kept": KEPT})
        assert "Removing 1 periodic task(s)" in caplog.text
        assert "old-report" in caplog.text


class TestOptions:
    def test_keep_accepts_names_and_patterns(self, schedule):
        for name in ("admin-backup", "admin-report", "manual", "stale"):
            add_row(name)
        start_beat(schedule, {"kept": KEPT}, CELERY_BEAT_PRUNE_KEEP=["admin-*", "manual"])
        assert names() == {"kept", CLEANUP, "admin-backup", "admin-report", "manual"}

    def test_keep_as_a_single_string(self, schedule):
        add_row("manual")
        start_beat(schedule, {"kept": KEPT}, CELERY_BEAT_PRUNE_KEEP="manual")
        assert "manual" in names()

    def test_dry_run_reports_and_deletes_nothing(self, schedule, caplog):
        add_row("stale")
        with caplog.at_level(logging.WARNING, logger="django_celery_beat_prune"):
            start_beat(schedule, {"kept": KEPT}, CELERY_BEAT_PRUNE_DRY_RUN=True)
        assert "stale" in names()
        assert "Would remove 1" in caplog.text

    def test_one_off_rows_are_kept_by_default(self, schedule):
        """One-off tasks are created from code for a single run; they were never
        going to be in settings."""
        clocked = ClockedSchedule.objects.create(clocked_time="2099-01-01T00:00:00Z")
        PeriodicTask.objects.create(name="send-once", task="tests.heartbeat", clocked=clocked, one_off=True)
        start_beat(schedule, {"kept": KEPT})
        assert "send-once" in names()

    def test_one_off_rows_can_be_pruned_too(self, schedule):
        clocked = ClockedSchedule.objects.create(clocked_time="2099-01-01T00:00:00Z")
        PeriodicTask.objects.create(name="send-once", task="tests.heartbeat", clocked=clocked, one_off=True)
        start_beat(schedule, {"kept": KEPT}, CELERY_BEAT_PRUNE_ONE_OFF=True)
        assert "send-once" not in names()

    def test_options_default_when_nothing_is_set(self, schedule):
        schedule({"kept": KEPT})
        assert options_for(app) == Options()


class TestCommand:
    def test_dry_run_lists_and_keeps(self, schedule, capsys):
        schedule({"kept": KEPT})
        add_row("stale")
        call_command("prune_periodic_tasks", "--dry-run")
        assert "Would remove 1 periodic task(s)" in capsys.readouterr().out
        assert "stale" in names()

    def test_removes_and_honours_keep(self, schedule, capsys):
        schedule({"kept": KEPT})
        add_row("stale")
        add_row("manual")
        call_command("prune_periodic_tasks", "--keep", "manual")
        out = capsys.readouterr().out
        assert "Removed 1 periodic task(s)" in out
        assert names() == {"manual"}

    def test_refuses_on_an_empty_schedule(self, schedule):
        schedule({})
        add_row("stale")
        with pytest.raises(CommandError):
            call_command("prune_periodic_tasks")
        assert "stale" in names()

    def test_nothing_to_prune(self, schedule, capsys):
        schedule({"kept": KEPT})
        call_command("prune_periodic_tasks")
        assert "Nothing to prune." in capsys.readouterr().out
