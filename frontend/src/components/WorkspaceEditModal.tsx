import { useState, useEffect, useRef } from 'react';
import { ChevronRight, ChevronLeft, Database, Wrench, Zap, History, Brain, Loader2, Save, Cpu, Globe } from 'lucide-react';
import { useToolList } from '@/hooks/useTools';
import { useKnowledgeList } from '@/hooks/useKnowledge';
import { useSkills } from '@/hooks/useSkills';
import { useWorkspaces, useUserContexts, useWorkspaceContexts, useUpdateWorkspace, useReinitWorkspaceContext } from '@/hooks/useWorkspaces';
import { useTemplates } from '@/hooks/useApps';
import type { Workspace, WorkspaceContextConfig } from '@/types/workspace';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';

interface Props {
  workspace: Workspace;
  onClose: () => void;
  onSaved?: () => void;
}

type Step = 'info' | 'resources';

interface ResourceItem {
  id: string;
  name: string;
  description?: string | null;
}

function ResourceSection({
  title,
  icon,
  items,
  selectedIds,
  onToggle,
}: {
  title: string;
  icon: React.ReactNode;
  items: ResourceItem[];
  selectedIds: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (items.length === 0) return null;

  const selectedCount = [...selectedIds].filter(id => items.some(i => i.id === id)).length;
  const allChecked = selectedCount === items.length;

  const handleSelectAll = () => {
    if (allChecked) {
      items.forEach(i => selectedIds.has(i.id) && onToggle(i.id));
    } else {
      items.forEach(i => !selectedIds.has(i.id) && onToggle(i.id));
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {icon}
        <span className="text-sm font-medium">{title}</span>
        {selectedCount > 0 && (
          <Badge variant="secondary" className="text-xs px-1.5 py-0 h-5">{selectedCount}</Badge>
        )}
        <button
          type="button"
          onClick={handleSelectAll}
          className="ml-auto text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          {allChecked ? 'Deselect all' : 'Select all'}
        </button>
      </div>
      <ScrollArea viewportClassName="max-h-36">
        <div className="space-y-1 pr-3">
          {items.map((item) => {
            const checked = selectedIds.has(item.id);
            return (
              <label
                key={item.id}
                className={cn(
                  'flex items-start gap-3 p-2.5 rounded-lg cursor-pointer border transition-all select-none',
                  checked
                    ? 'border-primary/50 bg-primary/5'
                    : 'border-border hover:border-border/80 hover:bg-muted/50'
                )}
              >
                <Checkbox
                  checked={checked}
                  onCheckedChange={() => onToggle(item.id)}
                  className="mt-0.5 shrink-0"
                />
                <div className="min-w-0">
                  <p className="text-sm font-medium leading-snug truncate">{item.name}</p>
                  {item.description && (
                    <p className="text-xs text-muted-foreground truncate mt-0.5">{item.description}</p>
                  )}
                </div>
              </label>
            );
          })}
        </div>
      </ScrollArea>
    </div>
  );
}

export default function WorkspaceEditModal({ workspace, onClose, onSaved }: Props) {
  const [step, setStep] = useState<Step>('info');
  const [name, setName] = useState(workspace.name);
  const [description, setDescription] = useState(workspace.description ?? '');
  const [selectedExecutorCode, setSelectedExecutorCode] = useState<string>(workspace.executor_code ?? '');

  const existingConfig = workspace.executor_config as Record<string, unknown> | null;
  const existingModel = existingConfig?.model as { name?: string; provider?: string } | undefined;
  const [modelName, setModelName] = useState<string>(existingModel?.name ?? '');
  const [modelProvider, setModelProvider] = useState<string>(existingModel?.provider ?? 'tongyi');
  const [globalEvent, setGlobalEvent] = useState<boolean>(
    existingConfig?.global_event !== undefined ? Boolean(existingConfig.global_event) : true
  );

  const { data: templates = [] } = useTemplates();
  const [selectedTools, setSelectedTools] = useState<Set<string>>(new Set());
  const [selectedKnowledge, setSelectedKnowledge] = useState<Set<string>>(new Set());
  const [selectedSkills, setSelectedSkills] = useState<Set<string>>(new Set());
  const [selectedSourceWorkspaces, setSelectedSourceWorkspaces] = useState<Set<string>>(new Set());
  const [selectedMemories, setSelectedMemories] = useState<Set<string>>(new Set());
  const preselected = useRef(false);

  const updateWorkspace = useUpdateWorkspace();
  const reinitContext = useReinitWorkspaceContext(workspace.id);

  // Available resources
  const { data: toolsData } = useToolList({ enabled_only: true, include_public: true });
  const { data: knowledgeData } = useKnowledgeList({ page: 1, page_size: 50 });
  const { data: skillsData } = useSkills({ page: 1, page_size: 50 });
  const { data: workspacesData } = useWorkspaces({ page: 1, page_size: 50 });
  const { data: memoriesData } = useUserContexts({ context_type: 'user_memory', page: 1, page_size: 50 });

  // Current workspace context items (to pre-select)
  const { data: currentContextData } = useWorkspaceContexts(workspace.id, { page: 1, page_size: 200 });

  const tools: ResourceItem[] = (toolsData?.tools ?? []).map((t: any) => ({
    id: t.id, name: t.display_name || t.name, description: t.description,
  }));
  const knowledge: ResourceItem[] = (knowledgeData?.items ?? []).map((k: any) => ({
    id: k.id, name: k.name, description: k.description,
  }));
  const skills: ResourceItem[] = (skillsData?.items ?? []).map((s: any) => ({
    id: s.id, name: s.name, description: s.description,
  }));
  const sourceWorkspaces: ResourceItem[] = (workspacesData?.items ?? [])
    .filter((w: any) => w.id !== workspace.id)
    .map((w: any) => ({
      id: w.id, name: w.name,
      description: w.description || `${w.run_count} run${w.run_count !== 1 ? 's' : ''}`,
    }));
  const memories: ResourceItem[] = ((memoriesData as any)?.items ?? []).map((m: any) => ({
    id: m.id,
    name: m.glance || m.summary?.slice(0, 60) || m.content?.slice(0, 60) || 'Memory',
    description: m.summary || m.content?.slice(0, 80),
  }));

  // Pre-select items that are currently in workspace context
  useEffect(() => {
    if (preselected.current || !currentContextData) return;
    preselected.current = true;

    const toolIds = new Set<string>();
    const knowledgeIds = new Set<string>();
    const skillIds = new Set<string>();
    const memoryIds = new Set<string>();

    for (const item of currentContextData.items) {
      const meta = item.meta as Record<string, string> | null;
      if (!meta) continue;
      if (meta.tool_id) toolIds.add(meta.tool_id);
      if (meta.knowledge_id) knowledgeIds.add(meta.knowledge_id);
      if (meta.skill_id) skillIds.add(meta.skill_id);
      if (meta.memory_id) memoryIds.add(meta.memory_id);
    }

    setSelectedTools(toolIds);
    setSelectedKnowledge(knowledgeIds);
    setSelectedSkills(skillIds);
    setSelectedMemories(memoryIds);
  }, [currentContextData]);

  const toggle = (set: Set<string>, setFn: (s: Set<string>) => void) => (id: string) => {
    const next = new Set(set);
    next.has(id) ? next.delete(id) : next.add(id);
    setFn(next);
  };

  const totalSelected =
    selectedTools.size + selectedKnowledge.size + selectedSkills.size +
    selectedSourceWorkspaces.size + selectedMemories.size;

  const totalItems =
    tools.length + knowledge.length + skills.length +
    sourceWorkspaces.length + memories.length;

  const allSelected = totalItems > 0 && totalSelected === totalItems;

  const handleSelectAll = () => {
    if (allSelected) {
      setSelectedTools(new Set());
      setSelectedKnowledge(new Set());
      setSelectedSkills(new Set());
      setSelectedSourceWorkspaces(new Set());
      setSelectedMemories(new Set());
    } else {
      setSelectedTools(new Set(tools.map(t => t.id)));
      setSelectedKnowledge(new Set(knowledge.map(k => k.id)));
      setSelectedSkills(new Set(skills.map(s => s.id)));
      setSelectedSourceWorkspaces(new Set(sourceWorkspaces.map(w => w.id)));
      setSelectedMemories(new Set(memories.map(m => m.id)));
    }
  };

  const hasNoResources =
    tools.length === 0 && knowledge.length === 0 && skills.length === 0 &&
    sourceWorkspaces.length === 0 && memories.length === 0;

  const isSubmitting = updateWorkspace.isPending || reinitContext.isPending;

  const handleSave = async () => {
    try {
      // Build executor_config from model + global_event
      const executorConfig: Record<string, unknown> = { global_event: globalEvent };
      if (modelName.trim()) {
        executorConfig.model = { name: modelName.trim(), provider: modelProvider };
      }

      await updateWorkspace.mutateAsync({
        workspaceId: workspace.id,
        data: {
          name: name.trim() || workspace.name,
          description: description || undefined,
          executor_code: selectedExecutorCode || undefined,
          executor_config: executorConfig,
        },
      });

      // Reinit context with new selection
      const config: WorkspaceContextConfig = {
        tool_ids: [...selectedTools],
        knowledge_ids: [...selectedKnowledge],
        skill_ids: [...selectedSkills],
        source_workspace_ids: [...selectedSourceWorkspaces],
        memory_ids: [...selectedMemories],
      };
      await reinitContext.mutateAsync(config);

      onSaved?.();
      onClose();
    } catch (err) {
      alert(`Save failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-lg p-0 gap-0 flex flex-col max-h-[90vh]" showCloseButton={false}>

        {/* Header */}
        <DialogHeader className="px-6 pt-6 pb-4 border-b border-border">
          <div className="flex items-start justify-between">
            <div>
              <DialogTitle className="text-base">Edit Workspace</DialogTitle>
              <p className="text-xs text-muted-foreground mt-0.5">
                Step {step === 'info' ? '1' : '2'} of 2 —{' '}
                {step === 'info' ? 'Name & executor' : 'Context resources'}
              </p>
            </div>
            <Button variant="ghost" size="icon" className="size-7 -mr-1 -mt-1" onClick={onClose}>
              <span className="sr-only">Close</span>✕
            </Button>
          </div>
          <div className="flex gap-2 mt-3">
            {(['info', 'resources'] as Step[]).map((s) => (
              <div
                key={s}
                className={cn(
                  'h-1 w-16 rounded-full transition-colors duration-300',
                  s === 'info' || step === 'resources' ? 'bg-primary' : 'bg-muted'
                )}
              />
            ))}
          </div>
        </DialogHeader>

        {/* Body */}
        <div className="px-6 py-5 overflow-y-auto flex-1 min-h-0">
          {step === 'info' ? (
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="ws-name">
                  Workspace Name <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="ws-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Workspace name"
                  autoFocus
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ws-desc">Description</Label>
                <Textarea
                  id="ws-desc"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={3}
                  placeholder="What is this workspace for?"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ws-executor" className="flex items-center gap-1.5">
                  <Cpu size={13} className="text-muted-foreground" />
                  Executor
                </Label>
                <select
                  id="ws-executor"
                  value={selectedExecutorCode}
                  onChange={(e) => setSelectedExecutorCode(e.target.value)}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="">— keep current —</option>
                  {templates.map((t) => (
                    <option key={t.executor_code} value={t.executor_code}>
                      {t.executor_name || t.executor_code}
                    </option>
                  ))}
                </select>
                {workspace.executor_code && (
                  <p className="text-xs text-muted-foreground">
                    Current: <span className="font-mono">{workspace.executor_code}</span>
                  </p>
                )}
              </div>

              {/* Model */}
              <div className="space-y-1.5">
                <Label className="flex items-center gap-1.5 text-sm font-medium">
                  Model
                </Label>
                <div className="flex gap-2">
                  <select
                    value={modelProvider}
                    onChange={(e) => setModelProvider(e.target.value)}
                    className="rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 w-32 shrink-0"
                  >
                    <option value="tongyi">tongyi</option>
                    <option value="ollama">ollama</option>
                  </select>
                  <Input
                    value={modelName}
                    onChange={(e) => setModelName(e.target.value)}
                    placeholder="e.g. qwen-plus"
                    className="flex-1"
                  />
                </div>
                <p className="text-xs text-muted-foreground">Leave blank to use the executor default.</p>
              </div>

              {/* Global Event */}
              <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <Globe size={13} className="text-muted-foreground shrink-0" />
                  <div>
                    <p className="text-sm font-medium leading-none">Global Event</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Load conversation history from all runs in this workspace.
                    </p>
                  </div>
                </div>
                <Checkbox
                  checked={globalEvent}
                  onCheckedChange={(v) => setGlobalEvent(Boolean(v))}
                />
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm text-muted-foreground">
                  Choose which resources are available in this workspace's context.
                </p>
                {!hasNoResources && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="shrink-0 h-7 px-2 text-xs"
                    onClick={handleSelectAll}
                  >
                    {allSelected ? 'Deselect All' : 'Select All'}
                  </Button>
                )}
              </div>

              {hasNoResources ? (
                <p className="text-sm text-muted-foreground text-center py-6">
                  No resources available yet.
                </p>
              ) : (
                <div className="space-y-4">
                  <ResourceSection
                    title="Tools"
                    icon={<Wrench size={14} className="text-blue-500" />}
                    items={tools}
                    selectedIds={selectedTools}
                    onToggle={toggle(selectedTools, setSelectedTools)}
                  />
                  {tools.length > 0 && knowledge.length > 0 && <Separator />}
                  <ResourceSection
                    title="Knowledge Bases"
                    icon={<Database size={14} className="text-green-500" />}
                    items={knowledge}
                    selectedIds={selectedKnowledge}
                    onToggle={toggle(selectedKnowledge, setSelectedKnowledge)}
                  />
                  {(tools.length > 0 || knowledge.length > 0) && skills.length > 0 && <Separator />}
                  <ResourceSection
                    title="Skills"
                    icon={<Zap size={14} className="text-purple-500" />}
                    items={skills}
                    selectedIds={selectedSkills}
                    onToggle={toggle(selectedSkills, setSelectedSkills)}
                  />
                  {sourceWorkspaces.length > 0 && <Separator />}
                  <ResourceSection
                    title="Import History From"
                    icon={<History size={14} className="text-orange-500" />}
                    items={sourceWorkspaces}
                    selectedIds={selectedSourceWorkspaces}
                    onToggle={toggle(selectedSourceWorkspaces, setSelectedSourceWorkspaces)}
                  />
                  {memories.length > 0 && <Separator />}
                  <ResourceSection
                    title="Memories"
                    icon={<Brain size={14} className="text-pink-500" />}
                    items={memories}
                    selectedIds={selectedMemories}
                    onToggle={toggle(selectedMemories, setSelectedMemories)}
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <DialogFooter className="px-6 py-4 border-t border-border flex-row justify-between sm:justify-between">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={step === 'info' ? onClose : () => setStep('info')}
            disabled={isSubmitting}
          >
            {step === 'resources' && <ChevronLeft className="size-3.5" />}
            {step === 'info' ? 'Cancel' : 'Back'}
          </Button>

          {step === 'info' ? (
            <Button
              type="button"
              size="sm"
              disabled={!name.trim()}
              onClick={() => setStep('resources')}
            >
              Next
              <ChevronRight className="size-3.5" />
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              disabled={isSubmitting}
              onClick={handleSave}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  Saving…
                </>
              ) : (
                <>
                  <Save className="size-3.5" />
                  Save Changes
                  {totalSelected > 0 && (
                    <Badge className="ml-1 h-4 px-1 text-[10px] bg-white/20 text-white hover:bg-white/20">
                      {totalSelected}
                    </Badge>
                  )}
                </>
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
