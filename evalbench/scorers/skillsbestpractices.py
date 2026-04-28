"""
SkillsBestPractices

LLM-based scorer that evaluates SKILL.md quality against best practices:
name compliance, description quality, body completeness, no TODOs,
and progressive disclosure design.
"""
from typing import Tuple, Any
import logging
import os
import re
import json
from scorers import comparator
from generators.models import get_generator
from util.config import load_yaml_config
from .prompt.skillsbestpractices import SKILLS_BEST_PRACTICES_PROMPT


class SkillsBestPractices(comparator.Comparator):
    """
    Evaluates the SKILL.md file for each activated skill against best practices.

    Configuration (under scorers.skills_best_practices in run YAML):
      model_config: path/to/model.yaml   (required)
      skills_dir: /path/to/skills/dir    (optional; defaults to model config setup.skills_dir)

    The scorer iterates over all activated skills (from accumulated_skills),
    reads each skill's SKILL.md from skills_dir/<skill_name>/SKILL.md,
    and scores it. The final score is the mean across all evaluated skills.
    """

    def __init__(self, config: dict, global_models):
        self.name = "skills_best_practices"
        self.model_config = config.get("model_config") or ""
        if not self.model_config:
            raise ValueError("model_config is required for SkillsBestPractices")

        # Try to get skills_dir from config, fallback to model config setup
        self.skills_dir = config.get("skills_dir") or ""
        if not self.skills_dir:
            model_config_data = load_yaml_config(self.model_config)
            self.skills_dir = model_config_data.get("setup", {}).get("skills_dir") or ""

        # Fallback to Claude's default plugin/skills directory
        if not self.skills_dir:
            home = os.path.expanduser("~")
            default_skills_dir = os.path.join(home, ".claude", "plugins", "cache")
            if os.path.isdir(default_skills_dir):
                self.skills_dir = default_skills_dir
                logging.info(f"Using default Claude skills directory: {self.skills_dir}")

        # Fallback to fake_home skills directory (.venv/fake_home_claude/.claude/skills)
        if not self.skills_dir:
            fake_home_skills = os.path.join(".venv", "fake_home_claude", ".claude", "skills")
            if os.path.isdir(fake_home_skills):
                self.skills_dir = os.path.abspath(fake_home_skills)
                logging.info(f"Using fake_home skills directory: {self.skills_dir}")

        if not self.skills_dir:
            raise ValueError(
                "skills_dir not found: not configured in scorer/model config, and Claude default directory ~/.claude/plugins/cache not found"
            )

        self.model = get_generator(global_models, self.model_config)

    def _find_skill_md(self, skill_name: str) -> str | None:
        """Resolves the SKILL.md path for a given skill name.

        Searches self.skills_dir for a subdirectory whose name matches skill_name,
        then returns the path to its SKILL.md. Returns None if not found.
        """
        # Direct match
        candidate = os.path.join(self.skills_dir, skill_name, "SKILL.md")
        if os.path.exists(candidate):
            return candidate
        # Case-insensitive fallback
        if os.path.isdir(self.skills_dir):
            for entry in os.listdir(self.skills_dir):
                if entry.lower() == skill_name.lower():
                    candidate = os.path.join(
                        self.skills_dir, entry, "SKILL.md"
                    )
                    if os.path.exists(candidate):
                        return candidate
        return None

    def _score_skill(self, skill_name: str) -> Tuple[float, str]:
        skill_md_path = self._find_skill_md(skill_name)
        if not skill_md_path:
            return 0.0, f"SKILL.md not found for skill '{skill_name}' in {self.skills_dir}"

        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                skill_md_content = f.read()
        except OSError as e:
            return 0.0, f"Failed to read SKILL.md for '{skill_name}': {e}"

        prompt = SKILLS_BEST_PRACTICES_PROMPT.format(
            skill_md_content=skill_md_content,
            skill_dir_name=skill_name,
        )

        try:
            response = self.model.generate(prompt)
            response_text = getattr(response, "stdout", response) if response else ""
            if not isinstance(response_text, str):
                logging.error(f"Failed to parse LLM response for '{skill_name}': not a string")
                return 0.0, "Failed to parse LLM response."

            logging.debug(f"Full LLM response for '{skill_name}': {response_text[:500]}")

            score_match = re.search(r"Score:\s*(\d+)", response_text)
            if score_match:
                score = float(min(100, max(0, int(score_match.group(1)))))
                # Extract detailed breakdown sections if present
                breakdown = []
                for line in response_text.split('\n'):
                    if any(x in line for x in ['**Metadata', '**Conciseness', '**Progressive', '**Clarity', '**Content', '**Summary']):
                        breakdown.append(line)
                detail_text = '\n'.join(breakdown) if breakdown else response_text[:500]
                logging.info(f"Score for '{skill_name}': {score:.0f}\nDetails: {detail_text[:200]}")
                return score, detail_text or response_text
            logging.error(f"Could not extract numeric score from response for '{skill_name}': {response_text[:200]}")
            return 0.0, f"Could not extract numeric score from response: {response_text[:200]}"
        except Exception as e:
            logging.error(f"SkillsBestPractices LLM call failed for '{skill_name}': {e}")
            return 0.0, f"Error calling model: {e}"

    def compare(
        self,
        nl_prompt: Any,
        golden_query: Any,
        query_type: Any,
        golden_execution_result: Any,
        golden_eval_result: Any,
        golden_error: Any,
        generated_query: Any,
        generated_execution_result: Any,
        generated_eval_result: Any,
        generated_error: Any,
    ) -> Tuple[float, str]:
        if generated_error:
            return 0.0, f"Generation error: {generated_error}"

        try:
            context = (
                json.loads(generated_eval_result)
                if isinstance(generated_eval_result, str)
                else generated_eval_result
            )
        except (json.JSONDecodeError, TypeError):
            return 0.0, "Invalid or missing eval result context."

        accumulated_skills = context.get("accumulated_skills", []) or []

        if not accumulated_skills:
            return 100.0, "No skills were activated; best practices check skipped."

        scores = []
        explanations = []
        logging.info(f"Evaluating {len(accumulated_skills)} skill(s) for best practices: {accumulated_skills}")
        for skill_name in accumulated_skills:
            score, explanation = self._score_skill(skill_name)
            scores.append(score)
            explanations.append(f"[{skill_name}] Score={score:.0f}: {explanation[:300]}")
            logging.info(f"  {skill_name}: {score:.0f} - {explanation[:100]}")

        final_score = sum(scores) / len(scores) if scores else 0.0
        summary = f"Mean best practices score across {len(scores)} skill(s): {final_score:.2f}\n"
        summary += "\n".join(explanations)
        logging.info(f"Final best practices score: {final_score:.2f}")
        logging.info(f"Summary: {summary}")
        return final_score, summary
