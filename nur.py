import streamlit as st
from supabase import create_client, Client
from typing import cast
from datetime import datetime
from openai import OpenAI, RateLimitError
import uuid
import time
import os
from dotenv import load_dotenv
import logging

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Загружаем .env
load_dotenv()

supabase_url_env = os.getenv("SUPABASE_URL")
supabase_key_env = os.getenv("SUPABASE_KEY")
openai_api_key_env = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=openai_api_key_env)

# Optional: dedicated Assistant ID for psychology chat
PSYCHOLOGY_ASSISTANT_ID = os.getenv("PSYCHOLOGY_ASSISTANT_ID", "").strip()

# Проверка переменных окружения
if not all([supabase_url_env, supabase_key_env, openai_api_key_env]):
    st.error("Қате: .env файлында айнымалылар анықталмаған.")
    logger.error("Айнымалылар окружения отсутствуют.")
    st.stop()

SUPABASE_URL: str = cast(str, supabase_url_env)
SUPABASE_KEY: str = cast(str, supabase_key_env)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PSYCHOLOGY_PROMPT = """
Сен ЕНТ-ға дайындалатын оқушыларға қолдау көрсететін достық психолог-консультантсың.
Жауаптарың түсінікті, жанашыр және практикалық кеңестер беретін болуы керек.
Оқушының жағдайын мұқият талдап, оған қолдау көрсет және нақты шешімдер ұсын.
Жауаптарың қазақ тілінде болуы керек.
Егер алдыңғы хабарламалар болса, оларды ескеріп, әңгімені жалғастыр.
Алдыңғы хабарламалар: {previous_messages}
"""

# Упрощенный CSS
CSS = """
<style>
    .stApp { color: #ffffff; max-width: 1200px; margin: 0 auto; font-family: Arial, sans-serif; }
    [data-testid="stSidebar"] { width: 300px; }
    .chat-history-item { color: #ffffff; padding: 10px; margin: 6px 0; border-radius: 8px; }
    .stButton > button { color: #ffffff; border-radius: 6px; }
    .stTextInput > div > input { color: #ffffff; }
    .header-container h1 { color: #ffffff; padding: 4px 0; border-radius: 0; }
</style>
"""

def load_psychology_chat_titles(user_id):
    try:
        response = supabase.table("psychology_chats").select("id, title, created_at").eq("user_id", user_id).execute()
        chats = sorted(response.data, key=lambda x: x["created_at"], reverse=True)
        logger.debug(f"Loaded psychology chats for user {user_id}: {chats}")
        return chats
    except Exception as e:
        logger.error(f"Ошибка загрузки чатов: {str(e)}")
        st.error(f"Чат тарихын жүктеу кезінде қате: {str(e)}")
        return []

def load_psychology_chat(chat_id):
    try:
        response = supabase.table("psychology_chats").select("messages").eq("id", chat_id).execute()
        if response.data:
            logger.debug(f"Loaded psychology chat {chat_id}: {response.data[0]}")
            return response.data[0]["messages"]
        return []
    except Exception as e:
        logger.error(f"Ошибка загрузки чата {chat_id}: {str(e)}")
        st.error(f"Чатты жүктеу кезінде қате: {str(e)}")
        return []

