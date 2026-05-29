# SignalFFT Operations Runbook

## System Overview

### Architecture

```
                          SignalFFT 8-Stage Pipeline
                          =========================

  EDGAR RSS                                                        Memory Graph
     |                                                              feedback
     v                                                                 |
  [Lambda Collector] --> S3 + DynamoDB events                          |
     |                                                                 |
     v  (raw-events queue)                                             |
  [Feature Extraction] --> DynamoDB features                           |
     |                                                                 |
     v  (features queue)                                               |
  [Signal Scoring + Attention Field] --> DynamoDB signals, attention    |
     |                                                                 |
     v  (signals queue)                                                |
  [Wave Engine] --> DynamoDB waves                                     |
     |                                                                 |
     v  (waves queue)                                                  |
  [Narrative Gravity] --> DynamoDB narratives                          |
     |                                                                 |
     v  (risk-input queue)                                             |
  [Risk Gateway] --> DynamoDB trade-candidates                         |
     |                                                                 |
     v  (candidates queue)                                             |
  [Execution Router (Paper Trade)] --> DynamoDB execution-telemetry    |
     |                                                                 |
     +---> [Outcome Feedback] --> DynamoDB graph-edges ----------------+

  IAM Planes:
    Intelligence: Collector, Feature, Signal, Wave, Narrative, Attention, Dashboard
    Decision:     Risk Gateway, Decision-Execution
    Execution:    Execution Router
```

### ECS Services

| Service | ECS Name | Image | IAM Role | Fargate Resources |
|---------|----------|-------|----------|-------------------|
| Intelligence Pipeline | `prod-signalfft-intelligence-pipeline` | `prod-signalfft-intelligence-pipeline:latest` | `role-intelligence` | 256 CPU / 512 MB |
| Risk Gateway | `prod-signalfft-risk-gateway` | `prod-signalfft-risk-gateway:latest` | `role-decision` | 256 CPU / 512 MB |
| Decision Execution | `prod-signalfft-decision-execution` | `prod-signalfft-decision-execution:latest` | `role-decision` | 256 CPU / 512 MB |
| Execution Router | `prod-signalfft-execution-router` | `prod-signalfft-decision-execution:latest` | `role-execution` | 256 CPU / 512 MB |
| Dashboard | `prod-signalfft-dashboard` | `prod-signalfft-intelligence-pipeline:latest` | `role-intelligence` | 256 CPU / 512 MB |

### Lambda Functions

| Function | Schedule | Purpose |
|----------|----------|---------|
| `prod-signalfft-edgar-collector` | EventBridge (cron) | Polls SEC EDGAR RSS, stores raw filings in S3, emits events |

### DynamoDB Tables

All tables use single-table design with `PK` (HASH) / `SK` (RANGE) key schema.

| Table | Purpose |
|-------|---------|
| `prod-signalfft-events` | Raw event metadata from collectors |
| `prod-signalfft-entities` | Entity registry (companies, tickers) |
| `prod-signalfft-features` | Extracted features (sentiment, mentions, dates) |
| `prod-signalfft-signals` | Scored signals from feature aggregation |
| `prod-signalfft-waves` | Wave patterns across signals |
| `prod-signalfft-narratives` | Narrative gravity scores |
| `prod-signalfft-attention-field` | Attention field state for entities |
| `prod-signalfft-trade-candidates` | Candidates that passed risk rules |
| `prod-signalfft-graph-edges` | Memory graph edges (outcome feedback) |
| `prod-signalfft-execution-telemetry` | Paper trade execution records |

### SQS Queues

Each queue has a corresponding `-dlq` dead-letter queue.

| Queue | Producer | Consumer |
|-------|----------|----------|
| `prod-signalfft-raw-events` | Lambda Collector | Feature Extraction |
| `prod-signalfft-features` | Feature Extraction | Signal Scoring |
| `prod-signalfft-signals` | Signal Scoring | Wave Engine |
| `prod-signalfft-waves` | Wave Engine | Narrative Gravity |
| `prod-signalfft-risk-input` | Intelligence Pipeline | Risk Gateway |
| `prod-signalfft-candidates` | Risk Gateway | Execution Router |

### ECR Repositories

