import asyncio
import json
import os
import re
from datetime import datetime
from typing import Optional

import httpx
from sqlmodel import Session as DBSession, select

from .models import Session, Lead
from .database import get_config, get_documents

GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL         = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
EVOLUTION_HOST     = os.getenv("EVOLUTION_HOST", "")
EVOLUTION_API_KEY  = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")
ATENDENTE_NUMBER   = os.getenv("ATENDENTE_NUMBER", "")

_PROMPT_HEADER = (
    "Você é o atendente virtual da {store_name}, loja de celulares em {store_address}.\n\n"
    "Objetivo: identificar o que o cliente quer e a forma de pagamento ({payment_types}).\n\n"
    "Responda SOMENTE com JSON neste formato, sem texto fora dele:\n"
    '{{"intent": "BOLETO" | "CARTAO_AVISTA" | "ASSISTENCIA" | "INDEFINIDO",'
    ' "reply": "<mensagem humanizada>"}}\n\n'
    "Regras obrigatórias:\n"
    "- Responda SOMENTE o JSON, sem qualquer texto fora dele.\n"
    "- No campo reply, use \\n\\n (dupla quebra de linha) para separar parágrafos distintos — cada parágrafo será enviado como uma mensagem separada no WhatsApp."
)

DEFAULT_CUSTOM_PROMPT = """Regras:
- Se o cliente não mencionou pagamento, faça sondagem consultiva e natural.
- Não invente preços ou modelos — diga que um consultor vai passar as opções.

Sobre a loja:
- 5 anos de mercado
- iPhones semi-novos e lacrados 100% originais (foco nos semi-novos)
- Androids lacrados: Samsung, Xiaomi, Realme e outros
- 1 ano de garantia nos aparelhos
- Acessórios: capas, películas, carregadores
- Assistência técnica completa, 6 meses de garantia nos serviços, peças homologadas Anatel
- Venda no boleto mesmo com restrição, entrada facilitada, parcelas acessíveis"""

_TONE_RULES = {
    "formal":   "Use linguagem formal e profissional em todas as respostas.",
    "informal": "Use linguagem descontraída, próxima e natural. Emojis com moderação.",
    "neutro":   "Use linguagem neutra e equilibrada.",
}


def _build_system_prompt() -> str:
    store_name      = get_config("store_name") or "Top Phone"
    store_address   = get_config("store_address") or "Belo Jardim-PE"
    pay_boleto      = get_config("pay_boleto", "1") == "1"
    pay_cartao      = get_config("pay_cartao", "1") == "1"
    pay_assistencia = get_config("pay_assistencia", "1") == "1"

    types = []
    if pay_boleto:       types.append("boleto")
    if pay_cartao:       types.append("cartão/à vista")
    if pay_assistencia:  types.append("assistência técnica")
    payment_types = ", ".join(types) or "a combinar"

    header = _PROMPT_HEADER.format(
        store_name=store_name,
        store_address=store_address,
        payment_types=payment_types,
    )
    custom    = get_config("bot_prompt") or DEFAULT_CUSTOM_PROMPT
    tone      = get_config("conversation_tone") or "informal"
    tone_rule = _TONE_RULES.get(tone, _TONE_RULES["informal"])

    # Documentos adicionais da loja (enviados pelo painel)
    docs = get_documents()
    docs_section = ""
    if docs:
        MAX_CHARS = 4000
        parts = []
        total = 0
        for d in docs:
            chunk = f"[{d.name}]\n{d.content.strip()}"
            if total + len(chunk) > MAX_CHARS:
                break
            parts.append(chunk)
            total += len(chunk)
        if parts:
            docs_section = "\n\nInformações adicionais da loja (use como referência):\n" + "\n---\n".join(parts)

    return f"{header}\n\nTom da conversa: {tone_rule}\n\n{custom}{docs_section}"


def _get_timeout() -> int:
    try:
        return int(get_config("session_timeout") or "30")
    except ValueError:
        return 30


def _is_cpf(text: str) -> bool:
    """Verifica se o texto parece um CPF (11 dígitos)."""
    digits = re.sub(r'\D', '', text)
    return len(digits) == 11


def _get_atendentes() -> list[str]:
    """Retorna lista de números de atendentes configurados."""
    raw = get_config("atendentes") or ATENDENTE_NUMBER
    return [a.strip() for a in raw.split(",") if a.strip()]


# ─── SESSÃO ──────────────────────────────────────────────────────────────────

