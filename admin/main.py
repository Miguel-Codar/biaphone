import os
import json
import re
from datetime import datetime
from pathlib import Path

import docker as docker_sdk
import httpx
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

load_dotenv()

ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD",  "admin123")
ADMIN_SECRET    = os.getenv("ADMIN_SECRET",    "change-this-secret-key")
CLIENTS_DIR     = Path(os.getenv("CLIENTS_DIR",    "/clients"))
BOT_IMAGE       = os.getenv("BOT_IMAGE",       "biaphone:latest")
BOT_SOURCE_DIR  = os.getenv("BOT_SOURCE_DIR",  "/bot_source")
BASE_PORT       = int(os.getenv("BASE_PORT",   "8000"))

# Evolution API compartilhada (uma só pra todos os clientes)
EVO_HOST        = os.getenv("EVOLUTION_HOST",    "").rstrip("/")
EVO_KEY         = os.getenv("EVOLUTION_API_KEY", "")

# Chave Groq compartilhada (usada se o cliente não tiver a própria)
GROQ_KEY_SHARED = os.getenv("GROQ_API_KEY", "")

REGISTRY_FILE   = CLIENTS_DIR / "registry.json"

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


def vps_host(request: Request) -> str:
    """Retorna o IP/host da VPS a partir da requisição, ou do .env."""
    override = os.getenv("VPS_HOST", "")
    if override:
        return override
    host = request.headers.get("host", "localhost")
    return host.split(":")[0]


# ─── Evolution API (compartilhada) ───────────────────────────

async def evo_create_instance(name: str) -> dict:
    """Cria uma nova instância na Evolution API."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{EVO_HOST}/instance/create",
            headers={"apikey": EVO_KEY, "Content-Type": "application/json"},
            json={"instanceName": name, "qrcode": False, "integration": "WHATSAPP-BAILEYS"},
        )
        # 409 = instância já existe, continua normalmente
        if r.status_code not in (200, 201, 409):
            raise Exception(f"Evolution API ({r.status_code}): {r.text[:200]}")
        return r.json() if r.is_success else {}


async def evo_set_webhook(name: str, webhook_url: str):
    """Configura o webhook da instância apontando pro bot do cliente."""
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{EVO_HOST}/webhook/set/{name}",
            headers={"apikey": EVO_KEY, "Content-Type": "application/json"},
            json={
                "webhook": {
                    "enabled": True,
                    "url": webhook_url,
                    "events": ["MESSAGES_UPSERT"],
                    "byEvents": True,
                }
            },
        )


async def evo_get_qr(name: str) -> str:
    """Busca o QR code da instância (base64)."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{EVO_HOST}/instance/connect/{name}",
            headers={"apikey": EVO_KEY},
        )
        r.raise_for_status()
        data = r.json()
        qr = data.get("base64") or data.get("qrcode", {}).get("base64", "") or data.get("qr", "")
        if qr and not qr.startswith("data:"):
            qr = f"data:image/png;base64,{qr}"
        return qr


async def evo_connection_state(name: str) -> str:
    """Retorna o estado de conexão da instância: open | connecting | close | ..."""
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.get(
            f"{EVO_HOST}/instance/connectionState/{name}",
            headers={"apikey": EVO_KEY},
        )
        data = r.json()
        return data.get("instance", {}).get("state", data.get("state", "unknown"))


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

    evo_ok = bool(EVO_HOST and EVO_KEY)

    return templates.TemplateResponse("dashboard.html", {
        "request":    request,
        "clients":    registry,
        "next_port":  next_port(registry),
        "total":      len(registry),
        "running":    sum(1 for c in registry if c.get("status") == "running"),
        "evo_ok":     evo_ok,
    })


# ─── Criar novo cliente (AJAX) ────────────────────────────────

