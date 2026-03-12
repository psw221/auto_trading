# auto_trading

국내 증시 자동매매 프로젝트입니다. 현재 기준으로는 `모의투자 환경`에서 REST/WS/Telegram/주문통보까지 검증된 상태입니다.

## 현재 상태

- KIS 모의투자 REST 인증 및 토큰 자동 발급
- KIS 시세 WebSocket 수신
- KIS 주문통보 WebSocket 수신 및 AES 복호화
- 매수/매도 order/fill 실수신 검증
- SQLite 상태 저장
- Telegram 알림 전송
- PowerShell launcher 지원

## 1. 저장소 준비

```powershell
git clone <repo-url>
cd auto_trading
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 2. 환경 파일 준비

`.env.example`를 복사해서 `.env`를 만듭니다.

```powershell
Copy-Item .env.example .env
```

`.env`에는 아래 값을 채웁니다.

- `AUTO_TRADING_ENV`
- `AUTO_TRADING_KIS_APP_KEY`
- `AUTO_TRADING_KIS_APP_SECRET`
- `AUTO_TRADING_KIS_CANO`
- `AUTO_TRADING_KIS_ACNT_PRDT_CD`
- `AUTO_TRADING_KIS_USER_ID`
- `AUTO_TRADING_TELEGRAM_BOT_TOKEN`
- `AUTO_TRADING_TELEGRAM_CHAT_ID`

주의:

- `.env`는 git에 올리지 않습니다.
- 현재 `.env`에 들어 있는 민감정보는 원격 저장소에 절대 커밋하면 안 됩니다.

## 3. 기본 점검

문법 확인:

```powershell
python -m compileall src tests
```

테스트 실행:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

단일 cycle 실행:

```powershell
python -m auto_trading --once --no-startup-recovery
```

## 4. launcher 사용

상태 확인:

```powershell
pwsh -File scripts/status_auto_trading.ps1
```

대시보드 조회:

```powershell
pwsh -File scripts/show_auto_trading_dashboard.ps1
```

시작:

```powershell
pwsh -File scripts/start_auto_trading.ps1
```

단일 실행:

```powershell
pwsh -File scripts/start_auto_trading.ps1 -Once -NoStartupRecovery
```

중지:

```powershell
pwsh -File scripts/stop_auto_trading.ps1
```

로그 경로:

- `data/runtime/logs/auto_trading.stdout.log`
- `data/runtime/logs/auto_trading.stderr.log`

## 5. 운영 문서

- 기술명세서: `docs/auto_trading_technical_spec_v0_1.md`
- 운영 runbook: `docs/auto_trading_runbook_v0_1.md`
- 실전 전환 checklist: `docs/auto_trading_real_ops_checklist_v0_1.md`
- 수동 개입 runbook: `docs/auto_trading_manual_intervention_runbook_v0_1.md`

## 6. 다른 PC에서 실행할 때 체크할 것

- Python 3.11 이상
- `requirements.txt` 설치
- `.env` 직접 생성
- KIS/Telegram 값 재확인
- `data/universe_master.csv`, `data/krx_holidays.csv` 존재 확인

## 7. git 업로드 전 주의

- `.env` 커밋 금지
- `data/*.db` 커밋 금지
- `data/runtime/` 로그/PID 커밋 금지
- 민감정보가 이미 외부에 노출됐다면 재발급 고려
