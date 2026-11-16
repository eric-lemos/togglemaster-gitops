# ToggleMaster GitOps

Manifests Kubernetes gerenciados pelo Argo CD. O Terraform cria a infraestrutura
AWS; o workflow GitOps instala os charts usando Helm CLI. Esta aplicação é
registrada no Argo CD pelo workflow **K8s Ops**:

```bash
kubectl apply -f k8s/core/argocd.yml
```

Depois disso, o Argo CD acompanha a branch `main` deste repositório, aplica `k8s/kustomization.yml` e mantém o namespace `togglemaster` sincronizado.

## Fluxo de imagens

Cada pipeline de aplicação publica a imagem no ECR e atualiza o campo `image` do manifesto correspondente. O commit no GitOps dispara a sincronização automática do Argo CD.

O repositório de apps precisa destas GitHub Variables:

```text
GITOPS_REPOSITORY=eric-lemos/togglemaster-gitops
GITOPS_BRANCH=main
GITOPS_AUTH_MANIFEST_PATH=k8s/apps/auth.yml
GITOPS_EVALUATION_MANIFEST_PATH=k8s/apps/evaluation.yml
GITOPS_ANALYTICS_MANIFEST_PATH=k8s/apps/analytics.yml
GITOPS_FLAG_MANIFEST_PATH=k8s/apps/flag.yml
GITOPS_TARGETING_MANIFEST_PATH=k8s/apps/targeting.yml
```

Configure também o secret `GITOPS_TOKEN` com permissão de escrita no repositório GitOps. As pipelines usam esse token somente no push para `main`.

## Requisitos AWS

Os nodes do EKS precisam conseguir fazer pull dos repositórios privados ECR. A role dos nodes deve permitir `ecr:GetAuthorizationToken` e as ações de leitura de camadas e imagens do ECR.

## Workflow manual

O workflow `.github/workflows/operations.yml` pode ser executado em **Actions > K8s Ops > Run workflow**:

- `Apply`: executa Image Build e Apply Helm Charts em paralelo, aplica os manifests Kubernetes após ambos e configura os Secrets de self-healing.
- `Postgres Schema`: remove e recria `postgres-schema-init`, aguarda sua conclusão e exibe os logs.
- `Validate Togglemaster`: aguarda os rollouts e executa o validador das aplicações.

Primeiro execute **Terraform Ops → Apply**. Depois, no GitOps, selecione
**Apply** para executar o fluxo completo na branch `main`. Os jobs de schema e
validação são opcionais e só executam quando selecionados. O schema é
independente e pode rodar em paralelo ao Apply; garanta que seus pré-requisitos
já estejam disponíveis. A validação aguarda Configure Secrets quando Apply
também é selecionado, mas não aguarda o schema. Para validar após o schema,
execute a validação em uma execução posterior. Uma falha impede os passos
dependentes.

Os valores e dashboards estão em `helm/`; os charts fixados são monitoring
92.17.0, Loki 7.5.0, OpenTelemetry Collector 0.178.2 (agent e gateway),
Ingress NGINX 4.15.1, Metrics Server 3.14.0 e Argo CD 10.6.0. O Helm CLI
gerencia os releases diretamente, sem state Terraform. O destroy do Terraform
desinstala os sete releases conhecidos antes de destruir o EKS. O state Helm
antigo não é removido automaticamente e não deve mais ser aplicado.

Configure no GitOps os secrets `DISCORD_WEBHOOK_URL` (URL original, sem
`/slack`), `PAGERDUTY_INTEGRATION_KEY` (Events API v2) e `DATADOG_API_KEY`,
além das credenciais
AWS e da variável `EKS_CLUSTER_NAME`. O job cria os Secrets Kubernetes de
notificação e, se ainda não existir, `monitoring-ui-basic-auth`, com senha
aleatória e hash bcrypt. A senha permanece no Secret Kubernetes, não no state
Terraform; não a compartilhe. O acesso HTTP não é seguro para produção.
Para consultar a senha do basic auth localmente:

```bash
kubectl -n observability get secret monitoring-ui-basic-auth -o jsonpath='{.data.password}' | base64 --decode; echo
```

## Acesso à interface do Argo CD

O usuário inicial padrão do Argo CD é `admin`. Para consultar a senha inicial no PowerShell, configure primeiro o `kubectl` para o cluster EKS e execute:

