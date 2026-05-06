# Planejamento de Desenvolvimento - Modulo Facebook

## 1. Objetivo

Este documento organiza o desenvolvimento do software descrito em [facebook_rebuild.md](./facebook_rebuild.md) em etapas iterativas para sprints, priorizando entregas incrementais, validacao tecnica antecipada e reducao de risco na migracao para a stack Scrapy + Playwright Stealth.

O foco do planejamento e entregar, ao final de cada sprint, um incremento verificavel do sistema, evitando uma implementacao monolitica e permitindo ajustes de arquitetura, observabilidade e operacao ao longo do projeto.

---

## 2. Premissas do Planejamento

- O modulo sera desenvolvido como servico backend, sem interface grafica propria.
- A API HTTP, RabbitMQ e PostgreSQL permanecem como componentes centrais da arquitetura.
- A migracao de Selenium para Scrapy + Playwright deve preservar o comportamento funcional descrito pelos FR-01 a FR-26.
- O desenvolvimento sera orientado por entregas pequenas, com validacao automatizada desde as primeiras sprints.
- Cada sprint deve produzir algo executavel, testavel e demonstravel.

---

## 3. Estrategia de Entrega

A estrategia recomendada e dividir o trabalho em 7 sprints. Cada sprint fecha um bloco coerente do fluxo:

1. Fundacao tecnica e esqueleto da solucao.
2. Entrada de consultas e orquestracao inicial.
3. Busca Google e descoberta de URLs.
4. Enriquecimento Facebook com Playwright Stealth.
5. Resiliencia, cache e tratamento de falhas.
6. Exportacao, observabilidade e operacao.
7. Endurecimento, testes finais e readiness de producao.

Essa sequencia reduz risco tecnico cedo, porque primeiro valida a base da aplicacao e depois o ponto mais sensivel da stack, que e o enriquecimento autenticado com browser automatizado.

---

## 4. Sprints

### Sprint 1 - Fundacao Tecnica e Arquitetura Base

**Objetivo**

Preparar a fundacao do projeto para suportar a nova arquitetura com separacao clara entre API, spiders, pipelines, middlewares e integracoes.

**Escopo**

- Estruturar o repositorio conforme a arquitetura alvo descrita na especificacao.
- Configurar ambiente Python, dependencias e padrao de configuracao por variaveis de ambiente.
- Definir base de logging estruturado com correlacao por `id_query`.
- Configurar Docker e Docker Compose para API, RabbitMQ, PostgreSQL e servicos de spiders.
- Preparar Alembic e estrutura inicial de migrations.
- Definir convencoes de erros, contratos internos e organizacao de modulos.

**Entregaveis**

- Estrutura inicial de codigo criada.
- `pyproject.toml` ou equivalente com dependencias principais.
- `docker-compose` funcional para ambiente local.
- Base de configuracao centralizada.
- Logging JSON padronizado.
- Pipeline de CI inicial para lint e testes basicos.

**Criterios de aceite**

- O ambiente sobe localmente com os servicos principais.
- A aplicacao consegue carregar configuracoes de ambiente sem valores hardcoded.
- Existe uma base de testes automatizados executando no CI.

---

### Sprint 2 - API de Consultas e Orquestracao Inicial

**Objetivo**

Entregar a camada de entrada do sistema, permitindo criar consultas, persisti-las e disparar o fluxo assincrono inicial.

**Escopo**

- Implementar `POST /facebook/queries`.
- Implementar persistencia inicial em `face_jobs`.
- Implementar publicacao de mensagens em `face.search.request`.
- Implementar `GET /facebook/queries/{id_query}` com status basico.
- Modelar eventos iniciais de job e trilha de auditoria.
- Validar payloads, regras de negocio e tratamento de erros HTTP.

**Entregaveis**

- API de criacao de consultas funcional.
- Repositorio de acesso a banco para jobs e eventos.
- Integracao RabbitMQ para publicacao da solicitacao de busca.
- Testes unitarios e de integracao da API.

**Criterios de aceite**

- Uma consulta pode ser criada via API e persistida com sucesso.
- O sistema publica a mensagem correspondente na fila correta.
- O status inicial do job pode ser consultado pela API.

---

### Sprint 3 - Busca Google e Descoberta de URLs

**Objetivo**

Colocar em producao o primeiro fluxo de coleta efetiva usando Scrapy em HTTP puro para descobrir URLs do Facebook com menor custo operacional.

**Escopo**

