"""
logger.py – Centralised logging for MeetRec.
All modules import `log` from here. Writes to records/meetrec.log.
"""

import logging
from pathlib import Path

LOG_FILE = Path(__file__).parent / "records" / "meetrec.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

log = logging.getLogger("meetrec")
