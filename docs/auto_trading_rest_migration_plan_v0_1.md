# auto_trading REST 전환 계획 v0.1

작성일: 2026-03-19  
문서 상태: Draft

---

# 1. 목표

주문 체결/주문 상태 통보는 WebSocket을 유지하고, 스코어 계산과 매수/매도 판단에 필요한 시세 수집은 REST 기반으로 전환한다.

핵심 목표는 다음과 같다.

- quote WebSocket 장애가 스코어 계산과 매매 판단을 멈추지 않도록 분리
- `socket is already closed` 반복 상황에서도 장중 스캔이 지속되도록 안정화
- REST 기준으로 동일한 입력 데이터를 재현 가능하게 만들어 디버깅을 쉽게 함

---

# 2. 전환 범위

## 2.1 유지 범위

- 주문 접수/거절/체결 WebSocket
- 주문 체결 Telegram 알림
- 주문 상태 동기화 및 체결 이벤트 처리

## 2.2 REST 전환 범위

- 스코어 계산용 시세 수집
- 보유 종목 익절/손절 판단용 현재가 수집
- 장중 시장 스캔 시 사용하는 최신 snapshot / 최근 bars 구성

---

# 3. 파일별 수정 계획

## `src/auto_trading/broker/kis_client.py`

- `get_daily_bars(symbol, lookback_days)` 추가
- 기존 일봉 차트 REST 응답에서 `open/high/low/close/volume/turnover`를 구조화
- 스코어 계산에 필요한 20일 bars를 REST에서 직접 구성

## `src/auto_trading/market_data/collector.py`

- REST snapshot 저장 메서드 추가
- REST bars 전체 교체 메서드 추가
- quote WS 없이도 cache가 스코어 계산 입력을 제공하도록 보강

## `src/auto_trading/app/bootstrap.py`

- scheduler에 REST market-data refresher 주입
- quote subscription updater는 더 이상 scheduler 핵심 경로로 쓰지 않음
- runtime은 order notice WS만 유지

## `src/auto_trading/app/scheduler.py`

- market scan 직전에 유니버스 + 보유 종목 대상 REST refresh 수행
- exit 판단은 REST snapshot/current price 기준으로 수행
- scoring은 REST daily bars 기준으로 수행

## `src/auto_trading/broker/kis_ws_client.py`

- quote subscription 책임 축소
- order notice WS 유지
- quote 관련 로직은 보조/호환 용도로만 남김

## `tests/unit/test_scheduler.py`

- REST refresh 후 scoring/exit가 동작하는 테스트 추가
- quote subscription 없이도 run_market_scan이 동작하는지 검증

## `tests/unit/test_kis_client.py`

- `get_daily_bars()` 파싱 테스트 추가

---

# 4. 1차 구현 범위

1차 구현은 아래만 포함한다.

- `get_daily_bars()` 추가
- REST 기반 market-data refresher 추가
- scheduler가 REST refresh 결과로 scoring/exit를 수행하도록 수정
- bootstrap에서 scheduler quote subscription 의존 제거

1차 구현에서 제외하는 항목:

- quote WS 완전 삭제
- 장중 분봉/틱 REST 최적화
- 캐시 저장소 영속화
- 호출 횟수 최적화

---

# 5. 1차 작업 순서

1. `kis_client.get_daily_bars()` 구현
2. `market_data.collector`에 REST snapshot/bars 반영 메서드 추가
3. bootstrap에 REST refresher helper 추가
4. scheduler가 market scan 전 REST refresh 수행하도록 변경
5. quote subscription updater를 scheduler 핵심 경로에서 제거
6. 테스트 추가 및 전체 회귀 확인

---

# 6. TODO

- [x] `get_daily_bars()` 구현
- [x] REST snapshot 반영 메서드 추가
- [x] REST bars 반영 메서드 추가
- [x] scheduler REST refresh 주입
- [x] scheduler scoring/exit가 REST cache 기반으로 동작하도록 수정
- [x] bootstrap에서 quote subscription 의존 제거
- [x] 단위 테스트 추가
- [x] 전체 테스트 통과 확인
