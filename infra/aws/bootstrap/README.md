# Bootstrap — Terraform remote state (already done, by hand)

Terraform cannot create the bucket its own state lives in (chicken and egg),
so these three commands were run manually, exactly once, before the first
`terraform init`. The bucket below exists and is the one referenced by
[`../backend.tf`](../backend.tf); this file is the record of how it was
made (and the recipe, should the environment ever be rebuilt from scratch).

State locking is native S3 (`use_lockfile = true` in `../backend.tf`,
Terraform >= 1.10), so the state bucket is the only resource bootstrap
has to create.

```bash
# 1. State bucket (us-east-1 needs no LocationConstraint)
aws s3api create-bucket \
  --bucket family-media-bot-tfstate \
  --region us-east-1

# 2. Versioning on — every state revision is recoverable (cheap insurance
#    against a corrupted or mistakenly-overwritten state file)
aws s3api put-bucket-versioning \
  --bucket family-media-bot-tfstate \
  --versioning-configuration Status=Enabled

# 3. Block all public access — state files contain resource IDs and ARNs
aws s3api put-public-access-block \
  --bucket family-media-bot-tfstate \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

`../backend.tf` already carries this bucket name, and `terraform init` has
been run against it — nothing further to do here.
