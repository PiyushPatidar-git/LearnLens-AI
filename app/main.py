import os, time, json, hashlib, hmac, secrets, collections
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import jwt
from .db import conn, init
from . import ingest, rag, assess
SECRET = os.environ.get("JWT_SECRET") or secrets.token_hex(32)   # set JWT_SECRET in prod so tokens survive restarts
MAX_MB = int(os.environ.get("MAX_UPLOAD_MB", 500)); UP = os.environ.get("UPLOAD_DIR", "uploads"); os.makedirs(UP, exist_ok=True)
app = FastAPI(title="LearnLens AI"); init()
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:8000").split(","), allow_methods=["*"], allow_headers=["*"])
_hits = collections.defaultdict(list)
@app.middleware("http")
async def limiter(req: Request, call_next):          # naive per-IP limiter: 120 req/min
    ip = req.client.host if req.client else "x"; now = time.time()
    _hits[ip] = [t for t in _hits[ip] if now - t < 60]
    if len(_hits[ip]) >= 120: raise HTTPException(429, "Too many requests")
    _hits[ip].append(now); return await call_next(req)
def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8); return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
def check_pw(pw, h): s = h.split("$")[0]; return hmac.compare_digest(hash_pw(pw, s), h)
def user(req: Request):
    tok = (req.headers.get("authorization") or req.query_params.get("token") or "").replace("Bearer ", "")
    try: return jwt.decode(tok, SECRET, algorithms=["HS256"])["uid"]
    except Exception: raise HTTPException(401, "Invalid or missing token")
def own_course(uid, cid):
    if not conn().execute("select 1 from courses where id=? and user_id=?", (cid, uid)).fetchone(): raise HTTPException(404, "Course not found")
class Reg(BaseModel): email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"); name: str = Field(min_length=1, max_length=80); password: str = Field(min_length=8, max_length=128)
class Login(BaseModel): email: str; password: str
@app.post("/api/auth/register")
def register(b: Reg):
    c = conn()
    try: uid = c.execute("insert into users(email,name,pw_hash,created) values(?,?,?,?)", (b.email.lower(), b.name, hash_pw(b.password), time.time())).lastrowid
    except Exception: raise HTTPException(409, "Email already registered")
    c.commit(); return dict(token=jwt.encode({"uid": uid, "exp": time.time() + 86400 * 7}, SECRET), name=b.name)
@app.post("/api/auth/login")
def login(b: Login):
    r = conn().execute("select * from users where email=?", (b.email.lower(),)).fetchone()
    if not r or not check_pw(b.password, r["pw_hash"]): raise HTTPException(401, "Wrong email or password")
    return dict(token=jwt.encode({"uid": r["id"], "exp": time.time() + 86400 * 7}, SECRET), name=r["name"])
class CourseIn(BaseModel): name: str = Field(min_length=1, max_length=120)
@app.post("/api/courses")
def new_course(b: CourseIn, uid=Depends(user)):
    c = conn(); i = c.execute("insert into courses(user_id,name) values(?,?)", (uid, b.name)).lastrowid; c.commit(); return dict(id=i, name=b.name)
