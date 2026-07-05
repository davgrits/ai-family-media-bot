# EKS cluster. Community module: the cluster + node group wiring (security
# groups, IAM for nodes, launch templates, OIDC provider) is exactly the
# boilerplate-heavy part; the design decisions live in the inputs below.

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = local.cluster_name
  cluster_version = "1.31"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets # nodes live in private subnets only

  # Endpoint: PUBLIC access, no CIDR restriction. Deliberate: the admin has a
  # dynamic home IP, and access is still gated by IAM authentication + TLS —
  # an open endpoint is not an open cluster. Hardened alternatives, not used
  # here: (a) cluster_endpoint_public_access_cidrs allowlist — breaks on
  # every ISP-assigned IP change; (b) private-only endpoint + VPN/bastion —
  # standing cost and operational weight a one-person project doesn't repay.
  cluster_endpoint_public_access  = true
  cluster_endpoint_private_access = true # nodes/pods reach the API privately

  # OIDC provider for IRSA — the auth backbone of irsa.tf: pods get AWS
  # credentials via their service account, no long-lived keys anywhere.
  enable_irsa = true

  # v20 uses EKS access entries; without this the identity running terraform
  # can create the cluster but not talk to it with kubectl.
  enable_cluster_creator_admin_permissions = true

  # Managed core addons, pinned to "most recent" rather than left to the
  # implicit self-managed defaults — upgrades become an explicit plan diff.
  cluster_addons = {
    coredns    = { most_recent = true }
    kube-proxy = { most_recent = true }
    vpc-cni    = { most_recent = true }
  }

  eks_managed_node_groups = {
    # Web tier: always warm — receives Telegram webhooks, enqueues, serves
    # health/metrics. On-demand because a Spot interruption here means
    # dropped webhooks.
    web = {
      instance_types = [var.web_instance_type]
      capacity_type  = "ON_DEMAND"

      min_size     = 1
      desired_size = 1
      max_size     = 2 # headroom for the rolling replacement during node group updates

      labels = {
        role = "web"
      }
    }

    # Worker tier: scales 0 -> N on SQS queue depth. KEDA (installed later via
    # Helm) scales the worker *pods*; cluster-autoscaler then adds/removes
    # *nodes* to fit them. SPOT: generation jobs are retryable by design
    # (SQS redrive, see sqs.tf), which is the textbook Spot workload —
    # ~70% cheaper compute for the expensive tier.
    workers = {
      instance_types = var.worker_instance_types
      capacity_type  = "SPOT"

      min_size     = 0
      desired_size = 0 # idle cluster costs nothing on the worker side
      max_size     = var.worker_max_size

      labels = {
        role = "worker"
      }

      # Taint keeps everything except the job workers (which tolerate it) off
      # these nodes — otherwise a system pod could land here and pin a Spot
      # node alive, blocking scale-to-zero.
      taints = {
        jobs = {
          key    = "workload"
          value  = "jobs"
          effect = "NO_SCHEDULE"
        }
      }
    }
  }
}
