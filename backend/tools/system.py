import os
import subprocess
import shlex
import logging
from typing import List, Dict, Any

logger = logging.getLogger("sovereign.tools.system")

# Safety list to block destructive terminal commands
BANNED_SUBSTRINGS = [
    "rm -rf /", "rm -rf ~", "rm -rf /home", "rm -rf $HOME",
    "dd if=", "mkfs", "shutdown", "reboot", "init 0",
    "chmod -R 777 /", "chown -R", ":(){:|:&};:"
]

def list_directory(directory_path: str) -> str:
    """Lists files and folders in the target directory."""
    if not os.path.exists(directory_path):
        return f"Error: Path '{directory_path}' does not exist."
    if not os.path.isdir(directory_path):
        return f"Error: Path '{directory_path}' is not a directory."
        
    try:
        items = os.listdir(directory_path)
        result = []
        for item in sorted(items):
            full_path = os.path.join(directory_path, item)
            is_dir = os.path.isdir(full_path)
            size = os.path.getsize(full_path) if not is_dir else 0
            type_str = "DIR" if is_dir else "FILE"
            size_str = f" ({size} bytes)" if not is_dir else ""
            result.append(f"[{type_str}] {item}{size_str}")
        return "\n".join(result) if result else "Directory is empty."
    except Exception as e:
        return f"Error listing directory: {str(e)}"

def read_file(file_path: str, start_line: int = 1, end_line: int = 150) -> str:
    """Reads lines from a file from start_line to end_line (1-indexed)."""
    if not os.path.exists(file_path):
        return f"Error: File '{file_path}' does not exist."
    if os.path.isdir(file_path):
        return f"Error: Path '{file_path}' is a directory, not a file."
        
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            
        total_lines = len(lines)
        # Convert to 0-based indices
        start_idx = max(0, start_line - 1)
        end_idx = min(total_lines, end_line)
        
        if start_idx >= total_lines:
            return f"Error: File only has {total_lines} lines. Requested start line was {start_line}."
            
        subset = lines[start_idx:end_idx]
        output = []
        for i, line in enumerate(subset, start=start_line):
            output.append(f"{i}: {line.rstrip()}")
            
        header = f"--- Reading lines {start_line} to {end_idx} of {total_lines} (File: {os.path.basename(file_path)}) ---\n"
        return header + "\n".join(output)
    except Exception as e:
        return f"Error reading file: {str(e)}"

def write_file(file_path: str, content: str) -> str:
    """Writes content to a file (creates parent directories if needed)."""
    try:
        # Create directories if they do not exist
        parent_dir = os.path.dirname(file_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
            
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} characters to file: {file_path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"

async def run_terminal_command(command: str) -> str:
    """Runs a shell command and returns output (stdout/stderr). Includes security checks."""
    # Check for banned substrings
    for banned in BANNED_SUBSTRINGS:
        if banned in command:
            return f"Security Error: Command blocked. Found banned instruction: '{banned}'"
            
    try:
        logger.info(f"Executing terminal command: {command}")
        # Run command shell=True since it could involve pipes or redirections
        process = await subprocess.create_subprocess_shell(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="~/sovereign_agent"
        )
        stdout, stderr = await process.communicate()
        
        stdout_str = stdout.decode("utf-8", errors="ignore")
        stderr_str = stderr.decode("utf-8", errors="ignore")
        
        output = ""
        if stdout_str:
            output += f"STDOUT:\n{stdout_str}\n"
        if stderr_str:
            output += f"STDERR:\n{stderr_str}\n"
        if not output:
            output = "Command finished with empty output."
            
        return f"Exit Code: {process.returncode}\n{output}"
    except Exception as e:
        return f"Error executing command: {str(e)}"