@app.get("/api/courses")
def courses(uid=Depends(user)): return [dict(r) for r in conn().execute("select * from courses where user_id=?", (uid,))]
@app.post("/api/sources/upload")
async def upload(bg: BackgroundTasks, course_id: int = Form(...), file: UploadFile = File(...), uid=Depends(user)):
    own_course(uid, course_id); typ = ingest.detect(file.filename or "")
    if not typ: raise HTTPException(415, "Unsupported file type")
    path = os.path.join(UP, secrets.token_hex(8) + os.path.splitext(file.filename)[1].lower()); size = 0
    with open(path, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_MB << 20: f.close(); os.remove(path); raise HTTPException(413, f"File exceeds {MAX_MB} MB")
            f.write(chunk)
    c = conn(); sid = c.execute("insert into sources(course_id,name,type,path,status) values(?,?,?,?,'queued')", (course_id, os.path.basename(file.filename), typ, path)).lastrowid
    c.commit(); bg.add_task(ingest.run, sid); return dict(id=sid, status="queued")
@app.get("/api/sources")
def sources(course_id: int, uid=Depends(user)):
    own_course(uid, course_id); return [dict(id=r["id"], name=r["name"], type=r["type"], status=r["status"], error=r["error"]) for r in conn().execute("select * from sources where course_id=?", (course_id,))]
@app.get("/api/sources/{sid}")
def source(sid: int, uid=Depends(user)):
    r = conn().execute("select s.* from sources s join courses c on c.id=s.course_id where s.id=? and c.user_id=?", (sid, uid)).fetchone()
    if not r: raise HTTPException(404)
    return dict(id=r["id"], name=r["name"], type=r["type"], status=r["status"])
@app.get("/api/sources/{sid}/file")
def source_file(sid: int, uid=Depends(user)):
    r = conn().execute("select s.* from sources s join courses c on c.id=s.course_id where s.id=? and c.user_id=?", (sid, uid)).fetchone()
    if not r: raise HTTPException(404)
    return FileResponse(r["path"], filename=r["name"] if r["type"] not in ("pdf", "video") else None)
@app.get("/api/chunks/{cid}")
def chunk(cid: int, uid=Depends(user)):
    r = conn().execute("select k.* from source_chunks k join courses c on c.id=k.course_id where k.id=? and c.user_id=?", (cid, uid)).fetchone()
    if not r: raise HTTPException(404)
    return dict(r)
class ChatIn(BaseModel): course_id: int; message: str = Field(min_length=1, max_length=2000); conversation_id: int | None = None; language: str = "English"; allow_outside: bool = False
@app.post("/api/chat")
def chat(b: ChatIn, uid=Depends(user)):
    own_course(uid, b.course_id); c = conn()
    cid = b.conversation_id or c.execute("insert into conversations(user_id,course_id,created) values(?,?,?)", (uid, b.course_id, time.time())).lastrowid
    m = assess.get_mastery(uid, b.course_id); hits, _ = rag.retrieve(b.course_id, b.message, k=3)
    p = m.get(hits[0]["r"]["topic"], {}).get("p") if hits else None     # personalise by the topic being asked about
    res = rag.answer(b.course_id, b.message, p, b.language, b.allow_outside)
    c.execute("insert into messages(conversation_id,role,content,created) values(?,?,?,?)", (cid, "user", b.message, time.time()))
    c.execute("insert into messages(conversation_id,role,content,mode,citations,created) values(?,?,?,?,?,?)", (cid, "assistant", res["text"], res["mode"], json.dumps(res["citations"]), time.time()))
    c.commit(); return dict(conversation_id=cid, topic_mastery=p, **res)
@app.get("/api/chat/history")
def history(conversation_id: int, uid=Depends(user)):
    if not conn().execute("select 1 from conversations where id=? and user_id=?", (conversation_id, uid)).fetchone(): raise HTTPException(404)
    return [dict(r) for r in conn().execute("select role,content,mode,citations from messages where conversation_id=? order by id", (conversation_id,))]
class GenIn(BaseModel): course_id: int; n: int = Field(5, ge=1, le=20); topic: str | None = None; difficulty: str | None = None; type: str = "mcq"
@app.post("/api/assessments/generate")
def gen(b: GenIn, uid=Depends(user)):
    own_course(uid, b.course_id); return assess.generate_assessment(uid, b.course_id, b.n, b.topic, b.difficulty, b.type)
class Ans(BaseModel): question_id: int; answer: str; seconds: float | None = None
class SubIn(BaseModel): course_id: int; answers: list[Ans]
@app.post("/api/assessments/{aid}/submit")
def sub(aid: int, b: SubIn, uid=Depends(user)):
    own_course(uid, b.course_id); return assess.submit(uid, b.course_id, aid, [a.model_dump() for a in b.answers])
@app.get("/api/mastery")
def mastery(course_id: int, uid=Depends(user)):
    own_course(uid, course_id); m = assess.get_mastery(uid, course_id)
    return dict(topics=m, overall=(sum(v["p"] for v in m.values()) / len(m)) if m else None, repetition_rate=assess.repetition_rate(uid, course_id),
                questions_answered=conn().execute("select count(*) n from question_attempts where user_id=?", (uid,)).fetchone()["n"])
@app.get("/api/topics")
def topics(course_id: int, uid=Depends(user)):
    own_course(uid, course_id)
    return [dict(r) for r in conn().execute("select topic, count(*) chunks from source_chunks where course_id=? group by topic order by min(id)", (course_id,))]
@app.get("/api/knowledge-map")
def kmap(course_id: int, uid=Depends(user)):
    own_course(uid, course_id); m = assess.get_mastery(uid, course_id); c = conn()
    nodes = [dict(id=r["topic"], mastery=m.get(r["topic"], {}).get("p"), subtopics=[x["subtopic"] for x in c.execute("select distinct subtopic from source_chunks where course_id=? and topic=? limit 6", (course_id, r["topic"]))])
             for r in c.execute("select distinct topic from source_chunks where course_id=?", (course_id,))]
    return dict(nodes=nodes, edges=[dict(source=r["requires"], target=r["topic"]) for r in c.execute("select * from prerequisites where course_id=?", (course_id,))])
@app.get("/api/recommendations")
def recs(course_id: int, uid=Depends(user)):
    own_course(uid, course_id); m = assess.get_mastery(uid, course_id); c = conn(); out = []
    allt = [r["topic"] for r in c.execute("select distinct topic from source_chunks where course_id=?", (course_id,))]
    for t in sorted(allt, key=lambda t: m.get(t, dict(p=assess.P_INIT))["p"])[:5]:
        ch = c.execute("select k.*, s.name sn from source_chunks k join sources s on s.id=k.source_id where k.course_id=? and k.topic=? limit 1", (course_id, t)).fetchone()
        out.append(dict(topic=t, mastery=m.get(t, {}).get("p"), source=rag.citation_label(dict(source_name=ch["sn"], **{k: ch[k] for k in ch.keys()})), chunk_id=ch["id"], source_id=ch["source_id"], action="Review, then practice 5 questions"))
    return out
@app.post("/api/evaluation/run")
def run_eval(course_id: int, uid=Depends(user)):
    own_course(uid, course_id); from eval.run_eval import run
    res = run(course_id); c = conn(); c.execute("insert into evaluation_runs(kind,results,created) values('rag',?,?)", (json.dumps(res), time.time())); c.commit(); return res
@app.get("/api/evaluation/results")
def eval_results(uid=Depends(user)):
    r = conn().execute("select * from evaluation_runs order by id desc limit 1").fetchone()
    return json.loads(r["results"]) if r else dict(status="Pending execution")
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
