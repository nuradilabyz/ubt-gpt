import streamlit as st
from supabase import create_client, Client
from openai import OpenAI, RateLimitError
from test import test_page
from nur import psychology_page, create_new_psychology_chat
from subjects import SUBJECTS
from feedback import feedback_page
import uuid
from datetime import datetime
import time
import os
from dotenv import load_dotenv
from typing import cast
import logging
import re
import base64
import hashlib

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Загружаем .env
load_dotenv()

supabase_url_env = os.getenv("SUPABASE_URL")
supabase_key_env = os.getenv("SUPABASE_KEY")
openai_api_key_env = os.getenv("OPENAI_API_KEY")
anon_email_env = os.getenv("ANON_EMAIL", "anonymous@example.com")
anon_password_env = os.getenv("ANON_PASSWORD", "anonymous123")

# Проверка переменных окружения ДО создания клиентов
if not all([supabase_url_env, supabase_key_env, openai_api_key_env]):
    st.error("Қате: .env файлындағы айнымалылар орнатылмаған.")
    logger.error("Переменные окружения отсутствуют.")
    st.stop()

SUPABASE_URL: str = cast(str, supabase_url_env)
SUPABASE_KEY: str = cast(str, supabase_key_env)
OPENAI_API_KEY: str = cast(str, openai_api_key_env)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

# Анонимді кіру үшін параметрлер (қоршаған орта арқылы бапталуы мүмкін)
ANON_EMAIL: str = cast(str, anon_email_env)
ANON_PASSWORD: str = cast(str, anon_password_env)

# SUBJECTS subjects.py модулінен импортталды

# Упрощенный CSS
CSS = """
<style>
    .stApp {
        color: #ffffff;
        max-width: 1200px;
        margin: 0 auto;
        font-family: Arial, sans-serif;
    }
    [data-testid="stSidebar"] {
        width: 300px;
    }
    .chat-history-item {
        color: #ffffff;
        padding: 10px;
        margin: 6px 0;
        border-radius: 8px;
    }
    .stButton > button {
        color: #ffffff;
        border-radius: 6px;
    }
    .stTextInput > div > input {
        color: #ffffff;
    }
    .header-container h1 {
        color: #ffffff;
        padding: 4px 0;
        border-radius: 0;
    }
    h2, h3, h4 { color: #ffffff; }
    .stMarkdown p { color: #ffffff; }
    .stSelectbox label { color: #ffffff; }
</style>
"""


# Функции управления пользователями
def sign_in(email, password):
    try:
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if response.user:
            user_id = response.user.id
            logger.debug(f"User signed in: {user_id}")
            # Persist Supabase session tokens
            try:
                if hasattr(response, "session") and response.session:
                    st.session_state["sb_access_token"] = getattr(response.session, "access_token", None)
                    st.session_state["sb_refresh_token"] = getattr(response.session, "refresh_token", None)
                else:
                    logger.warning("No session in sign-in response; tokens not stored")
            except Exception as token_err:
                logger.error(f"Failed to store session tokens: {token_err}")
            return user_id
        else:
            logger.error("Sign-in failed: No user returned")
            st.error("Қате: Жарамсыз email немесе құпия сөз.")
            return None
    except Exception as e:
        logger.error(f"Sign-in error: {str(e)}")
        st.error(f"Кіру кезінде қате: {str(e)}")
        return None


def sign_up(email, password):
    try:
        response = supabase.auth.sign_up({"email": email, "password": password})
        if response.user:
            user_id = response.user.id
            logger.debug(f"User registered: {user_id}")
            return user_id
        else:
            logger.error("Sign-up failed: No user returned")
            st.error("Қате: Тіркелу сәтсіз аяқталды.")
            return None
    except Exception as e:
        logger.error(f"Sign-up error: {str(e)}")
        st.error(f"Тіркелу кезінде қате: {str(e)}")
        return None


