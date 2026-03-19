# auto_trading WS Responsibility Spec v0.1

작성일: 2026-03-19  
문서 상태: Draft

---

# 1. 문서 목적

본 문서는 REST 전환 이후 WebSocket의 역할 범위를 명확히 정의한다.

---

# 2. 기본 원칙

현재 구조에서 전략 판단의 핵심 시세 수집은 REST가 담당한다.

WebSocket은 다음 역할만 핵심 책임으로 가진다.

- 주문 접수 / 거절 / 체결 통보 수신
- 주문 상태 이벤트를 `OrderEngine`으로 전달
- WS 연결 heartbeat 및 reconnect 감시

즉, 전략 스코어 계산과 보유 종목 매수/매도 판단은 WS에 의존하지 않는다.

---

# 3. WS 핵심 책임

## 3.1 유지 범위

- `subscribe_order_events()`
- 주문통보 파싱
- fill / order event 생성
- runtime reconnect
- WS 장애 시 REST fallback을 통한 주문 상태 reconcile 보조

## 3.2 비핵심 범위

아래는 더 이상 전략 핵심 경로가 아니다.

- quote tick 수신
- quote 기반 market-data cache 갱신
- quote subscription 유지 여부

quote 관련 코드는 현재 호환성과 테스트 자산을 위해 일부 남아 있을 수 있으나, 운영상의 핵심 경로로 간주하지 않는다.

---

# 4. 코드 기준 책임 분리

## `src/auto_trading/app/runtime.py`

- WS 연결 및 order notice 구독 담당
- `quote` 이벤트는 무시하고 `order` / `fill` 이벤트만 `OrderEngine`에 전달

## `src/auto_trading/app/scheduler.py`

- market scan / exit 판단 전 REST refresh 수행
- quote subscription을 호출하지 않음

## `src/auto_trading/broker/kis_ws_client.py`

- order notice / quote 파싱 기능 보유
- 운영상 핵심은 order notice 처리
- quote 관련 메서드는 보조/호환 성격

---

# 5. 운영 해석 규칙

운영 중 다음과 같이 해석한다.

- WS 장애가 나더라도 전략 스코어 계산이 멈추면 안 된다.
- 주문통보 WS가 일시 장애일 때는 fail-safe와 reconcile 경로가 보호한다.
- quote WS 상태는 참고 지표일 수는 있어도, 신규 진입/청산 판단의 핵심 원인이 되면 안 된다.

---

# 6. 후속 정리 가능 항목

향후 더 정리할 수 있는 항목은 다음과 같다.

- quote 파싱 테스트를 호환 레이어 기준으로 재분류
- quote 관련 필드/메서드의 deprecated 표시 검토
- runtime 생성자에서 `market_data_collector` 의존 제거 여부 검토
