import React, { useState, useCallback } from 'react';
import ReactFlow, { 
  addEdge, 
  Background, 
  Controls, 
  applyNodeChanges, 
  applyEdgeChanges,
  MarkerType
} from 'reactflow';
import 'reactflow/dist/style.css';
import { UploadCloud, Zap, Loader2, ArrowRight } from 'lucide-react';

const TARGET_SCHEMA_NODES = [
  { id: 't-mrn', data: { label: 'EMR: mrn (Optional, Auto-Generated)' }, position: { x: 450, y: 50 }, type: 'output', targetPosition: 'left' },
  { id: 't-full_name', data: { label: 'EMR: full_name (Required)' }, position: { x: 450, y: 120 }, type: 'output', targetPosition: 'left' },
  { id: 't-date_of_birth', data: { label: 'EMR: date_of_birth (YYYY-MM-DD)' }, position: { x: 450, y: 190 }, type: 'output', targetPosition: 'left' },
  { id: 't-phone_number', data: { label: 'EMR: phone_number' }, position: { x: 450, y: 260 }, type: 'output', targetPosition: 'left' },
  { id: 't-gender', data: { label: 'EMR: gender (M/F)' }, position: { x: 450, y: 330 }, type: 'output', targetPosition: 'left' }
];

export default function MigrationStudio({ masterKey, tenantSlug }) {
  const [file, setFile] = useState(null);
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const onNodesChange = useCallback((changes) => setNodes((nds) => applyNodeChanges(changes, nds)), []);
  const onEdgesChange = useCallback((changes) => setEdges((eds) => applyEdgeChanges(changes, eds)), []);
  
  const onConnect = useCallback((connection) => {
    // Only allow left-to-right connections from CSV to EMR Schema
    if (connection.source.startsWith('s-') && connection.target.startsWith('t-')) {
      setEdges((eds) => addEdge({ 
        ...connection, 
        animated: true, 
        style: { stroke: '#10b981', strokeWidth: 2 },
        markerEnd: { type: MarkerType.ArrowClosed, color: '#10b981' }
      }, eds));
    } else {
      alert("Invalid mapping. Drag from CSV column to EMR Schema.");
    }
  }, []);

  const handleFileUpload = async (e) => {
    const uploadedFile = e.target.files[0];
    if (!uploadedFile) return;
    setFile(uploadedFile);
    setLoading(true);

    const formData = new FormData();
    formData.append('file', uploadedFile);
    formData.append('master_setup_key', masterKey);

    try {
      const res = await fetch(`/api/migration/${tenantSlug}/analyze-csv`, {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (!res.ok) {
        const errText = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
        throw new Error(errText);
      }

      // Generate Input Source Nodes with handles on the RIGHT
      const sourceNodes = data.source_columns.map((col, index) => ({
        id: `s-${col}`,
        data: { label: `CSV: ${col}` },
        position: { x: 50, y: 50 + (index * 70) },
        type: 'input',
        sourcePosition: 'right',
      }));

      setNodes([...sourceNodes, ...TARGET_SCHEMA_NODES]);
      setEdges([]);
    } catch (err) {
      alert(`CSV Analysis Failed: ${err.message}`);
      setFile(null);
    } finally {
      setLoading(false);
    }
  };

  const handleExecuteMigration = async () => {
    if (edges.length === 0) return alert("Please map at least one field by dragging a wire.");
    setLoading(true);

    // Compile edges into JSON e.g. { "full_name": "CSV_Patient_Name" }
    const mapping = {};
    edges.forEach(edge => {
      const sourceCol = edge.source.replace('s-', '');
      const targetCol = edge.target.replace('t-', '');
      mapping[targetCol] = sourceCol;
    });

    const formData = new FormData();
    formData.append('file', file);
    formData.append('master_setup_key', masterKey);
    formData.append('entity_type', 'PATIENTS');
    formData.append('node_mapping_json', JSON.stringify(mapping));

    try {
      const res = await fetch(`/api/migration/${tenantSlug}/execute-node-mapping`, {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (!res.ok) {
        const errText = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
        throw new Error(errText);
      }
      setResult(data);
    } catch (err) {
      alert(`Migration Failed: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-[500px] w-full bg-slate-950 rounded-2xl border border-slate-800 flex flex-col overflow-hidden">
      <div className="p-3 border-b border-slate-800 flex items-center justify-between bg-slate-900/80">
        <div>
          <h3 className="font-bold text-white flex items-center gap-2">
            <Zap className="w-4 h-4 text-cyan-400" />
            Visual ETL Canvas
          </h3>
        </div>
        
        <div className="flex items-center gap-3">
          <label className="bg-slate-800 hover:bg-slate-700 cursor-pointer px-4 py-2 rounded-lg text-xs font-bold transition flex items-center gap-2">
            <UploadCloud className="w-4 h-4" />
            {file ? file.name : "1. Upload CSV"}
            <input type="file" accept=".csv" className="hidden" onChange={handleFileUpload} />
          </label>
          
          <button 
            onClick={handleExecuteMigration}
            disabled={!file || loading}
            className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-xs font-bold transition flex items-center gap-2 shadow-lg"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />}
            2. Run Injection
          </button>
        </div>
      </div>

      <div className="flex-1 relative">
        {result ? (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-950/90 z-10">
            <div className="text-center space-y-3">
              <div className="text-4xl font-black text-emerald-400">{result.records_migrated}</div>
              <div className="text-sm font-bold text-slate-300">Records Successfully Migrated</div>
              {result.error_count > 0 && (
                <div className="text-xs text-rose-400 bg-rose-500/10 p-2 rounded">
                  {result.error_count} records failed validation. See logs.
                </div>
              )}
              <button 
                onClick={() => { setResult(null); setFile(null); setNodes([]); setEdges([]); }} 
                className="mt-4 text-xs bg-slate-800 hover:bg-slate-700 px-4 py-2 rounded-lg font-bold"
              >
                Start New Migration
              </button>
            </div>
          </div>
        ) : (
          <ReactFlow 
            nodes={nodes} 
            edges={edges} 
            onNodesChange={onNodesChange} 
            onEdgesChange={onEdgesChange} 
            onConnect={onConnect}
            fitView
            className="dark"
          >
            <Background color="#334155" gap={16} />
            <Controls />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}