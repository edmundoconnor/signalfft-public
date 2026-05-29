###############################################################################
# SignalFFT DynamoDB Tables
# Each table uses PK (S) as hash key and SK (S) as range key.
# Table names follow the pattern: ${var.environment}-signalfft-{table_name}
# All tables use on-demand billing (PAY_PER_REQUEST).
###############################################################################

locals {
  common_tags = merge(var.tags, {
    environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# 1. entities
# GSI: entity_type-index (PK: entity_type S, SK: name S)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "entities" {
  name         = "${var.environment}-signalfft-entities"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "entity_type"
    type = "S"
  }

  attribute {
    name = "name"
    type = "S"
  }

  global_secondary_index {
    name            = "entity_type-index"
    hash_key        = "entity_type"
    range_key       = "name"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 2. events
# GSI: source-time-index (PK: source S, SK: created_at S)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "events" {
  name         = "${var.environment}-signalfft-events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "source"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name            = "source-time-index"
    hash_key        = "source"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 3. features
# GSI: entity-time-index (PK: entity_id S, SK: created_at S)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "features" {
  name         = "${var.environment}-signalfft-features"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "entity_id"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name            = "entity-time-index"
    hash_key        = "entity_id"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 4. signals
# GSI: score-index (PK: created_at S, SK: score N)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "signals" {
  name         = "${var.environment}-signalfft-signals"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  attribute {
    name = "score"
    type = "N"
  }

  global_secondary_index {
    name            = "score-index"
    hash_key        = "created_at"
    range_key       = "score"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 5. waves
# No GSI. TTL on "ttl" attribute.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "waves" {
  name         = "${var.environment}-signalfft-waves"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  dynamic "ttl" {
    for_each = var.ttl_enabled ? [1] : []
    content {
      attribute_name = "ttl"
      enabled        = true
    }
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 6. narratives
# GSI: lifecycle-index (PK: lifecycle_state S, SK: gravity_score N)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "narratives" {
  name         = "${var.environment}-signalfft-narratives"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "lifecycle_state"
    type = "S"
  }

  attribute {
    name = "gravity_score"
    type = "N"
  }

  global_secondary_index {
    name            = "lifecycle-index"
    hash_key        = "lifecycle_state"
    range_key       = "gravity_score"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 7. attention_field
# No GSI.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "attention_field" {
  name         = "${var.environment}-signalfft-attention-field"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 8. trade_candidates
# No GSI.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "trade_candidates" {
  name         = "${var.environment}-signalfft-trade-candidates"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 9. execution_telemetry
# No GSI. Execution-plane audit trail.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "execution_telemetry" {
  name         = "${var.environment}-signalfft-execution-telemetry"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 10. outcomes
# No GSI. Outcome tracking for signal P&L measurement.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "outcomes" {
  name         = "${var.environment}-signalfft-outcomes"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 11. shadow_scores
# No GSI. Shadow mode scoring for AI edge validation.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "shadow_scores" {
  name         = "${var.environment}-signalfft-shadow-scores"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 13. semantic_deltas
# GSI: severity-index (PK: entity_id S, SK: composite_score N)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "semantic_deltas" {
  name         = "${var.environment}-signalfft-semantic-deltas"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "entity_id"
    type = "S"
  }

  attribute {
    name = "composite_score"
    type = "N"
  }

  global_secondary_index {
    name            = "severity-index"
    hash_key        = "entity_id"
    range_key       = "composite_score"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 14. graph_edges
# GSI: reverse-lookup (PK: target_pk S, SK: reverse_sk S)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "graph_edges" {
  name         = "${var.environment}-signalfft-graph-edges"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "target_pk"
    type = "S"
  }

  attribute {
    name = "reverse_sk"
    type = "S"
  }

  global_secondary_index {
    name            = "reverse-lookup"
    hash_key        = "target_pk"
    range_key       = "reverse_sk"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.common_tags
}
