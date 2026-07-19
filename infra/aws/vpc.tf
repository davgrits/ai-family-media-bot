# Network. Community module: a VPC is boilerplate-heavy (route tables, NAT,
# IGW, subnet math) and carries no project-specific design decisions beyond
# the inputs below — exactly the case for terraform-aws-modules.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  # The cluster name is needed *before* the EKS module exists (subnet tags),
  # so it lives here as a plain string rather than a module output.
  cluster_name = var.project

  # 2 AZs: the minimum EKS accepts, and enough resilience for a personal
  # project. A third AZ would mostly add cross-AZ data charges.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = var.project
  cidr = "10.0.0.0/16"

  azs = local.azs
  # Private subnets are /20 (~4k IPs each): the VPC CNI assigns a real VPC IP
  # to every pod, so pods — not nodes — dominate address consumption.
  # Public subnets only hold the NAT gateway and any future load balancer,
  # so /24 is generous.
  private_subnets = ["10.0.0.0/20", "10.0.16.0/20"]
  public_subnets  = ["10.0.128.0/24", "10.0.129.0/24"]

  # SINGLE NAT gateway (not one per AZ) — a deliberate cost decision:
  # ~$32/month each, and the only egress that actually needs it is the
  # Telegram Bot API (all AWS traffic rides the VPC endpoints, see
  # endpoints.tf). The trade-off is that one AZ outage takes down Telegram
  # egress; acceptable for a personal project, and the comment is here so a
  # reviewer knows it was a choice, not an oversight.
  enable_nat_gateway = true
  single_nat_gateway = true

  # Required for VPC interface endpoints (private DNS) and for EKS in general.
  enable_dns_support   = true
  enable_dns_hostnames = true

  # Tags the EKS ecosystem expects for subnet discovery: the AWS load
  # balancer controller / in-tree provider picks internal-elb subnets for
  # internal Services, elb subnets for internet-facing ones, and the
  # cluster tag marks the subnets as usable by this cluster.
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb"             = "1"
    "kubernetes.io/cluster/${local.cluster_name}" = "shared"
  }
}
