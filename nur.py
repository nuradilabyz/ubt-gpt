# nur.py с исправлениями
import streamlit as st
from supabase import create_client, Client
from datetime import datetime
from openai import OpenAI, RateLimitError
import uuid
import time
import os
from dotenv import load_dotenv

# Загружаем .env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ====== Психологтың нұсқауы ======
PSYCHOLOGY_PROMPT = """
Сен ЕНТ-ға дайындалатын оқушыларға қолдау көрсететін достық психолог-консультантсың.
Жауаптарың түсінікті, жанашыр және практикалық кеңестер беретін болуы керек.
Оқушының жағдайын мұқият талдап, оған қолдау көрсет және нақты шешімдер ұсын.
Жауаптарың қазақ тілінде болуы керек.
Егер алдыңғы хабарламалар болса, оларды ескеріп, әңгімені жалғастыр.
Алдыңғы хабарламалар: {previous_messages}
"""

# ====== Чатты басқару функциялары ======
def load_psychology_chat_titles():
    try:
        response = supabase.table("psychology_chats").select("id, title, created_at").execute()
        chats = sorted(response.data, key=lambda x: x["created_at"], reverse=True)
        return chats
    except Exception as e:
        st.error(f"Чат тарихын жүктеу кезінде қате: {str(e)}")
        return []

def load_psychology_chat(chat_id):
    try:
        response = supabase.table("psychology_chats").select("messages").eq("id", chat_id).execute()
        if response.data:
            return response.data[0]["messages"]
        return []
    except Exception as e:
        st.error(f"Чатты жүктеу кезінде қате: {str(e)}")
        return []

def save_psychology_chat(chat_id: str, user_id: str, messages: list, title: str):
    try:
        existing = supabase.table("psychology_chats").select("id").eq("id", chat_id).execute()
        if existing.data:
            supabase.table("psychology_chats").update({"messages": messages, "title": title}).eq("id", chat_id).execute()
        else:
            supabase.table("psychology_chats").insert({
                "id": chat_id,
                "user_id": user_id,
                "messages": messages,
                "title": title,
                "created_at": datetime.utcnow().isoformat()
            }).execute()
    except Exception as e:
        st.error(f"Чатты сақтау кезінде қате: {str(e)}")

def delete_psychology_chat(chat_id):
    try:
        response = supabase.table("psychology_chats").delete().eq("id", chat_id).execute()
        return response.data is not None
    except Exception as e:
        st.error(f"Чатты жою кезінде қате: {str(e)}")
        return False

def rename_psychology_chat(chat_id, new_name):
    if not new_name:
        return False, "Жаңа атау бос болмауы керек."
    try:
        response = supabase.table("psychology_chats").select("id").eq("title", new_name).execute()
        if response.data:
            return False, "Бұл атаумен чат бар."
        supabase.table("psychology_chats").update({"title": new_name}).eq("id", chat_id).execute()
        return True, new_name
    except Exception as e:
        return False, f"Чат атауын өзгерту кезінде қате: {str(e)}"

def create_new_psychology_chat():
    try:
        chat_id = str(uuid.uuid4())
        title = datetime.now().strftime("%H:%M:%S")  # Изменено на час:минута:секунда для уникальности
        return chat_id, title
    except Exception as e:
        st.error(f"Жаңа чат құру кезінде қате: {str(e)}")
        return None, None

