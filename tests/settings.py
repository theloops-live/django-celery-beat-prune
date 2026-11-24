import os

SECRET_KEY = "not-a-secret"
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django_celery_beat",
    "django_celery_beat_prune",
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # The live test runs a real Beat process, which needs a file both
        # processes can open; everything else uses memory.
        "NAME": os.environ.get("PRUNE_TEST_DB", ":memory:"),
    }
}

CELERY_BROKER_URL = "memory://"
CELERY_BEAT_SCHEDULER = "django_celery_beat_prune.PruningScheduler"
CELERY_BEAT_SCHEDULE = {
    "heartbeat": {"task": "tests.heartbeat", "schedule": 60.0},
}
