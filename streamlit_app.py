"""면접 연습 챗봇의 화면 전체.

Streamlit 은 사용자가 버튼 하나만 눌러도 이 파일을 위에서 아래로 다시 실행한다.
그래서 값을 유지하려면 브라우저 탭별 저장소인 ``st.session_state`` 에 넣어야 하고,
"화면을 그린다"는 곧 "이 파일을 실행한다"는 뜻이다.

화면은 사용자 상태에 따라 여섯 갈래로 갈린다.

    비로그인            토큰 없음                 로그인 폼과 서비스 소개
    로그인 + 빈 목록     연습 기록 0건             첫 면접을 시작하라는 안내
    로그인 + 선택 안 함  목록은 있고 고르지 않음    무엇을 고르면 되는지
    로그인 + 내용 없음   대화는 있고 메시지 0건     예시 질문 세 개
    기본                주고받은 내용 있음         대화 화면
    세션 만료           토큰 만료                 왜 풀렸는지 + 다시 로그인

화면이 붙잡는 원칙 하나: **사용자의 행동과 시스템의 반응을 1대1로 잇는다.**

    답이 늦다            글자가 나오는 대로 흘려보낸다 (st.write_stream)
    답을 못 받았다        같은 질문으로 `다시 시도` — 다시 타이핑하게 하지 않는다
    답이 마음에 안 든다   기존 답을 지우고 `다시 생성` — 마지막 답변에만 붙는다
    평가하고 싶다        `도움됨` / `아쉬움` — 눌린 상태가 보이고 다시 누르면 취소된다

`다시 시도`와 `다시 생성`은 다르다. 하나는 실패를 복구하는 것이고,
다른 하나는 성공한 결과를 바꾸는 것이다.

대화 관련 호출에는 전부 ``headers=auth_headers()`` 를 싣는다. 백엔드의 /conversations/*
가 본인 대화일 때만 응답하기 때문이다 (남의 대화는 404).
"""

import streamlit as st

from common import (
    ApiError,
    SERVICE_NAME,
    SessionExpired,
    api,
    auth_headers,
    conversation_label,
    stream_answer,
)


# set_page_config 는 다른 st.* 호출보다 먼저, 딱 한 번만 부른다.
st.set_page_config(page_title=SERVICE_NAME, layout="centered")


# setdefault 는 "그 키가 없을 때만 기본값을 넣는다". 앱 첫 실행 때 한 번씩만 실행된다.
st.session_state.setdefault("access_token", None)      # None 이면 로그아웃 상태
st.session_state.setdefault("user_email", None)
st.session_state.setdefault("conversation_id", None)
st.session_state.setdefault("pending_question", None)  # 버튼으로 예약된 질문 (render_examples 참고)
# 세션이 풀린 이유. 토큰만 지우고 끝내면 사용자는 자기가 왜 로그아웃됐는지 모른다.
st.session_state.setdefault("expired_notice", None)
# 답을 못 받은 질문. `다시 시도` 가 이것을 쓴다. 긴 답변을 다시 타이핑하게 만들면 안 된다.
st.session_state.setdefault("failed_question", None)
# 삭제 확인 대기 중인 대화 id. 되돌릴 수 없는 동작에는 확인 단계를 둔다.
st.session_state.setdefault("confirm_delete", None)


# 빈 대화 화면에 아무것도 없으면 무엇을 써야 할지 모른다. 첫 발을 떼도록 돕는 예시.
EXAMPLE_QUESTIONS = [
    "면접을 시작해 주세요.",
    "1분 자기소개를 해보겠습니다.",
    "제 이력서에서 가장 많이 받을 질문이 뭘까요?",
]

# 백엔드 GET .../messages 의 limit 기본값(20)은 "앞에서부터 20건"이라 그대로 부르면
# 21번째 메시지부터 화면에 안 보인다. 화면은 대화 전체를 보여줘야 하므로 넉넉히 넘긴다.
MESSAGES_PAGE_LIMIT = 500


def sign_out(notice: str | None = None) -> None:
    """로그인 관련 상태를 한 번에 지우고 화면을 다시 그린다.

    로그아웃 버튼과 세션 만료 처리가 이 함수를 함께 쓴다. notice 를 주면
    다음 실행의 로그인 화면에 노란 배너로 뜬다.

    토큰만 지우고 conversation_id 를 남기면 다음 사람이 로그인했을 때 앞사람이 보던
    대화가 잠깐 보인다. 지울 곳을 한 함수에 모아 그 실수를 막는다.
    """
    st.session_state.access_token = None
    st.session_state.user_email = None
    st.session_state.conversation_id = None
    st.session_state.pending_question = None
    st.session_state.failed_question = None
    st.session_state.confirm_delete = None
    st.session_state.expired_notice = notice
    st.rerun()


