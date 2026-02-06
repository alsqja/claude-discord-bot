# CLAUDE.md

이 파일은 Claude Code (claude.ai/code)가 이 저장소에서 작업할 때 따라야 할 가이드를 제공합니다.

## 중요: 언어 설정

**이 프로젝트에서 작업할 때는 반드시 한국어로 응답하세요.** 사용자와의 모든 대화, 커밋 메시지, 코드 주석은 한국어로 작성해야 합니다.

## 프로젝트 개요

디스코드 채널을 통해 로컬 디렉토리에서 Claude Code CLI를 원격으로 실행하는 봇입니다. 각 디스코드 채널을 로컬 디렉토리에 매핑할 수 있으며, 사용자는 디스코드 메시지를 통해 Claude Code와 대화형으로 상호작용할 수 있습니다. 권한 요청, 추가 질문, 세션 지속성을 완전히 양방향으로 지원합니다.

## 아키텍처

### 핵심 컴포넌트

1. **ClaudeSession** (`src/session.py`)
   - Claude Code CLI 서브프로세스와의 양방향 통신 관리
   - PTY (pseudo-terminal)를 사용하여 `claude -p` 명령과 인터랙티브 I/O 처리
   - stream-json 메시지 파싱 처리 (assistant, content_block_delta, system subtypes)
   - 지속적인 세션 ID를 통한 `--resume` 플래그로 세션 재개 지원
   - 재시도 로직(최대 2회)을 포함한 권한 요청 처리 구현
   - 상태 관리: `is_running`, `is_waiting_input`, `is_waiting_permission`
   - 중요: 텍스트 기반 응답(권한용 y/n/yes!)에 `_send_to_pty()` 사용

2. **ClaudeDiscordBot** (`src/bot.py`)
   - 커스텀 명령어 접두사 `!`를 가진 Discord.py commands.Bot 서브클래스
   - 메시지 라우팅 처리: 명령어(접두사 `!`) vs. Claude 프롬프트
   - async lock을 사용한 채널 범위 세션 생명주기 관리
   - ConfigManager와 ChannelManager에 상태 관리 위임

3. **ConfigManager** (`src/managers/config_manager.py`)
   - `config.json`에 영속화: channel_mappings, channel_sessions, channel_skip_permissions
   - `channel_sessions`: 대화 연속성을 위해 디스코드 채널 ID를 Claude 세션 UUID에 매핑
   - `channel_skip_permissions`: `--dangerously-skip-permissions`를 위한 채널별 플래그

4. **ChannelManager** (`src/managers/channel_manager.py`)
   - 인메모리: 채널 락(동시 실행 방지) 및 활성 ClaudeSession 인스턴스
   - 새 세션 시작 전 채널당 락 획득

### 진입점

- **bot.py** (레거시 모놀리식 버전): 하위 호환성을 위해 유지되는 단일 파일 구현
- **main.py**: `src/bot.py`를 임포트하고 `asyncio.run(run_bot())`로 봇 실행
- **src/__init__.py**: 프로그래밍 방식 사용을 위한 `run_bot()` 노출

### 메시지 흐름

1. 사용자가 매핑된 디스코드 채널에 메시지 전송
2. 봇이 메시지가 `!`로 시작하는지(명령어) 또는 Claude 프롬프트인지 확인
3. Claude 프롬프트의 경우:
   - 세션이 존재하고 입력 대기 중인지 확인 → 메시지를 PTY로 전달
   - 세션이 이미 실행 중인지 확인 → "이미 실행 중" 메시지로 거부
   - 그 외 → 채널 락으로 새 ClaudeSession 생성
4. ClaudeSession이 stream-json 출력으로 `claude -p <prompt>` 서브프로세스 생성
5. 세션이 stdout을 줄 단위로 JSON 메시지로 파싱
6. 권한 요청 (subtype: permission_request) → 디스코드 버튼 UI 표시 → PTY로 y/n/yes! 전송
7. 입력 요청 (subtype: input_request) → 디스코드 모달 표시 → 텍스트를 PTY로 전송
8. 권한 거부 오류 → 프로세스 종료, UI 표시, 허용 시 --dangerously-skip-permissions로 재시작
9. 최종 결과 → 상태 메시지 삭제, 출력이 포함된 embed 전송 (max_output_length 초과 시 분할)

## 개발 명령어

### 설정
```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# .env를 편집하고 DISCORD_BOT_TOKEN 설정
```

