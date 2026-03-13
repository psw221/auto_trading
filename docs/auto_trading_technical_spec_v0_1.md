# 국내 증시 자동매매 프로그램 기술명세서 v0.1

작성일: 2026-03-11
문서 상태: Draft
인코딩: UTF-8
기준 문서: `docs/auto_trading_prd.md`

---

# 1. 목적

본 문서는 PRD를 기반으로 자동매매 시스템의 데이터 저장 구조와 주문/포지션 상태 제어 방식을 정의한다.

본 버전은 아래 범위를 우선 확정한다.

- SQLite 테이블 DDL 초안
- 주문 상태머신
- 포지션 상태머신
- 장애 복구 시 상태 동기화 원칙

---

# 2. 저장소 원칙

- 로컬 저장소는 SQLite를 사용한다.
- 브로커 계좌/주문 조회 결과를 최종 기준 상태로 간주한다.
- 로컬 DB는 운영 상태 캐시와 감사 로그 역할을 수행한다.
- 주문과 체결은 분리 저장한다.
- 전략 판단 근거는 별도 스냅샷으로 저장한다.

---

# 3. SQLite 테이블 DDL 초안

## 3.1 positions

현재 보유 포지션과 청산 진행 상태를 저장한다.

```sql
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    name TEXT,
    strategy_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('READY', 'OPENING', 'OPEN', 'CLOSING', 'CLOSED', 'ERROR')),
    qty INTEGER NOT NULL DEFAULT 0,
    avg_entry_price REAL,
    current_price REAL,
    score_at_entry INTEGER,
    target_weight REAL,
    opened_at TEXT,
    closed_at TEXT,
    exit_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_positions_symbol_status
    ON positions (symbol, status);
```

## 3.2 orders

주문 요청, 정정, 취소, 상태 업데이트를 추적한다.

```sql
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL UNIQUE,
    broker_order_id TEXT,
    env_dv TEXT NOT NULL CHECK (env_dv IN ('real', 'demo')),
    cano TEXT NOT NULL,
    acnt_prdt_cd TEXT NOT NULL,
    parent_order_id INTEGER,
    position_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type TEXT NOT NULL CHECK (order_type IN ('LIMIT', 'MARKET')),
    intent TEXT NOT NULL CHECK (
        intent IN ('ENTRY', 'EXIT', 'STOPLOSS', 'TAKEPROFIT', 'TIMEEXIT', 'REPLACE', 'CANCEL')
    ),
    tr_id TEXT NOT NULL,
    ord_dvsn TEXT NOT NULL,
    exchange_id TEXT NOT NULL DEFAULT 'KRX',
    rvse_cncl_dvsn_cd TEXT,
    qty_all_ord_yn TEXT CHECK (qty_all_ord_yn IN ('Y', 'N')),
    price REAL,
    qty INTEGER NOT NULL,
    filled_qty INTEGER NOT NULL DEFAULT 0,
    remaining_qty INTEGER NOT NULL,
    krx_fwdg_ord_orgno TEXT,
    orig_odno TEXT,
    ord_tmd TEXT,
    status TEXT NOT NULL CHECK (
        status IN (
            'PENDING_CREATE',
            'SUBMITTED',
            'ACKNOWLEDGED',
            'PARTIALLY_FILLED',
            'FILLED',
            'PENDING_REPLACE',
            'REPLACED',
            'PENDING_CANCEL',
            'CANCELED',
            'REJECTED',
            'FAILED',
            'UNKNOWN'
        )
    ),
    submitted_at TEXT,
    last_broker_update_at TEXT,
    broker_rt_cd TEXT,
    broker_msg_cd TEXT,
    broker_msg TEXT,
    failure_reason TEXT,
    request_payload_json TEXT,
    response_payload_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_order_id) REFERENCES orders(id),
    FOREIGN KEY (position_id) REFERENCES positions(id)
);

CREATE INDEX IF NOT EXISTS idx_orders_symbol_status
    ON orders (symbol, status);

CREATE INDEX IF NOT EXISTS idx_orders_position_id
    ON orders (position_id);

CREATE INDEX IF NOT EXISTS idx_orders_broker_order_id
    ON orders (broker_order_id);

CREATE INDEX IF NOT EXISTS idx_orders_orig_odno
    ON orders (orig_odno);
```

