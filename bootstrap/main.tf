# ============================================================
# Bootstrap (apply once per AWS account, by an administrator)
# ============================================================
# Creates what CI needs before it can deploy anything:
#   - GitHub OIDC identity provider + one deploy role per environment (no long-lived keys)
#   - Shared ECR repository for the query service image
#
#   cd bootstrap && terraform init && terraform apply
#
# Then set each deploy role ARN as the AWS_DEPLOY_ROLE_ARN variable of the matching
# GitHub environment (dev / staging / prod).

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.4.0, < 7.0.0"
    }
  }

  required_version = ">= 1.5.0"
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name of the project"
  type        = string
  default     = "enterprise-rag-app-on-aws"
}

variable "github_repo" {
  description = "GitHub repository (owner/name) allowed to assume the deploy roles"
  type        = string
  default     = "msajjauddin/enterprise-rag-app-on-aws"
}

variable "environments" {
  description = "GitHub environments that get a deploy role"
  type        = list(string)
  default     = ["dev", "staging", "prod"]
}

data "aws_partition" "current" {}

# =========================
# GitHub OIDC
# =========================

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "deploy" {
  for_each = toset(var.environments)

  name                 = "${var.project_name}-${each.key}-github-deploy"
  max_session_duration = 3600

  # Only workflow jobs bound to the matching GitHub environment can assume the role
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn },
      Action    = "sts:AssumeRoleWithWebIdentity",
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:environment:${each.key}"
        }
      }
    }]
  })
}

# Terraform manages VPCs, IAM roles, databases, etc., so the deploy role needs broad rights.
# Tighten with a permissions boundary once the resource set is stable.
resource "aws_iam_role_policy_attachment" "deploy" {
  for_each   = aws_iam_role.deploy
  role       = each.value.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AdministratorAccess"
}

# =========================
# ECR (shared by all environments; the same image is promoted dev -> staging -> prod)
# =========================

resource "aws_ecr_repository" "query_api" {
  name                 = "${var.project_name}-query-api"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "query_api" {
  repository = aws_ecr_repository.query_api.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1,
      description  = "Keep the last 50 images",
      selection = {
        tagStatus   = "any",
        countType   = "imageCountMoreThan",
        countNumber = 50
      },
      action = { type = "expire" }
    }]
  })
}

output "deploy_role_arns" {
  description = "Set each as AWS_DEPLOY_ROLE_ARN on the matching GitHub environment"
  value       = { for env, role in aws_iam_role.deploy : env => role.arn }
}

output "ecr_repository_url" {
  description = "ECR repository for the query service image"
  value       = aws_ecr_repository.query_api.repository_url
}
