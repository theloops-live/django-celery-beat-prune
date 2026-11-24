"""Make CELERY_BEAT_SCHEDULE authoritative for django-celery-beat.

A periodic task removed from settings stops running instead of living on as a
database row.
"""

__version__ = "1.0.0"

__all__ = [
    "DEFAULT_ENTRY_NAMES",
    "Options",
    "PruningScheduler",
    "Result",
    "options_for",
    "prune",
    "stale_names",
]


def __getattr__(name):
    # Everything is imported lazily. This package goes into INSTALLED_APPS for
    # its management command, and Django imports it while the app registry is
    # still loading, before django_celery_beat's models may be imported.
    # Celery resolves "django_celery_beat_prune.PruningScheduler" by
    # attribute, after Django is set up, which lands here.
    if name == "PruningScheduler":
        from .scheduler import PruningScheduler

        return PruningScheduler
    if name in __all__:
        from . import core as _core

        return getattr(_core, name)
    raise AttributeError(name)
