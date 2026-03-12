# 국내 증시 자동매매 운영 Runbook v0.1

작성일: 2026-03-12  
문서 상태: Draft  
인코딩: UTF-8

---

# 1. 목적

본 문서는 현재 구현된 자동매매 시스템의 운영 절차, 사전 점검 항목, 장중 모니터링, 장애 대응 절차를 정리한다.

본 문서는 현재 코드 상태를 기준으로 작성한다.  
즉, 구현이 끝난 항목과 아직 운영 자동화가 덜 끝난 항목을 구분해서 기록한다.

---

# 2. 현재 운영 가능 범위

현재 확인 완료된 항목:

- KIS 모의투자 REST 인증 및 토큰 자동 발급
- KIS 모의투자 잔고 조회
- Telegram 알림 전송
- KIS 시세 WebSocket 연결 및 quote 수신
- KIS 주문통보 WebSocket 구독
- AES 암호화 주문통보 복호화
- 매수 주문통보 `order/fill` 실수신 및 파싱
- 매도 주문통보 `order/fill` 실수신 및 파싱
- SQLite 상태 저장
- 주문/포지션 상태 전이
- 복구, fail-safe, 거래일/휴장일 처리
- 메인 실행 엔트리포인트
- 단일 cycle 실행 옵션과 graceful shutdown

현재 제한 사항:

- [`python -m auto_trading`](../src/auto_trading/__main__.py)는 정식 엔트리포인트로 동작한다.
- Windows PowerShell용 launcher 스크립트는 제공되지만, 작업 스케줄러/서비스 등록 자동화는 아직 없다.
- 운영 로그 조회 대시보드와 자동 재시작 정책은 아직 없다.

운영 판단:

- 모의투자 환경 운영 준비도는 높다.
- 실전 운영 전에는 launcher 스크립트와 운영 편의 명령을 추가하는 편이 좋다.

---

# 3. 사전 준비

## 3.1 필수 파일

- [`.env`](../.env)
- [`data/universe_master.csv`](../data/universe_master.csv)
- [`data/krx_holidays.csv`](../data/krx_holidays.csv)

## 3.2 필수 환경값

필수:

- `AUTO_TRADING_ENV`
- `AUTO_TRADING_KIS_APP_KEY`
- `AUTO_TRADING_KIS_APP_SECRET`
- `AUTO_TRADING_KIS_CANO`
- `AUTO_TRADING_KIS_ACNT_PRDT_CD`
- `AUTO_TRADING_KIS_USER_ID`
- `AUTO_TRADING_TELEGRAM_BOT_TOKEN`
- `AUTO_TRADING_TELEGRAM_CHAT_ID`

선택:

- `AUTO_TRADING_KIS_ACCESS_TOKEN`
- `AUTO_TRADING_KIS_REFRESH_TOKEN`
- `AUTO_TRADING_HOLIDAY_API_SERVICE_KEY`

권장:

- `AUTO_TRADING_ENV=demo`로 먼저 검증
- `AUTO_TRADING_KIS_BASE_URL`, `AUTO_TRADING_KIS_WS_URL`는 비우거나 삭제해도 기본값이 적용됨

## 3.3 Python 환경

필수 패키지:

- `pycryptodome`
- `websocket-client`

설치 예시:

```powershell
python -m pip install pycryptodome websocket-client
```

---

# 4. 장 시작 전 체크리스트

운영자는 장 시작 전에 아래 항목을 확인한다.

- `.env`가 모의투자 또는 실전 목적에 맞게 설정되어 있는지 확인
- `KIS APP KEY/SECRET`, `계좌번호`, `HTS ID`가 올바른지 확인
- Telegram 알림이 정상 도착하는지 확인
- `universe_master.csv`가 존재하는지 확인
- `krx_holidays.csv`가 존재하는지 확인
- DB 파일 경로가 쓰기 가능한지 확인
- 전일 미체결 주문이 없는지 확인
- 전일 `UNKNOWN`, `ERROR` 상태 주문/포지션이 없는지 확인
- 장전 REST/WS 연결 스모크 테스트가 성공하는지 확인

장전 점검 권장 순서:

1. 토큰 발급 확인
2. 잔고 조회 확인
3. Telegram 전송 확인
4. 시세 WS 연결 확인
5. 주문통보 WS 구독 확인
6. 보유/미체결 조회 확인

---

# 5. 권장 장전 절차

## 5.1 휴장일 파일 갱신

필요 시:

```powershell
python scripts/generate_holiday_calendar.py --year 2026 --output data/krx_holidays.csv
```

## 5.2 종목 마스터 갱신

필요 시:

```powershell
python scripts/generate_universe_master.py
```

## 5.3 런타임 스모크 테스트

권장 확인 항목:

- KIS 토큰 자동 발급 성공
- 잔고 조회 성공
- Telegram 알림 성공
- 시세 구독 성공
- 주문통보 구독 성공

---

# 6. 장중 운영 체크리스트

장중에는 아래 항목을 주기적으로 본다.

- `system_events`에 `ERROR`, `CRITICAL` 이벤트가 누적되는지 확인
- `UNKNOWN` 상태 주문이 생기는지 확인
- `ERROR` 상태 포지션이 생기는지 확인
- Telegram 알림이 끊기지 않는지 확인
- WebSocket heartbeat가 정상인지 확인
- 보유 종목 수가 `max_positions` 범위를 넘지 않는지 확인
- 동일 종목 중복 포지션이 없는지 확인
- 장중 신규 주문 차단 여부가 fail-safe로 걸리지 않았는지 확인

