"""Machine-wide settings service."""


class SettingsService:
    def __init__(self, readers, writers):
        self._readers = dict(readers)
        self._writers = dict(writers)

    def read(self, name):
        return self._readers[name]()

    def write(self, name, value):
        return self._writers[name](value)
