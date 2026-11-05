import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';

/**
 * Spatial-QAT frontend entry point.
 * Renders the root application shell.
 */
const root = ReactDOM.createRoot(document.getElementById('root')!);
root.render(
  <React.StrictMode>
    <div className="min-h-screen bg-gray-100 flex items-center justify-center">
      <h1 className="text-2xl font-bold text-gray-700">Spatial-QAT</h1>
    </div>
  </React.StrictMode>,
);
