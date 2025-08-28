# streamlit_app.py с исправлениями
import os
import streamlit as st
from supabase import create_client, Client
from openai import OpenAI, RateLimitError
from test import test_page  # test.py файлы
from nur import psychology_page, create_new_psychology_chat  # nur.py файлы
import uuid
from datetime import datetime
import time

import os
from dotenv import load_dotenv

# Загружаем .env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
# Пәндер бойынша ассистенттер мен векторлық қоймалар тізімі
SUBJECTS = {
    "Құқық негіздері": {
        "assistant_id": "asst_rfV8LBcsVkWveXkvJeLznQZL",
        "vector_store_id": "vs_689c92f46c6481918819bb833479faed"
    },
    "Информатика": {
        "assistant_id": "asst_NHeMToF2A2rQkyCW1hAcc98p",
        "vector_store_id": "vs_68a96e6989fc8191bd809a04a3b9262e"
    },
    "Қазақ_әдебиеті": {
        "assistant_id": "asst_WQI6p782I60JIQ5wFaRpscpp",
        "vector_store_id": "vs_68a9816503d481918df7ad5d636adf77"
    },
    "Дүниежүзі тарихы": {
        "assistant_id": "asst_HXTVe2JcPQOVteAH2vaTL6yy",
        "vector_store_id": "vs_68a9839f37e08191ad61d66b7f865834"
    },
    "Қазақ тілі": {
        "assistant_id": "asst_UyyzU3SiuA9sYifctwpR4X1G",
        "vector_store_id": "vs_68a9842055cc8191b18c50a9abf0a887"
    },
    "Химия": {
        "assistant_id": "asst_CDN5PTMSNk7SHHQT0IBlCPtZ",
        "vector_store_id": "vs_68a984b9919081918e3726352f84b3bf"
    },
    "Қазақстан тарихы": {
        "assistant_id": "asst_KXQFvlA3gXcDYK7R45uVUCoy",
        "vector_store_id": "vs_68a9870f966c819195becdd8590e966a"
    },
    "География": {
        "assistant_id": "asst_r5okZdEd4vRal023v837k4Yz",
        "vector_store_id": "vs_68aab608a65c8191a4fa93b4b7fefbaf"
    }
}

# ====== Чатты басқару функциялары ======
def delete_main_chat(chat_id):
    try:
        response = supabase.table("main_chats").delete().eq("id", chat_id).execute()
        return response.data is not None
    except Exception as e:
        st.error(f"Чатты жою кезінде қате: {str(e)}")
        return False

def rename_main_chat(chat_id, new_name):
    if not new_name:
        return False, "Жаңа атау бос болмауы керек."
    try:
        response = supabase.table("main_chats").select("id").eq("title", new_name).execute()
        if response.data:
            return False, "Бұл атаумен чат бар."
        supabase.table("main_chats").update({"title": new_name}).eq("id", chat_id).execute()
        return True, new_name
    except Exception as e:
        return False, f"Чат атауын өзгерту кезінде қате: {str(e)}"

