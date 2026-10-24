variable "project_name" {
  description = "Name of the project"
  type        = string
}

variable "stage" {
  description = "Deployment stage (dev, staging, prod)"
  type        = string
}

variable "anonymize_pii_types" {
  description = "PII entity types masked in questions and answers"
  type        = list(string)
  default     = ["CREDIT_DEBIT_CARD_NUMBER", "US_SOCIAL_SECURITY_NUMBER", "PASSWORD", "AWS_ACCESS_KEY", "AWS_SECRET_KEY"]
}
