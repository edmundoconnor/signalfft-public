variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "billing_mode" {
  description = "DynamoDB billing mode"
  type        = string
  default     = "PAY_PER_REQUEST"
}

variable "read_capacity" {
  description = "DynamoDB read capacity (PROVISIONED only)"
  type        = number
  default     = 5
}

variable "write_capacity" {
  description = "DynamoDB write capacity (PROVISIONED only)"
  type        = number
  default     = 5
}

variable "ttl_enabled" {
  description = "Enable TTL on waves table"
  type        = bool
  default     = true
}

variable "nat_gateway_count" {
  description = "Number of NAT gateways (0 = no NAT, ECS uses public subnets)"
  type        = number
  default     = 0
}

variable "collector_schedule_rate" {
  description = "EventBridge schedule rate for data collectors"
  type        = string
  default     = "rate(5 minutes)"
}

variable "account_id" {
  description = "AWS account ID"
  type        = string
}

variable "edgar_user_agent" {
  description = "SEC EDGAR user agent. Set a contact per SEC guidance for real deployments."
  type        = string
  default     = "SignalFFT public-example"
}

variable "bluesky_handle" {
  description = "Optional Bluesky handle used by the Bluesky collector"
  type        = string
  default     = ""
}

variable "dashboard_cognito_user_pool_id" {
  description = "Cognito user pool ID required by the dashboard API"
  type        = string
  default     = ""
}

variable "dashboard_cognito_client_id" {
  description = "Cognito app client ID required by the dashboard API"
  type        = string
  default     = ""
}

variable "dashboard_allowed_origins" {
  description = "Allowed browser origins for dashboard API CORS"
  type        = list(string)
  default     = []
}

variable "dashboard_auth_required" {
  description = "Require Cognito bearer tokens for dashboard API routes"
  type        = bool
  default     = true
}

variable "alert_email" {
  description = "Email address for pipeline alerts (leave empty to skip subscription)"
  type        = string
  default     = ""
}
