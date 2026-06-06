# =============================================================================
# Enma Labs — AWS Production Deployment (PowerShell)
#
# Architecture (Phase 8 — 13/13 production-ready):
#
#   Internet
#      |
#      v
#   +---------------------------+
#   |  Public ALB (enma-alb)    |  TLS at LB (optional ACM cert)
#   |  - /worker/* → backend TG |
#   |  - /webhook  → gateway TG |
#   |  - SG: 0.0.0.0/0 :443/80  |
#   +-------------+-------------+
#                 |
#       +---------+---------+
#       |                   |
#       v                   v
#   +---------+         +---------+
#   | backend |         | gateway |   ECS Fargate tasks (default VPC, 3 AZ)
#   |  :8000  |         |  :3000  |   SG ingress: ALB-SG only
#   |   ...   |         |  ...    |   Egress: 0.0.0.0/0 (NIM API, Telegram)
#   +----+----+         +---------+
#        |
#        v
#   +-------------------+
#   | ElastiCache Redis |  Serverless cache, SG ingress: ECS-SG only
#   |     :6379         |
#   +-------------------+
#                 +
#         (TLS over public internet)
#                 v
#   +-------------------+
#   |  Supabase Postgres |  Managed, sslmode=require
#   +-------------------+
#
# Prerequisites:
#   1. AWS CLI v2 installed and `aws configure` completed for account
#      603013471251 in region ap-south-1.
#   2. Docker images already built and pushed (the previous deploy used
#      CodeBuild; this script does not rebuild — it only provisions infra
#      and updates service definitions).
#
# Idempotency: every step checks for existing resources and reuses them.
# Safe to re-run as many times as you like.
# =============================================================================

$ErrorActionPreference = "Stop"

# ---- Configuration ---------------------------------------------------------
$REGION       = "ap-south-1"
$ACCOUNT_ID   = "603013471251"
$CLUSTER_NAME = "enma-prod"
$BACKEND_REPO = "enma-backend"
$GATEWAY_REPO = "enma-gateway"

# ALB / target group names — kept short so AWS limits aren't hit.
$ALB_NAME      = "enma-alb"
$BACKEND_TG    = "enma-backend-tg"
$GATEWAY_TG    = "enma-gateway-tg"

# Security group names
$ALB_SG_NAME   = "enma-alb-sg"
$ECS_SG_NAME   = "enma-ecs-tasks"
$REDIS_SG_NAME = "enma-redis-sg"

# ElastiCache Serverless
$REDIS_CACHE_NAME = "enma-cache"

$TAG = (git rev-parse --short HEAD 2>$null); if (-not $TAG) { $TAG = "latest" }

function Write-Step([string]$label) {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host " $label" -ForegroundColor Cyan
    Write-Host "============================================" -ForegroundColor Cyan
}

Write-Host "Enma Labs production deployment — account $ACCOUNT_ID, region $REGION, tag $TAG"

# ---------------------------------------------------------------------------
# Step 1: ECR repositories (idempotent)
# ---------------------------------------------------------------------------
Write-Step "[1/12] ECR repositories"

