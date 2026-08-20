# CGV 표 감시기 🎬

CGV 특정 극장·영화·상영관(IMAX 등)의 **취소표(빈자리 증가)** 와 **예매 오픈(새 회차)** 을
감시해서 텔레그램으로 알려주는 도구입니다.

[gpters의 "Hermes 봇과 함께한 주말 IMAX관 오디세이 예매하기" 글](https://www.gpters.org/nocode/post/reservation-odyssey-imax-theater-uWBCbeFnbZpeQGh)에서
아이디어를 얻었고, [DongminL/movie_reservation_notification](https://github.com/DongminL/movie_reservation_notification)의
새 CGV 사이트 접근 방식을 참고했습니다.

핵심 동작은 원문 블로그의 요구사항 그대로입니다:

> 최초 관측은 기준값으로만 저장해.
> 현재 예매 가능 좌석 수가 직전보다 클 때만 알려줘.
> 같거나 줄면 아무 메시지도 보내지 마.
> 새로 늘어난 좌석번호도 함께 표시해.
> A~C열만 늘어난 경우는 알리지 마.

알림 예시:

```
🎬 CGV 대구 IMAX ‘오디세이’ 빈자리 증가

- 8월 15일(토) 18:00
  - 2석 → 3석 (+1석)
  - 새 좌석: E6
```

## 동작 흐름

```
데이터 조회 → 조건 필터 → 직전 상태와 비교 → 오탐 필터 → 알림 → 상태 저장
(browser/api)  (영화·관·시간창)  (state.json)     (A~C열 등)  (Telegram)
```

- **browser 모드(기본)** — CGV 새 사이트는 Cloudflare 뒤에 있어 API를 직접 부르면
  403이 나기 쉽습니다(원문 블로그의 첫 403이 바로 이것). 그래서 Playwright 헤드리스
  브라우저로 실제 예매 페이지를 열고, 페이지가 스스로 호출하는 상영시간표
  API(`searchMovScnInfo`) 응답을 가로챕니다. 로그인 불필요.
- **api 모드** — 요청 한 번이면 되는 가벼운 방식. browser 모드 실행 시
  `state/discovered.json`에 남는 실제 API 주소를 config에 옮기면 전환할 수 있습니다.
- **응답 파싱은 auto** — CGV 응답 스키마가 바뀌어도 필드 이름 관습(시각/영화명/잔여석
  등)을 휴리스틱으로 찾아냅니다. 잘못 짚으면 `probe` 명령으로 확인하고 `list_path`와
  `fields`를 명시하면 됩니다.

## 설치

```bash
git clone <이 저장소>
cd cgv_watcher
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # browser 모드(기본)에 필요
```

## 설정

```bash
cp config.example.yaml config.yaml
```

`config.yaml`에서 채울 것 (자세한 주석은 예시 파일 참고):

1. **감시 대상** — 극장 코드(`theater_code`), 영화 키워드, 상영관 키워드(IMAX 등),
   날짜 범위, 시작시간 창.
   - 극장 코드(siteNo)는 CGV에서 그 극장 예매 페이지를 열었을 때 주소창의
     `siteNo=` 값입니다. 예: 용산아이파크몰 = `0013`.
2. **텔레그램** — [@BotFather](https://t.me/BotFather)로 봇을 만들어 토큰을 받고,
   봇에게 아무 메시지나 보낸 뒤 [@userinfobot](https://t.me/userinfobot) 등으로
   자신의 chat_id를 확인해 넣습니다. 비워두면 콘솔 출력만 합니다.
   (환경변수 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`가 있으면 우선합니다.)
3. **오탐 필터** — `alert.ignore_rows: [A, B, C]` — 이 열들"만" 새로 풀린 경우는
   알리지 않습니다. 앞좌석에 관심 없다면 그대로, 다 받고 싶으면 `[]`.

## 사용법

```bash
python -m cgv_watcher test-telegram   # 텔레그램 설정 확인
python -m cgv_watcher probe           # 엔드포인트/파싱 진단 (감시 전 1회 권장)
python -m cgv_watcher once --dry-run  # 알림 없이 1회 조회
python -m cgv_watcher watch           # 감시 시작 (기본 5분 간격)
```

취소표가 곧 풀릴 것 같은 순간(예매 오픈 직후 등)에는 **버스트 모드**:

```bash
python -m cgv_watcher burst -m 20     # 20분 동안만 2분 간격, 이후 자동 복귀
```

실행 중인 `watch` 루프를 재시작할 필요 없이 다음 주기부터 적용되고, 시간이 지나면
스스로 원래 주기로 돌아옵니다. (종료 시각을 코드에 박아두지 않는 것 — 원문 블로그의
"15:00 하드코딩" 사고에서 배운 부분입니다.)

### cron으로 돌리기

상태가 파일(`state/state.json`)에 남으므로 루프 대신 cron도 됩니다:

```cron
*/5 * * * * cd /path/to/cgv_watcher && .venv/bin/python -m cgv_watcher once >> watch.log 2>&1
```

## 좌석 상세(선택): "새 좌석: E6"까지 받으려면

기본 설정은 회차별 **잔여 좌석 수**만으로 감시합니다(이것만으로 취소표 알림은 충분히
동작합니다). 어느 좌석이 풀렸는지, A~C열 필터까지 쓰려면 좌석 조회 API를 찾아
`endpoints.seats`에 넣어야 합니다:

1. 브라우저에서 원하는 회차의 **좌석 선택 화면**까지 진입
2. 개발자도구(F12) → Network 탭에서 좌석 배치를 돌려주는 JSON 요청을 찾기
   (XHR/fetch만 필터, 좌석을 클릭할 때 오가는 요청 위주로)
3. 그 요청의 URL/메서드/본문을 `config.yaml`의 `seats:` 에 옮기기 — 본문 값 중
   회차마다 달라지는 것은 회차 응답의 키 이름으로 플레이스홀더 처리
   (예: `"scnSseq": "{scnSseq}"`)

## 트러블슈팅 (원문 블로그의 교훈들)

| 증상 | 원인/해결 |
|---|---|
| api 모드에서 403 | Cloudflare 차단. "로그인이 필요해서"가 아닙니다 — browser 모드로 바꾸거나, 파라미터·헤더 누락을 확인하세요. **"불가능"과 "미확인"을 구분할 것.** |
| browser 모드에서 캡처 실패 | 페이지 구조나 API 이름이 바뀐 것. 개발자도구로 실제 요청 이름을 확인해 `capture_pattern` 갱신. |
| 회차가 0개로 파싱됨 | `probe`로 리스트 후보와 키 이름을 보고 `list_path`/`fields`를 명시. |
| 알림이 왔는데 원하는 좌석이 아님 | `alert.ignore_rows`를 조정. 오탐은 실패가 아니라 필터 규칙을 선명하게 만드는 데이터입니다. |
| 감시가 도는지 모르겠음 | 로그의 최근 실행 시각, `state/state.json`의 `updated_at`, 실제 알림 도착까지 함께 확인. "코드 내용"이 아니라 "실행 경로"를 검증하세요. |
| 조회가 연속 실패 | 5회 연속 실패 시 텔레그램으로 1회 경고가 갑니다. 조용히 죽은 감시기는 없느니만 못하므로. |

## 주의

- 알림 전용입니다. **자동 예매·좌석 선점 기능은 없고, 넣지 마세요.** 매크로 예매는
  CGV 약관 위반이며 계정 제한 사유입니다.
- 폴링 주기는 최소 60초로 강제되어 있습니다. 기본 5분을 권장합니다.
- CGV 비공개 API는 언제든 바뀔 수 있습니다. 이 도구는 그걸 전제로 설계됐습니다 —
  코드를 고치는 게 아니라 config와 `probe`로 대응하세요.

## 프로젝트 구조

```
cgv_watcher/
├── __main__.py       # CLI (watch / once / probe / burst / test-telegram)
├── config.py         # config.yaml 로드·검증
├── cgv_api.py        # CGV 조회 클라이언트 (api/browser 모드)
├── browser_fetch.py  # Playwright로 페이지 열고 API 응답 가로채기
├── parsers.py        # 응답 JSON 휴리스틱(auto)/명시적 매핑 파싱
├── filters.py        # 회차 매칭, A~C열 오탐 필터, 알림 판단
├── state.py          # 직전 관측값 저장·변화 계산 (state/state.json)
├── notify.py         # 메시지 포맷 + 텔레그램 전송
├── scheduler.py      # 폴링 루프, 버스트 모드
└── watcher.py        # 1사이클: 조회→필터→비교→알림→저장
```
