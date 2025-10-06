import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path("data/deployments.db")
DATABASE_URL = DATABASE_PATH


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_connection():
    db_path = BASE_DIR / DATABASE_URL
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def database_connection():
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db():
    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS deployments (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                resource_group TEXT NOT NULL,
                repo_url TEXT NOT NULL,
                repo_branch TEXT NOT NULL,
                language TEXT NOT NULL,
                runtime TEXT,
                startup_file TEXT,
                azure_region TEXT NOT NULL,
                playbook_filename TEXT,
                awx_template_id INTEGER,
                awx_job_id INTEGER,
                status TEXT NOT NULL,
                app_url TEXT,
                error_message TEXT,
                params_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def row_to_deployment(row):
    params = json.loads(row["params_json"])
    return {
        "id": row["id"],
        "params": params,
        "status": row["status"],
        "playbook_filename": row["playbook_filename"],
        "awx_template_id": row["awx_template_id"],
        "awx_job_id": row["awx_job_id"],
        "app_url": row["app_url"],
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_deployment(deployment_id, params):
    init_db()
    now = utc_now()
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO deployments (
                id, name, resource_group, repo_url, repo_branch, language, runtime,
                startup_file, azure_region, status, params_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                deployment_id,
                params["nom_app"],
                params["resource_group"],
                params["app_repo_url"],
                params["app_repo_branch"],
                params["app_language"],
                params.get("python_version"),
                params.get("startup_file"),
                params["location"],
                "CREATED",
                json.dumps(params),
                now,
                now,
            ),
        )


def list_deployments():
    init_db()
    with database_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM deployments ORDER BY created_at DESC"
        ).fetchall()
    return [row_to_deployment(row) for row in rows]


def get_deployment(deployment_id):
    init_db()
    with database_connection() as connection:
        row = connection.execute(
            "SELECT * FROM deployments WHERE id = ?",
            (deployment_id,),
        ).fetchone()

    if row is None:
        return None
    return row_to_deployment(row)


def update_deployment(deployment_id, **fields):
    if not fields:
        return

    init_db()
    fields["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in fields)
    values = list(fields.values())
    values.append(deployment_id)

    with database_connection() as connection:
        connection.execute(
            f"UPDATE deployments SET {assignments} WHERE id = ?",
            values,
        )


def update_deployment_status(deployment_id, status, error_message=None):
    update_deployment(
        deployment_id,
        status=status,
        error_message=error_message,
    )
