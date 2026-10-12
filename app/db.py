import sqlite3, os, json
DB_PATH = os.environ.get("DB_PATH", "learnlens.db")
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, pw_hash TEXT, created REAL);
CREATE TABLE IF NOT EXISTS courses(id INTEGER PRIMARY KEY, user_id INT, name TEXT);
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, course_id INT, name TEXT, type TEXT, path TEXT, status TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS source_chunks(id INTEGER PRIMARY KEY, source_id INT, course_id INT, text TEXT, locator_kind TEXT,
  page INT, slide INT, start_s REAL, end_s REAL, topic TEXT, subtopic TEXT, concept TEXT);
CREATE TABLE IF NOT EXISTS prerequisites(id INTEGER PRIMARY KEY, course_id INT, topic TEXT, requires TEXT);
CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY, user_id INT, course_id INT, created REAL);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, conversation_id INT, role TEXT, content TEXT, mode TEXT, citations TEXT, created REAL);
CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, user_id INT, course_id INT, chunk_id INT, topic TEXT, concept TEXT,
  type TEXT, difficulty TEXT, text TEXT, options TEXT, correct TEXT, explanation TEXT, misconception TEXT, created REAL);
CREATE TABLE IF NOT EXISTS question_attempts(id INTEGER PRIMARY KEY, user_id INT, question_id INT, assessment_id INT, answer TEXT,
  correct INT, seconds REAL, created REAL);
CREATE TABLE IF NOT EXISTS assessments(id INTEGER PRIMARY KEY, user_id INT, course_id INT, kind TEXT, score REAL, created REAL);
CREATE TABLE IF NOT EXISTS mastery(user_id INT, course_id INT, topic TEXT, p REAL, attempts INT, updated REAL, PRIMARY KEY(user_id,course_id,topic));
CREATE TABLE IF NOT EXISTS misconceptions(id INTEGER PRIMARY KEY, user_id INT, course_id INT, topic TEXT, note TEXT, created REAL);
CREATE TABLE IF NOT EXISTS evaluation_runs(id INTEGER PRIMARY KEY, kind TEXT, results TEXT, created REAL);
CREATE INDEX IF NOT EXISTS ix_chunks_course ON source_chunks(course_id);
"""
def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False); c.row_factory = sqlite3.Row; return c
def init():
    c = conn(); c.executescript(SCHEMA); c.commit(); c.close()