def save_psychology_chat(chat_id, user_id, messages, title):
    try:
        existing = supabase.table("psychology_chats").select("id").eq("id", chat_id).execute()
        if existing.data:
            supabase.table("psychology_chats").update({
                "messages": messages,
                "title": title,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", chat_id).execute()
        else:
            supabase.table("psychology_chats").insert({
                "id": chat_id,
                "user_id": user_id,
                "messages": messages,
                "title": title,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }).execute()
        logger.debug(f"Saved psychology chat {chat_id} with title {title}")
    except Exception as e:
        logger.error(f"Ошибка сохранения чата {chat_id}: {str(e)}")
        st.error(f"Чатты сақтау кезінде қате: {str(e)}")

def delete_psychology_chat(chat_id):
    try:
        response = supabase.table("psychology_chats").delete().eq("id", chat_id).execute()
        logger.debug(f"Deleted psychology chat {chat_id}: {response.data}")
        return response.data is not None
    except Exception as e:
        logger.error(f"Ошибка удаления чата {chat_id}: {str(e)}")
        st.error(f"Чатты жою кезінде қате: {str(e)}")
        return False

def cleanup_empty_psychology_chats(user_id):
    try:
        resp = supabase.table("psychology_chats").select("id, messages").eq("user_id", user_id).execute()
        for row in (resp.data or []):
            msgs = row.get("messages") or []
            if not msgs:
                try:
                    supabase.table("psychology_chats").delete().eq("id", row.get("id")).execute()
                except Exception:
                    continue
    except Exception as e:
        logger.debug(f"Psychology chat cleanup skipped: {e}")

def rename_psychology_chat(chat_id, new_name):
    if not new_name:
        return False, "Жаңа атау бос болмауы керек."
    try:
        response = supabase.table("psychology_chats").select("id").eq("title", new_name).execute()
        if response.data:
            return False, "Бұл атаумен чат бар."
        supabase.table("psychology_chats").update({"title": new_name, "updated_at": datetime.utcnow().isoformat()}).eq("id", chat_id).execute()
        logger.debug(f"Renamed psychology chat {chat_id} to {new_name}")
        return True, new_name
    except Exception as e:
        logger.error(f"Ошибка переименования чата {chat_id}: {str(e)}")
        return False, f"Чат атауын өзгерту кезінде қате: {str(e)}"

def create_new_psychology_chat(user_id):
    try:
        chat_id = str(uuid.uuid4())
        title = "Жаңа чат"
        supabase.table("psychology_chats").insert({
            "id": chat_id,
            "user_id": user_id,
            "title": title,
            "messages": [],
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }).execute()
        logger.debug(f"Created new psychology chat {chat_id} for user {user_id}")
        return chat_id, title
    except Exception as e:
        logger.error(f"Ошибка создания чата: {str(e)}")
        st.error(f"Жаңа чат құру кезінде қате: {str(e)}")
        return None, None

def generate_chat_title(prompt):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Сұрақ негізінде қазақ тілінде қысқа тақырыпты анықта (максимум 5 сөз). Формат: 'Психология - [Тақырып]'"},
                {"role": "user", "content": f"Сұрақ: {prompt}"}
            ],
            temperature=0.5
        )
        content = response.choices[0].message.content
        if content is None:
            logger.warning("OpenAI тақырып контенті бос (None)")
            return "Психология - Сұрақ"
        title = content.strip()
        logger.debug(f"Generated psychology chat title: {title}")
        return title
    except Exception as e:
        logger.error(f"Ошибка генерации заголовка: {str(e)}")
        return "Психология - Сұрақ"



