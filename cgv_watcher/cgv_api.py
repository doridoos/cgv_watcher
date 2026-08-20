"""CGV 조회 클라이언트.

엔드포인트는 전부 config에서 온다. CGV가 API를 바꾸면 코드를 고치는 게 아니라
config.yaml의 URL/매핑만 갱신하면 된다. (README '엔드포인트 찾기' 참고)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import requests

from .browser_fetch import fetch_captured_json
from .config import Config, EndpointConfig
from .models import Seat, Showtime
from . import parsers

log = logging.getLogger("cgv_watcher.api")


class CgvApiError(Exception):
    pass


def _render(value: Any, vars: dict[str, str]) -> Any:
    """dict/list/str 안의 {placeholder}를 재귀 치환."""
    if isinstance(value, str):
        try:
            return value.format(**vars)
        except (KeyError, IndexError):
            return value
    if isinstance(value, dict):
        return {k: _render(v, vars) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, vars) for v in value]
    return value


class CgvClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": cfg.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://cgv.co.kr/",
                "Origin": "https://cgv.co.kr",
            }
        )

    def _vars(self, date: str = "", showtime: Optional[Showtime] = None) -> dict[str, str]:
        t = self.cfg.target
        v = {
            "date": date,
            "date_dash": f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else date,
            "theater_code": t.theater_code,
            "screen_code": t.screen_code,
            "movie_code": t.movie_code,
            "screening_id": "",
        }
        if showtime:
            v["screening_id"] = showtime.screening_id
            v["date"] = v["date"] or showtime.date
            d = showtime.date
            v["date_dash"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
            # 회차 raw 응답의 값도 플레이스홀더로 노출 (예: {raw[scnScdlNo]} 대신 {scnScdlNo})
            for k, val in showtime.raw.items():
                if isinstance(val, (str, int)) and k not in v:
                    v[k] = str(val)
        return v

    def _request(self, ep: EndpointConfig, vars: dict[str, str]) -> Any:
        url = _render(ep.url, vars)
        params = _render(ep.params, vars) or None
        body = _render(ep.json_body, vars) if ep.json_body is not None else None
        headers = _render(ep.headers, vars) or None
        log.debug("%s %s params=%s body=%s", ep.method, url, params, body)
        resp = self.session.request(
            ep.method,
            url,
            params=params,
            json=body,
            headers=headers,
            timeout=self.cfg.poll.timeout_sec,
        )
        if resp.status_code != 200:
            raise CgvApiError(
                f"HTTP {resp.status_code} from {url}\n"
                f"응답 앞부분: {resp.text[:300]!r}\n"
                "403이면 헤더/파라미터 누락일 수 있습니다. README 트러블슈팅 참고."
            )
        try:
            return resp.json()
        except json.JSONDecodeError:
            raise CgvApiError(
                f"JSON이 아닌 응답: {url}\n앞부분: {resp.text[:300]!r}"
            )

    def _fetch_raw(self, ep: EndpointConfig, vars: dict[str, str]) -> list[Any]:
        """모드에 따라 응답 JSON들을 가져온다 (browser 모드는 여러 개일 수 있음)."""
        if ep.mode == "browser":
            return fetch_captured_json(
                page_url=_render(ep.page_url, vars),
                capture_pattern=ep.capture_pattern,
                timeout_sec=max(self.cfg.poll.timeout_sec, 30),
                discovered_path=self.cfg.state_dir / "discovered.json",
            )
        return [self._request(ep, vars)]

    # ------------------------------------------------------------- 공개 메서드

    def fetch_showtimes(self, date: str) -> list[Showtime]:
        """지정 날짜의 상영 회차 목록."""
        ep = self.cfg.endpoints["showtimes"]
        shows: list[Showtime] = []
        seen: set[str] = set()
        for data in self._fetch_raw(ep, self._vars(date=date)):
            if ep.list_path == "auto":
                parsed = parsers.extract_showtimes_auto(data, date_hint=date)
            else:
                parsed = parsers.extract_showtimes_mapped(data, ep.list_path, ep.fields, date)
            for st in parsed:
                if st.key not in seen:
                    seen.add(st.key)
                    shows.append(st)
        log.info("%s: 회차 %d개 수신", date, len(shows))
        return shows

    def fetch_available_seats(self, showtime: Showtime) -> Optional[list[Seat]]:
        """회차의 예매 가능 좌석 목록. seats 엔드포인트가 없으면 None."""
        ep = self.cfg.endpoints.get("seats")
        if ep is None:
            return None
        if ep.mode == "browser":
            raise CgvApiError(
                "seats 엔드포인트는 api 모드만 지원합니다 (좌석 화면은 회차 클릭이 "
                "필요해서 단순 페이지 로드로는 캡처가 안 됨). state/discovered.json과 "
                "개발자도구로 좌석 API 주소를 찾아 url로 지정해주세요."
            )
        data = self._request(ep, self._vars(showtime=showtime))
        if ep.list_path == "auto":
            seats = parsers.extract_seats_auto(data)
        else:
            seats = parsers.extract_seats_mapped(data, ep.list_path, ep.fields)
        return [s for s in seats if s.available]

    def probe(self, date: str) -> dict:
        """진단용: 원본 응답과 파싱 결과를 함께 반환."""
        ep = self.cfg.endpoints["showtimes"]
        raws = self._fetch_raw(ep, self._vars(date=date))
        shows: list[Showtime] = []
        seen: set[str] = set()
        for data in raws:
            parsed = (
                parsers.extract_showtimes_auto(data, date_hint=date)
                if ep.list_path == "auto"
                else parsers.extract_showtimes_mapped(data, ep.list_path, ep.fields, date)
            )
            for st in parsed:
                if st.key not in seen:
                    seen.add(st.key)
                    shows.append(st)
        candidates = [
            {"path": p, "size": len(lst), "sample_keys": sorted(lst[0].keys())[:20]}
            for data in raws
            for p, lst in parsers.walk_lists(data)
        ]
        return {"parsed": shows, "list_candidates": candidates, "raw": raws}
