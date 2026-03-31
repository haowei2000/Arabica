import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Library, FileText, Loader2, Trash2, FolderOpen, Brain, Search } from 'lucide-react';
import { useKnowledgeList, useCreateKnowledge, useDeleteKnowledge } from '@/hooks/useKnowledge';
import { generateKnowledgeDocumentsRoute } from '@/constants/routes';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { ViewToggle, type ViewMode } from '@/components/ViewToggle';
import { AccordionItem } from '@/components/AccordionItem';
import { ContextViewer } from '@/components/ContextViewer';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/formatDate';
import type { KnowledgeCreate } from '@/types/knowledge';

const INITIAL_FORM: KnowledgeCreate = {
  name: '',
  description: '',
  provider: 'default',
  indexing_technique: 'high_quality',
  permission: 'private',
};

const STATUS_DOT: Record<string, string> = {
  active:   'bg-green-500',
  inactive: 'bg-muted-foreground/30',
  indexing: 'bg-yellow-400',
  error:    'bg-red-500',
};

export default function KnowledgePage() {
  const navigate = useNavigate();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [formData, setFormData] = useState<KnowledgeCreate>(INITIAL_FORM);
  const [viewMode, setViewMode] = useState<ViewMode>('card');
  const [openItemId, setOpenItemId] = useState<string | null>(null);
  const [contextViewId, setContextViewId] = useState<string | null>(null);
  const { data: knowledgeData, isLoading } = useKnowledgeList();
  const contextViewItem = contextViewId ? (knowledgeData?.items ?? []).find((k) => k.id === contextViewId) : null;
  const createMutation = useCreateKnowledge();
  const deleteMutation = useDeleteKnowledge();

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createMutation.mutateAsync(formData);
      setShowCreateForm(false);
      setFormData(INITIAL_FORM);
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleDelete = async (id: string, name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete "${name}"?`)) return;
    try {
      await deleteMutation.mutateAsync(id);
    } catch (error) {
      alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleModeToggle = (m: ViewMode) => {
    setViewMode(m);
    if (m !== 'drawer') setOpenItemId(null);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const items = knowledgeData?.items ?? [];

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Library className="size-3.5 text-muted-foreground" />
            <h2 className="text-sm font-semibold">Knowledge</h2>
          </div>
          <span className="text-xs text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">
            {items.length}{knowledgeData && knowledgeData.total > items.length ? `/${knowledgeData.total}` : ''}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <ViewToggle mode={viewMode} onToggle={handleModeToggle} />
          <Button size="sm" onClick={() => setShowCreateForm(true)}>+ New</Button>
        </div>
      </div>

      {items.length > 0 ? viewMode === 'card' ? (
        /* ── Card grid ── */
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {items.map((kb) => {
            const statusDotCls = STATUS_DOT[kb.status] ?? 'bg-muted-foreground/30';
            return (
              <div key={kb.id} className="rounded-xl border border-border bg-card p-4 flex flex-col gap-2.5 hover:bg-muted/20 transition-colors">
                <div className="flex items-start gap-2.5">
                  <span className={cn('size-2 rounded-full mt-1 shrink-0', statusDotCls)} />
                  <p className="text-sm font-semibold leading-snug flex-1 min-w-0 truncate">{kb.name}</p>
                </div>
                {kb.description && <p className="text-xs text-muted-foreground line-clamp-2">{kb.description}</p>}
                <div className="flex flex-wrap gap-1.5">
                  <span className={cn('text-[10px] px-2 py-0.5 rounded-full border',
                    kb.permission === 'public'
                      ? 'bg-green-100 text-green-700 border-green-200/70 dark:bg-green-900/30 dark:text-green-300 dark:border-green-800/50'
                      : 'bg-muted text-muted-foreground border-border/50'
                  )}>{kb.permission}</span>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 capitalize">{kb.status}</span>
                </div>
                <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                  <span className="inline-flex items-center gap-0.5 tabular-nums"><FileText className="size-2.5" />{kb.document_count}</span>
                  <span className="tabular-nums">{kb.chunk_count} chunks</span>
                  <span className="ml-auto">{formatRelativeTime(kb.created_at)}</span>
                </div>
                <div className="flex gap-2 pt-2 border-t border-border/40 mt-auto">
                  <Button size="sm" variant="outline" className="flex-1 gap-1.5" onClick={() => navigate(generateKnowledgeDocumentsRoute(kb.id))}>
                    <FolderOpen className="size-3.5" />Open
                  </Button>
                  <Button size="sm" variant="outline" className="gap-1.5" title="Search inside"
                    onClick={() => navigate(`${generateKnowledgeDocumentsRoute(kb.id)}?search=true`)}>
                    <Search className="size-3.5" />
                  </Button>
                  <Button size="sm" variant="outline" className="gap-1.5" title="View Context" onClick={(e) => { e.stopPropagation(); setContextViewId(kb.id); }}>
                    <Brain className="size-3.5" />
                  </Button>
                  <Button size="sm" variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                    disabled={deleteMutation.isPending} onClick={(e) => handleDelete(kb.id, kb.name, e)}>
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-card overflow-visible divide-y divide-border/50">
          {items.map((kb) => {
            const statusDotCls = STATUS_DOT[kb.status] ?? 'bg-muted-foreground/30';
            const permissionChip = (
              <span className={cn(
                'text-[10px] px-2 py-0.5 rounded-full border shrink-0',
                kb.permission === 'public'
                  ? 'bg-green-100 text-green-700 border-green-200/70 dark:bg-green-900/30 dark:text-green-300 dark:border-green-800/50'
                  : 'bg-muted text-muted-foreground border-border/50'
              )}>
                {kb.permission}
              </span>
            );

            const rowHeader = (
              <>
                <span className={cn('size-2 rounded-full shrink-0', statusDotCls)} />
                <span className="text-sm font-medium flex-1 min-w-0 truncate">{kb.name}</span>
                {permissionChip}
                <span className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground/60 tabular-nums shrink-0"><FileText className="size-2.5" />{kb.document_count}</span>
              </>
            );

            if (viewMode === 'list') {
              return (
                <div
                  key={kb.id}
                  className="group relative flex items-center gap-3 px-4 py-3 hover:bg-muted/40 transition-colors cursor-pointer"
                  onClick={() => navigate(generateKnowledgeDocumentsRoute(kb.id))}
                >
                  <span className={cn('size-2 rounded-full shrink-0', statusDotCls)} />
                  <span className="text-sm font-medium flex-1 min-w-0 truncate">{kb.name}</span>
                  {permissionChip}
                  <span className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground/60 tabular-nums shrink-0"><FileText className="size-2.5" />{kb.document_count}</span>
                  <FolderOpen className="size-3.5 text-muted-foreground/40 group-hover:text-muted-foreground/70 transition-colors shrink-0" />
                  <button
                    type="button"
                    title="Search"
                    className="size-7 flex items-center justify-center rounded opacity-0 group-hover:opacity-100 transition-opacity hover:bg-muted shrink-0"
                    onClick={(e) => { e.stopPropagation(); navigate(`${generateKnowledgeDocumentsRoute(kb.id)}?search=true`); }}
                  >
                    <Search className="size-3.5 text-muted-foreground" />
                  </button>
                  <button
                    type="button"
                    title="Context"
                    className="size-7 flex items-center justify-center rounded opacity-0 group-hover:opacity-100 transition-opacity hover:bg-muted shrink-0"
                    onClick={(e) => { e.stopPropagation(); setContextViewId(kb.id); }}
                  >
                    <Brain className="size-3.5 text-muted-foreground" />
                  </button>
                  <button
                    type="button"
                    className="size-7 flex items-center justify-center rounded opacity-0 group-hover:opacity-100 transition-opacity hover:bg-destructive/10 shrink-0"
                    onClick={(e) => handleDelete(kb.id, kb.name, e)}
                  >
                    <Trash2 className="size-3.5 text-destructive/70" />
                  </button>
                  {/* Hover tooltip */}
                  <div className="absolute right-2 top-full mt-1 z-50 w-72 rounded-xl border border-border bg-card shadow-lg shadow-black/10 p-3 invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-[opacity,visibility] duration-150 pointer-events-none">
                    <div className="space-y-2 text-xs">
                      {kb.description && <p className="text-foreground/80 leading-relaxed">{kb.description}</p>}
                      <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pt-1.5 border-t border-border/50 text-muted-foreground">
                        <span>Status</span><span className="text-foreground capitalize">{kb.status}</span>
                        <span>Indexing</span><span className="text-foreground">{kb.indexing_technique}</span>
                        <span>Documents</span><span className="text-foreground tabular-nums">{kb.document_count}</span>
                        <span>Chunks</span><span className="text-foreground tabular-nums">{kb.chunk_count}</span>
                        <span>Created</span><span className="text-foreground">{formatRelativeTime(kb.created_at)}</span>
                      </div>
                    </div>
                  </div>
                </div>
              );
            }

            // Drawer mode
            return (
              <AccordionItem
                key={kb.id}
                isOpen={openItemId === kb.id}
                onToggle={() => setOpenItemId(openItemId === kb.id ? null : kb.id)}
                header={rowHeader}
                detail={
                  <div className="space-y-3">
                    {kb.description && (
                      <p className="text-xs text-foreground/80 leading-relaxed">{kb.description}</p>
                    )}
                    <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                      <span className="text-muted-foreground">Status</span>
                      <span className="flex items-center gap-1.5">
                        <span className={cn('size-1.5 rounded-full', statusDotCls)} />
                        <span className="capitalize">{kb.status}</span>
                      </span>
                      <span className="text-muted-foreground">Indexing</span>
                      <span>{kb.indexing_technique}</span>
                      <span className="text-muted-foreground">Permission</span>
                      <span>{kb.permission}</span>
                      <span className="text-muted-foreground">Documents</span>
                      <span className="tabular-nums">{kb.document_count}</span>
                      <span className="text-muted-foreground">Chunks</span>
                      <span className="tabular-nums">{kb.chunk_count}</span>
                      <span className="text-muted-foreground">Created</span>
                      <span>{formatRelativeTime(kb.created_at)}</span>
                    </div>
                    <div className="flex gap-2 pt-2 border-t border-border/40">
                      <Button
                        size="sm"
                        variant="outline"
                        className="flex-1 gap-1.5"
                        onClick={() => navigate(generateKnowledgeDocumentsRoute(kb.id))}
                      >
                        <FolderOpen className="size-3.5" />
                        Open Documents
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1.5"
                        onClick={() => navigate(`${generateKnowledgeDocumentsRoute(kb.id)}?search=true`)}
                      >
                        <Search className="size-3.5" />
                        Search
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1.5"
                        onClick={() => setContextViewId(kb.id)}
                      >
                        <Brain className="size-3.5" />
                        Context
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                        disabled={deleteMutation.isPending}
                        onClick={(e) => handleDelete(kb.id, kb.name, e)}
                      >
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
          <Library className="mx-auto size-8 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground mb-3">No knowledge bases yet</p>
          <Button size="sm" onClick={() => setShowCreateForm(true)}>Create Knowledge Base</Button>
        </div>
      )}

      {/* Context Viewer */}
      {contextViewId && contextViewItem && (
        <ContextViewer
          open={!!contextViewId}
          onClose={() => setContextViewId(null)}
          entityType="knowledge"
          entityId={contextViewId}
          entityName={contextViewItem.name}
        />
      )}

      {/* Create Dialog */}
      <Dialog open={showCreateForm} onOpenChange={(open) => { if (!open) { setShowCreateForm(false); setFormData(INITIAL_FORM); } }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>New Knowledge Base</DialogTitle>
          </DialogHeader>
          <form id="create-knowledge-form" onSubmit={handleCreate} className="space-y-4">
            <div className="space-y-1.5">
              <Label>Name *</Label>
              <Input value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} placeholder="e.g., Product Documentation" required />
            </div>
            <div className="space-y-1.5">
              <Label>Description</Label>
              <Textarea value={formData.description || ''} onChange={(e) => setFormData({ ...formData, description: e.target.value })} placeholder="Describe the purpose of this knowledge base" rows={3} />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Indexing</Label>
                <Select value={formData.indexing_technique} onValueChange={(v) => setFormData({ ...formData, indexing_technique: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="high_quality">High Quality</SelectItem>
                    <SelectItem value="economy">Economy</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Permission</Label>
                <Select value={formData.permission} onValueChange={(v) => setFormData({ ...formData, permission: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="private">Private</SelectItem>
                    <SelectItem value="public">Public</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </form>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setShowCreateForm(false); setFormData(INITIAL_FORM); }}>Cancel</Button>
            <Button type="submit" form="create-knowledge-form" disabled={createMutation.isPending}>
              {createMutation.isPending ? 'Creating...' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
