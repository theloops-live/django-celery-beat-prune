"""A real `celery beat` process, started the way a deployment starts it.

The unit tests call setup_schedule() by hand. This one checks the part they
cannot: that Celery resolves "django_celery_beat_prune.PruningScheduler" from
the setting, after Django is set up, and that a stale row is gone once Beat
has started.
"""
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

ROOT = Path(__file__).resolve().parent.parent


def run(args, env, **kw):
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True, **kw)


def test_beat_removes_a_stale_row_at_start(tmp_path):
    db = tmp_path / "beat.sqlite3"
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "tests.settings",
        "PRUNE_TEST_DB": str(db),
        "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
    }
    migrated = run(["-m", "django", "migrate", "--verbosity", "0"], env)
    assert migrated.returncode == 0, migrated.stderr

    seed = (
        "import django; django.setup();"
        "from django_celery_beat.models import IntervalSchedule, PeriodicTask;"
        "i = IntervalSchedule.objects.create(every=1, period='hours');"
        "PeriodicTask.objects.create(name='left-behind', task='tests.heartbeat', interval=i)"
    )
    seeded = run(["-c", seed], env)
    assert seeded.returncode == 0, seeded.stderr

    beat = subprocess.Popen(
        [sys.executable, "-m", "celery", "-A", "tests.celery_app", "beat", "--loglevel", "INFO",
         "--pidfile", str(tmp_path / "beat.pid")],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 30
        rows = None
        while time.monotonic() < deadline:
            time.sleep(0.5)
            with sqlite3.connect(db) as conn:
                rows = {r[0] for r in conn.execute("select name from django_celery_beat_periodictask")}
            if "heartbeat" in rows and "left-behind" not in rows:
                break
    finally:
        beat.terminate()
        output = beat.communicate(timeout=15)[0]

    assert "PruningScheduler" in output, output
    assert rows is not None and "heartbeat" in rows, output
    assert "left-behind" not in rows, output
    assert "left-behind" in output  # named in the warning
