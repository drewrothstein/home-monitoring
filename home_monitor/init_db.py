"""
Database initialization script.
"""

from home_monitor.database import init_database

if __name__ == "__main__":
    init_database()
else:
    # Allow importing the function directly
    __all__ = ["init_database"]