### 3.2.1 브로커 필드 매핑 원칙

- `env_dv`
  - 한국투자증권 주문 API의 실전/모의 구분값을 저장한다.
- `cano`, `acnt_prdt_cd`
  - 주문, 정정취소, 잔고조회에서 공통으로 사용하는 계좌 식별자다.
- `tr_id`
  - 브로커 거래 ID를 저장한다.
  - 실전/모의 및 매수/매도/정정취소에 따라 값이 달라진다.
- `ord_dvsn`
  - 브로커 주문구분값을 저장한다.
  - 지정가, 시장가 등 실제 요청값을 그대로 보관한다.
- `exchange_id`
  - 공식 샘플코드 기준 `KRX`, `NXT`, `SOR` 값을 사용한다.
- `krx_fwdg_ord_orgno`, `orig_odno`
  - 정정/취소 호출 시 원주문 식별자로 필요하다.
  - 최초 주문 응답에서 확보 후 반드시 저장해야 한다.
- `qty_all_ord_yn`
  - 정정/취소 시 잔량 전부 주문 여부를 저장한다.
- `request_payload_json`, `response_payload_json`
  - 장애 분석과 재시작 복구를 위해 원본 요청/응답을 저장한다.

초기 구현에서는 브로커 응답 전문 전체를 정규화하지 않고, 핵심 필드는 컬럼으로 분리하고 나머지는 JSON으로 저장한다.

## 3.3 fills

부분 체결과 다중 체결을 저장한다.

```sql
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    broker_fill_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    fill_price REAL NOT NULL,
    fill_qty INTEGER NOT NULL,
    fill_amount REAL NOT NULL,
    filled_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_fills_order_id
    ON fills (order_id);

CREATE INDEX IF NOT EXISTS idx_fills_symbol_filled_at
    ON fills (symbol, filled_at);
```

## 3.4 trade_logs

진입부터 청산까지 하나의 완결된 거래를 기록한다.

```sql
CREATE TABLE IF NOT EXISTS trade_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    entry_order_id INTEGER,
    exit_order_id INTEGER,
    entry_price REAL,
    exit_price REAL,
    qty INTEGER NOT NULL,
    gross_pnl REAL,
    net_pnl REAL,
    pnl_pct REAL,
    entry_at TEXT,
    exit_at TEXT,
    exit_reason TEXT,
    holding_days INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (position_id) REFERENCES positions(id),
    FOREIGN KEY (entry_order_id) REFERENCES orders(id),
    FOREIGN KEY (exit_order_id) REFERENCES orders(id)
);

CREATE INDEX IF NOT EXISTS idx_trade_logs_symbol_entry_at
    ON trade_logs (symbol, entry_at);
```

## 3.5 system_events

시스템 이벤트와 장애 이력을 저장한다.

```sql
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')),
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_system_events_occurred_at
    ON system_events (occurred_at);

CREATE INDEX IF NOT EXISTS idx_system_events_component
    ON system_events (component);
```

## 3.6 strategy_snapshots

매수 후보 또는 매수 실행 시점의 전략 점수와 지표 값을 저장한다.

```sql
CREATE TABLE IF NOT EXISTS strategy_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    snapshot_time TEXT NOT NULL,
    score_total INTEGER NOT NULL,
    volume_score INTEGER,
    momentum_score INTEGER,
    ma_score INTEGER,
    atr_score INTEGER,
    rsi_score INTEGER,
    price REAL NOT NULL,
    ma5 REAL,
    ma20 REAL,
    rsi REAL,
    atr REAL,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_snapshots_symbol_snapshot_time
    ON strategy_snapshots (symbol, snapshot_time);
```

---

# 4. 주문 상태 전이표

## 4.1 주문 상태 정의

