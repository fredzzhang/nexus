"""Use lazy imports to avoid scripts being loaded before execution"""
def __getattr__(name):
    if name == "generate_with_nano_banana":
        from .nano_banana_fal_ai import generate_with_nano_banana
        return generate_with_nano_banana
    if name == "generate_with_claude":
        from .claude_bedrock import generate_with_claude
        return generate_with_claude
    if name == "single_inference_with_claude":
        from .claude_bedrock import single_inference_with_claude
        return single_inference_with_claude
    raise AttributeError(f"module 'nexus.gen' has no attribute {name!r}")
