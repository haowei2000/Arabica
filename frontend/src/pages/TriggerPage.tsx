import { useState, useMemo, useEffect } from 'react';
import { Zap, Loader2, Trash2, Pencil, Activity, Filter, ChevronDown, X } from 'lucide-react';
import { ViewToggle, type ViewMode } from '@/components/ViewToggle';
import { AccordionItem } from '@/components/AccordionItem';
import { formatRelativeTime } from '@/utils/formatDate';
import { useTriggers, useCreateTrigger, useUpdateTrigger, useDeleteTrigger } from '@/hooks/useTriggers';
import { useInnerTools } from '@/hooks/useTools';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { cn } from '@/lib/utils';
import type { TriggerCreate, Trigger } from '@/types/trigger';
import type { InnerToolInfo } from '@/types/tool';

// ─── Constants ─────────────────────────────────────────────────────────────

const CONDITION_TYPES = ['always', 'keyword', 'regex', 'jsonpath'] as const;

// ─── JSON Schema field parser ───────────────────────────────────────────────

interface SchemaField {
  key: string;
  type: string;           // "string" | "number" | "integer" | "boolean" | "array"
  description: string;
  required: boolean;
  default?: unknown;
  enum?: string[];
}

/** Parse JSON Schema into a flat list of fields, excluding workspace_id */
function parseSchemaFields(schema: Record<string, unknown>): SchemaField[] {
  const properties = (schema.properties ?? {}) as Record<string, Record<string, unknown>>;
  const required = (schema.required ?? []) as string[];
  return Object.entries(properties)
    .filter(([key]) => key !== 'workspace_id')
    .map(([key, prop]) => ({
      key,
      type: String(prop.type ?? 'string'),
      description: String(prop.description ?? ''),
      required: required.includes(key),
      default: prop.default,
      enum: prop.enum ? (prop.enum as string[]) : undefined,
    }));
}

// ─── Dynamic param form ─────────────────────────────────────────────────────

