"""화면 여러 곳에서 공통으로 쓰는 설정과 백엔드 호출 함수.

백엔드를 부르는 함수가 두 개다.

- ``api()``           완성된 JSON 하나를 받아 온다 (return)
- ``stream_answer()`` 아직 안 끝난 응답을 조각으로 내어준다 (yield). SSE 전용

오류는 두 등급으로 나눈다.

- ``ApiError``       화면 안에서 안내로 보여주면 되는 일반 오류
- ``SessionExpired`` 화면 전체를 로그인으로 되돌려야 하는 오류 (ApiError 의 자식)

오류 문구는 이 파일에서만 만든다. 화면마다 따로 쓰면 같은 상황이 다르게 보이고,
나중에 고칠 때 빠뜨리는 곳이 생긴다.
"""

import os
import json

import httpx
import streamlit as st


# 백엔드 주소를 Secrets → 환경변수 → 로컬 기본값 순으로 읽는다.
# 배포한 백엔드 주소는 코드를 고치지 않고 넣어야 하므로 그 자리를 Secrets 로 비워 둔다.
# try 로 감싸는 이유: 로컬에는 .streamlit/secrets.toml 이 없어서 st.secrets 접근만으로 예외가 난다.
try:
    _backend_url_secret = st.secrets.get("BACKEND_URL")
except Exception:
    _backend_url_secret = None

BACKEND_URL = _backend_url_secret or os.environ.get(
    "BACKEND_URL", "http://127.0.0.1:8000"
)

# httpx 기본 타임아웃은 5초다. 무료 플랜의 백엔드는 잠에서 깨는 데 그보다 오래 걸린다.
HTTP_TIMEOUT = 60

SERVICE_NAME = "면접 연습 챗봇"


class ApiError(Exception):
    """화면에 그대로 보여줄 수 있는 오류.

    별도 클래스로 두면 ``except ApiError`` 로 우리가 던진 것만 골라 잡을 수 있다.
    메시지에는 예외 이름이 아니라 사용자가 무엇을 하면 되는지를 담는다.
    """


class SessionExpired(ApiError):
    """로그인이 풀린 상태.

    ApiError 를 상속하므로 ``except ApiError`` 로도 잡힌다. 세션 만료 처리를 아직
    붙이지 않은 화면이 있어도 최소한 오류 안내로는 표시되어 화면이 하얗게 되지 않는다.

    그러면서 이름을 나눠 두는 이유는 처리 방식이 다르기 때문이다.
    일반 오류는 그 자리에 안내를 띄우지만, 이것은 화면 전체를 로그인으로 되돌려야 한다.
    """


def auth_headers() -> dict:
    """인증 헤더를 만들어 돌려준다.

    상수가 아니라 함수여야 한다. Streamlit 은 다시 그릴 때 스크립트만 재실행하고
    이미 import 한 모듈은 다시 읽지 않는다. 상수로 두면 앱이 처음 뜰 당시의 값
    (로그인 전이라 None)이 끝까지 남아 "Bearer None" 을 계속 보내게 된다.
    """
    return {"Authorization": f"Bearer {st.session_state.access_token}"}


def _error_detail(response: httpx.Response) -> str:
    """오류 응답에서 detail 문장만 꺼낸다. 없거나 문자열이 아니면 빈 문자열.

    FastAPI 는 HTTPException 의 문장을 {"detail": ...} 로 보낸다. 거기에 원인이 들어 있어
    버리기 아깝다. 다만 본문이 JSON 이 아닐 수도 있고(프록시 오류 페이지 등),
    422 는 detail 이 문장이 아니라 리스트로 오므로 둘 다 걸러 낸다.
    """
    try:
        detail = response.json().get("detail", "")
    except ValueError:
        return ""
    return detail if isinstance(detail, str) else ""


