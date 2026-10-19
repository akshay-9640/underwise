"""One structured log line per pipeline step: event name plus key=value pairs.
Never logs document text, model output or secrets."""

import logging
import sys

_logger = logging.getLogger("underwise")
if not _logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def log(event: str, level: int = logging.INFO, **fields) -> None:
    _logger.log(level, " ".join([f"event={event}"] + [f"{k}={str(v)!r}" if " " in str(v) else f"{k}={v}" for k, v in fields.items()]))
