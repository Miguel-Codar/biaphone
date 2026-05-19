import os
import json
import re
from datetime import datetime
from pathlib import Path

import docker as docker_sdk
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

load_dotenv()

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_SECRET   = os.getenv("ADMIN_SECRET",   "change-this-secret-key")
CLIENTS_DIR    = Path(os.getenv("CLIENTS_DIR",    "/clients"))
BOT_IMAGE      = os.getenv("BOT_IMAGE",       "biaphone:latest")
BOT_SOURCE_DIR = os.getenv("BOT_SOURCE_DIR",  "/bot_source")
BASE_PORT      = int(os.getenv("BASE_PORT",   "8000"))

REGISTRY_FILE  = CLIENTS_DIR / "registry.json"

app = FastAPI(title="Biaphone Admin")
app.add_middleware(SessionMiddleware, secret_key=ADMIN_SECRET)

templates = Jinja2Templates(directory="templates")


# ─── Utilitários ──────────────────────────────────────────────

def dc():
    return docker_sdk.from_env()


def load_registry() -> list:
    if not REGISTRY_FILE.exists():
        return []
    with open(REGISTRY_FILE) as f:
        return json.load(f)


def save_registry(data: list):
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def next_port(registry: list) -> int:
    used = {c["port"] for c in registry}
    port = BASE_PORT
    while port in used:
        port += 1
    return port


def auth(request: Request) -> bool:
    return bool(request.session.get("authenticated"))


def container_status(name: str) -> str:
    try:
        return dc().containers.get(f"biaphone_{name}").status
    except docker_sdk.errors.NotFound:
        return "not_found"
    except Exception:
        return "error"


# ─── Login ────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if auth(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, password: str = Form("")):
    if password == ADMIN_PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Senha incorreta."})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ─── Dashboard ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not auth(request):
        return RedirectResponse("/login", status_code=302)

    registry = load_registry()
    for client in registry:
        client["status"] = container_status(client["name"])

    return templates.TemplateResponse("dashboard.html", {
        "request":    request,
        "clients":    registry,
        "next_port":  next_port(registry),
        "total":      len(registry),
        "running":    sum(1 for c in registry if c.get("status") == "running"),
        "created":    request.query_params.get("created"),
    })


# ─── Criar novo cliente ───────────────────────────────────────

@app.post("/clients/new")
async def new_client(
    request:             Request,
    name:                str = Form(...),
    store_name:          str = Form(...),
    store_address:       str = Form(""),
    evolution_host:      str = Form(...),
    evolution_api_key:   str = Form(...),
    evolution_instance:  str = Form(...),
    groq_api_key:        str = Form(...),
    atendente_number:    str = Form(""),
    port:                int = Form(...),
):
    if not auth(request):
        return RedirectResponse("/login", status_code=302)

    name = re.sub(r"[^a-z0-9_-]", "", name.lower().replace(" ", "-"))
    if not name:
        raise HTTPException(400, "Nome inválido — use apenas letras minúsculas, números e hífens.")

    registry = load_registry()
    if any(c["name"] == name for c in registry):
        raise HTTPException(400, f"Cliente '{name}' já existe no registro.")

    # Diretório e .env do cliente
    client_dir = CLIENTS_DIR / name
    (client_dir / "data").mkdir(parents=True, exist_ok=True)

    env_lines = [
        f"GROQ_API_KEY={groq_api_key}",
        "GROQ_MODEL=llama-3.3-70b-versatile",
        f"EVOLUTION_HOST={evolution_host}",
        f"EVOLUTION_API_KEY={evolution_api_key}",
        f"EVOLUTION_INSTANCE={evolution_instance}",
        f"ATENDENTE_NUMBER={atendente_number}",
        "WEBHOOK_SECRET=topphone2026",
        f"STORE_NAME={store_name}",
        f"STORE_ADDRESS={store_address}",
        "SESSION_TIMEOUT=30",
        "CONVERSATION_TONE=informal",
    ]
    (client_dir / ".env").write_text("\n".join(env_lines))

    # Sobe container
    try:
        dc().containers.run(
            image=BOT_IMAGE,
            name=f"biaphone_{name}",
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            ports={"8000/tcp": port},
            volumes={
                str(client_dir / ".env"):  {"bind": "/app/.env",  "mode": "rw"},
                str(client_dir / "data"):  {"bind": "/app/data",  "mode": "rw"},
            },
            environment={"DATABASE_URL": "sqlite:////app/data/bot.db"},
        )
    except Exception as e:
        raise HTTPException(500, f"Erro ao iniciar container: {e}")

    registry.append({
        "name":       name,
        "store_name": store_name,
        "port":       port,
        "created_at": datetime.utcnow().isoformat(),
    })
    save_registry(registry)

    return RedirectResponse("/?created=1", status_code=302)


# ─── Ações nos containers ─────────────────────────────────────

@app.post("/clients/{name}/restart")
async def restart(name: str, request: Request):
    if not auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        dc().containers.get(f"biaphone_{name}").restart(timeout=10)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/clients/{name}/stop")
async def stop(name: str, request: Request):
    if not auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        dc().containers.get(f"biaphone_{name}").stop(timeout=10)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/clients/{name}/start")
async def start(name: str, request: Request):
    if not auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        dc().containers.get(f"biaphone_{name}").start()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/clients/{name}")
async def delete(name: str, request: Request):
    if not auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        c = dc().containers.get(f"biaphone_{name}")
        c.stop(timeout=5)
        c.remove()
    except docker_sdk.errors.NotFound:
        pass
    except Exception as e:
        raise HTTPException(500, str(e))

    save_registry([c for c in load_registry() if c["name"] != name])
    return {"ok": True}


@app.get("/clients/{name}/logs")
async def logs(name: str, request: Request, tail: int = 100):
    if not auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        raw = dc().containers.get(f"biaphone_{name}").logs(tail=tail, timestamps=True)
        return {"logs": raw.decode("utf-8", errors="replace")}
    except Exception as e:
        raise HTTPException(500, str(e))


# ─── Rebuild imagem do bot ────────────────────────────────────

@app.post("/rebuild")
async def rebuild(request: Request):
    if not auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    src = Path(BOT_SOURCE_DIR)
    if not (src / "Dockerfile").exists():
        raise HTTPException(500, "Dockerfile não encontrado em BOT_SOURCE_DIR.")

    try:
        image, _ = dc().images.build(path=str(src), tag=BOT_IMAGE, rm=True)
        return {"ok": True, "image": image.tags}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/rebuild-restart")
async def rebuild_restart(request: Request):
    if not auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    src = Path(BOT_SOURCE_DIR)
    if not (src / "Dockerfile").exists():
        raise HTTPException(500, "Dockerfile não encontrado.")

    try:
        dc().images.build(path=str(src), tag=BOT_IMAGE, rm=True)
    except Exception as e:
        raise HTTPException(500, f"Build falhou: {e}")

    errors = []
    for c in dc().containers.list(all=True, filters={"name": "biaphone_"}):
        if "biaphone_admin" in c.name:
            continue
        try:
            c.restart(timeout=10)
        except Exception as e:
            errors.append(f"{c.name}: {e}")

    return {"ok": not errors, "restarted": [c.name for c in dc().containers.list(filters={"name": "biaphone_"})], "errors": errors}
