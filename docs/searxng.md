# SearXNG no Stack Facebook

Este projeto sobe um container `searxng` para concentrar a descoberta de URLs antes do enrich do Facebook.

## Objetivo

- Encaminhar toda busca da spider para um backend local de metabusca.
- Remover dependencias diretas do contrato da API com filtros `after` e `before`.
- Padronizar o consumo em `format=json`, o que simplifica parse, retry e observabilidade.

## Como subir

1. Preencha `SEARXNG_SECRET` no `.env`.
2. Confirme `SEARXNG_INTERNAL_URL=http://searxng:8080`.
3. Rode `docker compose up -d searxng`.
4. Se quiser o stack inteiro, rode `docker compose up --build`.

## Como a aplicacao usa o container

- O servico `face-search-spider` chama `SEARXNG_INTERNAL_URL`.
- A consulta enviada ao backend segue o formato `site:facebook.com "assunto"`.
- A spider pede `format=json` e pagina via `pageno`.
- O SearXNG fica exposto localmente em `http://localhost:8081`.

## Validacao manual

Use no navegador ou com `curl`:

```text
http://localhost:8081/search?q=site:facebook.com%20%22tema%22&format=json
```

Resultado esperado:

- Resposta HTTP `200`.
- Corpo JSON com a chave `results`.
- Cada item com `url` que a spider consegue normalizar.

## Arquivos relacionados

- `docker-compose.yml`
- `docker/searxng/settings.yml`
- `src/face/spiders/google_search.py`
- `.env.example`
