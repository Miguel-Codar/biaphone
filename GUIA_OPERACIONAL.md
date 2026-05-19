# Guia Operacional — Biaphone Bot

> Leia do começo ao fim uma vez. Depois use como referência sempre que precisar.

---

## Índice

1. [Setup inicial da VPS](#1-setup-inicial-da-vps)
2. [Estrutura do sistema](#2-estrutura-do-sistema)
3. [Painel de admin — visão geral](#3-painel-de-admin)
4. [Adicionar novo cliente — passo a passo](#4-adicionar-novo-cliente)
5. [Configurar o bot do cliente](#5-configurar-o-bot-do-cliente)
6. [Atualizar o código (deploy)](#6-atualizar-o-código-deploy)
7. [Manutenção e rotina](#7-manutenção-e-rotina)
8. [Troubleshooting — o que fazer quando algo quebra](#8-troubleshooting)
9. [Boas práticas e cuidados](#9-boas-práticas-e-cuidados)

---

## 1. Setup Inicial da VPS

Faça isso **uma única vez** ao colocar o sistema no ar pela primeira vez.

### 1.1 Requisitos

- VPS com Ubuntu 22.04 (recomendado) ou 20.04
- Mínimo: 2 GB RAM, 20 GB disco
- Acesso SSH como root

### 1.2 Conectar na VPS

```bash
ssh root@72.60.251.111
```

### 1.3 Instalar o Docker

```bash
curl -fsSL https://get.docker.com | sh
```

Verifique que instalou:

```bash
docker --version
# deve retornar: Docker version 24.x.x
```

### 1.4 Clonar o repositório

```bash
git clone https://github.com/Miguel-Codar/biaphone.git
cd biaphone
```

### 1.5 Configurar as credenciais

```bash
nano .env
```

Preencha **todos** os campos com as credenciais do cliente principal (topphone):

```
GROQ_API_KEY=gsk_...
EVOLUTION_HOST=http://IP:PORTA
EVOLUTION_API_KEY=sua-key
EVOLUTION_INSTANCE=nome-instancia
ATENDENTE_NUMBER=5581999999999
WEBHOOK_SECRET=topphone2026
STORE_NAME=Top Phone
STORE_ADDRESS=Rua Exemplo, 123 — Belo Jardim-PE
ADMIN_PASSWORD=COLOQUE_UMA_SENHA_FORTE_AQUI
ADMIN_SECRET=frase-longa-e-aleatoria-para-seguranca
```

Salve: `Ctrl+O` → `Enter` → `Ctrl+X`

> ⚠️ **Troque ADMIN_PASSWORD e ADMIN_SECRET por valores seus** antes de subir.

### 1.6 Subir o sistema

```bash
docker compose up -d --build
```

A primeira vez demora ~3 minutos (baixa imagens e instala dependências).

### 1.7 Verificar se está rodando

```bash
docker ps
```

Você deve ver dois containers:

```
biaphone_topphone   Up X minutes   0.0.0.0:8000->8000/tcp
biaphone_admin      Up X minutes   0.0.0.0:9000->9000/tcp
```

### 1.8 Acessar os painéis

| O que é | Endereço |
|---|---|
| **Admin** (você gerencia tudo aqui) | `http://72.60.251.111:9000` |
| **Painel topphone** (CRM + config do cliente) | `http://72.60.251.111:8000` |

---

## 2. Estrutura do Sistema

```
VPS 72.60.251.111
│
├── :9000  biaphone_admin     ← seu painel central de controle
│
├── :8000  biaphone_topphone  ← bot do cliente "Top Phone"
├── :8001  biaphone_cliente2  ← (criado via admin quando necessário)
└── :8002  biaphone_cliente3  ← (idem)

Pastas na VPS (/root/biaphone/):
├── .env                      ← credenciais do topphone
├── data/                     ← banco de dados do topphone
├── clients/
│   ├── registry.json         ← lista de todos os clientes
│   ├── topphone/             ← dados do cliente topphone (admin-managed)
│   ├── cliente2/
│   │   ├── .env              ← credenciais exclusivas do cliente2
│   │   └── data/             ← banco de dados do cliente2
│   └── ...
└── admin/                    ← código do painel de admin
```

**Cada cliente tem:**
- Seu próprio container Docker (processo isolado)
- Seu próprio `.env` com credenciais separadas
- Seu próprio banco de dados SQLite (leads não se misturam)
- Sua própria porta na VPS

---

## 3. Painel de Admin

Acesse: `http://72.60.251.111:9000`

Login com a senha que você definiu em `ADMIN_PASSWORD`.

### O que você vê no dashboard

```
┌─────────────────────────────────────────────┐
│  Biaphone Admin      [Rebuild+Reiniciar] [Sair] │
├─────────────────────────────────────────────┤
│  3 Total     2 Rodando                      │
├──────────────┬──────────────┬───────────────┤
│  Top Phone   │ Cliente 2    │  ...          │
│  topphone    │  cliente2    │               │
│  ● Rodando   │  ■ Parado    │               │
│  :8000       │  :8001       │               │
│  [Painel]    │  [Painel]    │               │
│  [Reiniciar] │  [Iniciar]   │               │
│  [Logs]      │  [Logs]      │               │
│  [Excluir]   │  [Excluir]   │               │
└──────────────┴──────────────┴───────────────┘
                              [➕ Novo Cliente]
```

### Botões explicados

| Botão | O que faz |
|---|---|
| **Painel** | Abre o CRM + configurações do cliente em nova aba |
| **Reiniciar** | Reinicia o container do bot (resolve a maioria dos travamentos) |
| **Parar / Iniciar** | Desliga ou liga o bot do cliente |
| **Logs** | Mostra as últimas 100 linhas de log em tempo real |
| **Excluir** | Remove o container (dados no disco são preservados) |
| **Rebuild + Reiniciar Todos** | Reconstrói a imagem com o código novo e reinicia todos os bots |

---

## 4. Adicionar Novo Cliente

### Passo a passo completo

**1. No painel admin** (`http://72.60.251.111:9000`), clique em **➕ Novo Cliente**.

**2. Preencha o formulário:**

| Campo | O que colocar | Exemplo |
|---|---|---|
| Slug | ID interno (sem espaços, minúsculas) | `phonecenter` |
| Porta | Próxima livre (o sistema sugere) | `8001` |
| Nome da Loja | Nome que aparece no bot | `Phone Center` |
| Endereço | Endereço da loja | `Rua das Flores, 99 — Recife-PE` |
| Evolution Host | URL da Evolution API do cliente | `http://IP:8080` |
| Evolution API Key | Chave da API Evolution do cliente | `abc123...` |
| Nome da Instância | Nome exato da instância criada | `phonecenter` |
| Groq API Key | Chave Groq do cliente | `gsk_...` |
| Número do Atendente | WhatsApp que recebe alertas | `5581999999999` |

**3. Clique em "🚀 Criar e Iniciar".**

O bot sobe em ~10 segundos. O card aparece no dashboard com status **● Rodando**.

**4. Acesse o painel do cliente** clicando em **Painel** no card.

Isso abre `http://72.60.251.111:8001/config` onde você vai configurar o WhatsApp.

---

## 5. Configurar o Bot do Cliente

Depois de criar o cliente, acesse o painel dele (`/config`) e siga esta ordem:

### 5.1 Conectar o WhatsApp

1. Na seção **"Status do WhatsApp"**, clique em **📷 Gerar QR Code**
2. Abra o WhatsApp no celular do número que vai atender
3. Vá em: **Menu (⋮) → Aparelhos conectados → Conectar aparelho**
4. Escaneie o QR Code na tela
5. Aguarde — o status muda automaticamente para **✅ Conectado**

> Se o QR Code expirar antes de escanear, clique em **🔄 Novo QR**.

### 5.2 Configurar o Webhook

Ainda na página de Configurações, clique em **🔗 Configurar Webhook**.

Isso aponta a Evolution API para o bot automaticamente.

Se aparecer um erro, configure manualmente na Evolution API:
- URL: `http://72.60.251.111:PORTA/webhook/whatsapp`
- Evento: `MESSAGES_UPSERT`

### 5.3 Personalizar o bot

Preencha na página de Configurações:

- **Nome e endereço da loja** — usados nas respostas automáticas
- **Tom da conversa** — informal (mais próximo), formal ou neutro
- **Formas de pagamento** — marque apenas as que o cliente oferece
- **Números dos atendentes** — quem recebe notificações de leads (pode ter vários, separados por vírgula)
- **Prompt do bot** — informações específicas da loja:
  ```
  - 5 anos no mercado
  - iPhones seminovos e lacrados, garantia de 1 ano
  - Androids: Samsung, Xiaomi, Realme
  - Parcelas facilitadas
  ```

Clique em **💾 Salvar configurações**.

### 5.4 Subir documentos (opcional mas recomendado)

No final da página de Configurações, seção **Documentos da Loja**:

1. Clique em escolher arquivo
2. Envie arquivos `.txt` ou `.pdf` com:
   - Tabela de preços dos modelos
   - Regras de garantia
   - Promoções da semana
   - Qualquer info que o bot deve saber

O bot usa esses documentos para responder perguntas específicas dos clientes.

### 5.5 Testar o bot

Mande uma mensagem para o WhatsApp conectado. O bot deve responder em ~5 segundos.

**Mensagens de teste:**
- "Oi" → deve cumprimentar e perguntar o que precisa
- "Quero comprar um iPhone no boleto" → deve iniciar coleta de documentos
- "Quero ver os preços" → deve sondar ou passar para atendente

O checklist no topo da página de Configurações mostra o que está faltando configurar.

---

## 6. Atualizar o Código (Deploy)

Quando houver uma atualização no código (nova funcionalidade ou correção de bug):

### Opção A — Automático (recomendado)

Faça `git push origin main` na sua máquina. O GitHub Actions vai automaticamente:
1. Conectar na VPS
2. Fazer `git pull`
3. Rebuildar a imagem
4. Reiniciar todos os containers

> Para isso funcionar, você precisa ter configurado a chave SSH nos Secrets do GitHub (ver seção 9.2 do arquivo `NOVO_CLIENTE.md`).

### Opção B — Manual na VPS

```bash
ssh root@72.60.251.111
cd /root/biaphone
git pull
docker compose up -d --build
```

Depois, no painel admin, clique em **🔄 Rebuild + Reiniciar Todos** para aplicar a nova versão em todos os clientes.

### Opção C — Via painel admin

No dashboard do admin, clique em **🔄 Rebuild + Reiniciar Todos**.

> Isso reconstrói a imagem com o código atual no disco e reinicia todos. Não faz `git pull` — combine com a opção B se quiser pegar código novo.

---

## 7. Manutenção e Rotina

### Ver status geral

```bash
ssh root@72.60.251.111
docker ps
```

Todos os containers `biaphone_*` devem mostrar `Up`.

### Ver logs de um cliente específico

**Via admin:** clique em **Logs** no card do cliente.

**Via SSH:**
```bash
docker logs biaphone_topphone --tail=50 -f
# Ctrl+C para sair
```

### Reiniciar um bot travado

**Via admin:** clique em **Reiniciar** no card do cliente.

**Via SSH:**
```bash
docker restart biaphone_topphone
```

### Verificar espaço em disco

```bash
df -h /
```

Se estiver acima de 80%, limpe imagens e containers não usados:

```bash
docker system prune -f
```

### Backup dos dados

Os dados de cada cliente ficam em:
- Topphone: `/root/biaphone/data/topphone.db`
- Outros: `/root/biaphone/clients/NOME_CLIENTE/data/bot.db`

Para fazer backup:

```bash
# Copia o banco de dados para a sua máquina
scp root@72.60.251.111:/root/biaphone/data/topphone.db ./backup_topphone_$(date +%Y%m%d).db
scp root@72.60.251.111:/root/biaphone/clients/cliente2/data/bot.db ./backup_cliente2_$(date +%Y%m%d).db
```

Faça backup semanalmente ou antes de qualquer atualização grande.

### Reiniciar a VPS

Se a VPS reiniciar (por manutenção do provedor), os containers sobem automaticamente por causa do `restart: unless-stopped` no docker-compose.yml. Não precisa fazer nada.

---

## 8. Troubleshooting

### ❌ Bot não responde no WhatsApp

**1. Verifique se o container está rodando:**
```bash
docker ps | grep biaphone_topphone
```
Se não aparecer, reinicie: `docker start biaphone_topphone`

**2. Verifique os logs:**
```bash
docker logs biaphone_topphone --tail=30
```
Procure por erros em vermelho.

**3. Verifique se o WhatsApp está conectado:**
Acesse `http://IP:PORTA/config` e veja o status no topo.

**4. Verifique se o webhook está configurado:**
Na página de Configurações, clique em "Configurar Webhook" novamente.

---

### ❌ QR Code não aparece ou diz "não disponível"

A instância da Evolution API pode já estar conectada (QR só aparece quando está desconectada).

Verifique o status primeiro. Se estiver desconectada mesmo assim, o problema é na Evolution API — verifique se ela está online e se o nome da instância está correto no `.env` do cliente.

---

### ❌ Container não sobe (erro no docker compose up)

```bash
docker compose logs admin
docker compose logs topphone
```

Causas comuns:
- Porta já em uso: mude a porta no `.env` ou `docker-compose.yml`
- `.env` com campo vazio: abra e verifique todos os campos
- Imagem não construída: rode `docker compose build` antes

---

### ❌ Painel admin não abre (porta 9000)

```bash
docker ps | grep biaphone_admin
```

Se o container não estiver rodando:
```bash
cd /root/biaphone
docker compose up -d admin
```

Se aparecer erro de permissão no Docker socket:
```bash
chmod 666 /var/run/docker.sock
```

---

### ❌ Leads não aparecem no CRM

O bot processa a mensagem mas o lead só aparece depois que o Groq identifica uma intenção. Verifique:
- A chave Groq está correta e ativa (console.groq.com)
- O modelo escolhido está disponível
- Veja os logs do container para ver o retorno do Groq

---

### ❌ Groq retornando erro

```bash
docker logs biaphone_topphone --tail=20 | grep GROQ
```

Se aparecer `401 Unauthorized`: a chave Groq expirou ou está errada. Atualize em `/config`.

Se aparecer `429 Too Many Requests`: limite de taxa atingido. O Groq Free tem limites — aguarde ou use uma chave paga.

---

### ❌ Mensagens em duplicata

O webhook está sendo chamado duas vezes. Na Evolution API, verifique se há apenas **um webhook configurado** para a instância.

---

## 9. Boas Práticas e Cuidados

### Segurança

**Troque as senhas padrão.** O `.env` vem com `ADMIN_PASSWORD=admin123` — mude antes de subir em produção:
```bash
nano /root/biaphone/.env
# altere ADMIN_PASSWORD e ADMIN_SECRET
docker compose restart admin
```

**Proteja a porta 9000 do admin** com firewall — deixe acessível apenas do seu IP:
```bash
ufw allow 8000/tcp    # bot do topphone (público)
ufw allow 8001/tcp    # bot do cliente2 (público)
ufw allow 9000/tcp    # admin — idealmente restrinja ao seu IP
ufw enable
```

Para restringir o admin ao seu IP:
```bash
ufw allow from SEU_IP_AQUI to any port 9000
```

---

### Credenciais

- **Nunca commit o `.env` real** no git (o `.gitignore` já está configurado para isso)
- Guarde as chaves dos clientes em local seguro (planilha protegida, password manager)
- Rotacione as chaves Groq e Evolution API periodicamente

---

### Antes de atualizar o código

1. Faça backup dos bancos de dados (seção 7)
2. Teste a atualização numa instância de teste antes de aplicar em todos
3. O `git push` automático atualiza todos os clientes — cuidado em horário de pico

---

### Organização por cliente

Para cada cliente que contratar, mantenha um documento com:
- Nome da loja e responsável
- Credenciais da Evolution API (host, key, instância)
- Chave Groq
- Porta que está usando na VPS
- Data de início do contrato

---

### Monitoramento mínimo

Verifique **uma vez por semana**:
```bash
ssh root@72.60.251.111
docker ps                  # todos os containers rodando?
df -h /                    # disco acima de 80%?
docker stats --no-stream   # memória e CPU normais?
```

---

### Recuperação de desastre

Se a VPS explodir e você precisar recriar tudo:

1. Provisione nova VPS
2. Instale Docker
3. Clone o repo: `git clone https://github.com/Miguel-Codar/biaphone.git`
4. Configure `.env`
5. Restaure os bancos de dados do backup
6. `docker compose up -d --build`
7. Para cada cliente: recriar container via admin panel com as mesmas credenciais e porta

O sistema volta ao ar em menos de 30 minutos com backups recentes.

---

## Referência rápida — Comandos mais usados

```bash
# Ver todos os containers
docker ps

# Reiniciar um bot
docker restart biaphone_NOME_CLIENTE

# Ver logs em tempo real
docker logs biaphone_NOME_CLIENTE -f --tail=50

# Atualizar código manualmente
cd /root/biaphone && git pull && docker compose up -d --build

# Limpar espaço em disco
docker system prune -f

# Backup rápido de todos os bancos
for db in $(find /root/biaphone/clients -name "*.db"); do cp "$db" "${db}.bak.$(date +%Y%m%d)"; done
cp /root/biaphone/data/topphone.db /root/biaphone/data/topphone.db.bak.$(date +%Y%m%d)
```

---

## Contatos e recursos

| Recurso | Link |
|---|---|
| Painel Admin | `http://72.60.251.111:9000` |
| Repositório | `https://github.com/Miguel-Codar/biaphone` |
| Groq Console | `https://console.groq.com` |
| Groq Models | LLaMA 3.3 70B Versatile (padrão) |
