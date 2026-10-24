# ================================================
# Query Service Module for RAG System
# ================================================
# FastAPI query service on ECS Fargate behind an internal ALB. CloudFront reaches the
# ALB through a VPC origin, so the ALB has no public address. Deployments are ECS-native
# blue/green; capacity follows scheduled min/max bounds plus target tracking.

locals {
  name           = "${var.project_name}-${var.stage}"
  service_name   = "${local.name}-query-api"
  container_name = "query-api"

  common_tags = {
    Project     = var.project_name
    Environment = var.stage
    ManagedBy   = "Terraform"
  }

  llm_profile_ids = distinct([var.generation_model_id, var.judge_model_id])

  # ADOT collector: OTLP from the app -> X-Ray
  otel_config = <<-YAML
    receivers:
      otlp:
        protocols:
          grpc:
            endpoint: 0.0.0.0:4317
          http:
            endpoint: 0.0.0.0:4318
    processors:
      batch: {}
    exporters:
      awsxray:
        region: ${var.aws_region}
    service:
      pipelines:
        traces:
          receivers: [otlp]
          processors: [batch]
          exporters: [awsxray]
  YAML
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# =========================
# Logs + cluster
# =========================

resource "aws_cloudwatch_log_group" "service" {
  name              = "/ecs/${local.service_name}"
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}

resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enhanced"
  }

  tags = local.common_tags
}

# =========================
# IAM
# =========================

resource "aws_iam_role" "execution" {
  name = "${local.service_name}-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "ecs-tasks.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name = "${local.service_name}-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "ecs-tasks.amazonaws.com" },
      Action    = "sts:AssumeRole",
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "task" {
  name = "query-service"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Sid    = "EmbeddingsAndRerank",
        Effect = "Allow",
        Action = ["bedrock:InvokeModel"],
        Resource = [
          "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/${var.embedding_model_id}",
          "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/${var.rerank_model_id}"
        ]
      },
      {
        Sid      = "Guardrail",
        Effect   = "Allow",
        Action   = ["bedrock:ApplyGuardrail"],
        Resource = var.guardrail_arn
      },
      {
        # OpenAI models on bedrock-runtime: the cross-Region inference profile, the
        # model in each destination Region, and the account's default project
        Sid    = "GenerationModels",
        Effect = "Allow",
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        Resource = concat(
          [for id in local.llm_profile_ids : "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/${id}"],
          [for id in local.llm_profile_ids : "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/${replace(id, "/^(us|global)\\./", "")}"],
          ["arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:project/default"]
        )
      },
      {
        Sid    = "ChatHistoryAndMetadata",
        Effect = "Allow",
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"],
        Resource = [
          var.chat_history_table_arn,
          var.metadata_table_arn,
          "${var.metadata_table_arn}/index/*"
        ]
      },
      {
        Sid      = "PresignedUploads",
        Effect   = "Allow",
        Action   = ["s3:PutObject"],
        Resource = "${var.documents_bucket_arn}/uploads/*"
      },
      {
        Sid      = "DatabaseCredentials",
        Effect   = "Allow",
        Action   = ["secretsmanager:GetSecretValue"],
        Resource = [var.db_secret_arn]
      },
      {
        Sid      = "Tracing",
        Effect   = "Allow",
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
        Resource = "*"
      }
    ]
  })
}

# Lets ECS shift ALB listener rule weights during blue/green deployments
resource "aws_iam_role" "ecs_infrastructure" {
  name = "${local.service_name}-ecs-infra-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "ecs.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ecs_infrastructure" {
  role       = aws_iam_role.ecs_infrastructure.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonECSInfrastructureRolePolicyForLoadBalancers"
}

# =========================
# Internal ALB
# =========================

resource "aws_lb" "main" {
  name               = substr("${local.name}-api", 0, 32)
  internal           = true
  load_balancer_type = "application"
  security_groups    = [var.alb_security_group_id]
  subnets            = var.private_subnet_ids

  # Streaming answers keep connections open; allow long gaps between tokens
  idle_timeout               = 300
  drop_invalid_header_fields = true

  tags = local.common_tags
}

resource "aws_lb_target_group" "app" {
  for_each = toset(["blue", "green"])

  name                 = substr("${local.name}-${each.key}", 0, 32)
  port                 = var.container_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 30

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
  }

  tags = local.common_tags
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "application/json"
      message_body = "{\"message\":\"Not found\"}"
      status_code  = "404"
    }
  }
}

resource "aws_lb_listener_rule" "app" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 100

  action {
    type = "forward"

    forward {
      target_group {
        arn    = aws_lb_target_group.app["blue"].arn
        weight = 100
      }
      target_group {
        arn    = aws_lb_target_group.app["green"].arn
        weight = 0
      }
    }
  }

  condition {
    path_pattern {
      values = ["/*"]
    }
  }

  # ECS rewrites the weights during blue/green deployments
  lifecycle {
    ignore_changes = [action]
  }
}

# =========================
# Task definition + service
# =========================

