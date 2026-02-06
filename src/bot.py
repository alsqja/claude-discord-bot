"""
디스코드 봇 클래스
명령어 처리 및 메시지 핸들링을 담당합니다.
"""

import os
import logging
from datetime import datetime

import discord
from discord.ext import commands
import aiohttp

from .managers import ConfigManager, ChannelManager
from .session import ClaudeSession

logger = logging.getLogger(__name__)


class ClaudeDiscordBot(commands.Bot):
    """Claude Code 연동 디스코드 봇"""

    def __init__(
        self,
        connector: aiohttp.TCPConnector,
        config_path: str = "config.json"
    ):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            connector=connector
        )

        self.config_manager = ConfigManager(config_path)
        self.channel_manager = ChannelManager()

        self._register_commands()

    # === 이벤트 핸들러 ===

    async def on_ready(self) -> None:
        """봇 준비 완료"""
        logger.info(f"봇 로그인: {self.user}")
        logger.info(f"등록된 매핑: {len(self.config_manager.get_all_mappings())}개")

    async def on_message(self, message: discord.Message) -> None:
        """메시지 수신 처리"""
        if message.author.bot:
            return

        # 명령어 처리
        if message.content.startswith("!"):
            await self.process_commands(message)
            return

        # 매핑된 채널만 처리
        directory = self.config_manager.get_directory(message.channel.id)
        if not directory:
            return

        await self._handle_claude_message(message, directory)

    async def _handle_claude_message(
        self,
        message: discord.Message,
        directory: str
    ) -> None:
        """Claude Code 관련 메시지 처리"""
        channel_id = message.channel.id
        session = self.channel_manager.get_session(channel_id)

        # 입력 대기 중인 세션이 있으면 메시지 전달
        if session and session.is_running and session.is_waiting_input:
            await session.send_user_input(message.content)
            await message.add_reaction("📝")
            return

        # 이미 실행 중이면 대기 메시지
        if session and session.is_running:
            await message.reply("⏳ **작업이 실행 중입니다.** 완료 후 다시 시도하세요.")
            return

        # 새 세션 시작
        await self._start_session(message, directory)

    async def _start_session(
        self,
        message: discord.Message,
        directory: str
    ) -> None:
        """새 Claude 세션 시작"""
        channel_id = message.channel.id
        lock = self.channel_manager.get_lock(channel_id)

        async with lock:
            # 기존 Claude Code 세션 ID 조회 (대화 이어가기)
            claude_session_id = self.config_manager.get_claude_session_id(channel_id)

            status_msg = await self._send_start_message(
                message, directory, is_resume=bool(claude_session_id)
            )

            skip_permissions = self.config_manager.get_skip_permissions(channel_id)

            session = ClaudeSession(
                directory=directory,
                channel=message.channel,
                status_msg=status_msg,
                timeout=self.config_manager.timeout,
                claude_session_id=claude_session_id,
                skip_permissions=skip_permissions
            )
            self.channel_manager.set_session(channel_id, session)

            try:
                logger.info(f"세션 시작: [{directory}] {message.content[:50]}...")
                if claude_session_id:
                    logger.info(f"기존 대화 이어가기: {claude_session_id}")

                success, output = await session.start(message.content)

                # 새로운 세션 ID가 있으면 저장
                if session.new_claude_session_id:
                    self.config_manager.set_claude_session_id(
                        channel_id, session.new_claude_session_id
                    )
                    logger.info(f"세션 ID 저장: {session.new_claude_session_id}")

                await self._send_result(
                    message=message,
                    success=success,
                    output=output,
                    elapsed=session.elapsed_seconds,
                    status_msg=status_msg
                )

            except Exception as e:
                logger.error(f"세션 오류: {e}")
                await message.reply(f"❌ 오류: {str(e)}")

            finally:
                self.channel_manager.clear_session(channel_id)

    async def _send_start_message(
        self,
        message: discord.Message,
        directory: str,
        is_resume: bool = False
    ) -> discord.Message:
        """시작 메시지 전송"""
        content_preview = message.content[:200]
        if len(message.content) > 200:
            content_preview += "..."

        title = "🔄 Claude Code 실행 중..." if not is_resume else "🔄 대화 이어가기..."

        embed = discord.Embed(
            title=title,
            description=f"```{content_preview}```",
            color=discord.Color.yellow()
        )
        embed.add_field(name="디렉토리", value=f"`{directory}`", inline=False)
        status_text = "⏳ 시작 중..." if not is_resume else "⏳ 이전 대화에서 이어가는 중..."
        embed.add_field(name="상태", value=status_text, inline=False)

        return await message.reply(embed=embed)

    async def _send_result(
        self,
        message: discord.Message,
        success: bool,
        output: str,
        elapsed: float,
        status_msg: discord.Message
    ) -> None:
        """결과 메시지 전송"""
        try:
            await status_msg.delete()
        except discord.HTTPException:
            pass

        color = discord.Color.green() if success else discord.Color.red()
        title = "✅ 작업 완료" if success else "❌ 작업 실패"

        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="⏱️ 소요 시간", value=f"{elapsed:.1f}초", inline=True)

        max_length = self.config_manager.max_output_length

        if len(output) <= max_length:
            embed.description = f"```\n{output}\n```"
            await message.reply(embed=embed)
        else:
            # 분할 전송
            embed.description = f"```\n{output[:max_length]}\n```\n*(분할됨)*"
            await message.reply(embed=embed)

            remaining = output[max_length:]
            while remaining:
                chunk = remaining[:max_length]
                remaining = remaining[max_length:]
                await message.channel.send(f"```\n{chunk}\n```")

    # === 명령어 등록 ===

    def _register_commands(self) -> None:
        """봇 명령어 등록"""

        @self.command(name="설정")
        async def cmd_set_directory(ctx: commands.Context, *, directory: str):
            """채널-디렉토리 연결"""
            directory = os.path.expanduser(directory.strip())

            if not os.path.isdir(directory):
                await ctx.send(f"❌ 디렉토리가 존재하지 않습니다: `{directory}`")
                return

            self.config_manager.set_directory(ctx.channel.id, directory)
            await ctx.send(f"✅ 이 채널이 연결되었습니다:\n`{directory}`")
            logger.info(f"채널 {ctx.channel.id} -> {directory} 매핑됨")

        @self.command(name="해제")
        async def cmd_remove_directory(ctx: commands.Context):
            """채널 연결 해제"""
            if self.config_manager.remove_directory(ctx.channel.id):
                await ctx.send("✅ 디렉토리 매핑이 해제되었습니다.")
            else:
                await ctx.send("❌ 이 채널에 연결된 디렉토리가 없습니다.")

        @self.command(name="중단")
        async def cmd_abort_session(ctx: commands.Context):
            """현재 세션 중단"""
            session = self.channel_manager.get_session(ctx.channel.id)

            if session and session.is_running:
                await session.abort()
                self.channel_manager.clear_session(ctx.channel.id)
                await ctx.send("🛑 세션이 중단되었습니다.")
            else:
                await ctx.send("❌ 실행 중인 세션이 없습니다.")

        @self.command(name="초기화")
        async def cmd_reset_session(ctx: commands.Context):
            """대화 기록 초기화 (새 대화 시작)"""
            if self.config_manager.clear_claude_session_id(ctx.channel.id):
                await ctx.send("🔄 대화 기록이 초기화되었습니다. 다음 메시지부터 새 대화로 시작합니다.")
                logger.info(f"채널 {ctx.channel.id} 세션 초기화됨")
            else:
                await ctx.send("ℹ️ 이 채널에 저장된 대화 기록이 없습니다.")

        @self.command(name="권한")
        async def cmd_toggle_permissions(ctx: commands.Context, mode: str = None):
            """권한 자동 허용 설정 (on/off)"""
            current = self.config_manager.get_skip_permissions(ctx.channel.id)

            if mode is None:
                status = "🟢 켜짐 (자동 허용)" if current else "🔴 꺼짐 (수동 승인)"
                await ctx.send(
                    f"**현재 권한 모드:** {status}\n"
                    f"변경: `!권한 on` 또는 `!권한 off`"
                )
                return

            mode = mode.lower()
            if mode in ("on", "켜기", "자동"):
                self.config_manager.set_skip_permissions(ctx.channel.id, True)
                await ctx.send(
                    "⚠️ **권한 자동 허용이 켜졌습니다.**\n"
                    "Claude가 파일 읽기/쓰기, 명령 실행 등을 자동으로 수행합니다.\n"
                    "신뢰할 수 있는 프로젝트에서만 사용하세요!"
                )
                logger.warning(f"채널 {ctx.channel.id} 권한 자동 허용 활성화")
            elif mode in ("off", "끄기", "수동"):
                self.config_manager.set_skip_permissions(ctx.channel.id, False)
                await ctx.send("✅ **권한 수동 승인 모드로 변경되었습니다.**")
                logger.info(f"채널 {ctx.channel.id} 권한 자동 허용 비활성화")
            else:
                await ctx.send("❌ 올바른 모드를 입력하세요: `on` 또는 `off`")

        @self.command(name="정보")
        async def cmd_show_info(ctx: commands.Context):
            """채널 정보 표시"""
            directory = self.config_manager.get_directory(ctx.channel.id)
            session = self.channel_manager.get_session(ctx.channel.id)
            claude_session_id = self.config_manager.get_claude_session_id(ctx.channel.id)
            skip_permissions = self.config_manager.get_skip_permissions(ctx.channel.id)

            embed = discord.Embed(title="📁 채널 정보", color=discord.Color.blue())

            if directory:
                embed.add_field(
                    name="연결된 디렉토리",
                    value=f"`{directory}`",
                    inline=False
                )

                if session and session.is_running:
                    status = "⏳ 입력 대기 중" if session.is_waiting_input else "🔄 실행 중"
                else:
                    status = "✅ 대기 중"

                embed.add_field(name="상태", value=status, inline=True)

                # 권한 모드
                perm_status = "🟢 자동 허용" if skip_permissions else "🔴 수동 승인"
                embed.add_field(name="권한", value=perm_status, inline=True)

                # 대화 세션 정보
                if claude_session_id:
                    embed.add_field(
                        name="💬 대화 세션",
                        value=f"`{claude_session_id[:8]}...` (대화 유지 중)",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="💬 대화 세션",
                        value="없음 (새 대화로 시작)",
                        inline=False
                    )
            else:
                embed.description = "`!설정 /path/to/directory` 로 설정하세요."

            await ctx.send(embed=embed)

        @self.command(name="목록")
        async def cmd_list_mappings(ctx: commands.Context):
            """모든 매핑 표시"""
            mappings = self.config_manager.get_all_mappings()

            if not mappings:
                await ctx.send("📭 등록된 매핑이 없습니다.")
                return

            embed = discord.Embed(
                title="📋 채널-디렉토리 매핑",
                color=discord.Color.green()
            )

            for channel_id, directory in mappings.items():
                channel = self.get_channel(int(channel_id))
                channel_name = channel.name if channel else "Unknown"
                session = self.channel_manager.get_session(int(channel_id))
                status = "🔄" if session and session.is_running else "✅"

                embed.add_field(
                    name=f"{status} #{channel_name}",
                    value=f"`{directory}`",
                    inline=False
                )

            await ctx.send(embed=embed)

        @self.command(name="도움")
        async def cmd_show_help(ctx: commands.Context):
            """도움말 표시"""
            embed = discord.Embed(
                title="🤖 Claude Code 봇 (양방향 인터랙티브)",
                description=(
                    "디스코드에서 Claude Code를 실행합니다.\n"
                    "**권한 요청, 추가 질문에 실시간 응답** 가능!\n"
                    "채널별로 대화가 유지됩니다."
                ),
                color=discord.Color.purple()
            )
            embed.add_field(name="!설정 <경로>", value="채널-디렉토리 연결", inline=False)
            embed.add_field(name="!해제", value="연결 해제", inline=False)
            embed.add_field(name="!중단", value="현재 실행 중단", inline=False)
            embed.add_field(name="!초기화", value="대화 기록 초기화 (새 대화 시작)", inline=False)
            embed.add_field(name="!권한 [on/off]", value="권한 자동 허용 설정", inline=False)
            embed.add_field(name="!정보", value="채널 정보 확인", inline=False)
            embed.add_field(name="!목록", value="모든 매핑 표시", inline=False)

            await ctx.send(embed=embed)
