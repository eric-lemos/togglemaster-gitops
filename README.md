# ToggleMaster GitOps

Manifests Kubernetes gerenciados pelo Argo CD. O Terraform instala o Ingress NGINX, o Metrics Server e o Argo CD no cluster. O workflow Terraform gerencia esses charts Helm em um state separado, depois de criar o EKS. Esta aplicação é registrada no Argo CD pelo workflow GitOps:

```bash
kubectl apply -f argocd/application.yaml
```

Depois disso, o Argo CD acompanha a branch `main` deste repositório, aplica a raiz `kustomization.yaml` e mantém o namespace `togglemaster` sincronizado.

## Fluxo de imagens

Cada pipeline de aplicação publica a imagem no ECR e atualiza o campo `image` do manifesto correspondente. O commit no GitOps dispara a sincronização automática do Argo CD.

O repositório de apps precisa destas GitHub Variables:

```text
GITOPS_REPOSITORY=eric-lemos/togglemaster-f3-gitops
GITOPS_BRANCH=main
GITOPS_AUTH_MANIFEST_PATH=apps/auth-service.yaml
GITOPS_EVALUATION_MANIFEST_PATH=apps/evaluation-service.yaml
GITOPS_ANALYTICS_MANIFEST_PATH=apps/analytics-service.yaml
GITOPS_FLAG_MANIFEST_PATH=apps/flag-service.yaml
GITOPS_TARGETING_MANIFEST_PATH=apps/targeting-service.yaml
```

Configure também o secret `GITOPS_TOKEN` com permissão de escrita no repositório GitOps. As pipelines usam esse token somente no push para `main`.

## Requisitos AWS

Os nodes do EKS precisam conseguir fazer pull dos repositórios privados ECR. A role dos nodes deve permitir `ecr:GetAuthorizationToken` e as ações de leitura de camadas e imagens do ECR.

## Workflow manual

O workflow `.github/workflows/gitops.yml` pode ser executado em **Actions > GitOps > Run workflow** com dois inputs independentes:

- `Kubernetes Apply`: prepara os secrets de runtime e aplica os manifests Kubernetes e a Application do Argo CD.
- `Run Postgres Schema Job`: remove e recria `postgres-schema-init`, aguarda sua conclusão e exibe os logs.

O workflow GitOps não instala charts nem executa Helm. Para criar ou atualizar os charts, execute o workflow Terraform em **Actions > Terraform CI > Run workflow** com `apply=true`. Ele aplica primeiro a infraestrutura e depois os charts em um state separado (`helm/terraform.tfstate`): Ingress NGINX com Service `LoadBalancer`, Metrics Server com `--kubelet-insecure-tls` e Argo CD com Service `LoadBalancer`. O `upgrade_install` permite ao Terraform assumir releases Helm que já existiam no cluster.

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
pip install -r scripts/requirements.txt
python3 scripts/validate_apps.py                       # descobre o ingress via kubectl
python3 scripts/validate_apps.py --base-url http://<ENDERECO-DO-INGRESS>
```

Sem `--base-url` (ou a variável `TOGGLEMASTER_BASE_URL`), o script lê
`status.loadBalancer` do Ingress `togglemaster/togglemaster-ingress` e aguarda
até 300 segundos (`--ingress-timeout`) o endereço do ELB existir e resolver no
DNS. A chave mestra vem da variável `MASTER_KEY`; sem ela, usa `togglemaster`.

No workflow **GitOps**, marque `Validate Apps` para executar o mesmo script no
GitHub Actions. O job roda depois de `Kubernetes Apply` e/ou do job de schema
quando marcados (e é pulado se algum deles falhar), aguarda o rollout de todos
os Deployments do namespace `togglemaster` e usa o secret `MASTER_KEY` do
repositório. A API key criada pelo teste é mascarada no log do Actions.

### Atualização da chave durante o teste local

O script `scripts/validate_apps.py` executa `update_key_on_k8s` depois de
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

O Ingress expõe cada serviço usando um prefixo curto e remove esse prefixo antes
de encaminhar a requisição ao serviço:

| Prefixo público | Serviço | Exemplos de endpoints |
| --- | --- | --- |
| `/auth` | `auth-service` | `GET /auth/health`, `GET /auth/validate`, `POST /auth/admin/keys` |
| `/flag` | `flag-service` | `GET /flag/health`, `GET /flag/flags`, `POST /flag/flags`, `GET /flag/flags/{name}` |
| `/targeting` | `targeting-service` | `GET /targeting/health`, `POST /targeting/rules`, `GET /targeting/rules/{flag_name}` |
| `/evaluation` | `evaluation-service` | `GET /evaluation/health`, `GET /evaluation/evaluate?user_id=...&flag_name=...` |
| `/analytics` | `analytics-service` | `GET /analytics/health` |

O `analytics-service` é um worker de eventos; seu endpoint HTTP é apenas o health
check. As operações de criação e alteração de flags e regras exigem uma API key
no cabeçalho de autorização. O endpoint administrativo
`POST /auth/admin/keys` exige a chave mestra configurada no serviço.

Para fazer requisições com `requests`, instale a dependência:

```bash
python -m pip install -r scripts/requirements.txt
```

Exemplo de uso:

```python
import requests

