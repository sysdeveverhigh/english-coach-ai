# apps/api/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from uuid import UUID, uuid4
import httpx, os, json
import re
from typing import Dict, Any

# ---- CONFIG ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")
CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
APP_ENV = os.getenv("APP_ENV", "prod")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
# <- lee cualquiera de los dos nombres que tienes en Render
SUPABASE_SERVICE_ROLE = (
    os.getenv("SUPABASE_SERVICE_ROLE") or os.getenv("SUPABASE_SERVICE") or ""
) # crea estas variables en Render

supabase_headers = {
    "apikey": SUPABASE_SERVICE_ROLE,
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

def json_error(name: str, detail: str, code: int = 500):
    return JSONResponse({"error": name, "detail": detail}, status_code=code)


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
# CONVERSACIONES
RESTAURANT_STEPS = [
    {
        "id": 0,
        "goal": "Saludar y pedir mesa",
        "teacher_native": "Hola {name}, hoy practicaremos cómo pedir en un restaurante. Imagina que llegas y quieres una mesa. Dime: “Buenas tardes, ¿tienen una mesa para [número de personas]?”",
        "expect_keywords": ["mesa", "personas", "buenas", "tardes"]
    },
    {
        "id": 1,
        "goal": "Pedir bebida",
        "teacher_native": "Perfecto. Ahora pide una bebida. Por ejemplo, “Me gustaría un agua sin gas, por favor” o “¿Tienen jugos naturales?”. Adelante.",
        "expect_keywords": ["agua", "jugo", "bebida", "por favor"]
    },
    {
        "id": 2,
        "goal": "Pedir plato principal y una recomendación",
        "teacher_native": "Muy bien. Ahora pide el plato principal y pregunta por una recomendación del chef. Inténtalo.",
        "expect_keywords": ["plato", "recomendación", "chef", "principal"]
    },
    {
        "id": 3,
        "goal": "Pedir la cuenta",
        "teacher_native": "Genial. Para terminar, pide la cuenta de forma cortés. Adelante.",
        "expect_keywords": ["cuenta", "por favor", "gracias"]
    }
]


# ---- HEALTH / ENVCHECK ----
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

# CLASES DE CONVERSACION

def lesson_coach_prompt(native: str, target: str, step_goal: str, expect_keywords: list[str]) -> str:
    return (
        "You are a warm language teacher. You will: (1) evaluate the student's TARGET-language utterance, "
        " (2) respond in the student's NATIVE language with a short, natural paragraph (no lists), "
        " (3) include one corrected TARGET sentence in quotes, and (4) return a JSON control block.\n\n"
        "Return a JSON object with keys: teacher_feedback, corrected_sentence, score, need_repeat.\n"
        "- teacher_feedback: short paragraph in NATIVE.\n"
        "- corrected_sentence: one concise sentence in TARGET, quoted.\n"
        "- score: 0.0–1.0 based on how well they achieved the goal.\n"
        "- need_repeat: true if they should try again; false if they can move on.\n\n"
        f"Context:\nNATIVE={native}; TARGET={target}; STEP_GOAL={step_goal}; EXPECT_KEYWORDS={expect_keywords}.\n"
        "Be strict but kind. If key info is missing, set need_repeat=true.\n"
    )

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

    # 1) Crea la sesión en Supabase
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

    # 2) Pedir intro al LLM (opcional; si falla, devolvemos vacío)
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
            else:
                # no bloquees el flujo si el LLM falla
                pass
        except Exception as e:
            print("LLM START EXC:", repr(e))
            # intro se queda ""

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

    # 1) LLM feedback JSON
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

    # 2) Guardar turno en Supabase
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

    advanced = len(user_text.split()) >= 6
    next_step_index = step_index + 1 if advanced else step_index
    next_teacher_text_native = (
        "Excelente. Ahora intenta pedir la bebida con una frase completa y cortés."
        if advanced and native_language.startswith("es") else
        "Great. Now try to order a drink using a complete and polite sentence." if advanced else ""
    )

    return {
        "teacher_feedback": teacher_feedback,
        "corrected_sentence": corrected_sentence,
        "advanced": advanced,
        "lesson_done": False,
        "next_step_index": next_step_index,
        "next_teacher_text_native": next_teacher_text_native,
    }



@app.post("/lesson/finish")
async def lesson_finish(lesson_id: str = Form(...)):
    async with httpx.AsyncClient() as c:
        r = await c.patch(
            f"{SUPABASE_URL}/rest/v1/lesson_sessions?id=eq.{lesson_id}",
            headers=supabase_headers, json={"status":"completed"}
        )
        if r.status_code not in (200,204):
            return JSONResponse({"error":"supabase_finish_failed","detail":r.text}, status_code=500)
    return {"ok": True}

