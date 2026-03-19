# auto_trading REST 전환 TODO v0.2

작성일: 2026-03-19  
문서 상태: Draft

---

# 1. 문서 목적

본 문서는 REST 시세 수집 전환 1차 완료 이후, 추가 안정화 및 운영성 개선 작업을 추적하기 위한 TODO 초안이다.

---

# 2. 현재 상태

현재까지 완료된 REST 전환 1차 범위는 다음과 같다.

- `get_daily_bars()` 구현
- REST snapshot / bars 반영 메서드 추가
- scheduler market-data refresh를 REST 기반으로 전환
- scoring / exit 판단이 REST cache 기준으로 동작하도록 수정
- scheduler 핵심 경로에서 quote subscription 의존 제거
- 관련 단위 테스트 추가 및 전체 회귀 통과

현재 구조는 다음 원칙을 따른다.

- 주문 접수/체결/주문 상태 통보는 WebSocket 유지
- 스코어 계산과 매수/매도 판단용 시세 수집은 REST 우선

---

# 3. 다음 목표

REST 2차에서는 다음 3가지를 우선 달성한다.

- REST refresh 성공/실패 상태를 운영자가 바로 확인할 수 있도록 가시성 확보
- 불필요한 중복 호출을 줄여 안정성과 효율 개선
- quote WS를 비핵심 경로로 더 명확하게 축소

---

# 4. 우선순위 작업

## 4.1 REST refresh 안정화

목표:

종목별 REST refresh 상태와 데이터 신선도를 추적해, 스캔 실패 원인을 바로 알 수 있게 만든다.

세부 TODO:

- [x] 종목별 `last_market_data_refresh_at` 저장
- [x] 종목별 `market_data_source=REST` 또는 유사 메타데이터 저장
- [x] refresh 실패 횟수 / 마지막 실패 시각 추적
- [x] stale data 판정 기준 정의
- [x] stale 종목 수를 대시보드에 표시
- [x] refresh 성공/실패 집계 테스트 추가

완료 기준:

- 대시보드에서 refresh 성공 여부와 stale 상태를 확인할 수 있어야 함
- REST 실패 시 단순 `scored_count=0`이 아니라 원인이 드러나야 함

## 4.2 REST 호출 최적화

목표:

장중 스캔에서 중복 REST 호출을 줄이고, 보유 종목과 유니버스 종목의 우선순위를 분리한다.

세부 TODO:

- [x] 보유 종목 우선 refresh 정책 추가
- [x] 유니버스 종목 refresh 주기 차등화 기준 정의
- [x] 직전 refresh 결과 재사용 캐시 정책 정의
- [x] 동일 스캔 내 중복 symbol 호출 방지 보강
- [x] REST 호출량 측정 로그 또는 카운터 추가
- [x] 호출 최적화 테스트 추가

완료 기준:

- 동일 스캔 주기 내 중복 호출이 최소화되어야 함
- 보유 종목은 항상 가장 우선적으로 최신 시세를 확보해야 함

## 4.3 exit 판단 정확도 보강

목표:

REST snapshot 기반 exit가 안정적으로 동작하면서도, 지표 기반 exit의 품질을 유지한다.

참조 문서:

- `docs/auto_trading_exit_data_quality_spec_v0_1.md`

세부 TODO:

- [x] `take_profit` / `stop_loss` 판단 시 snapshot staleness 확인
- [x] `ma5_breakdown` 같은 지표형 exit는 필요한 bars 품질 조건 명시
- [x] bars 부족 시 허용되는 exit / 불허되는 exit 규칙 문서화
- [x] 보유 종목 exit 판단 테스트 보강
- [x] stale snapshot 상태에서의 보호 로직 추가

완료 기준:

- 가격 기반 exit는 안정적으로 수행되어야 함
- 지표 기반 exit는 데이터 부족 시 오동작 없이 보수적으로 동작해야 함

## 4.4 quote WS 비핵심화 정리

목표:

quote WebSocket이 더 이상 전략 판단 핵심 경로가 아님을 코드 구조상 명확히 한다.

참조 문서:

- `docs/auto_trading_ws_responsibility_spec_v0_1.md`

세부 TODO:

- [x] quote WS 관련 사용 경로 재점검
- [x] 더 이상 필요 없는 scheduler 연계 코드 제거
- [x] order notice WS 전용 책임을 문서화
- [x] quote WS 보조/호환 용도 범위 정리
- [x] 관련 회귀 테스트 정리

완료 기준:

- 전략 판단 경로에서 quote WS가 빠져도 정상 동작해야 함
- WS는 주문/체결 이벤트 전용이라는 구조가 코드와 문서 모두에서 명확해야 함

## 4.5 운영 보강

목표:

REST 전환 이후 운영자가 상태를 더 쉽게 확인하고 대응할 수 있게 만든다.

세부 TODO:

- [x] 대시보드에 market-data refresh 상태 섹션 추가
- [x] 최근 refresh 실패 이벤트를 요약해서 표시
- [x] 필요 시 Telegram 시스템 알림에 refresh 장애 포함 검토
- [x] REST 전환 이후 운영 점검 체크리스트 문서화

완료 기준:

- 운영 중 REST 장애 여부를 대시보드만으로 빠르게 판단할 수 있어야 함

---

# 5. 권장 구현 순서

1. `REST refresh 안정화`
2. `REST 호출 최적화`
3. `exit 판단 정확도 보강`
4. `quote WS 비핵심화 정리`
5. `운영 보강`

---

# 6. 후속 연계 작업

REST 2차 이후 자연스럽게 이어질 수 있는 작업은 다음과 같다.

- [ ] 데일리 리포트 중복 전송 방지
- [ ] 장 마감 missed catch-up 전송
- [ ] 주간/월간 성과 리포트
- [ ] 미진입 사유 집계 고도화
