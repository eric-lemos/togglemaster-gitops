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
