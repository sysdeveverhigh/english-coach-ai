"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { resolveVoice } from "@/lib/voices";

type UUID = string & { readonly __brand: unique symbol };

interface StartResp {
  lesson_id: UUID;
  step_index: number;
  teacher_text_native: string;
}

interface TurnResp {
  teacher_feedback: string;
  corrected_sentence: string;
  advanced: boolean;
  lesson_done: boolean;
  next_step_index: number;
  next_teacher_text_native: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export default function RestaurantLessonPage() {
  // Config “rápida”: en el futuro saldrá del perfil del alumno
  const [studentName] = useState<string>("Octavio");
  const [nativeLang] = useState<"es" | "en">("es");   // idioma del feedback hablado
  const [targetLang] = useState<"en" | "es">("en");   // idioma que practica

  // Estado de la lección
  const [lessonId, setLessonId] = useState<UUID | null>(null);
  const [stepIndex, setStepIndex] = useState<number>(0);
  const [teacherIntro, setTeacherIntro] = useState<string>("");

  // Estado de turno (resultado)
  const [teacherFeedback, setTeacherFeedback] = useState<string>("");
  const [correctedSentence, setCorrectedSentence] = useState<string>("");

  // Audio players (un reproductor que reutilizamos)
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Grabación
  const [isRecording, setIsRecording] = useState<boolean>(false);
  const [secondsLeft, setSecondsLeft] = useState<number>(15);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const timerRef = useRef<number | null>(null);

  // Carga/errores UI
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string>("");

  // Helpers HTTP
  const postForm = useCallback(async (url: string, fd: FormData): Promise<Response> => {
    const r = await fetch(url, { method: "POST", body: fd });
    return r;
  }, []);

  // TTS con voz seleccionada por usuario (persistida en localStorage por /settings)
  const ttsBlob = useCallback(async (text: string, lang: string): Promise<Blob> => {
    const fd = new FormData();
    fd.append("text", text);
    fd.append("language", lang);
    // dejamos que el backend elija la voz por defecto,
    // pero si el usuario eligió una voz en /settings, resolveVoice la trae
    const v = resolveVoice(lang);
    fd.append("voice", v);

    const r = await postForm(`${API_BASE}/tts`, fd);
    if (!r.ok) {
      throw new Error(`TTS failed: ${r.status}`);
    }
    return await r.blob();
  }, [postForm]);

  const playBlob = useCallback(async (blob: Blob, autoplayDelayMs = 0): Promise<void> => {
    if (!audioRef.current) audioRef.current = new Audio();
    const a = audioRef.current;
    a.src = URL.createObjectURL(blob);

    await new Promise<void>((resolve) => {
      const start = () => {
        a.removeEventListener("canplaythrough", start);
        window.setTimeout(() => {
          a.play().catch(() => { /* ignorar bloqueo del navegador */ });
          resolve();
        }, autoplayDelayMs);
      };
      a.addEventListener("canplaythrough", start);
    });
  }, []);

  // Iniciar lección: crea sesión + reproduce consigna con autoplay (1.2s)
  const startLesson = useCallback(async () => {
    setBusy(true);
    setError("");
    setTeacherFeedback("");
    setCorrectedSentence("");
    try {
      const fd = new FormData();
      fd.append("user_id", crypto.randomUUID()); // para F&F puedes usar UUID efímero local
      fd.append("native_language", nativeLang);
      fd.append("target_language", targetLang);
      fd.append("topic", "restaurant");
      fd.append("student_name", studentName);

      const r = await postForm(`${API_BASE}/lesson/start`, fd);
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`start failed: ${r.status} ${txt.slice(0, 200)}`);
      }
      const data = (await r.json()) as StartResp;
      setLessonId(data.lesson_id);
      setStepIndex(data.step_index);
      setTeacherIntro(data.teacher_text_native ?? "");

      if (data.teacher_text_native) {
        const b = await ttsBlob(data.teacher_text_native, nativeLang);
        // autoplay tras un gesto del usuario (este handler lo dispara un click)
        await playBlob(b, 1200);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "start error");
    } finally {
      setBusy(false);
    }
  }, [nativeLang, targetLang, studentName, postForm, ttsBlob, playBlob]);

  // Grabación
  const startRecording = useCallback(async () => {
    setError("");
    if (isRecording) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunksRef.current = [];
      mr.ondataavailable = (ev: BlobEvent) => {
        if (ev.data && ev.data.size > 0) chunksRef.current.push(ev.data);
      };
      mr.onstop = () => {
        // detener tracks
        stream.getTracks().forEach(t => t.stop());
      };
      mediaRecorderRef.current = mr;
      mr.start();
      setIsRecording(true);
      setSecondsLeft(15);

      // contador & autostop a 15s
      if (timerRef.current) window.clearInterval(timerRef.current);
      timerRef.current = window.setInterval(() => {
        setSecondsLeft((s) => {
          if (s <= 1) {
            // autostop
            window.clearInterval(timerRef.current!);
            timerRef.current = null;
            stopRecording(); // llamamos stop
            return 0;
          }
          return s - 1;
        });
      }, 1000);
    } catch (e) {
      setError("No se pudo iniciar el micrófono. Revisa permisos.");
    }
  }, [isRecording]);

  const stopRecording = useCallback(() => {
    if (!isRecording) return;
    if (timerRef.current) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    const mr = mediaRecorderRef.current;
    if (mr && mr.state !== "inactive") {
      mr.stop();
    }
    setIsRecording(false);
  }, [isRecording]);

  // Enviar la respuesta: ASR → TURN → TTS (feedback nativo) → TTS (frase corregida en meta)
  const processAnswer = useCallback(async () => {
    if (!lessonId) {
      setError("Primero inicia la lección.");
      return;
    }
    setBusy(true);
    setError("");

    try {
      // blob del audio
      const audioBlob = new Blob(chunksRef.current, { type: "audio/webm" });
      if (audioBlob.size === 0) {
        throw new Error("No se capturó audio.");
      }

      // 1) ASR
      const asrFd = new FormData();
      asrFd.append("audio", audioBlob, "answer.webm");
      // ponemos el idioma meta para guiar el reconocimiento
      asrFd.append("language", targetLang);

      const asrResp = await postForm(`${API_BASE}/asr`, asrFd);
      if (!asrResp.ok) {
        const t = await asrResp.text();
        throw new Error(`ASR failed: ${asrResp.status} ${t.slice(0, 200)}`);
      }
      const asrJson = (await asrResp.json()) as { text: string };
      const userText = asrJson.text?.trim() ?? "";
      if (!userText) throw new Error("No se reconoció texto en el audio.");

      // 2) TURN
      const turnFd = new FormData();
      turnFd.append("lesson_id", lessonId);
      turnFd.append("step_index", String(stepIndex));
      turnFd.append("user_text", userText);
      turnFd.append("native_language", nativeLang);
      turnFd.append("target_language", targetLang);

      const turnResp = await postForm(`${API_BASE}/lesson/turn`, turnFd);
      if (!turnResp.ok) {
        const t = await turnResp.text();
        throw new Error(`TURN failed: ${turnResp.status} ${t.slice(0, 200)}`);
      }
      const turn = (await turnResp.json()) as TurnResp;
      setTeacherFeedback(turn.teacher_feedback ?? "");
      setCorrectedSentence(turn.corrected_sentence ?? "");

      // 3) TTS feedback (idioma nativo)
      if (turn.teacher_feedback) {
        const fbBlob = await ttsBlob(turn.teacher_feedback, nativeLang);
        await playBlob(fbBlob, 800); // leve pausa antes de hablar
      }
      // 4) TTS frase corregida (idioma meta)
      if (turn.corrected_sentence) {
        const corrBlob = await ttsBlob(turn.corrected_sentence, targetLang);
        await playBlob(corrBlob, 600);
      }

      // No avanzamos stepIndex (lo tenemos congelado hasta nivel 2)
    } catch (e) {
      setError(e instanceof Error ? e.message : "error en el procesamiento");
    } finally {
      setBusy(false);
    }
  }, [lessonId, stepIndex, nativeLang, targetLang, postForm, ttsBlob, playBlob]);

  // Limpieza al desmontar
  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.src = "";
      }
      const mr = mediaRecorderRef.current;
      if (mr && mr.state !== "inactive") mr.stop();
    };
  }, []);

  return (
    <main className="max-w-2xl mx-auto p-6 space-y-5">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Lección: Restaurante</h1>
        <a
          href="/settings"
          className="text-sm underline text-gray-700 hover:text-black"
        >
          Configurar voz
        </a>
      </header>

      <section className="space-y-3">
        <div className="text-sm text-gray-600">
          Nativo: <b>{nativeLang.toUpperCase()}</b> · Meta: <b>{targetLang.toUpperCase()}</b>
        </div>
        <div className="flex gap-3">
          <button
            disabled={busy}
            onClick={startLesson}
            className="bg-black text-white rounded px-4 py-2 disabled:opacity-50"
          >
            Iniciar lección
          </button>
        </div>
      </section>

      {teacherIntro && (
        <section className="p-3 border rounded bg-gray-50">
          <div className="text-sm text-gray-500 mb-1">Profesora:</div>
          <p>{teacherIntro}</p>
        </section>
      )}

      <section className="space-y-3">
        <div className="text-sm text-gray-500">
          Graba tu respuesta (máx. <b>15s</b>). El contador se detiene al presionar “Detener”.
        </div>
        <div className="flex items-center gap-3">
          {!isRecording ? (
            <button
              onClick={startRecording}
              className="bg-green-600 text-white rounded px-4 py-2"
              disabled={busy}
            >
              Grabar respuesta
            </button>
          ) : (
            <button
              onClick={stopRecording}
              className="bg-red-600 text-white rounded px-4 py-2"
            >
              Detener
            </button>
          )}
          <div className="text-sm tabular-nums">
            {isRecording ? `⏺ ${secondsLeft}s` : "⏹ listo"}
          </div>
          <button
            onClick={processAnswer}
            className="bg-blue-600 text-white rounded px-4 py-2 disabled:opacity-50"
            disabled={busy || isRecording || !lessonId}
            title={!lessonId ? "Primero inicia la lección" : ""}
          >
            Enviar respuesta
          </button>
        </div>
      </section>

      {(teacherFeedback || correctedSentence) && (
        <section className="grid gap-3">
          {teacherFeedback && (
            <div className="p-3 border rounded bg-amber-50">
              <div className="text-sm text-gray-500 mb-1">Feedback de la profesora:</div>
              <p>{teacherFeedback}</p>
            </div>
          )}
          {correctedSentence && (
            <div className="p-3 border rounded bg-emerald-50">
              <div className="text-sm text-gray-500 mb-1">Oración corregida ({targetLang.toUpperCase()}):</div>
              <p className="font-medium">{correctedSentence}</p>
            </div>
          )}
        </section>
      )}

      {error && (
        <div className="p-3 border rounded bg-red-50 text-red-700">
          {error}
        </div>
      )}
    </main>
  );
}