def get_or_create_session(phone: str, db: DBSession) -> Session:
    stmt = select(Session).where(Session.phone == phone)
    sess = db.exec(stmt).first()

    if sess and sess.stage == "TRANSFERIDO":
        minutos = (datetime.utcnow() - sess.updated_at).total_seconds() / 60
        timeout = _get_timeout()
        if minutos > timeout:
            print(f"[SESSÃO] {phone} — timeout {timeout}min atingido, resetando.")
            sess.stage        = "SONDAGEM"
            sess.payment_type = None
            sess.doc_rg       = False
            sess.doc_cpf      = False
            sess.doc_luz      = False
            sess.history      = "[]"
            sess.updated_at   = datetime.utcnow()   # garante reset do timer
            db.add(sess)
            db.commit()
            db.refresh(sess)
            return sess

    if not sess:
        sess = Session(phone=phone)
        db.add(sess)
        db.commit()
        db.refresh(sess)
    return sess


def save_session(sess: Session, db: DBSession):
    sess.updated_at = datetime.utcnow()
    db.add(sess)
    db.commit()
    db.refresh(sess)


def get_history(sess: Session) -> list:
    try:
        return json.loads(sess.history)
    except Exception:
        return []


def push_history(sess: Session, role: str, content: str):
    h = get_history(sess)
    h.append({"role": role, "content": content})
    if len(h) > 30:
        h = h[-30:]
    sess.history = json.dumps(h, ensure_ascii=False)


# ─── GROQ ─────────────────────────────────────────────────────────────────────

async def call_groq(history: list) -> dict:
    messages = [{"role": "system", "content": _build_system_prompt()}] + history
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": GROQ_MODEL, "temperature": 0.4, "max_tokens": 400, "messages": messages},
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)


# ─── EVOLUTION API ────────────────────────────────────────────────────────────

async def send_whatsapp(number: str, text: str, apply_delay: bool = True):
    """Envia mensagem WhatsApp. apply_delay=True adiciona o delay configurado (para respostas ao cliente)."""
    if apply_delay:
        try:
            delay_s = int(get_config("response_delay") or "2")
        except ValueError:
            delay_s = 2
        if delay_s > 0:
            await asyncio.sleep(delay_s)

    host = EVOLUTION_HOST.rstrip("/")
    url  = f"{host}/message/sendText/{EVOLUTION_INSTANCE}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"number": number, "text": text, "delay": 1000}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, headers=headers, json=payload)
        print(f"[EVOLUTION] sendText → {r.status_code} {r.text[:200]}")


async def send_whatsapp_parts(number: str, text: str, apply_delay: bool = True):
    """Divide a mensagem em parágrafos (\\n\\n) e envia cada um separadamente."""
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not parts:
        return
    for i, part in enumerate(parts):
        await send_whatsapp(number, part, apply_delay=(i == 0 and apply_delay))
        if i < len(parts) - 1:
            await asyncio.sleep(1.0)


async def notify_atendente(lead: Lead):
    """Notifica todos os atendentes configurados sobre um novo lead."""
    docs = []
    if lead.doc_rg:  docs.append("RG ✓")
    if lead.doc_cpf: docs.append("CPF ✓")
    if lead.doc_luz: docs.append("Luz ✓")
    docs_txt = " | ".join(docs) if docs else "Nenhum"

    pag_map = {
        "boleto":       "BOLETO",
        "cartao_avista":"CARTÃO / À VISTA",
        "assistencia":  "ASSISTÊNCIA TÉCNICA",
    }
    pag       = pag_map.get(lead.payment_type or "", lead.payment_type or "—")
    loja_nome = get_config("store_name") or "Top Phone"

    text = (
        f"🔔 *NOVO LEAD — {loja_nome}*\n\n"
        f"📱 Telefone: {lead.phone}\n"
        f"💳 Pagamento: {pag}\n"
        f"📋 Docs: {docs_txt}\n\n"
        f"👉 Acesse o painel e assuma o atendimento."
    )
    for atendente in _get_atendentes():
        await send_whatsapp(atendente, text, apply_delay=False)


# ─── LEAD ─────────────────────────────────────────────────────────────────────

def get_or_create_lead(phone: str, db: DBSession) -> Lead:
    stmt = select(Lead).where(Lead.phone == phone)
    lead = db.exec(stmt).first()
    if not lead:
        lead = Lead(phone=phone)
        db.add(lead)
        db.commit()
        db.refresh(lead)
    return lead


def save_lead(lead: Lead, db: DBSession):
    lead.updated_at = datetime.utcnow()
    db.add(lead)
    db.commit()
    db.refresh(lead)


def touch_lead_message_time(phone: str, db: DBSession):
    """Atualiza o timestamp da última mensagem do cliente no lead (se existir)."""
    stmt = select(Lead).where(Lead.phone == phone)
    lead = db.exec(stmt).first()
    if lead:
        lead.last_message_at = datetime.utcnow()
        db.add(lead)
        db.commit()


# ─── LÓGICA PRINCIPAL ─────────────────────────────────────────────────────────

