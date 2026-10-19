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

### Banco de dados e fotos: onde moram e por que nunca se perdem
- **Tudo no mesmo contêiner:** não há contêiner separado de banco. A aplicação usa **SQLite**, dentro do próprio contêiner.
- **Os dados de produção ficam em volumes do Docker, fora da pasta do Git:**
  - `sistema-votacao-dados` → banco (`/app/data/voting.db`) e backups automáticos (`/app/data/backups/`)
  - `sistema-votacao-fotos` → fotos dos candidatos (`/app/static/uploads/`)
- **`git pull`, `git clone` novo e `docker compose up -d --build` nunca tocam nesses volumes.** O banco e as fotos que vêm no repositório (`./data/voting.db` e `./static/uploads/`) só são usados **na primeira instalação**, quando o volume ainda está vazio. Depois disso o contêiner avisa no log: `Banco de produção preservado`.
- **Fotos novas do repositório** entram sem sobrescrever nenhuma foto existente.
- **Backup automático:** a cada subida do contêiner (inclusive a cada publicação) é feita uma cópia do banco em `/app/data/backups/`, antes de qualquer migração. Ficam as 20 últimas.
- **Nunca use `docker compose down -v`** em produção: o `-v` apaga os volumes, isto é, os votos.

### Levar os votos que já existem para o servidor novo (só na primeira instalação)
O banco que vem no repositório traz eventos, candidatos e configurações, mas **não os votos já feitos** (os e-mails de quem votou não ficam no Git).
Para começar com os votos atuais, antes do primeiro `docker compose up`:
1. Peça ao responsável pelo sistema a cópia atual do banco (`voting.db`).
2. Coloque-a no servidor em `./data/voting.db` (substituindo a do repositório; **não faça commit dela**).
3. Rode `docker compose up -d --build`. No log deve aparecer `Primeira instalação: criando o banco a partir de ./data/voting.db`.

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

O comando `up -d --build` reconstrói a imagem com o código novo e recria o contêiner. **Votos, eleitores, candidatos, histórico de exclusões e fotos continuam exatamente como estavam**, porque moram nos volumes. Conferir no log: `docker compose logs votacao | grep INFO` deve mostrar `Banco de produção preservado` e o nome do backup feito na subida.

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

O contêiner já faz um backup do banco a cada subida (pasta `/app/data/backups/`, 20 últimos). Para um backup completo fora do servidor (banco + fotos):
```bash
docker run --rm -v sistema-votacao-dados:/dados -v sistema-votacao-fotos:/fotos -v "$PWD":/saida alpine   tar -czf /saida/backup-votacao-$(date +%Y%m%d-%H%M).tar.gz -C / dados fotos
```

Restaurar um backup automático (exemplo):
```bash
docker compose exec votacao ls /app/data/backups
docker compose stop votacao
docker run --rm -v sistema-votacao-dados:/dados alpine cp /dados/backups/voting-AAAAMMDD-HHMMSS.db /dados/voting.db
docker compose start votacao
```

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
