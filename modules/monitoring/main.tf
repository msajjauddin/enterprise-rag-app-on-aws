# ================================================
# Monitoring Module for RAG System (Alarms & Dashboard)
# ================================================
# SNS alerting, alarms on the SLOs and the ingestion DLQ, and a dashboard with
# p95 latency, time-to-first-token, tokens, cost/query, cache hit and fallback rate.
# Application metrics are emitted by the query service as CloudWatch EMF.

# ========================
# Locals
# ========================

locals {
  name    = "${var.project_name}-${var.stage}"
  app_ns  = "RAGApp/${var.stage}"
  app_dim = ["Service", "query-api"]

  common_tags = {
    Project     = var.project_name
    Environment = var.stage
    ManagedBy   = "Terraform"
  }
}

# ==============================
# CloudWatch Log Groups for Lambda functions
# ==============================

resource "aws_cloudwatch_log_group" "document_processor" {
  name              = "/aws/lambda/${var.document_processor_name}"
  retention_in_days = 30

  tags = {
    Name = "${local.name}-document-processor-logs"
  }
}

resource "aws_cloudwatch_log_group" "db_init" {
  name              = "/aws/lambda/${var.db_init_name}"
  retention_in_days = 30

  tags = {
    Name = "${local.name}-db-init-logs"
  }
}

resource "aws_cloudwatch_log_group" "token_enricher" {
  name              = "/aws/lambda/${var.token_enricher_name}"
  retention_in_days = 30

  tags = {
    Name = "${local.name}-token-enricher-logs"
  }
}

# ==============================
# SNS Topic for Alerts
# ==============================

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"

  tags = {
    Name = "${local.name}-alerts"
  }
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ==============================
# CloudWatch Alarms
# ==============================

