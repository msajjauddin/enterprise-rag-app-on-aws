variable "project_name" {
  description = "Name of the project"
  type        = string
}

variable "stage" {
  description = "Deployment stage (dev, staging, prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

# -------------------------
# Networking
# -------------------------
variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnets for the ALB and Fargate tasks"
  type        = list(string)
}

variable "alb_security_group_id" {
  description = "Security group for the internal ALB"
  type        = string
}

variable "ecs_security_group_id" {
  description = "Security group for Fargate tasks"
  type        = string
}

variable "domain_aliases" {
  description = "Custom domain names for the CloudFront distribution (requires acm_certificate_arn)"
  type        = list(string)
  default     = []
}

variable "acm_certificate_arn" {
  description = "ACM certificate in us-east-1 for domain_aliases; empty uses the cloudfront.net certificate"
  type        = string
  default     = ""
}

# -------------------------
# Container
# -------------------------
variable "image_repository_url" {
  description = "ECR repository URL of the query service image"
  type        = string
}

variable "image_tag" {
  description = "Image tag to deploy (git SHA from CI)"
  type        = string
}

variable "container_port" {
  description = "Container port"
  type        = number
  default     = 8000
}

variable "task_cpu" {
  description = "Task CPU units"
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Task memory (MiB)"
  type        = number
  default     = 2048
}

variable "otel_collector_version" {
  description = "AWS Distro for OpenTelemetry collector image tag"
  type        = string
  default     = "v0.49.0"
}

variable "app_environment" {
  description = "Environment variables for the application container"
  type        = map(string)
  default     = {}
}

variable "log_retention_days" {
  description = "CloudWatch log retention"
  type        = number
  default     = 30
}

variable "bake_time_minutes" {
  description = "Minutes the previous (blue) version stays up after traffic shifts, for instant rollback"
  type        = number
  default     = 5
}

# -------------------------
# Autoscaling
# -------------------------
variable "initial_desired_count" {
  description = "Task count at creation (autoscaling owns it afterwards)"
  type        = number
  default     = 2
}

variable "default_min_tasks" {
  description = "Min tasks before the first scheduled action runs"
  type        = number
  default     = 2
}

variable "default_max_tasks" {
  description = "Max tasks before the first scheduled action runs"
  type        = number
  default     = 4
}

variable "cpu_target_percent" {
  description = "Target average CPU for target tracking"
  type        = number
  default     = 60
}

variable "requests_per_target" {
  description = "Target ALB requests per task per minute"
  type        = number
  default     = 100
}

variable "schedule_timezone" {
  description = "Timezone for scheduled scaling (office timezone)"
  type        = string
  default     = "Asia/Kolkata"
}

variable "scaling_schedules" {
  description = "Scheduled min/max task bounds (cron in schedule_timezone)"
  type = list(object({
    name      = string
    schedule  = string
    min_tasks = number
    max_tasks = number
  }))
  default = []
}

# -------------------------
# Data + AI dependencies
# -------------------------
variable "embedding_model_id" {
  description = "Bedrock embedding model"
  type        = string
}

variable "rerank_model_id" {
  description = "Bedrock rerank model"
  type        = string
  default     = "cohere.rerank-v3-5:0"
}

variable "generation_model_id" {
  description = "Inference profile ID of the answer model (e.g. us.openai.gpt-6-luna)"
  type        = string
}

variable "judge_model_id" {
  description = "Inference profile ID of the evaluation judge model"
  type        = string
}

variable "guardrail_arn" {
  description = "ARN of the Bedrock guardrail"
  type        = string
}

variable "chat_history_table_arn" {
  description = "ARN of the chat history table"
  type        = string
}

variable "metadata_table_arn" {
  description = "ARN of the document metadata table"
  type        = string
}

variable "documents_bucket_arn" {
  description = "ARN of the documents bucket"
  type        = string
}

variable "db_secret_arn" {
  description = "ARN of the application database credentials secret"
  type        = string
}
