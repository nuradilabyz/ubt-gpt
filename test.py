import streamlit as st
from supabase import create_client, Client
from typing import cast
import json
import re
from openai import OpenAI, RateLimitError
import time
import os
from dotenv import load_dotenv
import logging
from datetime import datetime
import uuid
from subjects import SUBJECTS

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Загружаем .env
load_dotenv()

supabase_url_env = os.getenv("SUPABASE_URL")
supabase_key_env = os.getenv("SUPABASE_KEY")
openai_api_key_env = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=openai_api_key_env)

# Проверка переменных окружения
if not all([supabase_url_env, supabase_key_env, openai_api_key_env]):
    st.error("Ошибка: Не заданы переменные окружения в .env файле.")
    logger.error("Переменные окружения отсутствуют.")
    st.stop()

SUPABASE_URL: str = cast(str, supabase_url_env)
SUPABASE_KEY: str = cast(str, supabase_key_env)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# SUBJECTS imported from subjects.py

def clean_response(text):
    try:
        text = re.sub(r'```json\s*|\s*```', '', text, flags=re.MULTILINE)
        text = text.strip()
        json_start = text.find('[')
        json_end = text.rfind(']') + 1
        if json_start == -1 or json_end <= json_start:
            logger.error(f"JSON boundaries not found: {text[:500]}...")
            st.error(f"JSON шекараларын табу мүмкін емес: {text[:500]}...")
            return None
        json_text = text[json_start:json_end]
        json.loads(json_text)
        logger.debug(f"Cleaned JSON response: {json_text[:100]}...")
        return json_text
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {str(e)}")
        st.error(f"JSON пішімі қате: {str(e)}")
        return None


def generate_batch(subject, batch_size=5):
    content = f"""
{subject} пәні бойынша {batch_size} сұрақты көп таңдаулы түрде қазақ тілінде генерациялаңыз, ЕНТ оқулықтарына сәйкес.

Талаптар:
1. Әр сұрақ мыналарды қамтиды:
   - Сұрақ мәтіні (ЕНТ оқулықтарынан, толтырғышсыз).
   - 4 жауап нұсқасы: 1 дұрыс, 3 шынайы, бірақ қате.
   - Дереккөз: оқулық атауы және бет нөмірі (мысалы, "25 бет").
   - Контекст: оқулықтан алынған қысқа үзінді (100 сөзге дейін).
   - Түсініктеме: дұрыс жауаптың неге дұрыс екенін және әр қате жауаптың неге қате екенін түсіндіретін мәтін (100–150 сөз, қазақ тілінде, оқулық мазмұнына сәйкес).
2. Жауап пішімі:
   - ТЕК жарамды JSON, [ басталып, ] аяқталады.
   - ```json, түсініктемелер немесе артық мәтінсіз.
   - Әр сұрақ үшін өрістер:
     - text: строка (сұрақ мәтіні).
     - options: 4 строка массиві (жауап нұсқалары).
     - correct_option: сан (0–3, дұрыс жауап индексі).
     - book_title: строка (оқулық атауы, мысалы, "Құқық негіздері 10 сынып").
     - page: строка (мысалы, "25 бет").
     - context: строка (оқулықтан контекст).
     - explanation: строка (дұрыс және қате жауаптардың түсініктемесі).
3. Мысал:
   [
     {{
       "text": "Құқық нормалары дегеніміз не?",
       "options": ["a) Заңмен реттелетін ережелер", "b) Моральдық нормалар", "c) Дін ережелері", "d) Әдет-ғұрыптар"],
       "correct_option": 0,
       "book_title": "Құқық негіздері 10 сынып",
       "page": "10 бет",
       "context": "Құқық нормалары – қоғамдық қатынастарды реттейтін, мемлекетпен бекітілген ережелер.",
       "explanation": "Дұрыс жауап – a) Заңмен реттелетін ережелер, өйткені құқық нормалары мемлекетпен бекітіліп, заңды күшке ие болады. b) Моральдық нормалар қоғамдық санамен реттеледі, бірақ заңды күші жоқ. c) Дін ережелері діни сенімдерге негізделген. d) Әдет-ғұрыптар – қоғамдық дәстүрлер, бірақ олар заңмен міндетті емес."
     }}
   ]
4. Тексеру: дәл {batch_size} сұрақ, деректер ЕНТ оқулықтарына сәйкес, бет, контекст және түсініктеме оқулық мазмұнына дәл сәйкес болуы керек.
"""

    max_retries = 5
    retry_delay = 10
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system",
                     "content": "Сен ЕНТ оқулықтарына негізделген сұрақтар генерациялайтын мұғалімсің."},
                    {"role": "user", "content": content}
                ],
                temperature=0.7
            )
            response_content = response.choices[0].message.content
            if response_content is None:
                logger.error("OpenAI жауап бос (None)")
                return []
            cleaned_response = clean_response(response_content)
            if not cleaned_response:
                return []
            questions = json.loads(cleaned_response)
            logger.debug(f"Generated batch: {len(questions)} questions")
            return questions
        except RateLimitError:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                logger.error("OpenAI rate limit exceeded")
                st.error(
                    "Қате: OpenAI лимиті асып кетті. 2-3 минут күтіңіз немесе OpenAI есептік жазбаңызды тексеріңіз: https://platform.openai.com/account/usage")
                return []
        except Exception as e:
            logger.error(f"Ошибка генерации партии: {str(e)}")
            st.error(f"Партияны генерациялау кезінде қате: {str(e)}")
            return []
        time.sleep(5)