| 상태 | 설명 |
|---|---|
| `PENDING_CREATE` | 주문 생성 완료, 아직 브로커 전송 전 |
| `SUBMITTED` | 브로커로 주문 전송 완료 |
| `ACKNOWLEDGED` | 브로커 접수 확인 완료 |
| `PARTIALLY_FILLED` | 일부 수량 체결 |
| `FILLED` | 전량 체결 완료 |
| `PENDING_REPLACE` | 정정 요청 전송 또는 대기 중 |
| `REPLACED` | 정정 완료 후 신규 주문 조건 반영 |
| `PENDING_CANCEL` | 취소 요청 전송 또는 대기 중 |
| `CANCELED` | 주문 취소 완료 |
| `REJECTED` | 브로커 거부 |
| `FAILED` | 내부 처리 실패 |
| `UNKNOWN` | 주문 결과 불명확, 조회 필요 |

## 4.2 허용 전이

| 현재 상태 | 이벤트 | 다음 상태 | 비고 |
|---|---|---|---|
| `PENDING_CREATE` | 브로커 전송 성공 | `SUBMITTED` | 주문 요청 송신 성공 |
| `PENDING_CREATE` | 내부 검증 실패 | `FAILED` | 수량/가격/리스크 조건 오류 |
| `SUBMITTED` | 접수 확인 수신 | `ACKNOWLEDGED` | 브로커 주문번호 확보 |
| `SUBMITTED` | 응답 불명확 | `UNKNOWN` | 타임아웃, 네트워크 단절 |
| `SUBMITTED` | 즉시 거부 응답 | `REJECTED` | 주문 가능 수량 부족 등 |
| `ACKNOWLEDGED` | 일부 체결 | `PARTIALLY_FILLED` | 누적 체결 수량 갱신 |
| `ACKNOWLEDGED` | 전량 체결 | `FILLED` | 주문 종료 |
| `ACKNOWLEDGED` | 정정 요청 | `PENDING_REPLACE` | 5초 미체결 대응 |
| `ACKNOWLEDGED` | 취소 요청 | `PENDING_CANCEL` | 재주문 전 선행 취소 |
| `ACKNOWLEDGED` | 브로커 거부 통지 | `REJECTED` | 비정상 상태 포함 |
| `ACKNOWLEDGED` | 응답 불명확 | `UNKNOWN` | 조회 필요 |
| `PARTIALLY_FILLED` | 추가 체결 | `PARTIALLY_FILLED` | remaining 수량 유지 |
| `PARTIALLY_FILLED` | 잔여 전량 체결 | `FILLED` | 주문 종료 |
| `PARTIALLY_FILLED` | 잔여 수량 정정 요청 | `PENDING_REPLACE` | 잔여 수량 기준 |
| `PARTIALLY_FILLED` | 잔여 수량 취소 요청 | `PENDING_CANCEL` | 잔여 수량 기준 |
| `PARTIALLY_FILLED` | 응답 불명확 | `UNKNOWN` | 조회 필요 |
| `PENDING_REPLACE` | 정정 완료 | `REPLACED` | 정정 주문 반영 |
| `PENDING_REPLACE` | 정정 실패 | `UNKNOWN` | 이중 주문 방지 위해 조회 우선 |
| `REPLACED` | 접수 확인 | `ACKNOWLEDGED` | 정정 결과 기반 재진입 |
| `PENDING_CANCEL` | 취소 완료 | `CANCELED` | 주문 종료 |
| `PENDING_CANCEL` | 취소 실패 | `UNKNOWN` | 원주문 상태 조회 필요 |
| `UNKNOWN` | 주문 조회 후 접수 확인 | `ACKNOWLEDGED` | 조회 기준 보정 |
| `UNKNOWN` | 주문 조회 후 일부 체결 확인 | `PARTIALLY_FILLED` | 조회 기준 보정 |
| `UNKNOWN` | 주문 조회 후 전량 체결 확인 | `FILLED` | 조회 기준 보정 |
| `UNKNOWN` | 주문 조회 후 취소 확인 | `CANCELED` | 조회 기준 보정 |
| `UNKNOWN` | 주문 조회 후 거부 확인 | `REJECTED` | 조회 기준 보정 |

