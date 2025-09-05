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
from base64 import b64encode
import hashlib

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

def get_current_user_id():
    try:
        auth_user_resp = supabase.auth.get_user()
        if auth_user_resp and getattr(auth_user_resp, "user", None):
            user_id = auth_user_resp.user.id
            logger.debug(f"Got user_id from Supabase auth: {user_id}")
            return user_id
    except Exception as e:
        logger.debug(f"Error getting user from Supabase auth: {e}")
    session_user_id = st.session_state.get("user_id")
    logger.debug(f"Got user_id from session state: {session_user_id}")
    return session_user_id

def canonical_subject(subject: str) -> str:
    try:
        canonical = " ".join((subject or "").strip().split())
        logger.debug(f"Canonical subject: '{subject}' -> '{canonical}'")
        return canonical
    except Exception as e:
        logger.error(f"Error canonicalizing subject '{subject}': {e}")
        return subject or ""

def normalize_text(value: str) -> str:
    try:
        return " ".join((value or "").strip().split())
    except Exception:
        return value or ""

def normalize_question_text(text: str) -> str:
    try:
        t = (text or "").lower()
        t = re.sub(r"<[^>]+>", " ", t)          # remove HTML tags
        t = re.sub(r"\*|_|`|#{1,6}", " ", t)    # strip markdown markers
        t = re.sub(r"[^\w\sқғүұәіңһөҚҒҮҰӘІҢҺӨ]", " ", t)  # keep letters/digits/underscore/space (kk friendly)
        t = re.sub(r"\s+", " ", t).strip()
        logger.debug(f"Normalized text: '{text}' -> '{t}'")
        return t
    except Exception as e:
        logger.error(f"Error normalizing text '{text}': {e}")
        return normalize_text(text or "").lower()

def create_unique_question_key(question: dict) -> str:
    """
    Создает абсолютно уникальный ключ для вопроса на основе:
    1. Текст вопроса (нормализованный)
    2. Варианты ответов (нормализованные)
    3. Правильный ответ
    4. Название книги
    5. Страница
    """
    import hashlib
    import json
    
    try:
        # Извлекаем все важные данные
        text = question.get("text", "")
        options = question.get("options", [])
        correct_option = question.get("correct_option", 0)
        book_title = question.get("book_title", "")
        page = question.get("page", "")
        
        # Нормализуем текст вопроса
        norm_text = normalize_question_text(text)
        
        # Нормализуем варианты ответов
        norm_options = []
        for opt in options:
            norm_opt = normalize_question_text(opt)
            norm_options.append(norm_opt)
        
        # Нормализуем название книги и страницу
        norm_book = normalize_question_text(book_title)
        norm_page = normalize_question_text(page)
        
        # Создаем уникальную строку из всех компонентов
        unique_string = f"{norm_text}|{json.dumps(norm_options, ensure_ascii=False, sort_keys=True)}|{correct_option}|{norm_book}|{norm_page}"
        
        # Генерируем SHA256 хеш
        key = hashlib.sha256(unique_string.encode("utf-8")).hexdigest()
        
        logger.debug(f"Generated UNIQUE question key:")
        logger.debug(f"  Text: '{text[:50]}...'")
        logger.debug(f"  Options: {len(options)} items")
        logger.debug(f"  Correct: {correct_option}")
        logger.debug(f"  Book: '{book_title}'")
        logger.debug(f"  Page: '{page}'")
        logger.debug(f"  Key: {key}")
        
        return key
        
    except Exception as e:
        logger.error(f"Error creating unique question key: {e}")
        # Fallback to simple text-based key
        return question_key_from_text(question.get("text", ""))

def question_key_from_text(text: str) -> str:
    """
    Простой ключ только по тексту (для обратной совместимости)
    """
    import hashlib
    norm = normalize_question_text(text)
    key = hashlib.sha256(norm.encode("utf-8")).hexdigest()
    logger.debug(f"Generated simple question key: '{text}' -> '{key}'")
    return key

def compute_question_hash(question: dict) -> str:
    # Use the new unique key generation
    return create_unique_question_key(question)

