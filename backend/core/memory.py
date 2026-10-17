import os
import sqlite3
import json
import logging
import datetime
import numpy as np
from typing import List, Dict, Any, Tuple
from backend.core.llm import LLMClient

logger = logging.getLogger("sovereign.memory")

class LocalMemory:
    def __init__(self, db_path: str = "~/sovereign_agent/backend/sovereign.db", llm_client: LLMClient = None):
        self.db_path = db_path
        self.llm = llm_client or LLMClient()
        self._init_db()

    def _get_conn(self):
        # Create directories if they do not exist
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes SQLite schema for session tracking and vector storage."""
        with self._get_conn() as conn:
            # 1. Sessions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    task TEXT,
                    checklist TEXT,
                    current_step INTEGER,
                    state TEXT,
                    created_at TEXT
                )
            """)
            # 2. Steps table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    step_index INTEGER,
                    thought TEXT,
                    action TEXT,
                    args TEXT,
                    observation TEXT,
                    reflection TEXT,
                    timestamp TEXT
                )
            """)
            # 3. Vector memory table (using plain JSON arrays for float storage)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vector_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT,
                    embedding TEXT,
                    source TEXT,
                    timestamp TEXT
                )
            """)
            conn.commit()
        logger.info(f"Initialized SQLite database at {self.db_path}")

    # --- Session State Persistence ---
    
    def save_session(self, session_id: str, task: str, checklist: List[str], current_step: int, state: str):
        """Saves current state of the agent session."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO sessions (session_id, task, checklist, current_step, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    task=excluded.task,
                    checklist=excluded.checklist,
                    current_step=excluded.current_step,
                    state=excluded.state
            """, (
                session_id,
                task,
                json.dumps(checklist),
                current_step,
                state,
                datetime.datetime.now().isoformat()
            ))
            conn.commit()

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Loads a session's details from the database."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row:
                return {
                    "session_id": row["session_id"],
                    "task": row["task"],
                    "checklist": json.loads(row["checklist"]),
                    "current_step": row["current_step"],
                    "state": row["state"],
                    "created_at": row["created_at"]
                }
        return None

    def save_step(self, session_id: str, step_index: int, thought: str, action: str, args: Dict[str, Any], observation: str, reflection: str):
        """Logs a single ReAct step's detail."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO steps (session_id, step_index, thought, action, args, observation, reflection, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                step_index,
                thought,
                action,
                json.dumps(args),
                observation,
                reflection,
                datetime.datetime.now().isoformat()
            ))
            conn.commit()

    def get_steps(self, session_id: str) -> List[Dict[str, Any]]:
        """Returns history of steps executed in a session."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM steps WHERE session_id = ? ORDER BY step_index ASC", (session_id,)).fetchall()
            return [dict(r) for r in rows]

    # --- Vector RAG Memory ---

    async def add_document(self, text: str, source: str = "user_input") -> bool:
        """Embeds text locally via nomic-embed-text and inserts into vector DB."""
        embedding = await self.llm.get_embedding(text)
        if not embedding:
            logger.warning(f"Could not generate embedding for text: '{text[:50]}...'")
            return False

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO vector_memory (text, embedding, source, timestamp)
                VALUES (?, ?, ?, ?)
            """, (
                text,
                json.dumps(embedding),
                source,
                datetime.datetime.now().isoformat()
            ))
            conn.commit()
        logger.info(f"Saved document to offline vector memory. Source: {source}")
        return True

    async def search(self, query: str, limit: int = 3) -> List[Tuple[Dict[str, Any], float]]:
        """
        Runs local semantic search:
        1. Embeds query locally.
        2. Pulls stored document vectors from SQLite.
        3. Computes Cosine Similarity in Python using numpy.
        """
        query_vector = await self.llm.get_embedding(query)
        if not query_vector:
            logger.warning(f"Could not generate query embedding for: '{query}'")
            return []

        # Read all documents
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM vector_memory").fetchall()

        if not rows:
            return []

        results = []
        q_v = np.array(query_vector)
        q_norm = np.linalg.norm(q_v)

        if q_norm == 0:
            return []

        for row in rows:
            doc_id = row["id"]
            doc_text = row["text"]
            doc_source = row["source"]
            doc_timestamp = row["timestamp"]
            
            try:
                doc_vector = np.array(json.loads(row["embedding"]))
            except Exception:
                continue

            doc_norm = np.linalg.norm(doc_vector)
            if doc_norm == 0:
                continue

            # Compute Cosine Similarity: A . B / (|A| * |B|)
            similarity = np.dot(q_v, doc_vector) / (q_norm * doc_norm)
            
            results.append(({
                "id": doc_id,
                "text": doc_text,
                "source": doc_source,
                "timestamp": doc_timestamp
            }, float(similarity)))

        # Sort by similarity score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]
