variable "region" {
  description = "AWS region for everything. us-east-1: Bedrock model availability is broadest here and it is the cheapest mainstream region."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name — used as the name/prefix for the VPC, EKS cluster, queue, bucket, ECR repo, and IAM roles."
  type        = string
  default     = "ai-family-media-bot"
}

variable "web_instance_type" {
  description = "Instance type for the always-on web node group. The web tier only receives webhooks, enqueues, and serves health/metrics — t3.small is plenty."
  type        = string
  default     = "t3.small"
}

variable "worker_instance_types" {
  description = "Instance types for the Spot worker node group. Several similar types are listed on purpose: Spot capacity is per-pool, so diversification cuts interruption rates."
  type        = list(string)
  default     = ["t3.medium", "t3a.medium"]
}

variable "worker_max_size" {
  description = "Upper bound for the worker node group. Workers scale 0 -> N on SQS queue depth (KEDA scales the pods, cluster-autoscaler follows with nodes); this caps the blast radius of a queue flood."
  type        = number
  default     = 5
}

variable "bedrock_text_model_id" {
  description = "Bedrock model id for story generation. Ids prefixed 'us.' are cross-region inference profiles (see irsa.tf for the IAM implications). Must match BEDROCK_TEXT_MODEL_ID in the app config."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "bedrock_image_model_id" {
  description = <<-EOT
    Bedrock model id for illustration generation. Must match BEDROCK_IMAGE_MODEL_ID in the app config.
    NOTE (2026-07): amazon.nova-canvas-v1:0 is lifecycle LEGACY. As of this date there is NO
    active text-to-image model in us-east-1; active successors (stability.stable-image-core-v1:1,
    ~same price) are us-west-2 only with no cross-region inference profile. Staying on legacy
    in-region keeps traffic on the bedrock-runtime VPC endpoint. Revisit when AWS announces EOL
    or ships an in-region successor. Migration is a default change here + ImageProvider schema tweak.
  EOT
  type        = string
  default     = "amazon.nova-canvas-v1:0"
}
