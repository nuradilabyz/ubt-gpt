import logging
from typing import List, Optional, Tuple, Dict, Any

import os
import json
import math
import time
import re
import zipfile
import html as html_lib
import numpy as np
import streamlit as st
from supabase import Client
from subjects import SUBJECTS
from openai import OpenAI

logger = logging.getLogger(__name__)


# =========================
# Local books helpers (folder-based)
# =========================

def get_books_base_dir() -> str:
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = "."
    return os.path.join(cwd, "books")


def list_local_subject_folders() -> List[str]:
    base = get_books_base_dir()
    try:
        items = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        items.sort()
        return items
    except Exception:
        return []


def list_files_in_subject_folder(subject_folder: str) -> List[str]:
    base = get_books_base_dir()
    folder_path = os.path.join(base, subject_folder)
    try:
        files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f)) and f.lower().endswith((".txt", ".pdf", ".epub"))]
        files.sort()
        return files
    except Exception:
        return []


def _epub_fallback_read_text(path: str) -> str:
    try:
        texts: List[str] = []
        with zipfile.ZipFile(path, 'r') as zf:
            for name in zf.namelist():
                low = name.lower()
                if low.endswith(('.xhtml', '.html', '.htm')):
                    try:
                        data = zf.read(name)
                        s = data.decode('utf-8', errors='ignore')
                        # Strip tags rudimentarily if bs4 not available
                        s = re.sub(r'<[^>]+>', ' ', s)
                        s = html_lib.unescape(s)
                        s = re.sub(r'\s+', ' ', s)
                        texts.append(s.strip())
                    except Exception:
                        continue
        return "\n\n".join(texts)
    except Exception:
        return ""


