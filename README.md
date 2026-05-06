# GUIAI - Facebook

Base tecnica inicial do modulo Facebook do InfoPolitica, alinhada com a especificacao em `docs/facebook_rebuild.md`.

## Status Atual

O repositorio ja cobre a base das Sprints 1 a 3 do planejamento:

- fundacao tecnica, configuracao centralizada e observabilidade basica
- API de consultas com persistencia inicial e publicacao em fila
- fluxo de busca Google com Scrapy, persistencia de URLs descobertas e encaminhamento para enriquecimento

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

## Ajustes do Google Search

- `GOOGLE_SEARCH_PROVIDER=auto` usa a API oficial quando `GOOGLE_SEARCH_API_KEY` e `GOOGLE_SEARCH_ENGINE_ID` existem; sem credenciais, usa HTML como fallback.
- `GOOGLE_SEARCH_PROVIDER=api` força o uso da Custom Search JSON API para clientes existentes do Google Programmable Search.
- `GOOGLE_SEARCH_PROVIDER=html` força a busca HTML, usando limites conservadores para reduzir bloqueios.
- `GOOGLE_SEARCH_BROWSER_FALLBACK_ENABLED` e `GOOGLE_SEARCH_BROWSER_FALLBACK_LIMIT` ativam uma tentativa com Playwright quando o Google responder com desafio dependente de JS, como `enablejs_challenge`.
- `GOOGLE_SEARCH_LANGUAGE`, `GOOGLE_SEARCH_REGION` e `GOOGLE_SEARCH_RESULTS_PER_PAGE` controlam o fingerprint basico da busca.
- `GOOGLE_SEARCH_CONSENT_COOKIE` ajuda a reduzir intersticiais de consentimento do Google.
- `GOOGLE_SEARCH_BLOCK_RETRY_LIMIT` define quantas novas tentativas a spider faz ao detectar pagina de desafio.
- `GOOGLE_SEARCH_DOWNLOAD_DELAY`, `GOOGLE_SEARCH_CONCURRENT_REQUESTS_PER_DOMAIN`, `GOOGLE_SEARCH_AUTOTHROTTLE_TARGET_CONCURRENCY`, `GOOGLE_SEARCH_AUTOTHROTTLE_START_DELAY` e `GOOGLE_SEARCH_AUTOTHROTTLE_MAX_DELAY` deixam o search mais conservador sem afetar o enrich.

- `GOOGLE_SEARCH_FALLBACK_PROVIDER=bing` troca para Bing quando o Google retornar `google_sorry` ou outro desafio sem recuperacao.

## Banco de dados

- Execute migrations com `alembic upgrade head`.
- O `docker compose` executa `alembic upgrade head` via o servico `face-migrate` antes de subir API e spiders.
- O `face-migrate` tenta reconectar ao banco por alguns ciclos antes de falhar, o que reduz erro de bootstrap em ambientes mais lentos.
