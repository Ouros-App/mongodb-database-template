#!/usr/bin/env python3
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pymongo import MongoClient, ReturnDocument


ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def load_config(root: Path) -> dict:
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))

    def expand(value):
        if isinstance(value, str):
            return ENV_RE.sub(lambda match: os.environ[match.group(1)], value)
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        return value

    return expand(raw)


def script_entries(root: Path, cfg: dict) -> list[tuple[Path, str]]:
    directory = root / cfg["database"]["scripts_path"]
    entries, seen = [], set()
    for item in cfg["database"]["execution_order"]:
        name, mode = (item, "on_change") if isinstance(item, str) else (item.get("file"), item.get("mode", "on_change"))
        path = Path(name) if isinstance(name, str) else None
        identity = path.as_posix() if path else ""
        if not path or path.is_absolute() or ".." in path.parts or path.suffix != ".json" or identity in seen or mode not in {"always", "on_change", "once", "never"}:
            raise ValueError("Cada script exige arquivo JSON relativo e modo valido.")
        target = directory / path
        if not target.is_file():
            raise FileNotFoundError(f"Script MongoDB nao encontrado: {target}")
        seen.add(identity)
        entries.append((target, mode))
    return entries


def git_value(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def apply_scripts(root: Path, cfg: dict, db, commit_id: str) -> None:
    control = db["controle_scripts_mongo"]
    control.create_index("arquivo", unique=True)
    base = root / cfg["database"]["scripts_path"]
    for path, mode in script_entries(root, cfg):
        identity = path.relative_to(base).as_posix()
        content = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(content.encode()).hexdigest()
        previous = control.find_one({"arquivo": identity})
        if mode == "never" or (mode == "once" and previous) or (mode == "on_change" and previous and previous["checksum"] == checksum):
            print(f"[SKIP] {identity}: {mode}")
            continue
        commands = json.loads(content)
        for command in commands if isinstance(commands, list) else [commands]:
            db.command(command)
        control.update_one({"arquivo": identity}, {"$set": {"checksum": checksum, "commit_id": commit_id}}, upsert=True)
        print(f"[RUN] {identity}: {mode}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    cfg = load_config(root)
    db_cfg = cfg["database"]
    client = MongoClient(host=db_cfg["host"], port=int(db_cfg["port"]), username=db_cfg["user"], password=db_cfg["password"], authSource=db_cfg["auth_database"])
    db = client[db_cfg["name"]]
    commit_id = os.getenv("GITHUB_SHA") or git_value(root, "rev-parse", "HEAD")
    commit_message = os.getenv("GITHUB_COMMIT_MESSAGE") or git_value(root, "log", "-1", "--pretty=%B")
    if commit_id == "unknown":
        raise RuntimeError("Nao foi possivel identificar o commit atual.")
    apply_scripts(root, cfg, db, commit_id)
    version = db["controle_contadores"].find_one_and_update({"_id": "versao"}, {"$inc": {"value": 1}}, upsert=True, return_document=ReturnDocument.AFTER)["value"]
    db[db_cfg["version_collection"]].insert_one({"versao": version, "commit_id": commit_id, "comentario_commit": commit_message})
    client.close()


if __name__ == "__main__":
    main()
