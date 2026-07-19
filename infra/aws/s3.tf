# Media bucket — the prod implementation of the app's StoragePort. Workers
# write generated PNGs to generated/<job_id>.png and send them to Telegram.

resource "aws_s3_bucket" "media" {
  # Account id suffix: bucket names are global, and this keeps the name unique
  # without hardcoding the account anywhere.
  bucket = "${var.project}-media-${data.aws_caller_identity.current.account_id}"
}

# Belt-and-braces: nothing in this bucket is ever public, and no future
# bucket policy can accidentally make it so.
resource "aws_s3_bucket_public_access_block" "media" {
  bucket = aws_s3_bucket.media.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-S3 (AES256): encryption at rest with zero key management. KMS would add
# per-request cost and an extra IAM surface for no real gain here — the
# threat model is "private family media", not regulated data.
resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Versioning is deliberately OFF (no versioning resource = unversioned):
# generated media is reproducible on demand, so keeping old object versions
# would only double storage cost for content nobody can even re-request.

# FinOps: generated media is ephemeral — it is delivered to Telegram within
# seconds of creation. 30 days is a comfortable window for debugging and
# re-sending; after that, objects are pure storage cost.
resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    id     = "expire-generated-media"
    status = "Enabled"

    filter {} # whole bucket — everything in here is ephemeral

    expiration {
      days = 30
    }

    # Failed multipart uploads otherwise linger invisibly and bill forever.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
