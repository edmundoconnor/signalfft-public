###############################################################################
# CloudWatch Operations Dashboard
# Single-pane-of-glass for SignalFFT pipeline health.
# Uses only native AWS metrics — no custom metric filters.
###############################################################################

locals {
  cluster   = var.ecs_cluster_name
  prefix    = "${var.environment}-signalfft"

  ecs_services = [
    "intelligence-pipeline",
    "decision-execution",
    "risk-gateway",
    "execution-router",
    "dashboard",
  ]

  queues = [
    "raw-events",
    "features",
    "signals",
    "waves",
    "risk-input",
    "candidates",
  ]

  key_tables = [
    "signals",
    "trade-candidates",
    "graph-edges",
  ]

  all_tables = [
    "entities",
    "events",
    "features",
    "signals",
    "waves",
    "narratives",
    "attention-field",
    "trade-candidates",
    "graph-edges",
    "execution-telemetry",
  ]

  lambda_collectors = [
    "${local.prefix}-edgar-collector",
    "${local.prefix}-finnhub-news-collector",
    "${local.prefix}-bluesky-collector",
  ]
}

resource "aws_cloudwatch_dashboard" "operations" {
  dashboard_name = "${local.prefix}-operations"

  dashboard_body = jsonencode({
    widgets = concat(
      # -----------------------------------------------------------------------
      # ROW 1 — ECS Services (y=0)
      # -----------------------------------------------------------------------
      [
        {
          type   = "metric"
          x      = 0
          y      = 0
          width  = 12
          height = 6
          properties = {
            title   = "ECS CPU Utilization (%)"
            view    = "timeSeries"
            stacked = true
            region  = var.region
            period  = 300
            stat    = "Average"
            metrics = [
              for svc in local.ecs_services : [
                "AWS/ECS", "CPUUtilization",
                "ClusterName", local.cluster,
                "ServiceName", "${local.prefix}-${svc}",
              ]
            ]
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 0
          width  = 12
          height = 6
          properties = {
            title   = "ECS Memory Utilization (%)"
            view    = "timeSeries"
            stacked = true
            region  = var.region
            period  = 300
            stat    = "Average"
            metrics = [
              for svc in local.ecs_services : [
                "AWS/ECS", "MemoryUtilization",
                "ClusterName", local.cluster,
                "ServiceName", "${local.prefix}-${svc}",
              ]
            ]
          }
        },
      ],

      # -----------------------------------------------------------------------
      # ROW 2 — Queue Health (y=6)
      # -----------------------------------------------------------------------
      [
        {
          type   = "metric"
          x      = 0
          y      = 6
          width  = 12
          height = 6
          properties = {
            title   = "Queue Depths (main)"
            view    = "timeSeries"
            stacked = false
            region  = var.region
            period  = 300
            stat    = "Average"
            metrics = [
              for q in local.queues : [
                "AWS/SQS", "ApproximateNumberOfMessagesVisible",
                "QueueName", "${local.prefix}-${q}",
              ]
            ]
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 6
          width  = 12
          height = 6
          properties = {
            title   = "DLQ Depths (should be 0)"
            view    = "timeSeries"
            stacked = false
            region  = var.region
            period  = 300
            stat    = "Average"
            yAxis   = { left = { min = 0 } }
            metrics = [
              for q in local.queues : [
                "AWS/SQS", "ApproximateNumberOfMessagesVisible",
                "QueueName", "${local.prefix}-${q}-dlq",
              ]
            ]
            annotations = {
              horizontal = [
                {
                  label = "Alert threshold"
                  value = 1
                  color = "#d13212"
                }
              ]
            }
          }
        },
      ],

      # -----------------------------------------------------------------------
      # ROW 3 — Collector Lambdas (y=12)
      # -----------------------------------------------------------------------
      [
        {
          type   = "metric"
          x      = 0
          y      = 12
          width  = 8
          height = 6
          properties = {
            title   = "Collector Invocations"
            view    = "timeSeries"
            stacked = false
            region  = var.region
            period  = 300
            stat    = "Sum"
            metrics = [
              for fn in local.lambda_collectors : [
                "AWS/Lambda", "Invocations", "FunctionName", fn,
              ]
            ]
          }
        },
        {
          type   = "metric"
          x      = 8
          y      = 12
          width  = 8
          height = 6
          properties = {
            title   = "Collector Errors"
            view    = "timeSeries"
            stacked = false
            region  = var.region
            period  = 300
            stat    = "Sum"
            metrics = [
              for fn in local.lambda_collectors : [
                "AWS/Lambda", "Errors", "FunctionName", fn,
              ]
            ]
            annotations = {
              horizontal = [
                {
                  label = "Error threshold"
                  value = 1
                  color = "#d13212"
                }
              ]
            }
          }
        },
        {
          type   = "metric"
          x      = 16
          y      = 12
          width  = 8
          height = 6
          properties = {
            title   = "Collector Duration (ms)"
            view    = "timeSeries"
            stacked = false
            region  = var.region
            period  = 300
            metrics = [
              for fn in local.lambda_collectors : [
                "AWS/Lambda", "Duration", "FunctionName", fn, { stat = "Average" },
              ]
            ]
          }
        },
      ],

      # -----------------------------------------------------------------------
      # ROW 4 — DynamoDB (y=18)
      # -----------------------------------------------------------------------
      [
        {
          type   = "metric"
          x      = 0
          y      = 18
          width  = 12
          height = 6
          properties = {
            title   = "DynamoDB Read/Write Capacity (key tables)"
            view    = "timeSeries"
            stacked = false
            region  = var.region
            period  = 300
            stat    = "Sum"
            metrics = concat(
              [for t in local.key_tables : ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", "${local.prefix}-${t}", { label = "${t} reads" }]],
              [for t in local.key_tables : ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", "${local.prefix}-${t}", { label = "${t} writes" }]],
            )
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 18
          width  = 12
          height = 6
          properties = {
            title   = "DynamoDB Throttled Requests (should be 0)"
            view    = "timeSeries"
            stacked = true
            region  = var.region
            period  = 300
            stat    = "Sum"
            yAxis   = { left = { min = 0 } }
            metrics = [
              for t in local.all_tables : [
                "AWS/DynamoDB", "ThrottledRequests",
                "TableName", "${local.prefix}-${t}",
              ]
            ]
          }
        },
      ],

      # -----------------------------------------------------------------------
      # ROW 5 — Cost (y=24)
      # -----------------------------------------------------------------------
      [
        {
          type   = "text"
          x      = 0
          y      = 24
          width  = 24
          height = 3
          properties = {
            markdown = <<-EOT
              ## Cost Management
              **[View detailed costs in Cost Explorer](https://us-east-1.console.aws.amazon.com/cost-management/home#/dashboard)**

              ECS Fargate is the primary cost driver. Use `signalfft-ecs-toggle.sh` to scale down when not actively working.
            EOT
          }
        },
      ],
    )
  })
}
