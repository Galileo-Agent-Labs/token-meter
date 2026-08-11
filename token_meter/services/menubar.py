"""Compact native-client projection service."""


class MenubarService:
    def __init__(self, build):
        self._build = build

    def state(self, session_id=None):
        return self._build(session_id)
