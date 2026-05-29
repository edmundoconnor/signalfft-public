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

variable "edgar_user_agent" {
  type    = string
  default = "SignalFFT public-example"
}

variable "edgar_lookback_days" {
  type    = string
  default = "3"
}

variable "outcomes_table_name" {
  type = string
}

variable "bluesky_handle" {
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

# ---------------------------------------------------------------------------
# Finnhub News Collector
# ---------------------------------------------------------------------------

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
      FINNHUB_API_KEY_PARAM = "/signalfft/${var.environment}/finnhub-api-key"
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

# ---------------------------------------------------------------------------
# Bluesky Social Collector
# ---------------------------------------------------------------------------

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
      BLUESKY_HANDLE       = var.bluesky_handle
      BLUESKY_APP_PASSWORD_PARAM = "/signalfft/${var.environment}/bluesky-app-password"
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

# ---------------------------------------------------------------------------
# Outcome Price Collector
# ---------------------------------------------------------------------------

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
      OUTCOMES_TABLE           = var.outcomes_table_name
      ENVIRONMENT              = var.environment
      AWS_REGION_NAME          = "us-east-1"
      ALPACA_API_KEY_PARAM     = "/signalfft/${var.environment}/alpaca-api-key"
      ALPACA_SECRET_KEY_PARAM = "/signalfft/${var.environment}/alpaca-secret-key"
      OUTCOME_SCAN_LIMIT       = "100"
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
