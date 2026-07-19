# VPC endpoints — private paths to AWS services.
#
# Design intent: traffic gets a paid private path only when it earns it —
# constant volume or sensitive payloads. Interface endpoints cost ~$7.3/AZ/mo
# each just to exist, so each one below states WHY it is worth it, and the
# deliberately-excluded ones are documented at the bottom. The only traffic
# leaving through the (single) NAT gateway is the Telegram Bot API plus
# rare, non-sensitive AWS control traffic (image pulls, logs — see bottom).

# WHY S3: generated media is written to / read from the media bucket by the
# workers. Gateway type — it is free and hangs off the private route tables
# instead of billing per-ENI-hour like an Interface endpoint would.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids

  tags = {
    Name = "${var.project}-s3"
  }
}

# Interface endpoints (PrivateLink ENIs, billed hourly) — each entry names the
# service and the WHY. The value doubles as a Purpose tag on the endpoint.
locals {
  interface_endpoints = {
    # WHY bedrock-runtime: InvokeModel carries the most sensitive payloads in
    # the system — the family's prompts and the generated story/illustration.
    # These must never ride the public internet through the NAT.
    "bedrock-runtime" = "Bedrock InvokeModel - story and illustration generation"

    # WHY sts: IRSA. Pods exchange their projected service-account token for
    # AWS credentials via AssumeRoleWithWebIdentity on every refresh — this
    # is the auth path for Bedrock/S3/SQS access and must work privately.
    "sts" = "STS AssumeRoleWithWebIdentity - IRSA credential exchange"

    # WHY sqs: the jobs queue is the spine of the async pipeline — the web
    # tier enqueues every request and workers long-poll around the clock, and
    # the messages carry the family's prompt text. Constant, sensitive
    # traffic is exactly what should not be metered NAT traffic.
    "sqs" = "SQS jobs queue - enqueue and worker long-polling"
  }
}

# Deliberately EXCLUDED interface endpoints — a FinOps decision, not an
# oversight. At this project's traffic volume, these ride the NAT gateway
# ($0.045/GB) instead of paying a fixed ~$14.6/mo (2 AZs) each:
#   - ecr.api / ecr.dkr: image pulls happen only on deploys and node
#     launches — a few hundred MB/month, pennies of NAT traffic — and
#     container images are not sensitive. (Layer blobs come from S3 and
#     already use the free gateway endpoint above.)
#   - logs: low-volume, non-sensitive operational telemetry.
# Cutting these three halves the fixed endpoint bill (~$87 -> ~$44/mo) while
# keeping private paths for everything constant (SQS, STS) or carrying
# family content (Bedrock).

resource "aws_vpc_endpoint" "interface" {
  for_each = local.interface_endpoints

  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.vpc.private_subnets
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true # SDKs resolve the normal service hostname straight to the endpoint

  tags = {
    Name    = "${var.project}-${each.key}"
    Purpose = each.value
  }
}

# Dedicated SG for all interface endpoints: HTTPS in from the private subnets,
# nothing else. Endpoints only ever *receive* TLS from workloads, so no egress
# rules are defined at all.
resource "aws_security_group" "vpc_endpoints" {
  name_prefix = "${var.project}-vpce-"
  description = "HTTPS to VPC interface endpoints from private subnets"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description = "HTTPS from private subnets"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = module.vpc.private_subnets_cidr_blocks
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.project}-vpc-endpoints"
  }
}
