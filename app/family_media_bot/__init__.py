"""AI Family Media Bot.

The web tier receives Telegram updates and enqueues jobs; a worker consumes the
queue and produces a short bedtime story plus one illustration. Every cloud
dependency (queue, story, image, storage) sits behind an interface, so the same
app logic runs on a laptop with fakes and on GKE against real GCP services.
"""

__version__ = "0.1.0"
