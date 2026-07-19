"""Provision a device user. A real instrument does this at manufacture."""
import sys, os, getpass

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "device_app"))

from config import load_config
from store import open_db, create_user, list_users, audit
from auth import hash_password, ROLES


def main():
    if len(sys.argv) != 3 or sys.argv[2] not in ROLES:
        print(f"usage: useradd.py <username> <{'|'.join(sorted(ROLES))}>")
        return 1

    username, role = sys.argv[1], sys.argv[2]
    cfg = load_config(os.path.join(_here, "..", "config.json"))
    conn = open_db(cfg["db_path"])

    pw = getpass.getpass("password: ")
    if len(pw) < 8:
        print("password must be at least 8 characters")
        return 1
    if pw != getpass.getpass("confirm: "):
        print("passwords do not match")
        return 1

    try:
        create_user(conn, username, hash_password(pw), role)
    except Exception as e:
        print(f"failed: {e}")
        return 1

    audit(conn, {"action": "user_created", "username": username, "role": role})
    print(f"created {username} ({role})")
    print("users:", [f"{u}({r})" for u, r, _ in list_users(conn)])
    return 0


if __name__ == "__main__":
    sys.exit(main())