# ESPECIFICAÇÃO DE REQUISITOS DE SOFTWARE – MÓDULO FACEBOOK
## (Stack Reformulada: Scrapy + Playwright Stealth)

**Código do documento:** INFOPOL-FB-SRS-001-v2  
**Versão:** 2.0.0 (rascunho)  
**Data:** 2026-05-05  
**Status:** Em elaboração  
**Responsável:** Squad Dados / Scrapy InfoPolitica  
**Aprovadores:** A definir  
**Alteração principal:** Substituição de Selenium + undetected-chromedriver por Scrapy + scrapy-playwright com patches de stealth

---

## 1. Introdução

### 1.1 Propósito

Este documento especifica os requisitos de software do módulo Facebook do projeto InfoPolitica, reformulado para adotar **Scrapy como framework principal de crawling** e **scrapy-playwright com patches de stealth** como camada de renderização de páginas dinâmicas, em substituição à stack anterior baseada em Selenium + undetected-chromedriver.

### 1.2 Escopo

O módulo Facebook tem por objetivo coletar, enriquecer e disponibilizar dados públicos do domínio `facebook.com` relacionados a termos de busca informados por sistemas clientes, utilizando o Google como mecanismo de descoberta, **Scrapy + Playwright** para navegação automatizada, RabbitMQ para orquestração de jobs entre serviços e PostgreSQL como fonte canônica de dados.

Estão fora do escopo deste documento:
- Coleta e análise de dados do Instagram.
- Distribuição como instalador desktop Windows.
- Interface gráfica cliente; qualquer interface visual é considerada consumidor externo da API.

Em caso de conflito entre o comportamento legado e esta especificação, prevalece o comportamento descrito neste documento.

### 1.3 Justificativa da Mudança de Stack

A migração de Selenium + undetected-chromedriver para Scrapy + Playwright Stealth se justifica pelos seguintes fatores técnicos:

| Critério | Selenium + undetected-chromedriver | Scrapy + Playwright Stealth |
|---|---|---|
| Fila de requisições | Manual (externa) | **Nativa no Scrapy** |
| Pipeline de dados | Manual | **Nativo no Scrapy** |
| Retry automático | Manual | **Nativo no Scrapy** |
| Paralelismo | Síncrono por padrão | **Assíncrono (asyncio + twisted)** |
| Manutenção anti-detecção | Patches binários frágeis; quebram a cada update do Chrome | **Patches via JS inject, mais estáveis** |
| Interceptação de rede | Não suportada | **Nativa no Playwright** |
| Uso híbrido (HTTP puro + browser) | Não suportado | **Suportado: Playwright só onde necessário** |

### 1.4 Definições, Acrônimos e Abreviações

- **API** – Application Programming Interface.
- **DLQ** – Dead Letter Queue.
- **SRS** – Software Requirements Specification.
- **TTL** – Time To Live (tempo de vida do cache).
- **`id_query`** – Identificador funcional principal de cada consulta de coleta.
- **Consulta** – Unidade de trabalho identificada por `id_query`, `subject`, intervalo de datas e origem (`query_source`).
- **Spider** – Classe Scrapy responsável por definir regras de extração e crawling.
- **Pipeline** – Componente Scrapy de processamento pós-extração (validação, persistência).
- **Middleware** – Camada intermediária Scrapy para proxies, user-agents, cookies e retry.
- **Stealth Patch** – Conjunto de scripts JavaScript injetados via Playwright para remover rastros de automação antes do carregamento da página.

---

## 2. Visão Geral do Sistema

### 2.1 Contexto e Limites

O módulo Facebook opera como um subsistema de coleta de dados, exposto via API HTTP e integrado com outros componentes de InfoPolitica por meio de `id_query` e filas RabbitMQ.

Limites principais:
- **Entrada:** requisições HTTP (REST) ou mensagens em filas contendo consultas de coleta.
- **Saída:** registros enriquecidos em PostgreSQL, arquivos JSON/XLSX derivados, eventos de progresso e status via API.

