# ================================================
# Bedrock Module for RAG System (Guardrails)
# ================================================
# Guardrail applied to user questions and generated answers via the ApplyGuardrail API.

locals {
  name = "${var.project_name}-${var.stage}"
}

resource "aws_bedrock_guardrail" "main" {
  name                      = "${local.name}-guardrail"
  description               = "Content and PII guardrail for ${local.name}"
  blocked_input_messaging   = "Sorry, I can't help with that request."
  blocked_outputs_messaging = "Sorry, I can't share that answer."

  content_policy_config {
    dynamic "filters_config" {
      for_each = ["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"]
      content {
        type            = filters_config.value
        input_strength  = "HIGH"
        output_strength = "HIGH"
      }
    }

    # Prompt-attack detection applies to input only
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = var.anonymize_pii_types
      content {
        type   = pii_entities_config.value
        action = "ANONYMIZE"
      }
    }
  }

  tags = {
    Project     = var.project_name
    Environment = var.stage
    ManagedBy   = "Terraform"
  }
}

resource "aws_bedrock_guardrail_version" "main" {
  guardrail_arn = aws_bedrock_guardrail.main.guardrail_arn
  description   = "Managed by Terraform"

  lifecycle {
    replace_triggered_by = [aws_bedrock_guardrail.main]
  }
}