resource "aws_ecs_task_definition" "app" {
  family                   = local.service_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = local.container_name
      image     = "${var.image_repository_url}:${var.image_tag}"
      essential = true

      portMappings = [{ containerPort = var.container_port, protocol = "tcp" }]

      environment = [for k, v in merge(var.app_environment, {
        STAGE                       = var.stage
        AWS_REGION                  = var.aws_region
        PORT                        = tostring(var.container_port)
        OTEL_SERVICE_NAME           = local.service_name
        OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4317"
        METRICS_NAMESPACE           = "RAGApp/${var.stage}"
      }) : { name = k, value = tostring(v) }]

      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:${var.container_port}/health').status == 200 else 1)\""]
        interval    = 15
        timeout     = 5
        retries     = 3
        startPeriod = 20
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.service.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "app"
        }
      }
    },
    {
      name      = "otel-collector"
      image     = "public.ecr.aws/aws-observability/aws-otel-collector:${var.otel_collector_version}"
      essential = false

      environment = [{ name = "AOT_CONFIG_CONTENT", value = local.otel_config }]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.service.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "otel"
        }
      }
    }
  ])

  tags = local.common_tags
}

resource "aws_ecs_service" "app" {
  name             = local.service_name
  cluster          = aws_ecs_cluster.main.id
  task_definition  = aws_ecs_task_definition.app.arn
  launch_type      = "FARGATE"
  platform_version = "LATEST"
  desired_count    = var.initial_desired_count

  health_check_grace_period_seconds = 30
  enable_execute_command            = false
  propagate_tags                    = "SERVICE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.ecs_security_group_id]
    assign_public_ip = false
  }

  deployment_configuration {
    strategy             = "BLUE_GREEN"
    bake_time_in_minutes = var.bake_time_minutes
  }

  # Roll back automatically if the 5xx alarm fires while the new version is live
  alarms {
    alarm_names = [aws_cloudwatch_metric_alarm.deployment_5xx.alarm_name]
    enable      = true
    rollback    = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app["blue"].arn
    container_name   = local.container_name
    container_port   = var.container_port

    advanced_configuration {
      alternate_target_group_arn = aws_lb_target_group.app["green"].arn
      production_listener_rule   = aws_lb_listener_rule.app.arn
      role_arn                   = aws_iam_role.ecs_infrastructure.arn
    }
  }

  lifecycle {
    # Autoscaling owns the task count; ECS swaps target groups between deployments
    ignore_changes = [desired_count, load_balancer]
  }

  depends_on = [aws_lb_listener_rule.app, aws_iam_role_policy_attachment.ecs_infrastructure]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "deployment_5xx" {
  alarm_name          = "${local.service_name}-deployment-5xx"
  alarm_description   = "Target 5xx during a deployment; triggers ECS rollback"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 10
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }

  tags = local.common_tags
}

# =========================
# Autoscaling: scheduled bounds + target tracking
# =========================

resource "aws_appautoscaling_target" "app" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.app.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.default_min_tasks
  max_capacity       = var.default_max_tasks

  # Scheduled actions move these bounds; don't fight them on every apply
  lifecycle {
    ignore_changes = [min_capacity, max_capacity]
  }
}

resource "aws_appautoscaling_policy" "cpu" {
  name               = "${local.service_name}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.app.service_namespace
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = var.cpu_target_percent
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

resource "aws_appautoscaling_policy" "requests" {
  for_each = toset(["blue", "green"])

  name               = "${local.service_name}-requests-${each.key}"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.app.service_namespace
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = var.requests_per_target
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    # Only the target group currently receiving traffic reports requests
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.main.arn_suffix}/${aws_lb_target_group.app[each.key].arn_suffix}"
    }
  }
}

resource "aws_appautoscaling_scheduled_action" "app" {
  for_each = { for s in var.scaling_schedules : s.name => s }

  name               = "${local.service_name}-${each.key}"
  service_namespace  = aws_appautoscaling_target.app.service_namespace
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension
  schedule           = each.value.schedule
  timezone           = var.schedule_timezone

  scalable_target_action {
    min_capacity = each.value.min_tasks
    max_capacity = each.value.max_tasks
  }
}

# =========================
# CloudFront -> ALB (VPC origin)
# =========================

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_vpc_origin" "alb" {
  vpc_origin_endpoint_config {
    name                   = "${local.name}-alb"
    arn                    = aws_lb.main.arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"

    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }

  tags = local.common_tags
}

resource "aws_cloudfront_distribution" "api" {
  enabled         = true
  comment         = "${local.name} query API"
  price_class     = "PriceClass_100"
  http_version    = "http2and3"
  is_ipv6_enabled = true
  aliases         = var.domain_aliases

  origin {
    domain_name = aws_lb.main.dns_name
    origin_id   = "alb"

    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.alb.id
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = false
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = var.acm_certificate_arn == ""
    acm_certificate_arn            = var.acm_certificate_arn == "" ? null : var.acm_certificate_arn
    ssl_support_method             = var.acm_certificate_arn == "" ? null : "sni-only"
    minimum_protocol_version       = var.acm_certificate_arn == "" ? "TLSv1" : "TLSv1.2_2021"
  }

  tags = local.common_tags
}
