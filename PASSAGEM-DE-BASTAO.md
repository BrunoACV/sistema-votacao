# Passagem de Bastão — Sistema de Votação do Concurso de Halloween INTS

Este documento é a porta de entrada técnica para o **Analista de Infraestrutura** e equipe de operações do INTS que assume a sustentação, deploy e manutenção do sistema.

O projeto segue rigorosamente o mesmo padrão de containerização, organização e documentação do **Portal de Dashboards INTS**.

---

## 1. Ordem Recomendada de Leitura

1. **Este documento (`PASSAGEM-DE-BASTAO.md`), na íntegra.**
2. **`DOCKER.md`** — guia operacional para subir, atualizar e monitorar o sistema via Docker Compose.
3. **`docker-compose.yml`** e **`Dockerfile`** — declaração dos serviços, volumes de persistência e usuário seguro.
4. **`.env`** — variáveis de ambiente operacionais.

---

## 2. Visão Geral da Aplicação

O sistema foi desenvolvido especificamente para o concurso institucional de fantasias de Halloween do INTS:

- **Frontend:** HTML5 semântico com Tailwind CSS configurado no tema **Dracula Dark** (fundo escuro `#282a36`, cards `#343746`, acentos `#bd93f9`, `#50fa7b`, `#ff79c6`, `#ffb86c`, `#8be9fd`, `#f1fa8c`, `#ff5555`). Não possui tema claro por especificação de identidade visual.
- **Backend:** Python 3.11+, Flask modular estruturado com Blueprints (`public` e `admin`), servido em produção por servidor WSGI **Gunicorn** (2 workers, 4 threads assíncronas).
- **Banco de Dados:** SQLite 3 em modo WAL (`data/voting.db`), garantindo alta concorrência de leitura e escrita com transações ACID.
- **Fotos:** Armazenamento local persistente em `static/uploads/`, com sanitização de nomes UUID, compressão e validação segura de tipos MIME (Pillow).

---

## 3. Estado Atual e Funcionalidades Entregues

| Recurso | Status | Descrição |
|---|---|---|
| **Tema Dracula Dark** | 100% | Aplicado em todas as 10 telas e componentes. |
| **Inscrição de Candidatos** | 100% | Cadastro com nome, fantasia, foto, setor e função institucional. |
| **Urna de Votação Secreta** | 100% | Rota `/vote` separada, oculta da tela pública de cadastro. |
| **Busca de Candidatos** | 100% | Campo de busca interativo em tempo real por nome/fantasia na votação. |
| **Validação @ints.org.br** | 100% | Rejeita imediatamente qualquer e-mail externo, com mensagem clara no card. |
| **Voto Único por Colaborador** | 100% | Impede duplicidade tanto em tempo real via API quanto na gravação no banco. |
| **Painel de Moderação** | 100% | Protegido por senha (`ADMIN_PASSWORD`), com KPIs de votos e desclassificação. |
| **Navegação do Moderador** | 100% | Central de Acesso Rápido e Navbar conectando as **5 telas do sistema**. |
| **Auditoria e Remoção de Votos** | 100% | Tela `/admin/voters` para exclusão de votos individuais ou reset geral. |
| **Placar e Pódio Olímpico** | 100% | Pódio estilizado do 1º ao 3º lugar com ranking geral dos candidatos. |
| **Docker e Compose** | 100% | Imagem Debian Bookworm, usuário não-root, volumes mapeados e healthcheck. |
| **Testes Automatizados** | 100% | 43 testes unitários e de integração cobrindo todas as rotas e regras. |

---

## 4. Arquitetura de Rotas e Telas

| Rota | Blueprint | Visibilidade | Finalidade |
|---|---|---|---|
| `/` | `public.index` | Pública | Redirecionamento automático (302) para `/results`. |
| `/results` | `public.results` | Pública | Placar oficial, pódio olímpico e ranking geral. |
| `/register` | `public.register` | Pública | Formulário de inscrição dos colaboradores no concurso. |
| `/vote` | `public.vote` | Secreta / Restrita | Urna de votação exclusiva para colaboradores com e-mail `@ints.org.br`. |
| `/admin` | `admin.dashboard` | Moderador | Painel administrativo com KPIs, desclassificação e acesso rápido. |
| `/admin/voters` | `admin.voters_audit` | Moderador | Auditoria detalhada de votantes com remoção de votos. |
| `/admin/login` | `admin.login` | Moderador | Tela de autenticação por senha do moderador. |
| `/health` | Core | Pública / Infra | Healthcheck (retorna JSON `{"status": "ok", "app": "ints_voting"}`). |
| `/api/check-voter` | `public.api_check_voter` | API Interna | Verificação em tempo real de elegibilidade do e-mail. |

