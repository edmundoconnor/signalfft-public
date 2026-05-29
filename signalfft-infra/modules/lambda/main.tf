variable "environment" {
  type = string
}

variable "intelligence_role_arn" {
  type = string
}

variable "s3_bucket_name" {
  type = string
}

variable "events_table_name" {
  type = string
}

variable "raw_events_queue_url" {
  type = string
}

variable "collector_schedule_rule_arn" {
  type = string
}

variable "edgar_user_agent" {
  type    = string
  default = "SignalFFT edmundoconnor@gmail.com"
}

variable "edgar_lookback_days" {
  type    = string
  default = "3"
}

variable "finnhub_schedule_rule_arn" {
  type    = string
  default = ""
}

variable "bluesky_schedule_rule_arn" {
  type    = string
  default = ""
}

variable "outcomes_table_name" {
  type = string
}

variable "outcome_schedule_rule_arn" {
  type    = string
  default = ""
}

variable "filing_fetch_queue_url" {
  type = string
}

variable "filing_ready_queue_url" {
  type = string
}

variable "filing_fetch_queue_arn" {
  type = string
}

# Placeholder zip — actual code deployed via CI/CLI
data "archive_file" "placeholder" {
  type        = "zip"
  output_path = "${path.module}/placeholder.zip"

  source {
    content  = "# placeholder - code deployed via CI"
    filename = "placeholder.py"
  }
}