def sign_out():
    try:
        supabase.auth.sign_out()
        logger.debug("User signed out")
        st.session_state.user_id = None
        st.session_state.main_chat_id = None
        st.session_state.main_chat_title = None
        st.session_state.main_messages = None
        st.session_state.main_thread_id = None
        st.session_state.action_state = {"action": None, "chat_id": None}
        # Clear persisted auth tokens
        st.session_state.pop("sb_access_token", None)
        st.session_state.pop("sb_refresh_token", None)
        # Clear post-signup flags
        st.session_state.pop("just_registered", None)
        st.session_state.pop("just_registered_email", None)
        st.rerun()
    except Exception as e:
        logger.error(f"Sign-out error: {str(e)}")
        st.error(f"Шығу кезінде қате: {str(e)}")


# Поддержка анонимного пользователя
def sign_in_anonymous():
    try:
        response = supabase.auth.sign_in_with_password({
            "email": ANON_EMAIL,
            "password": ANON_PASSWORD
        })
        if response.user:
            user_id = response.user.id
            logger.debug(f"Anonymous user signed in: {user_id}")
            # Persist Supabase session tokens for anonymous session
            try:
                if hasattr(response, "session") and response.session:
                    st.session_state["sb_access_token"] = getattr(response.session, "access_token", None)
                    st.session_state["sb_refresh_token"] = getattr(response.session, "refresh_token", None)
                else:
                    logger.warning("No session in anonymous sign-in response; tokens not stored")
            except Exception as token_err:
                logger.error(f"Failed to store anonymous session tokens: {token_err}")
            return user_id
        else:
            logger.error("Anonymous sign-in failed: No user returned")
            st.error("Қате: Анонимді кіру сәтсіз аяқталды.")
            return None
    except Exception as e:
        logger.error(f"Anonymous sign-in error: {str(e)}")
        # Егер қолданушы жоқ болса, бір рет тіркеп көреміз
        try:
            response = supabase.auth.sign_up({"email": ANON_EMAIL, "password": ANON_PASSWORD})
            if response.user:
                # Кейбір жағдайларда sign_up автоматты кіру жасамайды — сондықтан қайта sign_in
                response2 = supabase.auth.sign_in_with_password({"email": ANON_EMAIL, "password": ANON_PASSWORD})
                if response2.user:
                    user_id = response2.user.id
                    if hasattr(response2, "session") and response2.session:
                        st.session_state["sb_access_token"] = getattr(response2.session, "access_token", None)
                        st.session_state["sb_refresh_token"] = getattr(response2.session, "refresh_token", None)
                    logger.debug(f"Anonymous user auto-registered and signed in: {user_id}")
                    return user_id
        except Exception as e2:
            logger.error(f"Anonymous sign-up fallback failed: {e2}")
        st.error("Анонимді кіру кезінде қате. Әкімшіге хабарласыңыз.")
        return None


# Функции управления чатами
def load_main_chat_titles(user_id):
    try:
        response = supabase.table("main_chats").select("id, title, created_at").eq("user_id", user_id).execute()
        chats = sorted(response.data, key=lambda x: x["created_at"], reverse=True)
        logger.debug(f"Loaded main chats for user {user_id}: {chats}")
        return chats
    except Exception as e:
        logger.error(f"Ошибка загрузки чатов: {str(e)}")
        st.error(f"Чат тарихын жүктеу кезінде қате: {str(e)}")
        return []


def load_main_chat(chat_id):
    try:
        response = supabase.table("main_chats").select("messages, thread_id").eq("id", chat_id).execute()
        if response.data:
            logger.debug(f"Loaded chat {chat_id}: {response.data[0]}")
            return response.data[0]["messages"], response.data[0]["thread_id"]
        return [], None
    except Exception as e:
        logger.error(f"Ошибка загрузки чата {chat_id}: {str(e)}")
        st.error(f"Чатты жүктеу кезінде қате: {str(e)}")
        return [], None