```powershell
$encodedPassword = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}'
[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($encodedPassword))
```

No Linux, use:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```

O comando decodifica o valor do Secret localmente; não publique nem compartilhe a senha. O Secret `argocd-initial-admin-secret` pode deixar de existir depois que a senha inicial for alterada. Se ele não for encontrado, use o procedimento de redefinição de senha documentado pelo Argo CD.

Para consultar o endereço externo provisionado para o Argo CD:

```powershell
kubectl get service argocd-server -n argocd
```

Use o valor de `EXTERNAL-IP` (que no EKS normalmente é um hostname AWS) no navegador. A configuração atual usa `LoadBalancer` e HTTP, sem TLS.

## Requisições HTTP aos endpoints

### Execução do validador

```bash
pip install -r scripts/validate_apps/requirements.txt
python3 scripts/validate_apps/validate_apps.py                       # discovers ingress via kubectl
python3 scripts/validate_apps/validate_apps.py --base-url http://<ENDERECO-DO-INGRESS>
```

Sem `--base-url` (ou a variável `TOGGLEMASTER_BASE_URL`), o script lê
`status.loadBalancer` do Ingress `togglemaster/togglemaster-ingress` e aguarda
até 300 segundos (`--ingress-timeout`) o endereço do ELB existir e resolver no
DNS. A chave mestra vem da variável `MASTER_KEY`; sem ela, usa `togglemaster`.

No workflow **K8s Ops**, marque `Validate Togglemaster` para executar o mesmo script no
GitHub Actions. Se `Apply` também estiver marcado, o job aguarda Configure
Secrets e é pulado se o Apply falhar. Ele não depende do job de schema. Aguarda o rollout de todos
os Deployments do namespace `togglemaster` e usa o secret `MASTER_KEY` do
repositório. A API key criada pelo teste é mascarada no log do Actions.

### Atualização da chave durante o teste local

O script `scripts/validate_apps/validate_apps.py` executa `update_key_on_k8s` depois de
`update_rule`, antes das avaliações. Ele usa a API key criada pelo teste para
criar ou atualizar `togglemaster-runtime/SERVICE_API_KEY` no namespace
`togglemaster`, reinicia o Deployment `evaluation-service` e aguarda o rollout
por até 120 segundos. Se alguma etapa falhar, o teste é interrompido.

Esse teste altera o cluster do contexto atual do `kubectl` e a chave usada por
todas as réplicas do evaluation-service. Confira `kubectl config current-context`
antes de executar. É necessário ter permissão para criar/atualizar Secrets,
reiniciar o Deployment e acompanhar o rollout. A chave não é salva em arquivo
local nem passada nos argumentos do comando. Não execute em produção.

Ao terminar os testes, `show_cache_hits` consulta os logs dos últimos 10 minutos
de todos os pods em `Running` com o label `app=evaluation-service` usando `kubectl logs` e
exibe apenas os `Cache HIT` da flag criada pelo teste. A leitura exige permissão
para listar pods e acessar seus logs. Se nenhum registro for encontrado, exibe
um aviso; falhas do comando interrompem o teste.

Depois das avaliações, `check_dynamodb()` usa a AWS CLI para listar os eventos
da flag criada pelo teste na tabela `ToggleMasterAnalytics` em `us-east-1`.
Exibe JSON sem os marcadores de tipo do DynamoDB, por timestamp decrescente.
Requer AWS CLI instalada, credenciais locais e permissão `dynamodb:Scan`.
O scan percorre a tabela inteira (com paginação automática), mesmo com filtro;
use apenas em tabelas pequenas de teste. A consulta tem timeout de 60 segundos.
Como o processamento SQS é assíncrono, uma consulta vazia gera aviso; para
consultar novamente, use `validator.check_dynamodb()` na mesma instância.
Tabela e região podem ser alteradas pelos argumentos `table_name` e `region`.
O validador não consulta a SQS diretamente: registros da flag criada nesta
execução no DynamoDB evidenciam o fluxo evaluation-service -> SQS ->
analytics-service -> DynamoDB. Isso não comprova a exclusão das mensagens da fila.

O Ingress expõe cada serviço usando seu nome como prefixo e remove esse prefixo antes
de encaminhar a requisição ao serviço:

| Prefixo público | Serviço | Exemplos de endpoints |
| --- | --- | --- |
| `/auth-service` | `auth-service` | `GET /auth-service/health`, `GET /auth-service/validate`, `POST /auth-service/admin/keys` |
| `/flag-service` | `flag-service` | `GET /flag-service/health`, `GET /flag-service/flags`, `POST /flag-service/flags`, `PUT /flag-service/flags/{name}` |
| `/targeting-service` | `targeting-service` | `GET /targeting-service/health`, `POST /targeting-service/rules`, `GET /targeting-service/rules/{flag_name}` |
| `/evaluation-service` | `evaluation-service` | `GET /evaluation-service/health`, `GET /evaluation-service/evaluate?user_id=...&flag_name=...` |
| `/analytics-service` | `analytics-service` | `GET /analytics-service/health` |

O `analytics-service` é um worker de eventos; seu endpoint HTTP é apenas o health
check. As operações de criação e alteração de flags e regras exigem uma API key
no cabeçalho de autorização. O endpoint administrativo
`POST /auth-service/admin/keys` exige a chave mestra configurada no serviço.

Para fazer requisições com `requests`, instale a dependência:

```bash
python -m pip install -r scripts/validate_apps/requirements.txt
```

Exemplo de uso:

```python
import requests