- Implementar `GoogleSearchSpider`.
- Implementar normalizacao e classificacao de URLs encontradas.
- Implementar uso preferencial da Custom Search JSON API quando configurada, mantendo HTML como fallback controlado.
- Implementar pipeline de persistencia em `face_records`.
- Implementar pipeline de eventos para progresso da busca.
- Implementar paginacao, limites de busca e configuracoes de concorrencia.
- Integrar descoberta com publicacao de URLs candidatas para enriquecimento.
- Definir e implementar o endpoint base de retry por etapa da query, cobrindo ao menos a etapa `search` nesta sprint.

**Entregaveis**

- Spider de busca funcional via Scrapy.
- Busca por API oficial quando `GOOGLE_SEARCH_API_KEY` e `GOOGLE_SEARCH_ENGINE_ID` estiverem configurados.
- Persistencia das URLs descobertas.
- Eventos de progresso da fase de busca.
- Endpoint de retry da etapa de busca funcional e integrado com reenfileiramento em `face.search.request`.
- Testes unitarios para classificacao e normalizacao.
- Testes de integracao da spider com banco e fila.

**Criterios de aceite**

- Uma consulta dispara o spider de busca e retorna URLs classificadas.
- As URLs ficam registradas com referencia ao `id_query`.
- O sistema consegue encaminhar URLs descobertas para a etapa seguinte.
- Uma query com falha, bloqueio ou necessidade de reprocessamento na etapa de busca pode ser reenfileirada via API sem recriacao da consulta.

---

### Sprint 4 - Enriquecimento Facebook com Playwright Stealth

**Objetivo**

Entregar o fluxo mais critico do sistema: abrir paginas do Facebook autenticadas, aplicar stealth patch e extrair metadados estruturados.

**Escopo**

- Implementar `FacebookEnrichSpider`.
- Implementar `browser.py` baseado em Playwright.
- Implementar middleware ou callback para aplicacao do stealth patch.
- Implementar injecao de cookies de sessao autenticada.
- Integrar parser de Facebook para extrair metadados por categoria de URL.
- Implementar deteccao inicial de captcha, checkpoint e falhas de autenticacao.

**Entregaveis**

- Spider de enriquecimento com Playwright funcional.
- Contexto autenticado reutilizavel.
- Extracao de metadados persistida em `face_records`.
- Fechamento correto de paginas e sessoes Playwright.
- Testes de integracao para renderizacao e parsing.

**Criterios de aceite**

- Uma URL descoberta pode ser enriquecida com sucesso em ambiente controlado.
- O stealth patch e aplicado antes do carregamento da pagina.
- Cookies de sessao sao injetados corretamente.
- Os principais metadados definidos na especificacao sao persistidos.

---

### Sprint 5 - Cache, Retry, DLQ e Resiliencia Operacional

**Objetivo**

Fortalecer a confiabilidade do sistema para operacao continua, reduzindo custo, retrabalho e falhas recorrentes.

**Escopo**

- Implementar `face_search_cache` e `face_recent_results_cache`.
- Implementar pipelines de verificacao de cache antes da persistencia ou reprocessamento.
- Implementar retry nativo e customizado com integracao a DLQ.
- Implementar middleware de proxy e politicas de rotacao.
- Implementar tratamento estruturado de captcha e integracao com 2Captcha.
- Configurar limites como `CLOSESPIDER_ERRORCOUNT`, timeout e backoff.

**Entregaveis**

- Cache funcional de busca e enriquecimento.
- Retry configurado com criterios claros.
- Encaminhamento para `face.dead_letter` em falhas persistentes.
- Registro de motivos de falha e invalidez de sessao.
- Testes cobrindo idempotencia, duplicidade e falhas intermitentes.

**Criterios de aceite**

- O sistema reaproveita resultados de cache quando aplicavel.
- Falhas temporarias sao reprocessadas sem duplicar registros.
- Falhas definitivas sao rastreaveis e encaminhadas para DLQ.
- O fluxo se degrada de forma controlada sem derrubar o processo inteiro.

---

### Sprint 6 - Exportacao, Endpoints de Consulta e Observabilidade

**Objetivo**

Completar o fluxo de consumo externo, permitindo consultar registros, acompanhar progresso detalhado e gerar artefatos finais.

**Escopo**

- Implementar `GET /facebook/queries/{id_query}/records`.
- Implementar `GET /facebook/queries/{id_query}/exports`.
- Implementar `POST /facebook/queries/{id_query}/export`.
- Implementar `face-export-worker` para JSON e XLSX.
- Consolidar status de job com eventos e etapas da coleta.
- Expor metricas operacionais e de spiders via `StatsCollector`.

