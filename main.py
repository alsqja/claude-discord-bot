#!/usr/bin/env python3
"""
Claude Code Discord Bot - 엔트리 포인트
디스코드 채널에서 Claude Code를 실행하는 봇입니다.
"""

import asyncio
import logging
import sys

import aiohttp
import discord

from src.config import load_bot_config, create_ssl_context
from src.bot import ClaudeDiscordBot


# 로깅 설정 (DEBUG로 상세 로그 확인)
logging.basicConfig(
    level=logging.DEBUG,  # DEBUG로 변경하여 상세 로그 확인
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
# discord.py 로그는 INFO로 유지
logging.getLogger('discord').setLevel(logging.INFO)
logging.getLogger('aiohttp').setLevel(logging.INFO)

logger = logging.getLogger(__name__)


async def run_bot() -> None:
    """봇 실행"""
    # 설정 로드
    config = load_bot_config()
    if not config:
        print("❌ DISCORD_BOT_TOKEN을 설정하세요.")
        print("   환경 변수 또는 .env 파일에 설정할 수 있습니다.")
        return

    # SSL 컨텍스트 생성 (macOS 호환)
    ssl_context = create_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    # 봇 생성 및 실행
    bot = ClaudeDiscordBot(connector=connector)

    try:
        logger.info("봇을 시작합니다...")
        await bot.start(config.token)

    except discord.LoginFailure:
        print("❌ 로그인 실패. 토큰을 확인하세요.")

    except KeyboardInterrupt:
        logger.info("봇을 종료합니다...")

    except Exception as e:
        print(f"❌ 오류: {e}")
        logger.exception("예기치 않은 오류 발생")

    finally:
        await bot.close()


def main() -> None:
    """메인 함수"""
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
