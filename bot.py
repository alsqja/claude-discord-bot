"""
Discord Bot - Claude Code 연동
각 디스코드 채널을 로컬 디렉토리에 매핑하여 Claude Code 명령을 실행합니다.
"""

import discord
from discord.ext import commands
import subprocess
import asyncio
import json
import os
import ssl
import certifi
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import Optional
import logging

# macOS SSL 인증서 문제 해결
ssl_context = ssl.create_default_context(cafile=certifi.where())

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ChannelLockManager:
    """채널별 락 관리 - 동시 실행 방지"""

    def __init__(self):
        self._locks: dict[int, asyncio.Lock] = {}
        self._running: dict[int, bool] = {}
        self._current_task: dict[int, str] = {}

    def get_lock(self, channel_id: int) -> asyncio.Lock:
        """채널별 락 가져오기 (없으면 생성)"""
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
            self._running[channel_id] = False
        return self._locks[channel_id]

    def is_running(self, channel_id: int) -> bool:
        """해당 채널에서 작업이 실행 중인지 확인"""
        return self._running.get(channel_id, False)

    def set_running(self, channel_id: int, running: bool, task: str = ""):
        """실행 상태 설정"""
        self._running[channel_id] = running
        self._current_task[channel_id] = task if running else ""

    def get_current_task(self, channel_id: int) -> str:
        """현재 실행 중인 작업 가져오기"""
        return self._current_task.get(channel_id, "")


