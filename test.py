import streamlit as st
import json
import re
from openai import OpenAI, RateLimitError
import time

# OpenAI API орнату
import os
from dotenv import load_dotenv

# Загружаем .env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Пәндер тізімі
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

# Модель жауабын тазалау функциясы
def clean_response(text):
    try:
        text = re.sub(r'```json\s*|\s*```', '', text, flags=re.MULTILINE)
        text = text.strip()
        json_start = text.find('[')
        json_end = text.rfind(']') + 1
        if json_start == -1 or json_end <= json_start:
            st.error(f"JSON шекараларын табу мүмкін емес: {text[:500]}...")
            return None
        json_text = text[json_start:json_end]
        json.loads(json_text)
        return json_text
    except json.JSONDecodeError as e:
        st.error(f"JSON пішімі қате: {str(e)}")
        return None

# Сұрақтар партиясын генерациялау функциясы
def generate_batch(subject, batch_size=5):
    content = f"""
{subject} пәні бойынша {batch_size} сұрақты көп таңдаулы түрде қазақ тілінде генерациялаңыз, ЕНТ оқулықтарына сәйкес.

Талаптар:
1. Әр сұрақ мыналарды қамтиды:
   - Сұрақ мәтіні (ЕНТ оқулықтарынан, толтырғышсыз, нөмірленген, мысалы, "1. Сұрақ мәтіні").
   - Сұрақтар өте қиын болуы тиіс!!!
   - 4 жауап нұсқасы: 1 дұрыс, 3 шынайы, бірақ қате.
   - Дереккөз: оқулық атауы және бет нөмірі (мысалы, "25 бет" немесе "25-26 бет").
   - Түсініктеме: дұрыс жауаптың неге дұрыс екенін және әр қате жауаптың неге қате екенін түсіндіретін мәтін (100–150 сөз, қазақ тілінде, оқулық мазмұнына сәйкес).
2. Жауап пішімі:
   - ТЕК жарамды JSON, [ басталып, ] аяқталады.
   - ```json, түсініктемелер немесе артық мәтінсіз.
   - Әр сұрақ үшін өрістер:
     - text: строка (сұрақ мәтіні, нөмірленген).
     - options: 4 строка массиві (жауап нұсқалары).
     - correct_option: сан (0–3, дұрыс жауап индексі).
     - book_title: строка (оқулық атауы, мысалы, "Құқық негіздері 10 сынып").
     - page: строка (мысалы, "25 бет").
     - explanation: строка (дұрыс және қате жауаптардың түсініктемесі).
3. Мысал:
   [
     {{
       "text": "1. Құқық нормалары дегеніміз не?",
       "options": ["a) Заңмен реттелетін ережелер", "b) Моральдық нормалар", "c) Дін ережелері", "d) Әдет-ғұрыптар"],
       "correct_option": 0,
       "book_title": "Құқық негіздері 10 сынып",
       "page": "10 бет",
       "explanation": "Дұрыс жауап – a) Заңмен реттелетін ережелер, өйткені құқық нормалары мемлекетпен бекітіліп, заңды күшке ие болады. b) Моральдық нормалар қоғамдық санамен реттеледі, бірақ заңды күші жоқ. c) Дін ережелері діни сенімдерге негізделген. d) Әдет-ғұрыптар – қоғамдық дәстүрлер, бірақ олар заңмен міндетті емес."
     }}
   ]
4. Тексеру: дәл {batch_size} сұрақ, деректер ЕНТ оқулықтарына сәйкес, бет және түсініктеме оқулық мазмұнына дәл сәйкес болуы керек.
"""

    max_retries = 5
    retry_delay = 10  # Initial delay in seconds
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Сен ЕНТ оқулықтарына негізделген сұрақтар генерациялайтын мұғалімсің."},
                    {"role": "user", "content": content}
                ],
                temperature=0.7
            )
            response_content = response.choices[0].message.content
            cleaned_response = clean_response(response_content)
            if not cleaned_response:
                return []
            try:
                questions = json.loads(cleaned_response)
                return questions
            except json.JSONDecodeError as e:
                st.error(f"JSON қатесі: {str(e)}")
                return []
        except RateLimitError:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                st.error("Қате: OpenAI лимиті асып кетті. 2-3 минут күтіңіз немесе OpenAI есептік жазбаңызды тексеріңіз: https://platform.openai.com/account/usage")
                return []
        except Exception as e:
            st.error(f"Партияны генерациялау кезінде қате: {str(e)}")
            return []
        time.sleep(5)  # Delay to avoid rapid requests

