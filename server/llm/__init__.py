"""LLMs on NVIDIA's hosted API (build.nvidia.com), which speaks the OpenAI chat-completions protocol."""
from .nvidia import LlmError, chat, list_models, probe  # noqa: F401