resource "aws_cloudwatch_log_group" "edgar_collector" {
  name              = "/aws/lambda/${var.environment}-signalfft-edgar-collector"
  retention_in_days = 14

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lambda_function" "edgar_collector" {
  function_name = "${var.environment}-signalfft-edgar-collector"
  runtime       = "python3.12"
  handler       = "collectors.edgar.handler.lambda_handler"
  role          = var.intelligence_role_arn
  timeout       = 120
  memory_size   = 256
  filename      = data.archive_file.placeholder.output_path

  environment {
    variables = {
      ARTIFACTS_BUCKET       = var.s3_bucket_name
      ARTIFACT_BUCKET        = var.s3_bucket_name
      EVENTS_TABLE           = var.events_table_name
      RAW_EVENTS_QUEUE_URL   = var.raw_events_queue_url
      EDGAR_USER_AGENT       = var.edgar_user_agent
      EDGAR_LOOKBACK_DAYS    = var.edgar_lookback_days
      FILING_FETCH_QUEUE_URL = var.filing_fetch_queue_url
    }
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "eventbridge-${var.environment}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.edgar_collector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = var.collector_schedule_rule_arn
}

# ---------------------------------------------------------------------------
# Finnhub News Collector
# ---------------------------------------------------------------------------

data "aws_ssm_parameter" "finnhub_api_key" {
  name = "/signalfft/${var.environment}/finnhub-api-key"
}

resource "aws_cloudwatch_log_group" "finnhub_collector" {
  name              = "/aws/lambda/${var.environment}-signalfft-finnhub-news-collector"
  retention_in_days = 14

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lambda_function" "finnhub_collector" {
  function_name = "${var.environment}-signalfft-finnhub-news-collector"
  runtime       = "python3.12"
  handler       = "collectors.finnhub_news.handler.lambda_handler"
  role          = var.intelligence_role_arn
  timeout       = 120
  memory_size   = 256
  filename      = data.archive_file.placeholder.output_path

  environment {
    variables = {
      ARTIFACT_BUCKET      = var.s3_bucket_name
      ARTIFACTS_BUCKET     = var.s3_bucket_name
      EVENTS_TABLE         = var.events_table_name
      RAW_EVENTS_QUEUE_URL = var.raw_events_queue_url
      FINNHUB_API_KEY      = data.aws_ssm_parameter.finnhub_api_key.value
    }
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lambda_permission" "finnhub_eventbridge" {
  statement_id  = "eventbridge-finnhub-${var.environment}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.finnhub_collector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = var.finnhub_schedule_rule_arn
}

# ---------------------------------------------------------------------------
# Bluesky Social Collector
# ---------------------------------------------------------------------------

data "aws_ssm_parameter" "bluesky_app_password" {
  name = "/signalfft/${var.environment}/bluesky-app-password"
}

resource "aws_cloudwatch_log_group" "bluesky_collector" {
  name              = "/aws/lambda/${var.environment}-signalfft-bluesky-collector"
  retention_in_days = 14

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lambda_function" "bluesky_collector" {
  function_name = "${var.environment}-signalfft-bluesky-collector"
  runtime       = "python3.12"
  handler       = "collectors.bluesky.handler.lambda_handler"
  role          = var.intelligence_role_arn
  timeout       = 120
  memory_size   = 256
  filename      = data.archive_file.placeholder.output_path

  environment {
    variables = {
      ARTIFACT_BUCKET      = var.s3_bucket_name
      ARTIFACTS_BUCKET     = var.s3_bucket_name
      EVENTS_TABLE         = var.events_table_name
      RAW_EVENTS_QUEUE_URL = var.raw_events_queue_url
      BLUESKY_HANDLE       = "lebeaurulesall.bsky.social"
      BLUESKY_APP_PASSWORD = data.aws_ssm_parameter.bluesky_app_password.value
    }
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lambda_permission" "bluesky_eventbridge" {
  statement_id  = "eventbridge-bluesky-${var.environment}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bluesky_collector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = var.bluesky_schedule_rule_arn
}

# ---------------------------------------------------------------------------
# Outcome Price Collector
# ---------------------------------------------------------------------------

data "aws_ssm_parameter" "alpaca_api_key" {
  name = "/signalfft/${var.environment}/alpaca-api-key"
}

data "aws_ssm_parameter" "alpaca_secret_key" {
  name = "/signalfft/${var.environment}/alpaca-secret-key"
}

resource "aws_cloudwatch_log_group" "outcome_collector" {
  name              = "/aws/lambda/${var.environment}-signalfft-outcome-collector"
  retention_in_days = 14

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lambda_function" "outcome_collector" {
  function_name = "${var.environment}-signalfft-outcome-collector"
  runtime       = "python3.12"
  handler       = "collectors.outcome.handler.lambda_handler"
  role          = var.intelligence_role_arn
  timeout       = 300
  memory_size   = 256
  filename      = data.archive_file.placeholder.output_path

  environment {
    variables = {
      OUTCOMES_TABLE     = var.outcomes_table_name
      ENVIRONMENT        = var.environment
      AWS_REGION_NAME    = "us-east-1"
      ALPACA_API_KEY     = data.aws_ssm_parameter.alpaca_api_key.value
      ALPACA_SECRET_KEY  = data.aws_ssm_parameter.alpaca_secret_key.value
      OUTCOME_SCAN_LIMIT = "100"
    }
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lambda_permission" "outcome_eventbridge" {
  statement_id  = "eventbridge-outcome-${var.environment}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.outcome_collector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = var.outcome_schedule_rule_arn
}

# ---------------------------------------------------------------------------
# Filing Fetcher (Task 03)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "filing_fetcher" {
  name              = "/aws/lambda/${var.environment}-signalfft-filing-fetcher"
  retention_in_days = 14

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lambda_function" "filing_fetcher" {
  function_name = "${var.environment}-signalfft-filing-fetcher"
  runtime       = "python3.12"
  handler       = "collectors.filing_fetch.handler.lambda_handler"
  role          = var.intelligence_role_arn
  timeout       = 300
  memory_size   = 512
  filename      = data.archive_file.placeholder.output_path

  environment {
    variables = {
      ENVIRONMENT            = var.environment
      AWS_REGION_NAME        = "us-east-1"
      ARTIFACTS_BUCKET       = var.s3_bucket_name
      ARTIFACT_BUCKET        = var.s3_bucket_name
      EVENTS_TABLE           = var.events_table_name
      FILING_READY_QUEUE_URL = var.filing_ready_queue_url
      EDGAR_USER_AGENT       = var.edgar_user_agent
    }
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lambda_event_source_mapping" "filing_fetcher" {
  event_source_arn = var.filing_fetch_queue_arn
  function_name    = aws_lambda_function.filing_fetcher.arn
  batch_size       = 1
  enabled          = true
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "edgar_collector_arn" {
  value = aws_lambda_function.edgar_collector.arn
}

output "edgar_collector_function_name" {
  value = aws_lambda_function.edgar_collector.function_name
}

output "finnhub_collector_arn" {
  value = aws_lambda_function.finnhub_collector.arn
}

output "finnhub_collector_function_name" {
  value = aws_lambda_function.finnhub_collector.function_name
}

output "bluesky_collector_arn" {
  value = aws_lambda_function.bluesky_collector.arn
}

output "bluesky_collector_function_name" {
  value = aws_lambda_function.bluesky_collector.function_name
}

output "outcome_collector_arn" {
  value = aws_lambda_function.outcome_collector.arn
}

output "outcome_collector_function_name" {
  value = aws_lambda_function.outcome_collector.function_name
}

output "filing_fetcher_arn" {
  value = aws_lambda_function.filing_fetcher.arn
}

output "filing_fetcher_function_name" {
  value = aws_lambda_function.filing_fetcher.function_name
}