class ConfigManager:
    """설정 파일 관리"""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> dict:
        """설정 파일 로드"""
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"channel_mappings": {}, "settings": {}}

    def save_config(self):
        """설정 파일 저장"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def get_directory(self, channel_id: int) -> Optional[str]:
        """채널 ID에 매핑된 디렉토리 가져오기"""
        return self.config.get("channel_mappings", {}).get(str(channel_id))

    def set_directory(self, channel_id: int, directory: str):
        """채널-디렉토리 매핑 설정"""
        if "channel_mappings" not in self.config:
            self.config["channel_mappings"] = {}
        self.config["channel_mappings"][str(channel_id)] = directory
        self.save_config()

    def remove_directory(self, channel_id: int):
        """채널-디렉토리 매핑 제거"""
        if str(channel_id) in self.config.get("channel_mappings", {}):
            del self.config["channel_mappings"][str(channel_id)]
            self.save_config()

    def get_all_mappings(self) -> dict:
        """모든 매핑 가져오기"""
        return self.config.get("channel_mappings", {})


class ClaudeCodeExecutor:
    """Claude Code CLI 실행기"""

    def __init__(self, timeout: int = 300):
        self.timeout = timeout  # 기본 5분 타임아웃

    async def execute(self, directory: str, prompt: str) -> tuple[bool, str]:
        """
        Claude Code 명령 실행

        Args:
            directory: 작업 디렉토리
            prompt: Claude Code에 보낼 프롬프트

        Returns:
            (성공 여부, 출력 결과)
        """
        if not os.path.isdir(directory):
            return False, f"❌ 디렉토리가 존재하지 않습니다: {directory}"

        try:
            # Claude Code CLI 실행 (--print 옵션으로 비대화형 모드)
            process = await asyncio.create_subprocess_exec(
                "claude",
                "-p", prompt,  # 프롬프트
                "--output-format", "text",  # 텍스트 출력
                cwd=directory,
                stdin=asyncio.subprocess.DEVNULL,  # stdin 닫기 (대화형 입력 방지)
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return False, f"⏰ 작업 시간 초과 ({self.timeout}초)"

            output = stdout.decode('utf-8', errors='replace')
            error = stderr.decode('utf-8', errors='replace')

            if process.returncode == 0:
                return True, output if output else "✅ 작업 완료 (출력 없음)"
            else:
                return False, f"❌ 오류 발생:\n{error or output}"

        except FileNotFoundError:
            return False, "❌ Claude Code CLI가 설치되지 않았습니다. `npm install -g @anthropic-ai/claude-code` 로 설치하세요."
        except Exception as e:
            return False, f"❌ 실행 오류: {str(e)}"


class ClaudeDiscordBot(commands.Bot):
    """Claude Code 연동 디스코드 봇"""

    def __init__(self, connector: aiohttp.TCPConnector, config_path: str = "config.json"):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            connector=connector  # SSL 커넥터 전달
        )

        self.config_manager = ConfigManager(config_path)
        self.lock_manager = ChannelLockManager()
        self.executor = ClaudeCodeExecutor()

        # 명령어 등록
        self._register_commands()

    def _register_commands(self):
        """명령어 등록"""

        @self.command(name="설정")
        async def set_directory(ctx, *, directory: str):
            """현재 채널을 디렉토리에 매핑"""
            # 경로 정규화
            directory = os.path.expanduser(directory.strip())

            if not os.path.isdir(directory):
                await ctx.send(f"❌ 디렉토리가 존재하지 않습니다: `{directory}`")
                return

            self.config_manager.set_directory(ctx.channel.id, directory)
            await ctx.send(f"✅ 이 채널이 다음 디렉토리에 연결되었습니다:\n`{directory}`")
            logger.info(f"채널 {ctx.channel.id} -> {directory} 매핑됨")

        @self.command(name="해제")
        async def remove_directory(ctx):
            """현재 채널의 디렉토리 매핑 해제"""
            directory = self.config_manager.get_directory(ctx.channel.id)
            if directory:
                self.config_manager.remove_directory(ctx.channel.id)
                await ctx.send(f"✅ 디렉토리 매핑이 해제되었습니다.")
            else:
                await ctx.send("❌ 이 채널에 연결된 디렉토리가 없습니다.")

        @self.command(name="정보")
        async def show_info(ctx):
            """현재 채널의 매핑 정보 표시"""
            directory = self.config_manager.get_directory(ctx.channel.id)
            is_running = self.lock_manager.is_running(ctx.channel.id)

            embed = discord.Embed(
                title="📁 채널 정보",
                color=discord.Color.blue()
            )

            if directory:
                embed.add_field(
                    name="연결된 디렉토리",
                    value=f"`{directory}`",
                    inline=False
                )
                status = "🔄 실행 중" if is_running else "✅ 대기 중"
                embed.add_field(name="상태", value=status, inline=True)

                if is_running:
                    current_task = self.lock_manager.get_current_task(ctx.channel.id)
                    if current_task:
                        embed.add_field(
                            name="현재 작업",
                            value=f"`{current_task[:50]}...`" if len(current_task) > 50 else f"`{current_task}`",
                            inline=False
                        )
            else:
                embed.description = "이 채널에 연결된 디렉토리가 없습니다.\n`!설정 /path/to/directory` 로 설정하세요."

            await ctx.send(embed=embed)

        @self.command(name="목록")
        async def list_mappings(ctx):
            """모든 채널-디렉토리 매핑 목록"""
            mappings = self.config_manager.get_all_mappings()

            if not mappings:
                await ctx.send("📭 등록된 매핑이 없습니다.")
                return

            embed = discord.Embed(
                title="📋 채널-디렉토리 매핑 목록",
                color=discord.Color.green()
            )

            for channel_id, directory in mappings.items():
                channel = self.get_channel(int(channel_id))
                channel_name = channel.name if channel else f"Unknown ({channel_id})"
                is_running = self.lock_manager.is_running(int(channel_id))
                status = "🔄" if is_running else "✅"

                embed.add_field(
                    name=f"{status} #{channel_name}",
                    value=f"`{directory}`",
                    inline=False
                )

            await ctx.send(embed=embed)

        @self.command(name="도움")
        async def show_help(ctx):
            """도움말 표시"""
            embed = discord.Embed(
                title="🤖 Claude Code 봇 도움말",
                description="디스코드 채널에서 Claude Code를 실행합니다.",
                color=discord.Color.purple()
            )

            embed.add_field(
                name="📌 기본 사용법",
                value="채널에 메시지를 보내면 연결된 디렉토리에서 Claude Code가 실행됩니다.",
                inline=False
            )

            embed.add_field(
                name="!설정 <경로>",
                value="현재 채널을 디렉토리에 연결\n예: `!설정 /Users/user/project`",
                inline=False
            )

            embed.add_field(
                name="!해제",
                value="현재 채널의 디렉토리 연결 해제",
                inline=False
            )

            embed.add_field(
                name="!정보",
                value="현재 채널의 연결 정보 및 상태 확인",
                inline=False
            )

            embed.add_field(
                name="!목록",
                value="모든 채널-디렉토리 매핑 목록 표시",
                inline=False
            )

            embed.add_field(
                name="⚠️ 주의사항",
                value="• 같은 채널에서 동시 실행은 불가능합니다\n• 작업 완료 후 다음 명령을 보내세요",
                inline=False
            )

            await ctx.send(embed=embed)

    async def on_ready(self):
        """봇 준비 완료"""
        logger.info(f"봇 로그인: {self.user}")
        logger.info(f"등록된 매핑: {len(self.config_manager.get_all_mappings())}개")

    async def on_message(self, message: discord.Message):
        """메시지 수신 처리"""
        # 봇 자신의 메시지 무시
        if message.author.bot:
            return

        # 명령어 처리
        if message.content.startswith("!"):
            await self.process_commands(message)
            return

        # 디렉토리 매핑 확인
        directory = self.config_manager.get_directory(message.channel.id)
        if not directory:
            return  # 매핑 없으면 무시

        # 동시 실행 체크
        if self.lock_manager.is_running(message.channel.id):
            current_task = self.lock_manager.get_current_task(message.channel.id)
            await message.reply(
                f"⏳ **작업이 이미 실행 중입니다.**\n"
                f"현재 작업이 완료된 후 다시 보내주세요.\n"
                f"현재 작업: `{current_task[:50]}...`" if len(current_task) > 50 else f"⏳ **작업이 이미 실행 중입니다.**\n현재 작업이 완료된 후 다시 보내주세요.\n현재 작업: `{current_task}`"
            )
            return

        # Claude Code 실행
        await self._execute_claude(message, directory)

    async def _execute_claude(self, message: discord.Message, directory: str):
        """Claude Code 실행 및 결과 전송"""
        prompt = message.content
        channel_id = message.channel.id

        # 락 획득 및 상태 설정
        lock = self.lock_manager.get_lock(channel_id)

        async with lock:
            self.lock_manager.set_running(channel_id, True, prompt)

            # 실행 시작 알림
            start_embed = discord.Embed(
                title="🔄 Claude Code 실행 중...",
                description=f"```{prompt[:200]}{'...' if len(prompt) > 200 else ''}```",
                color=discord.Color.yellow()
            )
            start_embed.add_field(name="디렉토리", value=f"`{directory}`", inline=False)
            start_msg = await message.reply(embed=start_embed)

            try:
                # Claude Code 실행
                logger.info(f"실행: [{directory}] {prompt[:50]}...")
                start_time = datetime.now()

                success, output = await self.executor.execute(directory, prompt)

                elapsed = (datetime.now() - start_time).total_seconds()

                # 결과 전송
                await self._send_result(message, success, output, elapsed, start_msg)

            except Exception as e:
                logger.error(f"실행 오류: {e}")
                await message.reply(f"❌ 오류 발생: {str(e)}")

            finally:
                self.lock_manager.set_running(channel_id, False)

    async def _send_result(
        self,
        message: discord.Message,
        success: bool,
        output: str,
        elapsed: float,
        start_msg: discord.Message
    ):
        """결과 메시지 전송"""
        # 시작 메시지 삭제
        try:
            await start_msg.delete()
        except:
            pass

        # 결과 임베드 생성
        color = discord.Color.green() if success else discord.Color.red()
        title = "✅ 작업 완료" if success else "❌ 작업 실패"

        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="⏱️ 소요 시간", value=f"{elapsed:.1f}초", inline=True)

        # 출력이 길면 여러 메시지로 분할
        MAX_LENGTH = 1900

        if len(output) <= MAX_LENGTH:
            embed.description = f"```\n{output}\n```"
            await message.reply(embed=embed)
        else:
            # 첫 번째 메시지
            embed.description = f"```\n{output[:MAX_LENGTH]}\n```\n*(결과가 길어서 분할됩니다)*"
            await message.reply(embed=embed)

            # 나머지 분할 전송
            remaining = output[MAX_LENGTH:]
            while remaining:
                chunk = remaining[:MAX_LENGTH]
                remaining = remaining[MAX_LENGTH:]
                await message.channel.send(f"```\n{chunk}\n```")


async def run_bot():
    """비동기 봇 실행"""
    # 환경 변수에서 토큰 로드
    token = os.getenv("DISCORD_BOT_TOKEN")

    if not token:
        # .env 파일에서 로드 시도
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    if line.startswith("DISCORD_BOT_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"\'')
                        break

    if not token:
        print("❌ DISCORD_BOT_TOKEN 환경 변수를 설정하세요.")
        print("   또는 .env 파일에 DISCORD_BOT_TOKEN=your_token 형식으로 저장하세요.")
        return

    # SSL 컨텍스트가 적용된 커넥터 생성 (이벤트 루프 내에서)
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    # 봇 생성 및 실행
    bot = ClaudeDiscordBot(connector=connector)

    try:
        await bot.start(token)
    except discord.LoginFailure:
        print("❌ 디스코드 로그인 실패. 토큰을 확인하세요.")
    except Exception as e:
        print(f"❌ 오류: {e}")
    finally:
        await bot.close()


def main():
    """메인 함수"""
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
