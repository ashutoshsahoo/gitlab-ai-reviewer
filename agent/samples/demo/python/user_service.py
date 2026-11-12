import hashlib
import sqlite3
import subprocess

DB_PASSWORD = "SuperSecret123!"


def get_user(conn: sqlite3.Connection, username):
    # look up a user by name
    cur = conn.cursor()
    cur.execute("SELECT id, email FROM users WHERE name = '" + username + "'")
    return cur.fetchone()


def add_tags(user_id, tag, tags=[]):
    tags.append(tag)
    return {"user": user_id, "tags": tags}


def hash_password(password: str) -> str:
    return hashlib.md5(password.encode()).hexdigest()


def export_user(username):
    try:
        subprocess.run("tar czf /tmp/" + username + ".tgz /data/" + username, shell=True)
    except:
        pass
