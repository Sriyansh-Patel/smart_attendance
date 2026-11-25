import json
import sqlite3
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from .config import (
    DB_PATH,
    DEVICE_SECRET_KEY,
    MONGODB_URI,
    MONGODB_DB_NAME,
    MONGODB_DATA_API_URL,
    MONGODB_DATA_API_KEY,
    MONGODB_DATA_SOURCE,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MongoDataAPIClient:
    """Lightweight HTTP client for MongoDB Atlas Data API."""

    def __init__(self, base_url: str, api_key: str, data_source: str, database: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.data_source = data_source
        self.database = database

    def _call(self, action: str, collection: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/action/{action}"
        headers = {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }
        body = {
            "dataSource": self.data_source,
            "database": self.database,
            "collection": collection,
            **payload,
        }
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def find_one(self, collection: str, filter_doc: Dict[str, Any], sort: Optional[Dict[str, int]] = None) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {"filter": filter_doc}
        if sort:
            # Data API findOne doesn't accept sort directly; use find with limit=1
            res = self._call("find", collection, {"filter": filter_doc, "sort": sort, "limit": 1})
            docs = res.get("documents", [])
            return docs[0] if docs else None
        return self._call("findOne", collection, payload).get("document")

    def find(self, collection: str, filter_doc: Optional[Dict[str, Any]] = None, sort: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"filter": filter_doc or {}}
        if sort:
            payload["sort"] = sort
        return self._call("find", collection, payload).get("documents", [])

    def update_one(self, collection: str, filter_doc: Dict[str, Any], update_doc: Dict[str, Any], upsert: bool = False):
        return self._call("updateOne", collection, {
            "filter": filter_doc,
            "update": update_doc,
            "upsert": upsert,
        })

    def update_many(self, collection: str, filter_doc: Dict[str, Any], update_doc: Dict[str, Any]):
        return self._call("updateMany", collection, {
            "filter": filter_doc,
            "update": update_doc,
        })

    def insert_one(self, collection: str, document: Dict[str, Any]):
        return self._call("insertOne", collection, {"document": document})


class DatabaseManager:
    """Unified database adapter supporting PyMongo, MongoDB Atlas Data API, and SQLite fallback."""

    def __init__(self):
        self.mode = "sqlite"
        self.mongo_client = None
        self.mongo_db = None
        self.data_api: Optional[MongoDataAPIClient] = None
        self.last_error: Optional[str] = None
        self._connect()

    def _connect(self):
        # 1. Try standard MongoDB URI (PyMongo / Atlas SRV / Local MongoDB)
        if MONGODB_URI:
            try:
                import certifi
                from pymongo import MongoClient
                kwargs: Dict[str, Any] = {"serverSelectionTimeoutMS": 5000}
                if MONGODB_URI.startswith("mongodb+srv://") or "tls=true" in MONGODB_URI.lower() or "ssl=true" in MONGODB_URI.lower():
                    kwargs["tlsCAFile"] = certifi.where()
                client = MongoClient(MONGODB_URI, **kwargs)
                client.admin.command("ping")
                self.mongo_client = client
                self.mongo_db = client[MONGODB_DB_NAME]
                self.mode = "mongodb"
                self.last_error = None
                print(f"[DB] Connected to MongoDB ({MONGODB_DB_NAME})")
                return
            except Exception as e:
                self.last_error = f"MongoDB URI connection failed: {e}"
                print(f"[DB] {self.last_error}. Checking Data API / SQLite fallback.")

        # 2. Try MongoDB Atlas Data API
        if MONGODB_DATA_API_URL and MONGODB_DATA_API_KEY:
            try:
                client = MongoDataAPIClient(
                    MONGODB_DATA_API_URL, MONGODB_DATA_API_KEY, MONGODB_DATA_SOURCE, MONGODB_DB_NAME
                )
                client.find("sessions", {})
                self.data_api = client
                self.mode = "mongodb_data_api"
                self.last_error = None
                print(f"[DB] Connected to MongoDB Atlas Data API ({MONGODB_DB_NAME})")
                return
            except Exception as e:
                self.last_error = f"MongoDB Data API connection failed: {e}"
                print(f"[DB] {self.last_error}. Falling back to SQLite.")

        # 3. Local SQLite Fallback
        self.mode = "sqlite"
        print(f"[DB] Using local SQLite database at {DB_PATH}")

    def _sqlite_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def get_status(self) -> Dict[str, Any]:
        return {
            "backend": self.mode,
            "connected": True,
            "database_name": MONGODB_DB_NAME if self.mode.startswith("mongodb") else "attendance.db",
            "mongodb_configured": bool(MONGODB_URI or (MONGODB_DATA_API_URL and MONGODB_DATA_API_KEY)),
            "warning": self.last_error,
        }

    def init_db(self):
        if self.mode == "mongodb":
            self.mongo_db.students.create_index("student_id", unique=True)
            self.mongo_db.sessions.create_index("session_id", unique=True)
            self.mongo_db.devices.create_index("device_key", unique=True)
            self.mongo_db.attendance.create_index([("session_id", 1), ("student_id", 1)], unique=True)
            if self.mongo_db.sessions.count_documents({}) == 0:
                self.mongo_db.sessions.insert_one({
                    "session_id": "SESSION_DEFAULT",
                    "course_name": "General Attendance Session",
                    "org_type": "general",
                    "created_at": _utc_now_iso(),
                    "is_active": 1,
                })
            if self.mongo_db.devices.count_documents({"device_key": DEVICE_SECRET_KEY}) == 0:
                self.mongo_db.devices.insert_one({
                    "device_key": DEVICE_SECRET_KEY,
                    "device_name": "ESP32-LED-Matrix",
                    "last_seen": None,
                    "is_active": 1,
                })
            return

        if self.mode == "mongodb_data_api":
            if not self.data_api.find_one("sessions", {}):
                self.data_api.insert_one("sessions", {
                    "session_id": "SESSION_DEFAULT",
                    "course_name": "General Attendance Session",
                    "org_type": "general",
                    "created_at": _utc_now_iso(),
                    "is_active": 1,
                })
            if not self.data_api.find_one("devices", {"device_key": DEVICE_SECRET_KEY}):
                self.data_api.insert_one("devices", {
                    "device_key": DEVICE_SECRET_KEY,
                    "device_name": "ESP32-LED-Matrix",
                    "last_seen": None,
                    "is_active": 1,
                })
            return

        # SQLite initialization
        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            student_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            department TEXT,
            embedding TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            course_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS otps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            session_id TEXT,
            device_id TEXT,
            created_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            is_used INTEGER DEFAULT 0
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confidence REAL,
            status TEXT DEFAULT 'PRESENT',
            otp_used TEXT,
            UNIQUE(session_id, student_id)
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device_key TEXT PRIMARY KEY,
            device_name TEXT,
            last_seen TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
        """)
        cursor.execute("SELECT COUNT(*) FROM sessions")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO sessions (session_id, course_name, is_active) VALUES (?, ?, 1)",
                ("SESSION_DEFAULT", "General Attendance Session"),
            )
        cursor.execute("SELECT COUNT(*) FROM devices WHERE device_key = ?", (DEVICE_SECRET_KEY,))
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO devices (device_key, device_name, is_active) VALUES (?, ?, ?)",
                (DEVICE_SECRET_KEY, "ESP32-LED-Matrix", 1),
            )
        conn.commit()
        conn.close()

    # --- Member / Student Queries ---
    def save_student(self, student_id: str, name: str, department: str, embedding: List[float]):
        s_id = student_id.strip()
        s_name = name.strip()
        s_dept = (department or "General").strip()
        now_iso = _utc_now_iso()

        if self.mode == "mongodb":
            self.mongo_db.students.update_one(
                {"student_id": s_id},
                {"$set": {
                    "student_id": s_id,
                    "name": s_name,
                    "department": s_dept,
                    "embedding": embedding,
                    "updated_at": now_iso,
                }, "$setOnInsert": {"created_at": now_iso}},
                upsert=True,
            )
            return

        if self.mode == "mongodb_data_api":
            self.data_api.update_one(
                "students",
                {"student_id": s_id},
                {"$set": {
                    "student_id": s_id,
                    "name": s_name,
                    "department": s_dept,
                    "embedding": embedding,
                    "updated_at": now_iso,
                }, "$setOnInsert": {"created_at": now_iso}},
                upsert=True,
            )
            return

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO students (student_id, name, department, embedding)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(student_id) DO UPDATE SET
                name = excluded.name,
                department = excluded.department,
                embedding = excluded.embedding
        """, (s_id, s_name, s_dept, json.dumps(embedding)))
        conn.commit()
        conn.close()

    def get_student(self, student_id: str) -> Optional[Dict[str, Any]]:
        s_id = student_id.strip()
        if self.mode == "mongodb":
            doc = self.mongo_db.students.find_one({"student_id": s_id}, {"_id": 0})
            return doc

        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("students", {"student_id": s_id})
            if doc:
                doc.pop("_id", None)
            return doc

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT student_id, name, department, embedding, created_at FROM students WHERE student_id = ?", (s_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                "student_id": row["student_id"],
                "name": row["name"],
                "department": row["department"],
                "embedding": json.loads(row["embedding"]),
                "created_at": row["created_at"],
            }
        return None

    def list_students(self) -> List[Dict[str, Any]]:
        if self.mode == "mongodb":
            docs = list(self.mongo_db.students.find({}, {"_id": 0}).sort("student_id", 1))
            for d in docs:
                emb = d.pop("embedding", None)
                d["embedding_dimensions"] = len(emb) if isinstance(emb, list) else 0
            return docs

        if self.mode == "mongodb_data_api":
            docs = self.data_api.find("students", {}, sort={"student_id": 1})
            for d in docs:
                d.pop("_id", None)
                emb = d.pop("embedding", None)
                d["embedding_dimensions"] = len(emb) if isinstance(emb, list) else 0
            return docs

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT student_id, name, department, embedding, created_at FROM students ORDER BY student_id ASC")
        rows = cursor.fetchall()
        conn.close()
        result = []
        for row in rows:
            item = dict(row)
            emb_raw = item.pop("embedding", None)
            try:
                emb_list = json.loads(emb_raw) if emb_raw else []
            except Exception:
                emb_list = []
            item["embedding_dimensions"] = len(emb_list)
            result.append(item)
        return result

    # --- Session Queries ---
    def create_session(self, session_id: str, course_name: str) -> Dict[str, Any]:
        sess_id = session_id.strip()
        c_name = course_name.strip()
        now_iso = _utc_now_iso()

        if self.mode == "mongodb":
            self.mongo_db.sessions.update_many({}, {"$set": {"is_active": 0}})
            self.mongo_db.sessions.update_one(
                {"session_id": sess_id},
                {"$set": {"session_id": sess_id, "course_name": c_name, "is_active": 1},
                 "$setOnInsert": {"created_at": now_iso}},
                upsert=True,
            )
            return {"session_id": sess_id, "course_name": c_name, "is_active": 1}

        if self.mode == "mongodb_data_api":
            self.data_api.update_many("sessions", {}, {"$set": {"is_active": 0}})
            self.data_api.update_one(
                "sessions",
                {"session_id": sess_id},
                {"$set": {"session_id": sess_id, "course_name": c_name, "is_active": 1},
                 "$setOnInsert": {"created_at": now_iso}},
                upsert=True,
            )
            return {"session_id": sess_id, "course_name": c_name, "is_active": 1}

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE sessions SET is_active = 0")
        cursor.execute("""
            INSERT INTO sessions (session_id, course_name, is_active)
            VALUES (?, ?, 1)
            ON CONFLICT(session_id) DO UPDATE SET
                course_name = excluded.course_name,
                is_active = 1
        """, (sess_id, c_name))
        conn.commit()
        conn.close()
        return {"session_id": sess_id, "course_name": c_name, "is_active": 1}

    def get_active_session(self) -> Optional[Dict[str, Any]]:
        if self.mode == "mongodb":
            return self.mongo_db.sessions.find_one({"is_active": 1}, {"_id": 0}, sort=[("created_at", -1)])

        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("sessions", {"is_active": 1}, sort={"created_at": -1})
            if doc:
                doc.pop("_id", None)
            return doc

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT session_id, course_name, created_at, is_active FROM sessions WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        sess_id = session_id.strip()
        if self.mode == "mongodb":
            return self.mongo_db.sessions.find_one({"session_id": sess_id}, {"_id": 0})
        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("sessions", {"session_id": sess_id})
            if doc:
                doc.pop("_id", None)
            return doc

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT session_id, course_name, created_at, is_active FROM sessions WHERE session_id = ?", (sess_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def list_sessions(self) -> List[Dict[str, Any]]:
        if self.mode == "mongodb":
            return list(self.mongo_db.sessions.find({}, {"_id": 0}).sort("created_at", -1))
        if self.mode == "mongodb_data_api":
            docs = self.data_api.find("sessions", {}, sort={"created_at": -1})
            for d in docs:
                d.pop("_id", None)
            return docs

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT session_id, course_name, created_at, is_active FROM sessions ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # --- Device & OTP Queries ---
    def verify_device(self, device_key: str) -> bool:
        d_key = device_key.strip()
        now_iso = _utc_now_iso()

        if self.mode == "mongodb":
            doc = self.mongo_db.devices.find_one({"device_key": d_key, "is_active": 1})
            if doc:
                self.mongo_db.devices.update_one({"device_key": d_key}, {"$set": {"last_seen": now_iso}})
                return True
            return False

        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("devices", {"device_key": d_key, "is_active": 1})
            if doc:
                self.data_api.update_one("devices", {"device_key": d_key}, {"$set": {"last_seen": now_iso}})
                return True
            return False

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT is_active FROM devices WHERE device_key = ?", (d_key,))
        row = cursor.fetchone()
        if row and row["is_active"] == 1:
            cursor.execute("UPDATE devices SET last_seen = ? WHERE device_key = ?", (now_iso, d_key))
            conn.commit()
            conn.close()
            return True
        conn.close()
        return False

    def get_device_info(self) -> Optional[Dict[str, Any]]:
        if self.mode == "mongodb":
            return self.mongo_db.devices.find_one({"is_active": 1}, {"_id": 0, "device_key": 0}, sort=[("last_seen", -1)])
        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("devices", {"is_active": 1}, sort={"last_seen": -1})
            if doc:
                doc.pop("_id", None)
                doc.pop("device_key", None)
            return doc

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT device_name, last_seen, is_active FROM devices WHERE is_active = 1 ORDER BY last_seen DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def save_new_otp(self, code: str, session_id: str, device_id: str, created_at: str, expires_at: str):
        if self.mode == "mongodb":
            self.mongo_db.otps.update_many({"session_id": session_id, "is_used": 0}, {"$set": {"is_used": 1}})
            self.mongo_db.otps.insert_one({
                "code": code,
                "session_id": session_id,
                "device_id": device_id,
                "created_at": created_at,
                "expires_at": expires_at,
                "is_used": 0,
            })
            return

        if self.mode == "mongodb_data_api":
            self.data_api.update_many("otps", {"session_id": session_id, "is_used": 0}, {"$set": {"is_used": 1}})
            self.data_api.insert_one("otps", {
                "code": code,
                "session_id": session_id,
                "device_id": device_id,
                "created_at": created_at,
                "expires_at": expires_at,
                "is_used": 0,
            })
            return

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE otps SET is_used = 1 WHERE session_id = ? AND is_used = 0", (session_id,))
        cursor.execute("""
            INSERT INTO otps (code, session_id, device_id, created_at, expires_at, is_used)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (code, session_id, device_id, created_at, expires_at))
        conn.commit()
        conn.close()

    def get_current_valid_otp(self, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        now_iso = _utc_now_iso()
        if self.mode == "mongodb":
            flt: Dict[str, Any] = {"is_used": 0, "expires_at": {"$gt": now_iso}}
            if session_id:
                flt["session_id"] = session_id
            return self.mongo_db.otps.find_one(flt, {"_id": 0}, sort=[("created_at", -1)])

        if self.mode == "mongodb_data_api":
            flt = {"is_used": 0, "expires_at": {"$gt": now_iso}}
            if session_id:
                flt["session_id"] = session_id
            doc = self.data_api.find_one("otps", flt, sort={"created_at": -1})
            if doc:
                doc.pop("_id", None)
            return doc

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        if session_id:
            cursor.execute("""
                SELECT id, code, session_id, device_id, created_at, expires_at
                FROM otps
                WHERE session_id = ? AND is_used = 0 AND expires_at > ?
                ORDER BY created_at DESC LIMIT 1
            """, (session_id, now_iso))
        else:
            cursor.execute("""
                SELECT id, code, session_id, device_id, created_at, expires_at
                FROM otps
                WHERE is_used = 0 AND expires_at > ?
                ORDER BY created_at DESC LIMIT 1
            """, (now_iso,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def validate_and_consume_otp(self, code: str, session_id: str) -> bool:
        now_iso = _utc_now_iso()
        c_val = code.strip()
        s_val = session_id.strip()

        if self.mode == "mongodb":
            doc = self.mongo_db.otps.find_one({
                "code": c_val,
                "$or": [{"session_id": s_val}, {"session_id": None}, {"session_id": "GLOBAL"}],
                "is_used": 0,
                "expires_at": {"$gt": now_iso},
            })
            return doc is not None

        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("otps", {
                "code": c_val,
                "$or": [{"session_id": s_val}, {"session_id": None}, {"session_id": "GLOBAL"}],
                "is_used": 0,
                "expires_at": {"$gt": now_iso},
            })
            return doc is not None

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM otps
            WHERE code = ? AND (session_id = ? OR session_id IS NULL OR session_id = 'GLOBAL')
              AND is_used = 0 AND expires_at > ?
            LIMIT 1
        """, (c_val, s_val, now_iso))
        row = cursor.fetchone()
        conn.close()
        return row is not None

    # --- Attendance Queries ---
    def record_attendance(self, session_id: str, student_id: str, confidence: float, status: str = "PRESENT", otp_used: str = "") -> bool:
        now_iso = _utc_now_iso()
        if self.mode == "mongodb":
            try:
                self.mongo_db.attendance.update_one(
                    {"session_id": session_id, "student_id": student_id},
                    {"$set": {
                        "session_id": session_id,
                        "student_id": student_id,
                        "confidence": confidence,
                        "status": status,
                        "otp_used": otp_used,
                        "timestamp": now_iso,
                    }},
                    upsert=True,
                )
                return True
            except Exception as e:
                print(f"[DB] MongoDB error recording attendance: {e}")
                return False

        if self.mode == "mongodb_data_api":
            try:
                self.data_api.update_one(
                    "attendance",
                    {"session_id": session_id, "student_id": student_id},
                    {"$set": {
                        "session_id": session_id,
                        "student_id": student_id,
                        "confidence": confidence,
                        "status": status,
                        "otp_used": otp_used,
                        "timestamp": now_iso,
                    }},
                    upsert=True,
                )
                return True
            except Exception as e:
                print(f"[DB] MongoDB Data API error recording attendance: {e}")
                return False

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO attendance (session_id, student_id, confidence, status, otp_used)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, student_id) DO UPDATE SET
                    confidence = excluded.confidence,
                    status = excluded.status,
                    otp_used = excluded.otp_used,
                    timestamp = CURRENT_TIMESTAMP
            """, (session_id, student_id, confidence, status, otp_used))
            conn.commit()
            return True
        except Exception as e:
            print(f"[DB] SQLite error recording attendance: {e}")
            return False
        finally:
            conn.close()

    def get_session_attendance(self, session_id: str) -> List[Dict[str, Any]]:
        if self.mode in ("mongodb", "mongodb_data_api"):
            if self.mode == "mongodb":
                att_docs = list(self.mongo_db.attendance.find({"session_id": session_id}, {"_id": 0}).sort("timestamp", 1))
                members = {m["student_id"]: m for m in self.mongo_db.students.find({}, {"_id": 0, "embedding": 0})}
            else:
                att_docs = self.data_api.find("attendance", {"session_id": session_id}, sort={"timestamp": 1})
                members = {m["student_id"]: m for m in self.data_api.find("students", {})}

            results = []
            for idx, r in enumerate(att_docs, start=1):
                r.pop("_id", None)
                member = members.get(r["student_id"], {})
                results.append({
                    "id": idx,
                    "session_id": r["session_id"],
                    "student_id": r["student_id"],
                    "student_name": member.get("name", "N/A"),
                    "department": member.get("department", "N/A"),
                    "timestamp": r["timestamp"],
                    "confidence": r["confidence"],
                    "status": r["status"],
                    "otp_used": r["otp_used"],
                })
            return results

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.id, a.session_id, a.student_id, s.name as student_name, s.department,
                   a.timestamp, a.confidence, a.status, a.otp_used
            FROM attendance a
            LEFT JOIN students s ON a.student_id = s.student_id
            WHERE a.session_id = ?
            ORDER BY a.timestamp ASC
        """, (session_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]


