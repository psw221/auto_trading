# Auto Trading EOD REST Reconcile Plan v0.1

## 1. 목적

장중에는 WebSocket 누락, broker holdings 기반 추정 복구, 주문 상태 지연 반영이 섞일 수 있다.  
이 때문에 장마감 후 로컬 `fills`, `orders`, `trade_logs`, 실현손익 집계가 실제 계좌 기준과 어긋날 수 있다.

이 문서의 목적은 장마감 후 REST API로 당일 체결내역을 다시 조회해서 로컬 거래 원장을 보정하는 방향을 정리하는 것이다.

## 2. 문제 정의

현재 구조에서는 아래 문제가 발생할 수 있다.

- 체결 알림이 WS에서 누락됨
- `broker holdings` 존재/부재를 근거로 주문을 `FILLED`로 추정 복구함
- 실제 체결가/체결시각이 누락되거나 추정값으로 대체됨
- `trade_logs` 청산 이력이 빠지거나 왜곡됨
- 일일 리포트와 기간 실현손익이 실제 계좌와 다르게 계산됨

즉 장중 운영 상태와 장마감 후 손익 확정 상태를 같은 데이터로 처리하는 것이 현재 품질 한계의 핵심이다.

## 3. 목표 상태

장중과 장마감 후 데이터 신뢰 수준을 분리한다.

- 장중:
  - 운영용 상태
  - 빠른 알림과 포지션 추적 우선
  - 일부 추정 복구 허용

- 장마감 후:
  - 확정 손익용 상태
  - REST API 체결내역 기준으로 원장 보정
  - 일일 리포트와 기간 손익은 이 보정 결과를 사용

## 4. 핵심 원칙

1. 당일 체결내역 REST 응답을 장마감 후 authoritative source로 사용한다.
2. 장중 추정 복구는 허용하되, EOD 보정에서 실제 체결 원장으로 다시 맞춘다.
3. `trade_logs`는 EOD 보정 이후 값을 손익 기준 원장으로 본다.
4. 일일 리포트는 가능하면 EOD 보정 완료 후 전송한다.

## 5. EOD 보정 대상

장마감 후 아래 항목을 순서대로 보정한다.

### 5.1 fills

- 당일 `daily_fills` REST 조회
- 로컬 `fills`에 없는 broker fill row를 추가
- 기존 추정 체결이 있더라도 실제 fill 데이터가 있으면 fill 기준으로 우선 반영

### 5.2 orders

- `SUBMITTED`, `ACKNOWLEDGED`, `PARTIALLY_FILLED`, `UNKNOWN` 주문 재확인
- 실제 체결된 주문은 `FILLED`로 확정
- broker open order에도 없고 daily fills에도 없으면 상태를 보수적으로 정리

### 5.3 positions

- 체결 결과 기준으로 `OPEN`, `CLOSED` 상태 재정렬
- qty, avg_entry_price, current_price, closed_at, exit_reason 보정

### 5.4 trade_logs

- 누락된 entry/exit를 backfill
- `exit_order_id`, `exit_price`, `net_pnl`, `pnl_pct`, `exit_at`, `exit_reason` 재보정
- 가능하면 실제 fill 기준으로 손익을 다시 계산

## 6. 기대 효과

- 당일 체결 누락이 일일 리포트까지 이어지는 문제 완화
- 기간 실현손익과 계좌 기준 손익 차이 축소
- `broker holdings` 추정 복구 의존도 감소
- Telegram 체결/복구 알림 품질 개선
- 장중 운영과 EOD 손익 원장을 분리해서 해석 가능

## 7. 구현 범위 제안

### 7.1 1차 구현 범위

가장 먼저 필요한 최소 범위는 아래 4개다.

1. 장마감 후 `daily_fills` 전량 REST 조회
2. 누락 `fills` backfill
3. 누락 `trade_logs` entry/exit backfill
4. EOD 보정 후 일일 리포트 생성

이 범위만 구현해도 손익 신뢰도는 크게 좋아진다.

### 7.2 2차 구현 범위

1차가 안정화되면 아래를 추가한다.

1. `estimated` 체결과 `actual` 체결 구분 저장
2. 실현손익 조회에서 `actual only` / `actual + estimated` 분리
3. EOD 보정 이력 로그 저장
4. 브로커 기준 손익 조회 스크립트 추가

## 8. 파일별 수정 계획

### 8.1 broker

- `src/auto_trading/broker/kis_client.py`
  - 당일 체결 조회 메서드 재사용 또는 확장
  - 장마감 전용 조회 편의 함수 추가 가능

### 8.2 portfolio sync

- `src/auto_trading/portfolio/service.py`
  - EOD 보정 진입점 추가
  - fills/orders/positions/trade_logs 순차 보정 로직 추가

### 8.3 order reconciliation

- `src/auto_trading/orders/engine.py`
  - 장중 reconciliation과 EOD reconciliation 역할 분리
  - 장마감 후에는 fill 우선 확정 경로 사용

### 8.4 repositories

- `src/auto_trading/storage/repositories/orders.py`
  - EOD backfill 대상 주문 조회 helper
- `src/auto_trading/storage/repositories/trade_logs.py`
  - 누락 entry/exit backfill helper
- `src/auto_trading/storage/repositories/fills.py`
  - broker fill dedupe helper 보강 필요 가능

### 8.5 scripts

- `scripts/backfill_realized_pnl.py`
  - 현재 누락된 trade log 보정 스크립트
  - 향후 EOD reconcile 스크립트로 확장 가능
- 신규 권장:
  - `scripts/reconcile_eod_fills.py`
  - `scripts/reconcile_eod_fills.ps1`

### 8.6 reports

- `src/auto_trading/app/dashboard.py`
  - EOD 보정 기준 손익 조회 함수 추가 가능
- `src/auto_trading/notifications/telegram.py`
  - EOD 보정 완료 알림 또는 보정 결과 요약 메시지 추가 가능

## 9. 권장 실행 흐름

### 장중

1. 주문 제출
2. WS/REST 기반 빠른 체결 반영
3. 필요 시 추정 복구 허용

### 장마감 후

1. `daily_fills` REST 조회
2. fills backfill
3. orders 상태 보정
4. positions 상태 보정
5. trade_logs 보정
6. 실현손익 계산
7. 일일 리포트 전송

## 10. 운영상 주의점

1. 장마감 후 보정 전 일일 리포트를 보내면 추정 손익이 들어갈 수 있다.
2. 당일 체결 REST 응답 품질이 좋지 않으면 `estimated` 표기가 필요하다.
3. 기존 `trade_logs`에 이미 왜곡된 과거 이력이 있으면 1회성 backfill만으로 완전 정리가 안 될 수 있다.
4. 손익 비교 시 `D+2 예수금`, `총자산`, `실현손익`은 완전히 같은 지표가 아니라는 점을 문서로 분리해야 한다.

## 11. 추천 다음 작업

1. EOD 체결 보정 스크립트 초안 추가
2. 일일 리포트 전송 시점을 EOD 보정 이후로 이동
3. 실현손익 조회 스크립트에 `actual only` 옵션 추가
4. 과거 왜곡 이력 정리용 1회성 데이터 정합화 작업 수행