base_url = "http://<ENDERECO-EXTERNO-DO-INGRESS>"
response = requests.get(f"{base_url}/auth/health", timeout=10)
print(response.status_code)
print(response.json())
```

Consulte as rotas acima para usar o prefixo público correspondente ao serviço;
por exemplo, o health do Auth é `/auth/health` (não `/auth-service/health`).

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
```

As credenciais AWS devem estar ativas no momento da execução. As demais Secrets são lidas pelo workflow e transformadas em um Secret Kubernetes chamado `togglemaster-secrets`; não é necessário codificá-las manualmente em Base64.

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

- Traces e logs ficam desligados nos manifests (`OTEL_TRACES_EXPORTER=none` e
  `OTEL_LOGS_EXPORTER=none`). Os logs já chegam ao Loki pelo agent.
- Para ativar traces depois do APM, altere `OTEL_TRACES_EXPORTER` e habilite o
  pipeline de traces no `otel-gateway` (repositório Terraform).
- O evaluation-service grava o contexto de trace nos atributos das mensagens SQS
  e o analytics-service o lê, para o fluxo aparecer como um único trace.
- Os serviços Python usam `opentelemetry-instrument` via `gunicorn.conf.py`; os
  serviços Go usam `otel.go` com `otelhttp`.

## Self-healing do auth-service

O CronJob anterior foi removido dos manifests; o Argo CD remove seus recursos
gerenciados após a publicação e sincronização. A nova arquitetura é
Alertmanager → webhook interno HTTPS → GitHub App → GitHub Actions → EKS.
Por padrão o receptor tem zero réplicas, a rota do Alertmanager está desativada
e a Action só atua se a variável `SELF_HEALING_ENABLED` for `true`.

O receptor aceita apenas notificações autenticadas do alerta
`ToggleMasterAuthHighErrorRate` com `service=auth-service`. O repositório, a
branch e a Action de destino são fixos. A Action consulta novamente o
Prometheus; sem alerta firing, ou em caso de erro, não reinicia o serviço.
Cada restart atualiza atomicamente a annotation de histórico e a annotation de
rollout com `resourceVersion`: no mínimo 15 minutos entre tentativas e no
máximo três em uma janela móvel de uma hora. Conflitos interrompem a execução.
O Argo CD preserva as duas annotations, mas excluir e recriar o Deployment
apaga o histórico. Um rollout bem-sucedido não garante que o alerta foi
resolvido nem corrige necessariamente a causa raiz.

