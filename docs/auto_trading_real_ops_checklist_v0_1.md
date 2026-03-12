# 국내 증시 자동매매 실전 전환 Checklist v0.1

작성일: 2026-03-12  
문서 상태: Draft  
인코딩: UTF-8

---

# 1. 목적

본 문서는 `demo` 환경에서 검증된 자동매매 시스템을 `real` 환경으로 전환할 때 필요한 사전 점검, 전환 절차, 금지사항, 전환 당일 체크리스트를 정리한다.

이 문서는 “실전 계좌에 주문이 실제로 나가는 순간”을 기준으로 작성한다.

---

# 2. 전환 전 원칙

- 실전 전환은 반드시 모의투자 검증 완료 후 진행한다.
- 실전 첫 운영일은 최소 주문 수량으로 시작한다.
- 첫 실전일에는 운영자가 장중 지속 모니터링을 수행한다.
- `UNKNOWN`, `ERROR`, Telegram 장애가 있는 상태에서는 실전 전환하지 않는다.
- 장전 복구, 주문통보 WebSocket, Telegram 알림이 모두 정상인 경우에만 전환한다.

---

# 3. 실전 전환 전 필수 완료 항목

아래 항목이 모두 완료되어야 한다.

- 모의투자 REST 인증/잔고 조회 성공
- 모의투자 quote WebSocket 수신 성공
- 모의투자 order notice WebSocket 수신 성공
- AES 주문통보 복호화 성공
- 매수 `order/fill` 실수신 검증 완료
- 매도 `order/fill` 실수신 검증 완료
- launcher 스크립트 시작/중지 검증 완료
- 대시보드 조회 검증 완료
- 전체 테스트 통과

권장 확인 명령:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
pwsh -File scripts/show_auto_trading_dashboard.ps1
```

---

# 4. `.env` 실전 전환 항목

실전 전환 시 `.env`에서 확인할 값:

- `AUTO_TRADING_ENV=real`
- `AUTO_TRADING_KIS_APP_KEY`
- `AUTO_TRADING_KIS_APP_SECRET`
- `AUTO_TRADING_KIS_CANO`
- `AUTO_TRADING_KIS_ACNT_PRDT_CD`
- `AUTO_TRADING_KIS_USER_ID`
- `AUTO_TRADING_TELEGRAM_BOT_TOKEN`
- `AUTO_TRADING_TELEGRAM_CHAT_ID`

권장:

- `AUTO_TRADING_KIS_BASE_URL`, `AUTO_TRADING_KIS_WS_URL`는 비워두거나 삭제해서 기본 실전 URL 사용
- 실전 전환 직전 `.env`를 다시 검토
- 실전/모의 `.env`를 별도 보관하는 경우 파일 혼동 방지

주의:

- 실전 계좌번호와 모의 계좌번호 혼용 금지
- 실전 HTS ID와 모의 HTS ID 혼용 금지

---

# 5. 실전 전환 금지 조건

아래 조건 중 하나라도 있으면 실전 전환 금지:

- `system_events` 최근 1일 내 `CRITICAL` 존재
- `UNKNOWN` 주문 존재
- `ERROR` 포지션 존재
- Telegram 알림 실패 지속
- quote WebSocket 수신 불안정
- order notice WebSocket 구독 실패
- 장전 `recovery` 실패
- 휴장일 파일 또는 유니버스 파일 누락
- 운영자가 장중 모니터링 불가능

---

# 6. 실전 전환 전 리허설

실전 전환 전 최소 1회 아래 리허설을 권장한다.

1. `.env`를 실전 값과 같은 형식으로 준비
2. `AUTO_TRADING_ENV=demo` 상태에서 launcher로 실행
3. 장전 recovery 확인
4. quote/order notice WebSocket 연결 확인
5. 대시보드 조회 확인
6. 장중 장애 가정
7. `UNKNOWN` / `ERROR` 대응 절차 문서 확인

리허설 확인 항목:

- start/stop/status 스크립트 동작
- 대시보드 출력
- Telegram 알림 도착
- DB 상태 저장

---

# 7. 실전 전환 당일 체크리스트

## 7.1 장 시작 30분 전

- `.env`가 실전 값으로 설정되어 있는지 확인
- 실전 계좌 잔고 조회 확인
- Telegram 테스트 전송 확인
- `pwsh -File scripts/show_auto_trading_dashboard.ps1` 실행
- `UNKNOWN`, `ERROR` 상태가 없는지 확인
- `universe_master.csv` 존재 확인
- `krx_holidays.csv` 존재 확인

## 7.2 장 시작 15분 전

- `python -m auto_trading --once` 또는 launcher 1회 실행으로 recovery 확인
- quote WebSocket 연결 확인
- order notice WebSocket 구독 확인
- 로그 파일 경로 확인

## 7.3 장 시작 직전

- 운영자 모니터링 준비 완료
- Telegram 알림 채널 확인 완료
- 첫 주문 수량 최소화 확인
- 긴급 중지 방법 확인

---

# 8. 실전 첫날 운용 원칙

- 첫날은 최소 수량으로만 시작
- 공격적인 종목 수 확장은 금지
- 장중 운영자는 대시보드를 주기적으로 확인
- 첫 체결 이후 `fills`, `orders`, `positions`를 즉시 확인
- 이상 시 신규 진입 차단 후 수동 점검

권장 운영 확인 명령:

```powershell
pwsh -File scripts/status_auto_trading.ps1
pwsh -File scripts/show_auto_trading_dashboard.ps1
```

---

# 9. 긴급 중지 절차

실전 장중 이상 발생 시:

1. 신규 주문 차단 상태 확인
2. 즉시 프로세스 상태 확인
3. 필요 시 아래 명령으로 중지

```powershell
pwsh -File scripts/stop_auto_trading.ps1
```

4. 브로커 HTS/MTS에서 미체결 주문 직접 확인
5. 보유 종목과 로컬 `positions` 비교
6. `system_events` 확인
7. 원인 파악 전 자동 재시작 금지

---

# 10. 실전 전환 후 확인해야 할 것

전환 후 첫 1일 동안 중점 확인 항목:

- Telegram 알림 누락 여부
- quote/order notice WebSocket 끊김 여부
- `UNKNOWN` 주문 발생 여부
- `ERROR` 포지션 발생 여부
- SQLite 기록 누락 여부
- 체결 시각과 로컬 기록 시각 차이

---

# 11. 실전 운영 승인 기준

실전 운영은 아래 조건을 모두 만족할 때만 승인:

- 모의투자 매수/매도 실주문 검증 완료
- launcher 스크립트 검증 완료
- 대시보드 조회 스크립트 검증 완료
- recovery 리허설 완료
- 운영자 개입 절차 숙지 완료
- 실전 `.env` 검토 완료
- Telegram 실전 채널 확인 완료
- 첫날 최소 주문 수량 정책 확정

---

# 12. 실전 전환 후 다음 작업

실전 전환 문서화 이후 권장 작업:

1. 장애 시 수동 개입 runbook 상세화
2. Windows 작업 스케줄러 등록 가이드 추가
3. 운영 로그 대시보드 확장
4. 실전 첫날 회고 문서 추가