def save_main_chat(chat_id, user_id, messages, title, thread_id):
    try:
        existing = supabase.table("main_chats").select("id").eq("id", chat_id).execute()
        if existing.data:
            supabase.table("main_chats").update({
                "messages": messages,
                "title": title,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", chat_id).execute()
        else:
            supabase.table("main_chats").insert({
                "id": chat_id,
                "user_id": user_id,
                "messages": messages,
                "title": title,
                "thread_id": thread_id,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }).execute()
        logger.debug(f"Saved chat {chat_id} with title {title}")
    except Exception as e:
        logger.error(f"Ошибка сохранения чата {chat_id}: {str(e)}")
        st.error(f"Чатты сақтау кезінде қате: {str(e)}")


def delete_main_chat(chat_id):
    try:
        response = supabase.table("main_chats").delete().eq("id", chat_id).execute()
        logger.debug(f"Deleted chat {chat_id}: {response.data}")
        return response.data is not None
    except Exception as e:
        logger.error(f"Ошибка удаления чата {chat_id}: {str(e)}")
        st.error(f"Чатты жою кезінде қате: {str(e)}")
        return False


def rename_main_chat(chat_id, new_name):
    if not new_name:
        return False, "Жаңа атау бос болмауы керек."
    try:
        response = supabase.table("main_chats").select("id").eq("title", new_name).execute()
        if response.data:
            return False, "Бұл атаумен чат бар."
        supabase.table("main_chats").update({"title": new_name, "updated_at": datetime.utcnow().isoformat()}).eq("id",
                                                                                                                 chat_id).execute()
        logger.debug(f"Renamed chat {chat_id} to {new_name}")
        return True, new_name
    except Exception as e:
        logger.error(f"Ошибка переименования чата {chat_id}: {str(e)}")
        return False, f"Чат атауын өзгерту кезінде қате: {str(e)}"


def create_new_main_chat(user_id):
    try:
        chat_id = str(uuid.uuid4())
        title = "Жаңа чат"
        thread_id = client.beta.threads.create().id
        supabase.table("main_chats").insert({
            "id": chat_id,
            "user_id": user_id,
            "title": title,
            "messages": [],
            "thread_id": thread_id,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }).execute()
        logger.debug(f"Created new chat {chat_id} with thread {thread_id} for user {user_id}")
        return chat_id, title, thread_id
    except Exception as e:
        logger.error(f"Ошибка создания чата: {str(e)}")
        st.error(f"Жаңа чат құру кезінде қате: {str(e)}")
        return None, None, None


def cleanup_empty_main_chats(user_id):
    try:
        resp = supabase.table("main_chats").select("id, messages").eq("user_id", user_id).execute()
        for row in (resp.data or []):
            try:
                msgs = row.get("messages") or []
            except Exception:
                msgs = []
            if not msgs:
                try:
                    supabase.table("main_chats").delete().eq("id", row.get("id")).execute()
                except Exception:
                    continue
    except Exception as e:
        logger.debug(f"Main chat cleanup skipped: {e}")


def generate_chat_title(prompt, subject):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system",
                 "content": "Сұрақ негізінде қазақ тілінде қысқа тақырыпты анықта (максимум 5 сөз). Формат: '[Пән] - [Тақырып]'"},
                {"role": "user", "content": f"Пән: {subject}\nСұрақ: {prompt}"}
            ],
            temperature=0.5
        )
        content = response.choices[0].message.content
        if content is not None:
            title = content.strip()
        else:
            title = f"{subject} - Сұрақ"
            logger.warning("Received None as chat title content from OpenAI.")
        logger.debug(f"Generated title: {title}")
        return title
    except Exception as e:
        logger.error(f"Ошибка генерации заголовка: {str(e)}")
        return f"{subject} - Сұрақ"

EXTENDED_SYSTEM_PROMPT = """
Сен қазақ мектебінің оқушыларына көмек көрсететін ассистентсің. Жауаптарың толық, түсінікті әрі нақты болуы тиіс. Әр сұраққа: 
1. Тақырыпты қысқаша түсіндіріп өт (негізгі ұғымдар мен контекст); 
2. Негізгі жауапты нақты айт; 
3. Егер жауап күрделі болса — қарапайым тілмен қосымша түсіндіріп бер; 
4. Қажет болса — мысал келтір немесе салыстыру арқылы түсіндір; 
5. Оқушы бірден түсінбесе, оны қайтадан түсіндіру үшін қосымша дайын бол; 
6. Ұқсас сұрақтар немесе ҰБТ форматында қандай сұрақ келуі мүмкін екенін ұсын; 
7. Қайта есте сақтау үшін қысқаша қорытынды жаз немесе мнемоника көмектес; 
8. Ақпарат тек берілген мәтіннен алынуы керек, сыртқы мәліметтерді қолданба.
"""



