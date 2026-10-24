# ============================================================
# Root Module for RAG Application Deployment in Dev env (main.tf)
# ============================================================
# Provisions project-wide infrastructure by composing all modules.
# The three environments share this file; differences live in terraform.tfvars.

data "aws_availability_zones" "available" {
  state = "available"
}

# Shared across environments; created once by bootstrap/
data "aws_ecr_repository" "query_api" {
  name = "${var.project_name}-query-api"
}

# =====================================
# S3 Bucket to Store Lambda ZIP Artifacts
# =====================================

resource "aws_s3_bucket" "lambda_code" {
  bucket = "${var.project_name}-${var.stage}-lambda-code"

  tags = {
    Name        = "${var.project_name}-${var.stage}-lambda-code"
    Environment = var.stage
  }

  # Prevent destruction of existing buckets
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lambda_code" {
  bucket = aws_s3_bucket.lambda_code.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "lambda_code" {
  bucket                  = aws_s3_bucket.lambda_code.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "lambda_code" {
  bucket = aws_s3_bucket.lambda_code.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ====================
# VPC Networking Module
# ====================

module "vpc" {
  source = "../../modules/vpc"

  project_name = var.project_name
  stage        = var.stage
  aws_region   = var.aws_region
  vpc_cidr     = var.vpc_cidr
  az_count     = var.az_count

  single_nat_gateway   = var.single_nat_gateway
  enable_flow_logs     = var.enable_flow_logs
  create_bastion_sg    = var.create_bastion_sg
  bastion_allowed_cidr = var.bastion_allowed_cidr
  enable_rds_proxy     = var.enable_rds_proxy
}

# =======================
# Storage (S3 + DynamoDB)
# =======================

module "storage" {
  source = "../../modules/storage"

  project_name = var.project_name
  stage        = var.stage

  enable_lifecycle_rules      = var.enable_lifecycle_rules
  standard_ia_transition_days = var.standard_ia_transition_days
}

# ==================================
# Database (Aurora PostgreSQL + pgvector)
# ==================================

module "database" {
  source = "../../modules/database"

  project_name         = var.project_name
  stage                = var.stage
  db_subnet_group_name = module.vpc.db_subnet_group_name
  db_security_group_id = module.vpc.database_security_group_id
  availability_zones   = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  instance_class        = var.db_instance_class
  reader_count          = var.db_reader_count
  serverless_min_acu    = var.db_serverless_min_acu
  serverless_max_acu    = var.db_serverless_max_acu
  db_name               = var.db_name
  db_username           = var.db_username
  deletion_protection   = var.db_deletion_protection
  backup_retention_days = var.db_backup_retention_days

  enable_rds_proxy        = var.enable_rds_proxy
  proxy_subnet_ids        = module.vpc.private_subnet_ids
  proxy_security_group_id = module.vpc.rds_proxy_security_group_id
}

# ==================================
# Cache (ElastiCache Valkey)
# ==================================

module "cache" {
  source = "../../modules/cache"

  project_name      = var.project_name
  stage             = var.stage
  node_type         = var.cache_node_type
  node_count        = var.cache_node_count
  subnet_group_name = module.vpc.cache_subnet_group_name
  security_group_id = module.vpc.cache_security_group_id
}

# ==================================
# Bedrock Guardrail
# ==================================

module "bedrock" {
  source = "../../modules/bedrock"

  project_name = var.project_name
  stage        = var.stage
}

# =======================
# Compute (ingestion pipeline + Lambdas)
# =======================

module "compute" {
  source = "../../modules/compute"

  project_name       = var.project_name
  stage              = var.stage
  lambda_memory_size = var.lambda_memory_size

  documents_bucket     = module.storage.documents_bucket_name
  documents_bucket_arn = module.storage.documents_bucket_arn
  metadata_table       = module.storage.metadata_table_name
  metadata_table_arn   = module.storage.metadata_table_arn
  lambda_code_bucket   = aws_s3_bucket.lambda_code.bucket

  vpc_subnet_ids           = module.vpc.private_subnet_ids
  lambda_security_group_id = module.vpc.lambda_security_group_id

  db_write_host        = module.database.write_host
  db_cluster_endpoint  = module.database.cluster_endpoint
  db_name              = module.database.db_name
  db_app_secret_arn    = module.database.app_secret_arn
  db_master_secret_arn = module.database.master_secret_arn

  embedding_model_id   = var.embedding_model_id
  embedding_dimensions = var.embedding_dimensions
}

# ====================
# Auth (Cognito federated with the corporate IdP)
# ====================

module "auth" {
  source = "../../modules/auth"

  project_name = var.project_name
  stage        = var.stage
  aws_region   = var.aws_region

  allowed_callback_urls = var.ui_callback_urls
  allowed_logout_urls   = var.ui_callback_urls

  pre_token_lambda_arn  = module.compute.token_enricher_arn
  pre_token_lambda_name = module.compute.token_enricher_name

  identity_provider_type = var.identity_provider_type
  identity_provider_name = var.identity_provider_name
  saml_metadata_url      = var.saml_metadata_url
  oidc_issuer            = var.oidc_issuer
  oidc_client_id         = var.oidc_client_id
  oidc_client_secret     = var.oidc_client_secret
  idp_email_claim        = var.idp_email_claim
  idp_groups_claim       = var.idp_groups_claim
}

# ====================
# Query service (FastAPI on Fargate, ALB, CloudFront)
# ====================

module "query_service" {
  source = "../../modules/query_service"

  project_name = var.project_name
  stage        = var.stage
  aws_region   = var.aws_region

  vpc_id                = module.vpc.vpc_id
  private_subnet_ids    = module.vpc.private_subnet_ids
  alb_security_group_id = module.vpc.alb_security_group_id
  ecs_security_group_id = module.vpc.ecs_security_group_id
  domain_aliases        = var.api_domain_aliases
  acm_certificate_arn   = var.api_acm_certificate_arn

  image_repository_url = data.aws_ecr_repository.query_api.repository_url
  image_tag            = var.image_tag
  task_cpu             = var.task_cpu
  task_memory          = var.task_memory

  initial_desired_count = var.default_min_tasks
  default_min_tasks     = var.default_min_tasks
  default_max_tasks     = var.default_max_tasks
  schedule_timezone     = var.schedule_timezone
  scaling_schedules     = var.scaling_schedules

  embedding_model_id     = var.embedding_model_id
  rerank_model_id        = var.rerank_model_id
  guardrail_arn          = module.bedrock.guardrail_arn
  chat_history_table_arn = module.storage.chat_history_table_arn
  metadata_table_arn     = module.storage.metadata_table_arn
  documents_bucket_arn   = module.storage.documents_bucket_arn
  db_secret_arn          = module.database.app_secret_arn

  app_environment = {
    DB_READ_HOST             = module.database.read_host
    DB_NAME                  = module.database.db_name
    DB_SECRET_ARN            = module.database.app_secret_arn
    REDIS_HOST               = module.cache.primary_endpoint
    COGNITO_USER_POOL_ID     = module.auth.cognito_user_pool_id
    COGNITO_APP_CLIENT_ID    = module.auth.cognito_app_client_id
    COGNITO_EVAL_CLIENT_ID   = module.auth.cognito_eval_client_id
    CHAT_HISTORY_TABLE       = module.storage.chat_history_table_name
    METADATA_TABLE           = module.storage.metadata_table_name
    DOCUMENTS_BUCKET         = module.storage.documents_bucket_name
    EMBEDDING_MODEL_ID       = var.embedding_model_id
    EMBEDDING_DIMENSIONS     = var.embedding_dimensions
    RERANK_MODEL_ID          = var.rerank_model_id
    GENERATION_MODEL_ID      = var.generation_model_id
    FALLBACK_MODEL_ID        = var.fallback_model_id
    JUDGE_MODEL_ID           = var.judge_model_id
    GENERATION_EFFORT        = var.generation_effort
    GUARDRAIL_ID             = module.bedrock.guardrail_id
    GUARDRAIL_VERSION        = module.bedrock.guardrail_version
    MCP_SERVER_URL           = var.mcp_server_url
    RAG_CONFIDENCE_THRESHOLD = var.rag_confidence_threshold
    RATE_LIMIT_PER_MINUTE    = var.rate_limit_per_minute
    SEMANTIC_CACHE_THRESHOLD = var.semantic_cache_threshold
    CACHE_TTL_SECONDS        = var.cache_ttl_seconds
    EVAL_SAMPLE_RATE         = var.eval_sample_rate
  }
}

# ===================
# Monitoring & Alerts
# ===================

module "monitoring" {
  source = "../../modules/monitoring"

  project_name            = var.project_name
  stage                   = var.stage
  aws_region              = var.aws_region
  alert_email             = var.alert_email
  document_processor_name = module.compute.document_processor_name
  db_init_name            = module.compute.db_init_name
  token_enricher_name     = module.compute.token_enricher_name
  ingestion_queue_name    = module.compute.ingestion_queue_name
  ingestion_dlq_name      = module.compute.ingestion_dlq_name
  alb_arn_suffix          = module.query_service.alb_arn_suffix
  ecs_cluster_name        = module.query_service.cluster_name
  ecs_service_name        = module.query_service.service_name
  db_cluster_id           = module.database.cluster_id
  db_writer_instance_id   = module.database.writer_instance_id
  has_db_reader           = var.db_reader_count > 0
}

# ===================
# Outputs
# ===================

output "api_endpoint" {
  description = "HTTPS endpoint of the query API (CloudFront)"
  value       = module.query_service.api_endpoint
}

output "document_bucket" {
  description = "Name of the document bucket"
  value       = module.storage.documents_bucket_name
}

output "dynamodb_table" {
  description = "Name of the DynamoDB metadata table"
  value       = module.storage.metadata_table_name
}

output "chat_history_table" {
  description = "Name of the DynamoDB chat history table"
  value       = module.storage.chat_history_table_name
}

output "ingestion_queue_url" {
  description = "SQS queue that S3 events and connectors publish to"
  value       = module.compute.ingestion_queue_url
}

output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "db_endpoint" {
  description = "Aurora writer endpoint"
  value       = module.database.cluster_endpoint
}

output "db_init_function" {
  description = "Name of the db_init Lambda (run after each apply)"
  value       = module.compute.db_init_name
}

output "cognito_user_pool_id" {
  description = "ID of the Cognito User Pool"
  value       = module.auth.cognito_user_pool_id
}

output "cognito_app_client_id" {
  description = "ID of the Cognito App Client"
  value       = module.auth.cognito_app_client_id
}

output "cognito_domain" {
  description = "Cognito domain prefix for the hosted UI"
  value       = module.auth.cognito_domain
}

output "eval_client_secret_arn" {
  description = "Secret holding the CI evaluation client credentials"
  value       = module.auth.eval_client_secret_arn
}

output "ecs_cluster_name" {
  description = "ECS cluster of the query service"
  value       = module.query_service.cluster_name
}

output "ecs_service_name" {
  description = "ECS service of the query service"
  value       = module.query_service.service_name
}

output "dashboard_name" {
  description = "CloudWatch dashboard"
  value       = module.monitoring.dashboard_name
}