| Repository | Built from | Used by |
|------------|-----------|---------|
| `prod-signalfft-intelligence-pipeline` | `signalfft-engine/` | intelligence-pipeline, dashboard |
| `prod-signalfft-risk-gateway` | `signalfft-risk-gateway/` | risk-gateway |
| `prod-signalfft-decision-execution` | `signalfft-risk-gateway/` + `signalfft-execution/` wheel | decision-execution, execution-router |

---

## Daily Operations

### Starting the System

Scale all ECS services to 1:

```bash
CLUSTER=prod-signalfft
for SVC in intelligence-pipeline risk-gateway decision-execution execution-router dashboard; do
  aws ecs update-service --cluster $CLUSTER --service prod-signalfft-$SVC \
    --desired-count 1 --no-cli-pager
done
```

### Stopping the System

Scale all ECS services to 0:

```bash
CLUSTER=prod-signalfft
for SVC in intelligence-pipeline risk-gateway decision-execution execution-router dashboard; do
  aws ecs update-service --cluster $CLUSTER --service prod-signalfft-$SVC \
    --desired-count 0 --no-cli-pager
done
```

### Checking System Health

**CloudWatch Dashboard:**
https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards/dashboard/prod-signalfft-operations

**Quick CLI health check:**

```bash
# ECS service status
aws ecs describe-services --cluster prod-signalfft \
  --services prod-signalfft-intelligence-pipeline prod-signalfft-risk-gateway \
            prod-signalfft-decision-execution prod-signalfft-execution-router \
            prod-signalfft-dashboard \
  --no-cli-pager \
  --query 'services[].{name:serviceName,running:runningCount,desired:desiredCount,status:status}' \
  --output table

# Queue depths (all queues)
for Q in raw-events features signals waves risk-input candidates; do
  DEPTH=$(aws sqs get-queue-attributes \
    --queue-url https://sqs.us-east-1.amazonaws.com/<aws-account-id>/prod-signalfft-$Q \
    --attribute-names ApproximateNumberOfMessages \
    --query 'Attributes.ApproximateNumberOfMessages' --output text --no-cli-pager)
  DLQ_DEPTH=$(aws sqs get-queue-attributes \
    --queue-url https://sqs.us-east-1.amazonaws.com/<aws-account-id>/prod-signalfft-$Q-dlq \
    --attribute-names ApproximateNumberOfMessages \
    --query 'Attributes.ApproximateNumberOfMessages' --output text --no-cli-pager)
  printf "%-15s queue=%s  dlq=%s\n" "$Q" "$DEPTH" "$DLQ_DEPTH"
done

# DynamoDB table record counts
for TABLE in events entities features signals waves narratives attention-field trade-candidates graph-edges execution-telemetry; do
  COUNT=$(aws dynamodb scan --table-name prod-signalfft-$TABLE --select COUNT \
    --no-cli-pager --query 'Count' --output text 2>/dev/null || echo "N/A")
  printf "%-25s %s records\n" "$TABLE" "$COUNT"
done
```

---

## Deploying Changes

### Code Change -> Deploy (CI/CD)

1. Create a branch from `main`
2. Make changes, commit, push
3. Open PR -> test workflow runs automatically (`test.yml`)
4. Merge to `main` -> deploy workflow triggers (`deploy.yml`):
   - Detects which packages changed (engine, risk-gateway, execution, common)
   - Builds Docker images and pushes to ECR (tagged `latest` + git SHA)
   - Forces ECS redeployment of affected services
5. Verify: check CloudWatch logs for the updated service

```bash
aws logs tail /ecs/prod-signalfft/<service-name> --since 5m --follow
```

### Manual Deploy (if CI/CD is down)

**Build and deploy intelligence-pipeline:**

```bash
# Build common wheel
cd signalfft-common && pip install build && python -m build && cd ..

# Build and push Docker image
cd signalfft-engine
cp ../signalfft-common/dist/*.whl .
ECR_REPO="<aws-account-id>.dkr.ecr.us-east-1.amazonaws.com/prod-signalfft-intelligence-pipeline"
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <aws-account-id>.dkr.ecr.us-east-1.amazonaws.com
docker build -t ${ECR_REPO}:latest .
docker push ${ECR_REPO}:latest

# Force ECS redeploy
aws ecs update-service --cluster prod-signalfft \
  --service prod-signalfft-intelligence-pipeline --force-new-deployment --no-cli-pager
aws ecs update-service --cluster prod-signalfft \
  --service prod-signalfft-dashboard --force-new-deployment --no-cli-pager
```

