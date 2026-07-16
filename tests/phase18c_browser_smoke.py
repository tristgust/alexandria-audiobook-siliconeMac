from __future__ import annotations

# Compatibility entry point. The maintained interactive browser smoke lives
# in phase18c_roster_browser_smoke.py.
from phase18c_roster_browser_smoke import main, run


__all__ = ["main", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
