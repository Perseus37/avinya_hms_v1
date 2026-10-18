import React, { useState, useCallback, useEffect, useRef } from 'react';
import ReactFlow, {
  Background, Controls, applyNodeChanges, applyEdgeChanges, addEdge,
  MarkerType, Handle, Position
} from 'reactflow';
import 'reactflow/dist/style.css';
import { Upload, Play, Eye, X, Settings2, Table as TableIcon, Database, Settings, Plus, GitBranch, Merge, Trash2 } from 'lucide-react';

const TRANSFORM_OPTIONS = [
  ["constant", "Constant value"],
  ["trim", "Trim whitespace"],
  ["uppercase", "Uppercase"],
  ["lowercase", "Lowercase"],
  ["map", "Map values"],
  ["replace", "Replace text"],
  ["conditional", "Conditional value"],
  ["combine", "Combine inputs"],
  ["coalesce", "First non-empty input"],
  ["null_if", "Replace matching value with null"],
  ["substring", "Substring"],
  ["split_part", "Split and select part"],
  ["to_number", "Parse number"],
  ["add", "Add number"],
  ["subtract", "Subtract number"],
  ["multiply", "Multiply number"],
  ["divide", "Divide number"],
  ["parse_date", "Parse date"],
];

function TargetSchemaNode({ id, data }) {
  const hasFallback = !!data.fallback;
  return (
    <div className={`px-4 py-2 shadow-sm rounded-lg border-2 bg-white flex flex-col min-w-[220px] ${hasFallback ? 'border-emerald-500' : 'border-slate-300'}`}>
      <Handle type="target" position={Position.Left} className="w-3 h-3 bg-cyan-500" />
      <div className="flex justify-between items-center gap-3">
        <div className="font-bold text-xs text-slate-700">
          {data.label}{data.required ? <span className="ml-1 text-rose-600">*</span> : null}
        </div>
        <button
          onClick={() => data.onEditFallback(id)}
          className={`p-1.5 rounded transition-colors ${hasFallback ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}
          title="Fallback value"
        >
          <Settings size={14} />
        </button>
      </div>
      {hasFallback && (
        <div className="text-[9px] font-bold text-emerald-600 mt-1 uppercase tracking-wider">
          Fallback configured
        </div>
      )}
    </div>
  );
}

function EtlTransformNode({ id, data }) {
  const operationLabel = TRANSFORM_OPTIONS.find(([value]) => value === data.operation)?.[1] || data.operation;
  const isConstant = data.operation === "constant";
  return (
    <div className="min-w-[230px] rounded-xl border-2 border-violet-400 bg-white p-3 shadow-md">
      {!isConstant && <Handle type="target" position={Position.Left} className="w-3 h-3 bg-violet-500" />}
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[9px] font-black uppercase tracking-wider text-violet-700">{data.kind}</span>
        <button type="button" onClick={() => data.onRemove(id)} title="Delete node" className="nodrag rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600">
          <Trash2 size={13} />
        </button>
      </div>
      <select
        className="nodrag w-full rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-[11px] font-bold text-slate-800"
        value={data.operation}
        aria-label="Transformation operation"
        onChange={(event) => data.onOperationChange(id, event.target.value)}
      >
        {TRANSFORM_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      <button type="button" onClick={() => data.onEdit(id)} className="nodrag mt-2 text-left text-[10px] font-semibold text-violet-700 hover:underline">
        Configure {operationLabel}
      </button>
      {data.operation === "combine" && <Handle type="target" position={Position.Left} id="input-2" style={{ top: "75%" }} className="w-3 h-3 bg-violet-500" />}
      {data.operation === "coalesce" && <Handle type="target" position={Position.Left} id="input-2" style={{ top: "75%" }} className="w-3 h-3 bg-violet-500" />}
      <Handle type="source" position={Position.Right} className="w-3 h-3 bg-violet-500" />
    </div>
  );
}

const nodeTypes = { targetNode: TargetSchemaNode, etlNode: EtlTransformNode };

export default function MigrationStudio({ tenants, request, catalogs, notify }) {
  // Extract dynamic schemas passed from the Control Plane API
  const { migrationSchemas = {} } = catalogs || {};
  const availableEntities = Object.keys(migrationSchemas);

  const [selectedTenantSlug, setSelectedTenantSlug] = useState("");
  const [entityType, setEntityType] = useState("");
  const [duplicatePolicy, setDuplicatePolicy] = useState("skip");
  const [duplicateField, setDuplicateField] = useState("");

  const [nodes, setNodes] = useState([]);
  const nodesRef = useRef(nodes);
  const [edges, setEdges] = useState([]);
  const [file, setFile] = useState(null);

  const [isUploading, setIsUploading] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);

  const [previewData, setPreviewData] = useState(null);
  const [previewModalOpen, setPreviewModalOpen] = useState(false);

  const [transformModalOpen, setTransformModalOpen] = useState(false);
  const [activeTransformNodeId, setActiveTransformNodeId] = useState(null);
  const [transformForm, setTransformForm] = useState({
    fallback: "",
    value: "",
    oldValue: "",
    newValue: "",
    map: "",
    operator: "equals",
    compareValue: "",
    whenTrue: "",
    whenFalse: "",
    separator: " ",
    operand: "",
    start: "0",
    length: "",
    delimiter: ",",
    part_index: "0",
    input_format: "%Y-%m-%d",
    output_format: "%Y-%m-%d",
  });

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  // Sync default entity type once catalogs load from backend
  useEffect(() => {
    if (availableEntities.length > 0 && !entityType) {
      setEntityType(availableEntities[0]);
    }
  }, [availableEntities, entityType]);

  const uniqueFields = (Array.isArray(migrationSchemas[entityType])
    ? migrationSchemas[entityType]
    : []).filter((field) => field.unique);
  useEffect(() => {
    if (!uniqueFields.some((field) => field.id === duplicateField)) {
      setDuplicateField(uniqueFields[0]?.id || "");
    }
  }, [entityType, migrationSchemas, duplicateField]);

  const onNodesChange = useCallback((changes) => setNodes((nds) => applyNodeChanges(changes, nds)), []);
  const onEdgesChange = useCallback((changes) => setEdges((eds) => applyEdgeChanges(changes, eds)), []);
  const onConnect = useCallback((connection) => {
    const sourceNode = nodesRef.current.find((node) => node.id === connection.source);
    const targetNode = nodesRef.current.find((node) => node.id === connection.target);
    if (!sourceNode || !targetNode || sourceNode.type === "targetNode" || sourceNode.id.startsWith("target-") ||
        targetNode.id.startsWith("source-") || targetNode.type === "input" ||
        (targetNode.type !== "targetNode" && targetNode.type !== "etlNode")) return;
    const styledConnection = {
      ...connection,
      animated: true,
      style: { stroke: '#06b6d4', strokeWidth: 3 },
      markerEnd: { type: MarkerType.ArrowClosed, color: '#06b6d4' },
    };
    setEdges((eds) => addEdge(styledConnection, eds));
  }, []);

  const handleOpenTransformModal = useCallback((nodeId) => {
    const node = nodesRef.current.find((currentNode) => currentNode.id === nodeId);
    if (!node) return;
    const config = node.data.config || {};
    setTransformForm({
      fallback: node.data.fallback || "",
      value: config.value || "",
      oldValue: config.oldValue || "",
      newValue: config.newValue || "",
      map: config.map ? JSON.stringify(config.map, null, 2) : "",
      operator: config.operator || "equals",
      compareValue: config.compareValue || "",
      whenTrue: config.whenTrue || "",
      whenFalse: config.whenFalse || "",
      separator: config.separator ?? " ",
      operand: config.operand ?? "",
      start: String(config.start ?? 0),
      length: config.length == null ? "" : String(config.length),
      delimiter: config.delimiter ?? ",",
      part_index: String(config.part_index ?? 0),
      input_format: config.input_format ?? "%Y-%m-%d",
      output_format: config.output_format ?? "%Y-%m-%d",
    });
    setActiveTransformNodeId(nodeId);
    setTransformModalOpen(true);
  }, []);

  const handleRemoveNode = useCallback((nodeId) => {
    setNodes((current) => current.filter((node) => node.id !== nodeId));
    setEdges((current) => current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
  }, []);

  const handleChangeOperation = useCallback((nodeId, operation) => {
    setNodes((current) => current.map((node) => node.id === nodeId
      ? { ...node, data: { ...node.data, operation } }
      : node));
  }, []);

  const addTransformNode = useCallback((operation, kind) => {
    const id = `etl-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
    setNodes((current) => [
      ...current,
      {
        id,
        type: "etlNode",
        position: { x: 480, y: 100 + current.filter((node) => node.type === "etlNode").length * 115 },
        data: {
          kind,
          operation,
          config: {},
          onEdit: handleOpenTransformModal,
          onRemove: handleRemoveNode,
          onOperationChange: handleChangeOperation,
        },
      },
    ]);
  }, [handleChangeOperation, handleOpenTransformModal, handleRemoveNode]);

  const handleSaveTransformRules = () => {
    const activeNode = nodesRef.current.find((node) => node.id === activeTransformNodeId);
    if (!activeNode) return;
    try {
      const config = { ...activeNode.data.config };
      if (activeNode.type === "targetNode") {
        setNodes((current) => current.map((node) => node.id === activeTransformNodeId
          ? { ...node, data: { ...node.data, fallback: transformForm.fallback } }
          : node));
      } else {
        if (activeNode.data.operation === "map") {
          config.map = transformForm.map.trim() ? JSON.parse(transformForm.map) : {};
          if (!config.map || Array.isArray(config.map) || typeof config.map !== "object") {
            throw new Error("Value map must be a JSON object.");
          }
        } else if (activeNode.data.operation === "constant") {
          config.value = transformForm.value;
        } else if (activeNode.data.operation === "replace") {
          config.oldValue = transformForm.oldValue;
          config.newValue = transformForm.newValue;
        } else if (activeNode.data.operation === "conditional") {
          config.operator = transformForm.operator;
          config.compareValue = transformForm.compareValue;
          config.whenTrue = transformForm.whenTrue;
          config.whenFalse = transformForm.whenFalse;
        } else if (activeNode.data.operation === "combine") {
          config.separator = transformForm.separator;
        } else if (activeNode.data.operation === "null_if") {
          config.compareValue = transformForm.compareValue;
        } else if (activeNode.data.operation === "substring") {
          config.start = Number(transformForm.start);
          config.length = transformForm.length === "" ? null : Number(transformForm.length);
        } else if (activeNode.data.operation === "split_part") {
          config.delimiter = transformForm.delimiter;
          config.part_index = Number(transformForm.part_index);
        } else if (["add", "subtract", "multiply", "divide"].includes(activeNode.data.operation)) {
          config.operand = transformForm.operand;
        } else if (activeNode.data.operation === "parse_date") {
          config.input_format = transformForm.input_format;
          config.output_format = transformForm.output_format;
        }
        setNodes((current) => current.map((node) => node.id === activeTransformNodeId
          ? { ...node, data: { ...node.data, config } }
          : node));
      }
      setTransformModalOpen(false);
    } catch (error) {
      notify(error.message || "Invalid transformation configuration.", "err");
    }
  };

  const initTargetNodes = useCallback((selectedEntity) => {
    if (!selectedEntity || !migrationSchemas[selectedEntity]) return;

    const schema = migrationSchemas[selectedEntity];
    const schemaFields = Array.isArray(schema)
      ? schema
      : Object.entries(schema).map(([id, type]) => ({ id, label: id, type }));
    const newTargetNodes = schemaFields.map((field, idx) => ({
      id: `target-${field.id}`,
      type: 'targetNode',
      position: { x: 1010, y: 90 + (idx * 90) },
      data: {
        label: field.label || field.id,
        required: field.required,
        fallback: "",
        onEditFallback: handleOpenTransformModal,
      },
    }));

    setNodes((nds) => {
      const sourceNodes = nds.filter(n => n.id.startsWith("source-"));
      return [...newTargetNodes, ...sourceNodes];
    });
    setEdges([]);
  }, [migrationSchemas, handleOpenTransformModal]);

  useEffect(() => {
    initTargetNodes(entityType);
  }, [entityType, initTargetNodes]);

  const handleFileUpload = async (e) => {
    const selectedFile = e.target.files[0];
    if (!selectedFile) return;
    if (!selectedTenantSlug) {
      notify("Select a target hospital before uploading a CSV.", "err");
      e.target.value = "";
      return;
    }
    setFile(selectedFile);
    setIsUploading(true);

    const formData = new FormData();
    formData.append("file", selectedFile);

    try {
      const data = await request(`/api/migration/${selectedTenantSlug}/analyze-csv`, {
        method: "POST",
        body: formData
      }, "csv-upload");

      const sourceNodes = data.source_columns.map((col, idx) => ({
        id: `source-${col}`,
        type: 'input',
        data: { label: `[CSV] ${col}`, sourceColumn: col },
        position: { x: 30, y: 90 + (idx * 75) },
        sourcePosition: 'right',
        targetPosition: 'left',
        className: 'border-2 border-slate-300 bg-white shadow-sm rounded-lg p-3 font-mono text-xs font-bold text-slate-800'
      }));

      setNodes(nds => {
        const targets = nds.filter(n => n.id.startsWith("target-"));
        return [...targets, ...sourceNodes];
      });
      setEdges([]);
    } catch (error) {
      setFile(null);
      notify(`CSV analysis failed: ${error.message}`, "err");
    } finally {
      setIsUploading(false);
    }
  };

  const buildExpression = (nodeId, seen = new Set()) => {
    if (seen.has(nodeId)) throw new Error("The ETL graph contains a cycle.");
    const node = nodes.find((current) => current.id === nodeId);
    if (!node) throw new Error("The ETL graph references a missing node.");
    if (node.id.startsWith("source-")) {
      return { type: "source", column: node.data.sourceColumn || node.id.slice("source-".length) };
    }
    if (node.type !== "etlNode") {
      throw new Error("Connections must end at a destination or transformation node.");
    }
    const nextSeen = new Set(seen).add(nodeId);
    const inputs = edges
      .filter((edge) => edge.target === nodeId)
      .map((edge) => buildExpression(edge.source, nextSeen));
    const operation = node.data.operation;
    if (operation === "constant") {
      return { type: "constant", value: node.data.config?.value ?? "" };
    }
    if (operation === "combine" || operation === "coalesce") {
      if (inputs.length < 2) throw new Error("A combine node needs at least two connected inputs.");
      return operation === "combine"
        ? { type: "combine", inputs, separator: node.data.config?.separator ?? " " }
        : { type: "coalesce", inputs };
    }
    if (inputs.length !== 1) {
      throw new Error(`The ${operation} block needs exactly one connected input.`);
    }
    return {
      type: "transform",
      operation,
      input: inputs[0],
      ...node.data.config,
    };
  };

  const compileMappingJson = () => {
    const mapping = {};
    const targetNodes = nodes.filter((node) => node.type === "targetNode");
    targetNodes.forEach((target) => {
      const inputEdges = edges.filter((edge) => edge.target === target.id);
      const field = target.id.slice("target-".length);
      const rule = {};
      if (inputEdges.length > 1) {
        throw new Error(`Destination field '${field}' can have only one connected input.`);
      }
      if (inputEdges.length === 1) {
        rule.expression = buildExpression(inputEdges[0].source);
      }
      if (target.data.fallback) rule.fallback = target.data.fallback;
      if (Object.keys(rule).length) mapping[field] = rule;
    });
    return mapping;
  };

  const validateRequiredMappings = () => {
    const schema = migrationSchemas[entityType];
    const requiredFields = Array.isArray(schema) ? schema.filter((field) => field.required) : [];
    const mappedTargets = new Set(edges.map((edge) => edge.target));
    if (duplicatePolicy !== "allow") {
      const duplicateTarget = nodes.find((node) => node.id === `target-${duplicateField}`);
      if (
        !duplicateField ||
        (!mappedTargets.has(`target-${duplicateField}`) &&
          !String(duplicateTarget?.data?.fallback || "").trim())
      ) {
        notify("Connect the selected unique duplicate key to a CSV value or configure its fallback.", "err");
        return false;
      }
    }
    const missingFields = requiredFields.filter((field) => {
      if (mappedTargets.has(`target-${field.id}`)) return false;
      const target = nodes.find((node) => node.id === `target-${field.id}`);
      return !String(target?.data?.fallback || "").trim();
    });
    if (missingFields.length) {
      notify(
        `Map or provide fallback values for required fields: ${missingFields.map((field) => field.label || field.id).join(", ")}`,
        "err",
      );
      return false;
    }
    return true;
  };

  const handlePreview = async () => {
    if (!file || edges.length === 0) return notify("Upload a CSV and connect at least one source field.", "err");
    if (!entityType) return notify("Select a destination table before previewing.", "err");
    if (!validateRequiredMappings()) return;
    const formData = new FormData();
    formData.append("file", file);
    formData.append("entity_type", entityType);
    formData.append("duplicate_policy", duplicatePolicy);
    if (duplicatePolicy !== "allow" && duplicateField) formData.append("duplicate_field", duplicateField);

    try {
      formData.append("node_mapping_json", JSON.stringify(compileMappingJson()));
      const data = await request(`/api/migration/${selectedTenantSlug}/preview-mapping`, {
        method: "POST",
        body: formData
      }, "csv-preview");

      setPreviewData(data);
      setPreviewModalOpen(true);
    } catch (error) {
      notify(`Preview failed: ${error.message}`, "err");
    }
  };

  const handleExecute = async () => {
    if (!file || edges.length === 0) return notify("Upload a CSV and connect at least one source field.", "err");
    if (!entityType) return notify("Select a destination table before running a migration.", "err");
    if (!validateRequiredMappings()) return;
    if (!window.confirm(`Initialize background migration into ${selectedTenantSlug}? This will lock the current mapping.`)) return;

    setIsExecuting(true);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("entity_type", entityType);
    formData.append("duplicate_policy", duplicatePolicy);
    if (duplicatePolicy !== "allow" && duplicateField) formData.append("duplicate_field", duplicateField);

    try {
      formData.append("node_mapping_json", JSON.stringify(compileMappingJson()));
      const data = await request(`/api/migration/${selectedTenantSlug}/execute-node-mapping`, {
        method: "POST",
        body: formData
      }, "csv-execute");
      notify(data.message);
    } catch (error) {
      notify(`Execution failed: ${error.message}`, "err");
    } finally {
      setIsExecuting(false);
    }
  };

  return (
    <div className="relative space-y-6 h-full min-h-[calc(100vh-2rem)] md:min-h-0 flex flex-col pb-6">
      <div className="flex flex-wrap items-center justify-between gap-4 shrink-0">
        <div>
          <h1 className="text-2xl font-black tracking-tight">Visual ETL Data Migration Studio</h1>
          <p className="text-xs theme-text-muted mt-0.5">Build a left-to-right ETL flow: source columns, reusable transform/condition/combine blocks, then destination fields.</p>
          <p className="mt-1 max-w-3xl text-[11px] theme-text-muted">Use CSV preview to validate mapped values. Duplicate handling requires a destination field with a single-column unique constraint. Back up the tenant before importing; Update intentionally overwrites mapped fields on matching records.</p>
        </div>
        <div className="w-full md:w-auto flex flex-wrap items-center gap-2 bg-white p-2 rounded-xl shadow-sm border border-slate-200">
          <div className="min-w-0 flex flex-wrap items-center gap-2 px-2 md:border-r border-slate-200">
            <span className="text-[10px] font-bold uppercase text-slate-500">Target Tenant</span>
            <select
              value={selectedTenantSlug}
              onChange={(e) => setSelectedTenantSlug(e.target.value)}
              className="bg-slate-100 rounded-lg px-3 py-2 text-xs font-mono font-bold outline-none text-slate-800"
            >
              <option value="">-- Select Hospital --</option>
              {tenants.map(t => <option key={t.subdomain_slug} value={t.subdomain_slug}>{t.hospital_name} ({t.subdomain_slug})</option>)}
            </select>
          </div>
          <div className="min-w-0 flex flex-wrap items-center gap-2 px-2">
            <span className="text-[10px] font-bold uppercase text-slate-500">DB Table</span>
            <select
              value={entityType}
              onChange={(e) => setEntityType(e.target.value)}
              disabled={availableEntities.length === 0}
              className="bg-slate-100 rounded-lg px-3 py-2 text-xs font-mono font-bold text-cyan-700 outline-none disabled:opacity-50"
            >
              {availableEntities.length === 0 && <option value="">No migration schemas loaded</option>}
              {availableEntities.map(k => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>
          <div className="min-w-0 flex flex-wrap items-center gap-2 px-2 md:border-l border-slate-200">
            <label className="text-[10px] font-bold uppercase text-slate-500" htmlFor="migration-duplicate-policy">Duplicates</label>
            <select id="migration-duplicate-policy" value={duplicatePolicy} onChange={(event) => setDuplicatePolicy(event.target.value)} className="bg-slate-100 rounded-lg px-3 py-2 text-xs font-bold text-slate-800">
              <option value="skip">Skip matching rows</option>
              <option value="reject">Reject matching rows</option>
              <option value="update">Update matching rows</option>
              <option value="allow">Allow (database constraints apply)</option>
            </select>
            {duplicatePolicy !== "allow" && (
              <select aria-label="Unique field for duplicate matching" value={duplicateField} onChange={(event) => setDuplicateField(event.target.value)} disabled={!uniqueFields.length} className="max-w-48 bg-slate-100 rounded-lg px-3 py-2 text-xs font-mono text-slate-800 disabled:opacity-50">
                {!uniqueFields.length && <option value="">No unique fields</option>}
                {uniqueFields.map((field) => <option key={field.id} value={field.id}>{field.label || field.id}</option>)}
              </select>
            )}
            {duplicatePolicy !== "allow" && !uniqueFields.length && <span className="text-[10px] text-amber-700">No unique key is available; choose Allow to use database constraints.</span>}
          </div>
        </div>
      </div>

      <div className="flex-1 theme-panel rounded-2xl overflow-hidden border relative min-h-[480px] md:min-h-[600px] flex flex-col">
        {selectedTenantSlug ? (
          <>
            <header className="flex flex-wrap items-center justify-between gap-3 px-4 sm:px-6 py-4 bg-slate-50 border-b border-slate-200 z-10 shrink-0">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-cyan-100 text-cyan-600 rounded-lg"><Settings2 size={18} /></div>
                <div>
                  <h2 className="text-sm font-black uppercase tracking-wider text-slate-800">Mapper: {selectedTenantSlug} &rarr; {entityType}</h2>
                  <p className="text-[10px] font-mono text-slate-500 mt-0.5">Connect left to right, choose operations on blocks, and delete blocks with the trash icon.</p>
                </div>
              </div>
              <div className="w-full flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2 px-4 py-2 text-xs font-bold text-slate-700 bg-white border border-slate-300 shadow-sm rounded-lg cursor-pointer hover:bg-slate-50 transition-colors">
                  <Upload size={14} />
                  {isUploading ? "Parsing..." : file ? file.name : "1. Upload CSV"}
                  <input type="file" accept=".csv" className="hidden" onChange={handleFileUpload} />
                </label>
                <button onClick={handlePreview} disabled={!file || edges.length === 0 || isUploading || isExecuting} className="flex items-center gap-2 px-4 py-2 text-xs font-bold text-slate-700 bg-white border border-slate-300 shadow-sm rounded-lg hover:bg-slate-50 transition-colors disabled:opacity-50">
                  <Eye size={14} /> 2. Preview Data
                </button>
                <button
                  onClick={handleExecute}
                  disabled={isExecuting || isUploading || !file || edges.length === 0}
                  className="flex items-center gap-2 px-5 py-2 text-xs font-black text-white bg-cyan-600 shadow-sm rounded-lg hover:bg-cyan-700 transition-colors disabled:opacity-50"
                >
                  <Play size={14} /> {isExecuting ? "Executing..." : "3. Run Migration"}
                </button>
              </div>
            </header>

            <div className="flex-grow w-full relative bg-slate-100">
              <div className="absolute left-3 top-3 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white/95 p-2 shadow-sm">
                <span className="px-1 text-[9px] font-black uppercase tracking-wider text-slate-500">Add ETL block</span>
                <button type="button" onClick={() => addTransformNode("trim", "TRANSFORM")} className="flex items-center gap-1 rounded-lg bg-violet-50 px-2.5 py-1.5 text-[10px] font-bold text-violet-800 hover:bg-violet-100">
                  <Plus size={12} /> Transform
                </button>
                <button type="button" onClick={() => addTransformNode("combine", "COMBINE")} className="flex items-center gap-1 rounded-lg bg-cyan-50 px-2.5 py-1.5 text-[10px] font-bold text-cyan-800 hover:bg-cyan-100">
                  <Merge size={12} /> Combine
                </button>
                <button type="button" onClick={() => addTransformNode("conditional", "CONDITION")} className="flex items-center gap-1 rounded-lg bg-amber-50 px-2.5 py-1.5 text-[10px] font-bold text-amber-800 hover:bg-amber-100">
                  <GitBranch size={12} /> Condition
                </button>
                <button type="button" onClick={() => addTransformNode("constant", "VALUE")} className="rounded-lg bg-slate-100 px-2.5 py-1.5 text-[10px] font-bold text-slate-700 hover:bg-slate-200">
                  Constant
                </button>
              </div>
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                nodeTypes={nodeTypes}
                fitView
              >
                <Background color="#cbd5e1" gap={20} size={2} />
                <Controls className="bg-white border-slate-200 shadow-sm" />
              </ReactFlow>
            </div>
          </>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-center p-6 bg-slate-900/40 backdrop-blur-sm z-20">
            <Database className="w-12 h-12 text-slate-300 mb-4 opacity-50" />
            <h3 className="text-lg font-black text-white shadow-sm">No Target Tenant Selected</h3>
            <p className="text-xs text-slate-300 mt-2 max-w-md">Please select a destination hospital from the top dropdown to initialize the Visual ETL canvas.</p>
          </div>
        )}
      </div>

      {/* Rules Modal */}
      {transformModalOpen && (
        <div className="absolute inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-6">
          <div className="bg-white w-full max-w-md rounded-2xl shadow-2xl p-6">
            <div className="flex justify-between items-center border-b pb-3 mb-4">
              <h3 className="font-black text-slate-800">Configure ETL Block</h3>
              <button onClick={() => setTransformModalOpen(false)}><X className="w-5 h-5 text-slate-400 hover:text-slate-700" /></button>
            </div>
            <div className="space-y-4 text-xs">
              {nodes.find((node) => node.id === activeTransformNodeId)?.type === "targetNode" ? (
                <>
                  <p className="text-slate-600">Set a value to use when the connected input is blank. Required fields can also be populated by a Constant Value ETL block.</p>
                  <label className="block font-bold text-slate-600 uppercase">
                    Fallback value
                    <input type="text" value={transformForm.fallback} onChange={(event) => setTransformForm((current) => ({ ...current, fallback: event.target.value }))} placeholder='e.g. "OTHER" or "1900-01-01"' className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" />
                  </label>
                </>
              ) : (
                <>
                  <div className="rounded-lg bg-violet-50 p-3 text-slate-700">
                    Block: <strong>{TRANSFORM_OPTIONS.find(([value]) => value === nodes.find((node) => node.id === activeTransformNodeId)?.data.operation)?.[1]}</strong>
                  </div>
                  {["constant"].includes(nodes.find((node) => node.id === activeTransformNodeId)?.data.operation) && (
                    <label className="block font-bold text-slate-600 uppercase">Constant value
                      <input value={transformForm.value} onChange={(event) => setTransformForm((current) => ({ ...current, value: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" />
                    </label>
                  )}
                  {nodes.find((node) => node.id === activeTransformNodeId)?.data.operation === "combine" && (
                    <label className="block font-bold text-slate-600 uppercase">Join separator
                      <input value={transformForm.separator} onChange={(event) => setTransformForm((current) => ({ ...current, separator: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" />
                    </label>
                  )}
                  {nodes.find((node) => node.id === activeTransformNodeId)?.data.operation === "coalesce" && <p className="rounded bg-cyan-50 p-3 text-slate-700">Connect at least two input nodes. The first non-empty value, from topmost/first connected input, is used.</p>}
                  {nodes.find((node) => node.id === activeTransformNodeId)?.data.operation === "map" && (
                    <label className="block font-bold text-slate-600 uppercase">Value map (JSON)
                      <textarea rows={5} value={transformForm.map} onChange={(event) => setTransformForm((current) => ({ ...current, map: event.target.value }))} placeholder='{"M": "MALE", "F": "FEMALE"}' className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" />
                    </label>
                  )}
                  {nodes.find((node) => node.id === activeTransformNodeId)?.data.operation === "replace" && (
                    <>
                      <label className="block font-bold text-slate-600 uppercase">Find text<input value={transformForm.oldValue} onChange={(event) => setTransformForm((current) => ({ ...current, oldValue: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" /></label>
                      <label className="block font-bold text-slate-600 uppercase">Replace with<input value={transformForm.newValue} onChange={(event) => setTransformForm((current) => ({ ...current, newValue: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" /></label>
                    </>
                  )}
                  {nodes.find((node) => node.id === activeTransformNodeId)?.data.operation === "null_if" && (
                    <label className="block font-bold text-slate-600 uppercase">Null when value equals
                      <input value={transformForm.compareValue} onChange={(event) => setTransformForm((current) => ({ ...current, compareValue: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" />
                    </label>
                  )}
                  {nodes.find((node) => node.id === activeTransformNodeId)?.data.operation === "substring" && (
                    <div className="grid grid-cols-2 gap-3">
                      <label className="font-bold text-slate-600 uppercase">Start index<input type="number" value={transformForm.start} onChange={(event) => setTransformForm((current) => ({ ...current, start: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" /></label>
                      <label className="font-bold text-slate-600 uppercase">Length (optional)<input type="number" min="0" value={transformForm.length} onChange={(event) => setTransformForm((current) => ({ ...current, length: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" /></label>
                    </div>
                  )}
                  {nodes.find((node) => node.id === activeTransformNodeId)?.data.operation === "split_part" && (
                    <div className="grid grid-cols-2 gap-3">
                      <label className="font-bold text-slate-600 uppercase">Delimiter<input value={transformForm.delimiter} onChange={(event) => setTransformForm((current) => ({ ...current, delimiter: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" /></label>
                      <label className="font-bold text-slate-600 uppercase">Part index (0-based)<input type="number" value={transformForm.part_index} onChange={(event) => setTransformForm((current) => ({ ...current, part_index: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" /></label>
                    </div>
                  )}
                  {["add", "subtract", "multiply", "divide"].includes(nodes.find((node) => node.id === activeTransformNodeId)?.data.operation) && (
                    <label className="block font-bold text-slate-600 uppercase">Number to {nodes.find((node) => node.id === activeTransformNodeId)?.data.operation}
                      <input type="number" value={transformForm.operand} onChange={(event) => setTransformForm((current) => ({ ...current, operand: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" />
                    </label>
                  )}
                  {nodes.find((node) => node.id === activeTransformNodeId)?.data.operation === "parse_date" && (
                    <div className="grid grid-cols-2 gap-3">
                      <label className="font-bold text-slate-600 uppercase">Input format<input value={transformForm.input_format} onChange={(event) => setTransformForm((current) => ({ ...current, input_format: event.target.value }))} placeholder="%d/%m/%Y" className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" /></label>
                      <label className="font-bold text-slate-600 uppercase">Output format<input value={transformForm.output_format} onChange={(event) => setTransformForm((current) => ({ ...current, output_format: event.target.value }))} placeholder="%Y-%m-%d" className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" /></label>
                    </div>
                  )}
                  {nodes.find((node) => node.id === activeTransformNodeId)?.data.operation === "conditional" && (
                    <>
                      <label className="block font-bold text-slate-600 uppercase">Condition
                        <select value={transformForm.operator} onChange={(event) => setTransformForm((current) => ({ ...current, operator: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-slate-800">
                          <option value="equals">Equals</option><option value="not_equals">Does not equal</option><option value="contains">Contains</option><option value="is_empty">Is empty</option><option value="not_empty">Is not empty</option>
                        </select>
                      </label>
                      {!["is_empty", "not_empty"].includes(transformForm.operator) && <label className="block font-bold text-slate-600 uppercase">Compare to<input value={transformForm.compareValue} onChange={(event) => setTransformForm((current) => ({ ...current, compareValue: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" /></label>}
                      <label className="block font-bold text-slate-600 uppercase">If true<input value={transformForm.whenTrue} onChange={(event) => setTransformForm((current) => ({ ...current, whenTrue: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" /></label>
                      <label className="block font-bold text-slate-600 uppercase">If false<input value={transformForm.whenFalse} onChange={(event) => setTransformForm((current) => ({ ...current, whenFalse: event.target.value }))} className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-slate-800" /></label>
                    </>
                  )}
                </>
              )}
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <button onClick={() => setTransformModalOpen(false)} className="px-4 py-2 font-bold text-slate-500 hover:bg-slate-100 rounded-lg">Cancel</button>
              <button onClick={handleSaveTransformRules} className="px-4 py-2 bg-emerald-500 text-white font-bold rounded-lg hover:bg-emerald-600">Save Rules</button>
            </div>
          </div>
        </div>
      )}

      {/* Data Preview Modal */}
      {previewModalOpen && previewData && (
        <div className="absolute inset-0 z-50 bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-6">
          <div className="bg-white w-full max-w-5xl rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh]">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-slate-50 shrink-0">
              <div className="flex items-center gap-3">
                <TableIcon className="w-5 h-5 text-cyan-500" />
                <div>
                  <h3 className="font-black text-sm uppercase text-slate-800">Migration Data Preview ({entityType})</h3>
                  <p className="text-[10px] font-mono text-slate-500">First {previewData.previews?.length} transformed rows</p>
                  <p className="text-[10px] font-semibold text-amber-700">{previewData.duplicate_count ?? 0} duplicate row(s) detected by {previewData.duplicate_field || "database constraints"}; policy: {previewData.duplicate_policy}</p>
                </div>
              </div>
              <button onClick={() => setPreviewModalOpen(false)} className="p-2 hover:bg-slate-200 rounded-lg"><X className="w-5 h-5 text-slate-500" /></button>
            </div>

            <div className="flex-1 overflow-auto p-6 bg-white">
              {previewData.errors?.length > 0 && (
                <div className="mb-4 p-4 bg-rose-50 border-l-4 border-rose-500 rounded-r-lg">
                  <h4 className="text-xs font-bold text-rose-700 uppercase mb-2">Data Integrity Warnings</h4>
                  <ul className="text-[11px] font-mono text-rose-600 space-y-1">
                    {previewData.errors.map((e, idx) => (<li key={idx}>Row {e.row}: {e.error}</li>))}
                  </ul>
                </div>
              )}

              <table className="w-full text-left border-collapse whitespace-nowrap">
                <thead>
                  <tr className="border-b-2 border-slate-800 text-[10px] uppercase text-slate-500">
                    <th className="py-3 px-4 font-black">Row</th>
                    <th className="py-3 px-4 font-black">Duplicate</th>
                    {previewData.previews?.[0] && Object.keys(previewData.previews[0].data).map(k => (
                       <th key={k} className="py-3 px-4 font-black">{k}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="text-xs font-mono text-slate-700">
                  {previewData.previews?.map((p) => (
                    <tr key={p.row} className="border-b border-slate-100 hover:bg-slate-50">
                      <td className="py-3 px-4 font-bold text-slate-400">#{p.row}</td>
                      <td className={`py-3 px-4 font-bold ${p.duplicate ? "text-amber-700" : "text-emerald-700"}`}>{p.duplicate ? "YES" : "NO"}</td>
                      {Object.entries(p.data).map(([k, v]) => (
                        <td key={k} className="py-3 px-4">
                          {typeof v === 'boolean' ? (v ? "TRUE" : "FALSE") : v || <span className="text-slate-300">null</span>}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="px-6 py-4 border-t bg-slate-50 flex justify-end shrink-0">
              <button onClick={() => setPreviewModalOpen(false)} className="px-5 py-2.5 bg-slate-800 text-white text-xs font-bold rounded-xl hover:bg-slate-700 transition-colors">Close Preview</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}