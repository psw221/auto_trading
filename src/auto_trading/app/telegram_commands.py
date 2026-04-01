from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from urllib import parse, request

from auto_trading.app.dashboard import build_dashboard_summary, build_strategy_targets_summary
from auto_trading.config.schema import Settings
from auto_trading.notifications.telegram import TelegramNotifier


@dataclass(slots=True)
class TelegramCommandService:
    settings: Settings
    notifier: TelegramNotifier
    system_events_repository: object
    poll_interval_seconds: float = 5.0
    command_timeout_seconds: float = 20.0
    max_response_chars: int = 3500
    repo_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[3])
    _next_poll_at: float = 0.0
    _last_update_id: int = 0
    _initialized: bool = False

    def poll_once(self) -> None:
        if not self.settings.telegram_bot_token or not self.settings.telegram_chat_id:
            return
        now = monotonic()
        if now < self._next_poll_at:
            return
        self._next_poll_at = now + self.poll_interval_seconds
        try:
            updates = self._fetch_updates(offset=self._last_update_id or None)
        except Exception as exc:
            self.system_events_repository.create(
                event_type='telegram_command_poll_failed',
                severity='WARN',
                component='telegram_command',
                message=f'Telegram command polling failed: {exc}',
                payload={},
            )
            return
        if not updates:
            self._initialized = True
            return
        next_update_id = max(self._extract_update_id(update) for update in updates) + 1
        if not self._initialized:
            self._last_update_id = next_update_id
            self._initialized = True
            return
        self._initialized = True
        self._last_update_id = next_update_id
        for update in updates:
            self._handle_update(update)

    def _fetch_updates(self, *, offset: int | None) -> list[dict[str, object]]:
        params = {'timeout': '0'}
        if offset is not None and offset > 0:
            params['offset'] = str(offset)
        query = parse.urlencode(params)
        req = request.Request(
            url=f'{self._build_get_updates_url()}?{query}',
            method='GET',
        )
        with request.urlopen(req, timeout=5.0, context=self.notifier.ssl_context) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if not payload.get('ok', False):
            raise ValueError(str(payload.get('description', 'Telegram API returned an error.')))
        updates = payload.get('result', [])
        return [update for update in updates if isinstance(update, dict)]

    @staticmethod
    def _extract_update_id(update: dict[str, object]) -> int:
        try:
            return int(update.get('update_id', 0))
        except (TypeError, ValueError):
            return 0

    def _handle_update(self, update: dict[str, object]) -> None:
        message = update.get('message')
        if not isinstance(message, dict):
            return
        text = str(message.get('text', '') or '').strip()
        if not text:
            return
        chat = message.get('chat')
        if not isinstance(chat, dict):
            return
        chat_id = str(chat.get('id', '') or '').strip()
        if chat_id != str(self.settings.telegram_chat_id):
            return
        command = text.split()[0].strip().lower()
        if command in ('/help', '/start'):
            self.notifier.send_command_response({'command': command, 'message': self._format_help_message()})
            return
        if command not in self._command_specs():
            if command.startswith('/'):
                self.notifier.send_command_response({'command': command, 'message': self._format_help_message()})
            return
        response = self._run_command(command)
        self.notifier.send_command_response({'command': command, 'message': response})

    def _run_command(self, command: str) -> str:
        if command == '/dashboard':
            return self._trim_message(self._build_dashboard_message())
        if command == '/targets':
            return self._trim_message(self._build_targets_message())

        spec = self._command_specs()[command]
        try:
            result = subprocess.run(
                spec['argv'],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=self.command_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f'[AUTO_TRADING] {command} 결과\n응답 시간이 초과되었습니다.'
        except OSError as exc:
            return f'[AUTO_TRADING] {command} 결과\n명령 실행 실패: {exc}'

        output = self._combine_command_output(result.stdout, result.stderr)
        if result.returncode != 0:
            body = f'실행 코드: {result.returncode}\n{output}' if output else f'실행 코드: {result.returncode}'
            return self._trim_message(f"[AUTO_TRADING] {spec['label']}\n{body}")
        if command == '/status':
            return self._trim_message(self._format_status_output(output))
        if command == '/pnl':
            return self._trim_message(self._format_pnl_output(output))
        return self._trim_message(f"[AUTO_TRADING] {spec['label']}\n{output or '출력 없음'}")

    @staticmethod
    def _combine_command_output(stdout: str, stderr: str) -> str:
        stdout_text = stdout.strip()
        stderr_text = stderr.strip()
        if stdout_text and stderr_text:
            return f'{stdout_text}\n\n[stderr]\n{stderr_text}'
        return stdout_text or stderr_text

    def _build_dashboard_message(self) -> str:
        summary = build_dashboard_summary(self.settings.db_path, self.settings.universe_master_path)
        if not summary.db_exists:
            return '[AUTO_TRADING] /dashboard\nDB 파일이 없습니다.'
        lines = [
            '[AUTO_TRADING] /dashboard',
            f'포지션: {summary.active_positions}개 (OPENING {summary.opening_positions} / CLOSING {summary.closing_positions})',
            f'주문: 미체결 {summary.open_orders}건 / 미확인 {summary.unknown_orders}건 / 오류 포지션 {summary.error_positions}건',
        ]
        scan = summary.latest_market_scan or {}
        if scan:
            lines.append(
                '스캔: universe {universe_count} / scored {scored_count} / qualified {qualified_count} / entries {entry_signal_count}'.format(
                    universe_count=int(scan.get('universe_count') or 0),
                    scored_count=int(scan.get('scored_count') or 0),
                    qualified_count=int(scan.get('qualified_count') or 0),
                    entry_signal_count=int(scan.get('entry_signal_count') or 0),
                )
            )
        refresh = summary.latest_market_data_refresh or {}
        if refresh:
            lines.append(
                '시세: refreshed {refreshed_count} / failed {failed_count} / stale {stale_symbol_count}'.format(
                    refreshed_count=int(refresh.get('refreshed_count') or 0),
                    failed_count=int(refresh.get('failed_count') or 0),
                    stale_symbol_count=int(refresh.get('stale_symbol_count') or 0),
                )
            )
        if summary.tracked_positions:
            lines.append('')
            lines.append('[추적 포지션]')
            for item in summary.tracked_positions[:3]:
                lines.append(
                    f"- {self._display_symbol(item)} | {item.get('status', '')} | {self._format_int(item.get('qty'))}주 | 평균 {self._format_number(item.get('avg_entry_price'))} | 현재 {self._format_number(item.get('current_price'))}"
                )
        if summary.today_targets:
            lines.append('')
            lines.append('[상위 타겟]')
            for item in summary.today_targets[:3]:
                lines.append(
                    f"- {self._display_symbol(item)} | 점수 {int(item.get('score_total') or 0)} | 현재 {self._format_number(item.get('price'))} | MA5 {self._format_number(item.get('ma5'))}"
                )
        if summary.recent_errors:
            lines.append('')
            lines.append('[최근 오류]')
            for item in summary.recent_errors[:3]:
                lines.append(f"- {item.get('component', '')} | {item.get('message', '')}")
        return '\n'.join(lines)

    def _build_targets_message(self) -> str:
        summary = build_strategy_targets_summary(self.settings.db_path, self.settings.universe_master_path, limit=5)
        if not summary.db_exists:
            return '[AUTO_TRADING] /targets\nDB 파일이 없습니다.'
        lines = [
            '[AUTO_TRADING] /targets',
            f'기준일: {summary.target_date}',
        ]
        if not summary.today_targets:
            lines.append('오늘 저장된 전략 타겟이 없습니다.')
            return '\n'.join(lines)
        for index, item in enumerate(summary.today_targets[:5], start=1):
            lines.append(
                f"{index}. {self._display_symbol(item)} | 점수 {int(item.get('score_total') or 0)} | 현재 {self._format_number(item.get('price'))} | MA5 {self._format_number(item.get('ma5'))} | MA20 {self._format_number(item.get('ma20'))}"
            )
        latest_snapshot_time = str(summary.today_targets[0].get('snapshot_time') or '').strip()
        if latest_snapshot_time:
            lines.append(f'최신 스냅샷: {latest_snapshot_time}')
        return '\n'.join(lines)

    def _format_status_output(self, output: str) -> str:
        fields = self._parse_key_value_output(output)
        status = fields.get('status', 'unknown')
        lines = ['[AUTO_TRADING] /status']
        if status == 'running':
            lines.append('상태: 실행 중')
            lines.append(f"PID: {fields.get('pid', '-')}")
            lines.append(f"프로세스: {fields.get('process_name', '-')}")
            lines.append(f"시작 시각: {fields.get('started_at', '-')}")
        elif status == 'stopped':
            lines.append('상태: 중지')
            lines.append(f"PID: {fields.get('pid', '<none>')}")
        else:
            lines.append(f'상태: {status}')
        note = fields.get('note', '')
        if note:
            lines.append(f'참고: {note}')
        return '\n'.join(lines)

    def _format_pnl_output(self, output: str) -> str:
        if not output.strip():
            return '[AUTO_TRADING] /pnl\n출력 없음'
        lines = [line.rstrip() for line in output.splitlines()]
        picked: list[str] = ['[AUTO_TRADING] /pnl']
        section = ''
        closed_lines = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('[AUTO_TRADING]'):
                continue
            if stripped.startswith('[실현손익 요약]'):
                section = 'summary'
                picked.append('')
                picked.append('[요약]')
                continue
            if stripped.startswith('[청산 내역]'):
                section = 'trades'
                picked.append('')
                picked.append('[최근 청산]')
                continue
            if stripped.startswith('[최고/최저]'):
                section = 'extremes'
                picked.append('')
                picked.append('[최고/최저]')
                continue
            if stripped.startswith('['):
                section = ''
                continue
            if stripped.startswith('기간:'):
                picked.append(stripped)
                continue
            if section == 'summary':
                picked.append(stripped)
                continue
            if section == 'trades' and stripped.startswith('- '):
                if closed_lines < 5:
                    picked.append(stripped)
                    closed_lines += 1
                continue
            if section == 'extremes':
                picked.append(stripped)
        return '\n'.join(picked)

    @staticmethod
    def _parse_key_value_output(output: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in output.splitlines():
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip()
        return result

    def _trim_message(self, message: str) -> str:
        trimmed = message.strip()
        if len(trimmed) <= self.max_response_chars:
            return trimmed
        suffix = '\n\n... (truncated)'
        return f"{trimmed[: self.max_response_chars - len(suffix)].rstrip()}{suffix}"

    def _build_get_updates_url(self) -> str:
        return f'https://api.telegram.org/bot{self.settings.telegram_bot_token}/getUpdates'

    def _format_help_message(self) -> str:
        lines = [
            '[AUTO_TRADING] 사용 가능한 명령',
            '/status',
            '/dashboard',
            '/targets',
            '/pnl',
        ]
        return '\n'.join(lines)

    def _command_specs(self) -> dict[str, dict[str, object]]:
        script_root = self.repo_root / 'scripts'
        return {
            '/status': {
                'label': '/status 결과',
                'argv': ['pwsh', '-NoProfile', '-File', str(script_root / 'status_auto_trading.ps1')],
            },
            '/dashboard': {
                'label': '/dashboard 결과',
                'argv': ['pwsh', '-NoProfile', '-File', str(script_root / 'show_auto_trading_dashboard.ps1')],
            },
            '/targets': {
                'label': '/targets 결과',
                'argv': ['pwsh', '-NoProfile', '-File', str(script_root / 'show_strategy_targets.ps1')],
            },
            '/pnl': {
                'label': '/pnl 결과',
                'argv': ['pwsh', '-NoProfile', '-File', str(script_root / 'show_realized_pnl.ps1'), '--days', '7'],
            },
        }

    @staticmethod
    def _display_symbol(item: dict[str, object]) -> str:
        symbol = str(item.get('symbol', '') or '').strip()
        name = str(item.get('name', '') or '').strip()
        return f'{name} ({symbol})' if name else symbol

    @staticmethod
    def _format_number(value: object) -> str:
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            text = str(value or '').strip()
            return text or '-'

    @staticmethod
    def _format_int(value: object) -> str:
        try:
            return f"{int(float(str(value))):,}"
        except (TypeError, ValueError):
            text = str(value or '').strip()
            return text or '0'