resource "aws_cloudwatch_metric_alarm" "ingestion_dlq" {
  alarm_name          = "${local.name}-ingestion-dlq-not-empty"
  alarm_description   = "Documents failed ingestion after max retries and landed in the DLQ"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    QueueName = var.ingestion_dlq_name
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "document_processor_errors" {
  alarm_name          = "${local.name}-document-processor-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_description   = "Errors in the document processor Lambda"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = var.document_processor_name
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "api_5xx_rate" {
  alarm_name          = "${local.name}-api-error-rate"
  alarm_description   = "Query API 5xx rate above the 1% SLO"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "error_rate"
    expression  = "100 * errors / MAX([requests, 1])"
    label       = "5xx %"
    return_data = true
  }

  metric_query {
    id = "errors"
    metric {
      metric_name = "HTTPCode_Target_5XX_Count"
      namespace   = "AWS/ApplicationELB"
      period      = 300
      stat        = "Sum"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
    }
  }

  metric_query {
    id = "requests"
    metric {
      metric_name = "RequestCount"
      namespace   = "AWS/ApplicationELB"
      period      = 300
      stat        = "Sum"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
    }
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "ttft_p95" {
  alarm_name          = "${local.name}-ttft-p95"
  alarm_description   = "p95 time-to-first-token above the 2 s SLO"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "TimeToFirstTokenMs"
  namespace           = local.app_ns
  period              = 300
  extended_statistic  = "p95"
  threshold           = 2000
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    Service = "query-api"
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "latency_p95" {
  alarm_name          = "${local.name}-answer-latency-p95"
  alarm_description   = "p95 full-answer latency above the 8 s SLO"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "QueryLatencyMs"
  namespace           = local.app_ns
  period              = 300
  extended_statistic  = "p95"
  threshold           = 8000
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    Service = "query-api"
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "aurora_cpu" {
  alarm_name          = "${local.name}-aurora-writer-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBInstanceIdentifier = var.db_writer_instance_id
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "aurora_replica_lag" {
  count               = var.has_db_reader ? 1 : 0
  alarm_name          = "${local.name}-aurora-replica-lag"
  alarm_description   = "Reader (standby) lagging the writer"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "AuroraReplicaLagMaximum"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 2000
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    DBClusterIdentifier = var.db_cluster_id
  }

  tags = local.common_tags
}

# ==============================
# Dashboard
# ==============================

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${local.name}-rag"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title  = "Latency p95 (ms): time-to-first-token and full answer"
          region = var.aws_region
          stat   = "p95"
          period = 300
          metrics = [
            concat([local.app_ns, "TimeToFirstTokenMs"], local.app_dim),
            concat([local.app_ns, "QueryLatencyMs"], local.app_dim)
          ]
          annotations = { horizontal = [{ value = 2000, label = "TTFT SLO" }, { value = 8000, label = "Answer SLO" }] }
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title  = "Requests and 5xx (ALB)"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.alb_arn_suffix],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", var.alb_arn_suffix]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 8, height = 6,
        properties = {
          title  = "Tokens per 5 min"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            concat([local.app_ns, "InputTokens"], local.app_dim),
            concat([local.app_ns, "OutputTokens"], local.app_dim)
          ]
        }
      },
      {
        type = "metric", x = 8, y = 6, width = 8, height = 6,
        properties = {
          title   = "Estimated model cost per query (USD)"
          region  = var.aws_region
          stat    = "Average"
          period  = 300
          metrics = [concat([local.app_ns, "EstimatedCostUSD"], local.app_dim)]
        }
      },
      {
        type = "metric", x = 16, y = 6, width = 8, height = 6,
        properties = {
          title  = "Cache hit rate and web-search fallback rate (%)"
          region = var.aws_region
          period = 300
          metrics = [
            [{ expression = "100 * hits", label = "Cache hit %", id = "hit_rate" }],
            [{ expression = "100 * fallback", label = "Web fallback %", id = "fallback_rate" }],
            concat([local.app_ns, "CacheHit"], local.app_dim, [{ id = "hits", stat = "Average", visible = false }]),
            concat([local.app_ns, "WebSearchFallback"], local.app_dim, [{ id = "fallback", stat = "Average", visible = false }])
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 8, height = 6,
        properties = {
          title  = "Online eval (sampled) and user feedback"
          region = var.aws_region
          stat   = "Average"
          period = 3600
          metrics = [
            concat([local.app_ns, "EvalFaithfulness"], local.app_dim),
            concat([local.app_ns, "EvalAnswerRelevancy"], local.app_dim),
            concat([local.app_ns, "UserFeedback"], local.app_dim)
          ]
        }
      },
      {
        type = "metric", x = 8, y = 12, width = 8, height = 6,
        properties = {
          title  = "Guardrail interventions and rate-limited requests"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            concat([local.app_ns, "GuardrailIntervened"], local.app_dim),
            concat([local.app_ns, "RateLimited"], local.app_dim)
          ]
        }
      },
      {
        type = "metric", x = 16, y = 12, width = 8, height = 6,
        properties = {
          title  = "Ingestion queue depth and DLQ"
          region = var.aws_region
          stat   = "Maximum"
          period = 300
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.ingestion_queue_name],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.ingestion_dlq_name]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 18, width = 12, height = 6,
        properties = {
          title  = "Fargate tasks and CPU"
          region = var.aws_region
          period = 300
          metrics = [
            ["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", var.ecs_cluster_name, "ServiceName", var.ecs_service_name, { stat = "Average" }],
            ["AWS/ECS", "CPUUtilization", "ClusterName", var.ecs_cluster_name, "ServiceName", var.ecs_service_name, { stat = "Average", yAxis = "right" }]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 18, width = 12, height = 6,
        properties = {
          title  = "Aurora writer CPU and connections"
          region = var.aws_region
          period = 300
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.db_writer_instance_id, { stat = "Average" }],
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", var.db_writer_instance_id, { stat = "Maximum", yAxis = "right" }]
          ]
        }
      }
    ]
  })
}
