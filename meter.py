#!/usr/bin/env python3
"""Token Meter compatibility facade and executable composition root."""

import sys

from token_meter import app as _application


if __name__ == "__main__":
    _application.main()
else:
    # Preserve the historical mutable module surface used by integrations and
    # tests. Patching ``meter.NAME`` therefore patches the owning application
    # module instead of a detached star-import copy.
    _application.__file__ = __file__
    sys.modules[__name__] = _application
