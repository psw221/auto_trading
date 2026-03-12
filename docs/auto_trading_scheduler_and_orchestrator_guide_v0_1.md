# 국내 증시 자동매매 스케줄러/오케스트레이터 연동 가이드 v0.1

작성일: 2026-03-12  
문서 상태: Draft  
인코딩: UTF-8

---

# 1. 목적

본 문서는 자동매매 시스템을 Windows 작업 스케줄러 또는 외부 오케스트레이터와 연동하는 기준을 정리한다.

대상은 아래 두 가지다.

- Windows 작업 스케줄러
- 오픈클로 같은 외부 스케줄링/오케스트레이션 도구

---

# 2. 현재 프로젝트의 연동 포인트

현재 시스템은 아래 실행 진입점을 제공한다.

- [`python -m auto_trading`](/c:/Dev/Python/auto_trading/src/auto_trading/__main__.py)
- `python -m auto_trading --once --no-startup-recovery`
- [`scripts/start_auto_trading.ps1`](/c:/Dev/Python/auto_trading/scripts/start_auto_trading.ps1)
- [`scripts/stop_auto_trading.ps1`](/c:/Dev/Python/auto_trading/scripts/stop_auto_trading.ps1)
- [`scripts/status_auto_trading.ps1`](/c:/Dev/Python/auto_trading/scripts/status_auto_trading.ps1)
- [`scripts/show_auto_trading_dashboard.ps1`](/c:/Dev/Python/auto_trading/scripts/show_auto_trading_dashboard.ps1)

권장 원칙:

- 거래 로직은 이 프로젝트가 담당
- 기동, 중지, 스케줄, 헬스체크는 외부 도구가 담당
- 주문 복구와 상태 보정 로직은 외부 도구로 옮기지 않는다

---

# 3. Windows 작업 스케줄러 연동

## 3.1 권장 작업 구성

권장 작업은 아래 4개다.

1. 장전 시작
2. 장후 중지
3. 주기 상태 점검
4. 수동 복구 보조 실행

예시 시각:

- 장전 시작: 평일 08:45
- 장후 중지: 평일 15:35
- 상태 점검: 평일 5분 간격
- 수동 복구 보조: 필요 시 수동 실행

## 3.2 장전 시작 작업

프로그램:

```text
powershell.exe
```

인수:

```text
-ExecutionPolicy Bypass -File "C:\Dev\Python\auto_trading\scripts\start_auto_trading.ps1"
```

시작 위치:

```text
C:\Dev\Python\auto_trading
```

주의:

- 사용자 로그온 여부와 무관하게 실행하려면 계정 권한과 실행 정책을 미리 점검
- `.venv`를 쓴다면 스크립트 내부 또는 호출 인수에서 정확한 Python 경로를 사용

## 3.3 장후 중지 작업

프로그램:

```text
powershell.exe
```

인수:

```text
-ExecutionPolicy Bypass -File "C:\Dev\Python\auto_trading\scripts\stop_auto_trading.ps1"
```

## 3.4 주기 상태 점검 작업

프로그램:

```text
powershell.exe
```

인수:

```text
-ExecutionPolicy Bypass -File "C:\Dev\Python\auto_trading\scripts\show_auto_trading_dashboard.ps1"
```

용도:

- 주기적으로 상태를 확인
- 작업 스케줄러 실행 결과 로그로 남김
- 필요 시 후속 알림 작업과 연결

## 3.5 작업 스케줄러 설정 권장값

- `Run whether user is logged on or not`
- `Do not start a new instance` 또는 중복 실행 방지
- 실패 시 재시도는 보수적으로 설정
- 노트북이면 절전 모드 진입 차단 확인

금지:

- 동일 시간대에 `start_auto_trading.ps1`를 중복 등록
- 장애 원인 미확인 상태에서 무한 자동 재기동

---

# 4. 서비스형 운영

현재 프로젝트는 상주형 루프를 지원하므로 서비스형 운영도 가능하다.

권장 방식:

- Windows 서비스 래퍼 사용
- 또는 작업 스케줄러에서 장전 시작 후 장후 중지

실무적으로는 현재 단계에서 작업 스케줄러 방식이 더 단순하다.

이유:

- 장 시작/종료 시각이 명확함
- 운영자가 중지/재시작을 이해하기 쉬움
- 장중 장애 시 수동 개입과 충돌이 적음

---

# 5. 오픈클로 같은 외부 오케스트레이터 연동

## 5.1 가능한 연동 방식

