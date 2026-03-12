# 국내 증시 자동매매 수동 개입 Runbook v0.1

작성일: 2026-03-12  
문서 상태: Draft  
인코딩: UTF-8

---

# 1. 목적

본 문서는 자동매매 운영 중 `UNKNOWN 주문`, `ERROR 포지션`, `WS 단절`, `Telegram 장애`, `브로커 응답 이상`이 발생했을 때 운영자가 수동으로 개입하는 절차를 정리한다.

본 문서의 목표는 아래 3가지다.

- 주문 사고 방지
- 로컬 상태와 브로커 상태 정합성 회복
- 자동매매 재개 여부를 보수적으로 판단

---

# 2. 수동 개입 기본 원칙

- 브로커 상태를 로컬 상태보다 우선한다.
- 상태가 불명확하면 신규 주문을 막고 조회를 먼저 한다.
- `UNKNOWN` 주문이 남아 있으면 동일 종목 재주문을 하지 않는다.
- 복구가 끝나기 전까지는 자동매매를 재개하지 않는다.
- 모든 수동 조치는 시간, 이유, 결과를 기록한다.

---

# 3. 공통 초기 대응

장애 유형과 무관하게 아래 순서로 시작한다.

1. [`scripts/status_auto_trading.ps1`](/c:/Dev/Python/auto_trading/scripts/status_auto_trading.ps1)로 프로세스 상태 확인
2. [`scripts/show_auto_trading_dashboard.ps1`](/c:/Dev/Python/auto_trading/scripts/show_auto_trading_dashboard.ps1)로 최근 `ERROR/CRITICAL` 이벤트 확인
3. 자동매매가 계속 실행 중이면 신규 주문 차단 상태인지 확인
4. 브로커 계좌 기준으로 `보유`, `미체결`, `당일 체결`을 확인
5. 수동 개입 기록을 남긴다

권장 기록 항목:

- 발생 시각
- 장애 유형
- 관련 종목
- 관련 주문번호
- 브로커 조회 결과
- 수행한 조치
- 자동매매 재개 여부

---

# 4. 장애 유형별 절차

## 4.1 주문 상태 `UNKNOWN`

증상:

- 주문 응답 타임아웃
- 주문번호 미확정
- 주문통보 미수신
- dashboard에 `unknown_orders > 0`

절차:

1. 동일 종목 신규 주문 중지 확인
2. 브로커 미체결 주문 조회
3. 브로커 당일 체결 조회
4. 해당 주문번호 또는 종목 기준으로 상태를 확정
5. 상태를 아래 중 하나로 정리

- `ACKNOWLEDGED`
- `PARTIALLY_FILLED`
- `FILLED`
- `CANCELED`
- `REJECTED`

판단 기준:

- 미체결 조회에 있으면 `ACKNOWLEDGED` 또는 `PARTIALLY_FILLED`
- 당일 체결에만 있고 잔여 수량이 없으면 `FILLED`
- 미체결/체결 모두 없고 브로커 원주문도 없으면 `CANCELED` 또는 `REJECTED`

금지:

- 상태 확정 전 동일 종목 재주문
- 응답 실패 직후 즉시 동일 주문 재전송

재개 조건:

- `UNKNOWN` 주문이 0건
- 관련 포지션 상태가 브로커 보유와 일치

---

## 4.2 포지션 상태 `ERROR`

증상:

- 로컬 포지션 수량과 브로커 보유 수량 불일치
- 체결은 끝났는데 포지션 상태가 `OPENING` 또는 `CLOSING`에 멈춤
- dashboard에 `error_positions > 0`

절차:

1. 브로커 보유 종목 조회
2. 로컬 [`positions`](/c:/Dev/Python/auto_trading/src/auto_trading/portfolio/models.py)와 비교
3. 주문/체결 이력 조회
4. 아래 기준으로 수동 판단

- 브로커 보유 수량 > 0: `OPEN`
- 브로커 보유 수량 = 0 이고 청산 체결 확인: `CLOSED`
- 브로커 보유 수량 = 0 이고 진입 자체가 없었음: `READY`

5. 불일치 원인이 해소됐는지 dashboard 재확인

주의:

