import json
import httpx
import logging
import re
from typing import List, Dict, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sovereign.llm")

class LLMClient:
    """
    LLM Client for managing offline local Ollama interaction.
    Designed with fallbacks and JSON parsing recovery for 1B-3B models.
    """
    def __init__(self, base_url: str = "http://localhost:11434", default_model: str = "qwen2.5-coder:3b"):
        self.base_url = base_url
        self.default_model = default_model
        self.fallback_models = [
            "qwen2.5-coder:1.5b",
            "deepseek-r1:1.5b",
            "granite3.2-vision:2b",
            "llama3.2:1b",
            "gemma3:1b"
        ]

    async def check_connection(self) -> bool:
        """Checks if local Ollama server is up."""
        async with httpx.AsyncClient(timeout=2.0) as client:
            try:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
            except Exception:
                return False

    async def get_available_models(self) -> List[str]:
        """Returns a list of all locally pulled Ollama models."""
        async with httpx.AsyncClient(timeout=3.0) as client:
            try:
                response = await client.get(f"{self.base_url}/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    return [m["name"] for m in data.get("models", [])]
            except Exception as e:
                logger.error(f"Failed to fetch available models: {e}")
            return []

    def truncate_context(self, messages: List[Dict[str, str]], max_chars: int = 16000) -> List[Dict[str, str]]:
        """
        Trims message history to prevent context window overflow.
        Always preserves the system message (typically index 0) and newest messages.
        """
        system_msg = None
        other_msgs = []
        
        for msg in messages:
            if msg.get("role") == "system":
                system_msg = msg
            else:
                other_msgs.append(msg)
                
        # Estimate character length
        total_chars = len(system_msg.get("content", "")) if system_msg else 0
        keep_msgs = []
        
        # Iterate backwards through other messages and keep until we hit max_chars
        for msg in reversed(other_msgs):
            msg_len = len(msg.get("content", ""))
            if total_chars + msg_len <= max_chars:
                keep_msgs.insert(0, msg)
                total_chars += msg_len
            else:
                break
                
        final_msgs = []
        if system_msg:
            final_msgs.append(system_msg)
        final_msgs.extend(keep_msgs)
        return final_msgs

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        json_mode: bool = False,
        max_tokens: int = 2048
    ) -> str:
        """
        Sends chat completion request to local Ollama.
        Retries with fallback models if the target model is missing or fails.
        """
        # Determine target model
        target_model = model or self.default_model
        available = await self.get_available_models()
        
        # Resolve target model fallback if not pulled
        if target_model not in available and f"{target_model}:latest" not in available:
            logger.warning(f"Target model '{target_model}' not found in local Ollama.")
            # Search fallback
            found_fallback = False
            for fb in [self.default_model] + self.fallback_models:
                if fb in available or f"{fb}:latest" in available:
                    logger.info(f"Using fallback model: {fb}")
                    target_model = fb
                    found_fallback = True
                    break
            if not found_fallback and available:
                logger.info(f"Fallback to first available model: {available[0]}")
                target_model = available[0]

        # Truncate context to keep it safe for local inference speeds
        truncated_msgs = self.truncate_context(messages)

        payload = {
            "model": target_model,
            "messages": truncated_msgs,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "stream": False
        }

        # Keep json formatting optional, as it can confuse small 1B/3B models if prompt structure is rigid
        if json_mode:
            payload["format"] = "json"

        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
                return data.get("message", {}).get("content", "")
            except Exception as e:
                logger.error(f"Error calling local model '{target_model}': {e}")
                # Try fallback models immediately
                for fb in self.fallback_models:
                    if fb != target_model and (fb in available or f"{fb}:latest" in available):
                        logger.info(f"Retrying request with fallback '{fb}' due to error.")
                        payload["model"] = fb
                        try:
                            response = await client.post(f"{self.base_url}/api/chat", json=payload)
                            if response.status_code == 200:
                                return response.json().get("message", {}).get("content", "")
                        except Exception as retry_err:
                            logger.error(f"Fallback '{fb}' failed: {retry_err}")
                raise e

    def parse_json_from_response(self, text: str) -> Dict[str, Any]:
        """
        Utility to clean and parse JSON blocks from LLM text responses.
        Extremely important for 1B-3B models which may include markdown wrappers.
        """
        # Try raw json load
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # Try extracting code block ```json ... ```
        pattern = r"```json\s*(.*?)\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try generic markdown code block ``` ... ```
        pattern_generic = r"```\s*(.*?)\s*```"
        match_generic = re.search(pattern_generic, text, re.DOTALL)
        if match_generic:
            try:
                return json.loads(match_generic.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Attempt to isolate anything between { and }
        brace_pattern = r"(\{.*\})"
        match_brace = re.search(brace_pattern, text, re.DOTALL)
        if match_brace:
            try:
                return json.loads(match_brace.group(1).strip())
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not extract valid JSON from LLM response: {text[:200]}...")

    async def get_embedding(self, text: str, model: str = "nomic-embed-text:latest") -> List[float]:
        """Fetches vector embedding for the input text using local Ollama model."""
        payload = {
            "model": model,
            "prompt": text
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(f"{self.base_url}/api/embeddings", json=payload)
                if response.status_code == 200:
                    return response.json().get("embedding", [])
                else:
                    # Try fallback to embed endpoint
                    response = await client.post(f"{self.base_url}/api/embed", json={"model": model, "input": text})
                    if response.status_code == 200:
                        embeddings = response.json().get("embeddings", [])
                        if embeddings:
                            return embeddings[0]
            except Exception as e:
                logger.error(f"Embedding generation failed: {e}")
            return []