base_url = "http://<ENDERECO-EXTERNO-DO-INGRESS>"
response = requests.get(f"{base_url}/auth-service/health", timeout=10)
print(response.status_code)
print(response.json())
```

Consulte as rotas acima para usar o prefixo público correspondente ao serviço;
por exemplo, o health do Auth é `/auth-service/health`.

Configure no repositório GitOps:

### Variables

```text
AWS_REGION=us-east-1
EKS_CLUSTER_NAME=togglemaster-eks-cluster
AWS_SQS_URL=https://sqs.us-east-1.amazonaws.com/ACCOUNT_ID/togglemaster-analytics-events
AWS_DYNAMODB_TABLE=ToggleMasterAnalytics
```

### Secrets

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_SESSION_TOKEN
AUTH_DATABASE_URL
FLAG_DATABASE_URL
TARGETING_DATABASE_URL
MASTER_KEY
REDIS_URL
DATADOG_API_KEY
```

As credenciais AWS devem estar ativas no momento da execução. Os secrets das
aplicações são transformados em `togglemaster-secrets`; a chave Datadog é
armazenada separadamente em `observability/datadog`. Não é necessário
codificá-los manualmente em Base64.

Exemplos de valores:

```text
AUTH_DATABASE_URL=postgresql://auth_admin:REPLACE_WITH_PASSWORD@togglemaster-auth.example.amazonaws.com:5432/postgres?sslmode=require
FLAG_DATABASE_URL=postgresql://flags_admin:REPLACE_WITH_PASSWORD@togglemaster-flags.example.amazonaws.com:5432/postgres?sslmode=require
TARGETING_DATABASE_URL=postgresql://targeting_admin:REPLACE_WITH_PASSWORD@togglemaster-targeting.example.amazonaws.com:5432/postgres?sslmode=require
MASTER_KEY=REPLACE_WITH_A_LONG_RANDOM_KEY
REDIS_URL=redis://:REPLACE_WITH_REDIS_TOKEN@togglemaster-redis.example.cache.amazonaws.com:6379/0
```

Se o Redis exigir TLS, use `rediss://` no lugar de `redis://`. Caracteres especiais em usuários ou senhas devem ser URL-encoded, por exemplo `@` como `%40` e `#` como `%23`. Nunca versione esses valores no repositório.

## Telemetria OpenTelemetry

Os cinco serviços enviam métricas por OTLP/gRPC para `otel-gateway.observability`
(`OTEL_EXPORTER_OTLP_ENDPOINT`), que as expõe ao Prometheus. O nome do serviço
vem de `OTEL_SERVICE_NAME` e alimenta o painel de requisições HTTP do Grafana.

- Os traces são enviados ao Datadog no site `us5.datadoghq.com`. O gateway
  também envia métricas derivadas de spans para as funcionalidades de APM.
  Isso pode gerar custos no Datadog, conforme o plano e o volume recebido.