def read_text_from_file(subject_folder: str, filename: str) -> str:
    base = get_books_base_dir()
    path = os.path.join(base, subject_folder, filename)
    low = filename.lower()
    try:
        if low.endswith(".txt"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        if low.endswith(".pdf"):
            try:
                from PyPDF2 import PdfReader  # type: ignore
            except Exception:
                st.error("PDF оқу үшін PyPDF2 пакетін орнатыңыз: pip install PyPDF2")
                return ""
            try:
                reader = PdfReader(path)
                texts = []
                for page in reader.pages:
                    try:
                        texts.append(page.extract_text() or "")
                    except Exception:
                        continue
                return "\n\n".join(texts)
            except Exception:
                return ""
        if low.endswith(".epub"):
            # Try ebooklib + bs4 first
            try:
                from ebooklib import epub  # type: ignore
                from bs4 import BeautifulSoup  # type: ignore
                book = epub.read_epub(path)
                texts = []
                for item in book.get_items():
                    if item.get_type() == 9:  # DOCUMENT
                        html = item.get_content().decode("utf-8", errors="ignore")
                        soup = BeautifulSoup(html, "html.parser")
                        texts.append(soup.get_text(" "))
                return "\n\n".join(texts)
            except Exception:
                # Fallback to zip/html stripping without external deps
                fb = _epub_fallback_read_text(path)
                if not fb:
                    st.error("EPUB оқу үшін ebooklib және beautifulsoup4 пакеттерін орнатыңыз: pip install ebooklib beautifulsoup4")
                return fb
    except Exception:
        return ""
    return ""


def chunk_text(text: str, target_tokens: int = 900, overlap_tokens: int = 100) -> List[str]:
    if not text:
        return []
    target_chars = max(1000, target_tokens * 4)
    overlap_chars = max(0, overlap_tokens * 4)
    chunks: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(n, i + target_chars)
        chunk = text[i:end]
        chunk = chunk.strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        i = end - overlap_chars
        if i < 0:
            i = 0
    return chunks


def embed_texts(client: OpenAI, texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    try:
        resp = client.embeddings.create(model="text-embedding-3-small", input=texts)
        return [item.embedding for item in getattr(resp, "data", [])]
    except Exception as e:
        logger.error(f"Embedding error: {e}")
        return [[] for _ in texts]


def ingest_book_to_vector_chunks(supabase: Client, subject: str, filename: str, full_text: str) -> Tuple[bool, str, List[str]]:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    chunks = chunk_text(full_text)
    if not chunks:
        return False, "Мәтін бос немесе оқылмады.", []
    embeddings = embed_texts(client, chunks)
    used_ids: List[str] = []
    try:
        rows = []
        for idx, (chunk_text_item, emb) in enumerate(zip(chunks, embeddings)):
            rows.append({
                "subject": subject,
                "filename": filename,
                "chunk_index": idx,
                "content": chunk_text_item,
                "embedding": emb if emb else None,
            })
        batch_size = 50
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i+batch_size]
            resp = supabase.table("vector_chunks").insert(batch).execute()
            try:
                for r in (resp.data or []):
                    rid = r.get("id")
                    if isinstance(rid, str):
                        used_ids.append(rid)
            except Exception:
                pass
        return True, f"{len(rows)} чанктар енгізілді.", used_ids
    except Exception as e:
        msg = str(e)
        logger.error(f"Ingest error: {msg}")
        if "chunk_index" in msg or "PGRST204" in msg:
            st.error("Supabase кестесінде 'chunk_index' жоқ немесе schema cache ескі. Төмендегі SQL-ді орындаңыз:")
            st.code(
                """
create extension if not exists vector;

create table if not exists vector_chunks (
  id uuid primary key default gen_random_uuid(),
  subject text not null,
  filename text not null,
  chunk_index int not null,
  content text not null,
  embedding vector(1536),
  created_at timestamptz default now()
);

alter table vector_chunks add column if not exists chunk_index int not null default 0;
alter table vector_chunks alter column chunk_index drop default;
create index if not exists idx_vector_chunks_subject on vector_chunks(subject);
create index if not exists idx_vector_chunks_filename on vector_chunks(filename);
create index if not exists idx_vector_chunks_embedding on vector_chunks using ivfflat (embedding vector_l2_ops);
                """,
                language="sql"
            )
        return False, f"Ингест қате: {msg}", []


# =========================
# Data access for Supabase vectors/summaries
# =========================

def load_summary_books(supabase: Client, subject: str) -> List[str]:
    try:
        resp = supabase.table("summaries").select("book_title, book_name").eq("subject", subject).order("book_title").execute()
        titles = []
        for row in (resp.data or []):
            bt = row.get("book_title") or row.get("book_name")
            if bt:
                titles.append(bt)
        seen = set()
        uniq: List[str] = []
        for t in titles:
            if not isinstance(t, str) or not t:
                continue
            if t not in seen:
                uniq.append(t)
                seen.add(t)
        return uniq
    except Exception as e:
        logger.error(f"Failed to load summary books: {e}")
        return []


def fetch_summary(supabase: Client, subject: str, book_title: str) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """Return (markdown, created_at, meta) where meta may include section_summaries, method_used, ordering_confidence, chunk_ids_used."""
    try:
        resp = supabase.table("summaries").select("summary_content, summary_text, created_at, section_summaries, method_used, ordering_confidence, chunk_ids_used").eq("subject", subject).or_(f"book_title.eq.{book_title},book_name.eq.{book_title}").limit(1).execute()
        if resp.data:
            row = resp.data[0]
            markdown = row.get("summary_content") or row.get("summary_text") or ""
            meta = {
                "section_summaries": row.get("section_summaries"),
                "method_used": row.get("method_used"),
                "ordering_confidence": row.get("ordering_confidence"),
                "chunk_ids_used": row.get("chunk_ids_used"),
            }
            return markdown, row.get("created_at"), meta
        return None, None, None
    except Exception as e:
        logger.error(f"Failed to fetch summary: {e}")
        return None, None, None


def _normalize_chunk(row: Dict[str, Any]) -> Dict[str, Any]:
    cid = row.get("chunk_id") or row.get("id") or row.get("uuid") or row.get("_id") or ""
    raw_id = row.get("id")
    text_val = row.get("text") or row.get("content") or ""
    return {
        "chunk_id": cid if isinstance(cid, str) else str(cid) if cid is not None else "",
        "id_raw": raw_id if isinstance(raw_id, str) else str(raw_id) if raw_id is not None else "",
        "text": text_val if isinstance(text_val, str) else str(text_val),
        "embedding": row.get("embedding"),
        "filename": row.get("filename"),
        "metadata": row.get("metadata") or {},
    }


def fetch_book_chunks_by_filename(supabase: Client, filename: str, subject: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        query = supabase.table("vector_chunks").select("id, chunk_id, content, text, embedding, filename, metadata")
        query = query.eq("filename", filename)
        if subject:
            query = query.eq("subject", subject)
        resp = query.execute()
        rows = resp.data or []
        return [_normalize_chunk(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to fetch chunks for {filename}: {e}")
        return []


def fetch_chunks_by_ids(supabase: Client, chunk_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not chunk_ids:
        return {}
    try:
        resp = supabase.table("vector_chunks").select("id, chunk_id, content, text, filename, metadata").in_("id", chunk_ids).execute()
        rows = resp.data or []
        if not rows:
            resp2 = supabase.table("vector_chunks").select("id, chunk_id, content, text, filename, metadata").in_("chunk_id", chunk_ids).execute()
            rows = resp2.data or []
        normed = [_normalize_chunk(r) for r in rows]
        out: Dict[str, Dict[str, Any]] = {}
        for r in normed:
            key = r.get("chunk_id") or r.get("id_raw")
            if isinstance(key, str) and key:
                out[key] = r
        return out
    except Exception as e:
        logger.error(f"Failed to fetch chunks by ids: {e}")
        return {}


def load_vector_filenames_by_subject(supabase: Client, subject: str) -> List[str]:
    try:
        resp = supabase.table("vector_chunks").select("filename").eq("subject", subject).execute()
        names = [row.get("filename") for row in (resp.data or [])]
        seen = set()
        out: List[str] = []
        for n in names:
            if isinstance(n, str) and n and n not in seen:
                out.append(n)
                seen.add(n)
        return out
    except Exception as e:
        logger.error(f"Failed to load filenames by subject: {e}")
        return []


# =========================
# Cleaning & ordering
# =========================

def _normalize_text(t: str) -> str:
    return " ".join((t or "").strip().split()).lower()


def clean_and_dedupe_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    cleaned: List[Dict[str, Any]] = []
    for ch in chunks:
        text = ch.get("text") or ""
        if not isinstance(text, str):
            continue
        norm = _normalize_text(text)
        if len(norm) < 20:
            continue
        h = hash(norm)
        if h in seen:
            continue
        seen.add(h)
        cleaned.append(ch)
    return cleaned


def _embeddings_matrix(chunks: List[Dict[str, Any]]) -> Optional[np.ndarray]:
    emb_list = []
    for ch in chunks:
        emb = ch.get("embedding")
        if isinstance(emb, list) and emb and all(isinstance(x, (int, float)) for x in emb):
            emb_list.append(np.array(emb, dtype=float))
        else:
            return None
    if not emb_list:
        return None
    return np.vstack(emb_list)


def _order_by_metadata(chunks: List[Dict[str, Any]]):
    indexes = []
    valid = 0
    for i, ch in enumerate(chunks):
        meta = ch.get("metadata") or {}
        idx = meta.get("chunk_index")
        if isinstance(idx, int):
            valid += 1
        indexes.append(idx)
    if valid / max(len(chunks), 1) > 0.9:
        order = sorted(range(len(chunks)), key=lambda i: (indexes[i] if isinstance(indexes[i], int) else 10**9))
        return order, "chunk_index", 0.95

    offsets = []
    valid = 0
    for i, ch in enumerate(chunks):
        meta = ch.get("metadata") or {}
        off = meta.get("char_offset_start")
        if isinstance(off, int):
            valid += 1
        offsets.append(off)
    if valid / max(len(chunks), 1) > 0.9:
        order = sorted(range(len(chunks)), key=lambda i: (offsets[i] if isinstance(offsets[i], int) else 10**9))
        return order, "char_offset_start", 0.9

    sorders = []
    valid = 0
    for i, ch in enumerate(chunks):
        meta = ch.get("metadata") or {}
        so = meta.get("source_order") or meta.get("source_sequence")
        if isinstance(so, (int, float)):
            valid += 1
        sorders.append(so)
    if valid / max(len(chunks), 1) > 0.9:
        order = sorted(range(len(chunks)), key=lambda i: (sorders[i] if isinstance(sorders[i], (int, float)) else 10**9))
        return order, "source_order", 0.85

    return None, "none", 0.0


def _order_semantic(chunks: List[Dict[str, Any]]):
    emb = _embeddings_matrix(chunks)
    if emb is None:
        return list(range(len(chunks))), 0.2, {"method": "as_is"}

    norms = np.linalg.norm(emb, axis=1, keepdims=True) + 1e-12
    E = emb / norms

    U, S, Vt = np.linalg.svd(E - E.mean(axis=0, keepdims=True), full_matrices=False)
    pc1 = U[:, 0]
    order_a = np.argsort(pc1).tolist()

    sim = E @ E.T
    np.fill_diagonal(sim, -1.0)
    start = int(np.argmin(sim.mean(axis=1)))
    visited = {start}
    path = [start]
    cur = start
    for _ in range(len(chunks) - 1):
        nxt = int(np.argmax(sim[cur]))
        tries = 0
        while nxt in visited and tries < len(chunks):
            sim[cur, nxt] = -1.0
            nxt = int(np.argmax(sim[cur]))
            tries += 1
        if nxt in visited:
            unv = [i for i in range(len(chunks)) if i not in visited]
            if not unv:
                break
            nxt = unv[0]
        visited.add(nxt)
        path.append(nxt)
        cur = nxt
    order_b = path

    pos_a = {idx: i for i, idx in enumerate(order_a)}
    agree = 0
    total = len(order_b)
    for i in range(total - 1):
        a1 = pos_a.get(order_b[i], 0)
        a2 = pos_a.get(order_b[i + 1], 0)
        if a2 >= a1:
            agree += 1
    confidence = 0.4 + 0.4 * (agree / max(total - 1, 1))
    return order_a, confidence, {"method": "semantic_pca_greedy", "agreement": agree / max(total - 1, 1)}


def determine_ordering(chunks: List[Dict[str, Any]]):
    if not chunks:
        return [], "none", 0.0
    meta_order, method, conf = _order_by_metadata(chunks)
    if meta_order is not None:
        ordered = [chunks[i] for i in meta_order]
        return ordered, method, conf
    order_sem, conf_sem, extra = _order_semantic(chunks)
    ordered = [chunks[i] for i in order_sem]
    return ordered, extra.get("method", "semantic"), conf_sem


# =========================
# Blocking & LLM calls
# =========================

def _approx_tokens(text: str) -> int:
    return max(1, int(len(text) / 4))


def split_into_blocks(ordered_chunks: List[Dict[str, Any]], target_tokens: int = 2500) -> List[List[Dict[str, Any]]]:
    blocks: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    cur_tokens = 0
    for ch in ordered_chunks:
        t = ch.get("text") or ""
        cur_tokens += _approx_tokens(t)
        cur.append(ch)
        if cur_tokens >= target_tokens and cur:
            blocks.append(cur)
            cur = []
            cur_tokens = 0
    if cur:
        blocks.append(cur)
    return blocks


def _build_passages(block: List[Dict[str, Any]]) -> str:
    lines = ["---"]
    for ch in block:
        cid = ch.get("chunk_id") or ""
        text = ch.get("text") or ""
        lines.append(f"[chunk_id: {cid}] {text}")
    lines.append("---")
    return "\n".join(lines)


def summarize_block(client: OpenAI, block: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    passages = _build_passages(block)
    system = (
        "You are a meticulous educator and summarizer. Only use the text below. Do NOT add external info. "
        "Produce comprehensive study notes (конспект) covering all key concepts, definitions, explanations, examples, and takeaways."
    )
    user = (
        "Create a DETAILED Markdown section (not short) using the passages. Requirements:\n"
        "- Use headings with ## and ### for sections and subsections.\n"
        "- Use bullet points for key ideas, lists, and steps.\n"
        "- Use **bold** for important terms and definitions; use *italic* for side notes.\n"
        "- Use emojis like ✅ ❌ ⭐ ⚠️ to highlight key points.\n"
        "- You may use inline HTML color spans (e.g., <span style=\"color:red\">...</span>) to emphasize critical phrases.\n"
        "- Include formulas or code blocks if present using fenced blocks.\n"
        "- After EACH paragraph add provenance: [sources: <comma-separated chunk_ids>].\n"
        "- If uncertain, mark [uncertain]. Do NOT hallucinate.\n\n"
        f"PASSAGES:\n{passages}\nReturn only valid, rich Markdown."
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1,
            max_tokens=1800,
        )
        content = resp.choices[0].message.content or ""
        ids: List[str] = []
        for ch in block:
            cid = ch.get("chunk_id")
            if isinstance(cid, str) and cid:
                ids.append(cid)
        return content, ids
    except Exception as e:
        logger.error(f"Block summarize error: {e}")
        return "", []


def merge_blocks(client: OpenAI, block_summaries: List[str]) -> str:
    joined = "\n\n".join(f"({i+1}) {bs}" for i, bs in enumerate(block_summaries))
    system = (
        "You are a summarizer. Only use the provided block summaries to create a unified, LONG, exam-ready Markdown study note. "
        "Do NOT invent facts. Keep fidelity and provenance for paragraphs."
    )
    user = (
        "Merge and refine the block summaries into a comprehensive book summary with the following:\n"
        "- A Table of Contents at the top.\n"
        "- Clear sections with ## and ###.\n"
        "- Bullet points for concepts and examples.\n"
        "- **Bold** key terms, *italic* side notes, and emojis (✅ ❌ ⭐ ⚠️).\n"
        "- Allow inline HTML color spans for emphasis.\n"
        "- Keep each paragraph ending with [sources: <chunk ids>].\n\n"
        f"BLOCK_SUMMARIES:\n{joined}"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1,
            max_tokens=3000,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"Merge summarize error: {e}")
        return ""


def expand_summary_if_short(client: OpenAI, merged_markdown: str, block_summaries: List[str]) -> str:
    try:
        if not merged_markdown:
            return merged_markdown
        if len(merged_markdown) >= SUMMARY_MIN_CHARS:
            return merged_markdown
        joined = "\n\n".join(f"({i+1}) {bs}" for i, bs in enumerate(block_summaries))
        system = (
            "You are an expert educator. Expand and enrich the summary while strictly staying within the provided block summaries. "
            "Do NOT add external information."
        )
        user = (
            "The current merged summary is too short. Expand it into a LONG, exam-ready study note with:\n"
            "- More detailed explanations and context;\n"
            "- Additional bullet points, examples, and definitions;\n"
            "- Use of **bold**, *italic*, ✅ ❌ ⭐ ⚠️ markers;\n"
            "- Inline HTML color spans where helpful;\n"
            "- Maintain paragraph-level provenance [sources: <chunk ids>].\n\n"
            "Here are the block summaries you must rely on (no external info):\n"
            f"{joined}\n\n"
            "Here is the current merged summary. Expand and improve it while preserving its structure and fidelity:\n"
            f"{merged_markdown}"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.15,
            max_tokens=4000,
        )
        content = resp.choices[0].message.content or ""
        # If expansion still shorter, return whichever is longer
        return content if len(content) > len(merged_markdown) else merged_markdown
    except Exception as e:
        logger.error(f"Expand summary error: {e}")
        return merged_markdown


def build_and_save_summary_via_assistant(subject: str, book_title: str) -> Tuple[bool, str, str]:
    """Fallback builder using assistant + file_search over the subject's vector store to summarize a specific file by name."""
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        assistant_id = SUBJECTS.get(subject, {}).get("assistant_id")
        if not assistant_id:
            return False, "Assistant not configured for subject.", ""
        thread = client.beta.threads.create()
        prompt = f"""Төмендегі векторлық базада тек осы файлды ғана пайдалан: {book_title}

Осы кітап бойынша қысқа, құрылымды Markdown-конспект жаса. **маңызды терминдерді** қалыңмен, *түсіндірмелерді* курсивпен бер. Қажет жерлерде түстер/эмодзилерді қолдан. Сыртқы мәлімет қоспа."""
        client.beta.threads.messages.create(thread_id=thread.id, role="user", content=prompt)
        run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=assistant_id, tools=[{"type": "file_search"}])
        while run.status in ["queued", "in_progress"]:
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            time.sleep(2)
        if run.status != "completed":
            try:
                client.beta.threads.delete(thread.id)
            except Exception:
                pass
            return False, f"Assistant run failed: {run.status}", ""
        messages = client.beta.threads.messages.list(thread_id=thread.id, limit=1)
        content_blocks = messages.data[0].content
        md_parts: List[str] = []
        for block in content_blocks:
            try:
                text_part = getattr(block, "text", None)
                if text_part is not None:
                    value = getattr(text_part, "value", None)
                    if isinstance(value, str):
                        md_parts.append(value)
            except Exception:
                continue
        try:
            client.beta.threads.delete(thread.id)
        except Exception:
            pass
        md = "\n".join(md_parts).strip()
        return (True if md else False), ("OK" if md else "Empty content"), md
    except Exception as e:
        return False, f"Assistant fallback error: {e}", ""


# =========================
# Save & pipeline
# =========================

def save_summary(supabase: Client, subject: str, book_title: str, summary_markdown: str, meta: Dict[str, Any]) -> bool:
    try:
        payload = {
            "subject": subject,
            "subject_name": subject,
            "book_title": book_title,
            "book_name": book_title,
            "summary_content": summary_markdown,
            "summary_text": summary_markdown,
            "section_summaries": meta.get("section_summaries"),
            "method_used": meta.get("method_used"),
            "ordering_confidence": meta.get("ordering_confidence"),
            "chunk_ids_used": meta.get("chunk_ids_used"),
        }
        try:
            supabase.table("summaries").delete().or_(f"and(subject.eq.{subject},book_title.eq.{book_title}),and(subject.eq.{subject},book_name.eq.{book_title})").execute()
        except Exception:
            pass
        supabase.table("summaries").insert(payload).execute()
        return True
    except Exception as e:
        try:
            supabase.table("summaries").delete().or_(f"and(subject.eq.{subject},book_title.eq.{book_title}),and(subject.eq.{subject},book_name.eq.{book_title})").execute()
        except Exception:
            pass
        try:
            supabase.table("summaries").insert({
                "subject": subject,
                "subject_name": subject,
                "book_title": book_title,
                "book_name": book_title,
                "summary_content": summary_markdown,
                "summary_text": summary_markdown,
            }).execute()
            logger.warning(f"Saved minimal summary without extended metadata due to error: {e}")
            return True
        except Exception as e2:
            logger.error(f"Failed to save summary: {e2}")
            return False


def build_and_save_summary(supabase: Client, subject: str, book_title: str, filename: str) -> Tuple[bool, str]:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    chunks = fetch_book_chunks_by_filename(supabase, filename, subject=subject)
    if not chunks:
        ok, msg, md = build_and_save_summary_via_assistant(subject, filename)
        if ok and md:
            meta = {"section_summaries": [], "method_used": "assistant_file_search", "ordering_confidence": None, "chunk_ids_used": []}
            saved = save_summary(supabase, subject, book_title, md, meta)
            return (True if saved else False), ("Сақталды" if saved else "Сақтау сәтсіз")
        return False, "Кітап үшін векторлық чанктар табылмады."

    cleaned = clean_and_dedupe_chunks(chunks)
    ordered, method_used, conf = determine_ordering(cleaned)

    blocks = split_into_blocks(ordered, target_tokens=2500)
    block_summaries: List[str] = []
    chunk_ids_used: List[str] = []
    for b in blocks:
        bs, ids = summarize_block(client, b)
        if bs:
            block_summaries.append(bs)
        if ids:
            chunk_ids_used.extend(ids)

    if not block_summaries:
        return False, "Блок-дөңгелету нәтиже бермеді."

    merged = merge_blocks(client, block_summaries)
    if not merged:
        return False, "Қорытынды суммари құрастыру сәтсіз."

    # Expansion pass if too short
    final_summary = expand_summary_if_short(client, merged, block_summaries)

    section_summaries = [{"section_title": f"Section {i+1}", "paragraph_id": f"p{i+1}", "chunk_ids": []} for i in range(len(block_summaries))]

    meta = {
        "section_summaries": section_summaries,
        "method_used": method_used,
        "ordering_confidence": conf,
        "chunk_ids_used": list(dict.fromkeys(chunk_ids_used)),
    }

    ok = save_summary(supabase, subject, book_title, final_summary, meta)
    return ok, ("Сақталды" if ok else "Сақтау сәтсіз")


# =========================
# UI Page
# =========================

def summary_page(supabase: Client) -> None:
    st.markdown("<h1 style='color:#ffffff;'>SUMMARY📚</h1>", unsafe_allow_html=True)

    st.markdown(
        """
        <style>
        .summary-container h1, .summary-container h2, .summary-container h3, .summary-container h4, .summary-container p, .summary-container li, .summary-container code, .summary-container blockquote { color: #ffffff; }
        .summary-container code { background: rgba(255,255,255,0.06); padding: 2px 6px; border-radius: 6px; }
        .summary-container blockquote { border-left: 4px solid #888; padding: 8px 12px; margin: 8px 0; background: rgba(255,255,255,0.03); }
        .summary-badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; margin-right: 8px; background: rgba(255,255,255,0.08); }
        .tag-warning { color: #ff6b6b; font-weight: 600; }
        .tag-example { color: #3bd16f; font-weight: 600; }
        .tag-info { color: #5ab0ff; font-weight: 600; }
        .hl { background: rgba(255, 230, 0, 0.25); padding: 0 4px; border-radius: 4px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Local subject (folder) selection
    local_subjects = list_local_subject_folders()
    if not local_subjects:
        st.warning("'books/' қалтасы табылмады немесе бос. Кемінде бір пән қалтасын қосыңыз.")
        return

    subject_folder = st.selectbox("Пән қалтасын таңдаңыз (books/)", local_subjects, key="summary_local_subject")

    files = list_files_in_subject_folder(subject_folder)
    if not files:
        st.info("Бұл пән қалтасында файлдар табылмады (.txt, .pdf, .epub).")
        return

    file_choice = st.selectbox("Кітап файлын таңдаңыз", files, key="summary_local_file")

    # Use folder name as subject; book_title is filename
    subject = subject_folder
    book_title = file_choice

    # Check if summary exists
    content, created_at, meta = fetch_summary(supabase, subject, book_title)

    # Actions row
    colA, colB = st.columns([1, 1])
    with colA:
        if not content:
            if st.button("Create Summary", key="create_summary_btn"):
                full_text = read_text_from_file(subject_folder, file_choice)
                if not full_text:
                    st.error("Файлдан мәтін оқылмады.")
                    return
                with st.spinner("Индекстеу және суммари құрастыру..."):
                    ok_ing, msg_ing, _ = ingest_book_to_vector_chunks(supabase, subject, book_title, full_text)
                    if not ok_ing:
                        st.error(msg_ing)
                        return
                    ok_sum, msg_sum = build_and_save_summary(supabase, subject, book_title, book_title)
                    if ok_sum:
                        st.success(msg_sum)
                        st.rerun()
                    else:
                        st.error(msg_sum)
        else:
            st.success("Суммари бар.")
    with colB:
        if content:
            if st.button("Regenerate", key="regen_summary_btn"):
                full_text = read_text_from_file(subject_folder, file_choice)
                if not full_text:
                    st.error("Файлдан мәтін оқылмады.")
                    return
                with st.spinner("Қайта индекстеу және суммари құрастыру..."):
                    # Optional: clear previous vectors for this file+subject to avoid duplicates
                    try:
                        supabase.table("vector_chunks").delete().eq("subject", subject).eq("filename", book_title).execute()
                    except Exception:
                        pass
                    ok_ing, msg_ing, _ = ingest_book_to_vector_chunks(supabase, subject, book_title, full_text)
                    if not ok_ing:
                        st.error(msg_ing)
                        return
                    ok_sum, msg_sum = build_and_save_summary(supabase, subject, book_title, book_title)
                    if ok_sum:
                        st.success(msg_sum)
                        st.rerun()
                    else:
                        st.error(msg_sum)

    # Render
    content, created_at, meta = fetch_summary(supabase, subject, book_title)
    if content:
        st.markdown(
            f"<div class='summary-container'><span class='summary-badge'>📌 {subject}</span><span class='summary-badge'>📚 {book_title}</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(f"<div class='summary-container'>{content}</div>", unsafe_allow_html=True)
        show_sources = st.checkbox("Show sources (chunks)", value=False, key="summary_show_sources")
        if show_sources and meta and isinstance(meta.get("chunk_ids_used"), list):
            chunks_map = fetch_chunks_by_ids(supabase, [cid for cid in meta["chunk_ids_used"] if isinstance(cid, str)])
            with st.expander("Sources", expanded=False):
                for cid in meta["chunk_ids_used"]:
                    if not isinstance(cid, str):
                        continue
                    row = chunks_map.get(cid)
                    if not row:
                        continue
                    st.markdown(f"**{cid}** — `{row.get('filename','')}`")
                    st.markdown(f"> {row.get('text','')}")
