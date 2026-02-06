"""
설정 관리 모듈
애플리케이션 설정 및 환경 변수를 관리합니다.
"""

import os
import ssl
import certifi
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


@dataclass
class BotConfig:
    """봇 설정"""
    token: str
    command_prefix: str = "!"
    timeout: int = 600  # 10분


@dataclass
class SSLConfig:
    """SSL 설정"""
    context: ssl.SSLContext


def create_ssl_context() -> ssl.SSLContext:
    """macOS SSL 인증서 문제 해결을 위한 SSL 컨텍스트 생성"""
    return ssl.create_default_context(cafile=certifi.where())


def load_token() -> Optional[str]:
    """
    디스코드 봇 토큰 로드
    환경 변수 또는 .env 파일에서 로드
    """
    token = os.getenv("DISCORD_BOT_TOKEN")

    if not token:
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            with open(env_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DISCORD_BOT_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"\'')
                        break

    return token


def load_bot_config() -> Optional[BotConfig]:
    """봇 설정 로드"""
    token = load_token()
    if not token:
        return None
    return BotConfig(token=token)