def send_prompt(thread_id, prompt, subject):
    max_retries = 5
    retry_delay = 10
    for attempt in range(max_retries):
        try:
            # Send user message
            client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=prompt + EXTENDED_SYSTEM_PROMPT
            )
            # Create run with additional instructions
            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=SUBJECTS[subject]["assistant_id"],
                tools=[{"type": "file_search"}],
            )
            while run.status in ["queued", "in_progress"]:
                run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
                time.sleep(2)
            if run.status != "completed":
                error_msg = f"Ассистенттің орындау қатесі: {run.status}"
                if run.last_error:
                    error_msg += f" ({run.last_error.code}: {run.last_error.message})"
                logger.error(error_msg)
                st.error(error_msg)
                return None
            messages = client.beta.threads.messages.list(thread_id=thread_id, limit=1)
            content_blocks = messages.data[0].content
            response = ""
            file_ids = set()
            
            for block in content_blocks:
                try:
                    text_part = getattr(block, 'text', None)
                    if text_part is not None:
                        value = getattr(text_part, 'value', None)
                        if isinstance(value, str):
                            response += value
                        # Collect file IDs from text annotations if present
                        annotations = getattr(text_part, 'annotations', None)
                        if annotations:
                            for ann in annotations:
                                file_citation = getattr(ann, 'file_citation', None)
                                if file_citation:
                                    fid = getattr(file_citation, 'file_id', None)
                                    if isinstance(fid, str):
                                        file_ids.add(fid)
                                else:
                                    # file_path annotations can also reference files
                                    file_path = getattr(ann, 'file_path', None)
                                    if file_path:
                                        fid = getattr(file_path, 'file_id', None)
                                        if isinstance(fid, str):
                                            file_ids.add(fid)
                                    else:
                                        # Fallback if annotation exposes file_id directly
                                        fid = getattr(ann, 'file_id', None)
                                        if isinstance(fid, str):
                                            file_ids.add(fid)
                    # Some SDKs expose file citations at block level (rare)
                    file_citation_block = getattr(block, 'file_citation', None)
                    if file_citation_block:
                        fid = getattr(file_citation_block, 'file_id', None)
                        if isinstance(fid, str):
                            file_ids.add(fid)
                except Exception:
                    continue
            
            # Strip inline citation markers like 【4:6†source】 and †source leftovers
            try:
                response = re.sub(r"【[^】]*】", "", response)
                response = re.sub(r"†source", "", response, flags=re.IGNORECASE)
            except Exception:
                pass
            
            if not response:
                logger.warning("No text content found in assistant response.")
                st.error("Ассистент жауап бере алмады немесе жауапта мәтін жоқ.")
                return None
            
            # Resolve file IDs to filenames
            filenames = []
            for fid in sorted(file_ids):
                try:
                    fobj = client.files.retrieve(fid)
                    fname = getattr(fobj, 'filename', None) or fid
                    filenames.append(fname)
                except Exception:
                    filenames.append(fid)
            
            if filenames:
                response += f"\n\n**📚 Дереккөздер:** {', '.join(dict.fromkeys(filenames))}"
            
            logger.debug(f"Received response: {response[:100]}...")
            return response
        except RateLimitError:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                logger.error("OpenAI rate limit exceeded")
                st.error("Қате: OpenAI лимиті асып кетті. 2-3 минут күтіңіз немесе OpenAI есептік жазбаңызды тексеріңіз: https://platform.openai.com/account/usage")
                return None
        except Exception as e:
            logger.error(f"Ошибка отправки запроса: {str(e)}")
            st.error(f"Қате: {str(e)}")
            return None
        time.sleep(5)


def extract_kazakh_text_from_image(image_bytes: bytes, mime_type: str = "image/png") -> str:
    try:
        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('utf-8')}"
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Суреттен қазақша мәтінді дәл шығарып бер. Тек мәтіннің өзін қайтар."},
                        {"type": "image_url", "image_url": {"url": data_url}}
                    ]
                }
            ],
            temperature=0
        )
        content = resp.choices[0].message.content
        return content.strip() if isinstance(content, str) else (content or "").strip()
    except Exception:
        return ""


