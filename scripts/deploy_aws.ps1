# =============================================================================
# Enma Labs — AWS Deployment Script (PowerShell)
#
# Prerequisites:
#   1. AWS CLI v2 installed (double-click AWSCLIV2.msi from your Temp folder)
#   2. Run: aws configure
#      - Access Key ID + Secret Access Key for account 603013471251
#      - Region: ap-south-1
#      - Output: json
#   3. Docker Desktop running (for image builds)
#
# Usage:
#   .\scripts\deploy_aws.ps1
#
# This script creates:
#   - ECR repositories for backend + gateway
#   - ECS cluster (Fargate)
#   - Task definitions
#   - ALB + target groups
#   - ECS services
#   - CloudWatch log groups
# =============================================================================

$ErrorActionPreference = "Stop"
$REGION = "ap-south-1"
$ACCOUNT_ID = "603013471251"
$CLUSTER_NAME = "enma-prod"
$BACKEND_REPO = "enma-backend"
$GATEWAY_REPO = "enma-gateway"
$VPC_CIDR = "10.0.0.0/16"
$TAG = (git rev-parse --short HEAD)

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Enma Labs AWS Deployment" -ForegroundColor Cyan
Write-Host " Account: $ACCOUNT_ID | Region: $REGION" -ForegroundColor Cyan
Write-Host " Image tag: $TAG" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Step 1: ECR Repositories
# ---------------------------------------------------------------------------
Write-Host "`n[1/8] Creating ECR repositories..." -ForegroundColor Yellow

