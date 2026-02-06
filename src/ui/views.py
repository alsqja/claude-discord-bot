"""
디스코드 View 컴포넌트
버튼, 선택 메뉴 등 인터랙티브 요소를 정의합니다.
"""

import discord
from discord import ui
from typing import TYPE_CHECKING, Callable, Awaitable, Optional

from .modals import UserInputModal

if TYPE_CHECKING:
    from ..session import ClaudeSession


class PermissionView(ui.View):
    """권한 요청 버튼 UI"""

    def __init__(
        self,
        tool_name: str,
        description: str,
        on_response: Callable[[bool, bool], Awaitable[None]]
    ):
        super().__init__(timeout=300)  # 5분 타임아웃
        self.tool_name = tool_name
        self.description = description
        self._on_response = on_response
        self.response: Optional[str] = None

    @ui.button(label="✅ 허용", style=discord.ButtonStyle.success)
    async def allow_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button
    ) -> None:
        """단일 허용"""
        self.response = "allow"
        await interaction.response.send_message(
            f"✅ `{self.tool_name}` 허용됨",
            ephemeral=True
        )
        await self._on_response(True, False)
        self.stop()

    @ui.button(label="❌ 거부", style=discord.ButtonStyle.danger)
    async def deny_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button
    ) -> None:
        """거부"""
        self.response = "deny"
        await interaction.response.send_message(
            f"❌ `{self.tool_name}` 거부됨",
            ephemeral=True
        )
        await self._on_response(False, False)
        self.stop()

    @ui.button(label="🔓 모두 허용", style=discord.ButtonStyle.primary)
    async def allow_all_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button
    ) -> None:
        """세션 내 모든 권한 허용"""
        self.response = "allow_all"
        await interaction.response.send_message(
            "🔓 이 세션의 모든 권한 허용됨",
            ephemeral=True
        )
        await self._on_response(True, True)
        self.stop()


class AnswerButtonView(ui.View):
    """답변 버튼 UI"""

    def __init__(
        self,
        question: str,
        on_answer: Callable[[str], Awaitable[None]]
    ):
        super().__init__(timeout=300)
        self.question = question
        self._on_answer = on_answer

    @ui.button(label="📝 답변하기", style=discord.ButtonStyle.primary)
    async def answer_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button
    ) -> None:
        """답변 모달 열기"""
        modal = UserInputModal(
            question=self.question,
            on_submit_callback=self._on_answer
        )
        await interaction.response.send_modal(modal)