def email_confirmation_page():
    """Page shown after successful email confirmation"""
    st.markdown("<div class='header-container'><h1>🧠 UBT-GPT</h1></div>", unsafe_allow_html=True)
    st.markdown(CSS, unsafe_allow_html=True)
    
    st.success("✅ Email растау сәтті аяқталды!")
    st.markdown("**Сіздің email мекенжайыңыз сәтті расталды.**")
    st.markdown("Енді сіз жүйеге кіре аласыз.")
    
    if st.button("Кіру бетіне өту", key="go_to_login"):
        # Clear any confirmation-related session state
        st.session_state.pop("email_confirmed", None)
        st.rerun()

def login_page():
    st.markdown("<div class='header-container'><h1>UBT-GPT🏆</h1></div>", unsafe_allow_html=True)

    # After successful registration, show confirmation notice and keep user on login
    if st.session_state.get("just_registered"):
        reg_email_info = st.session_state.get("just_registered_email")
        info_msg = "Тіркелу сәтті аяқталды. Email-ді растаңыз"
        if reg_email_info:
            info_msg += f" ({reg_email_info})"
        info_msg += " және содан кейін кіріңіз."
        st.info(info_msg)

    # Вкладки для входа и регистрации
    tab1, tab2 = st.tabs(["Кіру", "Тіркелу"])  # Анонимді кіру алынып тасталды

    with tab1:
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Құпия сөз", type="password", key="login_password")

        if st.button("Кіру", key="login_button"):
            user_id = sign_in(email, password)
            if user_id:
                st.session_state.user_id = user_id
                # Clear post-signup flags if present
                st.session_state.pop("just_registered", None)
                st.session_state.pop("just_registered_email", None)
                st.success("Сәтті кірдіңіз!")
                st.rerun()

    with tab2:
        reg_email = st.text_input("Email", key="register_email")
        reg_password = st.text_input("Құпия сөз", type="password", key="register_password")
        if st.button("Тіркелу", key="register_button"):
            user_id = sign_up(reg_email, reg_password)
            if user_id:
                # Do not set user_id here; Supabase may require email confirmation
                st.session_state["just_registered"] = True
                st.session_state["just_registered_email"] = reg_email
                st.rerun()

    # Анонимді кіру UI толық алынды