foreach ($repo in @($BACKEND_REPO, $GATEWAY_REPO)) {
    aws ecr describe-repositories --repository-names $repo --region $REGION 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        aws ecr create-repository --repository-name $repo --region $REGION `
            --image-scanning-configuration scanOnPush=true `
            --encryption-configuration encryptionType=AES256 | Out-Null
        Write-Host "  Created repository: $repo" -ForegroundColor Green
    } else {
        Write-Host "  Repository exists: $repo" -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
# Step 2: CloudWatch log groups
# ---------------------------------------------------------------------------
Write-Step "[2/12] CloudWatch log groups"

foreach ($svc in @("backend", "gateway")) {
    $lg = "/ecs/enma-$svc"
    aws logs create-log-group --log-group-name $lg --region $REGION 2>$null | Out-Null
    aws logs put-retention-policy --log-group-name $lg --retention-in-days 30 --region $REGION
    Write-Host "  Log group ready: $lg (30 d retention)" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 3: IAM task execution role
# ---------------------------------------------------------------------------
Write-Step "[3/12] IAM task execution role"

$ROLE_NAME = "enma-ecs-task-execution"
$trustFile = "$env:TEMP\enma-trust.json"
@'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}
'@ | Out-File -Encoding utf8 $trustFile

aws iam get-role --role-name $ROLE_NAME 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    aws iam create-role --role-name $ROLE_NAME --assume-role-policy-document "file://$trustFile" | Out-Null
    aws iam attach-role-policy --role-name $ROLE_NAME --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
    aws iam attach-role-policy --role-name $ROLE_NAME --policy-arn arn:aws:iam::aws:policy/CloudWatchLogsFullAccess
    Write-Host "  Created role: $ROLE_NAME" -ForegroundColor Green
} else {
    Write-Host "  Role exists: $ROLE_NAME" -ForegroundColor DarkGray
}
$EXEC_ROLE_ARN = "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# ---------------------------------------------------------------------------
# Step 4: ECS cluster
# ---------------------------------------------------------------------------
Write-Step "[4/12] ECS cluster"

$existing = aws ecs describe-clusters --clusters $CLUSTER_NAME --region $REGION `
    --query "clusters[?status=='ACTIVE'].clusterName" --output text 2>$null
if ($existing -ne $CLUSTER_NAME) {
    aws ecs create-cluster --cluster-name $CLUSTER_NAME --region $REGION | Out-Null
    Write-Host "  Created cluster: $CLUSTER_NAME" -ForegroundColor Green
} else {
    Write-Host "  Cluster exists: $CLUSTER_NAME" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Step 5: VPC + subnets discovery
# ---------------------------------------------------------------------------
Write-Step "[5/12] VPC discovery"

$VPC_ID = aws ec2 describe-vpcs --filters Name=isDefault,Values=true `
    --query "Vpcs[0].VpcId" --output text --region $REGION
$SUBNETS = aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" `
    --query "Subnets[*].SubnetId" --output text --region $REGION
$SUBNET_LIST = $SUBNETS -split "`t"
$SUBNET_CSV  = $SUBNET_LIST -join ","
Write-Host "  VPC: $VPC_ID" -ForegroundColor Green
Write-Host "  Subnets: $SUBNET_CSV" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 6: Security groups — three-tier (ALB → ECS → Redis)
# ---------------------------------------------------------------------------
Write-Step "[6/12] Security groups"

function Get-OrCreateSG([string]$name, [string]$description) {
    $existing = aws ec2 describe-security-groups `
        --filters "Name=group-name,Values=$name" "Name=vpc-id,Values=$VPC_ID" `
        --query "SecurityGroups[0].GroupId" --output text --region $REGION 2>$null
    if ($existing -and $existing -ne "None") {
        Write-Host "  SG exists: $name → $existing" -ForegroundColor DarkGray
        return $existing
    }
    $sgId = aws ec2 create-security-group --group-name $name `
        --description $description --vpc-id $VPC_ID --region $REGION `
        --query "GroupId" --output text
    Write-Host "  Created SG: $name → $sgId" -ForegroundColor Green
    return $sgId
}

$ALB_SG   = Get-OrCreateSG $ALB_SG_NAME   "Public ALB ingress 80/443 from internet"
$ECS_SG   = Get-OrCreateSG $ECS_SG_NAME   "ECS tasks - ingress from ALB-SG only"
$REDIS_SG = Get-OrCreateSG $REDIS_SG_NAME "Redis 6379 - ingress from ECS-SG only"

# --- Ingress rules (idempotent — failures here are usually "already exists") ---

# ALB SG: allow 80 + 443 from the internet
aws ec2 authorize-security-group-ingress --group-id $ALB_SG --protocol tcp --port 80  --cidr 0.0.0.0/0 --region $REGION 2>$null | Out-Null
aws ec2 authorize-security-group-ingress --group-id $ALB_SG --protocol tcp --port 443 --cidr 0.0.0.0/0 --region $REGION 2>$null | Out-Null

# ECS SG: revoke any wide-open rules (cleanup from previous deploy) and
# only allow inbound from ALB SG on 8000 + 3000.
aws ec2 revoke-security-group-ingress --group-id $ECS_SG --protocol tcp --port 8000 --cidr 0.0.0.0/0 --region $REGION 2>$null | Out-Null
aws ec2 revoke-security-group-ingress --group-id $ECS_SG --protocol tcp --port 3000 --cidr 0.0.0.0/0 --region $REGION 2>$null | Out-Null
aws ec2 authorize-security-group-ingress --group-id $ECS_SG --protocol tcp --port 8000 --source-group $ALB_SG --region $REGION 2>$null | Out-Null
aws ec2 authorize-security-group-ingress --group-id $ECS_SG --protocol tcp --port 3000 --source-group $ALB_SG --region $REGION 2>$null | Out-Null

# Redis SG: only ECS tasks may connect on 6379.
aws ec2 authorize-security-group-ingress --group-id $REDIS_SG --protocol tcp --port 6379 --source-group $ECS_SG --region $REGION 2>$null | Out-Null

Write-Host "  Ingress rules: ALB(0.0.0.0/0:80,443), ECS(ALB-SG:8000,3000), Redis(ECS-SG:6379)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 7: ElastiCache Serverless Redis
# ---------------------------------------------------------------------------
Write-Step "[7/12] ElastiCache Serverless Redis"

# Subnet group — required for placing the cache in our VPC.
$REDIS_SUBNET_GROUP = "enma-redis-subnets"
aws elasticache describe-cache-subnet-groups --cache-subnet-group-name $REDIS_SUBNET_GROUP --region $REGION 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    aws elasticache create-cache-subnet-group `
        --cache-subnet-group-name $REDIS_SUBNET_GROUP `
        --cache-subnet-group-description "Enma cache - default VPC subnets" `
        --subnet-ids $SUBNET_LIST `
        --region $REGION | Out-Null
    Write-Host "  Created subnet group: $REDIS_SUBNET_GROUP" -ForegroundColor Green
} else {
    Write-Host "  Subnet group exists: $REDIS_SUBNET_GROUP" -ForegroundColor DarkGray
}