**Build and deploy risk-gateway + decision-execution:**

```bash
cd signalfft-common && pip install build && python -m build && cd ..
cd signalfft-execution && python -m build && cd ..

# Risk gateway image
cd signalfft-risk-gateway
cp ../signalfft-common/dist/*.whl .
ECR_REPO="<aws-account-id>.dkr.ecr.us-east-1.amazonaws.com/prod-signalfft-risk-gateway"
docker build -t ${ECR_REPO}:latest .
docker push ${ECR_REPO}:latest

# Decision execution image (includes execution wheel)
cp ../signalfft-execution/dist/*.whl .
ECR_REPO="<aws-account-id>.dkr.ecr.us-east-1.amazonaws.com/prod-signalfft-decision-execution"
docker build -t ${ECR_REPO}:latest .
docker push ${ECR_REPO}:latest

# Force ECS redeploy
aws ecs update-service --cluster prod-signalfft \
  --service prod-signalfft-risk-gateway --force-new-deployment --no-cli-pager
aws ecs update-service --cluster prod-signalfft \
  --service prod-signalfft-decision-execution --force-new-deployment --no-cli-pager
aws ecs update-service --cluster prod-signalfft \
  --service prod-signalfft-execution-router --force-new-deployment --no-cli-pager
```

### Terraform Changes

```bash
cd signalfft-infra
terraform plan -var-file=envs/prod.tfvars
terraform apply -var-file=envs/prod.tfvars
```

> **Note:** ECS services have `lifecycle { ignore_changes = [task_definition] }`. After Terraform creates a new task definition revision, you must manually update the service:
> ```bash
> aws ecs update-service --cluster prod-signalfft \
>   --service prod-signalfft-<service> \
>   --task-definition prod-signalfft-<service>:<new-revision> \
>   --force-new-deployment --no-cli-pager
> ```

---

## Troubleshooting

### DLQ Has Messages

1. **Identify which DLQ:**
   ```bash
   for Q in raw-events features signals waves risk-input candidates; do
     DEPTH=$(aws sqs get-queue-attributes \
       --queue-url https://sqs.us-east-1.amazonaws.com/<aws-account-id>/prod-signalfft-$Q-dlq \
       --attribute-names ApproximateNumberOfMessages \
       --query 'Attributes.ApproximateNumberOfMessages' --output text --no-cli-pager)
     [ "$DEPTH" != "0" ] && echo "$Q-dlq: $DEPTH messages"
   done
   ```

2. **Sample a message:**
   ```bash
   aws sqs receive-message \
     --queue-url https://sqs.us-east-1.amazonaws.com/<aws-account-id>/prod-signalfft-<queue>-dlq \
     --max-number-of-messages 1 --no-cli-pager
   ```

3. **Common causes:**
   - Consumer service is down or crashed (check ECS running count)
   - Schema mismatch (code deployed without matching model changes)
   - IAM permissions issue (check CloudWatch logs for AccessDenied)
   - Queue URL misconfiguration (check ECS task definition env vars)

4. **After fixing root cause:**
   ```bash
   # Purge DLQ (only after confirming messages are stale)
   aws sqs purge-queue \
     --queue-url https://sqs.us-east-1.amazonaws.com/<aws-account-id>/prod-signalfft-<queue>-dlq \
     --no-cli-pager
   ```

### ECS Service Won't Start

1. **Check service events:**
   ```bash
   aws ecs describe-services --cluster prod-signalfft \
     --services prod-signalfft-<service> --no-cli-pager \
     --query 'services[0].events[:5]'
   ```

2. **Check logs:**
   ```bash
   aws logs tail /ecs/prod-signalfft/<service> --since 10m
   ```

3. **Common causes:**
   - Bad Docker image (build error, missing dependency)
   - Missing environment variable (check task definition)
   - IAM permissions (wrong task role)
   - Port conflict (dashboard on 8080)
   - Image not found in ECR (check push succeeded)

