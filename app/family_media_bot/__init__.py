"""AI Family Media Bot — runnable application stub.

Web tier receives Telegram webhooks and enqueues jobs; a worker consumes the
queue and produces a short bedtime story + one illustration. Everything that
touches AWS (queue, story, image, storage) sits behind an interface so the stub
runs locally with fakes and swaps to AWS later without changing app logic.
"""

__version__ = "0.1.0"
