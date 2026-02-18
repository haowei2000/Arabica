import { useState } from 'react';
import { GitBranch, Loader2, Trash2, Pencil, X } from 'lucide-react';
import { ViewToggle, type ViewMode } from '@/components/ViewToggle';
import { AccordionItem } from '@/components/AccordionItem';
import { formatRelativeTime } from '@/utils/formatDate';
import { useTriggers, useCreateTrigger, useUpdateTrigger, useDeleteTrigger } from '@/hooks/useTriggers';
import { useToolList } from '@/hooks/useTools';
import { useKnowledgeList } from '@/hooks/useKnowledge';
import { useSkills } from '@/hooks/useSkills';
import { useUserContexts } from '@/hooks/useWorkspaces';
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

// ─── Constants ────────────────────────────────────────────────────────────────

const CONDITION_TYPES = ['always', 'keyword', 'regex', 'jsonpath'] as const;
const ACTION_TYPES = [
  'glance_context',
  'list_context',
  'read_context',
  'glob_context',
  'search_context',
] as const;

type ContextCategory = 'tools' | 'knowledge' | 'skills' | 'memory';
const CONTEXT_CATEGORIES: ContextCategory[] = ['tools', 'knowledge', 'skills', 'memory'];

const CATEGORY_COLOR: Record<ContextCategory, string> = {
  tools:     'bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-800',
  knowledge: 'bg-green-100 text-green-700 border-green-200 dark:bg-green-900/30 dark:text-green-300 dark:border-green-800',
  skills:    'bg-purple-100 text-purple-700 border-purple-200 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-800',
  memory:    'bg-pink-100 text-pink-700 border-pink-200 dark:bg-pink-900/30 dark:text-pink-300 dark:border-pink-800',
};

// What param key each action_type uses
const ACTION_PARAM_KEY: Record<string, string> = {
  glance_context: 'prefix',
  list_context:   'prefix',
  read_context:   'path',
  glob_context:   'pattern',
  search_context: 'query',
};

