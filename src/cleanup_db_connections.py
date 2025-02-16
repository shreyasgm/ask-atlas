import psycopg2
import pandas as pd
import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Set up paths and load environment variables
BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=BASE_DIR / ".env")
db_url = os.environ.get("ATLAS_DB_URL")


def log_and_cleanup_connections():
    # Create logs directory if it doesn't exist
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)

    # Set up log file with today's date
    today = datetime.now().strftime("%Y%m%d")
    log_file = log_dir / f"db_connections_{today}.log"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entries = [f"\n{'=' * 80}", f"Connection check at {timestamp}", f"{'-' * 40}"]

    try:
        with psycopg2.connect(db_url) as conn:
            with conn.cursor() as cursor:
                # Get connection statistics
                cursor.execute("""
                    SELECT state, usename, COUNT(*) 
                    FROM pg_stat_activity 
                    WHERE usename != 'rdsadmin'
                    GROUP BY state, usename
                    ORDER BY usename, state;
                """)
                stats = cursor.fetchall()

                log_entries.append("\nConnections by state and user:")
                for state, user, count in stats:
                    state_str = state if state else "null"
                    log_entries.append(f"- {user:<10} | {state_str:<25} | {count:>3}")

                # Get idle connections for cleanup
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM pg_stat_activity 
                    WHERE state IN ('idle', 'idle in transaction', 'idle in transaction (aborted)')
                    AND usename IN ('readonly', 'postgres')
                    AND pid != pg_backend_pid();
                """)
                idle_count = cursor.fetchone()[0]

                # Terminate idle connections
                cursor.execute("""
                    SELECT pg_terminate_backend(pid) 
                    FROM pg_stat_activity 
                    WHERE state IN ('idle', 'idle in transaction', 'idle in transaction (aborted)')
                    AND usename IN ('readonly', 'postgres')
                    AND pid != pg_backend_pid();
                """)
                conn.commit()

                log_entries.append(f"\nCleaned up {idle_count} idle connection(s)")

    except Exception as e:
        log_entries.append(f"\nERROR: {str(e)}")

    # Write to log file
    with open(log_file, "a") as f:
        f.write("\n".join(log_entries) + "\n")


if __name__ == "__main__":
    log_and_cleanup_connections()
