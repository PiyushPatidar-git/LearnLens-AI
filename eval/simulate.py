"""Simulated-student personalization eval. Requires an ingested course. Writes results ONLY after real execution:
python -m eval.simulate <course_id>   (creates throwaway users; answers drawn from a per-profile accuracy by topic)"""
import sys, random, json
from app import assess
from app.db import conn, init
PROFILES = {"A": lambda t, i: .85 if i == 0 else .3, "B": lambda t, i: .35 if i == 1 else .7, "C": lambda t, i: .3}
def run(course_id, rounds=4, n=5):
    init(); c = conn(); topics = [r["topic"] for r in c.execute("select distinct topic from source_chunks where course_id=?", (course_id,))]
    out = {}
    for name, acc in PROFILES.items():
        uid = c.execute("insert into users(email,name,pw_hash,created) values(?,?,?,0)", (f"sim{name}{random.random()}@x", f"Sim {name}", "x")).lastrowid; c.commit(); rnd = random.Random(7)
        for _ in range(rounds):
            a = assess.generate_assessment(uid, course_id, n)
            ans = []
            for q in a["questions"]:
                row = c.execute("select correct,options from questions where id=?", (q["id"],)).fetchone()
                ok = rnd.random() < acc(q["topic"], topics.index(q["topic"]))
                wrong = [o for o in json.loads(row["options"] or "[]") if o != row["correct"]] or ["?"]
                ans.append(dict(question_id=q["id"], answer=row["correct"] if ok else wrong[0], seconds=10))
            assess.submit(uid, course_id, a["assessment_id"], ans)
        out[name] = dict(final_mastery={t: round(v["p"], 3) for t, v in assess.get_mastery(uid, course_id).items()},
                         repetition_rate=assess.repetition_rate(uid, course_id), note="Initial mastery = BKT prior 0.30 for every topic")
    return out
if __name__ == "__main__": print(json.dumps(run(int(sys.argv[1])), indent=2))
