"""config.yaml 로드와 검증.

민감정보(텔레그램 토큰)가 들어가므로 config.yaml은 git에 커밋하지 않는다.
구조는 config.example.yaml 참고.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml


class ConfigError(Exception):
    pass


@dataclass
class EndpointConfig:
    """CGV 조회 엔드포인트 하나에 대한 요청 정의.

    mode:
      browser — Playwright로 page_url(실제 예매 페이지)을 열고, 페이지가 부르는
                API 응답 중 URL에 capture_pattern이 포함된 것을 가로챈다.
                Cloudflare 403을 우회하는 기본 권장 모드.
      api     — url로 직접 HTTP 요청. 가볍지만 403이 날 수 있다.

    url/page_url/params/json 안의 문자열에는 {date}, {date_dash}, {theater_code},
    {screen_code}, {movie_code}, {screening_id} 플레이스홀더를 쓸 수 있다.
    """

    url: str = ""
    mode: str = "api"  # "api" | "browser"
    page_url: str = ""
    capture_pattern: str = "searchMovScnInfo"
    method: str = "GET"
    params: dict = field(default_factory=dict)
    json_body: Optional[dict] = None
    headers: dict = field(default_factory=dict)
    # 응답 JSON에서 목록을 찾는 방법: "auto"(휴리스틱) 또는 "a.b.c" 점 표기 경로
    list_path: str = "auto"
    # list_path가 경로일 때 각 항목에서 필드를 뽑는 매핑 (모델필드: 응답키)
    fields: dict = field(default_factory=dict)


@dataclass
class TargetConfig:
    """무엇을 감시할지."""

    theater_code: str = ""
    theater_name: str = ""
    movie_keyword: str = ""  # 영화 제목 부분 일치 (공백 무시, 대소문자 무시)
    movie_code: str = ""
    hall_keyword: str = "IMAX"  # 상영관 이름 부분 일치
    grade_code: str = ""  # 상영등급코드 정확 일치 (CGV: IMAX='03'). 이름 매칭보다 견고
    screen_code: str = ""
    date_from: str = ""  # YYYYMMDD, 비우면 오늘
    date_to: str = ""  # YYYYMMDD, 비우면 date_from + (days-1)
    days: int = 3
    time_from: str = "00:00"  # 회차 시작시간 창
    time_to: str = "23:59"

    def dates(self) -> list[str]:
        start = (
            date(int(self.date_from[:4]), int(self.date_from[4:6]), int(self.date_from[6:8]))
            if self.date_from
            else date.today()
        )
        if self.date_to:
            end = date(int(self.date_to[:4]), int(self.date_to[4:6]), int(self.date_to[6:8]))
        else:
            end = start + timedelta(days=max(self.days, 1) - 1)
        if end < start:
            raise ConfigError(f"date_to({self.date_to})가 date_from보다 앞입니다")
        out = []
        d = start
        while d <= end:
            out.append(d.strftime("%Y%m%d"))
            d += timedelta(days=1)
        return out


@dataclass
class AlertConfig:
    """언제 알릴지 (오탐 필터 포함)."""

    # 이 열들'만' 새로 풀렸다면 알리지 않는다 (블로그의 A~C열 교훈)
    ignore_rows: list[str] = field(default_factory=list)
    # 좌석 상세를 조회할 수 없을 때, 좌석 수 증가만으로 알릴지
    alert_on_count_only: bool = True
    # 감시 대상 회차가 처음 발견됐을 때(예매 오픈 자체)도 알릴지
    alert_on_new_showtime: bool = True
    # 콜드스타트(상태 파일이 빈 최초 실행) 때 요약 1건을 보낼지.
    # 이때의 기존 회차들은 '오픈'으로 알리지 않고 기준값으로만 저장된다
    startup_summary: bool = True


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass
class PollConfig:
    interval_sec: int = 300  # 평소 5분
    burst_interval_sec: int = 120  # 버스트 중 2분
    jitter_sec: int = 20  # 서버 부담과 패턴 감지를 줄이기 위한 무작위 지연
    timeout_sec: int = 15
    error_notify_after: int = 5  # 연속 실패가 이 횟수에 도달하면 1회 경고 알림
    # 브라우저로 캡처한 세션(Cloudflare 쿠키 포함)의 재사용 시간.
    # __cf_bm 쿠키 수명이 약 30분이라 여유를 두고 20분 (참고 프로젝트와 동일)
    session_ttl_min: int = 20


@dataclass
class Config:
    target: TargetConfig
    alert: AlertConfig
    telegram: TelegramConfig
    poll: PollConfig
    endpoints: dict[str, EndpointConfig]
    state_dir: Path
    # 클라우드 VM 등에서 Chromium 실행에 필요한 인자 (참고 프로젝트의 CLOUD_SANDBOX_ARGS)
    # 예: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
    browser_args: list[str] = field(default_factory=list)
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )


def _endpoint(raw: dict) -> EndpointConfig:
    mode = str(raw.get("mode", "api")).lower()
    if mode not in ("api", "browser"):
        raise ConfigError(f"endpoint mode는 api 또는 browser여야 합니다: {mode!r}")
    if mode == "browser" and not raw.get("page_url"):
        raise ConfigError("browser 모드에는 page_url이 필요합니다")
    if mode == "api" and not raw.get("url"):
        raise ConfigError("api 모드에는 url이 필요합니다")
    return EndpointConfig(
        url=str(raw.get("url") or ""),
        mode=mode,
        page_url=str(raw.get("page_url") or ""),
        capture_pattern=str(raw.get("capture_pattern") or "searchMovScnInfo"),
        method=str(raw.get("method", "GET")).upper(),
        params=raw.get("params") or {},
        json_body=raw.get("json"),
        headers=raw.get("headers") or {},
        list_path=raw.get("list_path", "auto"),
        fields=raw.get("fields") or {},
    )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"설정 파일이 없습니다: {path}\n"
            "config.example.yaml을 config.yaml로 복사한 뒤 값을 채워주세요."
        )
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # 텔레그램 봇(bot 모드)이 바꾼 값은 state/overrides.yaml에 저장된다.
    # config.yaml(주석 있는 원본)은 건드리지 않고 그 위에 병합한다.
    ov_state_dir = Path(raw.get("state_dir") or (path.parent / "state"))
    ov_path = ov_state_dir / "overrides.yaml"
    if ov_path.exists():
        try:
            overrides = yaml.safe_load(ov_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            overrides = {}
        for section in ("target", "alert", "poll"):
            if isinstance(overrides.get(section), dict):
                raw[section] = {**(raw.get(section) or {}), **overrides[section]}

    tg_raw = raw.get("telegram") or {}
    telegram = TelegramConfig(
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or str(tg_raw.get("bot_token") or ""),
        chat_id=os.environ.get("TELEGRAM_CHAT_ID") or str(tg_raw.get("chat_id") or ""),
    )

    t_raw = raw.get("target") or {}
    target = TargetConfig(
        theater_code=str(t_raw.get("theater_code") or ""),
        theater_name=str(t_raw.get("theater_name") or ""),
        movie_keyword=str(t_raw.get("movie_keyword") or ""),
        movie_code=str(t_raw.get("movie_code") or ""),
        hall_keyword=str(t_raw.get("hall_keyword") or ""),
        grade_code=str(t_raw.get("grade_code") or ""),
        screen_code=str(t_raw.get("screen_code") or ""),
        date_from=str(t_raw.get("date_from") or ""),
        date_to=str(t_raw.get("date_to") or ""),
        days=int(t_raw.get("days") or 3),
        time_from=str(t_raw.get("time_from") or "00:00"),
        time_to=str(t_raw.get("time_to") or "23:59"),
    )

    a_raw = raw.get("alert") or {}
    alert = AlertConfig(
        ignore_rows=[str(r).upper() for r in (a_raw.get("ignore_rows") or [])],
        alert_on_count_only=bool(a_raw.get("alert_on_count_only", True)),
        alert_on_new_showtime=bool(a_raw.get("alert_on_new_showtime", True)),
        startup_summary=bool(a_raw.get("startup_summary", True)),
    )

    p_raw = raw.get("poll") or {}
    poll = PollConfig(
        interval_sec=int(p_raw.get("interval_sec") or 300),
        burst_interval_sec=int(p_raw.get("burst_interval_sec") or 120),
        jitter_sec=int(p_raw.get("jitter_sec") or 20),
        timeout_sec=int(p_raw.get("timeout_sec") or 15),
        error_notify_after=int(p_raw.get("error_notify_after") or 5),
        session_ttl_min=int(p_raw.get("session_ttl_min") or 20),
    )
    if poll.interval_sec < 60:
        raise ConfigError("poll.interval_sec는 60초 이상으로 설정해주세요 (서버 부담 방지)")

    ep_raw = raw.get("endpoints") or {}
    endpoints = {name: _endpoint(ep) for name, ep in ep_raw.items()}
    if "showtimes" not in endpoints:
        raise ConfigError(
            "endpoints.showtimes가 설정되지 않았습니다.\n"
            "README의 '엔드포인트 찾기' 절차를 따라 상영시간표 요청 URL을 채워주세요."
        )

    state_dir = Path(raw.get("state_dir") or (path.parent / "state"))
    state_dir.mkdir(parents=True, exist_ok=True)

    return Config(
        target=target,
        alert=alert,
        telegram=telegram,
        poll=poll,
        endpoints=endpoints,
        state_dir=state_dir,
        browser_args=[str(a) for a in (raw.get("browser_args") or [])],
        user_agent=str(raw.get("user_agent") or Config.user_agent),
    )