def api(method: str, path: str, **kwargs):
    """백엔드를 부르고 JSON 을 돌려준다. 실패는 ApiError 계열로 던진다.

    kwargs 는 httpx.request 에 그대로 넘어간다 (json=, params=, headers= 등).
    본문이 없는 응답(204)은 None 을 돌려준다.
    """
    try:
        response = httpx.request(
            method, f"{BACKEND_URL}{path}", timeout=HTTP_TIMEOUT, **kwargs
        )
    except httpx.ConnectError:
        raise ApiError(
            "백엔드 서버에 연결할 수 없습니다. "
            "backend 폴더에서 `uv run uvicorn app.main:app --reload` 가 떠 있는지 확인하세요."
        )
    except httpx.TimeoutException:
        raise ApiError("서버가 제때 응답하지 않았습니다. 잠시 후 다시 시도하세요.")

    # 401 을 빈 목록으로 처리하면 화면에 "대화가 없습니다"가 떠서 기록이 사라진 것처럼 보인다.
    # 그래서 전용 예외로 던지고, 문구에 기록이 남아 있다는 사실을 반드시 넣는다.
    if response.status_code == 401:
        raise SessionExpired(
            "로그인이 만료되었습니다. 기록은 그대로 있으니 다시 로그인해 주세요."
        )

    # 422 = 보낸 값의 형식이 서버 검증에 걸림.
    if response.status_code == 422:
        raise ApiError(
            "입력한 값의 형식이 올바르지 않습니다. "
            "user_id 는 `3fa85f64-5717-4562-b3fc-2c963f66afa6` 같은 UUID 여야 합니다."
        )

    # 503 = 모델 호출 실패. 백엔드가 detail 에 원인을 담아 보낸다.
    # 429(하루 한도 초과)는 재시도해도 안 되므로 다른 문구를 쓴다.
    if response.status_code == 503:
        detail = _error_detail(response)
        if "429" in detail or "RESOURCE_EXHAUSTED" in detail:
            raise ApiError(_QUOTA_MESSAGE)
        raise ApiError(f"답변을 만들지 못했습니다. {detail}")

    if response.status_code >= 400:
        raise ApiError(
            f"요청이 실패했습니다 (상태 코드 {response.status_code}). {_error_detail(response)}".rstrip()
        )

    return response.json() if response.content else None


# 한도 초과 문구는 api() 와 stream_answer() 가 함께 쓴다.
_QUOTA_MESSAGE = (
    "오늘 쓸 수 있는 AI 요청 횟수를 다 썼습니다. "
    "무료 등급은 모델마다 하루 요청 수가 정해져 있습니다. "
    "내일 다시 시도하거나 강사에게 알리세요."
)


def stream_answer(path: str, payload: dict | None = None, headers: dict | None = None):
    """SSE 응답을 글자 조각으로 하나씩 내어주는 제너레이터.

    화면은 ``st.write_stream(stream_answer(...))`` 로 받아 조각을 이어 붙여 그린다.

    이벤트는 세 가지다.

    - {"text": ...}   글자 조각. 그대로 yield 한다
    - {"error": ...}  스트림 도중의 실패. 헤더가 이미 나가 상태 코드로는 알 수 없어
                      서버가 이벤트로 보낸다. 여기서 ApiError 로 바꿔 올린다
    - {"done": true}  끝. 제너레이터를 종료한다

    스트림이 열리기 전에 거절된 경우(404, 400 등)는 상태 코드가 정상적으로 온다.
    다만 스트림 모드라 ``response.read()`` 로 본문을 마저 읽어야 detail 을 꺼낼 수 있다.
    """
    try:
        with httpx.stream(
            "POST",
            f"{BACKEND_URL}{path}",
            json=payload or {},
            headers=headers,                                      # None 이면 httpx 가 무시한다
            timeout=HTTP_TIMEOUT,
        ) as response:
            if response.status_code == 401:
                response.read()
                raise SessionExpired(                             # api() 의 401 처리와 같은 문장
                    "로그인이 만료되었습니다. 기록은 그대로 있으니 다시 로그인해 주세요."
                )
            if response.status_code >= 400:
                response.read()
                raise ApiError(
                    f"요청이 실패했습니다 (상태 코드 {response.status_code}). {_error_detail(response)}".rstrip()
                )

            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue                                      # 이벤트 구분용 빈 줄 등
                event = json.loads(line.removeprefix("data: "))
                if "error" in event:
                    detail = event["error"]
                    if "429" in detail or "RESOURCE_EXHAUSTED" in detail:
                        raise ApiError(_QUOTA_MESSAGE)
                    raise ApiError(f"답변을 만들지 못했습니다. {detail}")
                if event.get("done"):
                    return
                yield event["text"]
    except httpx.ConnectError:
        raise ApiError(
            "백엔드 서버에 연결할 수 없습니다. "
            "backend 폴더에서 `uv run uvicorn app.main:app --reload` 가 떠 있는지 확인하세요."
        )
    except httpx.TimeoutException:
        raise ApiError("응답이 너무 오래 걸려 중단했습니다. 다시 시도해 보세요.")


def conversation_label(conversation: dict) -> str:
    """대화 목록 한 줄에 표시할 라벨을 만든다.

    예: "백엔드 개발자 · 2026-08-26 09:12 · 3fa85f64"

    제목만 쓰면 같은 직무로 두 번 연습했을 때 목록에서 구분할 수 없어
    만든 시각과 id 앞자리를 함께 붙인다.
    """
    title = conversation.get("title") or "(제목 없음)"
    created = conversation["created_at"][:16].replace("T", " ")
    return f"{title} · {created} · {conversation['id'][:8]}"