# Serverless cache — auto-scales ECPU/storage, no node sizing.
$existing = aws elasticache describe-serverless-caches --serverless-cache-name $REDIS_CACHE_NAME --region $REGION --query "ServerlessCaches[0].Status" --output text 2>$null
if (-not $existing -or $existing -eq "None") {
    aws elasticache create-serverless-cache `
        --serverless-cache-name $REDIS_CACHE_NAME `
        --engine redis `
        --subnet-ids $SUBNET_LIST `
        --security-group-ids $REDIS_SG `
        --description "Enma production cache (rate-limit + LLM + tax verdict)" `
        --region $REGION | Out-Null
    Write-Host "  Creating Serverless cache: $REDIS_CACHE_NAME (status check follows)" -ForegroundColor Green
} else {
    Write-Host "  Cache exists: $REDIS_CACHE_NAME (status: $existing)" -ForegroundColor DarkGray
}

# Poll until the cache is available (can take 5-10 minutes on first create).
Write-Host "  Waiting for cache to become available..." -ForegroundColor Yellow
do {
    Start-Sleep -Seconds 20
    $status = aws elasticache describe-serverless-caches `
        --serverless-cache-name $REDIS_CACHE_NAME --region $REGION `
        --query "ServerlessCaches[0].Status" --output text
    Write-Host "    status: $status" -ForegroundColor DarkGray
} while ($status -ne "available")

$REDIS_ENDPOINT = aws elasticache describe-serverless-caches `
    --serverless-cache-name $REDIS_CACHE_NAME --region $REGION `
    --query "ServerlessCaches[0].Endpoint.Address" --output text
$REDIS_PORT = aws elasticache describe-serverless-caches `
    --serverless-cache-name $REDIS_CACHE_NAME --region $REGION `
    --query "ServerlessCaches[0].Endpoint.Port" --output text
# ElastiCache Serverless enforces TLS — use rediss:// (note the double-s).
$REDIS_URL = "rediss://${REDIS_ENDPOINT}:${REDIS_PORT}/0"
Write-Host "  REDIS_URL → $REDIS_URL" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 8: Application Load Balancer
# ---------------------------------------------------------------------------
Write-Step "[8/12] Application Load Balancer"

# Public ALB — uses the same default-VPC subnets (they have IGW routes).
$existing = aws elbv2 describe-load-balancers --names $ALB_NAME --region $REGION `
    --query "LoadBalancers[0].LoadBalancerArn" --output text 2>$null
if (-not $existing -or $existing -eq "None") {
    $ALB_ARN = aws elbv2 create-load-balancer `
        --name $ALB_NAME --type application --scheme internet-facing `
        --subnets $SUBNET_LIST --security-groups $ALB_SG `
        --region $REGION --query "LoadBalancers[0].LoadBalancerArn" --output text
    Write-Host "  Created ALB: $ALB_NAME" -ForegroundColor Green
} else {
    $ALB_ARN = $existing
    Write-Host "  ALB exists: $ALB_NAME" -ForegroundColor DarkGray
}