def create_new_main_chat():
    try:
        chat_id = str(uuid.uuid4())
        title = datetime.now().strftime("%H:%M:%S")  # Изменено на час:минута:секунда для уникальности
        thread_id = client.beta.threads.create().id
        # Save new chat to Supabase with thread_id
        supabase.table("main_chats").insert({
            "id": chat_id,
            "user_id": "anonymous",
            "title": title,
            "messages": [],
            "thread_id": thread_id,
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        return chat_id, title, thread_id
    except Exception as e:
        st.error(f"Жаңа чат құру кезінде қате: {str(e)}")
        return None, None, None


def send_prompt(thread_id, prompt, subject):
    max_retries = 5
    retry_delay = 10  # Initial delay in seconds
    for attempt in range(max_retries):
        try:
            client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=prompt
            )
            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=SUBJECTS[subject]["assistant_id"],
                tools=[{"type": "file_search"}]
            )
            while run.status in ["queued", "in_progress"]:
                run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
                time.sleep(2)
            if run.status != "completed":
                error_msg = f"Ассистенттің орындау қатесі: {run.status}"
                if run.last_error:
                    error_msg += f" ({run.last_error.code}: {run.last_error.message})"
                st.error(error_msg)
                return None
            messages = client.beta.threads.messages.list(thread_id=thread_id, limit=1)
            message_content = messages.data[0].content[0].text
            annotations = message_content.annotations
            citations = []

            for index, annotation in enumerate(annotations):
                message_content.value = message_content.value.replace(annotation.text, f' [{index}]')
                if file_citation := getattr(annotation, 'file_citation', None):
                    try:
                        cited_file = client.files.retrieve(file_citation.file_id)
                        citations.append(f'[{index}] {cited_file.filename}')
                    except Exception as e:
                        st.error(f"Файлды алу кезінде қате: {e}")
                        citations.append(f'[{index}] Белгісіз файл')

            message_content.value += '\n\n' + '\n'.join(citations)
            return message_content.value
        except RateLimitError:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                st.error(
                    "Қате: OpenAI лимиті асып кетті. 2-3 минут күтіңіз немесе OpenAI есептік жазбаңызды тексеріңіз: https://platform.openai.com/account/usage")
                return None
        except Exception as e:
            st.error(f"Қате: {str(e)}")
            return None
        time.sleep(5)  # Delay to avoid rapid requests

