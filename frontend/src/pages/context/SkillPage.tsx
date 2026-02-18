import { useState } from 'react';
import { Sparkles, Loader2, Trash2, Pencil, CheckCircle2, Circle } from 'lucide-react';
import { useSkills, useCreateSkill, useUpdateSkill, useDeleteSkill } from '@/hooks/useSkills';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { ViewToggle, type ViewMode } from '@/components/ViewToggle';
import { AccordionItem } from '@/components/AccordionItem';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/formatDate';
import type { SkillCreate, Skill } from '@/types/skill';

const INITIAL_FORM: SkillCreate = { name: '', description: '', content: '', tags: [] };

export default function SkillPage() {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [formData, setFormData] = useState<SkillCreate>(INITIAL_FORM);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');
  const [viewMode, setViewMode] = useState<ViewMode>('card');
  const [openItemId, setOpenItemId] = useState<string | null>(null);

  const { data: skillData, isLoading } = useSkills({
    tags: selectedTags.length > 0 ? selectedTags.join(',') : undefined,
  });
  const createMutation = useCreateSkill();
  const updateMutation = useUpdateSkill();
  const deleteMutation = useDeleteSkill();

  const skills = skillData?.items ?? [];
  const allTags = Array.from(new Set(skills.flatMap((s) => s.tags ?? []))).sort();

  const addTagToForm = () => {
    if (tagInput.trim() && !formData.tags?.includes(tagInput.trim())) {
      setFormData({ ...formData, tags: [...(formData.tags || []), tagInput.trim()] });
      setTagInput('');
    }
  };
  const removeTagFromForm = (tag: string) =>
    setFormData({ ...formData, tags: formData.tags?.filter((t) => t !== tag) || [] });

  const openCreateModal = () => { setFormData(INITIAL_FORM); setShowCreateModal(true); };
  const openEditModal = (skill: Skill) => {
    setEditingSkill(skill);
    setFormData({ name: skill.name, description: skill.description || '', content: skill.content, tags: skill.tags || [] });
    setShowEditModal(true);
  };
  const closeModal = () => {
    setShowCreateModal(false); setShowEditModal(false);
    setFormData(INITIAL_FORM); setEditingSkill(null);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try { await createMutation.mutateAsync(formData); closeModal(); }
    catch (error) { alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };
  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingSkill) return;
    try { await updateMutation.mutateAsync({ id: editingSkill.id, data: formData }); closeModal(); }
    catch (error) { alert(`Update failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };
  const handleDelete = async (skill: Skill, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete "${skill.name}"?`)) return;
    try { await deleteMutation.mutateAsync(skill.id); }
    catch (error) { alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };

  const handleModeToggle = (m: ViewMode) => {
    setViewMode(m);
    if (m === 'list') setOpenItemId(null);
  };

  if (isLoading) {
    return <div className="flex items-center justify-center py-16"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>;
  }

  const isModalOpen = showCreateModal || showEditModal;

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Sparkles className="size-3.5 text-muted-foreground" />
            <h2 className="text-sm font-semibold">Skills</h2>
          </div>
          <span className="text-xs text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">{skills.length}</span>
        </div>
        <div className="flex items-center gap-2">
          <ViewToggle mode={viewMode} onToggle={handleModeToggle} />
          <Button size="sm" onClick={openCreateModal}>+ New</Button>
        </div>
      </div>

      {/* Tag filter bar */}
      {allTags.length > 0 && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          {selectedTags.length > 0 && (
            <button type="button" onClick={() => setSelectedTags([])}
              className="text-[10px] px-2 py-1 rounded-full border border-border text-muted-foreground hover:text-foreground transition-colors">
              Clear
            </button>
          )}
          {allTags.map((tag) => (
            <button key={tag} type="button"
              onClick={() => setSelectedTags((prev) => prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag])}
              className={cn('text-[10px] px-2.5 py-1 rounded-full border font-medium transition-colors',
                selectedTags.includes(tag)
                  ? 'bg-foreground text-background border-foreground'
                  : 'bg-muted/50 text-muted-foreground border-border/50 hover:border-foreground/30'
              )}>
              {tag}
            </button>
          ))}
        </div>
      )}

      {skills.length > 0 ? viewMode === 'card' ? (
        /* ── Card grid ── */
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {skills.map((skill) => (
            <div key={skill.id} className="rounded-xl border border-border bg-card p-4 flex flex-col gap-2.5 hover:bg-muted/20 transition-colors">
              <div className="flex items-start gap-2.5">
                <span className={cn('size-2 rounded-full mt-1 shrink-0', skill.has_embedding ? 'bg-violet-500' : 'bg-muted-foreground/30')} />
                <p className="text-sm font-semibold leading-snug flex-1 min-w-0 truncate">{skill.name}</p>
              </div>
              {skill.description && <p className="text-xs text-muted-foreground line-clamp-2">{skill.description}</p>}
              <p className="text-[10px] text-muted-foreground font-mono leading-relaxed line-clamp-3 bg-muted/50 rounded p-2">
                {skill.content.slice(0, 150)}{skill.content.length > 150 ? '…' : ''}
              </p>
              {skill.tags && skill.tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {skill.tags.slice(0, 4).map((tag) => (
                    <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{tag}</span>
                  ))}
                  {skill.tags.length > 4 && <span className="text-[10px] text-muted-foreground/60">+{skill.tags.length - 4}</span>}
                </div>
              )}
              <div className="flex items-center gap-3 text-[10px] text-muted-foreground mt-auto">
                <span className="tabular-nums">{skill.content.length}c</span>
                <span className={cn('inline-flex items-center gap-0.5', skill.has_embedding ? 'text-violet-500' : 'text-muted-foreground/50')}>
                  {skill.has_embedding ? <CheckCircle2 className="size-3" /> : <Circle className="size-3" />}
                </span>
                <span className="ml-auto">{formatRelativeTime(skill.created_at)}</span>
              </div>
              <div className="flex gap-2 pt-2 border-t border-border/40">
                <Button size="sm" variant="outline" className="flex-1 gap-1.5" onClick={() => openEditModal(skill)}>
                  <Pencil className="size-3.5" />Edit
                </Button>
                <Button size="sm" variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                  disabled={deleteMutation.isPending} onClick={(e) => handleDelete(skill, e)}>
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-card overflow-visible divide-y divide-border/50">
          {skills.map((skill) => {
            const indexedDot = (
              <span className={cn('size-2 rounded-full shrink-0', skill.has_embedding ? 'bg-violet-500' : 'bg-muted-foreground/30')} />
            );
            const tagChips = skill.tags && skill.tags.length > 0 ? (
              <div className="flex items-center gap-1 shrink-0">
                {skill.tags.slice(0, 3).map((tag) => (
                  <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{tag}</span>
                ))}
                {skill.tags.length > 3 && <span className="text-[10px] text-muted-foreground/60">+{skill.tags.length - 3}</span>}
              </div>
            ) : null;

            const rowHeader = (
              <>
                {indexedDot}
                <span className="text-sm font-medium flex-1 min-w-0 truncate">{skill.name}</span>
                {tagChips}
                <span className="text-[10px] text-muted-foreground/50 tabular-nums shrink-0">{skill.content.length}c</span>
              </>
            );

            if (viewMode === 'list') {
              return (
                <div key={skill.id} className="group relative flex items-center gap-3 px-4 py-3 hover:bg-muted/40 transition-colors">
                  {indexedDot}
                  <span className="text-sm font-medium flex-1 min-w-0 truncate">{skill.name}</span>
                  {tagChips}
                  <span className="text-[10px] text-muted-foreground/50 tabular-nums shrink-0">{skill.content.length}c</span>
                  <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                    <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted transition-colors" onClick={() => openEditModal(skill)}>
                      <Pencil className="size-3.5 text-muted-foreground" />
                    </button>
                    <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-destructive/10 transition-colors" onClick={(e) => handleDelete(skill, e)}>
                      <Trash2 className="size-3.5 text-destructive/70" />
                    </button>
                  </div>
                  {/* Hover tooltip */}
                  <div className="absolute right-2 top-full mt-1 z-50 w-80 rounded-xl border border-border bg-card shadow-lg shadow-black/10 p-3 invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-[opacity,visibility] duration-150 pointer-events-none">
                    <div className="space-y-2 text-xs">
                      {skill.description && <p className="text-foreground/80 leading-relaxed">{skill.description}</p>}
                      <p className="text-muted-foreground font-mono leading-relaxed line-clamp-4 bg-muted/50 rounded p-2">
                        {skill.content.slice(0, 200)}{skill.content.length > 200 ? '…' : ''}
                      </p>
                      <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pt-1.5 border-t border-border/50 text-muted-foreground">
                        <span>Indexed</span><span className="text-foreground">{skill.has_embedding ? 'Yes' : 'No'}</span>
                        <span>Length</span><span className="text-foreground tabular-nums">{skill.content.length} chars</span>
                        <span>Created</span><span className="text-foreground">{formatRelativeTime(skill.created_at)}</span>
                      </div>
                    </div>
                  </div>
                </div>
              );
            }

            // Drawer mode
            return (
              <AccordionItem
                key={skill.id}
                isOpen={openItemId === skill.id}
                onToggle={() => setOpenItemId(openItemId === skill.id ? null : skill.id)}
                header={rowHeader}
                detail={
                  <div className="space-y-3">
                    {skill.description && <p className="text-xs text-foreground/80 leading-relaxed">{skill.description}</p>}
                    <pre className="text-[10px] text-muted-foreground font-mono leading-relaxed bg-muted/50 rounded-md p-3 max-h-40 overflow-y-auto whitespace-pre-wrap break-words">
                      {skill.content.slice(0, 600)}{skill.content.length > 600 ? '…' : ''}
                    </pre>
                    {skill.summary && <p className="text-xs italic text-muted-foreground">{skill.summary}</p>}
                    <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                      <span className="text-muted-foreground">Indexed</span>
                      <span className="flex items-center gap-1.5">
                        <span className={cn('size-1.5 rounded-full', skill.has_embedding ? 'bg-violet-500' : 'bg-muted-foreground/30')} />
                        {skill.has_embedding ? 'Yes' : 'No'}
                      </span>
                      <span className="text-muted-foreground">Length</span>
                      <span className="tabular-nums">{skill.content.length} chars</span>
                      {skill.tags && skill.tags.length > 0 && (
                        <><span className="text-muted-foreground">Tags</span><span>{skill.tags.join(', ')}</span></>
                      )}
                      <span className="text-muted-foreground">Created</span>
                      <span>{formatRelativeTime(skill.created_at)}</span>
                    </div>
                    <div className="flex gap-2 pt-2 border-t border-border/40">
                      <Button size="sm" variant="outline" className="flex-1 gap-1.5" onClick={() => openEditModal(skill)}>
                        <Pencil className="size-3.5" />Edit
                      </Button>
                      <Button size="sm" variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                        disabled={deleteMutation.isPending} onClick={(e) => handleDelete(skill, e)}>
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
          <Sparkles className="mx-auto size-8 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground mb-3">No skills yet</p>
          <Button size="sm" onClick={openCreateModal}>Create Skill</Button>
        </div>
      )}

      {/* Create / Edit Dialog */}
      <Dialog open={isModalOpen} onOpenChange={(open) => { if (!open) closeModal(); }}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{showCreateModal ? 'Create Skill' : 'Edit Skill'}</DialogTitle></DialogHeader>
          <form id="skill-form" onSubmit={showCreateModal ? handleCreate : handleUpdate} className="space-y-4">
            <div className="space-y-1.5"><Label>Name *</Label>
              <Input value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} placeholder="e.g., Python Best Practices" required /></div>
            <div className="space-y-1.5"><Label>Description</Label>
              <Input value={formData.description} onChange={(e) => setFormData({ ...formData, description: e.target.value })} placeholder="Brief description" /></div>
            <div className="space-y-1.5">
              <Label>Tags</Label>
              <div className="flex gap-2">
                <Input value={tagInput} onChange={(e) => setTagInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addTagToForm())} placeholder="Add a tag..." className="flex-1" />
                <Button type="button" variant="secondary" onClick={addTagToForm}>Add</Button>
              </div>
              {formData.tags && formData.tags.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-2">
                  {formData.tags.map((tag) => (
                    <Badge key={tag} variant="secondary" className="gap-1">{tag}
                      <button type="button" onClick={() => removeTagFromForm(tag)} className="hover:text-foreground ml-1">×</button>
                    </Badge>
                  ))}
                </div>
              )}
            </div>
            <div className="space-y-1.5"><Label>Content (Markdown) *</Label>
              <Textarea value={formData.content} onChange={(e) => setFormData({ ...formData, content: e.target.value })} rows={16}
                placeholder={`# Skill Title\n\n## Description\nWrite your skill documentation in Markdown...`} className="font-mono text-sm" required />
              <p className="text-xs text-muted-foreground">Supports Markdown formatting.</p>
            </div>
          </form>
          <DialogFooter>
            <Button variant="outline" onClick={closeModal}>Cancel</Button>
            <Button type="submit" form="skill-form" disabled={createMutation.isPending || updateMutation.isPending}>
              {createMutation.isPending || updateMutation.isPending ? 'Saving...' : showCreateModal ? 'Create Skill' : 'Update Skill'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
