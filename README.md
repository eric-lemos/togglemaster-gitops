# ToggleMaster GitOps

Manifests Kubernetes gerenciados pelo Argo CD. O Terraform instala o Argo CD e o Metrics Server no cluster; esta aplicação é registrada no Argo CD uma única vez:

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

O workflow `.github/workflows/gitops.yml` pode ser executado em **Actions > K8s Utils > Run workflow** com quatro inputs independentes:

- `K8s Apply`: aplica a raiz Kustomize (`kubectl apply -k .`).
- `Run Postgres Schema Job`: remove e recria `postgres-schema-init`, aguarda sua conclusão e exibe os logs.
- O job `deploy` também verifica e instala o Ingress NGINX como Service `LoadBalancer` antes de aplicar os manifests.
- `Install Metrics Server`: instala ou atualiza o chart do Metrics Server via Helm.
- `Install ArgoCD`: instala ou atualiza o chart do Argo CD, cria o Service `LoadBalancer` e registra a Application GitOps.

Os charts usam `helm upgrade --install`, portanto a mesma execução pode ser repetida com segurança. O Metrics Server é instalado com `--kubelet-insecure-tls`, necessário para clusters EKS em que o certificado apresentado pelo kubelet não é confiável pelo componente.

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