## 4.3 금지 규칙

- `UNKNOWN` 상태 주문이 존재하면 동일 종목 신규 주문을 생성하지 않는다.
- `FILLED`, `CANCELED`, `REJECTED`, `FAILED`는 종료 상태로 간주한다.
- 종료 상태의 주문은 상태를 되돌리지 않는다.
- 정정 또는 취소 요청 중에는 동일 주문에 대해 추가 정정/취소를 보내지 않는다.

## 4.4 한국투자증권 주문 API 반영 규칙

### 4.4.1 현금 주문 API 매핑

| 용도 | API 경로 | 실전 TR ID | 모의 TR ID |
|---|---|---|---|
| 현금 매수 | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0012U` | `VTTC0012U` |
| 현금 매도 | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0011U` | `VTTC0011U` |
| 정정취소 | `/uapi/domestic-stock/v1/trading/order-rvsecncl` | `TTTC0013U` | `VTTC0013U` |

브로커 공식 샘플코드 기준 주문 요청 제약은 아래와 같다.

- 주문 수량 `ORD_QTY`와 주문 단가 `ORD_UNPR`는 문자열로 전달한다.
- POST API BODY 키는 대문자로 구성한다.
- 주문 엔진은 `ord_dvsn`, `exchange_id`, `cano`, `acnt_prdt_cd`를 주문 레코드에 반드시 보관한다.
- 정정/취소 주문은 원주문 식별값 `krx_fwdg_ord_orgno`, `orig_odno` 없이는 전송하지 않는다.

### 4.4.2 정정/취소 선행 조회 규칙

한국투자증권 공식 샘플코드는 주식주문(정정취소) 호출 전에 `주식정정취소가능주문조회`를 통해 정정취소가능수량을 먼저 확인하도록 명시한다.

이에 따라 주문 엔진은 아래 순서를 강제한다.

1. 원주문 상태 조회
2. 정정취소 가능 수량 조회
3. 가능 수량이 0보다 큰 경우에만 정정/취소 요청 전송
4. 결과가 불명확하면 `UNKNOWN`으로 전이 후 재조회

즉, PRD의 `5초 미체결 -> 정정`, `10초 미체결 -> 시장가 전환`은 브로커 제약상 아래처럼 구현한다.

1. 원주문 가능 수량 조회
2. 가능 수량이 남아 있으면 정정 또는 취소 요청
3. 취소 확인 후 시장가 신규 주문 생성

### 4.4.3 응답 처리 규칙

- 브로커 응답의 `rt_cd`, `msg_cd`, `msg1`를 저장한다.
- 주문 응답에서 원주문번호와 주문시각을 확보하면 즉시 `orders`에 반영한다.
- 주문 응답 성공만으로 `FILLED` 처리하지 않는다.
- 체결 여부는 실시간 체결통보 또는 주문/체결 조회로 확정한다.

## 4.5 실시간 체결통보 반영 규칙

한국투자증권 공식 WebSocket 샘플 기준 체결통보 TR ID는 아래와 같다.

| 환경 | TR ID | 비고 |
|---|---|---|
| 실전 | `H0STCNI0` | 주문/정정/취소/거부 접수 통보 + 체결 통보 |
| 모의 | `H0STCNI9` | 주문/정정/취소/거부 접수 통보 + 체결 통보 |

샘플코드 설명 기준으로 `CNTG_YN` 값은 아래 의미를 가진다.

- `1`: 주문/정정/취소/거부 접수 통보
- `2`: 체결 통보

주문 엔진은 체결 상태 업데이트 우선순위를 아래와 같이 둔다.

1. WebSocket 체결통보
2. REST 주문/체결 조회
3. 로컬 타이머 기반 추정

로컬 타이머는 정정/취소 시점을 결정하는 데만 사용하고, 체결 확정 근거로 사용하지 않는다.

---

# 5. 포지션 상태 전이표

## 5.1 포지션 상태 정의

