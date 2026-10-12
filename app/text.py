import re, math, hashlib
from collections import Counter
import numpy as np
STOP = set("a an the of to in on for and or is are was were be been it its this that these those with as by at from into than then so such can will which what who whom how why when where does do did not no yes if else each per via vs also may might should would could their there they them we you your our i about over under between within".split())
def tokens(t): return [w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP and len(w) > 1]
def stem(w):
    for s in ("ing", "ed", "es", "s"):
        if w.endswith(s) and len(w) - len(s) >= 3: return w[:-len(s)]
    return w
def stoks(t): return [stem(w) for w in tokens(t)]
DIM = 512
def embed(t):
    """Hashed word + char-trigram embedding. Offline fallback for a neural embedding model (swap in bge/OpenAI/Voyage here)."""
    v = np.zeros(DIM, dtype=np.float32)
    for w in stoks(t):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
        for i in range(len(w) - 2):
            v[int(hashlib.md5(("#" + w[i:i+3]).encode()).hexdigest(), 16) % DIM] += 0.3
    n = np.linalg.norm(v); return v / n if n else v
class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.docs = [stoks(d) for d in docs]; self.k1, self.b = k1, b
        self.N = len(docs); self.avg = sum(map(len, self.docs)) / max(1, self.N)
        self.df = Counter(w for d in self.docs for w in set(d))
    def idf(self, w): return math.log(1 + (self.N - self.df.get(w, 0) + .5) / (self.df.get(w, 0) + .5))
    def scores(self, q):
        qs = stoks(q); out = []
        for d in self.docs:
            tf = Counter(d); s = 0
            for w in qs:
                if w in tf: s += self.idf(w) * tf[w] * (self.k1 + 1) / (tf[w] + self.k1 * (1 - self.b + self.b * len(d) / (self.avg or 1)))
            out.append(s)
        return out
def keyphrase(text, k=2):
    c = Counter(stoks(text)); return " ".join(w for w, _ in c.most_common(k)).title() or "General"
def hms(s):
    s = int(s or 0); return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"
