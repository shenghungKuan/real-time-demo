from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime
import logging
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Database configuration
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/messages_db"
)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# Initialize database
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            content TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

init_db()

# Serve index.html at root
@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

class ConnectionManager:
    def __init__(self):
        self.active_connections = []
        self.logger = logging.getLogger(__name__)

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.logger.info(f"Client connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        self.logger.info(f"Client disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast_updates(self, messages):
        if not self.active_connections:
            return
            
        message_data = json.dumps({
            "type": "update",
            "messages": messages
        })
        
        for connection in self.active_connections:
            try:
                await connection.send_text(message_data)
                self.logger.info("Update sent successfully")
            except Exception as e:
                self.logger.error(f"Error sending update to client: {e}")

manager = ConnectionManager()

def get_messages():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, content, timestamp::text FROM messages ORDER BY timestamp DESC")
        messages = cur.fetchall()
        cur.close()
        conn.close()
        return messages
    except Exception as e:
        logger.error(f"Database error when fetching messages: {e}")
        return []

async def check_database_updates():
    previous_messages = []
    while True:
        try:
            current_messages = get_messages()
            if current_messages != previous_messages:
                logger.info("Database changes detected, broadcasting updates")
                await manager.broadcast_updates(current_messages)
                previous_messages = current_messages
        except Exception as e:
            logger.error(f"Error checking database updates: {e}")
        await asyncio.sleep(1)  # Check every second

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(check_database_updates())

@app.get("/messages")
async def get_all_messages():
    messages = get_messages()
    logger.info(f"Retrieved {len(messages)} messages from database")
    return messages

@app.websocket("/wss")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep the connection alive and wait for disconnection
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("Client disconnected")
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)