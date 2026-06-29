"""Example usage of tiny_log."""

from __future__ import annotations

import time

from tiny_log import LogContext, configure, get_logger, log_call, new_request_id


def main() -> None:
    # JSON to stderr (default in non-TTY)
    configure(level="DEBUG", json=True)

    log = get_logger("myapp").bind(service="ingest", version="0.1.0")

    log.info("starting")
    log.debug("debug info here", extra={"step": 1})

    rid = new_request_id()
    with LogContext(request_id=rid, user_id=42):
        log.info("processing record", extra={"record_id": "rec-1"})

        def slow_op() -> int:
            time.sleep(0.05)
            return 7

        result = log_call(log, "expensive", slow_op)
        log.info("done", extra={"result": result})

    try:
        raise ValueError("oops")
    except ValueError:
        log.exception("failed to process")


if __name__ == "__main__":
    main()