| 상태 | 설명 |
|---|---|
| `READY` | 진입 전 감시 상태 |
| `OPENING` | 진입 주문이 제출되어 체결 대기 중 |
| `OPEN` | 매수 체결 완료, 보유 중 |
| `CLOSING` | 청산 주문이 제출되어 체결 대기 중 |
| `CLOSED` | 청산 완료 |
| `ERROR` | 상태 불일치 또는 수동 점검 필요 |

## 5.2 허용 전이

| 현재 상태 | 이벤트 | 다음 상태 | 비고 |
|---|---|---|---|
| `READY` | 진입 신호 승인 및 주문 생성 | `OPENING` | 리스크 검증 통과 후 |
| `OPENING` | 진입 주문 전량 체결 | `OPEN` | 평균 단가 확정 |
| `OPENING` | 진입 주문 취소 완료 | `READY` | 포지션 미진입 |
| `OPENING` | 주문 상태 불명확 | `ERROR` | 조회 및 보정 필요 |
| `OPEN` | 손절/익절/기간만료 청산 주문 생성 | `CLOSING` | 청산 사유 기록 |
| `OPEN` | 계좌 조회 상 보유 불일치 | `ERROR` | 수동 또는 자동 보정 필요 |
| `CLOSING` | 청산 주문 전량 체결 | `CLOSED` | 손익 확정 |
| `CLOSING` | 청산 주문 취소 후 보유 유지 | `OPEN` | 비정상 종료 후 복귀 가능 |
| `CLOSING` | 주문 상태 불명확 | `ERROR` | 조회 및 보정 필요 |
| `ERROR` | 계좌/주문 조회 후 상태 복구 성공 | `READY` | 미진입 확정 시 |
| `ERROR` | 계좌/주문 조회 후 보유 확인 | `OPEN` | 보유 포지션 복구 |
| `ERROR` | 계좌/주문 조회 후 청산 진행 확인 | `CLOSING` | 청산 주문 진행 중 |
| `ERROR` | 계좌/주문 조회 후 청산 완료 확인 | `CLOSED` | 손익 확정 후 종료 |

## 5.3 포지션 제약 규칙

- 동일 종목에 대해 `OPENING`, `OPEN`, `CLOSING` 상태 포지션은 동시에 1개만 허용한다.
- `OPENING` 상태에서는 추가 진입 주문을 생성하지 않는다.
- `CLOSING` 상태에서는 신규 청산 주문을 중복 생성하지 않는다.
- 포지션이 `ERROR` 상태이면 해당 종목의 전략 진입 판단을 중지한다.

---

# 6. 장애 복구 시 상태 동기화 절차

프로그램 시작 또는 장애 복구 시 아래 순서로 상태를 복구한다.

1. 브로커 인증 및 API 연결 상태 확인
2. 계좌 잔고 조회
3. 보유 종목 조회
4. 미체결 주문 조회
5. 최근 체결 조회
6. 로컬 `orders`, `positions`, `fills` 비교
7. `UNKNOWN`, `ERROR` 상태 보정
8. 사용자 승인 후 전략 재개

복구 원칙은 아래와 같다.

- 브로커 조회 결과를 우선한다.
- 로컬 상태와 불일치 시 `system_events`에 기록한다.
- 보정 과정에서 신규 주문은 금지한다.
- 복구 완료 전까지 전략 스캔은 수행하되 주문 생성은 차단할 수 있다.
- WebSocket 체결통보가 끊긴 경우 즉시 REST 조회 기반 모드로 전환한다.
- WebSocket 재연결은 지수 백오프로 수행한다.
- 한국투자증권 개발자센터 공지 기준, 과도한 무한 재연결은 차단될 수 있으므로 무한 루프 재접속을 금지한다.

---

# 7. 프로젝트 디렉터리 구조

초기 구현은 단일 프로세스 애플리케이션으로 시작하되, 모듈 단위 분리를 강제한다.

권장 디렉터리 구조는 아래와 같다.

