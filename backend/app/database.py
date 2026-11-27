import hashlib
import json
import sqlite3
import uuid
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from .config import (
    DB_PATH,
    DEVICE_SECRET_KEY,
    DEFAULT_COMPANY_ID,
    DEFAULT_COMPANY_NAME,
    DEFAULT_ADMIN_PIN,
    DEFAULT_ORG_MODE,
    MONGODB_URI,
    MONGODB_DB_NAME,
    MONGODB_DATA_API_URL,
    MONGODB_DATA_API_KEY,
    MONGODB_DATA_SOURCE,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.strip().encode("utf-8")).hexdigest()


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
        if sort:
            res = self._call("find", collection, {"filter": filter_doc, "sort": sort, "limit": 1})
            docs = res.get("documents", [])
            return docs[0] if docs else None
        return self._call("findOne", collection, {"filter": filter_doc}).get("document")

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

        self.mode = "sqlite"
        print(f"[DB] Using local SQLite database at {DB_PATH}")

    def _sqlite_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_sqlite_column(self, cursor: sqlite3.Cursor, table: str, column: str, definition: str):
        cursor.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cursor.fetchall()]
        if column not in cols:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def get_status(self) -> Dict[str, Any]:
        return {
            "backend": self.mode,
            "connected": True,
            "database_name": MONGODB_DB_NAME if self.mode.startswith("mongodb") else "attendance.db",
            "mongodb_configured": bool(MONGODB_URI or (MONGODB_DATA_API_URL and MONGODB_DATA_API_KEY)),
            "warning": self.last_error,
        }

    def init_db(self):
        now_iso = _utc_now_iso()
        default_pin_hash = _hash_pin(DEFAULT_ADMIN_PIN)

        if self.mode == "mongodb":
            # Ensure indexes (drop conflicting legacy single-field indexes safely if needed)
            try:
                self.mongo_db.companies.create_index("company_id", unique=True)
                self.mongo_db.devices.create_index("device_key", unique=True)
                self.mongo_db.attendance.create_index("attendance_id", unique=True, sparse=True)
            except Exception:
                pass

            if self.mongo_db.companies.count_documents({"company_id": DEFAULT_COMPANY_ID}) == 0:
                self.mongo_db.companies.insert_one({
                    "company_id": DEFAULT_COMPANY_ID,
                    "company_name": DEFAULT_COMPANY_NAME,
                    "admin_name": "Primary Administrator",
                    "admin_pin_hash": default_pin_hash,
                    "org_type": DEFAULT_ORG_MODE,
                    "created_at": now_iso,
                })
            if self.mongo_db.sessions.count_documents({"company_id": DEFAULT_COMPANY_ID}) == 0:
                # Also backfill legacy sessions without company_id
                self.mongo_db.sessions.update_many({"company_id": {"$exists": False}}, {"$set": {"company_id": DEFAULT_COMPANY_ID}})
                if self.mongo_db.sessions.count_documents({"company_id": DEFAULT_COMPANY_ID}) == 0:
                    self.mongo_db.sessions.insert_one({
                        "session_id": "SESSION_DEFAULT",
                        "company_id": DEFAULT_COMPANY_ID,
                        "course_name": "General Attendance Session",
                        "cutoff_time": None,
                        "created_at": now_iso,
                        "is_active": 1,
                    })
            self.mongo_db.students.update_many({"company_id": {"$exists": False}}, {"$set": {"company_id": DEFAULT_COMPANY_ID}})
            self.mongo_db.attendance.update_many({"company_id": {"$exists": False}}, {"$set": {"company_id": DEFAULT_COMPANY_ID}})
            if self.mongo_db.devices.count_documents({"device_key": DEVICE_SECRET_KEY}) == 0:
                self.mongo_db.devices.insert_one({
                    "device_key": DEVICE_SECRET_KEY,
                    "company_id": DEFAULT_COMPANY_ID,
                    "device_name": "ESP32-LED-Matrix",
                    "last_seen": None,
                    "is_active": 1,
                })
            return

        if self.mode == "mongodb_data_api":
            if not self.data_api.find_one("companies", {"company_id": DEFAULT_COMPANY_ID}):
                self.data_api.insert_one("companies", {
                    "company_id": DEFAULT_COMPANY_ID,
                    "company_name": DEFAULT_COMPANY_NAME,
                    "admin_name": "Primary Administrator",
                    "admin_pin_hash": default_pin_hash,
                    "org_type": DEFAULT_ORG_MODE,
                    "created_at": now_iso,
                })
            if not self.data_api.find_one("sessions", {"company_id": DEFAULT_COMPANY_ID}):
                self.data_api.insert_one("sessions", {
                    "session_id": "SESSION_DEFAULT",
                    "company_id": DEFAULT_COMPANY_ID,
                    "course_name": "General Attendance Session",
                    "cutoff_time": None,
                    "created_at": now_iso,
                    "is_active": 1,
                })
            if not self.data_api.find_one("devices", {"device_key": DEVICE_SECRET_KEY}):
                self.data_api.insert_one("devices", {
                    "device_key": DEVICE_SECRET_KEY,
                    "company_id": DEFAULT_COMPANY_ID,
                    "device_name": "ESP32-LED-Matrix",
                    "last_seen": None,
                    "is_active": 1,
                })
            return

        # SQLite initialization & migration
        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            company_id TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            admin_name TEXT NOT NULL,
            admin_pin_hash TEXT NOT NULL,
            org_type TEXT DEFAULT 'general',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            student_id TEXT PRIMARY KEY,
            company_id TEXT DEFAULT 'DEFAULT_ORG',
            name TEXT NOT NULL,
            department TEXT,
            embedding TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            company_id TEXT DEFAULT 'DEFAULT_ORG',
            course_name TEXT NOT NULL,
            cutoff_time TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS otps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            company_id TEXT DEFAULT 'DEFAULT_ORG',
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
            attendance_id TEXT,
            company_id TEXT DEFAULT 'DEFAULT_ORG',
            session_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notice_time TEXT,
            attendance_notice TEXT DEFAULT 'RECORDED',
            latitude REAL,
            longitude REAL,
            location_accuracy REAL,
            location_label TEXT,
            confidence REAL,
            status TEXT DEFAULT 'PRESENT',
            otp_used TEXT,
            UNIQUE(session_id, student_id)
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device_key TEXT PRIMARY KEY,
            company_id TEXT DEFAULT 'DEFAULT_ORG',
            device_name TEXT,
            last_seen TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
        """)

        # Migrate existing local SQLite tables seamlessly
        self._ensure_sqlite_column(cursor, "students", "company_id", "TEXT DEFAULT 'DEFAULT_ORG'")
        self._ensure_sqlite_column(cursor, "sessions", "company_id", "TEXT DEFAULT 'DEFAULT_ORG'")
        self._ensure_sqlite_column(cursor, "sessions", "cutoff_time", "TEXT")
        self._ensure_sqlite_column(cursor, "otps", "company_id", "TEXT DEFAULT 'DEFAULT_ORG'")
        self._ensure_sqlite_column(cursor, "devices", "company_id", "TEXT DEFAULT 'DEFAULT_ORG'")
        self._ensure_sqlite_column(cursor, "attendance", "attendance_id", "TEXT")
        self._ensure_sqlite_column(cursor, "attendance", "company_id", "TEXT DEFAULT 'DEFAULT_ORG'")
        self._ensure_sqlite_column(cursor, "attendance", "notice_time", "TEXT")
        self._ensure_sqlite_column(cursor, "attendance", "attendance_notice", "TEXT DEFAULT 'RECORDED'")
        self._ensure_sqlite_column(cursor, "attendance", "latitude", "REAL")
        self._ensure_sqlite_column(cursor, "attendance", "longitude", "REAL")
        self._ensure_sqlite_column(cursor, "attendance", "location_accuracy", "REAL")
        self._ensure_sqlite_column(cursor, "attendance", "location_label", "TEXT")

        cursor.execute("SELECT COUNT(*) FROM companies WHERE company_id = ?", (DEFAULT_COMPANY_ID,))
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO companies (company_id, company_name, admin_name, admin_pin_hash, org_type) VALUES (?, ?, ?, ?, ?)",
                (DEFAULT_COMPANY_ID, DEFAULT_COMPANY_NAME, "Primary Administrator", default_pin_hash, DEFAULT_ORG_MODE),
            )
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE company_id = ?", (DEFAULT_COMPANY_ID,))
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO sessions (session_id, company_id, course_name, is_active) VALUES (?, ?, ?, 1)",
                ("SESSION_DEFAULT", DEFAULT_COMPANY_ID, "General Attendance Session"),
            )
        cursor.execute("SELECT COUNT(*) FROM devices WHERE device_key = ?", (DEVICE_SECRET_KEY,))
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO devices (device_key, company_id, device_name, is_active) VALUES (?, ?, ?, 1)",
                (DEVICE_SECRET_KEY, DEFAULT_COMPANY_ID, "ESP32-LED-Matrix"),
            )
        conn.commit()
        conn.close()

    # --- Company / Administrator Queries ---
    def register_company(self, company_id: str, company_name: str, admin_name: str, admin_pin: str, org_type: str = "general") -> Dict[str, Any]:
        c_id = company_id.strip().upper().replace(" ", "_")
        c_name = company_name.strip()
        a_name = admin_name.strip()
        pin_hash = _hash_pin(admin_pin)
        now_iso = _utc_now_iso()
        default_session = f"{c_id}_SESSION_01"

        doc = {
            "company_id": c_id,
            "company_name": c_name,
            "admin_name": a_name,
            "org_type": org_type.strip().lower(),
            "created_at": now_iso,
        }

        if self.mode == "mongodb":
            if self.mongo_db.companies.find_one({"company_id": c_id}):
                raise ValueError(f"Company ID '{c_id}' already exists")
            self.mongo_db.companies.insert_one({**doc, "admin_pin_hash": pin_hash})
            self.mongo_db.sessions.insert_one({
                "session_id": default_session,
                "company_id": c_id,
                "course_name": f"{c_name} - Primary Session",
                "cutoff_time": None,
                "created_at": now_iso,
                "is_active": 1,
            })
            return doc

        if self.mode == "mongodb_data_api":
            if self.data_api.find_one("companies", {"company_id": c_id}):
                raise ValueError(f"Company ID '{c_id}' already exists")
            self.data_api.insert_one("companies", {**doc, "admin_pin_hash": pin_hash})
            self.data_api.insert_one("sessions", {
                "session_id": default_session,
                "company_id": c_id,
                "course_name": f"{c_name} - Primary Session",
                "cutoff_time": None,
                "created_at": now_iso,
                "is_active": 1,
            })
            return doc

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM companies WHERE company_id = ?", (c_id,))
        if cursor.fetchone()[0] > 0:
            conn.close()
            raise ValueError(f"Company ID '{c_id}' already exists")
        cursor.execute(
            "INSERT INTO companies (company_id, company_name, admin_name, admin_pin_hash, org_type) VALUES (?, ?, ?, ?, ?)",
            (c_id, c_name, a_name, pin_hash, doc["org_type"]),
        )
        cursor.execute(
            "INSERT INTO sessions (session_id, company_id, course_name, is_active) VALUES (?, ?, ?, 1)",
            (default_session, c_id, f"{c_name} - Primary Session"),
        )
        conn.commit()
        conn.close()
        return doc

    def authenticate_company(self, company_id: str, admin_pin: str) -> Optional[Dict[str, Any]]:
        c_id = company_id.strip().upper().replace(" ", "_")
        pin_hash = _hash_pin(admin_pin)

        if self.mode == "mongodb":
            doc = self.mongo_db.companies.find_one({"company_id": c_id, "admin_pin_hash": pin_hash}, {"_id": 0, "admin_pin_hash": 0})
            return doc

        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("companies", {"company_id": c_id, "admin_pin_hash": pin_hash})
            if doc:
                doc.pop("_id", None)
                doc.pop("admin_pin_hash", None)
            return doc

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT company_id, company_name, admin_name, org_type, created_at FROM companies WHERE company_id = ? AND admin_pin_hash = ?",
            (c_id, pin_hash),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_company(self, company_id: str) -> Optional[Dict[str, Any]]:
        c_id = company_id.strip().upper().replace(" ", "_")
        if self.mode == "mongodb":
            return self.mongo_db.companies.find_one({"company_id": c_id}, {"_id": 0, "admin_pin_hash": 0})
        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("companies", {"company_id": c_id})
            if doc:
                doc.pop("_id", None)
                doc.pop("admin_pin_hash", None)
            return doc
        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT company_id, company_name, admin_name, org_type, created_at FROM companies WHERE company_id = ?", (c_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def list_companies(self) -> List[Dict[str, Any]]:
        if self.mode == "mongodb":
            return list(self.mongo_db.companies.find({}, {"_id": 0, "admin_pin_hash": 0}).sort("company_name", 1))
        if self.mode == "mongodb_data_api":
            docs = self.data_api.find("companies", {}, sort={"company_name": 1})
            for d in docs:
                d.pop("_id", None)
                d.pop("admin_pin_hash", None)
            return docs
        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT company_id, company_name, admin_name, org_type, created_at FROM companies ORDER BY company_name ASC")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # --- Member / Student Queries ---
    def save_student(self, student_id: str, name: str, department: str, embedding: List[float], company_id: str = DEFAULT_COMPANY_ID):
        s_id = student_id.strip()
        c_id = (company_id or DEFAULT_COMPANY_ID).strip()
        s_name = name.strip()
        s_dept = (department or "General").strip()
        now_iso = _utc_now_iso()

        if self.mode == "mongodb":
            self.mongo_db.students.update_one(
                {"student_id": s_id, "company_id": c_id},
                {"$set": {
                    "student_id": s_id,
                    "company_id": c_id,
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
                {"student_id": s_id, "company_id": c_id},
                {"$set": {
                    "student_id": s_id,
                    "company_id": c_id,
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
            INSERT INTO students (student_id, company_id, name, department, embedding)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id) DO UPDATE SET
                company_id = excluded.company_id,
                name = excluded.name,
                department = excluded.department,
                embedding = excluded.embedding
        """, (s_id, c_id, s_name, s_dept, json.dumps(embedding)))
        conn.commit()
        conn.close()

    def get_student(self, student_id: str, company_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        s_id = student_id.strip()
        c_id = (company_id or DEFAULT_COMPANY_ID).strip()
        if self.mode == "mongodb":
            doc = self.mongo_db.students.find_one({"student_id": s_id, "company_id": c_id}, {"_id": 0})
            if not doc and not company_id:
                doc = self.mongo_db.students.find_one({"student_id": s_id}, {"_id": 0})
            return doc

        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("students", {"student_id": s_id, "company_id": c_id})
            if doc:
                doc.pop("_id", None)
            return doc

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT student_id, company_id, name, department, embedding, created_at FROM students WHERE student_id = ? AND company_id = ?",
            (s_id, c_id),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                "student_id": row["student_id"],
                "company_id": row["company_id"],
                "name": row["name"],
                "department": row["department"],
                "embedding": json.loads(row["embedding"]),
                "created_at": row["created_at"],
            }
        return None

    def list_students(self, company_id: Optional[str] = None) -> List[Dict[str, Any]]:
        flt: Dict[str, Any] = {"company_id": company_id.strip()} if company_id else {}
        if self.mode == "mongodb":
            docs = list(self.mongo_db.students.find(flt, {"_id": 0}).sort("student_id", 1))
            for d in docs:
                emb = d.pop("embedding", None)
                d["embedding_dimensions"] = len(emb) if isinstance(emb, list) else 0
            return docs

        if self.mode == "mongodb_data_api":
            docs = self.data_api.find("students", flt, sort={"student_id": 1})
            for d in docs:
                d.pop("_id", None)
                emb = d.pop("embedding", None)
                d["embedding_dimensions"] = len(emb) if isinstance(emb, list) else 0
            return docs

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        if company_id:
            cursor.execute("SELECT student_id, company_id, name, department, embedding, created_at FROM students WHERE company_id = ? ORDER BY student_id ASC", (company_id.strip(),))
        else:
            cursor.execute("SELECT student_id, company_id, name, department, embedding, created_at FROM students ORDER BY student_id ASC")
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
    def create_session(self, session_id: str, course_name: str, company_id: str = DEFAULT_COMPANY_ID, cutoff_time: Optional[str] = None) -> Dict[str, Any]:
        sess_id = session_id.strip()
        c_id = (company_id or DEFAULT_COMPANY_ID).strip()
        c_name = course_name.strip()
        cutoff = cutoff_time.strip() if cutoff_time else None
        now_iso = _utc_now_iso()

        if self.mode == "mongodb":
            self.mongo_db.sessions.update_many({"company_id": c_id}, {"$set": {"is_active": 0}})
            self.mongo_db.sessions.update_one(
                {"session_id": sess_id, "company_id": c_id},
                {"$set": {
                    "session_id": sess_id,
                    "company_id": c_id,
                    "course_name": c_name,
                    "cutoff_time": cutoff,
                    "is_active": 1,
                }, "$setOnInsert": {"created_at": now_iso}},
                upsert=True,
            )
            return {"session_id": sess_id, "company_id": c_id, "course_name": c_name, "cutoff_time": cutoff, "is_active": 1}

        if self.mode == "mongodb_data_api":
            self.data_api.update_many("sessions", {"company_id": c_id}, {"$set": {"is_active": 0}})
            self.data_api.update_one(
                "sessions",
                {"session_id": sess_id, "company_id": c_id},
                {"$set": {
                    "session_id": sess_id,
                    "company_id": c_id,
                    "course_name": c_name,
                    "cutoff_time": cutoff,
                    "is_active": 1,
                }, "$setOnInsert": {"created_at": now_iso}},
                upsert=True,
            )
            return {"session_id": sess_id, "company_id": c_id, "course_name": c_name, "cutoff_time": cutoff, "is_active": 1}

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE sessions SET is_active = 0 WHERE company_id = ?", (c_id,))
        cursor.execute("""
            INSERT INTO sessions (session_id, company_id, course_name, cutoff_time, is_active)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(session_id) DO UPDATE SET
                company_id = excluded.company_id,
                course_name = excluded.course_name,
                cutoff_time = excluded.cutoff_time,
                is_active = 1
        """, (sess_id, c_id, c_name, cutoff))
        conn.commit()
        conn.close()
        return {"session_id": sess_id, "company_id": c_id, "course_name": c_name, "cutoff_time": cutoff, "is_active": 1}

    def get_active_session(self, company_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        c_id = (company_id or DEFAULT_COMPANY_ID).strip()
        if self.mode == "mongodb":
            return self.mongo_db.sessions.find_one({"company_id": c_id, "is_active": 1}, {"_id": 0}, sort=[("created_at", -1)])
        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("sessions", {"company_id": c_id, "is_active": 1}, sort={"created_at": -1})
            if doc:
                doc.pop("_id", None)
            return doc

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT session_id, company_id, course_name, cutoff_time, created_at, is_active FROM sessions WHERE company_id = ? AND is_active = 1 ORDER BY created_at DESC LIMIT 1", (c_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_session(self, session_id: str, company_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        sess_id = session_id.strip()
        flt: Dict[str, Any] = {"session_id": sess_id}
        if company_id:
            flt["company_id"] = company_id.strip()
        if self.mode == "mongodb":
            return self.mongo_db.sessions.find_one(flt, {"_id": 0})
        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("sessions", flt)
            if doc:
                doc.pop("_id", None)
            return doc

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        if company_id:
            cursor.execute("SELECT session_id, company_id, course_name, cutoff_time, created_at, is_active FROM sessions WHERE session_id = ? AND company_id = ?", (sess_id, company_id.strip()))
        else:
            cursor.execute("SELECT session_id, company_id, course_name, cutoff_time, created_at, is_active FROM sessions WHERE session_id = ?", (sess_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def list_sessions(self, company_id: Optional[str] = None) -> List[Dict[str, Any]]:
        flt: Dict[str, Any] = {"company_id": company_id.strip()} if company_id else {}
        if self.mode == "mongodb":
            return list(self.mongo_db.sessions.find(flt, {"_id": 0}).sort("created_at", -1))
        if self.mode == "mongodb_data_api":
            docs = self.data_api.find("sessions", flt, sort={"created_at": -1})
            for d in docs:
                d.pop("_id", None)
            return docs

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        if company_id:
            cursor.execute("SELECT session_id, company_id, course_name, cutoff_time, created_at, is_active FROM sessions WHERE company_id = ? ORDER BY created_at DESC", (company_id.strip(),))
        else:
            cursor.execute("SELECT session_id, company_id, course_name, cutoff_time, created_at, is_active FROM sessions ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # --- Device & OTP Queries ---
    def verify_device(self, device_key: str) -> Optional[str]:
        d_key = device_key.strip()
        now_iso = _utc_now_iso()

        if self.mode == "mongodb":
            doc = self.mongo_db.devices.find_one({"device_key": d_key, "is_active": 1})
            if doc:
                self.mongo_db.devices.update_one({"device_key": d_key}, {"$set": {"last_seen": now_iso}})
                return doc.get("company_id", DEFAULT_COMPANY_ID)
            return None

        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("devices", {"device_key": d_key, "is_active": 1})
            if doc:
                self.data_api.update_one("devices", {"device_key": d_key}, {"$set": {"last_seen": now_iso}})
                return doc.get("company_id", DEFAULT_COMPANY_ID)
            return None

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT company_id, is_active FROM devices WHERE device_key = ?", (d_key,))
        row = cursor.fetchone()
        if row and row["is_active"] == 1:
            cursor.execute("UPDATE devices SET last_seen = ? WHERE device_key = ?", (now_iso, d_key))
            conn.commit()
            comp = row["company_id"] or DEFAULT_COMPANY_ID
            conn.close()
            return comp
        conn.close()
        return None

    def register_company_device(self, company_id: str, device_key: str, device_name: str = "ESP32-LED-Matrix"):
        c_id = company_id.strip()
        d_key = device_key.strip()
        if self.mode == "mongodb":
            self.mongo_db.devices.update_one(
                {"device_key": d_key},
                {"$set": {"device_key": d_key, "company_id": c_id, "device_name": device_name, "is_active": 1}},
                upsert=True,
            )
            return
        if self.mode == "mongodb_data_api":
            self.data_api.update_one(
                "devices",
                {"device_key": d_key},
                {"$set": {"device_key": d_key, "company_id": c_id, "device_name": device_name, "is_active": 1}},
                upsert=True,
            )
            return
        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO devices (device_key, company_id, device_name, is_active)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(device_key) DO UPDATE SET
                company_id = excluded.company_id,
                device_name = excluded.device_name,
                is_active = 1
        """, (d_key, c_id, device_name))
        conn.commit()
        conn.close()

    def get_device_info(self, company_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        flt: Dict[str, Any] = {"is_active": 1}
        if company_id:
            flt["company_id"] = company_id.strip()
        if self.mode == "mongodb":
            return self.mongo_db.devices.find_one(flt, {"_id": 0, "device_key": 0}, sort=[("last_seen", -1)])
        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("devices", flt, sort={"last_seen": -1})
            if doc:
                doc.pop("_id", None)
                doc.pop("device_key", None)
            return doc

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        if company_id:
            cursor.execute("SELECT company_id, device_name, last_seen, is_active FROM devices WHERE is_active = 1 AND company_id = ? ORDER BY last_seen DESC LIMIT 1", (company_id.strip(),))
        else:
            cursor.execute("SELECT company_id, device_name, last_seen, is_active FROM devices WHERE is_active = 1 ORDER BY last_seen DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def save_new_otp(self, code: str, session_id: str, device_id: str, created_at: str, expires_at: str, company_id: str = DEFAULT_COMPANY_ID):
        c_id = (company_id or DEFAULT_COMPANY_ID).strip()
        if self.mode == "mongodb":
            self.mongo_db.otps.update_many({"company_id": c_id, "session_id": session_id, "is_used": 0}, {"$set": {"is_used": 1}})
            self.mongo_db.otps.insert_one({
                "code": code,
                "company_id": c_id,
                "session_id": session_id,
                "device_id": device_id,
                "created_at": created_at,
                "expires_at": expires_at,
                "is_used": 0,
            })
            return

        if self.mode == "mongodb_data_api":
            self.data_api.update_many("otps", {"company_id": c_id, "session_id": session_id, "is_used": 0}, {"$set": {"is_used": 1}})
            self.data_api.insert_one("otps", {
                "code": code,
                "company_id": c_id,
                "session_id": session_id,
                "device_id": device_id,
                "created_at": created_at,
                "expires_at": expires_at,
                "is_used": 0,
            })
            return

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE otps SET is_used = 1 WHERE company_id = ? AND session_id = ? AND is_used = 0", (c_id, session_id))
        cursor.execute("""
            INSERT INTO otps (code, company_id, session_id, device_id, created_at, expires_at, is_used)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (code, c_id, session_id, device_id, created_at, expires_at))
        conn.commit()
        conn.close()

    def get_current_valid_otp(self, session_id: Optional[str] = None, company_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        now_iso = _utc_now_iso()
        c_id = (company_id or DEFAULT_COMPANY_ID).strip()
        if self.mode == "mongodb":
            flt: Dict[str, Any] = {"company_id": c_id, "is_used": 0, "expires_at": {"$gt": now_iso}}
            if session_id:
                flt["session_id"] = session_id
            return self.mongo_db.otps.find_one(flt, {"_id": 0}, sort=[("created_at", -1)])

        if self.mode == "mongodb_data_api":
            flt = {"company_id": c_id, "is_used": 0, "expires_at": {"$gt": now_iso}}
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
                SELECT id, code, company_id, session_id, device_id, created_at, expires_at
                FROM otps
                WHERE company_id = ? AND session_id = ? AND is_used = 0 AND expires_at > ?
                ORDER BY created_at DESC LIMIT 1
            """, (c_id, session_id, now_iso))
        else:
            cursor.execute("""
                SELECT id, code, company_id, session_id, device_id, created_at, expires_at
                FROM otps
                WHERE company_id = ? AND is_used = 0 AND expires_at > ?
                ORDER BY created_at DESC LIMIT 1
            """, (c_id, now_iso))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def is_otp_valid(self, code: str, session_id: str, company_id: str = DEFAULT_COMPANY_ID) -> bool:
        """Non-destructive check before face verification."""
        now_iso = _utc_now_iso()
        c_val = code.strip()
        s_val = session_id.strip()
        comp_val = (company_id or DEFAULT_COMPANY_ID).strip()

        if self.mode == "mongodb":
            doc = self.mongo_db.otps.find_one({
                "code": c_val,
                "company_id": comp_val,
                "$or": [{"session_id": s_val}, {"session_id": None}, {"session_id": "GLOBAL"}],
                "is_used": 0,
                "expires_at": {"$gt": now_iso},
            })
            return doc is not None

        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("otps", {
                "code": c_val,
                "company_id": comp_val,
                "$or": [{"session_id": s_val}, {"session_id": None}, {"session_id": "GLOBAL"}],
                "is_used": 0,
                "expires_at": {"$gt": now_iso},
            })
            return doc is not None

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM otps
            WHERE code = ? AND company_id = ?
              AND (session_id = ? OR session_id IS NULL OR session_id = 'GLOBAL')
              AND is_used = 0 AND expires_at > ?
            LIMIT 1
        """, (c_val, comp_val, s_val, now_iso))
        row = cursor.fetchone()
        conn.close()
        return row is not None

    def validate_and_consume_otp(self, code: str, session_id: str, company_id: str = DEFAULT_COMPANY_ID) -> bool:
        """
        Atomically consumes the OTP (is_used: 0 -> 1) so ONLY ONE user can mark attendance with it.
        """
        now_iso = _utc_now_iso()
        c_val = code.strip()
        s_val = session_id.strip()
        comp_val = (company_id or DEFAULT_COMPANY_ID).strip()

        if self.mode == "mongodb":
            updated = self.mongo_db.otps.find_one_and_update(
                {
                    "code": c_val,
                    "company_id": comp_val,
                    "$or": [{"session_id": s_val}, {"session_id": None}, {"session_id": "GLOBAL"}],
                    "is_used": 0,
                    "expires_at": {"$gt": now_iso},
                },
                {"$set": {"is_used": 1, "used_at": now_iso}},
            )
            return updated is not None

        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("otps", {
                "code": c_val,
                "company_id": comp_val,
                "$or": [{"session_id": s_val}, {"session_id": None}, {"session_id": "GLOBAL"}],
                "is_used": 0,
                "expires_at": {"$gt": now_iso},
            })
            if not doc:
                return False
            self.data_api.update_one("otps", {"code": c_val, "company_id": comp_val, "is_used": 0}, {"$set": {"is_used": 1, "used_at": now_iso}})
            return True

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE otps
            SET is_used = 1
            WHERE id = (
                SELECT id FROM otps
                WHERE code = ? AND company_id = ?
                  AND (session_id = ? OR session_id IS NULL OR session_id = 'GLOBAL')
                  AND is_used = 0 AND expires_at > ?
                LIMIT 1
            ) AND is_used = 0
        """, (c_val, comp_val, s_val, now_iso))
        consumed = cursor.rowcount == 1
        conn.commit()
        conn.close()
        return consumed

    # --- Attendance Queries ---
    def record_attendance(
        self,
        session_id: str,
        student_id: str,
        confidence: float,
        status: str = "PRESENT",
        otp_used: str = "",
        company_id: str = DEFAULT_COMPANY_ID,
        notice_time: str = "",
        attendance_notice: str = "RECORDED",
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        location_accuracy: Optional[float] = None,
        location_label: str = "Not Provided",
    ) -> Optional[Dict[str, Any]]:
        now_iso = _utc_now_iso()
        c_id = (company_id or DEFAULT_COMPANY_ID).strip()
        att_id = f"ATT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        doc = {
            "attendance_id": att_id,
            "company_id": c_id,
            "session_id": session_id,
            "student_id": student_id,
            "confidence": confidence,
            "status": status,
            "otp_used": otp_used,
            "timestamp": now_iso,
            "notice_time": notice_time or now_iso,
            "attendance_notice": attendance_notice,
            "latitude": latitude,
            "longitude": longitude,
            "location_accuracy": location_accuracy,
            "location_label": location_label,
        }

        if self.mode == "mongodb":
            try:
                self.mongo_db.attendance.update_one(
                    {"company_id": c_id, "session_id": session_id, "student_id": student_id},
                    {"$set": doc},
                    upsert=True,
                )
                return doc
            except Exception as e:
                print(f"[DB] MongoDB error recording attendance: {e}")
                return None

        if self.mode == "mongodb_data_api":
            try:
                self.data_api.update_one(
                    "attendance",
                    {"company_id": c_id, "session_id": session_id, "student_id": student_id},
                    {"$set": doc},
                    upsert=True,
                )
                return doc
            except Exception as e:
                print(f"[DB] MongoDB Data API error recording attendance: {e}")
                return None

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO attendance (
                    attendance_id, company_id, session_id, student_id, confidence, status,
                    otp_used, timestamp, notice_time, attendance_notice,
                    latitude, longitude, location_accuracy, location_label
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, student_id) DO UPDATE SET
                    attendance_id = excluded.attendance_id,
                    company_id = excluded.company_id,
                    confidence = excluded.confidence,
                    status = excluded.status,
                    otp_used = excluded.otp_used,
                    timestamp = excluded.timestamp,
                    notice_time = excluded.notice_time,
                    attendance_notice = excluded.attendance_notice,
                    latitude = excluded.latitude,
                    longitude = excluded.longitude,
                    location_accuracy = excluded.location_accuracy,
                    location_label = excluded.location_label
            """, (
                att_id, c_id, session_id, student_id, confidence, status, otp_used, now_iso,
                doc["notice_time"], attendance_notice, latitude, longitude, location_accuracy, location_label,
            ))
            conn.commit()
            return doc
        except Exception as e:
            print(f"[DB] SQLite error recording attendance: {e}")
            return None
        finally:
            conn.close()

    def get_attendance_by_id(self, attendance_id: str) -> Optional[Dict[str, Any]]:
        a_id = attendance_id.strip()
        if self.mode == "mongodb":
            return self.mongo_db.attendance.find_one({"attendance_id": a_id}, {"_id": 0})
        if self.mode == "mongodb_data_api":
            doc = self.data_api.find_one("attendance", {"attendance_id": a_id})
            if doc:
                doc.pop("_id", None)
            return doc
        conn = self._sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM attendance WHERE attendance_id = ?", (a_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_session_attendance(self, session_id: str, company_id: Optional[str] = None) -> List[Dict[str, Any]]:
        flt: Dict[str, Any] = {"session_id": session_id}
        if company_id:
            flt["company_id"] = company_id.strip()

        if self.mode in ("mongodb", "mongodb_data_api"):
            member_flt = {"company_id": company_id.strip()} if company_id else {}
            if self.mode == "mongodb":
                att_docs = list(self.mongo_db.attendance.find(flt, {"_id": 0}).sort("timestamp", 1))
                members = {m["student_id"]: m for m in self.mongo_db.students.find(member_flt, {"_id": 0, "embedding": 0})}
            else:
                att_docs = self.data_api.find("attendance", flt, sort={"timestamp": 1})
                members = {m["student_id"]: m for m in self.data_api.find("students", member_flt)}

            results = []
            for idx, r in enumerate(att_docs, start=1):
                r.pop("_id", None)
                member = members.get(r["student_id"], {})
                results.append({
                    "id": idx,
                    "attendance_id": r.get("attendance_id", ""),
                    "company_id": r.get("company_id", DEFAULT_COMPANY_ID),
                    "session_id": r["session_id"],
                    "student_id": r["student_id"],
                    "student_name": member.get("name", "N/A"),
                    "department": member.get("department", "N/A"),
                    "timestamp": r["timestamp"],
                    "notice_time": r.get("notice_time", r["timestamp"]),
                    "attendance_notice": r.get("attendance_notice", "RECORDED"),
                    "latitude": r.get("latitude"),
                    "longitude": r.get("longitude"),
                    "location_label": r.get("location_label", "Not Provided"),
                    "confidence": r["confidence"],
                    "status": r["status"],
                    "otp_used": r["otp_used"],
                })
            return results

        conn = self._sqlite_conn()
        cursor = conn.cursor()
        if company_id:
            cursor.execute("""
                SELECT a.id, a.attendance_id, a.company_id, a.session_id, a.student_id,
                       s.name as student_name, s.department, a.timestamp, a.notice_time,
                       a.attendance_notice, a.latitude, a.longitude, a.location_label,
                       a.confidence, a.status, a.otp_used
                FROM attendance a
                LEFT JOIN students s ON a.student_id = s.student_id AND a.company_id = s.company_id
                WHERE a.session_id = ? AND a.company_id = ?
                ORDER BY a.timestamp ASC
            """, (session_id, company_id.strip()))
        else:
            cursor.execute("""
                SELECT a.id, a.attendance_id, a.company_id, a.session_id, a.student_id,
                       s.name as student_name, s.department, a.timestamp, a.notice_time,
                       a.attendance_notice, a.latitude, a.longitude, a.location_label,
                       a.confidence, a.status, a.otp_used
                FROM attendance a
                LEFT JOIN students s ON a.student_id = s.student_id
                WHERE a.session_id = ?
                ORDER BY a.timestamp ASC
            """, (session_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]


db = DatabaseManager()


def init_db():
    db.init_db()


def get_db_status() -> Dict[str, Any]:
    return db.get_status()


def register_company(company_id: str, company_name: str, admin_name: str, admin_pin: str, org_type: str = "general") -> Dict[str, Any]:
    return db.register_company(company_id, company_name, admin_name, admin_pin, org_type)


def authenticate_company(company_id: str, admin_pin: str) -> Optional[Dict[str, Any]]:
    return db.authenticate_company(company_id, admin_pin)


def get_company(company_id: str) -> Optional[Dict[str, Any]]:
    return db.get_company(company_id)


def list_companies() -> List[Dict[str, Any]]:
    return db.list_companies()


def save_student(student_id: str, name: str, department: str, embedding: List[float], company_id: str = DEFAULT_COMPANY_ID):
    db.save_student(student_id, name, department, embedding, company_id)


def get_student(student_id: str, company_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return db.get_student(student_id, company_id)


def list_students(company_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return db.list_students(company_id)


def create_session(session_id: str, course_name: str, company_id: str = DEFAULT_COMPANY_ID, cutoff_time: Optional[str] = None) -> Dict[str, Any]:
    return db.create_session(session_id, course_name, company_id, cutoff_time)


def get_active_session(company_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return db.get_active_session(company_id)


def get_session(session_id: str, company_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return db.get_session(session_id, company_id)


def list_sessions(company_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return db.list_sessions(company_id)


def verify_device(device_key: str) -> Optional[str]:
    return db.verify_device(device_key)


def register_company_device(company_id: str, device_key: str, device_name: str = "ESP32-LED-Matrix"):
    db.register_company_device(company_id, device_key, device_name)


def get_device_info(company_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return db.get_device_info(company_id)


def save_new_otp(code: str, session_id: str, device_id: str, created_at: str, expires_at: str, company_id: str = DEFAULT_COMPANY_ID):
    db.save_new_otp(code, session_id, device_id, created_at, expires_at, company_id)


def get_current_valid_otp(session_id: Optional[str] = None, company_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return db.get_current_valid_otp(session_id, company_id)


def is_otp_valid(code: str, session_id: str, company_id: str = DEFAULT_COMPANY_ID) -> bool:
    return db.is_otp_valid(code, session_id, company_id)


def validate_and_consume_otp(code: str, session_id: str, company_id: str = DEFAULT_COMPANY_ID) -> bool:
    return db.validate_and_consume_otp(code, session_id, company_id)


def record_attendance(
    session_id: str,
    student_id: str,
    confidence: float,
    status: str = "PRESENT",
    otp_used: str = "",
    company_id: str = DEFAULT_COMPANY_ID,
    notice_time: str = "",
    attendance_notice: str = "RECORDED",
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    location_accuracy: Optional[float] = None,
    location_label: str = "Not Provided",
) -> Optional[Dict[str, Any]]:
    return db.record_attendance(
        session_id, student_id, confidence, status, otp_used,
        company_id, notice_time, attendance_notice, latitude, longitude, location_accuracy, location_label,
    )


def get_attendance_by_id(attendance_id: str) -> Optional[Dict[str, Any]]:
    return db.get_attendance_by_id(attendance_id)


def get_session_attendance(session_id: str, company_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return db.get_session_attendance(session_id, company_id)
