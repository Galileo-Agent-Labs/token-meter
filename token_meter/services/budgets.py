"""Budget projection service."""


class BudgetService:
    def __init__(self, settings, status):
        self._settings = settings
        self._status = status

    def settings(self):
        return self._settings()

    def status(self, months, settings=None, now=None):
        return self._status(months, settings=settings, now=now)
