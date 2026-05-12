"""Agentic Android scraper package.

LLM-driven exploration: the agent observes the device, decides which tap
to perform, executes it via ADB, observes again, and continues until its
goal is met or a budget is exhausted.

Public surface
--------------
- ``tools.AgentSession``   – holds device state + persists snapshots
- ``tools.TOOL_REGISTRY``  – name → callable mapping passed to the runner
- ``schemas.TOOL_SCHEMAS`` – OpenAI-format tool schemas
- ``llm_client.LLMClient`` – thin OpenAI-compatible chat client
- ``runner.run_agent``     – top-level orchestration
"""
