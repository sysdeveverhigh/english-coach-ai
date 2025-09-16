# apps/api/main.py
import os, json
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ──────────────────────────────────────────────────────────────────────────────
# App & CORS
# ──────────────────────────────────────────────────────────────────────────────
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://coach.everhighit.com",
        "https://english-coach-ai.onrender.com",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────────────────────────────
# Env & Config
# ──────────────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")
CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")  # LLM para texto

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
# acepta cualquiera de los dos nombres que pudiste configurar en Render
SUPABASE_SERVICE_ROLE = (
    os.getenv("SUPABASE_SERVICE_ROLE") or os.getenv("SUPABASE_SERVICE") or ""
)

supabase_headers = {
    "apikey": SUPABASE_SERVICE_ROLE,
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

def json_error(name: str, detail: str, code: int = 500):
    return JSONResponse({"error": name, "detail": detail}, status_code=code)

# ──────────────────────────────────────────────────────────────────────────────
# Health / Keepalive
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"ok": True, "env": os.getenv("APP_ENV", "prod")}

@app.get("/envcheck")
def envcheck():
    return {
        "OPENAI_API_KEY_set": bool(OPENAI_API_KEY),
        "SUPABASE_URL_set": bool(SUPABASE_URL),
        "SUPABASE_SERVICE_ROLE_set": bool(SUPABASE_SERVICE_ROLE),
    }

# 204 No Content SIN body (para evitar "Response content longer than Content-Length")
@app.get("/keepalive")
def keepalive():
    return Response(status_code=204)

# ──────────────────────────────────────────────────────────────────────────────
# ASR (Whisper)
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/asr")
async def asr(audio: UploadFile = File(...), language: str = Form("en")):
    if not OPENAI_API_KEY:
        return json_error("server_misconfig", "OPENAI_API_KEY is empty", 500)

    fname = audio.filename or "audio.webm"
    ctype = audio.content_type or "audio/webm"
    data = {"model": "whisper-1", "language": language}

    try:
        content = await audio.read()
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{OPENAI_BASE}/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                data=data,
                files={"file": (fname, content, ctype)},
            )
        if r.status_code != 200:
            print("ASR ERROR", r.status_code, r.text[:500])
            return json_error("openai_asr_failed", r.text, 500)
        j = r.json()
        return {"text": j.get("text", "")}
    except Exception as e:
        print("ASR EXCEPTION", repr(e))
        return json_error("server_exception", str(e), 500)

# ──────────────────────────────────────────────────────────────────────────────
# TTS (Aria para EN, Lumen para ES)  ← NUEVO
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/tts")
async def tts(text: str = Form(...), language: str = Form("en")):
    if not OPENAI_API_KEY:
        return json_error("server_misconfig", "OPENAI_API_KEY is empty", 500)

    # Selección de voz por idioma
    lang = (language or "").lower()
    if lang.startswith("en"):
        voice = "verse"   # recomendada para inglés (cálida, estilo profesora)
    elif lang.startswith("es"):
        voice = "sage"  # recomendada para español (clara y natural)
    else:
        voice = "alloy"  # fallback neutral

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{OPENAI_BASE}/audio/speech",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini-tts",
                    "voice": voice,
                    "input": text,
                },
            )
        if r.status_code != 200:
            print("TTS ERROR", r.status_code, r.text[:500])
            return json_error("openai_tts_failed", r.text, 500)

        # audio/mpeg (mp3)
        return Response(r.content, media_type="audio/mpeg")
    except Exception as e:
        print("TTS EXCEPTION", repr(e))
        return json_error("server_exception", str(e), 500)