### 2.2 Arquitetura de Alto Nível (Reformulada)

```
[Cliente / API]
      │ POST /facebook/queries
      ▼
[face-api]  ──publica──▶  [RabbitMQ]
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
   [face-search-spider]  [face-enrich-spider]  [face-export-worker]
   (Scrapy + HTTP puro)  (Scrapy + Playwright) (PostgreSQL → JSON/XLSX)
              │                │
              └────────────────┘
                       │
                  [PostgreSQL]
```

**Diferença chave da arquitetura anterior:** Os workers `face-search-worker` e `face-enrichment-worker` agora são **Scrapy Spiders**, não scripts Selenium standalone. O Playwright é ativado apenas no spider de enriquecimento (que precisa de JS), enquanto o spider de busca usa HTTP puro (mais rápido e sem risco de detecção).

### 2.3 Stakeholders Principais

- Equipe de dados / IA (consome dados enriquecidos para análise e modelos de NLP).
- Equipe de produto (especifica escopo e KPIs de coleta).
- Operação/DevOps (responsável por implantação, monitoramento e SRE).
- Compliance/Segurança (responsável por aderência a políticas internas e termos de uso das plataformas).

---

## 3. Requisitos de Interface Externa

### 3.1 Interface de API HTTP

A API REST deve expor, no mínimo, os seguintes endpoints (inalterados em relação à v1):

- `POST /facebook/queries` – criação de uma ou mais consultas.
- `GET /facebook/queries/{id_query}` – obtenção de status e progresso.
- `GET /facebook/queries/{id_query}/records` – leitura de registros descobertos e enriquecidos.
- `GET /facebook/queries/{id_query}/exports` – listagem de artefatos exportados.
- `POST /facebook/queries/{id_query}/export` – solicitação de exportação JSON/XLSX.

Códigos de resposta HTTP seguem o padrão da v1 (202, 200, 404, 409, 422, 500).

### 3.2 Interface de Filas (RabbitMQ)

As filas permanecem idênticas à v1. O RabbitMQ atua como orquestrador **entre serviços** (ex.: disparo de um spider de enriquecimento a partir de uma URL descoberta), complementando a fila interna do Scrapy que opera **dentro de cada spider**.

Filas mínimas:
- `face.search.request`
- `face.search.cache_lookup`
- `face.url.discovered`
- `face.enrich.request`
- `face.enrich.cache_lookup`
- `face.record.persisted`
- `face.export.request`
- `face.job.events`
- `face.dead_letter`

Cada fila deve possuir política de retry definida (número máximo de tentativas, backoff exponencial, critério de envio à `face.dead_letter`).

### 3.3 Interface com Navegadores (Scrapy + Playwright Stealth)

**Esta seção substitui completamente a seção 3.3 da v1 (Selenium).**

O sistema deve usar **Scrapy com scrapy-playwright** como camada de renderização de páginas dinâmicas. O Playwright deve ser configurado com patches de stealth aplicados via `add_init_script` antes de cada carregamento de página.

#### 3.3.1 Configuração Scrapy + Playwright (settings.py)

```python
# settings.py

DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}

PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": False,       # False como padrão para coleta autenticada (equivalente ao comportamento legado)
    "args": [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ],
}

PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30000  # ms

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
ASYNCIO_EVENT_LOOP = "uvloop"
```

#### 3.3.2 Stealth Patch (substituição do undetected-chromedriver)

O patch de stealth é aplicado via `add_init_script` no contexto Playwright antes do carregamento de qualquer página, removendo os sinais de automação que sistemas anti-bot detectam:

```python
STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'pt', 'en-US']});
    window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
    Object.defineProperty(navigator, 'permissions', {
        get: () => ({ query: () => Promise.resolve({ state: 'granted' }) })
    });
"""
```

