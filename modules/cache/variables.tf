variable "project_name" {
  description = "Name of the project"
  type        = string
}

variable "stage" {
  description = "Deployment stage (dev, staging, prod)"
  type        = string
}

variable "engine_version" {
  description = "Valkey engine version"
  type        = string
  default     = "8.0"
}

variable "node_type" {
  description = "Cache node type"
  type        = string
  default     = "cache.t4g.micro"
}

variable "node_count" {
  description = "Number of nodes (1 = single node, 2 = primary + replica)"
  type        = number
  default     = 1
}

variable "subnet_group_name" {
  description = "ElastiCache subnet group"
  type        = string
}

variable "security_group_id" {
  description = "Security group for the cache"
  type        = string
}
