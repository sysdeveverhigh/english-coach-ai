from fastapi import FastAPI, UploadFile, File, Form
from pydantic_settings import BaseSettings
from fastapi.middleware.cors import CORSMiddleware
import httpx, os, asyncio

class Settings(BaseSettings):
    OPENAI_API_KEY: str = ""
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    APP_ENV: str = "dev"

settings = Settings()

app = FastAPI()

# Ajusta origins para tu dominio web en Vercel
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://coach.everhighit.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENAI_BASE = "https://api.openai.com/v1"

@app.get("/health")
async def health():
    return {"ok": True, "env": settings.APP_ENV}

@app.post("/asr")
async def asr(audio: UploadFile = File(...), language: str = Form("en")):
    # Envía el archivo a Whisper para transcripción
    headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}"}
    form = {
        "model": "gpt-4o-mini-transcribe",  # o "whisper-1" si lo mantienes
        "language": language
    }
    files = {"file": (audio.filename, await audio.read(), audio.content_type)}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{OPENAI_BASE}/audio/transcriptions", headers=headers, data=form, files=files)
        r.raise_for_status()
        data = r.json()
    return {"text": data.get("text", "")}

@app.post("/chat")
async def chat(prompt: str = Form(...), language: str = Form("en")):
    headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"}
    # Simple system prompt bilingüe
    system = "You are a concise, encouraging language coach. Correct grammar and suggest natural phrasing."
    if language == "es":
        system = "Eres un coach de idiomas conciso y motivador. Corrige gramática y sugiere frases naturales."
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ]
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{OPENAI_BASE}/chat/completions", headers=headers, json=body)
        r.raise_for_status()
        out = r.json()
    text = out["choices"][0]["message"]["content"]
    return {"text": text}

@app.post("/tts")
async def tts(text: str = Form(...), voice: str = Form("alloy"), format: str = Form("mp3")):
    headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}"}
    data = {
        "model": "gpt-4o-mini-tts",
        "voice": voice,
        "input": text,
        "format": format
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{OPENAI_BASE}/audio/speech", headers=headers, json=data)
        r.raise_for_status()
        audio_bytes = r.content
    return {"audio_b64": audio_bytes.encode("base64") if hasattr(bytes, "encode") else audio_bytes.hex()}