O intervalo do Alertmanager é contado a partir da notificação, não do restart.
Se uma repetição chegar antes de decorridos 15 minutos do restart, ela será
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
`togglemaster/webhook`. A rotação também precisa listar os pods do
receptor.

1. Publique as alterações dos repositórios Terraform e GitOps. Aplique o
   Terraform para criar o repositório ECR `togglemaster/webhook`.
   O valor `self_healing_webhook_enabled` do Helm deve continuar `false`.
   Execute o workflow GitOps com **K8s Apply** para atualizar também a
   Application do Argo CD, que preserva a annotation de restart do receptor.
2. Execute manualmente o workflow **Self-healing**, escolhendo `build-image`.
   Essa opção constrói e publica a imagem que executa o receptor Python no
   Kubernetes; ela não executa o restart.
   Copie o endereço da imagem exibido no log para `image` em
   `webhook/resources.yaml`. A imagem usa a revisão Git como tag.
3. Nas configurações de **Actions** do repositório GitOps, crie as variáveis
   `SELF_HEALING_APP_ID` (App ID em **General**) e
   `SELF_HEALING_INSTALLATION_ID` (ID na URL da instalação do App). Crie o
   secret `SELF_HEALING_APP_PRIVATE_KEY` com o conteúdo integral do arquivo
   `.pem`, incluindo as linhas BEGIN e END. Não adicione a chave ao Git nem a
   compartilhe em mensagens.
   Execute o workflow **Self-healing** com `operation=configure-secrets` e
   `rotate_webhook_credentials=false`. Ele cria ou atualiza os Secrets nas
   duas namespaces, gera o token e o certificado TLS de 90 dias quando
   necessário e reinicia apenas o receptor, caso seu Deployment já exista.
   Os valores gerados permanecem no Kubernetes, não no state do Terraform.
   A execução de `restart` não modifica esses Secrets. Repita a configuração
   após atualizar a chave do GitHub App ou recriar o cluster.
4. Altere `replicas` para `1` no manifesto do receptor, publique o GitOps e
   aguarde o Deployment ficar pronto. Configure a variável de repositório
   `SELF_HEALING_ENABLED=true` no GitHub somente quando autorizar reinícios.
5. No repositório Terraform, defina `self_healing_webhook_enabled = true` em
   `helm/helm.auto.tfvars`, publique e aplique o Terraform. A rota específica
   repete notificações a cada 15 minutos, sem alterar a frequência do Discord
   ou do PagerDuty.
6. Valide o fluxo com uma falha controlada autorizada, conferindo o alerta no
   Prometheus, os logs do receptor, a execução da Action e o rollout. Um
   alerta sintético enviado apenas ao Alertmanager não autoriza restart.

Para suspender novas ações, altere `SELF_HEALING_ENABLED` para `false` no
GitHub; execuções já iniciadas não são canceladas. Desative também a rota Helm
e volte o receptor para zero réplicas nos manifests. A Action pode ser
acionada manualmente, mas sempre aplica as mesmas verificações.
O webhook seleciona a operação `restart`, que executa
`python3 webhook/self_healing.py --restart` no runner; sem essa flag o
mesmo arquivo inicia o servidor HTTPS do receptor.

### Renovação do token e do certificado

O workflow mostra a data de expiração do certificado sem expor credenciais.
Para renová-lo ou trocar o token, desative a rota no Helm, publique
`replicas: 0` para o receptor e aguarde a sincronização do Argo CD. Depois,
execute **Self-healing** com `operation=configure-secrets` e
`rotate_webhook_credentials=true`. A rotação exige zero réplicas e que todos
os pods do receptor tenham terminado, e atualiza os Secrets dos dois lados;
reative o receptor e a rota após a conclusão. Existe
uma interrupção de notificações de self-healing durante essa manutenção.
Se uma execução falhar parcialmente, mantenha o receptor desativado e repita
a rotação. Sem rotação, divergências entre os Secrets são tratadas como erro.
  