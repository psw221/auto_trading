from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_trading.app.telegram_commands import TelegramCommandService
from auto_trading.config.schema import Settings


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode('utf-8')

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _StubNotifier:
    def __init__(self) -> None:
        self.responses: list[dict[str, object]] = []
        self.ssl_context = None

    def send_command_response(self, payload: dict[str, object]) -> None:
        self.responses.append(payload)


class _StubSystemEventsRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def create(self, event_type: str, severity: str, component: str, message: str, payload: dict[str, object] | None = None) -> int:
        self.events.append(
            {
                'event_type': event_type,
                'severity': severity,
                'component': component,
                'message': message,
                'payload': payload or {},
            }
        )
        return len(self.events)


def _build_settings() -> Settings:
    return Settings(
        env='demo',
        db_path=Path('data/test_telegram_commands.db'),
        kis_base_url='https://example.com',
        kis_ws_url='ws://example.com',
        kis_app_key='key',
        kis_app_secret='secret',
        kis_cano='123',
        kis_acnt_prdt_cd='01',
        kis_access_token='token',
        kis_refresh_token='',
        kis_user_id='user1',
        universe_master_path=Path('data/universe_master.csv'),
        holiday_calendar_path=Path('data/krx_holidays.csv'),
        holiday_api_service_key='',
        telegram_bot_token='bot-token',
        telegram_chat_id='123456',
    )


class TelegramCommandServiceTest(unittest.TestCase):
    def test_poll_once_skips_backlog_on_first_poll(self) -> None:
        notifier = _StubNotifier()
        system_events = _StubSystemEventsRepository()
        service = TelegramCommandService(_build_settings(), notifier, system_events, poll_interval_seconds=0.0)
        payload = {
            'ok': True,
            'result': [
                {
                    'update_id': 100,
                    'message': {'chat': {'id': '123456'}, 'text': '/status'},
                }
            ],
        }
        with patch('auto_trading.app.telegram_commands.request.urlopen', return_value=_FakeResponse(payload)):
            service.poll_once()
        self.assertEqual([], notifier.responses)
        self.assertEqual(101, service._last_update_id)

    def test_poll_once_runs_allowed_command_and_sends_response(self) -> None:
        notifier = _StubNotifier()
        system_events = _StubSystemEventsRepository()
        service = TelegramCommandService(_build_settings(), notifier, system_events, poll_interval_seconds=0.0)
        service._initialized = True
        payload = {
            'ok': True,
            'result': [
                {
                    'update_id': 101,
                    'message': {'chat': {'id': '123456'}, 'text': '/status'},
                }
            ],
        }
        completed = type('CompletedProcess', (), {'stdout': 'status=running\npid=1234\nprocess_name=pwsh\nstarted_at=2026-04-01 11:00:00', 'stderr': '', 'returncode': 0})()
        with patch('auto_trading.app.telegram_commands.request.urlopen', return_value=_FakeResponse(payload)):
            with patch('auto_trading.app.telegram_commands.subprocess.run', return_value=completed) as mocked_run:
                service.poll_once()
        mocked_run.assert_called_once()
        self.assertEqual(1, len(notifier.responses))
        self.assertIn('[AUTO_TRADING] /status', notifier.responses[0]['message'])
        self.assertIn('상태: 실행 중', notifier.responses[0]['message'])
        self.assertIn('PID: 1234', notifier.responses[0]['message'])

    def test_poll_once_ignores_unapproved_chat_id(self) -> None:
        notifier = _StubNotifier()
        system_events = _StubSystemEventsRepository()
        service = TelegramCommandService(_build_settings(), notifier, system_events, poll_interval_seconds=0.0)
        service._initialized = True
        payload = {
            'ok': True,
            'result': [
                {
                    'update_id': 102,
                    'message': {'chat': {'id': '999999'}, 'text': '/status'},
                }
            ],
        }
        with patch('auto_trading.app.telegram_commands.request.urlopen', return_value=_FakeResponse(payload)):
            with patch('auto_trading.app.telegram_commands.subprocess.run') as mocked_run:
                service.poll_once()
        mocked_run.assert_not_called()
        self.assertEqual([], notifier.responses)

    def test_poll_once_replies_with_help_for_unknown_command(self) -> None:
        notifier = _StubNotifier()
        system_events = _StubSystemEventsRepository()
        service = TelegramCommandService(_build_settings(), notifier, system_events, poll_interval_seconds=0.0)
        service._initialized = True
        payload = {
            'ok': True,
            'result': [
                {
                    'update_id': 103,
                    'message': {'chat': {'id': '123456'}, 'text': '/unknown'},
                }
            ],
        }
        with patch('auto_trading.app.telegram_commands.request.urlopen', return_value=_FakeResponse(payload)):
            service.poll_once()
        self.assertEqual(1, len(notifier.responses))
        self.assertIn('/status', notifier.responses[0]['message'])
        self.assertIn('/dashboard', notifier.responses[0]['message'])


if __name__ == '__main__':
    unittest.main()