def psychology_page():
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
    try:
        cleanup_empty_psychology_chats(st.session_state.user_id)
    except Exception:
        pass
    if "psychology_chat_id" not in st.session_state:
        chat_id, title = create_new_psychology_chat(st.session_state.user_id)
        if chat_id is None:
            st.error("Жаңа чат құру мүмкін болмады. Supabase-те 'psychology_chats' таблицасы бар екеніне көз жеткізіңіз.")
            logger.error("Failed to create new psychology chat")
            return
        st.session_state.psychology_chat_id = chat_id
        st.session_state.psychology_chat_title = title
        st.session_state.psychology_messages = []
        logger.debug(f"Initialized psychology chat: {chat_id}")

    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
    "<div class='header-container'>"
    "<h1><b>NUR✨</b></h1>"
    "<p style='color:#ffffff;'><b>Бұл бет ҰБТ-ға дайындалып жүрген оқушыларға қолдау көрсетіп, жанашыр кеңес береді.</b><br>"
    "🧘‍♀️ <i>Стресс</i>, 🎯 <i>шоғырлану</i> немесе 💡 <i>мотивация</i> туралы кез келген сұрақтарыңызды қоя аласыз.</p>"
    "</div>",
    unsafe_allow_html=True
)

    with st.sidebar:
        st.markdown("<h2 style='text-align: center; color: #ffffff;'>💬 Чаттар</h2>", unsafe_allow_html=True)

        if st.button("🆕 Жаңа чат", key="new_psychology_chat"):
            try:
                if st.session_state.get("psychology_chat_id") and len(st.session_state.get("psychology_messages") or []) == 0:
                    delete_psychology_chat(st.session_state.get("psychology_chat_id"))
            except Exception:
                pass
            chat_id, title = create_new_psychology_chat(st.session_state.user_id)
            if chat_id is None:
                st.error("Жаңа чат құру мүмкін болмады.")
                logger.error("Failed to create new psychology chat")
                return
            st.session_state.psychology_chat_id = chat_id
            st.session_state.psychology_chat_title = title
            st.session_state.psychology_messages = []
            st.session_state.action_state = {"action": None, "chat_id": None}
            st.rerun()

        chat_files = load_psychology_chat_titles(st.session_state.user_id)
        for chat in chat_files:
            chat_id = chat["id"]
            chat_title = chat["title"]
            active = chat_id == st.session_state.get("psychology_chat_id", "")
            css_class = "chat-history-item active" if active else "chat-history-item"

            with st.container():
                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    if st.button(chat_title, key=f"select_psychology_{chat_id}"):
                        st.session_state.psychology_chat_id = chat_id
                        st.session_state.psychology_chat_title = chat_title
                        st.session_state.psychology_messages = load_psychology_chat(chat_id)
                        st.session_state.action_state = {"action": None, "chat_id": None}
                        st.rerun()
                with col2:
                    if st.button("✏️", key=f"rename_psychology_{chat_id}"):
                        st.session_state.action_state = {"action": "rename", "chat_id": chat_id}
                        st.rerun()
                with col3:
                    if st.button("🗑️", key=f"delete_psychology_{chat_id}"):
                        st.session_state.action_state = {"action": "delete", "chat_id": chat_id}
                        st.rerun()

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
                                        chat_id, title = create_new_psychology_chat(st.session_state.user_id)
                                        if chat_id is None:
                                            st.error("Жаңа чат құру мүмкін болмады.")
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

    # Негізгі мазмұн
    for msg in (st.session_state.psychology_messages or []):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_input = st.chat_input("✍️ Жағдайды сипаттаңыз немесе сұрақ қойыңыз...", key="psychology_input")
    if user_input:
        st.session_state.psychology_messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.spinner("Кеңес дайындалуда..."):
            max_retries = 5
            retry_delay = 10
            for attempt in range(max_retries):
                try:
                    # Prefer Assistant API if configured, otherwise fall back to Chat Completions
                    psychology_assistant_id = PSYCHOLOGY_ASSISTANT_ID

                    if psychology_assistant_id:
                        # Create a new thread for this conversation
                        thread = client.beta.threads.create()

                        # Add user message to thread
                        client.beta.threads.messages.create(
                            thread_id=thread.id,
                            role="user",
                            content=user_input
                        )

                        # Run the psychology assistant with file search
                        run = client.beta.threads.runs.create(
                            thread_id=thread.id,
                            assistant_id=psychology_assistant_id,
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
                                    elif hasattr(block, 'file_citation') and getattr(block, 'file_citation', None):
                                        # Extract source information from file citations
                                        file_citation = getattr(block, 'file_citation', None)
                                        if file_citation:
                                            file_id = getattr(file_citation, 'file_id', 'Unknown')
                                            sources.add(file_id)
                                except Exception:
                                    continue

                            # Add file sources at the end (if available)
                            if sources:
                                answer_text += f"\n\n**📚 Дереккөздер:** {', '.join(sources)}"

                            st.session_state.psychology_messages.append({"role": "assistant", "content": answer_text})
                            with st.chat_message("assistant"):
                                st.markdown(answer_text)

                            # Clean up thread
                            try:
                                client.beta.threads.delete(thread.id)
                            except Exception:
                                pass
                        else:
                            error_msg = f"Психология ассистенті жауап бере алмады: {run.status}"
                            if hasattr(run, 'last_error') and run.last_error:
                                error_msg += f" ({run.last_error.message})"
                            st.error(error_msg)
                            st.session_state.psychology_messages.append({"role": "assistant", "content": error_msg})
                            with st.chat_message("assistant"):
                                st.markdown(error_msg)
                            try:
                                client.beta.threads.delete(thread.id)
                            except Exception:
                                pass
                    else:
                        # Fallback to standard Chat Completions with a psychology-specific system prompt
                        previous_text = "\n".join(
                            [f"{m['role']}: {m['content']}" for m in (st.session_state.psychology_messages or []) if m.get('role') in ("user", "assistant")][-10:]
                        )
                        system_prompt = PSYCHOLOGY_PROMPT.format(previous_messages=previous_text)

                        completion = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_input},
                            ],
                            temperature=0.7,
                        )
                        answer_text = completion.choices[0].message.content or "Кешіріңіз, қазір жауап бере алмадым. Қайта көріңіз."
                        answer_text = answer_text.strip()

                        st.session_state.psychology_messages.append({"role": "assistant", "content": answer_text})
                        with st.chat_message("assistant"):
                            st.markdown(answer_text)

                    if len(st.session_state.psychology_messages) == 2:
                        new_title = generate_chat_title(user_input)
                        success, result = rename_psychology_chat(st.session_state.psychology_chat_id, new_title)
                        if success:
                            st.session_state.psychology_chat_title = result
                            st.session_state["psychology_title_renamed"] = True
                            logger.debug(f"Psychology chat renamed to {result}")

                    save_psychology_chat(
                        chat_id=st.session_state.psychology_chat_id,
                        user_id=st.session_state.user_id,
                        messages=st.session_state.psychology_messages,
                        title=st.session_state.psychology_chat_title
                    )
                    # If title was just renamed, rerun to refresh sidebar immediately
                    if st.session_state.get("psychology_title_renamed"):
                        st.session_state.pop("psychology_title_renamed", None)
                        st.rerun()
                    break
                except RateLimitError:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        logger.error("OpenAI rate limit exceeded")
                        st.error("Қате: OpenAI лимиті асып кетті. 2-3 минут күтіңіз немесе OpenAI есептік жазбаңызды тексеріңіз: https://platform.openai.com/account/usage")
                        st.session_state.psychology_messages.append({
                            "role": "assistant",
                            "content": "Кешіріңіз, қазір жауап бере алмаймын. Лимитке жеттіңіз."
                        })
                        with st.chat_message("assistant"):
                            st.markdown(st.session_state.psychology_messages[-1]["content"])
                        break
                except Exception as e:
                    # If Assistant flow failed (e.g., invalid assistant id), try a one-time fallback to Chat Completions
                    try:
                        previous_text = "\n".join(
                            [f"{m['role']}: {m['content']}" for m in (st.session_state.psychology_messages or []) if m.get('role') in ("user", "assistant")][-10:]
                        )
                        system_prompt = PSYCHOLOGY_PROMPT.format(previous_messages=previous_text)

                        completion = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_input},
                            ],
                            temperature=0.7,
                        )
                        answer_text = completion.choices[0].message.content or "Кешіріңіз, қазір жауап бере алмадым. Қайта көріңіз."
                        answer_text = answer_text.strip()

                        st.session_state.psychology_messages.append({"role": "assistant", "content": answer_text})
                        with st.chat_message("assistant"):
                            st.markdown(answer_text)

                        save_psychology_chat(
                            chat_id=st.session_state.psychology_chat_id,
                            user_id=st.session_state.user_id,
                            messages=st.session_state.psychology_messages,
                            title=st.session_state.psychology_chat_title
                        )
                        break
                    except Exception as e2:
                        logger.error(f"Ошибка обработки запроса: {str(e)}; fallback failed: {str(e2)}")
                        st.error(f"Қате: {str(e)}")
                        break
                time.sleep(5)
