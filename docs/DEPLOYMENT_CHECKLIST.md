# Deployment Checklist

Use this checklist before deploying from the public repository.

## Required Configuration

- Set `account_id` in the selected Terraform tfvars file or through `TF_VAR_account_id`.
- Set `edgar_user_agent` to a deployment-specific SEC user agent with an appropriate contact.
- Set `dashboard_cognito_user_pool_id` and `dashboard_cognito_client_id`.
- Set `dashboard_allowed_origins` to the exact production dashboard origins.
- Keep `dashboard_auth_required = true` outside local development.
- Set `bluesky_handle` only if the Bluesky collector is enabled.

## Required SSM Parameters

Create these SecureString parameters before deploying ECS or Lambda runtime workloads:

- `/signalfft/<environment>/alpaca-api-key`
- `/signalfft/<environment>/alpaca-secret-key`
- `/signalfft/<environment>/anthropic-api-key`
- `/signalfft/<environment>/finnhub-api-key`
- `/signalfft/<environment>/bluesky-app-password`

## Dashboard Build Variables

Set these when building the dashboard bundle:

- `VITE_COGNITO_USER_POOL_ID`
- `VITE_COGNITO_CLIENT_ID`
- `VITE_AUTH_REQUIRED=true`
- `VITE_API_BASE=/api`

## Validation Gates

- Run Python tests for each package from its package directory.
- Run `npm install --no-audit --no-fund` and `npm run build` in `signalfft-dashboard`.
- Run `terraform fmt -recursive`, `terraform validate`, and a reviewed `terraform plan`.
- Confirm the PR passes Test, Secret Scan, CodeQL, and Dependabot configuration checks.
- Confirm GitHub secret scanning and push protection are enabled on the repository.

## Public Sharing Notes

- Do not copy private `.git` history into this repository.
- Do not commit `.env`, Terraform state, credentials, certificates, keys, or generated archives.
- Keep real AWS account IDs, personal email addresses, handles, and production endpoints in deployment configuration, not in public source defaults.
