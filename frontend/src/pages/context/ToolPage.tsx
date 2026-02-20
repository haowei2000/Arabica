import { useState, useMemo, useCallback, useRef } from 'react';
import {
  Wrench, Loader2, Trash2, ToggleLeft, ToggleRight, Pencil, Cpu,
  MoveRight, Search, Copy, Play, X, Plus, ChevronRight, ChevronDown,
  ArrowRight, Check, CircleDot, Circle, Zap, ArrowLeft, Settings2,
  Link2, Type, Hash, ToggleLeft as ToggleIcon, List, Box, Braces,
  Download, Upload, Code2, FormInput, AlertCircle,
} from 'lucide-react';
import {
  useToolList, useCreateTool, useUpdateTool, useDeleteTool,
  useToggleTool, useInnerTools, useTestTool, useExportTool, useImportTool,
} from '@/hooks/useTools';
import { toolService } from '@/services/toolService';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { ViewToggle, type ViewMode } from '@/components/ViewToggle';
import { AccordionItem } from '@/components/AccordionItem';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/formatDate';
import type { UserTool, UserToolCreate, InnerToolInfo, ToolExportData } from '@/types/tool';

// ─── Types ───────────────────────────────────────────────────────────────────

interface SchemaParam {
  name: string;
  type: string;
  description: string;
  required: boolean;
  defaultValue: string;
}

type WizardStep = 'info' | 'schema' | 'mapping';
type EditMode = 'form' | 'json';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function getSchemaProperties(schema: Record<string, unknown> | undefined) {
  const s = schema as {
    properties?: Record<string, { type?: string; description?: string }>;
    required?: string[];
  } | undefined;
  return {
    props: s?.properties ?? {},
    required: new Set(s?.required ?? []),
  };
}

function paramsToSchema(params: SchemaParam[]): Record<string, unknown> {
  const properties: Record<string, unknown> = {};
  const required: string[] = [];
  for (const p of params) {
    if (!p.name.trim()) continue;
    const prop: Record<string, unknown> = { type: p.type };
    if (p.description) prop.description = p.description;
    if (p.defaultValue) {
      try { prop.default = JSON.parse(p.defaultValue); }
      catch { prop.default = p.defaultValue; }
    }
    properties[p.name] = prop;
    if (p.required) required.push(p.name);
  }
  return { type: 'object', properties, required };
}

function schemaToParams(schema: Record<string, unknown> | undefined): SchemaParam[] {
  const { props, required } = getSchemaProperties(schema);
  return Object.entries(props).map(([name, prop]) => ({
    name,
    type: prop.type ?? 'string',
    description: prop.description ?? '',
    required: required.has(name),
    defaultValue: '',
  }));
}

const PARAM_TYPES = ['string', 'integer', 'number', 'boolean', 'array', 'object'];

const PARAM_TYPE_ICON: Record<string, typeof Type> = {
  string: Type,
  integer: Hash,
  number: Hash,
  boolean: ToggleIcon,
  array: List,
  object: Braces,
};

const MODE_COLOR: Record<string, string> = {
  http:       'bg-blue-100 text-blue-700 border-blue-200/70 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-800/50',
  server_run: 'bg-purple-100 text-purple-700 border-purple-200/70 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-800/50',
  inner:      'bg-muted text-muted-foreground border-border/50',
  chain:      'bg-orange-100 text-orange-700 border-orange-200/70 dark:bg-orange-900/30 dark:text-orange-300 dark:border-orange-800/50',
  container:  'bg-red-100 text-red-700 border-red-200/70 dark:bg-red-900/30 dark:text-red-300 dark:border-red-800/50',
  client:     'bg-teal-100 text-teal-700 border-teal-200/70 dark:bg-teal-900/30 dark:text-teal-300 dark:border-teal-800/50',
};

const WIZARD_STEPS: { key: WizardStep; label: string; description: string }[] = [
  { key: 'info', label: 'Basic Info', description: 'Name & description' },
  { key: 'schema', label: 'Parameters', description: 'Define input schema' },
  { key: 'mapping', label: 'Mapping', description: 'Connect to tool' },
];

// ─── Component ───────────────────────────────────────────────────────────────