Este patch deve ser registrado no Playwright context em cada nova sessão, equivalendo funcionalmente ao que o `undetected-chromedriver` fazia por modificação binária, porém com maior estabilidade frente a atualizações do Chrome.

#### 3.3.3 Uso Híbrido: HTTP Puro vs. Playwright

O sistema deve ativar o Playwright **somente onde necessário**, para economizar recursos:

```python
# Spider de busca Google (HTTP puro — sem browser)
yield scrapy.Request(google_url, callback=self.parse_search)

# Spider de enriquecimento Facebook (com Playwright — JS necessário)
yield scrapy.Request(facebook_url, callback=self.parse_facebook, meta={
    "playwright": True,
    "playwright_context": "authenticated",
    "playwright_page_init_callback": apply_stealth_patch,
})
```

#### 3.3.4 Injeção de Cookies de Sessão

A injeção de cookies de sessão autenticada deve ser feita via Playwright context, substituindo o mecanismo de injeção anterior do Selenium:

```python
async def create_authenticated_context(playwright):
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir="./session_data",
    )
    cookies = load_cookies_from_db()  # face_session_cookies
    await context.add_cookies(cookies)
    return context
```

---

## 4. Requisitos Funcionais

Os requisitos funcionais FR-01 a FR-26 permanecem funcionalmente idênticos à v1. As alterações são exclusivamente de **implementação** (como cada requisito é atendido), não de **comportamento observável**.

### 4.1 Criação e Gestão de Consultas (FR-01 a FR-05)
Sem alteração funcional. A API (`face-api`) continua responsável por validação, persistência em `face_jobs` e publicação em `face.search.request`.

### 4.2 Busca Google e Descoberta de URLs (FR-06 a FR-10)

**Alteração de implementação:** O `face-search-worker` torna-se um **Scrapy Spider** que usa HTTP puro (sem browser) para busca no Google. Isso é mais rápido e menos sujeito a detecção do que a abordagem anterior com Selenium.

```python
class GoogleSearchSpider(scrapy.Spider):
    name = "google_search"

    def start_requests(self):
        query = f'site:facebook.com "{self.subject}" after:{self.start} before:{self.end}'
        url = f"https://www.google.com/search?q={quote(query)}"
        yield scrapy.Request(url, callback=self.parse, headers={
            "User-Agent": random.choice(USER_AGENTS),
        })

    def parse(self, response):
        for url in response.css("a[href*='facebook.com']::attr(href)").getall():
            normalized = normalize_url(url)
            category = classify_url(normalized)
            yield FacebookURLItem(url=normalized, category=category, id_query=self.id_query)

        next_page = response.css("a#pnnext::attr(href)").get()
        if next_page and self.page_count < MAX_PAGES:
            yield response.follow(next_page, callback=self.parse)
```

**FR-06 a FR-10** continuam sendo atendidos. A lógica de normalização, classificação e persistência é idêntica, movida para o **Pipeline Scrapy** em vez de código imperativo avulso.

### 4.3 Enriquecimento de Publicações (FR-11 a FR-15)

**Alteração de implementação:** O `face-enrichment-worker` torna-se um **Scrapy Spider com Playwright ativo**, consumindo mensagens de `face.enrich.request`.

```python
class FacebookEnrichSpider(scrapy.Spider):
    name = "facebook_enrich"

    def start_requests(self):
        url = self.facebook_url
        yield scrapy.Request(url, callback=self.parse, meta={
            "playwright": True,
            "playwright_context": "authenticated",
            "playwright_page_init_callback": apply_stealth_patch,
            "playwright_page_methods": [
                PageMethod("wait_for_selector", "[data-pagelet='FeedUnit']", timeout=15000),
            ],
        })

    async def parse(self, response):
        page = response.meta["playwright_page"]
        # Extração de metadados via seletores CSS/XPath
        data = extract_facebook_metadata(response, self.category)
        await page.close()
        yield FacebookRecordItem(**data, id_query=self.id_query)
```

