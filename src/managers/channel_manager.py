"""
채널 관리
채널별 락 및 세션 상태를 관리합니다.
"""

import asyncio
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..session import ClaudeSession


class ChannelManager:
    """채널별 락 및 세션 관리자"""

    def __init__(self):
        self._locks: dict[int, asyncio.Lock] = {}
        self._sessions: dict[int, 'ClaudeSession'] = {}

    def get_lock(self, channel_id: int) -> asyncio.Lock:
        """채널별 락 획득 (없으면 생성)"""
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
        return self._locks[channel_id]

    # === 세션 관리 ===

    def get_session(self, channel_id: int) -> Optional['ClaudeSession']:
        """채널의 현재 세션 조회"""
        return self._sessions.get(channel_id)

    def set_session(self, channel_id: int, session: 'ClaudeSession') -> None:
        """채널에 세션 설정"""
        self._sessions[channel_id] = session

    def clear_session(self, channel_id: int) -> None:
        """채널 세션 제거"""
        self._sessions.pop(channel_id, None)

    def is_running(self, channel_id: int) -> bool:
        """채널에서 세션이 실행 중인지 확인"""
        session = self._sessions.get(channel_id)
        return session.is_running if session else False

    def is_waiting_input(self, channel_id: int) -> bool:
        """채널에서 사용자 입력 대기 중인지 확인"""
        session = self._sessions.get(channel_id)
        return session.is_waiting_input if session else False

    # === 상태 조회 ===

    def get_active_channels(self) -> list[int]:
        """실행 중인 채널 목록"""
        return [
            channel_id
            for channel_id, session in self._sessions.items()
            if session.is_running
        ]

    def get_session_count(self) -> int:
        """활성 세션 수"""
        return sum(1 for s in self._sessions.values() if s.is_running)