def generate_test(subject):
    questions = []
    attempts = 0
    max_attempts = 10

    if f"cached_test_{subject}" not in st.session_state:
        st.session_state[f"cached_test_{subject}"] = []

    while len(questions) < 20 and attempts < max_attempts:
        try:
            batch_questions = generate_batch(subject, batch_size=5)
            if not batch_questions:
                attempts += 1
                time.sleep(3)
                continue
            for q in batch_questions:
                required_fields = ["text", "options", "correct_option", "book_title", "page", "context", "explanation"]
                if not all(key in q for key in required_fields):
                    logger.error(f"Missing required fields: {q}")
                    st.error(f"Міндетті өрістер жоқ: {q}")
                    continue
                if len(q["options"]) != 4:
                    logger.error(f"Invalid options count: {q['options']}")
                    st.error(f"Жауап нұсқаларының саны қате: {q['options']}")
                    continue
                if not isinstance(q["correct_option"], int) or q["correct_option"] not in range(4):
                    logger.error(f"Invalid correct_option: {q['correct_option']}")
                    st.error(f"Қате correct_option: {q['correct_option']}")
                    continue
                if not re.match(r'^\d+[-]?\d*\s*бет$', q.get("page", "")):
                    logger.error(f"Invalid page format: {q.get('page')}")
                    st.error(f"Бет пішімі қате: {q.get('page')}")
                    continue
                if not q.get("context"):
                    logger.error(f"Missing context: {q}")
                    st.error(f"Контекст жоқ: {q}")
                    continue
                if not q.get("explanation"):
                    logger.error(f"Missing explanation: {q}")
                    st.error(f"Түсініктеме жоқ: {q}")
                    continue
                if q not in questions and q not in st.session_state[f"cached_test_{subject}"]:
                    questions.append(q)
                    st.session_state[f"cached_test_{subject}"].append(q)
            attempts += 1
            time.sleep(5)
        except Exception as e:
            logger.error(f"Ошибка генерации партии: {str(e)}")
            st.error(f"Партияны генерациялау кезінде қате: {str(e)}")
            attempts += 1
            time.sleep(5)

    if len(questions) < 20:
        logger.error(f"Generated only {len(questions)} questions instead of 20")
        st.error(f"20 сұрақтың орнына тек {len(questions)} сұрақ құрылды.")
        return questions

    logger.debug(f"Generated test with {len(questions)} questions")
    return questions[:20]


def load_test_chat_titles(user_id):
    try:
        response = supabase.table("test_chats").select("id, title, created_at").eq("user_id", user_id).execute()
        chats = sorted(response.data, key=lambda x: x["created_at"], reverse=True)
        logger.debug(f"Loaded test chats for user {user_id}: {chats}")
        return chats
    except Exception as e:
        logger.error(f"Ошибка загрузки чатов: {str(e)}")
        st.error(f"Чат тарихын жүктеу кезінде қате: {str(e)}")
        return []


