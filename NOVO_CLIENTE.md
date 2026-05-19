# Tutorial — Setup de Novo Cliente

Este guia cobre todo o processo, da VPS zerada até o bot funcionando e integrado ao deploy automático.

---

## Pré-requisitos

- Acesso SSH à VPS do cliente (usuário `root` ou com sudo)
- IP da VPS
- Credenciais da **Evolution API** do cliente (host, API key, nome da instância)
- Chave **Groq** do cliente (console.groq.com)
- Número do WhatsApp do atendente do cliente

---

## Passo 1 — Configurar a VPS

No seu terminal local, rode o script de setup:

```bash
bash scripts/setup-vps.sh root@IP_DO_CLIENTE
```

O script:
- Instala o Docker automaticamente (se necessário)
- Clona o repositório em `/root/biaphone`
- Cria o arquivo `.env` a partir do template

> Se quiser usar porta SSH diferente ou caminho diferente:
> ```bash
> bash scripts/setup-vps.sh root@IP_DO_CLIENTE 22 8000 /root/biaphone
> ```

---

## Passo 2 — Editar o `.env` do cliente

Acesse a VPS e edite o arquivo:

```bash
ssh root@IP_DO_CLIENTE
nano /root/biaphone/.env
```

Preencha com as credenciais do cliente:

```env
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile

EVOLUTION_HOST=http://IP_EVOLUTION:PORTA
EVOLUTION_API_KEY=api-key-do-cliente
EVOLUTION_INSTANCE=nome-da-instancia

ATENDENTE_NUMBER=5581999999999

WEBHOOK_SECRET=topphone2026

STORE_NAME=Nome da Loja do Cliente
STORE_ADDRESS=Rua Exemplo, 123 — Cidade-UF
```

Salve com `Ctrl+O` → `Enter` → `Ctrl+X`.

---

## Passo 3 — Subir o bot

Ainda na VPS:

```bash
cd /root/biaphone
docker compose up -d --build
```

Aguarde o build (~2 min na primeira vez). Verifique se subiu:

```bash
docker compose logs -f
```

Você deve ver `Uvicorn running on http://0.0.0.0:8000`. Pressione `Ctrl+C` para sair dos logs.

---

## Passo 4 — Acessar o painel

Abra no navegador:

```
http://IP_DO_CLIENTE:8000
```

Você verá o CRM de leads. Acesse as **Configurações**:

```
http://IP_DO_CLIENTE:8000/config
```

O **Checklist de Setup** no topo mostrará o que ainda precisa ser feito.

---

## Passo 5 — Conectar o WhatsApp

Na página de Configurações:

1. Certifique-se que o campo **URL do servidor** (Evolution API), **API Key** e **Nome da instância** estão preenchidos
2. Clique em **💾 Salvar configurações**
3. Clique em **📷 Gerar QR Code**
4. Escaneie o QR Code com o WhatsApp do número que vai atender (Menu → Aparelhos conectados → Conectar aparelho)
5. Aguarde — o painel detecta a conexão automaticamente

---

## Passo 6 — Configurar o Webhook

Ainda nas Configurações, clique em **🔗 Configurar Webhook**.

Isso aponta a Evolution API para o bot automaticamente. O status ficará verde quando estiver funcionando.

> Se preferir configurar manualmente na Evolution API:
> - URL: `http://IP_DO_CLIENTE:8000/webhook/whatsapp`
> - Evento: `MESSAGES_UPSERT`

---

## Passo 7 — Personalizar o bot

Na página de Configurações, configure:

- **Nome e endereço da loja** — usados nas respostas do bot
- **Tom da conversa** — informal, formal ou neutro
- **Formas de pagamento ativas** — Boleto, Cartão/À vista, Assistência
- **Prompt do bot** — informações específicas da loja (modelos disponíveis, preços, garantias etc.)
- **Números dos atendentes** — quem recebe as notificações de novos leads

### Subir documentos da loja

No final da página de Configurações, na seção **Documentos da Loja**:

1. Clique em escolher arquivo
2. Selecione arquivos `.txt` ou `.pdf` (catálogo de produtos, tabela de preços, regras de troca etc.)
3. Clique em **⬆ Enviar**

O bot vai usar esses documentos como referência nas respostas.

---

## Passo 8 — Adicionar ao deploy automático

Edite o arquivo `clients.json` na sua máquina local e adicione o novo cliente:

```json
{
  "name": "nome-cliente",
  "host": "IP_DO_CLIENTE",
  "user": "root",
  "port": 22,
  "path": "/root/biaphone",
  "ssh_key": null,
  "app_port": 8000,
  "notes": "Nome da Loja — Cidade-UF"
}
```

A partir de agora, qualquer `git push` para a branch `main` atualiza **todos os clientes** automaticamente via GitHub Actions.

Para fazer deploy apenas neste novo cliente:

```bash
python deploy.py nome-cliente
```

---

## Passo 9 — Configurar deploy automático por SSH (uma vez só)

Para que o GitHub Actions consiga acessar as VPS via SSH, você precisa fazer isso uma vez:

### 9.1 — Gerar par de chaves SSH de deploy

```bash
ssh-keygen -t ed25519 -C "deploy@biaphone" -f ~/.ssh/biaphone_deploy -N ""
```

Isso cria dois arquivos:
- `~/.ssh/biaphone_deploy` → **chave privada** (vai pro GitHub)
- `~/.ssh/biaphone_deploy.pub` → **chave pública** (vai pra cada VPS)

### 9.2 — Instalar a chave pública em cada VPS

```bash
ssh-copy-id -i ~/.ssh/biaphone_deploy.pub root@IP_DO_CLIENTE
```

Repita para cada VPS existente.

### 9.3 — Adicionar a chave privada ao GitHub

1. Acesse: **GitHub → seu repositório → Settings → Secrets and variables → Actions**
2. Clique em **New repository secret**
3. Nome: `SSH_PRIVATE_KEY`
4. Valor: cole o conteúdo de `~/.ssh/biaphone_deploy`

```bash
cat ~/.ssh/biaphone_deploy   # copie e cole no GitHub
```

Pronto! A partir de agora, `git push origin main` dispara o deploy em todos os clientes.

---

## Resumo — Fluxo rápido

```
1. bash scripts/setup-vps.sh root@IP
2. ssh root@IP → nano /root/biaphone/.env → preencher
3. cd /root/biaphone && docker compose up -d --build
4. Abrir http://IP:8000/config
5. Salvar credenciais → Gerar QR → Escanear → Configurar Webhook
6. Adicionar entrada em clients.json
```

---

## Comandos úteis na VPS

```bash
# Ver logs em tempo real
docker compose -f /root/biaphone/docker-compose.yml logs -f

# Reiniciar o bot
docker compose -f /root/biaphone/docker-compose.yml restart

# Atualizar manualmente
cd /root/biaphone && git pull && docker compose up -d --build

# Verificar se está rodando
docker ps | grep topphone
```