def get_solved_keys(subject: str) -> set:
    user_id = get_current_user_id()
    keys: set[str] = set()
    if not user_id:
        logger.warning(f"No user_id found for getting solved keys for subject: {subject}")
        return keys
    try:
        subj = canonical_subject(subject)
        logger.info(f"=== FETCHING SOLVED KEYS for user_id={user_id}, subject='{subj}' ===")
        resp = supabase.table("user_correct_answers").select("question_key").eq("user_id", user_id).eq("subject", subj).execute()
        db_keys = set()
        for row in (resp.data or []):
            qk = row.get("question_key")
            if isinstance(qk, str):
                db_keys.add(qk)
        logger.info(f"Found {len(db_keys)} solved keys in database for subject '{subj}': {list(db_keys)[:5]}...")
        keys.update(db_keys)
    except Exception as e:
        logger.error(f"Error fetching solved keys from database: {e}")
    # merge with local session cache to be robust if network write/read lags
    try:
        cache_key = f"excluded_keys_cache_{subject}"
        cached = st.session_state.get(cache_key) or set()
        if isinstance(cached, set):
            logger.info(f"Adding {len(cached)} keys from session cache for subject '{subject}'")
            keys |= cached
    except Exception as e:
        logger.error(f"Error accessing session cache: {e}")
    logger.info(f"TOTAL solved keys for subject '{subject}': {len(keys)}")
    return keys

from datetime import datetime as _dt

def save_results(subject: str, questions: list, results: dict):
    user_id = get_current_user_id()
    if not user_id:
        logger.warning("No user_id found for saving results")
        return
    try:
        now_iso = _dt.utcnow().isoformat()
        subj = canonical_subject(subject)
        logger.info(f"=== SAVING RESULTS for user_id={user_id}, subject='{subj}' ===")
        attempts = []
        correct_rows = []
        newly_excluded = set()
        for idx, q in enumerate(questions or []):
            q_text = q.get("text", "")
            qkey = create_unique_question_key(q)  # Use unique key generation
            try:
                res = (results or {}).get("results", [])[idx]
            except Exception:
                res = None
            is_correct = bool(res and res.get("is_correct"))
            logger.info(f"Question {idx}: '{q_text[:50]}...' -> UNIQUE key: {qkey}, correct: {is_correct}")
            attempts.append({
                "user_id": user_id,
                "subject": subj,
                "question_key": qkey,
                "is_correct": is_correct,
            })
            if is_correct:
                correct_rows.append({
                    "user_id": user_id,
                    "subject": subj,
                    "question_key": qkey,
                    "first_answered_at": now_iso,
                    "last_answered_at": now_iso,
                    "times_correct": 1,
                })
                newly_excluded.add(qkey)
        if attempts:
            try:
                supabase.table("user_attempts").insert(attempts).execute()
                logger.debug(f"Saved {len(attempts)} attempts to user_attempts")
            except Exception as e:
                logger.error(f"Error saving attempts: {e}")
        if correct_rows:
            try:
                # Prefer upsert if available
                supabase.table("user_correct_answers").upsert(correct_rows, on_conflict="user_id,subject,question_key").execute()
                logger.info(f"Upserted {len(correct_rows)} correct answers to user_correct_answers")
            except Exception as e:
                logger.error(f"Upsert failed, trying insert/update: {e}")
                # Fallback: insert then update on conflict
                for row in correct_rows:
                    try:
                        supabase.table("user_correct_answers").insert(row).execute()
                        logger.debug(f"Inserted correct answer: {row['question_key']}")
                    except Exception as insert_err:
                        logger.debug(f"Insert failed, trying update: {insert_err}")
                        try:
                            supabase.table("user_correct_answers").update({
                                "last_answered_at": now_iso,
                            }).match({
                                "user_id": row["user_id"],
                                "subject": row["subject"],
                                "question_key": row["question_key"],
                            }).execute()
                            logger.debug(f"Updated existing correct answer: {row['question_key']}")
                        except Exception as update_err:
                            logger.error(f"Update also failed: {update_err}")
        # update local cache of excluded keys
        if newly_excluded:
            cache_key = f"excluded_keys_cache_{subj}"
            cached = st.session_state.get(cache_key) or set()
            if not isinstance(cached, set):
                cached = set()
            cached |= newly_excluded
            st.session_state[cache_key] = cached
            logger.info(f"Updated session cache with {len(newly_excluded)} new excluded keys")
    except Exception as e:
        logger.error(f"Error in save_results: {e}")

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