---

## 5. Padrão de Deploy — Docker & Docker Compose

Seguindo o padrão do **Portal de Dashboards**, o deploy em produção é executado em contêineres gerenciados pelo Docker Compose:

### 5.1 Arquivos do Docker
- **`Dockerfile`**: Base `python:3.11-slim-bookworm`, sem privilégios de root (roda com usuário `appuser` UID 1000), expõe a porta `8080` e executa via Gunicorn.
- **`docker-compose.yml`**: Serviço `voting`, container `ints-voting`, reinicialização `unless-stopped`, política de logs limitada (`10m`, 5 arquivos).
- **`.dockerignore`**: Exclui `.env`, `.git`, `.venv`, `__pycache__` e testes, mantendo a imagem leve (~250 MB).

### 5.2 Persistência de Dados (Volumes)
Dois diretórios do host são mapeados no contêiner para garantir sobrevivência total de dados entre deploys:
1. `./data:/app/data` — Contém o banco de dados SQLite `voting.db`.
2. `./static/uploads:/app/static/uploads` — Contém os arquivos JPG/PNG das fotos enviadas.

### 5.3 Comandos Rápidos
```bash
# Subir ou atualizar
docker compose up -d --build

# Ver logs
docker compose logs -f voting

# Reiniciar
docker compose restart voting

# Parar
docker compose down
```

---

## 6. Variáveis de Ambiente (`.env`)

O arquivo `.env` fica versionado no repositório Git (conforme padrão institucional do INTS), garantindo que ao clonar o projeto ele esteja 100% pronto para iniciar:

| Variável | Padrão | Descrição |
|---|---|---|
| `HOST` | `0.0.0.0` | Endereço IP de escuta. |
| `PORT` | `8080` | Porta interna da aplicação. |
| `HALLOWEEN_PORTA` | `8080` | Porta externa mapeada no host pelo Docker Compose. |
| `ADMIN_PASSWORD` | `Ints@Halloween2026!` | Senha de acesso ao painel de moderação. |
| `SECRET_KEY` | *(Hash seguro)* | Chave de assinatura criptográfica de sessão Flask. |
| `DATABASE_PATH` | `data/voting.db` | Caminho do arquivo SQLite. |
| `UPLOAD_FOLDER` | `static/uploads` | Diretório de salvamento das fotos. |
| `MAX_CONTENT_LENGTH` | `10485760` | Tamanho máximo de upload (10 MB). |
| `APP_ENV` | `production` | Modo de execução (`production`, `development`, `testing`). |
| `TZ` | `America/Bahia` | Fuso horário oficial do INTS. |

---

## 7. Proxy Reverso Nginx

Se a aplicação estiver atrás de um servidor Nginx na porta 80 ou 443 (como na VPS atual `163.176.30.72`), utilize a seguinte configuração:

```nginx
server {
    listen 80;
    server_name _;

    client_max_body_size 15M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 8. Rotina de Backup e Restauração

Para realizar cópias de segurança dos votos e fotos:

```bash
# Backup manual ou via cron
tar -czvf backup-halloween-$(date +%Y%m%d_%H%M%S).tar.gz data/ static/uploads/
```

Para restaurar em caso de migração de servidor:
1. Instale o Docker e Git no novo servidor.
2. Clone o repositório.
3. Descompacte o arquivo de backup sobrepondo as pastas `data/` e `static/uploads/`.
4. Execute `docker compose up -d --build`.

---

## 9. Testes Automatizados

A suíte de testes cobre todas as funcionalidades críticas:
```bash
# Execução no ambiente Python local
python -m unittest discover -s tests

# Ou dentro do contêiner Docker
docker compose run --rm voting python -m unittest discover -s tests
```
Resultado esperado: **43 testes executados com 100% de aprovação (OK)**.
