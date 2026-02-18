import { useState } from 'react';
import { Zap, Loader2, Trash2, Pencil } from 'lucide-react';
import { useSkills, useCreateSkill, useUpdateSkill, useDeleteSkill } from '@/hooks/useSkills';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { cn } from '@/lib/utils';
import type { SkillCreate, Skill } from '@/types/skill';

const INITIAL_FORM: SkillCreate = { name: '', description: '', content: '', tags: [] };

export default function SkillPage() {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [formData, setFormData] = useState<SkillCreate>(INITIAL_FORM);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');

  const { data: skillData, isLoading } = useSkills({
    tags: selectedTags.length > 0 ? selectedTags.join(',') : undefined,
  });
  const createMutation = useCreateSkill();
  const updateMutation = useUpdateSkill();
  const deleteMutation = useDeleteSkill();

  const skills = skillData?.items ?? [];
  const allTags = Array.from(new Set(skills.flatMap((s) => s.tags ?? []))).sort();

  const toggleTag = (tag: string) =>
    setSelectedTags((prev) => prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]);

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
    setShowCreateModal(false);
    setShowEditModal(false);
    setFormData(INITIAL_FORM);
    setEditingSkill(null);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createMutation.mutateAsync(formData);
      closeModal();
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingSkill) return;
    try {
      await updateMutation.mutateAsync({ id: editingSkill.id, data: formData });
      closeModal();
    } catch (error) {
      alert(`Update failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleDelete = async (skill: Skill) => {
    if (!confirm(`Are you sure you want to delete "${skill.name}"?`)) return;
    try {
      await deleteMutation.mutateAsync(skill.id);
    } catch (error) {
      alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const isModalOpen = showCreateModal || showEditModal;

  return (
    <div>
      <div className="mb-6 flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold">Skills</h2>
          <p className="text-sm text-muted-foreground mt-1">Manage reusable skill sets in Markdown format</p>
        </div>
        <Button onClick={openCreateModal}>+ Create Skill</Button>
      </div>

      {allTags.length > 0 && (
        <div className="mb-6 bg-card rounded-lg border border-border p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold">Filter by Tags</h3>
            {selectedTags.length > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setSelectedTags([])}>Clear All</Button>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {allTags.map((tag) => (
              <button
                key={tag}
                type="button"
                onClick={() => toggleTag(tag)}
                className={cn(
                  'px-3 py-1.5 text-sm rounded-full font-medium transition-colors',
                  selectedTags.includes(tag)
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-muted-foreground hover:bg-muted/70'
                )}
              >
                {tag}{selectedTags.includes(tag) && ' ✓'}
              </button>
            ))}
          </div>
        </div>
      )}

      {skills.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {skills.map((skill) => (
            <div key={skill.id} className="bg-card rounded-lg border border-border p-6 hover:shadow-lg transition-shadow">
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1 min-w-0">
                  <h3 className="text-lg font-semibold truncate">{skill.name}</h3>
                  {skill.description && (
                    <p className="text-xs text-muted-foreground mt-1">{skill.description}</p>
                  )}
                </div>
                {skill.has_embedding && (
                  <Badge variant="default" className="ml-2 shrink-0">Indexed</Badge>
                )}
              </div>

              {skill.tags && skill.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mb-3">
                  {skill.tags.map((tag) => (
                    <Badge key={tag} variant="secondary">{tag}</Badge>
                  ))}
                </div>
              )}

              <div className="text-sm text-muted-foreground mb-4 line-clamp-3 font-mono">
                {skill.content.substring(0, 150)}...
              </div>

              <div className="text-xs text-muted-foreground mb-4">
                <p>Created: {new Date(skill.created_at).toLocaleDateString()}</p>
                {skill.summary && <p className="mt-1 line-clamp-2">{skill.summary}</p>}
              </div>

              <div className="flex gap-2">
                <Button variant="outline" size="sm" className="flex-1 gap-1.5" onClick={() => openEditModal(skill)}>
                  <Pencil className="size-3.5" />
                  Edit
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                  disabled={deleteMutation.isPending}
                  onClick={() => handleDelete(skill)}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12">
          <Zap className="mx-auto size-12 text-muted-foreground/40 mb-4" />
          <h3 className="text-lg font-medium mb-1">No Skills Yet</h3>
          <p className="text-muted-foreground mb-4">Create your first skill to build reusable knowledge in Markdown format</p>
          <Button onClick={openCreateModal}>Create Skill</Button>
        </div>
      )}

      {/* Create / Edit Dialog */}
      <Dialog open={isModalOpen} onOpenChange={(open) => { if (!open) closeModal(); }}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{showCreateModal ? 'Create Skill' : 'Edit Skill'}</DialogTitle>
          </DialogHeader>

          <form id="skill-form" onSubmit={showCreateModal ? handleCreate : handleUpdate} className="space-y-4">
            <div className="space-y-1.5">
              <Label>Name *</Label>
              <Input
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="e.g., Python Best Practices"
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label>Description</Label>
              <Input
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="Brief description of this skill"
              />
            </div>

            <div className="space-y-1.5">
              <Label>Tags</Label>
              <div className="flex gap-2">
                <Input
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addTagToForm())}
                  placeholder="Add a tag..."
                  className="flex-1"
                />
                <Button type="button" variant="secondary" onClick={addTagToForm}>Add</Button>
              </div>
              {formData.tags && formData.tags.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-2">
                  {formData.tags.map((tag) => (
                    <Badge key={tag} variant="secondary" className="gap-1">
                      {tag}
                      <button type="button" onClick={() => removeTagFromForm(tag)} className="hover:text-foreground ml-1">×</button>
                    </Badge>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-1.5">
              <Label>Content (Markdown) *</Label>
              <Textarea
                value={formData.content}
                onChange={(e) => setFormData({ ...formData, content: e.target.value })}
                rows={16}
                placeholder={`# Skill Title\n\n## Description\nWrite your skill documentation in Markdown...`}
                className="font-mono text-sm"
                required
              />
              <p className="text-xs text-muted-foreground">Supports Markdown formatting with code blocks, headers, lists, etc.</p>
            </div>
          </form>

          <DialogFooter>
            <Button variant="outline" onClick={closeModal}>Cancel</Button>
            <Button
              type="submit"
              form="skill-form"
              disabled={createMutation.isPending || updateMutation.isPending}
            >
              {createMutation.isPending || updateMutation.isPending
                ? 'Saving...'
                : showCreateModal ? 'Create Skill' : 'Update Skill'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
