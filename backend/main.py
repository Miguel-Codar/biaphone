import asyncio
import base64
import io
import os
import json
from datetime import datetime, timedelta
from typing import Optional
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Depends, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session as DBSession, select
from dotenv import load_dotenv

load_dotenv()

from .database import create_db, get_session, engine, get_config, set_config
from .models import Lead, Config, Document
from .bot import process_message, DEFAULT_CUSTOM_PROMPT, send_whatsapp, _get_atendentes


# ─── TASK: lembrete de leads sem resposta ─────────────────────────────────────

async def _daily_report():
    """Envia relatório diário às 20h para os atendentes."""
    await asyncio.sleep(90)
    while True:
        now = datetime.now()
        target = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if now.hour >= 20:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            with DBSession(engine) as db:
                leads = db.exec(select(Lead)).all()
            hoje = [l for l in leads if l.created_at and l.created_at >= today]
            total       = len(hoje)
            convertidos = sum(1 for l in hoje if l.status == "convertido")
            em_analise  = sum(1 for l in hoje if l.status == "em_analise")
            perdidos    = sum(1 for l in hoje if l.status == "perdido")
            boleto      = sum(1 for l in hoje if l.payment_type == "boleto")
            cartao      = sum(1 for l in hoje if l.payment_type == "cartao_avista")
            assistencia = sum(1 for l in hoje if l.payment_type == "assistencia")
            loja        = get_config("store_name") or "Top Phone"
            texto = (
                f"📊 *RELATÓRIO DO DIA — {loja}*\n"
                f"📅 {datetime.now().strftime('%d/%m/%Y')}\n\n"
                f"👥 *Leads hoje:* {total}\n"
                f"✅ Convertidos: {convertidos}\n"
                f"🔄 Em análise: {em_analise}\n"
                f"❌ Perdidos: {perdidos}\n\n"
                f"💳 Boleto: {boleto}\n"
                f"💰 Cartão/À vista: {cartao}\n"
                f"🔧 Assistência: {assistencia}"
            )
            for atendente in _get_atendentes():
                await send_whatsapp(atendente, texto, apply_delay=False)
            print(f"[RELATÓRIO DIÁRIO] Enviado para {len(_get_atendentes())} atendente(s).")
        except Exception as e:
            print(f"[RELATÓRIO DIÁRIO] Erro: {e}")


async def _check_unanswered_leads():
    """Background task: notifica atendentes sobre leads aguardando resposta."""
    await asyncio.sleep(30)  # aguarda o app subir antes de começar
    while True:
        await asyncio.sleep(60)  # verifica a cada minuto
        try:
            timeout_min = int(get_config("notification_timeout") or "10")
            atendentes  = _get_atendentes()
            if not atendentes:
                continue

            cutoff        = datetime.utcnow() - timedelta(minutes=timeout_min)
            remind_cutoff = datetime.utcnow() - timedelta(minutes=timeout_min * 2)

            with DBSession(engine) as db:
                leads = db.exec(select(Lead).where(Lead.status == "em_analise")).all()
                for lead in leads:
                    if not lead.last_message_at:
                        continue
                    if lead.last_message_at > cutoff:
                        continue  # não passou tempo suficiente ainda
                    if lead.notified_at and lead.notified_at > remind_cutoff:
                        continue  # lembrete recente, evita spam

                    wait_min  = int((datetime.utcnow() - lead.last_message_at).total_seconds() / 60)
                    loja      = get_config("store_name") or "Top Phone"
                    pag_map   = {"boleto": "Boleto", "cartao_avista": "Cartão/À vista", "assistencia": "Assistência"}
                    pag       = pag_map.get(lead.payment_type or "", "—")
                    assigned  = lead.assigned_to or "Ninguém"

                    text = (
                        f"⚠️ *LEMBRETE — {loja}*\n\n"
                        f"📱 Cliente *{lead.phone}* aguarda resposta há *{wait_min} min*!\n"
                        f"💳 Pagamento: {pag}\n"
                        f"👤 Atribuído: {assigned}\n\n"
                        f"Acesse o painel e assuma o atendimento."
                    )
                    for atendente in atendentes:
                        await send_whatsapp(atendente, text, apply_delay=False)

                    lead.notified_at = datetime.utcnow()
                    db.add(lead)
                db.commit()
        except Exception as e:
            print(f"[NOTIF TASK] Erro: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db()
    # Semeia config inicial do .env (útil ao duplicar para novo cliente)
    defaults = {
        "store_name":          os.getenv("STORE_NAME", "Top Phone"),
        "store_address":       os.getenv("STORE_ADDRESS", "Av. Dep. José Mendonça, 116 — Centro, Belo Jardim-PE"),
        "session_timeout":     os.getenv("SESSION_TIMEOUT", "30"),
        "conversation_tone":   os.getenv("CONVERSATION_TONE", "informal"),
        "response_delay":      "2",
        "notification_timeout":"10",
        "atendentes":          os.getenv("ATENDENTE_NUMBER", ""),
        "financeiras":         "BV,Santander,Itaú,Bradesco",
        "pay_boleto":          "1",
        "pay_cartao":          "1",
        "pay_assistencia":     "1",
    }
    for k, v in defaults.items():
        if not get_config(k):
            set_config(k, v)

    task1 = asyncio.create_task(_check_unanswered_leads())
    task2 = asyncio.create_task(_daily_report())
    yield
    task1.cancel()
    task2.cancel()


app = FastAPI(title="Top Phone Bot", lifespan=lifespan)

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "frontend/templates"))

