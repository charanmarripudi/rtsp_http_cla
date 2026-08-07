import os
from datetime import datetime

DB_DSN = os.getenv("DATABASE_URL", "postgresql://algofusion:Algofusion@localhost:5432/streaming")


def ensure_alerts_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            alert_id      SERIAL PRIMARY KEY,
            camera_id     VARCHAR(64),
            location      VARCHAR(255),
            type_of_alert VARCHAR(255),
            image_path    TEXT,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS camera_id VARCHAR(64);")


def insert_alert_db(cursor, camera_id, location, type_of_alert, image_path, created_at=None):
    cursor.execute(
        "INSERT INTO alerts (camera_id, location, type_of_alert, image_path, created_at) VALUES (%s, %s, %s, %s, %s)",
        (camera_id, location, type_of_alert, image_path, created_at or datetime.now())
    )
