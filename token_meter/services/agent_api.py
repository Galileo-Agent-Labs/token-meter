"""Read-only agent API service used by MCP."""


class AgentAPIService:
    def __init__(self, check, usage, capabilities, queries):
        self._check = check
        self._usage = usage
        self._capabilities = capabilities
        self._queries = queries

    def check(self, **arguments):
        return self._check(**arguments)

    def usage(self, **arguments):
        return self._usage(**arguments)

    def capabilities(self, **arguments):
        return self._capabilities(**arguments)

    def sessions(self, **arguments):
        return self._queries.sessions(**arguments)

    def trace(self, **arguments):
        return self._queries.trace(**arguments)

    def stats(self, **arguments):
        return self._queries.stats(**arguments)

    def schema(self, **arguments):
        return self._queries.schema(**arguments)