try:
    app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "frontend/static")), name="static")
except Exception:
    pass


# ─── ÁUDIO: download + transcrição ────────────────────────────────────────────

async def _download_audio(key: dict, msg_obj: dict) -> Optional[str]:
    """Baixa áudio da Evolution API e retorna como base64."""
    host     = os.getenv("EVOLUTION_HOST", "").rstrip("/")
    api_key  = os.getenv("EVOLUTION_API_KEY", "")
    instance = os.getenv("EVOLUTION_INSTANCE", "")
    if not host or not instance:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r    = await client.post(
                f"{host}/chat/getBase64FromMediaMessage/{instance}",
                headers={"apikey": api_key, "Content-Type": "application/json"},
                json={"message": {"key": key, "message": msg_obj}},
            )
            data = r.json()
            return data.get("base64") or data.get("audio", {}).get("base64")
    except Exception as e:
        print(f"[AUDIO DL] Erro: {e}")
        return None


async def _transcribe_audio(b64_audio: str) -> Optional[str]:
    """Transcreve áudio usando Groq Whisper."""
    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key or not b64_audio:
        return None
    try:
        raw = b64_audio.split(",", 1)[1] if "," in b64_audio else b64_audio
        audio_bytes = base64.b64decode(raw)
        async with httpx.AsyncClient(timeout=40) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                files={"file": ("audio.ogg", io.BytesIO(audio_bytes), "audio/ogg")},
                data={"model": "whisper-large-v3", "response_format": "json", "language": "pt"},
            )
            r.raise_for_status()
            transcribed = r.json().get("text", "").strip()
            print(f"[WHISPER] Transcrito: {transcribed[:120]}")
            return transcribed or None
    except Exception as e:
        print(f"[WHISPER] Erro: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# WEBHOOK — Evolution API
# ═══════════════════════════════════════════════════════════════

@app.post("/webhook/whatsapp")
@app.post("/webhook/whatsapp/messages-upsert")
async def webhook(request: Request, db: DBSession = Depends(get_session)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "ignored"})

    event = body.get("event", "")
    if event not in ("messages.upsert", "message.created"):
        return JSONResponse({"status": "ignored"})

    data = body.get("data", {})
    key  = data.get("key", {})

    if key.get("fromMe", False):
        return JSONResponse({"status": "ignored"})

    remote_jid = key.get("remoteJid", "")
    phone = remote_jid.replace("@s.whatsapp.net", "").replace("@g.us", "")

    if "@g.us" in remote_jid:
        return JSONResponse({"status": "ignored"})

    msg_obj  = data.get("message", {})
    msg_type = data.get("messageType", "conversation")

    text_content = (
        msg_obj.get("conversation")
        or msg_obj.get("extendedTextMessage", {}).get("text")
        or ""
    )

    is_media = msg_type in (
        "imageMessage", "documentMessage", "audioMessage",
        "videoMessage", "stickerMessage",
    )
    media_type = "media" if is_media else "text"

    # ── Transcrição de áudio via Whisper ──────────────────────────────────────
    if msg_type == "audioMessage":
        b64 = await _download_audio(key, msg_obj)
        if b64:
            transcribed = await _transcribe_audio(b64)
            if transcribed:
                text_content = transcribed
                media_type   = "text"
                is_media     = False
                print(f"[ÁUDIO→TEXTO] {phone}: {transcribed[:80]}")

    if not text_content and not is_media:
        return JSONResponse({"status": "ignored"})

    message_text = text_content if text_content else f"[{msg_type}]"

    contact_name = data.get("pushName", "").strip()

    try:
        await process_message(phone, message_text, media_type, db, contact_name=contact_name)
    except Exception as e:
        import traceback
        print(f"[ERRO] {e}")
        traceback.print_exc()

    return JSONResponse({"status": "ok"})


