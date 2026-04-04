# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Capability-group routing module.

Uses a lightweight model to select capability groups by task semantics,
reducing main-model context overhead.
"""

import json
import logging
from typing import List, Optional

from fairyclaw.config import settings
from fairyclaw.core.capabilities.registry import CapabilityRegistry
from fairyclaw.infrastructure.llm.factory import create_llm_client

logger = logging.getLogger(__name__)


class ToolRouter:
    """Select capability groups for delegated tasks based on user intent."""

    def __init__(self, registry: CapabilityRegistry):
        """Initialize router dependencies.

        Args:
            registry (CapabilityRegistry): Capability registry that provides group metadata.

        Returns:
            None
        """
        self.registry = registry
        self.llm_client = create_llm_client(profile_name=settings.router_profile_name)

    async def select_groups(self, user_input: str) -> Optional[List[str]]:
        """Select capability groups to enable for current delegated task.

        Args:
            user_input (str): Delegation instruction text.

        Returns:
            Optional[List[str]]: Selected groups including sub-agent baseline groups.
            Falls back to full candidate set on routing errors.

        Raises:
            Exceptions from routing model are caught internally and converted to fallback result.
        """
        profiles = self.registry.get_group_profiles()

        baseline_groups = [p["group_name"] for p in profiles if p.get("always_enable_subagent", False)]
        candidate_profiles = [p for p in profiles if not p.get("always_enable_subagent", False)]

        if not candidate_profiles:
            logger.info(f"No non-core capability groups to route. Returning baseline groups: {baseline_groups}")
            return baseline_groups

        prompt = self._build_prompt(user_input, candidate_profiles)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Router Input Prompt:\n{prompt}")

        try:
            response_text = await self.llm_client.chat(messages=[{"role": "user", "content": prompt}])
            text = response_text.strip()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Router Raw Output:\n{text}")

            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            selected_groups = json.loads(text)
            if not isinstance(selected_groups, list):
                logger.warning(f"Router returned non-list: {selected_groups}")
                return baseline_groups + [p["group_name"] for p in candidate_profiles]

            valid_names = {p["group_name"] for p in candidate_profiles}
            result = [g for g in selected_groups if g in valid_names]
            final_result = list(set(baseline_groups + result))
            logger.info(f"Router selected groups: {final_result}")
            return final_result

        except Exception as e:
            error_details = str(e)
            if hasattr(e, "response") and hasattr(e.response, "text"):
                error_details += f" | Status: {e.response.status_code} | Response: {e.response.text}"
            logger.error(f"Router failed to select groups: {error_details}")
            return baseline_groups + [p["group_name"] for p in candidate_profiles]

    def _build_prompt(self, user_input: str, profiles: List[dict]) -> str:
        """Build routing prompt for lightweight selector model.

        Args:
            user_input (str): Delegation instruction text.
            profiles (List[dict]): Candidate capability group profiles.

        Returns:
            str: Prompt text requiring JSON-array output.
        """
        prompt = (
            "You are a system routing brain. The user's current goal is:\n"
            f"\"{user_input}\"\n\n"
            "We have the following capability groups available to call:\n"
        )

        for idx, p in enumerate(profiles, 1):
            prompt += f"{idx}. {p['group_name']}: {p['description']}\n"

        prompt += (
            "\nBased on the user's goal, please select the capability groups that might need to be enabled for this task.\n"
            "Please be restrained and only select truly relevant groups.\n"
            "Output the result as a JSON array of strings containing the group names. For example: [\"CoreOperations\", \"DatabaseTools\"].\n"
            "Do not output any other text besides the JSON array."
        )
        return prompt
