# Database Module variables.tf
variable "project_name" {
  description = "Name of the project"
  type        = string
}

variable "stage" {
  description = "Deployment stage (dev, staging, prod)"
  type        = string
}

variable "db_subnet_group_name" {
  description = "Name of the DB subnet group"
  type        = string
}

variable "db_security_group_id" {
  description = "ID of the security group for the database"
  type        = string
}

variable "availability_zones" {
  description = "AZs to spread writer and reader instances across"
  type        = list(string)
  default     = []
}

variable "engine_version" {
  description = "Aurora PostgreSQL engine version (16.3+ needed for auto-pause; pgvector 0.8 for iterative HNSW scans)"
  type        = string
  default     = "16.6"
}

variable "instance_class" {
  description = "Instance class for cluster instances. Use db.serverless for Serverless v2."
  type        = string
  default     = "db.serverless"
}

variable "reader_count" {
  description = "Number of reader instances. One reader serves vector-search reads and is the failover target."
  type        = number
  default     = 0
}

variable "serverless_min_acu" {
  description = "Serverless v2 minimum ACUs (0 enables auto-pause)"
  type        = number
  default     = 0
}

variable "serverless_max_acu" {
  description = "Serverless v2 maximum ACUs"
  type        = number
  default     = 4
}

variable "auto_pause_seconds" {
  description = "Idle seconds before a Serverless v2 instance pauses (min capacity 0 only)"
  type        = number
  default     = 1800
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

variable "app_db_username" {
  description = "Least-privileged role the application connects as"
  type        = string
  default     = "rag_app"
}

variable "backup_retention_days" {
  description = "Automated backup retention in days"
  type        = number
  default     = 7
}

variable "deletion_protection" {
  description = "Enable deletion protection and a final snapshot"
  type        = bool
  default     = false
}

variable "enable_rds_proxy" {
  description = "Front the cluster with RDS Proxy"
  type        = bool
  default     = false
}

variable "proxy_subnet_ids" {
  description = "Subnets for RDS Proxy"
  type        = list(string)
  default     = []
}

variable "proxy_security_group_id" {
  description = "Security group for RDS Proxy"
  type        = string
  default     = ""
}
