"""Adapters — concrete implementations of the ports.

Dev impls (fake/in-memory/local) let the stub run with no AWS; the Bedrock/SQS/S3
impls are honest stubs that get wired to AWS in a later session.
"""
