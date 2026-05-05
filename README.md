# GUIAI - Facebook

Base tecnica inicial do modulo Facebook do InfoPolitica, alinhada com a especificacao em `docs/facebook_rebuild.md`.

## Sprint 1

Esta entrega prepara a fundacao da solucao com:

- estrutura de codigo aderente a arquitetura alvo
- configuracao centralizada por variaveis de ambiente
- logging estruturado em JSON
- Docker e Docker Compose para ambiente local
- Alembic com migration inicial
- pipeline CI com lint e testes basicos

## Como executar localmente

1. Crie um arquivo `.env` a partir de `.env.example` e substitua todos os valores `replace_me`.
   Mantenha `DATABASE_URL`, `RABBITMQ_URL`, `POSTGRES_*` e `RABBITMQ_DEFAULT_*` coerentes entre si.
   Se estiver rodando via Docker Compose, use `postgres` e `rabbitmq` como hostnames nas URLs, nao `localhost`.
2. Instale dependencias com `python -m pip install -e ".[dev]"`.
3. Rode a API com `uvicorn face.api:app --host 0.0.0.0 --port 8000`.
4. Execute os testes com `pytest`.

## Como subir com Docker Compose

1. Crie um arquivo `.env` a partir de `.env.example` e substitua todos os valores `replace_me`.
   Mantenha `DATABASE_URL`, `RABBITMQ_URL`, `POSTGRES_*` e `RABBITMQ_DEFAULT_*` coerentes entre si.
   Para Compose, `DATABASE_URL` deve apontar para `@postgres:5432` e `RABBITMQ_URL` para `@rabbitmq:5672`.
2. Execute `docker compose up --build`.

## Desenvolvimento com watch

1. Suba o ambiente base com `docker compose up --build`.
2. Em outro terminal, rode `docker compose watch`.
3. Alteracoes em `src/`, `migrations/` e `alembic.ini` sao sincronizadas automaticamente.
4. O `face-api` usa `uvicorn --reload`, entao recarrega sem rebuild.
5. Os spiders `face-search-spider` e `face-enrich-spider` reiniciam automaticamente quando o codigo sincronizado muda.
6. Alteracoes em `pyproject.toml` ou `Dockerfile` disparam rebuild dos servicos acompanhados.

## Banco de dados

- Execute migrations com `alembic upgrade head`.
- O `docker compose` executa `alembic upgrade head` via o servico `face-migrate` antes de subir API e spiders.
- O `face-migrate` tenta reconectar ao banco por alguns ciclos antes de falhar, o que reduz erro de bootstrap em ambientes mais lentos.