```text
auto_trading/
├─ docs/
├─ src/
│  └─ auto_trading/
│     ├─ app/
│     │  ├─ bootstrap.py
│     │  ├─ scheduler.py
│     │  └─ runtime.py
│     ├─ broker/
│     │  ├─ kis_client.py
│     │  ├─ kis_ws_client.py
│     │  ├─ dto.py
│     │  └─ mapper.py
│     ├─ market_data/
│     │  ├─ collector.py
│     │  ├─ cache.py
│     │  └─ indicators.py
│     ├─ universe/
│     │  └─ builder.py
│     ├─ strategy/
│     │  ├─ scorer.py
│     │  ├─ signals.py
│     │  └─ models.py
│     ├─ portfolio/
│     │  ├─ service.py
│     │  └─ models.py
│     ├─ risk/
│     │  └─ engine.py
│     ├─ orders/
│     │  ├─ engine.py
│     │  ├─ state_machine.py
│     │  └─ models.py
│     ├─ failsafe/
│     │  ├─ monitor.py
│     │  └─ recovery.py
│     ├─ notifications/
│     │  └─ telegram.py
│     ├─ storage/
│     │  ├─ db.py
│     │  ├─ repositories/
│     │  └─ migrations/
│     ├─ config/
│     │  ├─ settings.py
│     │  └─ schema.py
│     └─ common/
│        ├─ enums.py
│        ├─ exceptions.py
│        └─ time.py
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  └─ fixtures/
├─ .env
├─ .env.example
└─ pyproject.toml
```

구조 원칙은 아래와 같다.

- 외부 API 의존은 `broker/`, `notifications/`로 한정한다.
- 전략 판단은 `strategy/`, 주문 실행은 `orders/`에서만 수행한다.
- DB 접근은 `storage/repositories/`를 통해서만 수행한다.
- `app/`는 조립과 스케줄링만 담당하고 도메인 로직을 갖지 않는다.

---

# 8. 모듈 인터페이스 명세

## 8.1 의존 방향

의존 방향은 아래 순서만 허용한다.

`app -> strategy/risk/orders/portfolio/failsafe -> broker/storage/notifications/common`

금지 규칙은 아래와 같다.

- `strategy`가 직접 `broker`를 호출하지 않는다.
- `orders`가 `strategy` 내부 계산 로직을 호출하지 않는다.
- `broker`는 `storage`를 직접 호출하지 않는다.
- `notifications` 실패가 주문 흐름을 중단시키지 않는다.

## 8.2 app.bootstrap

역할:

- 설정 로드
- SQLite 연결 초기화
- 브로커 REST/WebSocket 클라이언트 생성
- 서비스 조립
- 시작 시 상태 복구 수행

주요 인터페이스:

```python
def bootstrap() -> "ApplicationContainer": ...
```

## 8.3 app.scheduler

역할:

- 장전 `08:50` 종목 리스트 갱신 작업 실행
- 장중 `30초` 스캔 작업 실행
- 장마감 전후 복구/정리 작업 실행

주요 인터페이스:

```python
class SchedulerService:
    def run_forever(self) -> None: ...
    def run_pre_market(self) -> None: ...
    def run_market_scan(self) -> None: ...
    def run_post_market(self) -> None: ...
```

## 8.4 broker.kis_client

역할:

- 토큰 발급
- 주문/정정/취소 REST 호출
- 잔고/미체결/체결 조회

주요 인터페이스:

```python
class KISClient:
    def place_cash_order(self, request: "BrokerOrderRequest") -> "BrokerOrderResponse": ...
    def revise_or_cancel_order(self, request: "BrokerReviseCancelRequest") -> "BrokerOrderResponse": ...
    def get_balance(self) -> "BrokerBalance": ...
    def get_open_orders(self) -> list["BrokerOrderSnapshot"]: ...
    def get_daily_fills(self) -> list["BrokerFillSnapshot"]: ...
```

제약:

- 모든 POST 요청은 hashkey 적용을 전제로 한다.
- 실전/모의 `tr_id` 분기는 `broker.mapper`에서 결정한다.

