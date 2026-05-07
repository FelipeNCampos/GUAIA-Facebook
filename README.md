# GUIAI - Facebook

Base tecnica inicial do modulo Facebook do InfoPolitica, alinhada com a especificacao em `docs/facebook_rebuild.md`.

## Status Atual

O repositorio ja cobre a base das Sprints 1 a 3 do planejamento:

- fundacao tecnica, configuracao centralizada e observabilidade basica
- API de consultas com persistencia inicial e publicacao em fila
- fluxo de busca via SearXNG com Scrapy, persistencia de URLs descobertas e encaminhamento para enriquecimento

## Como executar localmente

1. Crie um arquivo `.env` a partir de `.env.example` e substitua todos os valores `replace_me`.
   Mantenha `DATABASE_URL`, `RABBITMQ_URL`, `POSTGRES_*` e `RABBITMQ_DEFAULT_*` coerentes entre si.
   Se estiver rodando via Docker Compose, use `postgres` e `rabbitmq` como hostnames nas URLs, nao `localhost`.
2. Instale dependencias com `python -m pip install -e ".[dev]"`.
3. Suba o SearXNG com `docker compose up -d searxng` se quiser validar o fluxo completo de busca localmente.
4. Rode a API com `uvicorn face.api:app --host 0.0.0.0 --port 8000`.
5. Execute os testes com `pytest`.

## Como subir com Docker Compose

1. Crie um arquivo `.env` a partir de `.env.example` e substitua todos os valores `replace_me`.
   Mantenha `DATABASE_URL`, `RABBITMQ_URL`, `POSTGRES_*`, `RABBITMQ_DEFAULT_*`, `SEARXNG_INTERNAL_URL`, `SEARXNG_BASE_URL` e `SEARXNG_SECRET` coerentes entre si.
   Para Compose, `DATABASE_URL` deve apontar para `@postgres:5432` e `RABBITMQ_URL` para `@rabbitmq:5672`.
2. Execute `docker compose up --build`.

## Container SearXNG

- O Compose agora sobe um servico `searxng` em `http://localhost:8081`.
- A spider de busca consulta o endpoint JSON interno `SEARXNG_INTERNAL_URL`, que por padrao aponta para `http://searxng:8080`.
- A configuracao montada em `docker/searxng/settings.yml` habilita `format=json`, mantendo a busca privada dentro do stack.
- Se quiser validar manualmente, use `http://localhost:8081/search?q=site:facebook.com%20%22tema%22&format=json`.
- O guia operacional detalhado do container esta em [docs/searxng.md](docs/searxng.md).

## Playwright

- `PLAYWRIGHT_HEADLESS_MODE=auto` usa modo headless automaticamente em ambientes sem `DISPLAY`, como containers Linux e WSL.
- `PLAYWRIGHT_HEADLESS_MODE=headless` forca execucao headless.
- `PLAYWRIGHT_HEADLESS_MODE=headed` forca execucao com janela visivel.
- `PLAYWRIGHT_HEADLESS=true` continua funcionando como atalho legado para forcar headless.

## Desenvolvimento com watch

1. Suba o ambiente base com `docker compose up --build`.
2. Em outro terminal, rode `docker compose watch`.
3. Alteracoes em `src/`, `migrations/` e `alembic.ini` sao sincronizadas automaticamente.
4. O `face-api` usa `uvicorn --reload`, entao recarrega sem rebuild.
5. Os spiders `face-search-spider` e `face-enrich-spider` reiniciam automaticamente quando o codigo sincronizado muda.
6. Alteracoes em `pyproject.toml` ou `Dockerfile` disparam rebuild dos servicos acompanhados.

## Ajustes da busca

- `SEARXNG_SEARCH_LANGUAGE`, `SEARXNG_SEARCH_REGION`, `SEARXNG_SEARCH_CATEGORY` e `SEARXNG_ENABLED_ENGINES` definem como a spider consulta o SearXNG.
- `SEARXNG_RESULTS_PER_PAGE` controla o volume de resultados pedidos por pagina.
- `SEARCH_MAX_PAGES` controla quantas paginas a spider percorre por tentativa. O limite total padrao fica em `SEARCH_MAX_PAGES * SEARXNG_RESULTS_PER_PAGE`.
- `SEARXNG_SAFE_SEARCH` repassa o nivel de safe search para o backend.
- `SEARCH_BLOCK_RETRY_LIMIT` define quantas novas tentativas a spider faz quando o backend responder com erro HTTP.
- `SEARCH_DOWNLOAD_DELAY`, `SEARCH_CONCURRENT_REQUESTS_PER_DOMAIN`, `SEARCH_AUTOTHROTTLE_TARGET_CONCURRENCY`, `SEARCH_AUTOTHROTTLE_START_DELAY` e `SEARCH_AUTOTHROTTLE_MAX_DELAY` deixam o search mais conservador sem afetar o enrich.

## Banco de dados

- Execute migrations com `alembic upgrade head`.
- O `docker compose` executa `alembic upgrade head` via o servico `face-migrate` antes de subir API e spiders.
- O `face-migrate` tenta reconectar ao banco por alguns ciclos antes de falhar, o que reduz erro de bootstrap em ambientes mais lentos.
