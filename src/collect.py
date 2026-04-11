#!/usr/bin/env python3
"""
Collect sources into the Obsidian vault's sources/ directory.

Usage:
    python collect.py url <url> [--title TITLE] [--tags TAG1,TAG2] [--memo MEMO]
    python collect.py text <text> [--title TITLE] [--tags TAG1,TAG2]
    python collect.py memo <content> [--title TITLE] [--tags TAG1,TAG2]
    python collect.py file <path> [--title TITLE] [--tags TAG1,TAG2]

Source files are immutable once created — collect appends, never overwrites.

This is a standalone script entrypoint. For programmatic use,
import from ``llm_wiki.collect_cli`` instead.
"""

import sys
from pathlib import Path

# Allow running as standalone script (outside the package)
sys.path.insert(0, str(Path(__file__).parent))

from llm_wiki.collect_cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