# ====== CSS ======
# CSS = """
# <style>
#     .stApp {
#         background-color: #000000;
#         color: #ffffff;
#         max-width: 1200px;
#         margin: 0 auto;
#         font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
#     }
#     .css-1d391kg {
#         background-color: #1a1a1a !important;
#         border-right: 1px solid #444 !important;
#         padding: 1rem;
#     }
#     [data-testid="stSidebar"] {
#         width: 300px !important;
#         background-color: #1a1a1a !important;
#     }
#     .chat-history-item {
#         background-color: #00cc00 !important;
#         border: 1px solid #00b300 !important;
#         color: #ffffff !important;
#         padding: 10px;
#         margin: 5px 0;
#         border-radius: 8px;
#         cursor: pointer;
#         display: flex;
#         justify-content: space-between;
#         align-items: center;
#         transition: background-color 0.2s;
#     }
#     .chat-history-item:hover {
#         background-color: #00b300 !important;
#     }
#     .chat-history-item.active {
#         background-color: #00b300 !important;
#         border-color: #00a300 !important;
#     }
#     .chat-action-btn {
#         background: none;
#         border: none;
#         cursor: pointer;
#         padding: 5px;
#         color: #ccc !important;
#         font-size: 14px;
#     }
#     .chat-action-btn:hover {
#         color: #ffffff !important;
#     }
#     .new-chat-btn {
#         background-color: #00cc00 !important;
#         color: #ffffff !important;
#         border-radius: 8px;
#         padding: 10px;
#         text-align: center;
#         margin-bottom: 10px;
#         cursor: pointer;
#     }
#     .new-chat-btn:hover {
#         background-color: #00b300 !important;
#     }
#     .stChatMessage {
#         border-radius: 10px;
#         padding: 15px;
#         margin: 10px 0;
#         max-width: 80%;
#         background-color: #00cc00 !important;
#         color: #ffffff !important;
#     }
#     .stChatMessage.user {
#         margin-left: auto;
#     }
#     .stChatMessage.assistant {
#         margin-right: auto;
#         background-color: rgba(0, 204, 0, 0.2) !important;
#     }
#     .stChatInput {
#         position: fixed;
#         bottom: 20px;
#         width: calc(100% - 340px);
#         max-width: 800px;
#         left: 50%;
#         transform: translateX(-50%);
#         background-color: #1a1a1a !important;
#         border: 1px solid #444 !important;
#         border-radius: 20px;
#         padding: 10px;
#         color: #ffffff !important;
#     }
#     .stButton > button {
#         background-color: #00cc00 !important;
#         color: #ffffff !important;
#         border-radius: 8px;
#     }
#     .stButton > button:hover {
#         background-color: #00b300 !important;
#     }
#     .stTextInput > div > input {
#         background-color: #1a1a1a !important;
#         color: #ffffff !important;
#         border: 1px solid #444 !important;
#     }
#     .header-container {
#         display: flex;
#         justify-content: space-between;
#         align-items: center;
#         margin-bottom: 20px;
#     }
#     .header-container h1 {
#         margin: 0;
#         color: #ffffff !important;
#         background-color: #00cc00 !important;
#         padding: 5px 10px;
#         border-radius: 5px;
#     }
# </style>
# """