@st.cache_data(ttl=300)
def load_options() -> dict:
    """백엔드에서 말투·길이 선택지와 기억 범위를 받아 5분간 캐싱한다.

    자주 바뀌지 않는 값이라 매번 부르는 것은 낭비다. 반대로, 백엔드에 항목을 새로
    추가했는데 KeyError 가 나면 5분 전 응답을 들고 있는 것이니 앱을 다시 띄우면 된다.
    """
    return api("GET", "/chat/options")


def render_login() -> None:
    """비로그인 화면. 이메일·비밀번호 입력과 로그인/회원가입 버튼."""
    # 세션이 풀려서 여기까지 온 경우에만 배너가 뜬다. 스스로 로그아웃한 사람에게는 안 뜬다.
    if st.session_state.expired_notice:
        st.warning(st.session_state.expired_notice)

    st.write("직무를 정하고 면접 질문에 답하며 연습합니다. 기록은 계정에 저장됩니다.")

    email = st.text_input("이메일", placeholder="you@example.com")
    password = st.text_input("비밀번호", type="password")

    login_column, signup_column = st.columns(2)
    action = None

    if login_column.button("로그인", use_container_width=True):
        action = "login"
    if signup_column.button("회원가입", use_container_width=True):
        action = "signup"

    if not action:
        return

    if not email or not password:
        st.error("이메일과 비밀번호를 모두 입력하세요.")
        return

    # 로그인과 회원가입은 경로만 다르고 본문 모양이 같다.
    # 이 try 는 남긴다. 로그인 실패는 화면 전체를 되돌릴 일이 아니라 그 자리에서 알려야 한다.
    try:
        result = api(
            "POST", f"/auth/{action}", json={"email": email, "password": password}
        )
    except ApiError as error:
        st.error(str(error))
        return

    # Supabase 의 이메일 확인 설정이 켜져 있으면 가입은 되지만 토큰이 None 으로 온다.
    if not result.get("access_token"):
        st.error("가입은 되었지만 바로 로그인되지 않았습니다. 강사에게 알리세요.")
        return

    st.session_state.access_token = result["access_token"]
    st.session_state.user_email = result["email"]
    st.session_state.expired_notice = None
    st.rerun()


def render_sidebar(options: dict, conversations: list) -> None:
    """왼쪽 사이드바. 연습 목록, 이름 변경·삭제, 새 면접 시작, 말투·길이 설정.

    conversations 를 인자로 받는 이유: 여기서 다시 조회하지 않고 호출한 쪽이 넘겨 주면
    세션 만료 예외를 파일 맨 아래 한 곳에서 잡을 수 있다.
    """
    with st.sidebar:
        st.caption(st.session_state.user_email)
        if st.button("로그아웃", use_container_width=True):
            sign_out()

        st.divider()
        st.subheader("연습 기록")

        if conversations:
            # { 대화id: 화면에 보일 라벨 }. id 를 키로 두어야 선택 결과로 라벨을 역조회할 수 있다.
            labels = {c["id"]: conversation_label(c) for c in conversations}
            ids = list(labels)
            current = st.session_state.conversation_id

            # index 와 key 를 주지 않으면 화면을 다시 그릴 때마다 첫 항목으로 리셋된다.
            selected = st.selectbox(
                "지난 연습",
                options=ids,
                format_func=lambda cid: labels[cid],
                index=ids.index(current) if current in ids else 0,
                key="conversation_select",
            )
            st.session_state.conversation_id = selected

            new_title = st.text_input("새 이름", key="rename_input")
            rename_column, delete_column = st.columns(2)

            if rename_column.button("이름 변경", use_container_width=True) and new_title:
                api(
                    "PATCH",
                    f"/me/conversations/{selected}",
                    json={"title": new_title},
                    headers=auth_headers(),
                )
                st.rerun()

            # 삭제는 두 단계로 나눈다. 되돌릴 수 없는 동작이라 실수로 누르면
            # 면접 기록이 통째로 사라진다.
            if delete_column.button("삭제", use_container_width=True):
                st.session_state.confirm_delete = selected
                st.rerun()
            if st.session_state.confirm_delete == selected:
                st.error(f"'{labels[selected]}' 의 면접 기록이 모두 지워집니다. 되돌릴 수 없습니다.")
                confirm_column, cancel_column = st.columns(2)
                if confirm_column.button("정말 삭제", type="primary", use_container_width=True):
                    api("DELETE", f"/me/conversations/{selected}", headers=auth_headers())
                    st.session_state.conversation_id = None
                    st.session_state.confirm_delete = None
                    st.rerun()
                if cancel_column.button("취소", use_container_width=True):
                    st.session_state.confirm_delete = None
                    st.rerun()
        else:
            st.caption("아직 연습 기록이 없습니다.")

        st.divider()
        job_title = st.text_input("직무", placeholder="예: 백엔드 개발자")
        if st.button("새 면접 시작", use_container_width=True) and job_title:
            # user_id 를 보내지 않는다. 서버가 토큰에서 꺼내 쓰므로 남의 명의로 만들 수 없다.
            created = api(
                "POST",
                "/me/conversations",
                json={"title": job_title},
                headers=auth_headers(),
            )
            st.session_state.conversation_id = created["id"]
            st.rerun()

        st.divider()
        st.subheader("면접관 설정")
        # key 를 주면 선택값이 session_state 에 자동 저장된다 (st.session_state.tone 등).
        st.radio("말투", options["tones"], key="tone", horizontal=True)
        st.radio("답변 길이", options["lengths"], key="length", horizontal=True)
        st.caption("고른 값은 다음 질문부터 적용됩니다.")


