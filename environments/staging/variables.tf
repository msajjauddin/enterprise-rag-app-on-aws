# =========================
# Root variables.tf
# =========================

# -------------------------
# Project Configuration
# -------------------------
variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name of the project"
  type        = string
}

variable "stage" {
  description = "Deployment stage (dev, staging, prod)"
  type        = string
}

# -------------------------
# Lambda Configuration
# -------------------------
variable "lambda_memory_size" {
  description = "Memory size for the document processor Lambda in MB"
  type        = number
  default     = 1024
}

# -------------------------
# VPC Configuration
# -------------------------
variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of Availability Zones to use"
  type        = number
  default     = 2
}

variable "single_nat_gateway" {
  description = "Use one NAT gateway instead of one per AZ"
  type        = bool
  default     = false
}

variable "enable_flow_logs" {
  description = "Enable VPC flow logs"
  type        = bool
  default     = false
}

variable "bastion_allowed_cidr" {
  description = "CIDR blocks allowed to connect to bastion hosts"
  type        = list(string)
  default     = ["0.0.0.0/0"] # Should be restricted to your company IP range in production
}

variable "create_bastion_sg" {
  description = "Create a security group for bastion hosts"
  type        = bool
  default     = false
}

# -------------------------
# Database Configuration
# -------------------------
variable "db_instance_class" {
  description = "Aurora instance class (db.serverless for Serverless v2)"
  type        = string
  default     = "db.serverless"
}

variable "db_reader_count" {
  description = "Aurora readers (1 in prod: read replica + failover standby)"
  type        = number
  default     = 0
}

variable "db_serverless_min_acu" {
  description = "Serverless v2 minimum ACUs (0 = auto-pause)"
  type        = number
  default     = 0
}

variable "db_serverless_max_acu" {
  description = "Serverless v2 maximum ACUs"
  type        = number
  default     = 4
}

variable "db_name" {
  description = "Name of the database"
  type        = string
  default     = "ragapp"
}

variable "db_username" {
  description = "Master username for the database"
  type        = string
  default     = "ragadmin"
}

variable "db_deletion_protection" {
  description = "Deletion protection + final snapshot on the Aurora cluster"
  type        = bool
  default     = false
}

variable "db_backup_retention_days" {
  description = "Aurora backup retention"
  type        = number
  default     = 7
}

variable "enable_rds_proxy" {
  description = "Front Aurora with RDS Proxy"
  type        = bool
  default     = false
}

# -------------------------
# Cache Configuration
# -------------------------
variable "cache_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t4g.micro"
}

variable "cache_node_count" {
  description = "ElastiCache nodes (2 = primary + replica)"
  type        = number
  default     = 1
}

# -------------------------
# Storage Configuration
# -------------------------
variable "enable_lifecycle_rules" {
  description = "Enable S3 lifecycle rules for cost optimization"
  type        = bool
  default     = false
}

variable "standard_ia_transition_days" {
  description = "Days before transitioning to STANDARD_IA storage class"
  type        = number
  default     = 90
}

# -------------------------
# Query service (Fargate)
# -------------------------
variable "image_tag" {
  description = "Query service image tag to deploy (set by CI to the git SHA)"
  type        = string
  default     = "latest"
}

variable "task_cpu" {
  description = "Fargate task CPU units"
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Fargate task memory (MiB)"
  type        = number
  default     = 2048
}

variable "default_min_tasks" {
  description = "Initial min tasks (scheduled actions take over)"
  type        = number
  default     = 1
}

variable "default_max_tasks" {
  description = "Initial max tasks (scheduled actions take over)"
  type        = number
  default     = 2
}

variable "schedule_timezone" {
  description = "Office timezone for scheduled scaling"
  type        = string
  default     = "Asia/Kolkata"
}

variable "scaling_schedules" {
  description = "Scheduled min/max task bounds"
  type = list(object({
    name      = string
    schedule  = string
    min_tasks = number
    max_tasks = number
  }))
  default = []
}

variable "api_domain_aliases" {
  description = "Custom domain names for the API CloudFront distribution"
  type        = list(string)
  default     = []
}

variable "api_acm_certificate_arn" {
  description = "ACM certificate (us-east-1) for api_domain_aliases"
  type        = string
  default     = ""
}

# -------------------------
# Models (Amazon Bedrock)
# -------------------------
variable "embedding_model_id" {
  description = "Bedrock embedding model"
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "embedding_dimensions" {
  description = "Embedding size; changing it requires re-ingesting all documents"
  type        = number
  default     = 1024
}

variable "rerank_model_id" {
  description = "Bedrock rerank model (empty disables reranking)"
  type        = string
  default     = "cohere.rerank-v3-5:0"
}

variable "generation_model_id" {
  description = "OpenAI model on Bedrock for answers (cross-Region inference profile ID; us.openai.gpt-6-sol for harder questions)"
  type        = string
  default     = "us.openai.gpt-6-luna"
}

variable "judge_model_id" {
  description = "OpenAI model on Bedrock used as the online-evaluation judge"
  type        = string
  default     = "us.openai.gpt-6-luna"
}

variable "generation_effort" {
  description = "Reasoning effort for answer generation: none, low, medium, high (low keeps time-to-first-token inside the SLO)"
  type        = string
  default     = "low"
}

# -------------------------
# Query behaviour
# -------------------------
variable "mcp_server_url" {
  description = "MCP web search server URL (empty disables web fallback)"
  type        = string
  default     = ""
}

variable "rag_confidence_threshold" {
  description = "Below this top relevance score, web search fallback is used"
  type        = number
  default     = 0.35
}

variable "rate_limit_per_minute" {
  description = "Queries per user per minute"
  type        = number
  default     = 20
}

variable "semantic_cache_threshold" {
  description = "Cosine similarity for a semantic cache hit"
  type        = number
  default     = 0.95
}

variable "cache_ttl_seconds" {
  description = "Cached answer lifetime"
  type        = number
  default     = 3600
}

variable "eval_sample_rate" {
  description = "Fraction of answers evaluated asynchronously by the judge model"
  type        = number
  default     = 0.05
}

# -------------------------
# Auth (corporate IdP)
# -------------------------
variable "ui_callback_urls" {
  description = "OAuth callback/logout URLs of the Streamlit UI"
  type        = list(string)
  default     = ["http://localhost:8501/"]
}

variable "identity_provider_type" {
  description = "SAML, OIDC or NONE"
  type        = string
  default     = "NONE"
}

variable "identity_provider_name" {
  description = "IdP name in Cognito"
  type        = string
  default     = "CorporateSSO"
}

variable "saml_metadata_url" {
  description = "SAML metadata URL of the corporate IdP"
  type        = string
  default     = ""
}

variable "oidc_issuer" {
  description = "OIDC issuer of the corporate IdP"
  type        = string
  default     = ""
}

variable "oidc_client_id" {
  description = "OIDC client ID at the corporate IdP"
  type        = string
  default     = ""
}

variable "oidc_client_secret" {
  description = "OIDC client secret at the corporate IdP (pass via TF_VAR_oidc_client_secret)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "idp_email_claim" {
  description = "IdP claim / SAML attribute for email"
  type        = string
  default     = "email"
}

variable "idp_groups_claim" {
  description = "IdP claim / SAML attribute for groups"
  type        = string
  default     = "groups"
}

# -------------------------
# Monitoring Configuration
# -------------------------
variable "alert_email" {
  description = "Email address for CloudWatch alerts"
  type        = string
  default     = ""
}