db = DatabaseManager()


# Module-level compatibility wrappers
def init_db():
    db.init_db()


def get_db_status() -> Dict[str, Any]:
    return db.get_status()


def save_student(student_id: str, name: str, department: str, embedding: List[float]):
    db.save_student(student_id, name, department, embedding)


def get_student(student_id: str) -> Optional[Dict[str, Any]]:
    return db.get_student(student_id)


def list_students() -> List[Dict[str, Any]]:
    return db.list_students()


def create_session(session_id: str, course_name: str) -> Dict[str, Any]:
    return db.create_session(session_id, course_name)


def get_active_session() -> Optional[Dict[str, Any]]:
    return db.get_active_session()


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    return db.get_session(session_id)


def list_sessions() -> List[Dict[str, Any]]:
    return db.list_sessions()


def verify_device(device_key: str) -> bool:
    return db.verify_device(device_key)


def get_device_info() -> Optional[Dict[str, Any]]:
    return db.get_device_info()


def save_new_otp(code: str, session_id: str, device_id: str, created_at: str, expires_at: str):
    db.save_new_otp(code, session_id, device_id, created_at, expires_at)


def get_current_valid_otp(session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return db.get_current_valid_otp(session_id)


def validate_and_consume_otp(code: str, session_id: str) -> bool:
    return db.validate_and_consume_otp(code, session_id)


def record_attendance(session_id: str, student_id: str, confidence: float, status: str = "PRESENT", otp_used: str = "") -> bool:
    return db.record_attendance(session_id, student_id, confidence, status, otp_used)


def get_session_attendance(session_id: str) -> List[Dict[str, Any]]:
    return db.get_session_attendance(session_id)
