# Everything the next layers need: kubeconfig setup, CI (image push), the
# app's Kubernetes manifests (env vars + IRSA role annotations), and KEDA's
# ScaledObject (queue URL).

output "cluster_name" {
  description = "EKS cluster name (aws eks update-kubeconfig --name <this>)"
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint"
  value       = module.eks.cluster_endpoint
}

output "ecr_repository_url" {
  description = "ECR repo URL — CI pushes the app image here"
  value       = aws_ecr_repository.app.repository_url
}

output "jobs_queue_url" {
  description = "SQS jobs queue URL — the app's QUEUE config and KEDA's scaling target"
  value       = aws_sqs_queue.jobs.url
}

output "jobs_queue_arn" {
  description = "SQS jobs queue ARN"
  value       = aws_sqs_queue.jobs.arn
}

output "media_bucket_name" {
  description = "S3 media bucket — the app's S3_BUCKET config"
  value       = aws_s3_bucket.media.bucket
}

output "app_role_arn" {
  description = "IRSA role ARN to annotate on the app service account (app/family-media-bot)"
  value       = aws_iam_role.app.arn
}

output "keda_operator_role_arn" {
  description = "IRSA role ARN to annotate on the KEDA operator service account (keda/keda-operator)"
  value       = aws_iam_role.keda_operator.arn
}

output "cluster_autoscaler_role_arn" {
  description = "IRSA role ARN to annotate on the cluster-autoscaler service account (kube-system/cluster-autoscaler)"
  value       = aws_iam_role.cluster_autoscaler.arn
}

output "vpc_id" {
  description = "VPC id"
  value       = module.vpc.vpc_id
}
