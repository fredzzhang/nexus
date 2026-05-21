def __getattr__(name):
    if name == "monitor_bucket":
        from .bucket_monitoring import monitor_bucket
        return monitor_bucket
    raise AttributeError(f"module 'nexus.aws' has no attribute {name!r}")
