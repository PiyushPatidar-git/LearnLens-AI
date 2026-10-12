import json, random, re, time
from .db import conn
from .text import stoks, tokens, embed
from . import llm
# ---------- Learner model: Bayesian Knowledge Tracing (simplified) ----------
P_INIT, P_LEARN, P_SLIP, P_GUESS = 0.30, 0.15, 0.10, 0.25
def bkt_update(p, correct, seconds=None):
    slip = P_SLIP + (0.1 if seconds is not None and seconds < 3 else 0)   # very fast wrong answers are more likely slips/guesses
    if correct: post = p * (1 - slip) / (p * (1 - slip) + (1 - p) * P_GUESS)
    else:       post = p * slip / (p * slip + (1 - p) * (1 - P_GUESS))
    return post + (1 - post) * P_LEARN
def get_mastery(user_id, course_id):
    return {r["topic"]: dict(p=r["p"], attempts=r["attempts"]) for r in conn().execute(
        "select * from mastery where user_id=? and course_id=?", (user_id, course_id))}
def record(c, user_id, course_id, topic, correct, seconds):
    r = c.execute("select p from mastery where user_id=? and course_id=? and topic=?", (user_id, course_id, topic)).fetchone()
    p = bkt_update(r["p"] if r else P_INIT, correct, seconds)
    c.execute("insert into mastery values(?,?,?,?,1,?) on conflict(user_id,course_id,topic) do update set p=?, attempts=attempts+1, updated=?",
              (user_id, course_id, topic, p, time.time(), p, time.time()))
    return p
def pick_difficulty(p, recent):
    """Explainable rule: mastery band, nudged by last-3 streak."""
    lvl = 0 if p < .4 else 1 if p < .7 else 2
    if len(recent) >= 3 and all(recent[-3:]): lvl = min(2, lvl + 1)
    if len(recent) >= 3 and not any(recent[-3:]): lvl = max(0, lvl - 1)
    return ["easy", "medium", "hard"][lvl]
