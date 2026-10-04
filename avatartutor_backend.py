from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import os
from datetime import datetime, timedelta
import json
import logging
import traceback
from typing import Optional, List, Dict
import re
import aiofiles
import pdfplumber
from docx import Document
from PIL import Image
import io

# Logging setup
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

# API Keys (from environment)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# Storage
UPLOAD_DIR = "/tmp/avatartutor_uploads"
USERS_FILE = "/tmp/users_data.json"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ============== USER DATA MANAGEMENT ==============

def load_users():
    """Load users from JSON file"""
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading users: {e}")
    return {}

def save_users(users_data):
    """Save users to JSON file"""
    try:
        with open(USERS_FILE, 'w') as f:
            json.dump(users_data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving users: {e}")

def get_or_create_user(user_id: str):
    """Get or create user profile"""
    users = load_users()
    
    if user_id not in users:
        users[user_id] = {
            "id": user_id,
            "created_at": datetime.now().isoformat(),
            "preferences": {
                "difficulty": "medium",
                "study_time": 30,
                "num_questions": 10,
                "compare_with_colleagues": False,
                "subject_detected": None
            },
            "knowledge_tree": {
                "topics": {},  # topic -> {last_practice: datetime, state: "green"/"yellow"/"red"}
            },
            "badges": [],  # List of {name, tier, subject, unlocked_at}
            "score": 0,
            "stats": {
                "total_answered": 0,
                "correct_answers": 0,
                "streak": 0
            },
            "colleagues": []  # List of colleague user_ids to compare with
        }
        save_users(users)
    
    return users[user_id]

# ============== FILE EXTRACTION ==============

async def extract_from_pdf(file_path: str) -> Dict:
    """Extract text, images, and video links from PDF"""
    try:
        content = {
            "text": "",
            "images": [],
            "video_links": []
        }
        
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                # Extract text
                text = page.extract_text()
                if text:
                    content["text"] += f"\n[Page {page_num + 1}]\n{text}"
                
                # Extract images
                for img_idx, img in enumerate(page.images):
                    try:
                        # Save image
                        img_path = f"{UPLOAD_DIR}/pdf_img_{page_num}_{img_idx}.png"
                        with open(img_path, 'wb') as f:
                            f.write(img["stream"].get_rawdata())
                        content["images"].append(img_path)
                    except Exception as e:
                        logger.warning(f"Could not extract image: {e}")
        
        # Find video links in text
        video_pattern = r'https?://(?:www\.)?(?:youtube\.com|youtu\.be|vimeo\.com)/\S+'
        content["video_links"] = re.findall(video_pattern, content["text"])
        
        return content
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        raise

async def extract_from_docx(file_path: str) -> Dict:
    """Extract text, images, and video links from Word document"""
    try:
        content = {
            "text": "",
            "images": [],
            "video_links": []
        }
        
        doc = Document(file_path)
        
        # Extract text and images
        for para in doc.paragraphs:
            content["text"] += para.text + "\n"
        
        # Extract images from document
        for rel in doc.part.rels.values():
            if "image" in rel.target_ref:
                try:
                    image_part = rel.target_part
                    img_path = f"{UPLOAD_DIR}/docx_img_{len(content['images'])}.png"
                    with open(img_path, 'wb') as f:
                        f.write(image_part.blob)
                    content["images"].append(img_path)
                except Exception as e:
                    logger.warning(f"Could not extract image: {e}")
        
        # Find video links
        video_pattern = r'https?://(?:www\.)?(?:youtube\.com|youtu\.be|vimeo\.com)/\S+'
        content["video_links"] = re.findall(video_pattern, content["text"])
        
        return content
    except Exception as e:
        logger.error(f"DOCX extraction error: {e}")
        raise

# ============== SUBJECT DETECTION ==============

def detect_subject(text: str) -> str:
    """Detect subject from document content"""
    text_lower = text.lower()
    
    # Medical keywords
    medical_keywords = ["medical", "anatomy", "diagnosis", "patient", "disease", "treatment", "clinical", 
                       "pathology", "physiology", "pharmacology", "surgery", "medicine", "doctor", "residency"]
    if sum(1 for kw in medical_keywords if kw in text_lower) > 3:
        return "medical"
    
    # English/Language keywords
    english_keywords = ["english", "grammar", "vocabulary", "toefl", "ielts", "speaking", "writing", 
                       "listening", "reading", "pronunciation", "language"]
    if sum(1 for kw in english_keywords if kw in text_lower) > 3:
        return "english"
    
    # Finance/CFA keywords
    finance_keywords = ["finance", "investment", "portfolio", "stock", "market", "trading", "valuation",
                       "cfa", "financial", "equity", "bond", "derivative"]
    if sum(1 for kw in finance_keywords if kw in text_lower) > 3:
        return "finance"
    
    # GMAT keywords
    gmat_keywords = ["gmat", "quantitative", "quant", "verbal", "logic", "reading", "data sufficiency"]
    if sum(1 for kw in gmat_keywords if kw in text_lower) > 2:
        return "gmat"
    
    return "general"

# ============== KNOWLEDGE TREE ==============

def update_knowledge_tree(user_id: str, topic: str):
    """Update knowledge tree leaf state"""
    users = load_users()
    user = users[user_id]
    
    if topic not in user["knowledge_tree"]["topics"]:
        user["knowledge_tree"]["topics"][topic] = {
            "last_practice": datetime.now().isoformat(),
            "state": "green"
        }
    else:
        user["knowledge_tree"]["topics"][topic]["last_practice"] = datetime.now().isoformat()
        user["knowledge_tree"]["topics"][topic]["state"] = "green"
    
    save_users(users)

def check_knowledge_tree_decay(user_id: str):
    """Check and update leaf decay (24h yellow, 48h red, 72h reset)"""
    users = load_users()
    user = users[user_id]
    now = datetime.now()
    
    for topic, data in user["knowledge_tree"]["topics"].items():
        last_practice = datetime.fromisoformat(data["last_practice"])
        hours_since = (now - last_practice).total_seconds() / 3600
        
        if hours_since > 72:
            user["knowledge_tree"]["topics"][topic]["state"] = "reset"
        elif hours_since > 48:
            user["knowledge_tree"]["topics"][topic]["state"] = "red"
        elif hours_since > 24:
            user["knowledge_tree"]["topics"][topic]["state"] = "yellow"
        else:
            user["knowledge_tree"]["topics"][topic]["state"] = "green"
    
    save_users(users)

# ============== BADGE SYSTEM ==============

BADGE_DEFINITIONS = {
    "medical": {
        "anatomy_expert": {"description": "80%+ on anatomy", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}},
        "diagnosis_master": {"description": "Perfect score on clinical cases", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}},
        "pharmacology_pro": {"description": "High score on pharmacology", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}}
    },
    "english": {
        "vocabulary_wizard": {"description": "95%+ on vocabulary", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}},
        "grammar_guardian": {"description": "Perfect grammar section", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}},
        "speaking_champion": {"description": "80%+ on speaking", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}}
    },
    "finance": {
        "market_analyst": {"description": "80%+ on markets", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}},
        "portfolio_builder": {"description": "Perfect on portfolio management", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}},
        "valuation_expert": {"description": "High valuation scores", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}}
    },
    "gmat": {
        "math_whiz": {"description": "90%+ on quant", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}},
        "logic_master": {"description": "Perfect on logic", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}},
        "timing_champion": {"description": "Beat time limit", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}}
    },
    "general": {
        "first_steps": {"description": "Answered first question", "thresholds": {"bronze": 1, "silver": 5, "gold": 20}},
        "streak_king": {"description": "5+ correct in a row", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}},
        "quick_thinker": {"description": "Answer in <10 seconds", "thresholds": {"bronze": 5, "silver": 15, "gold": 50}},
        "marathon_master": {"description": "25+ questions answered", "thresholds": {"bronze": 1, "silver": 3, "gold": 10}}
    }
}

