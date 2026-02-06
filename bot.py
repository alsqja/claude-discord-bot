"""
Discord Bot - Claude Code 연동 (양방향 상호작용 지원)
각 디스코드 채널을 로컬 디렉토리에 매핑하여 Claude Code 명령을 실행합니다.
퍼미션 요청, 추가 질문 등 상호작용을 디스코드에서 처리합니다.
"""

import discord
from discord.ext import commands
from discord import ui
import asyncio
import json
import os
import ssl
import certifi
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable
import logging
import uuid

# macOS SSL 인증서 문제 해결
ssl_context = ssl.create_default_context(cafile=certifi.where())

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============== 디스코드 UI 컴포넌트 ==============

class PermissionView(ui.View):
    """퍼미션 요청 버튼 UI"""

    def __init__(self, session: 'ClaudeSession', tool_name: str, description: str):
        super().__init__(timeout=300)  # 5분 타임아웃
        self.session = session
        self.tool_name = tool_name
        self.description = description
        self.response = None

    @ui.button(label="✅ 허용", style=discord.ButtonStyle.success)
    async def allow_button(self, interaction: discord.Interaction, button: ui.Button):
        self.response = "allow"
        await interaction.response.send_message(f"✅ `{self.tool_name}` 허용됨", ephemeral=True)
        await self.session.send_permission_response(True)
        self.stop()

    @ui.button(label="❌ 거부", style=discord.ButtonStyle.danger)
    async def deny_button(self, interaction: discord.Interaction, button: ui.Button):
        self.response = "deny"
        await interaction.response.send_message(f"❌ `{self.tool_name}` 거부됨", ephemeral=True)
        await self.session.send_permission_response(False)
        self.stop()

    @ui.button(label="🔓 모두 허용", style=discord.ButtonStyle.primary)
    async def allow_all_button(self, interaction: discord.Interaction, button: ui.Button):
        self.response = "allow_all"
        await interaction.response.send_message("🔓 이 세션의 모든 권한 허용됨", ephemeral=True)
        await self.session.send_permission_response(True, allow_all=True)
        self.stop()


class UserInputModal(ui.Modal):
    """사용자 입력 모달"""

    def __init__(self, session: 'ClaudeSession', question: str):
        super().__init__(title="Claude Code 질문")
        self.session = session
        self.answer_input = ui.TextInput(
            label=question[:45] if len(question) > 45 else question,
            style=discord.TextStyle.paragraph,
            placeholder="답변을 입력하세요...",
            required=True,
            max_length=2000
        )
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"📝 답변 전송됨: {self.answer_input.value[:100]}...", ephemeral=True)
        await self.session.send_user_message(self.answer_input.value)


class AnswerButton(ui.View):
    """답변 버튼 UI"""

    def __init__(self, session: 'ClaudeSession', question: str):
        super().__init__(timeout=300)
        self.session = session
        self.question = question

    @ui.button(label="📝 답변하기", style=discord.ButtonStyle.primary)
    async def answer_button(self, interaction: discord.Interaction, button: ui.Button):
        modal = UserInputModal(self.session, self.question)
        await interaction.response.send_modal(modal)


# ============== Claude Code 세션 관리 ==============

