# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio
import os
import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Any, Dict, Optional
import httpx
import logging
import sys

# Configuration
CALLBACK_PORT = 8001
FAIRYCLAW_API_URL = "http://localhost:8000"
FAIRYCLAW_API_TOKEN = "sk-fairyclaw-dev-token"
TEST_ROOT_DIR = "/tmp/fairyclaw_test"
INPUT_FILE = os.path.join(TEST_ROOT_DIR, "input.txt")
OUTPUT_FILE = os.path.join(TEST_ROOT_DIR, "output.txt")

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SystemTest")

# FastAPI App for Callback
app = FastAPI()

class CallbackPayload(BaseModel):
    session_id: str
    role: str
    content: str
    type: str

@app.post("/callback")
async def handle_callback(payload: CallbackPayload):
    logger.info(f"Received Callback from Session {payload.session_id}")
    print(f"\n{'='*20} AGENT MESSAGE {'='*20}")
    print(f"Type: {payload.type}")
    print(f"Content: {payload.content}")
    print(f"{'='*55}\n")
    
    if payload.type == "file":
        print(f"[AGENT SENT FILE ID]: {payload.content}")

    return {"status": "ok"}

async def run_test_logic():
    # Wait for callback server to start
    await asyncio.sleep(2)
    
    logger.info("Starting Test Logic...")

    # 1. Create Test Environment
    if not os.path.exists(TEST_ROOT_DIR):
        try:
            os.makedirs(TEST_ROOT_DIR)
            logger.info(f"Created test directory: {TEST_ROOT_DIR}")
        except PermissionError:
            logger.error(f"Permission denied creating {TEST_ROOT_DIR}. Please run as appropriate user or change TEST_ROOT_DIR.")
            return

    # Create input file
    with open(INPUT_FILE, "w") as f:
        f.write("This is a secret message. The code is 42. Please extract the code.")
    logger.info(f"Created input file: {INPUT_FILE}")

    # 2. Create Session
    headers = {"Authorization": f"Bearer {FAIRYCLAW_API_TOKEN}"}
    async with httpx.AsyncClient(headers=headers) as client:
        try:
            logger.info("Creating Session...")
            session_resp = await client.post(
                f"{FAIRYCLAW_API_URL}/v1/sessions",
                json={
                    "platform": "test_script",
                    "title": "System Flow Test",
                    "meta": {"test": True}
                }
            )
            
            if session_resp.status_code != 201:
                logger.error(f"Failed to create session: {session_resp.status_code} {session_resp.text}")
                return
            
            session_data = session_resp.json()
            session_id = session_data["session_id"]
            logger.info(f"Session Created: {session_id}")

            # 3. Send Prompt
            prompt = (
                f"Please read the file '{INPUT_FILE}' using your read_file tool. "
                f"Analyze its content to find the secret code. "
                f"Then write the result to '{OUTPUT_FILE}' using your write_file tool. "
                f"Finally, call send_message to tell me you are done and what the code is."
            )
            
            logger.info(f"Sending Prompt: {prompt}")
            chat_resp = await client.post(
                f"{FAIRYCLAW_API_URL}/{session_id}/chat",
                json={
                    "segments": [
                        {
                            "type": "text",
                            "content": prompt
                        }
                    ]
                }
            )
            
            if chat_resp.status_code == 200:
                logger.info("Chat request accepted (Background processing started). Waiting for callback...")
            else:
                logger.error(f"Chat request failed: {chat_resp.status_code} {chat_resp.text}")

        except httpx.RequestError as exc:
            logger.error(f"An error occurred while requesting {exc.request.url!r}.")
            logger.info("Make sure the FairyClaw server is running on http://localhost:8000")
        except Exception as e:
            logger.error(f"Error during test logic: {e}")

@app.on_event("startup")
async def startup_event():
    # Run test logic in background
    asyncio.create_task(run_test_logic())

if __name__ == "__main__":
    print(f"Starting Callback Server on port {CALLBACK_PORT}...")
    print(f"Ensure FAIRYCLAW_FILESYSTEM_ROOT_DIR includes {TEST_ROOT_DIR}")
    uvicorn.run(app, host="0.0.0.0", port=CALLBACK_PORT)