foreach ($repo in @($BACKEND_REPO, $GATEWAY_REPO)) {
    $exists = aws ecr describe-repositories --repository-names $repo --region $REGION 2>&1
    if ($LASTEXITCODE -ne 0) {
        aws ecr create-repository `
            --repository-name $repo `
            --region $REGION `
            --image-scanning-configuration scanOnPush=true `
            --encryption-configuration encryptionType=AES256
        Write-Host "  Created: $repo" -ForegroundColor Green
    } else {
        Write-Host "  Exists: $repo" -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
# Step 2: Build & Push Docker Images
# ---------------------------------------------------------------------------
Write-Host "`n[2/8] Building and pushing Docker images..." -ForegroundColor Yellow

$ECR_URI = "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_URI

# Backend
Write-Host "  Building backend..." -ForegroundColor DarkGray
docker build --target prod -t "${ECR_URI}/${BACKEND_REPO}:${TAG}" -t "${ECR_URI}/${BACKEND_REPO}:latest" ./enma-backend
docker push "${ECR_URI}/${BACKEND_REPO}:${TAG}"
docker push "${ECR_URI}/${BACKEND_REPO}:latest"
Write-Host "  Pushed: ${BACKEND_REPO}:${TAG}" -ForegroundColor Green

# Gateway
Write-Host "  Building gateway..." -ForegroundColor DarkGray
docker build --target prod -t "${ECR_URI}/${GATEWAY_REPO}:${TAG}" -t "${ECR_URI}/${GATEWAY_REPO}:latest" ./enma-gateway
docker push "${ECR_URI}/${GATEWAY_REPO}:${TAG}"
docker push "${ECR_URI}/${GATEWAY_REPO}:latest"
Write-Host "  Pushed: ${GATEWAY_REPO}:${TAG}" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 3: CloudWatch Log Groups
# ---------------------------------------------------------------------------
Write-Host "`n[3/8] Creating CloudWatch log groups..." -ForegroundColor Yellow

foreach ($svc in @("backend", "gateway")) {
    $logGroup = "/ecs/enma-$svc"
    aws logs create-log-group --log-group-name $logGroup --region $REGION 2>&1 | Out-Null
    aws logs put-retention-policy --log-group-name $logGroup --retention-in-days 30 --region $REGION
    Write-Host "  Log group: $logGroup (30d retention)" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 4: IAM Task Execution Role
# ---------------------------------------------------------------------------
Write-Host "`n[4/8] Creating ECS task execution role..." -ForegroundColor Yellow

$ROLE_NAME = "enma-ecs-task-execution"
$TRUST_POLICY = @'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
'@

$trustPolicyFile = "$env:TEMP\enma-trust-policy.json"
$TRUST_POLICY | Out-File -FilePath $trustPolicyFile -Encoding utf8

$roleExists = aws iam get-role --role-name $ROLE_NAME 2>&1
if ($LASTEXITCODE -ne 0) {
    aws iam create-role `
        --role-name $ROLE_NAME `
        --assume-role-policy-document "file://$trustPolicyFile"
    aws iam attach-role-policy `
        --role-name $ROLE_NAME `
        --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
    aws iam attach-role-policy `
        --role-name $ROLE_NAME `
        --policy-arn arn:aws:iam::aws:policy/CloudWatchLogsFullAccess
    Write-Host "  Created role: $ROLE_NAME" -ForegroundColor Green
} else {
    Write-Host "  Role exists: $ROLE_NAME" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Step 5: ECS Cluster
# ---------------------------------------------------------------------------
Write-Host "`n[5/8] Creating ECS Fargate cluster..." -ForegroundColor Yellow

$clusterExists = aws ecs describe-clusters --clusters $CLUSTER_NAME --region $REGION --query "clusters[?status=='ACTIVE'].clusterName" --output text 2>&1
if ($clusterExists -ne $CLUSTER_NAME) {
    aws ecs create-cluster `
        --cluster-name $CLUSTER_NAME `
        --capacity-providers FARGATE `
        --default-capacity-provider-strategy capacityProvider=FARGATE,weight=1 `
        --region $REGION
    Write-Host "  Created cluster: $CLUSTER_NAME" -ForegroundColor Green
} else {
    Write-Host "  Cluster exists: $CLUSTER_NAME" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Step 6: Task Definitions
# ---------------------------------------------------------------------------
Write-Host "`n[6/8] Registering task definitions..." -ForegroundColor Yellow

$EXECUTION_ROLE_ARN = "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# Backend task definition
$backendTaskDef = @"
{
  "family": "enma-backend",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "$EXECUTION_ROLE_ARN",
  "taskRoleArn": "$EXECUTION_ROLE_ARN",
  "containerDefinitions": [{
    "name": "backend",
    "image": "${ECR_URI}/${BACKEND_REPO}:${TAG}",
    "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
    "essential": true,
    "healthCheck": {
      "command": ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)\" || exit 1"],
      "interval": 30,
      "timeout": 5,
      "retries": 3,
      "startPeriod": 15
    },
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/enma-backend",
        "awslogs-region": "$REGION",
        "awslogs-stream-prefix": "ecs"
      }
    },
    "environment": [
      {"name": "ENV", "value": "production"},
      {"name": "LOG_LEVEL", "value": "info"},
      {"name": "APP_PORT", "value": "8000"},
      {"name": "WEB_CONCURRENCY", "value": "2"},
      {"name": "DATABASE_URL", "value": "PLACEHOLDER_SET_VIA_SECRETS"},
      {"name": "BACKEND_API_KEY", "value": "PLACEHOLDER_SET_VIA_SECRETS"},
      {"name": "GATEWAY_HMAC_SECRET", "value": "PLACEHOLDER_SET_VIA_SECRETS"},
      {"name": "TELEGRAM_BOT_TOKEN", "value": "PLACEHOLDER_SET_VIA_SECRETS"},
      {"name": "SENTRY_DSN", "value": "PLACEHOLDER_SET_VIA_SECRETS"},
      {"name": "LLM_API_KEY", "value": "PLACEHOLDER_SET_VIA_SECRETS"},
      {"name": "LAYOUT_MODEL_ENDPOINT", "value": "https://integrate.api.nvidia.com/v1/chat/completions"},
      {"name": "LAYOUT_MODEL_NAME", "value": "meta/llama-3.1-8b-instruct"},
      {"name": "EXTRACTION_MODEL_ENDPOINT", "value": "https://integrate.api.nvidia.com/v1/chat/completions"},
      {"name": "EXTRACTION_MODEL_NAME", "value": "nvidia/nemotron-ocr-v1"},
      {"name": "REASONING_MODEL_ENDPOINT", "value": "https://integrate.api.nvidia.com/v1/chat/completions"},
      {"name": "REASONING_MODEL_NAME", "value": "meta/llama-3.3-70b-instruct"},
      {"name": "EMBEDDING_ENDPOINT", "value": "https://integrate.api.nvidia.com/v1/embeddings"},
      {"name": "EMBEDDING_MODEL_NAME", "value": "nvidia/nv-embedqa-e5-v5"},
      {"name": "EMBEDDING_DIMENSIONS", "value": "1024"},
      {"name": "WHISPER_ENDPOINT", "value": "https://integrate.api.nvidia.com/v1/audio/transcriptions"}
    ]
  }]
}
"@

$backendTaskFile = "$env:TEMP\enma-backend-task.json"
$backendTaskDef | Out-File -FilePath $backendTaskFile -Encoding utf8
aws ecs register-task-definition --cli-input-json "file://$backendTaskFile" --region $REGION
Write-Host "  Registered: enma-backend" -ForegroundColor Green

# Gateway task definition
$gatewayTaskDef = @"
{
  "family": "enma-gateway",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "$EXECUTION_ROLE_ARN",
  "containerDefinitions": [{
    "name": "gateway",
    "image": "${ECR_URI}/${GATEWAY_REPO}:${TAG}",
    "portMappings": [{"containerPort": 3000, "protocol": "tcp"}],
    "essential": true,
    "healthCheck": {
      "command": ["CMD-SHELL", "node -e \"fetch('http://localhost:3000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\" || exit 1"],
      "interval": 30,
      "timeout": 5,
      "retries": 3,
      "startPeriod": 10
    },
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/enma-gateway",
        "awslogs-region": "$REGION",
        "awslogs-stream-prefix": "ecs"
      }
    },
    "environment": [
      {"name": "NODE_ENV", "value": "production"},
      {"name": "LOG_LEVEL", "value": "info"},
      {"name": "PORT", "value": "3000"},
      {"name": "BACKEND_URL", "value": "http://localhost:8000"},
      {"name": "BACKEND_API_KEY", "value": "PLACEHOLDER_SET_VIA_SECRETS"},
      {"name": "GATEWAY_HMAC_SECRET", "value": "PLACEHOLDER_SET_VIA_SECRETS"},
      {"name": "TELEGRAM_BOT_TOKEN", "value": "PLACEHOLDER_SET_VIA_SECRETS"},
      {"name": "SENTRY_DSN", "value": "PLACEHOLDER_SET_VIA_SECRETS"}
    ]
  }]
}
"@