### Collector Not Running

1. **Invoke Lambda manually:**
   ```bash
   aws lambda invoke --function-name prod-signalfft-edgar-collector \
     --payload '{}' /tmp/collector-out.json --no-cli-pager
   cat /tmp/collector-out.json
   ```

2. **Check EventBridge rule:**
   ```bash
   aws events list-rules --name-prefix prod-signalfft --no-cli-pager
   ```

3. **Common causes:**
   - EventBridge rule disabled
   - Lambda timeout (SEC EDGAR rate limiting)
   - S3 bucket permissions

### Signal Scores Are Low

- **Normal range:** 0.05-0.15 for single-source EDGAR data
- Scores will increase when news/social media collectors are added (activates Velocity + Cross-Source scoring dimensions)
- Check attention field:
  ```bash
  aws dynamodb scan --table-name prod-signalfft-attention-field \
    --max-items 5 --no-cli-pager
  ```

### Too Many Rejected Candidates

- Check risk gateway parameters:
  - `MIN_SIGNAL_SCORE`: 0.05 (minimum signal score to consider)
  - `MAX_CANDIDATES_PER_WINDOW`: 10 (per 5-minute window)
- These limits are intentionally conservative for paper trading

### High AWS Bill

1. Check Cost Explorer in AWS Console
2. Verify ECS services are scaled to 0 when not actively working:
   ```bash
   aws ecs describe-services --cluster prod-signalfft \
     --services prod-signalfft-intelligence-pipeline prod-signalfft-risk-gateway \
               prod-signalfft-decision-execution prod-signalfft-execution-router \
               prod-signalfft-dashboard \
     --no-cli-pager --query 'services[].{name:serviceName,desired:desiredCount}' --output table
   ```
3. Check DynamoDB consumed capacity for unexpected spikes
4. Emergency shutdown:
   ```bash
   for SVC in intelligence-pipeline risk-gateway decision-execution execution-router dashboard; do
     aws ecs update-service --cluster prod-signalfft --service prod-signalfft-$SVC \
       --desired-count 0 --no-cli-pager
   done
   ```

---

## Architecture Reference

### Environment Variables (per service)

**intelligence-pipeline:**
| Variable | Value |
|----------|-------|
| `ENVIRONMENT` | `prod` |
| `AWS_REGION` | `us-east-1` |
| `ARTIFACTS_BUCKET` / `S3_BUCKET` | `prod-signalfft-artifacts` |
| `RAW_EVENTS_QUEUE_URL` | SQS URL for `prod-signalfft-raw-events` |
| `FEATURES_QUEUE_URL` | SQS URL for `prod-signalfft-features` |
| `SIGNALS_QUEUE_URL` | SQS URL for `prod-signalfft-signals` |
| `WAVES_QUEUE_URL` | SQS URL for `prod-signalfft-waves` |
| `RISK_INPUT_QUEUE_URL` | SQS URL for `prod-signalfft-risk-input` |
| `ENTITIES_TABLE` | `prod-signalfft-entities` |
| `EVENTS_TABLE` | `prod-signalfft-events` |
| `FEATURES_TABLE` | `prod-signalfft-features` |
| `SIGNALS_TABLE` | `prod-signalfft-signals` |
| `WAVES_TABLE` | `prod-signalfft-waves` |
| `NARRATIVES_TABLE` | `prod-signalfft-narratives` |
| `ATTENTION_FIELD_TABLE` | `prod-signalfft-attention-field` |
| `GRAPH_EDGES_TABLE` | `prod-signalfft-graph-edges` |

**risk-gateway:**
| Variable | Value |
|----------|-------|
| `INPUT_QUEUE_URL` | SQS URL for `prod-signalfft-risk-input` |
| `OUTPUT_QUEUE_URL` | SQS URL for `prod-signalfft-candidates` |
| `SIGNALS_TABLE` | `prod-signalfft-signals` |
| `TRADE_CANDIDATES_TABLE` | `prod-signalfft-trade-candidates` |
| `MIN_SIGNAL_SCORE` | `0.05` |
| `MAX_CANDIDATES_PER_WINDOW` | `10` |

