import os
import uuid
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'report_history')

class ReportRequest(BaseModel):
    messages: list
    persona: str
    user_id: str

import threading
tasks = {}

from agents.orchestrator_agent import check_clarity_only, run_generation_pipeline

@app.post("/generate")
async def generate_report(req: ReportRequest):
    clarity = check_clarity_only(req.messages, req.persona)
    if "error" in clarity:
        return {"error": clarity["error"]}
        
    task_id = str(uuid.uuid4())
    def bg_task():
        try:
            tasks[task_id] = run_generation_pipeline(req.messages, req.persona, req.user_id, DATA_DIR)
        except Exception as exc:
            tasks[task_id] = {"error": f"Report generation failed: {exc}"}
        
    threading.Thread(target=bg_task).start()
    
    clarity["task_id"] = task_id
    return clarity

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    if task_id in tasks:
        result = tasks[task_id]
        if "error" in result:
            return {"error": result["error"]}
        return {"report_id": result.get("report_id"), "status": "success"}
    return {"status": "processing"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
