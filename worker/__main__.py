"""``python -m worker <task_id>``."""

import sys

from worker.entrypoint import main

if __name__ == "__main__":
    sys.exit(main())
