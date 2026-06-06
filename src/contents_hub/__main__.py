"""Allow running contents-hub as `python -m contents_hub`."""

import sys

from contents_hub.cli import main

sys.exit(main())