## 8.5 broker.kis_ws_client

역할:

- 실시간 시세 수신
- 주문 접수/체결 통보 수신
- 접속 끊김 감지 및 재연결

주요 인터페이스:

```python
class KISWebSocketClient:
    def connect(self) -> None: ...
    def subscribe_quotes(self, symbols: list[str]) -> None: ...
    def subscribe_order_events(self) -> None: ...
    def poll_events(self) -> list["BrokerRealtimeEvent"]: ...
```

제약:

- 체결 통보는 주문 상태 업데이트의 1차 이벤트 소스로 사용한다.
- 연결 끊김 시 `failsafe.monitor`에 즉시 상태를 전달한다.

## 8.6 market_data.collector

역할:

- 실시간 시세 이벤트 수집
- 종목별 최신 가격/거래량 캐시 갱신
- 전략 입력용 시계열 생성

주요 인터페이스:

```python
class MarketDataCollector:
    def update_quote(self, event: "BrokerRealtimeEvent") -> None: ...
    def get_latest_snapshot(self, symbol: str) -> "MarketSnapshot": ...
    def get_recent_bars(self, symbol: str, window: int) -> list["Bar"]: ...
```

## 8.7 universe.builder

역할:

- 장전 종목 리스트 생성
- 코스피/ETF 필터링
- 거래대금 기준 상위 50 종목 선별

주요 인터페이스:

```python
class UniverseBuilder:
    def rebuild(self, as_of: "datetime") -> list["UniverseItem"]: ...
```

## 8.8 strategy.scorer / strategy.signals

역할:

- 점수 계산
- 매수/매도 신호 생성
- 전략 스냅샷 생성

주요 인터페이스:

```python
class StrategyScorer:
    def score(self, snapshot: "MarketSnapshot") -> "StrategyScore": ...

class SignalEngine:
    def evaluate_entry(self, candidates: list["StrategyScore"]) -> list["EntrySignal"]: ...
    def evaluate_exit(self, position: "Position", snapshot: "MarketSnapshot") -> "ExitSignal | None": ...
```

출력 원칙:

- `evaluate_entry`는 주문을 만들지 않고 신호만 반환한다.
- 점수 `70` 이상 종목만 후속 리스크 검증 대상으로 넘긴다.

## 8.9 portfolio.service

역할:

- 보유 포지션 상태 조회
- 잔고/현금/평균단가 반영
- 재시작 시 브로커 상태와 로컬 상태 정합성 검증

주요 인터페이스:

```python
class PortfolioService:
    def sync_from_broker(self) -> None: ...
    def get_open_positions(self) -> list["Position"]: ...
    def get_position(self, symbol: str) -> "Position | None": ...
    def apply_fill(self, fill: "BrokerFillSnapshot") -> None: ...
```

## 8.10 risk.engine

역할:

- 종목 수 제한 검증
- 종목당 투자 비중 검증
- 일손실 한도 검증
- 신규 진입 가능 여부 결정

주요 인터페이스:

```python
class RiskEngine:
    def can_enter(self, signal: "EntrySignal", portfolio: "PortfolioSnapshot") -> "RiskDecision": ...
    def can_exit(self, signal: "ExitSignal", portfolio: "PortfolioSnapshot") -> "RiskDecision": ...
    def target_order_size(self, signal: "EntrySignal", portfolio: "PortfolioSnapshot") -> "OrderSizing": ...
```

판단 원칙:

- 손절/기간만료 청산은 신규 진입보다 우선한다.
- 일손실 한도 초과 시 `ENTRY`만 차단하고 `EXIT`는 허용한다.

## 8.11 orders.engine

역할:

- 주문 생성
- 5초 정정
- 10초 시장가 전환
- 주문 상태 갱신

주요 인터페이스:

```python
class OrderEngine:
    def submit_entry(self, signal: "EntrySignal", sizing: "OrderSizing") -> "Order": ...
    def submit_exit(self, signal: "ExitSignal", position: "Position") -> "Order": ...
    def handle_broker_event(self, event: "BrokerRealtimeEvent") -> None: ...
    def reconcile_unknown_orders(self) -> None: ...
```

