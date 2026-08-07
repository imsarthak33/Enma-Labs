# CI Auto-Deploy Setup (GitHub Actions → ECS)

`.github/workflows/deploy-backend.yml` deploys the backend on every push to
`main` that touches `enma-backend/**`. It runs the existing
`scripts/deploy.py` (CodeBuild → ECR digest → pinned task def → ECS
`update-service`), so the only new piece is letting GitHub authenticate to
AWS via OIDC (no long-lived access keys).

Do these **one-time** steps in the AWS account **`603013471251`** (region
`ap-south-1`) and in the GitHub repo settings. Until they're done, the
workflow's "Configure AWS credentials" step will fail.

---

## 1. Create the GitHub OIDC identity provider (skip if it already exists)

IAM → Identity providers → Add provider → OpenID Connect:
- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

CLI equivalent:
```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

## 2. Create the deploy role

Create an IAM role (e.g. `enma-github-deploy`) with this **trust policy** —
it lets only this repo assume the role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::603013471251:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": [
            "repo:imsarthak33/Enma-Labs:environment:production",
            "repo:imsarthak33/Enma-Labs:ref:refs/heads/main"
          ]
        }
      }
    }
  ]
}
```

Attach this **permissions policy** (mirrors what `scripts/deploy.py` calls;
tighten as you like):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PushBuildSource",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::enma-build-source-603013471251/enma-backend.zip"
    },
    {
      "Sid": "RunCodeBuild",
      "Effect": "Allow",
      "Action": ["codebuild:StartBuild", "codebuild:BatchGetBuilds"],
      "Resource": "arn:aws:codebuild:ap-south-1:603013471251:project/enma-backend-build"
    },
    {
      "Sid": "ReadEcrDigest",
      "Effect": "Allow",
      "Action": "ecr:DescribeImages",
      "Resource": "arn:aws:ecr:ap-south-1:603013471251:repository/enma-backend"
    },
    {
      "Sid": "RegisterTaskDef",
      "Effect": "Allow",
      "Action": ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"],
      "Resource": "*"
    },
    {
      "Sid": "UpdateService",
      "Effect": "Allow",
      "Action": ["ecs:UpdateService", "ecs:DescribeServices", "ecs:ListTasks", "ecs:DescribeTasks"],
      "Resource": "*"
    },
    {
      "Sid": "PassEcsRoles",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::603013471251:role/enma-*",
      "Condition": { "StringEquals": { "iam:PassedToService": "ecs-tasks.amazonaws.com" } }
    }
  ]
}
```

> `iam:PassRole` must cover your task **execution role** and **task role**
> (the ones on the current `enma-backend` task def). If they aren't named
> `enma-*`, change the `Resource` to their exact ARNs — otherwise
> `RegisterTaskDefinition` will be denied.

## 3. Give GitHub the role ARN

Repo → **Settings → Secrets and variables → Actions → Variables** → New
variable:
- Name: `AWS_DEPLOY_ROLE_ARN`
- Value: the ARN of the role from step 2
  (`arn:aws:iam::603013471251:role/enma-github-deploy`)

## 4. (Optional) Manual approval gate

The workflow's job uses `environment: production`. To require a click before
each deploy: repo → **Settings → Environments → production → Required
reviewers** → add yourself. With no reviewers configured, deploys run
automatically. To drop the gate entirely, remove the `environment: production`
line from the workflow.

## 5. Test

Push a trivial change under `enma-backend/`, or use the Actions tab → *Deploy
backend to ECS* → **Run workflow**. Watch the job; on success it prints the
new image digest, task-def ARN, and the running task — same output as running
`scripts/deploy.py` by hand.

---

**Note:** deploys still **preserve existing task-def env vars** (the script
clones the current task def and only swaps the image). So this does not change
your `*_MODEL_NAME` overrides — manage those separately in the task def.
