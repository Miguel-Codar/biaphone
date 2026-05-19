from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class Lead(SQLModel, table=True):
    id: Optional[int]            = Field(default=None, primary_key=True)
    phone: str                   = Field(index=True)
    name: Optional[str]          = None
    payment_type: Optional[str]  = None          # boleto | cartao_avista | assistencia
    status: str                  = Field(default="novo")
    # novo | em_analise | aguardando_financeira | financeira_aprovada
    # financeira_reprovada | convertido | perdido
    doc_rg: bool                 = Field(default=False)
    doc_cpf: bool                = Field(default=False)
    doc_luz: bool                = Field(default=False)
    notes: Optional[str]         = None
    assigned_to: Optional[str]   = None          # atendente responsável
    financeira: Optional[str]    = None          # financeira utilizada no boleto
    last_message_at: Optional[datetime] = None   # última mensagem recebida do cliente
    notified_at: Optional[datetime]     = None   # último lembrete enviado ao atendente
    created_at: datetime         = Field(default_factory=datetime.now)
    updated_at: datetime         = Field(default_factory=datetime.now)


class Session(SQLModel, table=True):
    id: Optional[int]           = Field(default=None, primary_key=True)
    phone: str                  = Field(index=True, unique=True)
    stage: str                  = Field(default="SONDAGEM")
    payment_type: Optional[str] = None
    doc_rg: bool                = Field(default=False)
    doc_cpf: bool               = Field(default=False)
    doc_luz: bool               = Field(default=False)
    history: str                = Field(default="[]")
    updated_at: datetime        = Field(default_factory=datetime.now)


class Config(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str          = Field(unique=True, index=True)
    value: str


class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    content: str
    created_at: datetime = Field(default_factory=datetime.now)
