"""Software-update orchestration service."""


class UpdateService:
    def __init__(self, status, check, install):
        self.status = status
        self.check = check
        self.install = install
