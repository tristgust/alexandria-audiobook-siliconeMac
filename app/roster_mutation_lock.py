from __future__ import annotations

import threading


APPROVED_ROSTER_MUTATION_LOCK = threading.RLock()