class ClaudeSession:
    """Claude Code 프로세스 세션 관리"""

    def __init__(self, directory: str, channel: discord.TextChannel, status_msg: discord.Message):
        self.session_id = str(uuid.uuid4())[:8]
        self.directory = directory
        self.channel = channel
        self.status_msg = status_msg
        self.process: Optional[asyncio.subprocess.Process] = None
        self.is_running = False
        self.is_waiting_input = False
        self.current_content = ""
        self.current_tool = None
        self.full_output = []
        self.start_time = datetime.now()
        self.last_update = datetime.now()
        self._permission_future: Optional[asyncio.Future] = None

    async def start(self, prompt: str) -> tuple[bool, str]:
        """세션 시작 및 프롬프트 실행"""
        if not os.path.isdir(self.directory):
            return False, f"❌ 디렉토리가 존재하지 않습니다: {self.directory}"

        try:
            self.process = await asyncio.create_subprocess_exec(
                "claude",
                "-p", prompt,
                "--output-format", "stream-json",
                "--input-format", "stream-json",  # 양방향 스트리밍
                cwd=self.directory,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            self.is_running = True
            self.start_time = datetime.now()

            # 스트림 읽기 시작
            result = await self._read_stream()

            return True, result

        except FileNotFoundError:
            return False, "❌ Claude Code CLI가 설치되지 않았습니다."
        except Exception as e:
            logger.error(f"세션 시작 오류: {e}")
            return False, f"❌ 실행 오류: {str(e)}"
        finally:
            self.is_running = False

    async def _read_stream(self) -> str:
        """스트림 읽기 및 처리"""
        try:
            while True:
                line = await asyncio.wait_for(
                    self.process.stdout.readline(),
                    timeout=600  # 10분 타임아웃
                )

                if not line:
                    break

                await self._process_line(line)

            await self.process.wait()
            return "\n".join(self.full_output) if self.full_output else self.current_content

        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
            return "⏰ 작업 시간 초과"

    async def _process_line(self, line: bytes):
        """한 줄 처리"""
        try:
            data = json.loads(line.decode('utf-8', errors='replace'))
            msg_type = data.get("type", "")

            logger.debug(f"[{self.session_id}] 메시지 타입: {msg_type}")

            if msg_type == "assistant":
                await self._handle_assistant(data)

            elif msg_type == "content_block_delta":
                await self._handle_delta(data)

            elif msg_type == "content_block_start":
                await self._handle_block_start(data)

            elif msg_type == "content_block_stop":
                self.current_tool = None

            elif msg_type == "result":
                await self._handle_result(data)

            # 사용자 입력 요청 감지
            elif msg_type == "user_input_request":
                await self._handle_input_request(data)

            # 퍼미션 요청 감지
            elif msg_type == "permission_request":
                await self._handle_permission_request(data)

        except json.JSONDecodeError:
            text = line.decode('utf-8', errors='replace').strip()
            if text:
                self.current_content += text + "\n"
                await self._update_status()

    async def _handle_assistant(self, data: dict):
        """어시스턴트 메시지 처리"""
        content = data.get("message", {}).get("content", [])
        for block in content:
            if block.get("type") == "text":
                self.current_content = block.get("text", "")
                await self._update_status()

    async def _handle_delta(self, data: dict):
        """스트리밍 델타 처리"""
        delta = data.get("delta", {})
        if delta.get("type") == "text_delta":
            text = delta.get("text", "")
            self.current_content += text
            await self._update_status()

    async def _handle_block_start(self, data: dict):
        """컨텐츠 블록 시작"""
        block = data.get("content_block", {})
        if block.get("type") == "tool_use":
            self.current_tool = block.get("name", "도구 실행")
            await self._update_status()

    async def _handle_result(self, data: dict):
        """최종 결과 처리"""
        result_text = data.get("result", "")
        if result_text:
            self.full_output.append(result_text)

    async def _handle_input_request(self, data: dict):
        """사용자 입력 요청 처리"""
        question = data.get("question", "추가 정보가 필요합니다")
        self.is_waiting_input = True

        embed = discord.Embed(
            title="❓ Claude Code 질문",
            description=question,
            color=discord.Color.blue()
        )
        embed.set_footer(text="아래 버튼을 눌러 답변하세요")

        view = AnswerButton(self, question)
        await self.channel.send(embed=embed, view=view)

    async def _handle_permission_request(self, data: dict):
        """퍼미션 요청 처리"""
        tool_name = data.get("tool", "알 수 없는 도구")
        description = data.get("description", "이 작업을 허용하시겠습니까?")
        self.is_waiting_input = True

        embed = discord.Embed(
            title="🔐 권한 요청",
            description=f"**{tool_name}**\n\n{description}",
            color=discord.Color.orange()
        )

        view = PermissionView(self, tool_name, description)
        await self.channel.send(embed=embed, view=view)

        # 응답 대기
        self._permission_future = asyncio.Future()
        try:
            await asyncio.wait_for(self._permission_future, timeout=300)
        except asyncio.TimeoutError:
            await self.send_permission_response(False)

    async def send_permission_response(self, allowed: bool, allow_all: bool = False):
        """퍼미션 응답 전송"""
        if self.process and self.process.stdin:
            response = {
                "type": "permission_response",
                "allowed": allowed,
                "allow_all": allow_all
            }
            self.process.stdin.write((json.dumps(response) + "\n").encode())
            await self.process.stdin.drain()

        self.is_waiting_input = False
        if self._permission_future and not self._permission_future.done():
            self._permission_future.set_result(allowed)

    async def send_user_message(self, message: str):
        """사용자 메시지 전송"""
        if self.process and self.process.stdin:
            user_msg = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": message
                }
            }
            self.process.stdin.write((json.dumps(user_msg) + "\n").encode())
            await self.process.stdin.drain()

        self.is_waiting_input = False
        await self._update_status(f"📝 답변 전송됨")

    async def _update_status(self, extra_status: str = None):
        """상태 메시지 업데이트"""
        now = datetime.now()
        elapsed = (now - self.last_update).total_seconds()

        # rate limit 방지: 1.5초 간격
        if elapsed < 1.5:
            return

        self.last_update = now

        try:
            # 상태 표시
            if extra_status:
                status = extra_status
            elif self.is_waiting_input:
                status = "⏳ 사용자 입력 대기 중..."
            elif self.current_tool:
                status = f"🔧 {self.current_tool}"
            else:
                status = "💭 응답 생성 중..."

            # 컨텐츠 미리보기 (최대 800자)
            preview = self.current_content[-800:] if len(self.current_content) > 800 else self.current_content
            if len(self.current_content) > 800:
                preview = "...\n" + preview

            embed = discord.Embed(
                title="🔄 Claude Code 실행 중...",
                color=discord.Color.yellow()
            )
            embed.add_field(name="상태", value=status, inline=True)

            elapsed_time = (now - self.start_time).total_seconds()
            embed.add_field(name="경과", value=f"{elapsed_time:.1f}초", inline=True)

            if preview.strip():
                embed.add_field(
                    name="실시간 출력",
                    value=f"```\n{preview[:1000]}\n```",
                    inline=False
                )

            await self.status_msg.edit(embed=embed)

        except discord.HTTPException as e:
            logger.warning(f"상태 업데이트 실패: {e}")

    async def abort(self):
        """세션 중단"""
        if self.process:
            self.process.kill()
            await self.process.wait()
        self.is_running = False