오픈클로가 아래 기능을 제공하면 연동 가능하다.

- 스케줄 기반 작업 실행
- 명령행 프로그램 실행
- 작업 결과 수집
- 재시도 정책
- 알림 또는 상태 조회

연동 구조:

- 오케스트레이터가 `start/stop/status/dashboard` 스크립트를 호출
- 자동매매 본체는 현재 저장소 코드가 수행

즉 역할 분리는 이렇게 본다.

- 오픈클로: 실행 제어, 스케줄, 감시
- auto_trading: 전략, 주문, 복구, 상태 저장

## 5.2 권장 연결 대상

오케스트레이터에서 직접 호출하기 좋은 대상은 아래다.

- [`scripts/start_auto_trading.ps1`](/c:/Dev/Python/auto_trading/scripts/start_auto_trading.ps1)
- [`scripts/stop_auto_trading.ps1`](/c:/Dev/Python/auto_trading/scripts/stop_auto_trading.ps1)
- [`scripts/status_auto_trading.ps1`](/c:/Dev/Python/auto_trading/scripts/status_auto_trading.ps1)
- [`scripts/show_auto_trading_dashboard.ps1`](/c:/Dev/Python/auto_trading/scripts/show_auto_trading_dashboard.ps1)

운영 패턴 예시:

1. 장전 08:45 `start_auto_trading.ps1`
2. 장중 5분마다 `status_auto_trading.ps1`
3. 장중 10분마다 `show_auto_trading_dashboard.ps1`
4. 장후 15:35 `stop_auto_trading.ps1`

## 5.3 오픈클로 연동 시 주의점

- 작업 디렉터리를 저장소 루트로 고정
- `.env` 파일 경로를 동일하게 유지
- 중복 기동 방지 규칙 적용
- 재시작 정책은 `UNKNOWN 주문`과 `ERROR 포지션` 존재 시 중지 쪽으로 둔다
- 외부 도구는 주문 복구를 직접 하지 않고 본체 로직에 맡긴다

## 5.4 권장하지 않는 방식

- 외부 오케스트레이터가 직접 브로커 API 호출
- 외부 오케스트레이터가 DB를 직접 수정
- 장애 발생 시 무조건 재기동
- 주문통보 단절 상태에서 자동 재시작 반복

---

# 6. 장애 상황에서 스케줄러/오케스트레이터 정책

권장 정책:

- quote WS 단절만 1회 발생: 본체 fallback 허용
- 주문통보 단절 발생: 운영자 확인 전 자동 재기동 보류
- `UNKNOWN` 주문 존재: 자동 재기동 금지
- `ERROR` 포지션 존재: 자동 재기동 금지
- Telegram 실패 단독 발생: 자동매매 유지 가능, 수동 감시 강화

즉 재기동 정책은 아래 기준이 안전하다.

- `status=stopped` 이고 중대한 상태 불일치 없음: 재기동 가능
- 상태 불일치 존재: 운영자 승인 후 재기동

---

# 7. 다른 PC에 배치할 때 확인할 항목

- Python 설치 경로 고정
- 가상환경 경로 고정
- `.env` 개별 생성
- `data/universe_master.csv` 준비
- `data/krx_holidays.csv` 준비
- PowerShell 실행 정책 확인
- 로그 저장 경로 쓰기 권한 확인

권장:

- 운영용 PC는 절전 해제
- 자동 업데이트 시간과 장중 시간이 겹치지 않게 설정

---

# 8. 권장 도입 순서

1. 로컬 수동 실행 검증
2. launcher 스크립트 검증
3. Windows 작업 스케줄러 등록
4. 장전/장후 자동화 검증
5. 필요 시 오픈클로 연동

이 순서를 권장하는 이유는 단순하다.

- 문제 원인을 분리하기 쉽다
- 자동매매 본체 문제와 외부 오케스트레이터 문제를 구분할 수 있다

---

# 9. 운영 문서 연결

- [`docs/auto_trading_runbook_v0_1.md`](/c:/Dev/Python/auto_trading/docs/auto_trading_runbook_v0_1.md)
- [`docs/auto_trading_manual_intervention_runbook_v0_1.md`](/c:/Dev/Python/auto_trading/docs/auto_trading_manual_intervention_runbook_v0_1.md)
- [`docs/auto_trading_real_ops_checklist_v0_1.md`](/c:/Dev/Python/auto_trading/docs/auto_trading_real_ops_checklist_v0_1.md)
