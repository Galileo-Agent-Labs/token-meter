"""Dependency-injected Token Meter application service graph."""


class Application:
    def __init__(self, *, sessions, settings, budgets, capabilities, updates,
                 deletion, menubar, agent_api, current_state, cross_session,
                 health):
        self.sessions = sessions
        self.settings = settings
        self.budgets = budgets
        self.capabilities = capabilities
        self.updates = updates
        self.deletion = deletion
        self.menubar = menubar
        self.agent_api = agent_api
        self._current_state = current_state
        self._cross_session = cross_session
        self._health = health

    def current_state(self):
        return self._current_state()

    def cross_session(self):
        return self._cross_session()

    def health(self):
        return self._health()
