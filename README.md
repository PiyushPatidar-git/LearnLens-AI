# LearnLens AI — Track D prototype
Your course. Your knowledge base. Your personalized AI tutor.

## Run
```
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env   # set JWT_SECRET; optionally ANTHROPIC_API_KEY
export $(grep -v '^#' .env | xargs) && uvicorn app.main:app --reload
```
Open http://localhost:8000 → register → create course → upload → ask / practice.

## Works with and without an LLM key
No key: extractive cited answers, template cloze MCQs, heuristic topics. With key: LLM answers (citation-validated, falls back to extractive if citations invalid), LLM question generation + independent verifier, VLM figure captions, LLM metadata/prereq extraction.

## Design choices
- **Retrieval**: BM25 + hashed dense vectors → reciprocal-rank fusion → query-coverage rerank; metadata topic filter. Dense part is an offline stand-in; swap `text.embed` for a real embedding model (+ pgvector/Qdrant).
- **Out-of-scope**: best reranked score < `OUT_OF_SCOPE_THRESHOLD` (default 0.45, tune on your data) → refusal with the two-option prompt; outside answers are labeled OUTSIDE KNOWLEDGE.
- **Learner model**: simplified Bayesian Knowledge Tracing per topic (P0 .30, learn .15, slip .10, guess .25). Chosen over IRT/DKT because it is explainable with 4 parameters and works with few attempts. Difficulty: mastery <40% easy, <70% medium, else hard, nudged by last-3 streak.
- **Question QA**: evidence-in-source check, option sanity, answer-in-options, embedding-similarity duplicate check against history, independent LLM solve (when key present). Failures are rejected and counted.

## Status (honest)
Implemented: auth, courses, upload+ingestion (PDF/PPTX/DOCX/image/video), RAG, tutor with citations & click-through, MCQ generation/verification/novelty, BKT, report, recommendations, knowledge map, eval harness.
**Not implemented yet**: Alembic migrations (schema is `app/db.py`), Postgres/pgvector, true cross-encoder reranker, short-answer/numerical grading, diagnostic flow UI, flashcards/notes/audio brief, study planner, voice, React/TS frontend (current UI is dependency-free HTML).
**Evaluation results: Pending execution.** Run `POST /api/evaluation/run?course_id=N` and `python -m eval.simulate N`; edit `eval/dataset.json` keywords to match your course. RAGAS metrics stay "Pending execution" until wired to an LLM judge.
