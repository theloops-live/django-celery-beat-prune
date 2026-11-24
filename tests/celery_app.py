import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")

from celery import Celery  # noqa: E402

app = Celery("tests")
app.config_from_object("django.conf:settings", namespace="CELERY")


@app.task(name="tests.heartbeat")
def heartbeat():
    return "ok"