// Whether this action type benefits from the two-level context picker
const USE_CONTEXT_PICKER: Record<string, boolean> = {
  glance_context: true,
  list_context:   true,
  read_context:   true,
  glob_context:   true,
  search_context: false,
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

interface ContextItem { path: string; name: string; description?: string | null }

/** Detect which category a contextParam belongs to */
function getCategoryFromParam(param: string): ContextCategory | null {
  const bare = param.replace('/*', '');
  for (const cat of CONTEXT_CATEGORIES) {
    if (bare === cat || bare.startsWith(`${cat}/`)) return cat;
  }
  return null;
}

/** Given a category, build the "all items in category" default param value */
function allCategoryParam(cat: ContextCategory, actionType: string): string {
  return actionType === 'glob_context' ? `${cat}/*` : cat;
}

/** Extract the param string from saved action_params */
function extractParam(action_params: Record<string, unknown> | null | undefined, action_type: string): string {
  if (!action_params) return '';
  const key = ACTION_PARAM_KEY[action_type];
  return key ? String(action_params[key] ?? '') : '';
}

/** Build action_params object from the structured param value */
function buildParams(value: string, action_type: string): Record<string, unknown> | undefined {
  const key = ACTION_PARAM_KEY[action_type];
  if (!key || !value.trim()) return undefined;
  return { [key]: value.trim() };
}

// ─── Context Picker sub-component ─────────────────────────────────────────────

function ContextPicker({
  actionType,
  value,
  onChange,
  categoryItems,
}: {
  actionType: string;
  value: string;
  onChange: (v: string) => void;
  categoryItems: Record<ContextCategory, ContextItem[]>;
}) {
  const activeCategory = getCategoryFromParam(value);

  const handleCategoryClick = (cat: ContextCategory) => {
    if (activeCategory === cat) {
      onChange(''); // deselect
    } else {
      onChange(allCategoryParam(cat, actionType));
    }
  };

  const handleItemClick = (path: string) => {
    const newVal = actionType === 'glob_context' ? path : path;
    onChange(newVal === value ? allCategoryParam(activeCategory!, actionType) : newVal);
  };

  const paramKey = ACTION_PARAM_KEY[actionType] ?? 'prefix';
  const items = activeCategory ? categoryItems[activeCategory] : [];

  return (
    <div className="space-y-3 rounded-lg border border-border bg-muted/30 p-4">
      <Label>{paramKey.charAt(0).toUpperCase() + paramKey.slice(1)}</Label>

      {/* Level 1 — category chips */}
      <div className="flex flex-wrap gap-1.5">
        {CONTEXT_CATEGORIES.map((cat) => {
          const isActive = activeCategory === cat;
          return (
            <button
              key={cat}
              type="button"
              onClick={() => handleCategoryClick(cat)}
              className={cn(
                'px-3 py-1 text-xs rounded-full border font-medium transition-all',
                isActive
                  ? CATEGORY_COLOR[cat]
                  : 'bg-background border-border text-muted-foreground hover:border-foreground/30'
              )}
            >
              {cat}
            </button>
          );
        })}
      </div>

      {/* Level 2 — items within selected category */}
      {activeCategory && (
        <div className="rounded-md border border-border overflow-hidden">
          {/* "All [category]" option */}
          {(() => {
            const allVal = allCategoryParam(activeCategory, actionType);
            const isSelected = value === allVal;
            return (
              <button
                type="button"
                onClick={() => onChange(allVal)}
                className={cn(
                  'w-full flex items-center gap-3 px-3 py-2 text-left text-sm transition-colors border-b border-border',
                  isSelected
                    ? cn(CATEGORY_COLOR[activeCategory], 'font-medium')
                    : 'bg-card hover:bg-muted/50'
                )}
              >
                <span className={cn('size-2 rounded-full shrink-0', isSelected ? 'bg-current' : 'bg-border')} />
                <span className="flex-1 font-medium">All {activeCategory}</span>
                <span className="text-xs font-mono opacity-60">{allVal}</span>
              </button>
            );
          })()}

          {/* Specific items */}
          {items.length > 0 ? (
            <ScrollArea viewportClassName="max-h-48">
              <div>
                {items.map((item) => {
                  const isSelected = value === item.path;
                  return (
                    <button
                      key={item.path}
                      type="button"
                      onClick={() => handleItemClick(item.path)}
                      className={cn(
                        'w-full flex items-center gap-3 px-3 py-2 text-left text-sm transition-colors border-b border-border last:border-0',
                        isSelected
                          ? cn(CATEGORY_COLOR[activeCategory], 'font-medium')
                          : 'bg-card hover:bg-muted/50'
                      )}
                    >
                      <span className={cn('size-2 rounded-full shrink-0', isSelected ? 'bg-current' : 'bg-muted-foreground/30')} />
                      <div className="flex-1 min-w-0">
                        <p className="truncate font-medium leading-tight">{item.name}</p>
                        {item.description && (
                          <p className="text-xs text-muted-foreground truncate mt-0.5">{item.description}</p>
                        )}
                      </div>
                      <span className="text-xs font-mono opacity-50 shrink-0 max-w-32 truncate">{item.path}</span>
                    </button>
                  );
                })}
              </div>
            </ScrollArea>
          ) : (
            <p className="text-xs text-muted-foreground px-3 py-2 bg-card">No items found.</p>
          )}
        </div>
      )}

      {/* Manual input / current value display */}
      <div className="flex items-center gap-2">
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="or type a custom path…"
          className="font-mono text-sm flex-1"
        />
        {value && (
          <Button type="button" variant="ghost" size="icon" className="size-8 shrink-0" onClick={() => onChange('')}>
            <X className="size-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────

const INITIAL_FORM: TriggerCreate = {
  name: '',
  description: '',
  event_type: 'user.message',
  condition_type: 'always',
  condition_value: '',
  condition_field: 'message',
  action_type: 'glance_context',
  action_params: undefined,
  priority: 0,
  enabled: true,
};

export default function TriggerPage() {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>('card');
  const [openItemId, setOpenItemId] = useState<string | null>(null);

  const handleModeToggle = (m: ViewMode) => {
    setViewMode(m);
    if (m === 'list') setOpenItemId(null);
  };
  const [editingTrigger, setEditingTrigger] = useState<Trigger | null>(null);
  const [formData, setFormData] = useState<TriggerCreate>(INITIAL_FORM);
  const [contextParam, setContextParam] = useState('');
  const [searchQuery, setSearchQuery] = useState(''); // for search_context action

  // Always fetch — needed for the context picker when modal opens
  const { data: toolsData }     = useToolList({ enabled_only: true, include_public: true });
  const { data: knowledgeData } = useKnowledgeList({ page: 1, page_size: 100 });
  const { data: skillsData }    = useSkills({ page: 1, page_size: 100 });
  const { data: memoriesData }  = useUserContexts({ context_type: 'user_memory', page: 1, page_size: 50 });

  const { data: triggersData, isLoading } = useTriggers({ page: 1, page_size: 100 });
  const createMutation = useCreateTrigger();
  const updateMutation = useUpdateTrigger();
  const deleteMutation = useDeleteTrigger();

  const triggers = triggersData?.items ?? [];

  // Build category → items map for the picker
  const categoryItems: Record<ContextCategory, ContextItem[]> = {
    tools: (toolsData?.tools ?? []).map((t: any) => ({
      path: `tools/${t.tool_code || t.id}`,
      name: t.display_name || t.name,
      description: t.description,
    })),
    knowledge: (knowledgeData?.items ?? []).map((k: any) => ({
      path: `knowledge/${k.id}`,
      name: k.name,
      description: k.description,
    })),
    skills: (skillsData?.items ?? []).map((s: any) => ({
      path: `skills/${s.id}`,
      name: s.name,
      description: s.description,
    })),
    memory: ((memoriesData as any)?.items ?? []).map((m: any) => ({
      path: `memory/${m.id}`,
      name: m.glance || m.summary?.slice(0, 60) || 'Memory',
      description: m.summary?.slice(0, 80),
    })),
  };

  const openCreateModal = () => {
    setFormData(INITIAL_FORM);
    setContextParam('');
    setSearchQuery('');
    setShowCreateModal(true);
  };

  const openEditModal = (trigger: Trigger) => {
    setEditingTrigger(trigger);
    const param = extractParam(trigger.action_params, trigger.action_type);
    setFormData({
      name: trigger.name,
      description: trigger.description || '',
      event_type: trigger.event_type,
      condition_type: trigger.condition_type,
      condition_value: trigger.condition_value || '',
      condition_field: trigger.condition_field || 'message',
      action_type: trigger.action_type,
      action_params: trigger.action_params || undefined,
      priority: trigger.priority,
      enabled: trigger.enabled,
    });
    if (trigger.action_type === 'search_context') {
      setSearchQuery(param);
      setContextParam('');
    } else {
      setContextParam(param);
      setSearchQuery('');
    }
    setShowEditModal(true);
  };

  const closeModal = () => {
    setShowCreateModal(false);
    setShowEditModal(false);
    setFormData(INITIAL_FORM);
    setEditingTrigger(null);
    setContextParam('');
    setSearchQuery('');
  };

  const handleActionTypeChange = (v: string) => {
    setFormData({ ...formData, action_type: v });
    // Reset params when switching to/from search_context
    setContextParam('');
    setSearchQuery('');
  };

  const getActionParams = () => {
    if (formData.action_type === 'search_context') {
      return buildParams(searchQuery, 'search_context');
    }
    return buildParams(contextParam, formData.action_type);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createMutation.mutateAsync({ ...formData, action_params: getActionParams() });
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
        data: { ...formData, action_params: getActionParams() },
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
  const useContextPicker = USE_CONTEXT_PICKER[formData.action_type];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold">Triggers</h2>
          <span className="text-xs text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">
            {triggers.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <ViewToggle mode={viewMode} onToggle={handleModeToggle} />
          <Button size="sm" onClick={openCreateModal}>+ New</Button>
        </div>
      </div>

      {triggers.length > 0 ? viewMode === 'card' ? (
        /* ── Card grid ── */
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {triggers.map((trigger) => {
            const paramVal = extractParam(trigger.action_params, trigger.action_type);
            const cat = getCategoryFromParam(paramVal) as ContextCategory | null;
            return (
              <div key={trigger.id} className="rounded-xl border border-border bg-card p-4 flex flex-col gap-2.5 hover:bg-muted/20 transition-colors">
                <div className="flex items-start gap-2.5">
                  <span className={cn('size-2 rounded-full mt-1 shrink-0', trigger.enabled ? 'bg-green-500' : 'bg-muted-foreground/30')} />
                  <p className="text-sm font-semibold leading-snug flex-1 min-w-0 truncate">{trigger.name}</p>
                </div>
                {trigger.description && <p className="text-xs text-muted-foreground line-clamp-2">{trigger.description}</p>}
                <div className="flex flex-wrap gap-1.5">
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 font-mono">
                    {trigger.event_type}
                  </span>
                  {trigger.condition_type !== 'always' && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 border border-orange-200/70 dark:bg-orange-900/30 dark:text-orange-300 dark:border-orange-800/50">
                      {trigger.condition_type}
                    </span>
                  )}
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 border border-amber-200/70 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800/50">
                    {trigger.action_type}
                  </span>
                </div>
                {paramVal && (
                  <span className={cn(
                    'text-[10px] px-2 py-0.5 rounded-full border font-mono self-start max-w-full truncate',
                    cat ? CATEGORY_COLOR[cat] : 'bg-muted text-muted-foreground border-border/50'
                  )}>
                    {paramVal}
                  </span>
                )}
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
            );
          })}
        </div>
      ) : (
        /* ── List / Drawer ── */
        <div className="rounded-xl border border-border bg-card overflow-visible divide-y divide-border/50">
          {triggers.map((trigger) => {
            const paramVal = extractParam(trigger.action_params, trigger.action_type);
            const cat = getCategoryFromParam(paramVal) as ContextCategory | null;

            const statusDot = (
              <span className={cn('size-2 rounded-full shrink-0', trigger.enabled ? 'bg-green-500' : 'bg-muted-foreground/30')} />
            );
            const chips = (
              <>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 shrink-0 font-mono">
                  {trigger.event_type}
                </span>
                {trigger.condition_type !== 'always' && (
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-orange-100 text-orange-700 border border-orange-200/70 dark:bg-orange-900/30 dark:text-orange-300 dark:border-orange-800/50 shrink-0">
                    {trigger.condition_type}
                  </span>
                )}
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 border border-amber-200/70 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800/50 shrink-0">
                  {trigger.action_type}
                </span>
                {paramVal && (
                  <span className={cn(
                    'text-[10px] px-2 py-0.5 rounded-full border font-mono shrink-0 max-w-32 truncate',
                    cat ? CATEGORY_COLOR[cat] : 'bg-muted text-muted-foreground border-border/50'
                  )}>
                    {paramVal}
                  </span>
                )}
              </>
            );

            const rowHeader = (
              <>{statusDot}<span className="text-sm font-medium flex-1 min-w-0 truncate">{trigger.name}</span>{chips}</>
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
                  {/* Hover tooltip */}
                  <div className="absolute right-2 top-full mt-1 z-50 w-72 rounded-xl border border-border bg-card shadow-lg shadow-black/10 p-3 invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-[opacity,visibility] duration-150 pointer-events-none">
                    <div className="space-y-2 text-xs">
                      {trigger.description && <p className="text-foreground/80 leading-relaxed">{trigger.description}</p>}
                      <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pt-1.5 border-t border-border/50 text-muted-foreground">
                        <span>Event</span><span className="text-foreground font-mono">{trigger.event_type}</span>
                        <span>Condition</span><span className="text-foreground">{trigger.condition_type}</span>
                        {trigger.condition_value && (<><span>Match</span><span className="text-foreground font-mono truncate">{trigger.condition_value}</span></>)}
                        <span>Action</span><span className="text-foreground">{trigger.action_type}</span>
                        {paramVal && (<><span>{ACTION_PARAM_KEY[trigger.action_type] ?? 'param'}</span><span className="text-foreground font-mono truncate">{paramVal}</span></>)}
                        <span>Priority</span><span className="text-foreground tabular-nums">{trigger.priority}</span>
                        <span>Status</span><span className="text-foreground">{trigger.enabled ? 'Enabled' : 'Disabled'}</span>
                        <span>Created</span><span className="text-foreground">{new Date(trigger.created_at).toLocaleDateString()}</span>
                      </div>
                    </div>
                  </div>
                </div>
              );
            }

            // Drawer mode
            return (
              <AccordionItem
                key={trigger.id}
                isOpen={openItemId === trigger.id}
                onToggle={() => setOpenItemId(openItemId === trigger.id ? null : trigger.id)}
                header={rowHeader}
                detail={
                  <div className="space-y-3">
                    {trigger.description && <p className="text-xs text-foreground/80 leading-relaxed">{trigger.description}</p>}
                    <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                      <span className="text-muted-foreground">Event</span><span className="font-mono">{trigger.event_type}</span>
                      <span className="text-muted-foreground">Condition</span><span>{trigger.condition_type}</span>
                      {trigger.condition_value && (<><span className="text-muted-foreground">Match</span><span className="font-mono">{trigger.condition_value}</span></>)}
                      {trigger.condition_field && trigger.condition_field !== 'message' && (<><span className="text-muted-foreground">Field</span><span className="font-mono">{trigger.condition_field}</span></>)}
                      <span className="text-muted-foreground">Action</span><span>{trigger.action_type}</span>
                      {paramVal && (<><span className="text-muted-foreground">{ACTION_PARAM_KEY[trigger.action_type] ?? 'param'}</span><span className="font-mono">{paramVal}</span></>)}
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
          <GitBranch className="mx-auto size-8 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground mb-3">No triggers yet</p>
          <Button size="sm" onClick={openCreateModal}>Create Trigger</Button>
        </div>
      )}

      {/* Create / Edit Dialog */}
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
                placeholder="e.g., Inject Tools on Message"
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
                  placeholder="e.g., user.message"
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

            {/* Action Type */}
            <div className="space-y-1.5">
              <Label>Action Type *</Label>
              <Select value={formData.action_type} onValueChange={handleActionTypeChange}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {ACTION_TYPES.map((at) => (
                    <SelectItem key={at} value={at}>{at}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Context picker — two-level for prefix/path/pattern actions */}
            {useContextPicker && (
              <ContextPicker
                actionType={formData.action_type}
                value={contextParam}
                onChange={setContextParam}
                categoryItems={categoryItems}
              />
            )}

            {/* Query input for search_context */}
            {formData.action_type === 'search_context' && (
              <div className="space-y-1.5 rounded-lg border border-border bg-muted/30 p-4">
                <Label>Search Query</Label>
                <Input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="e.g., web search tool"
                  className="font-mono text-sm"
                />
                <p className="text-xs text-muted-foreground">
                  Static query injected into the context search at trigger time.
                </p>
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
