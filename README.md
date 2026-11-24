# django-celery-beat-prune

Delete an entry from `CELERY_BEAT_SCHEDULE`, deploy, and with
[django-celery-beat](https://github.com/celery/django-celery-beat) the task
**keeps running**. `DatabaseScheduler` copies your settings into the
`PeriodicTask` table on every Beat start with `update_or_create`, and nothing
ever deletes. The row stays there and stays enabled, firing on schedule. Nothing
in the logs says so, and the deploy looks like it worked.

This package adds the missing half. Settings become the schedule, and the
table becomes a copy of it: when Beat starts, rows with no entry in
`CELERY_BEAT_SCHEDULE` are removed, and each one is named in the log.

```python
# settings.py
CELERY_BEAT_SCHEDULER = "django_celery_beat_prune.PruningScheduler"
```

That's the whole setup. Restart Beat, and any task you have removed from
settings stops.

## Install

```bash
pip install django-celery-beat-prune
```

Add the app if you want the management command:

```python
INSTALLED_APPS = [
    # ...
    "django_celery_beat",
    "django_celery_beat_prune",
]
```

Requires Python 3.10+, Django 4.2+, Celery 5.3+ and django-celery-beat 2.5+.
CI tests both the oldest and the newest of each.

## What it does, and what it deliberately doesn't

- **Rows are deleted, not disabled.** `update_or_create` never writes
  `enabled`. A row disabled by mistake would stay disabled through every later
  restart, and that task would be silently dead. A row deleted by mistake is
  recreated from settings at the next start.
- **The `enabled` flag is never touched.** Unticking *Enabled* in the admin
  keeps working as the emergency switch that needs no deploy.
- **An empty schedule prunes nothing.** If `CELERY_BEAT_SCHEDULE` comes back
  empty, settings almost certainly failed to load. Taken literally, an empty
  schedule would delete every row and stop everything, so the package logs a
  warning and leaves the table alone.
- **Celery's own `celery.backend_cleanup` is kept.** Beat recreates it at every
  start anyway.
- **One-off tasks are kept by default.** Rows with `one_off=True` are
  normally created from code for a single run and were never meant to be in
  settings.
- **A pruned task can't fire one last time.** It is also dropped from the
  schedule the running Beat already holds, so it can't fire before Beat next
  re-reads the table.

## Tasks created in the admin

If you also create periodic tasks by hand or from code, protect them by name or
by shell-style pattern:

```python
CELERY_BEAT_PRUNE_KEEP = ["manual-*", "nightly-export"]
```

## Options

| Setting | Default | Meaning |
|---|---|---|
| `CELERY_BEAT_PRUNE_KEEP` | `()` | Names or patterns (`fnmatch`) never removed |
| `CELERY_BEAT_PRUNE_DRY_RUN` | `False` | Log what would be removed, remove nothing |
| `CELERY_BEAT_PRUNE_ONE_OFF` | `False` | Also remove `one_off=True` rows |

These are read from your Celery config (`beat_prune_keep`, and so on) or,
failing that, from the Django settings above.

## Look before you prune

The first time, see what would go without restarting Beat:

```bash
python manage.py prune_periodic_tasks --dry-run
python manage.py prune_periodic_tasks --keep "manual-*"
```

The command needs your Celery app to be imported, which it is in the usual
layout (`from .celery import app as celery_app` in your project's
`__init__.py`).

## Using the function directly

```python
from django_celery_beat_prune import prune, Options

result = prune(app, Options(keep=("manual-*",), dry_run=True))
print(result.removed)
```

## Why this exists

We run [TheLoops](https://theloops.live), which streams pre-recorded video to
YouTube, Twitch and other platforms around the clock, and its periodic work runs
on Celery Beat. We once commented a task out of settings because it made
about 10,000 useless API calls a day. When we later moved Beat's schedule into
the database, we noticed that on a plain `DatabaseScheduler` that fix would
have changed nothing. This package is the scheduler we wrote then. The background is in
[our engineering notes](https://theloops.live/engineering/django-celery-beat-prune).

## License

MIT
