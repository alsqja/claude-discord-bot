"""
디스코드 Modal 컴포넌트
사용자 입력을 받는 모달 창을 정의합니다.
"""

import discord
from discord import ui
from typing import TYPE_CHECKING, Callable, Awaitable

if TYPE_CHECKING:
    from ..session import ClaudeSession


class UserInputModal(ui.Modal):
    """사용자 텍스트 입력 모달"""

    def __init__(
        self,
        question: str,
        on_submit_callback: Callable[[str], Awaitable[None]]
    ):
        # 제목은 45자 제한
        title = question[:42] + "..." if len(question) > 45 else question
        super().__init__(title="Claude Code")

        self._callback = on_submit_callback

        self.answer_input = ui.TextInput(
            label=title,
            style=discord.TextStyle.paragraph,
            placeholder="답변을 입력하세요...",
            required=True,
            max_length=2000
        )
        self.add_item(self.answer_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """모달 제출 처리"""
        answer = self.answer_input.value
        preview = answer[:100] + "..." if len(answer) > 100 else answer

        await interaction.response.send_message(
            f"📝 답변 전송됨: {preview}",
            ephemeral=True
        )
        await self._callback(answer)
