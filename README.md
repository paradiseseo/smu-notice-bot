
# SMU Notice Discord Bot (MVP)

상명대학교 통합공지 페이지(https://www.smu.ac.kr/kor/life/notice.do) 의 새 공지를 60분 간격으로 감지해 디스코드 채널에 Webhook으로 전송하는 봇입니다.

## 빠른 시작 (GitHub 웹만 사용)

1. GitHub에 로그인 → 우측 상단 **+** → **New repository** → `smu-notice-bot` 생성
2. 리포지토리에서 **Add file → Upload files** → 이 폴더의 파일들을 모두 업로드 → Commit
3. **Settings → Secrets and variables → Actions → New repository secret**
   - 이름: `DISCORD_WEBHOOK_URL`
   - 값: 디스코드 채널에서 생성한 Webhook URL
4. **Actions** 탭 → 처음이면 **I understand my workflows, enable them** 클릭
5. 좌측 워크플로 `smu-notice-bot` → **Run workflow**(수동 실행)로 테스트
6. 정상 동작하면 60분마다 자동 실행됩니다. (GitHub Actions **UTC 기준**)

## Discord Webhook 만들기
- 디스코드 서버 → 공지 보낼 채널 톱니바퀴 → **Integrations → Webhooks → New Webhook**
- 이름/아이콘 설정 후 **Copy Webhook URL**

## 로컬 테스트
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python main.py
```

## 주의
- GitHub Actions의 `cron`은 **UTC**입니다. 한국시간과 무관하게 60분 간격 실행은 동일하게 동작합니다.
- 게시판 구조가 바뀌면 `main.py`의 `fetch_list_items()` 셀렉터를 수정하세요.
- 과도한 알림 방지: `MAX_SEND_PER_RUN` 및 실행 주기를 조절하세요.
