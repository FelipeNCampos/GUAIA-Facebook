from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urljoin

import httpx
from common.logging import get_logger

from face.config import Settings, get_settings

logger = get_logger(__name__)

CaptchaType = Literal["recaptcha_v2", "hcaptcha", "turnstile"]

_TWOCAPTCHA_IN_URL = "https://2captcha.com/in.php"
_TWOCAPTCHA_RESULT_URL = "https://2captcha.com/res.php"
_HTML_BODY_LIMIT = 250_000
_FORM_PATTERN = re.compile(
    r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>",
    re.IGNORECASE | re.DOTALL,
)
_INPUT_PATTERN = re.compile(r"<input\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
_TEXTAREA_PATTERN = re.compile(
    r"<textarea\b(?P<attrs>[^>]*)>(?P<value>.*?)</textarea>",
    re.IGNORECASE | re.DOTALL,
)
_RECAPTCHA_SITE_KEY_PATTERNS = (
    re.compile(
        r"""data-sitekey\s*=\s*["'](?P<site_key>[^"']+)["']""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""sitekey\s*[:=]\s*["'](?P<site_key>[^"']+)["']""",
        re.IGNORECASE,
    ),
)
_CHALLENGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "captcha": ("captcha_keyword_captcha",),
    "g-recaptcha": ("captcha_keyword_g_recaptcha",),
    "h-captcha": ("captcha_keyword_hcaptcha",),
    "turnstile": ("captcha_keyword_turnstile",),
    "robot": ("captcha_keyword_robot",),
    "robots": ("captcha_keyword_robot",),
    "unusual traffic": ("captcha_keyword_unusual_traffic",),
    "verify you are human": ("captcha_keyword_verify_human",),
    "are you a robot": ("captcha_keyword_robot",),
    "attention required": ("captcha_keyword_attention_required",),
    "access denied": ("captcha_keyword_access_denied",),
    "cf-chl": ("captcha_keyword_cloudflare",),
    "checking your browser": ("captcha_keyword_browser_check",),
}


def is_captcha_url(url: str) -> bool:
    lowered = url.lower()
    return "captcha" in lowered or "checkpoint" in lowered


@dataclass(slots=True)
class CaptchaChallenge:
    captcha_type: CaptchaType
    site_key: str | None
    page_url: str
    submit_url: str
    token_field_name: str
    form_method: Literal["GET", "POST"] = "POST"
    form_inputs: dict[str, str] = field(default_factory=dict)
    marker_types: tuple[str, ...] = ()

    def build_submission_fields(self, token: str) -> dict[str, str]:
        fields = dict(self.form_inputs)
        fields[self.token_field_name] = token
        if self.captcha_type == "recaptcha_v2":
            fields.setdefault("g-recaptcha-response", token)
        elif self.captcha_type == "hcaptcha":
            fields.setdefault("h-captcha-response", token)
        else:
            fields.setdefault("cf-turnstile-response", token)
        return fields