function ParamForm({
  fields,
  values,
  onChange,
}: {
  fields: SchemaField[];
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}) {
  if (fields.length === 0) return (
    <p className="text-xs text-muted-foreground italic">This tool has no configurable parameters.</p>
  );

  return (
    <div className="space-y-3">
      {fields.map((field) => {
        const val = values[field.key];
        const label = (
          <Label key={field.key + '-label'} className="flex items-center gap-1">
            {field.key}
            {field.required && <span className="text-destructive">*</span>}
            {!field.required && <span className="text-[10px] text-muted-foreground">(optional)</span>}
          </Label>
        );

        if (field.type === 'boolean') {
          return (
            <div key={field.key} className="flex items-center gap-2">
              <Checkbox
                checked={Boolean(val ?? field.default ?? false)}
                onCheckedChange={(v) => onChange(field.key, v === true)}
              />
              {label}
              {field.description && <span className="text-xs text-muted-foreground">{field.description}</span>}
            </div>
          );
        }

        if (field.enum) {
          return (
            <div key={field.key} className="space-y-1">
              {label}
              <Select
                value={String(val ?? field.default ?? field.enum[0])}
                onValueChange={(v) => onChange(field.key, v)}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {field.enum.map((opt) => (
                    <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {field.description && <p className="text-xs text-muted-foreground">{field.description}</p>}
            </div>
          );
        }

        if (field.type === 'number' || field.type === 'integer') {
          return (
            <div key={field.key} className="space-y-1">
              {label}
              <Input
                type="number"
                value={val !== undefined ? String(val) : String(field.default ?? '')}
                onChange={(e) => onChange(field.key, e.target.value === '' ? undefined : Number(e.target.value))}
                placeholder={field.description}
              />
            </div>
          );
        }

        // Default: string / array
        return (
          <div key={field.key} className="space-y-1">
            {label}
            <Input
              value={val !== undefined ? String(val) : String(field.default ?? '')}
              onChange={(e) => onChange(field.key, e.target.value || undefined)}
              placeholder={field.description}
              className="font-mono text-sm"
            />
            {field.description && <p className="text-xs text-muted-foreground">{field.description}</p>}
          </div>
        );
      })}
    </div>
  );
}

// ─── Tool picker combobox ────────────────────────────────────────────────────

function ToolPicker({
  value,
  onChange,
  tools,
}: {
  value: string;
  onChange: (name: string, tool: InnerToolInfo | null) => void;
  tools: InnerToolInfo[];
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState(value);

  useEffect(() => { setSearch(value); }, [value]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    return tools.filter(
      (t) => t.name.includes(q) || t.display_name.toLowerCase().includes(q) || t.category.toLowerCase().includes(q)
    ).slice(0, 30);
  }, [tools, search]);

  const grouped = useMemo(() => {
    const map = new Map<string, InnerToolInfo[]>();
    for (const t of filtered) {
      if (!map.has(t.category)) map.set(t.category, []);
      map.get(t.category)!.push(t);
    }
    return map;
  }, [filtered]);

  const handleSelect = (tool: InnerToolInfo) => {
    onChange(tool.name, tool);
    setSearch(tool.name);
    setOpen(false);
  };

  const handleInputChange = (v: string) => {
    setSearch(v);
    onChange(v, tools.find((t) => t.name === v) ?? null);
    setOpen(true);
  };

  const handleClear = () => {
    setSearch('');
    onChange('', null);
    setOpen(false);
  };

  return (
    <div className="relative">
      <div className="flex items-center gap-1">
        <div className="relative flex-1">
          <Input
            value={search}
            onChange={(e) => handleInputChange(e.target.value)}
            onFocus={() => setOpen(true)}
            onBlur={() => setTimeout(() => setOpen(false), 150)}
            placeholder="e.g., glance_context, http_request…"
            className="font-mono text-sm pr-8"
            required
          />
          {search && (
            <button
              type="button"
              onClick={handleClear}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="size-9 shrink-0"
          onClick={() => setOpen((v) => !v)}
        >
          <ChevronDown className="size-4" />
        </Button>
      </div>

      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-lg border border-border bg-card shadow-lg shadow-black/10 overflow-hidden">
          <ScrollArea viewportClassName="max-h-56">
            {grouped.size === 0 ? (
              <p className="text-xs text-muted-foreground px-3 py-2">No tools found.</p>
            ) : (
              Array.from(grouped.entries()).map(([cat, items]) => (
                <div key={cat}>
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground px-3 py-1.5 bg-muted/50 border-b border-border/50">
                    {cat}
                  </p>
                  {items.map((tool) => (
                    <button
                      key={tool.name}
                      type="button"
                      onMouseDown={() => handleSelect(tool)}
                      className={cn(
                        'w-full flex items-start gap-3 px-3 py-2 text-left text-sm hover:bg-muted/60 transition-colors border-b border-border/30 last:border-0',
                        value === tool.name && 'bg-primary/5 text-primary'
                      )}
                    >
                      <span className="font-mono text-xs mt-0.5 shrink-0 w-36 truncate">{tool.name}</span>
                      <span className="text-xs text-muted-foreground truncate">{tool.description}</span>
                    </button>
                  ))}
                </div>
              ))
            )}
          </ScrollArea>
        </div>
      )}
    </div>
  );
}

// ─── Main component ──────────────────────────────────────────────────────────

const INITIAL_FORM: TriggerCreate = {
  name: '',
  description: '',
  event_type: 'user.message',
  condition_type: 'always',
  condition_value: '',
  condition_field: 'message',
  tool_name: '',
  action_params: undefined,
  priority: 0,
  enabled: true,
};

export default function TriggerPage() {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>('card');
  const [openItemId, setOpenItemId] = useState<string | null>(null);
  const [editingTrigger, setEditingTrigger] = useState<Trigger | null>(null);
  const [formData, setFormData] = useState<TriggerCreate>(INITIAL_FORM);
  // Dynamic param values from the schema-driven form
  const [paramValues, setParamValues] = useState<Record<string, unknown>>({});
  // Currently selected tool info (null = custom / unknown tool)
  const [selectedTool, setSelectedTool] = useState<InnerToolInfo | null>(null);

  const handleModeToggle = (m: ViewMode) => {
    setViewMode(m);
    if (m === 'list') setOpenItemId(null);
  };

  const { data: triggersData, isLoading } = useTriggers({ page: 1, page_size: 100 });
  const { data: innerToolData } = useInnerTools();
  const createMutation = useCreateTrigger();
  const updateMutation = useUpdateTrigger();
  const deleteMutation = useDeleteTrigger();

  const triggers = triggersData?.items ?? [];
  const allTools: InnerToolInfo[] = innerToolData?.inner_tools ?? [];

  /** Fields to display, derived from selected tool's schema */
  const schemaFields = useMemo<SchemaField[]>(() => {
    if (!selectedTool) return [];
    return parseSchemaFields(selectedTool.input_schema as Record<string, unknown>);
  }, [selectedTool]);

  /** Build action_params from dynamic form values, stripping undefined */
  const buildActionParams = (): Record<string, unknown> | undefined => {
    if (schemaFields.length === 0) return undefined;
    const result: Record<string, unknown> = {};
    for (const field of schemaFields) {
      const v = paramValues[field.key];
      if (v !== undefined && v !== '') result[field.key] = v;
    }
    return Object.keys(result).length > 0 ? result : undefined;
  };

  const handleToolChange = (name: string, tool: InnerToolInfo | null) => {
    setFormData((f) => ({ ...f, tool_name: name }));
    setSelectedTool(tool);
    setParamValues({});
  };

  const handleParamChange = (key: string, value: unknown) => {
    setParamValues((prev) => ({ ...prev, [key]: value }));
  };

  const resetForm = () => {
    setFormData(INITIAL_FORM);
    setSelectedTool(null);
    setParamValues({});
  };

  const openCreateModal = () => {
    resetForm();
    setShowCreateModal(true);
  };

  const openEditModal = (trigger: Trigger) => {
    setEditingTrigger(trigger);
    const tool = allTools.find((t) => t.name === trigger.tool_name) ?? null;
    setSelectedTool(tool);
    setFormData({
      name: trigger.name,
      description: trigger.description || '',
      event_type: trigger.event_type,
      condition_type: trigger.condition_type,
      condition_value: trigger.condition_value || '',
      condition_field: trigger.condition_field || 'message',
      tool_name: trigger.tool_name,
      action_params: trigger.action_params || undefined,
      priority: trigger.priority,
      enabled: trigger.enabled,
    });
    // Pre-fill param values from saved action_params
    setParamValues((trigger.action_params as Record<string, unknown>) ?? {});
    setShowEditModal(true);
  };

  const closeModal = () => {
    setShowCreateModal(false);
    setShowEditModal(false);
    setEditingTrigger(null);
    resetForm();
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createMutation.mutateAsync({ ...formData, action_params: buildActionParams() });
      closeModal();
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingTrigger) return;
    try {
      await updateMutation.mutateAsync({
        id: editingTrigger.id,
        data: { ...formData, action_params: buildActionParams() },
      });
      closeModal();
    } catch (error) {
      alert(`Update failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleDelete = async (trigger: Trigger) => {
    if (!confirm(`Are you sure you want to delete "${trigger.name}"?`)) return;
    try {
      await deleteMutation.mutateAsync(trigger.id);
    } catch (error) {
      alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const isModalOpen = showCreateModal || showEditModal;
  const needsConditionValue = formData.condition_type !== 'always';

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div>
      {/* ── Header ── */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Zap className="size-3.5 text-muted-foreground" />
            <h2 className="text-sm font-semibold">Triggers</h2>
          </div>
          <span className="text-xs text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">
            {triggers.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <ViewToggle mode={viewMode} onToggle={handleModeToggle} />
          <Button size="sm" onClick={openCreateModal}>+ New</Button>
        </div>
      </div>

      {/* ── Trigger list ── */}
      {triggers.length > 0 ? viewMode === 'card' ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {triggers.map((trigger) => (
            <div key={trigger.id} className="rounded-xl border border-border bg-card p-4 flex flex-col gap-2.5 hover:bg-muted/20 transition-colors">
              <div className="flex items-start gap-2.5">
                <span className={cn('size-2 rounded-full mt-1 shrink-0', trigger.enabled ? 'bg-green-500' : 'bg-muted-foreground/30')} />
                <p className="text-sm font-semibold leading-snug flex-1 min-w-0 truncate">{trigger.name}</p>
              </div>
              {trigger.description && <p className="text-xs text-muted-foreground line-clamp-2">{trigger.description}</p>}
              <div className="flex flex-wrap gap-1.5">
                <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 font-mono">
                  <Activity className="size-2.5" />{trigger.event_type}
                </span>
                {trigger.condition_type !== 'always' && (
                  <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 border border-orange-200/70 dark:bg-orange-900/30 dark:text-orange-300 dark:border-orange-800/50">
                    <Filter className="size-2.5" />{trigger.condition_type}
                  </span>
                )}
                <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 border border-amber-200/70 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800/50">
                  <Zap className="size-2.5" />{trigger.tool_name}
                </span>
              </div>
              <div className="flex items-center gap-3 text-[10px] text-muted-foreground mt-auto">
                <span className="tabular-nums">p:{trigger.priority}</span>
                <span className={cn(trigger.enabled ? 'text-green-600' : '')}>{trigger.enabled ? 'enabled' : 'disabled'}</span>
                <span className="ml-auto">{formatRelativeTime(trigger.created_at)}</span>
              </div>
              <div className="flex gap-2 pt-2 border-t border-border/40">
                <Button size="sm" variant="outline" className="flex-1 gap-1.5" onClick={() => openEditModal(trigger)}>
                  <Pencil className="size-3.5" />Edit
                </Button>
                <Button size="sm" variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                  disabled={deleteMutation.isPending} onClick={() => handleDelete(trigger)}>
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-card overflow-visible divide-y divide-border/50">
          {triggers.map((trigger) => {
            const statusDot = <span className={cn('size-2 rounded-full shrink-0', trigger.enabled ? 'bg-green-500' : 'bg-muted-foreground/30')} />;
            const chips = (
              <>
                <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 shrink-0 font-mono">
                  <Activity className="size-2.5" />{trigger.event_type}
                </span>
                {trigger.condition_type !== 'always' && (
                  <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 border border-orange-200/70 dark:bg-orange-900/30 dark:text-orange-300 dark:border-orange-800/50 shrink-0">
                    <Filter className="size-2.5" />{trigger.condition_type}
                  </span>
                )}
                <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 border border-amber-200/70 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800/50 shrink-0">
                  <Zap className="size-2.5" />{trigger.tool_name}
                </span>
              </>
            );

            if (viewMode === 'list') {
              return (
                <div key={trigger.id} className="group relative flex items-center gap-3 px-4 py-3 hover:bg-muted/40 transition-colors">
                  {statusDot}
                  <span className="text-sm font-medium flex-1 min-w-0 truncate">{trigger.name}</span>
                  {chips}
                  <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                    <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted transition-colors" onClick={() => openEditModal(trigger)}>
                      <Pencil className="size-3.5 text-muted-foreground" />
                    </button>
                    <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-destructive/10 transition-colors" disabled={deleteMutation.isPending} onClick={() => handleDelete(trigger)}>
                      <Trash2 className="size-3.5 text-destructive/70" />
                    </button>
                  </div>
                  <div className="absolute right-2 top-full mt-1 z-50 w-72 rounded-xl border border-border bg-card shadow-lg p-3 invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-[opacity,visibility] duration-150 pointer-events-none">
                    <div className="space-y-2 text-xs">
                      {trigger.description && <p className="text-foreground/80">{trigger.description}</p>}
                      <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pt-1.5 border-t border-border/50 text-muted-foreground">
                        <span>Event</span><span className="text-foreground font-mono">{trigger.event_type}</span>
                        <span>Condition</span><span className="text-foreground">{trigger.condition_type}</span>
                        {trigger.condition_value && (<><span>Match</span><span className="text-foreground font-mono truncate">{trigger.condition_value}</span></>)}
                        <span>Tool</span><span className="text-foreground font-mono">{trigger.tool_name}</span>
                        <span>Priority</span><span className="text-foreground tabular-nums">{trigger.priority}</span>
                        <span>Status</span><span className="text-foreground">{trigger.enabled ? 'Enabled' : 'Disabled'}</span>
                      </div>
                    </div>
                  </div>
                </div>
              );
            }

            return (
              <AccordionItem
                key={trigger.id}
                isOpen={openItemId === trigger.id}
                onToggle={() => setOpenItemId(openItemId === trigger.id ? null : trigger.id)}
                header={<>{statusDot}<span className="text-sm font-medium flex-1 min-w-0 truncate">{trigger.name}</span>{chips}</>}
                detail={
                  <div className="space-y-3">
                    {trigger.description && <p className="text-xs text-foreground/80">{trigger.description}</p>}
                    <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                      <span className="text-muted-foreground">Event</span><span className="font-mono">{trigger.event_type}</span>
                      <span className="text-muted-foreground">Condition</span><span>{trigger.condition_type}</span>
                      {trigger.condition_value && (<><span className="text-muted-foreground">Match</span><span className="font-mono">{trigger.condition_value}</span></>)}
                      <span className="text-muted-foreground">Tool</span><span className="font-mono">{trigger.tool_name}</span>
                      {trigger.action_params && (<><span className="text-muted-foreground">Params</span><span className="font-mono truncate">{JSON.stringify(trigger.action_params)}</span></>)}
                      <span className="text-muted-foreground">Priority</span><span className="tabular-nums">{trigger.priority}</span>
                      <span className="text-muted-foreground">Status</span>
                      <span className="flex items-center gap-1.5">
                        <span className={cn('size-1.5 rounded-full', trigger.enabled ? 'bg-green-500' : 'bg-muted-foreground/30')} />
                        {trigger.enabled ? 'Enabled' : 'Disabled'}
                      </span>
                      <span className="text-muted-foreground">Created</span><span>{formatRelativeTime(trigger.created_at)}</span>
                    </div>
                    <div className="flex gap-2 pt-2 border-t border-border/40">
                      <Button size="sm" variant="outline" className="flex-1 gap-1.5" onClick={() => openEditModal(trigger)}>
                        <Pencil className="size-3.5" />Edit
                      </Button>
                      <Button size="sm" variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                        disabled={deleteMutation.isPending} onClick={() => handleDelete(trigger)}>
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </div>
                }
              />
            );
          })}
        </div>
      ) : (
        <div className="text-center py-16 border border-dashed border-border rounded-xl">
          <Zap className="mx-auto size-8 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground mb-3">No triggers yet</p>
          <Button size="sm" onClick={openCreateModal}>Create Trigger</Button>
        </div>
      )}

      {/* ── Create / Edit Dialog ── */}
      <Dialog open={isModalOpen} onOpenChange={(open) => { if (!open) closeModal(); }}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{showCreateModal ? 'Create Trigger' : 'Edit Trigger'}</DialogTitle>
          </DialogHeader>

          <form id="trigger-form" onSubmit={showCreateModal ? handleCreate : handleUpdate} className="space-y-4">
            {/* Name */}
            <div className="space-y-1.5">
              <Label>Name *</Label>
              <Input
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="e.g., Inject memory context on every message"
                required
              />
            </div>

            {/* Description */}
            <div className="space-y-1.5">
              <Label>Description</Label>
              <Textarea
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                rows={2}
                placeholder="What does this trigger do?"
              />
            </div>

            {/* Event Type + Condition Type */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Event Type *</Label>
                <Input
                  value={formData.event_type}
                  onChange={(e) => setFormData({ ...formData, event_type: e.target.value })}
                  placeholder="user.message"
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label>Condition Type</Label>
                <Select
                  value={formData.condition_type}
                  onValueChange={(v) => setFormData({ ...formData, condition_type: v })}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CONDITION_TYPES.map((ct) => (
                      <SelectItem key={ct} value={ct}>{ct}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* Condition Value + Field */}
            {needsConditionValue && (
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label>Condition Value</Label>
                  <Input
                    value={formData.condition_value}
                    onChange={(e) => setFormData({ ...formData, condition_value: e.target.value })}
                    placeholder="keyword / regex / jsonpath"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Condition Field</Label>
                  <Input
                    value={formData.condition_field}
                    onChange={(e) => setFormData({ ...formData, condition_field: e.target.value })}
                    placeholder="message"
                  />
                </div>
              </div>
            )}

            {/* Tool picker */}
            <div className="space-y-1.5">
              <Label>Tool Name *</Label>
              <ToolPicker
                value={formData.tool_name}
                onChange={handleToolChange}
                tools={allTools}
              />
              {selectedTool && (
                <p className="text-xs text-muted-foreground">{selectedTool.description}</p>
              )}
              {!selectedTool && formData.tool_name && (
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  Tool not found in registry — params will be saved as-is.
                </p>
              )}
            </div>

            {/* Dynamic param form */}
            {selectedTool && (
              <div className="rounded-lg border border-border bg-muted/30 p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                    Parameters
                  </p>
                  <span className="text-[10px] text-muted-foreground">workspace_id is auto-injected</span>
                </div>
                <ParamForm
                  fields={schemaFields}
                  values={paramValues}
                  onChange={handleParamChange}
                />
              </div>
            )}

            {/* Priority + Enabled */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Priority</Label>
                <Input
                  type="number"
                  value={formData.priority}
                  onChange={(e) => setFormData({ ...formData, priority: parseInt(e.target.value) || 0 })}
                />
              </div>
              <div className="flex items-end pb-1">
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={formData.enabled}
                    onCheckedChange={(checked) =>
                      setFormData({ ...formData, enabled: checked === true })
                    }
                  />
                  <span className="text-sm font-medium">Enabled</span>
                </label>
              </div>
            </div>
          </form>

          <DialogFooter>
            <Button variant="outline" onClick={closeModal}>Cancel</Button>
            <Button
              type="submit"
              form="trigger-form"
              disabled={createMutation.isPending || updateMutation.isPending}
            >
              {createMutation.isPending || updateMutation.isPending
                ? 'Saving...'
                : showCreateModal ? 'Create Trigger' : 'Update Trigger'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