# ============== 기존 매니저 클래스들 ==============

class ChannelLockManager:
    """채널별 락 및 세션 관리"""

    def __init__(self):
        self._locks: dict[int, asyncio.Lock] = {}
        self._sessions: dict[int, ClaudeSession] = {}

    def get_lock(self, channel_id: int) -> asyncio.Lock:
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
        return self._locks[channel_id]

    def get_session(self, channel_id: int) -> Optional[ClaudeSession]:
        return self._sessions.get(channel_id)

    def set_session(self, channel_id: int, session: ClaudeSession):
        self._sessions[channel_id] = session

    def clear_session(self, channel_id: int):
        if channel_id in self._sessions:
            del self._sessions[channel_id]

    def is_running(self, channel_id: int) -> bool:
        session = self._sessions.get(channel_id)
        return session.is_running if session else False


class ConfigManager:
    """설정 파일 관리"""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> dict:
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"channel_mappings": {}, "settings": {}}

    def save_config(self):
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def get_directory(self, channel_id: int) -> Optional[str]:
        return self.config.get("channel_mappings", {}).get(str(channel_id))

    def set_directory(self, channel_id: int, directory: str):
        if "channel_mappings" not in self.config:
            self.config["channel_mappings"] = {}
        self.config["channel_mappings"][str(channel_id)] = directory
        self.save_config()

    def remove_directory(self, channel_id: int):
        if str(channel_id) in self.config.get("channel_mappings", {}):
            del self.config["channel_mappings"][str(channel_id)]
            self.save_config()

    def get_all_mappings(self) -> dict:
        return self.config.get("channel_mappings", {})


