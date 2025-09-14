# apps/api/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx, os, json
import re
from typing import Dict, Any

# ---- CONFIG ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
APP_ENV = os.getenv("APP_ENV", "prod")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE = os.getenv("SUPABASE_SERVICE_ROLE", "")  # crea esta variable en Render
supabase_headers = {
    "apikey": SUPABASE_SERVICE_ROLE,
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE}",
    "Content-Type": "application/json",
}

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
    user_id: str = Form(...),
    native_language: str = Form("es"),
    target_language: str = Form("en"),
    topic: str = Form("restaurant"),
    student_name: str = Form(""),
):
    if topic != "restaurant":
        return JSONResponse({"error": "unsupported_topic"}, status_code=400)

    # crear sesión
    sess_payload = {
        "user_id": user_id,
        "topic": topic,
        "native_lang": native_language,
        "target_lang": target_language,
        "step_index": 0,
        "status": "active",
    }
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{SUPABASE_URL}/rest/v1/lesson_sessions", headers=supabase_headers, json=sess_payload)
        if r.status_code not in (200,201):
            return JSONResponse({"error":"supabase_create_session_failed","detail":r.text}, status_code=500)
        sess = r.json()[0] if isinstance(r.json(), list) else r.json()

    step = RESTAURANT_STEPS[0]
    teacher_text_native = step["teacher_native"].format(name=student_name or "allumno")

    return {
        "lesson_id": sess["id"],
        "step_index": 0,
        "teacher_text_native": teacher_text_native,
        "goal": step["goal"]
    }

@app.post("/lesson/turn")
async def lesson_turn(
    lesson_id: str = Form(...),
    step_index: int = Form(...),
    user_text: str = Form(...),
    native_language: str = Form("es"),
    target_language: str = Form("en"),
):
    step = next((s for s in RESTAURANT_STEPS if s["id"] == step_index), None)
    if not step:
        return JSONResponse({"error":"invalid_step"}, status_code=400)

    system = lesson_coach_prompt(native_language, target_language, step["goal"], step["expect_keywords"])
    body = {
        "model": "gpt-4o-mini",
        "temperature": 0.4,
        "messages": [
            {"role":"system","content": system},
            {"role":"user", "content": f"STUDENT_UTTERANCE (TARGET): {user_text}"}
        ]
    }

    r = await http_client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type":"application/json"},
        json=body
    )
    if r.status_code != 200:
        return JSONResponse({"error":"openai_chat_failed","detail":r.text}, status_code=500)

    raw = r.json()["choices"][0]["message"]["content"]

    # Tolerancia a formato: intenta parsear JSON
    try:
        j = json.loads(raw)
    except Exception:
        # fallback: extrae con heurística mínima
        j = {
            "teacher_feedback": raw,
            "corrected_sentence": "",
            "score": 0.5,
            "need_repeat": True
        }

    teacher_feedback = j.get("teacher_feedback","").strip()
    corrected = j.get("corrected_sentence","").strip().strip('"')
    score = float(j.get("score",0.5))
    need_repeat = bool(j.get("need_repeat", True))

    # registrar turno
    turn_payload = {
        "lesson_id": lesson_id,
        "step_index": step_index,
        "user_text": user_text,
        "teacher_feedback": teacher_feedback,
        "corrected_sentence": corrected,
        "score": score,
        "need_repeat": need_repeat
    }
    async with httpx.AsyncClient() as c:
        r2 = await c.post(f"{SUPABASE_URL}/rest/v1/lesson_turns", headers=supabase_headers, json=turn_payload)
        if r2.status_code not in (200,201):
            return JSONResponse({"error":"supabase_insert_turn_failed","detail":r2.text}, status_code=500)

        # actualizar sesión: avg y conteo
        # Nota: calculamos incrementalmente en SQL simple
        upd = await c.patch(
            f"{SUPABASE_URL}/rest/v1/lesson_sessions?id=eq.{lesson_id}",
            headers=supabase_headers,
            json={"turns_count": "turns_count+1", "avg_score": "avg_score"}  # dummy; haremos SELECT para calcular
        )

        # recalcular avg en app (simple): lee últimos 30 turnos
        q = await c.get(
            f"{SUPABASE_URL}/rest/v1/lesson_turns?lesson_id=eq.{lesson_id}&select=score&order=created_at.desc&limit=30",
            headers=supabase_headers
        )
        scores = [float(x.get("score",0)) for x in q.json()] if q.status_code==200 else []
        avg = round(sum(scores)/len(scores), 3) if scores else 0.0
        await c.patch(
            f"{SUPABASE_URL}/rest/v1/lesson_sessions?id=eq.{lesson_id}",
            headers=supabase_headers,
            json={"avg_score": avg}
        )

    # avanzar o repetir
    if not need_repeat:
        next_idx = step_index + 1
        # si hay siguiente paso, actualiza step_index
        if next_idx < len(RESTAURANT_STEPS):
            async with httpx.AsyncClient() as c:
                await c.patch(
                    f"{SUPABASE_URL}/rest/v1/lesson_sessions?id=eq.{lesson_id}",
                    headers=supabase_headers,
                    json={"step_index": next_idx}
                )
            next_step = RESTAURANT_STEPS[next_idx]
            return {
                "teacher_feedback": teacher_feedback,
                "corrected_sentence": corrected,
                "score": score,
                "need_repeat": False,
                "advanced": True,
                "next_step_index": next_idx,
                "next_teacher_text_native": next_step["teacher_native"]
            }
        else:
            # última etapa: marcar como completed si avg >= 0.75
            status = "completed" if avg >= 0.75 else "active"
            async with httpx.AsyncClient() as c:
                await c.patch(
                    f"{SUPABASE_URL}/rest/v1/lesson_sessions?id=eq.{lesson_id}",
                    headers=supabase_headers,
                    json={"status": status}
                )
            return {
                "teacher_feedback": teacher_feedback,
                "corrected_sentence": corrected,
                "score": score,
                "need_repeat": False,
                "advanced": False,
                "lesson_done": (status=="completed"),
                "avg_score": avg
            }

    # repetir
    return {
        "teacher_feedback": teacher_feedback,
        "corrected_sentence": corrected,
        "score": score,
        "need_repeat": True,
        "advanced": False
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

