# IRSA — IAM Roles for Service Accounts. Each in-cluster identity gets its
# own role, assumable only via the cluster's OIDC provider and only by one
# specific service account. No node-wide permissions, no access keys, no
# secrets: this is the "no API keys stored in secrets" requirement from the
# app contract, made concrete.
#
# RAW resources on purpose — least-privilege policy content is where the
# design lives, and it should be readable in full, not hidden behind a module.

locals {
  # role key => the exact service account (namespace:name) allowed to assume it
  irsa_service_accounts = {
    app                = "system:serviceaccount:app:family-media-bot"
    keda_operator      = "system:serviceaccount:keda:keda-operator"
    cluster_autoscaler = "system:serviceaccount:kube-system:cluster-autoscaler"
  }

  # Bedrock IAM subtlety: model ids prefixed "us." are cross-region inference
  # profiles, not plain models. Invoking through a profile requires
  # bedrock:InvokeModel on BOTH the profile ARN (account-scoped) AND the
  # underlying foundation model in every region the profile may route to.
  # Plain ids (e.g. amazon.titan-*) are just a foundation-model ARN in-region.
  us_inference_profile_regions = ["us-east-1", "us-east-2", "us-west-2"]

  bedrock_invoke_arns = distinct(flatten([
    for id in [var.bedrock_text_model_id, var.bedrock_image_model_id] :
    startswith(id, "us.")
    ? concat(
      ["arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/${id}"],
      [for r in local.us_inference_profile_regions : "arn:aws:bedrock:${r}::foundation-model/${trimprefix(id, "us.")}"]
    )
    : ["arn:aws:bedrock:${var.region}::foundation-model/${id}"]
  ]))
}

# Shared trust-policy shape: assumable only with a web identity token issued
# by THIS cluster's OIDC provider, for THIS service account, audience sts.
data "aws_iam_policy_document" "irsa_assume" {
  for_each = local.irsa_service_accounts

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:sub"
      values   = [each.value]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

# --- App role: the web + worker pods --------------------------------------

resource "aws_iam_role" "app" {
  name               = "${var.project}-app"
  description        = "IRSA role for the family-media-bot pods: Bedrock invoke, media bucket R/W, jobs queue"
  assume_role_policy = data.aws_iam_policy_document.irsa_assume["app"].json
}

data "aws_iam_policy_document" "app" {
  # Exactly the two configured models (and, for inference profiles, the
  # foundation models they route to) — not bedrock:* on *.
  statement {
    sid       = "InvokeConfiguredModels"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel"]
    resources = local.bedrock_invoke_arns
  }

  # Object read/write in the media bucket. The /* wildcard is the object key
  # path, not a resource wildcard — object-level actions require it.
  statement {
    sid    = "MediaObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.media.arn}/*"]
  }

  # ListBucket on the bucket itself: the /readyz probe does a HeadBucket to
  # verify S3 reachability, and HeadBucket authorizes against s3:ListBucket.
  statement {
    sid       = "MediaBucketProbe"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }

  # Full job lifecycle on the one jobs queue: web enqueues (Send), workers
  # consume (Receive/Delete), /readyz checks reachability (GetQueueAttributes).
  statement {
    sid    = "JobsQueue"
    effect = "Allow"
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.jobs.arn]
  }
}

resource "aws_iam_role_policy" "app" {
  name   = "least-privilege"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app.json
}

# --- KEDA operator role: reads queue depth to scale workers ---------------

resource "aws_iam_role" "keda_operator" {
  name               = "${var.project}-keda-operator"
  description        = "IRSA role for the KEDA operator: read jobs queue depth (scaling signal only)"
  assume_role_policy = data.aws_iam_policy_document.irsa_assume["keda_operator"].json
}

data "aws_iam_policy_document" "keda_operator" {
  # KEDA only ever *reads* the queue length — it must not be able to consume
  # or send messages.
  statement {
    sid       = "ReadQueueDepth"
    effect    = "Allow"
    actions   = ["sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.jobs.arn]
  }
}

resource "aws_iam_role_policy" "keda_operator" {
  name   = "least-privilege"
  role   = aws_iam_role.keda_operator.id
  policy = data.aws_iam_policy_document.keda_operator.json
}

# --- Cluster-autoscaler role: node scaling for the worker tier ------------

resource "aws_iam_role" "cluster_autoscaler" {
  name               = "${var.project}-cluster-autoscaler"
  description        = "IRSA role for cluster-autoscaler, scoped to this cluster's node groups by tag"
  assume_role_policy = data.aws_iam_policy_document.irsa_assume["cluster_autoscaler"].json
}

data "aws_iam_policy_document" "cluster_autoscaler" {
  # Describe* / Get* don't support resource-level permissions in IAM, so
  # Resource must be "*" here — the wildcard is an AWS constraint, not a
  # shortcut. All of these are read-only.
  statement {
    sid    = "ReadOnlyDiscovery"
    effect = "Allow"
    actions = [
      "autoscaling:DescribeAutoScalingGroups",
      "autoscaling:DescribeAutoScalingInstances",
      "autoscaling:DescribeLaunchConfigurations",
      "autoscaling:DescribeScalingActivities",
      "ec2:DescribeImages",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeLaunchTemplateVersions",
      "ec2:GetInstanceTypesFromInstanceRequirements",
      "eks:DescribeNodegroup", # lets CA read labels/taints for scale-from-zero on managed node groups
    ]
    resources = ["*"]
  }

  # The mutating actions are where scoping matters: allowed only on ASGs
  # tagged as belonging to THIS cluster. EKS managed node groups add the
  # k8s.io/cluster-autoscaler/<cluster-name> tag to their ASGs automatically,
  # which is also what CA's auto-discovery mode matches on.
  statement {
    sid    = "ScaleOwnNodeGroups"
    effect = "Allow"
    actions = [
      "autoscaling:SetDesiredCapacity",
      "autoscaling:TerminateInstanceInAutoScalingGroup",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/k8s.io/cluster-autoscaler/${module.eks.cluster_name}"
      values   = ["owned"]
    }
  }
}

resource "aws_iam_role_policy" "cluster_autoscaler" {
  name   = "least-privilege"
  role   = aws_iam_role.cluster_autoscaler.id
  policy = data.aws_iam_policy_document.cluster_autoscaler.json
}