### 실행
```bash
python bot.py          # 레거시 모놀리식 버전
# 또는
python main.py         # 모듈식 버전 (src/bot.py 사용)
```

### 환경
- 요구사항: Python 3.10+, Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)
- Discord 봇은 Developer Portal에서 MESSAGE CONTENT INTENT 활성화 필요
- macOS SSL: 인증서 검증을 위해 certifi 사용 (bot.py의 ssl_context 참조)

## 디스코드 명령어

- `!설정 <경로>` - 현재 채널을 로컬 디렉토리에 매핑
- `!해제` - 현재 채널 매핑 해제
- `!중단` - 채널에서 실행 중인 세션 중단
- `!초기화` - 대화 기록 지우기 (새 세션 시작)
- `!권한 [on/off]` - 자동 권한 모드 토글 (--dangerously-skip-permissions)
- `!정보` - 채널 매핑, 세션 ID, 권한 모드 표시
- `!목록` - 모든 채널 매핑 목록
- `!도움` - 도움말 표시

## 주요 구현 세부사항

### 세션 재개
- 세션 ID는 stream-json 메시지에서 추출됨 (`data.get("session_id")`)
- `config.json`의 `channel_sessions[channel_id]`에 저장
- 대화 컨텍스트를 이어가기 위해 `claude --resume <session_id>`로 전달
- 새로 시작하려면 `!초기화` 사용

### 권한 처리
두 가지 모드:
1. **수동 승인** (기본값): 디스코드 버튼 UI 표시, 사용자가 허용/거부/모두 허용 클릭
2. **자동 허용**: `!권한 on`으로 활성화, `--dangerously-skip-permissions` 플래그 추가

엣지 케이스: 실행 중 권한이 거부되면, 세션이 tool_result에서 오류를 감지하고 프로세스를 종료하며 UI를 표시하고, 사용자가 승인하면 해당 실행에 대해 자동 권한이 활성화된 상태로 재시작합니다.

### PTY vs Stream-JSON
- 입력: PTY를 통한 텍스트 (`os.fdopen(master_fd)`) - JSON stdin이 아님
- 출력: stdout의 JSON 라인 (`stream-json` 형식)
- 이것이 `send_permission_response`가 JSON이 아닌 텍스트로 "y"/"n"/"yes!"를 보내는 이유

### 속도 제한
- 상태 메시지 업데이트는 1.5초 간격으로 제한됨 (`UPDATE_INTERVAL`)
- `_update_status()`의 Discord embed 편집은 업데이트 전 경과 시간을 확인

### 동시성
- 채널당 하나의 세션만 `ChannelManager.get_lock(channel_id)`로 강제
- 세션 실행 중 다른 프롬프트 전송 시도 → "⏳ 작업이 실행 중입니다" 응답

## UI 컴포넌트

`src/ui/`에 위치:
- **PermissionView**: ✅ 허용, ❌ 거부, 🔓 모두 허용 버튼이 있는 Discord 버튼 뷰
- **AnswerButtonView**: "📝 답변하기" 버튼 표시 → UserInputModal 트리거
- **UserInputModal**: 단락 스타일의 텍스트 입력을 위한 Discord 모달(팝업)

모든 UI 콜백은 세션 메서드 호출: `send_permission_response()`, `send_user_input()`

## 설정 파일 구조

`config.json`:
```json
{
  "channel_mappings": {
    "1234567890": "/path/to/project"
  },
  "channel_sessions": {
    "1234567890": "9b3fc5d6-ef35-4e79-b919-1c7fb381ffbd"
  },
  "channel_skip_permissions": {
    "1234567890": true
  },
  "settings": {
    "timeout": 600,
    "max_output_length": 4000
  }
}
```

## 디버깅

- bot.py에서 로깅 구성: `logging.basicConfig(level=logging.INFO)`
- 세션 로그는 추적을 위한 `[session_id]` 접두사 포함
- `_read_stream()`에서 returncode != 0이면 stderr 확인
- 스트림 메시지 검사를 위해 `logger.debug()` 사용

## 일반적인 함정

세션 로직 수정 시 기억할 사항:
- `claude -p` 모드는 응답 후 자동 종료됨 (원샷), `claude` 대화형 모드와 다름
- 권한 재시도 로직은 프로세스를 종료하고 새 플래그로 재시작해야 함
- 세션 상태 (`is_waiting_input`, `is_waiting_permission`)는 재시도 간 적절히 재설정되어야 함