권장 조회 명령:

```powershell
pwsh -File scripts/show_auto_trading_dashboard.ps1
```

우선 확인 대상 이벤트:

- `broker_exception`
- `order_unknown`
- `reconcile_failed`
- `position_mismatch`
- `notification_failed`

---

# 7. 장후 체크리스트

- 당일 체결 내역과 `fills` 저장 내역 비교
- `trade_logs` 생성 여부 확인
- 남아 있는 미체결 주문 확인
- `UNKNOWN`, `ERROR` 상태 존재 여부 확인
- Telegram 알림 실패 여부 확인
- 익일 장전용 `holiday/universe` 입력 파일 상태 확인

---

# 8. 장애 대응 절차

## 8.1 WebSocket 끊김

증상:

- quote 수신 중단
- 주문통보 수신 중단
- heartbeat 누락

대응:

1. 신규 주문 차단 확인
2. REST fallback 모드 진입 확인
3. `reconcile_unknown_orders()` 수행 여부 확인
4. 재연결 성공 여부 확인
5. 재연결 후 quote/order 이벤트 재수신 확인

## 8.2 주문 상태 `UNKNOWN`

증상:

- 주문 응답 타임아웃
- 브로커 응답 불명확
- 주문통보 미수신

대응:

1. 동일 종목 신규 주문 금지
2. 미체결 주문 조회
3. 당일 체결 조회
4. `ACKNOWLEDGED / PARTIALLY_FILLED / FILLED / CANCELED / REJECTED`로 보정
5. 보정 실패 시 운영자 점검

## 8.3 포지션 상태 `ERROR`

증상:

- 로컬 보유와 브로커 보유 불일치
- 주문은 종료됐는데 포지션 상태가 애매함

대응:

1. 브로커 보유 종목 조회
2. 로컬 `positions` 비교
3. `OPEN / READY / CLOSED` 중 하나로 보정
4. `system_events` 확인
5. 복구 전 신규 진입 금지

## 8.4 Telegram 실패

증상:

- `notification_failed`

대응:

1. 봇 토큰/채팅 ID 확인
2. 네트워크 상태 확인
3. `system_events`로 장애 추적 지속
4. 알림 채널 복구 전에는 수동 모니터링 강화

---

# 9. 운영 명령 예시

## 9.1 단위 테스트

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

## 9.2 상태 확인

```powershell
pwsh -File scripts/status_auto_trading.ps1
```

## 9.3 시작

```powershell
pwsh -File scripts/start_auto_trading.ps1
```

## 9.4 중지

```powershell
pwsh -File scripts/stop_auto_trading.ps1
```

## 9.5 대시보드 조회

```powershell
pwsh -File scripts/show_auto_trading_dashboard.ps1
```

## 9.6 단일 cycle 실행

```powershell
python -m auto_trading --once --no-startup-recovery
```

## 9.7 일반 실행

```powershell
python -m auto_trading
```

## 9.8 문법 확인

```powershell
python -m compileall src tests
```

## 9.9 유니버스 마스터 생성

```powershell
python scripts/generate_universe_master.py
```

## 9.10 휴장일 파일 생성

```powershell
python scripts/generate_holiday_calendar.py --year 2026 --output data/krx_holidays.csv
```

---

# 10. 운영 중 확인할 DB 테이블

핵심 테이블:

- `positions`
- `orders`
- `fills`
- `trade_logs`
- `system_events`
- `strategy_snapshots`

운영자가 우선 확인할 테이블 목적:

- `positions`: 현재 보유 상태 확인
- `orders`: 주문 상태 확인
- `fills`: 실제 체결 누적 확인
- `trade_logs`: 진입~청산 이력 확인
- `system_events`: 장애/경고 추적
- `strategy_snapshots`: 진입 판단 근거 추적

---

# 11. 운영 승인 기준

모의투자 운영 승인 기준:

- REST 토큰 자동 발급 성공
- 잔고 조회 성공
- Telegram 전송 성공
- quote WS 수신 성공
- order notice WS 구독 성공
- AES 복호화 성공
- 매수/매도 order/fill 파싱 성공
- `UNKNOWN` 주문 복구 테스트 성공
- 전체 테스트 통과

실전 운영 전 추가 승인 기준:

- 운영자 개입 절차 문서화
- 실전 계좌 최소 수량 검증
- 재시작 복구 리허설 완료

참고 문서:

- [`docs/auto_trading_real_ops_checklist_v0_1.md`](./auto_trading_real_ops_checklist_v0_1.md)
- [`docs/auto_trading_manual_intervention_runbook_v0_1.md`](./auto_trading_manual_intervention_runbook_v0_1.md)
- [`docs/auto_trading_scheduler_and_orchestrator_guide_v0_1.md`](./auto_trading_scheduler_and_orchestrator_guide_v0_1.md)

---

# 12. 현재 권장 다음 작업

현재 시점에서 운영 관점의 다음 우선순위는 아래와 같다.

1. 운영 대시보드 출력 확장
2. 실전 첫날 운영 회고 템플릿 추가
