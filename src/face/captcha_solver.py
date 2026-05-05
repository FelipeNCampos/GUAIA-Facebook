from __future__ import annotations

from common.logging import get_logger

logger = get_logger(__name__)


class CaptchaSolver:
    def solve(self) -> str:
        logger.warning("2Captcha integration not implemented yet")
        raise NotImplementedError("2Captcha integration will be delivered in a later sprint")
