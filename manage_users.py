"""Provision accounts from a trusted terminal, never from the public portal."""
import argparse
from getpass import getpass
import os
from pathlib import Path
from dotenv import load_dotenv
from engine.auth import Auth


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--role", choices=["admin", "doctor"], required=True)
    args = parser.parse_args()
    password = getpass("Password (at least 12 characters): ")
    if getpass("Confirm password: ") != password:
        parser.error("Passwords do not match.")
    data_dir = os.getenv("DEID_DATA_DIR", str(Path(__file__).parent / ".deid-data"))
    Auth(data_dir).add_user(args.username, password, args.role)
    print(f"Created {args.role} account: {args.username}")


if __name__ == "__main__":
    main()
