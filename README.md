# Sistema de Votação Institucional de Halloween — INTS

Sistema web corporativo desenvolvido para o concurso anual de fantasias de Halloween do **Instituto Nacional de Tecnologia e Saúde (INTS)**.

Permite cadastro público de participantes com upload de fotos, votação única por colaborador restrita a e-mails institucionais (`@ints.org.br`), busca interativa de candidatos, placar com pódio olímpico e painel administrativo completo de moderação e auditoria com capacidade de remoção de votos.

---

## 🎃 Principais Características

- **Tema Dracula Dark Exclusivo:** Interface imersiva e moderna com paleta Dracula Dark (`#282a36`), sem opção de tema claro.
- **Validação Estrita de E-mail:** Apenas contas corporativas `@ints.org.br` são aceitas para votar.
- **Voto Único por Colaborador:** Verificação em tempo real via API e trava transacional no banco SQLite.
- **Votação com Busca:** Campo de busca em tempo real por nome do participante ou descrição da fantasia.
- **Votação Restrita (`/vote`):** Oculta na navegação pública para evitar acesso antecipado antes da liberação oficial.
- **Painel de Moderação (`/admin`):**
  - Métricas e KPIs (total de candidatos, votantes únicos, votos e média geral).
  - Central de Acesso Rápido conectando todas as **5 telas do sistema** para o moderador autenticado.
  - Desclassificação e remoção de candidatos.
- **Auditoria de Eleitores (`/admin/voters`):**
  - Listagem dos colaboradores que votaram.
  - Exclusão individual de votos (permitindo que o colaborador vote novamente se necessário).
  - Opção de zerar todos os votos em caso de reinício de votação.
- **Placar e Pódio Olímpico (`/results`):** Exibição estilizada dos 3 primeiros colocados (ouro, prata e bronze) e ranking geral.

---

## 🚀 Como Executar

### Opção 1: Via Docker Compose (Padrão de Produção e Infraestrutura)

Seguindo o padrão dos sistemas institucionais do INTS:

```bash
# 1. Clonar o repositório
git clone https://github.com/BrunoACV/sistema-votacao-ints.git
cd sistema-votacao-ints

# 2. Subir o contêiner
docker compose up -d --build

# 3. Acompanhar os logs
docker compose logs -f votacao
```

O sistema estará acessível em: `http://localhost:9090`.

Para detalhes completos de operação, portas alternativas e rotinas de backup, consulte o [DOCKER.md](DOCKER.md).

---

### Opção 2: Execução Local em Desenvolvimento (Python)

```bash
# 1. Criar e ativar o ambiente virtual
python -m venv .venv
source .venv/bin/activate  # No Windows: .venv\Scripts\activate

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Iniciar o servidor
python run.py
```

Ou simplesmente execute o script `iniciar.bat` (Windows) ou `./iniciar.sh` (Linux).

---

## 🧭 Mapa de Telas do Sistema

| Tela | URL | Descrição |
|---|---|---|
| **Placar & Pódio** | `/results` | Classificação oficial dos mais votados e pódio de Halloween. |
| **Inscrição de Candidato** | `/register` | Cadastro institucional de fantasias, setor e função. |
| **Votação** | `/vote` | Tela de votação restrita a e-mails `@ints.org.br`. |
| **Painel de Moderação** | `/admin` | Dashboard administrativo com métricas e gestão de candidatos. |
| **Auditoria de Eleitores** | `/admin/voters` | Lista de votantes com opção de exclusão e zeramento de votos. |
| **Health Check** | `/health` | Monitoramento de disponibilidade (HTTP 200 OK). |

---

## 🧪 Testes Automatizados

A aplicação conta com suíte completa de testes automatizados com cobertura ponta a ponta:

```bash
python -m unittest discover -s tests
```

---

## 📋 Documentação para Infraestrutura

- Para instruções detalhadas de deploy, volumes e monitoramento, leia o [DOCKER.md](DOCKER.md).
- Para a arquitetura completa e diretrizes técnicas para o analista responsável, leia o [PASSAGEM-DE-BASTAO.md](PASSAGEM-DE-BASTAO.md).

---

© 2026 Instituto Nacional de Tecnologia e Saúde (INTS). Todos os direitos reservados.
