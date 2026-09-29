# Subir o Sistema de Votação do Concurso de Halloween INTS com Docker

Guia para quem instala e atualiza o sistema de votação num servidor com Docker.

---

## 1 · O que o servidor precisa

- **Docker Engine com Docker Compose v2** (o comando moderno é `docker compose`, com espaço).
- **Git** e acesso de leitura ao repositório do projeto.
- **Memória:** o sistema consome ~100 a 150 MB de RAM em operação normal (Gunicorn + Flask). Reserve **512 MB** (1 GB para folga ampla em picos de votação).
- **Disco:** ~50 MB para banco de dados e imagens em `data/` e `static/uploads/`, mais a imagem Docker (~250 MB).
- **Rede de SAÍDA:**
  - Acesso à internet durante o build para baixar a imagem base `python:3.11-slim-bookworm` e pacotes do `requirements.txt`.
  - CDN Tailwind CSS (`cdn.tailwindcss.com`) acessada pelo navegador dos usuários.
- **Rede de ENTRADA:** a porta publicada (padrão **8080** ou **8081** via Nginx) liberada para a rede corporativa/colaboradores.

---

## 2 · Primeira instalação

```bash
git clone https://github.com/BrunoACV/ints_voting_system.git
cd ints_voting_system
docker compose up -d --build
```

- **Outra porta no host:** se a porta 8080 já estiver ocupada no servidor, passe a variável antes:
  ```bash
  HALLOWEEN_PORTA=8081 docker compose up -d --build
  ```
- **Conferir funcionamento:**
  - `docker compose ps` deve mostrar o contêiner `ints-voting` como `Up` e `(healthy)`.
  - `docker compose logs -f voting` deve exibir a inicialização do Gunicorn com os workers ativos.
- **Persistência de Dados e Fotos:**
  - O banco de dados SQLite (`data/voting.db`) e as fotos dos candidatos (`static/uploads/`) são mapeados diretamente como volumes do host no `docker-compose.yml`.
  - Eles sobrevivem a qualquer reinicialização, atualização de versão ou rebuild do contêiner.

---

## 3 · Atualizar para uma versão nova

Quando houver novos commits e melhorias no Git:

```bash
cd ints_voting_system
git pull
docker compose up -d --build
docker image prune -f
```

O comando `up --build` reconstrói a imagem com o código novo e recria o contêiner sem perder os votos ou as fotos salvas nas pastas do host.

---

## 4 · Dia a dia do Analista de Infraestrutura

| O quê | Comando |
|---|---|
| **Ver logs em tempo real** | `docker compose logs -f voting` |
| **Reiniciar aplicação** | `docker compose restart voting` |
| **Parar contêiner** | `docker compose down` |
| **Verificar status e saúde** | `docker compose ps` |
| **Testar endpoint de saúde** | `curl -s http://localhost:8080/health` |

---

## 5 · Backup e Restauração

Toda a informação viva do sistema está em duas pastas locais:

1. **Banco SQLite:** pasta `./data/voting.db` (participantes, registros de votantes @ints, contagem de votos).
2. **Fotos dos Participantes:** pasta `./static/uploads/` (imagens das fantasias).

Para fazer backup:
```bash
# Exemplo de backup diário/semanal
tar -czvf backup-halloween-$(date +%Y%m%d).tar.gz data/ static/uploads/
```

Para restaurar num servidor novo, basta extrair as pastas `data/` e `static/uploads/` no diretório do projeto antes de rodar `docker compose up -d`.

---

## 6 · Cuidados de Segurança e Configuração

- **O `.env` com as configurações vem no repositório** (seguindo o padrão institucional estabelecido no Portal de Dashboards): o analista clona e o sistema sobe imediatamente pronto para produção.
- **O repositório Git deve permanecer PRIVADO.**
- **A imagem Docker é para uso interno institucional**, não devendo ser enviada para registros públicos.
- **A imagem em si não embute o `.env`**: ele é excluído pelo `.dockerignore` e injetado em tempo de execução via diretiva `env_file` do Docker Compose.
- **Senha do Painel de Moderação (`ADMIN_PASSWORD`):** pode ser alterada diretamente no arquivo `.env` do servidor e aplicada com `docker compose restart voting`.
- **Integração com Nginx (Proxy Reverso):**
  Se o servidor utilizar Nginx na porta 80/443 apontando para o sistema, utilize o seguinte bloco de proxy:
  ```nginx
  location / {
      proxy_pass http://127.0.0.1:8080;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      client_max_body_size 15M;
  }
  ```