# ============== 메인 봇 클래스 ==============

class ClaudeDiscordBot(commands.Bot):
    """Claude Code 연동 디스코드 봇 (양방향 상호작용)"""

    def __init__(self, connector: aiohttp.TCPConnector, config_path: str = "config.json"):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            connector=connector
        )

        self.config_manager = ConfigManager(config_path)
        self.session_manager = ChannelLockManager()
        self._register_commands()

    def _register_commands(self):
        """명령어 등록"""

        @self.command(name="설정")
        async def set_directory(ctx, *, directory: str):
            directory = os.path.expanduser(directory.strip())
            if not os.path.isdir(directory):
                await ctx.send(f"❌ 디렉토리가 존재하지 않습니다: `{directory}`")
                return
            self.config_manager.set_directory(ctx.channel.id, directory)
            await ctx.send(f"✅ 이 채널이 연결되었습니다:\n`{directory}`")
            logger.info(f"채널 {ctx.channel.id} -> {directory} 매핑됨")

        @self.command(name="해제")
        async def remove_directory(ctx):
            if self.config_manager.get_directory(ctx.channel.id):
                self.config_manager.remove_directory(ctx.channel.id)
                await ctx.send("✅ 디렉토리 매핑이 해제되었습니다.")
            else:
                await ctx.send("❌ 이 채널에 연결된 디렉토리가 없습니다.")

        @self.command(name="중단")
        async def abort_session(ctx):
            """현재 세션 중단"""
            session = self.session_manager.get_session(ctx.channel.id)
            if session and session.is_running:
                await session.abort()
                self.session_manager.clear_session(ctx.channel.id)
                await ctx.send("🛑 세션이 중단되었습니다.")
            else:
                await ctx.send("❌ 실행 중인 세션이 없습니다.")

        @self.command(name="정보")
        async def show_info(ctx):
            directory = self.config_manager.get_directory(ctx.channel.id)
            session = self.session_manager.get_session(ctx.channel.id)

            embed = discord.Embed(title="📁 채널 정보", color=discord.Color.blue())

            if directory:
                embed.add_field(name="연결된 디렉토리", value=f"`{directory}`", inline=False)

                if session and session.is_running:
                    status = "⏳ 입력 대기 중" if session.is_waiting_input else "🔄 실행 중"
                else:
                    status = "✅ 대기 중"
                embed.add_field(name="상태", value=status, inline=True)
            else:
                embed.description = "`!설정 /path/to/directory` 로 설정하세요."

            await ctx.send(embed=embed)

        @self.command(name="목록")
        async def list_mappings(ctx):
            mappings = self.config_manager.get_all_mappings()
            if not mappings:
                await ctx.send("📭 등록된 매핑이 없습니다.")
                return

            embed = discord.Embed(title="📋 채널-디렉토리 매핑", color=discord.Color.green())
            for channel_id, directory in mappings.items():
                channel = self.get_channel(int(channel_id))
                channel_name = channel.name if channel else f"Unknown"
                session = self.session_manager.get_session(int(channel_id))
                status = "🔄" if session and session.is_running else "✅"
                embed.add_field(name=f"{status} #{channel_name}", value=f"`{directory}`", inline=False)

            await ctx.send(embed=embed)

        @self.command(name="도움")
        async def show_help(ctx):
            embed = discord.Embed(
                title="🤖 Claude Code 봇",
                description="디스코드에서 Claude Code를 실행합니다.\n퍼미션 요청, 추가 질문에 응답할 수 있습니다.",
                color=discord.Color.purple()
            )
            embed.add_field(name="!설정 <경로>", value="채널-디렉토리 연결", inline=False)
            embed.add_field(name="!해제", value="연결 해제", inline=False)
            embed.add_field(name="!중단", value="현재 실행 중단", inline=False)
            embed.add_field(name="!정보", value="채널 정보 확인", inline=False)
            embed.add_field(name="!목록", value="모든 매핑 표시", inline=False)
            await ctx.send(embed=embed)

    async def on_ready(self):
        logger.info(f"봇 로그인: {self.user}")
        logger.info(f"등록된 매핑: {len(self.config_manager.get_all_mappings())}개")

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if message.content.startswith("!"):
            await self.process_commands(message)
            return

        directory = self.config_manager.get_directory(message.channel.id)
        if not directory:
            return

        # 현재 세션이 입력 대기 중이면 메시지를 세션에 전달
        session = self.session_manager.get_session(message.channel.id)
        if session and session.is_running and session.is_waiting_input:
            await session.send_user_message(message.content)
            await message.add_reaction("📝")
            return

        # 이미 실행 중이면 대기 메시지
        if session and session.is_running:
            await message.reply("⏳ **작업이 실행 중입니다.** 완료 후 다시 시도하세요.")
            return

        # 새 세션 시작
        await self._start_session(message, directory)

    async def _start_session(self, message: discord.Message, directory: str):
        """새 Claude 세션 시작"""
        channel_id = message.channel.id
        lock = self.session_manager.get_lock(channel_id)

        async with lock:
            # 시작 메시지
            start_embed = discord.Embed(
                title="🔄 Claude Code 실행 중...",
                description=f"```{message.content[:200]}```",
                color=discord.Color.yellow()
            )
            start_embed.add_field(name="디렉토리", value=f"`{directory}`", inline=False)
            start_embed.add_field(name="상태", value="⏳ 시작 중...", inline=False)
            status_msg = await message.reply(embed=start_embed)

            # 세션 생성
            session = ClaudeSession(directory, message.channel, status_msg)
            self.session_manager.set_session(channel_id, session)

            try:
                logger.info(f"세션 시작: [{directory}] {message.content[:50]}...")
                success, output = await session.start(message.content)

                elapsed = (datetime.now() - session.start_time).total_seconds()
                await self._send_result(message, success, output, elapsed, status_msg)

            except Exception as e:
                logger.error(f"세션 오류: {e}")
                await message.reply(f"❌ 오류: {str(e)}")

            finally:
                self.session_manager.clear_session(channel_id)

    async def _send_result(self, message, success, output, elapsed, status_msg):
        """결과 전송"""
        try:
            await status_msg.delete()
        except:
            pass

        color = discord.Color.green() if success else discord.Color.red()
        title = "✅ 작업 완료" if success else "❌ 작업 실패"

        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="⏱️ 소요 시간", value=f"{elapsed:.1f}초", inline=True)

        MAX_LENGTH = 1900
        if len(output) <= MAX_LENGTH:
            embed.description = f"```\n{output}\n```"
            await message.reply(embed=embed)
        else:
            embed.description = f"```\n{output[:MAX_LENGTH]}\n```\n*(분할됨)*"
            await message.reply(embed=embed)

            remaining = output[MAX_LENGTH:]
            while remaining:
                chunk = remaining[:MAX_LENGTH]
                remaining = remaining[MAX_LENGTH:]
                await message.channel.send(f"```\n{chunk}\n```")


# ============== 메인 함수 ==============

async def run_bot():
    token = os.getenv("DISCORD_BOT_TOKEN")

    if not token:
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    if line.startswith("DISCORD_BOT_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"\'')
                        break

    if not token:
        print("❌ DISCORD_BOT_TOKEN을 설정하세요.")
        return

    connector = aiohttp.TCPConnector(ssl=ssl_context)
    bot = ClaudeDiscordBot(connector=connector)

    try:
        await bot.start(token)
    except discord.LoginFailure:
        print("❌ 로그인 실패. 토큰을 확인하세요.")
    except Exception as e:
        print(f"❌ 오류: {e}")
    finally:
        await bot.close()


def main():
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