async def process_message(phone: str, message: str, msg_type: str, db: DBSession):
    sess = get_or_create_session(phone, db)

    # Atualiza timestamp da mensagem no lead (para monitoramento de ausência de resposta)
    touch_lead_message_time(phone, db)

    if sess.stage == "TRANSFERIDO":
        print(f"[BOT] {phone} — sessão transferida, ignorando mensagem.")
        return

    pay_boleto      = get_config("pay_boleto", "1") == "1"
    pay_cartao      = get_config("pay_cartao", "1") == "1"
    pay_assistencia = get_config("pay_assistencia", "1") == "1"

    is_media = msg_type == "media"
    push_history(sess, "user", message)

    if sess.stage in ("BOLETO_RG", "BOLETO_CPF", "BOLETO_LUZ"):
        reply, transferir = await _boleto_step(sess, message, is_media, db)
        push_history(sess, "assistant", reply)
        save_session(sess, db)
        await send_whatsapp_parts(phone, reply)
        if transferir:
            await _transferir(phone, sess, db)
        return

    try:
        groq_resp = await call_groq(get_history(sess))
        intent = groq_resp.get("intent", "INDEFINIDO")
        reply  = groq_resp.get("reply", "Olá! Como posso te ajudar? 😊")
        print(f"[GROQ] {phone} — intent: {intent}")
    except Exception as e:
        intent = "INDEFINIDO"
        reply  = "Oi! Como posso te ajudar hoje? 😊"
        print(f"[GROQ ERROR] {e}")

    push_history(sess, "assistant", reply)

    if intent == "BOLETO" and pay_boleto:
        sess.stage        = "BOLETO_RG"
        sess.payment_type = "boleto"
        _sync_lead(phone, sess, db)

    elif intent == "CARTAO_AVISTA" and pay_cartao:
        sess.stage        = "TRANSFERIDO"
        sess.payment_type = "cartao_avista"
        _sync_lead(phone, sess, db)
        save_session(sess, db)
        await send_whatsapp_parts(phone, reply)
        await _transferir(phone, sess, db)
        return

    elif intent == "ASSISTENCIA" and pay_assistencia:
        sess.stage        = "TRANSFERIDO"
        sess.payment_type = "assistencia"
        _sync_lead(phone, sess, db)
        save_session(sess, db)
        await send_whatsapp_parts(phone, reply)
        await _transferir(phone, sess, db)
        return

    save_session(sess, db)
    await send_whatsapp_parts(phone, reply)


async def _boleto_step(sess: Session, message: str, is_media: bool, db: DBSession):
    transferir = False
    reply = ""

    if sess.stage == "BOLETO_RG":
        if is_media:
            sess.doc_rg = True
            sess.stage  = "BOLETO_CPF"
            reply = (
                "Perfeito, recebi o RG/CNH! ✅\n\n"
                "Agora me passa o número do seu *CPF* (ex: 000.000.000-00).\n"
                "Pode digitar ou enviar foto do documento. 📄"
            )
        else:
            reply = (
                "Ainda aguardando a foto do seu *RG ou CNH (frente e verso)*. "
                "Quando estiver pronto, é só mandar! 📸"
            )

    elif sess.stage == "BOLETO_CPF":
        if is_media or _is_cpf(message):
            sess.doc_cpf = True
            sess.stage   = "BOLETO_LUZ"
            reply = (
                "Ótimo! CPF recebido ✅\n\n"
                "Última etapa: me envia a *conta de energia elétrica* — foto ou PDF.\n\n"
                "⚠️ A fatura precisa estar no *mesmo nome* do documento enviado."
            )
        else:
            reply = (
                "Por favor, me envie o número do *CPF* (11 dígitos, ex: 000.000.000-00) "
                "ou uma foto do documento. 📄"
            )

    elif sess.stage == "BOLETO_LUZ":
        if is_media:
            sess.doc_luz = True
            sess.stage   = "TRANSFERIDO"
            reply = (
                "Show, recebi tudo! 🙌\n\n"
                "Documentos encaminhados para análise. "
                "Em breve um consultor vai te chamar aqui no WhatsApp. 😊"
            )
            transferir = True
        else:
            reply = "Ainda aguardando a *conta de energia elétrica* (foto ou PDF). Pode enviar! 📄"

    return reply, transferir


def _sync_lead(phone: str, sess: Session, db: DBSession):
    lead = get_or_create_lead(phone, db)
    lead.payment_type = sess.payment_type
    lead.doc_rg  = sess.doc_rg
    lead.doc_cpf = sess.doc_cpf
    lead.doc_luz = sess.doc_luz
    if lead.status == "novo":
        lead.status = "em_analise"
    save_lead(lead, db)


async def _transferir(phone: str, sess: Session, db: DBSession):
    lead = get_or_create_lead(phone, db)
    lead.payment_type    = sess.payment_type
    lead.doc_rg          = sess.doc_rg
    lead.doc_cpf         = sess.doc_cpf
    lead.doc_luz         = sess.doc_luz
    lead.status          = "em_analise"
    lead.last_message_at = datetime.utcnow()
    save_lead(lead, db)
    await notify_atendente(lead)