def render_empty(message: str, hint: str) -> None:
    """빈 화면 안내 카드. 안내와 힌트를 둘 다 인자로 강제해 "다음에 뭘 하면 되는지"를 빠뜨리지 않게 한다."""
    st.info(message)
    st.caption(hint)


def ask(conversation_id: str, question: str) -> None:
    """질문을 보내고 답이 흘러나오는 것을 보여준다.

    스트리밍은 전체 시간을 줄이지 않는다. 줄이는 것은 아무것도 없는 화면을 보는 시간이다.

    실패하면 질문을 failed_question 에 남긴다. render_conversation 의 `다시 시도` 가
    그것을 쓴다. 다만 세션 만료는 화면 전체를 되돌려야 하므로 다시 던져
    파일 맨 아래 한 곳에서 처리하게 한다.
    """
    # 내 질문을 먼저 그린다. 아직 DB 목록에는 없지만 "보내졌다"는 느낌을 즉시 준다.
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        try:
            st.write_stream(
                stream_answer(
                    f"/conversations/{conversation_id}/chat",
                    {
                        "content": question,
                        "tone": st.session_state.tone,
                        "length": st.session_state.length,
                    },
                    auth_headers(),
                )
            )
        except SessionExpired:
            raise
        except ApiError as error:
            st.session_state.failed_question = question
            st.error(str(error))
            return                   # rerun 하지 않는다. 오류 문구가 보여야 한다

    st.session_state.failed_question = None   # 비우지 않으면 `다시 시도` 가 계속 보인다
    st.rerun()                                # 저장된 메시지가 목록에서 다시 그려지도록


def regenerate(conversation_id: str) -> None:
    """마지막 답변을 지우고 새로 받는다.

    질문을 다시 보내지 않는다. 서버가 마지막 사용자 질문을 그대로 쓰므로
    말투와 길이만 보낸다 (바꿔 놓고 다시 생성하는 경우가 많다).
    """
    with st.chat_message("assistant"):
        try:
            st.write_stream(
                stream_answer(
                    f"/conversations/{conversation_id}/regenerate",
                    {
                        "tone": st.session_state.tone,
                        "length": st.session_state.length,
                    },
                    auth_headers(),
                )
            )
        except SessionExpired:
            raise
        except ApiError as error:
            st.error(str(error))
            return
    st.rerun()


def render_examples() -> None:
    """대화 첫 진입 시 예시 질문 세 개를 버튼으로 보여준다.

    버튼 안에서 바로 ask() 를 부르면 화면을 다시 그리는 도중이라 결과가 나타나지 않는다.
    그래서 질문을 pending_question 에 예약하고 rerun 한 뒤, 다음 실행에서 처리한다.
    """
    st.caption("이렇게 시작해 보세요")
    columns = st.columns(len(EXAMPLE_QUESTIONS))
    for column, question in zip(columns, EXAMPLE_QUESTIONS):
        if column.button(question, use_container_width=True):
            st.session_state.pending_question = question
            st.rerun()


