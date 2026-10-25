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
  description = "Memory size for the document processor in MB (page rendering for PDF analysis needs headroom)"
  type        = number
  default     = 2048
}

variable "ingestion_timeout" {
  description = "Timeout for the document processor in seconds (queue visibility is 6x this). Large PDFs with many tables and charts need minutes."
  type        = number
  default     = 900
}

variable "ingestion_batch_size" {
  description = "SQS messages per Lambda invocation (1 keeps a large analysed PDF within the 15-minute limit; concurrency gives throughput)"
  type        = number
  default     = 1
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

variable "document_analysis_mode" {
  description = "PDF analysis: triage (only pages with tables, figures or scans get Textract / vision), full (Textract on every page), off (text only)"
  type        = string
  default     = "triage"

  validation {
    condition     = contains(["triage", "full", "off"], var.document_analysis_mode)
    error_message = "document_analysis_mode must be triage, full or off."
  }
}

variable "vision_model_id" {
  description = "Bedrock model (cross-Region inference profile ID) that describes charts and figures"
  type        = string
  default     = "us.openai.gpt-6-luna"
}

variable "max_figures_per_document" {
  description = "Cost guard: maximum figures described per document"
  type        = number
  default     = 30
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
