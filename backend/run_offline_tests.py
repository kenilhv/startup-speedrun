"""Run tests without loading .env files or permitting outbound TCP connections."""
import sys
import unittest
from unittest.mock import patch
from .test_support import offline_network


def main():
    # Guard discovery/imports too, not just individual test methods.
    with patch("dotenv.load_dotenv", return_value=False), offline_network():
        suite = unittest.defaultTestLoader.discover("backend", pattern="test_*.py", top_level_dir=".")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