def award_badge(user_id: str, badge_name: str, subject: str):
    """Award or upgrade badge to user"""
    users = load_users()
    user = users[user_id]
    
    # Find existing badge
    existing = next((b for b in user["badges"] if b["name"] == badge_name), None)
    
    if existing:
        # Upgrade tier
        tiers = ["bronze", "silver", "gold"]
        current_tier_idx = tiers.index(existing["tier"])
        if current_tier_idx < 2:
            existing["tier"] = tiers[current_tier_idx + 1]
    else:
        # Create new badge
        user["badges"].append({
            "name": badge_name,
            "subject": subject,
            "tier": "bronze",
            "unlocked_at": datetime.now().isoformat()
        })
    
    save_users(users)

# ============== LEADERBOARD ==============

def calculate_weighted_score(user_id: str) -> float:
    """Calculate weighted score based on recent activity"""
    users = load_users()
    user = users.get(user_id)
    
    if not user:
        return 0
    
    # For now, simple: base score + badge multiplier
    base_score = user["score"]
    badge_multiplier = 1 + (len(user["badges"]) * 0.1)
    
    return base_score * badge_multiplier

def get_leaderboard(user_id: str, limit: int = 10) -> List:
    """Get leaderboard including user's colleagues"""
    users = load_users()
    user = users.get(user_id)
    
    if not user or not user["preferences"]["compare_with_colleagues"]:
        return []
    
    colleagues = user.get("colleagues", [])
    
    leaderboard = []
    for colleague_id in colleagues:
        if colleague_id in users:
            colleague = users[colleague_id]
            score = calculate_weighted_score(colleague_id)
            leaderboard.append({
                "user_id": colleague_id,
                "score": score,
                "badges": len(colleague["badges"]),
                "streak": colleague["stats"]["streak"]
            })
    
    leaderboard.sort(key=lambda x: x["score"], reverse=True)
    return leaderboard[:limit]