def generate_batch(subject, batch_size=10, exclusion_texts=None):
    content = f"""
{subject} пәні бойынша {batch_size} сұрақты көп таңдаулы түрде қазақ тілінде генерациялаңыз, ЕНТ оқулықтарына сәйкес.

Талаптар:
1. Әр сұрақ мыналарды қамтиды:
   - Сұрақ мәтіні (ЕНТ оқулықтарынан).
   - 4 жауап нұсқасы: 1 дұрыс, 3 қате.
   - Дереккөз: оқулық атауы және бет нөмірі.
   - Контекст: қысқа үзінді (50 сөзге дейін).
   - Түсініктеме: дұрыс жауаптың неге дұрыс екенін түсіндіретін мәтін (50–80 сөз).
2. Жауап пішімі:
   - ТЕК жарамды JSON, [ басталып, ] аяқталады.
   - Әр сұрақ үшін өрістер:
     - text: строка (сұрақ мәтіні).
     - options: 4 строка массиві (жауап нұсқалары).
     - correct_option: сан (0–3, дұрыс жауап индексі).
     - book_title: строка (оқулық атауы).
     - page: строка (мысалы, "25 бет").
     - context: строка (оқулықтан контекст).
     - explanation: строка (түсініктеме).
3. Мысал:
   [
     {{
       "text": "Құқық нормалары дегеніміз не?",
       "options": ["a) Заңмен реттелетін ережелер", "b) Моральдық нормалар", "c) Дін ережелері", "d) Әдет-ғұрыптар"],
       "correct_option": 0,
       "book_title": "Құқық негіздері 10 сынып",
       "page": "10 бет",
       "context": "Құқық нормалары – қоғамдық қатынастарды реттейтін ережелер.",
       "explanation": "Дұрыс жауап – a) Заңмен реттелетін ережелер, өйткені құқық нормалары мемлекетпен бекітіліп, заңды күшке ие болады."
     }}
   ]
4. Тексеру: дәл {batch_size} сұрақ, деректер ЕНТ оқулықтарына сәйкес.
"""

    # Append explicit exclusion list to the prompt to prevent repeats
    try:
        if exclusion_texts:
            items = []
            seen_items = set()
            total_chars = 0
            # Limit the total size of exclusion section
            for t in exclusion_texts:
                if not isinstance(t, str):
                    continue
                tx = " ".join((t or "").strip().split())
                if not tx:
                    continue
                if tx in seen_items:
                    continue
                if total_chars + len(tx) > 6000:  # Reduced from 12000 to 6000
                    break
                items.append(tx)
                seen_items.add(tx)
                total_chars += len(tx)
            if items:
                exclusion_section = (
                    "ЕСКЕРТУ: Төмендегі сұрақтарға оқушы БҰРЫН ДҰРЫС жауап берген. "
                    "Осы сұрақтарды ҚОСПАҢЫЗ. ТЕК ЖАҢА СҰРАҚТАР ҚҰРЫҢЫЗ.\n"
                    "ALREADY_ANSWERED_QUESTIONS:\n"
                )
                exclusion_section += "\n".join(items[:50]) + "\n\n"  # Limit to first 50 items
                exclusion_section += f"STRICT: Do NOT include these questions. Generate exactly {batch_size} NEW questions.\n\n"
                content = exclusion_section + content
                logger.debug(f"Included {len(items)} exclusion items into the prompt")
    except Exception as e:
        logger.debug(f"Failed to add exclusion list to prompt: {e}")

    # Log final prompt content to terminal when creating the test
    try:
        logger.info("=== FINAL PROMPT CONTENT (generate_batch) ===\n" + content)
    except Exception:
        pass



    max_retries = 3
    retry_delay = 5
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gpt-5",
                messages=[
                    {"role": "system", "content": "Сен ЕНТ оқулықтарына негізделген сұрақтар генерациялайтын мұғалімсің."},
                    {"role": "user", "content": content}
                ],
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
                st.error("Қате: OpenAI лимиті асып кетті. 2-3 минут күтіңіз немесе OpenAI есептік жазбаңызды тексеріңіз: https://platform.openai.com/account/usage")
                return []
        except Exception as e:
            logger.error(f"Ошибка генерации партии: {str(e)}")
            st.error(f"Партияны генерациялау кезінде қате: {str(e)}")
            return []
        time.sleep(1)