**FR-11 a FR-15** continuam sendo atendidos. O comportamento de detecção de captcha (FR-25, FR-26) é preservado, agora monitorado via interceptação de rede Playwright:

```python
async def handle_response(response):
    if "checkpoint" in response.url or "captcha" in response.url:
        raise CaptchaDetectedError(response.url)

page.on("response", handle_response)
```

### 4.4 Cache de Pesquisa e de Resultados (FR-16 a FR-18)
Sem alteração funcional. A verificação de cache ocorre no Pipeline Scrapy antes da persistência.

### 4.5 Exportação de Resultados (FR-19 a FR-22)
Sem alteração. O `face-export-worker` permanece desacoplado do browser e continua operando puramente sobre PostgreSQL.

### 4.6 Monitoramento e Auditoria (FR-23 a FR-24)
Sem alteração funcional. Adicionalmente, o Scrapy emite métricas nativas (requests/min, items scraped, erros) que podem ser expostas via `StatsCollector` e integradas ao sistema de observabilidade existente.

### 4.7 Captcha e 2Captcha (FR-25 a FR-26)
Sem alteração funcional. A detecção de captcha agora é feita via interceptação de URL no Playwright (mais confiável do que análise de DOM com Selenium). A resolução via 2Captcha permanece idêntica.

---

## 5. Requisitos Não Funcionais

### 5.1 Desempenho e Capacidade

**NFR-01** – A criação de consulta via API deve responder rapidamente (ordem de segundos), independente da duração da coleta assíncrona.

**NFR-02** – O sistema deve permitir configuração de concorrência por variáveis de ambiente, utilizando os parâmetros nativos do Scrapy:

```python
CONCURRENT_REQUESTS = 16              # paralelismo global
CONCURRENT_REQUESTS_PER_DOMAIN = 4   # limite por domínio
DOWNLOAD_DELAY = 2                    # delay base entre requests
RANDOMIZE_DOWNLOAD_DELAY = True       # jitter automático (0.5x a 1.5x o delay)
AUTOTHROTTLE_ENABLED = True           # ajuste automático de velocidade
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0
```

**Vantagem sobre a v1:** O Scrapy gerencia concorrência nativamente via asyncio/Twisted, eliminando a necessidade de gerenciar manualmente threads ou processos Selenium paralelos.

### 5.2 Confiabilidade e Tolerância a Falhas

**NFR-03** – O sistema deve tolerar falhas pontuais de Playwright, rede e parsing, isolando erros por URL.

**NFR-04** – O retry de requisições deve ser configurado via middleware nativo do Scrapy:

```python
RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]
RETRY_EXCEPTIONS = [
    "scrapy.core.downloader.handlers.http11.TunnelError",
    "twisted.internet.error.TimeoutError",
]
```

Mensagens que excederem o limite de tentativas são encaminhadas à `face.dead_letter` via middleware customizado.

**NFR-05 (novo):** O Scrapy deve ser configurado com `CLOSESPIDER_ERRORCOUNT` para interromper spiders que acumulem erros excessivos, evitando consumo de recursos em sessões degradas:

```python
CLOSESPIDER_ERRORCOUNT = 50
```

### 5.3 Segurança e Proteção de Dados

**NFR-06** – Credenciais, cookies de sessão e chaves de API devem ser mantidos em variáveis de ambiente ou cofre seguro, nunca em código-fonte ou logs.

**NFR-07** – Cookies armazenados em `face_session_cookies` devem incluir campos de auditoria (`created_at`, `expires_at`, `last_validated_at`, `is_active`, `invalid_reason`) e ser invalidados quando expirados ou em falhas de autenticação. Este comportamento é idêntico à v1 e não depende da camada de browser.

### 5.4 Manutenibilidade e Portabilidade

**NFR-08** – A solução deve ser executável em ambiente Linux conteinerizado via Docker e Docker Compose. O serviço `selenium-chrome` da v1 é **substituído** por uma imagem com Playwright e Chromium instalados:

