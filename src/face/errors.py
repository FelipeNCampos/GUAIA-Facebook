from __future__ import annotations


class FaceModuleError(Exception):
    """Base exception for the Facebook module."""


class CaptchaDetectedError(FaceModuleError):
    """Raised when a captcha or checkpoint is detected."""