**Entregaveis**

- Endpoints de consulta de registros e exports.
- Worker de exportacao funcional.
- Artefatos JSON e XLSX gerados a partir do PostgreSQL.
- Metricacao minima de requests, retries, erros e itens processados.
- Dashboards ou especificacao de monitoramento para operacao.

**Criterios de aceite**

- O cliente consegue consultar registros enriquecidos por `id_query`.
- O cliente consegue solicitar e listar exports gerados.
- As metricas principais do fluxo ficam disponiveis para observacao operacional.

---

### Sprint 7 - Qualidade Final, Testes E2E e Preparacao para Producao

**Objetivo**

Fechar o ciclo com validacao ponta a ponta, endurecimento operacional e readiness para implantacao.

**Escopo**

- Executar testes ponta a ponta do fluxo completo.
- Revisar performance, concorrencia e consumo de recursos.
- Revisar seguranca de credenciais, cookies e logs.
- Validar migrations, bootstrap de ambiente e recuperacao de falhas.
- Finalizar pipeline CI/CD com release por tag.
- Produzir documentacao operacional e runbooks minimos.

**Entregaveis**

- Suite E2E cobrindo o fluxo principal.
- Ajustes finais de tuning de Scrapy e Playwright.
- Pipeline CI/CD consolidado.
- Checklist de readiness para producao.
- Documentacao de operacao, troubleshooting e rollback.

**Criterios de aceite**

- O fluxo completo funciona do `POST /facebook/queries` ate a disponibilizacao do export.
- Os requisitos funcionais e nao funcionais prioritarios estao cobertos por validacao.
- O ambiente esta apto para homologacao ou rollout controlado.

---

## 5. Backlog Transversal

Alguns temas devem ser trabalhados ao longo de varias sprints, e nao apenas em uma entrega isolada:

- Testes automatizados unitarios, integracao e E2E.
- Observabilidade, logs e metricas.
- Seguranca de segredos, cookies e dados sensiveis.
- Documentacao tecnica e operacional.
- Revisao de selectors, parsing e adaptacao a mudancas do Facebook.
- Tuning de concorrencia, delays, throttling e proxies.

---

## 6. Ordem de Priorizacao

Se houver necessidade de reduzir escopo inicial, a ordem recomendada de priorizacao e:

1. Fundacao tecnica.
2. API de consultas.
3. Busca Google.
4. Enriquecimento Facebook.
5. Cache e resiliencia.
6. Exportacao.
7. Hardening final.

Com isso, o primeiro marco relevante do projeto e um MVP tecnico capaz de:

- Receber uma consulta.
- Descobrir URLs do Facebook.
- Enriquecer ao menos um subconjunto de URLs.
- Persistir registros consultaveis.

---

## 7. Riscos Principais e Mitigacoes

**Risco 1 - Mudancas no comportamento do Facebook**

Mitigacao:
- Isolar parsing e seletores.
- Manter testes de integracao com paginas de referencia.
- Monitorar quedas de extracao por tipo de URL.

**Risco 2 - Bloqueios, captcha e checkpoint**

Mitigacao:
- Aplicar stealth patch de forma padronizada.
- Rotacionar proxies e controlar taxa de acesso.
- Implementar deteccao e tratamento automatizado de captcha.

**Risco 3 - Complexidade operacional da stack assincrona**

Mitigacao:
- Validar Docker, logging e observabilidade desde a Sprint 1.
- Introduzir spiders e workers gradualmente.
- Padronizar contratos entre API, filas e pipelines.

**Risco 4 - Regressao funcional na migracao da stack**

Mitigacao:
- Manter rastreabilidade entre FR/NFR e entregas por sprint.
- Cobrir fluxos criticos com testes automatizados.
- Executar homologacao incremental por fase do processo.

---

## 8. Marco de Evolucao Esperado

Ao final das sprints, o modulo deve estar apto a:

- Receber consultas via API.
- Orquestrar processamento por RabbitMQ.
- Descobrir URLs do Facebook via Google com Scrapy.
- Enriquecer paginas usando Playwright com stealth.
- Persistir e reaproveitar dados com cache.
- Exportar resultados em JSON e XLSX.
- Operar com monitoramento, retry, DLQ e readiness de producao.

Esse planejamento cria uma trilha de implementacao progressiva, com ganhos reais a cada sprint e menor risco de concentrar integracoes complexas apenas no final do projeto.
