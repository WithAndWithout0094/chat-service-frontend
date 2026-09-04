# 면접 연습 챗봇 — 화면

직무를 정하고 면접 질문에 답하며 연습하는 챗봇의 Streamlit 화면입니다.

API 서버는 별도 저장소에 있습니다: `chat-service-backend`

## 로컬 실행

백엔드가 먼저 떠 있어야 합니다.

```bash
uv sync
uv run streamlit run streamlit_app.py
```

기본으로 `http://127.0.0.1:8000` 의 백엔드를 봅니다.
다른 주소를 쓰려면 환경변수 `BACKEND_URL` 을 지정합니다.

## 파일

```
streamlit_app.py   화면 전체 — 로그인, 사이드바, 대화 창
common.py          백엔드 호출(api / stream_answer), 오류 정의, 설정
```

## 화면이 지키는 것

사용자의 행동과 시스템의 반응을 1대1로 잇습니다.

| 사용자가 겪는 것 | 화면이 하는 일 |
| --- | --- |
| 답이 늦다 | 글자가 나오는 대로 흘려보낸다 (SSE 스트리밍) |
| 답을 못 받았다 | 같은 질문으로 `다시 시도` — 다시 타이핑하게 하지 않는다 |
| 답이 마음에 안 든다 | 기존 답을 지우고 `다시 생성` |
| 평가하고 싶다 | `도움됨` / `아쉬움` — 눌린 상태가 보이고 다시 누르면 취소된다 |
| 로그인이 풀렸다 | 왜 풀렸는지 알리고, 기록은 남아 있다고 말해 준다 |
| 대화를 지우려 한다 | 되돌릴 수 없으므로 확인 단계를 거친다 |

빈 화면도 그냥 비어 있지 않고 "다음에 무엇을 하면 되는지"를 함께 보여줍니다.

## Streamlit Community Cloud 배포

1. [share.streamlit.io](https://share.streamlit.io) → **Create app** → 이 저장소 선택
2. **Main file path**: `streamlit_app.py`
3. **Advanced settings → Secrets** 에 배포된 백엔드 주소를 넣습니다

```toml
BACKEND_URL = "https://your-backend.onrender.com"
```

`common.py` 는 Secrets → 환경변수 → 로컬 기본값 순서로 읽습니다.
그래서 배포 환경에서는 Secrets 값이 쓰이고, 로컬에서는 코드를 고치지 않아도 그대로 동작합니다.

`.streamlit/secrets.toml` 은 로컬에서 쓰더라도 커밋하지 않습니다(`.gitignore`).

> 저장소 목록에 안 보이면, Streamlit 은 Render 와 별개의 GitHub App 이라 접근을 따로 허용해야 합니다.
> GitHub → Settings → Applications → Installed GitHub Apps → Streamlit → Configure 에서 이 저장소를 추가합니다.
> 비공개 저장소라면 비공개 접근도 함께 허용해야 합니다.
