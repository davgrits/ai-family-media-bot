# Container registry — one repo, both tiers run the same image (RUN_MODE env
# var selects web vs worker behavior, per the app's contract).

resource "aws_ecr_repository" "app" {
  name = var.project

  # Immutable tags force one-tag-per-build (e.g. git SHA): what runs in the
  # cluster is always traceable to a commit, and "latest drift" — two
  # different images having worn the same tag — becomes impossible.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true # free basic CVE scan on every push
  }
}

# Keep the last 10 images: enough history to roll back a bad deploy, while
# stopping the repo from growing a few hundred MB with every CI run.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the 10 most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
