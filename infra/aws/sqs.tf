# Job queue — the prod implementation of the app's QueuePort
# (webhook enqueues, workers consume; KEDA scales workers on queue depth).
# RAW resources: the queue's tuning IS the design, so it stays visible.

resource "aws_sqs_queue" "jobs" {
  name = "${var.project}-jobs"

  # Generation takes seconds-to-minutes (two Bedrock calls + S3 + Telegram),
  # so a consumed message stays invisible for 5 minutes. Shorter would
  # redeliver jobs that are still mid-generation; much longer would delay
  # retries after a genuine worker crash.
  visibility_timeout_seconds = 300

  # Long polling: workers block up to 10s per ReceiveMessage instead of
  # hammering the API with empty receives — fewer requests, faster pickup.
  receive_wait_time_seconds = 10

  # A bedtime-story request that has sat unprocessed for a day is stale — the
  # evening is over. Don't generate (and pay Bedrock for) media nobody wants.
  message_retention_seconds = 86400

  # After 3 failed receives the job is parked in the DLQ instead of looping
  # forever: 3 attempts covers transient failures (Spot interruption, Bedrock
  # throttle) without burning money on a poison message.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "jobs_dlq" {
  name = "${var.project}-jobs-dlq"

  # Maximum retention: dead letters exist to be inspected, and debugging a
  # personal project is not a same-day activity.
  message_retention_seconds = 1209600 # 14 days
}

# Only the jobs queue may redrive into this DLQ — prevents other (future)
# queues from accidentally being pointed at it.
resource "aws_sqs_queue_redrive_allow_policy" "jobs_dlq" {
  queue_url = aws_sqs_queue.jobs_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.jobs.arn]
  })
}