- Antes do envio, o gateway remove atributos, eventos e mensagens de erro dos
  spans e substitui seus nomes por categorias genéricas. Apenas os nomes
  conhecidos dos serviços são preservados; outros são identificados como
  `unknown`. A configuração prioriza privacidade em vez de detalhes de rota.
- Logs continuam desligados nos SDKs (`OTEL_LOGS_EXPORTER=none`) e chegam ao
  Loki pelo agent; não são enviados ao Datadog.
- O workflow **K8s Ops → Apply** cria `observability/datadog` a partir do
  secret `DATADOG_API_KEY` e atualiza o gateway antes de aplicar os manifests.
  Execute o Apply novamente após trocar a chave. Não versione nem compartilhe
  o valor da chave.
- Após publicar as alterações e executar o Apply, execute **Validate
  Togglemaster** para gerar tráfego e verifique os traces e o Service Map no
  Datadog US5. A sincronização automática do Argo CD pode habilitar os SDKs
  antes do Apply; a exportação depende do gateway atualizado.
- O evaluation-service grava o contexto de trace nos atributos das mensagens SQS
  e o analytics-service o lê, para o fluxo aparecer como um único trace.
- Os serviços Python usam `opentelemetry-instrument` via `gunicorn.conf.py`; os
  serviços Go usam `otel.go` com `otelhttp`.

## Self-healing do auth-service

O CronJob anterior foi removido dos manifests; o Argo CD remove seus recursos
gerenciados após a publicação e sincronização. A nova arquitetura é
Alertmanager → webhook interno HTTPS → GitHub App → GitHub Actions → EKS.
O manifesto configura uma réplica do receptor. `SELF_HEALING_ENABLED` autoriza
reinícios e controla a rota do Alertmanager no próximo Apply.

O receptor aceita apenas notificações autenticadas do alerta
`ToggleMasterAuthHighErrorRate` com `service=auth-service`. O repositório, a
branch e a Action de destino são fixos. A Action consulta novamente o
Prometheus; sem alerta firing, ou em caso de erro, não reinicia o serviço.
Cada restart atualiza atomicamente a annotation de histórico e a annotation de
rollout com `resourceVersion`: no mínimo 2 minutos entre restarts e no
máximo três em uma janela móvel de 10 minutos. Conflitos interrompem a execução.
O Argo CD preserva as duas annotations, mas excluir e recriar o Deployment
apaga o histórico. Um rollout bem-sucedido não garante que o alerta foi
resolvido nem corrige necessariamente a causa raiz.

O intervalo do Alertmanager é contado a partir da notificação, não do restart.
Se uma repetição chegar antes de decorridos 2 minutos do restart, ela será
ignorada e uma nova tentativa dependerá da próxima notificação.

### Preparação e ativação

Execute as etapas apenas quando a ativação for autorizada. Elas dependem de
credenciais AWS válidas, do cluster existente e dos secrets e variáveis AWS já
usados pelo GitOps. O usuário AWS da Action precisa de acesso ao proxy do
serviço Prometheus e de `get`, `patch` e `watch` no Deployment
`togglemaster/auth-service`. O GitHub App deve estar instalado neste repositório
com permissão **Actions: Read and write**. A operação de configuração também
precisa de permissões para ler e criar/atualizar os Secrets nas namespaces
`togglemaster` e `observability`, e para reiniciar e acompanhar o Deployment
`togglemaster/self-healing`.

1. Configure `SELF_HEALING_ENABLED=false` no GitHub. Crie a infraestrutura
   com **Terraform Ops → Apply**. A rota permanece desativada na primeira
   instalação.
2. Nas configurações de **Actions** do repositório GitOps, crie as variáveis
   `SELF_HEALING_APP_ID` (App ID em **General**) e
   `SELF_HEALING_INSTALLATION_ID` (ID na URL da instalação do App). Crie o
   secret `SELF_HEALING_APP_PRIVATE_KEY` com o conteúdo integral do arquivo
   `.pem`, incluindo as linhas BEGIN e END. Não adicione a chave ao Git nem a
   compartilhe em mensagens.