def main_page():
    st.title("UBT-GPT")
    # Сессия күйін инициализациялау
    if "main_chat_id" not in st.session_state:
        chat_id, title, thread_id = create_new_main_chat()
        if chat_id is None:
            st.error("Жаңа чат құру мүмкін болмады. Supabase-те 'main_chats' таблицасы бар екеніне көз жеткізіңіз.")
            return
        st.session_state.main_chat_id = chat_id
        st.session_state.main_chat_title = title
        st.session_state.main_messages = []
        st.session_state.main_thread_id = thread_id

    # CSS стилі
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
    # st.markdown(CSS, unsafe_allow_html=True)

    # Бүйірлік панель
    with st.sidebar:
        st.markdown(
            "<h2 style='text-align: center; color: #ffffff; background-color: #00cc00; padding: 5px; border-radius: 20px;'>💬 Чаттар</h2>",
            unsafe_allow_html=True)

        # Жаңа чат түймесі
        if st.button("🆕Жаңа чат", key="new_main_chat", help="Жаңа чат бастау"):
            chat_id, title, thread_id = create_new_main_chat()
            if chat_id is None:
                st.error("Жаңа чат құру мүмкін болмады. Supabase-те 'main_chats' таблицасы бар екеніне көз жеткізіңіз.")
                return
            st.session_state.main_chat_id = chat_id
            st.session_state.main_chat_title = title
            st.session_state.main_messages = []
            st.session_state.main_thread_id = thread_id
            st.rerun()

        # Чат тарихы
        chat_files = load_main_chat_titles()
        for chat in chat_files:
            chat_id = chat["id"]
            chat_title = chat["title"]
            active = chat_id == st.session_state.get("main_chat_id", "")
            css_class = "chat-history-item active" if active else "chat-history-item"

            with st.container():
                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    if st.button(chat_title, key=f"select_main_{chat_id}", help="Чатты ашу"):
                        st.session_state.main_chat_id = chat_id
                        st.session_state.main_chat_title = chat_title
                        st.session_state.main_messages = load_main_chat(chat_id)
                        thread_id = load_thread_id(chat_id)
                        if not thread_id:
                            try:
                                thread_id = client.beta.threads.create().id
                                save_thread_id(chat_id, thread_id)
                            except Exception as e:
                                st.error(f"Чат ағынын құру кезінде қате: {str(e)}")
                                return
                        st.session_state.main_thread_id = thread_id
                        st.rerun()
                with col2:
                    if st.button("✏️", key=f"rename_main_{chat_id}", help="Чат атауын өзгерту"):
                        st.session_state.main_action_state = {"action": "rename", "chat_id": chat_id}
                        st.rerun()
                with col3:
                    if st.button("🗑️", key=f"delete_main_{chat_id}", help="Чатты жою"):
                        st.session_state.main_action_state = {"action": "delete", "chat_id": chat_id}
                        st.rerun()

                # Атауды өзгерту немесе жою әрекеттерін өңдеу
                if st.session_state.get("main_action_state", {}).get("chat_id") == chat_id:
                    if st.session_state.main_action_state["action"] == "rename":
                        new_name = st.text_input("Жаңа атау енгізіңіз:", key=f"rename_input_main_{chat_id}")
                        col_save, col_cancel = st.columns(2)
                        with col_save:
                            if st.button("Сақтау", key=f"save_rename_main_{chat_id}"):
                                success, result = rename_main_chat(chat_id, new_name)
                                if success:
                                    if chat_id == st.session_state.get("main_chat_id", ""):
                                        st.session_state.main_chat_title = result
                                    st.session_state.main_action_state = {"action": None, "chat_id": None}
                                    st.rerun()
                                else:
                                    st.error(result)
                        with col_cancel:
                            if st.button("Болдырмау", key=f"cancel_rename_main_{chat_id}"):
                                st.session_state.main_action_state = {"action": None, "chat_id": None}
                                st.rerun()
                    elif st.session_state.main_action_state["action"] == "delete":
                        st.warning(f"'{chat_title}' чатын жоюды растаңыз:")
                        col_confirm, col_cancel = st.columns(2)
                        with col_confirm:
                            if st.button("Иә, жою", key=f"confirm_delete_main_{chat_id}"):
                                if delete_main_chat(chat_id):
                                    if chat_id == st.session_state.get("main_chat_id", ""):
                                        chat_id, title, thread_id = create_new_main_chat()
                                        if chat_id is None:
                                            st.error("Жаңа чат құру мүмкін болмады. Supabase-те 'main_chats' таблицасы бар екеніне көз жеткізіңіз.")
                                        return
                                        st.session_state.main_chat_id = chat_id
                                        st.session_state.main_chat_title = title
                                        st.session_state.main_messages = []
                                        st.session_state.main_thread_id = thread_id
                                    st.session_state.main_action_state = {"action": None, "chat_id": None}
                                    st.rerun()
                                else:
                                    st.error("Чатты жою кезінде қате шықты.")
                        with col_cancel:
                            if st.button("Жоқ", key=f"cancel_delete_main_{chat_id}"):
                                st.session_state.main_action_state = {"action": None, "chat_id": None}
                                st.rerun()

    # Негізгі мазмұн
    user_id = "anonymous"  # Болашақта Supabase аутентификациясымен ауыстыруға болады
    st.markdown("<h2 style='color: #ffffff; background-color: #00cc00; padding: 8px; border-radius: 8px;'>💬 Пәндер бойынша сұрақ-жауап</h2>", unsafe_allow_html=True)

    # Пән таңдау
    subject = st.selectbox("Пәнді таңдаңыз!", list(SUBJECTS.keys()), key="main_subject_select")

    # Таңдалған чаттың хабарламаларын көрсету
    for msg in st.session_state.main_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Чат енгізу
    user_input = st.chat_input("✍️ Сұрағыңызды енгізіңіз...", key="main_chat_input")
    if user_input:
        # Пайдаланушы хабарламасын қосу
        st.session_state.main_messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.spinner("Жауап дайындалуда..."):
            # Алдыңғы хабарламаларды шектеу (соңғы 3 хабарлама)
            previous_messages = "\n".join([f"{msg['role']}: {msg['content']}" for msg in st.session_state.main_messages[-3:]])
            prompt = f"""
Сен {subject} пәні бойынша тәжірибелі мұғалімсің. Пайдаланушының сұрағына қазақ тілінде нақты, түсінікті және оқулыққа сәйкес жауап бер.
Векторлық қоймадан (ID: {SUBJECTS[subject]["vector_store_id"]}) тиісті ақпаратты пайдалан.
Егер алдыңғы хабарламалар болса, оларды ескеріп, әңгімені жалғастыр.
Алдыңғы хабарламалар: {previous_messages}
Пайдаланушы сұрағы: {user_input}
"""
            answer = send_prompt(st.session_state.main_thread_id, prompt, subject)
            if answer:
                st.session_state.main_messages.append({"role": "assistant", "content": answer})
                with st.chat_message("assistant"):
                    st.markdown(answer)
                # Чатты Supabase-те сақтау
                save_main_chat(
                    chat_id=st.session_state.main_chat_id,
                    user_id=user_id,
                    messages=st.session_state.main_messages,
                    title=st.session_state.main_chat_title,
                    thread_id=st.session_state.main_thread_id
                )
            else:
                # Fallback response if rate limit is hit
                st.session_state.main_messages.append({
                    "role": "assistant",
                    "content": "Кешіріңіз, қазір жауап бере алмаймын. Лимитке жеттіңіз. 2-3 минут күтіңіз немесе OpenAI есептік жазбаңызды тексеріңіз: https://platform.openai.com/account/usage"
                })
                with st.chat_message("assistant"):
                    st.markdown(st.session_state.main_messages[-1]["content"])

