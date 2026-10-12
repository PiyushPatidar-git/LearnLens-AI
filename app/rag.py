import os, re, numpy as np
from .db import conn
from .text import BM25, embed, stoks, tokens, hms
from . import llm
THRESH = float(os.environ.get("OUT_OF_SCOPE_THRESHOLD", 0.45))
def citation_label(r):
    n = r["source_name"]
    if r["locator_kind"] == "page": return f"{n} — Page {r['page']}"
    if r["locator_kind"] == "slide": return f"{n} — Slide {r['slide']}"
    return f"{n} — {hms(r['start_s'])}"
def load(course_id):
    return conn().execute("""select c.*, s.name source_name, s.type source_type from source_chunks c join sources s on s.id=c.source_id
                             where c.course_id=?""", (course_id,)).fetchall()
def retrieve(course_id, query, k=5, topic=None):
    rows = load(course_id)
    if topic: rows = [r for r in rows if r["topic"] == topic] or rows          # metadata filter
    if not rows: return [], 0.0
    bm = np.array(BM25([r["text"] for r in rows]).scores(query))
    qv = embed(query); dn = np.array([float(embed(r["text"]) @ qv) for r in rows])
    # reciprocal-rank fusion of keyword + dense
    fused = np.zeros(len(rows))
    for arr in (bm, dn):
        for rank, i in enumerate(np.argsort(-arr)): fused[i] += 1 / (60 + rank)
    top = list(np.argsort(-fused)[:k * 2])
    # rerank: query-term coverage + dense similarity
    qs = set(stoks(query))
    def cov(i): return len(qs & set(stoks(rows[i]["text"]))) / max(1, len(qs))
    rer = sorted(top, key=lambda i: -(0.6 * cov(i) + 0.4 * dn[i]))[:k]
    best = max((0.6 * cov(i) + 0.4 * dn[i]) for i in rer) if rer else 0.0
    return [dict(r=rows[i], score=float(0.6 * cov(i) + 0.4 * dn[i])) for i in rer], float(best)
def _cite(hit, n): 
    r = hit["r"]
    return dict(n=n, label=citation_label(r), source_id=r["source_id"], type=r["source_type"], page=r["page"], slide=r["slide"],
                start_s=r["start_s"], chunk_id=r["id"], snippet=r["text"][:300])
def extractive(question, hits):
    qs = set(stoks(question)); sents = []
    for h in hits:
        for s in re.split(r"(?<=[.!?])\s+|\n", h["r"]["text"]):
            if len(s.strip()) > 25: sents.append((len(qs & set(stoks(s))), s.strip(), h))
    sents.sort(key=lambda x: -x[0]); pick = sents[:4]
    return " ".join(f"{s} [{hits.index(h)+1}]" for _, s, h in pick)
def style_for(p):
    if p is None: return "Use a balanced explanation."
    if p < .4: return "Student is weak on this topic: beginner-friendly, define terms, use a simple example, then a check question."
    if p > .8: return "Student is strong: be concise, add an exam-level or application-style extension."
    return "Student is developing: explain clearly and highlight common pitfalls."
def validate_citations(answer, n):
    used = {int(x) for x in re.findall(r"\[(\d+)\]", answer)}
    return answer if used and used <= set(range(1, n + 1)) else None
def answer(course_id, question, mastery_p=None, language="English", allow_outside=False, topic=None):
    hits, best = retrieve(course_id, question, topic=topic)
    if best < THRESH:
        if allow_outside:
            t = llm.complete("Answer briefly from general knowledge.", question, 500) or "Outside-knowledge mode needs an LLM key (ANTHROPIC_API_KEY)."
            return dict(mode="outside", text=t, citations=[], confidence=best)
        return dict(mode="refused", confidence=best, citations=[], text=(
            "I couldn't find this information in your uploaded course material.\n\nI can either:\n"
            "1. Answer only from your course material, or\n2. Provide an outside-knowledge answer clearly labeled as such."))
    ctx = "\n\n".join(f"[{i+1}] ({citation_label(h['r'])})\n{h['r']['text'][:900]}" for i, h in enumerate(hits))
    sys = ("You are a tutor that teaches ONLY from the provided course excerpts. Cite with [n] after each claim. "
           "If excerpts don't support something, say so. " + style_for(mastery_p) +
           (f" Reply in {language}, keeping technical terms in English." if language != "English" else ""))
    out = llm.complete(sys, f"Question: {question}\n\nExcerpts:\n{ctx}", 900)
    out = validate_citations(out, len(hits)) if out else None
    if not out: out = extractive(question, hits)  # fallback: extractive answer, always citation-valid
    return dict(mode="source", text=out, confidence=best, citations=[_cite(h, i + 1) for i, h in enumerate(hits)])