$gatewayTaskFile = "$env:TEMP\enma-gateway-task.json"
$gatewayTaskDef | Out-File -FilePath $gatewayTaskFile -Encoding utf8
aws ecs register-task-definition --cli-input-json "file://$gatewayTaskFile" --region $REGION
Write-Host "  Registered: enma-gateway" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 7: Get Default VPC + Subnets
# ---------------------------------------------------------------------------
Write-Host "`n[7/8] Discovering VPC and subnets..." -ForegroundColor Yellow

$VPC_ID = aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text --region $REGION
Write-Host "  VPC: $VPC_ID" -ForegroundColor Green

$SUBNETS = aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" --query "Subnets[*].SubnetId" --output text --region $REGION
$SUBNET_LIST = $SUBNETS -split "`t"
Write-Host "  Subnets: $($SUBNET_LIST -join ', ')" -ForegroundColor Green

# Create security group for ECS tasks
$SG_NAME = "enma-ecs-tasks"
$sgExists = aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC_ID" --query "SecurityGroups[0].GroupId" --output text --region $REGION 2>&1
if ($sgExists -eq "None" -or $LASTEXITCODE -ne 0) {
    $SG_ID = aws ec2 create-security-group `
        --group-name $SG_NAME `
        --description "Enma ECS tasks - backend 8000, gateway 3000" `
        --vpc-id $VPC_ID `
        --region $REGION `
        --query "GroupId" --output text

    # Allow inbound on 8000 and 3000
    aws ec2 authorize-security-group-ingress --group-id $SG_ID --protocol tcp --port 8000 --cidr 0.0.0.0/0 --region $REGION
    aws ec2 authorize-security-group-ingress --group-id $SG_ID --protocol tcp --port 3000 --cidr 0.0.0.0/0 --region $REGION
    Write-Host "  Security group: $SG_ID" -ForegroundColor Green
} else {
    $SG_ID = $sgExists
    Write-Host "  Security group exists: $SG_ID" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Step 8: ECS Services
# ---------------------------------------------------------------------------
Write-Host "`n[8/8] Creating ECS services..." -ForegroundColor Yellow

$SUBNET_JSON = ($SUBNET_LIST | ForEach-Object { "`"$_`"" }) -join ","

# Backend service
$backendSvcExists = aws ecs describe-services --cluster $CLUSTER_NAME --services enma-backend --region $REGION --query "services[?status=='ACTIVE'].serviceName" --output text 2>&1
if ($backendSvcExists -ne "enma-backend") {
    aws ecs create-service `
        --cluster $CLUSTER_NAME `
        --service-name enma-backend `
        --task-definition enma-backend `
        --desired-count 1 `
        --launch-type FARGATE `
        --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_JSON],securityGroups=[$SG_ID],assignPublicIp=ENABLED}" `
        --region $REGION
    Write-Host "  Created service: enma-backend" -ForegroundColor Green
} else {
    aws ecs update-service `
        --cluster $CLUSTER_NAME `
        --service enma-backend `
        --task-definition enma-backend `
        --force-new-deployment `
        --region $REGION
    Write-Host "  Updated service: enma-backend" -ForegroundColor Green
}

# Gateway service
$gatewaySvcExists = aws ecs describe-services --cluster $CLUSTER_NAME --services enma-gateway --region $REGION --query "services[?status=='ACTIVE'].serviceName" --output text 2>&1
if ($gatewaySvcExists -ne "enma-gateway") {
    aws ecs create-service `
        --cluster $CLUSTER_NAME `
        --service-name enma-gateway `
        --task-definition enma-gateway `
        --desired-count 1 `
        --launch-type FARGATE `
        --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_JSON],securityGroups=[$SG_ID],assignPublicIp=ENABLED}" `
        --region $REGION
    Write-Host "  Created service: enma-gateway" -ForegroundColor Green
} else {
    aws ecs update-service `
        --cluster $CLUSTER_NAME `
        --service enma-gateway `
        --task-definition enma-gateway `
        --force-new-deployment `
        --region $REGION
    Write-Host "  Updated service: enma-gateway" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host " Deployment complete!" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host @"

Next steps:
  1. Update task definitions with real secrets:
     - DATABASE_URL (from Supabase dashboard)
     - TELEGRAM_BOT_TOKEN (from BotFather)
     - LLM_API_KEY (OpenAI key)
     - SENTRY_DSN (from Sentry project settings)
     - BACKEND_API_KEY (generate: openssl rand -hex 32)
     - GATEWAY_HMAC_SECRET (generate: openssl rand -hex 32)

  2. Redeploy after updating secrets:
     aws ecs update-service --cluster enma-prod --service enma-backend --force-new-deployment --region ap-south-1
     aws ecs update-service --cluster enma-prod --service enma-gateway --force-new-deployment --region ap-south-1

  3. Check health:
     aws ecs describe-services --cluster enma-prod --services enma-backend enma-gateway --region ap-south-1

  4. View logs:
     aws logs tail /ecs/enma-backend --follow --region ap-south-1
     aws logs tail /ecs/enma-gateway --follow --region ap-south-1
"@
