import os
import asyncio
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from backend.core.llm import LLMClient
from backend.core.agent import AgentState
from backend.core.agent_factory import create_agent
from backend.core.memory import LocalMemory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sovereign.api")

app = FastAPI(title="Sovereign Agent API", version="1.0.0")

# Enable CORS for local file loads or dashboard dev servers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global runtime state
llm_client = LLMClient(default_model="qwen2.5-coder:1.5b")
active_agent = None
active_task = None
memory_db = LocalMemory(llm_client=llm_client)

class ConnectionManager:
    """Manages WebSocket connections and broadcasts real-time agent streams."""
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Active: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)

manager = ConnectionManager()

class TaskRequest(BaseModel):
    task: str
    session_id: Optional[str] = "default_session"
    model: Optional[str] = "qwen2.5-coder:1.5b"

async def run_agent_loop(task: str, session_id: str, model_name: str):
    """Asynchronous background worker coordinating the agent run loop."""
    global active_agent, active_task
    try:
        active_agent = create_agent(session_id, model=model_name)
        await active_agent.initialize_task(task)
        
        # Broadcast initial checklist and state
        await manager.broadcast({
            "type": "status",
            "data": active_agent.get_status()
        })
        
        step_limit = 25
        for i in range(step_limit):
            if active_agent.state in [AgentState.COMPLETED, AgentState.FAILED, AgentState.PAUSED]:
                break
                
            # Broadcast state
            await manager.broadcast({
                "type": "state_change",
                "data": {"state": active_agent.state}
            })
            
            # Execute step
            step_log = await active_agent.step()
            
            # Save step to SQLite persistence
            memory_db.save_step(
                session_id=session_id,
                step_index=step_log.get("step", 0),
                thought=step_log.get("thought", ""),
                action=step_log.get("action", ""),
                args=step_log.get("args", {}),
                observation=step_log.get("observation", ""),
                reflection=step_log.get("reflection", "")
            )
            
            # Save session summary state
            memory_db.save_session(
                session_id=session_id,
                task=task,
                checklist=active_agent.checklist,
                current_step=active_agent.current_step_index,
                state=active_agent.state
            )

            # Broadcast step event details
            await manager.broadcast({
                "type": "step",
                "data": step_log
            })
            
            # Brief sleep between steps to let UI catch up
            await asyncio.sleep(1.0)
            
        if active_agent.state == AgentState.EXECUTING:
            active_agent.state = AgentState.COMPLETED
            
        await manager.broadcast({
            "type": "status",
            "data": active_agent.get_status()
        })
        logger.info("Agent run loop complete.")
    except Exception as e:
        logger.error(f"Error in background run loop: {e}", exc_info=True)
        if active_agent:
            active_agent.state = AgentState.FAILED
        await manager.broadcast({
            "type": "error",
            "data": {"error": str(e)}
        })
    finally:
        active_task = None

@app.get("/status")
async def get_status():
    """Returns general service health and active agent stats."""
    ollama_ok = await llm_client.check_connection()
    models = await llm_client.get_available_models()
    
    agent_info = None
    if active_agent:
        agent_info = active_agent.get_status()
        
    return {
        "ollama_connected": ollama_ok,
        "available_models": models,
        "agent_running": active_task is not None,
        "agent_info": agent_info
    }

@app.post("/run")
async def start_task(req: TaskRequest):
    """Starts the agent on a new task asynchronously."""
    global active_task
    if active_task is not None:
        raise HTTPException(status_code=400, detail="An agent task is already running.")
        
    # Start task in background
    active_task = asyncio.create_task(run_agent_loop(req.task, req.session_id, req.model))
    return {"message": "Agent task started successfully.", "session_id": req.session_id}

@app.post("/pause")
async def pause_task():
    """Pauses active agent execution."""
    global active_agent
    if not active_agent:
        raise HTTPException(status_code=400, detail="No active agent session.")
    active_agent.state = AgentState.PAUSED
    return {"message": "Agent paused."}

@app.post("/stop")
async def stop_task():
    """Aborts active agent execution."""
    global active_agent, active_task
    if active_task:
        active_task.cancel()
        active_task = None
    if active_agent:
        active_agent.state = AgentState.FAILED
    return {"message": "Agent execution stopped."}

@app.get("/")
async def read_root():
    """Serves the premium dashboard index HTML."""
    return FileResponse("~/sovereign_agent/frontend/index.html")

@app.get("/screenshot")
async def get_screenshot():
    """Fetches the latest browser automation view frame."""
    screenshot_path = "~/sovereign_agent/frontend/live_view.png"
    if os.path.exists(screenshot_path):
        return FileResponse(screenshot_path, media_type="image/png")
    # Return 1x1 transparent pixel if file is missing
    transparent_pixel = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
        b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfc\xcf\xc0\x00\x00"
        b"\x05\x00\x01\x07\xf5\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    from fastapi.responses import Response
    return Response(content=transparent_pixel, media_type="image/png")

@app.get("/search_memory")
async def search_memory(query: str, limit: int = 3):
    """Semantic vector lookup against SQLite nomic-embed database."""
    results = await memory_db.search(query, limit=limit)
    return [{"document": doc, "score": score} for doc, score in results]

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Establishes real-time log streaming session."""
    await manager.connect(websocket)
    # Send current status on connect
    if active_agent:
        await websocket.send_json({
            "type": "status",
            "data": active_agent.get_status()
        })
    try:
        while True:
            # Keep connection open and receive user command if needed
            data = await websocket.receive_text()
            logger.info(f"Received WS command: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WS error: {e}")
        manager.disconnect(websocket)