# ============== API ENDPOINTS ==============

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), user_id: str = "demo_user"):
    """Upload and extract content from PDF or Word document"""
    try:
        user = get_or_create_user(user_id)
        
        # Save file
        file_path = f"{UPLOAD_DIR}/{user_id}_{file.filename}"
        async with aiofiles.open(file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        # Extract based on file type
        if file.filename.endswith('.pdf'):
            extracted = await extract_from_pdf(file_path)
        elif file.filename.endswith('.docx') or file.filename.endswith('.doc'):
            extracted = await extract_from_docx(file_path)
        else:
            raise HTTPException(status_code=400, detail="Only PDF and DOCX files supported")
        
        # Detect subject
        subject = detect_subject(extracted["text"])
        user["preferences"]["subject_detected"] = subject
        
        # Update user
        save_users({user_id: user} | load_users())
        
        return {
            "status": "success",
            "filename": file.filename,
            "subject_detected": subject,
            "text_length": len(extracted["text"]),
            "images": len(extracted["images"]),
            "video_links": len(extracted["video_links"]),
            "content_preview": extracted["text"][:500]
        }
    except Exception as e:
        logger.error(f"Upload error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/generatequestions")
async def generate_questions(request: dict, user_id: str = "demo_user"):
    """Generate questions with personalization"""
    try:
        user = get_or_create_user(user_id)
        content = request.get("content", "")
        
        # Get user preferences
        difficulty = user["preferences"].get("difficulty", "medium")
        num_questions = user["preferences"].get("num_questions", 10)
        subject = user["preferences"].get("subject_detected", "general")
        
        prompt = f"""You are an AI exam tutor. Based on the following study material, generate {num_questions} quiz questions.

Study Material:
{content}

Requirements:
- Difficulty level: {difficulty}
- Generate {num_questions} questions
- For each question, provide 4 options (A, B, C, D)
- Mark the correct option
- Provide a detailed explanation
- Format as JSON: [{{"question": "...", "options": ["A...", "B...", "C...", "D..."], "correct": 0, "explanation": "..."}}]
- Subject detected: {subject}
- Include relevant concepts from the material

Return ONLY valid JSON array, no markdown or extra text."""

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
            raise Exception(f"Anthropic API error: {response.status_code}")

        api_response = response.json()
        questions_text = api_response["content"][0]["text"]
        
        # Parse JSON
        import json
        questions = json.loads(questions_text)
        
        # Update knowledge tree
        update_knowledge_tree(user_id, subject)
        
        # Award badge for first question
        if user["stats"]["total_answered"] == 0:
            award_badge(user_id, "first_steps", "general")
        
        return {"questions": questions}
    
    except json.JSONDecodeError:
        logger.error("Failed to parse questions JSON")
        raise HTTPException(status_code=500, detail="Failed to parse AI response")
    except Exception as e:
        logger.error(f"Generate questions error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/speak")
async def speak_message(request: dict):
    """Text-to-speech using ElevenLabs"""
    try:
        text = request.get("text", "Hello!")
        
        response = await httpx.AsyncClient(timeout=30.0).post(
            "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "text": text,
                "model_id": "eleven_monolingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75
                }
            }
        )

        if response.status_code != 200:
            raise Exception(f"ElevenLabs API error: {response.status_code}")

        audio_data = response.content
        import base64
        audio_base64 = base64.b64encode(audio_data).decode()
        
        return {"audio": f"data:audio/mpeg;base64,{audio_base64}"}
    
    except Exception as e:
        logger.error(f"Speech error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/user/{user_id}")
