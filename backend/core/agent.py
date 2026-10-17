import json
import logging
import re
import traceback
from typing import List, Dict, Any, Tuple, Optional
from backend.core.llm import LLMClient
from backend.core.prompts import SYSTEM_PROMPT, PLANNING_PROMPT, REFLECTION_PROMPT

logger = logging.getLogger("sovereign.agent")

class AgentState:
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    REFLECTING = "reflecting"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"

class AgentCore:
    def __init__(self, llm_client: LLMClient, session_id: str = "default_session"):
        self.llm = llm_client
        self.session_id = session_id
        self.state = AgentState.IDLE
        self.task = ""
        self.checklist: List[str] = []
        self.current_step_index = 0
        self.history: List[Dict[str, str]] = []
        self.logs: List[Dict[str, Any]] = []
        self.tools_registry = {}
        
    def register_tool(self, name: str, func: Any):
        """Registers a executable tool function with the agent."""
        self.tools_registry[name] = func
        logger.info(f"Registered tool: {name}")

    def get_status(self) -> Dict[str, Any]:
        """Returns the current runtime status of the agent."""
        return {
            "session_id": self.session_id,
            "state": self.state,
            "task": self.task,
            "checklist": self.checklist,
            "current_step_index": self.current_step_index,
            "history_length": len(self.history),
            "logs": self.logs[-20:] # Return last 20 execution events
        }

    async def initialize_task(self, task: str):
        """Initializes a new task, plans steps using LLM, and sets up state."""
        self.task = task
        self.state = AgentState.PLANNING
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.logs = []
        self.current_step_index = 0
        
        logger.info(f"Initializing task: {task}")
        
        # Ask LLM to generate a plan
        plan_prompt = PLANNING_PROMPT.format(request=task)
        try:
            response = await self.llm.chat_completion(
                messages=[{"role": "user", "content": plan_prompt}],
                temperature=0.1
            )
            # Try parsing the checklist from response
            self.checklist = self.llm.parse_json_from_response(response)
            if not isinstance(self.checklist, list):
                self.checklist = [str(self.checklist)]
        except Exception as e:
            logger.error(f"Failed to plan checklist, using default single step: {e}")
            self.checklist = [f"Execute user task: {task}"]
            
        self.state = AgentState.EXECUTING
        # Append initial task to history
        self.history.append({
            "role": "user",
            "content": f"Task Checklist:\n" + "\n".join(f"- {step}" for step in self.checklist) + f"\n\nStart working on the task: {task}"
        })

    def parse_action(self, response_text: str) -> Tuple[str, Dict[str, Any], str]:
        """
        Parses response text to extract thoughts and action name/args.
        Robust fallback handling for small models.
        """
        thought = ""
        action_name = ""
        action_args = {}

        # 1. Try extracting thoughts
        thought_match = re.search(r"<thought>(.*?)</thought>", response_text, re.DOTALL | re.IGNORECASE)
        if thought_match:
            thought = thought_match.group(1).strip()
        else:
            # If no tags, check anything before the action block or treat the whole text as thought
            action_idx = response_text.find("<action")
            if action_idx != -1:
                thought = response_text[:action_idx].strip()
            else:
                thought = response_text.strip()

        # 2. Try extracting action name and args using <action name="..."> tag
        action_match = re.search(r"<action\s+name=[\"\'](.*?)[\"\']\s*>(.*?)</action>", response_text, re.DOTALL | re.IGNORECASE)
        if action_match:
            action_name = action_match.group(1).strip()
            args_str = action_match.group(2).strip()
            try:
                action_args = json.loads(args_str)
            except json.JSONDecodeError:
                # Try finding JSON inside arguments if malformed
                try:
                    action_args = self.llm.parse_json_from_response(args_str)
                except Exception:
                    logger.warning(f"Failed to parse action args JSON: {args_str}")
                    action_args = {"raw_value": args_str}
        else:
            # 3. Fallback: Check if response itself is raw JSON containing 'action' and 'args'
            try:
                raw_json = self.llm.parse_json_from_response(response_text)
                if isinstance(raw_json, dict) and ("action" in raw_json or "action_name" in raw_json):
                    action_name = raw_json.get("action") or raw_json.get("action_name", "")
                    action_args = raw_json.get("args") or raw_json.get("arguments", {})
                    thought = raw_json.get("thought", thought)
            except Exception:
                pass

        # 4. Critical fallback: if no action is parsed but model outputted task completion phrasing
        if not action_name:
            if "complete" in response_text.lower() or "done" in response_text.lower():
                action_name = "task_complete"
                action_args = {"output": response_text}
            else:
                # Run terminal fallback or write file fallback if it looks like code
                action_name = "no_action_parsed"
                action_args = {"content": response_text}

        return action_name, action_args, thought

    async def step(self) -> Dict[str, Any]:
        """
        Executes one step in the agent cycle:
        1. Run LLM to get thoughts and action.
        2. Execute tool.
        3. Capture observation.
        4. Reflect and update history.
        """
        if self.state in [AgentState.COMPLETED, AgentState.FAILED]:
            return {"status": "finished", "state": self.state}

        self.state = AgentState.EXECUTING
        logger.info("Starting agent execution step...")

        # 1. Call LLM
        response_text = ""
        try:
            response_text = await self.llm.chat_completion(
                messages=self.history,
                temperature=0.15
            )
        except Exception as e:
            self.state = AgentState.FAILED
            err_msg = f"LLM Inference failed: {str(e)}"
            logger.error(err_msg)
            return {"status": "error", "error": err_msg}

        # 2. Parse thoughts and action
        action_name, action_args, thought = self.parse_action(response_text)
        logger.info(f"Agent Thought: {thought}")
        logger.info(f"Agent Action: {action_name} | Args: {action_args}")

        # Add LLM response to history
        self.history.append({
            "role": "assistant",
            "content": response_text
        })

        # 3. Handle Special Complete Tool
        if action_name == "task_complete":
            self.state = AgentState.COMPLETED
            summary = action_args.get("output", "Task complete.")
            log_entry = {
                "step": self.current_step_index,
                "thought": thought,
                "action": action_name,
                "args": action_args,
                "observation": summary
            }
            self.logs.append(log_entry)
            return log_entry

        # 4. Execute tool if registered
        observation = ""
        if action_name in self.tools_registry:
            try:
                tool_func = self.tools_registry[action_name]
                # Executing tool async or sync
                if hasattr(tool_func, "__call__"):
                    if int(str(tool_func.__code__.co_flags)) & 0x80: # Check if coroutine
                        observation = await tool_func(**action_args)
                    else:
                        observation = tool_func(**action_args)
                else:
                    observation = f"Error: Tool '{action_name}' is not callable."
            except Exception as e:
                tb = traceback.format_exc()
                observation = f"Error executing tool '{action_name}': {str(e)}\nTraceback:\n{tb}"
                logger.error(observation)
        elif action_name == "no_action_parsed":
            observation = "Error: Could not identify tool call format. Please use <action name=\"tool_name\">{json}</action> explicitly."
        else:
            observation = f"Error: Tool '{action_name}' is not available. Please choose from registered tools."

        logger.info(f"Observation: {observation[:300]}...")

        # 5. Reflection Phase (Self-Correction for 1B-3B intelligence scaling)
        self.state = AgentState.REFLECTING
        try:
            ref_prompt = REFLECTION_PROMPT.format(
                last_thought=thought,
                last_action=action_name,
                last_action_args=json.dumps(action_args),
                observation=observation
            )
            reflection = await self.llm.chat_completion(
                messages=[{"role": "user", "content": ref_prompt}],
                temperature=0.1
            )
            logger.info(f"Self-Reflection: {reflection}")
        except Exception as e:
            reflection = f"Failed self-reflection helper: {e}"

        # Append observation to history
        self.history.append({
            "role": "user",
            "content": f"Observation from '{action_name}':\n{observation}\n\nReflection:\n{reflection}"
        })

        # Record log
        log_entry = {
            "step": self.current_step_index,
            "thought": thought,
            "action": action_name,
            "args": action_args,
            "observation": observation,
            "reflection": reflection
        }
        self.logs.append(log_entry)

        # Advance step counter
        self.current_step_index += 1
        self.state = AgentState.IDLE
        return log_entry