def generate_test(subject):
    questions = []
    attempts = 0
    max_attempts = 5

    if f"cached_test_{subject}" not in st.session_state:
        st.session_state[f"cached_test_{subject}"] = []

    subj = canonical_subject(subject)
    logger.debug(f"=== GENERATING TEST FOR SUBJECT: '{subj}' ===")
    
    # Fetch solved canonical keys for this user+subject and track per-batch seen
    solved_text_keys = get_solved_keys(subj)
    # Fetch exclusion texts from saved tests for this user/subject to guide the model
    exclusion_texts = []
    try:
        exclusion_texts = fetch_exclusion_texts(subj, solved_text_keys, max_items=500)  # Reduced from 2000 to 500
        logger.debug(f"Fetched {len(exclusion_texts)} exclusion texts from saved_tests")
    except Exception as e:
        logger.debug(f"Exclusion texts fetch failed: {e}")
    seen_text_keys = set()
    excluded_session = st.session_state.get(f"excluded_keys_cache_{subj}") or set()
    if isinstance(excluded_session, set):
        solved_text_keys |= excluded_session
    logger.debug(f"Total solved keys to exclude: {len(solved_text_keys)}")
    logger.debug(f"Sample solved keys: {list(solved_text_keys)[:3]}")

    # Create progress bar
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    while len(questions) < 20 and attempts < max_attempts:
        try:
            # Update progress
            progress = min(len(questions) / 20, 1.0)
            progress_bar.progress(progress)
            status_text.text(f"Сұрақтар генерациялануда... {len(questions)}/20")
            
            batch_questions = generate_batch(subject, batch_size=10, exclusion_texts=exclusion_texts)
            if not batch_questions:
                attempts += 1
                continue
            before_cnt = len(batch_questions)
            logger.debug(f"Generated batch of {before_cnt} questions")
            
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
                
                q_text = q.get("text", "")
                q_key = create_unique_question_key(q)  # Use unique key generation
                logger.debug(f"Checking question: '{q_text[:50]}...' -> UNIQUE key: {q_key}")
                
                if q_key in solved_text_keys:
                    logger.debug(f"SKIPPING - Question already solved: {q_key}")
                    continue
                if q_key in seen_text_keys:
                    logger.debug(f"SKIPPING - Question already in current batch: {q_key}")
                    continue
                if q not in questions and q not in st.session_state[f"cached_test_{subject}"]:
                    questions.append(q)
                    seen_text_keys.add(q_key)
                    st.session_state[f"cached_test_{subject}"].append(q)
                    logger.debug(f"ADDED question: {q_key}")
                else:
                    logger.debug(f"SKIPPING - Question already in test or cache")
                    
            after_cnt = len(questions)
            logger.debug(f"Batch processing: {before_cnt} candidates -> {after_cnt} total questions so far")
            attempts += 1
       
        except Exception as e:
            logger.error(f"Ошибка генерации партии: {str(e)}")
            st.error(f"Партияны генерациялау кезінде қате: {str(e)}")
            attempts += 1


    # Clear progress bar
    progress_bar.empty()
    status_text.empty()
    
    if len(questions) < 20:
        logger.error(f"Generated only {len(questions)} questions instead of 20 (subject={subj})")
        if len(questions) == 0:
            st.info("Бұл пән бойынша жаңа сұрақтар қалған жоқ. Қателеріңізді қайталап шығыңыз.")
        else:
            st.error(f"20 сұрақтың орнына тек {len(questions)} сұрақ құрылды.")
        return questions

    logger.debug(f"=== FINAL TEST GENERATED: {len(questions)} questions ===")
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





def cleanup_empty_test_chats(user_id):
    try:
        resp = supabase.table("test_chats").select("id, messages").eq("user_id", user_id).execute()
        for row in (resp.data or []):
            msgs = row.get("messages") or []
            if not msgs:
                try:
                    supabase.table("test_chats").delete().eq("id", row.get("id")).execute()
                except Exception:
                    continue
    except Exception as e:
        logger.debug(f"Test chat cleanup skipped: {e}")
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
        