def load_test_chat(chat_id):
    try:
        response = supabase.table("test_chats").select("messages").eq("id", chat_id).execute()
        if response.data:
            logger.debug(f"Loaded test chat {chat_id}: {response.data[0]}")
            return response.data[0]["messages"]
        return []
    except Exception as e:
        logger.error(f"Ошибка загрузки чата {chat_id}: {str(e)}")
        st.error(f"Чатты жүктеу кезінде қате: {str(e)}")
        return []


def save_test_chat(chat_id, user_id, messages, title):
    try:
        existing = supabase.table("test_chats").select("id").eq("id", chat_id).execute()
        if existing.data:
            supabase.table("test_chats").update({
                "messages": messages,
                "title": title,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", chat_id).execute()
        else:
            supabase.table("test_chats").insert({
                "id": chat_id,
                "user_id": user_id,
                "messages": messages,
                "title": title,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }).execute()
        logger.debug(f"Saved test chat {chat_id} with title {title}")
    except Exception as e:
        logger.error(f"Ошибка сохранения чата {chat_id}: {str(e)}")
        st.error(f"Чатты сақтау кезінде қате: {str(e)}")


def delete_test_chat(chat_id):
    try:
        response = supabase.table("test_chats").delete().eq("id", chat_id).execute()
        logger.debug(f"Deleted test chat {chat_id}: {response.data}")
        return response.data is not None
    except Exception as e:
        logger.error(f"Ошибка удаления чата {chat_id}: {str(e)}")
        st.error(f"Чатты жою кезінде қате: {str(e)}")
        return False


def load_saved_test(chat_id):
    try:
        response = supabase.table("saved_tests").select("test_json").eq("id", chat_id).execute()
        if response.data:
            return response.data[0].get("test_json")
        return None
    except Exception as e:
        logger.error(f"Ошибка загрузки сохраненного теста: {str(e)}")
        return None


def save_or_update_saved_test(chat_id, user_id, subject, test_json):
    try:
        existing = supabase.table("saved_tests").select("id").eq("id", chat_id).execute()
        now_iso = datetime.utcnow().isoformat()
        payload = {
            "subject": subject,
            "test_json": test_json,
            "updated_at": now_iso
        }
        if existing.data:
            supabase.table("saved_tests").update(payload).eq("id", chat_id).execute()
        else:
            payload.update({
                "id": chat_id,
                "user_id": user_id,
                "created_at": now_iso
            })
            supabase.table("saved_tests").insert(payload).execute()
        logger.debug(f"Saved full test payload for chat {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка сохранения полного теста: {str(e)}")


def rename_test_chat(chat_id, new_name):
    if not new_name:
        return False, "Жаңа атау бос болмауы керек."
    try:
        response = supabase.table("test_chats").select("id").eq("title", new_name).execute()
        if response.data:
            return False, "Бұл атаумен чат бар."
        supabase.table("test_chats").update({"title": new_name, "updated_at": datetime.utcnow().isoformat()}).eq("id",
                                                                                                                 chat_id).execute()
        logger.debug(f"Renamed test chat {chat_id} to {new_name}")
        return True, new_name
    except Exception as e:
        logger.error(f"Ошибка переименования чата {chat_id}: {str(e)}")
        return False, f"Чат атауын өзгерту кезінде қате: {str(e)}"


def create_new_test_chat(user_id):
    try:
        # Use the authenticated user's id to satisfy RLS (auth.uid() = user_id)
        auth_user = None
        try:
            auth_user_resp = supabase.auth.get_user()
            if auth_user_resp and getattr(auth_user_resp, "user", None):
                auth_user = auth_user_resp.user
        except Exception:
            auth_user = None

        insert_user_id = user_id
        if auth_user and getattr(auth_user, "id", None):
            insert_user_id = auth_user.id
            if user_id and user_id != insert_user_id:
                logger.debug(f"Adjusting user_id for insert to match auth.uid(): {insert_user_id}")

        chat_id = str(uuid.uuid4())
        title = "Жаңа тест чаты"
        supabase.table("test_chats").insert({
            "id": chat_id,
            "user_id": insert_user_id,
            "title": title,
            "messages": [],
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }).execute()
        logger.debug(f"Created new test chat {chat_id} for user {insert_user_id}")
        return chat_id, title
    except Exception as e:
        logger.error(f"Ошибка создания чата: {str(e)}")
        st.error(f"Жаңа чат құру кезінде қате: {str(e)}")
        return None, None


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
        if content is None:
            logger.warning("OpenAI тақырып контенті бос (None)")
            return f"{subject} - Сұрақ"
        title = content.strip()
        logger.debug(f"Generated test chat title: {title}")
        return title
    except Exception as e:
        logger.error(f"Ошибка генерации заголовка: {str(e)}")
        return f"{subject} - Сұрақ"


def test_page():
    if "user_id" not in st.session_state or not st.session_state.user_id:
        st.error("Сіз авторизациядан өтуіңіз керек!")
        return

    # Supabase сессиясын қалпына келтіруге тырысамыз (егер негізгі бетте сақталған болса)
    try:
        access_token = st.session_state.get("sb_access_token")
        refresh_token = st.session_state.get("sb_refresh_token")
        if access_token and refresh_token:
            try:
                # Restore session on the existing global client used below
                supabase.auth.set_session(access_token=access_token, refresh_token=refresh_token)
            except Exception:
                pass
    except Exception:
        pass

    if "action_state" not in st.session_state:
        st.session_state.action_state = {"action": None, "chat_id": None}
    if "test_chat_id" not in st.session_state:
        chat_id, title = create_new_test_chat(st.session_state.user_id)
        if chat_id is None:
            st.error("Жаңа чат құру мүмкін болмады. Supabase-те 'test_chats' таблицасы бар екеніне көз жеткізіңіз.")
            logger.error("Failed to create new test chat")
            return
        st.session_state.test_chat_id = chat_id
        st.session_state.test_chat_title = title
        st.session_state.test_messages = []
        st.session_state["test_input_value"] = ""
        # Жаңа чатқа ауысқанда тест күйін толық тазалау
        st.session_state.current_test = None
        st.session_state.user_answers = {}
        st.session_state.test_submitted = False
        st.session_state.test_results = None
        logger.debug(f"Initialized test chat: {chat_id}")

    # Бар чат үшін сақталған тестті жүктеп көреміз (бар болса)
    try:
        if st.session_state.get("test_chat_id"):
            saved_payload = load_saved_test(st.session_state.test_chat_id)
            if saved_payload:
                st.session_state.current_test = saved_payload.get("questions") or []
                st.session_state.user_answers = saved_payload.get("user_answers") or {}
                st.session_state.test_results = saved_payload.get("results")
                st.session_state.test_submitted = bool(saved_payload.get("submitted"))
    except Exception:
        pass

    # CSS
    st.markdown("""
    <style>
        .stApp { background: #0b0b0f; color: #ffffff; max-width: 1200px; margin: 0 auto; font-family: Arial, sans-serif; }
        [data-testid=\"stSidebar\"] { width: 300px; background: #0f0f12; border-right: 1px solid #1c1c20; }
        .chat-history-item { background: #17171b; border: 1px solid #22232a; color: #ffffff; padding: 10px; margin: 6px 0; border-radius: 8px; }
        .chat-history-item:hover { background: #1e1e24; }
        .chat-history-item.active { background: #1b1b20; border-color: #2a2b33; }
        .stButton > button { background: #2c2d34; color: #fff; border-radius: 6px; }
        .stButton > button:hover { background: #3a3b45; }
    </style>
    """, unsafe_allow_html=True)

    # Бүйірлік панель
    with st.sidebar:
        st.markdown(
            "<h2 style='text-align: center; color: #ffffff; background-color: #00cc00; padding: 10px; border-radius: 8px;'>💬 Тест чаттары</h2>",
            unsafe_allow_html=True)

        if st.button("🆕 Жаңа тест чаты", key="new_test_chat"):
            chat_id, title = create_new_test_chat(st.session_state.user_id)
            if chat_id is None:
                st.error("Жаңа чат құру мүмкін болмады.")
                logger.error("Failed to create new test chat")
                return
            st.session_state.test_chat_id = chat_id
            st.session_state.test_chat_title = title
            st.session_state.test_messages = []
            st.session_state.action_state = {"action": None, "chat_id": None}
            st.session_state["test_input_value"] = ""
            # Жаңа чат үшін тест UI күйін тазалау
            st.session_state.current_test = None
            st.session_state.user_answers = {}
            st.session_state.test_submitted = False
            st.session_state.test_results = None
            st.rerun()

        chat_files = load_test_chat_titles(st.session_state.user_id)
        for chat in chat_files:
            chat_id = chat["id"]
            chat_title = chat["title"]
            active = chat_id == st.session_state.get("test_chat_id", "")
            css_class = "chat-history-item active" if active else "chat-history-item"

            with st.container():
                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    if st.button(chat_title, key=f"select_test_{chat_id}"):
                        st.session_state.test_chat_id = chat_id
                        st.session_state.test_chat_title = chat_title
                        st.session_state.test_messages = load_test_chat(chat_id)
                        st.session_state.action_state = {"action": None, "chat_id": None}
                        st.session_state["test_input_value"] = ""
                        # Сақталған тестті жүктеу (бар болса) немесе тазалау
                        saved_payload = load_saved_test(chat_id)
                        if saved_payload:
                            st.session_state.current_test = saved_payload.get("questions") or []
                            st.session_state.user_answers = saved_payload.get("user_answers") or {}
                            st.session_state.test_submitted = bool(saved_payload.get("submitted"))
                            st.session_state.test_results = saved_payload.get("results")
                        else:
                            st.session_state.current_test = None
                            st.session_state.user_answers = {}
                            st.session_state.test_submitted = False
                            st.session_state.test_results = None
                        st.rerun()
                with col2:
                    if st.button("✏️", key=f"rename_test_{chat_id}"):
                        st.session_state.action_state = {"action": "rename", "chat_id": chat_id}
                        st.rerun()
                with col3:
                    if st.button("🗑️", key=f"delete_test_{chat_id}"):
                        st.session_state.action_state = {"action": "delete", "chat_id": chat_id}
                        st.rerun()

                if st.session_state.action_state["chat_id"] == chat_id:
                    if st.session_state.action_state["action"] == "rename":
                        new_name = st.text_input("Жаңа атау енгізіңіз:", key=f"rename_input_test_{chat_id}")
                        col_save, col_cancel = st.columns(2)
                        with col_save:
                            if st.button("Сақтау", key=f"save_rename_test_{chat_id}"):
                                success, result = rename_test_chat(chat_id, new_name)
                                if success:
                                    if chat_id == st.session_state.get("test_chat_id", ""):
                                        st.session_state.test_chat_title = result
                                    st.session_state.action_state = {"action": None, "chat_id": None}
                                    st.rerun()
                                else:
                                    st.error(result)
                        with col_cancel:
                            if st.button("Болдырмау", key=f"cancel_rename_test_{chat_id}"):
                                st.session_state.action_state = {"action": None, "chat_id": None}
                                st.rerun()
                    elif st.session_state.action_state["action"] == "delete":
                        st.warning(f"'{chat_title}' чатын жоюды растаңыз:")
                        col_confirm, col_cancel = st.columns(2)
                        with col_confirm:
                            if st.button("Иә, жою", key=f"confirm_delete_test_{chat_id}"):
                                if delete_test_chat(chat_id):
                                    if chat_id == st.session_state.get("test_chat_id", ""):
                                        chat_id, title = create_new_test_chat(st.session_state.user_id)
                                        if chat_id is None:
                                            st.error("Жаңа чат құру мүмкін болмады.")
                                            return
                                        st.session_state.test_chat_id = chat_id
                                        st.session_state.test_chat_title = title
                                        st.session_state.test_messages = []
                                    st.session_state.action_state = {"action": None, "chat_id": None}
                                    st.rerun()
                                else:
                                    st.error("Чатты жою кезінде қате шықты.")
                        with col_cancel:
                            if st.button("Жоқ", key=f"cancel_delete_test_{chat_id}"):
                                st.session_state.action_state = {"action": None, "chat_id": None}
                                st.rerun()

    # Негізгі мазмұн
    st.title("Мектеп пәндері бойынша тесттер")

    # Show test status
    if st.session_state.get("current_test"):
        if st.session_state.get("test_submitted"):
            st.success("✅ Тест аяқталды - нәтижелерді көруге болады")
        else:
            st.info("📝 Тест жүріп жатыр - сұрақтарға жауап беріңіз")

    subject = st.selectbox("Пәнді таңдаңыз", list(SUBJECTS.keys()), key="test_subject_select")

    # Show test creation button if no test exists, or show "Start New Test" if current test is completed
    if not st.session_state.get("current_test"):
        if st.button("Тест құру", key="create_test"):
            if subject not in SUBJECTS:
                st.error(f"'{subject}' пәні қолдау таппайды.")
                logger.error(f"Unsupported subject: {subject}")
                return
            with st.spinner("Тест құрылуда..."):
                test_questions = generate_test(subject)
            if test_questions and len(test_questions) == 20:
                st.session_state.current_test = test_questions
                st.session_state.user_answers = {}
                st.session_state.test_submitted = False
                st.session_state.test_results = None
                st.session_state.test_messages.append({
                    "role": "system",
                    "content": f"Жаңа тест құрылды: {subject}. Сұрақтар саны: {len(test_questions)}"
                })
                # Пән + күн форматы (дд.мм.гг) бойынша чат атауын орнату
                date_str = datetime.now().strftime('%d.%m.%y')
                new_title = f"{subject} - {date_str}"
                success, result = rename_test_chat(st.session_state.test_chat_id, new_title)
                if success:
                    st.session_state.test_chat_title = result

                # Толық тестті бастапқы күйде сақтаймыз
                initial_payload = {
                    "subject": subject,
                    "questions": test_questions,
                    "user_answers": {},
                    "results": None,
                    "submitted": False,
                    "created_at": datetime.utcnow().isoformat()
                }
                save_or_update_saved_test(
                    chat_id=st.session_state.test_chat_id,
                    user_id=st.session_state.user_id,
                    subject=subject,
                    test_json=initial_payload
                )

                save_test_chat(
                    chat_id=st.session_state.test_chat_id,
                    user_id=st.session_state.user_id,
                    messages=st.session_state.test_messages,
                    title=st.session_state.test_chat_title
                )
                st.success("Тест сәтті құрылды!")
                st.rerun()
            else:
                logger.error(f"Failed to generate full test: {len(test_questions)} questions")
                st.error("Толық тест құру мүмкін болмады.")
                st.session_state.test_messages.append({
                    "role": "system",
                    "content": "Тест құру сәтсіз аяқталды."
                })
                save_test_chat(
                    chat_id=st.session_state.test_chat_id,
                    user_id=st.session_state.user_id,
                    messages=st.session_state.test_messages,
                    title=st.session_state.test_chat_title
                )

    # Тест чаты
    for msg in (st.session_state.test_messages or []):
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        text = content.lstrip()
        # JSON мета-хабарларды және жүйелік хабарларды көрсетпейміз
        if (text.startswith("{") and '"type": "test_created"' in text) or msg.get("role") not in ["user", "assistant"]:
            continue
        with st.chat_message(msg.get("role", "assistant")):
            st.markdown(content)

    user_input = st.chat_input("Тест бойынша сұрағыңызды енгізіңіз...",
                               key=f"test_input_{st.session_state.test_chat_id}")
    if user_input:
        st.session_state.test_messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.spinner("Жауап дайындалуда..."):
            try:
                # Use the subject-specific assistant with file search
                assistant_id = SUBJECTS[subject]["assistant_id"]

                # Create a new thread for this conversation
                thread = client.beta.threads.create()

                # Add user message to thread
                client.beta.threads.messages.create(
                    thread_id=thread.id,
                    role="user",
                    content=user_input
                )

                # Run the assistant with file search
                run = client.beta.threads.runs.create(
                    thread_id=thread.id,
                    assistant_id=assistant_id,
                    tools=[{"type": "file_search"}]
                )

                # Wait for completion
                while run.status in ["queued", "in_progress"]:
                    run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
                    time.sleep(2)

                if run.status == "completed":
                    # Get the response
                    messages = client.beta.threads.messages.list(thread_id=thread.id, limit=1)
                    response_content = messages.data[0].content

                    # Extract text and sources
                    answer_text = ""
                    sources = set()

                    for block in response_content:
                        try:
                            if hasattr(block, 'text') and getattr(block, 'text', None):
                                text_part = getattr(block, 'text', None)
                                if text_part and hasattr(text_part, 'value'):
                                    value = getattr(text_part, 'value', None)
                                    if isinstance(value, str):
                                        answer_text += value
                                # Collect file IDs from text annotations if present
                                annotations = getattr(text_part, 'annotations', None)
                                if annotations:
                                    for ann in annotations:
                                        file_citation = getattr(ann, 'file_citation', None)
                                        if file_citation:
                                            fid = getattr(file_citation, 'file_id', None)
                                            if isinstance(fid, str):
                                                sources.add(fid)
                                        else:
                                            file_path = getattr(ann, 'file_path', None)
                                            if file_path:
                                                fid = getattr(file_path, 'file_id', None)
                                                if isinstance(fid, str):
                                                    sources.add(fid)
                                            else:
                                                fid = getattr(ann, 'file_id', None)
                                                if isinstance(fid, str):
                                                    sources.add(fid)
                            # Fallback: citations at block level
                            file_citation_block = getattr(block, 'file_citation', None)
                            if file_citation_block:
                                fid = getattr(file_citation_block, 'file_id', None)
                                if isinstance(fid, str):
                                    sources.add(fid)
                        except Exception:
                            # Skip blocks that can't be processed
                            continue

                    # Strip inline citation markers like 【4:6†source】 and †source
                    try:
                        answer_text = re.sub(r"【[^】]*】", "", answer_text)
                        answer_text = re.sub(r"†source", "", answer_text, flags=re.IGNORECASE)
                    except Exception:
                        pass

                    # Resolve file IDs to filenames and append
                    if sources:
                        filenames = []
                        for fid in sorted(sources):
                            try:
                                fobj = client.files.retrieve(fid)
                                fname = getattr(fobj, 'filename', None) or fid
                                filenames.append(fname)
                            except Exception:
                                filenames.append(fid)
                        answer_text += f"\n\n**📚 Дереккөздер:** {', '.join(dict.fromkeys(filenames))}"
                    else:
                        answer_text += f"\n\n**📚 Дереккөз:** {subject} оқулығы"

                    st.session_state.test_messages.append({"role": "assistant", "content": answer_text})
                    with st.chat_message("assistant"):
                        st.markdown(answer_text)
                else:
                    error_msg = f"Ассистент жауап бере алмады: {run.status}"
                    if hasattr(run, 'last_error') and run.last_error:
                        error_msg += f" ({run.last_error.message})"
                    st.error(error_msg)
                    st.session_state.test_messages.append({"role": "assistant", "content": error_msg})

                # Clean up thread
                client.beta.threads.delete(thread.id)

            except Exception as e:
                error_msg = f"Жауап алу кезінде қате: {str(e)}"
                st.error(error_msg)
                st.session_state.test_messages.append({"role": "assistant", "content": error_msg})

            if len(st.session_state.test_messages) == 2:
                new_title = generate_chat_title(user_input, subject)
                success, result = rename_test_chat(st.session_state.test_chat_id, new_title)
                if success:
                    st.session_state.test_chat_title = result
                    logger.debug(f"Test chat renamed to {result}")

            save_test_chat(
                chat_id=st.session_state.test_chat_id,
                user_id=st.session_state.user_id,
                messages=st.session_state.test_messages,
                title=st.session_state.test_chat_title
            )

    # Тестті көрсету
    current_test = st.session_state.get("current_test") or []
    if current_test:
        st.subheader(f"{subject} пәні бойынша тест")
        test_results = st.session_state.get("test_results")

        # Only show questions if test is not submitted
        if not st.session_state.get("test_submitted"):
            for i, question in enumerate(current_test):
                st.write(f"**{i + 1}. {question['text']}**")

                # Show radio buttons for answering
                answer = st.radio(
                    f"{i + 1} сұраққа жауап таңдаңыз",
                    question["options"],
                    key=f"q_{i}_{st.session_state.test_chat_id}",
                    index=None
                )
                (st.session_state.setdefault("user_answers", {}))[i] = answer

        # Persist partial progress only when answers actually change (reduces slowness)
        user_answers = st.session_state.get("user_answers") or {}
        # Use a JSON-based representation to avoid mixed-type key sort errors
        try:
            normalized_answers_repr = json.dumps({str(k): user_answers.get(k) for k in user_answers}, sort_keys=True,
                                                 ensure_ascii=False)
        except Exception:
            normalized_answers_repr = str(user_answers)
        if st.session_state.get("last_saved_answers_repr") != normalized_answers_repr:
            st.session_state["last_saved_answers_repr"] = normalized_answers_repr
            partial_payload = {
                "subject": subject,
                "questions": current_test,
                "user_answers": user_answers,
                "results": st.session_state.get("test_results"),
                "submitted": bool(st.session_state.get("test_submitted")),
                "updated_at": datetime.utcnow().isoformat()
            }
            save_or_update_saved_test(
                chat_id=st.session_state.test_chat_id,
                user_id=st.session_state.user_id,
                subject=subject,
                test_json=partial_payload
            )
        # After progress saving, show feedback if submitted
        # If already submitted, render per-question feedback reliably
        if st.session_state.get("test_submitted") and isinstance(test_results, dict):
            st.markdown("---")
            st.markdown("## 🎯 Тест аяқталды!")
            st.markdown("### 📊 Сіздің нәтижелеріңіз:")
            st.markdown(
                f"**Ұпай: {test_results.get('score', 0)} / {test_results.get('total', len(current_test))} ({test_results.get('score', 0) / test_results.get('total', len(current_test)) * 100:.1f}%)**")
            st.markdown("---")

            for idx, _ in enumerate(current_test):
                result = test_results.get("results", [])[idx]
                status = "✅ Дұрыс" if result["is_correct"] else "❌ Қате"
                status_color = "green" if result["is_correct"] else "red"

                st.markdown(f"### {idx + 1}. {result['question']}")
                st.markdown(
                    f"**Сіздің жауабыңыз:** <span style='color: {status_color};'>{result['user_answer']}</span>",
                    unsafe_allow_html=True)
                st.markdown(f"**Дұрыс жауап:** <span style='color: green;'>{result['correct_answer']}</span>",
                            unsafe_allow_html=True)
                st.markdown(f"**Күй:** {status}")
                st.markdown(f"**Контекст:** {result['context']}")
                st.markdown(f"**Түсініктеме:** {result['explanation']}")
                st.markdown("---")

        # Show submit button only if test is not submitted
        if not st.session_state.get("test_submitted"):
            user_answers = st.session_state.get("user_answers") or {}
            answered_all = all(user_answers.get(i) is not None for i in range(len(current_test)))
            if answered_all:
                if st.button("Жауаптарды жіберу", key="submit_test"):
                    correct_count = 0
                    results = []
                    for i, question in enumerate(current_test):
                        user_answer = user_answers.get(i)
                        is_correct = user_answer == question["options"][question["correct_option"]]
                        if is_correct:
                            correct_count += 1
                        results.append({
                            "question": question["text"],
                            "user_answer": user_answer,
                            "correct_answer": question["options"][question["correct_option"]],
                            "is_correct": is_correct,
                            "book_title": question["book_title"],
                            "page": question["page"],
                            "context": question["context"],
                            "explanation": question["explanation"]
                        })
                    st.session_state.test_results = {
                        "score": correct_count,
                        "total": len(current_test),
                        "results": results
                    }
                    st.session_state.test_submitted = True
                    st.session_state.test_messages.append({
                        "role": "system",
                        "content": f"Тест аяқталды. Ұпай: {correct_count}/{len(current_test)}"
                    })
                    # Save the final full test payload (questions, user answers, results)
                    final_payload = {
                        "subject": subject,
                        "questions": current_test,
                        "user_answers": user_answers,
                        "results": st.session_state.test_results,
                        "submitted": True,
                        "submitted_at": datetime.utcnow().isoformat()
                    }
                    save_or_update_saved_test(
                        chat_id=st.session_state.test_chat_id,
                        user_id=st.session_state.user_id,
                        subject=subject,
                        test_json=final_payload
                    )
                    save_test_chat(
                        chat_id=st.session_state.test_chat_id,
                        user_id=st.session_state.user_id,
                        messages=st.session_state.test_messages,
                        title=st.session_state.test_chat_title
                    )
                    st.rerun()  # Force rerun to show results immediately
            else:
                st.warning("Барлық сұрақтарға жауап беріңіз!")
    else:
        # Test is completed - show completion message and option to start new test
        st.info("🎯 Бұл тест аяқталды. Сіз тек нәтижелерді көре аласыз.")

        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("🆕 Жаңа тест бастау", key="start_new_test"):
                # Clear current test state
                st.session_state.current_test = None
                st.session_state.user_answers = {}
                st.session_state.test_submitted = False
                st.session_state.test_results = None
                st.rerun()
        with col2:
            st.info("Жаңа тест бастау үшін жоғарыдағы 'Жаңа тест чаты' түймесін басыңыз.")