# Сұрақтар партиясын генерациялау функциясы
def generate_test(subject):
    questions = []
    attempts = 0
    max_attempts = 10

    # Cache questions in session state
    if f"cached_test_{subject}" not in st.session_state:
        st.session_state[f"cached_test_{subject}"] = []

    while len(questions) < 20 and attempts < max_attempts:
        try:
            batch_questions = generate_batch(subject, batch_size=5)
            for q in batch_questions:
                required_fields = ["text", "options", "correct_option", "book_title", "page", "explanation"]
                if not all(key in q for key in required_fields):
                    st.error(f"Міндетті өрістер жоқ: {q}")
                    continue
                if len(q["options"]) != 4:
                    st.error(f"Жауап нұсқаларының саны қате: {q['options']}")
                    continue
                if not isinstance(q["correct_option"], int) or q["correct_option"] not in range(4):
                    st.error(f"Қате correct_option: {q['correct_option']}")
                    continue
                if not re.match(r'^\d+[-]?\d*\s*бет$', q.get("page", "")):
                    st.error(f"Бет пішімі қате: {q.get('page')}")
                    continue
                if not q.get("explanation"):
                    st.error(f"Түсініктеме жоқ: {q}")
                    continue
                if q not in questions and q not in st.session_state[f"cached_test_{subject}"]:
                    questions.append(q)
                    st.session_state[f"cached_test_{subject}"].append(q)
            attempts += 1
            time.sleep(5)  # Delay to avoid rapid requests
        except Exception as e:
            st.error(f"Партияны генерациялау кезінде қате: {str(e)}")
            attempts += 1
            time.sleep(5)

    if len(questions) < 20:
        st.error(f"20 сұрақтың орнына тек {len(questions)} сұрақ құрылды. 2-3 минут күтіңіз немесе OpenAI есептік жазбаңызды тексеріңіз: https://platform.openai.com/account/usage")
        return questions

    # Исправляем нумерацию вопросов
    for i, question in enumerate(questions):
        # Извлекаем текущий номер из текста вопроса
        match = re.match(r'^\d+\.\s*(.*)', question["text"])
        if match:
            question["text"] = f"{i + 1}. {match.group(1)}"
        else:
            question["text"] = f"{i + 1}. {question['text']}"

    return questions[:20]  # 20-дан артық болса, кесеміз

def test_page():
    # Сессия күйін инициализациялау
    if "test_results" not in st.session_state:
        st.session_state.test_results = None
    if "current_test" not in st.session_state:
        st.session_state.current_test = None
    if "user_answers" not in st.session_state:
        st.session_state.user_answers = {}
    if "test_submitted" not in st.session_state:
        st.session_state.test_submitted = False

    # Streamlit интерфейсі
    st.title("TEST📝")
    subject = st.selectbox("Пәнді таңдаңыз", list(SUBJECTS.keys()))

    if st.button("️Тест құру 📄️"):
        if subject not in SUBJECTS:
            st.error(f"'{subject}'📚пәні қолдау таппайды.")
            return
        with st.spinner("⏳Тест құрылуда..."):
            test_questions = generate_test(subject)
            if test_questions and len(test_questions) == 20:
                st.session_state.current_test = test_questions
                st.session_state.user_answers = {}
                st.session_state.test_submitted = False
                st.session_state.test_results = None
                st.success("Тест сәтті құрылды!")
                st.rerun()
            else:
                st.error("Толық тест құру мүмкін болмады. 2-3 минут күтіңіз немесе OpenAI есептік жазбаңызды тексеріңіз: https://platform.openai.com/account/usage")

    # Тестті көрсету
    if st.session_state.current_test:
        st.subheader(f"{subject} пәні бойынша тест")
        if st.session_state.test_submitted and st.session_state.test_results:
            score = st.session_state.test_results["score"]
            total = st.session_state.test_results["total"]
            st.markdown(f"<h2 style='color: #1f77b4;'>Сіздің ұпайыңыз: {score} / {total} ({score / total * 100:.1f}%)</h2>",
                        unsafe_allow_html=True)

        for i, question in enumerate(st.session_state.current_test):
            st.write(f"**{question['text']}**")
            answer = st.radio("", question["options"],
                            key=f"q_{i}_{subject}", index=None)
            st.session_state.user_answers[i] = answer

            if st.session_state.test_submitted and st.session_state.test_results:
                result = st.session_state.test_results["results"][i]
                status = "Дұрыс" if result["is_correct"] else "Қате"
                status_color = "green" if result["is_correct"] else "red"
                st.markdown(f"Сіздің жауабыңыз: <span style='color: {status_color};'>{result['user_answer']}</span>",
                            unsafe_allow_html=True)
                st.markdown(f"Дұрыс жауап: <span style='color: green;'>{result['correct_answer']}</span>",
                            unsafe_allow_html=True)
                st.markdown(f"Күй: <span style='color: {status_color};'>{status}</span>", unsafe_allow_html=True)
                st.markdown(f"**Түсініктеме**: {result['explanation']}")
                st.markdown("---")

        if not st.session_state.test_submitted:
            if len(st.session_state.user_answers) == len(st.session_state.current_test) and all(st.session_state.user_answers.values()):
                if st.button("Жауаптарды жіберу"):
                    correct_count = 0
                    results = []
                    for i, question in enumerate(st.session_state.current_test):
                        user_answer = st.session_state.user_answers.get(i)
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
                            "explanation": question["explanation"]
                        })
                    st.session_state.test_results = {
                        "score": correct_count,
                        "total": len(st.session_state.current_test),
                        "results": results
                    }
                    st.session_state.test_submitted = True
                    st.rerun()
            else:
                st.warning("Барлық сұрақтарға жауап беріңіз!")