export default function ToolPage() {
  // ── Refs ──
  const importFileRef = useRef<HTMLInputElement>(null);
  const fillTextareaRefs = useRef<Record<string, HTMLTextAreaElement>>({});

  // ── List state ──
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [viewMode, setViewMode] = useState<ViewMode>('card');
  const [openItemId, setOpenItemId] = useState<string | null>(null);

  // ── Create / Edit state ──
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingTool, setEditingTool] = useState<UserTool | null>(null);
  const [wizardStep, setWizardStep] = useState<WizardStep>('info');
  const [basicInfo, setBasicInfo] = useState({ name: '', display_name: '', description: '', category: 'custom', timeout: 30 });
  const [inputParams, setInputParams] = useState<SchemaParam[]>([]);
  const [selectedSuccessor, setSelectedSuccessor] = useState<InnerToolInfo | null>(null);
  const [paramFills, setParamFills] = useState<Record<string, string>>({});

  // ── Edit mode (form / json) ──
  const [editMode, setEditMode] = useState<EditMode>('form');
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);

  // ── Successor picker state ──
  const [successorSearch, setSuccessorSearch] = useState('');
  const [successorCategory, setSuccessorCategory] = useState('all');

  // ── Test dialog state ──
  const [testingTool, setTestingTool] = useState<UserTool | null>(null);
  const [testParamsJson, setTestParamsJson] = useState('{}');

  // ── Queries & mutations ──
  const { data: toolData, isLoading: toolsLoading } = useToolList({
    enabled_only: false,
    tags: selectedTags.length > 0 ? selectedTags.join(',') : undefined,
  });
  const { data: innerToolData } = useInnerTools();
  const createMutation = useCreateTool();
  const updateMutation = useUpdateTool();
  const deleteMutation = useDeleteTool();
  const toggleMutation = useToggleTool();
  const testMutation = useTestTool();
  const exportMutation = useExportTool();
  const importMutation = useImportTool();

  // ── Derived data ──
  const allInnerTools = innerToolData?.inner_tools ?? [];

  const innerToolCategories = useMemo(() => {
    const cats = new Set(allInnerTools.map(t => t.category));
    return ['all', ...Array.from(cats).sort()];
  }, [allInnerTools]);

  const filteredSuccessors = useMemo(() => {
    let list = allInnerTools;
    if (successorCategory !== 'all') list = list.filter(t => t.category === successorCategory);
    if (successorSearch.trim()) {
      const q = successorSearch.toLowerCase();
      list = list.filter(t =>
        t.name.toLowerCase().includes(q) ||
        t.display_name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q)
      );
    }
    return list;
  }, [allInnerTools, successorCategory, successorSearch]);

  const successorProps = useMemo(() => {
    if (!selectedSuccessor) return { props: {}, required: new Set<string>() };
    return getSchemaProperties(selectedSuccessor.input_schema);
  }, [selectedSuccessor]);

  const inputParamNames = useMemo(() => inputParams.filter(p => p.name.trim()).map(p => p.name), [inputParams]);

  // ── Wizard validation ──
  const isStepValid = useCallback((step: WizardStep): boolean => {
    switch (step) {
      case 'info':
        return !!(basicInfo.name.trim() && basicInfo.display_name.trim() && basicInfo.description.trim());
      case 'schema':
        return true; // params are optional
      case 'mapping':
        return !!selectedSuccessor;
      default:
        return false;
    }
  }, [basicInfo, selectedSuccessor]);

  const stepIndex = WIZARD_STEPS.findIndex(s => s.key === wizardStep);

  // ── Form ↔ JSON sync ──

  const formToJsonObj = useCallback(() => ({
    name: basicInfo.name,
    display_name: basicInfo.display_name,
    description: basicInfo.description,
    execution_mode: 'inner',
    category: basicInfo.category,
    timeout: basicInfo.timeout,
    enabled: true,
    is_public: false,
    input_schema: paramsToSchema(inputParams),
    inner_tool_name: selectedSuccessor?.name ?? null,
    parameter_mapping: selectedSuccessor ? { ...paramFills } : {},
  }), [basicInfo, inputParams, selectedSuccessor, paramFills]);

  const applyJsonObj = useCallback((obj: Record<string, unknown>) => {
    setBasicInfo({
      name: (obj.name as string) ?? '',
      display_name: (obj.display_name as string) ?? '',
      description: (obj.description as string) ?? '',
      category: (obj.category as string) ?? 'custom',
      timeout: (obj.timeout as number) ?? 30,
    });
    setInputParams(schemaToParams(obj.input_schema as Record<string, unknown> | undefined));
    const innerName = (obj.inner_tool_name as string | null) ?? null;
    if (innerName) {
      const successor = allInnerTools.find(t => t.name === innerName) ?? null;
      setSelectedSuccessor(successor);
    } else {
      setSelectedSuccessor(null);
    }
    setParamFills((obj.parameter_mapping as Record<string, string>) ?? {});
  }, [allInnerTools]);

  const switchToJson = useCallback(() => {
    setJsonText(JSON.stringify(formToJsonObj(), null, 2));
    setJsonError(null);
    setEditMode('json');
  }, [formToJsonObj]);

  const switchToForm = useCallback(() => {
    try {
      const obj = JSON.parse(jsonText) as Record<string, unknown>;
      applyJsonObj(obj);
      setJsonError(null);
      setEditMode('form');
    } catch (e) {
      setJsonError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [jsonText, applyJsonObj]);

  // ── Handlers ──

  const resetForm = useCallback(() => {
    setBasicInfo({ name: '', display_name: '', description: '', category: 'custom', timeout: 30 });
    setInputParams([]);
    setSelectedSuccessor(null);
    setParamFills({});
    setSuccessorSearch('');
    setSuccessorCategory('all');
    setWizardStep('info');
    setEditMode('form');
    setJsonText('');
    setJsonError(null);
  }, []);

  const openCreateModal = () => { resetForm(); setShowCreateModal(true); };

  const selectSuccessor = (tool: InnerToolInfo) => {
    setSelectedSuccessor(tool);
    const { props } = getSchemaProperties(tool.input_schema);
    const names = new Set(inputParams.filter(p => p.name.trim()).map(p => p.name));
    const fills: Record<string, string> = {};
    for (const key of Object.keys(props)) {
      fills[key] = names.has(key) ? `{{${key}}}` : '';
    }
    setParamFills(fills);
  };

  const handleCreate = async () => {
    // If in JSON mode, parse and apply the JSON first
    let effectiveName = basicInfo.name;
    let effectiveDisplayName = basicInfo.display_name;
    let effectiveDescription = basicInfo.description;
    let effectiveCategory = basicInfo.category;
    let effectiveTimeout = basicInfo.timeout;
    let effectiveInputSchema = paramsToSchema(inputParams);
    let effectiveSuccessorName = selectedSuccessor?.name ?? null;
    let effectiveParamFills = { ...paramFills };

    if (editMode === 'json') {
      let obj: Record<string, unknown>;
      try { obj = JSON.parse(jsonText) as Record<string, unknown>; }
      catch (e) { setJsonError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`); return; }
      effectiveName = (obj.name as string) ?? '';
      effectiveDisplayName = (obj.display_name as string) ?? '';
      effectiveDescription = (obj.description as string) ?? '';
      effectiveCategory = (obj.category as string) ?? 'custom';
      effectiveTimeout = (obj.timeout as number) ?? 30;
      effectiveInputSchema = (obj.input_schema as Record<string, unknown>) ?? {};
      effectiveSuccessorName = (obj.inner_tool_name as string | null) ?? null;
      effectiveParamFills = (obj.parameter_mapping as Record<string, string>) ?? {};
    }

    if (!effectiveSuccessorName) { alert('Please select a successor tool'); return; }

    const payload: UserToolCreate = {
      name: effectiveName,
      display_name: effectiveDisplayName,
      description: effectiveDescription,
      execution_mode: 'inner',
      input_schema: effectiveInputSchema,
      category: effectiveCategory,
      timeout: effectiveTimeout,
      enabled: true,
      is_public: false,
      inner_tool_name: effectiveSuccessorName,
      parameter_mapping: effectiveParamFills,
    };
    try {
      await createMutation.mutateAsync(payload);
      setShowCreateModal(false);
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const openEditModal = (tool: UserTool) => {
    setEditingTool(tool);
    setBasicInfo({
      name: tool.name,
      display_name: tool.display_name,
      description: tool.description,
      category: tool.category ?? 'custom',
      timeout: tool.timeout ?? 30,
    });
    setInputParams(schemaToParams(tool.input_schema));
    const successor = allInnerTools.find(t => t.name === tool.inner_tool_name) ?? null;
    setSelectedSuccessor(successor);
    setParamFills(tool.parameter_mapping ? { ...tool.parameter_mapping } : {});
    setWizardStep('info');
    setEditMode('form');
    setJsonText('');
    setJsonError(null);
  };

  const handleUpdate = async () => {
    if (!editingTool) return;

    let effectiveDisplayName = basicInfo.display_name;
    let effectiveDescription = basicInfo.description;
    let effectiveCategory = basicInfo.category;
    let effectiveTimeout = basicInfo.timeout;
    let effectiveInputSchema = paramsToSchema(inputParams);
    let effectiveSuccessorName = selectedSuccessor?.name ?? null;
    let effectiveParamFills = { ...paramFills };

    if (editMode === 'json') {
      let obj: Record<string, unknown>;
      try { obj = JSON.parse(jsonText) as Record<string, unknown>; }
      catch (e) { setJsonError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`); return; }
      effectiveDisplayName = (obj.display_name as string) ?? '';
      effectiveDescription = (obj.description as string) ?? '';
      effectiveCategory = (obj.category as string) ?? 'custom';
      effectiveTimeout = (obj.timeout as number) ?? 30;
      effectiveInputSchema = (obj.input_schema as Record<string, unknown>) ?? {};
      effectiveSuccessorName = (obj.inner_tool_name as string | null) ?? null;
      effectiveParamFills = (obj.parameter_mapping as Record<string, string>) ?? {};
    }

    const payload: Record<string, unknown> = {
      display_name: effectiveDisplayName,
      description: effectiveDescription,
      category: effectiveCategory,
      timeout: effectiveTimeout,
      input_schema: effectiveInputSchema,
    };
    if (effectiveSuccessorName) {
      payload.inner_tool_name = effectiveSuccessorName;
      payload.parameter_mapping = effectiveParamFills;
    }
    try {
      await updateMutation.mutateAsync({ id: editingTool.id, data: payload });
      setEditingTool(null);
    } catch (error) {
      alert(`Update failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleUseAsTemplate = async (tool: UserTool) => {
    try {
      const tpl = await toolService.getToolAsTemplate(tool.id);
      const tplData = tpl.template as Record<string, unknown>;
      resetForm();
      setBasicInfo({
        name: '', display_name: '',
        description: (tplData.description as string) ?? '',
        category: (tplData.category as string) ?? 'custom',
        timeout: (tplData.timeout as number) ?? 30,
      });
      setInputParams(schemaToParams(tplData.input_schema as Record<string, unknown>));
      const innerName = (tplData.inner_tool_name as string) ?? null;
      if (innerName) {
        const successor = allInnerTools.find(t => t.name === innerName) ?? null;
        setSelectedSuccessor(successor);
        setParamFills((tplData.parameter_mapping as Record<string, string>) ?? {});
      }
      setShowCreateModal(true);
    } catch { alert('Failed to load tool template'); }
  };

  const handleDelete = async (id: string, name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete "${name}"?`)) return;
    try { await deleteMutation.mutateAsync(id); }
    catch (error) { alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };

  const handleToggle = async (id: string, currentEnabled: boolean, e: React.MouseEvent) => {
    e.stopPropagation();
    try { await toggleMutation.mutateAsync({ id, enabled: !currentEnabled }); }
    catch (error) { alert(`Toggle failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };

  const openTestDialog = (tool: UserTool) => {
    setTestingTool(tool);
    const schema = tool.input_schema as { properties?: Record<string, { type?: string; default?: unknown }> };
    const params: Record<string, unknown> = {};
    if (schema?.properties) {
      for (const [key, prop] of Object.entries(schema.properties)) {
        if (prop.default !== undefined) params[key] = prop.default;
        else if (prop.type === 'string') params[key] = '';
        else if (prop.type === 'integer' || prop.type === 'number') params[key] = 0;
        else if (prop.type === 'boolean') params[key] = false;
        else if (prop.type === 'array') params[key] = [];
        else if (prop.type === 'object') params[key] = {};
      }
    }
    setTestParamsJson(JSON.stringify(params, null, 2));
    testMutation.reset();
  };

  const handleRunTest = () => {
    if (!testingTool) return;
    let params: Record<string, unknown>;
    try { params = JSON.parse(testParamsJson); }
    catch { alert('Parameters is not valid JSON'); return; }
    testMutation.mutate({ id: testingTool.id, parameters: params });
  };

  // ── Export / Import ──

  const handleExportTool = async (tool: UserTool, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      const exportData = await exportMutation.mutateAsync(tool.id);
      const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${tool.name}.tool.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (error) {
      alert(`Export failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (ev) => {
      try {
        const data = JSON.parse(ev.target?.result as string) as ToolExportData;
        await importMutation.mutateAsync(data);
      } catch (error) {
        alert(`Import failed: ${error instanceof Error ? error.message : 'Invalid JSON file'}`);
      }
    };
    reader.readAsText(file);
    // Reset so the same file can be re-imported
    e.target.value = '';
  };

  // ── Loading ──
  if (toolsLoading) {
    return <div className="flex items-center justify-center py-16"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>;
  }

  const tools = toolData?.tools ?? [];
  const isInner = (t: UserTool) => t.tool_type === 'inner';
  const allTags = Array.from(new Set(tools.flatMap(t => t.tags ?? []))).sort();
  const isEditing = !!editingTool;
  const dialogOpen = showCreateModal || isEditing;

  // ═══════════════════════════════════════════════════════════════════════════
  // ─── Wizard Step Contents ─────────────────────────────────────────────────
  // ═══════════════════════════════════════════════════════════════════════════

  const renderStepInfo = () => (
    <div className="space-y-5 animate-fade-in">
      {/* Hero header */}
      <div className="flex items-center gap-2.5 pb-4 border-b border-border/50">
        <div className="size-8 rounded-lg bg-muted flex items-center justify-center dark:bg-primary-900/40">
          <Settings2 className="size-4 text-muted-foreground dark:text-primary-400" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-foreground">Tool Identity</h3>
          <p className="text-[11px] text-muted-foreground">Define your tool's basic information</p>
        </div>
      </div>

      {/* Name row */}
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label className="text-xs font-medium text-foreground/80">
            Identifier <span className="text-destructive">*</span>
          </Label>
          <Input
            value={basicInfo.name}
            onChange={(e) => setBasicInfo({ ...basicInfo, name: e.target.value })}
            placeholder="weather_lookup"
            className="font-mono text-sm h-9 bg-muted/30 border-border/60 focus:bg-background transition-colors"
            required
            readOnly={isEditing}
            disabled={isEditing}
          />
          <p className="text-[10px] text-muted-foreground/70">Unique machine-readable name (snake_case)</p>
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs font-medium text-foreground/80">
            Display Name <span className="text-destructive">*</span>
          </Label>
          <Input
            value={basicInfo.display_name}
            onChange={(e) => setBasicInfo({ ...basicInfo, display_name: e.target.value })}
            placeholder="Weather Lookup"
            className="text-sm h-9 bg-muted/30 border-border/60 focus:bg-background transition-colors"
            required
          />
          <p className="text-[10px] text-muted-foreground/70">Human-friendly label shown in UI</p>
        </div>
      </div>

      {/* Description */}
      <div className="space-y-1.5">
        <Label className="text-xs font-medium text-foreground/80">
          Description <span className="text-destructive">*</span>
        </Label>
        <Textarea
          value={basicInfo.description}
          onChange={(e) => setBasicInfo({ ...basicInfo, description: e.target.value })}
          placeholder="Describe what this tool does and when an agent should use it..."
          rows={3}
          className="text-sm bg-muted/30 border-border/60 focus:bg-background transition-colors resize-none"
          required
        />
      </div>

      {/* Category + Timeout */}
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label className="text-xs font-medium text-foreground/80">Category</Label>
          <Input
            value={basicInfo.category}
            onChange={(e) => setBasicInfo({ ...basicInfo, category: e.target.value })}
            className="text-sm h-9 bg-muted/30 border-border/60 focus:bg-background transition-colors"
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs font-medium text-foreground/80">Timeout (seconds)</Label>
          <Input
            type="number"
            value={basicInfo.timeout}
            onChange={(e) => setBasicInfo({ ...basicInfo, timeout: parseInt(e.target.value) || 30 })}
            min={1} max={3600}
            className="text-sm h-9 bg-muted/30 border-border/60 focus:bg-background transition-colors tabular-nums"
          />
        </div>
      </div>
    </div>
  );

  const renderStepSchema = () => (
    <div className="space-y-5 animate-fade-in">
      {/* Hero header */}
      <div className="flex items-start gap-2.5 pb-4 border-b border-border/50">
        <div className="size-8 rounded-lg bg-muted flex items-center justify-center shrink-0 dark:bg-secondary-900/40">
          <Box className="size-4 text-muted-foreground dark:text-secondary-400" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-foreground">Input Parameters</h3>
          <p className="text-[11px] text-muted-foreground">Define what your tool accepts as input</p>
          {inputParamNames.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {inputParamNames.map(name => {
                const param = inputParams.find(p => p.name === name)!;
                const Icon = PARAM_TYPE_ICON[param.type] || Type;
                return (
                  <span key={name} className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-muted border border-border/60 text-muted-foreground font-mono">
                    <Icon className="size-2.5" />
                    {name}
                  </span>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Param rows */}
      {inputParams.length === 0 ? (
        <button
          type="button"
          onClick={() => setInputParams([{ name: '', type: 'string', description: '', required: false, defaultValue: '' }])}
          className="w-full group flex flex-col items-center gap-2.5 py-8 border-2 border-dashed border-border/60 hover:border-border hover:bg-muted/30 rounded-xl transition-all duration-200"
        >
          <div className="size-10 rounded-full bg-muted/60 group-hover:bg-muted flex items-center justify-center transition-colors">
            <Plus className="size-4 text-muted-foreground transition-colors" />
          </div>
          <div className="text-center">
            <p className="text-xs font-medium text-foreground/80">Add Input Parameter</p>
            <p className="text-[10px] text-muted-foreground mt-0.5">Define the parameters your tool will accept</p>
          </div>
        </button>
      ) : (
        <div className="space-y-2">
          {inputParams.map((param, idx) => {
            const Icon = PARAM_TYPE_ICON[param.type] || Type;
            return (
              <div
                key={idx}
                className="group relative rounded-lg border border-border/60 bg-card/50 hover:bg-card hover:border-border transition-all duration-200 overflow-hidden"
              >
                {/* Left accent bar */}
                <div className={cn(
                  'absolute left-0 top-0 bottom-0 w-0.5 transition-colors',
                  param.name.trim() ? 'bg-border' : 'bg-border/40'
                )} />

                <div className="pl-3.5 pr-2 py-2.5">
                  <div className="grid grid-cols-[1fr_100px] gap-2.5 items-start">
                    {/* Top row: name + type */}
                    <div className="flex items-center gap-2">
                      <div className="size-6 rounded bg-muted/60 flex items-center justify-center shrink-0">
                        <Icon className="size-3 text-muted-foreground" />
                      </div>
                      <Input
                        value={param.name}
                        onChange={(e) => { const next = [...inputParams]; next[idx] = { ...param, name: e.target.value }; setInputParams(next); }}
                        placeholder="param_name"
                        className="h-7 text-xs font-mono flex-1 bg-transparent border-transparent hover:border-border focus:border-border focus:bg-background transition-all"
                      />
                    </div>
                    <Select
                      value={param.type}
                      onValueChange={(v) => { const next = [...inputParams]; next[idx] = { ...param, type: v }; setInputParams(next); }}
                    >
                      <SelectTrigger className="h-7 text-[11px] bg-transparent border-transparent hover:border-border">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {PARAM_TYPES.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Description row */}
                  <div className="mt-1.5 ml-8">
                    <Input
                      value={param.description}
                      onChange={(e) => { const next = [...inputParams]; next[idx] = { ...param, description: e.target.value }; setInputParams(next); }}
                      placeholder="Brief description..."
                      className="h-6 text-[11px] text-muted-foreground bg-transparent border-transparent hover:border-border focus:border-border focus:bg-background focus:text-foreground transition-all"
                    />
                  </div>

                  {/* Bottom row: controls */}
                  <div className="flex items-center gap-3 mt-1.5 ml-8">
                    <label className="flex items-center gap-1.5 cursor-pointer group/check">
                      <input
                        type="checkbox"
                        checked={param.required}
                        onChange={(e) => { const next = [...inputParams]; next[idx] = { ...param, required: e.target.checked }; setInputParams(next); }}
                        className="size-3 rounded border-border accent-primary"
                      />
                      <span className={cn('text-[10px] transition-colors', param.required ? 'text-foreground/80 font-medium' : 'text-muted-foreground')}>
                        Required
                      </span>
                    </label>
                    <button
                      type="button"
                      onClick={() => setInputParams(inputParams.filter((_, i) => i !== idx))}
                      className="ml-auto size-5 flex items-center justify-center rounded opacity-0 group-hover:opacity-100 hover:bg-destructive/10 transition-all"
                    >
                      <X className="size-3 text-muted-foreground hover:text-destructive" />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}

          {/* Add more button */}
          <button
            type="button"
            onClick={() => setInputParams([...inputParams, { name: '', type: 'string', description: '', required: false, defaultValue: '' }])}
            className="w-full flex items-center justify-center gap-1.5 py-2 border border-dashed border-border/60 hover:border-border hover:bg-muted/30 rounded-lg text-xs text-muted-foreground hover:text-foreground transition-all"
          >
            <Plus className="size-3" /> Add Parameter
          </button>
        </div>
      )}
    </div>
  );

  const renderStepMapping = () => {
    const { props: sProps, required: sRequired } = successorProps;
    const successorEntries = Object.entries(sProps);

    return (
      <div className="space-y-5 animate-fade-in">
        {/* Successor picker */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="size-6 rounded bg-muted flex items-center justify-center">
                <Link2 className="size-3 text-muted-foreground" />
              </div>
              <div>
                <h4 className="text-xs font-bold text-foreground">Successor Tool</h4>
                <p className="text-[10px] text-muted-foreground">Select the built-in tool to delegate to</p>
              </div>
            </div>
            {selectedSuccessor && (
              <button
                type="button"
                onClick={() => { setSelectedSuccessor(null); setParamFills({}); }}
                className="text-[10px] px-2 py-0.5 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/5 transition-colors"
              >
                Change
              </button>
            )}
          </div>

          {selectedSuccessor ? (
            <div className="flex items-center gap-3 rounded-xl border border-border/60 p-3.5 bg-muted/20">
              <div className="size-9 rounded-lg bg-muted flex items-center justify-center shrink-0 dark:bg-primary-900/40">
                <Cpu className="size-4 text-muted-foreground dark:text-primary-300" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold leading-snug">{selectedSuccessor.display_name}</p>
                <p className="text-[11px] text-muted-foreground truncate mt-0.5">{selectedSuccessor.description}</p>
                <div className="flex items-center gap-1.5 mt-1.5">
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-mono">{selectedSuccessor.name}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground border border-border/50">{selectedSuccessor.category}</span>
                  {successorEntries.length > 0 && (
                    <span className="text-[10px] text-muted-foreground">{successorEntries.length} params</span>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-3 rounded-xl border border-border/60 p-3.5 bg-muted/10">
              {/* Search + Category */}
              <div className="flex items-center gap-2">
                <div className="relative flex-1">
                  <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3 text-muted-foreground" />
                  <Input
                    value={successorSearch}
                    onChange={(e) => setSuccessorSearch(e.target.value)}
                    className="pl-7 h-8 text-xs bg-background"
                    placeholder="Search tools..."
                  />
                </div>
              </div>

              {/* Category pills */}
              <div className="flex flex-wrap gap-1">
                {innerToolCategories.map(cat => (
                  <button
                    key={cat}
                    type="button"
                    onClick={() => setSuccessorCategory(cat)}
                    className={cn(
                      'text-[10px] px-2.5 py-1 rounded-full font-medium transition-all duration-200',
                      successorCategory === cat
                        ? 'bg-primary text-primary-foreground shadow-sm shadow-primary/20'
                        : 'bg-muted/60 text-muted-foreground hover:bg-muted hover:text-foreground'
                    )}
                  >
                    {cat === 'all' ? 'All' : cat}
                  </button>
                ))}
              </div>

              {/* Tool grid */}
              <div className="grid grid-cols-2 gap-2 max-h-52 overflow-y-auto scrollbar-thin pr-1">
                {filteredSuccessors.map(tool => {
                  const propCount = Object.keys((tool.input_schema as { properties?: Record<string, unknown> })?.properties ?? {}).length;
                  return (
                    <button
                      key={tool.name}
                      type="button"
                      onClick={() => selectSuccessor(tool)}
                      className="group text-left rounded-lg border border-border/60 p-3 hover:border-border hover:bg-muted/40 transition-all duration-200"
                    >
                      <div className="flex items-center gap-1.5 mb-1.5">
                        <Cpu className="size-3 text-muted-foreground shrink-0" />
                        <span className="text-[11px] font-semibold truncate group-hover:text-foreground transition-colors">
                          {tool.display_name}
                        </span>
                      </div>
                      <p className="text-[10px] text-muted-foreground line-clamp-2 leading-relaxed">{tool.description}</p>
                      <div className="flex items-center gap-1.5 mt-2">
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-muted/60 text-muted-foreground">{tool.category}</span>
                        {propCount > 0 && <span className="text-[9px] text-muted-foreground/70">{propCount} params</span>}
                      </div>
                    </button>
                  );
                })}
                {filteredSuccessors.length === 0 && (
                  <p className="col-span-2 text-center text-xs text-muted-foreground py-6">No tools found</p>
                )}
              </div>
            </div>
          )}
        </div>

        {/* ── Parameter Mapping Flow ── */}
        {selectedSuccessor && successorEntries.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="size-6 rounded bg-muted flex items-center justify-center">
                <Zap className="size-3 text-muted-foreground" />
              </div>
              <div>
                <h4 className="text-xs font-bold text-foreground">Parameter Mapping</h4>
                <p className="text-[10px] text-muted-foreground">
                  Connect your input to <code className="font-mono bg-muted px-1 rounded">{selectedSuccessor.name}</code> parameters
                </p>
              </div>
            </div>

            {/* Visual mapping container */}
            <div className="rounded-xl border border-border/60 overflow-hidden">
              {/* Column headers */}
              <div className="grid grid-cols-[1fr_40px_1fr] bg-muted/40 border-b border-border/50 px-4 py-2">
                <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Target Param</span>
                <span />
                <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Value / Expression</span>
              </div>

              {/* Mapping rows */}
              {successorEntries.map(([paramName, paramMeta], idx) => {
                const isReq = sRequired.has(paramName);
                const fillValue = paramFills[paramName] ?? '';
                const hasVarRef = fillValue.includes('{{');
                const isFilled = fillValue.trim().length > 0;

                return (
                  <div
                    key={paramName}
                    className={cn(
                      'grid grid-cols-[1fr_40px_1fr] items-start px-4 py-3 bg-background transition-colors',
                      idx < successorEntries.length - 1 && 'border-b border-border/30',
                      hasVarRef && 'bg-muted/20'
                    )}
                  >
                    {/* Left: target param */}
                    <div className="min-w-0 pt-0.5">
                      <div className="flex items-center gap-1.5">
                        <span className={cn(
                          'size-1.5 rounded-full shrink-0',
                          isFilled ? 'bg-green-500' : isReq ? 'bg-destructive' : 'bg-border'
                        )} />
                        <code className="text-xs font-mono font-semibold text-foreground truncate">{paramName}</code>
                        {isReq && (
                          <span className="text-[8px] px-1 py-px rounded bg-destructive/10 text-destructive font-bold shrink-0 uppercase tracking-wide">req</span>
                        )}
                      </div>
                      <div className="ml-3 mt-0.5">
                        {paramMeta.type && (
                          <span className="text-[10px] text-muted-foreground/70 font-mono">{paramMeta.type}</span>
                        )}
                        {paramMeta.description && (
                          <p className="text-[10px] text-muted-foreground leading-snug mt-0.5 line-clamp-2">{paramMeta.description}</p>
                        )}
                      </div>
                    </div>

                    {/* Arrow */}
                    <div className="flex items-center justify-center pt-1">
                      <div className={cn(
                        'size-6 rounded-full flex items-center justify-center transition-colors',
                        isFilled ? 'bg-muted' : 'bg-muted/40'
                      )}>
                        <ArrowRight className={cn(
                          'size-3 transition-colors',
                          isFilled ? 'text-foreground/50' : 'text-muted-foreground/30'
                        )} />
                      </div>
                    </div>

                    {/* Right: expression editor + variable chips */}
                    <div className="space-y-1.5">
                      <Textarea
                        ref={(el) => { if (el) fillTextareaRefs.current[paramName] = el; }}
                        value={fillValue}
                        onChange={(e) => setParamFills({ ...paramFills, [paramName]: e.target.value })}
                        placeholder={isReq ? 'Required — e.g. {{city}} or "static text with {{var}}"' : 'Optional — static, {{var}}, or mixed expression'}
                        rows={1}
                        className={cn(
                          'min-h-[32px] text-xs font-mono transition-all resize-y bg-muted/20 border-border/60',
                          fillValue.includes('{{') && 'dark:border-primary-700 dark:bg-primary-900/20 dark:text-primary-300'
                        )}
                      />
                      {/* Insert variable chips */}
                      {inputParamNames.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1">
                          <span className="text-[9px] text-muted-foreground/60 mr-0.5">Insert:</span>
                          {inputParamNames.map(name => {
                            const varToken = `{{${name}}}`;
                            const isUsed = fillValue.includes(varToken);
                            return (
                              <button
                                key={name}
                                type="button"
                                onClick={() => {
                                  const textarea = fillTextareaRefs.current[paramName];
                                  if (textarea) {
                                    const start = textarea.selectionStart ?? fillValue.length;
                                    const end = textarea.selectionEnd ?? start;
                                    const newValue = fillValue.slice(0, start) + varToken + fillValue.slice(end);
                                    setParamFills({ ...paramFills, [paramName]: newValue });
                                    // Restore cursor after the inserted token
                                    requestAnimationFrame(() => {
                                      const pos = start + varToken.length;
                                      textarea.focus();
                                      textarea.setSelectionRange(pos, pos);
                                    });
                                  } else {
                                    setParamFills({ ...paramFills, [paramName]: fillValue + varToken });
                                  }
                                }}
                                className={cn(
                                  'inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-md border font-mono transition-all duration-200',
                                  isUsed
                                    ? 'bg-muted text-foreground/80 border-border dark:bg-primary-900/40 dark:text-primary-300 dark:border-primary-700'
                                    : 'bg-transparent text-muted-foreground border-border/40 hover:border-border hover:bg-muted hover:text-foreground dark:hover:border-primary-700 dark:hover:text-primary-300 dark:hover:bg-primary-950/30'
                                )}
                              >
                                <CircleDot className="size-2" />
                                {varToken}
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Mapping summary */}
            <div className="flex items-center gap-2 text-[10px] text-muted-foreground px-1">
              <span className="size-1.5 rounded-full bg-green-500" />
              <span>{Object.values(paramFills).filter(v => v.trim()).length} / {successorEntries.length} mapped</span>
              {Object.values(paramFills).filter(v => v.includes('{{')).length > 0 && (
                <>
                  <span className="text-border">|</span>
                  <span>{Object.values(paramFills).filter(v => v.includes('{{')).length} with variables</span>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    );
  };

  const renderJsonEditor = () => (
    <div className="space-y-3 animate-fade-in h-full flex flex-col">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="size-6 rounded bg-muted flex items-center justify-center">
            <Code2 className="size-3 text-muted-foreground" />
          </div>
          <div>
            <h4 className="text-xs font-bold text-foreground">JSON Editor</h4>
            <p className="text-[10px] text-muted-foreground">Edit the full tool configuration as JSON</p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setJsonText(JSON.stringify(formToJsonObj(), null, 2))}
          className="text-[10px] px-2 py-0.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors border border-border/50"
        >
          Reset from form
        </button>
      </div>

      {jsonError && (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2">
          <AlertCircle className="size-3.5 text-destructive shrink-0 mt-0.5" />
          <p className="text-[11px] text-destructive">{jsonError}</p>
        </div>
      )}

      <Textarea
        value={jsonText}
        onChange={(e) => { setJsonText(e.target.value); setJsonError(null); }}
        className="flex-1 font-mono text-xs bg-muted/20 border-border/60 focus:bg-background resize-none min-h-[420px]"
        spellCheck={false}
        placeholder='{"name": "my_tool", "display_name": "My Tool", ...}'
      />

      <div className="text-[10px] text-muted-foreground/60 space-y-0.5">
        <p>Required fields: <code className="font-mono bg-muted px-1 rounded">name</code>, <code className="font-mono bg-muted px-1 rounded">display_name</code>, <code className="font-mono bg-muted px-1 rounded">description</code>, <code className="font-mono bg-muted px-1 rounded">inner_tool_name</code></p>
        <p>Use <code className="font-mono bg-muted px-1 rounded">{"{{param_name}}"}</code> in parameter_mapping values to reference input parameters.</p>
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // ─── Main Render ──────────────────────────────────────────────────────────
  // ═══════════════════════════════════════════════════════════════════════════

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Wrench className="size-3.5 text-muted-foreground" />
            <h2 className="text-sm font-semibold">Tools</h2>
          </div>
          <span className="text-xs text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">{tools.length}</span>
        </div>
        <div className="flex items-center gap-2">
          <ViewToggle mode={viewMode} onToggle={(m) => { setViewMode(m); if (m !== 'drawer') setOpenItemId(null); }} />
          <Button size="sm" variant="outline" className="gap-1.5" onClick={() => importFileRef.current?.click()}>
            <Upload className="size-3" /> Import
          </Button>
          <Button size="sm" onClick={openCreateModal}>+ New</Button>
          <input ref={importFileRef} type="file" accept=".json" className="hidden" onChange={handleImportFile} />
        </div>
      </div>

      {/* Tag filter */}
      {allTags.length > 0 && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          {selectedTags.length > 0 && (
            <button type="button" onClick={() => setSelectedTags([])}
              className="text-[10px] px-2 py-1 rounded-full border border-border text-muted-foreground hover:text-foreground transition-colors">Clear</button>
          )}
          {allTags.map(tag => (
            <button key={tag} type="button"
              onClick={() => setSelectedTags(prev => prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag])}
              className={cn('text-[10px] px-2.5 py-1 rounded-full border font-medium transition-colors',
                selectedTags.includes(tag)
                  ? 'bg-foreground text-background border-foreground'
                  : 'bg-muted/50 text-muted-foreground border-border/50 hover:border-foreground/30')}>
              {tag}
            </button>
          ))}
        </div>
      )}

      {/* ── Tool List ── */}
      {tools.length > 0 ? viewMode === 'card' ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {tools.map(tool => {
            const inner = isInner(tool);
            const modeKey = inner ? 'inner' : (tool.inner_tool_name ? 'inner' : (tool.execution_mode || 'http'));
            return (
              <div key={tool.id} className={cn(
                'rounded-xl border bg-card p-4 flex flex-col gap-2.5 hover:bg-muted/20 transition-colors',
                inner ? 'border-border' : tool.enabled ? 'border-green-200/70 dark:border-green-800/40' : 'border-amber-200/70 dark:border-amber-800/40',
                !inner && !tool.enabled && 'opacity-70')}>
                <div className="flex items-start gap-2.5">
                  <span className={cn('size-2 rounded-full mt-1 shrink-0',
                    inner ? 'bg-muted-foreground/40' : tool.enabled ? 'bg-green-500' : 'bg-amber-400')} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold leading-snug truncate">{tool.display_name || tool.name}</p>
                    {tool.display_name && <p className="text-[10px] text-muted-foreground/60 font-mono truncate">{tool.name}</p>}
                  </div>
                  {!inner && (
                    <span className={cn('text-[10px] px-1.5 py-0.5 rounded-full border shrink-0 font-medium',
                      tool.enabled
                        ? 'bg-green-50 text-green-700 border-green-200/70 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800/50'
                        : 'bg-amber-50 text-amber-700 border-amber-200/70 dark:bg-amber-900/20 dark:text-amber-400 dark:border-amber-800/50')}>
                      {tool.enabled ? 'on' : 'off'}
                    </span>
                  )}
                </div>
                {tool.description && <p className="text-xs text-muted-foreground line-clamp-2">{tool.description}</p>}
                <div className="flex flex-wrap gap-1.5">
                  <span className={cn('text-[10px] px-2 py-0.5 rounded-full border', MODE_COLOR[modeKey] ?? MODE_COLOR.inner)}>
                    {inner ? 'built-in' : modeKey}
                  </span>
                  {tool.category && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50">{tool.category}</span>
                  )}
                  {!inner && tool.inner_tool_name && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-primary-50 text-primary-600 border border-primary-200/50 dark:bg-primary-900/20 dark:text-primary-400 dark:border-primary-800/40 font-mono">
                      {tool.inner_tool_name}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3 text-[10px] text-muted-foreground mt-auto">
                  {!inner && <span className="tabular-nums">{tool.usage_count ?? 0} calls</span>}
                  <span className="ml-auto">{formatRelativeTime(tool.created_at)}</span>
                </div>
                <div className="flex gap-2 pt-2 border-t border-border/40">
                  {inner ? (
                    <>
                      <Button size="sm" variant="outline" className="flex-1 gap-1.5" onClick={(e) => { e.stopPropagation(); handleUseAsTemplate(tool); }}>
                        <Copy className="size-3.5" />Use as Template
                      </Button>
                      <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => { e.stopPropagation(); openTestDialog(tool); }}>
                        <Play className="size-3.5" />Test
                      </Button>
                      <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => handleExportTool(tool, e)}>
                        <Download className="size-3.5" />
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button size="sm" variant="outline" className={cn('gap-1.5',
                        tool.enabled ? 'text-green-700 border-green-200/70' : 'text-amber-700 border-amber-200/70')}
                        disabled={toggleMutation.isPending} onClick={(e) => handleToggle(tool.id, tool.enabled, e)}>
                        {tool.enabled ? <ToggleRight className="size-3.5" /> : <ToggleLeft className="size-3.5" />}
                      </Button>
                      <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => { e.stopPropagation(); openEditModal(tool); }}>
                        <Pencil className="size-3.5" />
                      </Button>
                      <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => { e.stopPropagation(); openTestDialog(tool); }}>
                        <Play className="size-3.5" />
                      </Button>
                      <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => { e.stopPropagation(); handleUseAsTemplate(tool); }}>
                        <Copy className="size-3.5" />
                      </Button>
                      <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => handleExportTool(tool, e)}>
                        <Download className="size-3.5" />
                      </Button>
                      <Button size="sm" variant="outline" className="text-destructive border-destructive/30"
                        disabled={deleteMutation.isPending} onClick={(e) => handleDelete(tool.id, tool.display_name || tool.name, e)}>
                        <Trash2 className="size-3.5" />
                      </Button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        /* ── List / Drawer view ── */
        <div className="rounded-xl border border-border bg-card overflow-visible divide-y divide-border/50">
          {tools.map(tool => {
            const inner = isInner(tool);
            const modeKey = inner ? 'inner' : (tool.inner_tool_name ? 'inner' : (tool.execution_mode || 'http'));
            const statusDot = <span className={cn('size-2 rounded-full shrink-0', inner ? 'bg-muted-foreground/40' : tool.enabled ? 'bg-green-500' : 'bg-amber-400')} />;
            const modeChip = <span className={cn('text-[10px] px-2 py-0.5 rounded-full border shrink-0', MODE_COLOR[modeKey] ?? MODE_COLOR.inner)}>{inner ? 'built-in' : modeKey}</span>;

            const rowHeader = (
              <>
                {statusDot}
                <div className="flex-1 min-w-0 flex items-baseline gap-2">
                  <span className="text-sm font-medium truncate">{tool.display_name || tool.name}</span>
                  {tool.display_name && <span className="text-[10px] text-muted-foreground/60 font-mono truncate hidden sm:block">{tool.name}</span>}
                </div>
                {tool.category && <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 shrink-0">{tool.category}</span>}
                {modeChip}
                {!inner && tool.inner_tool_name && <span className="text-[10px] text-primary-600 dark:text-primary-400 font-mono shrink-0">{tool.inner_tool_name}</span>}
              </>
            );

            if (viewMode === 'list') {
              return (
                <div key={tool.id} className={cn('group relative flex items-center gap-3 px-4 py-2.5 hover:bg-muted/40 transition-colors', !inner && !tool.enabled && 'opacity-60')}>
                  {statusDot}
                  <div className="flex-1 min-w-0 flex items-baseline gap-2">
                    <span className="text-sm font-medium truncate">{tool.display_name || tool.name}</span>
                    {tool.display_name && <span className="text-[10px] text-muted-foreground/60 font-mono truncate hidden sm:block">{tool.name}</span>}
                  </div>
                  {tool.category && <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 shrink-0">{tool.category}</span>}
                  {modeChip}
                  {!inner && tool.inner_tool_name && <span className="text-[10px] text-primary-600 dark:text-primary-400 font-mono shrink-0">{tool.inner_tool_name}</span>}
                  <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                    <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted" onClick={(e) => { e.stopPropagation(); openTestDialog(tool); }}><Play className="size-3.5 text-muted-foreground" /></button>
                    {!inner && (
                      <>
                        <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted" onClick={(e) => handleToggle(tool.id, tool.enabled, e)}>
                          {tool.enabled ? <ToggleRight className="size-4 text-green-600" /> : <ToggleLeft className="size-4 text-amber-500" />}
                        </button>
                        <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted" onClick={(e) => { e.stopPropagation(); openEditModal(tool); }}><Pencil className="size-3.5 text-muted-foreground" /></button>
                      </>
                    )}
                    <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted" onClick={(e) => { e.stopPropagation(); handleUseAsTemplate(tool); }}><Copy className="size-3.5 text-muted-foreground" /></button>
                    <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted" onClick={(e) => handleExportTool(tool, e)}><Download className="size-3.5 text-muted-foreground" /></button>
                    {!inner && <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-destructive/10" onClick={(e) => handleDelete(tool.id, tool.display_name || tool.name, e)}><Trash2 className="size-3.5 text-destructive/70" /></button>}
                  </div>
                </div>
              );
            }

            // Drawer
            return (
              <AccordionItem key={tool.id} isOpen={openItemId === tool.id}
                onToggle={() => setOpenItemId(openItemId === tool.id ? null : tool.id)}
                header={rowHeader}
                detail={
                  <div className="space-y-3">
                    {tool.description && <p className="text-xs text-foreground/80 leading-relaxed">{tool.description}</p>}
                    <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                      {tool.inner_tool_name && <><span className="text-muted-foreground">Wraps</span><span className="font-mono">{tool.inner_tool_name}</span></>}
                      {tool.category && <><span className="text-muted-foreground">Category</span><span>{tool.category}</span></>}
                      {!inner && <><span className="text-muted-foreground">Timeout</span><span className="tabular-nums">{tool.timeout ?? 30}s</span></>}
                    </div>
                    {tool.parameter_mapping && Object.keys(tool.parameter_mapping).length > 0 && (
                      <div>
                        <p className="text-[10px] text-muted-foreground mb-1 font-semibold uppercase tracking-wide">Parameter Mapping</p>
                        <div className="rounded border border-primary-200/40 dark:border-primary-800/30 divide-y divide-primary-100/60 dark:divide-primary-800/20">
                          {Object.entries(tool.parameter_mapping).map(([k, v]) => (
                            <div key={k} className="flex items-center gap-2 px-3 py-1.5 text-xs">
                              <code className="font-mono text-foreground/70">{k}</code>
                              <MoveRight className="size-3 text-primary-400 shrink-0" />
                              <code className={cn('font-mono', String(v).includes('{{') ? 'text-primary-700 dark:text-primary-300' : 'text-foreground')}>{v}</code>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="flex gap-2 pt-2 border-t border-border/40">
                      <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => { e.stopPropagation(); openTestDialog(tool); }}><Play className="size-3.5" />Test</Button>
                      {!inner && (
                        <>
                          <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => { e.stopPropagation(); openEditModal(tool); }}><Pencil className="size-3.5" />Edit</Button>
                          <Button size="sm" variant="outline" className="text-destructive border-destructive/30" onClick={(e) => handleDelete(tool.id, tool.display_name || tool.name, e)}><Trash2 className="size-3.5" /></Button>
                        </>
                      )}
                      <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => { e.stopPropagation(); handleUseAsTemplate(tool); }}><Copy className="size-3.5" />{inner ? 'Use as Template' : ''}</Button>
                      <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => handleExportTool(tool, e)}><Download className="size-3.5" />Export</Button>
                    </div>
                  </div>
                }
              />
            );
          })}
        </div>
      ) : (
        <div className="text-center py-16 border border-dashed border-border rounded-xl">
          <Wrench className="mx-auto size-8 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground mb-3">No tools yet</p>
          <Button size="sm" onClick={openCreateModal}>Create Tool</Button>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {/* ── Test Dialog ── */}
      {/* ═══════════════════════════════════════════════════════════════════════ */}
      <Dialog open={!!testingTool} onOpenChange={(open) => { if (!open) setTestingTool(null); }}>
        <DialogContent className="max-w-xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Play className="size-4" /> Test — {testingTool?.display_name || testingTool?.name}
            </DialogTitle>
          </DialogHeader>
          {testingTool && (
            <div className="space-y-4">
              {(() => {
                const { props, required } = getSchemaProperties(testingTool.input_schema);
                return Object.keys(props).length > 0 && (
                  <div>
                    <p className="text-[10px] text-muted-foreground mb-1.5 font-semibold uppercase tracking-wide">Expected Parameters</p>
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(props).map(([name, prop]) => (
                        <span key={name} className={cn('text-[10px] px-2 py-0.5 rounded border font-mono',
                          required.has(name) ? 'bg-foreground/5 border-foreground/20 text-foreground' : 'bg-muted text-muted-foreground border-border/50')}>
                          {name}{prop.type ? `: ${prop.type}` : ''}
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })()}
              <div className="space-y-1.5">
                <Label className="text-xs">Parameters (JSON)</Label>
                <Textarea value={testParamsJson} onChange={(e) => setTestParamsJson(e.target.value)}
                  rows={6} className="font-mono text-sm" placeholder='{"key": "value"}' />
              </div>
              <Button onClick={handleRunTest} disabled={testMutation.isPending} className="w-full gap-2">
                {testMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
                {testMutation.isPending ? 'Running...' : 'Run Test'}
              </Button>
              {testMutation.data && (
                <div className={cn('rounded-lg border p-3 space-y-2',
                  testMutation.data.success ? 'border-green-200/70 bg-green-50/30 dark:border-green-800/40 dark:bg-green-900/10' : 'border-red-200/70 bg-red-50/30 dark:border-red-800/40 dark:bg-red-900/10')}>
                  <div className="flex items-center gap-2">
                    <span className={cn('size-2 rounded-full', testMutation.data.success ? 'bg-green-500' : 'bg-red-500')} />
                    <span className="text-xs font-semibold">{testMutation.data.success ? 'Success' : 'Failed'}</span>
                    {testMutation.data.execution_time_ms != null && <span className="text-[10px] text-muted-foreground tabular-nums ml-auto">{testMutation.data.execution_time_ms.toFixed(1)}ms</span>}
                  </div>
                  {testMutation.data.message && <p className="text-xs text-foreground/80">{testMutation.data.message}</p>}
                  {testMutation.data.error && <p className="text-xs text-red-600 dark:text-red-400">{testMutation.data.error}</p>}
                  {testMutation.data.data && (
                    <pre className="text-[10px] font-mono bg-background rounded p-2 overflow-x-auto max-h-48 overflow-y-auto border border-border/30">
                      {JSON.stringify(testMutation.data.data, null, 2)}
                    </pre>
                  )}
                </div>
              )}
              {testMutation.error && (
                <div className="rounded-lg border border-red-200/70 bg-red-50/30 p-3">
                  <p className="text-xs text-red-600">Request failed: {testMutation.error instanceof Error ? testMutation.error.message : 'Unknown error'}</p>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {/* ── Create / Edit Wizard Dialog ── */}
      {/* ═══════════════════════════════════════════════════════════════════════ */}
      <Dialog open={dialogOpen} onOpenChange={(open) => {
        if (!open) {
          setShowCreateModal(false);
          setEditingTool(null);
        }
      }}>
        <DialogContent className="max-w-2xl max-h-[92vh] flex flex-col p-0 gap-0 overflow-hidden" showCloseButton={false}>
          {/* ── Dialog Header with Step Indicator ── */}
          <div className="shrink-0 border-b border-border/60 bg-card/50">
            {/* Title bar */}
            <div className="flex items-center justify-between px-5 pt-4 pb-3">
              <div className="flex items-center gap-2.5">
                <div className="size-7 rounded-lg bg-muted flex items-center justify-center dark:bg-primary-900/40">
                  <Wrench className="size-3.5 text-muted-foreground dark:text-primary-300" />
                </div>
                <div>
                  <h2 className="text-sm font-bold text-foreground">{isEditing ? 'Edit Tool' : 'Create Tool'}</h2>
                  <p className="text-[10px] text-muted-foreground">
                    {isEditing ? `Editing ${editingTool?.display_name}` : 'Build a new tool with parameter mapping'}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {/* Mode toggle */}
                <div className="flex items-center rounded-lg border border-border/60 bg-muted/30 p-0.5">
                  <button
                    type="button"
                    onClick={() => {
                      if (editMode === 'json') switchToForm();
                    }}
                    className={cn(
                      'flex items-center gap-1 px-2.5 py-1 rounded-md text-[10px] font-medium transition-all duration-200',
                      editMode === 'form'
                        ? 'bg-background text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    <FormInput className="size-3" /> Form
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (editMode === 'form') switchToJson();
                    }}
                    className={cn(
                      'flex items-center gap-1 px-2.5 py-1 rounded-md text-[10px] font-medium transition-all duration-200',
                      editMode === 'json'
                        ? 'bg-background text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    <Code2 className="size-3" /> JSON
                  </button>
                </div>
                <button
                  type="button"
                  onClick={() => { setShowCreateModal(false); setEditingTool(null); }}
                  className="size-7 rounded-lg flex items-center justify-center hover:bg-muted transition-colors"
                >
                  <X className="size-4 text-muted-foreground" />
                </button>
              </div>
            </div>

            {/* Step indicators — hidden in JSON mode */}
            <div className={cn('flex items-center gap-0 px-5 pb-0', editMode === 'json' && 'hidden')}>
              {WIZARD_STEPS.map((step, idx) => {
                const isCurrent = wizardStep === step.key;
                const isCompleted = idx < stepIndex;
                const isClickable = idx <= stepIndex || isStepValid(WIZARD_STEPS[idx - 1]?.key);

                return (
                  <button
                    key={step.key}
                    type="button"
                    onClick={() => isClickable && setWizardStep(step.key)}
                    disabled={!isClickable}
                    className={cn(
                      'group relative flex items-center gap-2 px-4 py-2.5 flex-1 transition-all duration-200',
                      isCurrent && 'bg-background',
                      !isCurrent && isClickable && 'hover:bg-muted/40',
                      !isClickable && 'opacity-40 cursor-not-allowed',
                      // Bottom border indicator
                      'border-b-2',
                      isCurrent
                        ? 'border-foreground/30'
                        : isCompleted
                          ? 'border-border'
                          : 'border-transparent'
                    )}
                  >
                    {/* Step number circle */}
                    <div className={cn(
                      'size-5 rounded-full flex items-center justify-center shrink-0 text-[10px] font-bold transition-all',
                      isCurrent
                        ? 'bg-foreground/10 text-foreground'
                        : isCompleted
                          ? 'bg-muted text-muted-foreground'
                          : 'bg-muted text-muted-foreground'
                    )}>
                      {isCompleted ? <Check className="size-2.5" /> : idx + 1}
                    </div>
                    <div className="text-left min-w-0">
                      <p className={cn(
                        'text-[11px] font-semibold leading-tight truncate',
                        isCurrent ? 'text-foreground' : 'text-muted-foreground'
                      )}>
                        {step.label}
                      </p>
                      <p className="text-[9px] text-muted-foreground/70 truncate hidden sm:block">{step.description}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* ── Step Content ── */}
          <div className="flex-1 overflow-y-auto px-5 py-5 scrollbar-thin">
            {editMode === 'json' ? renderJsonEditor() : (
              <>
                {wizardStep === 'info' && renderStepInfo()}
                {wizardStep === 'schema' && renderStepSchema()}
                {wizardStep === 'mapping' && renderStepMapping()}
              </>
            )}
          </div>

          {/* ── Footer Navigation ── */}
          <div className="shrink-0 border-t border-border/60 bg-card/50 px-5 py-3 flex items-center justify-between">
            <div>
              {editMode === 'form' && stepIndex > 0 && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="gap-1.5"
                  onClick={() => setWizardStep(WIZARD_STEPS[stepIndex - 1].key)}
                >
                  <ArrowLeft className="size-3" /> Back
                </Button>
              )}
            </div>

            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => { setShowCreateModal(false); setEditingTool(null); }}
              >
                Cancel
              </Button>

              {editMode === 'form' && stepIndex < WIZARD_STEPS.length - 1 ? (
                <Button
                  type="button"
                  size="sm"
                  className="gap-1.5"
                  disabled={!isStepValid(wizardStep)}
                  onClick={() => setWizardStep(WIZARD_STEPS[stepIndex + 1].key)}
                >
                  Next <ArrowRight className="size-3" />
                </Button>
              ) : (
                <Button
                  type="button"
                  size="sm"
                  className="gap-1.5"
                  disabled={
                    (isEditing ? updateMutation.isPending : createMutation.isPending)
                    || (editMode === 'form' && !selectedSuccessor)
                  }
                  onClick={isEditing ? handleUpdate : handleCreate}
                >
                  {(isEditing ? updateMutation.isPending : createMutation.isPending) && (
                    <Loader2 className="size-3 animate-spin" />
                  )}
                  {isEditing
                    ? (updateMutation.isPending ? 'Saving...' : 'Save Changes')
                    : (createMutation.isPending ? 'Creating...' : 'Create Tool')
                  }
                </Button>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
