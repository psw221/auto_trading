# auto_trading REST 운영 체크리스트 v0.1

작성일: 2026-03-19  
문서 상태: Draft

---

# 1. 문서 목적

본 문서는 REST 기반 시세 수집 전환 이후, 장중 운영자가 확인해야 할 핵심 점검 항목을 정리한다.

---

# 2. 장 시작 전 확인

- 작업 스케줄러 또는 런타임 프로세스가 정상 시작되었는지 확인
- `pwsh -File scripts/status_auto_trading.ps1`
- `pwsh -File scripts/show_auto_trading_dashboard.ps1`
- `[latest_market_scan]`과 `[latest_market_data_refresh]`가 최근 시각으로 갱신되는지 확인
- `active_positions`, `unknown_orders`, `error_positions` 값 확인
- 최근 `recent_market_data_failures`가 반복되는지 확인

---

# 3. 장중 확인 포인트

아래 항목을 우선 본다.

## 3.1 market scan 상태

- `universe_count`
- `scored_count`
- `qualified_count`
- `top_candidate_count`

정상 기대값:

- `universe_count > 0`
- `scored_count`가 지속적으로 0으로 고정되지 않을 것

## 3.2 market-data refresh 상태

- `requested_count`
- `attempted_count`
- `refreshed_count`
- `skipped_count`
- `failed_count`
- `stale_symbol_count`

정상 해석:

- `skipped_count`는 캐시 재사용이므로 반드시 이상은 아님
- `failed_count=0`이 이상적
- `stale_symbol_count=0`이 이상적

주의 해석:

- `failed_count`가 연속 증가하면 REST 장애 가능성 검토
- `stale_symbol_count > 0`가 지속되면 exit 판단 보류 가능성 점검

## 3.3 Telegram 시스템 알림

다음 형태의 메시지가 오면 REST market-data 이상으로 해석한다.

- `REST refresh failed=...`
- `stale=...`
- `failed_symbols=...`
- `stale_symbols=...`

같은 내용은 5분 이내 중복 전송되지 않도록 제한되어 있다.

---

# 4. 이상 발생 시 대응

## 4.1 `failed_count` 증가

- API 호출 제한 또는 일시 장애 여부 점검
- KIS REST 응답 이상 여부 확인
- stderr 로그와 `recent_market_data_failures` 확인

## 4.2 `stale_symbol_count` 증가

- 보유 종목 stale 여부 우선 확인
- stale가 지속되면 해당 종목 exit 판단이 보류될 수 있음
- 필요 시 수동 모니터링 강화

## 4.3 `scored_count=0` 지속

- `latest_market_data_refresh`가 갱신되는지 확인
- `failed_count`, `stale_symbol_count`를 같이 확인
- 유니버스 자체가 비어 있는지 함께 확인

---

# 5. 장 마감 후 확인

- 데일리 리포트 수신 여부 확인
- `recent_market_data_failures`와 `recent_errors`에 반복 패턴이 있었는지 확인
- 필요 시 `show_daily_report.ps1`로 수동 리포트 재생성

---

# 6. 운영 원칙

- quote WS 장애만으로 전략 판단이 멈추면 안 된다.
- 운영 판단은 `latest_market_scan`과 `latest_market_data_refresh`를 함께 보고 내린다.
- 보유 종목의 stale data는 신규 타깃 부재보다 우선순위가 높다.
