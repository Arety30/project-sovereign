import os
import asyncio
import sys
import unittest

# Adjust path to import backend modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core.llm import LLMClient
from backend.core.agent import AgentCore, AgentState
from backend.tools.system import list_directory, read_file, write_file, run_terminal_command

class TestSovereignCore(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # We will use qwen2.5-coder:1.5b for tests to ensure it works on the existing models
        self.llm = LLMClient(default_model="qwen2.5-coder:1.5b")
        self.agent = AgentCore(self.llm, session_id="test_session")
        
    async def test_01_system_tools(self):
        print("\n--- Testing System Tools ---")
        test_file = "tests/temp_test_file.txt"
        
        # 1. Test write_file
        write_res = write_file(test_file, "Line 1: Hello sovereign offline agent!\nLine 2: Testing system tools.\nLine 3: End of file.")
        print(write_res)
        self.assertIn("Successfully wrote", write_res)
        
        # 2. Test read_file
        read_res = read_file(test_file, start_line=1, end_line=2)
        print(read_res)
        self.assertIn("Hello sovereign offline agent!", read_res)
        self.assertIn("Testing system tools.", read_res)
        self.assertNotIn("End of file", read_res)
        
        # 3. Test list_directory
        list_res = list_directory("tests")
        print("List directory outputs:")
        print(list_res)
        self.assertIn("temp_test_file.txt", list_res)
        
        # 4. Test terminal command security safeguards
        sec_res = await run_terminal_command("rm -rf /")
        print(sec_res)
        self.assertIn("Security Error: Command blocked", sec_res)
        
        # Cleanup
        if os.path.exists(test_file):
            os.remove(test_file)

    async def test_02_llm_client_connection(self):
        print("\n--- Testing LLM Client Connection ---")
        connected = await self.llm.check_connection()
        print(f"Ollama connected: {connected}")
        self.assertTrue(connected, "Ollama server is not running on localhost:11434!")
        
        if connected:
            available = await self.llm.get_available_models()
            print(f"Available models: {available}")
            self.assertGreater(len(available), 0, "No models pulled in Ollama!")

    async def test_03_agent_react_step(self):
        print("\n--- Testing Agent ReAct Loop ---")
        # Register a tool
        self.agent.register_tool("write_file", write_file)
        self.agent.register_tool("list_directory", list_directory)
        
        # Initialize task
        await self.agent.initialize_task("Write a greeting to greet_agent.txt containing 'Hello World'")
        print(f"Agent state: {self.agent.state}")
        print(f"Agent checklist: {self.agent.checklist}")
        self.assertEqual(self.agent.state, AgentState.EXECUTING)
        
        # Run one step
        step_res = await self.agent.step()
        print("Agent step log:")
        print(f"Thought: {step_res.get('thought')}")
        print(f"Action: {step_res.get('action')}")
        print(f"Args: {step_res.get('args')}")
        print(f"Observation: {step_res.get('observation')}")
        
        # Clean up files created
        created_file = "greet_agent.txt"
        if os.path.exists(created_file):
            print(f"Success! Agent successfully wrote: {created_file}")
            os.remove(created_file)
        elif os.path.exists("backend/core/greet_agent.txt"):
            os.remove("backend/core/greet_agent.txt")
        elif os.path.exists("backend/greet_agent.txt"):
            os.remove("backend/greet_agent.txt")
            
        self.assertIsNotNone(step_res.get("thought"))

if __name__ == "__main__":
    unittest.main()