def psychology_page():
    # ====== Сессия күйін инициализациялау ======
    if "action_state" not in st.session_state:
        st.session_state.action_state = {"action": None, "chat_id": None}
    if "psychology_chat_id" not in st.session_state:
        chat_id, title = create_new_psychology_chat()
        if chat_id is None:
            st.error("Жаңа чат құру мүмкін болмады. Supabase-те 'psychology_chats' таблицасы бар екеніне көз жеткізіңіз.")
            return
        st.session_state.psychology_chat_id = chat_id
        st.session_state.psychology_chat_title = title
        st.session_state.psychology_messages = []

    st.title("SENIŃ NURYŃ✨")
    # ====== Интерфейс ======
    # st.markdown(unsafe_allow_html=True)
    # st.markdown("""
    # <div class="header-container">
    #     <h1>🧠 ЕНТ Психолог-Көмекшісі</h1>
    # </div>
    # """, unsafe_allow_html=True)

    # ====== Бүйірлік панель ======
    with st.sidebar:
        st.markdown(
            "<h2 style='text-align: center; color: #ffffff; background-color: #00cc00; padding: 10px; border-radius: 8px;'>💬 Чаттар</h2>",
            unsafe_allow_html=True)

        # Жаңа чат түймесі
        if st.button("🆕 Жаңа чат", key="new_psychology_chat", help="Жаңа чат бастау"):
            chat_id, title = create_new_psychology_chat()
            if chat_id is None:
                st.error("Жаңа чат құру мүмкін болмады. Supabase-те 'psychology_chats' таблицасы бар екеніне көз жеткізіңіз.")
                return
            st.session_state.psychology_chat_id = chat_id
            st.session_state.psychology_chat_title = title
            st.session_state.psychology_messages = []
            st.session_state.action_state = {"action": None, "chat_id": None}
            st.rerun()

        # Чат тарихы
        chat_files = load_psychology_chat_titles()
        for chat in chat_files:
            chat_id = chat["id"]
            chat_title = chat["title"]
            active = chat_id == st.session_state.get("psychology_chat_id", "")
            css_class = "chat-history-item active" if active else "chat-history-item"

            with st.container():
                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    if st.button(chat_title, key=f"select_psychology_{chat_id}", help="Чатты ашу"):
                        st.session_state.psychology_chat_id = chat_id
                        st.session_state.psychology_chat_title = chat_title
                        st.session_state.psychology_messages = load_psychology_chat(chat_id)
                        st.session_state.action_state = {"action": None, "chat_id": None}
                        st.rerun()
                with col2:
                    if st.button("✏️", key=f"rename_psychology_{chat_id}", help="Чат атауын өзгерту"):
                        st.session_state.action_state = {"action": "rename", "chat_id": chat_id}
                        st.rerun()
                with col3:
                    if st.button("🗑️", key=f"delete_psychology_{chat_id}", help="Чатты жою"):
                        st.session_state.action_state = {"action": "delete", "chat_id": chat_id}
                        st.rerun()

                # Атауды өзгерту немесе жою әрекеттерін өңдеу
                if st.session_state.action_state["chat_id"] == chat_id:
                    if st.session_state.action_state["action"] == "rename":
                        new_name = st.text_input("Жаңа атау енгізіңіз:", key=f"rename_input_psychology_{chat_id}")
                        col_save, col_cancel = st.columns(2)
                        with col_save:
                            if st.button("Сақтау", key=f"save_rename_psychology_{chat_id}"):
                                success, result = rename_psychology_chat(chat_id, new_name)
                                if success:
                                    if chat_id == st.session_state.get("psychology_chat_id", ""):
                                        st.session_state.psychology_chat_title = result
                                    st.session_state.action_state = {"action": None, "chat_id": None}
                                    st.rerun()
                                else:
                                    st.error(result)
                        with col_cancel:
                            if st.button("Болдырмау", key=f"cancel_rename_psychology_{chat_id}"):
                                st.session_state.action_state = {"action": None, "chat_id": None}
                                st.rerun()
                    elif st.session_state.action_state["action"] == "delete":
                        st.warning(f"'{chat_title}' чатын жоюды растаңыз:")
                        col_confirm, col_cancel = st.columns(2)
                        with col_confirm:
                            if st.button("Иә, жою", key=f"confirm_delete_psychology_{chat_id}"):
                                if delete_psychology_chat(chat_id):
                                    if chat_id == st.session_state.get("psychology_chat_id", ""):
                                        chat_id, title = create_new_psychology_chat()
                                        if chat_id is None:
                                            st.error("Жаңа чат құру мүмкін болмады. Supabase-те 'psychology_chats' таблицасы бар екеніне көз жеткізіңіз.")
                                        return
                                        st.session_state.psychology_chat_id = chat_id
                                        st.session_state.psychology_chat_title = title
                                        st.session_state.psychology_messages = []
                                    st.session_state.action_state = {"action": None, "chat_id": None}
                                    st.rerun()
                                else:
                                    st.error("Чатты жою кезінде қате шықты.")
                        with col_cancel:
                            if st.button("Жоқ", key=f"cancel_delete_psychology_{chat_id}"):
                                st.session_state.action_state = {"action": None, "chat_id": None}
                                st.rerun()

    # ====== Негізгі мазмұн ======
    user_id = "anonymous"  # Болашақта Supabase аутентификациясымен ауыстыруға болады

    # Таңдалған чаттың хабарламаларын көрсету
    for msg in st.session_state.psychology_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Чат енгізу
    user_input = st.chat_input("✍️ Жағдайды сипаттаңыз немесе сұрақ қойыңыз...", key="psychology_input")
    if user_input:
        # Пайдаланушы хабарламасын қосу
        st.session_state.psychology_messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.spinner("Кеңес дайындалуда..."):
            max_retries = 5
            retry_delay = 10  # Initial delay in seconds
            for attempt in range(max_retries):
                try:
                    # Алдыңғы хабарламаларды шектеу (соңғы 3 хабарлама)
                    previous_messages = "\n".join([f"{msg['role']}: {msg['content']}" for msg in st.session_state.psychology_messages[-3:]])
                    prompt = PSYCHOLOGY_PROMPT.format(previous_messages=previous_messages) + f"\nОқушының жағдайы: {user_input}"

                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": "Сен тәжірибелі психологсың, оқушыларға кеңес бересің."},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.7
                    )

                    answer = response.choices[0].message.content
                    st.session_state.psychology_messages.append({"role": "assistant", "content": answer})
                    with st.chat_message("assistant"):
                        st.markdown(answer)

                    # Чатты Supabase-те сақтау
                    save_psychology_chat(
                        chat_id=st.session_state.psychology_chat_id,
                        user_id=user_id,
                        messages=st.session_state.psychology_messages,
                        title=st.session_state.psychology_chat_title
                    )
                    break
                except RateLimitError:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        st.error("Қате: OpenAI лимиті асып кетті. 2-3 минут күтіңіз немесе OpenAI есептік жазбаңызды тексеріңіз: https://platform.openai.com/account/usage")
                        st.session_state.psychology_messages.append({
                            "role": "assistant",
                            "content": "Кешіріңіз, қазір жауап бере алмаймын. Лимитке жеттіңіз. 2-3 минут күтіңіз немесе OpenAI есептік жазбаңызды тексеріңіз: https://platform.openai.com/account/usage"
                        })
                        with st.chat_message("assistant"):
                            st.markdown(st.session_state.psychology_messages[-1]["content"])
                        break
                except Exception as e:
                    st.error(f"Қате: {str(e)}")
                    break
                time.sleep(5)  # Delay to avoid rapid requests