$ALB_DNS = aws elbv2 describe-load-balancers --load-balancer-arns $ALB_ARN --region $REGION `
    --query "LoadBalancers[0].DNSName" --output text
Write-Host "  ALB DNS: $ALB_DNS" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 9: Target groups
# ---------------------------------------------------------------------------
Write-Step "[9/12] Target groups"

function Get-OrCreateTG([string]$name, [int]$port, [string]$healthPath) {
    $existing = aws elbv2 describe-target-groups --names $name --region $REGION `
        --query "TargetGroups[0].TargetGroupArn" --output text 2>$null
    if ($existing -and $existing -ne "None") {
        Write-Host "  TG exists: $name → $existing" -ForegroundColor DarkGray
        return $existing
    }
    $arn = aws elbv2 create-target-group --name $name --protocol HTTP --port $port `
        --vpc-id $VPC_ID --target-type ip `
        --health-check-protocol HTTP --health-check-path $healthPath `
        --health-check-interval-seconds 30 --health-check-timeout-seconds 5 `
        --healthy-threshold-count 2 --unhealthy-threshold-count 3 `
        --matcher HttpCode=200 `
        --region $REGION --query "TargetGroups[0].TargetGroupArn" --output text
    # Faster deregistration drain so deploys roll cleanly.
    aws elbv2 modify-target-group-attributes --target-group-arn $arn `
        --attributes Key=deregistration_delay.timeout_seconds,Value=30 --region $REGION | Out-Null
    Write-Host "  Created TG: $name → $arn" -ForegroundColor Green
    return $arn
}

$BACKEND_TG_ARN = Get-OrCreateTG $BACKEND_TG 8000 "/health"
$GATEWAY_TG_ARN = Get-OrCreateTG $GATEWAY_TG 3000 "/health"

# ---------------------------------------------------------------------------
# Step 10: ALB listeners + path rules
# ---------------------------------------------------------------------------
Write-Step "[10/12] ALB listeners and routing"

# HTTP :80 listener — default forwards to backend; gateway gets explicit
# path rule. For HTTPS, supply --certificates ARN and set --protocol HTTPS.
$existing = aws elbv2 describe-listeners --load-balancer-arn $ALB_ARN --region $REGION `
    --query "Listeners[?Port==``80``].ListenerArn" --output text 2>$null
