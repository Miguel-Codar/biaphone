#!/usr/bin/env python3
"""
Deploy automático — git pull + docker compose up --build em todas as VPS.

Uso:
    python deploy.py                  # deploya todos os clientes
    python deploy.py topphone         # deploya apenas um cliente
    python deploy.py --list           # lista clientes configurados
    python deploy.py --logs topphone  # exibe logs do container
"""
import json
import subprocess
import sys
from pathlib import Path

CLIENTS_FILE = Path(__file__).parent / "clients.json"

SEP = "─" * 52


def load_clients() -> list[dict]:
    if not CLIENTS_FILE.exists():
        print(f"❌  Arquivo não encontrado: {CLIENTS_FILE}")
        sys.exit(1)
    with open(CLIENTS_FILE) as f:
        return json.load(f)


def ssh(client: dict, command: str) -> int:
    host = client["host"]
    user = client.get("user", "root")
    port = client.get("port", 22)
    key  = client.get("ssh_key")

    args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=15",
        "-p", str(port),
    ]
    if key:
        args += ["-i", key]
    args += [f"{user}@{host}", command]

    return subprocess.run(args).returncode


def deploy(client: dict) -> bool:
    name = client["name"]
    path = client["path"]
    note = client.get("notes", "")

    print(f"\n{SEP}")
    print(f"  {name}  {client.get('user','root')}@{client['host']}:{path}")
    if note:
        print(f"  {note}")
    print(SEP)

    cmd = f"cd '{path}' && git pull && docker compose up -d --build 2>&1"
    rc  = ssh(client, cmd)

    if rc == 0:
        print(f"✅  [{name}] Deploy concluído!")
        return True
    print(f"❌  [{name}] Falhou (exit {rc})")
    return False


def show_logs(client: dict):
    path = client["path"]
    cmd  = f"cd '{path}' && docker compose logs --tail=50 2>&1"
    ssh(client, cmd)


def list_clients(clients: list[dict]):
    print(f"\n{'Nome':<20} {'Host':<18} {'Porta':<7} {'Path'}")
    print("─" * 68)
    for c in clients:
        print(f"{c['name']:<20} {c['host']:<18} {str(c.get('port',22)):<7} {c['path']}")


def main():
    args    = sys.argv[1:]
    clients = load_clients()

    if "--list" in args:
        list_clients(clients)
        return

    if "--logs" in args:
        idx    = args.index("--logs")
        target = args[idx + 1] if idx + 1 < len(args) else None
        found  = [c for c in clients if c["name"] == target] if target else clients
        if not found:
            print(f"❌  Cliente '{target}' não encontrado")
            sys.exit(1)
        for c in found:
            show_logs(c)
        return

    target = next((a for a in args if not a.startswith("-")), None)
    if target:
        clients = [c for c in clients if c["name"] == target]
        if not clients:
            print(f"❌  Cliente '{target}' não encontrado em clients.json")
            sys.exit(1)

    errors = []
    for client in clients:
        if not deploy(client):
            errors.append(client["name"])

    print(f"\n{'═' * 52}")
    if errors:
        print(f"⚠️   Falhou em: {', '.join(errors)}")
        sys.exit(1)
    print(f"🎉  Deploy concluído em {len(clients)} cliente(s)!")


if __name__ == "__main__":
    main()
