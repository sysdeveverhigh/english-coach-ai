"use client";
import { useEffect, useRef, useState } from "react";
import { supabase } from "@/lib/supabaseClient";

const MAX_MS = 15000;

// Tipo mínimo para evitar "any" en ondataavailable (algunas TS libs no traen BlobEvent)
interface BlobEventLike extends Event {
  data: Blob;
}

export default function RestaurantLesson() {
  const [lessonId, setLessonId] = useState<string>("");
  const [stepIndex, setStepIndex] = useState<number>(0);
  const [teacherIntro, setTeacherIntro] = useState<string>("");
  const [native, setNative] = useState("es");
  const [target, setTarget] = useState("en");
  const [userEmail, setUserEmail] = useState<string | null>(null);

  // grabación/estado
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [status, setStatus] = useState("");
  const [userText, setUserText] = useState("");
  const [feedback, setFeedback] = useState("");
  const [corrected, setCorrected] = useState("");

  // audio players
  const [audioIntro, setAudioIntro] = useState<string>("");
  const [audioA, setAudioA] = useState<string>(""); // feedback nativo
  const [audioB, setAudioB] = useState<string>(""); // corrected meta (slow)
  const refIntro = useRef<HTMLAudioElement | null>(null);
  const refA = useRef<HTMLAudioElement | null>(null);
  const refB = useRef<HTMLAudioElement | null>(null);

  // timer
  const [timerLeft, setTimerLeft] = useState<number>(MAX_MS / 1000);
  const timerRef = useRef<number | null>(null);
  const stopTimeoutRef = useRef<number | null>(null);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunks = useRef<BlobPart[]>([]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/exhaustive-deps
    (async () => {
      const base = process.env.NEXT_PUBLIC_API_BASE!;
      const { data: s } = await supabase.auth.getSession();
      const session = s.session;
      if (!session) {
        window.location.href = "/login";
        return;
      }
      setUserEmail(session.user.email ?? null);
      const { data: p } = await supabase
        .from("profiles")
        .select("*")
        .eq("user_id", session.user.id)
        .maybeSingle();
      if (!p) {
        window.location.href = "/profile";
        return;
      }
      setNative(p.native_lang || "es");
      setTarget(p.target_lang || "en");

      // inicia lección
      const fd = new FormData();
      fd.append("user_id", session.user.id);
      fd.append("native_language", p.native_lang || "es");
      fd.append("target_language", p.target_lang || "en");
      fd.append("topic", "restaurant");
      fd.append("student_name", p.full_name || "");
      const r = await fetch(`${base}/lesson/start`, { method: "POST", body: fd });
      const j = await r.json();
      setLessonId(j.lesson_id);
      setStepIndex(j.step_index);
      setTeacherIntro(j.teacher_text_native);

      // TTS intro (nativo)
      const tfd = new FormData();
      tfd.append("text", j.teacher_text_native);
      tfd.append("voice", "alloy");
      tfd.append("format", "mp3");
      tfd.append("pace", "normal");
      const tts = await fetch(`${base}/tts`, { method: "POST", body: tfd });
      const ab = await tts.arrayBuffer();
      const url = URL.createObjectURL(new Blob([ab], { type: "audio/mpeg" }));
      setAudioIntro(url);
      // autoplay
      setTimeout(() => refIntro.current?.play().catch(() => {}), 200);
    })();
  }, []);

  const startCountdown = () => {
    setTimerLeft(Math.round(MAX_MS / 1000));
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = window.setInterval(() => setTimerLeft((s) => (s > 0 ? s - 1 : 0)), 1000);
  };

  const startRec = async () => {
    if (isRecording || isProcessing) return;
    setUserText("");
    setFeedback("");
    setCorrected("");
    setStatus("");

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    // Detección tipada de MediaRecorder (sin any)
    let mimeType: string | undefined;
    if (typeof MediaRecorder !== "undefined") {
      if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {
        mimeType = "audio/webm;codecs=opus";
      } else if (MediaRecorder.isTypeSupported("audio/webm")) {
        mimeType = "audio/webm";
      }
    }
    const mr = mimeType
      ? new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 64000 })
      : new MediaRecorder(stream);

    chunks.current = [];
    mr.ondataavailable = (e: BlobEventLike) => {
      if (e.data && e.data.size > 0) chunks.current.push(e.data);
    };

    mr.onstop = async () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (stopTimeoutRef.current) clearTimeout(stopTimeoutRef.current);
      setIsProcessing(true);
      setStatus("Procesando…");
      try {
        const blob = new Blob(chunks.current, { type: mr.mimeType || "audio/webm" });
        chunks.current = [];

        const base = process.env.NEXT_PUBLIC_API_BASE!;
        // ASR
        const fd = new FormData();
        fd.append("audio", blob, "input.webm");
        fd.append("language", target);
        const asr = await fetch(`${base}/asr`, { method: "POST", body: fd });
        const asrJ = await asr.json();
        if (!asr.ok) {
          setStatus(`ASR ${asr.status}`);
          setIsProcessing(false);
          return;
        }
        setUserText(asrJ.text || "");

        // TURN
        const fd2 = new FormData();
        fd2.append("lesson_id", lessonId);
        fd2.append("step_index", String(stepIndex));
        fd2.append("user_text", asrJ.text || "");
        fd2.append("native_language", native);
        fd2.append("target_language", target);
        const tr = await fetch(`${base}/lesson/turn`, { method: "POST", body: fd2 });
        const tj = await tr.json();
        if (!tr.ok) {
          setStatus(`TURN ${tr.status}`);
          setIsProcessing(false);
          return;
        }
        setFeedback(tj.teacher_feedback || "");
        setCorrected(tj.corrected_sentence || "");

        // TTS A (feedback nativo)
        const fd3a = new FormData();
        fd3a.append("text", tj.teacher_feedback || "");
        fd3a.append("voice", "alloy");
        fd3a.append("format", "mp3");
        fd3a.append("pace", "normal");
        const ttsa = await fetch(`${base}/tts`, { method: "POST", body: fd3a });
        const abA = await ttsa.arrayBuffer();
        const urlA = URL.createObjectURL(new Blob([abA], { type: "audio/mpeg" }));
        setAudioA(urlA);

        // TTS B (corrected, lento)
        const fd3b = new FormData();
        fd3b.append("text", (tj.corrected_sentence || "").replace(/^"+|"+$/g, ""));
        fd3b.append("voice", "alloy");
        fd3b.append("format", "mp3");
        fd3b.append("pace", "slow");
        const ttsb = await fetch(`${base}/tts`, { method: "POST", body: fd3b });
        const abB = await ttsb.arrayBuffer();
        const urlB = URL.createObjectURL(new Blob([abB], { type: "audio/mpeg" }));
        setAudioB(urlB);

        setStatus(
          tj.lesson_done
            ? `Lección lista ${tj.avg_score ? `(promedio ${tj.avg_score})` : ""} ✅`
            : tj.advanced
            ? "¡Bien! Avancemos al siguiente paso."
            : "Repitamos este paso."
        );

        // autoplay: primero A, al terminar A reproducimos B
        setTimeout(() => refA.current?.play().catch(() => {}), 200);

        // Si avanzó, prepara siguiente intro
        if (tj.advanced && tj.next_teacher_text_native) {
          setStepIndex(tj.next_step_index);
          const pfd = new FormData();
          const base2 = process.env.NEXT_PUBLIC_API_BASE!;
          pfd.append("text", tj.next_teacher_text_native);
          pfd.append("voice", "alloy");
          pfd.append("format", "mp3");
          pfd.append("pace", "normal");
          const pr = await fetch(`${base2}/tts`, { method: "POST", body: pfd });
          const pab = await pr.arrayBuffer();
          const purl = URL.createObjectURL(new Blob([pab], { type: "audio/mpeg" }));
          setAudioIntro(purl);
        }
      } finally {
        setIsProcessing(false);
      }
    };

    mr.start(250);
    recRef.current = mr;
    setIsRecording(true);
    setStatus(`Grabando… (máx ${Math.round(MAX_MS / 1000)}s)`);
    setTimerLeft(Math.round(MAX_MS / 1000));
    startCountdown();
    stopTimeoutRef.current = window.setTimeout(() => {
      if (mr.state === "recording") mr.stop();
    }, MAX_MS);
  };

  const stopRec = () => {
    const mr = recRef.current;
    if (mr && mr.state === "recording") {
      if (timerRef.current) clearInterval(timerRef.current);
      if (stopTimeoutRef.current) clearTimeout(stopTimeoutRef.current);
      mr.stop();
      setStatus("Procesando…");
      setIsRecording(false);
    }
  };

  return (
    <main className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Lección: Restaurante</h1>
        <div className="text-sm text-gray-600">
          {userEmail} | {native}→{target}
        </div>
      </div>

      <div className="space-y-2">
        <div className="text-sm text-gray-700">
          Paso actual: <b>{stepIndex}</b>.{" "}
          {isRecording && (
            <>
              Tiempo restante: <span className="font-mono">{timerLeft}s</span>
            </>
          )}
        </div>

        {/* Consigna de la profe (nativo) */}
        <div className="bg-gray-50 p-3 rounded">
          <div className="text-sm text-gray-700 mb-1">Consigna de la profesora</div>
          <p className="text-sm">{teacherIntro}</p>
          {audioIntro && (
            <div className="mt-2">
              <audio ref={refIntro} src={audioIntro} controls />
            </div>
          )}
        </div>

        <div className="space-x-2">
          <button
            onClick={startRec}
            disabled={isRecording || isProcessing}
            className={`px-3 py-2 rounded text-white ${
              isRecording || isProcessing ? "bg-gray-400" : "bg-black"
            }`}
          >
            Grabar respuesta
          </button>
          <button onClick={stopRec} disabled={!isRecording} className="px-3 py-2 bg-gray-200 rounded">
            Detener
          </button>
        </div>

        <p className="text-sm text-gray-600">{status}</p>

        <div className="space-y-2">
          <p>
            <b>Tú dijiste:</b> {userText}
          </p>
          <p>
            <b>Profe:</b> {feedback}
          </p>
          {corrected && (
            <p>
              <b>Frase corregida:</b> “{corrected}”
            </p>
          )}
        </div>

        {/* Doble TTS */}
        <div className="space-y-2">
          {audioA && (
            <div>
              <div className="text-sm text-gray-700 mb-1">Explicación (nativo)</div>
              <audio
                ref={refA}
                src={audioA}
                controls
                onEnded={() => {
                  if (audioB) refB.current?.play().catch(() => {});
                }}
              />
            </div>
          )}
          {audioB && (
            <div>
              <div className="text-sm text-gray-700 mb-1">Shadowing (meta, lento)</div>
              <audio ref={refB} src={audioB} controls />
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
