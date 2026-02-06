"""
설정 파일 관리
채널-디렉토리 매핑 및 설정을 JSON 파일로 관리합니다.
"""

import json
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class ChannelMapping:
    """채널-디렉토리 매핑"""
    channel_id: int
    directory: str


class ConfigManager:
    """JSON 설정 파일 관리자"""

    DEFAULT_CONFIG = {
        "channel_mappings": {},
        "channel_sessions": {},  # 채널별 Claude Code 세션 ID
        "channel_skip_permissions": {},  # 채널별 권한 자동 허용 설정
        "settings": {
            "timeout": 600,
            "max_output_length": 4000
        }
    }

    def __init__(self, config_path: str = "config.json"):
        self._config_path = Path(config_path)
        self._config = self._load()

    def _load(self) -> dict:
        """설정 파일 로드"""
        if self._config_path.exists():
            with open(self._config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return self.DEFAULT_CONFIG.copy()

    def _save(self) -> None:
        """설정 파일 저장"""
        with open(self._config_path, 'w', encoding='utf-8') as f:
            json.dump(self._config, f, indent=2, ensure_ascii=False)

    # === 채널 매핑 관리 ===

    def get_directory(self, channel_id: int) -> Optional[str]:
        """채널 ID에 매핑된 디렉토리 조회"""
        return self._config.get("channel_mappings", {}).get(str(channel_id))

    def set_directory(self, channel_id: int, directory: str) -> None:
        """채널-디렉토리 매핑 설정"""
        if "channel_mappings" not in self._config:
            self._config["channel_mappings"] = {}
        self._config["channel_mappings"][str(channel_id)] = directory
        self._save()

    def remove_directory(self, channel_id: int) -> bool:
        """채널-디렉토리 매핑 제거"""
        key = str(channel_id)
        if key in self._config.get("channel_mappings", {}):
            del self._config["channel_mappings"][key]
            self._save()
            return True
        return False

    def get_all_mappings(self) -> dict[str, str]:
        """모든 채널-디렉토리 매핑 조회"""
        return self._config.get("channel_mappings", {}).copy()

    # === Claude Code 세션 관리 ===

    def get_claude_session_id(self, channel_id: int) -> Optional[str]:
        """채널의 Claude Code 세션 ID 조회"""
        return self._config.get("channel_sessions", {}).get(str(channel_id))

    def set_claude_session_id(self, channel_id: int, session_id: str) -> None:
        """채널의 Claude Code 세션 ID 저장"""
        if "channel_sessions" not in self._config:
            self._config["channel_sessions"] = {}
        self._config["channel_sessions"][str(channel_id)] = session_id
        self._save()

    def clear_claude_session_id(self, channel_id: int) -> bool:
        """채널의 Claude Code 세션 ID 삭제 (새 대화 시작)"""
        key = str(channel_id)
        if key in self._config.get("channel_sessions", {}):
            del self._config["channel_sessions"][key]
            self._save()
            return True
        return False

    def get_all_sessions(self) -> dict[str, str]:
        """모든 채널-세션 ID 매핑 조회"""
        return self._config.get("channel_sessions", {}).copy()

    # === 권한 자동 허용 설정 ===

    def get_skip_permissions(self, channel_id: int) -> bool:
        """채널의 권한 자동 허용 설정 조회"""
        return self._config.get("channel_skip_permissions", {}).get(str(channel_id), False)

    def set_skip_permissions(self, channel_id: int, skip: bool) -> None:
        """채널의 권한 자동 허용 설정"""
        if "channel_skip_permissions" not in self._config:
            self._config["channel_skip_permissions"] = {}
        self._config["channel_skip_permissions"][str(channel_id)] = skip
        self._save()

    # === 설정값 관리 ===

    def get_setting(self, key: str, default=None):
        """설정값 조회"""
        return self._config.get("settings", {}).get(key, default)

    @property
    def timeout(self) -> int:
        """타임아웃 설정"""
        return self.get_setting("timeout", 600)

    @property
    def max_output_length(self) -> int:
        """최대 출력 길이"""
        return self.get_setting("max_output_length", 4000)