# ---------- Generation ----------
def _sentences(t): return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", t) if 40 < len(s.strip()) < 260 and not s.startswith("[Figure")]
def _key_terms(sent, allterms):
    return [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", sent) if w.lower() in allterms]
def generate_template(chunk, pool_terms, difficulty, rnd):
    """Deterministic fallback: cloze MCQ built from a source sentence."""
    sents = _sentences(chunk["text"]); rnd.shuffle(sents)
    for s in sents:
        terms = _key_terms(s, pool_terms)
        if not terms: continue
        ans = max(terms, key=len) if difficulty != "easy" else terms[0]
        distract = [t for t in pool_terms if t.lower() != ans.lower() and t not in s]
        if len(distract) < 3: continue
        rnd.shuffle(distract); opts = [ans] + [d.title() if ans[0].isupper() else d for d in distract[:3]]; rnd.shuffle(opts)
        return dict(question=f"Fill in the blank from your course material: \"{s.replace(ans, '_____', 1)}\"", type="mcq", options=opts,
                    correct_answer=ans, explanation=f"The source states: \"{s}\"",
                    misconception=f"Confused '{ans}' with a related term from this topic.", evidence=s)
    return None
def generate_llm(chunk, difficulty, qtype):
    return llm.complete_json("You write exam questions strictly grounded in the excerpt.",
        f"Excerpt:\n{chunk['text'][:1500]}\nWrite one {difficulty} {qtype} question answerable ONLY from the excerpt. JSON keys: question, type, options (list, for mcq), "
        "correct_answer, explanation, misconception (what wrong belief a typical wrong answer reveals), evidence (verbatim sentence from excerpt).", 700)
# ---------- Verification ----------
def verify(q, chunk, existing_texts, sim_thresh=0.85):
    """Returns (ok, reason). Rule-based + independent LLM check when available."""
    ev = (q.get("evidence") or "")
    if not q.get("correct_answer") or not q.get("question"): return False, "malformed"
    if ev and ev.strip()[:60].lower() not in chunk["text"].lower(): return False, "evidence not found in source"
    if q["type"] == "mcq":
        o = q.get("options") or []
        if len(o) < 3 or len(set(x.lower() for x in o)) != len(o): return False, "bad options"
        if q["correct_answer"] not in o: return False, "answer not in options"
    elif not (set(stoks(q["correct_answer"])) & set(stoks(chunk["text"]))): return False, "answer unsupported by source"
    ev_ = embed(q["question"])
    for t in existing_texts:
        if float(embed(t) @ ev_) > sim_thresh: return False, "duplicate/near-duplicate"
    if llm.available():
        v = llm.complete_json("You independently verify exam questions.",
            f"Excerpt:\n{chunk['text'][:1500]}\nQuestion: {q['question']}\nOptions: {q.get('options')}\nClaimed answer: {q['correct_answer']}\n"
            'Solve it yourself from the excerpt. JSON: {"your_answer": str, "supported": bool, "unambiguous": bool}', 300)
        if v is not None and not (v.get("supported") and v.get("unambiguous") and
                                  str(v.get("your_answer", "")).strip().lower() == str(q["correct_answer"]).strip().lower()):
            return False, "independent verifier disagreed"
    return True, "ok"
def generate_assessment(user_id, course_id, n=5, topic=None, difficulty=None, qtype="mcq", seed=None):
    c = conn(); rnd = random.Random(seed)
    chunks = c.execute("""select c.*, s.name source_name from source_chunks c join sources s on s.id=c.source_id
                          where c.course_id=? and length(c.text)>80 """ + ("and c.topic=?" if topic else ""),
                       (course_id, topic) if topic else (course_id,)).fetchall()
    if not chunks: return dict(questions=[], rejected=[], error="no source material")
    pool = {w.lower() for ch in chunks for w in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", ch["text"]) if w.lower() not in __import__("app.text", fromlist=["STOP"]).STOP}
    hist = [r["text"] for r in c.execute("select text from questions where user_id=? and course_id=?", (user_id, course_id))]
    mastery = get_mastery(user_id, course_id); out, rejected, gen_total = [], [], 0
    topics = sorted({ch["topic"] for ch in chunks}, key=lambda t: mastery.get(t, dict(p=P_INIT))["p"])  # weakest first
    tries = 0
    while len(out) < n and tries < n * 12:
        tries += 1
        t = topics[len(out) % len(topics)] if not topic else topic
        cand = [ch for ch in chunks if ch["topic"] == t]; ch = rnd.choice(cand)
        diff = difficulty or pick_difficulty(mastery.get(t, dict(p=P_INIT))["p"], [])
        q = generate_llm(ch, diff, qtype) if llm.available() else None
        q = q or (generate_template(ch, pool, diff, rnd) if qtype in ("mcq", "mixed") else None)
        if not q: continue
        gen_total += 1; q.setdefault("type", "mcq")
        ok, why = verify(q, ch, hist + [x["question"] for x in out])
        if not ok: rejected.append(dict(question=q.get("question"), reason=why)); continue
        loc = dict(file=ch["source_name"], page=ch["page"], slide=ch["slide"], start_s=ch["start_s"])
        q.update(difficulty=diff, topic=t, concept=ch["concept"], source={k: v for k, v in loc.items() if v is not None}, chunk_id=ch["id"])
        cur = c.execute("insert into questions(user_id,course_id,chunk_id,topic,concept,type,difficulty,text,options,correct,explanation,misconception,created) values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, course_id, ch["id"], t, ch["concept"], q["type"], diff, q["question"], json.dumps(q.get("options")), q["correct_answer"], q["explanation"], q.get("misconception"), time.time()))
        q["id"] = cur.lastrowid; out.append(q)
    a = c.execute("insert into assessments(user_id,course_id,kind,created) values(?,?,?,?)", (user_id, course_id, qtype, time.time())).lastrowid
    c.commit()
    return dict(assessment_id=a, questions=[{k: v for k, v in q.items() if k not in ("correct_answer", "explanation", "misconception", "evidence")} for q in out],
                rejected=rejected, generated=gen_total, accepted=len(out))
def submit(user_id, course_id, assessment_id, answers):
    """answers: [{question_id, answer, seconds}]"""
    c = conn(); per_topic, results, mis, correct_n = {}, [], [], 0
    for a in answers:
        q = c.execute("select * from questions where id=? and user_id=?", (a["question_id"], user_id)).fetchone()
        if not q: continue
        ok = str(a["answer"]).strip().lower() == q["correct"].strip().lower(); correct_n += ok
        record(c, user_id, course_id, q["topic"], ok, a.get("seconds"))
        c.execute("insert into question_attempts(user_id,question_id,assessment_id,answer,correct,seconds,created) values(?,?,?,?,?,?,?)",
                  (user_id, q["id"], assessment_id, a["answer"], int(ok), a.get("seconds"), time.time()))
        per_topic.setdefault(q["topic"], []).append(ok)
        if not ok and q["misconception"]:
            c.execute("insert into misconceptions(user_id,course_id,topic,note,created) values(?,?,?,?,?)", (user_id, course_id, q["topic"], q["misconception"], time.time()))
            mis.append(dict(topic=q["topic"], note=q["misconception"]))
        results.append(dict(question_id=q["id"], correct=bool(ok), correct_answer=q["correct"], explanation=q["explanation"]))
    score = correct_n / max(1, len(results))
    c.execute("update assessments set score=? where id=?", (score, assessment_id)); c.commit()
    weak = [t for t, v in per_topic.items() if sum(v) / len(v) < .6]
    recs = []
    for t in weak:
        ch = c.execute("select c.*, s.name source_name from source_chunks c join sources s on s.id=c.source_id where c.course_id=? and c.topic=? limit 1", (course_id, t)).fetchone()
        if ch: recs.append(dict(topic=t, action="Review", source=dict(file=ch["source_name"], page=ch["page"], slide=ch["slide"], start_s=ch["start_s"], source_id=ch["source_id"])))
    return dict(score=score, topic_performance={t: sum(v) / len(v) for t, v in per_topic.items()}, weak_topics=weak,
                likely_misconceptions=mis, recommendations=recs, results=results, mastery=get_mastery(user_id, course_id))
def repetition_rate(user_id, course_id):
    ts = [r["text"] for r in conn().execute("select text from questions where user_id=? and course_id=?", (user_id, course_id))]
    return 0.0 if not ts else 1 - len(set(t.strip().lower() for t in ts)) / len(ts)