def fetch_exclusion_texts(subject: str, solved_keys: set, max_items: int = 100) -> list[str]:
    """
    Возвращает список текстов вопросов, которые пользователь уже решил правильно
    (по их ключам), извлекая их из сохранённых тестов в таблице saved_tests.
    Эти тексты добавляются в промпт как список исключений для GPT.
    """
    texts: list[str] = []
    if not solved_keys:
        return texts
    user_id = get_current_user_id()
    if not user_id:
        return texts
    subj = canonical_subject(subject)
    try:
        # Читаем недавние сохранённые тесты для пользователя и предмета
        resp = (
            supabase
            .table("saved_tests")
            .select("test_json")
            .eq("user_id", user_id)
            .eq("subject", subj)
            .order("updated_at", desc=True)
            .limit(min(max_items, 1000))
            .execute()
        )
        seen_keys = set()
        for row in (resp.data or []):
            payload = row.get("test_json")
            try:
                # В некоторых драйверах это может быть строка
                if isinstance(payload, str):
                    payload = json.loads(payload)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            questions = payload.get("questions") or []
            if not isinstance(questions, list):
                continue
            for q in questions:
                try:
                    qkey = create_unique_question_key(q)
                except Exception:
                    continue
                if qkey in solved_keys and qkey not in seen_keys:
                    qtext = q.get("text")
                    if isinstance(qtext, str) and qtext.strip():
                        texts.append(qtext)
                        seen_keys.add(qkey)
                        if len(texts) >= max_items:
                            return texts
    except Exception as e:
        logger.debug(f"fetch_exclusion_texts failed: {e}")
    return texts

def rename_test_chat(chat_id, new_name):
    if not new_name:
        return False, "Жаңа атау бос болмауы керек."
    try:
        response = supabase.table("test_chats").select("id").eq("title", new_name).execute()
        if response.data:
            return False, "Бұл атаумен чат бар."
        supabase.table("test_chats").update({"title": new_name, "updated_at": datetime.utcnow().isoformat()}).eq("id", chat_id).execute()
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
            model="gpt-5",
            messages=[
                {"role": "system", "content": "Сұрақ негізінде қазақ тілінде қысқа тақырыпты анықта (максимум 5 сөз). Формат: '[Пән] - [Тақырып]'"},
                {"role": "user", "content": f"Пән: {subject}\nСұрақ: {prompt}"}
            ],
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

