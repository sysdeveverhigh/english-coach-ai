# apps/api/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx, os
import re

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


# ---- CHAT (Correcciones bilingües guiadas y humanizador de texto) ----
def clean_for_speech(text: str) -> str:
    """
    Quita bullets, numeraciones y markdown que suenan robóticos en TTS.
    Convierte a un párrafo fluido.
    """
    # quita bullets tipo "-", "*", "•", "–" al inicio de línea
    text = re.sub(r'^\s*[-*•–]\s+', '', text, flags=re.MULTILINE)
    # quita numeraciones "1) ", "2. ", "3 - " al inicio de línea
    text = re.sub(r'^\s*\d+[\)\.\-:]\s+', '', text, flags=re.MULTILINE)
    # colapsa múltiples saltos de línea a uno
    text = re.sub(r'\n{2,}', '\n', text)
    # si aún quedan saltos, junta a un párrafo corto
    text = ' '.join(line.strip() for line in text.splitlines())
    # cleanup espacios múltiples
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text

@app.post("/chat")
async def chat(
    prompt: str = Form(...),
    native_language: str = Form("es"),   # "es" para explicaciones
    target_language: str = Form("en")    # "en" para práctica
):
    if not OPENAI_API_KEY:
        return JSONResponse({"error": "OPENAI_API_KEY is empty"}, status_code=500)

    # Instrucciones: tono cálido, 2–4 frases, nada de listas ni numeración.
    system = (
        "You are a warm, human language coach. Speak naturally, like a friendly teacher.\n"
        "Rules:\n"
        "- Write a single short paragraph (2–4 sentences). No lists. No numbering. No headings.\n"
        f"- Explain in the student's NATIVE language.\n"
        "- Include the corrected TARGET-language sentence inline (surrounded by quotes) and give a brief phonetic/intonation hint.\n"
        "- Keep it concise and encouraging."
    )

    user_content = (
        f"NATIVE={native_language}; TARGET={target_language}; "
        f"Student just said (in TARGET): {prompt}"
    )

    body = {
        "model": "gpt-4o-mini",
        "temperature": 0.5,  # un poco de calidez
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content}
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
    raw = out["choices"][0]["message"]["content"]
    natural = clean_for_speech(raw)
    return {"text": natural}

# ---- TTS (Audio de respuesta) ----
def pace_slow(text: str) -> str:
    """
    Inserta pausas suaves para que el TTS suene más pausado.
    Estrategia simple: coma cada 2–3 palabras y puntos suaves al final de frases.
    """
    # Normaliza espacios
    text = re.sub(r'\s+', ' ', text).strip()
    words = text.split(' ')
    out = []
    count = 0
    for w in words:
        out.append(w)
        count += 1
        if count in (2, 5, 8):
            out.append(',')  # pausas cortas
        if count >= 10:
            out.append('.')
            count = 0
    s = ' '.join(out)
    # Limpia posibles ", ." secuencias
    s = re.sub(r'\s+,', ', ', s)
    s = re.sub(r'\s+\.', '. ', s)
    return s.strip()

@app.post("/tts")
async def tts(
    text: str = Form(...),
    voice: str = Form("alloy"),
    format: str = Form("mp3"),
    pace: str = Form("normal"),  # <-- nuevo: "normal" | "slow"
):
    if not OPENAI_API_KEY:
        return JSONResponse({"error": "OPENAI_API_KEY is empty"}, status_code=500)

    speak_text = text if pace == "normal" else pace_slow(text)

    data = {"model": "gpt-4o-mini-tts", "voice": voice, "input": speak_text, "format": format}
    r = await http_client.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json=data
    )
    if r.status_code != 200:
        return JSONResponse({"error": "openai_tts_failed", "detail": r.text}, status_code=500)

    media = "audio/mpeg" if format == "mp3" else "audio/wav"
    return StreamingResponse(iter([r.content]), media_type=media)
