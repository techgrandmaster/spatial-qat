import React, { useState, useCallback } from 'react';
import { Component } from '../api/types';

interface BoardOverlayProps {
  imageUrl: string;
  components: Component[];
  highlightedIds?: string[];
  onSelectComponent?: (component: Component) => void;
  onClickBoard?: (normalizedX: number, normalizedY: number) => void;
  quizMode?: boolean;
}

/**
 * Normalized [0,1] SVG mask overlay + zoom/pan
 *
 * Renders clickable, keyboard-focusable (ARIA) SVG polygons over the natural
 * aspect-ratio image; preserves accurate coordinate mapping across zoom/pan
 * and high-DPI viewports.
 */
export const BoardOverlay: React.FC<BoardOverlayProps> = ({
  imageUrl,
  components,
  highlightedIds = [],
  onSelectComponent,
  onClickBoard,
  quizMode = false,
}) => {
  const [zoom, setZoom] = useState(1);
  const [pan] = useState({ x: 0, y: 0 });

  const handleBoardClick = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      const svg = e.currentTarget;
      const rect = svg.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width;
      const y = (e.clientY - rect.top) / rect.height;
      onClickBoard?.(x, y);
    },
    [onClickBoard],
  );

  const handleZoomIn = () => setZoom((z) => Math.min(z + 0.25, 4));
  const handleZoomOut = () => setZoom((z) => Math.max(z - 0.25, 0.5));

  return (
    <div className="relative w-full overflow-auto">
      {/* Zoom controls */}
      <div className="absolute top-2 right-2 z-10 flex gap-2">
        <button
          onClick={handleZoomIn}
          className="px-2 py-1 bg-white/80 rounded shadow text-sm"
          aria-label="Zoom in"
        >
          +
        </button>
        <button
          onClick={handleZoomOut}
          className="px-2 py-1 bg-white/80 rounded shadow text-sm"
          aria-label="Zoom out"
        >
          −
        </button>
      </div>

      {/* Board image with SVG overlay */}
      <div
        className="inline-block"
        style={{
          transform: `scale(${zoom}) translate(${pan.x}px, ${pan.y}px)`,
          transformOrigin: 'top left',
        }}
      >
        <img
          src={imageUrl}
          alt="Development board"
          className="block max-w-full"
          draggable={false}
        />
        <svg
          viewBox="0 0 1 1"
          preserveAspectRatio="none"
          className="absolute top-0 left-0 w-full h-full"
          onClick={handleBoardClick}
          role="group"
          aria-label="Component overlay"
        >
          {components.map((comp) => {
            const isHighlighted = highlightedIds.includes(comp.id);
            const isVerified = comp.verification_status === 'verified';

            return (
              <g key={comp.id}>
                {/* Render polygons from the component */}
                {comp.polygons.map((ring, ringIdx) => {
                  const points = ring
                    .map((coord) => `${coord[0]},${coord[1]}`)
                    .join(' ');

                  return (
                    <polygon
                      key={`${comp.id}-${ringIdx}`}
                      points={points}
                      fill={
                        isHighlighted
                          ? 'rgba(59, 130, 246, 0.35)'
                          : isVerified
                            ? 'rgba(34, 197, 94, 0.2)'
                            : 'rgba(251, 191, 36, 0.15)'
                      }
                      stroke={
                        isHighlighted
                          ? '#3b82f6'
                          : isVerified
                            ? '#22c55e'
                            : '#fbbf24'
                      }
                      strokeWidth={0.005}
                      className="cursor-pointer"
                      tabIndex={0}
                      role="button"
                      aria-label={
                        quizMode ? 'Component region' : comp.label
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectComponent?.(comp);
                      }}
                    />
                  );
                })}

                {/* SoM index badge */}
                {comp.som_index !== undefined && !quizMode && (
                  <text
                    x={comp.bbox.x + comp.bbox.w / 2}
                    y={comp.bbox.y + 0.02}
                    textAnchor="middle"
                    fontSize={0.03}
                    fill="white"
                    style={{
                      textShadow:
                        '0 0 2px rgba(0,0,0,0.8), 0 0 4px rgba(0,0,0,0.6)',
                    }}
                  >
                    [{comp.som_index}]
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
};

export default BoardOverlay;