async def get_user_profile(user_id: str):
    """Get user profile with badges and knowledge tree"""
    try:
        user = get_or_create_user(user_id)
        check_knowledge_tree_decay(user_id)
        user = load_users()[user_id]  # Reload after decay check
        
        return {
            "id": user["id"],
            "score": user["score"],
            "stats": user["stats"],
            "badges": user["badges"],
            "knowledge_tree": user["knowledge_tree"],
            "preferences": user["preferences"]
        }
    except Exception as e:
        logger.error(f"Get profile error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/answer")
async def submit_answer(request: dict, user_id: str = "demo_user"):
    """Submit answer and update stats"""
    try:
        user = get_or_create_user(user_id)
        is_correct = request.get("is_correct", False)
        subject = request.get("subject", "general")
        
        user["stats"]["total_answered"] += 1
        if is_correct:
            user["stats"]["correct_answers"] += 1
            user["stats"]["streak"] += 1
            user["score"] += 10
        else:
            user["stats"]["streak"] = 0
            user["score"] = max(0, user["score"] - 5)
        
        # Award streak badge
        if user["stats"]["streak"] >= 5:
            award_badge(user_id, "streak_king", "general")
        
        save_users({user_id: user} | load_users())
        
        return {"score": user["score"], "stats": user["stats"]}
    except Exception as e:
        logger.error(f"Submit answer error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/leaderboard/{user_id}")
async def get_leaderboard_endpoint(user_id: str):
    """Get leaderboard for user and colleagues"""
    try:
        leaderboard = get_leaderboard(user_id)
        return {"leaderboard": leaderboard}
    except Exception as e:
        logger.error(f"Leaderboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/add-colleague")
async def add_colleague(request: dict, user_id: str = "demo_user"):
    """Add colleague to compare with"""
    try:
        user = get_or_create_user(user_id)
        colleague_id = request.get("colleague_id")
        
        if colleague_id not in user.get("colleagues", []):
            user["colleagues"].append(colleague_id)
            save_users({user_id: user} | load_users())
        
        return {"status": "added", "colleagues": user["colleagues"]}
    except Exception as e:
        logger.error(f"Add colleague error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
