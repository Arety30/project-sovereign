import logging
from backend.core.llm import LLMClient
from backend.core.agent import AgentCore
from backend.tools.system import list_directory, read_file, write_file, run_terminal_command
from backend.tools.browser import (
    browser_navigate,
    browser_click,
    browser_type,
    browser_scroll,
    browser_take_screenshot,
    browser_get_html
)

logger = logging.getLogger("sovereign.agent_factory")

def create_agent(session_id: str = "default_session", model: str = "qwen2.5-coder:1.5b") -> AgentCore:
    """
    Factory function to initialize the Sovereign Agent Core 
    and register all filesystem and browser automation tools.
    """
    # Initialize the LLM Client (defaults to Ollama)
    llm = LLMClient(default_model=model)
    
    # Initialize the Agent
    agent = AgentCore(llm, session_id=session_id)
    
    # 1. Register Core System Tools
    agent.register_tool("list_directory", list_directory)
    agent.register_tool("read_file", read_file)
    agent.register_tool("write_file", write_file)
    agent.register_tool("run_terminal_command", run_terminal_command)
    
    # 2. Register Chromium Browser Tools
    agent.register_tool("browser_navigate", browser_navigate)
    agent.register_tool("browser_click", browser_click)
    agent.register_tool("browser_type", browser_type)
    agent.register_tool("browser_scroll", browser_scroll)
    agent.register_tool("browser_take_screenshot", browser_take_screenshot)
    agent.register_tool("browser_get_html", browser_get_html)
    
    logger.info(f"Successfully constructed Sovereign Agent (Session: {session_id}, Model: {model}) with all tools loaded.")
    return agent