실행 원칙:

- 주문 전송 전 반드시 `risk.engine` 승인 결과가 있어야 한다.
- `UNKNOWN` 주문 복구 중에는 동일 종목 신규 주문을 금지한다.
- 정정/취소는 선행 조회 성공 후에만 전송한다.

## 8.12 failsafe.monitor / failsafe.recovery

역할:

- API 응답 이상, 시세 단절, 체결통보 단절 감시
- 신규 주문 차단
- 미체결 주문 조회 및 복구 절차 실행

주요 인터페이스:

```python
class FailSafeMonitor:
    def record_heartbeat(self, component: str) -> None: ...
    def on_api_error(self, error: Exception) -> None: ...
    def on_stream_disconnect(self, stream_name: str) -> None: ...
    def should_block_new_orders(self) -> bool: ...

class RecoveryService:
    def recover(self) -> None: ...
```

## 8.13 notifications.telegram

역할:

- 체결 및 시스템 이벤트 알림 전송
- 중복 알림 방지 키 적용

주요 인터페이스:

```python
class TelegramNotifier:
    def send_trade_fill(self, payload: "TradeFillNotification") -> None: ...
    def send_system_event(self, payload: "SystemNotification") -> None: ...
```

제약:

- 전송 실패는 예외를 삼키고 `system_events`에만 기록한다.

## 8.14 storage.repositories

역할:

- DB 테이블별 CRUD 캡슐화
- 트랜잭션 경계 관리

주요 인터페이스:

```python
class OrdersRepository:
    def create(self, order: "Order") -> int: ...
    def update_status(self, order_id: int, status: str, **fields) -> None: ...
    def find_unknown_orders(self) -> list["Order"]: ...

class PositionsRepository:
    def upsert(self, position: "Position") -> None: ...
    def find_active(self) -> list["Position"]: ...
```

저장 원칙:

- 주문 상태 업데이트와 체결 반영은 하나의 트랜잭션으로 묶는다.
- 복구 루틴은 조회 결과를 단계별 이벤트 로그와 함께 저장한다.

---

# 9. 런타임 시퀀스

## 9.1 장전 시퀀스

1. `bootstrap` 실행
2. 설정 로드
3. DB 연결 및 마이그레이션 확인
4. 브로커 인증
5. 계좌/미체결/보유 상태 복구
6. `universe.builder.rebuild()` 실행
7. 시세 및 체결통보 구독 시작

## 9.2 장중 스캔 시퀀스

1. `market_data.collector`가 시세 캐시 갱신
2. `scheduler`가 30초마다 스캔 트리거
3. `strategy.scorer`가 종목별 점수 계산
4. `strategy.signals`가 진입 후보 생성
5. `risk.engine`이 진입 가능 여부 및 수량 산정
6. `orders.engine.submit_entry()` 호출
7. 주문 응답은 `orders` 테이블에 저장
8. 체결통보 수신 시 `portfolio.service.apply_fill()` 실행
9. 체결 알림 전송

## 9.3 청산 시퀀스

1. 보유 포지션별 손절/익절/기간만료 조건 평가
2. `risk.engine.can_exit()` 확인
3. `orders.engine.submit_exit()` 호출
4. 체결 완료 시 `trade_logs` 기록
5. 포지션 상태를 `CLOSED`로 종료

## 9.4 장애 복구 시퀀스

1. `failsafe.monitor`가 이상 감지
2. 신규 주문 차단
3. 미체결 주문 조회
4. `UNKNOWN` / `ERROR` 상태 보정
5. 사용자 승인 후 스케줄러 재개

---

# 10. 후속 상세화 항목

다음 버전 기술명세서에서 아래를 추가 정의한다.

- 주문 타임아웃 기준값
- 일손실 계산 기준
- 장전/장중/장후 스케줄 명세
- 텔레그램 알림 이벤트와 중복 방지 키 정의
- WebSocket 끊김 감지 임계치와 재연결 백오프 파라미터
