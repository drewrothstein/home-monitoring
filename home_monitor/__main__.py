"""
Main entry point for the package.

Allows running the package with: python -m home_monitor
"""

import sys

USAGE = """Usage:
  python -m home_monitor server   - Start HTTP server
  python -m home_monitor fetch    - Run data fetch once
  python -m home_monitor init-db  - Initialize database schema"""

COMMANDS = {
    "server": lambda: __import__("home_monitor.server", fromlist=["run_server"]).run_server(),
    "fetch": lambda: __import__(
        "home_monitor.fetcher", fromlist=["fetch_all_data"]
    ).fetch_all_data(),
    "init-db": lambda: __import__(
        "home_monitor.database", fromlist=["init_database"]
    ).init_database(),
}

if len(sys.argv) < 2:
    print(USAGE)
    sys.exit(1)

command = sys.argv[1]
if command in COMMANDS:
    COMMANDS[command]()
else:
    print(f"Unknown command: {command}")
    print(USAGE)
    sys.exit(1)
