"use client";
import { useRef, useState } from "react";

export default function Home() {
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const chunks = useRef<BlobPart[]>([]);
  const [text, setText] = useState("");
  const [reply, setReply] = useState("");
  const [audioURL, setAudioURL] = useState<string>("");

  const startRec = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mr = new MediaRecorder(stream, { mimeType: "audio/webm" });
    mr.ondataavailable = (e) => chunks.current.push(e.data);
    mr.onstop = async () => {
      const blob = new Blob(chunks.current, { type: "audio/webm" });
      chunks.current = [];
      const fd = new FormData();
      fd.append("audio", blob, "input.webm");
      fd.append("language", "en");
      const asr = await fetch(`${process.env.NEXT_PUBLIC_API_BASE}/asr`, { method: "POST", body: fd });
      const { text } = await asr.json();
      setText(text);

      const fd2 = new FormData();
      fd2.append("prompt", text);
      fd2.append("language", "en");
      const chat = await fetch(`${process.env.NEXT_PUBLIC_API_BASE}/chat`, { method: "POST", body: fd2 });
      const { text: ai } = await chat.json();
      setReply(ai);

      const fd3 = new FormData();
      fd3.append("text", ai);
      fd3.append("voice", "alloy");
      fd3.append("format", "mp3");
      const tts = await fetch(`${process.env.NEXT_PUBLIC_API_BASE}/tts`, { method: "POST", body: fd3 });
      // Para simplicidad, asume que devuelves audio como blob en el backend (ajusta según tu respuesta)
      const ab = await tts.arrayBuffer();
      const url = URL.createObjectURL(new Blob([ab], { type: "audio/mpeg" }));
      setAudioURL(url);
    };
    mr.start();
    setRec(mr);
  };

  const stopRec = () => rec?.stop();

  return (
    <main className="p-6 space-y-4">
      <h1 className="text-2xl font-bold">English Coach (MVP web)</h1>
      <div className="space-x-2">
        <button onClick={startRec} className="px-3 py-2 bg-black text-white rounded">Grabar</button>
        <button onClick={stopRec} className="px-3 py-2 bg-gray-200 rounded">Detener</button>
      </div>
      <div>
        <p><b>Tú dijiste:</b> {text}</p>
        <p><b>Coach:</b> {reply}</p>
      </div>
      {audioURL && <audio src={audioURL} controls autoPlay />}
    </main>
  );
}