def render_follow_ups() -> None:
    """면접관 답변 아래의 후속 액션 세 개.

    면접관이 이전 대화를 기억하므로 "방금"이라고만 해도 알아듣는다.
    직전 답변을 질문 안에 통째로 넣어 보낼 필요가 없어 저장되는 메시지도 짧다.
    """
    st.caption("이어서")
    actions = {
        "더 자세히": "방금 한 말을 예시를 들어 더 자세히 설명해 주세요.",
        "간단하게": "방금 한 말을 세 문장으로 줄여 주세요.",
        "다음 질문": "다음 면접 질문을 하나 주세요.",
    }
    columns = st.columns(len(actions))
    for column, (label, question) in zip(columns, actions.items()):
        if column.button(label, use_container_width=True):
            st.session_state.pending_question = question
            st.rerun()


def _remembered_count(messages: list, max_history: int) -> int:
    """모델에게 실제로 갈 메시지 수.

    백엔드 chat.py 의 _build_history 와 **같은 순서**로 센다.
    ① 마지막 초기화 지점 이후만 ② user/assistant 만 ③ 최대 max_history 개.
    순서가 어긋나면 화면의 안내 숫자가 실제와 달라진다. 고칠 때는 둘을 나란히 놓고 대조한다.
    """
    for index in range(len(messages) - 1, -1, -1):
        if messages[index]["role"] == "system":
            messages = messages[index + 1 :]
            break
    usable = [m for m in messages if m["role"] in ("user", "assistant")]
    return min(len(usable), max_history)


def render_context_controls(conversation_id: str, messages: list, max_history: int) -> None:
    """면접관이 무엇을 기억하는지 보여주고, 끊을 수 있게 한다.

    사용자는 모델이 무엇을 참고하는지 볼 수 없다. 화면이 말해 주지 않으면
    "왜 아까 한 말을 기억 못 하지" 또는 "왜 지운 얘기를 계속 하지"가 된다.

    max_history 를 인자로 받는 이유: 화면에 20 을 직접 적으면 백엔드에서 그 값을 고쳤을 때
    안내가 거짓말이 된다. /chat/options 가 내려준 값을 그대로 쓴다.

    초기화는 메시지를 지우지 않는다. 그 사실을 문구로 알려야 사용자가 무서워하지 않는다.
    """
    remembered = _remembered_count(messages, max_history)
    reset_column, info_column = st.columns([1, 3])
    if reset_column.button("맥락 초기화", use_container_width=True):
        api(
            "POST",
            f"/conversations/{conversation_id}/reset-context",
            headers=auth_headers(),
        )
        st.rerun()
    info_column.caption(
        f"면접관은 지금 이 대화의 최근 {remembered}개를 기억합니다 "
        f"(최대 {max_history}개). 초기화해도 기록은 남습니다."
    )


def render_feedback(conversation_id: str, message_id: str, current: str | None) -> None:
    """도움됨 / 아쉬움 버튼.

    이미 누른 것은 진한 색(primary)으로 보여야 한다. 안 그러면 자기가 평가했는지
    기억하지 못하고 계속 누른다. 그리고 같은 버튼을 다시 누르면 취소된다.
    되돌릴 수 없으면 아무도 누르지 않는다.

    on_click 은 화면을 다시 그리기 '전에' 실행되므로, 서버 저장이 끝난 뒤 새 상태가 그려진다.
    key 에 메시지 id 를 넣는 것은 같은 라벨의 버튼이 여러 개라 구분이 필요하기 때문이다.
    """
    up_column, down_column, _ = st.columns([1, 1, 8])
    up_column.button(
        "도움됨",
        key=f"up_{message_id}",
        type="primary" if current == "up" else "secondary",
        on_click=_toggle_feedback,
        args=(conversation_id, message_id, "up", current),
    )
    down_column.button(
        "아쉬움",
        key=f"down_{message_id}",
        type="primary" if current == "down" else "secondary",
        on_click=_toggle_feedback,
        args=(conversation_id, message_id, "down", current),
    )


def _toggle_feedback(conversation_id: str, message_id: str, value: str, current: str | None) -> None:
    """평가 버튼의 on_click. 같은 것을 다시 누르면 취소(None), 아니면 그 값으로 덮어쓴다."""
    api(
        "POST",
        f"/conversations/{conversation_id}/feedback",
        json={"message_id": message_id, "value": None if current == value else value},
        headers=auth_headers(),
    )