- 포지션 정리가 끝나기 전 신규 진입 금지
- 보유 수량이 모호하면 반드시 브로커 HTS/MTS 화면으로 이중 확인

---

## 4.3 WebSocket 단절

증상:

- quote 수신 중단
- 주문통보 수신 중단
- heartbeat 누락
- `fallback` 상태 지속

절차:

1. 신규 주문 차단 확인
2. REST fallback 동작 여부 확인
3. `UNKNOWN` 주문 재조회 수행
4. 브로커 시세/주문통보 재구독 가능 여부 확인
5. 재연결 성공 후 아래를 확인

- quote 재수신
- order notice 재수신
- 최근 미체결/체결 정합성

수동 중지 기준:

- 장중 2회 이상 반복 단절
- 주문통보 단절과 `UNKNOWN` 주문이 동시에 존재
- 재연결 후에도 order notice가 복구되지 않음

이 경우:

1. [`scripts/stop_auto_trading.ps1`](/c:/Dev/Python/auto_trading/scripts/stop_auto_trading.ps1)로 중지
2. 브로커 기준 미체결과 보유를 수동 점검
3. 원인 파악 전 재기동 금지

---

## 4.4 Telegram 알림 장애

증상:

- `notification_failed`
- Telegram 메시지 미도착

절차:

1. Bot token, chat id 설정 확인
2. 네트워크 연결 확인
3. 최근 `system_events`에서 알림 실패 원인 확인
4. 장애 중에는 dashboard 조회 주기를 줄여 수동 감시 강화

자동매매 중지 기준:

- Telegram만 실패하고 주문/복구는 정상인 경우 즉시 중지는 아님
- 다만 다른 장애와 동반되면 중지 쪽으로 판단

---

## 4.5 브로커 API 응답 이상

증상:

- `broker_exception`
- 잔고/주문/체결 조회 실패 반복
- 토큰 발급 실패

절차:

1. 동일 요청 재시도 전에 장애 범위를 확인
2. 토큰 재발급 가능 여부 확인
3. 잔고 조회와 주문 조회가 모두 실패하면 신규 주문 차단 유지
4. 브로커 API 정상화 전에는 자동매매 재개 금지

중지 기준:

- 인증 실패 지속
- 주문 조회 실패 지속
- 잔고 조회 실패 지속

---

# 5. 재시작 절차

자동매매를 중지한 뒤 재시작할 때는 아래 순서를 따른다.

1. 브로커 HTS/MTS에서 보유와 미체결 확인
2. `UNKNOWN` 주문 존재 여부 확인
3. dashboard로 최근 `ERROR/CRITICAL` 확인
4. 필요 시 `python -m auto_trading --once --no-startup-recovery`로 기초 점검
5. 정상 판단 시 [`scripts/start_auto_trading.ps1`](/c:/Dev/Python/auto_trading/scripts/start_auto_trading.ps1) 실행
6. 재시작 직후 quote/order notice 수신과 dashboard 상태를 다시 확인

---

# 6. 자동매매 재개 승인 기준

아래를 모두 만족할 때만 자동매매를 재개한다.

- `UNKNOWN` 주문 0건
- `ERROR` 포지션 0건
- 브로커 보유 수량과 로컬 포지션 일치
- 브로커 미체결과 로컬 주문 상태 일치
- quote WS 수신 정상
- order notice WS 수신 정상
- 운영자가 수동 점검 결과를 기록함

---

# 7. 권장 운영 명령

상태 확인:

```powershell
pwsh -File scripts/status_auto_trading.ps1
```

대시보드:

```powershell
pwsh -File scripts/show_auto_trading_dashboard.ps1
```

단일 점검 실행:

```powershell
python -m auto_trading --once --no-startup-recovery
```

중지:

```powershell
pwsh -File scripts/stop_auto_trading.ps1
```

재시작:

```powershell
pwsh -File scripts/start_auto_trading.ps1
```

---

# 8. 관련 문서

- [`docs/auto_trading_runbook_v0_1.md`](/c:/Dev/Python/auto_trading/docs/auto_trading_runbook_v0_1.md)
- [`docs/auto_trading_real_ops_checklist_v0_1.md`](/c:/Dev/Python/auto_trading/docs/auto_trading_real_ops_checklist_v0_1.md)