```yaml
# docker-compose.yml (trecho)
services:
  face-search-spider:
    build: ./spiders/search
    environment:
      - RABBITMQ_URL=amqp://rabbitmq:5672
      - DATABASE_URL=postgresql://postgres:5432/infopolitica

  face-enrich-spider:
    build: ./spiders/enrich
    environment:
      - RABBITMQ_URL=amqp://rabbitmq:5672
      - DATABASE_URL=postgresql://postgres:5432/infopolitica
      - PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
    volumes:
      - playwright-browsers:/ms-playwright

  rabbitmq:
    image: rabbitmq:3-management

  postgres:
    image: postgres:16
```

**NFR-09** – Migrations de banco devem ser gerenciadas por Alembic, sem alteração em relação à v1.

### 5.5 Observabilidade

**NFR-10** – Todos os serviços devem emitir logs estruturados em JSON com correlação por `id_query`.

**NFR-11 (novo):** O Scrapy deve expor métricas nativas via `StatsCollector` integradas ao sistema de monitoramento:

```python
# Métricas disponíveis nativamente
{
    "downloader/request_count": 1423,
    "downloader/response_status_count/200": 1380,
    "downloader/response_status_count/429": 12,
    "item_scraped_count": 847,
    "retry/count": 34,
    "playwright/page_count": 398,
}
```

---

## 6. Estrutura de Código (Reformulada)

```text
src/
  face/
    api.py                  # FastAPI / Flask – inalterado
    config.py               # variáveis de ambiente – inalterado
    models.py               # modelos Pydantic – inalterado
    queues.py               # integração RabbitMQ – inalterado
    repository.py           # acesso PostgreSQL – inalterado
    export_worker.py        # geração JSON/XLSX – inalterado

    spiders/
      google_search.py      # NOVO: Scrapy Spider – busca Google (HTTP puro)
      facebook_enrich.py    # NOVO: Scrapy Spider – enriquecimento (Playwright)

    pipelines/
      persist.py            # NOVO: Pipeline Scrapy – persistência em PostgreSQL
      cache.py              # NOVO: Pipeline Scrapy – verificação de cache
      events.py             # NOVO: Pipeline Scrapy – publicação de eventos

    middlewares/
      stealth.py            # NOVO: aplica patches JS de stealth via Playwright
      proxy.py              # NOVO: rotação de proxies por request
      retry.py              # NOVO: retry customizado com DLQ

    browser.py              # SUBSTITUÍDO: playwright_context (era selenium driver)
    facebook_parser.py      # inalterado – lógica de extração de metadados
    url_classifier.py       # inalterado – classificação de URLs por categoria
    captcha.py              # inalterado – detecção de captcha
    captcha_solver.py       # inalterado – integração 2Captcha
    login.py                # adaptado – injeção de cookies via Playwright context
    errors.py               # inalterado

  db/
    base.py
    migrations/

  common/
    logging.py
    time.py
    serialization.py
```

**Arquivos removidos (não mais necessários):**
- `browser.py` baseado em Selenium/undetected-chromedriver → substituído por `browser.py` baseado em Playwright context
- `search_worker.py` (script imperativo Selenium) → substituído por `spiders/google_search.py`
- `enrichment_worker.py` (script imperativo Selenium) → substituído por `spiders/facebook_enrich.py`

---

## 7. Modelo de Dados

Sem alteração em relação à v1. As tabelas são idênticas:

- `face_jobs` – metadados de consultas.
- `face_records` – registros por URL com dados enriquecidos.
- `face_job_events` – eventos e auditoria de processamento.
- `face_exports` – metadados de artefatos exportados.
- `face_session_cookies` – cookies de sessão por perfil de login.
- `face_search_cache` – cache de resultados de busca Google.
- `face_recent_results_cache` – cache de resultados de enriquecimento de URLs.

---

