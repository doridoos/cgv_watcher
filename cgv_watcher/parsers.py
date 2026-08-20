"""CGV 응답 JSON 파싱.

CGV는 비공개 API라 응답 스키마가 언제든 바뀔 수 있다. 그래서 두 가지 방식을 지원한다.

1. auto  — JSON 전체를 훑어 "상영 회차처럼 생긴" / "좌석처럼 생긴" 객체 목록을
           휴리스틱으로 찾아낸다. 스키마를 몰라도 대부분 동작하는 것이 목표.
2. 명시적 매핑 — config에서 list_path("data.scnList" 등)와 fields 매핑을 지정하면
           그대로 뽑는다. auto가 잘못 짚을 때 쓰는 정밀 모드.
"""

from __future__ import annotations

import re
from typing import Any, Iterator, Optional

from .models import Seat, Showtime

# ---------------------------------------------------------------- 공통 유틸

_TIME_RE = re.compile(r"^(\d{1,2}):?(\d{2})(:\d{2})?$")
_DATE_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")
_SEAT_ID_RE = re.compile(r"^[A-Za-z]{1,2}\s*-?\s*\d{1,3}$")


def walk_lists(node: Any, path: str = "") -> Iterator[tuple[str, list]]:
    """JSON 트리 안의 모든 '딕셔너리 리스트'를 (경로, 리스트)로 순회."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk_lists(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node):
            yield path, node
        for i, v in enumerate(node):
            yield from walk_lists(v, f"{path}[{i}]")


def get_path(node: Any, path: str) -> Any:
    """'data.scnList' 같은 점 표기 경로로 값 꺼내기. 리스트는 자동 평탄화."""
    cur = node
    for part in path.split("."):
        if not part:
            continue
        if isinstance(cur, list):
            flat = []
            for item in cur:
                v = item.get(part) if isinstance(item, dict) else None
                if isinstance(v, list):
                    flat.extend(v)
                elif v is not None:
                    flat.append(v)
            cur = flat
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def norm_time(v: Any) -> str:
    """'1830', '18:30', '18:30:00' → '18:30'."""
    s = str(v).strip()
    m = _TIME_RE.match(s)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if re.fullmatch(r"\d{4}", s):
        return f"{s[:2]}:{s[2:]}"
    return s


def norm_date(v: Any) -> str:
    s = str(v).strip()
    m = _DATE_RE.match(s)
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else s


def _to_int(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def _find_key(item: dict, patterns: list[str]) -> Optional[str]:
    """키 이름이 정규식 패턴 중 하나와 맞는 첫 키를 반환."""
    for pat in patterns:
        rx = re.compile(pat, re.IGNORECASE)
        for k in item.keys():
            if rx.search(k):
                return k
    return None


# ------------------------------------------------------- 상영 회차 (showtimes)

# CGV 신·구 사이트, 모바일 API에서 관측돼 온 키 이름 패턴들.
# 순서 = 우선순위. 스키마가 바뀌어도 이름 관습이 비슷하면 잡아낸다.
_K_START = [r"^scn?sr?t.*(tm|time)", r"start.*(tm|time)", r"^strt", r"play.*s.*t", r"^time$"]
_K_DATE = [r"(scn|play|show).*(ymd|date|dt)", r"^ymd$", r"^date$"]
# 정확한 제목 키(movNm)를 최우선 — movFomNm(포맷명 '2D' 등)류 오인 방지
_K_MOVIE = [r"^(expo)?movnm$", r"mov(?!fom|typ|kind|grad).*(nm|name|title)", r"movie", r"^title$"]
_K_HALL = [r"scns.?e?nm", r"(scr|screen|theab|hall).*(nm|name)", r"^hall", r"screen"]
_K_REMAIN = [r"(rest|remain|able|fr).*(seat|cnt)", r"seat.*(rest|remain|able|cnt)", r"^remain"]
_K_TOTAL = [r"(tot|all|cp).*seat", r"seat.*tot"]
_K_SCN_ID = [r"(scn|screening|play).*(no|id|num|sseq)", r"time.*table.*no"]
_K_SCREEN_CD = [r"(scr|screen).*(cd|code)"]
# CGV tcscnsGradCd (IMAX='03'). 관람등급 필드와 혼동하지 않게 구체적 패턴 우선
_K_GRADE = [r"tcscns.*grad", r"scns.*grad.*cd", r"grad.*cd"]


def _looks_like_showtime(item: dict) -> bool:
    start_k = _find_key(item, _K_START)
    if not start_k:
        return False
    return bool(_TIME_RE.match(str(item.get(start_k, "")).strip()))


def _showtime_from_item(item: dict, date_hint: str) -> Showtime:
    def g(patterns: list[str]) -> Any:
        k = _find_key(item, patterns)
        return item.get(k) if k else None

    date_v = g(_K_DATE)
    remaining = _to_int(g(_K_REMAIN))
    total = _to_int(g(_K_TOTAL))
    return Showtime(
        date=norm_date(date_v) if date_v else date_hint,
        start_time=norm_time(g(_K_START) or ""),
        movie=str(g(_K_MOVIE) or "").strip(),
        hall=str(g(_K_HALL) or "").strip(),
        grade=str(g(_K_GRADE) or "").strip(),
        screening_id=str(g(_K_SCN_ID) or "").strip(),
        remaining=remaining,
        total=total,
        raw=item,
    )


def extract_showtimes_auto(data: Any, date_hint: str) -> list[Showtime]:
    """응답 JSON에서 상영 회차 목록을 휴리스틱으로 추출."""
    best: list[dict] = []
    best_score = -1.0
    for path, lst in walk_lists(data):
        hits = [x for x in lst if _looks_like_showtime(x)]
        if not hits:
            continue
        # 시간 필드 외에 영화/상영관/좌석수 필드가 있을수록 점수 가산
        sample = hits[0]
        score = len(hits) + sum(
            2.0
            for pats in (_K_MOVIE, _K_HALL, _K_REMAIN)
            if _find_key(sample, pats)
        )
        if score > best_score:
            best_score, best = score, hits
    return [_showtime_from_item(x, date_hint) for x in best]


def extract_showtimes_mapped(
    data: Any, list_path: str, fields: dict, date_hint: str
) -> list[Showtime]:
    lst = get_path(data, list_path)
    if not isinstance(lst, list):
        return []
    out = []
    for item in lst:
        if not isinstance(item, dict):
            continue
        date_v = item.get(fields.get("date", ""), "")
        out.append(
            Showtime(
                date=norm_date(date_v) if date_v else date_hint,
                start_time=norm_time(item.get(fields.get("start_time", ""), "")),
                movie=str(item.get(fields.get("movie", ""), "") or "").strip(),
                hall=str(item.get(fields.get("hall", ""), "") or "").strip(),
                grade=str(item.get(fields.get("grade", ""), "") or "").strip(),
                screening_id=str(item.get(fields.get("screening_id", ""), "") or "").strip(),
                remaining=_to_int(item.get(fields.get("remaining", ""))),
                total=_to_int(item.get(fields.get("total", ""))),
                raw=item,
            )
        )
    return out


# ----------------------------------------------------------------- 좌석 (seats)

_K_SEAT_ID = [r"seat.*(nm|name|no|id|loc)", r"^seat$"]
_K_SEAT_ROW = [r"(row|rank).*(nm|name|no)?$", r"seat.*row"]
_K_SEAT_COL = [r"(col|num).*(nm|name|no)?$", r"seat.*(col|num)"]
_K_SEAT_STATUS = [r"(seat)?.*(stat|state|st.?cd|yn|able|sold|use)", r"^status$"]

# 상태값 해석: 예매 가능으로 볼 값들 / 불가로 볼 값들
_AVAILABLE_VALUES = {"y", "true", "1", "able", "available", "00", "aab01"}
_TAKEN_HINTS = ("sold", "sale", "hold", "lock", "disable", "n", "impossible")


def _seat_available(v: Any) -> bool:
    s = str(v).strip().lower()
    if s in _AVAILABLE_VALUES:
        return True
    return not any(h in s for h in _TAKEN_HINTS)


def _seat_from_item(item: dict) -> Optional[Seat]:
    sid_k = _find_key(item, _K_SEAT_ID)
    sid = str(item.get(sid_k, "")).strip() if sid_k else ""
    if not _SEAT_ID_RE.match(sid):
        row_k = _find_key(item, _K_SEAT_ROW)
        col_k = _find_key(item, _K_SEAT_COL)
        if row_k and col_k:
            sid = f"{str(item.get(row_k, '')).strip()}{str(item.get(col_k, '')).strip()}"
    if not _SEAT_ID_RE.match(sid):
        return None
    status_k = _find_key(item, _K_SEAT_STATUS)
    available = _seat_available(item.get(status_k)) if status_k else True
    return Seat(seat_id=sid.replace(" ", "").replace("-", "").upper(), available=available)


def extract_seats_auto(data: Any) -> list[Seat]:
    """응답 JSON에서 좌석 목록을 휴리스틱으로 추출. 가장 큰 좌석 리스트를 채택."""
    best: list[Seat] = []
    for path, lst in walk_lists(data):
        seats = [s for s in (_seat_from_item(x) for x in lst) if s]
        # 리스트 대부분이 좌석 형태여야 채택 (엉뚱한 리스트 오인 방지)
        if len(seats) >= max(3, int(len(lst) * 0.5)) and len(seats) > len(best):
            best = seats
    return best


def extract_seats_mapped(data: Any, list_path: str, fields: dict) -> list[Seat]:
    lst = get_path(data, list_path)
    if not isinstance(lst, list):
        return []
    out = []
    for item in lst:
        if not isinstance(item, dict):
            continue
        sid = str(item.get(fields.get("seat_id", ""), "") or "").strip()
        if not sid and fields.get("row") and fields.get("col"):
            sid = f"{item.get(fields['row'], '')}{item.get(fields['col'], '')}".strip()
        if not sid:
            continue
        status_field = fields.get("status")
        available = _seat_available(item.get(status_field)) if status_field else True
        out.append(Seat(seat_id=sid.replace(" ", "").upper(), available=available))
    return out