3. Publique o GitOps e execute **K8s Ops → Apply**. O job **Image Build** publica
   a imagem de `scripts/self_healing` e atualiza `k8s/core/self_healing.yml`
   com o hash do diretório como tag. O commit é enviado para `main`; o
   `GITHUB_TOKEN` precisa de permissão de escrita em Contents e a proteção de
   `main` deve permitir o push. Se o código mudar durante o build ou o push
   falhar, execute novamente na revisão atual de `main`.
   A operação aplica os manifests e cria os novos Secrets `self-healing` nas
   duas namespaces e `self-healing-tls` em `togglemaster`, incluindo um novo
   certificado TLS de 90 dias para `self-healing.togglemaster.svc`. Ela
   reinicia apenas o receptor se necessário, não o `auth-service`. Os Secrets
   antigos não são removidos automaticamente. Os valores gerados permanecem
   no Kubernetes, não no state do Terraform. Repita a configuração após
   atualizar a chave do GitHub App ou recriar o cluster.
4. Confirme que o Deployment `self-healing` está pronto. Somente quando
   autorizar reinícios, configure `SELF_HEALING_ENABLED=true` no GitHub e
   execute **K8s Ops → Apply** novamente. O job Helm exige o Secret `observability/self-healing`
   antes de habilitar a rota. A rota específica
   repete notificações a cada 2 minutos; o Discord e o PagerDuty também
   recebem repetições a cada 2 minutos enquanto o alerta permanecer ativo.
5. Valide o fluxo com uma falha controlada autorizada, conferindo o alerta no
   Prometheus, os logs do receptor, a execução da Action e o rollout. Um
   alerta sintético enviado apenas ao Alertmanager não autoriza restart.

Para suspender novas ações, altere `SELF_HEALING_ENABLED` para `false` no
GitHub; execuções já iniciadas não são canceladas. Execute **Apply** para
desativar a rota; até então o Alertmanager ainda pode enviar notificações.
Volte o receptor para zero réplicas nos manifests. A Action pode ser
acionada manualmente, mas sempre aplica as mesmas verificações.
O webhook dispara o workflow **Self Healing**, que executa
`python3 scripts/self_healing/self_healing.py --restart` no runner; sem essa flag o
mesmo arquivo inicia o servidor HTTPS do receptor.

O workflow mostra a data de expiração do certificado sem expor credenciais.
Ele não faz rotação automática: um certificado inválido ou com menos de sete
dias de validade e divergências entre os Secrets interrompem a configuração.
Para o laboratório, um cluster recriado recebe novas credenciais no próximo apply.

### Simulação de erro pelo GitHub Actions

Em um laboratório autorizado, configure e valide o self-healing, defina
`SELF_HEALING_ENABLED=true` e execute **Actions → Simulate Error → Run workflow**
na branch `main`. O workflow usa as credenciais AWS e
`MASTER_KEY` do repositório, configura o acesso ao EKS e executa
`scripts/simulate_error/simulate_error.py`.

O script gera 80 respostas HTTP 500 reais e controladas no `auth-service`,
acompanha o alerta no Prometheus, aguarda um restart e seu rollout e monitora
o alerta até ficar inativo. A regra usa uma janela de 2 minutos e exige
mais de 5% de erros e pelo menos 10 requisições estimadas por 30 segundos.
Após o rollout, o script espera até 10 minutos pela
resolução e falha caso o alerta permaneça ativo. A execução pode gerar
notificações reais no Discord e no PagerDuty; confirme a entrega e a resolução
das notificações separadamente. Não execute em produção. É necessário acesso
aos Deployments, ao port-forward do serviço e ao proxy do Prometheus.

### Simulação local de erro

Somente em um ambiente de teste autorizado, com o self-healing configurado e
`SELF_HEALING_ENABLED=true` no GitHub, execute o script abaixo. Ele gera 80
respostas HTTP 500 controladas no auth-service e pode provocar um restart real.
Não execute em produção.

Configure o `kubectl` para o cluster correto e garanta acesso aos Deployments,
ao port-forward do auth-service e ao proxy do Prometheus. No Bash:

```bash
read -rs -p "Master key: " MASTER_KEY; echo
export MASTER_KEY SELF_HEALING_ENABLED=true
python3 scripts/simulate_error/simulate_error.py
unset MASTER_KEY SELF_HEALING_ENABLED
```

A variável local `SELF_HEALING_ENABLED` é uma confirmação exigida pelo script;
ela não altera nem verifica a variável do GitHub. O script acompanha o alerta
até ficar inativo e o rollout, mas a entrega e a resolução das notificações no
Discord e no PagerDuty devem ser conferidas separadamente.
