# ===============================
# Compute Module for RAG System
# ===============================
# Ingestion pipeline (S3 / connectors -> SQS -> document processor Lambda, with DLQ),
# the db_init migration Lambda and the Cognito pre-token-generation Lambda.

# =========================
# Locals
# =========================

locals {
  name                    = "${var.project_name}-${var.stage}"
  document_processor_name = "${var.project_name}-${var.stage}-document-processor"
  db_init_name            = "${var.project_name}-${var.stage}-db-init"
  token_enricher_name     = "${var.project_name}-${var.stage}-token-enricher"

  common_tags = {
    Project     = var.project_name
    Environment = var.stage
    ManagedBy   = "Terraform"
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# =========================
# Ingestion queue + DLQ
# =========================

resource "aws_sqs_queue" "ingestion_dlq" {
  name                      = "${local.name}-ingestion-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true

  tags = local.common_tags
}

resource "aws_sqs_queue" "ingestion" {
  name                       = "${local.name}-ingestion"
  visibility_timeout_seconds = var.ingestion_timeout * 6
  message_retention_seconds  = 345600 # 4 days
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingestion_dlq.arn
    maxReceiveCount     = var.ingestion_max_receive_count
  })

  tags = local.common_tags
}

resource "aws_sqs_queue_redrive_allow_policy" "ingestion_dlq" {
  queue_url = aws_sqs_queue.ingestion_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue",
    sourceQueueArns   = [aws_sqs_queue.ingestion.arn]
  })
}

# S3 may publish object-created events to the queue
resource "aws_sqs_queue_policy" "ingestion" {
  queue_url = aws_sqs_queue.ingestion.id

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Sid       = "AllowS3Events",
      Effect    = "Allow",
      Principal = { Service = "s3.amazonaws.com" },
      Action    = "sqs:SendMessage",
      Resource  = aws_sqs_queue.ingestion.arn,
      Condition = {
        ArnEquals    = { "aws:SourceArn" = var.documents_bucket_arn },
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket = var.documents_bucket

  queue {
    queue_arn     = aws_sqs_queue.ingestion.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "uploads/"
  }

  depends_on = [aws_sqs_queue_policy.ingestion]
}

# =========================
# IAM - ingestion Lambdas
# =========================

resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-${var.stage}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "lambda.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${var.project_name}-${var.stage}-lambda-role"
  }
}

resource "aws_iam_policy" "lambda_policy" {
  name = "${var.project_name}-${var.stage}-lambda-policy"

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        Resource = "arn:${data.aws_partition.current.partition}:logs:*:*:*"
      },
      {
        Effect = "Allow",
        Action = ["s3:GetObject", "s3:ListBucket"],
        Resource = [
          var.documents_bucket_arn,
          "${var.documents_bucket_arn}/*"
        ]
      },
      {
        Effect = "Allow",
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"
        ],
        Resource = [
          var.metadata_table_arn,
          "${var.metadata_table_arn}/index/*"
        ]
      },
      {
        Effect   = "Allow",
        Action   = ["bedrock:InvokeModel"],
        Resource = "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/${var.embedding_model_id}"
      },
      {
        Effect   = "Allow",
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"],
        Resource = aws_sqs_queue.ingestion.arn
      },
      {
        Effect = "Allow",
        Action = [
          "ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses",
          "ec2:UnassignPrivateIpAddresses"
        ],
        Resource = "*"
      },
      {
        Effect   = "Allow",
        Action   = ["secretsmanager:GetSecretValue"],
        Resource = [var.db_app_secret_arn, var.db_master_secret_arn]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}

# =========================
# Lambda Code from S3
# =========================

data "aws_s3_object" "document_processor_code" {
  bucket = var.lambda_code_bucket
  key    = "lambda/document_processor.zip"
}

data "aws_s3_object" "db_init_code" {
  bucket = var.lambda_code_bucket
  key    = "lambda/db_init.zip"
}

data "aws_s3_object" "token_enricher_code" {
  bucket = var.lambda_code_bucket
  key    = "lambda/token_enricher.zip"
}

