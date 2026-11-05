import React, { useState, useRef, useCallback } from 'react';

interface VoicePushToTalkProps {
  onTranscript?: (text: string) => void;
  transcribeUrl?: string;
  apiKey?: string;
}

/**
 * MediaRecorder + Web Speech API synthesis toggle
 *
 * Records audio via MediaRecorder, transcribes via muse-voice-transcribe-1.0,
 * displays editable transcript, and provides browser speechSynthesis read-aloud.
 */
export const VoicePushToTalk: React.FC<VoicePushToTalkProps> = ({
  onTranscript,
  transcribeUrl = '/api/v1/workspaces/voice',
  apiKey,
}) => {
  const [recording, setRecording] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [transcribing, setTranscribing] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  const startRecording = useCallback(async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      chunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(chunksRef.current, {
          type: 'audio/webm',
        });
        await sendTranscript(audioBlob);
      };

      mediaRecorder.start();
      setRecording(true);
    } catch (err) {
      setError('Microphone access denied.');
    }
  }, []);

  const stopRecording = useCallback(() => {
    mediaRecorderRef.current?.stop();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    setRecording(false);
  }, []);

  const sendTranscript = useCallback
    async (audioBlob: Blob) => {
      setTranscribing(true);
      setError(null);
      try {
        const formData = new FormData();
        formData.append('audio', audioBlob, 'audio.webm');

        const headers: Record<string, string> = {};
        if (apiKey) {
          headers['Authorization'] = `Bearer ${apiKey}`;
        }

        const res = await fetch(transcribeUrl, {
          method: 'POST',
          headers,
          body: formData,
        });

        if (!res.ok) {
          throw new Error(`Transcription failed: ${res.statusText}`);
        }

        const data = await res.json();
        const text = data.transcript || data.text || '';
        setTranscript(text);
        onTranscript?.(text);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Transcription failed');
      } finally {
        setTranscribing(false);
      }
    },
    [transcribeUrl, apiKey, onTranscript],
  );

  const speak = useCallback((text: string) => {
    if (!window.speechSynthesis) {
      setError('Speech synthesis not supported in this browser.');
      return;
    }

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.onstart = () => setSpeaking(true);
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utterance);
  }, []);

  const stopSpeaking = useCallback(() => {
    window.speechSynthesis?.cancel();
    setSpeaking(false);
  }, []);

  return (
    <div className="space-y-3">
      {/* Push-to-talk button */}
      <div className="flex gap-2">
        {!recording ? (
          <button
            onClick={startRecording}
            className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
          >
            <span className="w-3 h-3 bg-white rounded-full" />
            Hold to Record
          </button>
        ) : (
          <button
            onClick={stopRecording}
            className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded animate-pulse"
          >
            <span className="w-3 h-3 bg-white rounded-full" />
            Recording... Release to Stop
          </button>
        )}
      </div>

      {/* Transcribing indicator */}
      {transcribing && (
        <p className="text-sm text-gray-500">Transcribing...</p>
      )}

      {/* Error */}
      {error && <p className="text-sm text-red-600">{error}</p>}

      {/* Transcript display */}
      {transcript && (
        <div className="border rounded p-3 space-y-2">
          <div className="flex justify-between items-center">
            <h4 className="text-sm font-semibold text-gray-700">
              Transcript
            </h4>
            <div className="flex gap-2">
              <button
                onClick={() => speak(transcript)}
                disabled={speaking}
                className="text-xs px-2 py-1 bg-blue-100 text-blue-700 rounded hover:bg-blue-200 disabled:opacity-50"
              >
                {speaking ? 'Speaking...' : '🔊 Read Aloud'}
              </button>
              {speaking && (
                <button
                  onClick={stopSpeaking}
                  className="text-xs px-2 py-1 bg-gray-100 text-gray-700 rounded"
                >
                  Stop
                </button>
              )}
            </div>
          </div>

          {/* Editable transcript */}
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            className="w-full border rounded p-2 text-sm h-20"
          />

          {/* Submit transcript as query */}
          <button
            onClick={() => onTranscript?.(transcript)}
            className="w-full px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700"
          >
            Submit as Question
          </button>
        </div>
      )}
    </div>
  );
};

export default VoicePushToTalk;