def extract_kazakh_text_from_image(image_bytes: bytes, mime_type: str = "image/png") -> str:
    try:
        data_url = f"data:{mime_type};base64,{b64encode(image_bytes).decode('utf-8')}"
        resp = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Суреттен қазақша мәтінді дәл шығарып бер. Тек мәтіннің өзін қайтар."},
                        {"type": "image_url", "image_url": {"url": data_url}}
                    ]
                }
            ],
        )
        content = resp.choices[0].message.content
        return content.strip() if isinstance(content, str) else (content or "").strip()
    except Exception:
        return ""

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
    try:
        cleanup_empty_test_chats(st.session_state.user_id)
    except Exception:
        pass
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
        .stApp { color: #ffffff; max-width: 1200px; margin: 0 auto; font-family: Arial, sans-serif; }
        [data-testid=\"stSidebar\"] { width: 300px; }
        .chat-history-item { color: #ffffff; padding: 10px; margin: 6px 0; border-radius: 8px; }
        .stButton > button { color: #ffffff; border-radius: 6px; }
        .stTextInput > div > input { color: #ffffff; }
        h1, h2, h3, h4, p, label { color: #ffffff; }
        .stAlert, .stChatMessage, [data-baseweb=\"notification\"], .stTabs [data-baseweb=\"tab-highlight\"] { background: transparent !important; border: 0 !important; }
    </style>
    """, unsafe_allow_html=True)

    # Бүйірлік панель
    with st.sidebar:
        st.markdown("<h2 style='text-align: center; color: #ffffff;'>💬 Тест чаттары</h2>", unsafe_allow_html=True)

        if st.button("🆕 Жаңа тест чаты", key="new_test_chat"):
            try:
                if st.session_state.get("test_chat_id") and len(st.session_state.get("test_messages") or []) == 0:
                    delete_test_chat(st.session_state.get("test_chat_id"))
            except Exception:
                pass
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
    st.markdown("<h1 style='color: #ffffff;'>TEST📝</h1>", unsafe_allow_html=True)
    st.markdown(
    "<p style='color:#ffffff;'>Бұл бет <b>пәндер бойынша тест тапсырып</b>, содан кейін сол <b>тестер туралы сұрақтар</b> қою үшін арналған📝</p>",unsafe_allow_html=True)
    
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
                # Пән + күн форматы (дд.мм.гг) бойынша чат атауын орнату (уникалды атау)
                date_str = datetime.now().strftime('%d.%m.%y')
                base_title = f"{subject} - {date_str}"
                attempt_idx = 1
                new_title = base_title
                while True:
                    success, result = rename_test_chat(st.session_state.test_chat_id, new_title)
                    if success:
                        st.session_state.test_chat_title = result
                        break
                    # Если дубликат — добавляем суффикс #N и пробуем снова
                    attempt_idx += 1
                    new_title = f"{base_title} #{attempt_idx}"

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

    # Image inputs for test chat
    # Camera controls placed above inputs
    cam_flag_key = f"show_test_camera_{st.session_state.test_chat_id}"
    if cam_flag_key not in st.session_state:
        st.session_state[cam_flag_key] = False
    cam_cols = st.columns([1, 1, 2])
    with cam_cols[0]:
        if not st.session_state.get(cam_flag_key):
            if st.button("Open Camera", key=f"test_open_camera_{st.session_state.test_chat_id}"):
                st.session_state[cam_flag_key] = True
                st.rerun()
        else:
            if st.button("Close Camera", key=f"test_close_camera_{st.session_state.test_chat_id}"):
                st.session_state[cam_flag_key] = False
                st.rerun()

    col_img1, col_img2 = st.columns(2)
    with col_img1:
        uploaded_img = st.file_uploader("Сурет жүктеу (JPEG/PNG)", type=["png", "jpg", "jpeg"], key=f"test_image_uploader_{st.session_state.test_chat_id}")
    captured_img = None
    with col_img2:
        if st.session_state.get(cam_flag_key):
            captured_img = st.camera_input("Камерадан түсіру", key=f"test_camera_{st.session_state.test_chat_id}")

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
            last_hash_key = f"last_test_img_hash_{st.session_state.test_chat_id}"
            last_hash = st.session_state.get(last_hash_key)
            if current_hash and current_hash != last_hash:
                st.session_state[last_hash_key] = current_hash
                extracted_text = extract_kazakh_text_from_image(image_bytes, mime_type)
                if extracted_text:
                    st.session_state.test_messages.append({"role": "user", "content": extracted_text})
                    with st.chat_message("user"):
                        st.markdown(extracted_text)
                    # continue with same assistant flow
                    try:
                        assistant_id = SUBJECTS[subject]["assistant_id"]
                    except Exception:
                        assistant_id = None
                    if assistant_id:
                        with st.spinner("Жауап дайындалуда..."):
                            try:
                                thread = client.beta.threads.create()
                                client.beta.threads.messages.create(
                                    thread_id=thread.id,
                                    role="user",
                                    content=extracted_text
                                )
                                run = client.beta.threads.runs.create(
                                    thread_id=thread.id,
                                    assistant_id=assistant_id,
                                    tools=[{"type": "file_search"}]
                                )
                                while run.status in ["queued", "in_progress"]:
                                    run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
                                    time.sleep(2)
                                if run.status == "completed":
                                    messages = client.beta.threads.messages.list(thread_id=thread.id, limit=1)
                                    response_content = messages.data[0].content
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
                                            file_citation_block = getattr(block, 'file_citation', None)
                                            if file_citation_block:
                                                fid = getattr(file_citation_block, 'file_id', None)
                                                if isinstance(fid, str):
                                                    sources.add(fid)
                                        except Exception:
                                            continue
                                    try:
                                        answer_text = re.sub(r"【[^】]*】", "", answer_text)
                                        answer_text = re.sub(r"†source", "", answer_text, flags=re.IGNORECASE)
                                    except Exception:
                                        pass
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
                                    st.session_state.test_messages.append({"role": "assistant", "content": answer_text})
                                    with st.chat_message("assistant"):
                                        st.markdown(answer_text)
                                client.beta.threads.delete(thread.id)
                            except Exception as e:
                                st.error(f"Жауап алу кезінде қате: {str(e)}")

    # Test chat input - moved outside of image processing block
    user_input = st.chat_input("Тест бойынша сұрағыңызды енгізіңіз...", key=f"test_input_{st.session_state.test_chat_id}")
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

            rerun_needed = False
            if len(st.session_state.test_messages) == 2:
                base_title = generate_chat_title(user_input, subject)
                attempt_idx = 1
                new_title = base_title
                while True:
                    success, result = rename_test_chat(st.session_state.test_chat_id, new_title)
                    if success:
                        st.session_state.test_chat_title = result
                        logger.debug(f"Test chat renamed to {result}")
                        rerun_needed = True
                        break
                    attempt_idx += 1
                    new_title = f"{base_title} #{attempt_idx}"

            save_test_chat(
                chat_id=st.session_state.test_chat_id,
                user_id=st.session_state.user_id,
                messages=st.session_state.test_messages,
                title=st.session_state.test_chat_title
            )
            if rerun_needed:
                st.rerun()

    # Тестті көрсету
    current_test = st.session_state.get("current_test") or []
    if current_test:
        st.subheader(f"{subject} пәні бойынша тест")
        test_results = st.session_state.get("test_results")

        # Only show questions if test is not submitted
        if not st.session_state.get("test_submitted"):
            # Use form to prevent automatic page refreshes
            with st.form("test_form", clear_on_submit=False):
                for i, question in enumerate(current_test):
                    st.write(f"**{i + 1}. {question['text']}**")
                    
                    # Show radio buttons for answering
                    answer = st.radio(
                        f"{i + 1} сұраққа жауап таңдаңыз",
                        question["options"],
                        key=f"q_{i}_{st.session_state.test_chat_id}",
                        index=None
                    )
                    
                    # Store answer in session state only if selected
                    if answer is not None:
                        (st.session_state.setdefault("user_answers", {}))[i] = answer
                
                # Submit button inside the form
                submitted = st.form_submit_button("Жауаптарды жіберу")
                
                # Check answers after form submission
                if submitted:
                    user_answers = st.session_state.get("user_answers") or {}
                    total_q = len(current_test)
                    answered_all = all(user_answers.get(i) is not None for i in range(total_q))
                    
                    if not answered_all:
                        unanswered = [i + 1 for i in range(total_q) if user_answers.get(i) is None]
                        st.warning(f"Барлық сұрақтарға жауап беріңіз! Жауап берілмеген сұрақтар: {', '.join(map(str, unanswered))}")
                    else:
                        # Process the test
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
                        # Persist only correctly answered questions for this user/subject
                        try:
                            logger.debug(f"Calling save_results for subject='{subject}' with {len(current_test)} questions")
                            save_results(subject, current_test, st.session_state.test_results)
                            logger.debug("save_results completed successfully")
                            
                            # Force refresh the solved keys cache after saving
                            subj = canonical_subject(subject)
                            cache_key = f"excluded_keys_cache_{subj}"
                            # Clear the cache to force fresh fetch on next test
                            if cache_key in st.session_state:
                                del st.session_state[cache_key]
                            logger.debug(f"Cleared cache for subject '{subj}' to force fresh fetch")
                        except Exception as e:
                            logger.error(f"Error in save_results: {e}")
                            st.error(f"Ошибка сохранения результатов: {e}")
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


        # Note: We no longer auto-save answers to prevent page refreshes during test taking
        # Answers are only saved when the user explicitly submits the test
        # After progress saving, show feedback if submitted
        # If already submitted, render per-question feedback reliably
        if st.session_state.get("test_submitted") and isinstance(test_results, dict):
            st.markdown("---")
            st.markdown("## 🎯 Тест аяқталды!")
            st.markdown("### 📊 Сіздің нәтижелеріңіз:")
            st.markdown(f"**Ұпай: {test_results.get('score', 0)} / {test_results.get('total', len(current_test))} ({test_results.get('score', 0) / test_results.get('total', len(current_test)) * 100:.1f}%)**")
            st.markdown("---")
            
            for idx, _ in enumerate(current_test):
                result = test_results.get("results", [])[idx]
                status = "✅ Дұрыс" if result["is_correct"] else "❌ Қате"
                status_color = "green" if result["is_correct"] else "red"
                
                st.markdown(f"### {idx + 1}. {result['question']}")
                st.markdown(f"**Сіздің жауабыңыз:** <span style='color: {status_color};'>{result['user_answer']}</span>", unsafe_allow_html=True)
                st.markdown(f"**Дұрыс жауап:** <span style='color: green;'>{result['correct_answer']}</span>", unsafe_allow_html=True)
                st.markdown(f"**Күй:** {status}")
                st.markdown(f"**Контекст:** {result['context']}")
                st.markdown(f"**Түсініктеме:** {result['explanation']}")
                st.markdown("---")


        else:
            # Test is completed - show completion message and option to start new test
            st.info("🎯 Бұл тест аяқталды. Сіз тек нәтижелерді көре аласыз.")
