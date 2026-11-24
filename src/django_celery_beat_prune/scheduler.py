from django_celery_beat.schedulers import DatabaseScheduler

from .core import options_for, prune


class PruningScheduler(DatabaseScheduler):
    """DatabaseScheduler that also removes rows the beat schedule no longer defines.

    Use it in place of DatabaseScheduler::

        CELERY_BEAT_SCHEDULER = "django_celery_beat_prune.PruningScheduler"

    Pruning runs once, at Beat start, right after the stock scheduler has
    written the settings into the table. The `enabled` flag is never touched,
    so disabling a task in the admin still works as an emergency switch.
    """

    def setup_schedule(self):
        super().setup_schedule()
        prune(self.app, options_for(self.app), schedule=self._schedule)
