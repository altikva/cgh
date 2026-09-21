# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Background logging carries a timestamp, so an owner.log line can
#              be placed against a specific event (a crash window, a restart)
#              rather than guessed from its position in the file.

from __future__ import annotations

import logging
import re

from codegraph.core.log import configure_background_logging


def test_owner_log_lines_carry_a_timestamp(capsys):
    logger = logging.getLogger("codegraph")
    logger.handlers.clear()  # bypass the idempotent guard for a clean setup
    try:
        configure_background_logging()
        logging.getLogger("codegraph.test").error("boom")
        err = capsys.readouterr().err
    finally:
        logger.handlers.clear()
    # e.g. "[codegraph] 2026-09-21 07:12:03 E codegraph.test: boom"
    assert re.search(
        r"\[codegraph\] \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} E codegraph\.test: boom",
        err,
    ), err
