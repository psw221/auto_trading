# auto_trading Exit Data Quality Spec v0.1

작성일: 2026-03-19  
문서 상태: Draft

---

# 1. 문서 목적

본 문서는 REST 기반 시세 수집 이후, 보유 종목 exit 판단 시 데이터 품질 부족 상황에서 어떤 exit를 허용하고 어떤 exit를 보류할지 정의한다.

---

# 2. 기본 원칙

exit 판단은 다음 두 가지를 분리해서 본다.

- 가격 기반 exit
- 지표 기반 exit

그리고 snapshot 품질은 다음 두 축으로 본다.

- `is_stale`: 현재 가격 snapshot이 오래되었는지 여부
- `indicators_ready`: 지표 계산에 필요한 bars가 충분한지 여부

기본 원칙은 다음과 같다.

- 가격 기반 exit는 `fresh snapshot`일 때만 허용한다.
- 지표 기반 exit는 `fresh snapshot + indicators_ready`일 때만 허용한다.
- 시간 기반 exit는 데이터 품질이 부족해도 허용한다.

---

# 3. exit 유형별 허용 규칙

## 3.1 `stop_loss`

허용 조건:

- `snapshot.price > 0`
- `snapshot.is_stale == False`

불허 조건:

- snapshot 가격이 없을 때
- snapshot이 stale일 때

이유:

- 오래된 가격으로 손절을 실행하면 의도와 다른 시점에 시장가 청산이 나갈 수 있다.
- 손절은 빠른 보호가 중요하지만, 잘못된 stale 가격으로 발동하는 것보다는 fresh price를 기다리는 쪽이 더 안전하다.

## 3.2 `take_profit`

허용 조건:

- `snapshot.price > 0`
- `snapshot.is_stale == False`

불허 조건:

- snapshot 가격이 없을 때
- snapshot이 stale일 때

이유:

- 익절은 stale 가격 기준으로 LIMIT 주문을 생성하면 잘못된 가격으로 주문이 나갈 위험이 있다.
- 따라서 fresh snapshot일 때만 허용한다.

## 3.3 `ma5_breakdown`

허용 조건:

- `snapshot.price > 0`
- `snapshot.is_stale == False`
- `snapshot.indicators_ready == True`
- `snapshot.ma5 > 0`

불허 조건:

- bars 부족으로 `indicators_ready == False`
- snapshot이 stale일 때
- `ma5 <= 0`

이유:

- `ma5_breakdown`은 가격 하나만으로 판단할 수 없고, 최근 bars 품질이 충분해야 한다.
- 지표형 exit는 데이터 품질이 확보되지 않으면 보수적으로 보류한다.

## 3.4 `time_exit`

허용 조건:

- `holding_days > 5`

불허 조건:

- 없음. 단, 주문 제출 단계의 브로커 예외는 별도로 처리한다.

이유:

- `time_exit`는 시장 구조 신호가 아니라 보유 기간 정책이므로 stale snapshot이어도 허용한다.
- 다만 주문 가격 결정은 별도 주문 타입 정책을 따른다.

---

# 4. bars 부족 시 해석 규칙

bars 부족은 일반적으로 아래 상황을 뜻한다.

- 장중 재시작 직후
- 일부 종목의 REST refresh가 아직 충분히 쌓이지 않은 상태
- 신규 보유 종목으로 아직 지표 계산 이력이 짧은 상태

이때의 정책은 다음과 같다.

- 허용:
  - `stop_loss` if fresh price available
  - `take_profit` if fresh price available
  - `time_exit`
- 불허:
  - `ma5_breakdown`
  - 기타 MA / RSI / ATR 등 지표형 exit

즉 bars가 부족해도 가격 기반 보호는 유지하고, 지표형 판단만 보류한다.

---

# 5. stale snapshot 시 해석 규칙

stale snapshot은 마지막 성공 refresh 시각이 `market_data_stale_after_seconds`를 초과한 경우를 뜻한다.

현재 기준:

- `market_data_stale_after_seconds = 120`

이때의 정책은 다음과 같다.

- 허용:
  - `time_exit`
- 불허:
  - `stop_loss`
  - `take_profit`
  - `ma5_breakdown`

즉 stale 상태에서는 가격/지표 기반 exit를 모두 보류하고, 정책성 청산만 허용한다.

---

# 6. 구현 반영 상태

현재 구현은 다음과 같이 반영되어 있다.

- `SignalEngine.evaluate_exit()`
  - stale snapshot이면 `stop_loss`, `take_profit` 불허
  - `indicators_ready == False`이면 `ma5_breakdown` 불허
  - `time_exit`는 stale이어도 허용
- `SchedulerService._build_position_exit_snapshot()`
  - refresh status 기준 `is_stale` 계산
  - bars 개수 기준 `indicators_ready` 계산

---

# 7. 후속 고려사항

향후 검토할 수 있는 확장 항목은 다음과 같다.

- stale snapshot으로 인해 exit가 보류된 경우 system event 기록
- stale 상태가 일정 시간 이상 지속되면 Telegram 경고 전송
- 지표형 exit별 최소 bars 조건 세분화
- `time_exit`의 주문 타입 정책 재검토
