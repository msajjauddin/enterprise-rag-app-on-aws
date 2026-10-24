variable "project_name" {
  description = "Name of the project"
  type        = string
}

variable "stage" {
  description = "Deployment stage (dev, staging, prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
}

variable "alert_email" {
  description = "Email address for CloudWatch alerts"
  type        = string
}

variable "document_processor_name" {
  description = "Name of the document processor Lambda function"
  type        = string
}

variable "db_init_name" {
  description = "Name of the db_init Lambda function"
  type        = string
}

variable "token_enricher_name" {
  description = "Name of the pre-token-generation Lambda function"
  type        = string
}

variable "ingestion_queue_name" {
  description = "Name of the ingestion queue"
  type        = string
}

variable "ingestion_dlq_name" {
  description = "Name of the ingestion dead-letter queue"
  type        = string
}

variable "alb_arn_suffix" {
  description = "ARN suffix of the query service ALB"
  type        = string
}

variable "ecs_cluster_name" {
  description = "ECS cluster name"
  type        = string
}

variable "ecs_service_name" {
  description = "ECS service name"
  type        = string
}

variable "db_cluster_id" {
  description = "Aurora cluster identifier"
  type        = string
}

variable "db_writer_instance_id" {
  description = "Aurora writer instance identifier"
  type        = string
}

variable "has_db_reader" {
  description = "Whether the cluster has a reader (enables replica-lag alarm)"
  type        = bool
  default     = false
}