def main_page():
    st.markdown("<h1 style='color: #ffffff;'>UBT-GPT🏆</h1>", unsafe_allow_html=True)
    st.markdown(
        "Мұнда сіз пәндер бойынша сұрақтар қойып, жауап ала аласыз немесе **TEST📝** және **NUR✨** бөлімдерін таңдай аласыз.")

    # Инициализация сессии
    try:
        cleanup_empty_main_chats(st.session_state.user_id)
    except Exception:
        pass
    if "main_chat_id" not in st.session_state:
        chat_id, title, thread_id = create_new_main_chat(st.session_state.user_id)
        if chat_id is None:
            st.error("Жаңа чат құру мүмкін болмады. Supabase-те 'main_chats' таблицасы бар екеніне көз жеткізіңіз.")
            logger.error("Failed to create new main chat")
            return
        st.session_state.main_chat_id = chat_id
        st.session_state.main_chat_title = title
        st.session_state.main_messages = []
        st.session_state.main_thread_id = thread_id
        st.session_state.action_state = {"action": None, "chat_id": None}
        logger.debug(f"Initialized session: chat_id={chat_id}, title={title}")

    # Бүйірлік панель
    with st.sidebar:
        st.markdown(
            "<h2 style='text-align: center; color: #ffffff;'>💬 Чаттар</h2>",
            unsafe_allow_html=True)

        # Жаңа чат
        if st.button("🆕 Жаңа чат", key="new_main_chat"):
            try:
                if st.session_state.get("main_chat_id") and len(st.session_state.get("main_messages") or []) == 0:
                    delete_main_chat(st.session_state.get("main_chat_id"))
            except Exception:
                pass
            chat_id, title, thread_id = create_new_main_chat(st.session_state.user_id)
            if chat_id is None:
                st.error("Жаңа чат құру мүмкін болмады.")
                logger.error("Failed to create new main chat")
                return
            st.session_state.main_chat_id = chat_id
            st.session_state.main_chat_title = title
            st.session_state.main_messages = []
            st.session_state.main_thread_id = thread_id
            st.session_state.action_state = {"action": None, "chat_id": None}
            st.rerun()

        # Чаттар тізімі: таңдау/қайта атау/жою
        chat_files = load_main_chat_titles(st.session_state.user_id)
        for chat in chat_files:
            chat_id = chat["id"]
            chat_title = chat["title"]
            active = chat_id == st.session_state.get("main_chat_id", "")
            css_class = "chat-history-item active" if active else "chat-history-item"

            with st.container():
                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    if st.button(chat_title, key=f"select_main_{chat_id}"):
                        st.session_state.main_chat_id = chat_id
                        st.session_state.main_chat_title = chat_title
                        messages, thread_id = load_main_chat(chat_id)
                        st.session_state.main_messages = messages or []
                        st.session_state.main_thread_id = thread_id
                        st.session_state.action_state = {"action": None, "chat_id": None}
                        st.rerun()
                with col2:
                    if st.button("✏️", key=f"rename_main_{chat_id}"):
                        st.session_state.action_state = {"action": "rename", "chat_id": chat_id}
                        st.rerun()
                with col3:
                    if st.button("🗑️", key=f"delete_main_{chat_id}"):
                        st.session_state.action_state = {"action": "delete", "chat_id": chat_id}
                        st.rerun()

                if st.session_state.action_state["chat_id"] == chat_id:
                    if st.session_state.action_state["action"] == "rename":
                        new_name = st.text_input("Жаңа атау енгізіңіз:", key=f"rename_input_main_{chat_id}")
                        col_save, col_cancel = st.columns(2)
                        with col_save:
                            if st.button("Сақтау", key=f"save_rename_main_{chat_id}"):
                                success, result = rename_main_chat(chat_id, new_name)
                                if success:
                                    if chat_id == st.session_state.get("main_chat_id", ""):
                                        st.session_state.main_chat_title = result
                                    st.session_state.action_state = {"action": None, "chat_id": None}
                                    st.rerun()
                                else:
                                    st.error(result)
                        with col_cancel:
                            if st.button("Болдырмау", key=f"cancel_rename_main_{chat_id}"):
                                st.session_state.action_state = {"action": None, "chat_id": None}
                                st.rerun()
                    elif st.session_state.action_state["action"] == "delete":
                        st.warning(f"'{chat_title}' чатын жоюды растаңыз:")
                        col_confirm, col_cancel = st.columns(2)
                        with col_confirm:
                            if st.button("Иә, жою", key=f"confirm_delete_main_{chat_id}"):
                                if delete_main_chat(chat_id):
                                    if chat_id == st.session_state.get("main_chat_id", ""):
                                        new_id, new_title, new_thread = create_new_main_chat(st.session_state.user_id)
                                        if new_id is None:
                                            st.error("Жаңа чат құру мүмкін болмады.")
                                            return
                                        st.session_state.main_chat_id = new_id
                                        st.session_state.main_chat_title = new_title
                                        st.session_state.main_messages = []
                                        st.session_state.main_thread_id = new_thread
                                    st.session_state.action_state = {"action": None, "chat_id": None}
                                    st.rerun()
                                else:
                                    st.error("Чатты жою кезінде қате шықты.")
                        with col_cancel:
                            if st.button("Жоқ", key=f"cancel_delete_main_{chat_id}"):
                                st.session_state.action_state = {"action": None, "chat_id": None}
                                st.rerun()

    # Негізгі интерфейс стилі
    st.markdown(CSS, unsafe_allow_html=True)

    # Негізгі мазмұн
    subject = st.selectbox("Пәнді таңдаңыз", list(SUBJECTS.keys()), key="subject_select")

    for msg in (st.session_state.main_messages or []):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Image inputs
    # Camera controls placed above inputs
    if "show_main_camera" not in st.session_state:
        st.session_state["show_main_camera"] = False
    cam_cols = st.columns([1, 1, 2])
    with cam_cols[0]:
        if not st.session_state.get("show_main_camera"):
            if st.button("Камераны ашу", key="main_open_camera"):
                st.session_state["show_main_camera"] = True
                st.rerun()
        else:
            if st.button("Камераны жабу", key="main_close_camera"):
                st.session_state["show_main_camera"] = False
                st.rerun()

    # Make camera bigger when open by widening its column
    if st.session_state.get("show_main_camera"):
        col_img1, col_img2 = st.columns([1, 3])
    else:
        col_img1, col_img2 = st.columns([1, 1])
    with col_img1:
        uploaded_img = st.file_uploader("Сурет жүктеу (JPEG/PNG)", type=["png", "jpg", "jpeg"], key="main_image_uploader")
    captured_img = None
    with col_img2:
        if st.session_state.get("show_main_camera"):
            # Inline close (X) button for camera
            x_cols = st.columns([0.85, 0.15])
            with x_cols[1]:
                if st.button("✖️", key="main_camera_close_x"):
                    st.session_state["show_main_camera"] = False
                    st.rerun()
            captured_img = st.camera_input("Камерадан түсіру", key="main_camera")

    # Auto-extract after upload or capture
    img_obj = uploaded_img or captured_img
    if img_obj is not None:
        try:
            image_bytes = img_obj.getvalue() if hasattr(img_obj, "getvalue") else img_obj.read()
        except Exception:
            image_bytes = None
        mime_type = getattr(img_obj, "type", None) or "image/png"
        if image_bytes:
            try:
                current_hash = hashlib.md5(image_bytes).hexdigest()
            except Exception:
                current_hash = None
            last_hash = st.session_state.get("last_main_img_hash")
            if current_hash and current_hash != last_hash:
                st.session_state["last_main_img_hash"] = current_hash
                extracted_text = extract_kazakh_text_from_image(image_bytes, mime_type)
                if extracted_text:
                    # Put extracted text into the chat input field
                    st.session_state["pending_text"] = extracted_text
                else:
                    st.error("Мәтін табылмады. Басқа ракурс/сапалы сурет жүктеп көріңіз.")

    # Show attached file info if any
    if st.session_state.get("pending_text"):
        st.info(f"📎 File attached: {len(st.session_state['pending_text'])} characters extracted")
    
    # Always use the same chat input field
    user_input = st.chat_input("Сұрағыңызды енгізіңіз...", key="main_input")
    
    if user_input:
        process_user_message(user_input, subject)

