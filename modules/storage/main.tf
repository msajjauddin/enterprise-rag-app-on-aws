# ================================================
# Storage Module for RAG System (S3 + DynamoDB)
# ================================================
# Provisions secure document storage and metadata store with best practices

# ==============================
# Locals
# ==============================

locals {
  bucket_name = "${var.project_name}-${var.stage}-documents"

  common_tags = {
    Project     = var.project_name
    Environment = var.stage
    ManagedBy   = "Terraform"
  }
}

# ==============================
# Document Storage - S3 bucket
# ==============================

resource "aws_s3_bucket" "documents" {
  bucket = local.bucket_name

  tags = {
    Name        = local.bucket_name
    Environment = var.stage
  }

  # Prevent destruction of existing buckets
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  count  = var.enable_lifecycle_rules ? 1 : 0
  bucket = aws_s3_bucket.documents.id

  # Documents stay retrievable (Standard-IA) so they can be re-ingested when the
  # embedding model changes. Archive tiers would block re-ingestion.
  rule {
    id     = "archive-old-documents"
    status = "Enabled"

    filter {
      prefix = ""
    }

    transition {
      days          = var.standard_ia_transition_days
      storage_class = "STANDARD_IA"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ==============================
# DynamoDB for metadata storage
# ==============================

locals {
  table_name = "${var.project_name}-${var.stage}-metadata"
}

# DynamoDB for metadata storage
resource "aws_dynamodb_table" "metadata" {
  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST" # Serverless - pay only for what you use
  hash_key     = "id"

  # Only define attributes used in key schemas
  attribute {
    name = "id"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "document_id"
    type = "S"
  }

  global_secondary_index {
    name            = "UserIndex"
    hash_key        = "user_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "DocumentIndex"
    hash_key        = "document_id"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = local.table_name
    Environment = var.stage
  }

  # Prevent destruction of existing tables
  lifecycle {
    prevent_destroy = true
  }
}
# ==============================
# DynamoDB for chat history
# ==============================
# One item per question/answer turn. Sort key is a time-ordered message id so a
# user's history reads newest-first with a single Query.

resource "aws_dynamodb_table" "chat_history" {
  name         = "${var.project_name}-${var.stage}-chat-history"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "message_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "message_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${var.project_name}-${var.stage}-chat-history"
    Environment = var.stage
  }
}

# The lifecycle rule became optional (count); keep the existing rule in state
moved {
  from = aws_s3_bucket_lifecycle_configuration.documents
  to   = aws_s3_bucket_lifecycle_configuration.documents[0]
}
