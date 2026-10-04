from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import json
import os

app = FastAPI()

# Enable CORS for Vercel frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (Vercel, localhost, etc)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Keys from environment variables
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
ELEVENLABS_KEY = os.getenv("ELEVENLABS_API_KEY")

# Request models
class GenerateQuestionsRequest(BaseModel):
    content: str

class SpeakRequest(BaseModel):
    text: str

# Health check
@app.get("/health")
async def health():
    return {"status": "ok", "message": "AvatarTutor backend is running!"}

# Generate questions from content using Claude
@app.post("/api/generatequestions")
async def generate_questions(request: GenerateQuestionsRequest):
    """Generate multiple choice questions from content using Claude"""
    
    if not request.content:
        raise HTTPException(status_code=400, detail="Content is required")
    
    if not ANTHROPIC_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01"
                },
                json={
                    "model": "claude-opus-4-1",
                    "max_tokens": 2000,
                    "messages": [{
                        "role": "user",
                        "content": f"""Create 5 multiple choice questions from this content. 
                        Format ONLY as valid JSON array with objects containing: {{question, options: [], correct: 0, explanation}}.
                        Do NOT include markdown or any text before/after the JSON.
                        Content: {request.content}"""
                    }]
                }
            )
        
        if response.status_code != 200:
            raise Exception(f"Anthropic API error: {response.text}")
        
        data = response.json()
        text = data["content"][0]["text"]
        
        # Extract JSON from response
        try:
            # Try to find JSON array in response
            import re
            json_match = re.search(r'\[[\s\S]*\]', text)
            if json_match:
                questions = json.loads(json_match.group())
                return {"questions": questions}
            else:
                raise ValueError("No JSON array found in response")
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=500, detail=f"Failed to parse questions: {str(e)}")
    
    except Exception as e:
        print(f"Error generating questions: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# Text-to-speech using ElevenLabs
@app.post("/api/speak")
async def speak(request: SpeakRequest):
    """Convert text to speech using ElevenLabs"""
    
    if not request.text:
        raise HTTPException(status_code=400, detail="Text is required")
    
    if not ELEVENLABS_KEY:
        raise HTTPException(status_code=500, detail="ELEVENLABS_API_KEY not configured")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM",
                headers={
                    "Content-Type": "application/json",
                    "xi-api-key": ELEVENLABS_KEY
                },
                json={
                    "text": request.text,
                    "model_id": "eleven_monolingual_v1",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75
                    }
                }
            )
        
        if response.status_code != 200:
            raise Exception(f"ElevenLabs API error: {response.text}")
        
        # Return audio as base64
        import base64
        audio_base64 = base64.b64encode(response.content).decode()
        
        return {
            "audio": f"data:audio/mpeg;base64,{audio_base64}"
        }
    
    except Exception as e:
        print(f"Error generating speech: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# Root endpoint
@app.get("/")
async def root():
    return {
        "message": "AvatarTutor Backend API",
        "version": "1.0",
        "endpoints": {
            "health": "/health",
            "generate_questions": "POST /api/generatequestions",
            "speak": "POST /api/speak"
        }
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
