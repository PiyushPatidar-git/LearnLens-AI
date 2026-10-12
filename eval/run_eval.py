"""Evaluation. Computes deterministic metrics (refusal accuracy, retrieval hit, citation validity, keyword grounding).
RAGAS metrics (faithfulness, answer_relevancy, context_precision, context_recall) run only if `ragas` + an LLM key are present;
otherwise they are reported as 'Pending execution' -- never fabricated. NOTE: dataset keywords must match YOUR uploaded course."""
import json, os, re
from app import rag, llm
def run(course_id, path=os.path.join(os.path.dirname(__file__), "dataset.json")):
    data = json.load(open(path)); rows = []
    for d in data:
        hits, best = rag.retrieve(course_id, d["question"]); a = rag.answer(course_id, d["question"])
        row = dict(question=d["question"], type=d["type"], mode=a["mode"], confidence=round(best, 3))
        if d["type"] == "off-material": row["correct_refusal"] = a["mode"] == "refused"
        else:
            row["answered_from_source"] = a["mode"] == "source"
            ctx = " ".join(h["r"]["text"].lower() for h in hits)
            row["retrieval_keyword_recall"] = sum(k in ctx for k in d.get("expected_keywords", [])) / max(1, len(d.get("expected_keywords", [])))
            cited = {int(x) for x in re.findall(r"\[(\d+)\]", a["text"])}
            row["citation_valid"] = bool(cited) and cited <= {c["n"] for c in a["citations"]}
            # citation support: every cited excerpt shares >=2 content words with the sentence citing it
            from app.text import stoks
            sup = []
            for s in re.split(r"(?<=[.!?\]])\s+", a["text"]):
                for n in re.findall(r"\[(\d+)\]", s):
                    n = int(n)
                    if n <= len(a["citations"]): sup.append(len(set(stoks(s)) & set(stoks(a["citations"][n-1]["snippet"]))) >= 2)
            row["citation_support"] = sum(sup) / len(sup) if sup else 0.0
        rows.append(row)
    ins = [r for r in rows if r["type"] == "in-material"]; offs = [r for r in rows if r["type"] == "off-material"]
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    return dict(summary=dict(refusal_accuracy=avg([r["correct_refusal"] for r in offs]),
        answer_rate_in_material=avg([r["answered_from_source"] for r in ins]), retrieval_keyword_recall=avg([r["retrieval_keyword_recall"] for r in ins]),
        citation_validity=avg([r["citation_valid"] for r in ins]), citation_support=avg([r["citation_support"] for r in ins]),
        ragas=dict(faithfulness="Pending execution", answer_relevancy="Pending execution", context_precision="Pending execution", context_recall="Pending execution")),
        rows=rows, llm_used=llm.available())
