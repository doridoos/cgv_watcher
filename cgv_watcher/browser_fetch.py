"""Playwright 브라우저 모드.

CGV 새 사이트는 Cloudflare 뒤에 있어서 requests로 API를 직접 부르면
403이 나기 쉽다 (원문 블로그의 첫 403, 참고 프로젝트가 Puppeteer를 쓴 이유).

이 모듈은 실제 예매 페이지를 헤드리스 브라우저로 열고, 페이지가 스스로
호출하는 API 응답(URL에 capture_pattern이 포함된 것)을 가로채서 돌려준다.
- 로그인 불필요, Cloudflare 통과는 브라우저가 알아서
- 부수 효과: 가로챈 요청의 실제 URL을 discovered.json에 남겨서
  가벼운 api 모드로 전환할 때 단서로 쓸 수 있다

필요 패키지:  pip install playwright  &&  playwright install chromium
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("cgv_watcher.browser")


class BrowserFetchError(Exception):
    pass


def fetch_captured_json(
    page_url: str,
    capture_pattern: str,
    timeout_sec: int = 30,
    settle_sec: float = 2.0,
    discovered_path: Optional[Path] = None,
) -> list[Any]:
    """page_url을 열고 capture_pattern이 URL에 포함된 응답들의 JSON을 수집."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserFetchError(
            "browser 모드에는 Playwright가 필요합니다:\n"
            "  pip install playwright && playwright install chromium\n"
            "또는 config에서 endpoints.showtimes.mode를 api로 바꾸세요."
        )

    captured: list[Any] = []
    discovered: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul")
            page = ctx.new_page()

            def on_response(resp):
                if capture_pattern not in resp.url:
                    return
                if resp.request.method == "OPTIONS":  # preflight엔 본문이 없다
                    return
                try:
                    captured.append(resp.json())
                    discovered.append(
                        {"url": resp.url, "method": resp.request.method, "status": resp.status}
                    )
                    log.debug("캡처: %s %s", resp.request.method, resp.url)
                except Exception:
                    log.debug("JSON 아님, 무시: %s", resp.url)

            page.on("response", on_response)
            page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)

            # 첫 캡처까지 대기 후, 추가 응답을 위해 잠시 더 기다린다
            deadline = time.monotonic() + timeout_sec
            while not captured and time.monotonic() < deadline:
                page.wait_for_timeout(300)
            if captured:
                page.wait_for_timeout(int(settle_sec * 1000))
        finally:
            browser.close()

    if not captured:
        raise BrowserFetchError(
            f"{timeout_sec}초 안에 '{capture_pattern}' 응답을 잡지 못했습니다.\n"
            f"페이지: {page_url}\n"
            "페이지 구조나 API 이름이 바뀌었을 수 있습니다. 브라우저 개발자도구로 "
            "실제 요청 이름을 확인해 capture_pattern을 갱신하세요."
        )

    if discovered_path is not None:
        try:
            discovered_path.write_text(
                json.dumps(discovered, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as e:
            log.debug("discovered.json 저장 실패: %s", e)

    return captured
