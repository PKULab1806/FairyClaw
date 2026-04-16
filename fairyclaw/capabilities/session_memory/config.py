from pydantic import BaseModel


class SessionMemoryRuntimeConfig(BaseModel):
    model_config = {"frozen": True}

    memory_root: str | None = None

    extraction_profile: str = "compaction_summarizer"
    compaction_profile: str = "compaction_summarizer"

    gap_headroom_items: int = 4
    min_gap_repair_cut_items: int = 3
    compaction_max_history_items: int = 120
    summary_char_limit: int = 1800
    summary_min_reserve_tokens: int = 256

    extract_trigger_message_count: int = 8
    extract_trigger_token_count: int = 2500
    extract_trigger_tool_round_count: int = 3
    extract_cooldown_turns: int = 2
    min_confidence_to_write_user: float = 0.7


runtime_config_model = SessionMemoryRuntimeConfig
