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

### Execucao do validador

```bash
pip install -r scripts/requirements.txt
python3 scripts/validate_apps.py                       # descobre o ingress via kubectl
python3 scripts/validate_apps.py --base-url http://<ENDERECO-DO-INGRESS>
```

Sem `--base-url` (ou a variavel `TOGGLEMASTER_BASE_URL`), o script le
`status.loadBalancer` do Ingress `togglemaster/togglemaster-ingress` e aguarda
ate 300 segundos (`--ingress-timeout`) o endereco do ELB existir e resolver no
DNS. A chave mestra vem da variavel `MASTER_KEY`; sem ela, usa `togglemaster`.

No workflow **GitOps**, marque `Validate Apps` para executar o mesmo script no
GitHub Actions. O job roda depois de `Kubernetes Apply` e/ou do job de schema
quando marcados (e e pulado se algum deles falhar), aguarda o rollout de todos
os Deployments do namespace `togglemaster` e usa o secret `MASTER_KEY` do
repositorio. A API key criada pelo teste e mascarada no log do Actions.

### Atualizacao da chave durante o teste local

O script `scripts/validate_apps.py` executa `update_key_on_k8s` depois de
`update_rule`, antes das avaliacoes. Ele usa a API key criada pelo teste para
criar ou atualizar `togglemaster-runtime/SERVICE_API_KEY` no namespace
`togglemaster`, reinicia o Deployment `evaluation-service` e aguarda o rollout
por ate 120 segundos. Se alguma etapa falhar, o teste e interrompido.

Esse teste altera o cluster do contexto atual do `kubectl` e a chave usada por
todas as replicas do evaluation-service. Confira `kubectl config current-context`
antes de executar. E necessario ter permissao para criar/atualizar Secrets,
reiniciar o Deployment e acompanhar o rollout. A chave nao e salva em arquivo
local nem passada nos argumentos do comando. Nao execute em producao.

Ao terminar os testes, `show_cache_hits` consulta os logs dos ultimos 10 minutos
de todos os pods em `Running` com o label `app=evaluation-service` usando `kubectl logs` e
exibe apenas os `Cache HIT` da flag criada pelo teste. A leitura exige permissao
para listar pods e acessar seus logs. Se nenhum registro for encontrado, exibe
um aviso; falhas do comando interrompem o teste.

Depois das avaliacoes, `check_dynamodb()` usa a AWS CLI para listar os eventos
da flag criada pelo teste na tabela `ToggleMasterAnalytics` em `us-east-1`.
Exibe JSON sem os marcadores de tipo do DynamoDB, por timestamp decrescente.
Requer AWS CLI instalada, credenciais locais e permissao `dynamodb:Scan`.
O scan percorre a tabela inteira (com paginacao automatica), mesmo com filtro;
use apenas em tabelas pequenas de teste. A consulta tem timeout de 60 segundos.
Como o processamento SQS e assincrono, uma consulta vazia gera aviso; para
consultar novamente, use `validator.check_dynamodb()` na mesma instancia.
Tabela e regiao podem ser alteradas pelos argumentos `table_name` e `region`.
O validador nao consulta a SQS diretamente: registros da flag criada nesta
execucao no DynamoDB evidenciam o fluxo evaluation-service -> SQS ->
analytics-service -> DynamoDB. Isso nao comprova a exclusao das mensagens da fila.

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