def process_user_message(user_input, subject):
    """Helper function to process user messages"""
    # Check if there's attached file content
    attached_content = st.session_state.get("pending_text", "")
    
    # Combine user input with attached content if any
    if attached_content:
        full_message = f"📎 Attached file content:\n{attached_content}\n\n👤 User question: {user_input}"
        # Clear the attached content after using it
        st.session_state.pop("pending_text", None)
    else:
        full_message = user_input
    
    # Display only the user's question in chat (not the full message with file content)
    st.session_state.main_messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
        if attached_content:
            st.markdown(f"📎 *File attached ({len(attached_content)} characters)*")

    with st.spinner("Жауап дайындалуда..."):
        # Send the full message (including file content) to the model
        response = send_prompt(st.session_state.main_thread_id, full_message, subject)
        if response:
            st.session_state.main_messages.append({"role": "assistant", "content": response})
            with st.chat_message("assistant"):
                st.markdown(response)

            # Автоматическое переименование после первого сообщения
            if len(st.session_state.main_messages) == 2:
                new_title = generate_chat_title(user_input, subject)
                success, result = rename_main_chat(st.session_state.main_chat_id, new_title)
                if success:
                    st.session_state.main_chat_title = result
                    st.session_state["main_title_renamed"] = True
                    logger.debug(f"Chat renamed to {result}")

            save_main_chat(
                chat_id=st.session_state.main_chat_id,
                user_id=st.session_state.user_id,
                messages=st.session_state.main_messages,
                title=st.session_state.main_chat_title,
                thread_id=st.session_state.main_thread_id
            )
            # Refresh immediately if title was renamed
            if st.session_state.get("main_title_renamed"):
                st.session_state.pop("main_title_renamed", None)
                st.rerun()


