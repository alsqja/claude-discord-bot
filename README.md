# Claude Code Discord Bot

디스코드 채널에서 로컬 디렉토리의 Claude Code를 원격으로 실행하는 봇입니다.

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                        Discord Server                           │
├─────────────────────────────────────────────────────────────────┤
│  #project-a  ──────┐                                            │
│  #project-b  ──────┼──> Discord Bot (bot.py)                    │
│  #project-c  ──────┘         │                                  │
└──────────────────────────────┼──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Local Machine                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  config.json (채널-디렉토리 매핑)                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ #project-a  →  /Users/user/projects/project-a           │   │
│  │ #project-b  →  /Users/user/projects/project-b           │   │
│  │ #project-c  →  /Users/user/projects/project-c           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                               │                                 │
│                               ▼                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Claude Code CLI                             │   │
│  │                                                          │   │
│  │   claude --print -p "로그인 UI 만들어"                    │   │
│  │        ↓                                                 │   │
│  │   [코드 생성/수정 실행]                                   │   │
│  │        ↓                                                 │   │
│  │   결과 출력 → Discord로 전송                              │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 주요 기능

- **채널-디렉토리 매핑**: 디스코드 채널별로 로컬 디렉토리 지정
- **원격 Claude Code 실행**: 채널에 메시지를 보내면 해당 디렉토리에서 Claude Code 실행
- **동시 실행 방지**: 같은 채널에서 중복 실행 시 대기 메시지 표시
- **실시간 상태 표시**: 실행 중/완료 상태 확인
- **결과 자동 분할**: 긴 출력은 자동으로 여러 메시지로 분할

## 요구 사항

- Python 3.10+
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)
- Discord Bot Token

## 설치

```bash
# 1. 프로젝트 디렉토리로 이동
cd claude-discord-bot

# 2. 가상환경 생성
python3 -m venv .venv

# 3. 가상환경 활성화
# macOS/Linux:
source /venv/bin/activate
# Windows:
# .venv\Scripts\activate

# 4. 의존성 설치
pip install -r requirements.txt

# 5. 환경 변수 설정
cp .env.example .env
# .env 파일을 열어 DISCORD_BOT_TOKEN 설정

# 6. 봇 실행
python bot.py
```

### 가상환경 비활성화

```bash
deactivate
```

### 재실행 시

```bash
cd claude-discord-bot
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python bot.py
```

## Discord Bot 생성 방법

1. [Discord Developer Portal](https://discord.com/developers/applications) 접속
2. "New Application" 클릭
3. Bot 탭에서 "Add Bot" 클릭
4. "Reset Token"으로 토큰 생성 후 복사
5. **중요**: "MESSAGE CONTENT INTENT" 활성화
6. OAuth2 → URL Generator에서:
   - Scopes: `bot`
   - Permissions: `Send Messages`, `Read Message History`, `Embed Links`
7. 생성된 URL로 서버에 봇 초대

## 사용법

### 명령어

| 명령어         | 설명                         | 예시                        |
| -------------- | ---------------------------- | --------------------------- |
| `!설정 <경로>` | 현재 채널을 디렉토리에 연결  | `!설정 /Users/user/project` |
| `!해제`        | 현재 채널의 연결 해제        | `!해제`                     |
| `!정보`        | 현재 채널의 연결 정보 확인   | `!정보`                     |
| `!목록`        | 모든 채널-디렉토리 매핑 표시 | `!목록`                     |
| `!도움`        | 도움말 표시                  | `!도움`                     |

### 사용 예시

```
# 1. 채널 설정
!설정 /Users/minbeom/Desktop/alsqja

# 2. Claude Code 명령 실행 (그냥 메시지 보내면 됨)
로그인 UI 만들어

# 3. 봇이 Claude Code 실행 후 결과 반환
✅ 작업 완료
[결과 출력...]
```

### 동시 실행 방지

```
사용자1: 로그인 UI 만들어
봇: 🔄 Claude Code 실행 중...

사용자2: 회원가입 페이지도 만들어
봇: ⏳ 작업이 이미 실행 중입니다.
    현재 작업이 완료된 후 다시 보내주세요.
    현재 작업: `로그인 UI 만들어`
```

## 설정 파일

`config.json` 구조:

```json
{
  "channel_mappings": {
    "123456789012345678": "/Users/user/project-a",
    "234567890123456789": "/Users/user/project-b"
  },
  "settings": {
    "timeout": 300,
    "max_output_length": 4000
  }
}
```

## 주의사항

- Claude Code CLI가 설치되어 있어야 합니다
- 봇이 실행되는 컴퓨터에서 해당 디렉토리에 접근 가능해야 합니다
- 장시간 작업은 타임아웃(기본 5분)될 수 있습니다

## 문제 해결

**Q: "Claude Code CLI가 설치되지 않았습니다" 오류**

```bash
npm install -g @anthropic-ai/claude-code
```

**Q: 봇이 메시지에 반응하지 않음**

- Discord Developer Portal에서 "MESSAGE CONTENT INTENT" 활성화 확인
- `!정보` 명령으로 채널 매핑 확인

**Q: 작업이 자주 타임아웃됨**

- `ClaudeCodeExecutor(timeout=600)` 으로 타임아웃 시간 증가
