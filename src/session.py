"""
Claude Code 세션 관리
Claude Code 프로세스와의 양방향 통신을 관리합니다.
"""

import asyncio
import json
import os
import pty
import uuid
import logging
from datetime import datetime
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass, field

import discord

from .ui import PermissionView, AnswerButtonView

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """세션 상태"""
    is_running: bool = False
    is_waiting_input: bool = False
    is_waiting_permission: bool = False  # 권한 대기 상태
    current_content: str = ""
    current_tool: Optional[str] = None
    permission_denied: list = field(default_factory=list)
    needs_permission_restart: bool = False  # 권한 허용 후 재시작 필요


class ClaudeSession:
    """Claude Code 프로세스 세션 (양방향 인터랙티브)"""

    UPDATE_INTERVAL = 1.5  # 상태 업데이트 간격 (rate limit 방지)

    def __init__(
        self,
        directory: str,
        channel: discord.TextChannel,
        status_msg: discord.Message,
        timeout: int = 600,
        claude_session_id: Optional[str] = None,
        skip_permissions: bool = False
    ):
        self.session_id = str(uuid.uuid4())[:8]
        self.directory = directory
        self.channel = channel
        self.status_msg = status_msg
        self.timeout = timeout
        self.skip_permissions = skip_permissions

        self.claude_session_id = claude_session_id
        self._new_claude_session_id: Optional[str] = None

        self._process: Optional[asyncio.subprocess.Process] = None
        self._state = SessionState()
        self._full_output: list[str] = []
        self._start_time = datetime.now()
        self._last_update = datetime.now()

        # PTY 관련
        self._master_fd: Optional[int] = None
        self._master_writer = None

        # 입력 대기용 Future
        self._input_future: Optional[asyncio.Future] = None
        self._input_event = asyncio.Event()

    # === 상태 프로퍼티 ===

    @property
    def is_running(self) -> bool:
        return self._state.is_running

    @property
    def is_waiting_input(self) -> bool:
        return self._state.is_waiting_input

    @property
    def elapsed_seconds(self) -> float:
        return (datetime.now() - self._start_time).total_seconds()

    @property
    def new_claude_session_id(self) -> Optional[str]:
        return self._new_claude_session_id

    # === 세션 실행 ===

    async def start(self, prompt: str) -> tuple[bool, str]:
        """세션 시작 및 프롬프트 실행 (PTY + -p 모드, 권한 재시작 지원)"""
        if not os.path.isdir(self.directory):
            return False, f"❌ 디렉토리가 존재하지 않습니다: {self.directory}"

        max_retries = 2  # 권한 허용 후 1회 재시도
        current_skip_permissions = self.skip_permissions

        for attempt in range(max_retries):
            try:
                # 상태 초기화
                self._state = SessionState()
                self._full_output = []

                # 명령어 구성 (-p 모드로 프롬프트 전달)
                cmd = [
                    "claude", "-p", prompt,
                    "--output-format", "stream-json",
                    "--verbose"
                ]

                # 세션 ID (첫 시도에서 얻은 ID 또는 기존 ID)
                session_id_to_use = self._new_claude_session_id or self.claude_session_id
                if session_id_to_use:
                    cmd.extend(["--resume", session_id_to_use])
                    logger.info(f"[{self.session_id}] 세션 이어가기: {session_id_to_use}")

                if current_skip_permissions:
                    cmd.append("--dangerously-skip-permissions")
                    logger.info(f"[{self.session_id}] 권한 자동 허용 모드")

                # PTY (pseudo-terminal) 생성
                master_fd, slave_fd = pty.openpty()

                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=self.directory,
                    stdin=slave_fd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                os.close(slave_fd)
                self._master_fd = master_fd
                self._master_writer = os.fdopen(master_fd, 'wb', buffering=0)

                logger.info(f"[{self.session_id}] 프로세스 시작 (시도 {attempt + 1}, PID: {self._process.pid})")

                self._state.is_running = True
                self._start_time = datetime.now()

                # 스트림 읽기
                result = await self._read_stream()

                # 권한 허용 후 재시작이 필요한 경우
                if self._state.needs_permission_restart and attempt < max_retries - 1:
                    logger.info(f"[{self.session_id}] 권한 허용됨, 재시작...")
                    current_skip_permissions = True  # 다음 시도는 권한 자동 허용
                    self._cleanup_pty()

                    # 상태 메시지 업데이트
                    try:
                        await self.status_msg.edit(
                            embed=discord.Embed(
                                title="🔄 권한 허용됨, 작업 재시작 중...",
                                color=discord.Color.blue()
                            )
                        )
                    except discord.HTTPException:
                        pass

                    continue  # 재시도

                return True, result

            except FileNotFoundError:
                return False, "❌ Claude Code CLI가 설치되지 않았습니다."
            except Exception as e:
                logger.error(f"세션 오류: {e}", exc_info=True)
                return False, f"❌ 실행 오류: {str(e)}"
            finally:
                self._state.is_running = False
                self._cleanup_pty()

        return False, "❌ 최대 재시도 횟수 초과"

    async def abort(self) -> None:
        """세션 중단"""
        if self._process:
            self._process.kill()
            await self._process.wait()
        self._state.is_running = False
        self._cleanup_pty()
        if self._input_future and not self._input_future.done():
            self._input_future.cancel()

    async def _end_session(self) -> None:
        """세션 정상 종료 (PTY 모드)"""
        try:
            # /exit 명령으로 Claude Code 종료
            await self._send_to_pty("/exit")
            logger.info(f"[{self.session_id}] 세션 종료 명령 전송")
        except Exception as e:
            logger.warning(f"[{self.session_id}] 세션 종료 중 오류: {e}")

    def _cleanup_pty(self) -> None:
        """PTY 리소스 정리"""
        if self._master_writer:
            try:
                self._master_writer.close()
            except Exception:
                pass
            self._master_writer = None
        self._master_fd = None

    # === PTY 전송 ===

    async def _send_to_pty(self, text: str) -> None:
        """PTY로 텍스트 전송"""
        if self._master_writer:
            data = (text + "\n").encode('utf-8')
            # 비동기로 쓰기 (블로킹 방지)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._master_writer.write, data)
            logger.debug(f"[{self.session_id}] PTY 전송: {text[:50]}...")

    async def send_permission_response(self, allowed: bool, allow_all: bool = False) -> None:
        """권한 응답 전송 (텍스트 형식)"""
        # 텍스트 형식으로 응답 (y/n/yes!)
        if allow_all:
            response = "yes!"  # 이 세션의 모든 유사 요청 허용
        elif allowed:
            response = "y"
        else:
            response = "n"

        await self._send_to_pty(response)
        self._state.is_waiting_input = False

        if self._input_future and not self._input_future.done():
            self._input_future.set_result(allowed)

        logger.info(f"[{self.session_id}] 권한 응답: {response}")

    async def send_user_input(self, text: str) -> None:
        """사용자 입력 전송 (텍스트 형식)"""
        await self._send_to_pty(text)
        self._state.is_waiting_input = False

        if self._input_future and not self._input_future.done():
            self._input_future.set_result(text)

        await self._update_status("📝 답변 전송됨")
        logger.info(f"[{self.session_id}] 사용자 입력: {text[:50]}...")

    # === 스트림 처리 ===

    async def _read_stream(self) -> str:
        """stdout 스트림 읽기"""
        try:
            line_count = 0
            while True:
                try:
                    line = await asyncio.wait_for(
                        self._process.stdout.readline(),
                        timeout=self.timeout
                    )
                except asyncio.TimeoutError:
                    # 입력 대기 중일 때는 타임아웃 무시
                    if self._state.is_waiting_input:
                        continue
                    raise

                if not line:
                    logger.info(f"[{self.session_id}] 스트림 종료 (총 {line_count}줄)")
                    break

                line_count += 1
                await self._process_line(line)

            await self._process.wait()
            return_code = self._process.returncode
            logger.info(f"[{self.session_id}] 프로세스 종료 (코드: {return_code})")

            if return_code != 0:
                stderr = await self._process.stderr.read()
                if stderr:
                    error_msg = stderr.decode('utf-8', errors='replace')
                    logger.error(f"[{self.session_id}] stderr: {error_msg}")
                    return f"❌ 오류:\n{error_msg}"

            return self._build_final_output()

        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()
            return "⏰ 작업 시간 초과"

    def _build_final_output(self) -> str:
        """최종 출력 조합"""
        if self._full_output:
            result = "\n".join(self._full_output)
            logger.info(f"[{self.session_id}] 최종 출력: {len(result)}자")
            return result

        if self._state.current_content:
            return self._state.current_content

        return "출력이 없습니다."

    async def _process_line(self, line: bytes) -> None:
        """한 줄 처리"""
        line_text = line.decode('utf-8', errors='replace').strip()
        if not line_text:
            return

        logger.debug(f"[{self.session_id}] 수신: {line_text[:200]}")

        try:
            data = json.loads(line_text)
            await self._handle_message(data)
        except json.JSONDecodeError:
            self._state.current_content += line_text + "\n"
            await self._update_status()

    async def _handle_message(self, data: dict) -> None:
        """메시지 타입별 처리"""
        msg_type = data.get("type", "")
        subtype = data.get("subtype", "")
        logger.info(f"[{self.session_id}] 메시지: type={msg_type}, subtype={subtype}")

        self._extract_session_id(data)

        # subtype 기반 처리 (system 메시지의 다양한 subtype)
        if msg_type == "system":
            await self._handle_system_message(data)
            return

        handlers = {
            "assistant": self._handle_assistant,
            "content_block_delta": self._handle_delta,
            "content_block_start": self._handle_block_start,
            "content_block_stop": self._handle_block_stop,
            "result": self._handle_result,
            "user": self._handle_user_message,
        }

        handler = handlers.get(msg_type)
        if handler:
            await handler(data)
        else:
            logger.debug(f"[{self.session_id}] 미처리 메시지: {data}")

    def _extract_session_id(self, data: dict) -> None:
        """세션 ID 추출"""
        session_id = data.get("session_id") or data.get("sessionId")
        if session_id and not self._new_claude_session_id:
            self._new_claude_session_id = session_id
            logger.info(f"[{self.session_id}] Claude 세션 ID: {session_id}")

    # === 메시지 핸들러 ===

    async def _handle_system_message(self, data: dict) -> None:
        """시스템 메시지 처리"""
        subtype = data.get("subtype", "")

        if subtype == "init":
            logger.info(f"[{self.session_id}] Claude Code 초기화 완료")
        elif subtype == "permission_request":
            await self._show_permission_ui(data)
        elif subtype == "input_request":
            await self._show_input_ui(data)

    async def _show_permission_ui(self, data: dict) -> None:
        """권한 요청 UI 표시"""
        tool_name = data.get("tool", data.get("permission", {}).get("tool", "알 수 없는 도구"))
        description = data.get("description", data.get("permission", {}).get("description", ""))
        path = data.get("path", data.get("permission", {}).get("path", ""))

        self._state.is_waiting_input = True

        embed = discord.Embed(
            title="🔐 권한 요청",
            description=f"**{tool_name}**",
            color=discord.Color.orange()
        )

        if path:
            embed.add_field(name="경로", value=f"`{path}`", inline=False)
        if description:
            embed.add_field(name="설명", value=description, inline=False)

        view = PermissionView(
            tool_name=tool_name,
            description=description,
            on_response=self.send_permission_response
        )

        await self.channel.send(embed=embed, view=view)
        logger.info(f"[{self.session_id}] 권한 UI 표시: {tool_name}")

        # 응답 대기
        self._input_future = asyncio.Future()
        try:
            await asyncio.wait_for(self._input_future, timeout=300)
        except asyncio.TimeoutError:
            await self.send_permission_response(False)
            logger.warning(f"[{self.session_id}] 권한 응답 타임아웃")

    async def _show_input_ui(self, data: dict) -> None:
        """사용자 입력 UI 표시"""
        question = data.get("question", data.get("message", "추가 정보가 필요합니다"))
        self._state.is_waiting_input = True

        embed = discord.Embed(
            title="❓ Claude Code 질문",
            description=question,
            color=discord.Color.blue()
        )

        view = AnswerButtonView(
            question=question,
            on_answer=self.send_user_input
        )

        await self.channel.send(embed=embed, view=view)
        logger.info(f"[{self.session_id}] 입력 UI 표시: {question[:50]}...")

        self._input_future = asyncio.Future()
        try:
            await asyncio.wait_for(self._input_future, timeout=300)
        except asyncio.TimeoutError:
            await self.send_user_input("취소됨")

    async def _handle_assistant(self, data: dict) -> None:
        """어시스턴트 메시지"""
        content = data.get("message", {}).get("content", [])
        for block in content:
            if block.get("type") == "text":
                self._state.current_content = block.get("text", "")
                await self._update_status()

    async def _handle_delta(self, data: dict) -> None:
        """스트리밍 델타"""
        delta = data.get("delta", {})
        if delta.get("type") == "text_delta":
            self._state.current_content += delta.get("text", "")
            await self._update_status()

    async def _handle_block_start(self, data: dict) -> None:
        """컨텐츠 블록 시작"""
        block = data.get("content_block", {})
        if block.get("type") == "tool_use":
            self._state.current_tool = block.get("name", "도구 실행")
            await self._update_status()

    async def _handle_block_stop(self, data: dict) -> None:
        """컨텐츠 블록 종료"""
        self._state.current_tool = None

    async def _handle_result(self, data: dict) -> None:
        """최종 결과"""
        result_text = data.get("result", "") or data.get("text", "")
        logger.info(f"[{self.session_id}] 결과 수신: {len(result_text)}자")
        if result_text:
            self._full_output.append(result_text)
        # -p 모드에서는 결과 후 자동 종료됨

    async def _handle_user_message(self, data: dict) -> None:
        """사용자/시스템 메시지 (권한 오류 포함)"""
        message = data.get("message", {})
        content = message.get("content", [])

        for item in content:
            if item.get("type") == "tool_result" and item.get("is_error"):
                error_content = item.get("content", "")
                if any(keyword in error_content.lower() for keyword in [
                    "permission", "haven't granted", "requires approval",
                    "require approval", "was blocked", "command requires"
                ]):
                    self._state.permission_denied.append(error_content)
                    logger.warning(f"[{self.session_id}] 권한 거부 감지: {error_content[:100]}")

                    # 첫 번째 권한 오류에서만 처리 (중복 방지)
                    if not self._state.is_waiting_permission:
                        await self._request_permission_and_restart(error_content)

    async def _request_permission_and_restart(self, error_content: str) -> None:
        """권한 요청 UI 표시 및 재시작 처리"""
        self._state.is_waiting_permission = True

        # 현재 프로세스 중단
        if self._process:
            self._process.kill()
            logger.info(f"[{self.session_id}] 권한 대기를 위해 프로세스 중단")

        # 권한 요청 UI 표시
        embed = discord.Embed(
            title="🔐 권한 필요",
            description=f"```{error_content[:500]}```",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="선택하세요",
            value="**허용** 시 권한을 부여하고 작업을 이어갑니다.\n**거부** 시 작업을 종료합니다.",
            inline=False
        )

        # 권한 응답용 View
        view = PermissionView(
            tool_name="권한 요청",
            description=error_content,
            on_response=self._on_permission_response
        )

        await self.channel.send(embed=embed, view=view)
        logger.info(f"[{self.session_id}] 권한 UI 표시, 사용자 응답 대기")

        # 응답 대기
        self._input_future = asyncio.Future()
        try:
            result = await asyncio.wait_for(self._input_future, timeout=300)
            if result:
                self._state.needs_permission_restart = True
                logger.info(f"[{self.session_id}] 권한 허용됨, 재시작 플래그 설정")
        except asyncio.TimeoutError:
            logger.warning(f"[{self.session_id}] 권한 응답 타임아웃")

    async def _on_permission_response(self, allowed: bool, allow_all: bool = False) -> None:
        """권한 응답 콜백"""
        if self._input_future and not self._input_future.done():
            self._input_future.set_result(allowed)

        if allowed:
            await self.channel.send("✅ 권한이 허용되었습니다. 작업을 이어갑니다...")
        else:
            await self.channel.send("❌ 권한이 거부되었습니다. 작업을 종료합니다.")

    # === 상태 업데이트 ===

    async def _update_status(self, extra_status: str = None) -> None:
        """디스코드 상태 업데이트"""
        now = datetime.now()
        if (now - self._last_update).total_seconds() < self.UPDATE_INTERVAL:
            return

        self._last_update = now

        try:
            embed = self._build_status_embed(extra_status)
            await self.status_msg.edit(embed=embed)
        except discord.HTTPException as e:
            logger.warning(f"상태 업데이트 실패: {e}")

    def _build_status_embed(self, extra_status: str = None) -> discord.Embed:
        """상태 임베드"""
        status = self._get_status_text(extra_status)
        preview = self._get_content_preview()

        embed = discord.Embed(
            title="🔄 Claude Code 실행 중...",
            color=discord.Color.yellow()
        )
        embed.add_field(name="상태", value=status, inline=True)
        embed.add_field(name="경과", value=f"{self.elapsed_seconds:.1f}초", inline=True)

        if preview.strip():
            embed.add_field(
                name="실시간 출력",
                value=f"```\n{preview[:1000]}\n```",
                inline=False
            )

        return embed

    def _get_status_text(self, extra_status: str = None) -> str:
        if extra_status:
            return extra_status
        if self._state.is_waiting_input:
            return "⏳ 사용자 입력 대기 중..."
        if self._state.current_tool:
            return f"🔧 {self._state.current_tool}"
        return "💭 응답 생성 중..."

    def _get_content_preview(self, max_length: int = 800) -> str:
        content = self._state.current_content
        if len(content) > max_length:
            return "...\n" + content[-max_length:]
        return content
