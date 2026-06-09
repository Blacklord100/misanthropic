"""Enable `python -m misanthropic` as an alias for the `misanthropic` command."""
import sys

from .cli import main

sys.exit(main())