**decision-execution:**
| Variable | Value |
|----------|-------|
| `RISK_INPUT_QUEUE_URL` | SQS URL for `prod-signalfft-risk-input` |
| `EXECUTION_INPUT_QUEUE_URL` | SQS URL for `prod-signalfft-candidates` |
| `CANDIDATES_QUEUE_URL` | SQS URL for `prod-signalfft-candidates` |
| `SIGNALS_TABLE` | `prod-signalfft-signals` |
| `TRADE_CANDIDATES_TABLE` | `prod-signalfft-trade-candidates` |
| `GRAPH_EDGES_TABLE` | `prod-signalfft-graph-edges` |
| `BROKER_MODE` | `paper` |

**execution-router:**
| Variable | Value |
|----------|-------|
| `INPUT_QUEUE_URL` | SQS URL for `prod-signalfft-candidates` |
| `BROKER_MODE` | `paper` |
| `EXECUTION_TELEMETRY_TABLE` | `prod-signalfft-execution-telemetry` |
| `GRAPH_EDGES_TABLE` | `prod-signalfft-graph-edges` |

**dashboard:**
| Variable | Value |
|----------|-------|
| `EVENTS_TABLE` | `prod-signalfft-events` |
| `FEATURES_TABLE` | `prod-signalfft-features` |
| `SIGNALS_TABLE` | `prod-signalfft-signals` |
| `WAVES_TABLE` | `prod-signalfft-waves` |
| `NARRATIVES_TABLE` | `prod-signalfft-narratives` |
| `ATTENTION_FIELD_TABLE` | `prod-signalfft-attention-field` |
| `TRADE_CANDIDATES_TABLE` | `prod-signalfft-trade-candidates` |
| `ENTITIES_TABLE` | `prod-signalfft-entities` |
| `GRAPH_EDGES_TABLE` | `prod-signalfft-graph-edges` |

### IAM Plane Isolation

Three IAM roles enforce hard boundaries between pipeline planes:

| Role | Plane | DynamoDB Access | SQS Access | Other |
|------|-------|-----------------|------------|-------|
| `role-intelligence` | Intelligence | Full CRUD: events, entities, features, signals, waves, narratives, attention-field, graph-edges | raw-events, features, signals, waves, risk-input | S3 R/W, Bedrock, CloudWatch |
| `role-decision` | Decision | Read-only: intelligence tables. Full CRUD: trade-candidates. Write: graph-edges | risk-input, signals (receive), candidates (send/receive) | CloudWatch |
| `role-execution` | Execution | Write: execution-telemetry, graph-edges | candidates (receive) | CloudWatch |

Each role has explicit DENY statements blocking cross-plane access for defense in depth.

### GitHub Repository

- **Monorepo:** https://github.com/edmundoconnor/signalfft
- **Packages:**
  - `signalfft-common/` — shared models, events, enums
  - `signalfft-collectors/` — Lambda collector (EDGAR)
  - `signalfft-engine/` — feature extraction, signal scoring, wave engine, narrative gravity, attention field, dashboard
  - `signalfft-risk-gateway/` — risk rules, candidate generator, provenance stamper
  - `signalfft-execution/` — execution router, paper-trade broker, telemetry recorder
  - `signalfft-dashboard/` — React dashboard frontend (optional)
  - `signalfft-infra/` — Terraform infrastructure (VPC, ECS, DynamoDB, SQS, IAM, CloudWatch)

### CI/CD

- **Test workflow** (`.github/workflows/test.yml`): runs on PR to main and push to main
- **Deploy workflow** (`.github/workflows/deploy.yml`): runs on push to main only
- **GitHub Actions IAM Role:** `signalfft-github-actions` (OIDC federated)
- **Secret required:** `AWS_DEPLOY_ROLE_ARN` in GitHub repo settings

### AWS Account

- **Account ID:** `<aws-account-id>`
- **Region:** us-east-1
- **ECS Cluster:** `prod-signalfft`
- **S3 Bucket:** `prod-signalfft-artifacts`
- **CloudWatch Dashboard:** `prod-signalfft-operations`
- **SNS Alerts Topic:** `prod-signalfft-pipeline-alerts`

### Contact

System owner