class TwoCaptchaClient:
    def __init__(self, settings: Settings | None = None) -> None:
        resolved_settings = settings or get_settings()
        self.api_key = resolved_settings.twocaptcha_api_key
        self.enabled = resolved_settings.twocaptcha_enabled and bool(self.api_key)
        self.max_solves = resolved_settings.twocaptcha_max_solves_per_run
        self.timeout = resolved_settings.twocaptcha_request_timeout
        self.poll_interval = resolved_settings.twocaptcha_poll_interval
        self.used_solves = 0
        self.attempted_solves = 0

    def can_solve(self) -> bool:
        return self.enabled and self.used_solves < self.max_solves

    def solve_challenge(
        self,
        challenge: CaptchaChallenge,
        *,
        logger_extra: Mapping[str, object] | None = None,
    ) -> str | None:
        if challenge.site_key is None:
            logger.warning(
                "Captcha challenge is missing a site key and cannot be sent to 2Captcha",
                extra=dict(logger_extra or {}),
            )
            return None

        if challenge.captcha_type == "hcaptcha":
            return self.solve_hcaptcha(
                site_key=challenge.site_key,
                url=challenge.page_url,
                logger_extra=logger_extra,
            )
        if challenge.captcha_type == "turnstile":
            return self.solve_turnstile(
                site_key=challenge.site_key,
                url=challenge.page_url,
                logger_extra=logger_extra,
            )
        return self.solve_recaptcha_v2(
            site_key=challenge.site_key,
            url=challenge.page_url,
            logger_extra=logger_extra,
        )

    def solve_recaptcha_v2(
        self,
        site_key: str,
        url: str,
        *,
        logger_extra: Mapping[str, object] | None = None,
    ) -> str | None:
        return self._solve_task(
            captcha_type="recaptcha_v2",
            create_payload={
                "method": "userrecaptcha",
                "googlekey": site_key,
                "pageurl": url,
            },
            logger_extra=logger_extra,
        )

    def solve_hcaptcha(
        self,
        site_key: str,
        url: str,
        *,
        logger_extra: Mapping[str, object] | None = None,
    ) -> str | None:
        return self._solve_task(
            captcha_type="hcaptcha",
            create_payload={
                "method": "hcaptcha",
                "sitekey": site_key,
                "pageurl": url,
            },
            logger_extra=logger_extra,
        )

    def solve_turnstile(
        self,
        site_key: str,
        url: str,
        *,
        logger_extra: Mapping[str, object] | None = None,
    ) -> str | None:
        return self._solve_task(
            captcha_type="turnstile",
            create_payload={
                "method": "turnstile",
                "sitekey": site_key,
                "pageurl": url,
            },
            logger_extra=logger_extra,
        )

    def _solve_task(
        self,
        *,
        captcha_type: CaptchaType,
        create_payload: Mapping[str, str],
        logger_extra: Mapping[str, object] | None = None,
    ) -> str | None:
        if not self.can_solve():
            return None

        self.attempted_solves += 1
        payload = {
            "key": self.api_key,
            "json": "1",
            **create_payload,
        }
        extra = dict(logger_extra or {})
        extra.setdefault("captcha_type", captcha_type)

        try:
            with httpx.Client(timeout=max(float(self.poll_interval), 30.0)) as client:
                task_id = self._create_task(client=client, payload=payload, extra=extra)
                if task_id is None:
                    return None

                token = self._poll_for_result(
                    client=client,
                    task_id=task_id,
                    captcha_type=captcha_type,
                    extra=extra,
                )
        except httpx.HTTPError:
            logger.warning(
                "2Captcha request failed with an HTTP error",
                extra=extra,
                exc_info=True,
            )
            return None

        if token is None:
            return None

        self.used_solves += 1
        return token

    def _create_task(
        self,
        *,
        client: httpx.Client,
        payload: Mapping[str, str],
        extra: Mapping[str, object],
    ) -> str | None:
        response = client.post(_TWOCAPTCHA_IN_URL, data=payload)
        response.raise_for_status()
        data = response.json()
        task_id = self._extract_success_value(data)
        if task_id is None:
            logger.warning(
                "2Captcha task creation failed",
                extra={**extra, "twocaptcha_response": data},
            )
            return None
        return task_id

    def _poll_for_result(
        self,
        *,
        client: httpx.Client,
        task_id: str,
        captcha_type: CaptchaType,
        extra: Mapping[str, object],
    ) -> str | None:
        deadline = time.monotonic() + float(self.timeout)
        while time.monotonic() < deadline:
            time.sleep(max(float(self.poll_interval), 1.0))
            response = client.get(
                _TWOCAPTCHA_RESULT_URL,
                params={
                    "key": self.api_key,
                    "action": "get",
                    "id": task_id,
                    "json": "1",
                },
            )
            response.raise_for_status()
            data = response.json()
            token = self._extract_success_value(data)
            if token is not None:
                return token

            request_value = str(data.get("request", ""))
            if request_value == "CAPCHA_NOT_READY":
                continue

            logger.warning(
                "2Captcha returned a terminal error while polling",
                extra={
                    **extra,
                    "captcha_type": captcha_type,
                    "twocaptcha_task_id": task_id,
                    "twocaptcha_response": data,
                },
            )
            return None

        logger.warning(
            "2Captcha solve request timed out before a token was ready",
            extra={
                **extra,
                "captcha_type": captcha_type,
                "twocaptcha_task_id": task_id,
                "twocaptcha_timeout_seconds": self.timeout,
            },
        )
        return None

    @staticmethod
    def _extract_success_value(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        if int(payload.get("status", 0)) != 1:
            return None
        request_value = payload.get("request")
        if not isinstance(request_value, str) or not request_value:
            return None
        return request_value


def detect_captcha_challenge(
    *,
    response_url: str,
    status_code: int,
    response_text: str,
    headers: Mapping[str, str] | None = None,
) -> CaptchaChallenge | None:
    text = response_text[:_HTML_BODY_LIMIT]
    lowered = text.lower()
    marker_types: list[str] = []

    if status_code in {403, 429}:
        marker_types.append(f"http_status_{status_code}")
    if is_captcha_url(response_url):
        marker_types.append("captcha_url")

    content_type = ""
    if headers is not None:
        content_type = headers.get("Content-Type", headers.get("content-type", ""))
    if "text/html" in content_type.lower() or "<html" in lowered:
        marker_types.append("html_protection_page")

    for needle, markers in _CHALLENGE_KEYWORDS.items():
        if needle in lowered:
            marker_types.extend(markers)

    captcha_type = _detect_captcha_type(lowered)
    site_key = _extract_site_key(text)
    if captcha_type is not None:
        marker_types.append(f"captcha_type_{captcha_type}")
    if site_key:
        marker_types.append("captcha_site_key_detected")

    is_suspected_challenge = bool(
        site_key
        or captcha_type
        or status_code in {403, 429}
        or any(marker.startswith("captcha_keyword_") for marker in marker_types)
        or "html_protection_page" in marker_types
    )
    if not is_suspected_challenge:
        return None

    resolved_type = captcha_type or "recaptcha_v2"
    form_details = _extract_form_details(text=text, page_url=response_url)
    return CaptchaChallenge(
        captcha_type=resolved_type,
        site_key=site_key,
        page_url=response_url,
        submit_url=form_details["submit_url"],
        token_field_name=_default_token_field_name(resolved_type),
        form_method=form_details["form_method"],
        form_inputs=form_details["form_inputs"],
        marker_types=tuple(dict.fromkeys(["captcha_challenge", *marker_types])),
    )


def _detect_captcha_type(lowered_text: str) -> CaptchaType | None:
    if "turnstile" in lowered_text or "cf-turnstile" in lowered_text:
        return "turnstile"
    if "hcaptcha" in lowered_text or "h-captcha" in lowered_text:
        return "hcaptcha"
    if "grecaptcha" in lowered_text or "g-recaptcha" in lowered_text or "recaptcha" in lowered_text:
        return "recaptcha_v2"
    return None


def _extract_site_key(text: str) -> str | None:
    for pattern in _RECAPTCHA_SITE_KEY_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return match.group("site_key")
    return None


def _extract_form_details(*, text: str, page_url: str) -> dict[str, object]:
    selected_form = None
    for match in _FORM_PATTERN.finditer(text):
        body = match.group("body").lower()
        attrs = match.group("attrs").lower()
        if any(
            marker in body or marker in attrs
            for marker in (
                "captcha",
                "g-recaptcha",
                "h-captcha",
                "turnstile",
                "cf-turnstile",
            )
        ):
            selected_form = match
            break

    if selected_form is None:
        return {
            "submit_url": page_url,
            "form_method": "POST",
            "form_inputs": {},
        }

    attrs = selected_form.group("attrs")
    body = selected_form.group("body")
    action = _extract_attribute(attrs, "action") or page_url
    method = (_extract_attribute(attrs, "method") or "POST").upper()
    form_inputs: dict[str, str] = {}

    for input_match in _INPUT_PATTERN.finditer(body):
        input_attrs = input_match.group("attrs")
        name = _extract_attribute(input_attrs, "name")
        if not name:
            continue
        form_inputs[name] = _extract_attribute(input_attrs, "value") or ""

    for textarea_match in _TEXTAREA_PATTERN.finditer(body):
        textarea_attrs = textarea_match.group("attrs")
        name = _extract_attribute(textarea_attrs, "name")
        if not name:
            continue
        form_inputs[name] = textarea_match.group("value").strip()

    return {
        "submit_url": urljoin(page_url, action),
        "form_method": "GET" if method == "GET" else "POST",
        "form_inputs": form_inputs,
    }


def _extract_attribute(raw_attributes: str, attribute_name: str) -> str | None:
    pattern = re.compile(
        rf"""\b{re.escape(attribute_name)}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
        re.IGNORECASE,
    )
    match = pattern.search(raw_attributes)
    if match is None:
        return None
    return match.group(1) or match.group(2) or match.group(3)


def _default_token_field_name(captcha_type: CaptchaType) -> str:
    if captcha_type == "hcaptcha":
        return "h-captcha-response"
    if captcha_type == "turnstile":
        return "cf-turnstile-response"
    return "g-recaptcha-response"
