# Going Going Gone — Documento de Arquitetura

**Equipe 3BH:** Kauê Escobar, Victor Dias e Vinícius Sarte
25 de novembro de 2026

## Sumário

1. [Síntese da abordagem arquitetural](#1-síntese-da-abordagem-arquitetural)
2. [Atributos de qualidade](#2-atributos-de-qualidade)
3. [Visão geral da arquitetura](#3-visão-geral-da-arquitetura)
4. [Domínio e limites de contexto](#4-domínio-e-limites-de-contexto)
5. [Princípios e padrões de projeto](#5-princípios-e-padrões-de-projeto)
6. [Resiliência, integração e inteligência artificial](#6-resiliência-integração-e-inteligência-artificial)
7. [Decisões arquiteturais](#7-decisões-arquiteturais)

## 1. Síntese da abordagem arquitetural

O Going Going Gone é uma casa de leilões online com lances em tempo real. A arquitetura, documentada com o Modelo C4, parte de uma escolha central: o registro de lances precisa de consistência forte, enquanto a transmissão do vídeo e do placar tolera um pequeno atraso. Por isso, separamos esses dois caminhos, para que cada um seja otimizado e escalado conforme sua necessidade (ADR-001). O domínio declara as interfaces de que precisa, como `PaymentPort` e `IdentityPort`, e a infraestrutura as implementa, o que protege as regras de negócio de mudanças externas.

Quando alguém dá um lance, ele chega por uma API REST (`PlaceBid`) e é gravado imediatamente em um banco PostgreSQL particionado por leilão. Cada leilão tem um único responsável pela escrita, o que garante uma só ordem oficial, e o identificador `bidId` impede que um reenvio gere duplicidade. Depois do aceite, o evento `BidAccepted` é publicado via Outbox e Kafka para a transmissão, o financeiro e a auditoria. O placar chega aos participantes via WebSocket, o vídeo é distribuído por CDN, a cobrança é feita após o arremate e o histórico imutável permite reconstruir a ordem dos lances durante cinco anos.

Nossas metas são confirmar 99% dos lances em menos de 200 ms, dar retorno visual ao usuário em menos de 300 ms e suportar picos de 10.000 lances por segundo com, no máximo, 20% de aumento na latência. A transmissão é dimensionada para até 1 milhão de conexões, e uma partição com falha deve ser recuperada em menos de 2 segundos, sem perda de lances confirmados. A integração de pagamento é protegida por timeout, retry e Circuit Breaker. A tokenização dos meios de pagamento e o arquivamento do vídeo após 90 dias contribuem para a segurança, o controle de custos e a adequação à LGPD. Em troca, aceitamos um pequeno atraso no placar, o limite de uma partição por leilão e uma operação mais complexa.

## 2. Atributos de qualidade

Priorizamos três atributos, cada um descrito como um cenário que pode ser testado (matriz QAW).

| Atributo | Cenário | Meta |
| --- | --- | --- |
| Desempenho (D1) | Durante o fechamento de um leilão com 200 lances por segundo, um arrematante envia um lance e o serviço o valida, ordena e confirma. | Latência p99 abaixo de 200 ms |
| Disponibilidade (D4) | Durante um leilão ativo, um nó da infraestrutura falha e outro assume a escrita a partir do último lance confirmado. | Recuperação em menos de 2 s, sem perder nem reprocessar lances |
| Escalabilidade (D3) | Em um pico com 50 leilões, a carga sobe de 5,8 para 10.000 lances por segundo e o sistema escala automaticamente. | Aumento de latência de no máximo 20% |

## 3. Visão geral da arquitetura

### 3.1. Contexto (C4 nível 1)

Duas pessoas interagem com a plataforma: o **arrematante**, que participa dos leilões e dá lances, e o **leiloeiro**, que os conduz. A plataforma, cujo núcleo são os lances em tempo real, integra-se a quatro sistemas externos: o gateway de pagamentos, o serviço de transmissão ao vivo, os sistemas judiciais (CNJ e editais) e o serviço de KYC e antifraude.

### 3.2. Contêineres (C4 nível 2)

Os usuários acessam o **Portal Web/API**, responsável pela autenticação e pelos comandos. Os lances seguem para o **Núcleo de Leilões**, que os ordena e grava no **banco de lances**. A partir do aceite, o **Pub/Sub (Kafka)** distribui os eventos para o módulo de **transmissão (Broadcast)**, que cuida do vídeo e do placar, e para o **Financeiro**, que trata faturas, custódia e repasses. Completam a visão os serviços externos de CDN/streaming, pagamento e identidade. A tabela detalha cada comunicação.

| Fluxo | Origem → Destino | Protocolo | Natureza |
| --- | --- | --- | --- |
| F1 | Usuários → Portal Web/API | HTTPS (REST) | Síncrono |
| F2 | Portal → Núcleo de Leilões | HTTPS (REST, OpenAPI) | Síncrono |
| F3 | Núcleo → Banco de lances | SQL | Síncrono |
| F4 | Núcleo → Pub/Sub | Evento `BidAccepted` via outbox | Assíncrono |
| F5 | Pub/Sub → Broadcast | Consumo de eventos | Assíncrono |
| F6 | Pub/Sub → Financeiro | Consumo de eventos | Assíncrono |
| F7 | Portal → Broadcast | HTTPS (REST) | Síncrono |
| F8 | Portal → Financeiro | HTTPS (REST) | Síncrono |
| F9 | Broadcast → CDN/Streaming | Streaming de vídeo (ex.: HLS) | Assíncrono |
| F10 | Financeiro → Gateway de Pagamento | HTTPS (REST) | Síncrono |
| F11 | Financeiro → KYC/Identidade | HTTPS (REST) | Síncrono |
| F12 | Broadcast → Usuários | WebSocket | Assíncrono |

### 3.3. Componentes do Núcleo de Leilões (C4 nível 3, proposta)

O **controlador PlaceBid** recebe a requisição e a entrega ao **motor de lances**, que aplica as **estratégias de validação** de cada tipo de leilão. Para gravar e consultar dados, o motor usa três interfaces: `BidRepository`, implementada por um adaptador PostgreSQL, e `PaymentPort` e `IdentityPort`, que conferem a caução e a habilitação já registradas, sem chamar o gateway durante o lance. Por fim, o **publicador do outbox** lê os eventos pendentes e envia o `BidAccepted` ao Kafka.

## 4. Domínio e limites de contexto

Seguindo o Domain-Driven Design (DDD), o coração do negócio (subdomínio principal) é o **motor de lances em tempo real**. Como apoio, temos o catálogo e a gestão de leilões, a transmissão e o placar, o antifraude e as notificações. Autenticação, pagamento, KYC e CDN são subdomínios genéricos, atendidos por soluções já consolidadas.

A comunicação com pagamento, KYC, transmissão e sistemas judiciais passa por uma camada anticorrupção. Seus adaptadores traduzem os contratos externos e preservam a linguagem do nosso domínio ([ADR-002](#adr-002--desacoplamento-com-dip-e-padrões-gof)).

## 5. Princípios e padrões de projeto

Dois princípios SOLID orientam o projeto. Pela **inversão de dependência (DIP)**, o domínio define as interfaces e a infraestrutura fornece as implementações. Pelo princípio **aberto/fechado (OCP)**, novas regras de leilão são adicionadas como novas estratégias, sem alterar o motor de lances.

Esses princípios se apoiam em três padrões GoF, cuja localização no código ainda é uma proposta:

| Padrão | Categoria | Onde será usado | O que resolve |
| --- | --- | --- | --- |
| Factory Method | Criacional | Integrações | Cria o adaptador do fornecedor escolhido |
| Adapter | Estrutural | Camada anticorrupção | Traduz dados externos para o domínio |
| Strategy | Comportamental | Motor de lances | Alterna as regras de cada tipo de leilão |

## 6. Resiliência, integração e inteligência artificial

As chamadas ao gateway de pagamento têm timeout, retry e Circuit Breaker. Se o serviço ficar indisponível, o arremate é mantido e a cobrança fica pendente para ser processada depois ([ADR-004](#adr-004--timeout-retry-e-circuit-breaker-no-gateway-de-pagamento)). Para absorver picos, o Kafka distribui os eventos aos consumidores, enquanto a gravação do lance permanece síncrona. Propomos também uma fila de mensagens com falha (Dead Letter Queue).

A inteligência artificial atua no combate a fraudes. Um modelo de detecção de anomalias analisa os lances aceitos e atualiza a pontuação de risco, sem atrasar o caminho principal. Dados pessoais são pseudonimizados, as saídas são validadas por JSON Schema, as decisões ficam registradas e os casos de risco alto passam por revisão humana ([ADR-006](#adr-006--antifraude-probabilístico-com-guardrails-de-ia)).

## 7. Decisões arquiteturais

Registramos seis decisões (ADRs), todas ainda com status de proposta.

| ADR | Decisão | Status | Data |
| --- | --- | --- | --- |
| [ADR-001](#adr-001--estilo-arquitetural-para-sincronização-de-lances-em-tempo-real) | Estilo arquitetural para sincronização de lances em tempo real | Proposto | 19/08/2026 |
| [ADR-002](#adr-002--desacoplamento-com-dip-e-padrões-gof) | Desacoplamento com DIP e padrões GoF | Proposto | 25/11/2026 |
| [ADR-003](#adr-003--comunicação-síncrona-no-lance-e-assíncrona-na-propagação) | Comunicação síncrona no lance e assíncrona na propagação | Proposto | 21/10/2026 |
| [ADR-004](#adr-004--timeout-retry-e-circuit-breaker-no-gateway-de-pagamento) | Timeout, retry e Circuit Breaker no gateway de pagamento | Proposto | 21/10/2026 |
| [ADR-005](#adr-005--banco-de-lances-particionado-por-leilão) | Banco de lances particionado por leilão | Proposto | 25/11/2026 |
| [ADR-006](#adr-006--antifraude-probabilístico-com-guardrails-de-ia) | Antifraude probabilístico com guardrails de IA | Proposto | 25/11/2026 |

### ADR-001 — Estilo arquitetural para sincronização de lances em tempo real

*Proposto em 19/08/2026 · Drivers: D1, D2 e D4*

> **Revisão:** o [ADR-005](#adr-005--banco-de-lances-particionado-por-leilão) mantém a partição por leilão, mas substitui a fila de ingestão antes do banco por gravação síncrona.

#### Contexto

O sistema precisa confirmar cada lance em menos de 200 ms (D1) e manter uma ordem consistente entre eles (D2), mesmo com 10.000 requisições de escrita por segundo no pico. Ao mesmo tempo, deve sustentar até 1.000.000 de conexões WebSocket acompanhando os leilões. Pelo teorema CAP, em uma partição de rede não é possível ter ao mesmo tempo disponibilidade total e consistência forte. Por isso, precisamos deixar claro onde cada uma será priorizada.

#### Decisão

Decidimos separar o caminho dos lances do caminho de transmissão. Os lances seguem uma arquitetura orientada a eventos, particionada por `leilao_id` (por exemplo, com Kafka), o que garante consistência forte dentro de cada leilão e permite que leilões diferentes escalem de forma independente. Já o vídeo e o placar usam publicação e assinatura (pub/sub) com consistência eventual, sem depender do banco de lances, e o vídeo é distribuído por CDN.

#### Alternativas consideradas

Avaliamos um banco relacional único com bloqueio otimista, mas ele não escalaria para 50 leilões disputados ao mesmo tempo sem contenção. Também descartamos a consistência eventual para os lances, porque ela viola o D2 e cria risco jurídico diante do Decreto 21.981/1932. Por fim, um único fluxo para lances e transmissão foi rejeitado, já que otimizar um lado prejudicaria o outro.

#### Consequências

Ganhamos escala horizontal por leilão e ordem garantida onde a lei exige. Em troca, o placar pode atrasar alguns milissegundos, cada leilão fica limitado à capacidade de uma partição e o componente que ordena os lances precisa se recuperar rapidamente em caso de falha (D4).

---

### ADR-002 — Desacoplamento com DIP e padrões GoF

*Proposto em 25/11/2026 · Drivers: D1 e D7*

#### Contexto

A plataforma depende de serviços externos de pagamento, transmissão, verificação de identidade (KYC) e sistemas judiciais ou de editais. Cada um tem seu próprio contrato, que pode mudar sem aviso da nossa parte. As regras de lance não devem depender deles.

#### Decisão

Aplicamos o Princípio da Inversão de Dependência: o domínio declara as interfaces de que precisa, como `PaymentPort`, `IdentityPort` e `BidRepository`, e a infraestrutura as implementa. Cada serviço externo terá um adaptador (padrão Adapter), que funciona como camada anticorrupção e traduz os dados para o modelo do domínio. As regras que variam conforme o tipo de leilão, como incremento mínimo, fechamento e proteção contra lances de última hora, serão tratadas com o padrão Strategy, sem alterar o motor de lances. A criação do adaptador de cada fornecedor ficará a cargo de um Factory Method.

#### Alternativas consideradas

Chamar as bibliotecas externas diretamente no domínio faria qualquer troca de fornecedor afetar as regras de negócio. Usar condicionais para cada tipo de leilão exigiria mexer no código crítico a cada nova modalidade. Um barramento central de integração acrescentaria infraestrutura e um ponto único de falha.

#### Consequências

O domínio pode ser testado sem os serviços externos, e a troca de um fornecedor fica restrita ao adaptador. O custo é um número maior de interfaces e classes, que precisam ser mantidas enxutas no caminho crítico.

Para garantir a decisão, um teste de arquitetura na integração contínua (CI) impedirá que o domínio importe a infraestrutura, e cada adaptador terá testes de contrato.

---

### ADR-003 — Comunicação síncrona no lance e assíncrona na propagação

*Proposto em 21/10/2026 · Escopo: comunicação entre contêineres (C4 nível 2)*

#### Contexto

Quem dá um lance precisa de uma resposta imediata e de uma ordem confiável, inclusive no pico de 10.000 requisições por segundo. A transmissão pode chegar a 1.000.000 de conexões e tolera um pequeno atraso. Se o registro do lance chamasse a transmissão e o financeiro de forma síncrona, qualquer lentidão nesses módulos atrasaria o lance.

#### Decisão

O caminho entre o portal, o Núcleo de Leilões e o banco de lances é síncrono, via HTTPS/REST, com contrato OpenAPI. Depois que o lance é aceito, o evento `BidAccepted` segue de forma assíncrona, por pub/sub, para a transmissão, o financeiro e a auditoria. Para não perder eventos, usamos o padrão Outbox: o evento é gravado na mesma transação do lance e publicado em seguida. Os identificadores `bidId` e `eventId` evitam duplicidades, e o `sequenceNumber` define a ordem oficial.

#### Alternativas consideradas

Chamar todos os consumidores de forma síncrona deixaria a disponibilidade do lance refém do módulo mais lento. Permitir que a transmissão lesse diretamente o banco de lances contrariaria o ADR-001. Também não adotamos gRPC em todos os fluxos, pois os clientes web e mobile não exigem esse ganho de desempenho.

#### Consequências

O lance fica protegido de falhas na transmissão, a distribuição para os espectadores escala de forma independente, o placar pode ser reprocessado e a ordem pode ser auditada. Em contrapartida, o placar passa a ter consistência eventual, os consumidores precisam tratar eventos repetidos e há mais componentes para operar.

Os contratos serão versionados (SemVer) e testados na CI. Todo fluxo novo deve ser registrado no diagrama de contêineres, e mudanças incompatíveis exigem uma nova versão e um novo ADR.

---

### ADR-004 — Timeout, retry e Circuit Breaker no gateway de pagamento

*Proposto em 21/10/2026 · Escopo: integração entre o Financeiro e o gateway de pagamento*

#### Contexto

O gateway de pagamento é externo e representa o elo mais frágil da arquitetura. Sem um limite de tempo, sua lentidão pode esgotar os recursos do módulo financeiro. Já repetir chamadas sem controle pode duplicar cobranças ou sobrecarregar o serviço.

#### Decisão

Cada tentativa terá limite de 1 segundo para conectar e 2 segundos para responder. Faremos no máximo três tentativas, com intervalos de 200 ms e 400 ms e variação aleatória (jitter) de 20%, apenas em casos de timeout ou erros 5xx e 429. Todas levarão a mesma `Idempotency-Key`, igual ao `chargeId`.

O Circuit Breaker observará as últimas 20 chamadas, com mínimo de 10, e será aberto quando pelo menos 50% falharem ou ficarem lentas. Após 30 segundos, fará cinco chamadas de teste e voltará ao normal se ao menos quatro tiverem sucesso. Enquanto estiver aberto, a cobrança será guardada no outbox como `PENDENTE_COBRANCA`: o arremate continua válido e o pagamento é processado depois.

#### Alternativas consideradas

Apenas repetir as chamadas aumentaria a sobrecarga do gateway. Tornar toda cobrança assíncrona eliminaria a confirmação imediata nos casos normais. Cancelar o arremate quando o gateway falhasse puniria o arrematante por um problema de terceiros.

#### Consequências

Uma falha no gateway não afeta os lances, não gera cobrança duplicada nem faz o arremate se perder, e a recuperação é automática. Por outro lado, surge a situação de cobrança pendente, o recebimento pode atrasar e os parâmetros precisam ser calibrados.

Os parâmetros ficarão em configuração versionada, com alerta quando o circuito abrir, painel de pendências e simulação de falha antes de cada entrega. Ainda falta definir por quanto tempo uma cobrança pode permanecer pendente.

---

### ADR-005 — Banco de lances particionado por leilão

*Proposto em 25/11/2026 · Drivers: D1, D2, D4 e D5*

#### Contexto

O banco precisa ordenar e gravar até 10.000 lances por segundo sem divergências. Também deve guardar cerca de 912 milhões de registros ao longo de cinco anos, o que representa de 1 a 1,5 TB considerando réplicas e índices.

#### Decisão

Adotamos o PostgreSQL dividido em partições (shards) por `leilao_id`, com um único responsável pela escrita em cada leilão. A API encaminha cada lance à instância correta, e uma única transação valida o lance, atribui o `sequenceNumber` e grava o registro junto com o outbox. As combinações de `leilao_id` com `sequenceNumber` e de `leilao_id` com `bidId` são únicas.

Os registros nunca são alterados, apenas acrescentados, e cada um guarda o hash do anterior. A confirmação só é enviada depois da réplica síncrona. Em seguida, o Kafka, usando `leilao_id` como chave, publica o `BidAccepted`.

Esta decisão revisa parte do [ADR-001](#adr-001--estilo-arquitetural-para-sincronização-de-lances-em-tempo-real): a partição por leilão permanece, mas o lance é gravado de forma síncrona, conforme o [ADR-003](#adr-003--comunicação-síncrona-no-lance-e-assíncrona-na-propagação), sem uma fila antes do banco.

#### Alternativas consideradas

Um banco relacional único concentraria a disputa por recursos. Um banco NoSQL com consistência eventual violaria o D2. Usar um banco em memória como fonte oficial aumentaria o risco de perder lances confirmados, e depender apenas do Kafka dificultaria consultas e validações transacionais.

#### Consequências

Obtemos uma ordem única, transações ACID e uma trilha auditável. O preço é uma latência um pouco maior por causa da réplica síncrona e uma operação mais complexa com vários shards. A recuperação em menos de 2 segundos (D4) é uma meta agressiva que ainda precisa ser validada.

Antes de aprovar este ADR, faremos testes de carga com 10.000 lances por segundo e testes de failover.

---

### ADR-006 — Antifraude probabilístico com guardrails de IA

*Proposto em 25/11/2026 · Drivers: D1, D5 e D6*

#### Contexto

Lances combinados para inflar preços (shill bidding) e contas falsas devem ser detectados, de preferência antes da confirmação e sem atraso perceptível. Como um modelo probabilístico pode errar, ele não deve decidir sozinho o destino de um lance.

#### Decisão

No momento do lance, aplicamos somente regras determinísticas, como habilitação, limite de requisições e vínculo com o vendedor, além de consultar uma pontuação de risco já calculada. Um modelo de detecção de anomalias analisa os eventos `BidAccepted` de forma assíncrona e atualiza essa pontuação. Casos de risco alto passam por revisão humana, e o modelo não cancela lances nem bane contas por conta própria.

Os dados pessoais são pseudonimizados, as saídas são validadas por JSON Schema, e registramos a versão do modelo, a pontuação e a decisão tomada. Se o modelo falhar, as regras continuam valendo.

#### Alternativas consideradas

Executar o modelo em cada lance colocaria em risco a meta de latência (D1). Deixar um modelo de linguagem (LLM) decidir bloqueios traria resultados pouco previsíveis e difíceis de auditar. Usar apenas regras fixas não identificaria bem fraudes coordenadas.

#### Consequências

A solução preserva a velocidade, mantém as decisões auditáveis e apoia o direito de revisão previsto no artigo 20 da LGPD. No entanto, a pontuação pode estar desatualizada e parte das fraudes só será percebida depois da confirmação, de modo que o D6 é atendido parcialmente.

Acompanharemos os falsos positivos e negativos, revisaremos o modelo periodicamente e aprovaremos cada nova versão antes de implantá-la.