# ═══════════════════════════════════════════════════════════════
# PAINEL — Página principal
# ═══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: DBSession = Depends(get_session)):
    leads = db.exec(select(Lead).order_by(Lead.updated_at.desc())).all()

    stats = {
        "total":      len(leads),
        "novo":       sum(1 for l in leads if l.status == "novo"),
        "em_analise": sum(1 for l in leads if l.status == "em_analise"),
        "convertido": sum(1 for l in leads if l.status == "convertido"),
        "perdido":    sum(1 for l in leads if l.status == "perdido"),
        "boleto":     sum(1 for l in leads if l.payment_type == "boleto"),
        "cartao":     sum(1 for l in leads if l.payment_type == "cartao_avista"),
        "assistencia":sum(1 for l in leads if l.payment_type == "assistencia"),
    }

    store_name  = get_config("store_name") or "Top Phone"
    financeiras = [f.strip() for f in (get_config("financeiras") or "").split(",") if f.strip()]
    atendentes  = _get_atendentes()

    return templates.TemplateResponse("index.html", {
        "request":     request,
        "leads":       leads,
        "stats":       stats,
        "store_name":  store_name,
        "financeiras": financeiras,
        "atendentes":  atendentes,
        "now":         datetime.utcnow(),
    })


# ═══════════════════════════════════════════════════════════════
# CRM — API de leads
# ═══════════════════════════════════════════════════════════════

@app.get("/api/leads")
async def list_leads(
    status: Optional[str]  = None,
    payment: Optional[str] = None,
    db: DBSession = Depends(get_session),
):
    stmt  = select(Lead).order_by(Lead.updated_at.desc())
    leads = db.exec(stmt).all()
    if status:
        leads = [l for l in leads if l.status == status]
    if payment:
        leads = [l for l in leads if l.payment_type == payment]
    return [l.model_dump() for l in leads]


@app.patch("/api/leads/{lead_id}")
async def update_lead(lead_id: int, request: Request, db: DBSession = Depends(get_session)):
    data = await request.json()
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    allowed = {"name", "status", "notes", "assigned_to", "financeira", "payment_type"}
    for key, val in data.items():
        if key in allowed:
            setattr(lead, key, val)
    lead.updated_at = datetime.utcnow()
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead.model_dump()


