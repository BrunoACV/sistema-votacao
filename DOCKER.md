# Subir o Sistema de Votação Institucional INTS com Docker

Guia para o analista de infraestrutura instalar e operar o sistema de votação em máquina virtual via Docker e Docker Compose, seguindo o mesmo padrão operacional do **Portal de Dashboards**.

---

## 1 · O que o servidor precisa

- **Docker Engine com Docker Compose v2** (comando padrão `docker compose`, com espaço).
- **Git** e acesso de leitura ao repositório:
  `https://github.com/BrunoACV/sistema-votacao.git`
- **Memória:** o sistema consome ~120 a 180 MB de RAM em operação (Gunicorn + Flask). Reserve **512 MB** (1 GB para ampla folga em picos de votação simultânea).
- **Disco:** ~100 MB para dados e imagens em `data/` e `static/uploads/`, mais a imagem Docker (~280 MB).
- **Rede de SAÍDA:**
  - Acesso à internet durante o build para baixar a imagem base `python:3.11-slim-bookworm` e dependências.
  - CDN Tailwind CSS (`cdn.tailwindcss.com`) acessada pelo navegador dos usuários finais.
- **Rede de ENTRADA:** a porta TCP publicada (padrão **9090** ou via proxy reverso Nginx/Apache) liberada no firewall para a rede corporativa/colaboradores.

---

## 2 · Primeira instalação

```bash
git clone https://github.com/BrunoACV/sistema-votacao.git
cd sistema-votacao
docker compose up -d --build
```

### Banco de Dados e Fotos inclusos no mesmo contêiner:
- **Tudo no mesmo contêiner:** Não há contêiner separado de banco de dados (como MySQL ou Postgres). A aplicação utiliza **SQLite integrado**, rodando diretamente no mesmo contêiner em alta velocidade.
- **Dados já inclusos:** O repositório já vem com a cópia exata do banco de dados oficial (`data/voting.db`) e todas as fotos dos participantes já homologados (`static/uploads/`).
- **Persistência garantida via volumes:** As pastas `./data` e `./static/uploads` do servidor são montadas nos volumes `/app/data` e `/app/static/uploads` do contêiner. Novos votos, novos candidatos e novas fotos continuam gravados com total segurança e **sobrevivem a restarts, atualizações e rebuilds**.
- **Resiliência de permissões:** O contêiner possui rotina automática de validação de permissões de escrita (`entrypoint.sh`), evitando falhas de permissão no volume montado.

### Ajuste de porta alternativa (opcional):
Se a porta 9090 já estiver ocupada no servidor, basta informar a porta desejada antes:
```bash
VOTACAO_PORTA=9095 docker compose up -d --build
```

### Conferir se está tudo rodando:
- `docker compose ps` deve mostrar o contêiner `sistema-votacao` como `Up` e `(healthy)`.
- `docker compose logs -f votacao` exibe os logs em tempo real do Gunicorn.
- Acesse pelo navegador: `http://<IP_DO_SERVIDOR>:9090`.

---

## 3 · Atualizar para uma versão nova

Quando houver novos commits e melhorias no Git:

```bash
cd sistema-votacao
git pull
docker compose up -d --build
docker image prune -f
```

O comando `up -d --build` reconstrói a imagem com o código novo e recria o contêiner sem perder os votos ou as fotos salvas nas pastas de persistência `./data` e `./static/uploads`.

---

## 4 · Dia a dia do Analista de Infraestrutura

| O quê | Comando |
|---|---|
| **Ver logs em tempo real** | `docker compose logs -f votacao` |
| **Reiniciar aplicação** | `docker compose restart votacao` |
| **Parar contêiner** | `docker compose down` |
| **Verificar status e saúde** | `docker compose ps` |
| **Testar endpoint de saúde** | `curl -s http://localhost:9090/health` |

---

## 5 · Backup e Restauração

Toda a informação viva do sistema está em duas pastas locais:

1. **Banco SQLite:** pasta `./data/voting.db` (participantes, registros de votantes @ints, contagem de votos).
2. **Fotos dos Participantes:** pasta `./static/uploads/` (imagens das candidaturas).

Para fazer backup manual ou agendado:
```bash
# Exemplo de backup diário
tar -czvf backup-votacao-$(date +%Y%m%d).tar.gz data/ static/uploads/
```

Para restaurar num servidor novo, basta extrair as pastas `data/` e `static/uploads/` no diretório do projeto antes de rodar `docker compose up -d`.

---

## 6 · Cuidados de Segurança e Configuração

- **O arquivo `.env` já vem no repositório** (seguindo o padrão institucional estabelecido no Portal de Dashboards): o analista clona e o sistema sobe imediatamente pronto para produção.
- **O repositório Git deve permanecer PRIVADO.**
- **A imagem Docker é para uso interno institucional**, não devendo ser enviada para registros públicos.
- **A imagem em si não embute o `.env`**: ele é excluído pelo `.dockerignore` e injetado em tempo de execução via diretiva `env_file` do Docker Compose.
- **Senha do Painel de Moderação (`ADMIN_PASSWORD`):** pode ser alterada diretamente no arquivo `.env` do servidor e aplicada com `docker compose restart votacao`.
- **Integração com Nginx (Proxy Reverso se aplicável):**
  Se a VM corporativa utilizar Nginx na porta 80 ou 443 apontando para o sistema, utilize o seguinte bloco de configuração:
  ```nginx
  location / {
      proxy_pass http://127.0.0.1:9090;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      client_max_body_size 15M;
  }
  ```
