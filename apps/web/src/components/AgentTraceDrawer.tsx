import React, { useEffect, useRef, useState } from 'react';
import { RunEvent, AgentStep } from '../api/types';

interface AgentTraceDrawerProps {
  runId?: string;
  eventsEndpoint?: string;
  budgetRemaining?: number;
  budgetCap?: number;
  onClose?: () => void;
}

/**
 * Live SSE step stream, cache badge & budget meter
 *
 * Connects to GET /api/v1/runs/{run_id}/events SSE stream and displays
 * every step, tool call, latency, token usage, cache hit status, and
 * estimated USD cost in real time.
 */
export const AgentTraceDrawer: React.FC<AgentTraceDrawerProps> = ({
  runId,
  eventsEndpoint = '/api/v1/runs/{run_id}/events',
  budgetRemaining = 120,
  budgetCap = 120,
  onClose,
}) => {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!runId) return;

    const url = eventsEndpoint.replace('{run_id}', runId);
    const eventSource = new EventSource(url);
    eventSourceRef.current = eventSource;

    eventSource.onopen = () => {
      setConnected(true);
      setError(null);
    };

    eventSource.addEventListener('event', (e: MessageEvent) => {
      try {
        const data: RunEvent = JSON.parse(e.data);
        setEvents((prev) => [...prev, data]);

        // Extract step info
        if (data.kind === 'step.started' || data.kind === 'step.completed') {
          const stepData = data.data as Record<string, unknown>;
          const step: AgentStep = {
            seq: (stepData.seq as number) ?? steps.length,
            kind: (stepData.kind as string) ?? data.kind,
            name: (stepData.name as string) ?? 'unknown',
            args: (stepData.args as Record<string, unknown>) ?? {},
            result_summary: stepData.result_summary as string | undefined,
            latency_ms: stepData.latency_ms as number | undefined,
            model_call_id: stepData.model_call_id as string | undefined,
          };
          setSteps((prev) => [...prev, step]);
        }
      } catch {
        // Ignore parse errors
      }
    });

    eventSource.onerror = () => {
      setConnected(false);
      setError('Connection lost. Reconnecting...');
    };

    return () => {
      eventSource.close();
    };
  }, [runId, eventsEndpoint]);

  const budgetPercent = (budgetRemaining / budgetCap) * 100;
  const budgetColor =
    budgetPercent > 50
      ? 'bg-green-500'
      : budgetPercent > 20
        ? 'bg-yellow-500'
        : 'bg-red-500';

  const getEventIcon = (kind: string) => {
    switch (kind) {
      case 'run.started':
        return '▶';
      case 'step.started':
        return '⏺';
      case 'tool.called':
        return '🔧';
      case 'tool.result':
        return '📦';
      case 'component.found':
        return '🔍';
      case 'message.delta':
        return '💬';
      case 'answer.final':
        return '✅';
      case 'run.completed':
        return '🏁';
      case 'run.failed':
        return '❌';
      default:
        return '•';
    }
  };

  return (
    <div className="w-96 bg-gray-900 text-gray-100 flex flex-col h-full">
      {/* Header */}
      <div className="flex justify-between items-center p-3 border-b border-gray-700">
        <h3 className="text-sm font-semibold">Agent Trace</h3>
        <button
          onClick={onClose}
          className="text-xs text-gray-400 hover:text-white"
        >
          ✕ Close
        </button>
      </div>

      {/* Connection status */}
      <div className="px-3 py-2 border-b border-gray-700 flex items-center gap-2">
        <span
          className={`w-2 h-2 rounded-full ${
            connected ? 'bg-green-400' : 'bg-red-400'
          }`}
        />
        <span className="text-xs text-gray-400">
          {connected ? 'Streaming' : 'Disconnected'}
        </span>
        {error && <span className="text-xs text-red-400 ml-auto">{error}</span>}
      </div>

      {/* Budget meter */}
      <div className="px-3 py-2 border-b border-gray-700">
        <div className="flex justify-between text-xs text-gray-400 mb-1">
          <span>Budget</span>
          <span>
            ${budgetRemaining.toFixed(2)} / ${budgetCap.toFixed(2)}
          </span>
        </div>
        <div className="w-full bg-gray-700 rounded-full h-2">
          <div
            className={`${budgetColor} h-2 rounded-full transition-all`}
            style={{ width: `${budgetPercent}%` }}
          />
        </div>
      </div>

      {/* Steps summary */}
      {steps.length > 0 && (
        <div className="px-3 py-2 border-b border-gray-700">
          <div className="text-xs text-gray-400 mb-1">
            Steps: {steps.length}
          </div>
          <div className="flex flex-wrap gap-1">
            {steps.map((step) => (
              <span
                key={step.seq}
                className="inline-block px-1.5 py-0.5 bg-gray-700 rounded text-xs"
                title={`${step.name} (${step.latency_ms ?? '?'}ms)`}
              >
                {step.name}
                {step.latency_ms !== undefined && (
                  <span className="text-gray-400 ml-1">
                    {step.latency_ms}ms
                  </span>
                )}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Event stream */}
      <div className="flex-1 overflow-y-auto p-3 space-y-1">
        {events.length === 0 ? (
          <p className="text-xs text-gray-500">
            Waiting for events...
          </p>
        ) : (
          events.map((event, idx) => (
            <div
              key={idx}
              className="flex items-start gap-2 text-xs py-1 border-b border-gray-800"
            >
              <span className="shrink-0">{getEventIcon(event.kind)}</span>
              <div className="min-w-0">
                <span className="text-gray-400">
                  {event.kind}
                </span>
                <span className="text-gray-500 ml-2">
                  {new Date(event.timestamp).toLocaleTimeString()}
                </span>
                {event.data && Object.keys(event.data).length > 0 && (
                  <pre className="mt-1 text-gray-300 truncate">
                    {JSON.stringify(event.data, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Footer with cache info */}
      <div className="px-3 py-2 border-t border-gray-700 text-xs text-gray-500">
        {events.filter((e) => e.data && 'cache_hit' in e.data).length > 0 && (
          <span className="text-green-400">✓ Cache hits detected</span>
        )}
      </div>
    </div>
  );
};

export default AgentTraceDrawer;