## 8. Fluxo de Processamento (Reformulado)

1. API recebe lista de consultas, valida payload e grava em `face_jobs`.
2. API verifica cache de pesquisa; se houver, persiste resultados e encaminha para enriquecimento/exportação.
3. Não havendo cache, API publica consulta em `face.search.request`.
4. **`GoogleSearchSpider` (Scrapy, HTTP puro)** consome a consulta, executa busca Google via requests HTTP paralelas, normaliza/classifica URLs e persiste em `face_records` via Pipeline Scrapy.
5. URLs são enviadas para checagem de cache e, se necessário, publicadas em `face.enrich.request`.
6. **`FacebookEnrichSpider` (Scrapy + Playwright Stealth)** consome URLs, aplica patch de stealth, injeta cookies de sessão via Playwright context, abre a página do Facebook, extrai metadados e persiste em `face_records` + cache via Pipeline Scrapy.
7. Eventos são registrados em `face_job_events` e `status_current` é atualizado via pipeline de eventos.
8. Com coleta finalizada, mensagem é publicada em `face.export.request`.
9. `face-export-worker` gera JSON/XLSX a partir de PostgreSQL e registra em `face_exports`.
10. Sistemas clientes consultam status, registros e exports por `id_query`.

---

## 9. Dependências e Versões

```toml
# pyproject.toml
[tool.poetry.dependencies]
python = "^3.11"
scrapy = "^2.11"
scrapy-playwright = "^0.0.40"
playwright = "^1.44"
aio-pika = "^9.4"         # RabbitMQ assíncrono
sqlalchemy = "^2.0"
alembic = "^1.13"
pydantic = "^2.7"
uvloop = "^0.19"           # event loop de alta performance
fastapi = "^0.111"
```

**Instalação do Chromium para Playwright:**
```bash
playwright install chromium
playwright install-deps chromium
```

**Removidas da v1:**
```
selenium
undetected-chromedriver
selenium-stealth
webdriver-manager
```

---

## 10. Testes e Aceitação

### 10.1 Testes Unitários
- Lógica de parsing, classificação e normalização de URLs — inalterados.
- Stealth patch: verificar que `navigator.webdriver` retorna `undefined` após injeção.
- Pipeline de persistência: verificar idempotência e tratamento de duplicatas.

### 10.2 Testes de Integração
- Scrapy + Playwright: verificar que páginas dinâmicas do Facebook são renderizadas corretamente.
- RabbitMQ: verificar consumo e publicação de mensagens pelos spiders.
- PostgreSQL: verificar pipelines de persistência e cache.

### 10.3 Testes Ponta a Ponta
- Ambiente Docker Compose completo levantando todos os serviços.
- Fluxo completo de uma consulta, do `POST /facebook/queries` até o export disponível em `GET /facebook/queries/{id}/exports`.

### 10.4 Critérios de Aceitação
O módulo será considerado aceito quando:
- Executar o fluxo completo da Seção 8 para um conjunto de consultas de teste.
- Atender todos os requisitos funcionais FR-01 a FR-26 e não funcionais NFR-01 a NFR-11.
- Possuir cobertura de testes automatizados mínima acordada e pipeline CI/CD operando em GitHub Actions com release por tag `vN.N.N`.

---

## 11. Controle de Versão do Documento

| Versão | Data | Responsável | Alteração |
|---|---|---|---|
| 1.0.0 | 2026-05-05 | Squad Dados | Versão inicial – Selenium + undetected-chromedriver |
| 2.0.0 | 2026-05-05 | Squad Dados | Migração para Scrapy + Playwright Stealth |

---

## 12. Anexos Recomendados

- Exemplos de payloads JSON de entrada e saída.
- Exemplos de registros normalizados e formatos de export.
- Lista de variáveis de ambiente e valores padrão.
- Trechos de configuração de Docker Compose para ambiente mínimo.
- Script completo de stealth patch (JavaScript).