# ──────────────────────────────────────────────────────────────────────────────
# Lessons: start / turn
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/lesson/start")
async def lesson_start(
    user_id: UUID = Form(...),
    native_language: str = Form(...),
    target_language: str = Form(...),
    topic: str = Form(...),
    student_name: str = Form("")
):
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE):
        return json_error("server_misconfig", "Supabase URL or Service Role missing", 500)

    # 1) Crear sesión en Supabase
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{SUPABASE_URL}/rest/v1/lesson_sessions",
                headers=supabase_headers,
                json={
                    "user_id": str(user_id),
                    "topic": topic,
                    "native_lang": native_language,
                    "target_lang": target_language,
                    "step_index": 0,
                },
            )
        print("SUPABASE START status:", resp.status_code, "body:", resp.text[:400])
        if resp.status_code not in (200, 201):
            return json_error("supabase_create_session_failed", resp.text, 500)
        row = resp.json()[0]
        lesson_id = row["id"]
    except Exception as e:
        print("SUPABASE START EXC:", repr(e))
        return json_error("supabase_exception", str(e), 500)

    # 2) Intro del LLM (opcional; si falla, no bloqueamos)
    intro = ""
    if OPENAI_API_KEY:
        system = (
            "Eres una profesora amable. Da una breve consigna en el idioma NATIVO del alumno, "
            "presenta el tema y termina con UNA pregunta para iniciar conversación. "
            "Nada de bullets ni números."
        )
        user = (
            f"Idioma nativo: {native_language}. Idioma meta: {target_language}. "
            f"Tema: {topic}. Alumno: {student_name or 'estudiante'}. "
            "Produce 1–2 frases naturales en el idioma nativo, cerrando con una pregunta simple."
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{OPENAI_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={
                        "model": CHAT_MODEL,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": 0.6,
                    },
                )
            print("LLM START status:", r.status_code, "body:", r.text[:400])
            if r.status_code == 200:
                j = r.json()
                intro = j["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print("LLM START EXC:", repr(e))
            # intro sigue vacío sin bloquear

    return {
        "lesson_id": lesson_id,
        "step_index": 0,
        "teacher_text_native": intro,
    }

@app.post("/lesson/turn")
async def lesson_turn(
    lesson_id: UUID = Form(...),
    step_index: int = Form(...),
    user_text: str = Form(...),
    native_language: str = Form(...),
    target_language: str = Form(...),
):
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE):
        return json_error("server_misconfig", "Supabase URL or Service Role missing", 500)

    teacher_feedback = ""
    corrected_sentence = ""

    # 1) LLM feedback (JSON estricto)
    if OPENAI_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    f"{OPENAI_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={
                        "model": CHAT_MODEL,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Eres una profesora de idiomas. Devuelve JSON con dos campos: "
                                    '{"teacher_feedback": <texto en idioma nativo del alumno>, '
                                    '"corrected_sentence": <una sola oración corregida en el idioma meta>}. '
                                    "Feedback cálido, 1–2 frases, sin listas."
                                ),
                            },
                            {
                                "role": "user",
                                "content": f'Idioma nativo="{native_language}", meta="{target_language}". Alumno dijo: "{user_text}".'
                            },
                        ],
                        "temperature": 0.5,
                        "response_format": {"type": "json_object"},
                    },
                )
            print("LLM TURN status:", r.status_code, "body:", r.text[:400])
            if r.status_code == 200:
                payload = r.json()["choices"][0]["message"]["content"]
                data = json.loads(payload)
                teacher_feedback = data.get("teacher_feedback", "")
                corrected_sentence = data.get("corrected_sentence", "")
        except Exception as e:
            print("LLM TURN EXC:", repr(e))

    # 2) Guardar turno
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            ins = await client.post(
                f"{SUPABASE_URL}/rest/v1/lesson_turns",
                headers=supabase_headers,
                json={
                    "lesson_id": str(lesson_id),
                    "step_index": step_index,
                    "user_text": user_text,
                    "teacher_feedback": teacher_feedback,
                    "corrected_sentence": corrected_sentence,
                    "score": 0.0,
                    "need_repeat": True,
                },
            )
        print("SUPABASE TURN status:", ins.status_code, "body:", ins.text[:400])
        if ins.status_code not in (200, 201):
            return json_error("supabase_insert_turn_failed", ins.text, 500)
    except Exception as e:
        print("SUPABASE TURN EXC:", repr(e))
        return json_error("supabase_exception", str(e), 500)

    # 3) Regla simple de avance
    advanced = len(user_text.split()) >= 6
    next_step_index = step_index + 1 if advanced else step_index
    next_teacher_text_native = (
        "Excelente. Ahora intenta pedir la bebida con una frase completa y cortés."
        if advanced and native_language.startswith("es")
        else "Great. Now try to order a drink using a complete and polite sentence."
        if advanced
        else ""
    )

    return {
        "teacher_feedback": teacher_feedback,
        "corrected_sentence": corrected_sentence,
        "advanced": advanced,
        "lesson_done": False,
        "next_step_index": next_step_index,
        "next_teacher_text_native": next_teacher_text_native,
    }