@app.post("/clients/new")
async def new_client(
    request:          Request,
    name:             str = Form(...),
    store_name:       str = Form(...),
    store_address:    str = Form(""),
    atendente_number: str = Form(""),
    port:             int = Form(...),
    groq_api_key:     str = Form(""),   # opcional — usa chave compartilhada se vazio
):
    if not auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    name = re.sub(r"[^a-z0-9_-]", "", name.lower().replace(" ", "-"))
    if not name:
        return JSONResponse({"error": "Nome inválido — use apenas letras minúsculas, números e hífens."}, status_code=400)

    registry = load_registry()
    if any(c["name"] == name for c in registry):
        return JSONResponse({"error": f"Cliente '{name}' já existe."}, status_code=400)

    groq_key = groq_api_key.strip() or GROQ_KEY_SHARED
    if not groq_key:
        return JSONResponse({"error": "Nenhuma chave Groq disponível. Configure GROQ_API_KEY no .env ou informe no formulário."}, status_code=400)

    # 1. Criar instância na Evolution API
    try:
        await evo_create_instance(name)
    except Exception as e:
        return JSONResponse({"error": f"Erro ao criar instância Evolution: {e}"}, status_code=500)

    # 2. Configurar webhook apontando pro container do novo cliente
    vps = vps_host(request)
    webhook_url = f"http://{vps}:{port}/webhook/whatsapp"
    try:
        await evo_set_webhook(name, webhook_url)
    except Exception as e:
        return JSONResponse({"error": f"Instância criada, mas webhook falhou: {e}"}, status_code=500)

    # 3. Criar diretório e .env do cliente
    client_dir = CLIENTS_DIR / name
    (client_dir / "data").mkdir(parents=True, exist_ok=True)

    env_lines = [
        f"GROQ_API_KEY={groq_key}",
        "GROQ_MODEL=llama-3.3-70b-versatile",
        f"EVOLUTION_HOST={EVO_HOST}",
        f"EVOLUTION_API_KEY={EVO_KEY}",
        f"EVOLUTION_INSTANCE={name}",
        f"ATENDENTE_NUMBER={atendente_number}",
        "WEBHOOK_SECRET=topphone2026",
        f"STORE_NAME={store_name}",
        f"STORE_ADDRESS={store_address}",
        "SESSION_TIMEOUT=30",
        "CONVERSATION_TONE=informal",
    ]
    (client_dir / ".env").write_text("\n".join(env_lines))

    # 4. Subir container Docker
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
        return JSONResponse({"error": f"Instância e .env criados, mas container falhou: {e}"}, status_code=500)

    # 5. Registrar
    registry.append({
        "name":       name,
        "store_name": store_name,
        "port":       port,
        "created_at": datetime.utcnow().isoformat(),
    })
    save_registry(registry)

    return JSONResponse({"ok": True, "name": name, "port": port})


# ─── QR Code e status de conexão ──────────────────────────────

@app.get("/clients/{name}/qr")
async def get_qr(name: str, request: Request):
    if not auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        qr = await evo_get_qr(name)
        if not qr:
            return JSONResponse({"error": "QR não disponível. A instância pode já estar conectada."}, status_code=404)
        return {"qr": qr}
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/clients/{name}/wa-status")
async def wa_status(name: str, request: Request):
    if not auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        state = await evo_connection_state(name)
        return {"state": state, "connected": state == "open"}
    except Exception as e:
        return {"state": "error", "connected": False, "detail": str(e)}


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

@app.post("/rebuild-restart")
async def rebuild_restart(request: Request):
    if not auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    src = Path(BOT_SOURCE_DIR)
    if not (src / "Dockerfile").exists():
        raise HTTPException(500, "Dockerfile não encontrado em BOT_SOURCE_DIR.")

    try:
        dc().images.build(path=str(src), tag=BOT_IMAGE, rm=True)
    except Exception as e:
        raise HTTPException(500, f"Build falhou: {e}")

    restarted, errors = [], []
    for c in dc().containers.list(all=True, filters={"name": "biaphone_"}):
        if "biaphone_admin" in c.name:
            continue
        try:
            c.restart(timeout=10)
            restarted.append(c.name)
        except Exception as e:
            errors.append(f"{c.name}: {e}")

    return {"ok": not errors, "restarted": restarted, "errors": errors}
