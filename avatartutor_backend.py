from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
from datetime import datetime
import json
import logging
import traceback
import re

# Logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# ============== STORAGE ==============
USERS_FILE = "/tmp/users.json"

def load_users():
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {}

def save_users(users):
    try:
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f)
    except Exception as e:
        logger.error(f"Save error: {e}")

def get_user(user_id):
    users = load_users()
    if user_id not in users:
        users[user_id] = {
            "id": user_id,
            "score": 0,
            "streak": 0,
            "correct": 0,
            "total": 0,
            "badges": [],
            "subject": "general",
            "tree": {}
        }
        save_users(users)
    return users[user_id]

# ============== SUBJECT DETECTION ==============
def detect_subject(text):
    text = text.lower()
    if any(w in text for w in ["medical", "anatomy", "doctor", "disease", "diagnosis"]):
        return "medical"
    elif any(w in text for w in ["english", "grammar", "vocabulary", "toefl", "ielts"]):
        return "english"
    elif any(w in text for w in ["finance", "investment", "stock", "cfa"]):
        return "finance"
    elif any(w in text for w in ["gmat", "quant", "math", "verbal"]):
        return "gmat"
    return "general"

# ============== API ==============

@app.get("/health")
async def health():
    return {"message": "AvatarTutor Backend API", "version": "1.0", "endpoints": {"health": "/health", "generate_questions": "POST /api/generatequestions", "speak": "POST /api/speak"}}

@app.post("/api/generatequestions")
async def generate_questions(request: dict, user_id: str = "demo_user"):
    try:
        user = get_user(user_id)
        content = request.get("content", "")
        
        if not content:
            raise HTTPException(status_code=400, detail="Content required")
        
        subject = detect_subject(content)
        user["subject"] = subject
        
        difficulty = request.get("difficulty", "medium")
        num_questions = request.get("num_questions", 10)
        
        prompt = f"""Generate {num_questions} quiz questions from this material ({difficulty} difficulty).

Material:
{content[:2000]}

Return ONLY valid JSON: [{{"question": "...", "options": ["A...", "B...", "C...", "D..."], "correct": 0, "explanation": "..."}}]"""

        response = await httpx.AsyncClient(timeout=30.0).post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-opus-4-5",
                "max_tokens": 4000,
                "messages": [{"role": "user", "content": prompt}]
            }
        )

        if response.status_code != 200:
            raise Exception(f"API error: {response.status_code}")

        api_response = response.json()
        text = api_response["content"][0]["text"]
        
        import json
        questions = json.loads(text)
        
        save_users({user_id: user} | load_users())
        return {"questions": questions}
    
    except Exception as e:
        logger.error(f"Error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/speak")
async def speak(request: dict):
    try:
        text = request.get("text", "Hello!")
        
        response = await httpx.AsyncClient(timeout=30.0).post(
            "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM",
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": "eleven_monolingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
            }
        )

        if response.status_code != 200:
            raise Exception(f"ElevenLabs error: {response.status_code}")

        import base64
        audio_base64 = base64.b64encode(response.content).decode()
        return {"audio": f"data:audio/mpeg;base64,{audio_base64}"}
    
    except Exception as e:
        logger.error(f"Speak error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/upload")
async def upload(file: UploadFile = File(...), user_id: str = "demo_user"):
    try:
        user = get_user(user_id)
        content = (await file.read()).decode('utf-8', errors='ignore')[:5000]
        subject = detect_subject(content)
        user["subject"] = subject
        save_users({user_id: user} | load_users())
        
        return {
            "status": "success",
            "filename": file.filename,
            "subject": subject,
            "preview": content[:200]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/user/{user_id}")
async def get_profile(user_id: str):
    user = get_user(user_id)
    return {"score": user["score"], "streak": user["streak"], "correct": user["correct"], "total": user["total"], "subject": user["subject"]}

@app.post("/api/answer")
async def submit_answer(request: dict, user_id: str = "demo_user"):
    user = get_user(user_id)
    is_correct = request.get("is_correct", False)
    
    user["total"] += 1
    if is_correct:
        user["correct"] += 1
        user["streak"] += 1
        user["score"] += 10
    else:
        user["streak"] = 0
    
    save_users({user_id: user} | load_users())
    return {"score": user["score"], "streak": user["streak"]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