def render_conversation(conversation_id: str, max_history: int) -> None:
    """가운데 대화 창 전체.

    지난 메시지 → 평가·다시 생성 → 다시 시도 → 기억 범위 → 후속 액션 → 입력칸 순으로 그린다.
    """
    # limit 을 넉넉히 넘긴다. 기본값 20 은 앞에서부터 20건이라 긴 대화가 잘린다.
    messages = api(
        "GET", f"/conversations/{conversation_id}/messages",
        params={"limit": MESSAGES_PAGE_LIMIT},
        headers=auth_headers(),
    )
    # {메시지id: "up"/"down"}. 아무것도 없으면 {}
    feedback = api(
        "GET", f"/conversations/{conversation_id}/feedback", headers=auth_headers()
    ) or {}

    if not messages:
        render_empty(
            "아직 주고받은 내용이 없습니다.",
            "아래 예시를 누르거나 직접 입력해서 면접을 시작하세요.",
        )
        render_examples()

    last_index = len(messages) - 1
    for index, message in enumerate(messages):
        if message["role"] == "system":
            # 맥락을 끊은 지점. 누가 한 말이 아니라 "여기서 끊겼다"는 표시라 말풍선이 아니다.
            st.divider()
            st.caption(message["content"])
            continue
        with st.chat_message(message["role"]):
            st.write(message["content"])
            if message["role"] == "assistant":
                render_feedback(
                    conversation_id, message["id"], feedback.get(message["id"])
                )
                if index == last_index:
                    # 다시 생성은 마지막 답변에만 붙인다. 중간 답변을 새로 만들면
                    # 그 뒤의 대화와 앞뒤가 안 맞는다.
                    if st.button("다시 생성", key=f"regen_{message['id']}"):
                        regenerate(conversation_id)

    # 답을 못 받은 상태. 같은 질문을 그대로 다시 보낼 수 있게 한다.
    if st.session_state.failed_question:
        st.warning("답변을 받지 못했습니다.")
        retry_column, cancel_column, _ = st.columns([1, 1, 6])
        if retry_column.button("다시 시도"):
            question = st.session_state.failed_question
            st.session_state.failed_question = None
            ask(conversation_id, question)
        if cancel_column.button("취소"):
            st.session_state.failed_question = None
            st.rerun()

    if messages:
        render_context_controls(conversation_id, messages, max_history)

    # 마지막이 면접관 답변일 때만 후속 액션을 보여준다.
    if messages and messages[-1]["role"] == "assistant":
        render_follow_ups()

    # 버튼이 예약해 둔 질문을 지금 처리한다. 즉시 비워야 다음 실행에서 두 번 보내지지 않는다.
    if st.session_state.pending_question:
        question = st.session_state.pending_question
        st.session_state.pending_question = None
        ask(conversation_id, question)

    if answer := st.chat_input("답변을 입력하세요"):
        ask(conversation_id, answer)


def render_signed_in() -> None:
    """로그인한 뒤의 화면 전체.

    여기서 나는 SessionExpired 는 파일 맨 아래가 한 번에 잡는다. 호출마다 try 를 쓰면
    스무 군데가 되고, 한 곳만 빠뜨려도 거기서 화면이 비어 보인다.
    """
    options = load_options()

    # 사용자가 고른 값이 있으면 유지되도록 setdefault 를 쓴다.
    st.session_state.setdefault("tone", options["default_tone"])
    st.session_state.setdefault("length", options["default_length"])

    # RLS 덕분에 조건 없이 조회해도 내 것만 온다.
    conversations = api("GET", "/me/conversations", headers=auth_headers())
    render_sidebar(options, conversations)

    st.caption(f"말투 {st.session_state.tone} · 길이 {st.session_state.length}")

    if not conversations:
        render_empty(
            "아직 연습 기록이 없습니다.",
            "왼쪽에서 지원할 직무를 적고 `새 면접 시작` 을 누르세요.",
        )
    elif not st.session_state.conversation_id:
        # 방어 가지. selectbox 가 첫 항목을 자동으로 고르므로 평소에는 닿지 않지만,
        # 선택이 비면 render_conversation(None) 이 되어 422 가 난다.
        render_empty(
            "연습할 면접을 고르세요.",
            "왼쪽 `지난 연습` 에서 하나를 선택하면 됩니다.",
        )
    else:
        render_conversation(
            st.session_state.conversation_id, options["max_history_messages"]
        )


# ── 실행 시작점 ───────────────────────────────────────────────────────

st.title(SERVICE_NAME)

# 세션 만료를 여기 한 곳에서만 처리한다. 어느 함수에서 튀어나오든 여기서 받는다.
try:
    if st.session_state.access_token:
        render_signed_in()
    else:
        render_login()
except SessionExpired as error:
    # 토큰을 지우고 로그인 화면으로. 이유는 다음 실행에서 노란 배너로 보여준다.
    sign_out(str(error))
except ApiError as error:
    st.error(str(error))
