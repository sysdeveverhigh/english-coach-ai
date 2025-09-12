# apps/api/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx, os

# ---- CONFIG ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
APP_ENV = os.getenv("APP_ENV", "prod")

# Cliente HTTP global (keep-alive) para reducir latencia TLS/handshake
http_client = httpx.AsyncClient(
    timeout=120.0,
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # aquí podrías "precalentar" si quisieras
    yield
    await http_client.aclose()

app = FastAPI(lifespan=lifespan)

# CORS: preview de Vercel + prod + local
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://english-coach-ai.onrender.com",  # pruebas directas API
        "https://coach.everhighit.com",           # prod web
        "http://localhost:3000",                  # local
    ],
    allow_origin_regex=r"^https://.*\.vercel\.app$",  # previews de Vercel
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- HEALTH / ENVCHECK ----
@app.get("/health")
def health():
    return {"ok": True, "env": APP_ENV}

@app.get("/envcheck")
def envcheck():
    return {"ok": True, "has_openai": bool(OPENAI_API_KEY), "openai_len": len(OPENAI_API_KEY or "")}

# ---- ASR (Whisper) ----
@app.post("/asr")
async def asr(audio: UploadFile = File(...), language: str = Form("en")):
    if not OPENAI_API_KEY:
        return JSONResponse({"error": "OPENAI_API_KEY is empty"}, status_code=500)

    fname = audio.filename or "audio.webm"
    ctype = audio.content_type or "audio/webm"
    data = {"model": "whisper-1", "language": language}

    try:
        content = await audio.read()
        r = await http_client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            data=data,
            files={"file": (fname, content, ctype)},
        )
        if r.status_code != 200:
            print("ASR ERROR", r.status_code, r.text[:500])
            return JSONResponse(
                {"error": "openai_asr_failed", "status": r.status_code, "detail": r.text},
                status_code=500,
            )
        j = r.json()
        return {"text": j.get("text", "")}
    except Exception as e:
        print("ASR EXCEPTION", repr(e))
        return JSONResponse({"error": "server_exception", "detail": str(e)}, status_code=500)

# ---- CHAT (Correcciones) ----
@app.post("/chat")
async def chat(prompt: str = Form(...), language: str = Form("en")):
    if not OPENAI_API_KEY:
        return JSONResponse({"error": "OPENAI_API_KEY is empty"}, status_code=500)

    system = (
        "You are a concise, encouraging language coach. "
        "Correct grammar, pronunciation hints, and provide 1–2 short examples."
    )
    if language == "es":
        system = (
            "Eres un coach de idiomas conciso y motivador. "
            "Corrige gramática, da pistas de pronunciación y 1–2 ejemplos breves."
        )

    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ]
    }

    r = await http_client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json=body
    )
    if r.status_code != 200:
        return JSONResponse({"error": "openai_chat_failed", "detail": r.text}, status_code=500)

    out = r.json()
    text = out["choices"][0]["message"]["content"]
    return {"text": text}

# ---- TTS (Audio de respuesta) ----
@app.post("/tts")
async def tts(text: str = Form(...), voice: str = Form("alloy"), format: str = Form("mp3")):
    if not OPENAI_API_KEY:
        return JSONResponse({"error": "OPENAI_API_KEY is empty"}, status_code=500)

    data = {"model": "gpt-4o-mini-tts", "voice": voice, "input": text, "format": format}
    r = await http_client.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json=data
    )
    if r.status_code != 200:
        return JSONResponse({"error": "openai_tts_failed", "detail": r.text}, status_code=500)

    media = "audio/mpeg" if format == "mp3" else "audio/wav"
    return StreamingResponse(iter([r.content]), media_type=media)

