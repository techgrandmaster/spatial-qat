import React from 'react';
import { Component, ComponentEvidence, DocumentPage } from '../api/types';

interface InspectorPanelProps {
  component: Component | null;
  evidence?: ComponentEvidence[];
  pages?: DocumentPage[];
  onConfirm?: (componentId: string) => void;
  onRename?: (componentId: string, newName: string) => void;
  onClose?: () => void;
}

/**
 * Component details, verify toggle & PDF page viewer
 *
 * Displays component label, role, verification_status (proposed vs. verified),
 * and cited datasheet snippets; allows user to confirm or rename components.
 */
export const InspectorPanel: React.FC<InspectorPanelProps> = ({
  component,
  evidence = [],
  pages = [],
  onConfirm,
  onRename,
  onClose,
}) => {
  const [editingName, setEditingName] = React.useState(false);
  const [nameInput, setNameInput] = React.useState('');
  const [selectedPage, setSelectedPage] = React.useState<DocumentPage | null>(
    null,
  );

  if (!component) {
    return (
      <div className="w-80 bg-white border-l p-4 text-gray-500">
        <button
          onClick={onClose}
          className="mb-4 text-sm text-blue-600 hover:underline"
        >
          ← Back
        </button>
        <p className="text-sm">
          Click a component on the board to inspect its details.
        </p>
      </div>
    );
  }

  const statusColor =
    component.verification_status === 'verified'
      ? 'text-green-600 bg-green-50'
      : component.verification_status === 'rejected'
        ? 'text-red-600 bg-red-50'
        : 'text-yellow-600 bg-yellow-50';

  const handleRename = () => {
    if (nameInput.trim()) {
      onRename?.(component.id, nameInput.trim());
      setEditingName(false);
    }
  };

  return (
    <div className="w-80 bg-white border-l overflow-y-auto">
      <div className="p-4">
        <button
          onClick={onClose}
          className="mb-4 text-sm text-blue-600 hover:underline"
        >
          ← Back
        </button>

        {/* Component name */}
        <div className="mb-4">
          {editingName ? (
            <div className="flex gap-2">
              <input
                type="text"
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
                className="flex-1 border rounded px-2 py-1 text-sm"
                autoFocus
                onKeyDown={(e) => e.key === 'Enter' && handleRename()}
              />
              <button
                onClick={handleRename}
                className="px-2 py-1 bg-blue-600 text-white text-sm rounded"
              >
                Save
              </button>
              <button
                onClick={() => setEditingName(false)}
                className="px-2 py-1 text-sm text-gray-500"
              >
                Cancel
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold">{component.label}</h2>
              <button
                onClick={() => {
                  setNameInput(component.label);
                  setEditingName(true);
                }}
                className="text-xs text-blue-600 hover:underline"
              >
                Rename
              </button>
            </div>
          )}
        </div>

        {/* Status badge */}
        <span
          className={`inline-block px-2 py-0.5 rounded text-xs font-medium mb-4 ${statusColor}`}
        >
          {component.verification_status}
        </span>

        {/* Details */}
        <dl className="space-y-2 text-sm mb-4">
          {component.noun_phrase && (
            <div>
              <dt className="text-gray-500">Noun Phrase</dt>
              <dd>{component.noun_phrase}</dd>
            </div>
          )}
          {component.category && (
            <div>
              <dt className="text-gray-500">Category</dt>
              <dd>{component.category}</dd>
            </div>
          )}
          {component.role && (
            <div>
              <dt className="text-gray-500">Role</dt>
              <dd>{component.role}</dd>
            </div>
          )}
          {component.description && (
            <div>
              <dt className="text-gray-500">Description</dt>
              <dd>{component.description}</dd>
            </div>
          )}
          {component.confidence !== undefined && (
            <div>
              <dt className="text-gray-500">Confidence</dt>
              <dd>{(component.confidence * 100).toFixed(1)}%</dd>
            </div>
          )}
          {component.som_index !== undefined && (
            <div>
              <dt className="text-gray-500">SoM Index</dt>
              <dd>[{component.som_index}]</dd>
            </div>
          )}
        </dl>

        {/* Verify button */}
        {component.verification_status === 'proposed' && onConfirm && (
          <button
            onClick={() => onConfirm(component.id)}
            className="w-full mb-4 px-3 py-2 bg-green-600 text-white text-sm rounded hover:bg-green-700"
          >
            ✓ Confirm Component
          </button>
        )}

        {/* Evidence / Citations */}
        {evidence.length > 0 && (
          <div className="mb-4">
            <h3 className="text-sm font-semibold mb-2">Citations</h3>
            <div className="space-y-2">
              {evidence.map((ev) => (
                <div
                  key={ev.id}
                  className="border rounded p-2 text-sm cursor-pointer hover:bg-gray-50"
                  onClick={() => {
                    const page = pages.find((p) => p.id === ev.document_page_id);
                    if (page) setSelectedPage(page);
                  }}
                >
                  {ev.quoted_text && (
                    <p className="text-gray-700 italic">"{ev.quoted_text}"</p>
                  )}
                  {ev.document_page_id && (
                    <p className="text-xs text-blue-600 mt-1">
                      → View datasheet page
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* PDF Page Viewer */}
        {selectedPage && (
          <div className="border rounded p-3 bg-gray-50">
            <div className="flex justify-between items-center mb-2">
              <h4 className="text-sm font-semibold">
                Page {selectedPage.page_number}
              </h4>
              <button
                onClick={() => setSelectedPage(null)}
                className="text-xs text-gray-500"
              >
                Close
              </button>
            </div>
            <pre className="text-xs whitespace-pre-wrap max-h-48 overflow-y-auto">
              {selectedPage.text_content}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
};

export default InspectorPanel;
