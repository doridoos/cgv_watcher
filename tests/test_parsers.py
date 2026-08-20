"""auto 파서가 실제 CGV 응답 형태(관측된 필드명)를 잘 뽑아내는지."""

from cgv_watcher import parsers

# 새 CGV 사이트 searchMovScnInfo 응답을 본뜬 픽스처
CGV_SHOWTIMES_FIXTURE = {
    "statusCode": "0000",
    "data": [
        {
            "scnYmd": "20260815",
            "scnsNo": "0136",
            "scnSseq": "3",
            "movNm": "오디세이",
            "scnsrtTm": "1800",
            "scnendTm": "2049",
            "frSeatCnt": 2,
            "cpSeatCnt": 241,
            "tcscnsGradCd": "03",
            "scnsEnm": "IMAX LASER",
        },
        {
            "scnYmd": "20260815",
            "scnsNo": "0132",
            "scnSseq": "5",
            "movNm": "어떤영화",
            "scnsrtTm": "2130",
            "scnendTm": "2320",
            "frSeatCnt": 100,
            "cpSeatCnt": 150,
            "tcscnsGradCd": "00",
            "scnsEnm": "2관",
        },
    ],
}


def test_auto_extract_showtimes():
    shows = parsers.extract_showtimes_auto(CGV_SHOWTIMES_FIXTURE, date_hint="20260815")
    assert len(shows) == 2
    st = shows[0]
    assert st.date == "20260815"
    assert st.start_time == "18:00"
    assert st.movie == "오디세이"
    assert st.hall == "IMAX LASER"
    assert st.remaining == 2
    assert st.total == 241


def test_auto_extract_ignores_end_time():
    shows = parsers.extract_showtimes_auto(CGV_SHOWTIMES_FIXTURE, date_hint="20260815")
    assert shows[0].start_time == "18:00"  # scnendTm(20:49)이 아니라 scnsrtTm


def test_showtime_keys_do_not_collide():
    shows = parsers.extract_showtimes_auto(CGV_SHOWTIMES_FIXTURE, date_hint="20260815")
    assert shows[0].key != shows[1].key


def test_mapped_extract():
    shows = parsers.extract_showtimes_mapped(
        CGV_SHOWTIMES_FIXTURE,
        list_path="data",
        fields={
            "date": "scnYmd",
            "start_time": "scnsrtTm",
            "movie": "movNm",
            "hall": "scnsEnm",
            "remaining": "frSeatCnt",
            "total": "cpSeatCnt",
            "screening_id": "scnSseq",
        },
        date_hint="20260815",
    )
    assert len(shows) == 2
    assert shows[0].screening_id == "3"
    assert shows[0].remaining == 2


SEATS_FIXTURE = {
    "data": {
        "seatList": [
            {"seatNo": "A1", "seatStatCd": "SOLD"},
            {"seatNo": "E6", "seatStatCd": "Y"},
            {"seatNo": "E7", "seatStatCd": "Y"},
            {"seatNo": "F10", "seatStatCd": "HOLD"},
        ]
    }
}


def test_auto_extract_seats():
    seats = parsers.extract_seats_auto(SEATS_FIXTURE)
    by_id = {s.seat_id: s.available for s in seats}
    assert by_id == {"A1": False, "E6": True, "E7": True, "F10": False}


def test_norm_time_variants():
    assert parsers.norm_time("1830") == "18:30"
    assert parsers.norm_time("18:30") == "18:30"
    assert parsers.norm_time("18:30:00") == "18:30"
    assert parsers.norm_time("930") == "09:30"


def test_get_path():
    assert parsers.get_path({"a": {"b": [1, 2]}}, "a.b") == [1, 2]
    assert parsers.get_path({"a": {}}, "a.b.c") is None


# --------------------- 실측 CGV 데이터에서 드러난 버그 회귀 테스트

REAL_CGV = {
    "data": [
        {
            "scnYmd": "20260815", "scnsNo": "018", "scnSseq": "4",
            "movNm": "오디세이", "movFomNm": "IMAX LASER 2D",
            "expoMovNm": "IMAX LASER 2D", "scnsrtTm": "1800", "scnendTm": "2049",
            "frSeatCnt": 2, "cpSeatCnt": 241, "scnsEnm": "IMAX관",
            "tcscnsGradCd": "03",
        },
        {
            "scnYmd": "20260815", "scnsNo": "018", "scnSseq": "6",
            "movNm": "오디세이", "movFomNm": "IMAX LASER 2D",
            "scnsrtTm": "2500", "frSeatCnt": 6, "cpSeatCnt": 241,
            "scnsEnm": "IMAX관", "tcscnsGradCd": "03",
        },
        {
            "scnYmd": "20260815", "scnsNo": "001", "scnSseq": "1",
            "movNm": "다른영화", "movFomNm": "2D", "scnsrtTm": "0900",
            "frSeatCnt": 1, "scnsEnm": "스트레스리스 시네마[CINE de CHEF]",
        },
    ]
}


def test_real_data_uses_title_not_format():
    # 버그: movNm(제목) 대신 movFomNm(포맷 '2D')을 집었었다
    shows = parsers.extract_showtimes_auto(REAL_CGV, "20260815")
    assert shows[0].movie == "오디세이"
    assert shows[0].movie != "IMAX LASER 2D"


def test_real_data_keeps_after_midnight_time():
    # 심야회차 25:00(새벽1시)이 잘리거나 깨지지 않아야
    shows = parsers.extract_showtimes_auto(REAL_CGV, "20260815")
    assert shows[1].start_time == "25:00"


def test_real_data_grade_and_hall():
    shows = parsers.extract_showtimes_auto(REAL_CGV, "20260815")
    assert shows[0].grade == "03"
    assert shows[0].hall == "IMAX관"


def test_real_data_keys_unique_including_hall():
    shows = parsers.extract_showtimes_auto(REAL_CGV, "20260815")
    keys = [s.key for s in shows]
    assert len(keys) == len(set(keys))  # 세 회차 모두 구분됨
