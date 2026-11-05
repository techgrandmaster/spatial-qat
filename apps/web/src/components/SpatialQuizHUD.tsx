import React, { useState } from 'react';
import {
  PublicAssessment,
  AssessmentAttemptResult,
} from '../api/types';

interface SpatialQuizHUDProps {
  assessment: PublicAssessment | null;
  onSubmitClick?: (
    itemId: string,
    x: number,
    y: number,
    explanation?: string,
  ) => Promise<AssessmentAttemptResult>;
  onNextItem?: () => void;
  onPrevItem?: () => void;
}

/**
 * Hidden-target locate/explain quiz interface
 *
 * Presents quiz items without exposing target_component_id or rubric.
 * Collects normalized click coordinates and optional explanation, then
 * displays separable feedback on spatial accuracy and conceptual understanding.
 */
export const SpatialQuizHUD: React.FC<SpatialQuizHUDProps> = ({
  assessment,
  onSubmitClick,
  onNextItem,
  onPrevItem,
}) => {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [clickCoords, setClickCoords] = useState<{
    x: number;
    y: number;
  } | null>(null);
  const [explanation, setExplanation] = useState('');
  const [result, setResult] = useState<AssessmentAttemptResult | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const currentItem = assessment?.items[currentIndex];

  const handleSubmit = async () => {
    if (!currentItem || !clickCoords || submitting) return;
    setSubmitting(true);
    try {
      const res = await onSubmitClick?.(
        currentItem.id,
        clickCoords.x,
        clickCoords.y,
        explanation.trim() || undefined,
      );
      if (res) setResult(res);
    } finally {
      setSubmitting(false);
    }
  };

  const handleReset = () => {
    setClickCoords(null);
    setExplanation('');
    setResult(null);
  };

  if (!assessment || !currentItem) {
    return (
      <div className="p-4 bg-white border rounded shadow">
        <p className="text-gray-500">No quiz loaded.</p>
      </div>
    );
  }

  const isExplain = currentItem.kind === 'explain';

  return (
    <div className="p-4 bg-white border rounded shadow space-y-4">
      {/* Quiz header */}
      <div className="flex justify-between items-center">
        <h2 className="text-lg font-semibold">{assessment.title}</h2>
        <span className="text-sm text-gray-500">
          {currentIndex + 1} / {assessment.items.length}
        </span>
      </div>

      {/* Navigation */}
      <div className="flex gap-2">
        <button
          onClick={() => {
            onPrevItem?.();
            setCurrentIndex((i) => Math.max(0, i - 1));
            handleReset();
          }}
          disabled={currentIndex === 0}
          className="px-3 py-1 text-sm border rounded disabled:opacity-50"
        >
          ← Previous
        </button>
        <button
          onClick={() => {
            onNextItem?.();
            setCurrentIndex((i) =>
              i < assessment.items.length - 1 ? i + 1 : i,
            );
            handleReset();
          }}
          disabled={currentIndex === assessment.items.length - 1}
          className="px-3 py-1 text-sm border rounded disabled:opacity-50"
        >
          Next →
        </button>
      </div>

      {/* Prompt */}
      <div className="border rounded p-3 bg-gray-50">
        <span className="text-xs font-medium text-gray-500 uppercase">
          {currentItem.kind}
        </span>
        <p className="mt-1 text-gray-900">{currentItem.prompt}</p>
        {currentItem.hint && (
          <p className="mt-2 text-sm text-amber-700 italic">
            💡 Hint: {currentItem.hint}
          </p>
        )}
      </div>

      {/* Click feedback */}
      {clickCoords && !result && (
        <p className="text-sm text-blue-600">
          Click registered at ({clickCoords.x.toFixed(3)},{' '}
          {clickCoords.y.toFixed(3)}).
          {isExplain && ' Add an explanation below (optional).'}
        </p>
      )}

      {/* Explanation input for explain items */}
      {isExplain && !result && (
        <textarea
          value={explanation}
          onChange={(e) => setExplanation(e.target.value)}
          placeholder="Explain your answer..."
          className="w-full border rounded p-2 text-sm h-20"
        />
      )}

      {/* Submit button */}
      {clickCoords && !result && (
        <button
          onClick={handleSubmit}
          disabled={submitting}
          className="w-full px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {submitting ? 'Grading...' : 'Submit Answer'}
        </button>
      )}

      {/* Result feedback */}
      {result && (
        <div className="border rounded p-4 space-y-3">
          {/* Spatial score */}
          <div>
            <h3 className="text-sm font-semibold text-gray-700">
              Spatial Accuracy
            </h3>
            <div className="mt-1">
              {result.spatial_hit ? (
                <span className="text-green-600 font-medium">✓ Hit!</span>
              ) : result.near_miss ? (
                <span className="text-yellow-600 font-medium">
                  ~ Near miss
                </span>
              ) : (
                <span className="text-red-600 font-medium">✗ Miss</span>
              )}
              <span className="ml-2 text-sm text-gray-500">
                (score: {result.spatial_score.toFixed(2)})
              </span>
            </div>
          </div>

          {/* Conceptual score */}
          {result.conceptual_status && result.conceptual_status !== 'skipped' && (
            <div>
              <h3 className="text-sm font-semibold text-gray-700">
                Conceptual Understanding
              </h3>
              <p className="text-sm text-gray-600">
                Status: {result.conceptual_status}
                {result.conceptual_score !== undefined &&
                  ` (${result.conceptual_score.toFixed(2)})`}
              </p>
              {result.feedback_text && (
                <p className="mt-1 text-sm italic text-gray-700">
                  "{result.feedback_text}"
                </p>
              )}
            </div>
          )}

          {/* Target reveal */}
          <div className="text-xs text-gray-500">
            Target component ID: {result.target_component_id}
            {result.clicked_component_id && (
              <span>
                {' '}
                | Clicked: {result.clicked_component_id}
              </span>
            )}
          </div>

          {/* Citations */}
          {result.citations && result.citations.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-gray-500">
                Supporting Citations
              </h4>
              <ul className="text-xs text-gray-600 list-disc list-inside">
                {result.citations.map((c, i) => (
                  <li key={i}>
                    Page {c.page_number}: "{c.quote}"
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Continue button */}
          <button
            onClick={() => {
              handleReset();
              onNextItem?.();
              setCurrentIndex((i) =>
                i < assessment.items.length - 1 ? i + 1 : i,
              );
            }}
            className="w-full px-4 py-2 bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
          >
            Continue →
          </button>
        </div>
      )}
    </div>
  );
};

export default SpatialQuizHUD;