def load_main_chat_titles():
    try:
        response = supabase.table("main_chats").select("id, title, created_at").execute()
        return sorted(response.data, key=lambda x: x["created_at"], reverse=True)
    except Exception as e:
        st.error(f"Чат тарихын жүктеу кезінде қате: {str(e)}")
        return []

def load_main_chat(chat_id):
    try:
        response = supabase.table("main_chats").select("messages").eq("id", chat_id).execute()
        return response.data[0]["messages"] if response.data else []
    except Exception as e:
        st.error(f"Чатты жүктеу кезінде қате: {str(e)}")
        return []

def load_thread_id(chat_id):
    try:
        response = supabase.table("main_chats").select("thread_id").eq("id", chat_id).execute()
        return response.data[0]["thread_id"] if response.data and response.data[0]["thread_id"] else None
    except Exception as e:
        st.error(f"Чат ағынын жүктеу кезінде қате: {str(e)}")
        return None

def save_thread_id(chat_id, thread_id):
    try:
        supabase.table("main_chats").update({"thread_id": thread_id}).eq("id", chat_id).execute()
    except Exception as e:
        st.error(f"Чат ағынын сақтау кезінде қате: {str(e)}")

def save_main_chat(chat_id: str, user_id: str, messages: list, title: str, thread_id: str):
    try:
        existing = supabase.table("main_chats").select("id").eq("id", chat_id).execute()
        if existing.data:
            supabase.table("main_chats").update({
                "messages": messages,
                "title": title,
                "thread_id": thread_id
            }).eq("id", chat_id).execute()
        else:
            supabase.table("main_chats").insert({
                "id": chat_id,
                "user_id": user_id,
                "messages": messages,
                "title": title,
                "thread_id": thread_id,
                "created_at": datetime.utcnow().isoformat()
            }).execute()
    except Exception as e:
        st.error(f"Чатты сақтау кезінде қате: {str(e)}")

# ====== Сессия күйін инициализациялау ======
if "action_state" not in st.session_state:
    st.session_state.action_state = {"action": None, "chat_id": None}
if "main_action_state" not in st.session_state:
    st.session_state.main_action_state = {"action": None, "chat_id": None}
if "psychology_chat_id" not in st.session_state:
    chat_id, title = create_new_psychology_chat()
    if chat_id is None:
        st.error("Жаңа чат құру мүмкін болмады. Supabase-те 'psychology_chats' таблицасы бар екеніне көз жеткізіңіз.")
        st.stop()
    st.session_state.psychology_chat_id = chat_id
    st.session_state.psychology_chat_title = title
    st.session_state.psychology_messages = []

# ====== Беттерді анықтау ======
main_page = st.Page(main_page, title="UBT-GPT🏆")
test_page = st.Page(test_page, title="TEST📝")
psychology_page = st.Page(psychology_page, title="NUR✨")
nav = st.navigation([main_page, test_page, psychology_page])

# ====== Навигация ======
nav.run()