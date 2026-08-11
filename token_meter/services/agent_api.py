"""Read-only agent API service used by MCP."""


class AgentAPIService:
    def __init__(self, check, usage, capabilities):
        self._check = check
        self._usage = usage
        self._capabilities = capabilities

    def check(self, **arguments):
        return self._check(**arguments)

    def usage(self, **arguments):
        return self._usage(**arguments)

    def capabilities(self, **arguments):
        return self._capabilities(**arguments)
