"""Playwright 브라우저 모드 + 세션 캡처.

CGV 새 사이트는 Cloudflare 뒤에 있어서 requests로 API를 직접 부르면
403이 나기 쉽다 (원문 블로그의 첫 403, 참고 프로젝트가 Puppeteer를 쓴 이유).

참고 프로젝트(DongminL/movie_reservation_notification)의 핵심 설계를 따른다:
매 폴링마다 브라우저를 띄우는 게 아니라, 브라우저로 예매 페이지를 한 번 열어
페이지가 스스로 호출하는 API의 (실제 URL + 실제 전송 헤더/쿠키)를 캡처해 두고,
이후에는 그 세션으로 가벼운 직접 HTTP 요청을 한다. Cloudflare 쿠키(__cf_bm)가
만료되는 ~20분마다, 혹은 직접 요청이 실패할 때만 브라우저를 다시 띄운다.

필요 패키지:  pip install playwright  &&  playwright install chromium
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("cgv_watcher.browser")

# requests로 재사용하면 곤란한 헤더들 (HTTP/2 의사헤더는 ":" 프리픽스로 별도 제거)
_DROP_HEADERS = {"host", "content-length", "accept-encoding", "connection"}


class BrowserFetchError(Exception):
    pass


@dataclass
class CapturedSession:
    """브라우저가 실제로 보낸 API 요청의 재사용 가능한 스냅샷."""

    api_url: str
    headers: dict[str, str]
    date: str  # 캡처 당시 조회한 날짜 (URL 쿼리에서 날짜 파라미터를 찾는 단서)
    captured_at: float = field(default_factory=time.time)

    def age_sec(self) -> float:
        return time.time() - self.captured_at


def fetch_captured_json(
    page_url: str,
    capture_pattern: str,
    date: str = "",
    timeout_sec: int = 30,
    settle_sec: float = 2.0,
    browser_args: Optional[list[str]] = None,
) -> tuple[list[Any], Optional[CapturedSession]]:
    """page_url을 열고 capture_pattern이 URL에 포함된 응답들의 JSON을 수집.

    date가 주어지면 URL에 그 날짜가 포함된 응답을 우선 채택한다 — 페이지가
    오늘 날짜를 먼저 조회하고 나서 요청한 날짜를 조회하는 경우의 오캡처 방지.
    반환: (JSON 목록, 재사용 세션 또는 None)
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserFetchError(
            "browser 모드에는 Playwright가 필요합니다:\n"
            "  pip install playwright && playwright install chromium\n"
            "또는 config에서 endpoints.showtimes.mode를 api로 바꾸세요."
        )

    matched: list[dict] = []  # {"json":…, "url":…, "request":…}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=browser_args or [])
        try:
            ctx = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul")
            page = ctx.new_page()

            def on_response(resp):
                if capture_pattern not in resp.url:
                    return
                if resp.request.method == "OPTIONS":  # preflight엔 본문이 없다
                    return
                try:
                    matched.append(
                        {"json": resp.json(), "url": resp.url, "request": resp.request}
                    )
                    log.debug("캡처: %s %s", resp.request.method, resp.url)
                except Exception:
                    log.debug("JSON 아님, 무시: %s", resp.url)

            page.on("response", on_response)
            page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)

            def has_wanted() -> bool:
                if not matched:
                    return False
                return (not date) or any(date in m["url"] for m in matched)

            # 원하는 응답이 잡힐 때까지 대기 후, 추가 응답을 위해 잠시 더 기다린다
            deadline = time.monotonic() + timeout_sec
            while not has_wanted() and time.monotonic() < deadline:
                page.wait_for_timeout(300)
            if matched:
                page.wait_for_timeout(int(settle_sec * 1000))

            # 날짜가 URL에 포함된 응답 우선, 없으면 패턴 매칭 전부 (경고만)
            wanted = [m for m in matched if date and date in m["url"]] or matched
            if matched and not (date and any(date in m["url"] for m in matched)) and date:
                log.warning(
                    "URL에 요청 날짜(%s)가 포함된 응답이 없어 패턴 매칭 응답을 그대로 사용",
                    date,
                )

            session = None
            if wanted:
                first = wanted[0]
                headers = {
                    k.lower(): v
                    for k, v in first["request"].all_headers().items()
                    if not k.startswith(":") and k.lower() not in _DROP_HEADERS
                }
                if "cookie" not in headers:
                    cookies = ctx.cookies(first["url"])
                    if cookies:
                        headers["cookie"] = "; ".join(
                            f"{c['name']}={c['value']}" for c in cookies
                        )
                session = CapturedSession(
                    api_url=first["url"], headers=headers, date=date
                )
        finally:
            browser.close()

    if not matched:
        raise BrowserFetchError(
            f"{timeout_sec}초 안에 '{capture_pattern}' 응답을 잡지 못했습니다.\n"
            f"페이지: {page_url}\n"
            "페이지 구조나 API 이름이 바뀌었을 수 있습니다. 브라우저 개발자도구로 "
            "실제 요청 이름을 확인해 capture_pattern을 갱신하세요."
        )

    return [m["json"] for m in wanted], session