# =========================
# Lambda Functions
# =========================

resource "aws_lambda_function" "document_processor" {
  function_name = local.document_processor_name
  role          = aws_iam_role.lambda_role.arn
  handler       = "document_processor.handler"
  runtime       = "python3.12"
  memory_size   = var.lambda_memory_size
  timeout       = var.ingestion_timeout

  environment {
    variables = {
      DOCUMENTS_BUCKET     = var.documents_bucket
      METADATA_TABLE       = var.metadata_table
      STAGE                = var.stage
      DB_HOST              = var.db_write_host
      DB_NAME              = var.db_name
      DB_SECRET_ARN        = var.db_app_secret_arn
      EMBEDDING_MODEL_ID   = var.embedding_model_id
      EMBEDDING_DIMENSIONS = var.embedding_dimensions
      EMBEDDING_BATCH_SIZE = 16
      CHUNK_SIZE           = 1000
      CHUNK_OVERLAP        = 200
    }
  }

  vpc_config {
    subnet_ids         = var.vpc_subnet_ids
    security_group_ids = [var.lambda_security_group_id]
  }

  tracing_config {
    mode = "Active"
  }

  s3_bucket        = var.lambda_code_bucket
  s3_key           = "lambda/document_processor.zip"
  source_code_hash = data.aws_s3_object.document_processor_code.etag

  tags = {
    Name = local.document_processor_name
  }
}

resource "aws_lambda_event_source_mapping" "ingestion" {
  event_source_arn                   = aws_sqs_queue.ingestion.arn
  function_name                      = aws_lambda_function.document_processor.arn
  batch_size                         = var.ingestion_batch_size
  maximum_batching_window_in_seconds = 10
  function_response_types            = ["ReportBatchItemFailures"]

  # Bounds concurrent writers on Aurora and Bedrock embedding throughput
  scaling_config {
    maximum_concurrency = var.ingestion_max_concurrency
  }
}

resource "aws_lambda_function" "db_init" {
  function_name = local.db_init_name
  role          = aws_iam_role.lambda_role.arn
  handler       = "db_init.handler"
  runtime       = "python3.12"
  memory_size   = 256
  timeout       = 300

  environment {
    variables = {
      STAGE                = var.stage
      DB_HOST              = var.db_cluster_endpoint
      DB_NAME              = var.db_name
      DB_MASTER_SECRET_ARN = var.db_master_secret_arn
      DB_APP_SECRET_ARN    = var.db_app_secret_arn
      EMBEDDING_DIMENSIONS = var.embedding_dimensions
      MAX_RETRIES          = var.max_retries
      RETRY_DELAY          = var.retry_delay
    }
  }

  vpc_config {
    subnet_ids         = var.vpc_subnet_ids
    security_group_ids = [var.lambda_security_group_id]
  }

  s3_bucket        = var.lambda_code_bucket
  s3_key           = "lambda/db_init.zip"
  source_code_hash = data.aws_s3_object.db_init_code.etag

  tags = {
    Name = local.db_init_name
  }
}

# =========================
# Cognito pre-token-generation trigger
# =========================
# Copies corporate IdP groups into cognito:groups so the access token carries them.

resource "aws_iam_role" "token_enricher" {
  name = "${local.name}-token-enricher-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "lambda.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "token_enricher_logs" {
  role       = aws_iam_role.token_enricher.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "token_enricher" {
  function_name = local.token_enricher_name
  role          = aws_iam_role.token_enricher.arn
  handler       = "token_enricher.handler"
  runtime       = "python3.12"
  memory_size   = 128
  timeout       = 5

  environment {
    variables = {
      GROUPS_ATTRIBUTE = "custom:groups"
    }
  }

  s3_bucket        = var.lambda_code_bucket
  s3_key           = "lambda/token_enricher.zip"
  source_code_hash = data.aws_s3_object.token_enricher_code.etag

  tags = {
    Name = local.token_enricher_name
  }
}
