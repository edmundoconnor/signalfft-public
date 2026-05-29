"""python -m execution"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [ExecutionRouter] %(name)s %(message)s",
)

from execution.router import ExecutionRouter

if __name__ == "__main__":
    ExecutionRouter().run()