# Навигация
def main():
    st.set_page_config(page_title="UBT-GPT🏆", layout="wide")

    # Check for email confirmation URL parameters
    try:
        # Get URL parameters
        query_params = st.query_params
        if query_params:
            # Check for Supabase email confirmation parameters
            # Supabase might use different parameter names in confirmation URLs
            has_confirmation_params = (
                ("access_token" in query_params and "refresh_token" in query_params) or
                ("token" in query_params) or
                ("type" in query_params and query_params["type"] == "signup") or
                ("type" in query_params and query_params["type"] == "recovery")
            )
            
            if has_confirmation_params:
                # This is likely an email confirmation callback
                try:
                    # Try to get tokens from URL parameters
                    access_token = query_params.get("access_token")
                    refresh_token = query_params.get("refresh_token")
                    
                    if access_token and refresh_token:
                        # Set the session with the tokens from the confirmation link
                        supabase.auth.set_session(access_token=access_token, refresh_token=refresh_token)
                        
                        # Store tokens in session state
                        st.session_state["sb_access_token"] = access_token
                        st.session_state["sb_refresh_token"] = refresh_token
                        
                        # Get user info to set user_id
                        user_response = supabase.auth.get_user()
                        if user_response and user_response.user:
                            st.session_state.user_id = user_response.user.id
                            st.session_state["email_confirmed"] = True
                            logger.info(f"Email confirmed for user: {user_response.user.email}")
                    else:
                        # If no tokens, just show confirmation message
                        st.session_state["email_confirmed"] = True
                        logger.info("Email confirmation detected (no tokens)")
                    
                    # Clear URL parameters to avoid showing them in the URL
                    st.query_params.clear()
                    
                except Exception as e:
                    logger.error(f"Error processing email confirmation: {e}")
                    st.error("Email растау кезінде қате шықты.")
    except Exception as e:
        logger.debug(f"Error checking URL parameters: {e}")

    # Show email confirmation page if user just confirmed their email
    if st.session_state.get("email_confirmed"):
        email_confirmation_page()
        return

    # Попытка восстановить сессию Supabase из сохраненных токенов
    try:
        access_token = st.session_state.get("sb_access_token")
        refresh_token = st.session_state.get("sb_refresh_token")
        if access_token and refresh_token:
            try:
                # set_session может обновить текущую сессию клиента
                supabase.auth.set_session(access_token=access_token, refresh_token=refresh_token)
                logger.debug("Supabase session restored from stored tokens")
            except Exception as set_err:
                logger.error(f"Failed to restore Supabase session: {set_err}")
    except Exception as e:
        logger.error(f"Error accessing stored tokens: {e}")

    # Если user_id отсутствует, но пользователь в Supabase есть — заполним user_id
    try:
        existing_user = supabase.auth.get_user()
        if existing_user and getattr(existing_user, "user", None) and not st.session_state.get("user_id"):
            st.session_state.user_id = existing_user.user.id
            logger.debug("Session user_id populated from Supabase user")
    except Exception as e:
        logger.debug(f"get_user pre-check failed (non-fatal): {e}")

    # Проверка авторизации
    if "user_id" not in st.session_state or not st.session_state.user_id:
        login_page()
        return

    # Проверка текущего пользователя
    try:
        user = supabase.auth.get_user()
        if not user or not user.user:
            st.error("Қолданушы авторизацияланбаған.")
            logger.error("No authenticated user found")
            login_page()
            return
    except Exception as e:
        logger.error(f"Error checking user authentication: {str(e)}")
        st.error("Қолданушы авторизацияланбаған.")
        login_page()
        return

    st.sidebar.markdown(f"Қош келдіңіз, {user.user.email}!")
    if st.sidebar.button("Шығу", key="sidebar_logout"):
        sign_out()

    page = st.sidebar.selectbox("Бетті таңдаңыз", ["UBT-GPT🏆", "TEST📝", "NUR✨", "Кері байланыс"], key="page_select")
    logger.debug(f"Selected page: {page}")

    if page == "UBT-GPT🏆":
        main_page()
    elif page == "TEST📝":
        test_page()
    elif page == "NUR✨":
        psychology_page()
    elif page == "Кері байланыс":
        feedback_page()


if __name__ == "__main__":
    main()
