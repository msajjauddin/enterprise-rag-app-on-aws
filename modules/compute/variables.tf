#Compute Module
# =========================
# Lambda Module Variables
# =========================

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
  description = "Memory size for the document processor in MB"
  type        = number
  default     = 1024
}

variable "ingestion_timeout" {
  description = "Timeout for the document processor in seconds (queue visibility is 6x this)"
  type        = number
  default     = 300
}

variable "ingestion_batch_size" {
  description = "SQS messages per Lambda invocation"
  type        = number
  default     = 5
}

variable "ingestion_max_concurrency" {
  description = "Maximum concurrent document processor invocations from SQS"
  type        = number
  default     = 10
}

variable "ingestion_max_receive_count" {
  description = "Attempts before a message moves to the DLQ"
  type        = number
  default     = 3
}

# -------------------------
# Storage & Metadata
# -------------------------
variable "documents_bucket" {
  description = "Name of the S3 bucket for documents"
  type        = string
}

variable "documents_bucket_arn" {
  description = "ARN of the S3 bucket for documents"
  type        = string
}

variable "metadata_table" {
  description = "Name of the DynamoDB table for metadata"
  type        = string
}

variable "metadata_table_arn" {
  description = "ARN of the DynamoDB table for metadata"
  type        = string
}

# -------------------------
# Networking Configuration
# -------------------------
variable "vpc_subnet_ids" {
  description = "List of subnet IDs for Lambda functions"
  type        = list(string)
}

variable "lambda_security_group_id" {
  description = "ID of the security group for Lambda functions"
  type        = string
}

# -------------------------
# Database
# -------------------------
variable "db_write_host" {
  description = "Host for application writes (RDS Proxy or cluster writer endpoint)"
  type        = string
}

variable "db_cluster_endpoint" {
  description = "Aurora writer endpoint used by db_init"
  type        = string
}

variable "db_name" {
  description = "Database name"
  type        = string
}

variable "db_app_secret_arn" {
  description = "ARN of the application database credentials secret"
  type        = string
}

variable "db_master_secret_arn" {
  description = "ARN of the RDS-managed master credentials secret"
  type        = string
}

variable "lambda_code_bucket" {
  description = "Name of the S3 bucket for Lambda code"
  type        = string
}

# -------------------------
# Bedrock Configuration
# -------------------------
variable "embedding_model_id" {
  description = "Bedrock embedding model"
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "embedding_dimensions" {
  description = "Embedding vector size (must match the pgvector column)"
  type        = number
  default     = 1024
}

variable "max_retries" {
  description = "Max Retry"
  type        = number
  default     = 5
}

variable "retry_delay" {
  description = "Retry Delay"
  type        = number
  default     = 10
}