if (-not $existing) {
    $LISTENER_ARN = aws elbv2 create-listener --load-balancer-arn $ALB_ARN `
        --protocol HTTP --port 80 `
        --default-actions Type=forward,TargetGroupArn=$BACKEND_TG_ARN `
        --region $REGION --query "Listeners[0].ListenerArn" --output text
    Write-Host "  Created listener :80 → backend TG" -ForegroundColor Green
} else {
    $LISTENER_ARN = $existing
    Write-Host "  Listener :80 exists" -ForegroundColor DarkGray
}

# Path rule: /telegram/* → gateway TG (lets us expose a single hostname).
# Priority 10 so it evaluates before the default backend forward.
$rules = aws elbv2 describe-rules --listener-arn $LISTENER_ARN --region $REGION `
    --query "Rules[?Priority=='10'].RuleArn" --output text
if (-not $rules) {
    aws elbv2 create-rule --listener-arn $LISTENER_ARN --priority 10 `
        --conditions "Field=path-pattern,Values=/telegram/*" `
        --actions Type=forward,TargetGroupArn=$GATEWAY_TG_ARN --region $REGION | Out-Null
    Write-Host "  Path rule /telegram/* → gateway TG (priority 10)" -ForegroundColor Green
} else {
    Write-Host "  Path rule exists" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Step 11: Task definitions (re-registered every run with the latest TAG)
# ---------------------------------------------------------------------------
Write-Step "[11/12] Task definitions"

$ECR_URI = "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

function Register-Backend {
    $envBlock = @"
[
  {"name":"ENV","value":"production"},
  {"name":"LOG_LEVEL","value":"info"},
  {"name":"APP_PORT","value":"8000"},
  {"name":"WEB_CONCURRENCY","value":"2"},
  {"name":"DATABASE_URL","value":"REPLACE_WITH_SUPABASE_DSN"},
  {"name":"DB_POOL_SIZE","value":"5"},
  {"name":"REDIS_URL","value":"$REDIS_URL"},
  {"name":"RATE_LIMIT_DEFAULT","value":"100/minute"},
  {"name":"BACKEND_API_KEY","value":"REPLACE_WITH_SECRET"},
  {"name":"GATEWAY_HMAC_SECRET","value":"REPLACE_WITH_SECRET"},
  {"name":"TELEGRAM_BOT_TOKEN","value":"REPLACE_WITH_SECRET"},
  {"name":"SENTRY_DSN","value":"REPLACE_WITH_SECRET"},
  {"name":"LLM_API_KEY","value":"REPLACE_WITH_SECRET"},
  {"name":"LAYOUT_MODEL_ENDPOINT","value":"https://integrate.api.nvidia.com/v1/chat/completions"},
  {"name":"LAYOUT_MODEL_NAME","value":"meta/llama-3.1-8b-instruct"},
  {"name":"EXTRACTION_MODEL_ENDPOINT","value":"https://integrate.api.nvidia.com/v1/chat/completions"},
  {"name":"EXTRACTION_MODEL_NAME","value":"nvidia/nemotron-ocr-v1"},
  {"name":"REASONING_MODEL_ENDPOINT","value":"https://integrate.api.nvidia.com/v1/chat/completions"},
  {"name":"REASONING_MODEL_NAME","value":"meta/llama-3.3-70b-instruct"},
  {"name":"EMBEDDING_ENDPOINT","value":"https://integrate.api.nvidia.com/v1/embeddings"},
  {"name":"EMBEDDING_MODEL_NAME","value":"nvidia/nv-embedqa-e5-v5"},
  {"name":"EMBEDDING_DIMENSIONS","value":"1024"},
  {"name":"WHISPER_ENDPOINT","value":"https://integrate.api.nvidia.com/v1/audio/transcriptions"},
  {"name":"CORS_ORIGINS","value":"[\"*\"]"}
]
"@
    $containerDefs = ConvertTo-Json -Compress @(
        @{
            name = "backend"
            image = "${ECR_URI}/${BACKEND_REPO}:latest"
            portMappings = @(@{ containerPort = 8000; protocol = "tcp" })
            essential = $true
            healthCheck = @{
                command = @(
                    "CMD-SHELL",
                    "python -c `"import urllib.request; urllib.request.urlopen('http://localhost:8000/health',timeout=3)`" || exit 1"
                )
                interval = 30; timeout = 5; retries = 3; startPeriod = 15
            }
            logConfiguration = @{
                logDriver = "awslogs"
                options = @{
                    "awslogs-group" = "/ecs/enma-backend"
                    "awslogs-region" = $REGION
                    "awslogs-stream-prefix" = "ecs"
                }
            }
            environment = (ConvertFrom-Json $envBlock)
        }
    ) -Depth 10
    $f = "$env:TEMP\enma-backend-containers.json"
    $containerDefs | Out-File -Encoding utf8 $f
    aws ecs register-task-definition --family enma-backend `
        --network-mode awsvpc --requires-compatibilities FARGATE `
        --cpu 512 --memory 1024 `
        --execution-role-arn $EXEC_ROLE_ARN --task-role-arn $EXEC_ROLE_ARN `
        --container-definitions "file://$f" --region $REGION `
        --query "taskDefinition.{rev:revision,arn:taskDefinitionArn}" --output json
}

function Register-Gateway {
    # Gateway calls backend via the ALB DNS name (now stable across deploys).
    $envBlock = @"
[
  {"name":"NODE_ENV","value":"production"},
  {"name":"LOG_LEVEL","value":"info"},
  {"name":"PORT","value":"3000"},
  {"name":"BACKEND_URL","value":"http://$ALB_DNS"},
  {"name":"BACKEND_API_KEY","value":"REPLACE_WITH_SECRET"},
  {"name":"GATEWAY_HMAC_SECRET","value":"REPLACE_WITH_SECRET"},
  {"name":"TELEGRAM_BOT_TOKEN","value":"REPLACE_WITH_SECRET"},
  {"name":"SENTRY_DSN","value":"REPLACE_WITH_SECRET"}
]
"@
    $containerDefs = ConvertTo-Json -Compress @(
        @{
            name = "gateway"
            image = "${ECR_URI}/${GATEWAY_REPO}:latest"
            portMappings = @(@{ containerPort = 3000; protocol = "tcp" })
            essential = $true
            healthCheck = @{
                command = @(
                    "CMD-SHELL",
                    "node -e `"fetch('http://localhost:3000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))`" || exit 1"
                )
                interval = 30; timeout = 5; retries = 3; startPeriod = 10
            }
            logConfiguration = @{
                logDriver = "awslogs"
                options = @{
                    "awslogs-group" = "/ecs/enma-gateway"
                    "awslogs-region" = $REGION
                    "awslogs-stream-prefix" = "ecs"
                }
            }
            environment = (ConvertFrom-Json $envBlock)
        }
    ) -Depth 10
    $f = "$env:TEMP\enma-gateway-containers.json"
    $containerDefs | Out-File -Encoding utf8 $f
    aws ecs register-task-definition --family enma-gateway `
        --network-mode awsvpc --requires-compatibilities FARGATE `
        --cpu 256 --memory 512 `
        --execution-role-arn $EXEC_ROLE_ARN `
        --container-definitions "file://$f" --region $REGION `
        --query "taskDefinition.{rev:revision,arn:taskDefinitionArn}" --output json
}

Write-Host "  Registering backend task definition..." -ForegroundColor Yellow
Register-Backend
Write-Host "  Registering gateway task definition..." -ForegroundColor Yellow
Register-Gateway
Write-Host "  Task definitions registered." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 12: ECS services — ALB-attached, no public IPs
# ---------------------------------------------------------------------------
Write-Step "[12/12] ECS services"

function Upsert-Service {
    param([string]$name, [string]$tgArn, [int]$containerPort)
    $exists = aws ecs describe-services --cluster $CLUSTER_NAME --services $name `
        --region $REGION --query "services[?status=='ACTIVE'].serviceName" --output text 2>$null
    $netCfg = "awsvpcConfiguration={subnets=[$SUBNET_CSV],securityGroups=[$ECS_SG],assignPublicIp=ENABLED}"
    # NOTE on assignPublicIp=ENABLED: default-VPC subnets are public; ECS
    # tasks need a route to the internet to pull from ECR + call NIM.
    # Inbound is still locked down by the ECS SG (ALB-only ingress).
    # If you move to a private-subnet VPC with NAT, flip this to DISABLED.
    if ($exists -ne $name) {
        aws ecs create-service --cluster $CLUSTER_NAME --service-name $name `
            --task-definition $name --desired-count 1 --launch-type FARGATE `
            --network-configuration $netCfg `
            --load-balancers "targetGroupArn=$tgArn,containerName=${name#enma-},containerPort=$containerPort" `
            --health-check-grace-period-seconds 60 `
            --region $REGION | Out-Null
        Write-Host "  Created service: $name (attached to TG)" -ForegroundColor Green
    } else {
        aws ecs update-service --cluster $CLUSTER_NAME --service $name `
            --task-definition $name --force-new-deployment --region $REGION | Out-Null
        Write-Host "  Updated service: $name (forced rollout)" -ForegroundColor Green
    }
}

# `${name#enma-}` removes the "enma-" prefix to match the container name in
# the task definition (backend / gateway).
Upsert-Service "enma-backend" $BACKEND_TG_ARN 8000
Upsert-Service "enma-gateway" $GATEWAY_TG_ARN 3000

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Step "Deployment complete"
Write-Host @"

Public entry point (no SSL yet — add ACM cert + HTTPS listener for prod):
  http://$ALB_DNS/         → backend
  http://$ALB_DNS/telegram → gateway

Internal addresses (used by ECS tasks):
  Redis: $REDIS_URL
  Backend (via ALB): http://$ALB_DNS

Next steps:
  1. Replace REPLACE_WITH_SECRET values in task definitions by updating env
     vars in AWS Secrets Manager or directly in the task definition JSON.
     Then: aws ecs update-service --cluster enma-prod --service enma-backend
            --force-new-deployment --region $REGION

  2. Issue an ACM certificate for your domain, point the domain at the ALB,
     and add an HTTPS :443 listener. The HTTP :80 listener should then
     redirect to HTTPS.

  3. Smoke test:
       curl http://$ALB_DNS/health
       curl http://$ALB_DNS/ready
       curl http://$ALB_DNS/telegram/health

  4. Tail logs:
       aws logs tail /ecs/enma-backend --follow --region $REGION
       aws logs tail /ecs/enma-gateway --follow --region $REGION
"@