@app.delete("/api/leads/{lead_id}")
async def delete_lead(lead_id: int, db: DBSession = Depends(get_session)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    db.delete(lead)
    db.commit()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# CONFIGURAÇÕES
# ═══════════════════════════════════════════════════════════════

_DB_DEFAULTS = {
    "store_name":          "Top Phone",
    "store_address":       "Av. Dep. José Mendonça, 116 — Centro, Belo Jardim-PE",
    "bot_prompt":          "",
    "conversation_tone":   "informal",
    "session_timeout":     "30",
    "response_delay":      "2",
    "notification_timeout":"10",
    "atendentes":          "",
    "financeiras":         "BV,Santander,Itaú,Bradesco",
    "pay_boleto":          "1",
    "pay_cartao":          "1",
    "pay_assistencia":     "1",
}


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    env_cfg = {
        "EVOLUTION_HOST":     os.getenv("EVOLUTION_HOST", ""),
        "EVOLUTION_API_KEY":  os.getenv("EVOLUTION_API_KEY", ""),
        "EVOLUTION_INSTANCE": os.getenv("EVOLUTION_INSTANCE", ""),
        "ATENDENTE_NUMBER":   os.getenv("ATENDENTE_NUMBER", ""),
        "GROQ_API_KEY":       os.getenv("GROQ_API_KEY", ""),
        "GROQ_MODEL":         os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
    }
    db_cfg = {k: get_config(k, v) for k, v in _DB_DEFAULTS.items()}
    if not db_cfg["bot_prompt"]:
        db_cfg["bot_prompt"] = DEFAULT_CUSTOM_PROMPT

    return templates.TemplateResponse("config.html", {
        "request": request,
        "cfg": {**env_cfg, **db_cfg},
    })


@app.post("/config")
async def save_config(
    request: Request,
    store_name: str          = Form("Top Phone"),
    store_address: str       = Form(""),
    bot_prompt: str          = Form(""),
    conversation_tone: str   = Form("informal"),
    session_timeout: str     = Form("30"),
    response_delay: str      = Form("2"),
    notification_timeout: str= Form("10"),
    atendentes: str          = Form(""),
    financeiras: str         = Form(""),
    pay_boleto: Optional[str]      = Form(None),
    pay_cartao: Optional[str]      = Form(None),
    pay_assistencia: Optional[str] = Form(None),
):
    set_config("store_name",          store_name)
    set_config("store_address",       store_address)
    set_config("bot_prompt",          bot_prompt)
    set_config("conversation_tone",   conversation_tone)
    set_config("session_timeout",     session_timeout)
    set_config("response_delay",      response_delay)
    set_config("notification_timeout",notification_timeout)
    set_config("atendentes",          atendentes)
    set_config("financeiras",         financeiras)
    set_config("pay_boleto",          "1" if pay_boleto else "0")
    set_config("pay_cartao",          "1" if pay_cartao else "0")
    set_config("pay_assistencia",     "1" if pay_assistencia else "0")

    return RedirectResponse(url="/config?saved=1", status_code=303)


# ═══════════════════════════════════════════════════════════════
# WHATSAPP — Status, QR Code, Setup de webhook
# ═══════════════════════════════════════════════════════════════

@app.get("/api/whatsapp/status")
async def whatsapp_status():
    host     = os.getenv("EVOLUTION_HOST", "").rstrip("/")
    api_key  = os.getenv("EVOLUTION_API_KEY", "")
    instance = os.getenv("EVOLUTION_INSTANCE", "")

    if not host or not instance:
        return {"connected": False, "state": "unconfigured"}

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r    = await client.get(
                f"{host}/instance/connectionState/{instance}",
                headers={"apikey": api_key},
            )
            data  = r.json()
            state = data.get("instance", {}).get("state", data.get("state", "unknown"))
            state = state.lower() if isinstance(state, str) else "unknown"
            return {"connected": state == "open", "state": state}
    except Exception as e:
        return {"connected": False, "state": "error", "detail": str(e)}


@app.get("/api/whatsapp/qr")
async def whatsapp_qr():
    host     = os.getenv("EVOLUTION_HOST", "").rstrip("/")
    api_key  = os.getenv("EVOLUTION_API_KEY", "")
    instance = os.getenv("EVOLUTION_INSTANCE", "")

    if not host or not instance:
        raise HTTPException(status_code=400, detail="Evolution API não configurada")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r    = await client.get(
                f"{host}/instance/connect/{instance}",
                headers={"apikey": api_key},
            )
            data = r.json()
            qr   = (
                data.get("base64")
                or data.get("qrcode", {}).get("base64", "")
                or data.get("qr", "")
            )
            if not qr:
                raise HTTPException(
                    status_code=502,
                    detail="QR Code não disponível. A instância pode já estar conectada.",
                )
            if not qr.startswith("data:"):
                qr = f"data:image/png;base64,{qr}"
            return {"qr": qr}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/whatsapp/setup-webhook")
async def setup_webhook(request: Request):
    """Configura o webhook na Evolution API automaticamente."""
    host     = os.getenv("EVOLUTION_HOST", "").rstrip("/")
    api_key  = os.getenv("EVOLUTION_API_KEY", "")
    instance = os.getenv("EVOLUTION_INSTANCE", "")

    if not host or not instance:
        raise HTTPException(status_code=400, detail="Evolution API não configurada")

    webhook_url = str(request.base_url).rstrip("/") + "/webhook/whatsapp"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{host}/webhook/set/{instance}",
                headers={"apikey": api_key, "Content-Type": "application/json"},
                json={
                    "webhook": {
                        "enabled":  True,
                        "url":      webhook_url,
                        "events":   ["MESSAGES_UPSERT"],
                        "byEvents": True,
                    }
                },
            )
            return {"ok": True, "webhook_url": webhook_url, "response": r.json()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ═══════════════════════════════════════════════════════════════
# MISC
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# DOCUMENTOS DA LOJA
# ═══════════════════════════════════════════════════════════════

@app.get("/api/docs")
async def list_docs():
    with DBSession(engine) as db:
        docs = db.exec(select(Document).order_by(Document.created_at.desc())).all()
    return [{"id": d.id, "name": d.name, "created_at": d.created_at.isoformat()} for d in docs]


@app.post("/api/docs")
async def upload_doc(file: UploadFile = File(...)):
    content_bytes = await file.read()
    name = file.filename or "documento"

    if name.lower().endswith(".pdf"):
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content_bytes))
            text = "\n".join(
                page.extract_text() for page in reader.pages if page.extract_text()
            )
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Erro ao ler PDF: {e}")
    else:
        text = content_bytes.decode("utf-8", errors="replace")

    text = text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Documento vazio ou sem texto extraível.")

    with DBSession(engine) as db:
        doc = Document(name=name, content=text)
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return {"id": doc.id, "name": doc.name, "chars": len(text)}


@app.delete("/api/docs/{doc_id}")
async def delete_doc(doc_id: int):
    with DBSession(engine) as db:
        doc = db.get(Document, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Documento não encontrado")
        db.delete(doc)
        db.commit()
    return {"ok": True}


@app.get("/api/config/default_prompt")
async def default_prompt():
    return {"prompt": DEFAULT_CUSTOM_PROMPT}


@app.get("/api/status")
async def status():
    return {
        "status":         "online",
        "evolution_host": os.getenv("EVOLUTION_HOST", "—"),
        "instance":       os.getenv("EVOLUTION_INSTANCE", "—"),
        "atendente":      os.getenv("ATENDENTE_NUMBER", "—"),
        "store":          get_config("store_name") or "—",
    }
