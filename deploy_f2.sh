#!/usr/bin/env bash
#
# Deploy F2.4: Wire Directionality into Live Pipeline
# Rebuilds 3 Docker images and force-deploys 5 ECS services.
#
set -euo pipefail

REGION="us-east-1"
ACCOUNT="${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID before running deploy_f2.sh}"
ECR_BASE="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
CLUSTER="prod-signalfft"
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
COMMIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
step "Step 1/8: ECR login"
# ---------------------------------------------------------------------------
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR_BASE"

# ---------------------------------------------------------------------------
step "Step 2/8: Build wheels (common + execution)"
# ---------------------------------------------------------------------------
(cd "$REPO_ROOT/signalfft-common"    && python3 -m build --wheel -q)
(cd "$REPO_ROOT/signalfft-execution" && python3 -m build --wheel -q)

# ---------------------------------------------------------------------------
step "Step 3/8: Build + push intelligence-pipeline image"
# ---------------------------------------------------------------------------
IMG_PIPELINE="${ECR_BASE}/prod-signalfft-intelligence-pipeline"

cp "$REPO_ROOT"/signalfft-common/dist/*.whl "$REPO_ROOT/signalfft-engine/"
cp -r "$REPO_ROOT/signalfft-opus/prompts" "$REPO_ROOT/signalfft-engine/prompts"
cp -r "$REPO_ROOT/signalfft-opus/config" "$REPO_ROOT/signalfft-engine/config"
docker build -t "${IMG_PIPELINE}:latest" -t "${IMG_PIPELINE}:${COMMIT_SHA}" \
  "$REPO_ROOT/signalfft-engine"
docker push "${IMG_PIPELINE}" --all-tags
rm -f "$REPO_ROOT"/signalfft-engine/*.whl
rm -rf "$REPO_ROOT/signalfft-engine/prompts"
rm -rf "$REPO_ROOT/signalfft-engine/config"

# ---------------------------------------------------------------------------
step "Step 4/8: Build + push risk-gateway image"
# ---------------------------------------------------------------------------
IMG_RISKGW="${ECR_BASE}/prod-signalfft-risk-gateway"

cp "$REPO_ROOT"/signalfft-common/dist/*.whl "$REPO_ROOT/signalfft-risk-gateway/"
docker build -t "${IMG_RISKGW}:latest" -t "${IMG_RISKGW}:${COMMIT_SHA}" \
  "$REPO_ROOT/signalfft-risk-gateway"
docker push "${IMG_RISKGW}" --all-tags
# leave common wheel in place — needed for next build too

# ---------------------------------------------------------------------------
step "Step 5/8: Build + push decision-execution image"
# ---------------------------------------------------------------------------
IMG_EXEC="${ECR_BASE}/prod-signalfft-decision-execution"

cp "$REPO_ROOT"/signalfft-execution/dist/*.whl "$REPO_ROOT/signalfft-risk-gateway/"
docker build -t "${IMG_EXEC}:latest" -t "${IMG_EXEC}:${COMMIT_SHA}" \
  "$REPO_ROOT/signalfft-risk-gateway"
docker push "${IMG_EXEC}" --all-tags
rm -f "$REPO_ROOT"/signalfft-risk-gateway/*.whl

# ---------------------------------------------------------------------------
step "Step 6/8: Force-deploy all 5 ECS services"
# ---------------------------------------------------------------------------
SERVICES=(
  prod-signalfft-intelligence-pipeline
  prod-signalfft-dashboard
  prod-signalfft-risk-gateway
  prod-signalfft-decision-execution
  prod-signalfft-execution-router
)

for svc in "${SERVICES[@]}"; do
  printf '  Deploying %s ...\n' "$svc"
  aws ecs update-service \
    --cluster "$CLUSTER" \
    --service "$svc" \
    --force-new-deployment \
    --query 'service.serviceName' \
    --output text \
    --region "$REGION"
done

# ---------------------------------------------------------------------------
step "Step 7/8: Clean up build artifacts"
# ---------------------------------------------------------------------------
rm -f "$REPO_ROOT"/signalfft-common/dist/*.whl
rm -f "$REPO_ROOT"/signalfft-execution/dist/*.whl

# ---------------------------------------------------------------------------
step "Step 8/8: Verify ECS deployments reaching steady state"
# ---------------------------------------------------------------------------
printf 'Waiting for services to stabilize (up to 10 min)...\n'
aws ecs wait services-stable \
  --cluster "$CLUSTER" \
  --services "${SERVICES[@]}" \
  --region "$REGION" \
  && printf '\033[1;32mAll 5 services reached steady state.\033[0m\n' \
  || printf '\033[1;31mTimeout — check AWS console for service status.\033[0m\n'

printf '\nDone. Images tagged: latest + %s\n' "$COMMIT_SHA"
printf 'Tail logs with:\n'
printf '  aws logs tail /ecs/prod-signalfft/intelligence-pipeline --follow --region %s\n' "$REGION"
printf '  aws logs tail /ecs/prod-signalfft/risk-gateway --follow --region %s\n' "$REGION"
