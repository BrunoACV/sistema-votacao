# Original User Request

## Initial Request — 2026-09-24T17:32:21Z

Sistema web institucional do INTS (Instituto Nacional de Tecnologia e Saúde) desenvolvido em Python e SQLite rodando na porta 8080, permitindo cadastro público de participantes com foto, formulário de avaliação onde colaboradores avaliam cada candidato atribuindo notas, restrito a e-mails institucionais `@ints.org.br` (um voto por pessoa), painel administrativo protegido por senha com recursos de moderação/exclusão, e tela de resultados.

Working directory: `C:\Trabalho\IA\Projetos\ints_voting_system`
Integrity mode: development

## Requirements

### R1. Cadastro Público de Participantes
Implementar formulário público e responsivo de inscrição para participantes com os campos: Nome Completo, E-mail do participante, Descrição/Identificação da fantasia/inscrição e Upload de Foto (JPG, PNG, WEBP).
- Cada e-mail de participante só pode se cadastrar uma única vez (bloquear duplicidade no cadastro).
- As imagens devem ser salvas com segurança em diretório local persistente e vinculadas ao banco SQLite.

### R2. Formulário Público de Votação / Avaliação por Notas
Implementar interface pública de avaliação listando todos os candidatos ativos com suas respectivas fotos e dados.
- O votante deve informar obrigatoriamente seu e-mail institucional.
- **Validação estrita de domínio:** Apenas e-mails terminados em `@ints.org.br` são aceitos.
- **Voto único:** O sistema deve verificar no banco se o e-mail já votou. Se já tiver votado, a submissão deve ser terminantemente rejeitada com alerta explicativo.
- **Pontuação:** O votante atribui uma nota/pontuação para cada participante cadastrado em uma única submissão consolidada.

### R3. Painel Administrativo de Moderação
Área de gerenciamento protegida por senha de administrador (configurável via variável de ambiente ou arquivo `.env`, ex: `ADMIN_PASSWORD`).
- Listagem completa de todos os participantes cadastrados com foto, data e quantidade de votos/médias.
- Capacidade de exclusão/desclassificação de candidatos (apagando registros dependentes no banco e arquivo de foto).
- Listagem dos e-mails institucionais que já votaram e suas respectivas avaliações.

### R4. Tela Pública de Resultados / Ranking
Página com o placar final e ranking dos participantes ordenados pela média ou pontuação total, com indicação visual de pódio, fotos dos candidatos e total de votos computados.

### R5. Execução e Infraestrutura
Aplicação desenvolvida em Python (ex: FastAPI ou Flask) com persistência em SQLite, configurada para iniciar e escutar na porta `8080` (`0.0.0.0:8080`), sem dependência de domínio específico (acessível diretamente via IP local ou `localhost:8080`).

## Acceptance Criteria

### Validações de Negócio e Segurança
- [ ] Cadastro com e-mail já existente é bloqueado com mensagem de erro clara.
- [ ] Uploads aceitam somente imagens válidas e as fotos são renderizadas corretamente nas telas públicas.
- [ ] Submissão de voto com e-mail não institucional (ex: `@gmail.com`, `@outlook.com`) é rejeitada com código HTTP 400/422 e aviso na tela.
- [ ] Submissão de voto com e-mail `@ints.org.br` já utilizado anteriormente é rejeitada, impedindo votos duplicados.
- [ ] Apenas requisições com a senha administrativa correta conseguem excluir participantes ou acessar a gestão interna.
- [ ] Banco de dados SQLite persiste dados de participantes, fotos e votos sem perda após reinicialização do serviço.

### Verificação Programática
- [ ] Script de teste automatizado (ex: `pytest` ou script Python com `requests`/`httpx`) executando a suíte de ponta a ponta:
  1. Cria 2 participantes com upload de foto mock.
  2. Valida tentativa de cadastro repetido (deve falhar).
  3. Envia votos de um e-mail `@ints.org.br` com notas para os participantes (deve suceder).
  4. Tenta reenviar voto com o mesmo e-mail `@ints.org.br` (deve falhar).
  5. Tenta votar com e-mail externo (deve falhar).
  6. Acessa área admin com senha, deleta um participante e valida exclusão no banco e no ranking.
  7. Valida que o servidor sobe na porta 8080.
